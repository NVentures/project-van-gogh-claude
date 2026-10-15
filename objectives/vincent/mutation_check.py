#!/usr/bin/env python3
"""Mutation check for tests/test_vincent.py: remove one guard, expect red.

A test that passes with its guard deleted proves nothing. This copies the
plugin tree to a temp folder (never the tree being graded), reverts one guard
of app/vincent.py at a time there, and runs the test file. Every row must read
CAUGHT. Children run with PYTHONDONTWRITEBYTECODE=1 so a same-size edit is
never masked by a cached .pyc.

    mutation_check.py            copies the tree itself and runs every row
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (what the guard is, the line that is the guard, what it is replaced with)
M = [
 ("state is a copy", "state = copy.deepcopy(job_watch._read_state())", "state = job_watch._read_state()"),
 ("two lines are no hand-off", "    if len(targets) != 1:\n        return None", "    if len(targets) < 1:\n        return None"),
 ("only this plugin's skill runs headless", "                and handoff[\"plugin\"] == _own_plugin_name()\n", ""),
 ("headless list is checked", "                and handoff[\"skill\"] in HEADLESS_RUNNABLE\n", ""),
 ("reply is capped", "    if len(whole) <= REPLY_CAP:\n        return whole", "    if True:\n        return whole"),
 ("memory text is redacted", "        text, n = _redacted(raw[:_MEMORY_TEXT_CAP])", "        text, n = raw[:_MEMORY_TEXT_CAP], 0"),
 ("chat memory is redacted", "                text, n = _redacted(_read(p)[:_PROJECT_FILE_CAP])", "                text, n = _read(p)[:_PROJECT_FILE_CAP], 0"),
 ("unreadable ledger is not an empty one", "        return None, f\"the record of runs could not be read ({type(exc).__name__})\"", "        return [], \"\""),
 ("same fact once", "        if _normal(fact) in known:", "        if False:"),
 ("reader's move comes first", "        ordered = sorted(attention,\n                         key=lambda a: not keyed.get(a[\"key\"], {}).get(\"needs_reader\"))", "        ordered = list(attention)"),
 ("a failed skill is not a result", "        return {\"ok\": False, \"text\": f\"{title} did not finish. What it \"\n                                     f\"reported: {detail}\"}", "        return {\"ok\": True, \"text\": \"Done.\"}"),
 ("missing plugin is reported", "            if not entry.get(\"installPath\") or not root.is_dir():", "            if False:"),
 ("own plugin is skipped", "        if name == own:\n            continue", "        if False:\n            continue"),
 ("user scope only", "entry.get(\"scope\") != \"user\"", "False"),
 ("needs_confirm flag", "                                and row[\"name\"] in NEEDS_CONFIRM)", "                                and False)"),
 ("secret in a remembered fact", "    fact, redactions = _redacted(fact)", "    fact, redactions = fact, 0"),
 ("block sits above the ledger", "            if marker in text:\n                head, tail = text.split(marker, 1)", "            if False:\n                head, tail = text.split(marker, 1)"),
 ("session resumes", "    if isinstance(rec, dict) and rec.get(\"day\") == day and rec.get(\"id\"):", "    if False:"),
 ("new day is a new conversation", "rec.get(\"day\") == day and rec.get(\"id\")", "rec.get(\"id\")"),
 ("watcher stopped is noticed", "    if last_tick and (now - last_tick) <= timedelta(hours=job_watch.STALE_TICK_H):", "    if True:"),
 ("attention includes failures", "            if row[\"status\"] in job_watch.ACTIONABLE or row[\"needs_reader\"]:", "            if row[\"needs_reader\"]:"),
 ("chat model honoured", "    model = config_loader.workbench_chat_model() or None", "    model = None"),
 ("a closing question starts nothing", "        if runs_headless(handoff) and is_asking(reply):", "        if False:"),
 ("turn timeout honoured", "prompt, root, model=model, timeout=VINCENT_TIMEOUT_S,", "prompt, root, model=model, timeout=99999,"),
]


def main() -> int:
    py = sys.executable
    base = Path(tempfile.mkdtemp(prefix="vincent-mutation-"))
    try:
        copy = base / "tree"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".venv", "evidence"))
        target = copy / "app" / "vincent.py"
        orig = target.read_text(encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        caught = 0
        for name, old, new in M:
            assert orig.count(old) == 1, f"anchor not unique: {name} ({orig.count(old)})"
            target.write_text(orig.replace(old, new), encoding="utf-8")
            r = subprocess.run([py, "-m", "pytest", "tests/test_vincent.py", "-q",
                                "-p", "no:cacheprovider", "-x"],
                               cwd=copy, env=env, capture_output=True, text=True)
            red = r.returncode != 0
            caught += red
            failed = [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
            print(f"{'CAUGHT' if red else 'MISSED'}  {name}"
                  + (f"  <- {failed[0][7:90]}" if failed else ""))
        target.write_text(orig, encoding="utf-8")
        clean = subprocess.run([py, "-m", "pytest", "tests/test_vincent.py", "-q",
                                "-p", "no:cacheprovider"], cwd=copy, env=env,
                               capture_output=True, text=True)
        print(f"unmutated copy: {'green' if clean.returncode == 0 else 'RED'}")
        print(f"{caught} of {len(M)} mutations caught")
        return 0 if caught == len(M) and clean.returncode == 0 else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
