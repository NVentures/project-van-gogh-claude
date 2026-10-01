"""
scheduler_setup.py — install / uninstall / status for the background scheduler.

Every job this product schedules is a row in `app/job_registry.py`. This module
turns a row into what each OS actually wants, and nothing here knows the name
of any particular job:

- macOS: a launchd LaunchAgent per job (~/Library/LaunchAgents/).
- Windows: a Task Scheduler task per job (Register-ScheduledTask via a
  PowerShell child, which keeps -StartWhenAvailable — a missed run fires when
  the machine wakes, mirroring launchd). Each task launches through a wscript
  `.vbs` in `~/.config/van-gogh/scheduler/` rather than cmd.exe directly, so
  no console window flashes at fire time (see _vbs_content).
- Linux: no scheduler is installed; skills still work invoked manually.

Four renderers do all of it: `_plist_for`, `_inner_for`, `_task_ps_for` and
`_plist_path`, and four commands loop over the table: `install`, `uninstall`,
`status` and `sync-digests`. They used to be eight near-identical functions
each, one per job, and the duplication was not cosmetic: the copies drifted,
and every drift was silent. `job_states` never reported the prep poller on
macOS, nor the prep poller or the finance brief on Windows, which meant
`job_watch` graded those jobs `not_scheduled` and never restarted them; the
Note tick was installed by one function and graded by none.

`tests/test_scheduler_golden.py` freezes exactly what these renderers must
produce, and every literal in it was written against the hand-written
functions they replaced.

Jobs that run a skill go through `app/skill_run.py` rather than spawning
`claude -p` themselves, so an unattended run gets the retries, the failure
ledger, and the CLI freshness check (app/claude_update.py): a `claude` build
too old for the current model 400s every scheduled job on the machine until
someone notices.

`install` installs every job the user has turned on and removes the rest, the
four emailed briefings included. `sync-digests` is the same code path narrowed
to those four, kept as its own subcommand because
/van-gogh:update-digest-preferences calls it by name after every config
change.

Deliberately outside the table: `LEGACY_SKILLS`, `LEGACY_TASK_NAMES` and
`remove_legacy_jobs`, which are the anti-registry: names that must only ever
be deleted, never installed.
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


# ── The generic renderers ────────────────────────────────────────────────────
#
# One implementation each of "what does this job's plist / Windows command line
# / registration PowerShell look like", driven by a `job_registry.Job` rather
# than by eight near-identical hand-written functions. Everything below them in
# this file used to exist once per job; `tests/test_scheduler_golden.py` freezes
# what they must produce, and every literal in it was written against the
# hand-written versions these replaced.


class _Ctx:
    """The four facts every renderer needs, resolved once per command.

    A frozen bundle rather than four arguments threaded through every call:
    the old code passed `(repo, python, claude, logs_dir)` positionally into
    twenty-odd functions, and a job that took them in a different order was a
    silent mis-render waiting to happen.
    """

    __slots__ = ("repo", "python", "claude", "logs")

    def __init__(self, repo: str, python: str, claude: str, logs: Path):
        self.repo = repo
        self.python = python
        self.claude = claude
        self.logs = logs


def run_ledger_scheduled_env() -> str:
    """The env var marking an OS-started run (see run_ledger.SCHEDULED_ENV)."""
    import run_ledger
    return run_ledger.SCHEDULED_ENV


def _job_command(job, ctx: _Ctx) -> str:
    """The shell command one run of this job executes (macOS form).

    `VAN_GOGH_SCHEDULED=1` marks a run the OS started, which is what lets a
    briefing wait for the morning's filing jobs on a catch-up wake without a
    hand-run briefing ever waiting for anything.
    """
    script = str(Path(ctx.repo) / "app" / job.script)
    parts = [f'cd "{ctx.repo}" &&', "VAN_GOGH_SCHEDULED=1",
             f'"{ctx.python}"', f'"{script}"']
    parts.extend(job.resolved_args())
    if job.needs_claude:
        parts.append(f'--claude "{ctx.claude}"')
    return " ".join(parts)


def _schedule_xml(cadence) -> str:
    """The plist block that says when. launchd's two shapes are not the same.

    A bare `<dict>` is one slot and an `<array>` is a list of them. The daily
    4:30 AM skills render the bare form; everything weekly renders the array,
    one dict per day. Rendering the daily job as a one-element array would
    still work, which is exactly why the distinction is preserved rather than
    normalized: "still works" is not "unchanged", and normalizing it would
    rewrite a file on every user's machine.
    """
    if cadence.kind == "interval":
        # A zero interval is a job that is not on a timer at all: it starts at
        # login and stays up. That is the Workbench, and it is the only one.
        parts = []
        if cadence.interval_min:
            parts.append(f"  <key>StartInterval</key>\n"
                         f"  <integer>{cadence.interval_min * 60}</integer>")
        if cadence.run_at_load:
            parts.append("  <key>RunAtLoad</key>\n  <true/>")
        if cadence.keep_alive:
            parts.append("  <key>KeepAlive</key>\n  <true/>")
        return "\n".join(parts)
    if not cadence.days:
        return ("  <key>StartCalendarInterval</key>\n"
                "  <dict>\n"
                f"    <key>Hour</key><integer>{cadence.hour}</integer>\n"
                f"    <key>Minute</key><integer>{cadence.minute}</integer>\n"
                "  </dict>")
    intervals = "\n".join(
        f"    <dict><key>Weekday</key>"
        f"<integer>{LAUNCHD_WEEKDAY[d]}</integer>"
        f"<key>Hour</key><integer>{cadence.hour}</integer>"
        f"<key>Minute</key><integer>{cadence.minute}</integer></dict>"
        for d in cadence.days)
    return ("  <key>StartCalendarInterval</key>\n  <array>\n"
            f"{intervals}\n  </array>")


def _plist_for(job, ctx: _Ctx, cadence=None) -> str:
    """This job's launchd LaunchAgent, in full."""
    cadence = cadence if cadence is not None else job.resolved_cadence()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{job.mac_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{escape(_job_command(job, ctx))}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(ctx.repo)}</string>
{_schedule_xml(cadence)}
  <key>StandardOutPath</key>
  <string>{escape(str(ctx.logs / job.log_name))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(ctx.logs / job.err_name))}</string>
