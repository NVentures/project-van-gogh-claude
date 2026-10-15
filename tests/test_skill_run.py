"""skill_run.py: the headless skill runner, under both clients.

The Claude argv is pinned to the literal 0.86.1 form; the Codex argv to the
flags the design names; the run row every run leaves carries the client.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host as hostmod  # noqa: E402
import run_ledger  # noqa: E402
import skill_run  # noqa: E402


class _Done:
    returncode, stdout, stderr = 0, "rendered\n", ""


def test_claude_argv_is_the_0_86_1_form(monkeypatch):
    monkeypatch.setenv(hostmod.HOST_ENV, "claude")
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.update(argv=argv, kw=kw) or _Done())
    monkeypatch.setattr(skill_run.config_loader, "repo_root", lambda: ROOT)
    skill_run._spawn("morning-coffee", "/bin/claude", "", 10)
    assert seen["argv"] == ["/bin/claude", "-p", "/van-gogh:morning-coffee",
                            "--permission-mode", "bypassPermissions"]
    assert seen["kw"]["stdin"] is subprocess.DEVNULL
    assert seen["kw"]["env"]["VAN_GOGH_UNATTENDED"] == "1"
    assert "ANTHROPIC_API_KEY" not in seen["kw"]["env"]


def test_codex_argv_carries_the_design_flags(monkeypatch):
    monkeypatch.setenv(hostmod.HOST_ENV, "codex")
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.update(argv=argv, kw=kw) or _Done())
    monkeypatch.setattr(skill_run.config_loader, "repo_root", lambda: ROOT)
    skill_run._spawn("vault-audit", "/bin/codex", "--auto", 10)
    argv = seen["argv"]
    assert argv[:2] == ["/bin/codex", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("-C") + 1] == str(hostmod._PLUGIN_ROOT)
    assert any(a.startswith("shell_environment_policy.set.CLAUDE_PLUGIN_ROOT=") for a in argv)
    assert argv[-1] == "$van-gogh:vault-audit --auto"
    assert seen["kw"]["stdin"] is subprocess.DEVNULL


def test_a_failed_run_names_the_command_as_typed(host, monkeypatch):
    class _Bad:
        returncode, stdout, stderr = 1, "", "boom"
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Bad())
    monkeypatch.setattr(skill_run.config_loader, "repo_root", lambda: ROOT)
    monkeypatch.setattr(skill_run.claude_update, "heal_if_stale", lambda *a, **k: False)
    try:
        skill_run._spawn("week", "/bin/x", "", 10)
    except RuntimeError as exc:
        text = str(exc)
    else:
        raise AssertionError("no error")
    if host == "claude":
        assert text.startswith("claude -p /van-gogh:week exited 1")
    else:
        assert text.startswith("codex exec $van-gogh:week exited 1")


def test_the_run_row_carries_the_host(host, monkeypatch, tmp_path):
    monkeypatch.setattr(run_ledger, "runs_path", lambda: tmp_path / "runs.jsonl")
    monkeypatch.setattr(run_ledger, "clear_started", lambda job: None)
    run_ledger.record_run("scheduled.week", "2026-10-14T07:00:00", "2026-10-14T07:01:00", 0, "")
    row = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert row["host"] == host
    assert row["job"] == "scheduled.week" and row["rc"] == 0


# ── The hourly meeting filing starts a model only when a meeting waits ──────

import meeting_ingest  # noqa: E402
import notetaker  # noqa: E402


class _Notetaker:
    def __init__(self, ids=(), error=None):
        self.ids, self.error = list(ids), error

    def iter_stubs(self, limit=30):
        if self.error:
            raise self.error
        return iter([{"id": i, "title": f"call {i}"} for i in self.ids])


def _hourly(monkeypatch, tmp_path, provider, filed=(), queue=()):
    monkeypatch.setattr(run_ledger, "runs_path", lambda: tmp_path / "runs.jsonl")
    monkeypatch.setattr(run_ledger, "mark_started", lambda job: None)
    monkeypatch.setattr(run_ledger, "clear_started", lambda job: None)
    monkeypatch.setattr(notetaker, "provider", lambda name=None: provider)
    monkeypatch.setattr(meeting_ingest, "get_ingested_meeting_ids", lambda: set(filed))
    monkeypatch.setattr(meeting_ingest, "read_deep_read_queue", lambda: list(queue))
    spawned = []
    monkeypatch.setattr(skill_run, "run_skill", lambda skill, *a, **k: spawned.append(skill))
    rc = skill_run.main(["meeting-ingest", "--claude", "/bin/claude"])
    row = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    return rc, spawned, row


def test_an_hour_with_nothing_new_starts_no_model(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_run, "_history_due", lambda: False)
    rc, spawned, row = _hourly(monkeypatch, tmp_path, _Notetaker(["a", "b"]), filed={"a", "b"})
    assert rc == 0 and spawned == []
    assert row["job"] == "scheduled.meeting-ingest" and row["rc"] == 0
    assert row["detail"] == "no new meetings"


def test_a_new_meeting_starts_the_filing(monkeypatch, tmp_path):
    rc, spawned, row = _hourly(monkeypatch, tmp_path, _Notetaker(["a", "b"]), filed={"a"})
    assert rc == 0 and spawned == ["meeting-ingest"] and row["rc"] == 0


def test_a_meeting_waiting_for_its_full_read_starts_the_filing(monkeypatch, tmp_path):
    rc, spawned, _ = _hourly(monkeypatch, tmp_path, _Notetaker(["a"]), filed={"a"},
                             queue=[{"meeting_id": "a"}])
    assert spawned == ["meeting-ingest"]


def test_no_notetaker_is_a_quiet_hour_not_a_failure(monkeypatch, tmp_path):
    err = notetaker.NotetakerError("GRANOLA_API_KEY is not set")
    rc, spawned, row = _hourly(monkeypatch, tmp_path, _Notetaker(error=err))
    assert rc == 0 and spawned == [] and row["detail"] == "no notetaker connected"


def test_a_notetaker_that_cannot_be_reached_fails_without_its_text(monkeypatch, tmp_path):
    err = notetaker.NotetakerError("HTTP error 401 for https://x/api?key=sk-PLANTED")
    rc, spawned, row = _hourly(monkeypatch, tmp_path, _Notetaker(error=err))
    assert rc == 1 and spawned == []
    assert "could not list meetings (NotetakerError)" in row["detail"]
    assert "PLANTED" not in row["detail"]


def test_only_meeting_filing_is_checked_first():
    assert set(skill_run.WORK_CHECKS) == {"meeting-ingest"}
    assert skill_run.has_work("ingest-workspace") == (True, "")


def test_a_quiet_hour_after_setup_goes_to_the_older_meetings(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_run, "_history_due", lambda: True)
    monkeypatch.setattr(skill_run, "_history_step",
                        lambda: "history: filed 6, 40 older meetings left")
    rc, spawned, row = _hourly(monkeypatch, tmp_path, _Notetaker(["a"]), filed={"a"})
    assert rc == 0 and spawned == []
    assert row["detail"] == "history: filed 6, 40 older meetings left"


def test_new_meetings_come_before_the_history(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_run, "_history_due", lambda: True)
    monkeypatch.setattr(skill_run, "_history_step",
                        lambda: (_ for _ in ()).throw(AssertionError("history ran")))
    rc, spawned, _ = _hourly(monkeypatch, tmp_path, _Notetaker(["a", "b"]), filed={"a"})
    assert rc == 0 and spawned == ["meeting-ingest"]


def test_the_history_waits_for_setup_to_finish(monkeypatch, tmp_path):
    import setup_state
    monkeypatch.setattr(setup_state, "load", lambda: {"boxes": []})
    monkeypatch.setattr(setup_state, "read_stamp", lambda: None)
    assert skill_run._history_due() is False
    monkeypatch.setattr(setup_state, "load", lambda: None)
    monkeypatch.setattr(setup_state, "read_stamp", lambda: {"run_id": "x"})
    monkeypatch.setattr(meeting_ingest, "read_history", lambda: {})
    assert skill_run._history_due() is True
    monkeypatch.setattr(meeting_ingest, "read_history", lambda: {"done": True})
    assert skill_run._history_due() is False
