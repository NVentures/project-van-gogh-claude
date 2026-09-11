"""
scheduler_setup.py — install / uninstall / status for the background scheduler.

One cross-platform implementation of what install-van-gogh Step 8 and
uninstall-van-gogh used to do in duplicated bash + PowerShell blocks:

- macOS: a launchd LaunchAgent per skill (~/Library/LaunchAgents/).
- Windows: a Task Scheduler task per skill (Register-ScheduledTask via a
  PowerShell child, which keeps -StartWhenAvailable — a missed run fires when
  the machine wakes, mirroring launchd). Each task launches through a wscript
  `.vbs` in `~/.config/van-gogh/scheduler/` rather than cmd.exe directly, so
  no console window flashes at fire time (see _vbs_content).
- Linux: no scheduler is installed; skills still work invoked manually.

Each job runs `app/skill_run.py <skill>` from the plugin root daily at 4:30 AM,
with stdout/stderr appended to `<skill>.log` / `<skill>.err` in the vault's
`van-gogh/logs/`. Jobs go through skill_run rather than spawning `claude -p`
themselves so an unattended run gets the retries, the failure ledger, and the
CLI freshness check (app/claude_update.py): a `claude` build too old for the
current model 400s every scheduled job on the machine until someone notices. The label/task names below are the single source of
truth for both install and uninstall — they must never be duplicated in a
SKILL.md again.

The `sync-digests` subcommand additionally manages one job per emailed
briefing (see the config `digest` block and app/digest_send.py): it installs a
weekday-scheduled job for every briefing that is opted in, and removes the
jobs for everything else. /van-gogh:update-digest-preferences runs it after
every config change; `uninstall` removes digest jobs too.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from platform_compat import NO_WINDOW, claude_bin

SKILLS = ("meeting-ingest", "ingest-workspace")

# Jobs from older versions, under their old skill names. They are removed on
# every install and uninstall: a renamed skill leaves a scheduled job behind
# that wakes up daily and fails, and a silent daily failure is worse than the
# rename it came from. Never delete an entry here — machines update from
# arbitrarily old versions.
LEGACY_SKILLS = ("granola-ingest",)

# Literal Windows task names from retired standalone scripts (the deleted
# scripts/register-scheduled-tasks.ps1 registered its hourly job under a name
# that predates the WIN_TASK template, so LEGACY_SKILLS can't reach it). Same
# rule: never delete an entry — machines update from arbitrarily old versions.
LEGACY_TASK_NAMES = ("Van Gogh - Granola Auto Ingest",)
MAC_LABEL = "com.monet.{skill}"
WIN_TASK = "VanGogh-{skill}"
SCHEDULE_HOUR, SCHEDULE_MINUTE = 4, 30

# The watcher is scheduled like everything else but is not a skill: it runs
# app/job_watch.py, not a `claude -p` prompt, so it stays out of SKILLS (which
# every loop over "skill jobs" iterates) and gets its own three lines here.
# It runs on a plain interval rather than a clock time because what it checks
# is whether the clock-time jobs did what they were supposed to.
WATCH_LABEL = "com.monet.job-watch"
WATCH_TASK = "VanGogh-job-watch"
WATCH_INTERVAL_MIN = 30

KPI_LABEL = "com.monet.kpi-email"
KPI_TASK = "VanGogh-kpi-email"

# The weekly finance brief (app/finance_send.py). Unlike the scorecard this one
# does reach a model, because reading QuickBooks goes through a confined
# `claude -p` holding the user's connector, so it inherits the CLI's failure
# taxonomy and needs the resolved binary path like the digest jobs do.
FINANCE_LABEL = "com.monet.finance-email"
FINANCE_TASK = "VanGogh-finance-email"

# Contact capture (app/contact_capture.py): the weekly address-book top-up.
# Weekly rather than a poller because the work is a batch by nature, and no
# `claude` path is embedded: it is pure Python against Gmail and the People
# API, so it cannot inherit the CLI's version, quota or classifier failures.
CAPTURE_LABEL = "com.monet.contact-capture"
CAPTURE_TASK = "VanGogh-contact-capture"

PREP_LABEL = "com.monet.prep-email"
PREP_TASK = "VanGogh-prep-email"
# Fifteen, not thirty: a prep promises to arrive about an hour before a call,
# and a 30 minute tick turns that into a 30 minute spread. The tick is cheap
# (a calendar read, and nothing else unless something is due).
PREP_INTERVAL_MIN = 15

NOTE_LABEL = "com.monet.note"
NOTE_TASK = "VanGogh-note"
# The Note tick (app/note_send.py): the watcher between briefings. Fifteen,
# like the prep poller, and cheap on a quiet tick (one inbound list call per
# account and a calendar read; the model runs only when something fires).
NOTE_INTERVAL_MIN = 15

DIGEST_MAC_LABEL = "com.monet.digest-{briefing}"
DIGEST_WIN_TASK = "VanGogh-digest-{briefing}"
# launchd StartCalendarInterval weekday numbers (0 = Sunday).
LAUNCHD_WEEKDAY = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
                   "thursday": 4, "friday": 5, "saturday": 6}


def _launch_agent_path(skill: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL.format(skill=skill)}.plist"


def _plist_content(skill: str, repo: str, python: str, claude: str,
                   logs_dir: Path) -> str:
    script = str(Path(repo) / "app" / "skill_run.py")
    command = f'cd "{repo}" && "{python}" "{script}" {skill} --claude "{claude}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{MAC_LABEL.format(skill=skill)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{SCHEDULE_HOUR}</integer>
    <key>Minute</key><integer>{SCHEDULE_MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / f"{skill}.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / f"{skill}.err"))}</string>
</dict>
</plist>
"""


def _watch_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{WATCH_LABEL}.plist"


def _watch_plist_content(repo: str, python: str, logs_dir: Path) -> str:
    """The watcher's launchd job: every WATCH_INTERVAL_MIN, and at login.

    RunAtLoad matters more here than anywhere else in the product. A laptop
    that was shut when the 07:00 digest was due wakes with that slot already
    missed, and the first thing it should do is notice.
    """
    script = str(Path(repo) / "app" / "job_watch.py")
    command = f'cd "{repo}" && "{python}" "{script}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{WATCH_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartInterval</key>
  <integer>{WATCH_INTERVAL_MIN * 60}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "job-watch.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "job-watch.err"))}</string>