</dict>
</plist>
"""


def _inner_for(job, ctx: _Ctx) -> str:
    """The cmd command line one run executes, inside the .vbs wrapper."""
    script = str(Path(ctx.repo) / "app" / job.script)
    # `set VAR=1&&` with NO space before the `&&`. cmd.exe takes everything
    # between `=` and the separator literally, so `set VAR=1 && ...` assigns
    # the string "1 " -- with a trailing space -- and every `== "1"` test
    # downstream then fails. The gate would be dead on Windows and nowhere
    # else, which is the kind of thing nobody finds for a year.
    parts = [f'set {run_ledger_scheduled_env()}=1&&', f'"{ctx.python}"',
             f'"{script}"']
    parts.extend(job.resolved_args())
    if job.needs_claude:
        parts.append(f'--claude "{ctx.claude}"')
    parts.append(f'1>> "{ctx.logs / job.log_name}"')
    parts.append(f'2>> "{ctx.logs / job.err_name}"')
    return " ".join(parts)


def _trigger_ps(cadence) -> str:
    """The PowerShell that builds this job's trigger.

    Three shapes, and the differences are real. An interval job has no
    Task Scheduler equivalent at all, so it gets a daily trigger carrying a
    repetition of indefinite duration. A daily clock job spells its time with
    a literal `am`. A weekly one names its days.
    """
    if cadence.kind == "interval" and not cadence.interval_min:
        # At logon, and nothing else: the Workbench is a server, not a tick.
        return "$trigger = New-ScheduledTaskTrigger -AtLogon; "
    if cadence.kind == "interval":
        return (f"$trigger = New-ScheduledTaskTrigger -Daily -At {cadence.anchor}; "
                f"$trigger.Repetition = (New-ScheduledTaskTrigger -Once "
                f"-At {cadence.anchor} -RepetitionInterval "
                f"(New-TimeSpan -Minutes {cadence.interval_min}) "
                "-RepetitionDuration ([TimeSpan]::MaxValue)).Repetition; ")
    if not cadence.days:
        return (f"$trigger = New-ScheduledTaskTrigger -Daily "
                f"-At {cadence.hour}:{cadence.minute:02d}am; ")
    return (f"$trigger = New-ScheduledTaskTrigger -Weekly "
            f"-DaysOfWeek {cadence.windows_days} "
            f"-At {cadence.hour}:{cadence.minute:02d}; ")


def _task_ps_for(job, ctx: _Ctx, vbs: Path, cadence=None) -> str:
    """The PowerShell command that registers this job as a scheduled task."""
    cadence = cadence if cadence is not None else job.resolved_cadence()
    argument = f'//B //Nologo "{vbs}"'
    return (
        _trigger_ps(cadence)
        + "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
        + f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' "
          f"-Argument {_ps_quote(argument)} "
          f"-WorkingDirectory {_ps_quote(ctx.repo)}; "
        + f"Register-ScheduledTask -TaskName {_ps_quote(job.win_task)} "
          f"-Action $action -Trigger $trigger -Settings $settings "
          f"-Description {_ps_quote(job.description)} -Force | Out-Null"
    )


def _plist_path(job) -> Path:
    """Where this job's LaunchAgent lives."""
    return Path.home() / "Library" / "LaunchAgents" / f"{job.mac_label}.plist"


