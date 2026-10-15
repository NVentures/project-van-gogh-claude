"""The install checklist: shape, invariants, verification from disk, the line."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import setup_state as ss


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    return tmp_path


def test_new_checklist_has_nine_open_boxes_in_order(state_dir):
    data = ss.new("20261001-170000-abcd", "darwin", "0.81.0")
    assert [b["key"] for b in data["boxes"]] == list(ss.KEYS)
    assert len(ss.KEYS) == 9
    assert all(b["status"] == ss.OPEN for b in data["boxes"])
    assert not ss.resolved(data)
    assert ss.first_open(data)["key"] == "plugin"


def test_save_is_atomic_and_load_round_trips(state_dir):
    data = ss.new("r1")
    path = ss.save(data)
    assert path == state_dir / "setup" / "checklist.json"
    assert ss.exists()
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".checklist-")]
    again = ss.load()
    assert again["run_id"] == "r1"
    assert [b["key"] for b in again["boxes"]] == list(ss.KEYS)


def test_load_returns_none_for_missing_or_corrupt(state_dir):
    assert ss.load() is None
    ss.checklist_path().parent.mkdir(parents=True)
    ss.checklist_path().write_text("{not json", encoding="utf-8")
    assert ss.load() is None
    ss.checklist_path().write_text(json.dumps({"boxes": "nope"}), encoding="utf-8")
    assert ss.load() is None


def test_accounts_and_vault_can_never_be_skipped(state_dir):
    data = ss.new("r1")
    with pytest.raises(ss.ChecklistError):
        ss.set_status(data, "accounts", ss.SKIPPED)
    with pytest.raises(ss.ChecklistError):
        ss.set_status(data, "vault", ss.SKIPPED)
    ss.set_status(data, "deals", ss.SKIPPED, "none ticked")
    assert ss.box(data, "deals")["status"] == ss.SKIPPED
    # A hand-edited file cannot smuggle it in either.
    data["boxes"][3]["status"] = ss.SKIPPED
    with pytest.raises(ss.ChecklistError):
        ss.save(data)


def test_save_refuses_a_wrong_box_set(state_dir):
    data = ss.new("r1")
    del data["boxes"][0]
    with pytest.raises(ss.ChecklistError):
        ss.save(data)


def test_resolved_means_every_box_done_skipped_or_not_applicable(state_dir):
    data = ss.new("r1")
    for key in ss.KEYS:
        ss.set_status(data, key, ss.DONE)
    assert ss.resolved(data)
    ss.set_status(data, "meetings", ss.NOT_APPLICABLE, "no notetaker")
    ss.set_status(data, "deals", ss.SKIPPED)
    assert ss.resolved(data)
    ss.set_status(data, "briefings", ss.PENDING, "rendering")
    assert not ss.resolved(data)
    assert [b["key"] for b in ss.open_boxes(data)] == ["briefings"]


def test_one_line_names_count_action_and_resume_command(state_dir):
    data = ss.new("r1")
    for key in ("plugin", "python", "credentials"):
        ss.set_status(data, key, ss.DONE)
    line = ss.one_line(data)
    assert line.startswith("Van Gogh setup: 6 of 9 install steps left.")
    assert "sign in to your accounts" in line
    assert ss.RESUME_COMMAND in line
    assert len(line) <= 120
    # Every possible next box fits the budget.
    for key in ss.KEYS:
        d = ss.new("r1")
        for k in ss.KEYS:
            if k == key:
                break
            ss.set_status(d, k, ss.DONE)
        assert len(ss.one_line(d)) <= 120, key
    for key in ss.KEYS:
        ss.set_status(data, key, ss.DONE)
    assert ss.one_line(data) == ""


def test_one_line_is_empty_when_there_is_no_checklist(state_dir):
    assert ss.one_line() == ""


def test_verify_ticks_from_disk_and_reopens_a_broken_box(state_dir):
    data = ss.new("r1")
    calls = {"n": 0}

    def jobs_ok():
        calls["n"] += 1
        return ss.DONE, "3 jobs loaded"

    def jobs_gone():
        return ss.OPEN, "1 of 3 jobs not loaded"

    def boom():
        raise RuntimeError("scheduler unreadable")

    changed = ss.verify(data, checks={"jobs": jobs_ok, "vault": boom})
    assert changed == ["jobs"]
    assert ss.box(data, "jobs")["status"] == ss.DONE
    assert ss.box(data, "vault")["status"] == ss.OPEN
    assert ss.verify(data, checks={"jobs": jobs_ok}) == []
    assert ss.verify(data, checks={"jobs": jobs_gone}) == ["jobs"]
    assert ss.box(data, "jobs")["status"] == ss.OPEN
    assert ss.box(data, "jobs")["detail"] == "1 of 3 jobs not loaded"
    assert calls["n"] == 2


def test_verify_leaves_skipped_and_not_applicable_alone(state_dir):
    data = ss.new("r1")
    ss.set_status(data, "deals", ss.SKIPPED)
    ss.set_status(data, "meetings", ss.NOT_APPLICABLE)
    changed = ss.verify(data, checks={"deals": lambda: (ss.DONE, "x"),
                                      "meetings": lambda: (ss.OPEN, "x")})
    assert changed == []
    assert ss.box(data, "deals")["status"] == ss.SKIPPED
    assert ss.box(data, "meetings")["status"] == ss.NOT_APPLICABLE


def test_default_checks_read_the_state_dir(state_dir, monkeypatch):
    data = ss.new("r1")
    # The jobs check reads the real OS scheduler, which on a developer's Mac
    # may hold loaded jobs; give it no opinion so this test is hermetic. The
    # plugin check asks the real CLI when it is on PATH: pretend it is not.
    monkeypatch.setattr(ss, "_claude_on_path", lambda: None)
    checks = {**ss.CHECKS, "jobs": lambda: (None, "")}
    # Nothing on disk: every default check says open or has no opinion.
    changed = ss.verify(data, checks=checks)
    assert changed == []
    assert ss.box(data, "credentials")["status"] == ss.OPEN
    # Plant a credential and a token, then re-verify.
    (state_dir / "google_client_secret.json").write_text(
        json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}),
        encoding="utf-8")
    (state_dir / ".env").write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\n", encoding="utf-8")
    changed = ss.verify(data, checks=checks)
    assert set(changed) == {"credentials", "accounts"}
    assert ss.box(data, "credentials")["detail"] == "Google"
    assert ss.box(data, "accounts")["detail"] == "1 signed in"


def test_ledger_and_stamp(state_dir):
    ss.log_event("start", run_id="r1", **{"class": "machine"})
    ss.log_event("step_begin", run_id="r1", step="accounts", **{"class": "human"})
    rows = ss.read_ledger()
    assert [r["event"] for r in rows] == ["start", "step_begin"]
    assert rows[1]["step"] == "accounts" and rows[1]["class"] == "human"
    assert ss.read_stamp() is None
    ss.write_stamp({"run_id": "r1", "total": "14:45"})
    assert ss.read_stamp()["run_id"] == "r1"
    assert ss.read_stamp()["completed"]
    assert ss.remove_stamp() is True
    assert ss.remove_stamp() is False


def test_remove_and_tick_list(state_dir):
    data = ss.new("r1")
    ss.set_status(data, "plugin", ss.DONE, "van-gogh 0.81.0")
    ss.set_status(data, "meetings", ss.NOT_APPLICABLE)
    ss.set_status(data, "deals", ss.SKIPPED)
    ss.set_status(data, "briefings", ss.PENDING)
    lines = ss.tick_list(data)
    assert lines[0] == "[x] Plugin installed (van-gogh 0.81.0)"
    assert lines[6] == "[s] Deals and people seeded"
    assert lines[7] == "[-] Meetings filed"
    assert lines[8] == "[~] First briefings shown"
    ss.save(data)
    assert ss.remove() is True
    assert ss.remove() is False
    assert not ss.exists()


def test_logs_dir_is_never_under_documents(state_dir, monkeypatch):
    monkeypatch.delenv("VAN_GOGH_SETUP_LOGS", raising=False)
    path = ss.logs_dir()
    assert "Documents" not in path.parts
    monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(state_dir / "logs"))
    assert ss.logs_dir() == state_dir / "logs"


def test_cli_status_and_line(state_dir, capsys):
    assert ss.main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": False, "stamp": False}
    ss.save(ss.new("r9"))
    assert ss.main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] and out["total"] == 9 and len(out["lines"]) == 9
    assert ss.main(["line"]) == 0
    assert "Van Gogh setup: 9 of 9 install steps left." in capsys.readouterr().out


def test_the_plugin_box_asks_the_cli_before_trusting_the_pointer(state_dir, monkeypatch):
    """The plugin-root pointer survives an uninstall (every script run
    rewrites it), so on its own it must never tick the box when the CLI can
    be asked. The pointer is the fallback only when `claude` is not on PATH."""
    (state_dir / "plugin-root").write_text(
        str(Path(__file__).resolve().parent.parent), encoding="utf-8")
    monkeypatch.setattr(ss, "_claude_on_path", lambda: "/fake/claude")
    monkeypatch.setattr(ss, "_plugin_listed", lambda claude: False)
    assert ss._check_plugin() == (ss.OPEN, "plugin not installed (claude plugin list)")
    monkeypatch.setattr(ss, "_plugin_listed", lambda claude: True)
    assert ss._check_plugin()[0] == ss.DONE
    # The CLI could not answer: the pointer decides.
    monkeypatch.setattr(ss, "_plugin_listed", lambda claude: None)
    assert ss._check_plugin()[0] == ss.DONE
    monkeypatch.setattr(ss, "_claude_on_path", lambda: None)
    assert ss._check_plugin()[0] == ss.DONE


def test_plugin_listed_reads_the_cli_reply(monkeypatch):
    import subprocess
    reply = '[{"id": "van-gogh@van-gogh", "version": "0.81.0"}, {"id": "other@x"}]'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, reply, ""))
    assert ss._plugin_listed("/fake/claude") is True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "[]", ""))
    assert ss._plugin_listed("/fake/claude") is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"))
    assert ss._plugin_listed("/fake/claude") is None


def test_the_nudge_line_before_the_plugin_exists_points_at_the_terminal(state_dir):
    """There is no /van-gogh command to type before the plugin is installed."""
    data = ss.new("r1")
    for key in ("plugin", "python"):
        line = ss.one_line(data)
        assert "Terminal (PowerShell on Windows)" in line and ss.RESUME_COMMAND not in line
        assert len(line) <= 120
        ss.set_status(data, key, ss.DONE)
    assert ss.RESUME_COMMAND in ss.one_line(data)


def test_the_accounts_check_reads_the_expiry_stamp_beside_each_token(state_dir):
    """A token whose stated expiry has passed is not a working sign-in; a
    token with a future stamp, or with none, is. No network anywhere."""
    from datetime import datetime, timezone
    env = state_dir / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\nMS_GRAPH_REFRESH_TOKEN_WORK=tok2\n", encoding="utf-8")
    assert ss._check_accounts() == (ss.DONE, "2 signed in"), "no stamp counts as present"
    env.write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\nGOOGLE_REFRESH_EXPIRES_GMAIL=2999-01-01T00:00:00Z\n"
                   "MS_GRAPH_REFRESH_TOKEN_WORK=tok2\n", encoding="utf-8")
    assert ss._check_accounts() == (ss.DONE, "2 signed in")
    env.write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\nGOOGLE_REFRESH_EXPIRES_GMAIL=2026-10-08T17:00:00Z\n"
                   "MS_GRAPH_REFRESH_TOKEN_WORK=tok2\n", encoding="utf-8")
    before = datetime(2026, 10, 8, 16, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 8, 17, 1, tzinfo=timezone.utc)
    assert ss._check_accounts(now=before)[0] == ss.DONE
    assert ss._check_accounts(now=after) == (
        ss.OPEN, "the Google sign-in for Gmail expired on October 8")
    env.write_text("MS_GRAPH_REFRESH_TOKEN_WORK=tok2\nMS_GRAPH_REFRESH_EXPIRES_WORK=2020-01-02T00:00:00+00:00\n",
                   encoding="utf-8")
    assert ss._check_accounts() == (ss.OPEN, "the Microsoft sign-in for Work expired on January 2")
    env.write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\nGOOGLE_REFRESH_EXPIRES_GMAIL=not a date\n", encoding="utf-8")
    assert ss._check_accounts()[0] == ss.DONE, "an unreadable stamp is no evidence of expiry"
    data = ss.new("r1")
    ss.set_status(data, "accounts", ss.DONE)
    env.write_text("GOOGLE_REFRESH_TOKEN_GMAIL=tok\nGOOGLE_REFRESH_EXPIRES_GMAIL=2020-01-02T00:00:00Z\n", encoding="utf-8")
    assert ss.verify(data, checks={"accounts": ss._check_accounts}) == ["accounts"]
    assert ss.box(data, "accounts")["status"] == ss.OPEN
