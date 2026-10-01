"""The golden corpus: exactly what every scheduled job renders, frozen.

This file exists to make a refactor of `scheduler_setup` provable rather than
plausible. It renders every job's launchd plist, its Windows `.vbs` inner
command line and its Task Scheduler PowerShell under one fixed macOS context
and one fixed Windows context, and asserts each against a literal written out
here in full.

Written and committed green against the hand-written renderers, BEFORE the
job registry replaced them. That commit is the contract: while the registry
was built alongside the old code, any diff in this file was a behaviour change
nobody asked for. It stays forever afterwards, because it is what makes adding
the next job safe -- a new row that quietly changes an existing job's plist
fails here by name.

Literals, not a fixture file, and not a snapshot the suite can regenerate: a
golden file with a `--update` flag is a golden file that gets updated by
reflex on the morning it goes red. Changing a string here has to be typed on
purpose, which is the point.

Three whitespace facts are load-bearing and easy to lose in a rewrite, so each
has a named test below rather than only living inside a big literal:

* `StartCalendarInterval` is a bare `<dict>` for the daily 4:30 AM skills and
  an `<array>` of per-day dicts for everything weekly;
* the Windows daily trigger is `-Daily -At 4:30am` (that literal `am`) while
  every weekly one is `-Weekly -DaysOfWeek X -At H:MM`;
* the repetition anchor is `12:05am` for the watcher and the prep poller but
  `12:10am` for the Note tick.

Paths are built with `Path` here exactly as the renderers build them, so the
same literals hold on a Windows runner, where `Path("C:/repo") / "app"` joins
with a backslash. A hardcoded "/repo/app/skill_run.py" would pass on macOS and
fail on Windows for a reason that has nothing to do with the scheduler.
"""

from pathlib import Path

import pytest

import job_registry
import scheduler_setup as s

# ── The two fixed contexts ───────────────────────────────────────────────────

MAC_REPO = "/repo"
MAC_PY = "/venv/bin/python"
MAC_CLAUDE = "/bin/claude"
MAC_LOGS = Path("/logs")

WIN_REPO = r"C:\repo"
WIN_PY = r"C:\venv\python.exe"
WIN_CLAUDE = r"C:\bin\claude.cmd"
WIN_LOGS = Path(r"C:\logs")
WIN_VBS = Path(r"C:\state\scheduler\Task.vbs")

MAC = s._Ctx(MAC_REPO, MAC_PY, MAC_CLAUDE, MAC_LOGS)
WIN = s._Ctx(WIN_REPO, WIN_PY, WIN_CLAUDE, WIN_LOGS)

_C = job_registry.Cadence


def _row(key, **overrides):
    """One registry row, with its config-driven parts pinned.

    The corpus freezes RENDERING. Contact capture is the one row whose
    arguments come from config, so its lookback window is pinned here rather
    than read, or these literals would move when someone edited a default.
    """
    job = job_registry.by_key(key)
    assert job is not None, f"no registry row named {key!r}"
    fields = {**job.__dict__, **overrides}
    if key == "contact-capture":
        fields["args"] = ("--apply", "--days", str(CAPTURE_DAYS))
        fields["args_from_config"] = None
    return job_registry.Job(**fields)

# Cadences pinned here rather than read from config: this file freezes the
# RENDERING, and a config-driven time would make the corpus move when someone
# edits a default. The times below are the ones the product shipped when this
# was frozen; §4's new defaults change config, never these literals.
FINANCE_WHEN = ("monday", 7, 0)
CAPTURE_WHEN = ("sunday", 5, 0)
CAPTURE_DAYS = 30
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def _script(repo: str, name: str) -> str:
    """A script path built the way the renderers build it (platform joins)."""
    return str(Path(repo) / "app" / name)


def _log(logs: Path, name: str) -> str:
    return str(logs / name)


