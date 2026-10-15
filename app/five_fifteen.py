#!/usr/bin/env python3
"""
five_fifteen.py — Weekly client "5:15 report" evidence pack.

A 5:15 report (5 minutes to read, 15 to write) is a once-a-week client-facing
update: a look back at the week (worked on / accomplished / identified / still
outstanding) and a look forward (next week's focus), written to keep the user
and each advisory client aligned.

This script does the deterministic work; the /van-gogh:five-fifteen skill does
the narrative synthesis. Per client it:
  - resolves the week window ending on the report Friday,
  - fetches notetaker meetings for that window (shared notetaker.py fetch) and
    filters them to the client by keyword,
  - locates and reads the PRIOR 5:15 report so the skill can carry forward
    "still outstanding" items,
  - resolves the recipient, the from-account mailbox, the signoff, and the
    output path,
  - passes through the connector signals (email / Slack / Teams / HubSpot) the
    skill gathered in Tier 2 via --input.

It emits JSON to stdout. All user-specific data comes from config.json via
config_loader.

Usage:
    python app/five_fifteen.py --client client-one --input signals.json
    python app/five_fifteen.py --client client-one --week-ending 2026-05-29
"""

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import notetaker
from config_loader import (
    client_by_tag,
    force_utf8_io,
    resolved_client,
    resolved_meta,
    user_tz,
)

PRIOR_REPORT_MAX_CHARS = 8000

# 5:15 reports are archived one-per-Friday as YYYY-MM-DD.md.
_REPORT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


# ── Dates ─────────────────────────────────────────────────────────────────────

def mdy(d) -> str:
    """Cross-platform M/D/YYYY (no platform-specific strftime padding flags)."""
    return f"{d.month}/{d.day}/{d.year}"


def most_recent_friday(today) -> "datetime.date":
    """The Friday on or before `today` (a date). Monday=0 .. Sunday=6, Friday=4."""
    return today - timedelta(days=(today.weekday() - 4) % 7)


def resolve_week(week_ending: str | None):
    """Return week-window dict. Window = the 7 days ending on the report Friday
    (Saturday..Friday), which covers the working week plus the prior weekend."""
    if week_ending:
        end = datetime.strptime(week_ending, "%Y-%m-%d").date()
    else:
        end = most_recent_friday(datetime.now(user_tz()).date())
    start = end - timedelta(days=6)
    iso_year, iso_week, _ = end.isocalendar()
    return {
        "ending": end.isoformat(),
        "start": start.isoformat(),
        "label": f"{iso_year}-W{iso_week:02d}",
        "ending_display": mdy(end),
        "range_display": f"{mdy(start)} to {mdy(end)}",
    }


# ── Meetings ──────────────────────────────────────────────────────────────────

def fetch_client_meetings(start_str: str, end_str: str, keywords: list):
    """Notetaker meetings in [start, end], split into (matched, all_titles, error).

    `matched` are notes whose title/summary/attendees contain a client keyword,
    shaped for the report. `all_titles` lists every note in the window (title +
    date) so the skill can pull more if keyword match missed one.
    """
    if not notetaker.configured():
        return [], [], f"no {notetaker.env_key()} configured"
    try:
        meetings = notetaker.fetch_meetings(end_str, start_str, include_summary=True)
    except RuntimeError as e:
        return [], [], str(e)

    all_titles = [{"title": m["title"], "date": m["date"]} for m in meetings]

    kw_lower = [k.lower() for k in keywords if k]
    if not kw_lower:
        # No keywords -> match NOTHING, never everything. `matched` feeds a
        # client-facing report draft, so a config omission must not sweep in
        # every meeting in the window (including other clients' calls). Surface
        # it as an error the skill reports instead of silently over-matching.
        return [], all_titles, "client has no keywords[] configured — 0 meetings matched"
    matched = []
    for m in meetings:
        summary = m.pop("summary_text", "")
        haystack = (m["title"] + " " + summary + " " + " ".join(m["attendees"])).lower()
        if not any(k in haystack for k in kw_lower):
            continue
        matched.append({**m, "summary": summary[:2000]})

    return matched, all_titles, None


# ── Prior report ──────────────────────────────────────────────────────────────

def find_prior_report(report_dir: Path, current_name: str):
    """Most recent 5:15 report in report_dir dated strictly before current_name.

    Only date-named files (YYYY-MM-DD.md) count — a stray `template.md`/`notes.md`
    must never be mistaken for last week's report. And only reports earlier than
    the current week, so a `--week-ending` in the past never carries forward from
    a report dated after it."""
    if not report_dir or not report_dir.exists():
        return None, ""
    candidates = sorted(
        (p for p in report_dir.glob("*.md")
         if _REPORT_NAME_RE.match(p.name) and p.name < current_name),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        return None, ""
    prior = candidates[0]
    try:
        text = prior.read_text(encoding="utf-8")[:PRIOR_REPORT_MAX_CHARS]
    except Exception:
        text = ""
    return str(prior), text


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    force_utf8_io()
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="client tag (see config.json clients[])")
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector signals JSON (email/slack/teams/hubspot)")
    parser.add_argument("--week-ending", metavar="YYYY-MM-DD",
                        help="report Friday; default = most recent Friday")
    args = parser.parse_args()

    errors = []

    client_cfg = client_by_tag(args.client)
    if not client_cfg:
        known = sorted(c["tag"] for c in resolved_meta()["clients"] if c.get("tag"))
        print(json.dumps({
            "error": f"unknown client tag: {args.client}",
            "known": known,
        }, indent=2))
        raise SystemExit(1)

    client = resolved_client(args.client)
    week = resolve_week(args.week_ending)
    if args.week_ending and datetime.strptime(args.week_ending, "%Y-%m-%d").weekday() != 4:
        errors.append(f"--week-ending {args.week_ending} is not a Friday; the week window may be off")

    # Output path: one file per report Friday inside the client's archive folder.
    report_dir = Path(client["report_dir"]) if client["report_dir"] else None
    output_name = f"{week['ending']}.md"
    output_path = str(report_dir / output_name) if report_dir else ""

    # Prior report for carry-forward.
    prior_path, prior_text = (None, "")
    if report_dir:
        prior_path, prior_text = find_prior_report(report_dir, output_name)

    # Meetings — matched to this client by keyword.
    meetings_matched, meetings_all, gerr = fetch_client_meetings(
        week["start"], week["ending"], client.get("keywords", [])
    )
    if gerr:
        errors.append(f"{notetaker.safe_display_name()}: {gerr}")

    # Connector signals gathered by the skill (Tier 2).
    signals = {}
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as f:
                signals = json.load(f)
        except Exception as e:
            errors.append(f"input signals: {e}")

    output = {
        "meta": resolved_meta(),
        "client": client,
        "week": week,
        "meetings": meetings_matched,
        "meetings_all_titles": meetings_all,
        "prior_report": {
            "path": prior_path,
            "text": prior_text,
            "found": bool(prior_path),
        },
        "output_path": output_path,
        "signals": signals,
        "errors": errors,
    }

    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