</dict>
</plist>
"""


def _watch_inner(repo: str, python: str, logs_dir: Path) -> str:
    """The cmd command line one watcher tick executes (inside the vbs)."""
    log = str(logs_dir / "job-watch.log")
    err = str(logs_dir / "job-watch.err")
    script = str(Path(repo) / "app" / "job_watch.py")
    return f'"{python}" "{script}" 1>> "{log}" 2>> "{err}"'


def _watch_task_ps(repo: str, vbs: Path) -> str:
    """The PowerShell that registers the watcher as a repeating task.

    A repetition of indefinite duration on a daily trigger is Task
    Scheduler's way of saying "every N minutes, forever"; there is no
    interval trigger to match launchd's StartInterval.
    """
    argument = f'//B //Nologo "{vbs}"'
    return (
        "$trigger = New-ScheduledTaskTrigger -Daily -At 12:05am; "
        "$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:05am "
        f"-RepetitionInterval (New-TimeSpan -Minutes {WATCH_INTERVAL_MIN}) "
        "-RepetitionDuration ([TimeSpan]::MaxValue)).Repetition; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(WATCH_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: re-runs scheduled jobs that failed or never fired')} "
        "-Force | Out-Null"
    )


def _prep_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PREP_LABEL}.plist"


def _prep_plist_content(repo: str, python: str, claude: str, logs_dir: Path) -> str:
    """The prep poller: every PREP_INTERVAL_MIN, and at login.

    Carries the resolved claude path for the same reason the digest jobs do:
    a prep is written by a headless render, and launchd's PATH is too sparse to
    find the CLI at fire time.
    """
    script = str(Path(repo) / "app" / "prep_send.py")
    command = f'cd "{repo}" && "{python}" "{script}" --claude "{claude}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PREP_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartInterval</key>
  <integer>{PREP_INTERVAL_MIN * 60}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "prep-email.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "prep-email.err"))}</string>
</dict>
</plist>
"""


def _prep_inner(repo: str, python: str, claude: str, logs_dir: Path) -> str:
    """The cmd command line one prep tick executes (inside the vbs)."""
    log = str(logs_dir / "prep-email.log")
    err = str(logs_dir / "prep-email.err")
    script = str(Path(repo) / "app" / "prep_send.py")
    return f'"{python}" "{script}" --claude "{claude}" 1>> "{log}" 2>> "{err}"'


def _prep_task_ps(repo: str, vbs: Path) -> str:
    """Register the prep poller as a repeating task (see _watch_task_ps)."""
    argument = f'//B //Nologo "{vbs}"'
    return (
        "$trigger = New-ScheduledTaskTrigger -Daily -At 12:05am; "
        "$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:05am "
        f"-RepetitionInterval (New-TimeSpan -Minutes {PREP_INTERVAL_MIN}) "
        "-RepetitionDuration ([TimeSpan]::MaxValue)).Repetition; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(PREP_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: emails a meeting prep before each call')} "
        "-Force | Out-Null"
    )


def _note_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{NOTE_LABEL}.plist"


def _note_plist_content(repo: str, python: str, claude: str, logs_dir: Path) -> str:
    """The Note tick: every NOTE_INTERVAL_MIN, and at login. Carries the
    resolved claude path for the same reason the prep poller does."""
    script = str(Path(repo) / "app" / "note_send.py")
    command = f'cd "{repo}" && "{python}" "{script}" --claude "{claude}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{NOTE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartInterval</key>
  <integer>{NOTE_INTERVAL_MIN * 60}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "note.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "note.err"))}</string>
</dict>
</plist>
"""


def _note_inner(repo: str, python: str, claude: str, logs_dir: Path) -> str:
    """The cmd command line one Note tick executes (inside the vbs)."""
    log = str(logs_dir / "note.log")
    err = str(logs_dir / "note.err")
    script = str(Path(repo) / "app" / "note_send.py")
    return f'"{python}" "{script}" --claude "{claude}" 1>> "{log}" 2>> "{err}"'


def _note_task_ps(repo: str, vbs: Path) -> str:
    """Register the Note tick as a repeating task (see _watch_task_ps)."""
    argument = f'//B //Nologo "{vbs}"'
    return (
        "$trigger = New-ScheduledTaskTrigger -Daily -At 12:10am; "
        "$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:10am "
        f"-RepetitionInterval (New-TimeSpan -Minutes {NOTE_INTERVAL_MIN}) "
        "-RepetitionDuration ([TimeSpan]::MaxValue)).Repetition; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(NOTE_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: the watcher between briefings')} "
        "-Force | Out-Null"
    )


def _kpi_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{KPI_LABEL}.plist"


def _kpi_plist_content(repo: str, python: str, logs_dir: Path,
                       day: str, hour: int, minute: int) -> str:
    """The weekly scorecard: one calendar slot a week.

    No `claude` path is embedded, unlike the digest and prep jobs: the
    scorecard is computed entirely in Python from the events log, so it cannot
    inherit the CLI's version, quota or classifier failures.
    """
    script = str(Path(repo) / "app" / "kpi_send.py")
    command = f'cd "{repo}" && "{python}" "{script}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{KPI_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>{LAUNCHD_WEEKDAY[day]}</integer><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "kpi-email.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "kpi-email.err"))}</string>
</dict>
</plist>
"""


def _kpi_inner(repo: str, python: str, logs_dir: Path) -> str:
    """The cmd line one scorecard run executes (inside the vbs)."""
    log = str(logs_dir / "kpi-email.log")
    err = str(logs_dir / "kpi-email.err")
    script = str(Path(repo) / "app" / "kpi_send.py")
    return f'"{python}" "{script}" 1>> "{log}" 2>> "{err}"'


def _kpi_task_ps(repo: str, vbs: Path, day: str, hour: int, minute: int) -> str:
    """Register the scorecard as a weekly task."""
    argument = f'//B //Nologo "{vbs}"'
    return (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day.capitalize()} "
        f"-At {hour}:{minute:02d}; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(KPI_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: emails a weekly scorecard')} "
        "-Force | Out-Null"
    )


def _finance_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{FINANCE_LABEL}.plist"