def _plist(label: str, command: str, repo: str, schedule: str,
           out: str, err: str) -> str:
    """The one plist shape every job renders, with its schedule block spliced.

    A helper rather than twelve copies of forty lines: the whitespace that
    matters is inside `schedule`, and burying it in repeated boilerplate is how
    a diff in it goes unread.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>{command}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{repo}</string>
{schedule}
  <key>StandardOutPath</key>
  <string>{out}</string>
  <key>StandardErrorPath</key>
  <string>{err}</string>
</dict>
</plist>
"""


def _daily(hour: int, minute: int) -> str:
    """The bare <dict> form. Only the two daily skill jobs use it."""
    return ("  <key>StartCalendarInterval</key>\n"
            "  <dict>\n"
            f"    <key>Hour</key><integer>{hour}</integer>\n"
            f"    <key>Minute</key><integer>{minute}</integer>\n"
            "  </dict>")


def _weekly(weekday: int, hour: int, minute: int) -> str:
    """The <array>-of-dicts form, one dict per day."""
    return ("  <key>StartCalendarInterval</key>\n"
            "  <array>\n"
            f"    <dict><key>Weekday</key><integer>{weekday}</integer>"
            f"<key>Hour</key><integer>{hour}</integer>"
            f"<key>Minute</key><integer>{minute}</integer></dict>\n"
            "  </array>")


def _login_item() -> str:
    """The server form: at login, kept alive, on no timer at all."""
    return ("  <key>RunAtLoad</key>\n  <true/>\n"
            "  <key>KeepAlive</key>\n  <true/>")


def _interval(seconds: int) -> str:
    """The poller form: StartInterval plus RunAtLoad."""
    return (f"  <key>StartInterval</key>\n  <integer>{seconds}</integer>\n"
            "  <key>RunAtLoad</key>\n  <true/>")


# ── macOS plists ─────────────────────────────────────────────────────────────

# The two background skills no longer share a slot. They both ran at 4:30,
# which meant two headless renders against the same vault at the same instant;
# twenty minutes apart costs nothing and removes the collision.
SKILL_TIMES = {"meeting-ingest": (4, 30), "ingest-workspace": (4, 50)}


@pytest.mark.parametrize("skill", list(s.SKILLS))
def test_golden_skill_plist(skill):
    hour, minute = SKILL_TIMES[skill]
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "skill_run.py")}" {skill} '
               f'--claude "{MAC_CLAUDE}"')
    assert s._plist_for(_row(skill), MAC) == _plist(
        f"com.monet.{skill}", command, MAC_REPO, _daily(hour, minute),
        _log(MAC_LOGS, f"{skill}.log"), _log(MAC_LOGS, f"{skill}.err"))


def test_golden_watch_plist():
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "job_watch.py")}"')
    assert s._plist_for(_row("job-watch"), MAC) == _plist(
        "com.monet.job-watch", command, MAC_REPO, _interval(30 * 60),
        _log(MAC_LOGS, "job-watch.log"), _log(MAC_LOGS, "job-watch.err"))


def test_golden_prep_plist():
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "prep_send.py")}" --claude "{MAC_CLAUDE}"')
    assert s._plist_for(_row("prep-email"), MAC) == _plist(
        "com.monet.prep-email", command, MAC_REPO, _interval(15 * 60),
        _log(MAC_LOGS, "prep-email.log"), _log(MAC_LOGS, "prep-email.err"))


def test_golden_note_plist():
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "note_send.py")}" --claude "{MAC_CLAUDE}"')
    assert s._plist_for(_row("note"), MAC) == _plist(
        "com.monet.note", command, MAC_REPO, _interval(15 * 60),
        _log(MAC_LOGS, "note.log"), _log(MAC_LOGS, "note.err"))


def test_golden_finance_plist():
    day, hour, minute = FINANCE_WHEN
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "finance_send.py")}" --claude "{MAC_CLAUDE}"')
    assert s._plist_for(_row("finance-email"), MAC, cadence=_C(days=(day,), hour=hour, minute=minute)) == _plist(
        "com.monet.finance-email", command, MAC_REPO, _weekly(1, hour, minute),
        _log(MAC_LOGS, "finance-email.log"), _log(MAC_LOGS, "finance-email.err"))


