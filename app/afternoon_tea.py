#!/usr/bin/env python3
"""
afternoon_tea.py — End-of-day retrospective data pull.

Pulls today's sent mail across all configured accounts, notetaker meetings,
tomorrow's calendar, and the morning briefing's action items. Auto-detects
completed tasks via fuzzy match against sent mail and meeting attendees.
Outputs JSON for a skill consumer to render as the /afternoon-tea briefing.

All user-specific data comes from config.json via config_loader.

Usage:
    python app/afternoon_tea.py
"""

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import extensions
import kpi_events
import user_state
import workbench_data
from config_loader import (
    account_emails_lower,
    extra_spam_fragments,
    google_accounts,
    hotcache_active_threads_heading,
    hotcache_path,
    internal_domains,
    internal_team_emails,
    microsoft_accounts,
    morning_coffee_md_path,
    resolved_meta,
    travel_buffer_minutes,
    travel_enabled,
    user_secondary_tz,
    user_tz,
)
from google_client import google_client
from microsoft_client import microsoft_client
from data_sources import is_tier1, load_input, missing_input_error
import notetaker
from notetaker import fetch_meetings
from platform_compat import fmt_local_time
from hotcache_sync import match_deal, sync_hotcache
from week_review import (assign_bucket, build_front_page, empty_front_page,
                         group_by_bucket, load_active_thread_emails,
                         render_buckets_md)
from follow_up_radar import collect_nudges
from deal_status import KILL_RE
import collectors

# Date-relative windows (today/tomorrow) and rendered times compute against the
# user's configured timezone; the secondary zone is None unless a dual display
# (e.g. PT / ET) is opted into.
USER_TZ = user_tz()
USER_SECONDARY_TZ = user_secondary_tz()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Loads OAuth refresh tokens for the calendar fetch; the notetaker key is loaded by notetaker.py.
user_state.load_env()
COMPLETION_MATCH_THRESHOLD = 0.5

HOTCACHE_PATH = str(hotcache_path())
MORNING_COFFEE_MD = str(morning_coffee_md_path())
ACTIVE_THREADS_HEADING = hotcache_active_threads_heading()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

INTERNAL_DOMAINS = internal_domains()
INTERNAL_EMAILS = account_emails_lower() | {e.lower() for e in internal_team_emails()}

# Generic reputation-pool / no-reply fragments, extended from config.
_GENERIC_SPAM_FRAGMENTS = [
    "noreply", "no-reply", "donotreply", "notifications@",
    "notification@", "alerts@", "mailer@", "lemwarmup", "• warmup",
]
SPAM_FRAGMENTS = _GENERIC_SPAM_FRAGMENTS + list(extra_spam_fragments())

CALENDAR_NOISE_PREFIXES = ["accepted:", "declined:", "canceled:", "invitation:"]

# Inbound mail is pulled over a 2-day window (today + yesterday) so a kill/pass
# that lands late at night or gets forwarded the next morning still surfaces (a
# sent-only retro is blind to a counterparty pass, which arrives inbound).
# Today-only would reopen that gap at day boundaries.
INBOUND_LOOKBACK_DAYS = 1

# Marketing / automated bulk senders: inbound noise, not real correspondence.
BULK_LOCALPARTS = {
    "mail", "email", "news", "newsletter", "newsletters", "updates", "update",
    "info", "hello", "team", "marketing", "promotions", "promo", "offers",
    "sales", "deals", "store", "shop", "rewards", "points", "club", "social",
    "community", "digest", "notify", "members", "member", "account", "accounts",
    "billing", "invoices", "receipts", "do-not-reply", "do_not_reply",
}
BULK_DOMAIN_PREFIXES = (
    "mail.", "email.", "e.", "em.", "news.", "newsletter.", "marketing.",
    "crm.", "mailer.", "notifications.", "updates.", "info.", "engage.",
    "send.", "go.", "link.", "click.", "t.", "r.", "m.",
)
BULK_DOMAIN_FRAGMENTS = (
    "mailchimp", "sendgrid", "sparkpostmail", "mailgun", "list-manage",
    "substack", "beehiiv", "hubspotemail", "sendinblue", "klaviyo",
    "exacttarget", "mktomail", "rsgsv", "mcsv",
)

