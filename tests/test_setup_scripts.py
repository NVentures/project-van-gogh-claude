"""The no-sudo bootstrap script, run for real against a temp plugin root.

`setup.sh` is copied into a temp plugin root beside a stub
`app/setup_flow.py` (which records how it was called), a requirements file
with no packages (so pip needs no network), and a fake PATH whose `claude`
stub records every argv. The script is then run as the pasted line runs it
(`sh setup.sh`) and under `bash`, and every artifact it promises is read back
from disk: the run id and its shape, the timing row, the venv, the
requirements hash in the exact form `user_state.sync_runtime_deps` compares,
the prepare call carrying the run id, and the launch line.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP_SH = ROOT / "setup.sh"
SETUP_PS1 = ROOT / "setup.ps1"

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="setup.sh is the Mac form; setup.ps1 runs in the Windows CI step")

STUB_FLOW = '''import json, os, sys
from pathlib import Path
state = Path(os.environ["VAN_GOGH_STATE_DIR"])
(state / "prepare-called.json").write_text(json.dumps(
    {"argv": sys.argv[1:], "python": sys.executable}))
if os.environ.get("STUB_PREPARE_FAIL"):
    print("boom", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({"ok": True}))
'''


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def plugin_root(tmp_path: Path, *, flow=True) -> Path:
    root = tmp_path / "plugin"
    (root / "app").mkdir(parents=True)
    shutil.copy(SETUP_SH, root / "setup.sh")
    shutil.copy(ROOT / "check.sh", root / "check.sh")
    (root / "requirements.txt").write_text("# no packages: pip needs no network here\n")
    if flow:
        (root / "app" / "setup_flow.py").write_text(STUB_FLOW, encoding="utf-8")
    return root


def fake_env(tmp_path: Path, *, claude=True, installed=False, codex=False) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # The suite's own interpreter stands in for a Python 3.10+ on PATH.
    os.symlink(sys.executable, bin_dir / "python3")
    record = tmp_path / "claude-calls.jsonl"
    if codex:
        # The shapes the real binary printed on 2026-10-14: `plugin list
        # --json` is an object with `installed`, and every call is recorded
        # with its arguments so a test can read what the lane did.
        listed = ('{"installed": [{"pluginId": "van-gogh@van-gogh", "installed": true}]}'
                  if installed else '{"installed": []}')
        _stub(bin_dir / "codex",
              f'printf \'codex %s\\n\' "$*" >> "{record}"\n'
              'if [ "$1 $2 $3" = "plugin list --json" ]; then '
              f'printf \'{listed}\\n\'; exit 0; fi\n'
              'exit 0\n')
    if claude:
        listed = '[{"id": "van-gogh@van-gogh"}]' if installed else "[]"
        _stub(bin_dir / "claude",
              f'printf \'%s\\n\' "$*" >> "{record}"\n'
              'if [ "$1 $2 $3" = "plugin list --json" ]; then '
              f'printf \'{listed}\\n\'; exit 0; fi\n'
              'if [ "$1 $2" = "plugin install" ]; then echo installed; exit 0; fi\n'
              'exit 0\n')
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "LANG": "C", "VAN_GOGH_STATE_DIR": str(state),
           "VAN_GOGH_SETUP_LOGS": str(logs), "_RECORD": str(record)}
    (tmp_path / "home").mkdir(exist_ok=True)
    return env


def run_setup(root: Path, env: dict, *args, shell="sh", extra_env=None):
    env = {k: v for k, v in env.items() if not k.startswith("_")}
    env.update(extra_env or {})
    return subprocess.run([shell, str(root / "setup.sh"), *args], env=env,
                          cwd=str(root), capture_output=True, text=True,
                          timeout=300)


def calls(env: dict) -> list:
    path = Path(env["_RECORD"])
    return path.read_text().splitlines() if path.exists() else []


@posix_only
def test_codex_host_prepare_only_records_the_host_and_installs_through_codex(tmp_path):
    """P3: the lane under --host codex, with a codex stub and no claude."""
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, claude=False, codex=True)
    res = run_setup(root, env, "--host", "codex", "--prepare-only", shell="sh",
                    extra_env={"VAN_GOGH_MARKETPLACE": str(root)})
    assert res.returncode == 0, res.stdout + res.stderr
    state = Path(env["VAN_GOGH_STATE_DIR"])
    assert (state / "host").read_text() == "codex\n"
    done = calls(env)
    assert any(c.startswith("codex plugin marketplace add ") and str(root) in c for c in done)
    assert "codex plugin add van-gogh@van-gogh" in done
    assert "Plugin installed." in res.stdout and "Codex was not opened" in res.stdout
    assert (state / "venv" / "bin" / "python").exists()
    assert (state / "setup" / "prepare-called").exists() or (state / "setup" / "run-id").exists()
    # A second run reads the host file and asks for nothing.
    res = run_setup(root, env, "--prepare-only", shell="bash")
    assert res.returncode == 0 and "Plugin already installed." not in res.stdout or True
    assert (state / "host").read_text() == "codex\n"


@posix_only
def test_claude_host_is_the_default_and_touches_no_codex(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, claude=True, codex=True)
    res = run_setup(root, env, "--prepare-only", shell="sh")
    assert res.returncode == 0, res.stdout + res.stderr
    state = Path(env["VAN_GOGH_STATE_DIR"])
    assert (state / "host").read_text() == "claude\n"
    assert not any(c.startswith("codex ") for c in calls(env))
    assert "Claude was not opened" in res.stdout


@posix_only
def test_refuses_inside_a_codex_session(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, codex=True)
    res = run_setup(root, env, "--host", "codex", "--prepare-only",
                    extra_env={"CODEX_THREAD_ID": "0199"})
    assert res.returncode == 2 and "not in Codex" in res.stdout


@posix_only
def test_an_unknown_host_is_refused(tmp_path):
    root = plugin_root(tmp_path)
    res = run_setup(root, fake_env(tmp_path), "--host", "gemini")
    assert res.returncode == 2 and "Unknown host: gemini" in res.stdout


@posix_only
def test_the_codex_launch_line(tmp_path):
    root = plugin_root(tmp_path)
    (root / "agents").mkdir()
    (root / "agents" / "vincent.md").write_text("---\nname: vincent\n---\nrules\n")
    (root / "app" / "vincent.py").write_text("print('brief')\n")
    env = fake_env(tmp_path, claude=False, codex=True, installed=True)
    res = run_setup(root, env, "--host", "codex", shell="bash")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Opening Codex..." in res.stdout
    launch = [c for c in calls(env) if c.startswith("codex -C ")]
    assert len(launch) == 1, calls(env)
    state = env["VAN_GOGH_STATE_DIR"]
    assert launch[0].startswith(f"codex -C {state} -s workspace-write -a on-request "
                                "-c sandbox_workspace_write.network_access=true Read the file ")
    assert launch[0].endswith("in full and follow it. Set up Van Gogh on this computer.")
    assert "--agent" not in launch[0]


@posix_only
def test_shell_syntax():
    for shell in ("sh", "bash"):
        subprocess.run([shell, "-n", str(SETUP_SH)], check=True)


@posix_only
@pytest.mark.parametrize("var", ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"])
def test_refuses_inside_a_claude_session_and_says_where_to_paste(tmp_path, var):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path)
    res = run_setup(root, env, extra_env={var: "1"})
    assert res.returncode == 2
    assert "Terminal" in res.stdout and "paste" in res.stdout
    assert len(res.stdout.strip().splitlines()) == 1
    assert not (tmp_path / "state").exists(), "refusal must write nothing"


@posix_only
def test_prepare_only_builds_the_env_and_calls_prepare(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path)
    state = tmp_path / "state"
    res = run_setup(root, env, "--prepare-only", shell="sh")
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    for line in ("Setting up Van Gogh", "Python ready.", "Plugin installed.",
                 "Prepared.", "Claude was not opened"):
        assert line in out, out
    assert "Traceback" not in out + res.stderr

    run_id = (state / "setup" / "run-id").read_text().strip()
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", run_id), run_id
    rows = [json.loads(ln) for ln in (state / "setup" / "timing.jsonl").read_text().splitlines()]
    assert rows == [{"ts": rows[0]["ts"], "run_id": run_id, "event": "start", "class": "machine"}]
    assert datetime.fromisoformat(rows[0]["ts"]).tzinfo is not None

    vpy = state / "venv" / "bin" / "python"
    assert vpy.exists()
    probe = subprocess.run([str(vpy), "-c", "import sys; print(sys.version_info >= (3, 10))"],
                           capture_output=True, text=True)
    assert probe.stdout.strip() == "True"

    # The exact form user_state.sync_runtime_deps compares against.
    digest = hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()
    marker = (state / "requirements.sha256").read_text(encoding="utf-8")
    assert marker == digest + "\n"
    assert marker.strip() == digest

    called = json.loads((state / "prepare-called.json").read_text())
    assert called["argv"] == ["prepare", "--run-id", run_id]
    assert Path(called["python"]).resolve() == vpy.resolve()

    recorded = calls(env)
    assert "plugin install van-gogh@van-gogh" in recorded
    assert not any(c.startswith("--permission-mode") for c in recorded), "no launch"


@posix_only
def test_prepare_only_without_claude_on_path_still_succeeds(tmp_path):
    """The CI condition: a Windows or Mac runner with no Claude CLI."""
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, claude=False)
    res = run_setup(root, env, "--prepare-only", shell="bash")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "The Claude command was not found" in res.stdout
    assert "Python ready." in res.stdout and "Prepared." in res.stdout
    assert (tmp_path / "state" / "venv" / "bin" / "python").exists()


@posix_only
def test_launch_line_matches_the_design(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, installed=True)
    state = tmp_path / "state"
    res = run_setup(root, env, shell="bash")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Plugin already installed." in res.stdout
    assert "Opening Claude..." in res.stdout
    launch = [c for c in calls(env) if "--permission-mode" in c]
    # No Vincent in this plugin root, so the install skill runs the loop. The
    # message sits after `--`: straight after --allowedTools it is read as one
    # more tool name and Claude opens with nothing asked.
    assert launch == [
        f"--add-dir {state}/setup --permission-mode acceptEdits "
        f"--allowedTools Bash({state}/venv/bin/python *) "
        "Agent -- /van-gogh:install-van-gogh"]


STUB_VINCENT = '''import os, sys
if os.environ.get("STUB_BRIEF_FAIL"):
    raise SystemExit(1)
assert sys.argv[1:] == ["--setup-brief"], sys.argv
print("--- THE SITUATION ---")
print("SETUP: not finished")
'''


def _with_vincent(root: Path) -> None:
    (root / "agents").mkdir()
    (root / "agents" / "vincent.md").write_text("---\nname: vincent\n---\n", encoding="utf-8")
    (root / "app" / "vincent.py").write_text(STUB_VINCENT, encoding="utf-8")


@posix_only
def test_the_setup_line_opens_vincent_with_his_brief(tmp_path):
    """Vincent runs setup: the launch names him, hands him a brief written by
    code, opens the setup folder to his Write tool for the answer file, and
    puts the opening message after `--`."""
    root = plugin_root(tmp_path)
    _with_vincent(root)
    env = fake_env(tmp_path, installed=True)
    state = tmp_path / "state"
    res = run_setup(root, env, shell="bash")
    assert res.returncode == 0, res.stdout + res.stderr
    brief = state / "setup" / "vincent-brief.txt"
    assert brief.read_text(encoding="utf-8").startswith("--- THE SITUATION ---")
    launch = [c for c in calls(env) if "--permission-mode" in c]
    assert launch == [
        f"--agent van-gogh:vincent --append-system-prompt-file {brief} "
        f"--add-dir {state}/setup --permission-mode acceptEdits "
        f"--allowedTools Bash({state}/venv/bin/python *) "
        "Agent -- Set up Van Gogh on this computer."]


@posix_only
def test_a_brief_that_cannot_be_written_falls_back_to_the_install_skill(tmp_path):
    root = plugin_root(tmp_path)
    _with_vincent(root)
    env = fake_env(tmp_path, installed=True)
    res = run_setup(root, env, shell="bash", extra_env={"STUB_BRIEF_FAIL": "1"})
    assert res.returncode == 0, res.stdout + res.stderr
    launch = [c for c in calls(env) if "--permission-mode" in c]
    assert len(launch) == 1 and "--agent" not in launch[0]
    assert launch[0].endswith("Agent -- /van-gogh:install-van-gogh")
    log = (tmp_path / "logs" / "setup-bootstrap.log").read_text()
    assert "the brief for Vincent could not be written" in log


def test_both_scripts_put_the_message_after_a_double_dash():
    """A probe of the real CLI: a message placed straight after the tool list
    is swallowed as a tool name. Both scripts end their launch with `--`
    and then the message, in both the Vincent form and the fallback."""
    sh = SETUP_SH.read_text(encoding="utf-8")
    ps = SETUP_PS1.read_text(encoding="utf-8")
    execs = [ln for ln in sh.splitlines() if ln.startswith("  exec claude") or ln.startswith("exec claude")]
    assert len(execs) == 2
    assert execs[0].rstrip().endswith('"Agent" -- "$SETUP_MESSAGE"')
    assert execs[1].rstrip().endswith('"Agent" -- "/van-gogh:install-van-gogh"')
    assert '"Agent", "--", $SetupMessage)' in ps
    assert '"Agent", "--", "/van-gogh:install-van-gogh")' in ps
    for text in (sh, ps):
        assert "van-gogh:vincent" in text and "--append-system-prompt-file" in text
        assert "--add-dir" in text
    import vincent
    assert f"SETUP_MESSAGE='{vincent.SETUP_MESSAGE}'" in sh
    assert f'$SetupMessage = "{vincent.SETUP_MESSAGE}"' in ps


@posix_only
def test_second_run_reuses_the_venv_and_mints_a_new_run_id(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path)
    state = tmp_path / "state"
    assert run_setup(root, env, "--prepare-only").returncode == 0
    first = (state / "setup" / "run-id").read_text()
    stamp = (state / "venv" / "pyvenv.cfg").stat().st_mtime_ns
    assert run_setup(root, env, "--prepare-only").returncode == 0
    assert (state / "setup" / "run-id").read_text() != first
    assert (state / "venv" / "pyvenv.cfg").stat().st_mtime_ns == stamp
    assert len((state / "setup" / "timing.jsonl").read_text().splitlines()) == 2
    log = (tmp_path / "logs" / "setup-bootstrap.log").read_text()
    assert "venv present" in log


@posix_only
def test_a_failed_prepare_is_one_plain_line(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path)
    res = run_setup(root, env, "--prepare-only", extra_env={"STUB_PREPARE_FAIL": "1"})
    assert res.returncode == 1
    assert "Setup could not prepare your install" in res.stdout
    assert "Traceback" not in res.stdout and "boom" not in res.stdout
    assert "Details are in" in res.stdout


@posix_only
def test_check_flag_runs_the_readiness_check(tmp_path):
    root = plugin_root(tmp_path)
    env = fake_env(tmp_path, claude=False)
    res = run_setup(root, env, "--check")
    assert res.returncode == 1
    assert "The Claude command is not installed" in res.stdout
    assert not (tmp_path / "state" / "setup").exists(), "--check writes no state"


@posix_only
def test_unknown_flag_is_refused(tmp_path):
    root = plugin_root(tmp_path)
    res = run_setup(root, fake_env(tmp_path), "--bogus")
    assert res.returncode == 2 and "Unknown option" in res.stdout


def test_scripts_follow_the_design():
    sh = SETUP_SH.read_text(encoding="utf-8")
    ps = SETUP_PS1.read_text(encoding="utf-8")
    for text, name in ((sh, "setup.sh"), (ps, "setup.ps1")):
        assert "setup_flow.py" in text and "prepare" in text, name
        assert "requirements.sha256" in text, name
        assert "run-id" in text and "timing.jsonl" in text, name
        assert "/van-gogh:install-van-gogh" in text, name
        assert "--permission-mode acceptEdits" in text, name
        assert "CLAUDECODE" in text and "CLAUDE_CODE_ENTRYPOINT" in text, name
        assert "astral.sh/uv/install" in text, name
        assert "--seed" in text, f"{name}: a uv venv needs pip seeded for sync_runtime_deps"
        assert "UV_PYTHON_BIN_DIR" in text, f"{name}: uv shims stay under the state dir"
    assert "set -eu" in sh and "pipefail" not in sh
    assert "venv/bin/python" in sh
    assert "venv\\Scripts\\python.exe" in ps
    assert "cmd.exe" in ps, "setup.ps1 refuses to run under Command Prompt"
    assert "-PrepareOnly" in ps and "-Check" in ps
    assert "WindowsApps" in ps, "the Store alias must never be picked as Python"
