#!/usr/bin/env python3
"""
meeting_prep.py — Pre-call briefing data pull.

Finds a target calendar event, resolves attendees to entity pages + Granola
history, surfaces relevant hotcache and week-file context.

All user-specific values come from config.json via config_loader.

Usage:
    python app/meeting_prep.py [--meeting "next|<title fragment>|HH:MM"]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config_loader import (
    account_emails_lower,
    business_tags,
    google_accounts,
    internal_team_emails,
    microsoft_accounts,
    resolved_meta,
    user_secondary_tz,
    user_tz,
    vault,
    weekly_dir,
    workspace_week_md_path,
)
from google_client import google_client
from microsoft_client import microsoft_client
from data_sources import is_tier1, load_input, missing_input_error
from context_pack import (
    extract_action_items,
    extract_hotcache_snippets,
    find_entity_page,
    find_meeting_sources,
)
from platform_compat import fmt_local_time

# Date-relative windows and rendered times compute against the user's zone;
# the secondary zone is None unless a dual display (e.g. PT / ET) is opted into.
USER_TZ = user_tz()
USER_SECONDARY_TZ = user_secondary_tz()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

# The user's own email addresses (excluded from attendee lists).
USER_EMAILS = account_emails_lower()

# Internal teammates to drop from attendee lists, plus the user themselves.
INTERNAL_TEAM_EMAILS = {e.lower() for e in internal_team_emails()} | USER_EMAILS

# Vault paths (all config-driven).
WEEKLY_DIR = weekly_dir()
WORKSPACE_WEEK_MD = workspace_week_md_path()

# week-file tag markers: business tags plus a generic marker set.
_WEEK_TAGS = sorted(
    {t.upper() for t in business_tags()} | {"DEAL", "ACTION", "PERSONAL"}
)
WEEK_TAG_RE = re.compile(r"\[(" + "|".join(_WEEK_TAGS) + r")\]", re.IGNORECASE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_iso(s):
    s = s.split(".")[0].rstrip("Z")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_local(dt_utc):
    return fmt_local_time(dt_utc, USER_TZ, USER_SECONDARY_TZ)


# ── Calendar fetching ─────────────────────────────────────────────────────────

def _time_window(days_ahead):
    now = datetime.now(timezone.utc)
    return (
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59Z"),
    )


def fetch_outlook_events(label, days_ahead=7):
    time_min, time_max = _time_window(days_ahead)
    try:
        data = microsoft_client(label).calendar_view(time_min, time_max, top=50)
        events = []
        for item in data.get("value", []):
            start_raw = item.get("start", {}).get("dateTime", "")
            if not start_raw:
                continue
            try:
                dt_utc = parse_iso(start_raw)
            except Exception:
                continue
            attendees = [
                {"name": a["emailAddress"]["name"], "email": a["emailAddress"]["address"].lower()}
                for a in item.get("attendees", [])
                if a.get("emailAddress", {}).get("address", "").lower() not in INTERNAL_TEAM_EMAILS
            ]
            events.append({
                "source": label,
                "title": item.get("subject", "(No title)"),
                "dt_utc": dt_utc,
                "time_str": fmt_local(dt_utc),
                "day_str": (lambda d: d.strftime("%a %b") + f" {d.day}")(dt_utc.astimezone(USER_TZ)),
                "attendees": attendees,
            })
        return events
    except Exception as e:
        print(f"[warn] {label} fetch failed: {e}", flush=True, file=sys.stderr)
        return []


def _fetch_google_events(days_ahead, source_label):
    time_min, time_max = _time_window(days_ahead)
    try:
        data = google_client(source_label).calendar.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        events = []
        for item in data.get("items", []):
            start = item.get("start", {})
            start_raw = start.get("dateTime") or start.get("date")
            if not start_raw or "T" not in start_raw:
                continue
            try:
                dt_utc = parse_iso(start_raw)
            except Exception:
                continue
            attendees = [
                {
                    "name": a.get("displayName") or a.get("email", ""),
                    "email": a.get("email", "").lower(),
                }
                for a in item.get("attendees", [])
                if not a.get("self", False)
                and a.get("email", "").lower() not in INTERNAL_TEAM_EMAILS
            ]
            events.append({
                "source": source_label,
                "title": item.get("summary", "(No title)"),
                "dt_utc": dt_utc,
                "time_str": fmt_local(dt_utc),
                "day_str": (lambda d: d.strftime("%a %b") + f" {d.day}")(dt_utc.astimezone(USER_TZ)),
                "attendees": attendees,
            })
        return events
    except Exception as e:
        print(f"[warn] {source_label} fetch failed: {e}", flush=True, file=sys.stderr)
        return []


def fetch_all_events(days_ahead=7):
    """Fetch upcoming events across every configured account."""
    events = []
    for a in MICROSOFT_ACCOUNTS:
        events += fetch_outlook_events(a["label"], days_ahead)
    for a in GOOGLE_ACCOUNTS:
        events += _fetch_google_events(days_ahead, a["label"])
    return events


def events_from_input(data):
    """Build internal event dicts from Tier 2 --input calendar data.

    Mirrors the shape produced by fetch_outlook_events / fetch_google_events so
    the rest of the pipeline (merge/select/resolve) is tier-agnostic. Internal
    team members are filtered from attendees here, matching the Tier 1 fetches.
    """
    events = []
    for item in data.get("calendar", []):
        start_raw = item.get("start") or item.get("_sort", "")
        if not start_raw:
            continue
        try:
            dt_utc = parse_iso(start_raw)
        except Exception:
            continue
        attendees = [
            {"name": a.get("name") or a.get("email", ""), "email": a.get("email", "").lower()}
            for a in item.get("attendees", [])
            if a.get("email", "").lower() not in INTERNAL_TEAM_EMAILS
        ]
        events.append({
            "source": item.get("source", ""),
            "title": item.get("title", "(No title)"),
            "dt_utc": dt_utc,
            "time_str": fmt_local(dt_utc),
            "day_str": (lambda d: d.strftime("%a %b") + f" {d.day}")(dt_utc.astimezone(USER_TZ)),
            "attendees": attendees,
        })
    return events


def merge_events(all_events):
    """Deduplicate by title+time (within 5 min), keep attendee list with most entries."""
    merged = []
    for evt in sorted(all_events, key=lambda e: e["dt_utc"]):
        placed = False
        for seen in merged:
            same_title = evt["title"].lower().strip() == seen["title"].lower().strip()
            close_time = abs((evt["dt_utc"] - seen["dt_utc"]).total_seconds()) < 300
            if same_title and close_time:
                if len(evt["attendees"]) > len(seen["attendees"]):
                    seen["attendees"] = evt["attendees"]
                placed = True
                break
        if not placed:
            merged.append(evt)
    return merged


# ── Event selection ───────────────────────────────────────────────────────────

def find_target_event(events, meeting_arg):
    now_utc = datetime.now(timezone.utc)
    future = [e for e in events if e["dt_utc"] > now_utc]

    if meeting_arg == "next":
        with_attendees = [e for e in future if e["attendees"]]
        return with_attendees[0] if with_attendees else (future[0] if future else None)

    time_match = re.match(r"^(\d{1,2}):(\d{2})$", meeting_arg)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        today_local = datetime.now(USER_TZ).date()
        for e in future:
            ep = e["dt_utc"].astimezone(USER_TZ)
            if ep.date() == today_local and ep.hour == h and ep.minute == m:
                return e
        return None

    fragment = meeting_arg.lower()
    for e in future:
        if fragment in e["title"].lower():
            return e
    return None


# ── Entity page lookup ────────────────────────────────────────────────────────

# ── week-file context ─────────────────────────────────────────────────────────

def extract_week_context(names):
    files = sorted(WEEKLY_DIR.glob("week-*.md"), key=lambda f: f.name, reverse=True) if WEEKLY_DIR.exists() else []
    if files:
        week_file = files[0]
    elif WORKSPACE_WEEK_MD.exists():
        week_file = WORKSPACE_WEEK_MD
    else:
        return {"file": None, "lines": []}
    tokens = [t for name in names for t in name.split() if len(t) > 2]
    try:
        lines = week_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {"file": str(week_file), "lines": []}
    # Only surface task bullets and tagged lines — ignore prose mentions
    matched = [
        line.strip()
        for line in lines
        if any(t.lower() in line.lower() for t in tokens)
        and line.strip()
        and (
            re.match(r"^\s*-\s+\[", line)  # task bullet [ ] or [x]
            or WEEK_TAG_RE.search(line)
        )
    ]
    return {"file": str(week_file), "lines": matched}


# ── Shared resolution helper ──────────────────────────────────────────────────

def resolve_event(event):
    """Resolve a single event into a prep-ready dict (attendees, sources, context)."""
    attendees = event["attendees"]
    names = [a["name"] for a in attendees if a["name"]]

    resolved = []
    all_action_items = []
    for a in attendees:
        name = a["name"]
        entity_page = find_entity_page(name)
        sources = find_meeting_sources(name)
        for src in sources:
            for item in extract_action_items(src):
                all_action_items.append({"source": Path(src).name, "item": item})
        resolved.append({
            "name": name,
            "email": a["email"],
            "entity_page": entity_page,
            "meeting_sources": sources,
        })

    return {
        "meeting": {
            "title": event["title"],
            "time": event["time_str"],
            "day": event["day_str"],
            "source": event["source"],
        },
        "attendees": resolved,
        "hotcache_snippets": extract_hotcache_snippets(names),
        "week_context": extract_week_context(names),
        "unresolved_action_items": all_action_items,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def _record_prep(mode, events) -> None:
    """Note which calls were held, and which got a brief, for the scorecard.

    Only the hashed identity of each meeting is recorded, never its title. The
    two modes are the two halves of one ratio: `today` is every outside call on
    the calendar, `brief` is one that a prep was actually written for.
    Fails open, because a prep is worth more than a statistic about it.
    """
    try:
        import kpi_events

        keys = []
        for evt in events or []:
            if not isinstance(evt, dict):
                continue
            key = kpi_events.meeting_key(evt.get("title") or "",
                                         evt.get("day_str") or "")
            if key not in keys:
                keys.append(key)
        if keys:
            kpi_events.record(kpi_events.PREP, mode=mode, keys=keys)
    except Exception:                                           # noqa: BLE001
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", default="next",
                        help="'next', 'HH:MM', or a title fragment")
    parser.add_argument("--today", action="store_true",
                        help="Return prep data for ALL of today's events (morning coffee mode)")
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector calendar JSON (skips calendar API calls)")
    args = parser.parse_args()

    if not args.input and not is_tier1():
        missing_input_error("meeting_prep.py")

    if args.input:
        print("[1/4] Loading calendar from connector data...", flush=True, file=sys.stderr)
        all_events = merge_events(events_from_input(load_input(args.input)))
    else:
        print("[1/4] Fetching calendars...", flush=True, file=sys.stderr)
        all_events = merge_events(fetch_all_events())

    if not all_events:
        print(json.dumps({"error": "No upcoming events found"}))
        return

    # ── --today mode: all events for today ────────────────────────────────────
    if args.today:
        today_local = datetime.now(USER_TZ).date()
        today_events = sorted(
            [e for e in all_events if e["dt_utc"].astimezone(USER_TZ).date() == today_local],
            key=lambda e: e["dt_utc"],
        )
        print(f"[2/4] Found {len(today_events)} event(s) today — resolving...", flush=True, file=sys.stderr)
        meetings = []
        for i, evt in enumerate(today_events, 1):
            print(f"[3/4] Resolving meeting {i}/{len(today_events)}: {evt['title']}", flush=True, file=sys.stderr)
            meetings.append(resolve_event(evt))
        print("[4/4] Done.", flush=True, file=sys.stderr)
        # The day's outside calls: the scorecard's denominator for prep
        # coverage. Titles are hashed on the way in, never stored.
        _record_prep("today", today_events)
        print(json.dumps({"meta": resolved_meta(), "date": str(today_local), "meetings": meetings}, indent=2, default=str))
        return

    # ── single-meeting mode (original behaviour) ───────────────────────────────
    print("[2/4] Finding target meeting...", flush=True, file=sys.stderr)
    event = find_target_event(all_events, args.meeting)
    if not event:
        print(json.dumps({"error": f"No event matching '{args.meeting}'"}))
        return

    print(f"[3/4] Resolving {len(event['attendees'])} attendee(s)...", flush=True, file=sys.stderr)
    resolved_data = resolve_event(event)

    print("[4/4] Pulling hotcache + week context...", flush=True, file=sys.stderr)
    _record_prep("brief", [event])
    output = {"meta": resolved_meta(), **resolved_data}
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
