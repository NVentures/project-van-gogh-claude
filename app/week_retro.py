#!/usr/bin/env python3
"""
week_retro.py — data collection for /week-retro skill.
Outputs JSON to stdout.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import collectors
import extensions
import kpi_events
from platform_compat import NO_WINDOW
from config_loader import (
    vault as _vault,
    sources_dir,
    weekly_dir,
    project_pages,
    business_for_text,
    hotcache_path,
    user_name_regex,
    google_accounts,
    microsoft_accounts,
    resolved_meta,
    user_tz,
)
from google_client import google_client
from microsoft_client import microsoft_client
from data_sources import is_tier1, load_input, missing_input_error

# "Today" for week bounds / days-open math follows the user's configured zone.
USER_TZ = user_tz()


def _today_local() -> date:
    return datetime.now(timezone.utc).astimezone(USER_TZ).date()


OBSIDIAN = _vault()
WIKI = OBSIDIAN / "wiki"
SOURCES_DIR = sources_dir()
WEEKLY_DIR = weekly_dir()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

# Project pages come from config.json businesses[].
PROJECT_PAGES = project_pages()
_USER_NAME_RE = user_name_regex()

HOTCACHE_PATH = hotcache_path()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # repo root: cwd for week_review.py subprocess

# Anti-drift coverage contract. week-retro gets the mail + deal half via the
# week_review shell-out, and reads calendar / meetings / hotcache / obsidian here.
COLLECTORS_USED = {
    collectors.CALENDAR, collectors.MEETINGS, collectors.HOTCACHE_READ,
    collectors.OBSIDIAN_TASKS, collectors.SENT_MAIL, collectors.INBOUND_MAIL,
    collectors.KILL_SCAN, collectors.DEAL_MATCH,
}


def week_bounds(ref: date = None):
    if ref is None:
        ref = _today_local()
    monday = ref - timedelta(days=ref.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def fetch_calendar(monday: date, friday: date) -> tuple:
    """Aggregate calendar events across all configured accounts:
    Microsoft (Outlook) + Google primary + Google secondary (if configured)."""
    fri_end = friday + timedelta(days=1)
    events: list = []
    errors: list = []

    def _parse_event(subject, start_raw, end_raw, organizer):
        try:
            if "T" in start_raw:
                start_dt = datetime.fromisoformat(start_raw[:19])
                end_dt = datetime.fromisoformat(end_raw[:19]) if end_raw and "T" in end_raw else start_dt
                duration_min = int((end_dt - start_dt).total_seconds() / 60)
                event_date = start_dt.date().isoformat()
            else:
                duration_min = 0
                event_date = start_raw[:10]
        except Exception:
            duration_min = 0
            event_date = start_raw[:10] if start_raw else ""
        return {"subject": subject, "date": event_date, "duration_min": duration_min, "organizer": organizer}

    t_min = f"{monday.isoformat()}T00:00:00Z"
    t_max = f"{fri_end.isoformat()}T00:00:00Z"

    # Outlook (Microsoft Graph) — every configured Microsoft account
    for a in MICROSOFT_ACCOUNTS:
        try:
            data = microsoft_client(a["label"]).calendar_view(t_min, t_max, top=50)
            for item in data.get("value", []):
                start_raw = item.get("start", {}).get("dateTime", "")
                if not start_raw:
                    continue
                events.append(_parse_event(
                    item.get("subject", "(no subject)"),
                    start_raw,
                    item.get("end", {}).get("dateTime", ""),
                    item.get("organizer", {}).get("emailAddress", {}).get("name", ""),
                ))
        except Exception as e:
            errors.append(f"{a['label']} calendar exception: {e}")

    # Google Calendar — every configured Google account
    for a in GOOGLE_ACCOUNTS:
        gcal_label = a["label"]
        try:
            data = google_client(gcal_label).calendar.events().list(
                calendarId="primary",
                timeMin=t_min,
                timeMax=t_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            for item in data.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                start = item.get("start", {})
                start_raw = start.get("dateTime") or start.get("date", "")
                end = item.get("end", {})
                end_raw = end.get("dateTime") or end.get("date", "")
                if not start_raw:
                    continue
                events.append(_parse_event(
                    item.get("summary", "(no subject)"),
                    start_raw,
                    end_raw,
                    item.get("organizer", {}).get("email", ""),
                ))
        except Exception as e:
            errors.append(f"{gcal_label} calendar exception: {e}")

    # Dedup same event across calendars (exact title + date)
    seen: set = set()
    unique = []
    for ev in events:
        key = (ev["subject"].lower().strip(), ev["date"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    return unique, errors


def parse_action_items(text: str) -> tuple:
    in_section = False
    open_items = []
    done_items = []

    for line in text.splitlines():
        if re.match(r"^##\s+Action Items", line):
            in_section = True
            continue
        if in_section and re.match(r"^##", line):
            break
        if in_section:
            if re.match(r"^\s*-\s+\[ \]", line):
                open_items.append(re.sub(r"^\s*-\s+\[ \]\s*", "", line).strip())
            elif re.match(r"^\s*-\s+\[x\]", line, re.IGNORECASE):
                done_items.append(re.sub(r"^\s*-\s+\[x\]\s*", "", line, flags=re.IGNORECASE).strip())

    return open_items, done_items


def parse_frontmatter_date(text: str) -> str | None:
    match = re.search(r"^created:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    val = match.group(1).strip().strip("'\"")
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val[:len(fmt)], fmt).date().isoformat()
        except ValueError:
            continue
    return val[:10] if len(val) >= 10 else None


def guess_business(title: str) -> str:
    return business_for_text(title) or "Unknown"


def fetch_meeting_sources(monday: date, friday: date) -> tuple:
    errors = []
    sources = []
    all_open = []

    if not SOURCES_DIR.exists():
        return [], [], [f"sources dir not found: {SOURCES_DIR}"]

    date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")

    for f in sorted(SOURCES_DIR.glob("*.md")):
        m = date_pattern.search(f.name)
        if not m:
            continue
        try:
            file_date = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if not (monday <= file_date <= friday):
            continue

        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"Error reading {f.name}: {e}")
            continue

        open_items, done_items = parse_action_items(text)
        user_owned = [i for i in open_items if _USER_NAME_RE.search(i)]

        title = re.sub(r"\s*-?\s*\d{4}-\d{2}-\d{2}", "", f.stem).strip(" -")
        business = guess_business(title)

        created_date = parse_frontmatter_date(text) or file_date.isoformat()
        try:
            days_open = (_today_local() - date.fromisoformat(created_date)).days
        except Exception:
            days_open = 0

        sources.append({
            "title": title,
            "date": file_date.isoformat(),
            "filename": f.name,
            "business": business,
            "action_items_open": open_items,
            "action_items_done": done_items,
            "user_owned_open": user_owned,
        })

        for item in open_items:
            all_open.append({
                "text": item,
                "source": title,
                "days_open": days_open,
                "overdue": days_open > 7,
                "user_owned": bool(_USER_NAME_RE.search(item)),
            })

    return sources, all_open, errors


def calendar_meeting_gaps(events: list, sources: list) -> list:
    def tokens(s: str) -> set:
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    source_token_sets = [tokens(s["title"]) for s in sources]

    gaps = []
    for ev in events:
        ev_tokens = tokens(ev["subject"])
        if not ev_tokens:
            continue
        matched = False
        for st in source_token_sets:
            if not st:
                continue
            overlap = len(ev_tokens & st) / max(len(ev_tokens), len(st))
            if overlap >= 0.4:
                matched = True
                break
        if not matched:
            gaps.append(ev)

    return gaps


def fetch_obsidian_log(monday: date) -> tuple:
    log_path = WIKI / "log.md"
    if not log_path.exists():
        return [], [f"log.md not found at {log_path}"]

    try:
        text = log_path.read_text(encoding="utf-8")
    except Exception as e:
        return [], [f"Error reading log.md: {e}"]

    date_re = re.compile(r"^\*{0,2}(\d{4}-\d{2}-\d{2})")
    entries = []
    current_lines = []
    current_date = None

    for line in text.splitlines():
        m = date_re.match(line.strip())
        if m:
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                d = None
            if d and d >= monday:
                if current_lines:
                    entries.append("\n".join(current_lines).strip())
                current_lines = [line.strip()]
                current_date = d
                continue
            else:
                if current_lines:
                    entries.append("\n".join(current_lines).strip())
                current_lines = []
                current_date = None
        elif current_date and line.strip():
            current_lines.append(line.strip())

    if current_lines:
        entries.append("\n".join(current_lines).strip())

    return entries, []


def fetch_devlog_entries() -> tuple:
    results = {}
    errors = []

    for name, path in PROJECT_PAGES.items():
        if not path.exists():
            errors.append(f"{name}: page not found at {path}")
            results[name] = None
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"{name}: read error: {e}")
            results[name] = None
            continue

        in_section = False
        bullets = []
        for line in text.splitlines():
            if re.match(r"^##\s+Dev Log", line):
                in_section = True
                continue
            if in_section and re.match(r"^##", line):
                break
            if in_section and line.strip().startswith("-"):
                bullets.append(line.strip())

        results[name] = bullets[-1] if bullets else None

    return results, errors


def fetch_prior_retro_commitments() -> tuple:
    if not WEEKLY_DIR.exists():
        return "", []

    retro_files = sorted(WEEKLY_DIR.glob("retro-*.md"), reverse=True)
    if not retro_files:
        return "", []

    try:
        text = retro_files[0].read_text(encoding="utf-8")
    except Exception as e:
        return "", [f"Error reading {retro_files[0].name}: {e}"]

    in_section = False
    lines = []
    for line in text.splitlines():
        if re.search(r"3 Commitments|Next Week", line, re.IGNORECASE):
            in_section = True
            lines.append(line)
            continue
        if in_section and re.match(r"^##", line):
            break
        if in_section:
            lines.append(line)

    return "\n".join(lines).strip(), []


def fetch_week_deals(monday: date) -> tuple:
    """Shell out to week_review.py for full deal coverage (waiting / inbox / cold
    plus status_change_alerts plus met_since). --no-classify skips the metered
    Haiku call: a history consumer does not need per-thread summaries. Fail-open:
    returns ({}, [error]) on any failure rather than aborting the retro."""
    days = max(1, (date.today() - monday).days + 1)
    # --no-extensions: data subprocess; the retro attaches its own sections,
    # so an extension must not also run under the "week" briefing name here.
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "week_review.py"),
           "--since", str(days), "--no-classify", "--no-sidecar",
           "--no-extensions"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=300, cwd=PROJECT_ROOT,
                                creationflags=NO_WINDOW)
        if result.returncode != 0:
            return {}, [f"week_review shell-out failed: {result.stderr[:300]}"]
        return json.loads(result.stdout), []
    except Exception as e:
        return {}, [f"week_review shell-out: {e}"]


def summarize_week_deals(wk: dict) -> dict:
    """Condense the week_review deal pipeline into a retro-shaped summary: deals
    that died or are at risk (kill/pass alerts), deals likely handled in a meeting
    (met_since), and the still-open counts."""
    waiting = wk.get("waiting_on_user", [])
    inbox = wk.get("inbox_pending", [])
    cold = wk.get("cold_urgent", []) + wk.get("cold_monitor", [])
    met = [e for sec in (waiting, inbox, cold) for e in sec if e.get("met_since")]
    return {
        "died_or_at_risk": wk.get("status_change_alerts", []),
        "likely_handled_met": [
            {"name": e.get("counterparty_name"), "subject": e.get("subject"),
             "met": e.get("met_since")} for e in met
        ],
        "waiting_on_user_count": len(waiting),
        "inbox_pending_count": len(inbox),
        "cold_count": len(cold),
    }


def fetch_hotcache_deal_state() -> tuple:
    """Each active-threads deal's stage + last_contact, read via the shared
    collectors.hotcache_read. Fail-open."""
    try:
        deals = collectors.hotcache_read(HOTCACHE_PATH)
        state = [
            {"deal": d["heading"], "stage": d["fields"].get("stage"),
             "last_contact": d["fields"].get("last_contact")}
            for d in deals
        ]
        return state, []
    except Exception as e:
        return [], [f"hotcache deal state: {e}"]


def _page_note() -> str:
    """One line when this briefing's web page did not update. Never raises."""
    try:
        import briefing_html
        return briefing_html.page_line("week-retro")
    except Exception:                                           # noqa: BLE001
        return ""


