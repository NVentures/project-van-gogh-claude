#!/usr/bin/env python3
"""
week_review.py — Weekly mission control data pull.

Pulls calendar (Google + Outlook), Obsidian roadmap tasks, and sent-mail
deal classification across both accounts. Outputs a JSON blob to stdout
for Claude to render as the /week briefing.

Usage:
    python3 app/week_review.py [--since 30] [--week-ahead 6] [--sources-days 7]
"""

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import config_loader
import extensions
import kpi_events
import priority_judge
from config_loader import (
    account_labels,
    allow_domains,
    deal_critical_domains,
    done_log_path,
    extra_spam_fragments,
    full_sidecar_path,
    google_accounts,
    hotcache_path,
    internal_domains,
    internal_team_emails,
    logs_dir,
    microsoft_accounts,
    resolved_meta,
    sources_dir,
    travel_buffer_minutes,
    travel_enabled,
    user_secondary_tz,
    user_tz,
    weekly_dir,
    workspace_week_md_path,
)
from google_client import google_client
from microsoft_client import microsoft_client
from data_sources import is_tier1, load_input, missing_input_error
from claude_cli import run_claude
from platform_compat import fmt_local_time
from hotcache_sync import _generic, _tokens
import collectors

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

# The user's configured timezone(s). Date-relative windows (today/tomorrow) and
# all rendered times compute against USER_TZ; USER_SECONDARY_TZ is None unless
# the user opts into a side-by-side dual display (e.g. PT / ET).
USER_TZ = user_tz()
USER_SECONDARY_TZ = user_secondary_tz()

HOTCACHE_PATH = str(hotcache_path())
OBSIDIAN_SOURCES_DIR = str(sources_dir())
OBSIDIAN_WEEKLY = str(weekly_dir())
DONE_LOG_PATH = str(done_log_path())
WORKSPACE_WEEK_MD = str(workspace_week_md_path())
LOGS_DIR = str(logs_dir())
FULL_SIDECAR_PATH = str(full_sidecar_path())

# Anti-drift coverage contract: the /week hub is the most complete briefing, so it
# consults every source category. test_briefing_coverage.py enforces this set.
COLLECTORS_USED = {
    collectors.SENT_MAIL, collectors.INBOUND_MAIL, collectors.MEETINGS,
    collectors.CALENDAR, collectors.HOTCACHE_READ, collectors.OBSIDIAN_TASKS,
    collectors.KILL_SCAN, collectors.DEAL_MATCH,
}

# Sender domains forced tier-1 regardless of Haiku's call. Configured via
# email_filters.deal_critical_domains. Same defensive allowlist pattern as
# INTERNAL_TEAM_EMAILS — a known-important counterparty (counsel, lender,
# direct partner) should never be silently dropped by classifier error.
DEAL_CRITICAL_DOMAINS = deal_critical_domains()

# Generic spam patterns. User-specific additions live in
# config.json email_filters.extra_spam_fragments.
GENERIC_SPAM_FRAGMENTS = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications@", "notification@", "alerts@", "mailer@",
    "newsletter@", "news@", "updates@", "marketing@",
    "support@", "help@", "info@", "bounce", "daemon", "postmaster",
    "linkedin.com", "twitter.com", "dropbox.com",
    "events@", "digest@", "feedback@",
    # Cold lead-gen junk-TLD / spam-domain patterns
    ".help", ".info", ".live", ".xyz", ".online", ".biz", ".store",
    "dmarcreport", "dmarcreports", "dmarc-support",
]

SPAM_FRAGMENTS = GENERIC_SPAM_FRAGMENTS + list(extra_spam_fragments())

# Subject patterns that mark newsletters, marketing, system noise, loan spam.
NOISE_SUBJECT_PATTERNS = [
    "newsletter", "digest", "webinar", "reminder:", "out of office",
    "[webinar", "(sponsored)", "weekender:", "issue #", "weekly brief",
    "monthly brief", "bi-weekly", "read now", "save the date",
    "[preview]", "phishing:", "junk:", "automatic reply",
    "report domain:", "your receipt", "your renewal", "renewal notice",
    "trial ending", "trial ends", "pre-qualified", "pre-approval",
    "credit cleared", "approval range", "eligibility increased",
    "updated terms", "updated approval", "loc pre-approval",
    "credit review update", "review complete (terms",
    "family office capital", "family office allocation",
    "save $", "limited time", "last chance",
    "[exclusive invite]", "free shipping", "membership rewards",
    "monthly update", "weekly update", "daily regulatory update",
    "weekly legislative summary",
]

# High-signal sender domains. These ALWAYS pass even if they'd hit a generic
# noise filter. Configure via config.json email_filters.allow_domains and
# email_filters.internal_domains.
ALLOW_DOMAINS = allow_domains() | internal_domains()


def is_allow_domain(addr):
    a = (addr or "").lower()
    if "@" not in a:
        return False
    return a.rsplit("@", 1)[1] in ALLOW_DOMAINS

# Lemlist warmup-pool emails carry these tags in the subject. Drop before Haiku.
WARMUP_SUBJECT_PATTERNS = [
    "• lemwarmup",
    "lemwarmup",
    "• warmup",
    "[lemwarmup]",
]

# Internal team emails — loaded from config.json email_filters.internal_team_emails.
INTERNAL_TEAM_EMAILS = internal_team_emails()

# Overreach guard (Item 11). When deal flow is thin, the age-driven cold buckets
# over-promote low-signal mail to "red" (cold_urgent). A cold_urgent thread whose
# counterparty has NO live deal in hotcache and is older than this is demoted to
# cold_monitor (lower severity, never rendered as a top/red priority). 30 days =
# the --since cap, well past the 14d cold_urgent threshold, so a genuinely active
# counterparty (in hotcache) is never affected.
STALE_COUNTERPARTY_DAYS = 30