# Anti-drift coverage contract: afternoon-tea consults sent + inbound mail,
# meetings, tomorrow's calendar, the hotcache, and the kill / deal-match scan.
# test_briefing_coverage.py enforces this set. KILL_RE now lives in deal_status
# (imported above) so the kill regex is single-sourced across every briefing.
COLLECTORS_USED = {
    collectors.SENT_MAIL, collectors.INBOUND_MAIL, collectors.MEETINGS,
    collectors.CALENDAR, collectors.HOTCACHE_READ, collectors.KILL_SCAN,
    collectors.DEAL_MATCH,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_iso(s):
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.\d+", "", s)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        s_clean = re.sub(r"[+-]\d{2}:\d{2}$", "", s)
        dt = datetime.strptime(s_clean, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)


def fmt_local(dt_utc):
    return fmt_local_time(dt_utc, USER_TZ, USER_SECONDARY_TZ)


def is_spam(addr):
    addr_l = addr.lower()
    return any(frag in addr_l for frag in SPAM_FRAGMENTS)


def is_internal(addr):
    addr_l = addr.lower()
    domain = addr_l.split("@")[-1] if "@" in addr_l else ""
    return addr_l in INTERNAL_EMAILS or domain in INTERNAL_DOMAINS


def is_bulk(addr):
    """Marketing / automated bulk sender: inbound noise, not real correspondence."""
    addr_l = addr.lower()
    if "@" not in addr_l:
        return False
    local, domain = addr_l.split("@", 1)
    if local in BULK_LOCALPARTS:
        return True
    if domain.startswith(BULK_DOMAIN_PREFIXES):
        return True
    return any(frag in domain for frag in BULK_DOMAIN_FRAGMENTS)


def is_warmup(subject):
    s = subject.lower()
    return "lemwarmup" in s or "• warmup" in s


def is_calendar_noise(subject):
    s = subject.lower()
    return any(s.startswith(p) for p in CALENDAR_NOISE_PREFIXES)


# ── Calendar (tomorrow) ───────────────────────────────────────────────────────

def _parse_calendar_event(item, title_key, time_key_path, source):
    """Shared event parser for Google and Outlook calendar items."""
    start = item
    for k in time_key_path:
        start = (start or {}).get(k, {})
    start_raw = start if isinstance(start, str) else None

    if not start_raw:
        # Try alternate date-only key
        alt = item.get("start", {})
        start_raw = alt.get("dateTime") or alt.get("date") or ""

    if not start_raw:
        return None

    try:
        if "T" in start_raw:
            dt_utc = parse_iso(start_raw)
            time_str = fmt_local(dt_utc)
            sort_key = start_raw
        else:
            time_str = "All day"
            sort_key = start_raw + "T00:00:00+00:00"
    except Exception:
        time_str = start_raw
        sort_key = start_raw

    title = item.get(title_key) or "(No title)"
    if is_calendar_noise(title):
        return None

    return {"title": title, "time": time_str, "source": source, "_sort": sort_key}


def fetch_tomorrow_calendar(today_utc):
    # Compute "tomorrow" in the user's local zone. UTC may already be the next
    # calendar day (e.g. after 5pm PT), which would point a day too far.
    tomorrow_local = today_utc.astimezone(USER_TZ) + timedelta(days=1)
    local_offset = tomorrow_local.strftime("%z")
    local_offset_rfc = local_offset[:3] + ":" + local_offset[3:]
    tomorrow_date = tomorrow_local.strftime("%Y-%m-%d")
    time_min = f"{tomorrow_date}T00:00:00{local_offset_rfc}"
    time_max = f"{tomorrow_date}T23:59:59{local_offset_rfc}"
    events = []

    # Google Calendars — one per configured Google account
    for a in GOOGLE_ACCOUNTS:
        try:
            data = google_client(a["label"]).calendar.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            ).execute()
            for item in data.get("items", []):
                e = _parse_calendar_event(item, "summary", ["start", "dateTime"], a["label"])
                if e:
                    events.append(e)
        except Exception:
            pass

    # Outlook Calendars — one per configured Microsoft account
    for a in MICROSOFT_ACCOUNTS:
        try:
            data = microsoft_client(a["label"]).calendar_view(time_min, time_max, top=50)
            for item in data.get("value", []):
                e = _parse_calendar_event(item, "subject", ["start", "dateTime"], a["label"])
                if e:
                    events.append(e)
        except Exception:
            pass

    # Deduplicate (same title within 5 min across sources)
    merged = []
    for e in events:
        dup = False
        for m in merged:
            if e["title"].lower().strip() == m["title"].lower().strip():
                try:
                    t1 = parse_iso(e["_sort"])
                    t2 = parse_iso(m["_sort"])
                    if abs((t1 - t2).total_seconds()) < 300:
                        dup = True
                        break
                except Exception:
                    pass
        if not dup:
            merged.append(e)

    merged.sort(key=lambda e: e.get("_sort", ""))
    for e in merged:
        e.pop("_sort", None)

    return merged[:8]


