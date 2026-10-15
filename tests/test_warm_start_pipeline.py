"""warm_start: the install's pipeline commands (wait-scan, ingest-start,
ingest-finish, first-briefing) and the first-day briefing choice (P21).

Every subprocess is stubbed; no provider, no model, no clock but the one the
test hands in.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import afternoon_tea as at
import claude_cli
import config_loader as cl
import warm_start as ws


def _snapshot(**sections):
    base = {
        "waiting_on_user": [
            {"subject": "Re: Harbor redline", "counterparty_email": "alice@counsel.com",
             "counterparty_name": "Alice Ng", "reply_age_days": 3, "source": "Gmail",
             "is_internal": False, "body_preview": "see attached"},
        ],
        "inbox_pending": [
            {"subject": "Diligence list", "counterparty_email": "carla@grid.com",
             "counterparty_name": "Carla Diaz", "reply_age_days": 1, "source": "Gmail",
             "is_internal": False, "body_preview": "can you send the list"},
        ],
        "cold_urgent": [], "cold_monitor": [], "filtered_pending": [],
        "calendar": [], "obsidian_tasks": [], "status_change_alerts": [], "errors": [],
    }
    base.update(sections)
    return {"kind": "week_review_scan", "saved": "2026-10-01T16:00:00+00:00",
            "since_days": 60, "sections": base,
            "thread_ids": {k: [] for k in ("waiting_on_user", "inbox_pending",
                                            "filtered_pending", "cold_urgent", "cold_monitor")}}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    monkeypatch.setattr(claude_cli, "_spawn",
                        lambda *a, **k: pytest.fail("a model was called"))
    return tmp_path / "state"


class _R:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


# ── scan records its start and saves its snapshot ────────────────────────────

def test_scan_saves_the_snapshot_and_records_when_it_started(state, tmp_path, monkeypatch):
    seen = {}

    class _P:
        pid = 4242

    monkeypatch.setattr(ws, "_job_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ws.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, kw=kw) or _P())
    assert ws.start_scan(60) == 4242
    cmd = seen["cmd"]
    assert "--save-scan" in cmd and cmd[cmd.index("--save-scan") + 1] == str(ws.scan_path())
    assert "--since" in cmd and "60" in cmd and "--no-extensions" in cmd
    meta = json.loads(ws.scan_meta_path().read_text(encoding="utf-8"))
    assert meta["pid"] == 4242 and meta["started_ts"] > 0
    # The child's output goes to a log, never to DEVNULL, and the meta says where.
    assert meta["log"] == str(tmp_path / "scan.log")
    assert seen["kw"]["stderr"] is subprocess.STDOUT and seen["kw"]["stdin"] is subprocess.DEVNULL
    assert Path(seen["kw"]["stdout"].name) == tmp_path / "scan.log"


# ── wait-scan ────────────────────────────────────────────────────────────────

def _clock():
    t = [1000.0]

    def now():
        return t[0]

    def sleep(s):
        t[0] += s
    return now, sleep


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "week_review_latest.full.json"
    monkeypatch.setattr(ws.config_loader, "full_sidecar_path", lambda: path)
    return path


def test_wait_scan_reports_counts_once_the_snapshot_is_fresh(state, sidecar, monkeypatch):
    now, sleep = _clock()
    ws._write_json(ws.scan_meta_path(), {"started_ts": 0, "pid": os.getpid()})
    ws._write_json(ws.scan_path(), _snapshot())
    ws._write_json(sidecar, {"waiting_on_user": []})
    out = ws.wait_scan(timeout=10, poll=1, sleep=sleep, now=now)
    assert out["ready"] is True
    assert out["scan"] == str(ws.scan_path()) and out["sidecar"] == str(sidecar)
    assert out["counts"] == {"waiting_on_user": 1, "inbox_pending": 1, "cold_urgent": 0,
                             "cold_monitor": 0, "calendar": 0}
    assert out["since"] == 60


def test_wait_scan_times_out_while_the_scan_still_runs(state, monkeypatch):
    now, sleep = _clock()
    ws._write_json(ws.scan_meta_path(), {"started_ts": 2e12, "pid": os.getpid()})
    out = ws.wait_scan(timeout=5, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False and out["timeout"] is True
    assert out["waited_s"] >= 5


def test_wait_scan_stops_at_once_when_the_scan_process_died(state, sidecar, monkeypatch):
    now, sleep = _clock()
    monkeypatch.setattr(ws, "_pid_alive", lambda pid: False)
    ws._write_json(ws.scan_meta_path(), {"started_ts": 2e12, "pid": 99999})
    out = ws.wait_scan(timeout=600, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False
    assert out["error"] == "the scan exited without writing its snapshot and sidecar"
    assert out["waited_s"] == 0


def test_wait_scan_is_not_ready_on_the_snapshot_alone(state, sidecar, monkeypatch):
    """The snapshot used to be the completion signal while `propose` reads
    the sidecar, written separately: a caller saw ready with no sidecar."""
    now, sleep = _clock()
    ws._write_json(ws.scan_meta_path(), {"started_ts": 0, "pid": os.getpid()})
    ws._write_json(ws.scan_path(), _snapshot())
    out = ws.wait_scan(timeout=3, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False and out["timeout"] is True
    assert out["have"] == {"snapshot": True, "sidecar": False}
    # Nor on the sidecar alone.
    ws.scan_path().unlink()
    ws._write_json(sidecar, {})
    out = ws.wait_scan(timeout=3, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False and out["have"] == {"snapshot": False, "sidecar": True}


def test_wait_scan_ignores_a_snapshot_older_than_the_start(state, monkeypatch):
    now, sleep = _clock()
    ws._write_json(ws.scan_path(), _snapshot())
    ws._write_json(ws.scan_meta_path(), {"started_ts": 2e12, "pid": os.getpid()})
    out = ws.wait_scan(timeout=2, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False


# ── ingest-start and ingest-finish ───────────────────────────────────────────

def test_ingest_start_detaches_the_prefetch(state, monkeypatch):
    seen = {}

    class _P:
        pid = 777

    monkeypatch.setattr(ws.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, kw=kw) or _P())
    out = ws.start_ingest(limit=10)
    assert out["started"] and out["pid"] == 777
    cmd = seen["cmd"]
    assert "--prefetch" in cmd and cmd[cmd.index("--limit") + 1] == "10"
    assert cmd[cmd.index("--staged-dir") + 1] == str(ws.staged_dir())
    assert Path(out["staged_dir"]) == state / "setup" / "staged"
    kw = seen["kw"]
    assert kw["start_new_session"] is True
    assert kw["stdin"] is subprocess.DEVNULL and kw["stderr"] is subprocess.STDOUT
    assert kw["creationflags"] == ws.NO_WINDOW
    assert Path(out["log"]).name == "prefetch.log"
    assert json.loads((ws.staged_dir() / "prefetch.json").read_text(encoding="utf-8"))["pid"] == 777


def _manifest(filed, failed=(), deferred=(), degraded=None):
    return {"filed": [{"id": i, "title": f"Meeting {i}", "path": f"/x/{i}.json"} for i in filed],
            "failed": [{"id": i, "title": f"Meeting {i}", "error": "NotetakerError: 500",
                        "attempts": 3} for i in failed],
            "deferred": [{"id": i, "title": f"Meeting {i}", "reason": "usage_cap"}
                         for i in deferred],
            "degraded": degraded}


def test_ingest_finish_files_serially_and_reports_the_three_counts(state, monkeypatch):
    d = ws.staged_dir()
    ws._write_json(d / "manifest.json", _manifest(["a", "b"], failed=["c"]))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "--staged" in cmd:
            return _R(0, "✓ Wrote /vault/wiki/sources/Harbor walk - 2026-09-30.md\n"
                         "✓ Wrote /vault/wiki/sources/Acme call - 2026-09-29.md\n")
        return _R(0, "backfill ok")
    monkeypatch.setattr(ws.subprocess, "run", run)

    out = ws.finish_ingest()
    assert [c[2] for c in calls] == ["--staged", "--backfill"]
    assert calls[0][calls[0].index("--staged") + 1] == str(d) and "--apply" in calls[0]
    assert out["filed"] == ["Harbor walk - 2026-09-30", "Acme call - 2026-09-29"]
    assert out["pages"] == out["filed"]
    assert out["failed"] == [{"id": "c", "title": "Meeting c", "error": "NotetakerError: 500"}]
    assert out["deferred"] == [] and out["degraded"] is None
    assert out["counts"] == {"filed": 2, "failed": 1, "deferred": 0, "summary_only": 0}
    assert out["ingested"] and out["backfilled"]


def test_ingest_finish_after_a_usage_cap_is_degraded_with_the_count(state, monkeypatch):
    """P12: what finished is filed, the rest is deferred with a count, and
    the result says degraded so the checklist box stays pending."""
    d = ws.staged_dir()
    ws._write_json(d / "manifest.json", _manifest(
        ["a"], deferred=["b", "c", "d"], degraded={"reason": "usage_cap", "deferred": 3}))
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _R(
        0, "✓ Wrote /v/wiki/sources/Harbor walk - 2026-09-30.md\n"))
    out = ws.finish_ingest()
    assert out["filed"] == ["Harbor walk - 2026-09-30"]
    assert [r["id"] for r in out["deferred"]] == ["b", "c", "d"]
    assert out["degraded"] == {"reason": "usage_cap", "deferred": 3}
    assert out["counts"] == {"filed": 1, "failed": 0, "deferred": 3, "summary_only": 0}


def test_ingest_finish_says_so_while_the_prefetch_still_runs(state, monkeypatch):
    d = ws.staged_dir()
    ws._write_json(d / "prefetch.json", {"pid": os.getpid()})
    monkeypatch.setattr(ws.subprocess, "run",
                        lambda cmd, **kw: pytest.fail("filed before the prefetch finished"))
    out = ws.finish_ingest()
    assert out["pending"] is True and not out["ingested"]
    assert out["counts"] == {"filed": 0, "failed": 0, "deferred": 0, "summary_only": 0}


def test_ingest_finish_with_nothing_staged_is_an_error_not_a_pass(state, monkeypatch):
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _R(0))
    out = ws.finish_ingest()
    assert not out["ingested"] and "ingest-start" in out["errors"][0]


def test_ingest_finish_with_an_empty_backlog_is_finished(state, monkeypatch):
    ws._write_json(ws.staged_dir() / "manifest.json", _manifest([]))
    calls = []
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _R(0))
    out = ws.finish_ingest()
    assert out["ingested"] and out["counts"]["filed"] == 0
    assert [c[2] for c in calls] == ["--backfill"]


def test_run_ingest_is_two_phase_unless_serial(state, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "--prefetch" in cmd:
            ws._write_json(ws.staged_dir() / "manifest.json", _manifest(["a"]))
            return _R(0, "{}")
        if "--staged" in cmd:
            return _R(0, "✓ Wrote /v/wiki/sources/A - 2026-09-30.md\n")
        return _R(0)
    monkeypatch.setattr(ws.subprocess, "run", run)
    out = ws.run_ingest(limit=10)
    assert [c[2] for c in calls] == ["--prefetch", "--staged", "--backfill"]
    assert out["ingested"] and out["backfilled"] and out["filed"] == ["A - 2026-09-30"]


# ── first-briefing ───────────────────────────────────────────────────────────

@pytest.fixture
def new_york(monkeypatch):
    saved = cl._config
    cfg = json.loads(json.dumps(saved))
    cfg["user"]["timezone"] = "America/New_York"
    monkeypatch.setattr(cl, "_config", cfg)
    yield
    monkeypatch.setattr(cl, "_config", saved)


def _briefing_run(seen):
    def run(cmd, **kw):
        seen.append(cmd)
        payload = {"counts": {"open": 2}, "errors": [],
                   "action_items": {"open": ["Reply to Alice Ng"]}}
        return _R(0, json.dumps(payload))
    return run


@pytest.mark.parametrize("now, expected", [
    ("2026-10-01T11:59:00", "morning-coffee"),
    ("2026-10-01T12:01:00", "afternoon-tea"),
    # UTC-aware clocks: 15:59Z is 11:59 in New York, 16:01Z is 12:01. In UTC
    # both are afternoon, so these two prove the configured zone is used.
    ("2026-10-01T15:59:00+00:00", "morning-coffee"),
    ("2026-10-01T16:01:00+00:00", "afternoon-tea"),
])
def test_auto_picks_the_briefing_by_the_local_clock(state, new_york, now, expected):
    ws._write_json(ws.scan_path(), _snapshot())
    seen = []
    out = ws.first_briefing("auto", now=now, run=_briefing_run(seen))
    assert out["which"] == expected
    script = {"morning-coffee": "morning_coffee.py", "afternoon-tea": "afternoon_tea.py"}[expected]
    assert Path(seen[0][1]).name == script
    assert seen[0][seen[0].index("--reuse-scan") + 1] == str(ws.scan_path())
    assert Path(out["data"]).exists() and out["errors"] == []
    assert out["local_time"] in ("11:59", "12:01")


def test_first_briefing_week_renders_from_the_scan_with_no_extensions(state):
    ws._write_json(ws.scan_path(), _snapshot())
    seen = []
    out = ws.first_briefing("week", run=_briefing_run(seen))
    assert Path(seen[0][1]).name == "week_review.py"
    assert "--reuse-scan" in seen[0] and "--no-extensions" in seen[0]
    assert out["sidecar"].endswith("week_review_latest.full.json")
    assert json.loads(Path(out["data"]).read_text(encoding="utf-8"))["counts"] == {"open": 2}


def test_first_briefing_without_a_scan_is_an_error(state):
    out = ws.first_briefing("week", run=lambda *a, **k: pytest.fail("ran without a scan"))
    assert "No saved scan" in out["error"]


def test_pick_briefing_boundary_is_noon():
    tz = ZoneInfo("America/New_York")
    assert ws.pick_briefing(datetime(2026, 10, 1, 11, 59, tzinfo=tz)) == "morning-coffee"
    assert ws.pick_briefing(datetime(2026, 10, 1, 12, 0, tzinfo=tz)) == "afternoon-tea"


# ── Afternoon Tea on a day with no Morning Coffee file ───────────────────────

def test_afternoon_tea_builds_its_open_list_from_the_scan_on_the_first_day(
        state, tmp_path, monkeypatch, capsys):
    """P21: no Morning Coffee file, waiting threads in the scan, no fetch."""
    snap = tmp_path / "scan.json"
    snap.write_text(json.dumps(_snapshot(calendar=[
        {"source": "Google", "title": "Harbor site walk", "day": "Fri Oct 2",
         "time": "10:00 AM", "location": ""}])), encoding="utf-8")
    monkeypatch.setattr(at, "MORNING_COFFEE_MD", str(tmp_path / "missing-morning-coffee.md"))
    monkeypatch.setattr(at, "HOTCACHE_PATH", str(tmp_path / "hotcache.md"))
    monkeypatch.setattr(at, "fetch_meetings", lambda *a, **k: [])
    monkeypatch.setattr(at, "is_tier1", lambda: False)
    for name in ("google_client", "microsoft_client", "fetch_tomorrow_calendar",
                 "fetch_gmail_sent_today", "fetch_outlook_sent_today",
                 "fetch_gmail_received_today", "fetch_outlook_received_today"):
        monkeypatch.setattr(at, name,
                            lambda *a, _n=name, **k: pytest.fail(f"{_n} was called"))
    monkeypatch.setattr(sys, "argv", ["afternoon_tea.py", "--reuse-scan", str(snap)])
    at.main()
    out = json.loads(capsys.readouterr().out)
    open_items = out["action_items"]["open"]
    assert open_items, "the first retro must not read as nothing to do"
    assert out["action_items"]["from_scan"] is True
    assert any("Alice Ng" in i and "Harbor redline" in i for i in open_items)
    assert any("Carla Diaz" in i for i in open_items)
    assert out["sent_today"] == []
    assert out["inbound_today"] and out["inbound_today"][0]["from_email"] == "carla@grid.com"
    assert not any(e.startswith("Morning coffee") for e in out["errors"])


def test_afternoon_tea_prefers_the_morning_file_when_it_exists(state, tmp_path, monkeypatch, capsys):
    snap = tmp_path / "scan.json"
    snap.write_text(json.dumps(_snapshot()), encoding="utf-8")
    morning = tmp_path / "morning-coffee.md"
    morning.write_text("# Morning\n\n- [ ] Call the bank\n- [x] Done thing\n", encoding="utf-8")
    monkeypatch.setattr(at, "MORNING_COFFEE_MD", str(morning))
    monkeypatch.setattr(at, "HOTCACHE_PATH", str(tmp_path / "hotcache.md"))
    monkeypatch.setattr(at, "fetch_meetings", lambda *a, **k: [])
    monkeypatch.setattr(at, "is_tier1", lambda: False)
    monkeypatch.setattr(sys, "argv", ["afternoon_tea.py", "--reuse-scan", str(snap)])
    at.main()
    out = json.loads(capsys.readouterr().out)
    assert out["action_items"]["open"] == ["Call the bank"]
    assert out["action_items"]["from_scan"] is False


def test_open_items_from_scan_dedup_and_name_the_counterparty():
    items = at.open_items_from_scan(_snapshot(waiting_on_user=[
        {"subject": "Re: Harbor redline", "counterparty_name": "Alice Ng"},
        {"subject": "Re: Harbor redline", "counterparty_name": "Alice Ng"},
        {"subject": "", "counterparty_name": ""},
    ], inbox_pending=[]))
    assert items == ['Reply to Alice Ng: "Re: Harbor redline"']


def test_tomorrow_from_scan_filters_on_the_day_label():
    today = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    snap = _snapshot(calendar=[
        {"source": "Google", "title": "Tomorrow thing", "day": "Fri Oct 2", "time": "9:00 AM"},
        {"source": "Google", "title": "Today thing", "day": "Thu Oct 1", "time": "9:00 AM"},
    ])
    events = at.tomorrow_from_scan(snap, today)
    assert [e["title"] for e in events] == ["Tomorrow thing"]


def test_ingest_finish_passes_through_what_was_filed_from_the_summary_only(state, monkeypatch):
    """A meeting the install had no time to read is still filed (10 of 10);
    the orchestrator is told which, and why, so it can say so."""
    d = ws.staged_dir()
    manifest = _manifest(["a", "b"])
    manifest["summary_only"] = [{"id": "b", "title": "Meeting b",
                                 "reason": "over the install's time budget",
                                 "over_budget": True}]
    manifest["budget_s"] = 200
    ws._write_json(d / "manifest.json", manifest)
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _R(
        0, "\u2713 Wrote /v/wiki/sources/A - 2026-09-30.md\n"
           "\u2713 Wrote /v/wiki/sources/B - 2026-09-29.md\n"))
    out = ws.finish_ingest()
    assert out["counts"] == {"filed": 2, "failed": 0, "deferred": 0, "summary_only": 1}
    assert out["summary_only"] == [{"id": "b", "title": "Meeting b",
                                    "reason": "over the install's time budget",
                                    "over_budget": True}]
    assert out["budget_s"] == 200
    assert out["degraded"] is None and out["ingested"], \
        "a summary-only meeting is filed, not a degraded install"


# ── A scan that dies at start says why ───────────────────────────────────────

CRASHING_SCAN = """import sys
print("partial stdout before the crash")
sys.stderr.write("Traceback (most recent call last):\\n")
sys.stderr.write('  File "week_review.py", line 1, in <module>\\n')
sys.stderr.write("RuntimeError: Missing refresh token: env var "
                 "GOOGLE_REFRESH_TOKEN_GMAIL is not set\\n")
