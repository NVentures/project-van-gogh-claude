"""A late wake must not let a briefing render against an unfiled vault.

launchd coalesces every missed `StartCalendarInterval` run and fires it on
wake, which is the behaviour you want: a 07:00 briefing on a laptop opened at
08:30 does run at 08:30. But it coalesces to one run per job and preserves no
order between jobs, so a machine off from 04:00 to 08:30 on a Monday fires the
two ingests, Week and Morning Coffee simultaneously, and Coffee can read a
vault nothing has filed yet.

The watcher cannot catch this: a Coffee that ran early exits 0 and writes a
file, so it grades as delivered while being thin. Nothing downstream can tell
a thin briefing from a quiet morning.

So the gate is a ledger read rather than a guessed delay. Three properties
matter and each has a test: it waits when a dependency has not run, it does
not wait when everything has, and it never waits on a job that is switched off
or not installed -- which would hold every briefing for the full timeout on a
machine that simply does not have that job.
"""

import pathlib
import tempfile
from datetime import date, datetime, timedelta

import pytest

import job_registry
import run_ledger


@pytest.fixture
def ledger(monkeypatch):
    logs = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(run_ledger.config_loader, "logs_dir", lambda: logs)
    return logs


# A Monday, so the weekly rows in these tests are actually due.
MONDAY = datetime(2026, 9, 7, 8, 30)


def _record(job, rc=0, when=None):
    when = when or datetime.now()
    stamp = when.isoformat(timespec="seconds")
    run_ledger.record_run(job, stamp, stamp, rc)


class _Clock:
    """A fake sleep that advances a monotonic counter instead of waiting."""

    def __init__(self):
        self.slept = 0.0

    def __call__(self, seconds):
        self.slept += seconds


def _no_wait(monkeypatch):
    """Make the gate's own clock move only when it sleeps."""
    clock = _Clock()
    base = [1000.0]

    def monotonic():
        return base[0] + clock.slept

    monkeypatch.setattr(run_ledger, "_time", None, raising=False)
    import time as real_time
    monkeypatch.setattr(real_time, "monotonic", monotonic)
    return clock


def _seed_history():
    """One old row, so the ledger does not read as a brand-new machine."""
    old = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    run_ledger.record_run("digest.week", old, old, 0)


def _enable_everything(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "digest_enabled", lambda: True)
    monkeypatch.setattr(config_loader, "digest_briefings", lambda: {
        b: {"enabled": True, "days": ["monday"], "time": "07:00"}
        for b in ("morning-coffee", "afternoon-tea", "week", "week-retro")})


# ── The case it exists for ───────────────────────────────────────────────────

def test_it_waits_when_an_ingest_has_not_run_today(ledger, monkeypatch):
    """The catch-up morning: nothing has filed the vault yet."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    # This machine has run before (so it is not a fresh install), but nothing
    # has run TODAY: the catch-up morning.
    _seed_history()
    # Morning Coffee depends on both ingests and on Week. Record none of them.
    result = run_ledger.wait_for_dependencies("digest-morning-coffee",
                                              sleep=clock, scheduled=True,
                                              now=MONDAY)
    assert not result["satisfied"], "it gave up without noticing anything missing"
    assert set(result["missing"]) == {"meeting-ingest", "ingest-workspace",
                                      "digest-week"}
    assert clock.slept >= run_ledger.DEPENDENCY_TIMEOUT_S, \
        "it returned without actually waiting"


def test_it_runs_anyway_rather_than_waiting_forever(ledger, monkeypatch):
    """A briefing that never arrives is worse than one built on a stale vault."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    _seed_history()
    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True, now=MONDAY)
    # It ends, it says it was not satisfied, and it names what was missing so
    # the caller can say so rather than rendering silently.
    assert result["missing"]
    assert not result["satisfied"]


