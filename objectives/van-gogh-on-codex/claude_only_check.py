#!/usr/bin/env python3
"""P16: the Claude-only features, run as real subprocesses under host codex.

Three entry points, each spawned with VAN_GOGH_HOST=codex in the scratch
world: the finance brief (QuickBooks through a claude.ai connector), the
connector lookup with a saved connector, and a Tier 2 script with no OAuth
token (meeting prep). Each must print one sentence naming Claude, never a
Traceback, and the rendered section must say the source is unavailable
rather than show a zero.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)

S = Path(os.environ["VG_SCRATCH"])
WT = Path(__file__).resolve().parents[2]
PY = str(WT / ".venv" / "bin" / "python")
ENV = {**{k: v for k, v in os.environ.items() if k != "VAN_GOGH_HOST"},
       "HOME": str(S / "home"), "VAN_GOGH_STATE_DIR": str(S / "home" / ".config" / "van-gogh"),
       "CODEX_HOME": str(S / "codex-home"), "VAN_GOGH_HOST": "codex",
       "VAN_GOGH_DISABLE_UPDATE_CHECK": "1"}


def run(name, argv):
    r = subprocess.run([PY, *argv], env=ENV, capture_output=True, text=True, encoding="utf-8",
                       cwd=str(WT), timeout=300, stdin=subprocess.DEVNULL)
    text = (r.stdout or "") + (r.stderr or "")
    claude = "needs Claude" in text or "need Claude" in text
    trace = "Traceback" in text
    print(f"{name}: rc={r.returncode} names_claude={claude} traceback={trace}")
    print("   stderr:", (r.stderr or "").strip().splitlines()[-1][:160] if (r.stderr or "").strip() else "")
    return r, claude and not trace


def main() -> int:
    ok_all = True
    r, ok = run("finance_brief.py --json", ["app/finance_brief.py", "--json"])
    try:
        doc = json.loads(r.stdout[r.stdout.index("{"):])
        page = doc.get("finance_md") or ""
        section_ok = ("Claude" in page and "not read" in page and not doc.get("sections")
                      and doc.get("errors") and "Claude" in doc["errors"][0])
        print("   section: the page says QuickBooks was not read and names Claude, no zero rendered:", section_ok)
    except (ValueError, KeyError):
        section_ok = False; print("   section: no JSON")
    ok_all &= ok and section_ok

    r, ok = run("connector_lookup.py --test crm --name", ["app/connector_lookup.py", "--test", "crm", "--name", "Nobody Example"])
    text = r.stdout + r.stderr
    print("   verdict mentions absent/unavailable:", ("absent" in text) or ("not available" in text))
    ok_all &= ok

    r, ok = run("meeting_prep.py --today (no OAuth token)", ["app/meeting_prep.py", "--today"])
    print("   Tier 2 line names Claude and OAuth:", "needs Claude" in r.stderr and "OAuth" in r.stderr)
    ok_all &= ok

    # meeting_prep with a connector configured: a replayed calendar carries
    # one outside attendee, so live_context reaches the saved connector.
    import datetime as _dt
    # Inside today in local time, whatever the hour: 25 minutes out when
    # that is still today, else 10 minutes back (after 10 PM the old
    # "+2 hours" landed on tomorrow and --today dropped the meeting).
    now = _dt.datetime.now().astimezone()
    start_dt = now + _dt.timedelta(minutes=25)
    if start_dt.date() != now.date():
        start_dt = now - _dt.timedelta(minutes=10)
    start = start_dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    start = start[:-2] + ":" + start[-2:]
    replay = S / "p16-calendar.json"
    replay.write_text(json.dumps({"calendar": [{"source": "google", "subject": "Intro call with Example Co",
                                                "start": start, "end": start, "attendees": [
                                                    {"name": "Pat Example", "email": "pat@example-co.test"}]}]}))
    r, ok = run("meeting_prep.py --today --input <replayed calendar> (connector configured)",
                ["app/meeting_prep.py", "--today", "--input", str(replay)])
    try:
        doc = json.loads(r.stdout[r.stdout.index("{"):])
        # The rows live per attendee: meetings[*].attendees[*].connectors[*].
        rows = [c for m in doc.get("meetings", []) for a in m.get("attendees", []) for c in (a.get("connectors") or [])]
        conn_ok = bool(rows) and all("needs Claude" in (c.get("reason") or "") and c.get("status") == "absent"
                                     for c in rows) and "Traceback" not in r.stderr
        print("   connector rows:", len(rows), "each absent with the sentence:", conn_ok,
              "| configured:", [c.get("name") for c in doc.get("connectors") or []])
    except (ValueError, AttributeError):
        conn_ok = False; print("   no JSON from meeting_prep; stderr tail:", (r.stderr or "")[-200:])
    ok_all &= conn_ok
    print("VERDICT:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
