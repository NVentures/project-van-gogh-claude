#!/usr/bin/env python3
"""P6: does the demotion behave on data it was not tuned on?

Three configs, written the three ways real people write priorities:

  things       "Close QX Corp"            nouns, matchable by overlap
  intentions   "grow the pipeline"        the failure that motivated the layer
  none         no priorities at all       the majority of fresh installs

Each item is labeled with what SHOULD happen to it. The run fails on any guard
violation, not on a score: a deadline that gets hidden is a failure at any
precision. The control arm runs the identical corpus with the judgment layer
off, and its result must be measurably worse, or this test is not testing
anything.

Usage:
    .venv/bin/python objectives/ship-ready-review/corpus_eval.py [--control]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

import conftest                                                 # noqa: E402,F401
import config_loader as cl                                      # noqa: E402
import week_review as wr                                        # noqa: E402

# keep = must stay in the urgent section. demote = may be moved down.
CORPUS = {
    "things": {
        "priorities": [{"name": "Close QX Corp"}, {"name": "Close Northwind"}],
        "keywords": ["qx", "northwind", "acme"],
        "items": [
            ("QX Corp redline v4", "keep"),
            ("QX Corp signature page", "keep"),
            ("Northwind diligence list", "keep"),
            ("Northwind kickoff timing", "keep"),
            ("QX Corp counterparty intro", "keep"),
            ("Acme office furniture invoice", "demote"),
            ("Acme parking policy update", "demote"),
            ("Acme holiday party RSVP", "demote"),
            ("Acme printer toner reorder", "demote"),
            ("Acme contract expires 2026-09-30", "keep"),
        ],
    },
    "intentions": {
        "priorities": [{"name": "grow the pipeline"},
                       {"name": "keep the team happy"}],
        "keywords": ["acme", "widget"],
        "items": [
            ("Acme redline attached, need signature by Friday", "keep"),
            ("Widget deal term sheet from the buyer", "keep"),
            ("Acme new lead from the conference", "keep"),
            ("Widget team offsite planning", "keep"),
            ("Acme salary review for the sales team", "keep"),
            ("Acme two engineers resigned this week", "keep"),
            ("Acme office furniture invoice", "demote"),
            ("Widget parking policy update", "demote"),
            ("Acme printer toner reorder", "demote"),
            ("Widget building fire drill notice", "demote"),
        ],
    },
    "none": {
        "priorities": [],
        "keywords": ["acme", "widget"],
        "items": [
            ("Acme anything at all", "keep"),
            ("Widget anything at all", "keep"),
            ("Acme office furniture invoice", "keep"),
            ("Widget printer toner reorder", "keep"),
            ("Acme redline needs signature", "keep"),
            ("Widget parking policy", "keep"),
            ("Acme holiday party", "keep"),
            ("Widget fire drill", "keep"),
            ("Acme lead from conference", "keep"),
            ("Widget offsite planning", "keep"),
        ],
    },
}


def run_config(name, spec, use_judgment):
    cl._config = dict(cl._config)
    cl._config["judgment"] = {"enabled": use_judgment, "model": ""}
    cl._config["businesses"] = [{
        "tag": name, "display_name": name.title(), "project_page": "p.md",
        "meeting_route": "m", "keywords": spec["keywords"],
        "priorities": spec["priorities"],
    }]
    entries = [{"subject": s, "counterparty_email": f"p{i}@ext.com",
                "counterparty_name": "Someone", "age_days": 5, "_expect": exp}
               for i, (s, exp) in enumerate(spec["items"])]
    out = {"cold_urgent": entries, "cold_monitor": [], "filtered_pending": []}
    judgment = wr.apply_priority_judgment(out["cold_urgent"]) if use_judgment else None
    wr.gate_cold_promotion(out, active_emails=set(), judgment=judgment)

    kept = {e["subject"] for e in out["cold_urgent"]}
    rows = []
    for e in entries:
        actual = "keep" if e["subject"] in kept else "demote"
        rows.append((e["subject"], e["_expect"], actual, e.get("demoted_because", "")))
    return rows, out


def score(rows):
    correct = sum(1 for _, exp, act, _ in rows if exp == act)
    hidden = [s for s, exp, act, _ in rows if exp == "keep" and act == "demote"]
    noise = [s for s, exp, act, _ in rows if exp == "demote" and act == "keep"]
    return correct, hidden, noise


def main():
    control = "--control" in sys.argv
    arm = "CONTROL (judgment off)" if control else "LIVE (judgment on)"
    print(f"=== {arm} ===\n")
    violations, totals = [], []

    for name, spec in CORPUS.items():
        rows, out = run_config(name, spec, use_judgment=not control)
        correct, hidden, noise = score(rows)
        totals.append((name, correct, len(rows), hidden, noise))
        print(f"[{name}] {correct}/{len(rows)} correct")
        for subj, exp, act, why in rows:
            mark = "  " if exp == act else "XX"
            print(f"  {mark} {subj:<50} expected {exp:<6} got {act}"
                  + (f"  ({why})" if why else ""))

        # Guards, checked as guards and not as scores.
        for subj, exp, act, _ in rows:
            if act == "demote" and ("by Friday" in subj or "expires 2026" in subj
                                    or "signature" in subj.lower()):
                violations.append(f"[{name}] hid a dated item: {subj}")
        if name == "intentions" and not control:
            demoted = [s for s, _, act, _ in rows if act == "demote"]
            if not demoted:
                violations.append(
                    "[intentions] demoted nothing at all; the guard is masking "
                    "a judgment layer that is not working")
        if name == "none":
            demoted = [s for s, _, act, _ in rows if act == "demote"]
            if demoted:
                violations.append(
                    f"[none] demoted {len(demoted)} items in a config with no "
                    "priorities set")
        print()

    total_correct = sum(c for _, c, _, _, _ in totals)
    total_n = sum(n for _, _, n, _, _ in totals)
    total_hidden = sum(len(h) for _, _, _, h, _ in totals)
    print(f"TOTAL {total_correct}/{total_n} correct, {total_hidden} kept items hidden")

    if violations:
        print("\nGUARD VIOLATIONS:")
        for v in violations:
            print("  " + v)
        return 1
    print("\nno guard violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