def test_it_does_not_wait_once_the_dependencies_have_run(ledger, monkeypatch):
    """The normal morning: everything ran hours ago, so this is one read."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    _record("scheduled.meeting-ingest")
    _record("scheduled.ingest-workspace")

    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True)
    assert result["satisfied"] and result["missing"] == []
    assert clock.slept == 0, "it waited even though everything had run"


def test_a_failed_dependency_does_not_count_as_having_run(ledger, monkeypatch):
    """rc 1 is a job that tried and failed, which filed nothing."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    _record("scheduled.meeting-ingest", rc=1)
    _record("scheduled.ingest-workspace")

    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True)
    assert result["missing"] == ["meeting-ingest"]


def test_yesterdays_run_does_not_count(ledger, monkeypatch):
    """The question is whether the vault was filed TODAY."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    yesterday = datetime.now() - timedelta(days=1)
    _record("scheduled.meeting-ingest", when=yesterday)
    _record("scheduled.ingest-workspace", when=yesterday)

    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True)
    assert set(result["missing"]) == {"meeting-ingest", "ingest-workspace"}


# ── A dependency that will never run is not a dependency ─────────────────────

def test_it_never_waits_on_a_job_that_is_switched_off(ledger, monkeypatch):
    """Otherwise every briefing holds for the full timeout on that machine.

    Morning Coffee depends on Week. A user who turned the Week briefing off
    must not have their Coffee held for twenty minutes every morning waiting
    for a job that is never going to run.
    """
    import config_loader
    monkeypatch.setattr(config_loader, "digest_enabled", lambda: True)
    monkeypatch.setattr(config_loader, "digest_briefings", lambda: {
        "morning-coffee": {"enabled": True, "days": ["monday"], "time": "07:00"},
        "week": {"enabled": False, "days": ["monday"], "time": "06:30"},
        "afternoon-tea": {"enabled": False},
        "week-retro": {"enabled": False},
    })
    clock = _no_wait(monkeypatch)

    _record("scheduled.meeting-ingest")
    _record("scheduled.ingest-workspace")

    result = run_ledger.wait_for_dependencies("digest-morning-coffee",
                                              sleep=clock, scheduled=True)
    assert result["satisfied"], "it waited on a briefing the user turned off"
    assert clock.slept == 0


def test_a_job_with_no_dependencies_returns_at_once(ledger, monkeypatch):
    """The finance brief reads QuickBooks, which is not in the vault."""
    clock = _no_wait(monkeypatch)
    result = run_ledger.wait_for_dependencies("finance-email", sleep=clock,
                                              scheduled=True)
    assert result == {"waited_s": 0, "satisfied": True, "missing": []}
    assert clock.slept == 0


def test_an_unknown_job_key_does_not_hang(ledger, monkeypatch):
    clock = _no_wait(monkeypatch)
    result = run_ledger.wait_for_dependencies("no-such-job", sleep=clock,
                                              scheduled=True)
    assert result["satisfied"] and clock.slept == 0


def test_the_gate_never_raises(ledger, monkeypatch):
    """A gate that can wedge a briefing is worse than no gate at all."""
    clock = _no_wait(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("the ledger is unreadable")

    monkeypatch.setattr(run_ledger, "read_runs", boom)
    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True)
    assert result["satisfied"], "a broken ledger must fall through to running"


# ── Two ways of not being a catch-up morning ─────────────────────────────────

def test_a_hand_run_briefing_never_waits(ledger, monkeypatch):
    """The pile-up is a property of the OS firing several jobs at once.

    Somebody who types /van-gogh:morning-coffee wants it now. Making them wait
    twenty minutes for a filing job would be absurd, and it is the reason the
    wait is gated on the scheduler's own marker rather than applied to every
    run of the script.
    """
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)
    _seed_history()

    monkeypatch.delenv(run_ledger.SCHEDULED_ENV, raising=False)
    result = run_ledger.wait_for_dependencies("digest-morning-coffee",
                                              sleep=clock, now=MONDAY)
    assert result["satisfied"] and clock.slept == 0


def test_the_scheduler_marks_its_own_runs(ledger, monkeypatch):
    """The env var is read from the environment when nothing is passed.

    Without this the gate would be dead code in production: every scheduled
    run would look hand-run and never wait.
    """
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)
    _seed_history()

    monkeypatch.setenv(run_ledger.SCHEDULED_ENV, "1")
    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              now=MONDAY)
    assert not result["satisfied"], "a scheduled run did not wait"


def test_every_scheduled_command_carries_the_marker():
    """And the scheduler must actually set it, on both platforms.

    This is the half that makes the test above meaningful: a marker nothing
    sets is a gate that never fires.
    """
    import scheduler_setup
    from pathlib import Path

    mac = scheduler_setup._Ctx("/repo", "/py", "/claude", Path("/logs"))
    win = scheduler_setup._Ctx(r"C:\repo", "/py", "/claude", Path(r"C:\logs"))
    job = job_registry.by_key("digest-morning-coffee")
    assert f"{run_ledger.SCHEDULED_ENV}=1" in scheduler_setup._job_command(job, mac)
    inner = scheduler_setup._inner_for(job, win)
    # No space before the `&&`: cmd.exe would fold it into the VALUE, making
    # the variable "1 " and every equality test downstream false.
    assert f"set {run_ledger.SCHEDULED_ENV}=1&&" in inner
    assert f"{run_ledger.SCHEDULED_ENV}=1 &&" not in inner


def test_a_machine_that_has_never_run_anything_does_not_wait(ledger, monkeypatch):
    """A fresh install is not a late wake.

    On day one the ledger is empty because nothing has ever run, not because
    this morning went wrong. Holding the first briefing for twenty minutes
    would be the user's first impression of the product.
    """
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)

    result = run_ledger.wait_for_dependencies("digest-week", sleep=clock,
                                              scheduled=True, now=MONDAY)
    assert result["satisfied"] and clock.slept == 0


def test_it_does_not_wait_for_a_job_that_is_not_due_today(ledger, monkeypatch):
    """Week runs on Mondays. On a Thursday there is nothing to wait for."""
    _enable_everything(monkeypatch)
    clock = _no_wait(monkeypatch)
    _seed_history()

    thursday = datetime(2026, 9, 10, 8, 30)
    assert thursday.weekday() == 3
    result = run_ledger.wait_for_dependencies("digest-morning-coffee",
                                              sleep=clock, scheduled=True,
                                              now=thursday)
    # The ingests are daily so they are still due; Week is not.
    assert "digest-week" not in result["missing"]


# ── The edges are real jobs ──────────────────────────────────────────────────

def test_every_dependency_names_a_real_row():
    """A typo would make a briefing wait on a job that does not exist.

    It would not hang -- an unknown key is skipped -- but it would silently
    stop gating, which is the failure the gate exists to prevent, arriving
    quietly through a misspelling.
    """
    keys = {job.key for job in job_registry.jobs()}
    for job in job_registry.jobs():
        for dep in job.depends_on:
            assert dep in keys, (
                f"{job.key} depends on {dep!r}, which is not a registry row")


def test_the_briefings_depend_on_what_files_the_vault():
    """The edges, stated so a change to them is a decision rather than a diff."""
    expected = {
        "digest-morning-coffee": {"meeting-ingest", "ingest-workspace",
                                  "digest-week"},
        "digest-afternoon-tea": {"meeting-ingest", "ingest-workspace"},
        "digest-week": {"meeting-ingest", "ingest-workspace"},
        "digest-week-retro": {"meeting-ingest", "ingest-workspace"},
        # QuickBooks is not in the vault, so waiting makes this no better.
        "finance-email": set(),
    }
    for key, deps in expected.items():
        assert set(job_registry.by_key(key).depends_on) == deps, key


def test_nothing_depends_on_itself_even_transitively():
    """A cycle would hold both jobs for the full timeout, every morning."""
    graph = {job.key: set(job.depends_on) for job in job_registry.jobs()}

    def reaches(start, target, seen=None):
        seen = seen or set()
        for dep in graph.get(start, ()):
            if dep == target:
                return True
            if dep in seen:
                continue
            seen.add(dep)
            if reaches(dep, target, seen):
                return True
        return False

    for key in graph:
        assert not reaches(key, key), f"{key} depends on itself"
