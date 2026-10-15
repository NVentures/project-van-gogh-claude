#!/usr/bin/env python3
"""Break one guard at a time in a copy of the tree and confirm a test notices.

A green suite proves the tests pass, not that they would fail. Each row here
removes one guard this build added, in a throwaway copy, and runs the tests
that are supposed to depend on it. A row is CAUGHT when those tests fail and
MISSED when they still pass. The real tree is never edited.

    mutation_check.py          run every row; exit 0 only if every row is caught
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (name, file, text to find exactly once, replacement, tests that must fail)
ROWS = [
    ("one briefing on must not switch on the other three", "app/schedule_plan.py",
     '_section(cfg, "digest", "briefings", other)["enabled"] = False',
     "pass", "tests/test_schedule_plan.py"),
    ("a fixed job has no setting of its own", "app/schedule_plan.py",
     "    if key in FIXED:\n        raise PlanError(f\"{name} comes with the schedule and has no setting of its own\")",
     "    if False:\n        raise PlanError(name)", "tests/test_schedule_plan.py"),
    ("a change that fails halfway changes nothing", "app/schedule_plan.py",
     "            trial = copy.deepcopy(out)", "            trial = out",
     "tests/test_schedule_plan.py"),
    ("a single common word names no job", "app/schedule_plan.py",
     'inside = [n for n in known if re.search(rf"(?<![\\w-]){re.escape(n)}(?![\\w-])", word)]',
     "inside = [n for n in known if n in word or word in n]", "tests/test_schedule_plan.py"),
    ("the old settings are kept before a write", "app/schedule_plan.py",
     '        shutil.copyfile(path, path.with_name(path.name + ".bak"))', "        pass",
     "tests/test_schedule_plan.py"),
    ("a dry run writes nothing", "app/schedule_plan.py",
     "    if done and not args.dry_run:", "    if done:", "tests/test_schedule_plan.py"),
    ("the table agrees with the scheduler on meeting prep", "app/schedule_plan.py",
     '        return {"on": bool(mine.get("enabled", False)),\n                "mode": mode',
     '        return {"on": True,\n                "mode": mode', "tests/test_schedule_plan.py"),
    ("a scratch state folder never reaches the real scheduler", "app/setup_state.py",
     '    return not os.environ.get("VAN_GOGH_STATE_DIR") or \\\n        os.environ.get("VAN_GOGH_SCHEDULER_SYNC") == "1"',
     "    return True", "tests/test_schedule_plan.py tests/test_setup_flow_e2e.py"),
    ("an answer file is deleted once read", "app/setup_flow.py",
     "        try:\n            target.unlink()\n        except OSError:\n            pass",
     "        pass", "tests/test_setup_flow_e2e.py"),
    ("an install with a schedule is shown its own", "app/setup_flow.py",
     "    cfg = copy.deepcopy(base) if chosen else schedule_plan.setup_defaults(base)",
     "    cfg = schedule_plan.setup_defaults(base)", "tests/test_setup_flow_e2e.py"),
    ("Change something with nothing said asks what", "app/setup_flow.py",
     "    else:\n        return _schedule_prompt(data, SCHEDULE_ASK)\n    ss.save(data)\n    return None",
     "    else:\n        ans[\"schedule\"] = \"all\"\n    ss.save(data)\n    return None",
     "tests/test_setup_flow_e2e.py"),
    ("switching every job off is scheduling nothing", "app/setup_flow.py",
     "        if not schedule_plan.any_on(schedule_plan.table(cfg)):", "        if False:",
     "tests/test_setup_flow_e2e.py"),
    ("a finished setup introduces Vincent", "app/setup_flow.py",
     "        lines.append(VINCENT_LINE)", "        pass", "tests/test_setup_flow_e2e.py"),
    ("no job is reported before one was scheduled", "app/vincent.py",
     '            return b["status"] in setup_state.RESOLVED\n    return True',
     "            return True\n    return True", "tests/test_vincent.py"),
    ("a step outside Claude is never offered", "app/vincent.py",
     '    if setup.get("before_claude"):\n        return (f"Setup is not finished',
     '    if False:\n        return (f"Setup is not finished', "tests/test_vincent.py"),
    ("the setup record is reported as stored", "app/vincent.py",
     "    if data is not None:\n        rows = _box_rows(data)",
     "    if data is not None:\n        import setup_flow\n"
     "        setup_state.verify(data, checks=setup_flow.quiet_checks(), reopen=False)\n"
     "        rows = _box_rows(data)", "tests/test_vincent.py"),
    ("the setup rules have every path written out", "app/vincent.py",
     '    text = text.replace("${CLAUDE_PLUGIN_ROOT}", posix)', "    pass", "tests/test_vincent.py"),
    ("a working vault with no record is not unfinished", "app/vincent.py",
     "    if _vault_config() is not None:\n        # A working vault", "    if False:\n        # A working vault",
     "tests/test_vincent.py"),
    ("the opening message comes after a double dash (sh)", "setup.sh",
     '"Agent" -- "$SETUP_MESSAGE"\nfi', '"Agent" "$SETUP_MESSAGE"\nfi', "tests/test_setup_scripts.py"),
    ("the opening message comes after a double dash (ps1)", "setup.ps1",
     '"Agent", "--", $SetupMessage)', '"Agent", $SetupMessage)', "tests/test_setup_scripts.py"),
    ("a brief that cannot be written falls back", "setup.sh",
     '>"$BRIEF" 2>>"$LOG" && [ -s "$BRIEF" ]; then', '>"$BRIEF" 2>>"$LOG" || true; then',
     "tests/test_setup_scripts.py"),
    ("the install skill hands no answer over in a here-document", "skills/install-van-gogh/SKILL.md",
     'next --answer-file "<prompt.answer_file>"\n  ```\n\n  Windows',
     "next --answer-stdin <<'VANGOGH_ANSWER'\n  ```\n\n  Windows", "tests/test_vincent.py"),
]


def main() -> int:
    py = os.environ.get("PY") or sys.executable
    caught = 0
    for name, rel, old, new, tests in ROWS:
        base = Path(tempfile.mkdtemp(prefix="vincent-setup-mut-"))
        try:
            tree = base / "tree"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
                ".venv", "__pycache__", ".git", "*.pyc", ".pytest_cache"))
            target = tree / rel
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"BROKEN ROW  {name}: the text to change occurs {text.count(old)} times")
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            r = subprocess.run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                                *tests.split()], cwd=str(tree), capture_output=True, text=True,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=900)
            if r.returncode != 0 and " failed" in r.stdout:
                caught += 1
                first = next((ln for ln in r.stdout.splitlines() if ln.startswith("FAILED")), "")
                print(f"CAUGHT  {name}: {first[7:110]}")
            else:
                print(f"MISSED  {name}: rc {r.returncode}, {r.stdout.strip().splitlines()[-1][:120]}")
        finally:
            shutil.rmtree(base, ignore_errors=True)
    print(f"{caught} of {len(ROWS)} mutations caught")
    return 0 if caught == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