def retro_front_page(all_open_items: list, nudges: list, errors: list) -> dict:
    """The retro's front page: what is still open at the end of the week.

    The retro has no mail buckets, so its items are the open action items the
    week left behind plus any overdue deliverable. They are shaped into the
    same bucket structure the other three briefings use so they go through the
    same selection and the same renderer: a second front-page renderer is how
    two halves of one product start disagreeing.
    """
    import week_review as wr
    import config_loader as _cl

    by_tag = {}
    for item in all_open_items:
        entry = {
            "subject": item.get("text") or "",
            "counterparty_name": "",
            "counterparty_email": "",
            "age_days": int(item.get("days_open") or 0),
            "body_preview": item.get("source") or "",
            "section": "waiting_on_user",
        }
        if item.get("overdue"):
            entry["days_late"] = max(1, int(item.get("days_open") or 0) - 7)
        entry["bucket"] = wr.assign_bucket(entry)
        entry["function"] = wr.assign_function(entry)
        by_tag.setdefault(entry["bucket"], []).append(entry)

    for nudge in nudges or []:
        entry = {
            "subject": nudge.get("text") or nudge.get("title") or "",
            "counterparty_name": nudge.get("counterparty") or "",
            "counterparty_email": "",
            "due": nudge.get("date") or nudge.get("due") or "",
            "age_days": 0,
            "body_preview": "",
            "section": "waiting_on_user",
        }
        entry["bucket"] = wr.assign_bucket(entry)
        entry["function"] = wr.assign_function(entry)
        by_tag.setdefault(entry["bucket"], []).append(entry)

    buckets = []
    for b in _cl.businesses():
        tag = b.get("tag")
        buckets.append({"tag": tag, "display_name": b.get("display_name", tag),
                        "priorities": _cl.business_priorities(tag),
                        "items": by_tag.get(tag, [])})
    leftovers = by_tag.get("unassigned", [])
    if leftovers:
        buckets.append({"tag": "unassigned", "display_name": "Couldn't place these",
                        "priorities": [], "items": leftovers})

    out = wr.empty_front_page()
    try:
        out = wr.build_front_page(buckets, "week", heading="STILL OPEN")
    except Exception as e:                                      # noqa: BLE001
        errors.append(f"Front page: {e}")
    out["buckets"] = buckets
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON (skips calendar API calls)")
    args = parser.parse_args()

    if not args.input and not is_tier1():
        missing_input_error("week_retro.py")

    errors = []
    monday, friday = week_bounds()

    if args.input:
        data = load_input(args.input)
        raw = data.get("calendar_week", [])
        calendar_events = [
            {"subject": e.get("subject", ""), "date": e.get("date", ""),
             "duration_min": e.get("duration_min", 0), "organizer": e.get("organizer", "")}
            for e in raw
        ]
    else:
        calendar_events, cal_errors = fetch_calendar(monday, friday)
        errors.extend(cal_errors)

    meeting_sources, all_open_items, src_errors = fetch_meeting_sources(monday, friday)
    errors.extend(src_errors)

    gaps = calendar_meeting_gaps(calendar_events, meeting_sources)

    log_entries, log_errors = fetch_obsidian_log(monday)
    errors.extend(log_errors)

    devlog_entries, dev_errors = fetch_devlog_entries()
    errors.extend(dev_errors)

    prior_commitments, retro_errors = fetch_prior_retro_commitments()
    errors.extend(retro_errors)

    # Full deal coverage via the week_review hub. Only under Tier 1 (direct OAuth)
    # can week_review fetch; in Tier 2 the deal half is unavailable through this
    # shell-out, so emit an empty summary rather than erroring.
    if is_tier1():
        wk_deals_raw, wk_errors = fetch_week_deals(monday)
        errors.extend(wk_errors)
    else:
        wk_deals_raw = {}
    deals_this_week = summarize_week_deals(wk_deals_raw)

    hotcache_deal_state, hc_errors = fetch_hotcache_deal_state()
    errors.extend(hc_errors)

    # API-sourced meeting list for the Mon-Fri window, kept SEPARATE from
    # calendar_meeting_gaps (which uses the LOCAL sources/*.md so its gap
    # semantics stay unchanged).
    meetings_api = collectors.meetings(max(0, (date.today() - monday).days), errors)
    # The retro renders overdue deliverables, so it emits them.
    try:
        from follow_up_radar import collect_nudges
        nudges, nudge_err = collect_nudges(_today_local())
        if nudge_err:
            errors.append(nudge_err)
    except Exception as e:                                      # noqa: BLE001
        nudges = []
        errors.append(f"Nudges: {e}")


    output = {
        "meta": resolved_meta(),
        "week_start": monday.isoformat(),
        "week_end": friday.isoformat(),
        "calendar_events": calendar_events,
        "meeting_sources_this_week": meeting_sources,
        "calendar_meeting_gaps": gaps,
        "obsidian_log_entries": log_entries,
        "devlog_last_entries": devlog_entries,
        "all_open_action_items": all_open_items,
        "prior_retro_commitments": prior_commitments,
        "deals_this_week": deals_this_week,
        "hotcache_deal_state": hotcache_deal_state,
        "status_change_alerts": wk_deals_raw.get("status_change_alerts", []),
        "meetings_api": meetings_api,
        "nudges": nudges,
        **retro_front_page(all_open_items, nudges, errors),
        "page_note": _page_note(),
        "errors": errors,
    }

    # User extension sections (fail open; see app/extensions.py).
    extensions.attach(output, "week-retro")

    # This output's meta carries the pending release notes; the render that
    # follows is their one showing (see plugin_update.mark_notes_shown).
    if (output.get("meta") or {}).get("whats_new"):
        import plugin_update
        plugin_update.mark_notes_shown()

    # One content-free row for the weekly scorecard (app/kpi_events.py).
    kpi_events.record_snapshot("week-retro", output)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
