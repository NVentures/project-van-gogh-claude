#!/usr/bin/env python3
"""Freeze the pre-change render output so P14 and P31 have a real control.

Run once, on the commit BEFORE the front-page work, and never again: a
snapshot regenerated from the code it is grading proves nothing. The files
land in tests/snapshots/ and tests/test_render_snapshots.py compares against
them on every run.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                  # noqa: E402,F401
import config_loader as cl                                       # noqa: E402
import briefing_fixtures as bf                                   # noqa: E402

bf.set_businesses(cl)

import week_review as wr                                         # noqa: E402
import digest_html                                               # noqa: E402

SNAP = ROOT / "tests" / "snapshots"
SNAP.mkdir(parents=True, exist_ok=True)

sections = bf.sample_sections()
for entry_list in sections.values():
    for e in entry_list:
        e["bucket"] = wr.assign_bucket(e)

buckets = wr.group_by_bucket(sections)

for briefing in ("week", "afternoon-tea"):
    for mode in ("terminal", "md"):
        text = wr.render_buckets_md(buckets, briefing, mode=mode)
        (SNAP / f"buckets_{briefing}_{mode}.txt").write_text(text, encoding="utf-8")

# digest HTML for all four briefings, over a representative markdown body
for name in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
    md = (f"# {name}\n\nA sentence about the day.\n\n"
          + wr.render_buckets_md(buckets, "week" if name != "afternoon-tea"
                                 else "afternoon-tea", mode="md"))
    (SNAP / f"digest_{name}.html").write_text(
        digest_html.md_to_email_html(md), encoding="utf-8")

print("froze", len(list(SNAP.iterdir())), "snapshot files")
