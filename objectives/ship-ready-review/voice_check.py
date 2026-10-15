#!/usr/bin/env python3
"""P26 and P27: does this read like a person wrote it?

The reader runs a business. They did not ask for software vocabulary and they
can tell when a machine wrote something. Two scans over real rendered output:
developer words that leaked out of the data model, and the tics that make
text read as AI-generated.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401
import config_loader as cl                                      # noqa: E402
import week_review as wr                                        # noqa: E402

problems = []

DEVELOPER_WORDS = [
    "unassigned", "no business keyword matched", "other signal", "buckets_md",
    "nudges", "priority_matched", "judged_relevant", "cold_urgent",
    "cold_monitor", "inbox_pending", "waiting_on_user", "None", "null",
    "True", "False", "dict", "json", "config.json", "traceback", "exception",
    "boolean", "enum", "verdict", "degraded_reason", "demoted_because",
    # The front-page work added its own vocabulary. Every one of these is a
    # word a developer uses about the data and a reader has no reason to meet.
    "bucket_tag", "judged", "sidecar", "degraded", "rank", "fold", "front_page",
    "fold_rows", "folded_md", "draft_note", "assign_function", "stale_item",
]
AI_TELLS = [
    "delve", "utilize", "robust", "seamless", "comprehensive", "leverage the",
    "it is worth noting", "it's worth noting", "i have analyzed",
    "i've analyzed", "here is a breakdown", "here's a breakdown",
    "in summary", "let me", "great question", "may potentially",
    "furthermore", "moreover", "in conclusion", "it should be noted",
    "navigate the complexities", "tapestry", "ever-evolving", "landscape of",
]

cl._config = dict(cl._config)
cl._config["judgment"] = {"enabled": False, "model": ""}
cl._config["businesses"] = [
    {"tag": "power", "display_name": "Power Market", "project_page": "p.md",
     "meeting_route": "m", "keywords": ["northwind", "qx"],
     "priorities": [{"name": "Close Northwind"}, {"name": "Close QX Corp"}]},
    {"tag": "board", "display_name": "Board Seat", "project_page": "p.md",
     "meeting_route": "m", "keywords": ["board"], "priorities": []},
]
out = {
    "waiting_on_user": [{"subject": "Northwind redline v4", "days_late": 3,
                         "counterparty_email": "a@b.com"}],
    "inbox_pending": [{"subject": "QX Corp signature page",
                       "counterparty_email": "b@c.com"}],
    "cold_urgent": [],
    "cold_monitor": [{"subject": "Board packet for October",
                      "counterparty_email": "c@d.com", "age_days": 20},
                     {"subject": "Office furniture invoice",
                      "counterparty_email": "d@e.com", "age_days": 30}],
}

renders = {}
for briefing in ("morning-coffee", "afternoon-tea", "week"):
    for mode in ("terminal", "md"):
        renders[f"{briefing}/{mode}"] = wr.render_buckets_md(
            wr.group_by_bucket(out), briefing, mode)
renders["week/degraded"] = wr.render_buckets_md(
    wr.group_by_bucket(out), "week",
    judgment={"degraded": True, "degraded_reasons": ["the usage limit was reached"]})

# The front page is what a reader now opens on, so it is the render that most
# needs this scan. Three shapes: a normal morning, a quiet one, and a bad one.
for entries in out.values():
    for e in entries:
        e["bucket"] = wr.assign_bucket(e)
_buckets = wr.group_by_bucket(out)
_built = wr.build_front_page(_buckets, "morning-coffee")
renders["front/page"] = _built["front_page_md"]
renders["front/rows"] = _built["fold_rows_md"]
renders["front/folded"] = _built["folded_md"]
renders["front/quiet"] = wr.render_front_page_md(
    [], {"open": 0, "late": 0, "front": 0, "folded": 0, "stale": 0})
renders["front/slice"] = wr.fold_slice(
    _buckets, {tuple(i["key"]) for i in _built["front_page"]}, "board")

print("P26  no developer vocabulary in anything a client reads")
for name, text in renders.items():
    low = text.lower()
    for word in DEVELOPER_WORDS:
        if word.lower() in low:
            problems.append(f"P26 {name} contains {word!r}")
            print(f"     HIT {name}: {word!r}")
print(f"     scanned {len(renders)} rendered outputs, "
      f"{sum(1 for p in problems if p.startswith('P26'))} hits")

print("\nP27  no AI tells")
for name, text in renders.items():
    low = text.lower()
    for tell in AI_TELLS:
        if tell in low:
            problems.append(f"P27 {name} contains {tell!r}")
            print(f"     HIT {name}: {tell!r}")
print(f"     scanned {len(renders)} rendered outputs, "
      f"{sum(1 for p in problems if p.startswith('P27'))} hits")

print("\nSample, so a person can read what a client would actually see:\n")
print(renders["front/page"])
print(renders["front/rows"])
print(renders["week/terminal"])
print("\nAnd the degraded notice:\n")
print(renders["week/degraded"].splitlines()[0])

print("\nRESULT:", "FAIL" if problems else "PASS")
for p in problems:
    print("  " + p)
sys.exit(1 if problems else 0)