# ── Sent mail today ───────────────────────────────────────────────────────────

def fetch_gmail_sent_today(gclient, account_email, source_label, today_str, since_str=None):
    """Fetch emails sent from a Gmail account between since_str and today_str
    (inclusive). since_str defaults to today_str — today only, unchanged behavior."""
    since_str = since_str or today_str
    cutoff = since_str.replace("-", "/")
    try:
        data = gclient.gmail.users().threads().list(
            userId="me",
            q=f"in:sent after:{cutoff}",
            maxResults=50,
        ).execute()
    except Exception:
        return []

    def get_thread(stub):
        tid = stub.get("id")
        if not tid:
            return None
        try:
            result = gclient.gmail.users().threads().get(
                userId="me",
                id=tid,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute(http=gclient.new_http())
            return result, stub.get("snippet", "")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(get_thread, data.get("threads", [])))

    sent = []
    seen_subjects = set()
    for item in fetched:
        if not item:
            continue
        thread_data, _ = item
        messages = thread_data.get("messages", [])
        if not messages:
            continue

        for msg in reversed(messages):
            hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            labels = msg.get("labelIds", [])
            if not ("SENT" in labels or account_email.lower() in hdrs.get("From", "").lower()):
                continue

            try:
                dt = parsedate_to_datetime(hdrs.get("Date", "")).astimezone(USER_TZ)
                d = dt.strftime("%Y-%m-%d")
                if d < since_str or d > today_str:
                    continue
            except Exception:
                continue

            subject = hdrs.get("Subject", "(No subject)")
            if is_warmup(subject) or is_calendar_noise(subject):
                break
            if subject in seen_subjects:
                break
            seen_subjects.add(subject)

            to_raw = hdrs.get("To", "")
            to_match = re.search(r"<([^>]+)>", to_raw)
            to_addr = to_match.group(1) if to_match else to_raw.strip()
            to_name = (
                to_raw[: to_raw.index("<")].strip().strip('"')
                if "<" in to_raw else to_addr
            )
            if is_spam(to_addr):
                break

            sent.append({
                "subject": subject,
                "to": to_name or to_addr,
                "to_email": to_addr,
                "account": source_label,
                "date": d,
                "tag": "",  # filled in later
            })
            break

    return sent


