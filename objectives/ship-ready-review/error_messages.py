#!/usr/bin/env python3
"""P19: when something breaks, does a business person know what to do?

Three things that will break on a real machine: the vault pointer missing, the
follow-ups ledger missing, and no `claude` on PATH. Each has to print one line
that names the fix. A traceback is not an error message; it is a support call.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/bin/python")
problems = []


def show(title, out, expect_any):
    print(f"\n{title}")
    text = (out.stdout + out.stderr).strip()
    for line in text.splitlines()[:6]:
        print("     " + line)
    if "Traceback (most recent call last)" in text:
        problems.append(f"{title}: printed a traceback")
    if not any(e.lower() in text.lower() for e in expect_any):
        problems.append(f"{title}: no instruction; expected one of {expect_any}")


state = Path(tempfile.mkdtemp(prefix="vg-err-"))
try:
    env = dict(os.environ, VAN_GOGH_STATE_DIR=str(state))

    show("1. No vault pointer (a fresh machine, or a moved vault)",
         subprocess.run([PY, "-c",
                         f"import sys; sys.path.insert(0, {str(ROOT / 'app')!r});"
                         "import config_loader as cl\n"
                         "try:\n    cl.vault()\nexcept Exception as e:\n"
                         "    print('ERROR:', e)"],
                        capture_output=True, text=True, env=env, cwd=str(ROOT)),
         ["install-van-gogh"])

    ledger = state / "missing-memory.md"
    show("2. No follow-ups ledger (they have not started one)",
         subprocess.run([PY, str(ROOT / "app/follow_up_radar.py"),
                         "--memory", str(ledger)],
                        capture_output=True, text=True, env=env, cwd=str(ROOT)),
         ["not found", "no such file", "create", "memory"])

    # With config present, only the ledger missing: the case a real user hits.
    vault = state / "vault"
    (vault / "van-gogh").mkdir(parents=True)
    import json
    cfg = json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))
    cfg["obsidian"]["vault_path"] = str(vault)
    (vault / "van-gogh" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (state / "vault-pointer").write_text(str(vault), encoding="utf-8")
    show("2b. Config present, ledger missing",
         subprocess.run([PY, str(ROOT / "app/follow_up_radar.py"),
                         "--memory", str(state / "nope.md")],
                        capture_output=True, text=True, env=env, cwd=str(ROOT)),
         ["no follow-up ledger", "config.json", "--memory"])

    empty = Path(tempfile.mkdtemp(prefix="vg-nopath-"))
    show("3. No `claude` on PATH (the CLI was never installed)",
         subprocess.run([PY, "-c",
                         f"import sys; sys.path.insert(0, {str(ROOT / 'app')!r});"
                         "from platform_compat import claude_bin\n"
                         "try:\n    print(claude_bin())\nexcept Exception as e:\n"
                         "    print('ERROR:', e)"],
                        capture_output=True, text=True,
                        env=dict(env, PATH=str(empty)), cwd=str(ROOT)),
         ["claude", "install", "path"])
    shutil.rmtree(empty, ignore_errors=True)
finally:
    shutil.rmtree(state, ignore_errors=True)

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
