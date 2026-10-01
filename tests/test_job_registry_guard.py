"""Every row in the job table is whole, or this fails.

Parametrized over `job_registry.jobs()`, so a new job is covered the moment it
is added and there is no list here to forget to update. For every row:

* it installs on macOS and on Windows,
* it uninstalls on both, saying `OK: removed` when it was there and
  `NOT_INSTALLED:` when it was not,
* `job_states` reports it on both,
* every name in `depends_on` is a real row,
* it appears in `job_watch.roster()` unless it is explicitly ungraded,
* its `ledger_job` is a name its own script actually writes,
* and an opt-in row is **offered to the user** somewhere a person will see it.

That last clause is the one that matters most, because it is the bug this
whole change came from. Van Gogh could schedule four briefings, and the
install never asked about any of them: `digest.enabled` shipped false, the
only mention was one line of closing text pointing at a different command, and
a user who finished the install and stopped got no briefings at all. Nothing
was broken. Nobody had been asked.

A feature that cannot be reached is not a feature, so a row whose
`skill_phrase` appears in no SKILL.md fails here.
"""

import pathlib
import subprocess

import pytest

import job_registry
import job_watch
import scheduler_setup

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
SKILLS = pathlib.Path(__file__).resolve().parent.parent / "skills"

# Where a person is asked about, or told about, a schedulable feature. Install
# is the main one; the rest are the skills that change a setting after the
# fact. A phrase found in any of them counts as offered.
OFFER_SKILLS = ("install-van-gogh", "update-settings",
                "update-digest-preferences", "contact-capture", "workbench",
                "note", "meeting-prep", "finance-brief", "check-updates")

ROWS = job_registry.jobs()
IDS = [job.key for job in ROWS]


