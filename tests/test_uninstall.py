"""The uninstall step under both clients: `setup_flow reset` clears the
client's own leftovers and names the one command that removes the plugin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host as hostmod  # noqa: E402
import setup_flow  # noqa: E402


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    monkeypatch.setattr(setup_flow, "_nudge_job", lambda action: {"ok": True})
    monkeypatch.setattr(setup_flow, "_keep_awake", lambda action: None)
    return tmp_path, state


def _reset(capsys):
    class A: pass
    rc = setup_flow.cmd_reset(A())
    out = capsys.readouterr().out
    return rc, json.loads(out[out.index("{"):])


def test_reset_under_codex_removes_block_and_host_file(scratch, monkeypatch, capsys):
    tmp, state = scratch
    monkeypatch.setenv(hostmod.HOST_ENV, "codex")
    hostmod.record("codex")
    cfg = tmp / "codex" / "config.toml"
    cfg.write_text('[plugins."van-gogh@van-gogh"]\nenabled = true\n', encoding="utf-8")
    assert hostmod.sync_codex_config("/root", ["/v"], path=cfg) == "written"
    rc, out = _reset(capsys)
    assert rc == 0 and out["ok"]
    assert out["codex_block"] is True and out["host_file"] is True
    assert hostmod.BLOCK_BEGIN not in cfg.read_text() and '[plugins."van-gogh@van-gogh"]' in cfg.read_text()
    assert not (state / "host").exists()
    assert out["plugin_remove_command"] == "codex plugin remove van-gogh@van-gogh"


def test_reset_under_claude_names_the_claude_command(scratch, monkeypatch, capsys):
    tmp, state = scratch
    monkeypatch.setenv(hostmod.HOST_ENV, "claude")
    hostmod.record("claude")
    rc, out = _reset(capsys)
    assert out["codex_block"] is False and out["host_file"] is True
    assert out["plugin_remove_command"] == "claude plugin uninstall van-gogh@van-gogh"


def test_remove_argv_both_hosts():
    assert hostmod.plugin_remove_argv("/c", "claude") == ["/c", "plugin", "uninstall", "van-gogh@van-gogh"]
    assert hostmod.plugin_remove_argv("/x", "codex") == ["/x", "plugin", "remove", "van-gogh@van-gogh"]


def test_the_uninstall_skill_tells_the_person_the_command():
    text = (ROOT / "skills" / "uninstall-van-gogh" / "SKILL.md").read_text(encoding="utf-8")
    assert "plugin_remove_command" in text and "codex plugin remove" in text


def _stub_binary(tmp_path, name="codex-stub", rc=0, say="removed"):
    """A script that records its argv, says one line and exits `rc`; a .cmd
    file on Windows, a shell script elsewhere."""
    log = tmp_path / "argv.log"
    if sys.platform == "win32":
        stub = tmp_path / (name + ".cmd")
        stub.write_text(f"@echo off\r\necho %* > \"{log}\"\r\necho {say}\r\nexit /b {rc}\r\n", encoding="utf-8")
    else:
        stub = tmp_path / name
        stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > " + str(log).replace(" ", "\\ ")
                        + f"\necho {say}" + (" >&2" if rc else "") + f"\nexit {rc}\n")
        stub.chmod(0o755)
    return stub, log


def _remove_plugin(capsys):
    class A: pass
    rc = setup_flow.cmd_remove_plugin(A())
    out = capsys.readouterr().out
    return rc, json.loads(out[out.index("{"):])


def test_remove_plugin_under_codex_runs_codex_plugin_remove(scratch, monkeypatch, capsys):
    tmp, state = scratch
    stub, log = _stub_binary(tmp)
    monkeypatch.setenv(hostmod.HOST_ENV, "codex")
    monkeypatch.setenv("VAN_GOGH_CODEX_BIN", str(stub))
    rc, out = _remove_plugin(capsys)
    assert rc == 0 and out["ok"] is True
    assert out["command"] == "codex plugin remove van-gogh@van-gogh"
    assert log.read_text().split() == ["plugin", "remove", "van-gogh@van-gogh"]


def test_remove_plugin_fails_open_when_the_client_refuses(scratch, monkeypatch, capsys):
    """A client that exits nonzero leaves ok false, the command for the
    person to run by hand, and what the client said."""
    tmp, state = scratch
    stub, _log = _stub_binary(tmp, rc=3, say="plugin not installed")
    monkeypatch.setenv(hostmod.HOST_ENV, "codex")
    monkeypatch.setenv("VAN_GOGH_CODEX_BIN", str(stub))
    rc, out = _remove_plugin(capsys)
    assert out["ok"] is False and out["returncode"] == 3
    assert out["command"] == "codex plugin remove van-gogh@van-gogh"
    assert "plugin not installed" in out["output"]


def test_scheduler_uninstall_removes_the_codex_jobs(tmp_path, monkeypatch, capsys):
    """The jobs a Codex install wrote (VAN_GOGH_HOST=codex in every plist)
    are removed by the same uninstall, every row, enabled or not."""
    import subprocess as _sp
    import job_registry
    import scheduler_setup as sch
    monkeypatch.setenv(hostmod.HOST_ENV, "codex")
    agents = tmp_path / "LaunchAgents"; agents.mkdir()
    monkeypatch.setattr(sch, "_plist_path", lambda job: agents / f"{job.mac_label}.plist")
    monkeypatch.setattr(sch, "remove_legacy_jobs", lambda p: None)
    monkeypatch.setattr(sch, "_run", lambda cmd: _sp.CompletedProcess(cmd, 0, "", ""))
    jobs = job_registry.jobs()
    assert jobs, "the registry names jobs"
    for job in jobs:
        (agents / f"{job.mac_label}.plist").write_text(
            "<plist><string>VAN_GOGH_HOST=codex</string></plist>", encoding="utf-8")
    assert sch.cmd_uninstall("darwin") == 0
    left = sorted(p.name for p in agents.iterdir())
    assert left == [], left


def test_the_uninstall_skill_runs_the_remove_under_codex_only():
    text = (ROOT / "skills" / "uninstall-van-gogh" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "remove-plugin" in flat and "Codex only" in flat
    assert "Under Claude skip this step" in flat
