"""app/host.py: which client, which binary, which words, and the config block."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import host  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv(host.HOST_ENV, raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv(host.CODEX_BIN_ENV, raising=False)
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    return tmp_path


def test_default_is_claude(clean_env):
    assert host.current() == "claude" and host.is_claude() and not host.is_codex()


def test_env_wins_over_file_and_signal(clean_env, monkeypatch):
    host.record("codex")
    monkeypatch.setenv("CODEX_THREAD_ID", "t")
    monkeypatch.setenv(host.HOST_ENV, "claude")
    assert host.current() == "claude"


def test_host_file_wins_over_signal(clean_env, monkeypatch):
    host.record("claude")
    monkeypatch.setenv("CODEX_THREAD_ID", "t")
    assert host.current() == "claude"
    host.record("codex")
    assert host.current() == "codex"
    assert (clean_env / "host").read_text() == "codex\n"


def test_codex_session_signal(clean_env, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "01abc")
    assert host.current() == "codex"


def test_garbage_in_file_or_env_falls_through(clean_env, monkeypatch):
    (clean_env / "host").write_text("gemini\n")
    monkeypatch.setenv(host.HOST_ENV, "nope")
    assert host.current() == "claude"
    with pytest.raises(ValueError):
        host.record("gemini")


def test_cmd_spelling():
    assert host.cmd("week", "claude") == "/van-gogh:week"
    assert host.cmd("week", "codex") == "$van-gogh:week"
    assert host.cmd('note "p q"', "codex") == '$van-gogh:note "p q"'


def test_claude_skill_argv_is_the_0_86_1_form():
    assert host.skill_cmd("morning-coffee", binary="C", host="claude") == [
        "C", "-p", "/van-gogh:morning-coffee", "--permission-mode", "bypassPermissions"]
    assert host.skill_cmd("note", 'x "y"', binary="C", host="claude")[2] == '/van-gogh:note x "y"'


def test_codex_skill_argv(monkeypatch):
    argv = host.skill_cmd("morning-coffee", binary="X", host="codex")
    assert argv[:2] == ["X", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[-1] == "$van-gogh:morning-coffee"
    override = argv[argv.index("-c") + 1]
    assert override.startswith("shell_environment_policy.set.CLAUDE_PLUGIN_ROOT=")
    assert str(host._PLUGIN_ROOT) in override


def test_toml_literal_keeps_windows_backslashes():
    assert host._toml_str(r"C:\Users\x\.codex") == r"'C:\Users\x\.codex'"
    assert host._toml_str("it's") == '"it\'s"'


def test_claude_completion_argv_unchanged():
    assert host.completion_cmd("m", binary="C", host="claude") == [
        "C", "-p", "--model", "m", "--tools", "", "--strict-mcp-config",
        "--disable-slash-commands"]


def test_codex_completion_argv(monkeypatch):
    monkeypatch.setattr(host, "_codex_settings", lambda: {})
    argv = host.completion_cmd("claude-haiku-4-5-20251001", binary="X", host="codex")
    for flag in ("--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "model_reasoning_effort='low'" in argv
    assert "-m" not in argv
    big = host.completion_cmd("claude-sonnet-5", binary="X", host="codex")
    assert not any("model_reasoning_effort" in a for a in big)
    monkeypatch.setattr(host, "_codex_settings", lambda: {"model": "gpt-x", "cheap_effort": "none"})
    argv = host.completion_cmd("claude-haiku-4-5-20251001", binary="X", host="codex",
                               schema_file="/s.json")
    assert argv[argv.index("-m") + 1] == "gpt-x"
    assert "model_reasoning_effort='none'" in argv
    assert argv[argv.index("--output-schema") + 1] == "/s.json"


def test_claude_readonly_argv_unchanged():
    assert host.readonly_cmd("m", "vincent", "/p", "sid", None, binary="C", host="claude") == [
        "C", "-p", "--model", "m", "--agent", "vincent", "--plugin-dir", "/p",
        "--session-id", "sid", "--restricted", "--tools", "Read,Grep,Glob",
        "--permission-prompts", "none", "--strict-mcp-config", "--disable-slash-commands"]
    assert "--resume" in host.readonly_cmd(resume="r", binary="C", host="claude")


def test_codex_readonly_and_resume(monkeypatch):
    monkeypatch.setattr(host, "_codex_settings", lambda: {})
    argv = host.readonly_cmd(binary="X", host="codex")
    assert argv[:2] == ["X", "exec"] and argv[argv.index("-s") + 1] == "read-only"
    argv = host.readonly_cmd(resume="thread-1", binary="X", host="codex")
    assert argv[:3] == ["X", "exec", "resume"] and argv[-1] == "thread-1"
    assert "-s" not in argv


def test_agent_prompt_strips_frontmatter():
    body = host.agent_prompt("vincent")
    assert not body.startswith("---")
    assert "Handing to" in body


def test_codex_agent_cmd_prefixes_the_agent_file(tmp_path):
    argv, stdin = host.agent_cmd("Fix the morning job.", "van-gogh-mechanic",
                                 host._PLUGIN_ROOT, [tmp_path], binary="X", host="codex")
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=true" in argv
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    assert stdin.endswith("Fix the morning job.")
    assert stdin.startswith(host.agent_prompt("van-gogh-mechanic")[:40])
    assert not any(a == "Fix the morning job." for a in argv)


def test_claude_agent_cmd_unchanged(tmp_path):
    argv, stdin = host.agent_cmd("p", "van-gogh-mechanic", "/root", [tmp_path],
                                 binary="C", host="claude")
    assert argv == ["C", "-p", "p", "--agent", "van-gogh-mechanic", "--plugin-dir", "/root",
                    "--add-dir", str(tmp_path), "--permission-mode", "bypassPermissions",
                    "--disable-slash-commands"]
    assert stdin is None


class _R:
    def __init__(self, stdout, rc=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, rc, stderr


def _jsonl(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_finish_codex_reads_the_last_agent_message():
    out = _jsonl({"type": "thread.started", "thread_id": "th1"},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "[1, 2]"}},
                 {"type": "turn.completed", "usage": {}})
    r = host.finish(_R(out), host="codex")
    assert r.stdout == "[1, 2]" and r.returncode == 0
    assert host.thread_id(_R(out)) == "th1"


def test_finish_codex_surfaces_errors_and_forbids_commands():
    failed = _jsonl({"type": "thread.started", "thread_id": "t"},
                    {"type": "error", "message": "{\"status\":400}"},
                    {"type": "turn.failed", "error": {"message": "bad"}})
    r = host.finish(_R(failed), host="codex")
    assert r.returncode == 1 and "400" in r.stderr and r.stdout == ""
    ran = _jsonl({"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "[]"}},
                 {"type": "turn.completed"})
    r = host.finish(_R(ran), forbid_commands=True, host="codex")
    assert r.returncode == 1 and r.stdout == "" and "ran a command" in r.stderr
    r = host.finish(_R(ran), forbid_commands=False, host="codex")
    assert r.returncode == 0 and r.stdout == "[]"
    bypass_note = _jsonl({"type": "item.completed", "item": {"type": "error", "message":
                          "`--dangerously-bypass-hook-trust` is enabled."}},
                         {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
                         {"type": "turn.completed"})
    assert host.finish(_R(bypass_note), host="codex").returncode == 0


def test_finish_claude_is_identity():
    r = _R("text")
    assert host.finish(r, host="claude") is r


def test_plugin_listed_both_shapes():
    assert host.plugin_listed('[{"id": "van-gogh@van-gogh"}]', "claude") is True
    assert host.plugin_listed('[{"id": "other"}]', "claude") is False
    assert host.plugin_listed("not json", "claude") is None
    doc = json.dumps({"installed": [{"pluginId": "van-gogh@van-gogh", "installed": True}]})
    assert host.plugin_listed(doc, "codex") is True
    assert host.plugin_listed(json.dumps({"installed": []}), "codex") is False
    assert host.plugin_listed("{}", "codex") is None


def test_plugin_update_outcomes(tmp_path):
    assert host.plugin_update_outcome(
        {"outcome": "ok", "updateOutcome": "updated", "newVersion": "0.87.0"}, "0.86.1",
        "claude") == ("ok", "0.87.0")
    assert host.plugin_update_outcome(
        {"outcome": "ok", "updateOutcome": "up_to_date", "newVersion": "0.86.1"}, "0.86.1",
        "claude")[0] == "unchanged"
    assert host.plugin_update_outcome({"outcome": "failed"}, "0.86.1", "claude")[0] == "error"
    assert host.plugin_update_outcome(
        {"version": "0.87.0", "installedPath": str(tmp_path)}, "0.86.1", "codex") == ("ok", "0.87.0")
    assert host.plugin_update_outcome(
        {"version": "0.86.1", "installedPath": str(tmp_path)}, "0.86.1", "codex")[0] == "unchanged"
    assert host.plugin_update_outcome(
        {"version": "0.87.0", "installedPath": str(tmp_path / "gone")}, "0.86.1", "codex")[0] == "error"
    assert host.plugin_update_outcome({}, "0.86.1", "codex")[0] == "error"


def test_plugin_update_argvs():
    assert host.plugin_update_argvs("C", "claude") == [
        ["C", "plugin", "marketplace", "update", "van-gogh"],
        ["C", "plugin", "update", "van-gogh@van-gogh", "--json"]]
    assert host.plugin_update_argvs("X", "codex") == [
        ["X", "plugin", "marketplace", "upgrade", "van-gogh"],
        ["X", "plugin", "add", "van-gogh@van-gogh", "--json"]]
    assert host.plugin_update_command("codex") == "codex plugin add van-gogh@van-gogh"
    assert host.plugin_update_command("claude") == "claude plugin update van-gogh@van-gogh"


def test_codex_binary_resolution(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    monkeypatch.setattr(host, "codex_candidates", lambda: [tmp_path / "missing"])
    with pytest.raises(FileNotFoundError) as exc:
        host.bin("codex")
    assert "codex" in str(exc.value)
    bundled = tmp_path / "bundled-codex"; bundled.write_text("")
    monkeypatch.setattr(host, "codex_candidates", lambda: [tmp_path / "missing", bundled])
    assert host.bin("codex") == str(bundled)
    override = tmp_path / "mine"; override.write_text("")
    monkeypatch.setenv(host.CODEX_BIN_ENV, str(override))
    assert host.bin("codex") == str(override)


def test_a_codex_bundled_in_any_app_is_a_candidate_after_chatgpt(tmp_path, monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    apps = tmp_path / "Applications"
    for name in ("Codex.app", "Other.app"):
        (apps / name / "Contents" / "Resources").mkdir(parents=True)
    (apps / "Codex.app" / "Contents" / "Resources" / "codex").write_text("")
    monkeypatch.setattr(host, "_apps_dirs", lambda: [apps])
    found = host.codex_candidates()
    assert found[1] == Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    assert found[2:] == [apps / "Codex.app" / "Contents" / "Resources" / "codex"]


def test_npm_shim_resolves_to_the_native_binary(tmp_path, monkeypatch):
    modules = tmp_path / "lib" / "node_modules"
    shim = modules / ".bin" / "codex"; shim.parent.mkdir(parents=True); shim.write_text("#!node")
    native = modules / "@openai" / "codex-darwin-arm64" / "vendor" / "aarch64-apple-darwin" / "codex"
    native.parent.mkdir(parents=True); native.write_text("")
    assert host._native_beside_shim(str(shim)) == str(native)
    assert host._native_beside_shim(str(tmp_path / "plain" / "codex")) is None
    monkeypatch.setattr(host.shutil, "which", lambda name: str(shim))
    assert host.bin("codex") == str(native)


def test_claude_binary_error_text_unchanged(monkeypatch):
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError) as exc:
        host.bin("claude")
    assert "The 'claude' CLI was not found on PATH" in str(exc.value)


def test_codex_block_and_sync(tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('# mine\nmodel = "m"\n\n[foreign]\nkeep = "yes"  # inline\n', encoding="utf-8")
    assert host.sync_codex_config("/root", ["/v", "/s"], path=cfg) == "written"
    text = cfg.read_text()
    assert text.startswith('# mine\nmodel = "m"\n\n[foreign]\nkeep = "yes"  # inline\n')
    assert host.BLOCK_BEGIN in text and host.BLOCK_END in text
    assert "writable_roots = ['/v', '/s']" in text
    assert host.sync_codex_config("/root", ["/v", "/s"], path=cfg) == "unchanged"
    assert host.sync_codex_config("/root2", ["/v"], path=cfg) == "written"
    text = cfg.read_text()
    assert text.count(host.BLOCK_BEGIN) == 1 and "/root2" in text and "/root'" not in text
    import tomllib
    doc = tomllib.loads(text)
    assert doc["shell_environment_policy"]["set"]["CLAUDE_PLUGIN_ROOT"] == "/root2"
    assert doc["sandbox_workspace_write"]["network_access"] is True
    assert doc["foreign"]["keep"] == "yes"
    assert host.remove_codex_block(cfg) is True
    assert host.BLOCK_BEGIN not in cfg.read_text() and "[foreign]" in cfg.read_text()
    assert host.remove_codex_block(cfg) is False


def test_codex_block_without_roots_has_no_roots_line(tmp_path):
    cfg = tmp_path / "config.toml"
    assert host.sync_codex_config("/root", [], path=cfg) == "written"
    assert "writable_roots" not in cfg.read_text() and "network_access = true" in cfg.read_text()


def test_sync_refuses_a_users_own_table(tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[sandbox_workspace_write]\nnetwork_access = false\n", encoding="utf-8")
    before = cfg.read_text()
    assert host.sync_codex_config("/root", ["/v"], path=cfg) == "refused:sandbox_workspace_write"
    assert cfg.read_text() == before
    err = capsys.readouterr().err
    assert "sandbox_workspace_write" in err and err.count("\n") == 1


def test_sync_honours_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    assert host.codex_config_path() == tmp_path / "ch" / "config.toml"
    assert host.sync_codex_config("/root", [], ) == "written"
    assert (tmp_path / "ch" / "config.toml").exists()


def test_version_reads_a_stubbed_binary(monkeypatch):
    class R:
        stdout = "codex-cli 0.162.1\n"
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: R())
    assert host.version("/x", "codex") == "0.162.1"
    def boom(*a, **k): raise OSError
    monkeypatch.setattr(host.subprocess, "run", boom)
    assert host.version("/x", "codex") == ""


def test_auth_ok_claude_reads_logged_in(monkeypatch):
    class R:
        stdout = '{"loggedIn": true}'
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: R())
    assert host.auth_ok("/c", "claude") is True


def test_auth_ok_codex_makes_a_real_call(monkeypatch):
    seen = {}
    class R:
        stdout = _jsonl({"type": "turn.completed"})
    def fake(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return R()
    monkeypatch.setattr(host.subprocess, "run", fake)
    monkeypatch.setattr(host, "_codex_settings", lambda: {})
    assert host.auth_ok("/x", "codex") is True
    assert seen["argv"][:2] == ["/x", "exec"] and "--ignore-user-config" in seen["argv"]
    assert seen["kw"]["input"].startswith("Reply")
    assert "ANTHROPIC_API_KEY" not in seen["kw"]["env"]


def test_sync_keeps_tables_the_client_wrote_inside_the_markers(tmp_path):
    """Codex's writer appends its tables before the trailing end comment,
    inside the markers; a sync must move them out, never drop them (the
    independent evaluation found the plugin silently uninstalled)."""
    cfg = tmp_path / "config.toml"
    assert host.sync_codex_config("/root", ["/v"], path=cfg) == "written"
    text = cfg.read_text()
    planted = ('[marketplaces.van-gogh]\nsource_type = "local"\nsource = "/x"\n\n'
               '[plugins."van-gogh@van-gogh"]\nenabled = true\n')
    text = text.replace(host.BLOCK_END, planted + host.BLOCK_END)
    cfg.write_text(text)
    assert host.sync_codex_config("/root2", ["/v"], path=cfg) == "written"
    after = cfg.read_text()
    import tomllib
    doc = tomllib.loads(after)
    assert doc["plugins"]["van-gogh@van-gogh"]["enabled"] is True
    assert doc["marketplaces"]["van-gogh"]["source"] == "/x"
    assert doc["shell_environment_policy"]["set"]["CLAUDE_PLUGIN_ROOT"] == "/root2"
    inner = after[after.index(host.BLOCK_BEGIN):after.index(host.BLOCK_END)]
    assert "[plugins." not in inner and "[marketplaces." not in inner
    assert host.remove_codex_block(cfg) is True
    doc = tomllib.loads(cfg.read_text())
    assert doc["plugins"]["van-gogh@van-gogh"]["enabled"] is True and "shell_environment_policy" not in doc


def test_npm_shim_skips_the_package_json_beside_the_binary(tmp_path, monkeypatch):
    modules = tmp_path / "lib" / "node_modules"
    shim = modules / ".bin" / "codex"; shim.parent.mkdir(parents=True); shim.write_text("#!node")
    vendor = modules / "@openai" / "codex-darwin-arm64" / "vendor" / "aarch64-apple-darwin"
    vendor.mkdir(parents=True)
    (vendor / "codex-package.json").write_text("{}")
    native = vendor / "codex"; native.write_text("")
    assert host._native_beside_shim(str(shim)) == str(native)


def test_block_survives_a_lost_end_marker(tmp_path):
    """`codex plugin marketplace remove` drops the trailing end comment; the
    block is then found by its two tables, so sync re-anchors and remove
    still works, and neither refuses the tables as the person's own."""
    cfg = tmp_path / "config.toml"
    assert host.sync_codex_config("/root", ["/v"], path=cfg) == "written"
    text = cfg.read_text().replace(host.BLOCK_END + "\n", "").replace(host.BLOCK_END, "")
    text += '\n[plugins."van-gogh@van-gogh"]\nenabled = true\n'
    cfg.write_text(text)
    assert host.BLOCK_END not in cfg.read_text()
    assert not host._table_defined_outside(cfg.read_text(), "shell_environment_policy")
    assert host.sync_codex_config("/root2", ["/v"], path=cfg) == "written"
    import tomllib
    after = cfg.read_text()
    doc = tomllib.loads(after)
    assert doc["shell_environment_policy"]["set"]["CLAUDE_PLUGIN_ROOT"] == "/root2"
    assert doc["plugins"]["van-gogh@van-gogh"]["enabled"] is True
    assert after.count(host.BLOCK_BEGIN) == 1 and after.count(host.BLOCK_END) == 1
    cfg.write_text(after.replace(host.BLOCK_END + "\n", ""))
    assert host.remove_codex_block(cfg) is True
    doc = tomllib.loads(cfg.read_text())
    assert "shell_environment_policy" not in doc and "sandbox_workspace_write" not in doc
    assert doc["plugins"]["van-gogh@van-gogh"]["enabled"] is True


def test_update_step_skippable_only_for_the_local_marketplace_upgrade():
    up = ["/x", "plugin", "marketplace", "upgrade", "van-gogh"]
    add = ["/x", "plugin", "add", "van-gogh@van-gogh", "--json"]
    msg = "error: marketplace `van-gogh` is not configured as a Git marketplace"
    assert host.update_step_skippable(up, msg, "codex") is True
    assert host.update_step_skippable(add, msg, "codex") is False
    assert host.update_step_skippable(up, "network unreachable", "codex") is False
    assert host.update_step_skippable(["/c", "plugin", "marketplace", "update", "van-gogh"], msg, "claude") is False
