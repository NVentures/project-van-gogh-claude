#!/usr/bin/env python3
"""P27: zero em or en dashes in the added diff lines and in rendered output.

Self-tests first. A scanner that has never found a dash it knows is there
cannot be trusted when it reports zero.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

EM = chr(0x2014)
EN = chr(0x2013)

problems = []

# ── the scanner proves itself first ──────────────────────────────────────────
planted = f"a line with an {EM} in it and an {EN} too"
found = [c for c in (EM, EN) if c in planted]
if len(found) != 2:
    print("FAIL  the scanner cannot find a dash it planted itself")
    raise SystemExit(1)
print("ok    the scanner finds a planted em dash and a planted en dash")

# ── added diff lines ─────────────────────────────────────────────────────────
#
# Against the branch this work is stacked on, not against main. Diffing to main
# from a stacked branch scans thirty-one commits somebody else wrote, and a
# dash in one of those is not this change's to answer for.
BASES = ["origin/feat/workbench", "origin/main"]
base = ""
for candidate in BASES:
    got = subprocess.run(["git", "merge-base", candidate, "HEAD"],
                         capture_output=True, text=True, cwd=str(ROOT))
    if got.returncode == 0 and got.stdout.strip():
        base = got.stdout.strip()
        print(f"ok    scanning what this branch added on top of {candidate}")
        break

# A report about a dash has to be allowed to quote the dash. The evaluation log
# in a punch list is a record of findings, not product output, so it is read
# for its own text and never counted as a violation.
EXEMPT = ("objectives/briefing-front-page/punchlist.md",
          "objectives/ship-ready-review/punchlist.md")


def added_lines(diff_text):
    out, current = [], ""
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git "):
            current = ln.split(" b/", 1)[-1]
            continue
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+") and current not in EXEMPT:
            out.append((current, ln))
    return out


added = added_lines(subprocess.run(
    ["git", "diff", f"{base}...HEAD"] if base else ["git", "diff", "HEAD"],
    capture_output=True, text=True, cwd=str(ROOT)).stdout)
added += added_lines(subprocess.run(["git", "diff"], capture_output=True,
                                    text=True, cwd=str(ROOT)).stdout)
hits = [(f, ln) for f, ln in added if EM in ln or EN in ln]
print(f"ok    {len(added)} added diff lines scanned, {len(hits)} carry a dash")
for f, ln in hits[:20]:
    problems.append(f"added line in {f}: {ln.strip()[:100]}")

# ── rendered fixture strings ─────────────────────────────────────────────────
import conftest                                                 # noqa: E402,F401
import config_loader as cl                                      # noqa: E402
import briefing_fixtures as bf                                  # noqa: E402

bf.set_businesses(cl)
import week_review as wr                                        # noqa: E402
import briefing_html as bh                                      # noqa: E402

sections = bf.random_sections(seed=2, n_items=140)
for entries in sections.values():
    for e in entries:
        e["bucket"] = wr.assign_bucket(e)
buckets = wr.group_by_bucket(sections)
built = wr.build_front_page(buckets, "morning-coffee")

rendered = {
    "front_page_md": built["front_page_md"],
    "fold_rows_md": built["fold_rows_md"],
    "folded_md": built["folded_md"],
    "opening_sentence": wr.opening_sentence(built["counts"]),
    "page_html": bh.render_page(built["front_page_md"] + built["folded_md"],
                                {"title": "Morning Coffee",
                                 "briefing_date": "2026-09-04"}),
}
for name, text in rendered.items():
    for ch, label in ((EM, "em dash"), (EN, "en dash")):
        if ch in text:
            problems.append(f"{name} contains an {label}")
print(f"ok    {len(rendered)} rendered strings scanned")

if problems:
    print(f"\nFAIL  {len(problems)} problems")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)
print("\nPASS  0 em or en dashes")
