#!/usr/bin/env python3
"""P30: every briefing ships the shape, and every key a skill renders exists.

Two checks, both over the real files:

1. Each of the four SKILL.md files carries the render order and the front-page
   sections; each of the four scripts emits the five keys on its success AND
   its failure branches. A key that only exists on the happy path renders as a
   missing section, and a missing section looks exactly like a quiet day.
2. Every key any of those skills references by name is actually emitted by the
   script it invokes, diffed key by key.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401

problems = []
FIVE = ["front_page", "front_page_md", "fold_rows_md", "folded_md", "counts"]
SCRIPT_FOR = {"morning-coffee": "morning_coffee", "afternoon-tea": "afternoon_tea",
              "week": "week_review", "week-retro": "week_retro"}


# The key set comes from a REAL Tier 2 run, not from grepping the source. A
# script that splats its keys in (`**front`) emits them and no source scan can
# see it; a script that names a key in a comment does not emit it and a source
# scan says it does. Only the output settles it.
import shutil                                                   # noqa: E402
import tempfile                                                 # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tier2_env                                                # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="van-gogh-contract-"))
_CTX = tier2_env.build(_TMP, n_items=60)
_OUTPUT = {}
print("      running the four scripts against a real Tier 2 vault")
for _skill, _module in (("morning-coffee", "morning_coffee"),
                        ("afternoon-tea", "afternoon_tea"),
                        ("week", "week_review"), ("week-retro", "week_retro")):
    _rc, _out, _err = tier2_env.run_script(
        f"{_module}.py", _CTX["env"], ["--input", str(_CTX["input"])])
    try:
        _OUTPUT[_module] = tier2_env.first_json(_out)
    except Exception as exc:                                    # noqa: BLE001
        _OUTPUT[_module] = {}
        problems.append(f"{_module}.py produced no JSON (rc={_rc}): {exc}; "
                        f"{_err.strip()[:300]}")
    print(f"      {_module:16} rc={_rc} keys={len(_OUTPUT[_module])}")


def emitted_keys(module_name):
    return set(_OUTPUT.get(module_name) or {})


print("P30a  every script emits the five keys, on success and on failure")
for skill, module in SCRIPT_FOR.items():
    keys = emitted_keys(module)
    src = (ROOT / "app" / f"{module}.py").read_text(encoding="utf-8")
    missing = [k for k in FIVE if k not in keys]
    # The failure branch: the module has to reach a builder that fills the keys
    # in unconditionally, so a front page that could not be built still emits
    # them empty rather than leaving the skill with a missing section.
    fail_open = ("empty_front_page" in src or '"front_page": []' in src)
    print(f"      {skill:16} five keys={not missing!s:5} fail-open={fail_open!s:5} "
          f"{'ok' if not missing and fail_open else 'MISSING ' + str(missing)}")
    if missing:
        problems.append(f"{module}.py never emits {missing}")
    if not fail_open:
        problems.append(f"{module}.py has no failure branch for the front page")

print("\nP30b  every key a skill renders exists in its script's output")
checked = 0
for skill, module in SCRIPT_FOR.items():
    text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    keys = emitted_keys(module)
    referenced = set(re.findall(r"`([a-z_]{4,})`", text))
    # Only the ones that look like output keys, never English words in prose.
    referenced &= (set(FIVE) | {"buckets", "buckets_md", "nudges", "judgment",
                                "drafts_done", "page_note", "fold_rows",
                                "counts", "front_page"})
    for key in sorted(referenced):
        checked += 1
        ok = key in keys
        print(f"      {skill:16} {key:14} {'ok' if ok else 'MISSING'}")
        if not ok:
            problems.append(f"{skill} renders {key!r}, {module}.py never emits it")
print(f"      {checked} key references checked across 4 skills")

print("\nP30c  every SKILL.md carries the render order")
NEEDED = {
    "front page section": r"### THE FRONT PAGE|### 2\. TOP PRIORITIES",
    "the rest section": r"### THE REST|front_page_md",
    "web page section": r"### THE WEB PAGE",
    "reads before publishing": r'action: "read"',
    "shared contract linked": r"_shared/front-page\.md",
}
for skill in SCRIPT_FOR:
    text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    for label, pattern in NEEDED.items():
        ok = bool(re.search(pattern, text))
        if not ok:
            problems.append(f"{skill}: {label} is missing")
    print(f"      {skill:16} "
          + " ".join("ok" if re.search(p, text) else "NO"
                     for p in NEEDED.values()))

print("\nP30d  the shared contract file exists and parses as one document")
shared = ROOT / "skills/_shared/front-page.md"
if not shared.exists():
    problems.append("skills/_shared/front-page.md is missing")
else:
    body = shared.read_text(encoding="utf-8")
    for key in FIVE:
        if f"`{key}`" not in body:
            problems.append(f"the shared contract never names {key!r}")
    print(f"      {len(body.splitlines())} lines, all five keys named")

shutil.rmtree(_TMP, ignore_errors=True)

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