sys.exit(1)
"""


def test_a_scan_that_crashes_at_start_leaves_its_reason_in_the_log(state, tmp_path, monkeypatch):
    """The real child, a stub week_review.py that exits 1 with a traceback."""
    import time
    script = tmp_path / "week_review_stub.py"
    script.write_text(CRASHING_SCAN, encoding="utf-8")
    monkeypatch.setattr(ws, "_SCAN_SCRIPT", str(script))
    monkeypatch.setattr(ws, "_job_logs_dir", lambda: tmp_path)

    pid = ws.start_scan(60)
    # Reap the child: a finished but unreaped child still answers signal 0,
    # so without this the pid reads alive forever. (In the install the
    # parent `warm_start.py scan` exits at once and init reaps the child.)
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        for _ in range(200):
            if not ws._pid_alive(pid):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the stub scan did not exit")

    log = (tmp_path / "scan.log").read_text(encoding="utf-8")
    assert "partial stdout before the crash" in log and "RuntimeError" in log

    now, sleep = _clock()
    out = ws.wait_scan(timeout=600, poll=1, sleep=sleep, now=now)
    assert out["ready"] is False
    assert out["error"] == "the scan exited without writing its snapshot and sidecar"
    assert out["reason"] == ("RuntimeError: Missing refresh token: env var "
                             "GOOGLE_REFRESH_TOKEN_GMAIL is not set")
    assert out["log"] == str(tmp_path / "scan.log")
    assert out["waited_s"] == 0


def test_the_reason_is_the_last_non_empty_line_truncated(tmp_path):
    log = tmp_path / "scan.log"
    log.write_text("first\n\nValueError: " + "x" * 400 + "\n\n\n", encoding="utf-8")
    reason = ws._last_log_line(log)
    assert reason.startswith("ValueError: xxx") and len(reason) == 200
    assert ws._last_log_line(tmp_path / "missing.log") == ""
    assert ws._last_log_line(None) == ""


def test_ingest_finish_quotes_the_prefetch_log_when_the_job_died(state, tmp_path, monkeypatch):
    d = ws.staged_dir()
    log = tmp_path / "prefetch.log"
    log.write_text("reading 10 meetings\nNotetakerError: HTTP 401 from Granola\n",
                   encoding="utf-8")
    ws._write_json(d / "prefetch.json", {"pid": 999999, "log": str(log)})
    monkeypatch.setattr(ws, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ws.subprocess, "run",
                        lambda cmd, **kw: pytest.fail("filed with no manifest"))
    out = ws.finish_ingest()
    assert not out["ingested"] and "ingest-start" in out["errors"][0]
    assert out["reason"] == "NotetakerError: HTTP 401 from Granola"
    assert out["log"] == str(log)


def test_ingest_finish_with_no_job_at_all_carries_no_reason(state, monkeypatch):
    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _R(0))
    out = ws.finish_ingest()
    assert "reason" not in out


# ── The scan writes the sidecar first and the snapshot last ──────────────────

TWO_STEP_SCAN = """import json, os, sys, time
argv = sys.argv
snap = argv[argv.index("--save-scan") + 1]
sidecar = os.environ["STUB_SIDECAR"]
# The OLD order, on purpose: snapshot first, sidecar two seconds later. The
# reader must not report ready in between whatever order a writer uses.
os.makedirs(os.path.dirname(snap), exist_ok=True)
json.dump({"kind": "week_review_scan", "since_days": 60, "sections": {
    "waiting_on_user": [{"subject": "x"}], "inbox_pending": [], "cold_urgent": [],
    "cold_monitor": [], "calendar": []}}, open(snap, "w"))
