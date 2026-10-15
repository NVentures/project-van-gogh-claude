#!/usr/bin/env python3
"""P23 Control B: plant real evidence, predict the score, check the prediction.

A rubric that only ever reports zero is indistinguishable from one that is
broken. This copies the REAL vault's logs, plants the evidence that is missing
(a briefing record showing both accounts returning mail, and a fortnight of
successful runs), and asserts the exact rows the rules predict, by number.
"""
import json
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

import audit_evidence as ae                                     # noqa: E402
import config_loader as cl                                      # noqa: E402

TODAY = date.today()
problems = []

real_logs = cl.logs_dir()
tmp = Path(tempfile.mkdtemp(prefix="audit-twin-"))
logs = tmp / "logs"
shutil.copytree(real_logs, logs)


def paths_with(logs_dir, labels, alerts=True):
    p = ae.paths_from_config(cl)
    p.logs = logs_dir
    p.account_labels = labels
    p.alerts_enabled = alerts
    return p


LABELS = cl.account_labels() or ["Gmail", "Outlook"]
jobs = [{"label": f"com.monet.{n}", "kind": "skill", "name": n,
         "state": "loaded", "path": "/x"} for n in ("meeting-ingest", "ingest-workspace")]

# ── before: the real logs, unplanted ─────────────────────────────────────────
before_paths = paths_with(logs, LABELS, alerts=False)
recs = ae.collect_evidence(TODAY, before_paths, jobs, [])
probes = ae.run_probes(before_paths, TODAY)
before = ae.score_criteria(recs, probes, before_paths, jobs, [], TODAY)
print(f"before   N1={before['N1']['points']}  D2={before['D2']['points']}  "
      f"D1={before['D1']['points']}")

# ── plant: a clean briefing record, and runs on three separate days ──────────
(logs / "afternoon_tea_latest.json").write_text(json.dumps({
    "today": TODAY.isoformat(),
    "errors": [],
    "sent": [{"account": label} for label in LABELS],
}), encoding="utf-8")

runs = []
for i, name in enumerate(("meeting-ingest", "ingest-workspace", "meeting-ingest")):
    runs.append({"job": f"scheduled.{name}",
                 "started": (TODAY - timedelta(days=i + 1)).isoformat() + "T04:30:00",
                 "finished": (TODAY - timedelta(days=i + 1)).isoformat() + "T04:34:00",
                 "rc": 0})
(logs / "runs.jsonl").write_text(
    "\n".join(json.dumps(r) for r in runs) + "\n", encoding="utf-8")

after_paths = paths_with(logs, LABELS, alerts=True)
recs2 = ae.collect_evidence(TODAY, after_paths, jobs, runs)
after = ae.score_criteria(recs2, probes, after_paths, jobs, runs, TODAY)
print(f"after    N1={after['N1']['points']}  D2={after['D2']['points']}  "
      f"D1={after['D1']['points']}")

# ── the predictions, named as numbers ────────────────────────────────────────
EXPECT = {
    "N1 before": (before["N1"]["points"], 0),
    "N1 after": (after["N1"]["points"], 5),
    "D2 before": (before["D2"]["points"], 0),
    "D2 after": (after["D2"]["points"], 5),
    "D1 with jobs loaded": (after["D1"]["points"], 5),
}
for name, (got, want) in EXPECT.items():
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:24} got {got}, predicted {want}")
    if not ok:
        problems.append(f"{name}: got {got}, predicted {want}")

# ── the real logs were never touched ─────────────────────────────────────────
if not (real_logs / "runs.jsonl").exists():
    print("ok   the real vault still has no run ledger: nothing was planted in it")
else:
    problems.append("the planted run ledger reached the real vault")

shutil.rmtree(tmp, ignore_errors=True)
print()
if problems:
    print(f"FAILED with {len(problems)} problems")
    for p in problems:
        print("  ", p)
    raise SystemExit(1)
print("PASSED every planted row moved to the predicted number")