def fetch_outlook_sent_today(label, sent_folder_id, today_str, since_str=None):
    """Fetch emails sent from an Outlook account between since_str and today_str
    (inclusive). since_str defaults to today_str — today only, unchanged behavior."""
    since_str = since_str or today_str
    try:
        data = microsoft_client(label).folder_messages(
            sent_folder_id,
            filter=(
                f"sentDateTime ge {since_str}T00:00:00Z and "
                f"sentDateTime le {today_str}T23:59:59Z"
            ),
            select="id,subject,sentDateTime,toRecipients,bodyPreview",
            top=200,
        )
    except Exception:
        return []

    sent = []
    seen_subjects = set()
    for msg in data.get("value", []):
        subject = msg.get("subject", "(No subject)")
        if is_warmup(subject) or is_calendar_noise(subject):
            continue
        if subject in seen_subjects:
            continue
        seen_subjects.add(subject)

        to_addr = ""
        to_name = ""
        for r in msg.get("toRecipients", []):
            ea = r.get("emailAddress", {})
            addr = ea.get("address", "")
            if not is_spam(addr) and not is_internal(addr):
                to_addr = addr
                to_name = ea.get("name", addr)
                break
        if not to_addr:
            # All recipients internal — still capture first one
            for r in msg.get("toRecipients", []):
                ea = r.get("emailAddress", {})
                to_addr = ea.get("address", "")
                to_name = ea.get("name", to_addr)
                break

        if not to_addr or is_spam(to_addr):
            continue

        sent.append({
            "subject": subject,
            "to": to_name,
            "to_email": to_addr,
            "account": label,
            "date": (msg.get("sentDateTime", "") or "")[:10],
            "tag": "",  # filled in later
        })

    return sent


# ── Inbound mail (kill/pass scan + deal activity) ─────────────────────────────

def fetch_gmail_received_today(gclient, account_email, source_label, today_str, since_str=None):
    """Fetch inbound mail in a Gmail inbox between since_str and today_str
    (inclusive). Returns the latest external (non-user) message per thread with a
    snippet for kill/pass detection. Mirrors fetch_gmail_sent_today."""
    since_str = since_str or today_str
    cutoff = since_str.replace("-", "/")
    try:
        data = gclient.gmail.users().threads().list(
            userId="me",
            q=f"in:inbox after:{cutoff}",
            maxResults=50,
        ).execute()
    except Exception:
        return []

    def get_thread(stub):
        tid = stub.get("id")
        if not tid:
            return None
        try:
            result = gclient.gmail.users().threads().get(
                userId="me",
                id=tid,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute(http=gclient.new_http())
            return result, stub.get("snippet", "")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(get_thread, data.get("threads", [])))

    received = []
    seen_subjects = set()
    for item in fetched:
        if not item:
            continue
        thread_data, snippet = item
        messages = thread_data.get("messages", [])
        if not messages:
            continue

        # Latest message NOT sent by the user: the most recent inbound in thread.
        for msg in reversed(messages):
            hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            from_raw = hdrs.get("From", "")
            from_match = re.search(r"<([^>]+)>", from_raw)
            from_addr = from_match.group(1) if from_match else from_raw.strip()
            from_name = (
                from_raw[: from_raw.index("<")].strip().strip('"')
                if "<" in from_raw else from_addr
            )
            if account_email.lower() in from_addr.lower():
                continue  # the user's own message; walk back

            try:
                dt = parsedate_to_datetime(hdrs.get("Date", "")).astimezone(USER_TZ)
                d = dt.strftime("%Y-%m-%d")
                if d < since_str or d > today_str:
                    continue
            except Exception:
                continue

            subject = hdrs.get("Subject", "(No subject)")
            if is_warmup(subject) or is_calendar_noise(subject):
                break
            if is_spam(from_addr) or is_bulk(from_addr):
                break
            if subject in seen_subjects:
                break
            seen_subjects.add(subject)

            received.append({
                "subject": subject,
                "from": from_name or from_addr,
                "from_email": from_addr,
                "account": source_label,
                "snippet": snippet,
                "date": d,
                "internal": is_internal(from_addr),
            })
            break

    return received


def fetch_outlook_received_today(label, today_str, since_str=None):
    """Fetch inbound mail in an Outlook inbox between since_str and today_str
    (inclusive). Mirrors fetch_outlook_sent_today; uses the well-known 'inbox'."""
    since_str = since_str or today_str
    try:
        data = microsoft_client(label).folder_messages(
            "inbox",
            filter=(
                f"receivedDateTime ge {since_str}T00:00:00Z and "
                f"receivedDateTime le {today_str}T23:59:59Z"
            ),
            select="id,subject,receivedDateTime,from,bodyPreview",
            top=200,
        )
    except Exception:
        return []

    received = []
    seen_subjects = set()
    for msg in data.get("value", []):
        subject = msg.get("subject", "(No subject)")
        if is_warmup(subject) or is_calendar_noise(subject):
            continue
        if subject in seen_subjects:
            continue

        ea = msg.get("from", {}).get("emailAddress", {})
        from_addr = ea.get("address", "")
        from_name = ea.get("name", from_addr)
        if not from_addr or is_spam(from_addr) or is_bulk(from_addr):
            continue
        seen_subjects.add(subject)

        received.append({
            "subject": subject,
            "from": from_name,
            "from_email": from_addr,
            "account": label,
            "snippet": msg.get("bodyPreview", "") or "",
            "date": (msg.get("receivedDateTime", "") or "")[:10],
            "internal": is_internal(from_addr),
        })

    return received


