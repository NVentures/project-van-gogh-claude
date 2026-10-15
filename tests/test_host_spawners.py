"""Every headless spawner, under both clients, through the `host` fixture.

Each test stubs `subprocess.run` and the binary resolver, then reads the argv
and stdin the spawner really builds, so a spawner that forgets the seam is
caught here for the client it forgot. The Claude argv are asserted against
the exact 0.86.1 forms; the Codex ones against the flags the design names.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host as hostmod  # noqa: E402

CODEX_REPLY = json.dumps({"type": "thread.started", "thread_id": "th-1"}) + "\n" + json.dumps(
    {"type": "item.completed", "item": {"type": "agent_message", "text": "rendered"}}) + "\n" + json.dumps(
    {"type": "turn.completed", "usage": {}}) + "\n"


class _Done:
    returncode = 0
    stderr = ""

    def __init__(self, stdout):
        self.stdout = stdout


@pytest.fixture
def spawn(monkeypatch, host):
    """Capture every subprocess.run; the reply is shaped for the client."""
    seen = []

    def fake(argv, **kw):
        seen.append((list(argv), kw))
        return _Done(CODEX_REPLY if host == "codex" else "rendered")
    monkeypatch.setattr(subprocess, "run", fake)
    return seen


def _bin(h):
    return "/x/claude" if h == "claude" else "/x/codex"


def test_skill_run(spawn, host, monkeypatch):
    import skill_run
    monkeypatch.setattr(skill_run.claude_update, "ensure_current", lambda *a, **k: {})
    monkeypatch.setattr(skill_run.config_loader, "repo_root", lambda: ROOT)
    skill_run.run_skill("vault-audit", _bin(host), "--auto")
    argv, kw = spawn[-1]
    assert argv == hostmod.skill_cmd("vault-audit", "--auto", _bin(host), host)
    assert kw["stdin"] is subprocess.DEVNULL and "ANTHROPIC_API_KEY" not in kw["env"]
    assert kw["env"]["VAN_GOGH_UNATTENDED"] == "1"
    if host == "claude":
        assert argv == ["/x/claude", "-p", "/van-gogh:vault-audit --auto",
                        "--permission-mode", "bypassPermissions"]
    else:
        assert argv[1] == "exec" and argv[-1] == "$van-gogh:vault-audit --auto"
        assert "--skip-git-repo-check" in argv and "--dangerously-bypass-approvals-and-sandbox" in argv


def test_digest_prep_and_note_renderers(spawn, host, monkeypatch):
    import digest_send, prep_send, note_send
    for mod in (digest_send, prep_send, note_send):
        monkeypatch.setattr(mod.config_loader, "repo_root", lambda: ROOT)
    digest_send.render_briefing("morning-coffee", _bin(host)) if hasattr(digest_send, "render_briefing") else None
    prep_send.render_prep("next", _bin(host)) if hasattr(prep_send, "render_prep") else None
    evt = Path("/tmp/evt.json")          # spelled the OS way on Windows too
    note_send.render_note(evt, _bin(host))
    argv, kw = spawn[-1]
    assert argv == hostmod.skill_cmd("note", f'"{evt}"', _bin(host), host)
    assert kw["stdin"] is subprocess.DEVNULL
    for argv, _ in spawn:
        assert argv[0] == _bin(host)
        assert ("-p" in argv) == (host == "claude")
        assert ("exec" in argv) == (host == "codex")


def test_mechanic_and_research_sessions(host, monkeypatch, tmp_path):
    import repair, operator_research
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    for mod in (repair, operator_research):
        argv = mod._command("look at it", _bin(host))
        stdin = mod._stdin("look at it", _bin(host))
        assert argv[0] == _bin(host)
        if host == "claude":
            assert argv[1:3] == ["-p", "look at it"] and "--agent" in argv and stdin is None
            assert "--plugin-dir" in argv and "--disable-slash-commands" in argv
        else:
            assert argv[1] == "exec" and argv[argv.index("-s") + 1] == "workspace-write"
            assert "look at it" not in argv
            assert stdin.endswith("look at it") and "Handing to" in stdin or "rule" in stdin.lower()
            assert any("CLAUDE_PLUGIN_ROOT" in a for a in argv)
    r_argv = repair._command("x", _bin(host))
    assert ("--add-dir" in r_argv) and str(tmp_path) in r_argv
    assert "--add-dir" not in operator_research._command("x", _bin(host))


def test_completion_and_readonly(spawn, host, monkeypatch):
    import claude_cli
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: _bin(host))
    monkeypatch.setattr(hostmod, "_codex_settings", lambda: {})
    r = claude_cli.run_claude("classify this", claude_cli.HAIKU_MODEL, timeout=5)
    argv, kw = spawn[-1]
    assert argv[0] == _bin(host) and kw["input"] == "classify this"
    assert r.stdout == "rendered" and r.returncode == 0
    if host == "claude":
        assert argv[1:4] == ["-p", "--model", claude_cli.HAIKU_MODEL]
    else:
        assert "--ignore-user-config" in argv and "model_reasoning_effort='low'" in argv
    r = claude_cli.run_claude_readonly("look", ROOT, agent="vincent", plugin_dir=ROOT,
                                       session_id="sid")
    argv, kw = spawn[-1]
    assert kw["cwd"] == str(ROOT) and r.stdout == "rendered"
    if host == "claude":
        assert "--agent" in argv and kw["input"] == "look" and "--session-id" in argv
    else:
        assert "--agent" not in argv and kw["input"].endswith("look")
        assert kw["input"].startswith(hostmod.agent_prompt("vincent")[:30])
        assert hostmod.thread_id(r) == "th-1"


def test_vincent_keeps_the_clients_own_thread_id(spawn, host, monkeypatch, tmp_path):
    import vincent
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(vincent.config_loader, "vault", lambda: tmp_path)
    monkeypatch.setattr(vincent.config_loader, "workbench_chat_model", lambda: "")
    monkeypatch.setattr(vincent, "situation", lambda now: {})
    monkeypatch.setattr(vincent, "_turn_prompt", lambda sit, text, channel: text)
    monkeypatch.setattr(vincent, "plugin_root", lambda: ROOT)
    import claude_cli
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: _bin(host))
    out = vincent.answer("hello", "test:chan")
    assert out["reply"]
    saved = json.loads((tmp_path / "vincent-sessions.json").read_text())["test:chan"]["id"]
    if host == "codex":
        assert saved == "th-1"
    else:
        assert saved and saved != "th-1"


def test_auth_probe_and_cli_update(spawn, host, monkeypatch):
    import claude_update
    monkeypatch.setattr(claude_update, "claude_bin", lambda: _bin(host))
    monkeypatch.setattr(claude_update, "cli_version", lambda c=None: "1.0.0")
    claude_update.update_now()
    assert spawn[-1][0] == [_bin(host), "update"]
    spawn.clear()
    if host == "claude":
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done('{"loggedIn": true}'))
    monkeypatch.setattr(hostmod, "_codex_settings", lambda: {})
    # The watcher's probe is stubbed by conftest (no live sign-in probes), so
    # the seam is asked directly, the way the watcher asks it.
    assert hostmod.auth_ok(_bin(host), host) is True
    if host == "codex":
        argv, kw = spawn[-1]
        assert argv[:2] == [_bin(host), "exec"] and "--ignore-user-config" in argv


def test_a_list_schema_is_wrapped_for_codex_and_unwrapped_after(host, monkeypatch, tmp_path):
    import claude_cli
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: _bin(host))
    monkeypatch.setattr(hostmod, "_codex_settings", lambda: {})
    seen = {}

    def fake(argv, **kw):
        seen["argv"] = argv
        if host == "codex":
            body = json.dumps({"items": [{"id": 0}]})
            return _Done(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": body}})
                         + "\n" + json.dumps({"type": "turn.completed"}) + "\n")
        return _Done('[{"id": 0}]')
    monkeypatch.setattr(subprocess, "run", fake)
    r = claude_cli.run_claude("classify", claude_cli.HAIKU_MODEL, timeout=5,
                              schema={"type": "array", "items": {"type": "object"}})
    assert json.loads(r.stdout) == [{"id": 0}]
    if host == "codex":
        path = seen["argv"][seen["argv"].index("--output-schema") + 1]
        doc = json.loads(Path(path).read_text())
        assert doc["type"] == "object" and doc["properties"]["items"]["type"] == "array"
        assert doc["required"] == ["items"] and doc["additionalProperties"] is False
    else:
        assert "--output-schema" not in seen["argv"]
