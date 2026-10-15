"""The Codex packaging: the manifest overlay, its hooks file, and the two
hook scripts' Codex mode. The Claude files are untouched by any of this and
`test_setup_notice_hook.py` keeps pinning them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host  # noqa: E402
import setup_state as ss  # noqa: E402

OVERLAY = ROOT / ".codex-plugin" / "plugin.json"
CLAUDE = ROOT / ".claude-plugin" / "plugin.json"
HOOKS = ROOT / "hooks" / "codex-hooks.json"
FIX = ROOT / "tests" / "fixtures" / "codex"


def test_overlay_is_in_lockstep_with_the_claude_manifest_and_pyproject():
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    claude = json.loads(CLAUDE.read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert overlay["name"] == claude["name"] == "van-gogh"
    assert overlay["version"] == claude["version"] == pyproject["project"]["version"]
    assert overlay["description"] == claude["description"]
    assert overlay["skills"] == "./skills/"
    assert overlay["hooks"] == "./hooks/codex-hooks.json" and HOOKS.exists()
    for key in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        assert overlay["interface"][key]
    assert "agents" not in overlay, "Codex manifests carry no agents"


def test_codex_hooks_run_the_same_scripts_spelled_for_codex():
    doc = json.loads(HOOKS.read_text(encoding="utf-8"))
    assert set(doc["hooks"]) == {"SessionStart", "Stop"}
    start = doc["hooks"]["SessionStart"][0]["hooks"][0]
    assert start["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/setup_notice.sh" --codex'
    assert start["commandWindows"].endswith('hooks/setup_notice.ps1" -Codex')
    assert "powershell" in start["commandWindows"]
    assert start["timeout"] <= 10
    stop = doc["hooks"]["Stop"][0]["hooks"][0]
    assert stop["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/handoff_line.sh"'
    claude_doc = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    claude_start = claude_doc["hooks"]["SessionStart"][0]["hooks"][0]
    assert "--codex" not in claude_start["command"] and "commandWindows" not in claude_start


def _run_notice(tmp_path, *flags):
    env = {k: v for k, v in os.environ.items() if k not in ("VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED")}
    env.update({"VAN_GOGH_STATE_DIR": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(ROOT),
                "VAN_GOGH_SETUP_LOGS": str(tmp_path / "logs")})
    return subprocess.run(["bash", str(ROOT / "hooks" / "setup_notice.sh"), *flags],
                          capture_output=True, text=True, encoding="utf-8", env=env,
                          stdin=subprocess.DEVNULL, timeout=30)


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_codex_mode_prints_the_plain_line_in_codex_spelling(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    data = ss.new("r1")
    for k in ss.KEYS[:5]:
        ss.set_status(data, k, ss.DONE)
    ss.save(data)
    # No venv in the scratch state: the bash fallback renders the line.
    res = _run_notice(tmp_path, "--codex")
    assert res.returncode == 0, res.stderr
    line = res.stdout.strip()
    assert line.startswith("Van Gogh setup: ") and "install steps left" in line
    assert not line.startswith("{"), "Codex gets the plain line, not JSON"
    assert "$van-gogh:install-van-gogh" in line and "/van-gogh:" not in line
    assert "In Codex, type" in line
    claude = _run_notice(tmp_path).stdout.strip()
    assert claude.startswith('{"systemMessage": ') and "/van-gogh:install-van-gogh" in claude
    assert "In Claude, type" in claude


# ── first run after an install from the Codex app's plugin screen ───────────
# The hook starts setup.sh --from-hook in the background. Every test here
# points the hook at a stub plugin root whose setup.sh only records its
# arguments, so no test can build a venv or touch a real Codex home.

def _stub_root(tmp_path, rc=0, say="Prepared."):
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    for name in ("setup_notice.sh", "codex_bootstrap.sh"):
        (root / "hooks" / name).write_bytes((ROOT / "hooks" / name).read_bytes())
    calls = tmp_path / "setup.calls"
    (root / "setup.sh").write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\necho "{say}"\nexit {rc}\n', encoding="utf-8")
    return root, calls


def _run_stub_notice(state, root, *flags):
    env = {k: v for k, v in os.environ.items()
           if k not in ("VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED", "VAN_GOGH_HOST")}
    env.update({"VAN_GOGH_STATE_DIR": str(state), "CLAUDE_PLUGIN_ROOT": str(root),
                "VAN_GOGH_SETUP_LOGS": str(state / "logs")})
    return subprocess.run(["bash", str(root / "hooks" / "setup_notice.sh"), *flags],
                          capture_output=True, text=True, encoding="utf-8", env=env,
                          stdin=subprocess.DEVNULL, timeout=30)


def _wait_rc(state, timeout=15):
    rc = state / "setup" / "bootstrap.rc"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if rc.exists() and rc.read_text().strip():
            return rc.read_text().strip()
        time.sleep(0.1)
    raise AssertionError("the background preparation never wrote bootstrap.rc")


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_codex_first_run_on_an_empty_computer_starts_the_preparation(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    root, calls = _stub_root(tmp_path)
    res = _run_stub_notice(state, root, "--codex")
    assert res.returncode == 0, res.stderr
    line = res.stdout.strip()
    assert line.startswith("Van Gogh is getting this computer ready in the background")
    assert "open a new chat and type $van-gogh:install-van-gogh" in line and "/van-gogh:" not in line
    assert _wait_rc(state) == "0"
    assert calls.read_text().split() == ["--host", "codex", "--from-hook"]
    assert (state / "setup" / "bootstrap.out").read_text().strip() == "Prepared."
    assert not (state / "setup" / "bootstrap.pid").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_codex_first_run_says_still_preparing_and_starts_nothing_while_it_runs(tmp_path):
    state = tmp_path / "state"
    (state / "setup").mkdir(parents=True)
    root, calls = _stub_root(tmp_path)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (state / "setup" / "bootstrap.pid").write_text(f"{sleeper.pid}\n")
        res = _run_stub_notice(state, root, "--codex")
    finally:
        sleeper.kill()
    assert res.stdout.strip().startswith("Van Gogh is still getting this computer ready")
    time.sleep(0.5)
    assert not calls.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
def test_codex_first_run_names_the_last_failure_and_tries_again(tmp_path):
    state = tmp_path / "state"
    (state / "setup").mkdir(parents=True)
    (state / "host").write_text("codex\n")
    (state / "setup" / "bootstrap.rc").write_text("1\n")
    (state / "setup" / "bootstrap.out").write_text(
        "Setting up Van Gogh...\nCould not download Python 3.12. Check the connection.\n\n")
    # A dead pid left behind by a killed run does not count as running.
    (state / "setup" / "bootstrap.pid").write_text("999999\n")
    root, calls = _stub_root(tmp_path, rc=1, say="Could not install the Python packages.")
    line = _run_stub_notice(state, root, "--codex").stdout.strip()
    assert line.startswith("Van Gogh's last try at getting ready stopped with: "
                           "Could not download Python 3.12. Check the connection. It is trying again now.")
    assert _wait_rc(state) == "1"
    assert calls.read_text().split() == ["--host", "codex", "--from-hook"]


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
@pytest.mark.parametrize("shape", ["pre-0.89 claude install", "claude host record",
                                   "claude session", "prepared codex install"])
def test_codex_first_run_never_touches_an_install_it_did_not_start(tmp_path, shape):
    state = tmp_path / "state"
    state.mkdir()
    flags = ("--codex",)
    if shape == "pre-0.89 claude install":      # a venv and no host record
        (state / "venv" / "bin").mkdir(parents=True)
        (state / "venv" / "bin" / "python").write_text("#!/bin/sh\nexit 1\n")
        (state / "venv" / "bin" / "python").chmod(0o755)
    elif shape == "claude host record":
        (state / "host").write_text("claude\n")
    elif shape == "claude session":             # the Claude hook, empty computer
        flags = ()
    else:
        (state / "host").write_text("codex\n")
        (state / "venv" / "bin").mkdir(parents=True)
        (state / "venv" / "bin" / "python").write_text("#!/bin/sh\nexit 1\n")
        (state / "venv" / "bin" / "python").chmod(0o755)
    root, calls = _stub_root(tmp_path)
    res = _run_stub_notice(state, root, *flags)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")
    time.sleep(0.5)
    assert not calls.exists() and not (state / "setup" / "bootstrap.pid").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh")
def test_setup_from_the_hook_is_not_refused_as_an_in_session_paste(tmp_path):
    env = {**os.environ, "CODEX_THREAD_ID": "t1", "HOME": str(tmp_path),
           "VAN_GOGH_STATE_DIR": str(tmp_path / "state"), "VAN_GOGH_SETUP_LOGS": str(tmp_path / "logs"),
           "PATH": "/usr/bin:/bin"}
    pasted = subprocess.run(["sh", str(ROOT / "setup.sh"), "--host", "codex", "--check"],
                            capture_output=True, text=True, env=env, timeout=60)
    assert pasted.returncode == 2 and "not in Codex" in pasted.stdout
    hooked = subprocess.run(["sh", str(ROOT / "setup.sh"), "--host", "codex", "--from-hook", "--check"],
                            capture_output=True, text=True, env=env, timeout=60)
    assert "not in Codex" not in hooked.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh")
@pytest.mark.parametrize("host_record", [None, "claude"])
def test_setup_from_a_chat_never_takes_over_a_claude_install(tmp_path, host_record):
    state = tmp_path / "state"
    (state / "venv" / "bin").mkdir(parents=True)
    (state / "venv" / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
    (state / "venv" / "bin" / "python").chmod(0o755)
    if host_record:
        (state / "host").write_text(host_record + "\n")
    env = {**os.environ, "HOME": str(tmp_path), "VAN_GOGH_STATE_DIR": str(state),
           "VAN_GOGH_SETUP_LOGS": str(tmp_path / "logs"), "PATH": "/usr/bin:/bin",
           "CODEX_THREAD_ID": "t1"}
    res = subprocess.run(["sh", str(ROOT / "setup.sh"), "--host", "codex", "--from-hook"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 3, res.stdout + res.stderr
    assert "already runs Van Gogh under Claude, so nothing was changed" in res.stdout
    assert sorted(p.name for p in state.iterdir()) == (["host", "venv"] if host_record else ["venv"])
    if host_record:
        assert (state / "host").read_text().strip() == host_record


@pytest.mark.skipif(sys.platform == "win32", reason="bash hook")
@pytest.mark.parametrize("shape,rc,expect,runs", [
    ("empty", 0, "Van Gogh is ready on this computer. Open a new chat and type $van-gogh:install-van-gogh", True),
    ("empty", 1, "Van Gogh could not get this computer ready: Could not install the Python packages.", True),
    ("prepared codex", 0, "Van Gogh is ready on this computer.", False),
    ("pre-0.89 claude install", 0, "This computer already runs Van Gogh under Claude, so nothing was changed.", False),
    ("claude host record", 0, "This computer already runs Van Gogh under Claude, so nothing was changed.", False),
])
def test_a_chat_whose_hook_never_ran_gets_one_line_from_code(tmp_path, shape, rc, expect, runs):
    state = tmp_path / "state"
    state.mkdir()
    if shape != "empty":
        (state / "venv" / "bin").mkdir(parents=True)
        (state / "venv" / "bin" / "python").write_text("#!/bin/sh\nexit 1\n")
        (state / "venv" / "bin" / "python").chmod(0o755)
    if shape == "prepared codex":
        (state / "host").write_text("codex\n")
    if shape == "claude host record":
        (state / "host").write_text("claude\n")
    root, calls = _stub_root(tmp_path, rc=rc,
                             say="Prepared." if rc == 0 else "Could not install the Python packages.")
    res = _run_stub_notice(state, root, "--codex", "--first-chat")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith(expect), res.stdout
    # The preparation runs in the foreground: it has finished when the line prints.
    assert calls.exists() is runs
    if runs:
        assert calls.read_text().split() == ["--host", "codex", "--from-hook"]
        assert (state / "setup" / "bootstrap.rc").read_text().strip() == str(rc)


def test_powershell_first_run_is_the_same_preparation():
    ps = (ROOT / "hooks" / "setup_notice.ps1").read_text(encoding="utf-8")
    first_run = ps[ps.index("if ($Codex) {\n  $b = Join-Path $state"):ps.index("if (-not (Test-Path -LiteralPath $check)) { exit 0 }")]
    assert "codex_bootstrap.ps1" in first_run and "Start-Process" in first_run
    assert "Van Gogh is getting this computer ready in the background" in first_run
    assert "Van Gogh is still getting this computer ready" in first_run
    assert "(-not $hostRec) -and (-not $venvOk)" in first_run and '($hostRec -eq "codex") -and (-not $venvOk)' in first_run
    wrapper = (ROOT / "hooks" / "codex_bootstrap.ps1").read_text(encoding="utf-8")
    assert "param([switch]$Codex, [switch]$FirstChat)" in ps
    assert "if ($FirstChat -and -not $prep)" in ps and "if ($FirstChat) {" in ps
    assert "-Host codex -FromHook" in wrapper and "bootstrap.rc" in wrapper and "bootstrap.pid" in wrapper
    setup = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    assert "[switch]$FromHook" in setup
    assert "if (-not $FromHook -and ($env:CLAUDECODE" in setup
    assert "if ($FromHook) { $PrepareOnly = [switch]$true }" in setup
    assert "if ($FromHook -and (Test-Path -LiteralPath $Vpy))" in setup and 'if ($prior -ne "codex")' in setup
    for text in (ps, wrapper, setup):
        assert chr(0x2014) not in text and chr(0x2013) not in text


def test_powershell_notice_has_the_same_codex_switch():
    ps = (ROOT / "hooks" / "setup_notice.ps1").read_text(encoding="utf-8")
    assert "param([switch]$Codex, [switch]$FirstChat)" in ps
    assert ps.index("param([switch]$Codex, [switch]$FirstChat)") < ps.index("if ($env:VAN_GOGH_UNATTENDED")
    assert "'$van-gogh:install-van-gogh'" in ps and "In $hostWord, type $installCmd" in ps
    assert "if ($Codex) {\n  Write-Output $line" in ps


def test_stop_hook_lets_a_codex_turn_stop(tmp_path):
    """A Codex Stop payload carries the same two fields the hook reads, and
    its transcript is a rollout the hook does not know: it must let the turn
    stop, silently, and never crash."""
    import handoff_hook
    payload = json.loads((FIX / "stop-payload.json").read_text(encoding="utf-8"))
    rollout = FIX / "rollout-excerpt.jsonl"
    payload["transcript_path"] = str(rollout)
    entries = [json.loads(l) for l in rollout.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert handoff_hook.missing_line(entries, payload["last_assistant_message"]) == ""
    res = subprocess.run([sys.executable, str(ROOT / "app" / "handoff_hook.py")],
                         input=json.dumps(payload), capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert (res.returncode, res.stdout.strip()) == (0, "")
    # Positive control: the Claude-shaped turn that dropped its line still blocks.
    import test_handoff_hook as th
    assert handoff_hook.missing_line(th.a_real_hand_off("Done."), "Done.") == th.LINE


def test_exec_fixture_reads_back_through_finish():
    """The captured `codex exec --json` shape, read the way every caller reads it."""
    class R:
        returncode, stderr = 0, ""
        stdout = (FIX / "exec-release-notes.jsonl").read_text(encoding="utf-8")
    r = host.finish(R(), host="codex")
    assert r.returncode == 0 and r.stdout.startswith("Updated 0.85.0 to 0.86.1")
    assert host.thread_id(r)
    r2 = host.finish(R(), forbid_commands=True, host="codex")
    assert r2.returncode == 1 and "ran a command" in r2.stderr