def _finance_plist_content(repo: str, python: str, claude: str, logs_dir: Path,
                           day: str, hour: int, minute: int) -> str:
    """The weekly finance brief: one calendar slot a week.

    The `claude` path is embedded, unlike the scorecard's: this job reads
    QuickBooks through a confined headless session, and launchd hands a job a
    PATH too sparse to resolve the binary at fire time.
    """
    script = str(Path(repo) / "app" / "finance_send.py")
    command = f'cd "{repo}" && "{python}" "{script}" --claude "{claude}"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{FINANCE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>{LAUNCHD_WEEKDAY[day]}</integer><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "finance-email.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "finance-email.err"))}</string>
</dict>
</plist>
"""


def _finance_inner(repo: str, python: str, claude: str, logs_dir: Path) -> str:
    """The cmd line one finance run executes (inside the vbs)."""
    log = str(logs_dir / "finance-email.log")
    err = str(logs_dir / "finance-email.err")
    script = str(Path(repo) / "app" / "finance_send.py")
    return f'"{python}" "{script}" --claude "{claude}" 1>> "{log}" 2>> "{err}"'


def _finance_task_ps(repo: str, vbs: Path, day: str, hour: int,
                     minute: int) -> str:
    """Register the finance brief as a weekly task."""
    argument = f'//B //Nologo "{vbs}"'
    return (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day.capitalize()} "
        f"-At {hour}:{minute:02d}; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(FINANCE_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: emails a weekly finance brief')} "
        "-Force | Out-Null"
    )


def _finance_cadence() -> tuple:
    """(day, hour, minute) for the brief, already validated by the loader."""
    import config_loader

    hour, minute = (int(p) for p in config_loader.finance_time().split(":", 1))
    return config_loader.finance_day(), hour, minute


def _kpi_cadence() -> tuple:
    """(day, hour, minute) for the scorecard, already validated by the loader."""
    import config_loader

    hour, minute = (int(p) for p in config_loader.kpi_time().split(":", 1))
    return config_loader.kpi_day(), hour, minute


def _capture_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{CAPTURE_LABEL}.plist"


def _capture_plist_content(repo: str, python: str, logs_dir: Path,
                           day: str, hour: int, minute: int, days: int) -> str:
    """The weekly capture: one calendar slot a week.

    `--apply` is present because this IS the unattended path. The human gate
    lives at the skill, which does not schedule the job until a dry-run report
    has been approved; a scheduled dry run would write a report nobody reads.
    """
    script = str(Path(repo) / "app" / "contact_capture.py")
    command = f'cd "{repo}" && "{python}" "{script}" --apply --days {days}'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{CAPTURE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>{LAUNCHD_WEEKDAY[day]}</integer><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / "contact-capture.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / "contact-capture.err"))}</string>
</dict>
</plist>
"""


def _capture_inner(repo: str, python: str, logs_dir: Path, days: int) -> str:
    """The cmd line one capture run executes (inside the vbs)."""
    log = str(logs_dir / "contact-capture.log")
    err = str(logs_dir / "contact-capture.err")
    script = str(Path(repo) / "app" / "contact_capture.py")
    return (f'"{python}" "{script}" --apply --days {days} '
            f'1>> "{log}" 2>> "{err}"')


def _capture_task_ps(repo: str, vbs: Path, day: str, hour: int,
                     minute: int) -> str:
    """Register contact capture as a weekly task."""
    argument = f'//B //Nologo "{vbs}"'
    return (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day.capitalize()} "
        f"-At {hour}:{minute:02d}; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(CAPTURE_TASK)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote('Project Van Gogh: saves people you correspond with into Contacts')} "
        "-Force | Out-Null"
    )


def _capture_cadence() -> tuple:
    """(day, hour, minute, window_days), already validated by the loader."""
    import config_loader

    hour, minute = (int(part) for part
                    in config_loader.contact_capture_time().split(":", 1))
    return (config_loader.contact_capture_day(), hour, minute,
            config_loader.contact_capture_window_days())


