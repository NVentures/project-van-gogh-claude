#!/usr/bin/env python3
"""P15 and P22.

P22: the repo's stated main coupling is that a SKILL.md renders whatever key
the script emits. When they drift, the skill prints nothing and nobody notices,
because a missing section looks like an empty day. This checks every key the
changed skills reference actually exists in the script's output.

P15: the permission-prompt install step, verified as safe by construction
rather than by reading it and nodding.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401

problems = []

# ── P22 ──────────────────────────────────────────────────────────────────────
print("P22  every key a skill renders exists in its script's output")

# Every SKILL.md this branch changed, paired with the script it invokes, and
# the keys it references. Hardcoding three files was how `nudges` shipped in
# two skills whose scripts never emitted it: the section simply never appeared,
# and a missing section looks exactly like a quiet day.
import subprocess
CHANGED = subprocess.run(
    ["git", "diff", "--name-only", "origin/main...HEAD", "--", "skills/"],
    capture_output=True, text=True, cwd=str(ROOT)).stdout.split()

SCRIPT_FOR = {
    "week": "week_review", "morning-coffee": "morning_coffee",
    "afternoon-tea": "afternoon_tea", "week-retro": "week_retro",
    "five-fifteen": "five_fifteen", "meeting-prep": "meeting_prep",
    "relationship-radar": "relationship_radar", "vault-audit": "vault_gardener",
    "follow-up-radar": "follow_up_radar",
    "calendar-stub-check": "calendar_stub_check",
}
# The keys this work introduced. Anything a skill renders must be emitted.
NEW_KEYS = ["buckets", "buckets_md", "nudges", "judgment"]


def emitted_keys(module_name):
    src = (ROOT / "app" / f"{module_name}.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'output\[[\'"]([a-z_]+)[\'"]\]\s*=', src))
    keys |= set(re.findall(r'^\s+[\'"]([a-z_]+)[\'"]\s*:', src, re.M))
    return keys


checked = 0
for skill_path in sorted(CHANGED):
    name = Path(skill_path).parent.name
    module = SCRIPT_FOR.get(name)
    if not module or not (ROOT / "app" / f"{module}.py").exists():
        continue
    text = (ROOT / skill_path).read_text(encoding="utf-8")
    keys = emitted_keys(module)
    for key in NEW_KEYS:
        # A key REFERENCE, not the English word. "sending nudges" in prose and
        # "Add a judgment" are sentences, not contract items; `nudges` and
        # {buckets_md} are.
        if not re.search(rf"`{key}`|\{{{key}\}}|\[[\'\"]{key}[\'\"]\]", text):
            continue
        checked += 1
        emitted = key in keys
        print(f"     {name:20} {key:12} emitted={emitted!s:5} "
              f"{'ok' if emitted else 'MISSING'}")
        if not emitted:
            problems.append(
                f"P22 {skill_path} renders {key!r}, {module}.py never emits it")
print(f"     checked {checked} key references across "
      f"{len([c for c in CHANGED if Path(c).parent.name in SCRIPT_FOR])} changed skills")

# ── P15 ──────────────────────────────────────────────────────────────────────
print("\nP15  the permission-prompt step is safe by construction")
install = (ROOT / "skills/install-van-gogh/SKILL.md").read_text(encoding="utf-8")
step = install[install.index("## Step 7b"):install.index("## Step 8")] \
    if "## Step 7b" in install else ""

checks = {
    "the step exists": bool(step),
    "asks rather than assumes": "AskUserQuestion" in step,
    "default is the safe answer": "default the safe answer" in step
                                  or "recommended" in step.lower(),
    "states the machine-wide blast radius": "every project on this computer" in step
                                            or "every project" in step,
    "shows the current settings first": "cat " in step and "settings.json" in step,
    "edits are targeted, not a rewrite": "targeted" in step.lower()
                                         and "never rewrite" in step.lower(),
    "gives an undo": "undo" in step.lower(),
    "silent on decline": "decline" in step.lower(),
    "has a PowerShell form": "powershell" in step.lower(),
}
for label, ok in checks.items():
    print(f"     {'ok ' if ok else 'NO '} {label}")
    if not ok:
        problems.append(f"P15 install step: {label} is missing")

# Nothing in the repo may ship the setting itself.
shipped = []
for p in list(ROOT.rglob("*.json")) + list(ROOT.rglob("*.md")):
    if ".git/" in str(p) or "objectives/" in str(p) or ".venv" in str(p):
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if '"defaultMode"' in t and "bypassPermissions" in t and "SKILL.md" not in p.name:
        shipped.append(str(p.relative_to(ROOT)))
print(f"     {'ok ' if not shipped else 'NO '} the repo ships no bypassPermissions setting"
      f"{': ' + ', '.join(shipped) if shipped else ''}")
if shipped:
    problems.append(f"P15 the repo ships the setting itself: {shipped}")

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