# Subjects that read as "potentially valuable" stakeholder/RTO feedback requests
# rather than a deal action. These belong in a lower-severity bucket, never red.
STAKEHOLDER_FEEDBACK_PATTERNS = [
    "stakeholder feedback", "feedback request", "request for feedback",
    "rto feedback", "return to office", "return-to-office", "rto policy",
    "share your feedback", "we want your feedback", "your input",
    "share your thoughts", "town hall", "survey:", "quick survey",
    "feedback survey", "all-hands", "all hands",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_iso(s):
    """Parse ISO datetime string to UTC-aware datetime."""
    s = s.split(".")[0].rstrip("Z")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_local(dt_utc):
    return fmt_local_time(dt_utc, USER_TZ, USER_SECONDARY_TZ)


def is_spam(addr):
    a = (addr or "").lower()
    # Allowlisted domains always pass — counsel, internal team, known partners.
    if is_allow_domain(a):
        return False
    if any(f in a for f in SPAM_FRAGMENTS):
        return True
    # Cold lead-gen domains often use junk TLDs (.help, .info, etc.)
    if "@" in a:
        domain = a.rsplit("@", 1)[1]
        if domain.endswith((".help", ".info", ".live", ".xyz", ".online", ".biz", ".store")):
            return True
    return False


def is_noisy_subject(subject):
    s = (subject or "").lower()
    if not s:
        return False
    # Emoji prefix is reliable marketing signal
    first = s.lstrip()[:2]
    if first and ord(first[0]) > 9000:  # emoji unicode range
        return True
    return any(p in s for p in NOISE_SUBJECT_PATTERNS)


def is_placeholder_reply(body_preview):
    """Detect short acknowledgment replies that promise substantive follow-up
    later. These should NOT mark a thread as 'answered' for inbox-sweep
    purposes (the counterparty is still effectively waiting on the user)."""
    if not body_preview:
        return False
    b = body_preview.lower().strip()
    if len(b) > 400:
        return False
    placeholder_phrases = [
        "will review", "review and get back", "will get back",
        "get back to you", "circle back", "look into this",
        "circle back to you", "take a look and", "thanks, will",
        "thank you, will", "got it, will", "received, will",
    ]
    return any(p in b for p in placeholder_phrases)


def is_self(addr, account):
    if not account:
        return False
    from email.utils import parseaddr
    parsed = parseaddr(addr or "")[1] or (addr or "")
    return parsed.strip().lower() == account.strip().lower()


def is_internal(addr):
    return (addr or "").lower() in INTERNAL_TEAM_EMAILS


def is_warmup(subject):
    s = (subject or "").lower()
    return any(p.lower() in s for p in WARMUP_SUBJECT_PATTERNS)


def is_stakeholder_feedback(subject):
    """True if the subject reads as an RTO / stakeholder feedback request.

    Potentially valuable to some users, but never a deal action, so it must not
    be promoted to red (cold_urgent). See Item 11 (deal-review overreach)."""
    s = (subject or "").lower()
    if not s:
        return False
    return any(p in s for p in STAKEHOLDER_FEEDBACK_PATTERNS)


def is_calendar_noise(subject):
    """Acceptance/decline/OOO replies that aren't real correspondence."""
    sl = (subject or "").lower().strip()
    return sl.startswith((
        "accepted:", "declined:", "canceled:", "cancelled:",
        "tentative:", "updated invitation", "fw: accepted:",
        "fwd: accepted:", "re: accepted:",
    ))


# Unicode whitespace garbage common in marketing-email bodyPreview fields.
# Zero-width spaces, soft hyphens, BOM, narrow no-break spaces — strip before
# truncation so the visible signal-to-noise ratio in body_preview is high.
UNICODE_PADDING_RE = re.compile(
    r"[͏­​‌‍‎‏   ﻿]+"
)


def trim_body(body, max_len=150):
    """Strip unicode padding (marketing-email garbage) and truncate."""
    if not body:
        return ""
    cleaned = UNICODE_PADDING_RE.sub(" ", body)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def is_deal_critical(addr):
    """True if sender domain is on the deal-critical allowlist (config-driven)."""
    a = (addr or "").lower()
    if "@" not in a:
        return False
    return a.rsplit("@", 1)[1] in DEAL_CRITICAL_DOMAINS


def normalize_subject(s):
    """Lowercase + strip up to 3 levels of Re:/Fw:/Fwd: prefixes."""
    s = (s or "").strip().lower()
    for _ in range(3):
        m = re.match(r"^(re|fw|fwd|aw|sv)\s*:\s*", s)
        if not m:
            break
        s = s[m.end():]
    return s.strip()


# done log line format: - [Label] "Subject" — Name (email@x.com) — tail
# Subject must stay on one line — otherwise an entry without parens-email lets
# the regex span lines and capture the next entry's subject opening as closing.
_DONE_LINE_RE = re.compile(r'"([^"\n]+)"[^\n]*?\(([^()\s\n]+@[^()\s\n]+)\)')


def load_done_set():
    """Parse done-{YYYY}.md → set of (normalized_subject, lowercased_email) tuples."""
    if not os.path.exists(DONE_LOG_PATH):
        return set()
    try:
        with open(DONE_LOG_PATH, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return set()
    done = set()
    for m in _DONE_LINE_RE.finditer(content):
        subj_norm = normalize_subject(m.group(1))
        email = m.group(2).strip().lower()
        done.add((subj_norm, email))
    return done


def is_suppressed(subject, email, done_set):
    """True if (normalized_subject, lowercased_email) is recorded in done log."""
    if not done_set:
        return False
    return (normalize_subject(subject), (email or "").strip().lower()) in done_set


def dedup_cross_account(entries):
    """Collapse a thread that surfaces on more than one account into one row.

    A counterparty CC'd on more than one account (e.g. both Gmail and Outlook)
    produces two near-identical entries. Key on (normalized_subject, lowercased
    counterparty email), keep the first occurrence, fold in the freshest age,
    and annotate `source` with every contributing account, e.g. "Gmail + Outlook".
    Rows with no subject and no email can't be matched, so they're never collapsed.
    """
    by_key = {}
    order = []
    for e in entries:
        subj = normalize_subject(e.get("subject", ""))
        email = (e.get("counterparty_email") or "").strip().lower()
        key = (subj, email) if (subj or email) else (id(e),)
        if key not in by_key:
            kept = dict(e)
            src = e.get("source", "")
            kept["sources"] = [src] if src else []
            by_key[key] = kept
            order.append(key)
        else:
            kept = by_key[key]
            src = e.get("source", "")
            if src and src not in kept["sources"]:
                kept["sources"].append(src)
            for age_field in ("reply_age_days", "age_days"):
                if age_field in e and e[age_field] < kept.get(age_field, 99):
                    kept[age_field] = e[age_field]
    result = []
    for key in order:
        kept = by_key[key]
        srcs = kept.pop("sources", [])
        if len(srcs) > 1:
            kept["source"] = " + ".join(srcs)
        result.append(kept)
    return result


def resolve_since(since_arg):
    """Resolve --since 'auto' → days since prior week.md was generated.

    Floor is 14 days — not 7 — because cold_monitor threads live in the 7–14d
    bucket. Fetching only 7d of sent mail would make those threads invisible
    until they're rescued by next week's carry-forward, creating a one-week
    blind spot. 14 covers the full cold range with negligible cost (the heavy
    bulk in the JSON is inbox_pending, anchored to last Monday separately).
    Cap at 30 (sanity for long gaps).
    """
    if since_arg != "auto":
        try:
            return max(1, int(since_arg))
        except (ValueError, TypeError):
            return 14
    if not os.path.exists(WORKSPACE_WEEK_MD):
        return 14
    try:
        with open(WORKSPACE_WEEK_MD, encoding="utf-8") as f:
            head = f.read(800)
        m = re.search(r"^generated:\s*(\d{4}-\d{2}-\d{2})", head, re.MULTILINE)
        if not m:
            return 14
        prior = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        today = datetime.now(USER_TZ).date()
        delta = (today - prior).days
        return max(14, min(delta, 30))
    except Exception:
        return 14


# ── Section A: Calendar ───────────────────────────────────────────────────────

def fetch_google_calendar(label, is_primary, today_utc, days_ahead):
    time_min = today_utc.strftime("%Y-%m-%dT00:00:00Z")
    time_max = (today_utc + timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59Z")
    gc = google_client(label)
    data = gc.calendar.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    source = "Google" if is_primary else f"Google ({label})"
    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        start_raw = start.get("dateTime") or start.get("date")
        if not start_raw:
            continue
        try:
            if "T" in start_raw:
                dt_utc = parse_iso(start_raw)
                time_str = fmt_local(dt_utc)
                _dt = dt_utc.astimezone(USER_TZ)
                day_str = _dt.strftime("%a %b") + f" {_dt.day}"
                sort_key = start_raw
            else:
                time_str = "All day"
                d = datetime.strptime(start_raw, "%Y-%m-%d")
                day_str = d.strftime("%a %b") + f" {d.day}"
                sort_key = start_raw
        except Exception:
            time_str = start_raw
            day_str = ""
            sort_key = start_raw
        events.append({
            "source": source,
            "title": item.get("summary", "(No title)"),
            "day": day_str,
            "time": time_str,
            # Carried for travel notices: an airport code here is what marks
            # an event as a flight (app/travel.py). Absent on most events.
            "location": item.get("location", "") or "",
            "_sort": sort_key,
        })
    return events


def fetch_outlook_calendar(label, today_utc, days_ahead):
    time_min = today_utc.strftime("%Y-%m-%dT00:00:00Z")
    time_max = (today_utc + timedelta(days=days_ahead)).strftime("%Y-%m-%dT23:59:59Z")
    data = microsoft_client(label).calendar_view(time_min, time_max, top=200)
    events = []
    for item in data.get("value", []):
        start_raw = item.get("start", {}).get("dateTime", "")
        if not start_raw:
            continue
        try:
            dt_utc = parse_iso(start_raw)
            time_str = fmt_local(dt_utc)
            _dt = dt_utc.astimezone(USER_TZ)
            day_str = _dt.strftime("%a %b") + f" {_dt.day}"
            sort_key = start_raw
        except Exception:
            time_str = start_raw
            day_str = ""
            sort_key = start_raw
        events.append({
            "source": label,
            "title": item.get("subject", "(No title)"),
            "day": day_str,
            "time": time_str,
            # Graph nests it one level down; same purpose as the Google side.
            "location": (item.get("location") or {}).get("displayName", "") or "",
            "_sort": sort_key,
        })
    return events


def merge_calendars(*event_lists):
    """Merge and deduplicate across N calendars (same title + start within 5 min = same event)."""
    merged = []
    for evts in event_lists:
        for e in evts:
            duplicate = False
            for me in merged:
                if e["title"].lower().strip() == me["title"].lower().strip():
                    try:
                        t1 = parse_iso(e["_sort"])
                        t2 = parse_iso(me["_sort"])
                        if abs((t1 - t2).total_seconds()) < 300:
                            duplicate = True
                            break
                    except Exception:
                        pass
            if not duplicate:
                merged.append(e)
    merged.sort(key=lambda e: e.get("_sort", ""))
    # _sort is kept, not popped. It is the only field carrying the original
    # instant with its UTC offset, and travel notices need that: a flight's
    # departure clock is not the traveller's clock (app/travel.py). The
    # day/time strings beside it are display text with no zone. Nothing
    # renders _sort; morning_coffee filters today's events on it.
    return merged


# ── Section B: Obsidian Tasks ─────────────────────────────────────────────────

def fetch_obsidian_tasks(since_days, sources_days=7):
    tasks = []
    seen = set()

    # Primary: hotcache action-items section (configured heading)
    from config_loader import hotcache_action_items_heading
    _action_heading = hotcache_action_items_heading()
    _action_heading_re = re.compile(r"^##\s+" + re.escape(_action_heading) + r"\s*$")
    if os.path.exists(HOTCACHE_PATH):
        with open(HOTCACHE_PATH, encoding="utf-8") as f:
            content = f.read()
        in_section = False
        for line in content.splitlines():
            if _action_heading_re.match(line):
                in_section = True
                continue
            if in_section and re.match(r"^## ", line):
                break
            if in_section and re.match(r"^- \[ \]", line):
                task = re.sub(r"^- \[ \]\s*", "", line).strip()
                fp = task[:60]
                if fp not in seen:
                    seen.add(fp)
                    tasks.append({"project": "Action Items", "task": task})

    # Secondary: source pages with "## Action Items" modified within sources_days
    if os.path.exists(OBSIDIAN_SOURCES_DIR):
        cutoff = datetime.now(timezone.utc).timestamp() - (sources_days * 86400)
        for fname in sorted(os.listdir(OBSIDIAN_SOURCES_DIR)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(OBSIDIAN_SOURCES_DIR, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    continue
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            title_m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else fname.replace(".md", "")
            in_action = False
            for line in content.splitlines():
                if re.match(r"^## Action Items", line):
                    in_action = True
                    continue
                if in_action and re.match(r"^## ", line):
                    break
                if in_action and re.match(r"^- \[ \]", line):
                    task = re.sub(r"^- \[ \]\s*", "", line).strip()
                    fp = task[:60]
                    if fp not in seen:
                        seen.add(fp)
                        tasks.append({"project": title, "task": task})

    return tasks


def load_prior_week_state(week_start_str):
    """Read prior week's Obsidian file → counts + unchecked items per section.

    Used for both trend deltas AND carry-forward: any unchecked waiting/cold
    line that isn't already surfaced by the current run is merged back into
    the output so the --since 7 window doesn't lose threads that aged past
    its boundary.
    """
    try:
        prior_date = (datetime.strptime(week_start_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        path = os.path.join(OBSIDIAN_WEEKLY, f"week-{prior_date}.md")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            content = f.read()

        # Match `- [ ] [Label] "Subject" — Name (email) — tail` lines. The
        # source label is any configured account label, plus the "Google (X)"
        # form emitted for non-primary Google calendars.
        _labels = list(account_labels()) + [f"Google ({a['label']})" for a in GOOGLE_ACCOUNTS]
        labels_alt = "|".join(re.escape(L) for L in _labels) or "(?!x)x"
        line_re = re.compile(
            rf'\[(?P<source>{labels_alt})\]\s*"(?P<subject>[^"\n]+)"\s*'
            rf'—\s*(?P<name>[^()\n]*?)\s*'
            rf'\((?P<email>[^()\s\n]+@[^()\s\n]+)\)\s*—?\s*(?P<tail>[^\n]*)'
        )

        sections = {"waiting": [], "cold_urgent": [], "cold_monitor": []}
        current = None
        for line in content.splitlines():
            low = line.strip().lower()
            if low.startswith("## deals — waiting on") or low.startswith("## deals - waiting on"):
                current = "waiting"
                continue
            if low.startswith("## deals — cold urgent") or low.startswith("## deals - cold urgent"):
                current = "cold_urgent"
                continue
            if low.startswith("## deals — cold monitor") or low.startswith("## deals - cold monitor"):
                current = "cold_monitor"
                continue
            if line.startswith("## "):
                current = None
                continue
            if not current:
                continue
            if not re.match(r"^\s*- \[ \]", line):
                continue
            m = line_re.search(line)
            if not m:
                continue
            sections[current].append({
                "source": m.group("source").strip(),
                "subject": m.group("subject").strip(),
                "counterparty_name": m.group("name").strip(),
                "counterparty_email": m.group("email").strip().lower(),
                "tail": m.group("tail").strip(),
                "carried": True,
            })

        return {
            "cold_urgent_count": len(sections["cold_urgent"]),
            "waiting_count": len(sections["waiting"]),
            "carried_waiting": sections["waiting"],
            "carried_cold_urgent": sections["cold_urgent"],
            "carried_cold_monitor": sections["cold_monitor"],
        }
    except Exception:
        return None


# ── Notetaker meeting cross-ref ───────────────────────────────────────────────

def _meeting_resolved(entry, meetings, today_date):
    """Return the most-recent notetaker meeting that likely resolved this email
    entry, or None. Resolves only when a meeting title shares a distinctive
    (non-generic) token with the counterparty/subject AND the meeting is dated
    on/after the entry's last email (a meeting before the email cannot have
    addressed it). Under-resolving is the safe failure: the entry stays visible."""
    if not meetings:
        return None
    gen = _generic()
    who = _tokens(f"{entry.get('counterparty_name', '')} {entry.get('subject', '')}")
    email = (entry.get("counterparty_email") or "").lower()
    domain = email.split("@")[-1] if "@" in email else ""
    if domain:
        sld = domain.split(".")[-2] if domain.count(".") >= 1 else domain
        if sld:
            who.add(sld)
    reply_age = entry.get("reply_age_days")
    age = entry.get("age_days")
    days_back = reply_age if reply_age is not None else (age or 0)
    last_email_date = today_date - timedelta(days=days_back)
    best = None
    best_date = None
    for mtg in meetings:
        try:
            mdate = datetime.strptime(mtg["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue
        if mdate < last_email_date:
            continue
        distinctive = (_tokens(mtg.get("title", "")) & who) - gen
        if any(len(t) >= 4 for t in distinctive) or len(distinctive) >= 2:
            if best is None or mdate > best_date:
                best, best_date = mtg, mdate
    return best


# ── Section C: Outlook Sent Mail ──────────────────────────────────────────────

def fetch_outlook_deals(label, sent_folder_id, account_email, since_days):
    today_utc = datetime.now(timezone.utc)
    cutoff = (today_utc - timedelta(days=since_days)).strftime("%Y-%m-%d")

    mc = microsoft_client(label)
    data = mc.folder_messages(
        sent_folder_id,
        filter=f"sentDateTime ge {cutoff}T00:00:00Z",
        select="id,subject,sentDateTime,toRecipients,conversationId,bodyPreview",
        top=100,
    )

    by_conv = {}
    for msg in data.get("value", []):
        cid = msg.get("conversationId")
        if not cid:
            continue
        subject = msg.get("subject", "")
        if is_warmup(subject) or is_calendar_noise(subject) or is_noisy_subject(subject):
            continue
        if cid not in by_conv or msg.get("sentDateTime", "") > by_conv[cid].get("sentDateTime", ""):
            by_conv[cid] = msg

    # Snapshot latest sentDateTime per conversation — used by the inbox sweep
    # to detect which inbound threads the user has already answered.
    sent_conv_dt = {cid: m.get("sentDateTime", "") for cid, m in by_conv.items()}

    def check_replies(item):
        cid, sent_msg = item
        sent_dt_str = sent_msg.get("sentDateTime", "")
        try:
            sent_dt = parse_iso(sent_dt_str)
        except Exception:
            return None

        counterparty = None
        for r in sent_msg.get("toRecipients", []):
            ea = r.get("emailAddress", {})
            addr = ea.get("address", "")
            name = ea.get("name", "")
            if not is_spam(addr) and not is_self(addr, account_email):
                counterparty = {"name": name, "email": addr}
                break
        if not counterparty:
            return None

        filter_str = f"conversationId eq '{cid}' and receivedDateTime gt '{sent_dt_str}'"
        try:
            reply_data = mc.messages(
                filter=filter_str,
                select="id,receivedDateTime,from,bodyPreview",
                top=5,
            )
        except Exception:
            reply_data = {"value": []}

        replies = [
            r for r in reply_data.get("value", [])
            if not is_spam(r.get("from", {}).get("emailAddress", {}).get("address", ""))
            and not is_self(r.get("from", {}).get("emailAddress", {}).get("address", ""), account_email)
        ]

        age_days = (today_utc - sent_dt).days
        entry = {
            "source": label,
            "subject": sent_msg.get("subject", ""),
            "counterparty_name": counterparty["name"],
            "counterparty_email": counterparty["email"],
            "age_days": age_days,
            "is_internal": is_internal(counterparty["email"]),
        }

        if replies:
            latest_reply = max(replies, key=lambda r: r.get("receivedDateTime", ""))
            try:
                reply_dt = parse_iso(latest_reply.get("receivedDateTime", sent_dt_str))
                entry["reply_age_days"] = (today_utc - reply_dt).days
            except Exception:
                entry["reply_age_days"] = age_days
            entry["body_preview"] = trim_body(latest_reply.get("bodyPreview", ""))
            return ("waiting", entry)
        elif age_days > 14:
            return ("cold_urgent", entry)
        elif age_days > 7:
            return ("cold_monitor", entry)
        return None  # recent — dropped

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check_replies, list(by_conv.items())))

    waiting_on_user, cold_urgent, cold_monitor = [], [], []
    for result in results:
        if result is None:
            continue
        category, entry = result
        if category == "waiting":
            waiting_on_user.append(entry)
        elif category == "cold_urgent":
            cold_urgent.append(entry)
        elif category == "cold_monitor":
            cold_monitor.append(entry)

    return waiting_on_user, cold_urgent, cold_monitor, sent_conv_dt


# ── Section C2: Outlook Inbox Sweep (unanswered inbound) ──────────────────────

def fetch_outlook_inbox_pending(label, sent_folder_id, account_email, since_days, sent_conv_dt):
    """Surface inbound Outlook threads where the user hasn't replied yet.

    Catches threads invisible to fetch_outlook_deals — the ones where the user
    has never sent in the conversation, or where the counterparty's latest
    message is newer than the user's last reply.
    """
    today_utc = datetime.now(timezone.utc)
    cutoff = (today_utc - timedelta(days=since_days)).strftime("%Y-%m-%d")

    mc = microsoft_client(label)
    try:
        data = mc.messages(
            filter=f"receivedDateTime ge {cutoff}T00:00:00Z",
            select="id,subject,receivedDateTime,from,conversationId,bodyPreview",
            top=999,
        )
    except Exception:
        return []

    by_conv = {}
    for msg in data.get("value", []):
        cid = msg.get("conversationId")
        if not cid:
            continue
        sender_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "")
        if not sender_addr or is_spam(sender_addr) or is_self(sender_addr, account_email):
            continue
        # Skip internal team messages when choosing the conversation anchor.
        # The action item lives with the external counterparty (e.g., deal
        # counsel at the counterparty's domain), not the internal team member
        # who replied most recently.
        if is_internal(sender_addr):
            continue
        subject = msg.get("subject", "") or ""
        if is_warmup(subject) or is_calendar_noise(subject) or is_noisy_subject(subject):
            continue
        existing = by_conv.get(cid)
        if not existing or msg.get("receivedDateTime", "") > existing.get("receivedDateTime", ""):
            by_conv[cid] = msg

    # Build a quick map of cid -> the user's last sent bodyPreview so we can
    # detect placeholder replies (short "will review and get back" notes
    # that should NOT count as substantive answers).
    cutoff_sent = (today_utc - timedelta(days=since_days)).strftime("%Y-%m-%d")
    sent_bodies = {}
    try:
        sb = mc.folder_messages(
            sent_folder_id,
            filter=f"sentDateTime ge {cutoff_sent}T00:00:00Z",
            select="conversationId,sentDateTime,bodyPreview",
            top=200,
        )
        for m in sb.get("value", []):
            cid = m.get("conversationId")
            if not cid:
                continue
            existing = sent_bodies.get(cid)
            if not existing or m.get("sentDateTime", "") > existing.get("sentDateTime", ""):
                sent_bodies[cid] = m
    except Exception:
        pass

    pending = []
    for cid, latest_inbound in by_conv.items():
        latest_inbound_dt_str = latest_inbound.get("receivedDateTime", "")
        latest_sent_dt = sent_conv_dt.get(cid, "")
        if latest_sent_dt and latest_sent_dt > latest_inbound_dt_str:
            # the user sent after the latest inbound — but check whether that
            # reply was just a placeholder acknowledgment. If so, the thread
            # still needs substantive follow-up.
            sent_msg = sent_bodies.get(cid, {})
            if not is_placeholder_reply(sent_msg.get("bodyPreview", "")):
                continue
            # else: fall through and surface as pending

        sender = latest_inbound.get("from", {}).get("emailAddress", {})
        try:
            recv_dt = parse_iso(latest_inbound_dt_str)
            age = (today_utc - recv_dt).days
        except Exception:
            age = 0

        pending.append({
            "source": label,
            "subject": latest_inbound.get("subject", ""),
            "counterparty_name": sender.get("name", ""),
            "counterparty_email": sender.get("address", ""),
            "age_days": age,
            "reply_age_days": age,
            "body_preview": trim_body(latest_inbound.get("bodyPreview", "") or ""),
            # Wider raw slice for the kill/pass scan (body_preview is trimmed to
            # 150, which can cut a forwarded pass's preamble). Popped after scan.
            "kill_text": (latest_inbound.get("bodyPreview", "") or "")[:500],
            "inbound_only": True,
            "is_internal": is_internal(sender.get("address", "")),
        })
    return pending


# ── Sections D+E: Gmail Sent Mail (shared) ────────────────────────────────────

def _fetch_gmail_account_deals(gclient, account_email, source_label, since_days):
    """Fetch and classify sent-mail threads for any Gmail account."""
    today_utc = datetime.now(timezone.utc)
    cutoff_str = (today_utc - timedelta(days=since_days)).strftime("%Y/%m/%d")

    data = gclient.gmail.users().threads().list(
        userId="me",
        q=f"in:sent after:{cutoff_str}",
        maxResults=50,
    ).execute()
    threads = data.get("threads", [])

    def get_thread(stub):
        thread_id = stub.get("id")
        if not thread_id:
            return None
        try:
            result = gclient.gmail.users().threads().get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            return result, stub.get("snippet", "")
        except Exception:
            return None

    fetched = [get_thread(stub) for stub in threads]

    waiting_on_user, cold_urgent, cold_monitor = [], [], []

    for item in fetched:
        if item is None:
            continue
        thread_data, snippet = item
        messages = thread_data.get("messages", [])
        if not messages:
            continue

        parsed = []
        for m in messages:
            hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            from_addr = hdrs.get("From", "")
            date_str = hdrs.get("Date", "")
            subject = hdrs.get("Subject", "")
            labels = m.get("labelIds", [])
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str).astimezone(timezone.utc)
            except Exception:
                continue
            parsed.append({"from": from_addr, "dt": dt, "subject": subject, "labels": labels})

        if not parsed:
            continue

        user_msgs = [
            p for p in parsed
            if is_self(p["from"], account_email) or "SENT" in p["labels"]
        ]
        if not user_msgs:
            continue

        latest_user = max(user_msgs, key=lambda x: x["dt"])
        latest_overall = max(parsed, key=lambda x: x["dt"])

        counterparty_name = ""
        counterparty_email = ""
        for p in parsed:
            if not is_self(p["from"], account_email):
                m = re.search(r"<([^>]+)>", p["from"])
                if m:
                    counterparty_email = m.group(1)
                    counterparty_name = p["from"][: p["from"].index("<")].strip().strip('"')
                else:
                    counterparty_email = p["from"].strip()
                    counterparty_name = ""
                break

        if not counterparty_email or is_spam(counterparty_email):
            continue

        subject = latest_user["subject"] or latest_overall["subject"]
        if is_warmup(subject) or is_calendar_noise(subject) or is_noisy_subject(subject):
            continue
        age_days = (today_utc - latest_user["dt"]).days
        entry = {
            "source": source_label,
            "subject": subject,
            "counterparty_name": counterparty_name,
            "counterparty_email": counterparty_email,
            "age_days": age_days,
            "is_internal": is_internal(counterparty_email),
        }

        if not is_self(latest_overall["from"], account_email):
            reply_age = (today_utc - latest_overall["dt"]).days
            entry["reply_age_days"] = reply_age
            entry["body_preview"] = trim_body(snippet)
            waiting_on_user.append(entry)
        elif age_days > 14:
            cold_urgent.append(entry)
        elif age_days > 7:
            cold_monitor.append(entry)
        # recent dropped

    return waiting_on_user, cold_urgent, cold_monitor


def _fetch_gmail_account_inbox_pending(gclient, account_email, source_label, since_days):
    """Find Gmail/secondary inbox threads where the user hasn't replied (latest
    thread message is from the counterparty, not the user)."""
    today_utc = datetime.now(timezone.utc)
    cutoff_str = (today_utc - timedelta(days=since_days)).strftime("%Y/%m/%d")

    try:
        data = gclient.gmail.users().threads().list(
            userId="me",
            q=f"in:inbox -in:sent -in:spam -category:promotions -category:social after:{cutoff_str}",
            maxResults=100,
        ).execute()
    except Exception:
        return []
    threads = data.get("threads", [])

    def get_thread(stub):
        thread_id = stub.get("id")
        if not thread_id:
            return None
        try:
            result = gclient.gmail.users().threads().get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            return result, stub.get("snippet", "")
        except Exception:
            return None

    fetched = [get_thread(stub) for stub in threads]

    from email.utils import parsedate_to_datetime
    pending = []
    for item in fetched:
        if item is None:
            continue
        thread_data, snippet = item
        messages = thread_data.get("messages", [])
        if not messages:
            continue

        parsed = []
        for m in messages:
            hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            from_addr = hdrs.get("From", "")
            date_str = hdrs.get("Date", "")
            subject = hdrs.get("Subject", "")
            labels = m.get("labelIds", [])
            try:
                dt = parsedate_to_datetime(date_str).astimezone(timezone.utc)
            except Exception:
                continue
            parsed.append({"from": from_addr, "dt": dt, "subject": subject, "labels": labels})

        if not parsed:
            continue

        latest = max(parsed, key=lambda x: x["dt"])
        # If the user sent the latest message, thread is answered — UNLESS that
        # reply was a placeholder ("will review and get back to you"), in
        # which case treat the prior counterparty message as still pending.
        if is_self(latest["from"], account_email) or "SENT" in latest["labels"]:
            if not is_placeholder_reply(snippet):
                continue
            non_user = [
                p for p in parsed
                if not (is_self(p["from"], account_email) or "SENT" in p["labels"])
            ]
            if not non_user:
                continue
            latest = max(non_user, key=lambda x: x["dt"])

        # If the most recent message is from an internal team member, walk
        # back to the latest external counterparty message — that's where
        # the action item lives.
        def _addr_from_header(s):
            mm = re.search(r"<([^>]+)>", s or "")
            return (mm.group(1) if mm else (s or "")).strip().lower()
        if is_internal(_addr_from_header(latest["from"])):
            external = [
                p for p in parsed
                if not is_internal(_addr_from_header(p["from"]))
                and not (is_self(p["from"], account_email) or "SENT" in p["labels"])
            ]
            if not external:
                continue
            latest = max(external, key=lambda x: x["dt"])

        sender_str = latest["from"]
        m_em = re.search(r"<([^>]+)>", sender_str)
        if m_em:
            sender_email = m_em.group(1)
            sender_name = sender_str[: sender_str.index("<")].strip().strip('"')
        else:
            sender_email = sender_str.strip()
            sender_name = ""

        if not sender_email or is_spam(sender_email):
            continue
        subject = latest["subject"] or ""
        if is_warmup(subject) or is_calendar_noise(subject):
            continue

        age = (today_utc - latest["dt"]).days
        pending.append({
            "source": source_label,
            "subject": subject,
            "counterparty_name": sender_name,
            "counterparty_email": sender_email,
            "age_days": age,
            "reply_age_days": age,
            "body_preview": trim_body(snippet),
            # Wider raw slice for the kill/pass scan (body_preview is trimmed to
            # 150). Popped after the scan to keep the output slim.
            "kill_text": (snippet or "")[:500],
            "inbound_only": True,
            "is_internal": is_internal(sender_email),
        })
    return pending


# ── Section F: Haiku Email Classification ────────────────────────────────────

def _build_classifier_prompt(items):
    """Build the Haiku classification prompt using the configured persona."""
    from config_loader import user_name, cfg
    _user = user_name()
    _role = cfg().get("user", {}).get("role_description", "")
    _persona = f"{_user}" + (f" ({_role})" if _role else "")

    # The buckets and, where the user has set them, the priorities inside each.
    # This is what lets the classifier put an item in the right bucket when no
    # configured keyword fired. It never overrules a keyword hit.
    _bucket_lines = []
    for b in config_loader.businesses():
        tag = b.get("tag")
        prios = [p.get("name", "") for p in config_loader.business_priorities(tag) if p.get("name")]
        line = f"  {tag}: {b.get('display_name', tag)}"
        if prios:
            line += " | priorities: " + "; ".join(prios)
        _bucket_lines.append(line)
    _buckets_block = ""
    if _bucket_lines:
        _buckets_block = (
            "\n\nThese are the buckets this person organizes work into"
            + (", with the priorities they have stated inside each"
               if any("priorities:" in ln for ln in _bucket_lines) else "")
            + ":\n" + "\n".join(_bucket_lines)
            + "\n\nUse the tag verbatim for \"bucket\". Use \"unassigned\" when no bucket "
              "clearly fits; a wrong bucket is worse than none. Set \"priority_matched\" to "
              "the priority text verbatim only when the item is genuinely about it, else \"\"."
        )

    # The function axis: which kind of work this is, inside whatever business
    # it belongs to. A closed vocabulary for the same reason the bucket tag is
    # one: a name the model invented renders a heading nobody configured.
    _functions = briefing_functions()
    _functions_csv = ", ".join(_functions)
    _functions_block = (
        "\n\nFile each item under one kind of work, using \"function\", chosen "
        f"only from this list: {_functions_csv}. Sales is winning or keeping "
        "customers and deals. Finance is money in and out of the business, "
        "raising it and moving it. Accounting is invoices, expenses and the "
        "books. Legal is contracts, redlines and signatures. People is hiring, "
        "payroll and the team. Operations is delivering the work: vendors, "
        "sites, permits, schedules. Product is what you build. Admin is the "
        "logistics of a working day. Use Other when none of them is clearly "
        "right; a wrong function is worse than Other."
    )

    return (
        f"Classify each email reply sent to {_persona}. "
        "Return a JSON array only, no other text.\n\n"
        + json.dumps(items)
        + _buckets_block
        + _functions_block
        + "\n\nFor each item return exactly:\n"
        '{"id": <int>, '
        '"summary": "<1 sentence: what they actually said, cleaned of quoted text and signatures>", '
        '"intent": "<action_required|deal_signal|decision_point|awaiting_their_move|intro|fyi|closing|stakeholder_feedback>", '
        '"urgency": "<high|medium|low>", '
        f'"suggested_action": "<1 sentence: what {_user.split()[0]} should do next, or No action needed>", '
        '"suppress": <true only if purely fyi or closing with no question or ask, false otherwise>, '
        '"bucket": "<a tag from the list above, or unassigned>", '
        f'"function": "<one of: {_functions_csv}>", '
        '"priority_matched": "<a stated priority verbatim, or empty string>"}'
        "\n\nUse intent 'stakeholder_feedback' for generic feedback / survey / "
        "RTO / town-hall / all-hands requests that are potentially useful but are "
        "NOT a deal action; never rate stakeholder_feedback urgency above 'low'."
    )


def _classify_batch(items):
    """Run Haiku on one batch of items. Returns {id: classification_dict}."""
    prompt = _build_classifier_prompt(items)
    try:
        result = run_claude(prompt, timeout=90)
        if result.returncode != 0:
            return {}
        raw = result.stdout.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        classifications = json.loads(raw)
        return {c["id"]: c for c in classifications if isinstance(c, dict) and "id" in c}
    except Exception:
        return {}


def classify_threads(threads, batch_size=25):
    """Batch-classify threads via Claude Haiku (chunks of `batch_size`).

    Returns (kept, filtered) tuple:
      kept     = threads to surface in the briefing
      filtered = threads Haiku tagged suppress=true (kept for audit, not dropped)

    Batches in chunks of 25 items because a single prompt with 100+ items
    blows past Haiku's reliable JSON-output window — it returns truncated or
    malformed responses and the fail-open path then surfaces everything
    unclassified. Chunks run in parallel.

    Deal-critical senders (DEAL_CRITICAL_DOMAINS) always go to `kept` regardless
    of Haiku's call, with urgency bumped to at least 'medium'. Same defensive
    allowlist pattern as INTERNAL_TEAM_EMAILS — a misclassification must
    never silently drop a known partner thread.
    """
    if not threads:
        return [], []

    # Defense in depth: drop any warmup subjects that slipped through.
    threads = [t for t in threads if not is_warmup(t.get("subject", ""))]
    if not threads:
        return [], []

    items = [
        {
            "id": i,
            "subject": t.get("subject", ""),
            "from": t.get("counterparty_name") or t.get("counterparty_email", ""),
            "snippet": t.get("body_preview", "")[:300],
        }
        for i, t in enumerate(threads)
    ]

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    by_id = {}
    with ThreadPoolExecutor(max_workers=min(6, len(batches))) as pool:
        for chunk in pool.map(_classify_batch, batches):
            by_id.update(chunk)

    kept, filtered = [], []
    for i, t in enumerate(threads):
        c = by_id.get(i, {})
        t["summary"] = c.get("summary", "")
        t["intent"] = c.get("intent", "")
        t["urgency"] = c.get("urgency", "")
        t["suggested_action"] = c.get("suggested_action", "")
        suppress = bool(c.get("suppress", False))

        # Bucket membership: the configured keyword wins, always. The model
        # only fills a gap, and only with a tag that actually exists in the
        # user's config, because a tag it invented would render a heading
        # nobody configured and hide the item under it.
        bucket = assign_bucket(t)
        if bucket == _BUCKET_UNASSIGNED:
            guess = (c.get("bucket") or "").strip()
            if guess and guess in config_loader.business_tags():
                bucket = guess
        t["bucket"] = bucket
        # Same rule as the bucket tag: the model's answer counts only when it
        # names a function the user actually configured. assign_function falls
        # back to the keyword table, so nothing is left unfiled.
        t["function"] = assign_function(
            {**t, "function": (c.get("function") or "").strip()})
        t["priority_matched"] = (c.get("priority_matched") or "").strip()
        if not t["priority_matched"] and bucket != _BUCKET_UNASSIGNED:
            t["priority_matched"] = match_priority(
                t, config_loader.business_priorities(bucket))

        # Overreach guard (Item 11): stakeholder/RTO/feedback is potentially
        # useful but never a deal action, so cap its urgency at low so it can't
        # be promoted to a red/high priority. Detect via Haiku's intent or subject.
        if t["intent"] == "stakeholder_feedback" or is_stakeholder_feedback(t.get("subject", "")):
            t["intent"] = "stakeholder_feedback"
            t["urgency"] = "low"

        # Allowlist override: deal-critical senders always surface tier-1.
        if is_deal_critical(t.get("counterparty_email", "")):
            suppress = False
            if t["urgency"] == "low" or not t["urgency"]:
                t["urgency"] = "medium"
            t["allowlisted"] = True

        t["suppress"] = suppress
        if suppress:
            filtered.append(t)
        else:
            kept.append(t)
    return kept, filtered


# Back-compat alias.
def classify_waiting_threads(threads):
    kept, _ = classify_threads(threads)
    return kept


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def load_active_thread_emails():
    """Return the set of counterparty emails named under the hotcache Active
    Threads heading (the user's live, actively-managed deals).

    Used by the overreach guard (Item 11): a cold counterparty NOT in this set
    has no live deal and should not be promoted to red. Returns an empty set if
    hotcache or the section is absent, which makes the guard purely additive
    (thin-flow installs simply have few/no active threads)."""
    if not os.path.exists(HOTCACHE_PATH):
        return set()
    from config_loader import hotcache_active_threads_heading
    heading = hotcache_active_threads_heading()
    heading_re = re.compile(r"^##\s+" + re.escape(heading) + r"\s*$")
    try:
        with open(HOTCACHE_PATH, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return set()
    emails = set()
    in_section = False
    for line in content.splitlines():
        if heading_re.match(line):
            in_section = True
            continue
        if in_section and re.match(r"^## ", line):
            break
        if in_section:
            for m in _EMAIL_RE.finditer(line):
                emails.add(m.group(0).strip().lower())
    return emails


def apply_priority_judgment(entries: list, judge=None) -> dict:
    """Ask the judgment layer which of these items serve a stated priority.

    Groups by bucket, one call per bucket that has priorities and items, and
    writes the verdict onto each entry. Returns a summary the briefing can show
    the reader, including whether the layer was degraded, because a page built
    without it must say so rather than look confident.

    Nothing here demotes anything. It records judgments; the gate decides.
    """
    summary = {"degraded": False, "degraded_reasons": [], "judged": 0, "buckets": []}
    if not priority_judge.judge_enabled():
        return summary

    by_bucket = {}
    for e in entries:
        tag = e.get("bucket") or assign_bucket(e)
        e["bucket"] = tag
        if tag != _BUCKET_UNASSIGNED:
            by_bucket.setdefault(tag, []).append(e)

    for tag, items in by_bucket.items():
        prios = config_loader.business_priorities(tag)
        if not prios:
            continue
        bucket = {"display_name": next(
            (b.get("display_name", tag) for b in config_loader.businesses()
             if b.get("tag") == tag), tag), "priorities": prios}
        result = priority_judge.judge_bucket(bucket, items, run=judge)
        if result["degraded"]:
            summary["degraded"] = True
            summary["degraded_reasons"].append(result["degraded_reason"])
            continue
        summary["buckets"].append(tag)
        for idx, verdict in result["verdicts"].items():
            items[idx]["judged_relevant"] = verdict["relevant"]
            items[idx]["judged_reason"] = verdict["reason"]
            if verdict["priority"]:
                items[idx]["priority_matched"] = verdict["priority"]
            summary["judged"] += 1
    return summary


def gate_cold_promotion(output, active_emails, judgment=None):
    """Overreach guard (Item 11): keep low-signal mail out of the red bucket.

    Runs after fetch + classification, before sort/emit, on both Tier 2 and
    Tier 1 paths. Three demotions, all additive (a genuinely active deal thread
    is untouched):
      1. Spam / newsletter / warmup that slipped a fetch-stage filter is pulled
         out of the cold buckets entirely → filtered_pending. Spam is never red.
      2. RTO / stakeholder-feedback subjects are demoted cold_urgent → cold_monitor.
      3. A cold_urgent counterparty with no live deal in hotcache (Active Threads)
         and age >= STALE_COUNTERPARTY_DAYS is demoted cold_urgent → cold_monitor.
    """
    surviving_urgent, surviving_monitor = [], []
    judgment = judgment or {}
    judgment_degraded = bool(judgment.get("degraded"))

    # GUARD: a bucket where nothing at all was found relevant is a bucket whose
    # priorities are worded in a way this cannot read. "Grow the pipeline" and
    # "redline attached, need signature Friday" are the same thing to a person
    # and share no words; if that gap swallowed every item, the honest move is
    # to hide nothing in that bucket rather than hand back an empty page that
    # looks like a quiet week.
    blind_buckets = set()
    seen_by_bucket = {}
    for entry in output["cold_urgent"]:
        # Resolve the bucket HERE, not lazily in the loop below: the guard has
        # to see every item's bucket before the first demotion decision, or it
        # is computed over an empty set and never fires.
        tag = entry.get("bucket") or assign_bucket(entry)
        entry["bucket"] = tag
        if tag == _BUCKET_UNASSIGNED:
            continue
        prios = config_loader.business_priorities(tag)
        if not prios:
            continue
        judged = entry.get("judged_relevant")
        relevant = (judged if judged is not None
                    else bool(entry.get("priority_matched")
                              or match_priority(entry, prios)))
        seen_by_bucket.setdefault(tag, False)
        if relevant:
            seen_by_bucket[tag] = True
    blind_buckets = {tag for tag, hit in seen_by_bucket.items() if not hit}

    def _is_noise(entry):
        subj = entry.get("subject", "")
        email = entry.get("counterparty_email", "")
        return is_spam(email) or is_warmup(subj) or is_noisy_subject(subj)

    for entry in output["cold_urgent"]:
        if _is_noise(entry):
            output["filtered_pending"].append(entry)
            continue
        subj = entry.get("subject", "")
        email = (entry.get("counterparty_email") or "").strip().lower()
        if is_stakeholder_feedback(subj):
            surviving_monitor.append(entry)
            continue
        if email not in active_emails and entry.get("age_days", 0) >= STALE_COUNTERPARTY_DAYS:
            surviving_monitor.append(entry)
            continue
        # 4. An item that belongs to no bucket, or to a bucket whose stated
        #    priorities it does not touch, is not red. This is the demotion
        #    that answers "it keeps showing me the RFP I am not leading
        #    instead of the two deals I said I am closing".
        #
        #    Three exemptions, each because something deterministic already
        #    knows better than a keyword list: an allowlisted deal-critical
        #    sender, a counterparty with a live deal in hotcache, and an
        #    install with no buckets configured at all. A bucket with no
        #    priorities set is also skipped for the priority half, or every
        #    item in it would be demoted for a setting the user never made.
        if (not entry.get("allowlisted") and email not in active_emails
                and config_loader.businesses() and not judgment_degraded
                and not priority_judge.has_deadline(entry)):
            bucket = entry.get("bucket") or assign_bucket(entry)
            # An item we could not place is NOT demoted. "Unassigned" means the
            # keywords did not reach it, which is our failure to categorize,
            # not evidence the user does not care. Hiding it would punish them
            # for a gap in a list nobody's keyword list is ever complete on,
            # and it is the same silent-hiding failure the guards above exist
            # to prevent, arriving by a different door. It already renders in
            # its own section, so it is not competing with the priorities.
            if bucket == _BUCKET_UNASSIGNED:
                surviving_urgent.append(entry)
                continue
            prios = config_loader.business_priorities(bucket)
            if prios and bucket not in blind_buckets:
                judged = entry.get("judged_relevant")
                matched = (entry.get("priority_matched")
                           or match_priority(entry, prios))
                relevant = judged if judged is not None else bool(matched)
                if not relevant:
                    names = ", ".join(p.get("name", "") for p in prios if p.get("name"))
                    shown = config_loader.business_display_name(bucket)
                    entry["demoted_because"] = (
                        entry.get("judged_reason")
                        or f"dropped down, since this is not one of your "
                           f"{shown} priorities: {names}")
                    surviving_monitor.append(entry)
                    continue
        surviving_urgent.append(entry)

    for entry in output["cold_monitor"]:
        if _is_noise(entry):
            output["filtered_pending"].append(entry)
            continue
        # Same test, same sentence, so that carrying no note means the item IS
        # about a stated priority rather than meaning nothing at all. It only
        # ever stamped an item demoted OUT of urgent, so an equally
        # non-priority item that was already in monitor carried nothing, and a
        # reader took that silence as the opposite verdict.
        if not entry.get("demoted_because") and not entry.get("allowlisted"):
            tag = entry.get("bucket") or assign_bucket(entry)
            entry["bucket"] = tag
            prios = (config_loader.business_priorities(tag)
                     if tag != _BUCKET_UNASSIGNED else [])
            if prios and tag not in blind_buckets:
                judged = entry.get("judged_relevant")
                matched = (entry.get("priority_matched")
                           or match_priority(entry, prios))
                if not (judged if judged is not None else bool(matched)):
                    names = ", ".join(p.get("name", "") for p in prios
                                      if p.get("name"))
                    shown = config_loader.business_display_name(tag)
                    entry["demoted_because"] = (
                        entry.get("judged_reason")
                        or f"not one of your {shown} priorities: {names}")
        surviving_monitor.append(entry)

    output["cold_urgent"] = surviving_urgent
    output["cold_monitor"] = surviving_monitor


# ── Priority buckets ──────────────────────────────────────────────────────────
#
# A bucket is one configured business: a venture, an advisory client, a company,
# a board seat, "personal". Whatever the user says it is. Every surfaced item
# lands in exactly one, and code decides which. The model gets to write the
# sentence inside a row and nothing else, because the complaint this whole layer
# answers ("it surfaces noise instead of what I told it I care about") is a
# membership problem, not a wording problem.

_BUCKET_UNASSIGNED = "unassigned"

# Fixed label vocabulary per briefing. A briefing may not invent a fourth label:
# a reader who has learned three words should never have to learn a fourth.
# "Older, still open" claimed to be about age and was not: the third label is
# assigned by which section an item came from, and those sections mean the user
# sent last and got no reply. So a ten day old item carried it beside a twenty
# one day old item reading "Waiting on you", and two items of identical age
# could carry opposite labels. It is about WHO is holding the thread, which is
# what the first two labels are about too.
_BUCKET_LABELS = {
    "morning-coffee": ("Late", "Waiting on you", "Waiting on them"),
    "week":           ("Late", "Waiting on you", "Waiting on them"),
    "afternoon-tea":  ("Done today", "Waiting on you", "Still open"),
}

# Everything below is read by someone who runs a business, not someone who
# reads code. "unassigned", "no keyword matched" and "other signal" are how a
# developer describes the data; they are not sentences a person says.
_LABEL_EVERYTHING_ELSE = "Everything else here"
_LABEL_NOTHING_MOVING = "nothing moving on this"
_LABEL_UNSORTED = "Couldn't place these"
# The old wording said these items "did not match any part of your work",
# which reads as a judgment about the invoice rather than an admission by the
# tool. It is the tool that failed to place them, and it should say so.
_LABEL_UNSORTED_WHY = "tell me where they belong and I will file them next time"

_BAND = "─" * 46
_DOT = "·"
# Wide enough for the longest label plus its colon and a space. A label
# that exactly fills the column renders with no gap after the colon.
_LABEL_COL = 20
_WRAP = 78


def assign_bucket(entry: dict) -> str:
    """Return the business tag this entry belongs to, or "unassigned".

    Deterministic: a configured keyword either appears in the entry's text or
    it does not. Nothing here guesses, because a wrong bucket is worse than no
    bucket: it hides an item under a heading the user was not reading.
    """
    haystack = " ".join([
        (entry.get("subject") or ""),
        (entry.get("counterparty_name") or ""),
        (entry.get("counterparty_email") or ""),
        (entry.get("body_preview") or ""),
    ])
    tag = config_loader.business_tag_for_text(haystack)
    if tag:
        return tag

    # No keyword matched. Before giving up, check the priorities themselves:
    # a keyword list is always incomplete, but the words someone used to write
    # down a priority are words they care about. Without this, an item titled
    # "Referral pipeline intro" lands in "not sorted" while the priority
    # "Build the referral pipeline" three inches above it reports nothing
    # moving. A reader who catches that once stops trusting the whole page.
    for b in config_loader.businesses():
        if match_priority(entry, b.get("priorities") or []):
            return b["tag"]
    return _BUCKET_UNASSIGNED


# The verbs and scaffolding a user writes around a priority. "Close QX Corp"
# and "Build the AB pipeline" are about QX Corp and the AB pipeline; the
# first word is how they phrased it, not what it is about. Dropping these
# BEFORE counting is what keeps "close" from matching every closing email.
_PRIORITY_STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "for", "with", "on", "in", "at", "by",
    "or", "our", "my", "get", "getting", "close", "closing", "build", "building",
    "ship", "shipping", "launch", "launching", "advance", "advancing", "finish",
    "finishing", "start", "starting", "keep", "keeping", "more", "new", "next",
    "up", "out", "over", "into", "work", "working", "push", "pushing",
}


def match_priority(entry: dict, priorities: list) -> str:
    """Return the name of the first configured priority this entry is about.

    Compares distinctive tokens, both sides tokenized, so a short but real
    token ("MT") counts and a substring collision ("mt" inside "amount")
    cannot. A user writes "Close QX Corp" and the mail says "QX Corp credit
    memo": that has to match, and an unrelated "closing thoughts" must not.

    Returns "" when nothing matches. That empty string is what the fourth
    demotion reads, so this leans toward saying no: a wrong priority heading
    is worse than an item sitting under Other signal in the right bucket.
    """
    hay = set(re.findall(r"[a-z0-9&]+", " ".join([
        (entry.get("subject") or ""),
        (entry.get("counterparty_name") or ""),
        (entry.get("body_preview") or ""),
    ]).lower()))
    for p in priorities:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        tokens = [w for w in re.findall(r"[a-z0-9&]+", name.lower())
                  if w not in _PRIORITY_STOPWORDS]
        if not tokens:
            continue
        present = [w for w in tokens if w in hay]
        if len(present) == len(tokens):
            return name
        # A single long distinctive token carries a match on its own; two
        # short ones have to both show up.
        if any(len(w) >= 6 for w in present) and len(present) >= 1:
            return name
    return ""


def group_by_bucket(output: dict) -> list:
    """Group the four surfaced sections into one list of buckets.

    Every configured business gets a bucket even when it is empty: a venture
    with nothing open is information, and silently omitting it reads as "no
    news" when it may mean "nothing is moving". "unassigned" is appended last
    and only when it has items.
    """
    sections = ("waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor")
    by_tag = {}
    for section in sections:
        for entry in output.get(section, []) or []:
            tag = entry.get("bucket") or assign_bucket(entry)
            by_tag.setdefault(tag, []).append((section, entry))

    buckets = []
    for b in config_loader.businesses():
        tag = b.get("tag")
        buckets.append({
            "tag": tag,
            "display_name": b.get("display_name", tag),
            "priorities": config_loader.business_priorities(tag),
            "items": [
                {**entry, "section": section} for section, entry in by_tag.get(tag, [])
            ],
        })
    leftovers = by_tag.get(_BUCKET_UNASSIGNED, [])
    if leftovers:
        buckets.append({
            "tag": _BUCKET_UNASSIGNED,
            "display_name": _LABEL_UNSORTED,
            "priorities": [],
            "items": [{**entry, "section": section} for section, entry in leftovers],
        })
    return buckets


def _label_for(entry: dict, briefing: str) -> str:
    """Which of the three labels this item renders under. Deterministic."""
    overdue, still_open, third = _BUCKET_LABELS[briefing]
    if entry.get("days_late") or entry.get("overdue"):
        return overdue
    if briefing == "afternoon-tea":
        if entry.get("done_today"):
            return overdue          # "Done today"
        if entry.get("section") in ("waiting_on_user", "inbox_pending"):
            return still_open       # "Open loop"
        return third                # "Still open"
    if entry.get("section") in ("waiting_on_user", "inbox_pending"):
        return still_open
    return third


def _sort_key(entry: dict):
    """Overdue first (most overdue on top), then due ascending, then age
    descending, ties alphabetical. Identical data must render identically."""
    late = int(entry.get("days_late") or 0)
    due = entry.get("due") or "9999-99-99"
    age = int(entry.get("age_days") or 0)
    return (-late, due, -age, (entry.get("subject") or "").lower())


def _item_line(entry: dict) -> str:
    """One item's text: title, then middle-dot fields only when they exist."""
    title = (entry.get("subject") or entry.get("title") or "").strip() or "(no subject)"
    parts = [title]
    who = (entry.get("counterparty_name") or "").strip()
    if who:
        parts.append(f"from {who}")
    age = entry.get("reply_age_days")
    if age is None:
        age = entry.get("age_days")
    if age is not None and not entry.get("days_late"):
        # Zero is a real age and used to render as nothing at all, so an item
        # that arrived this morning sat in a column of dated siblings with a
        # blank where its age should be.
        n = int(age)
        parts.append("today" if n == 0 else
                     "1 day ago" if n == 1 else f"{n} days ago")
    if entry.get("due"):
        parts.append(f"due {entry['due']}")
    if entry.get("format"):
        parts.append(str(entry["format"]))
    if entry.get("days_late"):
        n = int(entry["days_late"])
        parts.append(f"{n} day late" if n == 1 else f"{n} days late")
    line = f" {_DOT} ".join(parts)
    # Why it dropped, in the reader's own terms. A demotion nobody can see the
    # reason for is the silent hiding this whole layer exists to prevent.
    why = (entry.get("demoted_because") or "").strip()
    if why:
        line = f"{line}\n({why})"
    return line


def _wrap_bullet(text: str, indent: int, hanging: int, width: int = _WRAP) -> list:
    """Wrap one bullet with its continuations aligned under the text, never
    truncated: a title the user needs to recognize must survive whole."""
    words = text.split()
    lines, cur = [], ""
    first_width = width - indent
    rest_width = width - hanging
    for w in words:
        limit = first_width if not lines else rest_width
        candidate = f"{cur} {w}".strip()
        if cur and len(candidate) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur or not lines:
        lines.append(cur)
    out = [" " * indent + lines[0]]
    out += [" " * hanging + ln for ln in lines[1:]]
    return out


def _group_items(bucket: dict, by_function: bool = False) -> list:
    """Split one bucket's items into ordered (heading, items) groups.

    Stated priorities first, in the order the user wrote them, then whatever is
    left. `by_function` is the only difference between the terminal ledger and
    a fold: the fold breaks the leftover out under its function headings instead
    of a single "Everything else here", because a fold is read looking for a
    kind of work, not for a priority.
    """
    items = list(bucket.get("items") or [])
    priorities = bucket.get("priorities") or []
    claimed = set()
    groups = []
    for p in priorities:
        pname = (p.get("name") or "").strip()
        matched = []
        for idx, entry in enumerate(items):
            if idx in claimed:
                continue
            if (entry.get("priority_matched") or "") == pname or (
                    not entry.get("priority_matched")
                    and match_priority(entry, [p])):
                claimed.add(idx)
                # Remember which priority claimed it. A fold no longer has
                # priority headings, so this is the only place the item can say
                # what it is about, and it is the same field the classifier
                # fills when it runs.
                entry["priority_matched"] = pname
                matched.append(entry)
        groups.append((pname, matched))
    other = [e for i, e in enumerate(items) if i not in claimed]
    if by_function:
        # ONE partition, not two. A fold that grouped the priority-matched
        # items under a priority heading and the rest under function headings
        # gave the reader two different partitions of the same set: the row
        # said "Sales 3" and the heading behind it held 1. So a fold groups by
        # function over EVERY item, exactly the way the row counts them, and
        # the stated priorities lead inside their own function group and say
        # their name on the item line.
        by_fn = {}
        for entry in items:
            by_fn.setdefault(entry.get("function") or assign_function(entry),
                             []).append(entry)
        claimed_ids = {id(e) for _, matched in groups for e in matched}
        out = []
        for fname in briefing_functions():
            rows = by_fn.get(fname)
            if not rows:
                continue
            rows = ([e for e in rows if id(e) in claimed_ids]
                    + [e for e in rows if id(e) not in claimed_ids])
            out.append((fname, rows))
        return out
    if priorities:
        # Only meaningful when there are priorities for these to be other
        # than. In a bucket with none, it is a heading that says nothing.
        if other:
            groups.append((_LABEL_EVERYTHING_ELSE, other))
    else:
        groups.append(("", other))
    return groups


def _render_one_bucket(bucket: dict, briefing: str, mode: str = "terminal",
                       by_function: bool = False) -> list:
    """Render one bucket's body: the groups under its heading band.

    Extracted from `render_buckets_md` so the terminal ledger, the written
    file and a fold all draw from one renderer. With `by_function=False` the
    output is byte-identical to what this code produced before the front page
    existed, which is what the frozen snapshot in tests/snapshots checks.
    """
    labels = _BUCKET_LABELS[briefing]
    out = []
    for gname, gitems in _group_items(bucket, by_function=by_function):
        out.append("")
        if gname:
            out.append(gname)
        if not gitems:
            # A priority with nothing open is signal, not an empty row to
            # hide. It usually means the user is not actually working on
            # the thing they said mattered most.
            out.append(f"  {_DOT} {_LABEL_NOTHING_MOVING}" if mode == "terminal"
                       else f"- {_DOT} {_LABEL_NOTHING_MOVING}")
            continue
        for label in labels:
            rows = sorted([e for e in gitems if _label_for(e, briefing) == label],
                          key=_sort_key)
            # A label with nothing under it is omitted inside a group that
            # has items. The group itself is never omitted, and an empty
            # group still says so, so nothing disappears silently; this
            # only spares the reader three "none" rows per priority, which
            # is the difference between a briefing and a wall.
            if not rows:
                continue
            if mode == "md":
                out.append(f"**{label}**")
                for entry in rows:
                    item, _, why = _item_line(entry).partition("\n")
                    if by_function and (entry.get("priority_matched") or "").strip():
                        item = f"{item} {_DOT} on {entry['priority_matched']}"
                    out.append(f"- [ ] {item}")
                    if why:
                        out.append(f"  {why}")
            else:
                pad = (label + ":").ljust(_LABEL_COL)
                # The label column is part of the line, so it has to be
                # part of the width the wrap measures against; wrapping the
                # body alone overflows by exactly the column width.
                lead = 2 + _LABEL_COL
                for i, entry in enumerate(rows):
                    head = "  " + (pad if i == 0 else " " * _LABEL_COL)
                    item, _, why = _item_line(entry).partition("\n")
                    if by_function and (entry.get("priority_matched") or "").strip():
                        item = f"{item} {_DOT} on {entry['priority_matched']}"
                    wrapped = _wrap_bullet(f"{_DOT} [ ] {item}",
                                           indent=lead, hanging=lead + 4)
                    out.append(head + wrapped[0][lead:])
                    out.extend(wrapped[1:])
                    if why:
                        out.extend(_wrap_bullet(why, indent=lead + 4,
                                                hanging=lead + 4))
    return out


def render_buckets_md(buckets: list, briefing: str, mode: str = "terminal",
                      judgment: dict | None = None) -> str:
    """Render the bucket section. This whole section is code, not model output.

    mode "terminal" is the aligned column layout for the chat render; mode "md"
    is the written file, where zero-indent checkboxes let Obsidian render real
    clickable boxes instead of code-blocking the alignment.
    """
    if briefing not in _BUCKET_LABELS:
        raise ValueError(
            f"unknown briefing {briefing!r}; expected one of {sorted(_BUCKET_LABELS)}")
    if mode not in ("terminal", "md"):
        raise ValueError(f"unknown mode {mode!r}; expected 'terminal' or 'md'")
    labels = _BUCKET_LABELS[briefing]
    out = []

    # Say it at the top, before anything else, because a reader who does not
    # know the sorting was skipped will read a full list as a busy day.
    if (judgment or {}).get("degraded"):
        why = "; ".join((judgment or {}).get("degraded_reasons") or []) or "it did not run"
        out.append("Heads up: nothing was filtered out below, because "
                   f"{why}. You are seeing everything.")
        out.append("")

    # Only name buckets the reader can actually see. Naming one that was not
    # rendered ("you have not told me what matters in Personal", on a page with
    # no Personal section) reads as a string leaking out of the code.
    unset = [b.get("display_name") or b.get("tag") for b in buckets
             if b.get("tag") != _BUCKET_UNASSIGNED and not b.get("priorities")
             and b.get("items")]
    nothing_configured = not any(b.get("priorities") for b in buckets)

    for bucket in buckets:
        # Nothing to report and nothing configured to report against: printing
        # an empty heading here is scaffolding, not information, and on a fresh
        # install it pushed the only real item below two of them.
        if (not bucket.get("items") and not bucket.get("priorities")
                and bucket.get("tag") != _BUCKET_UNASSIGNED):
            continue
        name = (bucket.get("display_name") or bucket.get("tag") or "").upper()
        suffix = ""
        if bucket.get("tag") == _BUCKET_UNASSIGNED:
            if nothing_configured:
                name = "TODAY"
                suffix = ""
            else:
                suffix = f" {_DOT} {_LABEL_UNSORTED_WHY}"
        out.append(_BAND)
        out.append(f"{name}{suffix}")
        out.append(_BAND)

        out.extend(_render_one_bucket(bucket, briefing, mode))
        out.append("")

    if nothing_configured:
        out.append("")
        out.extend(_wrap_bullet(
            "Nothing is set up yet, so this is everything I found. Tell me what "
            "you actually work on and tomorrow's briefing sorts itself: run "
            "/van-gogh:update-settings", indent=0, hanging=0))
    elif unset:
        out.append("")
        which = ", ".join(unset)
        out.extend(_wrap_bullet(
            f"I am guessing at what matters in {which}. Tell me once and I will "
            "file it right from now on: run /van-gogh:update-settings",
            indent=0, hanging=0))

    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


# ── The front page ────────────────────────────────────────────────────────────
#
# Everything above renders the whole ledger. That is the right thing to keep and
# the wrong thing to open on: four businesses and a hundred open items is a wall,
# and the two questions a reader actually arrives with (what do I do this
# morning, and is the reply already written) are spread across all of it.
#
# So: a short page of at most seven items chosen by code, then one honest
# summary row per business, with the full ledger folded behind it. Nothing is
# deleted and nothing is hidden silently. Every folded item is counted in its
# row, and a late item is never folded.

# A fixed vocabulary, the same idea as the three labels above: a reader who has
# learned these words should never meet a tenth one the model invented.
FUNCTIONS_DEFAULT = ["Sales", "Finance", "Accounting", "Legal", "People",
                     "Operations", "Product", "Admin", "Other"]
FUNCTION_OTHER = "Other"

# Ordered, first hit wins, most specific first. Accounting sits above Finance
# and Legal above Sales on purpose: an invoice is bookkeeping before it is
# money, and a term sheet is a contract before it is a deal.
_FUNCTION_KEYWORDS = [
    ("Accounting", ["invoice", "receipt", "expense", "reimburs", "bookkeep",
                    "accounts payable", "accounts receivable", "quickbooks",
                    "statement of account", "past due balance"]),
    ("Legal", ["term sheet", "redline", "nda", "non-disclosure", "contract",
               "msa", "loi", "letter of intent", "agreement", "counsel",
               "indemn", "mipa", "lease", "amendment", "signature page",
               "countersign", "docusign", "closing set"]),
    ("People", ["candidate", "hiring", "recruit", "interview", "offer letter",
                "onboard", "resume", "headcount", "payroll", "benefits",
                "performance review", "finalists"]),
    ("Finance", ["financing", "loan", "capital call", "investor", "funding",
                 "budget", "wire", "bank", "debt", "equity raise", "valuation",
                 "term loan", "credit facility"]),
    ("Sales", ["rfp", "rfq", "proposal", "quote", "pricing", "demo",
               "pipeline", "prospect", "intro call", "customer", "renewal quote",
               "bid", "diligence list"]),
    ("Product", ["roadmap", "spec", "feature", "release notes", "bug",
                 "design review", "launch", "beta", "pricing page", "prototype"]),
    ("Operations", ["logistics", "vendor", "site visit", "site walk",
                    "construction", "permit", "procure", "shipment", "outage",
                    "interconnect", "schedule of work"]),
    ("Admin", ["reschedul", "travel", "flight", "hotel", "booking",
               "subscription", "password", "calendar invite", "expense policy",
               "conference registration"]),
]


def briefing_functions() -> list:
    """The configured function vocabulary, always ending in Other."""
    names = config_loader.briefing_functions()
    names = [n for n in names if str(n).strip()]
    if FUNCTION_OTHER not in names:
        names = names + [FUNCTION_OTHER]
    return names


def assign_function(entry: dict) -> str:
    """Which kind of work this item is. Never invents a name.

    Three sources in order: what the classifier returned (only if it names a
    configured function), a keyword table, then Other. Nothing is ever dropped
    for being unclassified; Other is a real row, not a bin.
    """
    allowed = briefing_functions()
    guess = (entry.get("function") or "").strip()
    if guess in allowed:
        return guess
    hay = " ".join([
        (entry.get("subject") or ""),
        (entry.get("body_preview") or ""),
    ]).lower()
    # The LONGEST matching keyword wins, not the first one in the table. First
    # hit wins filed "site walk" under Sales because "pricing" sat earlier in
    # the list, so the same kind of work landed in two different functions
    # depending on what else was in the subject line. A two word phrase is a
    # more specific claim than a single common word; the table order only
    # breaks a genuine tie.
    best_name, best_len = "", 0
    # User keywords (briefing.function_keywords in config.json) join the same
    # longest-match contest. They sit first, and the built-in table only
    # displaces a hit with a strictly longer one, so on a tie the user's word
    # wins — their vocabulary outranks ours for their own mail.
    user_table = list(config_loader.briefing_function_keywords().items())
    for name, words in user_table + _FUNCTION_KEYWORDS:
        if name not in allowed:
            continue
        hit = max((len(w) for w in words if w in hay), default=0)
        if hit > best_len:
            best_name, best_len = name, hit
    if best_name:
        return best_name
    return FUNCTION_OTHER if FUNCTION_OTHER in allowed else allowed[-1]


def _entry_key(entry: dict) -> tuple:
    return (normalize_subject(entry.get("subject") or ""),
            (entry.get("counterparty_email") or "").strip().lower())


def _entry_age(entry: dict) -> int:
    age = entry.get("reply_age_days")
    if age is None:
        age = entry.get("age_days")
    return int(age or 0)


def _is_waiting(entry: dict) -> bool:
    return entry.get("section") in ("waiting_on_user", "inbox_pending")


def is_stale_item(entry: dict, active_emails: set | None = None) -> bool:
    """Old, no live deal behind it, no date on it. Counted, never promoted."""
    if entry.get("days_late") or entry.get("due"):
        return False
    email = (entry.get("counterparty_email") or "").strip().lower()
    if email and email in (active_emails or set()):
        return False
    return _entry_age(entry) >= STALE_COUNTERPARTY_DAYS


_SIGNATURE_RE = re.compile(
    r"\b(signature|signatures|sign|signed|signing|execute|execution copy|"
    r"docusign|countersign)\b", re.IGNORECASE)
_DECISION_RE = re.compile(
    r"\b(decision|decide|approve|approval|go or no go|which option|"
    r"your call)\b", re.IGNORECASE)


def _action_for(entry: dict) -> str:
    """What the reader actually has to do. Drives whether a reply is drafted."""
    subject = entry.get("subject") or ""
    if not (entry.get("counterparty_email") or "").strip():
        return "task"
    if _SIGNATURE_RE.search(subject):
        return "signature"
    if _DECISION_RE.search(subject):
        return "decision"
    return "email reply"


def _why_for(entry: dict) -> str:
    """One clause saying why this is on the page, in the reader's own terms."""
    if entry.get("days_late"):
        n = int(entry["days_late"])
        return "1 day late" if n == 1 else f"{n} days late"
    if entry.get("due"):
        return f"due {entry['due']}"
    if entry.get("priority_matched"):
        return f"on {entry['priority_matched']}"
    n = _entry_age(entry)
    if n:
        return "1 day ago" if n == 1 else f"{n} days ago"
    return "open now"


def front_page(buckets: list, cap: int | None = None, alerts: dict | None = None,
               today=None, active_emails: set | None = None) -> list:
    """The at-most-`cap` items the reader should do first. Code decides.

    Rank, in order: late (most late first), dated (due soonest), waiting on the
    reader and tied to a stated priority (oldest first), other waiting on the
    reader (oldest first), then anything else still open (oldest first) so a
    quiet morning still has a page. Function never affects rank.

    Late items are never cut: the cap bounds how many NON-late items reach the
    page, so a day with nine late items shows nine of them and no filler.
    """
    del alerts, today                       # reserved: flags live on the rows
    if cap is None:
        cap = config_loader.briefing_front_page_cap()
    cap = max(3, min(9, int(cap)))
    active_emails = active_emails or set()

    late, tiers = [], {1: [], 2: [], 3: [], 4: []}
    for bucket in buckets:
        tag = bucket.get("tag")
        name = bucket.get("display_name") or tag
        for entry in bucket.get("items") or []:
            if is_stale_item(entry, active_emails):
                continue
            item = {
                "bucket_tag": tag,
                "bucket_name": name,
                "function": entry.get("function") or assign_function(entry),
                "label": _label_for(entry, "morning-coffee"),
                "why": _why_for(entry),
                "action": _action_for(entry),
                "account": entry.get("source") or "",
                "counterparty_email": entry.get("counterparty_email") or "",
                "counterparty_name": entry.get("counterparty_name") or "",
                "subject": entry.get("subject") or "",
                "days_late": int(entry.get("days_late") or 0),
                "due": entry.get("due") or "",
                "age_days": _entry_age(entry),
                "stale": False,
                "key": list(_entry_key(entry)),
            }
            subj = item["subject"].lower()
            if item["days_late"]:
                late.append(((-item["days_late"], subj), item))
            elif item["due"]:
                tiers[1].append(((item["due"], subj), item))
            elif _is_waiting(entry):
                relevant = entry.get("judged_relevant")
                if relevant is None:
                    relevant = bool(entry.get("priority_matched"))
                tiers[2 if relevant else 3].append(((-item["age_days"], subj), item))
            else:
                tiers[4].append(((-item["age_days"], subj), item))

    page = [item for _, item in sorted(late, key=lambda pair: pair[0])]
    room = max(0, cap - len(page))
    fill = []
    for tier in (1, 2, 3, 4):
        fill.extend(item for _, item in sorted(tiers[tier], key=lambda pair: pair[0]))
    return page + fill[:room]


def opening_counts(buckets: list, page: list,
                   active_emails: set | None = None) -> dict:
    """The numbers the opening sentence is allowed to say. All of them real.

    `on_you` and `on_them` split the page by who owes the next move. Rank is by
    date and does not consider direction, so a page can legitimately carry more
    items the counterparty owes than the reader does, and a sentence claiming
    all seven "need you first" is then false about most of them.
    """
    active_emails = active_emails or set()
    total = late = stale = 0
    for bucket in buckets:
        for entry in bucket.get("items") or []:
            total += 1
            if entry.get("days_late"):
                late += 1
            if is_stale_item(entry, active_emails):
                stale += 1
    theirs = _BUCKET_LABELS["morning-coffee"][2]
    on_them = sum(1 for i in page if i.get("label") == theirs)
    return {"open": total, "late": late, "front": len(page),
            "folded": total - len(page), "stale": stale,
            "on_you": len(page) - on_them, "on_them": on_them}


def opening_sentence(counts: dict) -> str:
    """Two sentences at most, under 60 words, at most three named numbers.

    A quiet morning and a broken scan have to read differently, so a run that
    found nothing says so in its own words rather than reporting zero of zero.
    """
    if not counts.get("open"):
        return "Nothing is open this morning. Either you are clear or the scan found nothing, and the run would have said so above."
    late = counts.get("late", 0)
    front = counts.get("front", 0)
    folded = counts.get("folded", 0)
    total = counts["open"]
    # Who owes the next move, said out loud. Without it a page that is mostly
    # waiting on other people still reads as a list of things to do, and four
    # sevenths of it is work the reader cannot do at all.
    on_them = counts.get("on_them")
    on_you = counts.get("on_you")
    split = ""
    if on_them:
        yours = ("one needs a reply from you" if on_you == 1
                 else f"{on_you} need a reply from you")
        theirs = ("one is waiting on someone else" if on_them == 1
                  else f"{on_them} are waiting on someone else")
        split = (f": {yours}, {theirs}" if on_you else
                 f", and every one of them is waiting on someone else")
    if late:
        if total == 1:
            head = "Your one open item is late"
        elif late == 1:
            head = f"One of your {total} open items is late"
        else:
            head = f"{late} of your {total} open items are late"
        rest = front - late
        if rest > 0:
            head += (" and one more comes next" if rest == 1
                     else f" and {rest} more come next")
        head += split
    elif total == 1:
        head = "You have one open item"
    elif front == 1:
        head = f"One of your {total} open items comes first{split}"
    else:
        head = f"{front} of your {total} open items come first{split}"
    tail = ""
    if folded == 1:
        tail = " The other one is filed below by business and function."
    elif folded > 1:
        tail = f" The other {folded} are filed below by business and function."
    return head + "." + tail


def bucket_summaries(buckets: list, front_keys: set,
                     alerts: dict | None = None) -> list:
    """One honest row per business: what is folded, broken out by function.

    At most two flags per row, because a row with four is a paragraph. The two
    that earn the space are a stated priority with nothing moving on it, and a
    contact behind a stated priority who has gone quiet.
    """
    alerts = alerts or {}
    quiet_by_tag = {}
    for item in alerts.get("newly_cold") or []:
        tag = item.get("bucket") or ""
        who = (item.get("counterparty_name") or item.get("name")
               or item.get("counterparty_email") or "").strip()
        days = int(item.get("age_days") or item.get("days") or 0)
        if tag and who:
            quiet_by_tag.setdefault(tag, []).append((who, days))

    rows = []
    for bucket in buckets:
        tag = bucket.get("tag")
        items = list(bucket.get("items") or [])
        folded = [e for e in items if tuple(_entry_key(e)) not in front_keys]
        counts = {}
        for entry in folded:
            fname = entry.get("function") or assign_function(entry)
            slot = counts.setdefault(fname, {"count": 0, "waiting": 0})
            slot["count"] += 1
            if _is_waiting(entry):
                slot["waiting"] += 1
        functions = [{"name": n, "count": counts[n]["count"],
                      "waiting": counts[n]["waiting"]}
                     for n in briefing_functions() if n in counts]

        flags = []
        for pname, gitems in _group_items(bucket):
            if pname and pname != _LABEL_EVERYTHING_ELSE and not gitems:
                flags.append(f"{pname}: nothing moving")
        for who, days in quiet_by_tag.get(tag, []):
            flags.append(f"{who} quiet {days} days, a priority contact")
        rows.append({
            "tag": tag,
            "display_name": bucket.get("display_name") or tag,
            "folded": len(folded),
            # The business's whole count, so the row can say "18 of 20" and
            # cannot be read as a total. Three of five cold readers read a bare
            # "CEDAR 18" as everything Cedar has open and then computed a
            # different grand total; one disclaimer line above the block did
            # not reach them, because a reader reads the row, not the preamble.
            "total": len(items),
            "functions": functions,
            "flags": flags[:2],
        })
    return rows


# ── Front page and fold rendering ────────────────────────────────────────────
#
# Same primitives as the ledger above (_BAND, _DOT, _LABEL_COL, _WRAP, the three
# labels): a second renderer is how two parts of one page start disagreeing.

def _tag_for(item: dict) -> str:
    return f"{(item.get('bucket_name') or '').upper()} {_DOT} {item.get('function') or ''}"


def _draft_note(item: dict) -> str:
    """The continuation line's left half: what is already done for this item."""
    note = (item.get("draft_note") or "").strip()
    if note:
        return note
    action = item.get("action")
    if action == "signature":
        return "signature only"
    if action == "task":
        return "no reply needed"
    if action == "decision":
        return "your call"
    # An undrafted reply says nothing the reader does not already know from the
    # item itself, and repeated down seven rows it is a column of noise. The
    # line earns its place once there is a draft to point at.
    return ""


def _right_align(left: str, right: str, indent: int, width: int = _WRAP) -> list:
    """Left text at `indent`, right text flush to `width`, never overlapping.

    A business name and a function name are both user-supplied, so the two
    halves can be long enough to collide. When they would, the tag drops to its
    own line rather than running into the note, which is the one thing a reader
    reads as a bug.
    """
    lead = " " * indent
    # A tag wider than the space it has cannot be pushed right without running
    # off the line, so it wraps at the indent like any other text instead.
    if len(right) > width - indent:
        wrapped = _wrap_bullet(right, indent=indent, hanging=indent, width=width)
        return ([lead + left] if left else []) + wrapped
    if not left:
        return [" " * (width - len(right)) + right]
    gap = width - indent - len(left) - len(right)
    if gap >= 2:
        return [lead + left + " " * gap + right]
    return [lead + left, " " * (width - len(right)) + right]


def render_front_page_md(page: list, counts: dict, briefing: str = "morning-coffee",
                         mode: str = "terminal", heading: str = "DO TODAY",
                         detail_cap: int | None = None) -> str:
    """The page a reader opens on. Same geometry as one bucket section.

    `detail_cap` is what keeps a genuinely bad morning on one screen. A late
    item is never folded, so a week with twenty of them puts twenty on the
    page; past the cap they render as the item line alone, without the
    "drafted 06:40, Outlook" continuation row. Nothing is hidden, the list just
    stops being a reading page and becomes a triage list, which is what a
    reader with twenty late items is doing anyway.
    """
    if briefing not in _BUCKET_LABELS:
        raise ValueError(f"unknown briefing {briefing!r}")
    if mode not in ("terminal", "md"):
        raise ValueError(f"unknown mode {mode!r}")
    labels = _BUCKET_LABELS[briefing]
    out = []
    out.extend(_wrap_bullet(opening_sentence(counts), indent=0, hanging=0))
    out.append("")
    if not page:
        out.append(_BAND)
        out.append(f"{heading}  {_DOT}  nothing needs you first")
        out.append(_BAND)
        return "\n".join(out) + "\n"

    if detail_cap is None:
        detail_cap = config_loader.briefing_front_page_cap()
    detailed = {id(i) for i in page[:detail_cap]}
    drafted = sum(1 for i in page if (i.get("draft_note") or "").strip())
    title = f"{heading}  {_DOT}  {len(page)} of {counts.get('open', len(page))}"
    if drafted:
        title += f"  {_DOT}  {drafted} replies drafted"
    if mode == "md":
        out.append(f"### {title}")
    else:
        out.append(_BAND)
        out.append(title)
        out.append(_BAND)

    lead = 2 + _LABEL_COL
    # Two real groups, in label order: what is late, then what needs a reply
    # from the reader, then what the reader is chasing. Inside a group the rank
    # order is preserved, so the soonest thing is still first where it matters.
    # Grouping by who owes the move is what makes the page usable: a reader
    # does their own work, then chases, and interleaving the two made the page
    # a list of unrelated obligations that also looked ragged.
    for label in labels:
        rows = [i for i in page
                if (i.get("label") if i.get("label") in labels else labels[-1])
                == label]
        if not rows:
            continue
        if mode == "md":
            out.append("")
            out.append(f"**{label}**")
            for item in rows:
                # One line per item, tag included. A continuation line indented
                # under a list item renders as a paragraph OUTSIDE the list on
                # every markdown surface, which put a stray line under each.
                note = _draft_note(item) if id(item) in detailed else ""
                tail = f"{_DOT} {note} " if note else ""
                out.append(f"- [ ] {_front_text(item)} {tail}{_DOT} "
                           f"{_tag_for(item)}")
            continue
        pad = (label + ":").ljust(_LABEL_COL)
        for i, item in enumerate(rows):
            head = "  " + (pad if i == 0 else " " * _LABEL_COL)
            # One line per item, note and tag included, the same shape the
            # markdown surfaces use. The note used to sit on its own
            # right-aligned continuation row here and inline there, so the two
            # renders described the same item differently for no reason a
            # reader could see. Wrapping keeps it inside _WRAP either way.
            note = _draft_note(item) if id(item) in detailed else ""
            tail = f"{_DOT} {note} " if note else ""
            text = f"{_front_text(item)} {tail}{_DOT} {_tag_for(item)}"
            wrapped = _wrap_bullet(f"{_DOT} [ ] {text}",
                                   indent=lead, hanging=lead + 4)
            out.append(head + wrapped[0][lead:])
            out.extend(wrapped[1:])
    return "\n".join(out) + "\n"


def _front_text(item: dict) -> str:
    parts = [item.get("subject") or "(no subject)"]
    who = (item.get("counterparty_name") or "").strip()
    if who:
        parts.append(who)
    parts.append(item.get("why") or "")
    return f" {_DOT} ".join([p for p in parts if p])


_ROW_NAME_COL = 15


def render_fold_rows_md(rows: list, mode: str = "terminal",
                        heading: str = "THE REST", front_count: int = 0) -> str:
    """One line per business: the count, the functions, at most two flags.

    `front_count` only decides whether the section says so, but it earns its
    place: a reader saw an item tagged PERSONAL and Legal on the page above,
    looked for it under Personal's "Legal 9", did not find it, and read the
    fold as short the items its own count claimed. These numbers deliberately
    exclude what was promoted, and a page has to say that out loud once.
    """
    if mode not in ("terminal", "md"):
        raise ValueError(f"unknown mode {mode!r}")
    live = [r for r in rows if r.get("folded")]
    total = sum(r["folded"] for r in live)
    title = f"{heading}, {total} item" + ("" if total == 1 else "s")
    out = []
    if mode == "md":
        out.append(f"### {title}")
    else:
        out.append(_BAND)
        out.append(title)
        out.append(_BAND)
    if not live:
        out.append("Nothing else is open.")
        return "\n".join(out) + "\n"
    for row in live:
        name = (row.get("display_name") or row.get("tag") or "").upper()
        whole = int(row.get("total") or row["folded"])
        count = (f"{row['folded']} of {whole}" if whole != row["folded"]
                 else str(row["folded"]))
        # "waiting" is spelled out once and then implied. The word repeated
        # eight times across one line is what turns a summary back into a wall.
        # Every parenthesis carries its own word, and the word says which
        # DIRECTION it means. "(3 waiting)" printed the right number and left
        # the direction to the reader, who has "Waiting on you" and "Waiting on
        # them" side by side three inches above it; two readings of the same
        # bracket imply completely different days. Same defect as the label
        # that used to say "Older, still open", one column over.
        segs = []
        for fn in row.get("functions") or []:
            seg = f"{fn['name']} {fn['count']}"
            if fn.get("waiting"):
                seg += f" ({fn['waiting']} on you)"
            segs.append(seg)
        body = "  ".join(segs)
        if mode == "md":
            out.append(f"- **{name}** {_DOT} {count} {_DOT} {body}")
            for flag in row.get("flags") or []:
                out.append(f"  - {flag}")
            continue
        head = name.ljust(_ROW_NAME_COL)[:_ROW_NAME_COL] + count.rjust(9)
        indent = _ROW_NAME_COL + 11
        wrapped = _wrap_bullet(body, indent=indent, hanging=indent)
        out.append(head + wrapped[0][len(head):])
        out.extend(wrapped[1:])
        for flag in row.get("flags") or []:
            out.extend(_wrap_bullet(flag, indent=indent, hanging=indent))
    return "\n".join(out) + "\n"


def render_folded_md(buckets: list, front_keys: set,
                     briefing: str = "morning-coffee") -> str:
    """The full ledger, one closed Obsidian callout per business.

    Closed, not omitted: `> [!note]-` folds in Obsidian, becomes a closed
    <details> in the Workbench and a shaded block in the digest, and every
    item inside it was already counted in its row.
    """
    if briefing not in _BUCKET_LABELS:
        raise ValueError(f"unknown briefing {briefing!r}")
    out = []
    for bucket in buckets:
        folded = [e for e in (bucket.get("items") or [])
                  if tuple(_entry_key(e)) not in front_keys]
        if not folded:
            continue
        sub = dict(bucket)
        sub["items"] = folded
        name = bucket.get("display_name") or bucket.get("tag") or ""
        n = len(folded)
        out.append(f"> [!note]- {name} {_DOT} {n} more")
        body = _render_one_bucket(sub, briefing, mode="md", by_function=True)
        for line in body:
            out.append(f"> {line}".rstrip())
        out.append("")
    return "\n".join(out).rstrip() + "\n" if out else ""


def empty_front_page() -> dict:
    """The five keys, empty. Emitted on every failure branch too.

    A skill renders whatever key its script emits; a key that only exists on
    the success path renders as a missing section, and a missing section looks
    exactly like a quiet day.
    """
    return {"front_page": [], "front_page_md": "", "fold_rows_md": "",
            "front_page_file_md": "", "fold_rows_file_md": "",
            "folded_md": "", "counts": {"open": 0, "late": 0, "front": 0,
                                        "folded": 0, "stale": 0},
            "fold_rows": [], "page_note": ""}


def fold_slice(buckets: list, front_keys: set, tag: str, function: str = "",
               briefing: str = "morning-coffee") -> str:
    """The exact text behind one fold, or one function group inside it.

    This is what "open Cedar" and "open Cedar sales" paste. It is a slice of
    the same render, never a fresh summary, so what the reader sees when they
    open a row is the ledger they would have seen anyway.
    """
    for bucket in buckets:
        if bucket.get("tag") != tag:
            continue
        folded = [e for e in (bucket.get("items") or [])
                  if tuple(_entry_key(e)) not in front_keys]
        if not folded:
            return ""
        sub = dict(bucket)
        sub["items"] = folded
        if function:
            # Select by function over EVERY folded item, never over the groups.
            # The groups put a priority-matched item under its priority, so
            # filtering them dropped exactly the items a business with stated
            # priorities cares most about: a row said "Sales 12" and the slice
            # behind it pasted 5. The row is counted this way, so the slice has
            # to be selected this way.
            wanted = function.strip().lower()
            sub["items"] = [e for e in folded
                            if (e.get("function") or assign_function(e)
                                ).strip().lower() == wanted]
            if not sub["items"]:
                return ""
            sub["priorities"] = []
        lines = _render_one_bucket(sub, briefing, mode="terminal",
                                  by_function=True)
        while lines and not lines[0]:
            lines.pop(0)
        return "\n".join(lines).rstrip() + "\n"
    return ""


def build_front_page(buckets: list, briefing: str = "morning-coffee",
                     alerts: dict | None = None, cap: int | None = None,
                     active_emails: set | None = None,
                     heading: str = "DO TODAY") -> dict:
    """Everything a briefing script emits for the front page, in one call.

    Wrapped by each script so a render bug costs the front page and never the
    briefing: the full ledger below it is unchanged either way.
    """
    active_emails = active_emails or set()
    for bucket in buckets:
        for entry in bucket.get("items") or []:
            entry.setdefault("function", assign_function(entry))
    if cap is None:
        cap = config_loader.briefing_front_page_cap()
    cap = max(3, min(9, int(cap)))
    page = front_page(buckets, cap=cap, alerts=alerts,
                      active_emails=active_emails)
    front_keys = {tuple(i["key"]) for i in page}
    counts = opening_counts(buckets, page, active_emails=active_emails)
    rows = bucket_summaries(buckets, front_keys, alerts=alerts)
    return {
        "front_page": page,
        # Terminal mode: the aligned columns the chat render pastes.
        "front_page_md": render_front_page_md(page, counts, briefing=briefing,
                                              heading=heading, detail_cap=cap),
        "fold_rows_md": render_fold_rows_md(rows, front_count=len(page)),
        # Markdown mode: what the written file, the web page, the Workbench and
        # the digest need. Column alignment is whitespace, and every markdown
        # renderer collapses whitespace, so pasting the terminal render into a
        # markdown surface turns the whole front page into one run-on
        # paragraph. It shipped that way onto a real published page and only a
        # screenshot caught it.
        "front_page_file_md": render_front_page_md(
            page, counts, briefing=briefing, heading=heading, mode="md",
            detail_cap=cap),
        "fold_rows_file_md": render_fold_rows_md(rows, mode="md",
                                                 front_count=len(page)),
        "folded_md": render_folded_md(buckets, front_keys, briefing=briefing),
        "counts": counts,
        "fold_rows": rows,
    }


# ── Kill/pass scan (shared by Tier 2 and Tier 1) ─────────────────────────────

def _run_kill_scan(output):
    """Scan raw inbound for counterparty kill/pass language tied to open hotcache
    deals, before classification could drop a pass email as 'closing, no ask'.
    KILL_RE is the noise-immune gate; match_deal ties a hit to an open deal.
    Surfaced for review, never auto-killed. kill_text carries a wider body slice
    than the trimmed body_preview so forwarded pass language is not truncated; it
    is popped after the scan to keep the output slim."""
    kill_headings = []
    try:
        kill_headings = [d["heading"] for d in collectors.hotcache_read(HOTCACHE_PATH)]
    except Exception as e:
        output["errors"].append(f"Hotcache deals: {e}")
    kill_items = [{
        "subject": e.get("subject", ""),
        "snippet": e.get("kill_text") or e.get("body_preview", ""),
        "from": e.get("counterparty_name", ""),
        "from_email": e.get("counterparty_email", ""),
        "account": e.get("source", ""),
        "internal": e.get("is_internal", False),
    } for e in output["inbox_pending"]]
    output["status_change_alerts"] = collectors.kill_scan(kill_items, kill_headings)
    for e in output["inbox_pending"]:
        e.pop("kill_text", None)


# ── Post-fetch pipeline (shared by Tier 2 and Tier 1) ────────────────────────

def _finalize(output: dict, args) -> None:
    """Apply done-log suppression, carry-forward, dedup, sort, stats, and emit."""
    done_set = load_done_set()

    def _filter_suppressed(section, honor_reply_age):
        kept_section = []
        for entry in section:
            if is_suppressed(entry.get("subject", ""), entry.get("counterparty_email", ""), done_set):
                if honor_reply_age and entry.get("reply_age_days", 99) <= 1:
                    kept_section.append(entry)
                    continue
                output["suppressed"].append(entry)
            else:
                kept_section.append(entry)
        return kept_section

    output["waiting_on_user"] = _filter_suppressed(output["waiting_on_user"], honor_reply_age=True)
    output["inbox_pending"]   = _filter_suppressed(output["inbox_pending"],   honor_reply_age=True)
    output["cold_urgent"]     = _filter_suppressed(output["cold_urgent"],     honor_reply_age=False)
    output["cold_monitor"]    = _filter_suppressed(output["cold_monitor"],    honor_reply_age=False)

    # Read the urgent items against what the user actually said matters, then
    # gate. The judgment runs first because the gate needs its verdicts, and it
    # is wrapped because a model outage must cost the judgment, never the run:
    # a degraded judgment demotes nothing at all.
    try:
        output["judgment"] = apply_priority_judgment(output["cold_urgent"])
    except Exception as e:                                      # noqa: BLE001
        output["judgment"] = {"degraded": True, "judged": 0, "buckets": [],
                              "degraded_reasons": [f"the judgment call failed ({e})"]}
        output["errors"].append(f"Priority judgment: {e}")

    # Overreach guard (Item 11): demote spam / stakeholder-feedback / stale-no-deal
    # items out of the red bucket so thin deal flow doesn't surface noise as red.
    active_emails = load_active_thread_emails()
    gate_cold_promotion(output, active_emails,
                        judgment=output.get("judgment"))

    prior = load_prior_week_state(output["week_start"])
    carried_count = 0
    if prior:
        seen_keys = set()
        for section in (output["waiting_on_user"], output["cold_urgent"],
                        output["cold_monitor"], output["inbox_pending"]):
            for e in section:
                seen_keys.add((
                    normalize_subject(e.get("subject", "")),
                    (e.get("counterparty_email") or "").lower(),
                ))

        def _carry(entries, target_section):
            nonlocal carried_count
            for entry in entries:
                key = (normalize_subject(entry["subject"]), entry["counterparty_email"])
                if key in seen_keys:
                    continue
                if key in done_set:
                    continue
                dest = target_section
                # Overreach guard on the carry path: a carried-forward item that
                # is spam/noise or stakeholder-feedback must not re-enter red.
                if target_section is output["cold_urgent"]:
                    subj = entry.get("subject", "")
                    if (is_spam(entry.get("counterparty_email", ""))
                            or is_warmup(subj) or is_noisy_subject(subj)):
                        dest = output["filtered_pending"]
                    elif is_stakeholder_feedback(subj):
                        dest = output["cold_monitor"]
                dest.append(entry)
                seen_keys.add(key)
                carried_count += 1

        _carry(prior.get("carried_waiting", []),     output["waiting_on_user"])
        _carry(prior.get("carried_cold_urgent", []),  output["cold_urgent"])
        _carry(prior.get("carried_cold_monitor", []), output["cold_urgent"])

    output["waiting_on_user"] = dedup_cross_account(output["waiting_on_user"])
    output["inbox_pending"]   = dedup_cross_account(output["inbox_pending"])
    output["cold_urgent"]     = dedup_cross_account(output["cold_urgent"])
    output["cold_monitor"]    = dedup_cross_account(output["cold_monitor"])

    # Meeting cross-ref: tag (do NOT remove) any thread a recent meeting likely
    # handled, so the briefing can de-prioritize it as "likely handled, confirm"
    # without dropping it from the counts/trend. Under-resolving is the safe miss.
    # The notetaker is a direct API call (not a connector), so this runs in both tiers.
    today_date = datetime.now(timezone.utc).astimezone(USER_TZ).date()
    meetings = collectors.meeting_texts(output.get("since_days", 14), output["errors"])
    resolved_by_meeting = 0
    for section in (output["waiting_on_user"], output["inbox_pending"],
                    output["cold_urgent"], output["cold_monitor"]):
        for entry in section:
            mtg = _meeting_resolved(entry, meetings, today_date)
            if mtg:
                entry["met_since"] = mtg["date"]
                resolved_by_meeting += 1

    # Meeting status-change scan: a kill OR a re-engagement/pivot announced in a
    # meeting never reaches mail, so the email kill scan (_run_kill_scan, which
    # ran before _finalize) is blind to it. Scan the same window's summaries
    # (already fetched above, with text), attributed to open deals, and merge
    # into status_change_alerts. The summary text stays local, never persisted
    # to output. Fail-open.
    try:
        kill_headings = [d["heading"] for d in collectors.hotcache_read(HOTCACHE_PATH)]
        output["status_change_alerts"].extend(
            collectors.meeting_status_scan(meetings, kill_headings)
        )
    except Exception as e:
        output["errors"].append(f"Meeting status scan: {e}")

    output["waiting_on_user"].sort(key=lambda x: x.get("reply_age_days", 0), reverse=True)
    output["inbox_pending"].sort(  key=lambda x: x.get("reply_age_days", 0), reverse=True)
    output["cold_urgent"].sort(    key=lambda x: x.get("age_days", 0),       reverse=True)
    output["cold_monitor"].sort(   key=lambda x: x.get("age_days", 0),       reverse=True)

    if prior:
        output["trend"] = {
            "cold_urgent_prev":  prior["cold_urgent_count"],
            "cold_urgent_delta": len(output["cold_urgent"]) - prior["cold_urgent_count"],
            "waiting_prev":      prior["waiting_count"],
            "waiting_delta":     len(output["waiting_on_user"]) - prior["waiting_count"],
        }

    allowlisted_count = sum(
        1 for e in (output["waiting_on_user"] + output["inbox_pending"])
        if e.get("allowlisted")
    )
    output["filter_stats"] = {
        "inbox_filtered_low_urgency": len(output["filtered_pending"]),
        "suppressed_done":            len(output["suppressed"]),
        "allowlisted_tier1":          allowlisted_count,
        "carried_forward_prior_week": carried_count,
        "resolved_by_meeting":        resolved_by_meeting,
    }

    # Buckets last, over the finished sections, so what the user reads is what
    # survived every filter. Wrapped because a render bug must cost the bucket
    # section, never the whole briefing.
    # The weekly briefing renders overdue deliverables too, so it has to emit
    # them. It referenced the key without ever producing it, and a section
    # that never appears reads exactly like a week with nothing overdue.
    try:
        from follow_up_radar import collect_nudges
        nudges, nudge_err = collect_nudges(
            datetime.now(timezone.utc).astimezone(USER_TZ).date())
        output["nudges"] = nudges
        if nudge_err:
            output["errors"].append(nudge_err)
    except Exception as e:                                      # noqa: BLE001
        output["nudges"] = []
        output["errors"].append(f"Nudges: {e}")

    output["judgment"] = output.get("judgment") or {"degraded": False,
                                                    "degraded_reasons": [],
                                                    "judged": 0, "buckets": []}
    try:
        output["buckets"] = group_by_bucket(output)
        output["buckets_md"] = render_buckets_md(
            output["buckets"], "week", judgment=output.get("judgment"))
    except Exception as e:
        output["buckets"] = []
        output["buckets_md"] = ""
        output["errors"].append(f"Bucket render: {e}")

    # The front page, over the same buckets. Wrapped separately from the
    # ledger above it: a bug in the selection must cost the short page, never
    # the full list, and the keys are emitted either way so a skill that
    # renders them never meets a missing section.
    output.update(empty_front_page())
    try:
        output.update(build_front_page(
            output["buckets"], "week",
            alerts={"newly_cold": output.get("cold_urgent") or []},
            active_emails=active_emails, heading="TOP PRIORITIES"))
    except Exception as e:                                      # noqa: BLE001
        output["errors"].append(f"Front page: {e}")

    # Travel across the WHOLE week, not just today. On a Monday the useful
    # question is which trips are coming and which are still unbooked, while
    # there is a week left to do something about it; the morning-of leave-by
    # time is Morning Coffee's job on the day. Runs here in _finalize so both
    # the live and the Tier 2 paths get it, after the calendar is populated.
    #
    # The ledger is keyed per briefing, so naming a trip here does not silence
    # the departure notice the traveller needs on the morning itself.
    _travel_commit = None
    if travel_enabled():
        # Imported here, not at module load: a missing or broken travel module
        # must cost the reader the travel section and nothing else.
        try:
            import travel
            _travel = travel.collect(
                output.get("calendar", []), output["week_start"], USER_TZ,
                travel_buffer_minutes(),
                briefing="week",
                # The whole week is in scope: flag an unbooked flight as soon
                # as it is on the calendar, and carry the forecast for one far
                # enough out to still be packing for.
                weather_lead_days=6, flights_min_days=1,
            )
            output["travel"] = _travel["notices"]
            # Rendered here so the skill pastes it rather than describing it.
            # Emitting only the raw notices left the skill instructing a paste
            # of `travel_md`, a key that was never in the payload, so the
            # block silently vanished from this briefing's file and page.
            output["travel_md"] = travel.render_travel_md(
                _travel["notices"], mode="terminal")
            output["travel_file_md"] = travel.render_travel_md(
                _travel["notices"], mode="md")
            if _travel["error"]:
                output["errors"].append(_travel["error"])
            # Held until the JSON is printed: the ledger records that the
            # reader was told, so it must not be written before they were.
            _travel_commit = _travel.get("commit")
        except Exception as e:                                  # noqa: BLE001
            output["errors"].append(f"Travel notices unavailable: {e}")

    # The link to this briefing's page, or why it did not update. Whichever
    # applies; the skill prints it as the last line either way.
    try:
        import briefing_html
        output["page_note"] = briefing_html.page_line("week")
    except Exception:                                           # noqa: BLE001
        output["page_note"] = ""

    # User extension sections (fail open; see app/extensions.py).
    # --no-extensions doubles as the "data subprocess" marker: a run that is
    # feeding morning_coffee/week_retro must not consume the release notes
    # either — its meta is data, never rendered to the user.
    if not getattr(args, "no_extensions", False):
        extensions.attach(output, "week")
        if (output.get("meta") or {}).get("whats_new"):
            import plugin_update
            plugin_update.mark_notes_shown()

    # One content-free row for the weekly scorecard, under the same guard the
    # extensions use: --no-extensions marks a data subprocess feeding another
    # briefing, and recording there would count one morning as two runs.
    if not getattr(args, "no_extensions", False):
        kpi_events.record_snapshot("week", output)

    if not args.no_sidecar:
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            with open(FULL_SIDECAR_PATH, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, default=str)
        except Exception as e:
            output["errors"].append(f"Sidecar write: {e}")

    slim = dict(output)
    slim["filtered_pending"] = [
        {k: v for k, v in e.items() if k != "body_preview"}
        for e in output["filtered_pending"]
    ]
    print(json.dumps(slim, indent=2, default=str))

    # The briefing is out. Only now is it true that the reader was shown this
    # week's travel notices, so only now is it honest to record them.
    if _travel_commit is not None:
        try:
            _travel_commit()
        except Exception:                                       # noqa: BLE001
            pass


def _run_tier2(output: dict, args, since_days: int) -> None:
    """Populate output from a Tier 2 --input tempfile and emit results."""
    data = load_input(args.input)
    output["calendar"]         = data.get("calendar", [])
    output["waiting_on_user"]  = data.get("waiting_on_user", [])
    output["inbox_pending"]    = data.get("inbox_pending", [])
    output["filtered_pending"] = data.get("filtered_pending", [])
    output["cold_urgent"]      = data.get("cold_urgent", [])
    output["cold_monitor"]     = data.get("cold_monitor", [])
    try:
        output["obsidian_tasks"] = fetch_obsidian_tasks(since_days, args.sources_days)
    except Exception as e:
        output["errors"].append(f"Obsidian tasks: {e}")
    _run_kill_scan(output)
    _finalize(output, args)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="auto",
                        help="Days back for email review. 'auto' (default) = days since prior week.md was generated, floored at 14.")
    parser.add_argument("--week-ahead", type=int, default=6, help="Days forward for calendar (default 6 = Mon–Sun, current week only)")
    parser.add_argument("--sources-days", type=int, default=7, help="Days back for source page tasks (default 7)")
    parser.add_argument("--no-sidecar", action="store_true",
                        help="Skip writing the full JSON sidecar to logs/week_review_latest.full.json")
    parser.add_argument("--no-extensions", action="store_true",
                        help="Skip user extension sections (morning_coffee passes this when it runs week_review as a data subprocess, so an extension never runs twice per briefing)")
    parser.add_argument("--no-classify", action="store_true",
                        help="Skip metered Haiku classification (a read-only history consumer like week-retro doesn't need per-thread summaries)")
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON (skips all API calls)")
    args = parser.parse_args()

    if not args.input and not is_tier1():
        missing_input_error("week_review.py")

    since_days = resolve_since(args.since)

    today_utc = datetime.now(timezone.utc)
    today_local = today_utc.astimezone(USER_TZ)

    weekday = today_local.weekday()  # 0=Mon … 6=Sun
    if weekday == 6:
        monday_local = today_local + timedelta(days=1)
    else:
        monday_local = today_local - timedelta(days=weekday)
    friday_local = monday_local + timedelta(days=4)
    monday_utc = monday_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    output = {
        "meta": resolved_meta(),
        "week_start": monday_local.strftime("%Y-%m-%d"),
        "week_end": friday_local.strftime("%Y-%m-%d"),
        "since_days": since_days,
        "calendar": [],
        "travel": [],            # trips this week (app/travel.py); empty most weeks
        "travel_md": "",         # the finished Travel block, terminal render
        "travel_file_md": "",    # the same block, with links, for the file
        "obsidian_tasks": [],
        "waiting_on_user": [],
        "inbox_pending": [],
        "filtered_pending": [],  # Haiku tagged suppress=true; kept for audit, not dropped
        "cold_urgent": [],
        "cold_monitor": [],
        "suppressed": [],        # matched done-{YYYY}.md; removed from above
        "status_change_alerts": [],  # counterparty kill/pass language; review, never auto-killed
        "extra_sections": [],    # user extension sections; filled in _finalize
        "errors": [],
    }

    if args.input:
        _run_tier2(output, args, since_days)
        return

    # Phase 1: fully independent fetches — dispatch in parallel across every
    # configured account. Each fetch is a direct API call bound on network I/O,
    # so threading gets near-linear speedup with no shared mutable state.
    # Iterating the accounts list (rather than fixed primary/secondary/outlook
    # slots) means single-account installs fire exactly one fetch per kind — no
    # spurious "*_REFRESH_TOKEN not set" errors for accounts that don't exist.
    n_fetches = 1 + 2 * (len(GOOGLE_ACCOUNTS) + len(MICROSOFT_ACCOUNTS))
    pool1 = ThreadPoolExecutor(max_workers=max(2, n_fetches))
    cal_futures = []   # (account, future)
    for a in GOOGLE_ACCOUNTS:
        cal_futures.append((a, pool1.submit(
            fetch_google_calendar, a["label"], a["is_primary"], monday_utc, args.week_ahead)))
    for a in MICROSOFT_ACCOUNTS:
        cal_futures.append((a, pool1.submit(
            fetch_outlook_calendar, a["label"], monday_utc, args.week_ahead)))
    f_obsidian = pool1.submit(fetch_obsidian_tasks, since_days, args.sources_days)
    deal_futures = []  # (account, future) — microsoft returns 4-tuple, google 3-tuple
    for a in MICROSOFT_ACCOUNTS:
        deal_futures.append((a, pool1.submit(
            fetch_outlook_deals, a["label"], a["sent_folder_id"], a["email"], since_days)))
    for a in GOOGLE_ACCOUNTS:
        deal_futures.append((a, pool1.submit(
            _fetch_gmail_account_deals, google_client(a["label"]), a["email"], a["label"], since_days)))
    pool1.shutdown(wait=False)

    event_lists = []
    for a, fut in cal_futures:
        try:
            event_lists.append(fut.result())
        except Exception as e:
            output["errors"].append(f"{a['label']} calendar: {e}")
    output["calendar"] = merge_calendars(*event_lists)

    try:
        output["obsidian_tasks"] = f_obsidian.result()
    except Exception as e:
        output["errors"].append(f"Obsidian tasks: {e}")

    # Deals. Microsoft fetchers also return a per-account sent-conversation map
    # used by the Phase 2 inbox sweep; Google fetchers return only the 3 lists.
    outlook_sent_conv_dt = {}  # account label -> {conversationId: sentDateTime}
    for a, fut in deal_futures:
        try:
            res = fut.result()
            if a["provider"] == "microsoft":
                d_wait, d_cold_u, d_cold_m, sent_conv_dt = res
                outlook_sent_conv_dt[a["label"]] = sent_conv_dt
            else:
                d_wait, d_cold_u, d_cold_m = res
            output["waiting_on_user"].extend(d_wait)
            output["cold_urgent"].extend(d_cold_u)
            output["cold_monitor"].extend(d_cold_m)
        except Exception as e:
            output["errors"].append(f"{a['label']} deals: {e}")

    # Phase 2: inbox sweep — catches threads where the user hasn't sent first
    # (invisible to the sent-mail flow above). Anchor the window to last Monday.
    # The Outlook inbox call depends on outlook_sent_conv_dt resolved in phase
    # 1, so phase 2 starts only after the phase-1 results are collected.
    last_monday_utc = (monday_local - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    inbox_since = max(1, (today_utc - last_monday_utc).days)

    pool2 = ThreadPoolExecutor(max_workers=max(2, len(GOOGLE_ACCOUNTS) + len(MICROSOFT_ACCOUNTS)))
    inbox_futures = []  # (account, future)
    for a in MICROSOFT_ACCOUNTS:
        inbox_futures.append((a, pool2.submit(
            fetch_outlook_inbox_pending, a["label"], a["sent_folder_id"], a["email"],
            inbox_since, outlook_sent_conv_dt.get(a["label"], {}))))
    for a in GOOGLE_ACCOUNTS:
        inbox_futures.append((a, pool2.submit(
            _fetch_gmail_account_inbox_pending, google_client(a["label"]), a["email"], a["label"], inbox_since)))
    pool2.shutdown(wait=False)

    for a, fut in inbox_futures:
        try:
            output["inbox_pending"].extend(fut.result())
        except Exception as e:
            output["errors"].append(f"{a['label']} inbox: {e}")

    # Kill/pass scan over the RAW inbound, before classification could drop a
    # pass email as "closing, no ask". Surfaced for review, never auto-killed.
    _run_kill_scan(output)

    # Classify: for waiting_on_user we still drop suppressed (FYI/closing acks
    # the user doesn't need to act on). For inbox_pending we tier into
    # filtered_pending so spam/low-urgency stays auditable. --no-classify skips
    # the metered Haiku call (a history consumer like week-retro doesn't need it).
    if not args.no_classify:
        try:
            kept_w, _ = classify_threads(output["waiting_on_user"])
            output["waiting_on_user"] = kept_w
        except Exception as e:
            output["errors"].append(f"Email classification: {e}")

        try:
            kept_i, filtered_i = classify_threads(output["inbox_pending"])
            output["inbox_pending"] = kept_i
            output["filtered_pending"] = filtered_i
        except Exception as e:
            output["errors"].append(f"Inbox classification: {e}")

    _finalize(output, args)


if __name__ == "__main__":
    main()