def _digest_launch_agent_path(briefing: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{DIGEST_MAC_LABEL.format(briefing=briefing)}.plist"


def _parse_cadence(cadence: dict) -> tuple[list[str], int, int]:
    """Validate a config digest cadence into (days, hour, minute)."""
    days = [str(d).strip().lower() for d in cadence.get("days", [])]
    bad = [d for d in days if d not in LAUNCHD_WEEKDAY]
    if not days or bad:
        raise ValueError(f"invalid digest days: {cadence.get('days')!r}")
    try:
        hour_s, minute_s = str(cadence.get("time", "")).split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise ValueError(f"invalid digest time (want HH:MM): {cadence.get('time')!r}")
    return days, hour, minute


def _digest_python() -> str:
    """The interpreter to embed in digest jobs: the managed per-user venv when
    it exists (survives dev checkouts running sync), else this interpreter."""
    import user_state
    venv = user_state.state_dir() / "venv"
    candidate = (venv / "Scripts" / "python.exe") if sys.platform == "win32" \
        else (venv / "bin" / "python")
    return str(candidate) if candidate.exists() else sys.executable


def _digest_plist_content(briefing: str, repo: str, python: str, claude: str,
                          logs_dir: Path, days: list[str], hour: int, minute: int) -> str:
    script = str(Path(repo) / "app" / "digest_send.py")
    command = f'cd "{repo}" && "{python}" "{script}" {briefing} --claude "{claude}"'
    intervals = "\n".join(
        f"    <dict><key>Weekday</key><integer>{LAUNCHD_WEEKDAY[d]}</integer>"
        f"<key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>"
        for d in days
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{DIGEST_MAC_LABEL.format(briefing=briefing)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(command)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(repo)}</string>
  <key>StartCalendarInterval</key>
  <array>
{intervals}
  </array>
  <key>StandardOutPath</key>
  <string>{escape(str(logs_dir / f"digest-{briefing}.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(logs_dir / f"digest-{briefing}.err"))}</string>
</dict>
</plist>
"""


def _ps_quote(s: str) -> str:
    """Single-quote a string for PowerShell ('' escapes a literal quote)."""
    return "'" + s.replace("'", "''") + "'"


def _vbs_dir() -> Path:
    """Machine-state home for the wscript launchers (survives plugin updates)."""
    import user_state
    return user_state.state_dir() / "scheduler"


def _vbs_path(task: str) -> Path:
    return _vbs_dir() / f"{task}.vbs"


def _vbs_content(inner: str) -> str:
    """A wscript launcher that runs `cmd /c <inner>` with no console window.

    A Task Scheduler action that executes cmd.exe directly pops a visible
    conhost window in the foreground on every fire. wscript.exe is a
    GUI-subsystem binary (no console of its own), and WScript.Shell.Run
    window-style 0 launches the cmd child hidden too. VBS escapes a literal
    double quote inside a string by doubling it. bWaitOnReturn must be True
    and the exit code propagated via WScript.Quit: otherwise wscript exits
    instantly, so Task Scheduler loses overlap protection (two concurrent
    briefing runs against the same vault files) and Last Run Result is
    always 0x0 even when the job crashes.
    """
    full = f'cmd.exe /c "{inner}"'
    return ('WScript.Quit CreateObject("WScript.Shell").Run("'
            + full.replace('"', '""') + '", 0, True)\r\n')


def _write_vbs(vbs: Path, content: str) -> None:
    """Write a .vbs launcher in the one encoding WSH reliably parses.

    Deliberate exception to the utf-8-everywhere rule: Windows Script Host
    reads .vbs files as the system ANSI codepage or UTF-16 LE with BOM —
    never UTF-8. A UTF-8 launcher whose embedded paths contain any
    non-ASCII character (non-English username, accented vault folder) is
    misdecoded through the ANSI codepage and the task fails with no error
    trail. Python's "utf-16" writes the LE BOM. newline="" stops write_text
    translating the intended \\r\\n into \\r\\r\\n on Windows.
    """
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(content, encoding="utf-16", newline="")


def _task_inner(skill: str, repo: str, python: str, claude: str,
                logs_dir: Path) -> str:
    """The cmd command line one scheduled skill run executes (inside the vbs)."""
    log = str(logs_dir / f"{skill}.log")
    err = str(logs_dir / f"{skill}.err")
    script = str(Path(repo) / "app" / "skill_run.py")
    return (f'"{python}" "{script}" {skill} --claude "{claude}" '
            f'1>> "{log}" 2>> "{err}"')


def _digest_inner(briefing: str, repo: str, python: str, claude: str,
                  logs_dir: Path) -> str:
    """The cmd command line one digest run executes (inside the vbs)."""
    log = str(logs_dir / f"digest-{briefing}.log")
    err = str(logs_dir / f"digest-{briefing}.err")
    script = str(Path(repo) / "app" / "digest_send.py")
    return (f'"{python}" "{script}" {briefing} --claude "{claude}" '
            f'1>> "{log}" 2>> "{err}"')


def _register_task_ps(skill: str, repo: str, vbs: Path) -> str:
    """The PowerShell command that registers one scheduled task."""
    task = WIN_TASK.format(skill=skill)
    argument = f'//B //Nologo "{vbs}"'
    return (
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {SCHEDULE_HOUR}:{SCHEDULE_MINUTE:02d}am; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(task)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote(f'Project Van Gogh: daily /van-gogh:{skill}')} "
        "-Force | Out-Null"
    )


def _digest_task_ps(briefing: str, repo: str, vbs: Path,
                    days: list[str], hour: int, minute: int) -> str:
    """The PowerShell command that registers one digest scheduled task."""
    task = DIGEST_WIN_TASK.format(briefing=briefing)
    argument = f'//B //Nologo "{vbs}"'
    days_of_week = ",".join(d.capitalize() for d in days)
    return (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days_of_week} "
        f"-At {hour}:{minute:02d}; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(argument)} "
        f"-WorkingDirectory {_ps_quote(repo)}; "
        f"Register-ScheduledTask -TaskName {_ps_quote(task)} -Action $action -Trigger $trigger "
        f"-Settings $settings -Description {_ps_quote(f'Project Van Gogh: emailed /van-gogh:{briefing} digest')} "
        "-Force | Out-Null"
    )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          creationflags=NO_WINDOW)


def _repo_root() -> str:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        return plugin_root
    from config_loader import repo_root
    return str(repo_root())


def _logs_dir() -> Path:
    from config_loader import logs_dir
    return logs_dir()


def remove_legacy_jobs(platform: str) -> None:
    """Unload and delete scheduled jobs left by skills that have since been
    renamed. Quiet and best-effort: nothing here is worth failing an install
    over, and on a fresh machine there is nothing to remove."""
    for skill in LEGACY_SKILLS:
        if platform == "darwin":
            plist = _launch_agent_path(skill)
            if not plist.exists():
                continue
            _run(["launchctl", "unload", str(plist)])
            plist.unlink(missing_ok=True)
            print(f"OK: removed legacy job {plist.name}")
        elif platform == "win32":
            task = WIN_TASK.format(skill=skill)
            if _run(["schtasks", "/query", "/tn", task]).returncode != 0:
                continue
            _run(["schtasks", "/delete", "/tn", task, "/f"])
            _vbs_path(task).unlink(missing_ok=True)
            print(f"OK: removed legacy task {task}")
    if platform == "win32":
        # Tasks registered by retired standalone scripts, by literal name.
        # These ran powershell.exe directly (no .vbs wrapper to unlink) and
        # never existed on macOS.
        for task in LEGACY_TASK_NAMES:
            if _run(["schtasks", "/query", "/tn", task]).returncode != 0:
                continue
            _run(["schtasks", "/delete", "/tn", task, "/f"])
            print(f"OK: removed legacy task {task}")


def cmd_install(platform: str) -> int:
    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler is installed on this OS; "
              "skills still work when invoked manually")
        return 0
    try:
        claude = claude_bin()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    repo = _repo_root()
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    python = _digest_python()

    remove_legacy_jobs(platform)

    if platform == "darwin":
        for skill in SKILLS:
            plist = _launch_agent_path(skill)
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(_plist_content(skill, repo, python, claude, logs),
                             encoding="utf-8")
            _run(["launchctl", "unload", str(plist)])  # ignore failure (not loaded yet)
            res = _run(["launchctl", "load", str(plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {plist.name}: {res.stderr.strip()}")
                return 1
        watch_plist = _watch_launch_agent_path()
        watch_plist.write_text(_watch_plist_content(repo, python, logs),
                               encoding="utf-8")
        _run(["launchctl", "unload", str(watch_plist)])  # ignore (not loaded yet)
        res = _run(["launchctl", "load", str(watch_plist)])
        if res.returncode != 0:
            print(f"ERROR: launchctl load failed for {watch_plist.name}: "
                  f"{res.stderr.strip()}")
            return 1
        # The prep poller is opt-in, so sync installs it or removes it to match
        # config. Removing on disable is the half that matters: a user who turns
        # prep emails off and still gets one has been ignored.
        import config_loader
        prep_plist = _prep_launch_agent_path()
        prep_on = config_loader.prep_email_enabled()
        if prep_on:
            prep_plist.write_text(
                _prep_plist_content(repo, python, claude, logs), encoding="utf-8")
            _run(["launchctl", "unload", str(prep_plist)])  # ignore (not loaded yet)
            res = _run(["launchctl", "load", str(prep_plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {prep_plist.name}: "
                      f"{res.stderr.strip()}")
                return 1
        elif prep_plist.exists():
            _run(["launchctl", "unload", str(prep_plist)])
            prep_plist.unlink(missing_ok=True)
            print(f"OK: removed {PREP_LABEL} (meeting prep email is off)")

        # The Note tick, same opt-in shape as the prep poller.
        note_plist = _note_launch_agent_path()
        note_on = config_loader.notes_enabled()
        if note_on:
            note_plist.write_text(
                _note_plist_content(repo, python, claude, logs), encoding="utf-8")
            _run(["launchctl", "unload", str(note_plist)])  # ignore (not loaded yet)
            res = _run(["launchctl", "load", str(note_plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {note_plist.name}: "
                      f"{res.stderr.strip()}")
                return 1
        elif note_plist.exists():
            _run(["launchctl", "unload", str(note_plist)])
            note_plist.unlink(missing_ok=True)
            print(f"OK: removed {NOTE_LABEL} (notes are off)")

        # The weekly scorecard, same opt-in shape: installed when it is on,
        # removed when it is off. A user who turned it off and still receives
        # one has been ignored.
        kpi_plist = _kpi_launch_agent_path()
        kpi_on = config_loader.kpi_enabled()
        if kpi_on:
            kpi_day, kpi_hour, kpi_minute = _kpi_cadence()
            kpi_plist.write_text(
                _kpi_plist_content(repo, python, logs, kpi_day, kpi_hour,
                                   kpi_minute), encoding="utf-8")
            _run(["launchctl", "unload", str(kpi_plist)])  # ignore (not loaded yet)
            res = _run(["launchctl", "load", str(kpi_plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {kpi_plist.name}: "
                      f"{res.stderr.strip()}")
                return 1
        elif kpi_plist.exists():
            _run(["launchctl", "unload", str(kpi_plist)])
            kpi_plist.unlink(missing_ok=True)
            print(f"OK: removed {KPI_LABEL} (the weekly scorecard is off)")

        # The weekly finance brief, same opt-in shape as the scorecard.
        finance_plist = _finance_launch_agent_path()
        finance_on = config_loader.finance_enabled()
        if finance_on:
            fin_day, fin_hour, fin_minute = _finance_cadence()
            finance_plist.write_text(
                _finance_plist_content(repo, python, claude, logs, fin_day,
                                       fin_hour, fin_minute), encoding="utf-8")
            _run(["launchctl", "unload", str(finance_plist)])  # not loaded yet
            res = _run(["launchctl", "load", str(finance_plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {finance_plist.name}: "
                      f"{res.stderr.strip()}")
                return 1
        elif finance_plist.exists():
            _run(["launchctl", "unload", str(finance_plist)])
            finance_plist.unlink(missing_ok=True)
            print(f"OK: removed {FINANCE_LABEL} "
                  "(the weekly finance brief is off)")

        # Contact capture, same opt-in shape as the scorecard.
        capture_plist = _capture_launch_agent_path()
        capture_on = config_loader.contact_capture_enabled()
        if capture_on:
            cap_day, cap_hour, cap_minute, cap_days = _capture_cadence()
            capture_plist.write_text(
                _capture_plist_content(repo, python, logs, cap_day, cap_hour,
                                       cap_minute, cap_days), encoding="utf-8")
            _run(["launchctl", "unload", str(capture_plist)])  # ignore (not loaded yet)
            res = _run(["launchctl", "load", str(capture_plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {capture_plist.name}: "
                      f"{res.stderr.strip()}")
                return 1
        elif capture_plist.exists():
            _run(["launchctl", "unload", str(capture_plist)])
            capture_plist.unlink(missing_ok=True)
            print(f"OK: removed {CAPTURE_LABEL} (contact capture is off)")

        loaded = _run(["launchctl", "list"]).stdout
        for skill in SKILLS:
            label = MAC_LABEL.format(skill=skill)
            print(f"OK: {label} loaded" if label in loaded
                  else f"ERROR: {label} did not appear in launchctl list")
        print(f"OK: {WATCH_LABEL} loaded" if WATCH_LABEL in loaded
              else f"ERROR: {WATCH_LABEL} did not appear in launchctl list")
        wanted = [MAC_LABEL.format(skill=s) for s in SKILLS] + [WATCH_LABEL]
        if prep_on:
            print(f"OK: {PREP_LABEL} loaded" if PREP_LABEL in loaded
                  else f"ERROR: {PREP_LABEL} did not appear in launchctl list")
            wanted.append(PREP_LABEL)
        if note_on:
            print(f"OK: {NOTE_LABEL} loaded" if NOTE_LABEL in loaded
                  else f"ERROR: {NOTE_LABEL} did not appear in launchctl list")
            wanted.append(NOTE_LABEL)
        if kpi_on:
            print(f"OK: {KPI_LABEL} loaded" if KPI_LABEL in loaded
                  else f"ERROR: {KPI_LABEL} did not appear in launchctl list")
            wanted.append(KPI_LABEL)
        if finance_on:
            print(f"OK: {FINANCE_LABEL} loaded" if FINANCE_LABEL in loaded
                  else f"ERROR: {FINANCE_LABEL} did not appear in launchctl list")
            wanted.append(FINANCE_LABEL)
        if capture_on:
            print(f"OK: {CAPTURE_LABEL} loaded" if CAPTURE_LABEL in loaded
                  else f"ERROR: {CAPTURE_LABEL} did not appear in launchctl list")
            wanted.append(CAPTURE_LABEL)
        return 0 if all(label in loaded for label in wanted) else 1

    for skill in SKILLS:
        task = WIN_TASK.format(skill=skill)
        vbs = _vbs_path(task)
        _write_vbs(vbs, _vbs_content(_task_inner(skill, repo, python, claude, logs)))
        res = _run(["powershell", "-NoProfile", "-Command",
                    _register_task_ps(skill, repo, vbs)])
        if res.returncode != 0:
            print(f"ERROR: Register-ScheduledTask failed for {task}: {res.stderr.strip()}")
            return 1
        verify = _run(["schtasks", "/query", "/tn", task])
        print(f"OK: {task} registered" if verify.returncode == 0
              else f"ERROR: {task} did not appear in schtasks /query")
        if verify.returncode != 0:
            return 1
    watch_vbs = _vbs_path(WATCH_TASK)
    _write_vbs(watch_vbs, _vbs_content(_watch_inner(repo, python, logs)))
    res = _run(["powershell", "-NoProfile", "-Command",
                _watch_task_ps(repo, watch_vbs)])
    if res.returncode != 0:
        print(f"ERROR: Register-ScheduledTask failed for {WATCH_TASK}: "
              f"{res.stderr.strip()}")
        return 1
    verify = _run(["schtasks", "/query", "/tn", WATCH_TASK])
    print(f"OK: {WATCH_TASK} registered" if verify.returncode == 0
          else f"ERROR: {WATCH_TASK} did not appear in schtasks /query")
    if verify.returncode != 0:
        return 1

    import config_loader

    # Two opt-in jobs, each installed or removed to match its own setting.
    # Neither may short-circuit the other: an early return here once meant
    # that turning prep emails off also skipped every job registered after it.
    rc = 0
    if not _register_optional_win_task(
            PREP_TASK, config_loader.prep_email_enabled(),
            lambda: _vbs_content(_prep_inner(repo, python, claude, logs)),
            lambda vbs: _prep_task_ps(repo, vbs),
            "meeting prep email is off"):
        rc = 1
    if not _register_optional_win_task(
            NOTE_TASK, config_loader.notes_enabled(),
            lambda: _vbs_content(_note_inner(repo, python, claude, logs)),
            lambda vbs: _note_task_ps(repo, vbs),
            "notes are off"):
        rc = 1

    kpi_day, kpi_hour, kpi_minute = _kpi_cadence()
    if not _register_optional_win_task(
            KPI_TASK, config_loader.kpi_enabled(),
            lambda: _vbs_content(_kpi_inner(repo, python, logs)),
            lambda vbs: _kpi_task_ps(repo, vbs, kpi_day, kpi_hour, kpi_minute),
            "the weekly scorecard is off"):
        rc = 1

    fin_day, fin_hour, fin_minute = _finance_cadence()
    if not _register_optional_win_task(
            FINANCE_TASK, config_loader.finance_enabled(),
            lambda: _vbs_content(_finance_inner(repo, python, claude, logs)),
            lambda vbs: _finance_task_ps(repo, vbs, fin_day, fin_hour, fin_minute),
            "the weekly finance brief is off"):
        rc = 1

    cap_day, cap_hour, cap_minute, cap_days = _capture_cadence()
    if not _register_optional_win_task(
            CAPTURE_TASK, config_loader.contact_capture_enabled(),
            lambda: _vbs_content(_capture_inner(repo, python, logs, cap_days)),
            lambda vbs: _capture_task_ps(repo, vbs, cap_day, cap_hour, cap_minute),
            "contact capture is off"):
        rc = 1
    return rc


def _register_optional_win_task(task: str, wanted: bool, vbs_content,
                                register_ps, off_note: str) -> bool:
    """Install or remove one opt-in scheduled task. True when it ended right.

    Removing on disable is the half that matters: a user who turns a feature
    off and still gets its email has been ignored.
    """
    if not wanted:
        res = _run(["schtasks", "/delete", "/tn", task, "/f"])
        _vbs_path(task).unlink(missing_ok=True)
        if res.returncode == 0:
            print(f"OK: removed {task} ({off_note})")
        return True
    vbs = _vbs_path(task)
    _write_vbs(vbs, vbs_content())
    res = _run(["powershell", "-NoProfile", "-Command", register_ps(vbs)])
    if res.returncode != 0:
        print(f"ERROR: Register-ScheduledTask failed for {task}: "
              f"{res.stderr.strip()}")
        return False
    verify = _run(["schtasks", "/query", "/tn", task])
    print(f"OK: {task} registered" if verify.returncode == 0
          else f"ERROR: {task} did not appear in schtasks /query")
    return verify.returncode == 0


def _remove_digest_job(platform: str, briefing: str, quiet_missing: bool = False) -> None:
    if platform == "darwin":
        plist = _digest_launch_agent_path(briefing)
        if not plist.exists():
            if not quiet_missing:
                print(f"NOT_INSTALLED: {plist.name}")
            return
        _run(["launchctl", "unload", str(plist)])  # ignore failure (not loaded)
        plist.unlink()
        print(f"OK: removed {plist.name}")
    else:
        task = DIGEST_WIN_TASK.format(briefing=briefing)
        res = _run(["schtasks", "/delete", "/tn", task, "/f"])
        _vbs_path(task).unlink(missing_ok=True)
        if res.returncode == 0:
            print(f"OK: removed {task}")
        elif not quiet_missing:
            print(f"NOT_INSTALLED: {task}")


def cmd_sync_digests(platform: str) -> int:
    """Install a job per opted-in briefing at its configured cadence; remove
    the jobs for every briefing that is not opted in. Idempotent."""
    from config_loader import DIGEST_BRIEFING_DEFAULTS, cfg, digest_briefings, digest_enabled

    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler on this OS; digests cannot be scheduled "
              "(run app/digest_send.py manually or via cron)")
        return 0
    try:
        enabled_all = digest_enabled()
        briefings = digest_briefings()
        raw_keys = set((cfg().get("digest", {}) or {}).get("briefings", {}) or {})
    except Exception as e:
        print(f"ERROR: cannot read config.json: {e}")
        return 1
    for key in sorted(raw_keys - set(DIGEST_BRIEFING_DEFAULTS)):
        print(f"WARN: unknown briefing {key!r} in digest.briefings — ignored "
              f"(known: {', '.join(sorted(DIGEST_BRIEFING_DEFAULTS))})")
    to_install = {name: cadence for name, cadence in briefings.items()
                  if enabled_all and cadence.get("enabled", False)}

    for name in briefings:
        if name not in to_install:
            _remove_digest_job(platform, name, quiet_missing=True)
    if not to_install:
        print("OK: digests disabled — no digest jobs scheduled")
        return 0

    try:
        claude = claude_bin()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    repo = _repo_root()
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    python = _digest_python()
    failed = False
    for name, cadence in sorted(to_install.items()):
        try:
            days, hour, minute = _parse_cadence(cadence)
        except ValueError as e:
            # Never leave a job running on a schedule the config no longer
            # describes — config and jobs must not drift apart.
            print(f"ERROR: {name}: {e} — job removed until the config is fixed")
            _remove_digest_job(platform, name, quiet_missing=True)
            failed = True
            continue
        when = f"{'/'.join(d[:3] for d in days)} {hour:02d}:{minute:02d}"
        if platform == "darwin":
            plist = _digest_launch_agent_path(name)
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(
                _digest_plist_content(name, repo, python, claude, logs, days, hour, minute),
                encoding="utf-8")
            _run(["launchctl", "unload", str(plist)])  # ignore failure (not loaded yet)
            res = _run(["launchctl", "load", str(plist)])
            if res.returncode != 0:
                print(f"ERROR: launchctl load failed for {plist.name}: {res.stderr.strip()}")
                failed = True
            else:
                print(f"OK: {DIGEST_MAC_LABEL.format(briefing=name)} scheduled ({when})")
        else:
            task = DIGEST_WIN_TASK.format(briefing=name)
            vbs = _vbs_path(task)
            _write_vbs(vbs, _vbs_content(_digest_inner(name, repo, python, claude, logs)))
            res = _run(["powershell", "-NoProfile", "-Command",
                        _digest_task_ps(name, repo, vbs, days, hour, minute)])
            if res.returncode != 0:
                print(f"ERROR: Register-ScheduledTask failed for {task}: {res.stderr.strip()}")
                failed = True
            else:
                print(f"OK: {task} scheduled ({when})")
    return 1 if failed else 0


def cmd_uninstall(platform: str) -> int:
    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler is installed on this OS; nothing to remove")
        return 0
    from config_loader import DIGEST_BRIEFING_DEFAULTS

    remove_legacy_jobs(platform)

    if platform == "darwin":
        for skill in SKILLS:
            plist = _launch_agent_path(skill)
            if not plist.exists():
                print(f"NOT_INSTALLED: {plist.name}")
                continue
            _run(["launchctl", "unload", str(plist)])  # ignore failure (not loaded)
            plist.unlink()
            print(f"OK: removed {plist.name}")
        for briefing in DIGEST_BRIEFING_DEFAULTS:
            _remove_digest_job(platform, briefing)
        watch_plist = _watch_launch_agent_path()
        if watch_plist.exists():
            _run(["launchctl", "unload", str(watch_plist)])  # ignore (not loaded)
            watch_plist.unlink()
            print(f"OK: removed {watch_plist.name}")
        else:
            print(f"NOT_INSTALLED: {watch_plist.name}")
        kpi_plist = _kpi_launch_agent_path()
        if kpi_plist.exists():
            _run(["launchctl", "unload", str(kpi_plist)])  # ignore (not loaded)
            kpi_plist.unlink()
            print(f"OK: removed {kpi_plist.name}")
        else:
            print(f"NOT_INSTALLED: {kpi_plist.name}")
        finance_plist = _finance_launch_agent_path()
        if finance_plist.exists():
            _run(["launchctl", "unload", str(finance_plist)])  # ignore (not loaded)
            finance_plist.unlink()
            print(f"OK: removed {finance_plist.name}")
        else:
            print(f"NOT_INSTALLED: {finance_plist.name}")
        capture_plist = _capture_launch_agent_path()
        if capture_plist.exists():
            _run(["launchctl", "unload", str(capture_plist)])  # ignore (not loaded)
            capture_plist.unlink()
            print(f"OK: removed {capture_plist.name}")
        else:
            print(f"NOT_INSTALLED: {capture_plist.name}")

        prep_plist = _prep_launch_agent_path()
        if prep_plist.exists():
            _run(["launchctl", "unload", str(prep_plist)])  # ignore (not loaded)
            prep_plist.unlink()
            print(f"OK: removed {prep_plist.name}")
        else:
            print(f"NOT_INSTALLED: {prep_plist.name}")
        note_plist = _note_launch_agent_path()
        if note_plist.exists():
            _run(["launchctl", "unload", str(note_plist)])  # ignore (not loaded)
            note_plist.unlink()
            print(f"OK: removed {note_plist.name}")
        else:
            print(f"NOT_INSTALLED: {note_plist.name}")
        return 0
    for skill in SKILLS:
        task = WIN_TASK.format(skill=skill)
        res = _run(["schtasks", "/delete", "/tn", task, "/f"])
        _vbs_path(task).unlink(missing_ok=True)
        print(f"OK: removed {task}" if res.returncode == 0 else f"NOT_INSTALLED: {task}")
    for briefing in DIGEST_BRIEFING_DEFAULTS:
        _remove_digest_job(platform, briefing)
    res = _run(["schtasks", "/delete", "/tn", WATCH_TASK, "/f"])
    _vbs_path(WATCH_TASK).unlink(missing_ok=True)
    print(f"OK: removed {WATCH_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {WATCH_TASK}")
    res = _run(["schtasks", "/delete", "/tn", PREP_TASK, "/f"])
    _vbs_path(PREP_TASK).unlink(missing_ok=True)
    print(f"OK: removed {PREP_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {PREP_TASK}")
    res = _run(["schtasks", "/delete", "/tn", NOTE_TASK, "/f"])
    _vbs_path(NOTE_TASK).unlink(missing_ok=True)
    print(f"OK: removed {NOTE_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {NOTE_TASK}")

    res = _run(["schtasks", "/delete", "/tn", KPI_TASK, "/f"])
    _vbs_path(KPI_TASK).unlink(missing_ok=True)
    print(f"OK: removed {KPI_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {KPI_TASK}")
    res = _run(["schtasks", "/delete", "/tn", FINANCE_TASK, "/f"])
    _vbs_path(FINANCE_TASK).unlink(missing_ok=True)
    print(f"OK: removed {FINANCE_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {FINANCE_TASK}")
    res = _run(["schtasks", "/delete", "/tn", CAPTURE_TASK, "/f"])
    _vbs_path(CAPTURE_TASK).unlink(missing_ok=True)
    print(f"OK: removed {CAPTURE_TASK}" if res.returncode == 0
          else f"NOT_INSTALLED: {CAPTURE_TASK}")
    try:
        _vbs_dir().rmdir()  # best-effort: leave no empty scheduler/ behind
    except OSError:
        pass
    return 0


def job_states(platform: str) -> list:
    """What the OS scheduler actually holds, as data. Read-only.

    Two callers: `cmd_status` prints it, and the vault audit scores Cadence
    from it. The audit needed evidence rather than a printed page, and
    inferring "the jobs are installed" from a briefing's file date is exactly
    the guess the evidence rubric exists to remove.

    Every row is `{label, kind, name, state, path}` with `state` one of
    `loaded`, `on disk, not loaded`, `not installed`, `installed` (Windows has
    no loaded/unloaded distinction), or `unsupported`.
    """
    from config_loader import DIGEST_BRIEFING_DEFAULTS

    rows = []
    if platform == "darwin":
        loaded = _run(["launchctl", "list"]).stdout
        for skill in SKILLS:
            label = MAC_LABEL.format(skill=skill)
            plist = _launch_agent_path(skill)
            state = "loaded" if label in loaded else ("on disk, not loaded" if plist.exists()
                                                      else "not installed")
            rows.append({"label": label, "kind": "skill", "name": skill,
                         "state": state, "path": str(plist)})
        for briefing in DIGEST_BRIEFING_DEFAULTS:
            label = DIGEST_MAC_LABEL.format(briefing=briefing)
            plist = _digest_launch_agent_path(briefing)
            state = "loaded" if label in loaded else ("on disk, not loaded" if plist.exists()
                                                      else "not installed")
            rows.append({"label": label, "kind": "digest", "name": briefing,
                         "state": state, "path": str(plist)})
        watch_plist = _watch_launch_agent_path()
        rows.append({"label": WATCH_LABEL, "kind": "watch", "name": "job-watch",
                     "state": ("loaded" if WATCH_LABEL in loaded else
                               "on disk, not loaded" if watch_plist.exists()
                               else "not installed"),
                     "path": str(watch_plist)})
        note_plist = _note_launch_agent_path()
        rows.append({"label": NOTE_LABEL, "kind": "note", "name": "note",
                     "state": ("loaded" if NOTE_LABEL in loaded else
                               "on disk, not loaded" if note_plist.exists()
                               else "not installed"),
                     "path": str(note_plist)})
        finance_plist = _finance_launch_agent_path()
        rows.append({"label": FINANCE_LABEL, "kind": "finance",
                     "name": "finance-email",
                     "state": ("loaded" if FINANCE_LABEL in loaded else
                               "on disk, not loaded" if finance_plist.exists()
                               else "not installed"),
                     "path": str(finance_plist)})
        capture_plist = _capture_launch_agent_path()
        rows.append({"label": CAPTURE_LABEL, "kind": "capture",
                     "name": "contact-capture",
                     "state": ("loaded" if CAPTURE_LABEL in loaded else
                               "on disk, not loaded" if capture_plist.exists()
                               else "not installed"),
                     "path": str(capture_plist)})
    elif platform == "win32":
        for skill in SKILLS:
            task = WIN_TASK.format(skill=skill)
            res = _run(["schtasks", "/query", "/tn", task])
            rows.append({"label": task, "kind": "skill", "name": skill,
                         "state": "installed" if res.returncode == 0 else "not installed",
                         "path": ""})
        for briefing in DIGEST_BRIEFING_DEFAULTS:
            task = DIGEST_WIN_TASK.format(briefing=briefing)
            res = _run(["schtasks", "/query", "/tn", task])
            rows.append({"label": task, "kind": "digest", "name": briefing,
                         "state": "installed" if res.returncode == 0 else "not installed",
                         "path": ""})
        res = _run(["schtasks", "/query", "/tn", WATCH_TASK])
        rows.append({"label": WATCH_TASK, "kind": "watch", "name": "job-watch",
                     "state": "installed" if res.returncode == 0
                              else "not installed", "path": ""})
        res = _run(["schtasks", "/query", "/tn", NOTE_TASK])
        rows.append({"label": NOTE_TASK, "kind": "note", "name": "note",
                     "state": "installed" if res.returncode == 0
                              else "not installed", "path": ""})
        res = _run(["schtasks", "/query", "/tn", CAPTURE_TASK])
        rows.append({"label": CAPTURE_TASK, "kind": "capture",
                     "name": "contact-capture",
                     "state": "installed" if res.returncode == 0
                              else "not installed", "path": ""})
    else:
        rows.append({"label": "", "kind": "", "name": "",
                     "state": "unsupported", "path": ""})
    return rows


def cmd_status(platform: str) -> int:
    rows = job_states(platform)
    if rows and rows[0]["state"] == "unsupported":
        print("UNSUPPORTED: no scheduler on this OS")
    elif platform == "darwin":
        for row in rows:
            print(f"{row['label']}: {row['state']} ({row['path']})")
    else:
        for row in rows:
            print(f"{row['label']}: {row['state']}")
    try:
        print(f"logs_dir: {_logs_dir()}")
    except Exception:
        print("logs_dir: unresolved (no config)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    sub.add_parser("sync-digests")
    args = parser.parse_args(argv)
    dispatch = {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status,
                "sync-digests": cmd_sync_digests}
    return dispatch[args.command](sys.platform)


if __name__ == "__main__":
    sys.exit(main())