def _digest_python() -> str:
    """The interpreter to embed in digest jobs: the managed per-user venv when
    it exists (survives dev checkouts running sync), else this interpreter."""
    import user_state
    venv = user_state.state_dir() / "venv"
    candidate = (venv / "Scripts" / "python.exe") if sys.platform == "win32" \
        else (venv / "bin" / "python")
    return str(candidate) if candidate.exists() else sys.executable


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
            # Its own path helper, not the registry's: these names exist only
            # to be deleted and must never be installable, which is exactly
            # why they are not rows in the table.
            plist = (Path.home() / "Library" / "LaunchAgents"
                     / f"{MAC_LABEL.format(skill=skill)}.plist")
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


def _install_one(job, ctx: _Ctx, platform: str) -> bool:
    """Install one job at its resolved cadence. True when it ended right.

    Raises nothing: a cadence the config no longer describes is the caller's
    problem to report, because the response to it is to REMOVE the job rather
    than leave one running on a schedule nobody wrote.
    """
    cadence = job.resolved_cadence()
    if platform == "darwin":
        plist = _plist_path(job)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(_plist_for(job, ctx, cadence=cadence), encoding="utf-8")
        _run(["launchctl", "unload", str(plist)])   # ignore (not loaded yet)
        res = _run(["launchctl", "load", str(plist)])
        if res.returncode != 0:
            print(f"ERROR: launchctl load failed for {plist.name}: "
                  f"{res.stderr.strip()}")
            return False
        # "loaded" on macOS, because that is what launchctl calls it and what
        # the user sees in `launchctl list`. A digest says "scheduled", which
        # is the word /van-gogh:update-digest-preferences has always printed.
        verb = "scheduled" if job.success_verb == "scheduled" else "loaded"
        print(f"OK: {job.mac_label} {verb} ({cadence.describe()})")
        return True

    vbs = _vbs_path(job.win_task)
    _write_vbs(vbs, _vbs_content(_inner_for(job, ctx)))
    res = _run(["powershell", "-NoProfile", "-Command",
                _task_ps_for(job, ctx, vbs, cadence=cadence)])
    if res.returncode != 0:
        print(f"ERROR: Register-ScheduledTask failed for {job.win_task}: "
              f"{res.stderr.strip()}")
        return False
    verify = _run(["schtasks", "/query", "/tn", job.win_task])
    if verify.returncode != 0:
        print(f"ERROR: {job.win_task} did not appear in schtasks /query")
        return False
    print(f"OK: {job.win_task} {job.success_verb} ({cadence.describe()})")
    return True


def _remove_one(job, platform: str, quiet_missing: bool = False,
                note: str = "") -> None:
    """Remove one job, whether or not it is there. Never fails a command.

    `note` is the reason, printed in the user's words when a feature was
    turned off rather than uninstalled: "removed com.monet.finance-email (the
    weekly finance brief is off)".
    """
    suffix = f" ({note})" if note else ""
    if platform == "darwin":
        plist = _plist_path(job)
        if not plist.exists():
            if not quiet_missing:
                print(f"NOT_INSTALLED: {plist.name}")
            return
        _run(["launchctl", "unload", str(plist)])   # ignore (not loaded)
        plist.unlink(missing_ok=True)
        print(f"OK: removed {plist.name}{suffix}")
        return
    res = _run(["schtasks", "/delete", "/tn", job.win_task, "/f"])
    _vbs_path(job.win_task).unlink(missing_ok=True)
    if res.returncode == 0:
        print(f"OK: removed {job.win_task}{suffix}")
    elif not quiet_missing:
        print(f"NOT_INSTALLED: {job.win_task}")


def _apply(job, ctx: _Ctx, platform: str) -> bool:
    """Install this job or remove it, to match what the user has turned on.

    The one rule, in one place. Removing on disable is the half that matters:
    a user who turns a feature off and still gets its email has been ignored.
    Both halves used to be written out per job, per platform, per command,
    which is how the Note tick came to be installed by one function and graded
    by none.

    A cadence the config cannot express removes the job and fails the command,
    rather than raising. That generalizes what the digest sync path already
    did: before this, a corrupt `kpi.time` raised out of `_kpi_cadence` and
    crashed the whole install, taking every other job with it.
    """
    if not job.is_enabled():
        _remove_one(job, platform, quiet_missing=True, note=job.off_note)
        return True
    try:
        job.resolved_cadence()
    except Exception as e:                                       # noqa: BLE001
        print(f"ERROR: {job.key}: {e}; job removed until the config is fixed")
        _remove_one(job, platform, quiet_missing=True)
        return False
    return _install_one(job, ctx, platform)


