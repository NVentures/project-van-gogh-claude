"""run_ledger.py — one row per unattended run, on every exit path.

The rows are the only evidence that a scheduled job ever succeeded, so the
tests care most about the paths that are easy to forget: an exception, and a
run that correctly decided to do nothing.
"""
import json
from datetime import date
from pathlib import Path

import pytest

import digest_send
import run_ledger
import skill_run


@pytest.fixture
def logs(tmp_path, monkeypatch):
    import config_loader as cl
    monkeypatch.setattr(cl, "logs_dir", lambda: tmp_path)
    return tmp_path


def rows(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_record_run_writes_one_row(logs):
    run_ledger.record_run("scheduled.week", "2026-09-09T07:00:00",
                          "2026-09-09T07:04:00", 0, "fine")
    got = rows(logs / "runs.jsonl")
    assert len(got) == 1
    assert got[0] == {"job": "scheduled.week", "started": "2026-09-09T07:00:00",
                      "finished": "2026-09-09T07:04:00", "rc": 0, "detail": "fine"}


def test_record_run_never_raises_when_the_path_is_unwritable(tmp_path, monkeypatch):
    import config_loader as cl
    # A directory where the file should be: the open fails, the caller does not.
    (tmp_path / "runs.jsonl").mkdir()
    monkeypatch.setattr(cl, "logs_dir", lambda: tmp_path)
    run_ledger.record_run("scheduled.week", "a", "b", 0)


def test_read_runs_tolerates_a_torn_last_line(logs):
    path = logs / "runs.jsonl"
    path.write_text(
        json.dumps({"job": "a", "started": "2026-09-08T07:00:00", "rc": 0}) + "\n"
        + '{"job": "b", "started": "2026-09-0',
        encoding="utf-8")
    got = run_ledger.read_runs(path)
    assert [r["job"] for r in got] == ["a"]


def test_read_runs_filters_by_date(logs):
    path = logs / "runs.jsonl"
    path.write_text("\n".join(
        json.dumps({"job": "a", "started": f"2026-09-0{d}T07:00:00", "rc": 0})
        for d in (1, 5, 9)), encoding="utf-8")
    assert len(run_ledger.read_runs(path, since=date(2026, 9, 5))) == 2


def test_run_dates_counts_days_not_runs(logs):
    got = [{"started": "2026-09-09T07:00:00", "rc": 0},
           {"started": "2026-09-09T08:00:00", "rc": 0},
           {"started": "2026-09-08T07:00:00", "rc": 1}]
    assert run_ledger.run_dates(got) == {date(2026, 9, 9)}
    assert len(run_ledger.run_dates(got, rc=None)) == 2


# ── skill_run wiring ─────────────────────────────────────────────────────────

def test_skill_run_records_a_success(logs, monkeypatch):
    monkeypatch.setattr(skill_run, "run_skill", lambda *a, **k: "out")
    assert skill_run.main(["week"]) == 0
    got = rows(logs / "runs.jsonl")
    assert got[0]["job"] == "scheduled.week" and got[0]["rc"] == 0


def test_skill_run_records_an_exception(logs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the CLI died")
    monkeypatch.setattr(skill_run, "run_skill", boom)
    assert skill_run.main(["week"]) == 1
    got = rows(logs / "runs.jsonl")
    assert got[0]["rc"] == 1 and "the CLI died" in got[0]["detail"]


# ── digest_send wiring ───────────────────────────────────────────────────────

def test_digest_records_a_skipped_run(logs, monkeypatch, capsys):
    import config_loader as cl
    monkeypatch.setattr(cl, "digest_enabled", lambda: False)
    assert digest_send.main(["week"]) == 0
    got = rows(logs / "runs.jsonl")
    assert got[0]["job"] == "digest.week" and got[0]["rc"] == 0
    assert got[0]["detail"].startswith("skipped:")


def test_digest_records_a_failure(logs, monkeypatch, capsys):
    import config_loader as cl
    monkeypatch.setattr(cl, "digest_enabled", lambda: True)
    # Every weekday, so the run reaches the sender check whatever day it is.
    monkeypatch.setattr(cl, "digest_briefings", lambda: {"week": {
        "enabled": True,
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday"]}})
    monkeypatch.setattr(cl, "digest_sender_account", lambda: None)
    assert digest_send.main(["week"]) == 1
    got = rows(logs / "runs.jsonl")
    assert got[0]["job"] == "digest.week" and got[0]["rc"] == 1


def test_digest_records_an_unexpected_exception(logs, monkeypatch):
    def boom(argv=None):
        raise RuntimeError("config blew up")
    monkeypatch.setattr(digest_send, "_run_digest", boom)
    assert digest_send.main(["week"]) == 1
    got = rows(logs / "runs.jsonl")
    assert got[0]["rc"] == 1 and "config blew up" in got[0]["detail"]


# ── A run that has started and not finished ──────────────────────────────────

@pytest.fixture
def state(tmp_path, monkeypatch):
    import user_state
    monkeypatch.setattr(user_state, "state_dir", lambda: tmp_path / "state")
    return tmp_path / "state"


def test_a_started_run_is_in_flight_until_its_row_is_written(logs, state):
    run_ledger.mark_started("digest.week")
    marker = run_ledger.in_flight("digest.week")
    assert marker and marker["job"] == "digest.week"
    assert run_ledger.in_flight("digest.morning-coffee") is None

    run_ledger.record_run("digest.week", "2026-09-09T07:00:00",
                          "2026-09-09T07:04:00", 0)
    assert run_ledger.in_flight("digest.week") is None
    assert not list((state / "inflight").glob("*.json"))


def test_a_failed_run_clears_its_marker_too(logs, state):
    run_ledger.mark_started("digest.week")
    run_ledger.record_run("digest.week", "2026-09-09T07:00:00",
                          "2026-09-09T07:04:00", 1, "boom")
    assert run_ledger.in_flight("digest.week") is None


def test_a_marker_whose_process_is_gone_is_not_in_flight(logs, state):
    """A run that was killed never wrote its row or removed its marker. That
    is the run the watcher must restart, so a dead process answers None."""
    import subprocess
    import sys
    from platform_compat import NO_WINDOW
    child = subprocess.Popen([sys.executable, "-c", "pass"],
                             creationflags=NO_WINDOW)
    child.wait()
    run_ledger.mark_started("digest.week")
    path = run_ledger._marker_path("digest.week")
    marker = json.loads(path.read_text(encoding="utf-8"))
    marker["pid"] = child.pid
    path.write_text(json.dumps(marker), encoding="utf-8")
    assert run_ledger.in_flight("digest.week") is None


def test_a_marker_past_the_cap_is_not_believed(logs, state):
    """A process id is reused after a reboot. The cap is what stops a stranger
    with the same number from holding a job forever."""
    from datetime import datetime, timedelta
    run_ledger.mark_started("digest.week")
    assert run_ledger.in_flight("digest.week")
    late = datetime.now() + timedelta(hours=run_ledger.INFLIGHT_MAX_H, minutes=1)
    assert run_ledger.in_flight("digest.week", now=late) is None


def test_a_torn_marker_is_not_in_flight(logs, state):
    path = run_ledger._marker_path("digest.week")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert run_ledger.in_flight("digest.week") is None


def test_a_skill_run_leaves_no_marker_behind(logs, state, monkeypatch):
    monkeypatch.setattr(skill_run, "run_skill", lambda *a, **k: None)
    seen = {}
    real = run_ledger.record_run

    def spy(job, *a, **k):
        seen["during"] = run_ledger.in_flight(job) is not None
        return real(job, *a, **k)
    monkeypatch.setattr(run_ledger, "record_run", spy)
    assert skill_run.main(["week"]) == 0
    assert seen["during"] is True, "it was marked while it ran"
    assert run_ledger.in_flight("scheduled.week") is None
