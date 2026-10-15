#!/usr/bin/env python3
"""P12: do the tests actually bind to the features, or just co-exist with them?

A suite that stays green when a feature is ripped out is not testing that
feature. Each mutation below removes one behavior, runs the suite, records
which named test caught it, and restores the file. Every edit is buffered and
written once, and the restore is unconditional, so an interrupt cannot leave a
half-mutated file behind.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/bin/python")

MUTATIONS = [
    ("priorities in config",
     "app/config_loader.py",
     [("            return list(b.get(\"priorities\", []))",
       "            return []")]),
    ("the demotion",
     "app/week_review.py",
     [("                if not relevant:",
       "                if False:")]),
    ("the deadline guard",
     "app/priority_judge.py",
     [("    if entry.get(\"days_late\") or entry.get(\"due\"):\n        return True",
       "    if False:\n        return True")]),
    ("the blind-bucket guard",
     "app/week_review.py",
     [("    blind_buckets = {tag for tag, hit in seen_by_bucket.items() if not hit}",
       "    blind_buckets = set()")]),
    ("nudges",
     "app/follow_up_radar.py",
     [("        if it.get(\"status\") != \"overdue\":\n            continue",
       "        if True:\n            continue")]),
]


def run_suite():
    r = subprocess.run([PY, "-m", "pytest", "-q", "--no-header", "-x", "-q"],
                       capture_output=True, text=True, cwd=str(ROOT))
    names = re.findall(r"FAILED (\S+)", r.stdout)
    if not names:
        names = re.findall(r"^(tests/\S+::\S+)", r.stdout, re.M)
    return r.returncode, names


def main():
    failures = []
    for label, relpath, edits in MUTATIONS:
        path = ROOT / relpath
        original = path.read_text(encoding="utf-8")
        text = original
        ok = True
        for old, new in edits:
            if text.count(old) != 1:
                print(f"[{label}] SKIP: anchor not found exactly once in {relpath}")
                ok = False
                break
            text = text.replace(old, new)
        if not ok:
            failures.append(label)
            continue
        try:
            path.write_text(text, encoding="utf-8")
            rc, names = run_suite()
        finally:
            path.write_text(original, encoding="utf-8")
        caught = rc != 0
        print(f"[{label}] suite went {'RED' if caught else 'GREEN'}"
              + (f", caught by {names[0]}" if names else ""))
        if not caught:
            failures.append(label)

    rc, _ = run_suite()
    print(f"\nrestored, suite green: {rc == 0}")
    print("RESULT:", "FAIL" if failures or rc != 0 else "PASS")
    if failures:
        print("features with no test binding:", failures)
    return 1 if (failures or rc != 0) else 0


if __name__ == "__main__":
    sys.exit(main())
