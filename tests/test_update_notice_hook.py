"""hooks/update_notice.sh: a newer Van Gogh is asked about at chat start.

The hook checks live (unless a check ran minutes ago), and hands the
model the question plus the command for each answer: as context under
Claude (additionalContext, which reaches the model; a systemMessage would
only be shown to the person), as plain text under Codex.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "update_notice.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash hook")


def _world(tmp_path, stamp=None, venv=True):
    if venv:
        (tmp_path / "venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, tmp_path / "venv" / "bin" / "python")
    if stamp is not None:
        (tmp_path / "plugin-update.json").write_text(json.dumps(stamp), encoding="utf-8")
    return tmp_path


def _run(tmp_path, *flags, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED", "VAN_GOGH_HOST",
                        "VAN_GOGH_DISABLE_UPDATE_CHECK")}
    env.update({"VAN_GOGH_STATE_DIR": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(ROOT),
                "VAN_GOGH_HOST": "claude", **extra})
    if "--codex" in flags:
        env.pop("VAN_GOGH_HOST")
    return subprocess.run(["bash", str(HOOK), *flags], env=env, capture_output=True,
                          text=True, timeout=30)


# Stamped just now, so the hook reuses it and never reaches the network here.
PENDING = {"update_available": True, "published": "99.0.0", "installed": "0.1.0",
           "checked_at": time.time()}


def test_claude_gets_the_question_as_context_for_the_model(tmp_path):
    res = _run(_world(tmp_path, PENDING))
    assert res.returncode == 0 and res.stderr == ""
    out = json.loads(res.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "systemMessage" not in out, "a systemMessage never reaches the model"
    assert ctx.startswith("Van Gogh 99.0.0 is ready.")
    assert "AskUserQuestion" in ctx and "header Update" in ctx
    assert "plugin_update.py\" --apply" in ctx and "plugin_update.py\" --decline" in ctx


def test_codex_gets_the_same_question_as_plain_text(tmp_path):
    res = _run(_world(tmp_path, PENDING), "--codex")
    assert res.returncode == 0
    assert res.stdout.startswith("Van Gogh 99.0.0 is ready.")
    assert "plain question" in res.stdout and "AskUserQuestion" not in res.stdout
    assert not res.stdout.lstrip().startswith("{")


@pytest.mark.parametrize("case", ["no stamp", "declined", "current", "no venv",
                                  "scheduled", "unattended"])
def test_silent_whenever_there_is_nothing_to_ask(tmp_path, case):
    stamp = dict(PENDING)
    extra = {}
    if case == "no stamp":
        stamp = None
    elif case == "declined":
        stamp["declined"] = "99.0.0"
    elif case == "current":
        stamp["update_available"] = False
    elif case == "scheduled":
        extra["VAN_GOGH_SCHEDULED"] = "1"
    elif case == "unattended":
        extra["VAN_GOGH_UNATTENDED"] = "1"
    res = _run(_world(tmp_path, stamp, venv=case != "no venv"), **extra)
    assert (res.returncode, res.stdout) == (0, ""), case


def test_both_hook_files_register_it_and_the_checks_stay_fresh():
    claude = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / "hooks" / "codex-hooks.json").read_text(encoding="utf-8"))
    c = [h["command"] for h in claude["hooks"]["SessionStart"][0]["hooks"]]
    x = codex["hooks"]["SessionStart"][0]["hooks"]
    assert 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/update_notice.sh"' in c
    upd = next(h for h in x if "update_notice" in h["command"])
    assert upd["command"].endswith("update_notice.sh\" --codex")
    assert upd["commandWindows"].endswith('hooks/update_notice.ps1" -Codex')
    assert os.access(HOOK, os.X_OK) and (ROOT / "hooks" / "update_notice.ps1").exists()
    sys.path.insert(0, str(ROOT / "app"))
    import plugin_update
    assert plugin_update.MAX_AGE_H <= 0.25, "skill runs re-check every quarter hour"
    assert plugin_update.SESSION_MAX_AGE_H <= 5 / 60, "chat start checks live"
    assert plugin_update.SESSION_FETCH_TIMEOUT_S <= 2, "the hook is killed at 5 seconds"
