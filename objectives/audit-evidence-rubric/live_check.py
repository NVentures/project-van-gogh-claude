#!/usr/bin/env python3
"""P23 Control A: the mutation matrix against the REAL rendered report.

A validator that has only ever passed a report it helped build proves nothing.
This mutates a COPY of the report the live skill actually wrote, one rule at a
time, and fails unless each mutation is caught by name. The copy matters: the
artifact being graded is never the artifact being mutated.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

import config_loader as cl                                      # noqa: E402
import audit_evidence as ae                                     # noqa: E402
import audit_ledger as al                                       # noqa: E402

REPORT = cl.van_gogh_root() / "vault-audit.md"
SIDECAR = cl.logs_dir() / "vault_audit_latest.json"
VAULT = cl.vault()
problems = []

original = REPORT.read_text(encoding="utf-8")
side = json.loads(SIDECAR.read_text(encoding="utf-8"))
tmp = Path(tempfile.mkdtemp(prefix="audit-mutate-"))
copy = tmp / "vault-audit.md"

# ── the control passes ───────────────────────────────────────────────────────
shutil.copy(REPORT, copy)
base = al.validate_report(copy, SIDECAR, VAULT)
print(f"control  ok={base['ok']} reasons={base['reasons']}")
if not base["ok"]:
    problems.append(f"the real report does not validate: {base['reasons']}")

# ── one mutation per rule ────────────────────────────────────────────────────
def code_row():
    for cid, row in side["score"]["rows"].items():
        if row["judged_by"] == "code":
            return cid, row["points"]
    return None, None


def model_row():
    for cid, row in side["score"]["rows"].items():
        if row["judged_by"] == "model":
            return cid
    return None


CID_CODE, PTS_CODE = code_row()
CID_MODEL = model_row()
total = re.search(r"\*\*Total (\d+) out of 100", original).group(1)
stage = ae.stage_for(int(total))
open_id = al.open_ids(side["ledger"])[0]

MUTATIONS = [
    ("missing_marker:score", ae.SCORE_BEGIN, ""),
    ("missing_marker:ledger", ae.LEDGER_END, ""),
    ("missing_marker:automation", ae.AUTOMATION_BEGIN, ""),
    ("dash_found:", "# ", f"# {chr(0x2014)} "),
    ("developer_word:sidecar", "Audit date:", "Sidecar note. Audit date:"),
    ("audit_date_mismatch:", f"Audit date: {side['today']}", "Audit date: 2026-01-01"),
    ("total_mismatch:", f"**Total {total} out of 100", "**Total 98 out of 100"),
    ("stage_mismatch:", f"Stage: {stage}", "Stage: Leveraged"),
    ("ledger_row_missing:", open_id, "0" * len(open_id)),
    ("automation_missing_candidate", "Candidate:", "Candidate:x\nOld:"),
    ("automation_missing_kpi", "KPI:", "Metric:"),
    ("automation_missing_size", "Size:", "Scale:"),
    ("automation_missing_picked", f"Picked: {side['today']}", "Filed: x"),
    ("automation_missing_refs", "Refs:", "Sources:"),
    ("automation_eliminate_no_verdict", "Eliminate: stop,",
     "Eliminate: it seems worth reconsidering at some point in the future,"),
]

# The score-row mutations need the row's exact rendered line.
row_line = re.search(rf"^\|\s*{CID_CODE}\s*\|[^|]*\|\s*{PTS_CODE}\s*\|",
                     original, re.MULTILINE)
if row_line:
    bad = row_line.group(0).replace(f"| {PTS_CODE} |", "| 5 |") if PTS_CODE != 5 \
        else row_line.group(0).replace("| 5 |", "| 3 |")
    MUTATIONS.append((f"score_mismatch:{CID_CODE}", row_line.group(0), bad))

model_line = re.search(rf"^\|\s*{CID_MODEL}\s*\|([^|]*)\|\s*(\d)\s*\|(.*)\|$",
                       original, re.MULTILINE)
if model_line:
    MUTATIONS.append((f"model_points_invalid:{CID_MODEL}", model_line.group(0),
                      model_line.group(0).replace(f"| {model_line.group(2)} |", "| 4 |")))
    if model_line.group(2) != "0":
        MUTATIONS.append((f"citation_unresolved:{CID_MODEL}", model_line.group(0),
                          f"| {CID_MODEL} |{model_line.group(1)}| "
                          f"{model_line.group(2)} | it looked fine to me |"))

for expected, old, new in MUTATIONS:
    if old not in original:
        problems.append(f"{expected}: mutation target absent: {old[:40]!r}")
        continue
    copy.write_text(original.replace(old, new, 1), encoding="utf-8")
    got = al.validate_report(copy, SIDECAR, VAULT)
    hit = any(r.startswith(expected) for r in got["reasons"])
    print(f"{'ok  ' if hit and not got['ok'] else 'FAIL'} {expected:38} "
          f"-> {[r for r in got['reasons'] if r.startswith(expected)] or got['reasons'][:2]}")
    if not hit:
        problems.append(f"{expected} was not caught: {got['reasons']}")

# ── the real report is untouched ─────────────────────────────────────────────
if REPORT.read_text(encoding="utf-8") != original:
    problems.append("the real report was modified by the mutation run")
else:
    print("ok   the real report on disk is byte identical after the run")

shutil.rmtree(tmp, ignore_errors=True)
print()
if problems:
    print(f"FAILED with {len(problems)} problems")
    for p in problems:
        print("  ", p)
    raise SystemExit(1)
print(f"PASSED {len(MUTATIONS)} mutations, every one caught by name")