def test_golden_capture_plist():
    day, hour, minute = CAPTURE_WHEN
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "contact_capture.py")}" '
               f'--apply --days {CAPTURE_DAYS}')
    assert s._plist_for(_row("contact-capture"), MAC, cadence=_C(days=(day,), hour=hour, minute=minute)) == _plist(
        "com.monet.contact-capture", command, MAC_REPO, _weekly(0, hour, minute),
        _log(MAC_LOGS, "contact-capture.log"),
        _log(MAC_LOGS, "contact-capture.err"))


@pytest.mark.parametrize("briefing,days,hour,minute,weekdays", [
    ("morning-coffee", WEEKDAYS, 7, 0, [1, 2, 3, 4, 5]),
    ("afternoon-tea", WEEKDAYS, 13, 0, [1, 2, 3, 4, 5]),
    ("week", ["monday"], 7, 0, [1]),
    ("week-retro", ["friday"], 7, 0, [5]),
])
def test_golden_digest_plist(briefing, days, hour, minute, weekdays):
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "digest_send.py")}" {briefing} '
               f'--claude "{MAC_CLAUDE}"')
    intervals = "\n".join(
        f"    <dict><key>Weekday</key><integer>{d}</integer>"
        f"<key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>"
        for d in weekdays)
    schedule = ("  <key>StartCalendarInterval</key>\n  <array>\n"
                f"{intervals}\n  </array>")
    assert s._plist_for(_row(f"digest-{briefing}"), MAC, cadence=_C(days=tuple(days), hour=hour, minute=minute)) == _plist(
        f"com.monet.digest-{briefing}", command, MAC_REPO, schedule,
        _log(MAC_LOGS, f"digest-{briefing}.log"),
        _log(MAC_LOGS, f"digest-{briefing}.err"))


def test_golden_workbench_plist():
    """The one long-running job: at login, kept alive, never on a timer.

    Every other job in the product is fire-and-exit, so this is the only plist
    carrying KeepAlive and the only one with no StartInterval and no
    StartCalendarInterval. A StartInterval here would restart the server every
    N minutes on top of itself.
    """
    command = (f'cd "{MAC_REPO}" &amp;&amp; VAN_GOGH_SCHEDULED=1 "{MAC_PY}" '
               f'"{_script(MAC_REPO, "workbench_serve.py")}" --no-browser')
    plist = s._plist_for(_row("workbench"), MAC)
    assert plist == _plist(
        "com.monet.workbench", command, MAC_REPO, _login_item(),
        _log(MAC_LOGS, "workbench.log"), _log(MAC_LOGS, "workbench.err"))
    assert "StartInterval" not in plist
    assert "StartCalendarInterval" not in plist


def test_the_workbench_never_opens_a_browser_at_login():
    """A login item that opened a window would hijack every boot."""
    assert "--no-browser" in s._plist_for(_row("workbench"), MAC)
    assert "--no-browser" in s._inner_for(_row("workbench"), WIN)


# ── Windows .vbs inner command lines ─────────────────────────────────────────

@pytest.mark.parametrize("skill", list(s.SKILLS))
def test_golden_skill_inner(skill):
    assert s._inner_for(_row(skill), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "skill_run.py")}" {skill} '
        f'--claude "{WIN_CLAUDE}" '
        f'1>> "{_log(WIN_LOGS, f"{skill}.log")}" '
        f'2>> "{_log(WIN_LOGS, f"{skill}.err")}"')


def test_golden_watch_inner():
    assert s._inner_for(_row("job-watch"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "job_watch.py")}" '
        f'1>> "{_log(WIN_LOGS, "job-watch.log")}" '
        f'2>> "{_log(WIN_LOGS, "job-watch.err")}"')


def test_golden_prep_inner():
    assert s._inner_for(_row("prep-email"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "prep_send.py")}" '
        f'--claude "{WIN_CLAUDE}" '
        f'1>> "{_log(WIN_LOGS, "prep-email.log")}" '
        f'2>> "{_log(WIN_LOGS, "prep-email.err")}"')