def _ctx_for_commands() -> _Ctx:
    """The four facts every renderer needs, resolved once."""
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    return _Ctx(_repo_root(), _digest_python(), claude_bin(), logs)


def cmd_install(platform: str) -> int:
    """Install every job the user has turned on; remove the rest.

    One loop over the registry, so "install every schedulable job" is true by
    construction. It used to install the two background skills, the watcher
    and four opt-in jobs, and leave the four briefings to `sync-digests` --
    which meant a user who finished install and stopped had two 4:30 AM ingest
    jobs and no briefings at all.

    Never returns at the first failure. A machine where one job cannot
    register is exactly the machine that needs the other eleven installed, and
    an early return here once meant that turning prep emails off also skipped
    every job registered after it.
    """
    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler is installed on this OS; "
              "skills still work when invoked manually")
        return 0
    import job_registry
    try:
        ctx = _ctx_for_commands()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1

    remove_legacy_jobs(platform)

    rc = 0
    for job in job_registry.jobs():
        if not _apply(job, ctx, platform):
            rc = 1
    return rc


def cmd_sync_digests(platform: str) -> int:
    """The install command, restricted to the four emailed briefings.

    Kept as its own subcommand, with its own output vocabulary, because
    `/van-gogh:update-digest-preferences` calls it by name after every config
    change. Since `install` now covers the digest rows too, this is the same
    code path narrowed to `kind == "digest"` rather than a second
    implementation of it.
    """
    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler on this OS; digests cannot be scheduled "
              "(run app/digest_send.py manually or via cron)")
        return 0
    import job_registry
    from config_loader import DIGEST_BRIEFING_DEFAULTS, cfg, digest_enabled

    try:
        enabled_all = digest_enabled()
        raw_keys = set((cfg().get("digest", {}) or {}).get("briefings", {}) or {})
    except Exception as e:                                       # noqa: BLE001
        print(f"ERROR: cannot read config.json: {e}")
        return 1
    for key in sorted(raw_keys - set(DIGEST_BRIEFING_DEFAULTS)):
        print(f"WARN: unknown briefing {key!r} in digest.briefings — ignored "
              f"(known: {', '.join(sorted(DIGEST_BRIEFING_DEFAULTS))})")

    rows = [j for j in job_registry.jobs() if j.kind == "digest"]
    if not enabled_all or not any(j.is_enabled() for j in rows):
        for job in rows:
            _remove_one(job, platform, quiet_missing=True)
        print("OK: digests disabled — no digest jobs scheduled")
        return 0

    try:
        ctx = _ctx_for_commands()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    rc = 0
    for job in rows:
        if not _apply(job, ctx, platform):
            rc = 1
    return rc


def cmd_uninstall(platform: str) -> int:
    """Remove every job this product installs, enabled or not.

    Every row, never only the enabled ones: a job whose feature was turned off
    after it was installed is exactly the job that would otherwise be left
    behind firing forever.
    """
    if platform not in ("darwin", "win32"):
        print("UNSUPPORTED: no scheduler is installed on this OS; nothing to remove")
        return 0
    import job_registry

    remove_legacy_jobs(platform)
    for job in job_registry.jobs():
        _remove_one(job, platform)
    if platform == "win32":
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

    One loop over the registry, on both platforms. It used to be four
    hand-written blocks that had each drifted: the prep poller was unreported
    on macOS, and the prep poller and finance brief were unreported on
    Windows. That is not a cosmetic gap -- `job_watch.evaluate` grades a job
    whose label it cannot find as `not_scheduled` and never kicks it, so an
    unreported job is an ungraded one. `tests/test_job_status_parity.py`
    states that failure in its own terms.

    Reports every row the registry knows, installed or not, rather than only
    the ones currently enabled: "not installed" is the answer to "is my
    scorecard running", and a row that vanished from the list entirely would
    make that question unanswerable.
    """
    import job_registry

    if platform not in ("darwin", "win32"):
        return [{"label": "", "kind": "", "name": "", "state": "unsupported",
                 "path": ""}]

    rows = []
    loaded = _run(["launchctl", "list"]).stdout if platform == "darwin" else ""
    for job in job_registry.jobs():
        if platform == "darwin":
            plist = _plist_path(job)
            state = ("loaded" if job.mac_label in loaded else
                     "on disk, not loaded" if plist.exists() else
                     "not installed")
            rows.append({"label": job.mac_label, "kind": job.state_name,
                         "name": job.key, "state": state, "path": str(plist)})
        else:
            res = _run(["schtasks", "/query", "/tn", job.win_task])
            rows.append({"label": job.win_task, "kind": job.state_name,
                         "name": job.key,
                         "state": ("installed" if res.returncode == 0
                                   else "not installed"),
                         "path": ""})
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
