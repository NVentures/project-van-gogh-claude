"""The two in-session nudge surfaces (P25): the session-start hook and the
in-command line, plus the carve-outs they rest on.

The hook is run as Claude Code runs it: `bash hooks/setup_notice.sh` with the
documented JSON on stdin, from three working folders. The venv python it
looks for is a shim that answers `setup_state.py line` with the real module
and records a `setup_nudge.py run --maybe` call instead of running it, so no
test can reach a scheduler or a notification centre.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import config_loader as cl
import digest_send
import job_registry
import setup_state as ss

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "setup_notice.sh"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
FRONT_PAGE = ROOT / "skills" / "_shared" / "front-page.md"

STDIN = {"session_id": "abc123", "cwd": "", "hook_event_name": "SessionStart",
         "source": "startup"}


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(tmp_path / "logs"))
    monkeypatch.delenv("VAN_GOGH_UNATTENDED", raising=False)
    monkeypatch.delenv("VAN_GOGH_SCHEDULED", raising=False)
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


def shim_venv(state_dir: Path) -> Path:
    """`<state>/venv/bin/python`: the real interpreter for setup_state, a
    recorder for setup_nudge."""
    record = state_dir / "nudge.calls"
    py = state_dir / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        f'  *setup_state.py) exec "{sys.executable}" "$@" ;;\n'
        f'  *setup_nudge.py) echo "$@" >> "{record}"; exit 0 ;;\n'
        "esac\nexit 1\n", encoding="utf-8")
    py.chmod(0o755)
    return record


def run_hook(cwd: Path, env_extra: dict | None = None) -> tuple:
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT), **(env_extra or {})}
    stdin = json.dumps({**STDIN, "cwd": str(cwd)})
    started = time.monotonic()
    res = subprocess.run(["bash", str(HOOK)], input=stdin, capture_output=True,
                         text=True, cwd=str(cwd), env=env, timeout=10)
    return res, time.monotonic() - started


def checklist(done=("plugin", "python", "credentials")) -> dict:
    data = ss.new("20261001-170000-a1b2", "darwin", "0.81.0")
    for key in done:
        ss.set_status(data, key, ss.DONE)
    ss.save(data)
    return data


# ── the hook ─────────────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="the bash hook runs under Git Bash there")
def test_hook_prints_the_line_from_three_working_folders_in_under_a_second(state_dir, tmp_path):
    record = shim_venv(state_dir)
    checklist()
    spaced = tmp_path / "a folder with spaces"
    spaced.mkdir()
    plain = tmp_path / "not-a-git-folder"
    plain.mkdir()
    for cwd in (Path.home(), spaced, plain):
        res, elapsed = run_hook(cwd)
        assert res.returncode == 0, res.stderr
        assert res.stderr == ""
        assert elapsed < 1.0, f"{cwd}: {elapsed:.2f}s"
        out = json.loads(res.stdout)
        assert out == {"systemMessage": ss.one_line()}
        assert out["systemMessage"].startswith("Van Gogh setup: 6 of 9 install steps left.")
    time.sleep(0.3)
    calls = record.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 3
    assert all(c.endswith("app/setup_nudge.py run --maybe") for c in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="the bash hook runs under Git Bash there")
def test_hook_is_silent_when_the_checklist_is_absent_or_the_run_is_unattended(state_dir, tmp_path):
    record = shim_venv(state_dir)
    res, _ = run_hook(tmp_path)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")
    checklist()
    for var in ("VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED"):
        res, elapsed = run_hook(tmp_path, {var: "1"})
        assert (res.returncode, res.stdout, res.stderr) == (0, "", "")
        assert elapsed < 1.0
    assert not record.exists()
    # Control: with the gate open, the same call prints.
    res, _ = run_hook(tmp_path)
    assert json.loads(res.stdout)["systemMessage"] == ss.one_line()


@pytest.mark.skipif(sys.platform == "win32", reason="the bash hook runs under Git Bash there")
def test_hook_builds_the_same_line_in_bash_when_there_is_no_venv_yet(state_dir, tmp_path):
    """Before the Python step there is no interpreter to ask, so the hook
    parses the JSON itself. The words must match setup_state.one_line for
    every box that can be the next one."""
    for key in ss.KEYS:
        data = ss.new("r1")
        for k in ss.KEYS:
            if k == key:
                break
            ss.set_status(data, k, ss.DONE)
        if key == "meetings":
            # A skipped and a not-applicable box count as resolved in bash too.
            ss.set_status(data, "deals", ss.SKIPPED)
            ss.set_status(data, "briefings", ss.NOT_APPLICABLE)
        ss.save(data)
        res, elapsed = run_hook(tmp_path)
        assert res.returncode == 0 and res.stderr == ""
        assert elapsed < 1.0
        assert json.loads(res.stdout) == {"systemMessage": ss.one_line(data)}, key
    # Everything resolved: the file still exists for a moment, and the hook
    # says nothing rather than "0 of 9 install steps left".
    data = ss.new("r1")
    for k in ss.KEYS:
        ss.set_status(data, k, ss.DONE)
    ss.save(data)
    res, _ = run_hook(tmp_path)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


def test_hooks_json_registers_the_bash_hook_for_every_session_start():
    doc = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    entries = doc["hooks"]["SessionStart"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == ""            # startup, resume, clear, compact, fork
    hooks = entries[0]["hooks"]
    # The setup line first, then the question about a newer Van Gogh
    # (tests/test_update_notice_hook.py).
    assert [h["command"] for h in hooks][1:] == ['bash "${CLAUDE_PLUGIN_ROOT}/hooks/update_notice.sh"']
    assert hooks[0]["type"] == "command"
    assert hooks[0]["shell"] == "bash"
    assert hooks[0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/setup_notice.sh"'
    assert hooks[0]["timeout"] <= 10
    # Stop holds the hand-off line guard (tests/test_handoff_hook.py).
    assert set(doc["hooks"]) == {"SessionStart", "Stop"}
    assert HOOK.exists() and os.access(HOOK, os.X_OK)
    assert (ROOT / "hooks" / "setup_notice.ps1").exists()


def test_hook_scripts_run_no_python_before_the_checklist_test_and_honour_the_state_override():
    sh = HOOK.read_text(encoding="utf-8")
    first_python = sh.index('"$PY"')
    assert sh.index('[ -f "$CHECK" ] || exit 0') < first_python
    assert 'STATE="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"' in sh
    assert 'VAN_GOGH_UNATTENDED' in sh and 'VAN_GOGH_SCHEDULED' in sh
    assert "nohup" in sh and "run --maybe" in sh
    assert "Documents" not in sh
    ps = (ROOT / "hooks" / "setup_notice.ps1").read_text(encoding="utf-8")
    assert "VAN_GOGH_STATE_DIR" in ps and "VAN_GOGH_UNATTENDED" in ps
    assert "systemMessage" in ps and "run" in ps and "--maybe" in ps
    for text in (sh, ps, HOOKS_JSON.read_text(encoding="utf-8")):
        assert chr(0x2014) not in text and chr(0x2013) not in text


def test_the_bash_action_table_matches_setup_state_boxes():
    sh = HOOK.read_text(encoding="utf-8")
    ps = (ROOT / "hooks" / "setup_notice.ps1").read_text(encoding="utf-8")
    for key, _title, _needs, action, _m in ss.BOXES:
        assert f'{key}) ACTION="{action}" ;;' in sh, key
        assert f'{key} = "{action}"' in ps, key


# ── the in-command line ──────────────────────────────────────────────────────

def test_meta_setup_notice_is_the_line_and_empty_when_there_is_nothing_to_say(state_dir, monkeypatch):
    assert cl._setup_notice_line() == ""
    assert cl.resolved_meta()["setup_notice"] == ""
    data = checklist()
    assert cl._setup_notice_line() == ss.one_line(data) != ""
    assert cl.resolved_meta()["setup_notice"] == ss.one_line(data)
    monkeypatch.setenv("VAN_GOGH_UNATTENDED", "1")
    assert cl._setup_notice_line() == ""
    assert cl.resolved_meta()["setup_notice"] == ""


def test_front_page_step_14_renders_the_line_and_offers_the_three_answers():
    text = FRONT_PAGE.read_text(encoding="utf-8")
    step = text.split("\n14. ", 1)[1].split("\n## ", 1)[0]
    assert step.startswith("`meta.setup_notice`")
    assert "{meta.setup_notice, verbatim}" in step
    assert "AskUserQuestion" in step and "header `Setup`" in step
    assert "once per session" in step
    for answer in ("**Resume now**", "**Skip this step**", "**Stop reminding me**"):
        assert answer in step
    assert "/van-gogh:install-van-gogh" in step
    assert "setup_flow.py\" skip --box KEY" in step
    assert "setup_nudge.py\" stop" in step
    assert "setup_state.py\" status" in step
    assert "digest email" in step
    assert step.count("```bash") == 1 and step.count("```powershell") == 1
    assert chr(0x2014) not in step and chr(0x2013) not in step
    # It sits after step 13 and nothing is renumbered.
    assert text.index("\n13. ") < text.index("\n14. ")
    assert "\n15. " not in text


def test_every_command_in_the_manifest_reaches_the_setup_line():
    """P25: every command the plugin ships prints the one line.

    Structurally: the line command lives in the one procedure every skill
    runs at its start (`_shared/python-runtime.md`, right after the venv
    guard), so a skill reaches it by referencing that file. The only skill
    allowed another route is the install itself, whose orchestrator prints
    the line in its own JSON (`setup_flow.py next` carries `line`).
    """
    runtime = (ROOT / "skills" / "_shared" / "python-runtime.md").read_text(encoding="utf-8")
    section = runtime.split("## The setup line", 1)[1].split("\n## ", 1)[0]
    assert '"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/setup_state.py" line' in section
    assert ('& "$HOME\\.config\\van-gogh\\venv\\Scripts\\python.exe" '
            '"$env:CLAUDE_PLUGIN_ROOT\\app\\setup_state.py" line') in section
    assert section.count("```bash") == 1 and section.count("```powershell") == 1
    assert "verbatim" in section and "nothing" in section
    assert runtime.index("## Ensure the venv exists") < runtime.index("## The setup line")

    skills = sorted(p for p in (ROOT / "skills").glob("*/SKILL.md"))
    assert len(skills) >= 30, "found too few skills, so this would pass on nothing"
    missing = []
    for md in skills:
        text = md.read_text(encoding="utf-8")
        name = md.parent.name
        if name == "install-van-gogh":
            continue
        if "_shared/python-runtime.md" in text or "setup_state.py\" line" in text:
            continue
        missing.append(name)
    assert not missing, f"skills that never reach the setup line: {missing}"
    # The front page renders it again under the rule, for the file and the digest.
    renderers = [md.parent.name for md in skills
                 if "front_page_md" in md.read_text(encoding="utf-8")]
    assert set(renderers) >= {"morning-coffee", "afternoon-tea", "week", "week-retro"}
    for name in renderers:
        assert "_shared/front-page.md" in (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8"), name
    assert "14. `meta.setup_notice`" in FRONT_PAGE.read_text(encoding="utf-8")
    assert '"setup_notice": _setup_notice_line(),' in (ROOT / "app" / "config_loader.py").read_text(encoding="utf-8")


def test_the_digest_footer_carries_the_line_unless_the_skill_wrote_it(state_dir, monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("# Week briefing\n\nBody.\n", encoding="utf-8")
    sent = {}

    def fake_send(account, to, subject, body, html=None):
        sent.update(body=body, html=html)
        return {}
    saved = cl._config
    try:
        cl._config = {**cl._config, "digest": {**cl._config.get("digest", {}),
                                               "enabled": True, "recipient_email": "t@x.com",
                                               "briefings": {"week": {"enabled": True}}}}
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send, "send_email", fake_send)
        # No checklist: the body is untouched.
        assert digest_send.main(["week", "--no-render"]) == 0
        capsys.readouterr()
        assert "Van Gogh setup" not in sent["body"]
        # A checklist: the line is appended under a rule and reaches the HTML.
        data = checklist()
        assert digest_send.main(["week", "--no-render"]) == 0
        capsys.readouterr()
        assert sent["body"].endswith(f"\n\n---\n{ss.one_line(data)}\n")
        assert "6 of 9 install steps left" in sent["html"]
        # The skill already wrote it into the file: not twice.
        md.write_text(f"# Week briefing\n\nBody.\n\n---\n{ss.one_line(data)}\n", encoding="utf-8")
        assert digest_send.main(["week", "--no-render"]) == 0
        capsys.readouterr()
        assert sent["body"].count(ss.one_line(data)) == 1
    finally:
        cl._config = saved


# ── the carve-outs ───────────────────────────────────────────────────────────

def test_the_setup_nudge_is_not_a_registry_row_on_purpose():
    import setup_nudge as sn
    keys = {job.key for job in job_registry.jobs()}
    assert "setup-nudge" not in keys
    assert sn.MAC_LABEL not in {job.mac_label for job in job_registry.jobs()}
    assert sn.WIN_TASK not in {job.win_task for job in job_registry.jobs()}
    page = (ROOT / "FOUR-CS.md").read_text(encoding="utf-8")
    assert "com.monet.setup-nudge" in page and "VanGogh-setup-nudge" in page
    assert "removes itself" in page
    # Prose, not a row: a row would make test_four_cs demand a registry entry.
    assert not re.search(r"^\| setup-nudge \|", page, re.M)
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "setup_nudge.py" in guide and "at most three times" in guide
    assert "from their own account" in guide
    for new in (ROOT / "app" / "setup_nudge.py", ROOT / "app" / "setup_state.py"):
        text = new.read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, new.name