def test_golden_note_inner():
    assert s._inner_for(_row("note"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "note_send.py")}" '
        f'--claude "{WIN_CLAUDE}" '
        f'1>> "{_log(WIN_LOGS, "note.log")}" '
        f'2>> "{_log(WIN_LOGS, "note.err")}"')


def test_golden_finance_inner():
    assert s._inner_for(_row("finance-email"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "finance_send.py")}" '
        f'--claude "{WIN_CLAUDE}" '
        f'1>> "{_log(WIN_LOGS, "finance-email.log")}" '
        f'2>> "{_log(WIN_LOGS, "finance-email.err")}"')


def test_golden_capture_inner():
    assert s._inner_for(_row("contact-capture"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "contact_capture.py")}" '
        f'--apply --days {CAPTURE_DAYS} '
        f'1>> "{_log(WIN_LOGS, "contact-capture.log")}" '
        f'2>> "{_log(WIN_LOGS, "contact-capture.err")}"')


def test_golden_workbench_inner():
    assert s._inner_for(_row("workbench"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "workbench_serve.py")}" --no-browser '
        f'1>> "{_log(WIN_LOGS, "workbench.log")}" '
        f'2>> "{_log(WIN_LOGS, "workbench.err")}"')


@pytest.mark.parametrize("briefing", ["morning-coffee", "afternoon-tea",
                                      "week", "week-retro"])
def test_golden_digest_inner(briefing):
    assert s._inner_for(_row(f"digest-{briefing}"), WIN) == (
        f'set VAN_GOGH_SCHEDULED=1&& "{WIN_PY}" "{_script(WIN_REPO, "digest_send.py")}" {briefing} '
        f'--claude "{WIN_CLAUDE}" '
        f'1>> "{_log(WIN_LOGS, f"digest-{briefing}.log")}" '
        f'2>> "{_log(WIN_LOGS, f"digest-{briefing}.err")}"')


# ── Windows registration PowerShell ──────────────────────────────────────────

_ARG = f"'//B //Nologo \"{WIN_VBS}\"'"
_SETTINGS = "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
_ACTION = (f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' "
           f"-Argument {_ARG} -WorkingDirectory '{WIN_REPO}'; ")


def _register(task: str, description: str) -> str:
    return (f"Register-ScheduledTask -TaskName '{task}' -Action $action "
            f"-Trigger $trigger -Settings $settings "
            f"-Description '{description}' -Force | Out-Null")


def _repeating(anchor: str, minutes: int) -> str:
    """A daily trigger carrying an indefinite repetition: "every N minutes"."""
    return (f"$trigger = New-ScheduledTaskTrigger -Daily -At {anchor}; "
            f"$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At {anchor} "
            f"-RepetitionInterval (New-TimeSpan -Minutes {minutes}) "
            "-RepetitionDuration ([TimeSpan]::MaxValue)).Repetition; ")


@pytest.mark.parametrize("skill", list(s.SKILLS))
def test_golden_skill_task_ps(skill):
    hour, minute = SKILL_TIMES[skill]
    assert s._task_ps_for(_row(skill), WIN, WIN_VBS) == (
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {hour}:{minute:02d}am; "
        + _SETTINGS + _ACTION
        + _register(f"VanGogh-{skill}",
                    f"Project Van Gogh: daily /van-gogh:{skill}"))


def test_golden_watch_task_ps():
    assert s._task_ps_for(_row("job-watch"), WIN, WIN_VBS) == (
        _repeating("12:05am", 30) + _SETTINGS + _ACTION
        + _register("VanGogh-job-watch",
                    "Project Van Gogh: re-runs scheduled jobs that failed "
                    "or never fired"))


def test_golden_prep_task_ps():
    assert s._task_ps_for(_row("prep-email"), WIN, WIN_VBS) == (
        _repeating("12:05am", 15) + _SETTINGS + _ACTION
        + _register("VanGogh-prep-email",
                    "Project Van Gogh: emails a meeting prep before each call"))


