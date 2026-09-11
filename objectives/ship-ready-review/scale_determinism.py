#!/usr/bin/env python3
"""P7 (scale) and P8 (determinism), in one run.

P7: 40 buckets, 200 priorities, 500 items. The rendered bucket set must equal
the configured bucket set exactly. Silent truncation is the failure being
hunted: a briefing that drops bucket 13 onward still looks complete.

P8: the same input rendered three times must be byte-identical with the
judgment layer off. The model's variance is allowed to live in the relevance
call and nowhere else.
"""
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401
import config_loader as cl                                      # noqa: E402
import week_review as wr                                        # noqa: E402

N_BUCKETS, N_PRIOS, N_ITEMS = 40, 5, 500

cl._config = dict(cl._config)
cl._config["judgment"] = {"enabled": False, "model": ""}
cl._config["businesses"] = [
    {"tag": f"b{i:02d}", "display_name": f"Business {i:02d}",
     "project_page": "p.md", "meeting_route": "m",
     "keywords": [f"kw{i:02d}"],
     "priorities": [{"name": f"Priority {j} for kw{i:02d}"} for j in range(N_PRIOS)]}
    for i in range(N_BUCKETS)
]
items = [{"subject": f"kw{i % N_BUCKETS:02d} thread {i} about Priority {i % N_PRIOS}",
          "counterparty_email": f"p{i}@ext.com", "age_days": i % 30}
         for i in range(N_ITEMS)]

out = {"waiting_on_user": items[:200], "inbox_pending": items[200:350],
       "cold_urgent": items[350:450], "cold_monitor": items[450:],
       "filtered_pending": []}

t0 = time.time()
buckets = wr.group_by_bucket(out)
rendered = wr.render_buckets_md(buckets, "week")
elapsed = time.time() - t0

configured = {b["tag"] for b in cl.businesses()}
got = {b["tag"] for b in buckets} - {"unassigned"}
missing = configured - got
extra = got - configured

print(f"P7  configured buckets: {len(configured)}")
print(f"P7  rendered buckets:   {len(got)}")
print(f"P7  missing:            {sorted(missing) or 'none'}")
print(f"P7  unexpected:         {sorted(extra) or 'none'}")
print(f"P7  items in:           {sum(len(v) for k, v in out.items() if k != 'filtered_pending')}")
print(f"P7  items rendered:     {sum(len(b['items']) for b in buckets)}")
print(f"P7  rendered chars:     {len(rendered)}")
print(f"P7  wall clock:         {elapsed:.2f}s")

hashes = []
for _ in range(3):
    b = wr.group_by_bucket({k: list(v) for k, v in out.items()})
    hashes.append(hashlib.sha256(
        wr.render_buckets_md(b, "week").encode("utf-8")).hexdigest())
print(f"\nP8  three renders: {hashes[0][:16]} {hashes[1][:16]} {hashes[2][:16]}")
print(f"P8  identical:     {len(set(hashes)) == 1}")

fail = bool(missing or extra) or len(set(hashes)) != 1 or \
    sum(len(b["items"]) for b in buckets) != N_ITEMS
print("\nRESULT:", "FAIL" if fail else "PASS")
sys.exit(1 if fail else 0)