# ── Hotcache ──────────────────────────────────────────────────────────────────

def parse_hotcache_threads():
    """Return thread names from the active-threads section of hotcache.md."""
    if not os.path.exists(HOTCACHE_PATH):
        return []
    with open(HOTCACHE_PATH, encoding="utf-8") as f:
        text = f.read()
    m = re.search(
        rf"##\s+{re.escape(ACTIVE_THREADS_HEADING)}(.*?)(?=\n## |\Z)",
        text,
        re.DOTALL,
    )
    if not m:
        return []
    return re.findall(r"^### (.+)", m.group(1), re.MULTILINE)


def _match_deal_thread(item, hotcache_threads):
    """Return the open hotcache thread this inbound item belongs to, or None.
    Thin wrapper over hotcache_sync.match_deal (distinctive-token, newsletter-immune)."""
    party = f"{item.get('from', '')} {item.get('from_email', '')} {item.get('subject', '')}"
    return match_deal(party, hotcache_threads)


def detect_status_changes(inbound_raw, hotcache_threads):
    """Scan inbound mail for counterparty kill/pass language and tie each hit to an
    open hotcache thread when possible. Forwarded kills (internal sender, external
    content) are included. Surfaced for review, never auto-actioned. Delegates to
    the shared kill scan so the regex + attribution are single-sourced."""
    return collectors.kill_scan(inbound_raw, hotcache_threads)


# ── Morning coffee action items ───────────────────────────────────────────────

def parse_morning_coffee_items():
    """Return (open_items, done_items) from the morning briefing checkboxes."""
    if not os.path.exists(MORNING_COFFEE_MD):
        return [], []
    with open(MORNING_COFFEE_MD, encoding="utf-8") as f:
        content = f.read()
    open_items = re.findall(r"^- \[ \] (.+)$", content, re.MULTILINE)
    done_items = re.findall(r"^- \[x\] (.+)$", content, re.MULTILINE)
    return open_items, done_items


# ── Tag and auto-detect ───────────────────────────────────────────────────────

def _tokens(text):
    return set(re.findall(r"\b[a-z]{3,}\b", text.lower()))


def tag_sent_item(item, hotcache_threads):
    if is_internal(item.get("to_email", "")):
        return "admin"
    combined = _tokens(item.get("to", "") + " " + item.get("to_email", "") + " " + item.get("subject", ""))
    for thread in hotcache_threads:
        if _tokens(thread) & combined:
            return "deal-move"
    return "relationship"


