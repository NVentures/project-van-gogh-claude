#!/usr/bin/env python3
"""calendar_stub_check.py: stub source pages for meetings that went unrecorded.

Runs nightly. Fetches yesterday's calendar events across all three accounts
(Google primary + secondary + Microsoft Outlook), then checks the vault's
sources/ directory for a matching source page (fuzzy title match within +/-1
day). For any real meeting with no source page, writes a stub:

    sources/{YYYY-MM-DD} {title} (stub).md   (status: unrecorded)

This closes the weekly-retro "Meetings Not Filed" blind spot: phone calls,
in-person meetings, and calls where the recorder failed never leave a trace
otherwise.

Looking at *yesterday* (not today) is deliberate: it leaves a same-day buffer
for meeting ingests to land before we'd flag a meeting as unrecorded.

    python app/calendar_stub_check.py            # write stubs
    python app/calendar_stub_check.py --dry-run  # report only, no writes
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from config_loader import (
    account_emails_lower,
    google_accounts,
    microsoft_accounts,
    sources_dir,
    user_first_name,
    user_tz,
)
from google_client import google_client
from microsoft_client import microsoft_client
from data_sources import is_tier1, load_input, missing_input_error
from vault_gardener import STUB_NOTES_PLACEHOLDER

# "Yesterday" / target-day math computes against the user's configured zone.
USER_TZ = user_tz()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

# Self-emails used to drop the user from attendee lists (lowercased).
USER_EMAILS = account_emails_lower()

SOURCES_DIR = str(sources_dir())

# Generic constants (kept as code constants — not user-specific).
TITLE_MATCH_JACCARD = 0.5     # fuzzy title-match threshold
DATE_WINDOW_DAYS = 1          # +/- day buffer when matching a recorded page
MAX_GOOGLE_RESULTS = 50       # Google Calendar API maxResults cap
MAX_OUTLOOK_RESULTS = 200     # Microsoft Graph API $top cap
FILENAME_MAX = 180            # filesystem-safe filename truncation
AGENDA_MAX = 800              # invite agenda truncation

# Title tokens that carry no matching signal; drop before comparing titles.
# The user's first name is added at runtime so the stopword set stays generic.
_TITLE_STOPWORDS = {
    "the", "and", "for", "with", "from", "call", "meeting", "sync", "catch",
    "catchup", "check", "checkin", "intro", "discussion", "recap", "review",
    "chat", "touch", "base", "touchbase", "1on1", "weekly", "biweekly", "daily",
    "monthly", "quick", "re", "fw", "fwd", "x", "vs",
}
_TITLE_STOPWORDS.add(user_first_name().lower())


def parse_iso(s):
    s = s.split(".")[0].rstrip("Z")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def safe_filename(title):
    """Filesystem-safe, collapse whitespace."""
    name = re.sub(r'[<>:"/\\|?*]', "", title)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:FILENAME_MAX]


# ── Title matching ────────────────────────────────────────────────────────────

def title_tokens(title):
    toks = {t for t in re.split(r"\W+", (title or "").lower()) if len(t) > 2}
    return {t for t in toks if t not in _TITLE_STOPWORDS}


def titles_match(a_tokens, b_tokens):
    """Fuzzy title match: Jaccard >= threshold, or one side subsets the other."""
    if not a_tokens or not b_tokens:
        return False
    inter = a_tokens & b_tokens
    if not inter:
        return False
    smaller = min(len(a_tokens), len(b_tokens))
    if len(inter) == smaller:  # subset match (handles short vs verbose titles)
        return True
    jaccard = len(inter) / len(a_tokens | b_tokens)
    return jaccard >= TITLE_MATCH_JACCARD


def load_recorded():
    """All existing source pages → list of (token_set, date). Stubs count too,
    so a meeting only ever gets stubbed once (idempotent across nightly runs)."""
    recorded = []
    if not os.path.isdir(SOURCES_DIR):
        return recorded
    for fname in os.listdir(SOURCES_DIR):
        if not fname.endswith(".md"):
            continue
        stem = fname[:-3]
        stem_clean = stem.replace("(stub)", "").strip()
        m = re.search(r"(\d{4}-\d{2}-\d{2})", stem_clean)
        date = None
        title_part = stem_clean
        if m:
            date = m.group(1)
            title_part = (stem_clean[:m.start()] + stem_clean[m.end():]).strip(" -")
        else:
            # Fall back to created: in frontmatter
            try:
                head = open(os.path.join(SOURCES_DIR, fname), encoding="utf-8").read(400)
                fm = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", head, re.MULTILINE)
                if fm:
                    date = fm.group(1)
            except Exception:
                pass
        recorded.append((title_tokens(title_part), date))
    return recorded


def is_recorded(event_title, event_date, recorded):
    ev_tokens = title_tokens(event_title)
    ev_d = datetime.strptime(event_date, "%Y-%m-%d").date()
    for r_tokens, r_date in recorded:
        if not r_date:
            continue
        try:
            if abs((datetime.strptime(r_date, "%Y-%m-%d").date() - ev_d).days) > DATE_WINDOW_DAYS:
                continue
        except ValueError:
            continue
        if titles_match(ev_tokens, r_tokens):
            return True
    return False


# ── Calendar fetch ────────────────────────────────────────────────────────────

def _window_utc(day_pt):
    start = day_pt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day_pt.replace(hour=23, minute=59, second=59, microsecond=0)
    return (start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _google_events(source_label, t_min, t_max):
    data = google_client(source_label).calendar.events().list(
        calendarId="primary",
        timeMin=t_min,
        timeMax=t_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=MAX_GOOGLE_RESULTS,
    ).execute()
    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        if not start.get("dateTime"):
            continue  # all-day → not a meeting we'd stub
        if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
            continue
        attendees, declined = [], False
        for a in item.get("attendees", []):
            if a.get("resource"):
                continue
            if a.get("self"):
                if a.get("responseStatus") == "declined":
                    declined = True
                continue
            attendees.append(a.get("displayName") or (a.get("email") or "").split("@")[0])
        if declined or not attendees:
            continue
        events.append({
            "title": item.get("summary", "(No title)"),
            "start_utc": parse_iso(start["dateTime"]),
            "attendees": attendees,
            "agenda": (item.get("description") or "").strip(),
            "source": source_label,
        })
    return events


def fetch_outlook_events(label, t_min, t_max):
    data = microsoft_client(label).calendar_view(t_min, t_max, top=MAX_OUTLOOK_RESULTS)
    events = []
    for item in data.get("value", []):
        if item.get("isAllDay") or item.get("isCancelled"):
            continue
        if (item.get("responseStatus") or {}).get("response") == "declined":
            continue
        start_raw = (item.get("start") or {}).get("dateTime", "")
        if not start_raw:
            continue
        attendees = []
        for a in item.get("attendees", []):
            ea = (a.get("emailAddress") or {})
            addr = (ea.get("address") or "").lower()
            if addr in USER_EMAILS:
                continue
            attendees.append(ea.get("name") or addr.split("@")[0])
        if not attendees:
            continue
        events.append({
            "title": item.get("subject", "(No title)"),
            "start_utc": parse_iso(start_raw),
            "attendees": attendees,
            "agenda": (item.get("bodyPreview") or "").strip(),
            "source": label,
        })
    return events


def events_from_input(data):
    """Build internal event dicts from Tier 2 --input calendar data.

    Mirrors the shape produced by _google_events / fetch_outlook_events. The
    connector fetch is expected to have already dropped all-day, cancelled, and
    declined events and excluded the user from attendees, same as the Tier 1
    fetchers do. `start_utc` is unused downstream, so it is left out.
    """
    events = []
    for item in data.get("calendar", []):
        events.append({
            "title": item.get("title", "(No title)"),
            "attendees": [a for a in item.get("attendees", []) if a],
            "agenda": (item.get("agenda") or "").strip(),
            "source": item.get("source", ""),
        })
    return events


def fetch_all(day_pt):
    t_min, t_max = _window_utc(day_pt)
    events, errors = [], []
    fetchers = [
        (a["label"], (lambda lbl=a["label"]: _google_events(lbl, t_min, t_max)))
        for a in GOOGLE_ACCOUNTS
    ]
    fetchers += [
        (a["label"], (lambda lbl=a["label"]: fetch_outlook_events(lbl, t_min, t_max)))
        for a in MICROSOFT_ACCOUNTS
    ]
    for label, fn in fetchers:
        try:
            events.extend(fn())
        except Exception as e:
            errors.append(f"{label}: {e}")
    return events, errors


# ── Stub writing ──────────────────────────────────────────────────────────────

def build_stub(event, event_date, today):
    attendees = event["attendees"]
    attendees_yaml = "[" + ", ".join(f'"{a}"' for a in attendees) + "]"
    agenda = event["agenda"]
    if len(agenda) > AGENDA_MAX:
        agenda = agenda[:AGENDA_MAX].rstrip() + "…"
    parts = [
        "---",
        "type: source",
        f"title: {event['title']}",
        f"created: {today}",
        f"updated: {today}",
        "sources: []",
        f"attendees: {attendees_yaml}",
        "tags: [stub, unrecorded]",
        "status: unrecorded",
        f"event_date: {event_date}",
        f"event_source: {event['source']}",
        "---",
        "",
        f"# {event['title']}",
        f"- **Date:** {event_date}",
        f"- **Source:** {event['source']} calendar (no recording found)",
        f"- **Attendees:** {' · '.join(attendees)}",
        "",
        "## Invite agenda",
        "",
        agenda if agenda else "%% No agenda in the calendar invite. %%",
        "",
        "## Notes",
        "",
        STUB_NOTES_PLACEHOLDER,
        "",
    ]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report what would be stubbed; write nothing.")
    ap.add_argument("--day", help="Override target day (YYYY-MM-DD). Default: yesterday in the user's timezone.")
    ap.add_argument("--input", metavar="PATH",
                    help="Tier 2: pre-fetched connector calendar JSON (skips calendar API calls)")
    args = ap.parse_args()

    if not args.input and not is_tier1():
        missing_input_error("calendar_stub_check.py")

    now_local = datetime.now(USER_TZ)
    if args.day:
        day_local = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=USER_TZ)
    else:
        day_local = now_local - timedelta(days=1)
    event_date = day_local.strftime("%Y-%m-%d")
    today = now_local.strftime("%Y-%m-%d")

    if args.input:
        events, errors = events_from_input(load_input(args.input)), []
    else:
        events, errors = fetch_all(day_local)
    recorded = load_recorded()

    created, skipped = [], 0
    seen_filenames = set()
    for ev in events:
        if is_recorded(ev["title"], event_date, recorded):
            skipped += 1
            continue
        fname = safe_filename(f"{event_date} {ev['title']} (stub)") + ".md"
        if fname in seen_filenames:
            continue  # same meeting on two accounts → one stub
        seen_filenames.add(fname)
        path = os.path.join(SOURCES_DIR, fname)
        if os.path.exists(path):
            skipped += 1
            continue
        if not args.dry_run:
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_stub(ev, event_date, today))
        created.append(fname)

    tag = "[dry-run] would create" if args.dry_run else "created"
    print(f"calendar_stub_check {event_date}: {len(events)} event(s) scanned, "
          f"{skipped} already recorded, {tag} {len(created)} stub(s).")
    for fname in created:
        print(f"  + {fname}")
    for err in errors:
        print(f"  ! {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