time.sleep(2)
os.makedirs(os.path.dirname(sidecar), exist_ok=True)
json.dump({"waiting_on_user": [{"subject": "x"}]}, open(sidecar, "w"))
"""


def test_wait_scan_waits_for_the_sidecar_and_propose_says_not_yet_before_it(
        state, sidecar, tmp_path, monkeypatch, capsys):
    import time
    script = tmp_path / "week_review_stub.py"
    script.write_text(TWO_STEP_SCAN, encoding="utf-8")
    monkeypatch.setattr(ws, "_SCAN_SCRIPT", str(script))
    monkeypatch.setattr(ws, "_job_logs_dir", lambda: tmp_path)
    monkeypatch.setenv("STUB_SIDECAR", str(sidecar))

    pid = ws.start_scan(60)
    for _ in range(100):
        if ws.scan_path().exists():
            break
        time.sleep(0.05)
    assert ws.scan_path().exists() and not sidecar.exists()

    # In between: not ready, and propose says "not yet" with a nonzero rc.
    out = ws.wait_scan(timeout=0.3, poll=0.1)
    assert out["ready"] is False and out["have"] == {"snapshot": True, "sidecar": False}
    monkeypatch.setattr(sys, "argv", ["warm_start.py", "propose"])
    with pytest.raises(SystemExit) as e:
        ws.main()
    assert e.value.code == 1
    reply = json.loads(capsys.readouterr().out)
    assert reply["error"] == "the scan has not written its results yet"
    assert reply["ready"] is False and reply["sidecar"] == str(sidecar)

    # After: ready, with both paths, and the sidecar really on disk.
    out = ws.wait_scan(timeout=15, poll=0.1)
    assert out["ready"] is True
    assert out["sidecar"] == str(sidecar) and sidecar.exists()
    assert out["counts"]["waiting_on_user"] == 1
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass


def test_propose_refuses_a_sidecar_older_than_this_scan(state, sidecar, monkeypatch, capsys):
    ws._write_json(sidecar, {"waiting_on_user": []})
    ws._write_json(ws.scan_meta_path(), {"started_ts": 2e12, "pid": os.getpid()})
    assert ws.sidecar_not_ready() == "the scan has not written its results yet"
    monkeypatch.setattr(sys, "argv", ["warm_start.py", "verify"])
    with pytest.raises(SystemExit) as e:
        ws.main()
    assert e.value.code == 1
    assert "not written its results yet" in capsys.readouterr().out
    # The same sidecar after this scan started is fine.
    ws._write_json(ws.scan_meta_path(), {"started_ts": 0, "pid": os.getpid()})
    assert ws.sidecar_not_ready() == ""


# ── The staged proposal survives midnight ────────────────────────────────────
#
# propose stages under today's date folder; apply and verify --picked used to
# read only today's, so a person who stopped at the deals question and came
# back the next morning was told to run propose again.

def _deal(i):
    return {"heading": f"Org{i}: subject {i}", "name": f"Person {i}",
            "email": f"p{i}@org{i}.com", "org": f"Org{i}", "subject": f"subject {i}",
            "section": "waiting_on_user", "account": "Gmail", "business": "ACME",
            "stage": "active", "last_contact": "2026-10-09", "next_action_due": "2026-10-12"}


@pytest.fixture
def staging(state, sidecar, tmp_path, monkeypatch):
    """A vault with a fresh sidecar and an empty warm-start log tree."""
    import config_loader as cl
    logs = tmp_path / "logs"
    monkeypatch.setattr(cl, "logs_dir", lambda: logs)
    ws._write_json(sidecar, _snapshot()["sections"])
    hot = tmp_path / "hotcache.md"
    hot.write_text("# Hot Cache\n\n## Active Threads\n\n## Key Numbers\n", encoding="utf-8")
    monkeypatch.setattr(cl, "hotcache_path", lambda: hot)
    monkeypatch.setattr(cl, "entities_dir", lambda: tmp_path / "entities")
    monkeypatch.setattr(ws, "_existing_entity_names", lambda: set())
    import week_review
    monkeypatch.setattr(week_review, "load_active_thread_emails", lambda: set())

    def stage(day, deals):
        d = logs / "warm-start" / day
        d.mkdir(parents=True, exist_ok=True)
        path = d / "proposal.json"
        path.write_text(json.dumps({"deals": deals, "entities": [], "today": day}),
                        encoding="utf-8")
        return path
    return types.SimpleNamespace(stage=stage, hot=hot, logs=logs)


import types  # noqa: E402


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["warm_start.py", *argv])
    code = 0
    try:
        ws.main()
    except SystemExit as e:
        code = e.code
    return code, json.loads(capsys.readouterr().out)


def test_apply_and_verify_find_yesterdays_proposal_without_a_flag(staging, monkeypatch, capsys):
    yesterday = staging.stage("2026-10-10", [_deal(1), _deal(2)])
    code, out = _run(monkeypatch, capsys, "apply", "--deals", "0,1")
    assert code == 0
    assert out["proposal_path"] == str(yesterday)
    assert out["written"] == ["Org1: subject 1", "Org2: subject 2"]
    assert "Org1: subject 1" in staging.hot.read_text(encoding="utf-8")

    code, out = _run(monkeypatch, capsys, "verify", "--picked", "0,1")
    assert code == 0
    assert out["proposal_path"] == str(yesterday)
    assert out["picked"] == 2 and out["missing"] == []


def test_the_newest_proposal_wins_across_date_folders(staging, monkeypatch, capsys):
    import os
    older = staging.stage("2026-10-09", [_deal(9)])
    newer = staging.stage("2026-10-10", [_deal(1)])
    os.utime(older, (1_000_000, 1_000_000))
    assert ws.newest_proposal_path() == newer
    code, out = _run(monkeypatch, capsys, "apply", "--deals", "0")
    assert out["proposal_path"] == str(newer) and out["written"] == ["Org1: subject 1"]


def test_an_explicit_proposal_path_wins_over_the_newest(staging, monkeypatch, capsys):
    older = staging.stage("2026-10-09", [_deal(9)])
    staging.stage("2026-10-10", [_deal(1)])
    code, out = _run(monkeypatch, capsys, "apply", "--deals", "0", "--proposal", str(older))
    assert code == 0 and out["proposal_path"] == str(older)
    assert out["written"] == ["Org9: subject 9"]
    code, out = _run(monkeypatch, capsys, "verify", "--picked", "0", "--proposal", str(older))
    assert out["proposal_path"] == str(older) and out["landed"] == ["Org9: subject 9"]


def test_no_proposal_anywhere_says_need_propose(staging, monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, "apply", "--deals", "0")
    assert code == 1 and out["need_propose"] is True
    assert "propose" in out["error"] and "propose" in out["hint"]
    code, out = _run(monkeypatch, capsys, "verify", "--picked", "0")
    assert code == 1 and out["need_propose"] is True
    # An explicit path that does not exist is the same answer, never a
    # silent fall back to some other proposal the indices do not belong to.
    staging.stage("2026-10-10", [_deal(1)])
    code, out = _run(monkeypatch, capsys, "apply", "--deals", "0", "--proposal",
                     str(staging.logs / "nowhere.json"))
    assert code == 1 and out["need_propose"] is True
    # verify without --picked needs no proposal at all.
    code, out = _run(monkeypatch, capsys, "verify")
    assert code == 0 and "need_propose" not in out and "proposal_path" not in out


def test_propose_names_the_file_it_staged(staging, monkeypatch, capsys):
    monkeypatch.setattr(ws, "_existing_headings", lambda: [])
    code, out = _run(monkeypatch, capsys, "propose")
    assert code == 0
    assert out["proposal_path"].endswith("proposal.json")
    assert Path(out["proposal_path"]).exists()
    assert ws.newest_proposal_path() == Path(out["proposal_path"])


def test_ingest_start_hands_the_day_window_to_the_prefetch(monkeypatch, tmp_path):
    import warm_start
    seen = {}

    class _Proc:
        pid = 4242

    monkeypatch.setattr(warm_start, "staged_dir", lambda: tmp_path / "staged")
    monkeypatch.setattr(warm_start, "_job_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(warm_start.subprocess, "Popen",
                        lambda cmd, **kw: seen.update(cmd=cmd) or _Proc())
    out = warm_start.start_ingest(200, 60)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--since-days") + 1] == "60"
    assert cmd[cmd.index("--limit") + 1] == "200"
    assert out["pid"] == 4242
    warm_start.start_ingest(10)
    assert "--since-days" not in seen["cmd"]