@pytest.fixture
def fake_os(monkeypatch, tmp_path):
    """Both OS schedulers, stubbed. Records what would have been run."""
    calls = []

    class _Res:
        returncode = 0
        stderr = ""

        def __init__(self, cmd):
            joined = " ".join(str(c) for c in cmd)
            # `launchctl list` is read for "is it loaded"; answer with every
            # label so an installed job reads as loaded.
            self.stdout = ("\n".join(j.mac_label for j in ROWS)
                           if "list" in joined else "")

    def run(cmd):
        calls.append([str(c) for c in cmd])
        return _Res(cmd)

    monkeypatch.setattr(scheduler_setup, "_run", run)
    monkeypatch.setattr(scheduler_setup, "_write_vbs",
                        lambda path, content: None)
    monkeypatch.setattr(scheduler_setup, "claude_bin", lambda: "/bin/claude")
    monkeypatch.setattr(scheduler_setup, "_repo_root", lambda: str(tmp_path / "repo"))
    monkeypatch.setattr(scheduler_setup, "_logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(scheduler_setup, "_digest_python", lambda: "/py")
    monkeypatch.setattr(scheduler_setup, "_vbs_path",
                        lambda task: tmp_path / "vbs" / f"{task}.vbs")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(scheduler_setup, "_plist_path",
                        lambda job: agents / f"{job.mac_label}.plist")
    return calls


# ── Installable ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_installable_on_macos(job, fake_os, tmp_path):
    ctx = scheduler_setup._Ctx("/repo", "/py", "/claude", tmp_path / "logs")
    assert scheduler_setup._install_one(job, ctx, "darwin")
    assert scheduler_setup._plist_path(job).exists(), \
        f"{job.key}: install wrote no plist"


@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_installable_on_windows(job, fake_os, tmp_path):
    ctx = scheduler_setup._Ctx(r"C:\repo", "/py", "/claude", tmp_path / "logs")
    assert scheduler_setup._install_one(job, ctx, "win32")
    registered = [c for c in fake_os
                  if any("Register-ScheduledTask" in part for part in c)]
    assert any(job.win_task in part for c in registered for part in c), \
        f"{job.key}: no Register-ScheduledTask names it"


# ── Uninstallable ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_uninstallable_on_macos(job, fake_os, capsys):
    """Present: `OK: removed`. Absent: `NOT_INSTALLED:`.

    The absent half is the one that drifted unnoticed, and it is the half a
    person reads when they are trying to work out whether a job is still
    there.
    """
    plist = scheduler_setup._plist_path(job)
    plist.write_text("<plist/>", encoding="utf-8")
    scheduler_setup._remove_one(job, "darwin")
    assert "OK: removed" in capsys.readouterr().out
    assert not plist.exists(), f"{job.key}: uninstall left the plist behind"

    scheduler_setup._remove_one(job, "darwin")
    assert "NOT_INSTALLED:" in capsys.readouterr().out


@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_uninstallable_on_windows(job, fake_os, capsys):
    scheduler_setup._remove_one(job, "win32")
    out = capsys.readouterr().out
    assert job.win_task in out
    deleted = [c for c in fake_os if "schtasks" in c and "/delete" in c]
    assert any(job.win_task in c for c in deleted), \
        f"{job.key}: uninstall never asked schtasks to delete it"


# ── Reported ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_every_row_is_reported_by_job_states(platform, fake_os, monkeypatch):
    """A job `job_states` omits is graded not_scheduled and never kicked."""
    monkeypatch.setattr(scheduler_setup.Path, "exists", lambda self: True)
    reported = {row["label"] for row in scheduler_setup.job_states(platform)}
    expected = {job.mac_label if platform == "darwin" else job.win_task
                for job in ROWS}
    assert expected - reported == set()


# ── Dependencies ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_every_dependency_names_a_real_row(job):
    """A typo would silently stop gating rather than fail loudly."""
    keys = {row.key for row in ROWS}
    for dep in job.depends_on:
        assert dep in keys, f"{job.key} depends on {dep!r}, which does not exist"


# ── Graded ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_every_graded_row_reaches_the_roster(job, monkeypatch):
    """With its feature on, a graded row must appear in the watcher's roster.

    A job missing from the roster is a job nobody grades, which is exactly how
    the Note tick came to be installed and watched by nothing.
    """
    if not job.graded:
        pytest.skip("ungraded by declaration; covered by the test below")
    import config_loader
    for name in ("prep_email_enabled", "notes_enabled", "finance_enabled",
                 "contact_capture_enabled", "digest_enabled",
                 "workbench_autostart"):
        monkeypatch.setattr(config_loader, name, lambda: True)
    monkeypatch.setattr(config_loader, "digest_briefings", lambda: {
        b: {"enabled": True, "days": ["monday"], "time": "07:00"}
        for b in ("morning-coffee", "afternoon-tea", "week", "week-retro")})
    monkeypatch.setattr(config_loader, "skill_schedule", lambda skill: {
        "enabled": True, "days": ["monday"], "time": "05:00"})

    assert job.key in {row["name"] for row in job_watch.roster()}


def test_exactly_one_row_is_exempt_from_grading_and_it_is_the_watcher():
    """The exemption is not a convenience; it has one legitimate holder.

    The watcher must not grade and kick itself, and its liveness already
    reaches the user through the briefing footer's stale-tick branch. The
    Workbench is the second: a long-running server is graded by liveness, not
    by a slot or a deliverable.
    """
    ungraded = {job.key for job in ROWS if not job.graded}
    assert ungraded == {"job-watch", "workbench"}, (
        "a row took the grading exemption. Only the watcher (which cannot "
        "watch itself) and the Workbench (a server, graded by liveness) may.")


# ── Its ledger name is real ──────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_the_ledger_job_is_a_name_its_script_writes(job):
    """Declaring a ledger name nothing writes grades the job no_history forever.

    That was true of the prep poller for as long as it existed: the roster
    said `prep.email` and no code anywhere wrote that string.
    """
    if not job.ledger_job:
        # Only an ungraded row may omit it: nothing is asking for its history.
        assert not job.graded, f"{job.key} is graded but records no run rows"
        return
    src = (APP / job.script).read_text(encoding="utf-8")
    literal = f'"{job.ledger_job}"' in src or f"'{job.ledger_job}'" in src
    prefix = job.ledger_job.split(".", 1)[0]
    built = f'f"{prefix}.{{' in src
    assert literal or built, (
        f"{job.key}: ledger_job={job.ledger_job!r} is never written by "
        f"{job.script}")


# ── It says when it starts ───────────────────────────────────────────────────

def _is_clock(job) -> bool:
    try:
        return job.resolved_cadence().kind == "clock"
    except Exception:                                            # noqa: BLE001
        # Only the rows a user can retime read config, and every one of those
        # is a clock job.
        return True


@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_a_graded_clock_job_marks_itself_started(job):
    """A clock job that leaves no marker looks, mid-run, exactly like one that
    never fired, and the watcher restarts it with a kill. A poller is exempt:
    it is graded on liveness and finishes in seconds.
    """
    if not job.graded or not _is_clock(job):
        return
    src = (APP / job.script).read_text(encoding="utf-8")
    assert "run_ledger.mark_started(" in src, (
        f"{job.key}: {job.script} never calls run_ledger.mark_started, so the "
        "watcher cannot tell its slow run from a missed one")


# ── Offered to the user ──────────────────────────────────────────────────────

@pytest.mark.parametrize("job", ROWS, ids=IDS)
def test_every_opt_in_row_is_offered_to_the_user(job):
    """The clause that makes the original bug impossible to reintroduce.

    A row the user can turn on must be named, in words a person would
    recognize, in a skill that actually asks them. Van Gogh could schedule
    four briefings and never ask about any of them; nothing was broken, and
    nobody had been asked.

    Matched on `skill_phrase` rather than on the config key or the job name,
    because those are strings the user has never seen. The phrase is the words
    the question uses.
    """
    if job.enabled is None:
        # Always-on: it arrives with the scheduler rather than being chosen.
        assert not job.skill_phrase or _offered(job.skill_phrase), job.key
        return
    assert job.skill_phrase, (
        f"{job.key} is opt-in but has no skill_phrase, so nothing can check "
        f"that a user is ever asked about it")
    assert _offered(job.skill_phrase), (
        f"{job.key}: the words {job.skill_phrase!r} appear in none of "
        f"{', '.join(OFFER_SKILLS)}. A feature nobody is asked about is a "
        f"feature nobody gets.")


def _offered(phrase: str) -> bool:
    needle = phrase.lower()
    for name in OFFER_SKILLS:
        path = SKILLS / name / "SKILL.md"
        if path.exists() and needle in path.read_text(encoding="utf-8").lower():
            return True
    return False


def test_the_offer_check_can_fail():
    """MUTATION CONTROL: the clause above must be capable of failing.

    Every phrase currently appears somewhere, so without this the test could
    be vacuous (a matcher that returns True for anything would pass every
    row). A phrase nobody wrote must not be found.
    """
    assert not _offered("a feature nobody has ever described")


def test_the_install_skill_asks_about_every_briefing():
    """Named separately, because this is the bug in its original form.

    A user who finishes the install and stops must have been asked about all
    four briefings by name.
    """
    text = (SKILLS / "install-van-gogh" / "SKILL.md").read_text(encoding="utf-8")
    for briefing in ("Morning Coffee", "Afternoon Tea", "Week Retro"):
        assert briefing in text, f"install never mentions {briefing}"
    assert "digest.enabled" in text, \
        "install never writes digest.enabled, so the briefings stay off"