def detect_completed_items(open_items, sent_today, meetings_today):
    """50% token overlap between action item and (sent subjects + meeting attendees) = done."""
    sent_signals = set()
    for m in sent_today:
        sent_signals.update(_tokens(m.get("subject", "")))
        sent_signals.update(_tokens(m.get("to", "")))
        sent_signals.update(_tokens(m.get("to_email", "")))
    for mtg in meetings_today:
        sent_signals.update(_tokens(mtg.get("title", "")))
        for a in mtg.get("attendees", []):
            sent_signals.update(_tokens(a))

    completed, still_open = [], []
    for item in open_items:
        toks = _tokens(item)
        if toks and len(toks & sent_signals) / len(toks) >= COMPLETION_MATCH_THRESHOLD:
            completed.append(item)
        else:
            still_open.append(item)

    return completed, still_open


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON (skips API calls)")
    args = parser.parse_args()

    if not args.input and not is_tier1():
        missing_input_error("afternoon_tea.py")

    today_utc = datetime.now(timezone.utc)
    today_str = today_utc.astimezone(USER_TZ).strftime("%Y-%m-%d")
    errors = []

    inbound_since_str = (
        today_utc.astimezone(USER_TZ) - timedelta(days=INBOUND_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    if args.input:
        # Tier 2: load sent + inbound mail + calendar from pre-fetched connector data
        data = load_input(args.input)
        sent_today = data.get("sent_today", [])
        inbound_raw = data.get("inbound_today", [])
        tomorrow_calendar = data.get("calendar_tomorrow", [])
    else:
        # Tier 1: fetch directly via OAuth clients
        sent_today = []
        futures = {}
        n_accts = len(GOOGLE_ACCOUNTS) + len(MICROSOFT_ACCOUNTS)
        with ThreadPoolExecutor(max_workers=max(2, n_accts)) as pool:
            for a in GOOGLE_ACCOUNTS:
                futures[a["label"]] = pool.submit(
                    fetch_gmail_sent_today, google_client(a["label"]), a["email"], a["label"], today_str
                )
            for a in MICROSOFT_ACCOUNTS:
                futures[a["label"]] = pool.submit(
                    fetch_outlook_sent_today, a["label"], a["sent_folder_id"], today_str
                )
            for label, fut in futures.items():
                try:
                    sent_today.extend(fut.result())
                except Exception as e:
                    errors.append(f"Sent mail ({label}): {e}")

        # Inbound mail (2-day window) across all accounts: feeds the kill/pass scan
        # and the deal-activity view.
        inbound_raw = []
        ifutures = {}
        with ThreadPoolExecutor(max_workers=max(2, n_accts)) as pool:
            for a in GOOGLE_ACCOUNTS:
                ifutures[a["label"]] = pool.submit(
                    fetch_gmail_received_today, google_client(a["label"]),
                    a["email"], a["label"], today_str, inbound_since_str
                )
            for a in MICROSOFT_ACCOUNTS:
                ifutures[a["label"]] = pool.submit(
                    fetch_outlook_received_today, a["label"], today_str, inbound_since_str
                )
            for label, fut in ifutures.items():
                try:
                    inbound_raw.extend(fut.result())
                except Exception as e:
                    errors.append(f"Inbound mail ({label}): {e}")

        tomorrow_calendar = []
        try:
            tomorrow_calendar = fetch_tomorrow_calendar(today_utc)
        except Exception as e:
            errors.append(f"Calendar: {e}")

    # The notetaker always runs (HTTP API key, not an OAuth connector). Request
    # summaries so the meeting status-change scan below can read them (no extra
    # API calls: the full note is already fetched).
    try:
        meetings_today = fetch_meetings(today_str, include_summary=True)
    except Exception as e:
        meetings_today = []
        errors.append(f"{notetaker.safe_display_name()}: {e}")

    # Hotcache threads
    try:
        hotcache_threads = parse_hotcache_threads()
    except Exception as e:
        hotcache_threads = []
        errors.append(f"Hotcache: {e}")

    # Tag sent items
    for item in sent_today:
        item["tag"] = tag_sent_item(item, hotcache_threads)

    # Status-change scan: kill/pass language in inbound (incl. forwarded), tied to
    # open hotcache deals. inbound_today is the external-only view; deal_inbound is
    # the slice matched to an open deal thread (newsletter-immune).
    status_change_alerts = detect_status_changes(inbound_raw, hotcache_threads)
    # Also scan today's meeting summaries: a kill or re-engagement/pivot announced
    # in a meeting never reaches mail. The summaries are already fetched (via
    # include_summary), so this adds no notetaker calls. Merge into the same
    # channel, then drop the transient summary text from meetings_today.
    _meeting_scan_items = [
        {"title": m.get("title", ""), "text": m.get("summary_text", ""), "date": today_str}
        for m in meetings_today
    ]
    status_change_alerts.extend(collectors.meeting_status_scan(_meeting_scan_items, hotcache_threads))
    for m in meetings_today:
        m.pop("summary_text", None)
    inbound_today = [it for it in inbound_raw if not it.get("internal")]
    deal_inbound = []
    for it in inbound_today:
        thread = _match_deal_thread(it, hotcache_threads)
        if thread:
            deal_inbound.append({**it, "thread": thread})

    # Keep the hotcache active-threads metadata honest: refresh last_contact, revive
    # dead-but-active deals, bump the staleness date from today's mail. Kills stay
    # human-confirmed via status_change_alerts; this writes only safe directions.
    mail_events = []
    for it in sent_today:
        if it.get("date"):
            mail_events.append({
                "date": it["date"],
                "party": f"{it.get('to', '')} {it.get('to_email', '')} {it.get('subject', '')}",
            })
    for it in inbound_raw:
        if it.get("date"):
            mail_events.append({
                "date": it["date"],
                "party": f"{it.get('from', '')} {it.get('from_email', '')} {it.get('subject', '')}",
            })
    try:
        hotcache_sync_result = sync_hotcache(mail_events, today_str)
    except Exception as e:
        hotcache_sync_result = {}
        errors.append(f"Hotcache sync: {e}")

    # Morning-coffee action items
    try:
        open_items, done_items = parse_morning_coffee_items()
    except Exception as e:
        open_items, done_items = [], []
        errors.append(f"Morning coffee: {e}")

    # Auto-detect which open items were completed
    auto_done, still_open = detect_completed_items(open_items, sent_today, meetings_today)

    # Buckets. Afternoon tea has its own fetch path, so it normalizes its three
    # item shapes onto the keys assign_bucket reads, then reuses week_review's
    # grouping and renderer rather than keeping a second copy of either.
    def _as_entry(raw, section, **extra):
        if isinstance(raw, str):
            return {"subject": raw, "section": section, **extra}
        return {
            "subject": raw.get("subject", ""),
            "counterparty_name": raw.get("from") or raw.get("to") or "",
            "counterparty_email": raw.get("from_email") or raw.get("to_email") or "",
            "body_preview": raw.get("body_preview", ""),
            "section": section,
            **extra,
        }

    bucket_entries = (
        [_as_entry(i, "done", done_today=True) for i in done_items]
        + [_as_entry(i, "open") for i in still_open]
        + [_as_entry(i, "inbox_pending") for i in deal_inbound]
    )
    buckets, buckets_md = [], ""
    try:
        for e in bucket_entries:
            e["bucket"] = assign_bucket(e)
        buckets = group_by_bucket({"waiting_on_user": bucket_entries})
        buckets_md = render_buckets_md(buckets, "afternoon-tea")
    except Exception as e:                                      # noqa: BLE001
        errors.append(f"Bucket render: {e}")

    nudges, nudge_err = collect_nudges(
        datetime.now(timezone.utc).astimezone(USER_TZ).date())
    if nudge_err:
        errors.append(nudge_err)

    # The front page over the same day list: what is still worth doing before
    # you stop, with everything else folded into its business row.
    front = empty_front_page()
    try:
        front = build_front_page(
            buckets, "afternoon-tea",
            alerts={"newly_cold": status_change_alerts},
            active_emails=load_active_thread_emails(),
            heading="BEFORE YOU STOP")
    except Exception as e:                                      # noqa: BLE001
        errors.append(f"Front page: {e}")
    page_note = ""
    try:
        import briefing_html
        page_note = briefing_html.page_line("afternoon-tea")
    except Exception:                                           # noqa: BLE001
        page_note = ""

    # Travel notices for TOMORROW. Morning Coffee asks "what does today need";
    # this asks "what does tomorrow need, while there is still an evening to
    # do something about it". An early flight is the case that matters: told
    # at 5:55 AM it is already too late to pack, and told the night before it
    # is not. So the departure window is one day wider here, and the ledger is
    # keyed per briefing so this evening's notice is not swallowed by the fact
    # that this morning's briefing mentioned the same trip.
    travel_notices = []
    travel_md = ""
    travel_file_md = ""
    _travel_commit = None
    if travel_enabled():
        # Imported here, not at module load: a missing or broken travel module
        # must cost the reader the travel section and nothing else.
        try:
            import travel
            _travel = travel.collect(
                tomorrow_calendar, today_str, USER_TZ,
                travel_buffer_minutes(),
                briefing="afternoon-tea",
                # Tomorrow's flight is tonight's problem: the departure notice
                # fires the day before rather than the morning of.
                departure_lead_days=1, weather_lead_days=3, flights_min_days=4,
            )
            travel_notices = _travel["notices"]
            # Rendered here so the skill pastes it rather than describing it.
            # Emitting only the raw notices left the skill instructing a paste
            # of `travel_md`, a key that was never in the payload, so the
            # block silently vanished from this briefing's file and page.
            travel_md = travel.render_travel_md(travel_notices, mode="terminal")
            travel_file_md = travel.render_travel_md(travel_notices, mode="md")
            if _travel["error"]:
                errors.append(_travel["error"])
            # Held until the JSON is printed. The ledger means "the reader was
            # told", so it must not be written before they were.
            _travel_commit = _travel.get("commit")
        except Exception as e:                                  # noqa: BLE001
            errors.append(f"Travel notices unavailable: {e}")

    # The opening quote. Selection and the no-repeat ledger both live in
    # app/quotes.py: told to "pick at random" from a list in this skill's
    # prose, a model has no memory of yesterday's briefing and repeats within
    # days. Like travel above, the ledger write is deferred until after the
    # JSON is printed, so a run that dies before rendering does not burn one.
    quote_md, _quote_commit = "", None
    try:
        import quotes
        _quote, _quote_commit = quotes.pick()
        quote_md = quotes.render(_quote)
    except Exception as e:                                      # noqa: BLE001
        errors.append(f"Quote unavailable: {e}")

    # The Notes the watcher sent or folded between the briefings today
    # (app/note_send.py), so the retro can open with them. Fails open: no
    # ledger, or an unreadable one, reads as no Notes.
    try:
        import note_watch
        import note_send
        notes_today = note_watch.notes_today(
            json.loads(note_send.ledger_path().read_text(encoding="utf-8")),
            today_str)
    except Exception:                                           # noqa: BLE001
        notes_today = []

    output = {
        "meta": resolved_meta(),
        "date": today_str,
        "quote_md": quote_md,
        "buckets": buckets,
        "buckets_md": buckets_md,
        **front,
        "page_note": page_note,
        "nudges": nudges,
        "notes_today": notes_today,
        "sent_today": sent_today,
        "deal_inbound": deal_inbound,
        "status_change_alerts": status_change_alerts,
        "inbound_count": len(inbound_today),
        "inbound_today": inbound_today,
        "hotcache_sync": hotcache_sync_result,
        "meetings_today": meetings_today,
        "tomorrow_calendar": tomorrow_calendar,
        "travel": travel_notices,
        "travel_md": travel_md,            # the finished Travel block, terminal
        "travel_file_md": travel_file_md,  # the same block, with links, for the file
        "hotcache_threads": hotcache_threads,
        "morning_priorities": done_items,
        "action_items": {
            "open": still_open,
            "completed": done_items,
            "auto_detected_done": auto_done,
        },
        "errors": errors,
    }

    # User extension sections (fail open; see app/extensions.py).
    extensions.attach(output, "afternoon-tea")

    # This output's meta carries the pending release notes; the render that
    # follows is their one showing (see plugin_update.mark_notes_shown).
    if (output.get("meta") or {}).get("whats_new"):
        import plugin_update
        plugin_update.mark_notes_shown()

    # Leave a machine-readable copy for the Workbench (app/workbench_serve.py).
    workbench_data.write_sidecar("afternoon_tea_latest", output)

    # And one content-free row for the weekly scorecard (app/kpi_events.py).
    kpi_events.record_snapshot("afternoon-tea", output)

    print(json.dumps(output, indent=2, default=str))

    # The briefing is now out. Only now is it true that the reader was shown
    # tomorrow's travel notices, so only now is it honest to record them.
    if _travel_commit is not None:
        try:
            _travel_commit()
        except Exception:                                       # noqa: BLE001
            pass

    # Same reasoning for the quote: "recently shown" must mean the reader was
    # actually shown it, so the ledger is written only once the briefing is out.
    if _quote_commit is not None:
        try:
            _quote_commit()
        except Exception:                                       # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
