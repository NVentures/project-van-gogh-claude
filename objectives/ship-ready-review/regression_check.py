#!/usr/bin/env python3
"""P9 and P10: an existing client must see EXACTLY what they saw before.

Not "does not crash". Set equality against the real pre-feature commit, run
over the same fixture, for two configs: one that predates this work entirely
(no priorities, no memory_sync, no judgment), and one with no buckets at all.

The baseline is produced by checking out the pre-feature commit's week_review
into a temp dir and importing it, so the comparison is against real prior
behavior rather than a remembered description of it.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_REF = "9a5e91e"          # the commit before priorities landed

FIXTURE = [
    {"subject": "Term sheet redline", "counterparty_email": "alice@counsel.com",
     "age_days": 16},
    {"subject": "Following up", "counterparty_email": "old@client.com", "age_days": 45},
    {"subject": "RTO Feedback Request", "counterparty_email": "hr@bigco.com",
     "age_days": 18},
    {"subject": "Your account statement", "counterparty_email": "noreply@bank.com",
     "age_days": 20},
    {"subject": "Widget deal question", "counterparty_email": "bob@acme.com",
     "age_days": 12},
    {"subject": "Invoice 4471", "counterparty_email": "ap@vendor.com", "age_days": 30},
]

DRIVER = '''
import json, sys
sys.path.insert(0, {app!r})
sys.path.insert(0, {tests!r})
import conftest
import config_loader as cl
cl._config = dict(cl._config)
{config}
import week_review as wr
out = {{"cold_urgent": json.loads({fixture!r}), "cold_monitor": [],
        "filtered_pending": []}}
wr.gate_cold_promotion(out, active_emails=set())
print(json.dumps({{
    "urgent": sorted(e["subject"] for e in out["cold_urgent"]),
    "monitor": sorted(e["subject"] for e in out["cold_monitor"]),
    "filtered": sorted(e["subject"] for e in out["filtered_pending"]),
}}))
'''

# An old config: no priorities, no memory_sync, no judgment block at all.
OLD_CONFIG = '''
for b in cl._config["businesses"]:
    b.pop("priorities", None)
cl._config.pop("judgment", None)
cl._config.pop("memory_sync", None)
'''
NO_BUCKETS = '''
cl._config["businesses"] = []
cl._config.pop("judgment", None)
'''


def run(app_dir, tests_dir, config_stmt):
    code = DRIVER.format(app=str(app_dir), tests=str(tests_dir),
                         config=config_stmt, fixture=json.dumps(FIXTURE))
    r = subprocess.run([str(ROOT / ".venv/bin/python"), "-c", code],
                       capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"driver failed:\n{r.stderr[-1500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "baseline"
        base.mkdir()
        subprocess.run(f"git archive {BASELINE_REF} app tests | tar -x -C {base}",
                       shell=True, check=True, cwd=str(ROOT))

        failures = []
        for name, stmt in (("pre-change config", OLD_CONFIG),
                           ("no buckets configured", NO_BUCKETS)):
            before = run(base / "app", base / "tests", stmt)
            after = run(ROOT / "app", ROOT / "tests", stmt)
            same = before == after
            print(f"[{name}] identical: {same}")
            for section in ("urgent", "monitor", "filtered"):
                print(f"   {section:9} before={before[section]}")
                print(f"   {section:9} after ={after[section]}")
                if before[section] != after[section]:
                    failures.append(f"{name}/{section}")
            print()

    print("RESULT:", "FAIL" if failures else "PASS")
    if failures:
        print("differing sections:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