def test_golden_note_task_ps():
    assert s._task_ps_for(_row("note"), WIN, WIN_VBS) == (
        _repeating("12:10am", 15) + _SETTINGS + _ACTION
        + _register("VanGogh-note",
                    "Project Van Gogh: the watcher between briefings"))


def test_golden_finance_task_ps():
    day, hour, minute = FINANCE_WHEN
    assert s._task_ps_for(_row("finance-email"), WIN, WIN_VBS, cadence=_C(days=(day,), hour=hour, minute=minute)) == (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday "
        f"-At {hour}:{minute:02d}; " + _SETTINGS + _ACTION
        + _register("VanGogh-finance-email",
                    "Project Van Gogh: emails a weekly finance brief"))


def test_golden_capture_task_ps():
    day, hour, minute = CAPTURE_WHEN
    assert s._task_ps_for(_row("contact-capture"), WIN, WIN_VBS, cadence=_C(days=(day,), hour=hour, minute=minute)) == (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday "
        f"-At {hour}:{minute:02d}; " + _SETTINGS + _ACTION
        + _register("VanGogh-contact-capture",
                    "Project Van Gogh: saves people you correspond with "
                    "into Contacts"))


@pytest.mark.parametrize("briefing,days,hour,minute,dow", [
    ("morning-coffee", WEEKDAYS, 7, 0,
     "Monday,Tuesday,Wednesday,Thursday,Friday"),
    ("afternoon-tea", WEEKDAYS, 13, 0,
     "Monday,Tuesday,Wednesday,Thursday,Friday"),
    ("week", ["monday"], 7, 0, "Monday"),
    ("week-retro", ["friday"], 7, 0, "Friday"),
])
def test_golden_digest_task_ps(briefing, days, hour, minute, dow):
    assert s._task_ps_for(_row(f"digest-{briefing}"), WIN, WIN_VBS, cadence=_C(days=tuple(days), hour=hour, minute=minute)) == (
        f"$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {dow} "
        f"-At {hour}:{minute:02d}; " + _SETTINGS + _ACTION
        + _register(f"VanGogh-digest-{briefing}",
                    f"Project Van Gogh: emailed /van-gogh:{briefing} digest"))


def test_golden_workbench_task_ps():
    """Windows: an at-logon trigger, through the .vbs so no console flashes."""
    ps = s._task_ps_for(_row("workbench"), WIN, WIN_VBS)
    assert ps == (
        "$trigger = New-ScheduledTaskTrigger -AtLogon; " + _SETTINGS + _ACTION
        + _register("VanGogh-workbench",
                    "Project Van Gogh: the Workbench, running at login"))
    assert "-Execute 'wscript.exe'" in ps and "cmd.exe" not in ps


# ── LaunchAgent paths ────────────────────────────────────────────────────────

def test_golden_launch_agent_paths():
    """Every plist lands in ~/Library/LaunchAgents under its own label.

    The path and the label are two spellings of one name, and an install that
    writes one while uninstall reads the other leaves a job running forever.
    """
    agents = Path.home() / "Library" / "LaunchAgents"
    expected = {
        "meeting-ingest": "com.monet.meeting-ingest",
        "ingest-workspace": "com.monet.ingest-workspace",
        "job-watch": "com.monet.job-watch",
        "prep-email": "com.monet.prep-email",
        "note": "com.monet.note",
        "finance-email": "com.monet.finance-email",
        "contact-capture": "com.monet.contact-capture",
        "digest-morning-coffee": "com.monet.digest-morning-coffee",
        "digest-afternoon-tea": "com.monet.digest-afternoon-tea",
        "digest-week": "com.monet.digest-week",
        "digest-week-retro": "com.monet.digest-week-retro",
        "workbench": "com.monet.workbench",
        "calendar-stub-check": "com.monet.calendar-stub-check",
        "relationship-radar": "com.monet.relationship-radar",
        "five-fifteen": "com.monet.five-fifteen",
        "voice-calibration": "com.monet.voice-calibration",
        "voice-generator": "com.monet.voice-generator",
    }
    # Every row, not a hand-kept subset: a new job that forgot its path helper
    # is exactly the bug this freezes against.
    assert {j.key for j in job_registry.jobs()} == set(expected)
    for key, label in expected.items():
        assert s._plist_path(_row(key)) == agents / f"{label}.plist"


