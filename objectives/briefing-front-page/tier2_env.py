#!/usr/bin/env python3
"""A throwaway Tier 2 install: a temp vault, a real config, a real input file.

This is what makes the live checks live. The scripts run as real subprocesses
against a real vault directory and a real `--input` fixture, exactly the way
Tier 2 runs on a machine where OAuth consent is blocked. Nothing here touches
the developer's own state: VAN_GOGH_STATE_DIR points at the temp dir too.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BUSINESSES = [
    {"tag": "harbor", "display_name": "Harbor Solar",
     "project_page": "wiki/projects/Harbor.md", "meeting_route": "10-Harbor/Meetings",
     "keywords": ["harbor", "meridian", "qx"],
     "priorities": [{"name": "Close Meridian", "detail": "", "added": "2026-08-01"},
                    {"name": "Land the QX diligence", "detail": "", "added": "2026-08-01"}]},
    {"tag": "northwind", "display_name": "Northwind DC",
     "project_page": "wiki/projects/Northwind.md", "meeting_route": "20-NW/Meetings",
     "keywords": ["northwind", "kestrel", "lumen"],
     "priorities": [{"name": "Sign Kestrel", "detail": "", "added": "2026-08-01"}]},
    {"tag": "cedar", "display_name": "Cedar",
     "project_page": "wiki/projects/Cedar.md", "meeting_route": "30-Cedar/Meetings",
     "keywords": ["cedar", "cedarworks"], "priorities": []},
    {"tag": "personal", "display_name": "Personal",
     "project_page": "wiki/projects/Personal.md", "meeting_route": "40-Personal/Meetings",
     "keywords": ["dentist", "summit"], "priorities": []},
]

SUBJECTS = ["redline v4", "diligence list", "term sheet", "grid study",
            "pricing page copy", "interconnect filing", "lease exhibit",
            "Q3 invoice", "travel booking", "engineering finalists",
            "signature page", "vendor quote", "board packet", "site walk",
            "payroll question", "RFP response", "budget review"]
NAMES = ["Dana Ruiz", "Tim Roberts", "Ray Okafor", "Marco Lind", "Ana Beltre",
         "Priya N.", "Brandon B.", "Sam Ito"]


def make_config(vault: Path, front_page_cap=7, publish_page=True) -> dict:
    cfg = json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))
    cfg["user"] = {"full_name": "Alex Reyes", "first_name": "Alex",
                   "name_variants": ["Alex Reyes", "Alex"],
                   "self_entities": ["Alex Reyes"],
                   "role_description": "Founder, four businesses",
                   "timezone": "America/Los_Angeles", "secondary_timezone": "America/New_York"}
    cfg["obsidian"]["vault_path"] = str(vault)
    cfg["businesses"] = BUSINESSES
    cfg["judgment"] = {"enabled": False, "model": ""}
    briefing = {"functions": ["Sales", "Finance", "Accounting", "Legal", "People",
                              "Operations", "Product", "Admin", "Other"],
                "publish_page": publish_page}
    if front_page_cap is not None:
        briefing["front_page_cap"] = front_page_cap
    cfg["briefing"] = briefing
    return cfg


def make_input(n_items: int = 100, seed: int = 21) -> dict:
    """A four-business, `n_items` Tier 2 connector payload."""
    rng = random.Random(seed)
    today = date.today()
    out = {"calendar": [], "waiting_on_user": [], "inbox_pending": [],
           "filtered_pending": [], "cold_urgent": [], "cold_monitor": []}
    sections = ["waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor"]
    for i in range(n_items):
        biz = rng.choice(BUSINESSES)
        kw = rng.choice(biz["keywords"])
        section = rng.choice(sections)
        entry = {
            "source": rng.choice(["Gmail", "Outlook"]),
            "subject": f"{kw.title()} {rng.choice(SUBJECTS)} {i}",
            "counterparty_name": rng.choice(NAMES),
            "counterparty_email": f"person{i}@counterparty{i % 9}.com",
            "body_preview": "Following up on the item we discussed.",
        }
        if section in ("waiting_on_user", "inbox_pending"):
            entry["reply_age_days"] = rng.randint(0, 30)
        else:
            entry["age_days"] = rng.randint(8, 70)
        roll = rng.random()
        if roll < 0.03:
            entry["days_late"] = rng.randint(1, 6)
        elif roll < 0.18:
            entry["due"] = (today + timedelta(days=rng.randint(0, 9))).isoformat()
        out[section].append(entry)
    out["calendar"] = [
        {"source": "Outlook", "title": "Kestrel term sheet call",
         "day": today.isoformat(), "time": "08:30",
         "attendees": ["ana@kestrel.com"], "duration_min": 30},
        {"source": "Gmail", "title": "Cedar product sync",
         "day": today.isoformat(), "time": "12:00", "attendees": [], "duration_min": 45},
    ]
    return out


def build(tmp: Path, n_items: int = 100, front_page_cap: int = 7,
          publish_page: bool = True, seed: int = 21) -> dict:
    """Lay out the vault and the state dir. Returns paths plus the child env."""
    vault = tmp / "vault"
    for rel in ("van-gogh/logs", "van-gogh/projects", "wiki/weekly", "wiki/sources",
                "wiki/entities", "wiki/projects"):
        (vault / rel).mkdir(parents=True, exist_ok=True)

    cfg = make_config(vault, front_page_cap=front_page_cap, publish_page=publish_page)
    (vault / "van-gogh" / "config.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8")

    state = tmp / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "vault-pointer").write_text(str(vault), encoding="utf-8")

    # A week file inside the seven-day window, so morning_coffee has one.
    monday = date.today() - timedelta(days=date.today().weekday())
    (vault / "wiki/weekly" / f"week-{monday.isoformat()}.md").write_text(
        f"---\ndate: {monday.isoformat()}\n---\n\n# Week of {monday.isoformat()}\n\n"
        "## Top Priorities\n\n1. Close Meridian\n2. Sign Kestrel\n\n"
        "## Deals - Waiting on Alex\n\n"
        '- [ ] [Outlook] "Meridian redline v4" from Dana Ruiz '
        "(dana@meridian.example.com) - 3 days\n\n"
        "## Open Tasks (Obsidian)\n\n- [ ] [Harbor Solar] Send the QX diligence list\n",
        encoding="utf-8")
    (vault / "wiki" / "hotcache.md").write_text(
        "# Hotcache\n\nupdated: " + date.today().isoformat() + "\n\n"
        "## Active Threads\n\n### Meridian Solar\n"
        "<!-- deal: stage=negotiating last_contact=" + date.today().isoformat() + " -->\n"
        "- dana@meridian.example.com\n\n"
        "### Kestrel Data Center\n"
        "<!-- deal: stage=dead last_contact=2026-06-01 -->\n"
        "- ana@kestrel.example.com\n\n"
        "## Alex's Action Items\n\n- [ ] Send the QX diligence list\n",
        encoding="utf-8")

    inp = tmp / "input.json"
    inp.write_text(json.dumps(make_input(n_items, seed=seed), indent=2),
                   encoding="utf-8")

    env = dict(os.environ)
    env["VAN_GOGH_STATE_DIR"] = str(state)
    env["VAN_GOGH_DISABLE_CLI_UPDATE"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    return {"vault": vault, "state": state, "input": inp, "env": env, "config": cfg}


def run_script(script: str, env: dict, args: list) -> tuple:
    """Run one app/ script as a real subprocess. Returns (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "app" / script)] + args,
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def first_json(text: str):
    """The first balanced JSON object in a stream that may carry prose first."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in output")


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="van-gogh-tier2-"))
    try:
        ctx = build(tmp)
        rc, out, err = run_script("morning_coffee.py", ctx["env"],
                                  ["--input", str(ctx["input"])])
        data = first_json(out)
        print("rc", rc, "keys", sorted(k for k in data if k.startswith("front")
                                       or k in ("counts", "fold_rows_md", "folded_md")))
        print(data["front_page_md"])
        print(data["fold_rows_md"])
        if err.strip():
            print("stderr:", err.strip()[:800], file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