# ── The three whitespace facts, stated in their own terms ────────────────────

def test_daily_skills_use_a_bare_dict_and_weekly_jobs_use_an_array():
    """launchd's two StartCalendarInterval shapes, which are not interchangeable.

    A single dict is one slot; an array is a list of them. Rendering the daily
    skills as a one-element array would still work, which is exactly why this
    is asserted: "still works" is not "unchanged", and a refactor that quietly
    normalizes one into the other has changed a file on every user's machine.
    """
    daily = s._plist_for(_row("meeting-ingest"), MAC)
    assert "<key>StartCalendarInterval</key>\n  <dict>" in daily
    assert "<array>\n    <dict><key>Weekday</key>" not in daily

    weekly = s._plist_for(_row("finance-email"), MAC,
                          cadence=_C(days=("monday",), hour=7, minute=0))
    assert "<key>StartCalendarInterval</key>\n  <array>" in weekly


def test_the_daily_windows_trigger_carries_a_literal_am():
    """`-At 4:30am`, not `-At 4:30`. Task Scheduler parses both, differently."""
    ps = s._task_ps_for(_row("meeting-ingest"), WIN, WIN_VBS)
    assert "-Daily -At 4:30am;" in ps
    assert "-Weekly" not in ps


def test_repetition_anchors_differ_between_the_pollers():
    """The Note tick anchors at 12:10am; the watcher and prep at 12:05am.

    Nothing depends on the difference, which is precisely the risk: it is the
    kind of detail a rewrite normalizes without noticing, and normalizing it
    rewrites a task on every Windows machine for no stated reason.
    """
    assert "-Daily -At 12:05am;" in s._task_ps_for(_row("job-watch"), WIN, WIN_VBS)
    assert "-Daily -At 12:05am;" in s._task_ps_for(_row("prep-email"), WIN, WIN_VBS)
    assert "-Daily -At 12:10am;" in s._task_ps_for(_row("note"), WIN, WIN_VBS)


def test_only_the_jobs_that_reach_a_model_carry_the_claude_path():
    """Who embeds `--claude` and who does not, frozen.

    launchd hands a job a PATH too sparse to resolve the CLI, so any job whose
    work reaches a model must carry the resolved binary. The watcher and
    contact capture are pure Python and must not: a `--claude` on one of those
    is an argument its script never declared.
    """
    for key in ("meeting-ingest", "prep-email", "note", "finance-email",
                "digest-week"):
        assert f'--claude "{MAC_CLAUDE}"' in s._plist_for(_row(key), MAC), key
    for key in ("job-watch", "contact-capture"):
        assert "--claude" not in s._plist_for(_row(key), MAC), key


def test_needs_claude_matches_what_each_script_accepts():
    """The registry's `needs_claude` flag against the scripts themselves.

    The flag decides whether `--claude <path>` is appended to a command line
    that runs unattended at 4:30 AM. Getting it wrong in either direction is
    silent until then: a job that needs the path cannot find the CLI, and a job
    that does not gets an argument its parser rejects.
    """
    import re
    app = Path(__file__).resolve().parent.parent / "app"
    for job in job_registry.jobs():
        src = (app / job.script).read_text(encoding="utf-8")
        accepts = bool(re.search(r'add_argument\(\s*["\']--claude["\']', src))
        assert accepts == job.needs_claude, (
            f"{job.key}: registry says needs_claude={job.needs_claude} but "
            f"{job.script} {'accepts' if accepts else 'does not accept'} --claude")
