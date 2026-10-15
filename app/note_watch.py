#!/usr/bin/env python3
"""The Note: what the watcher notices between two briefings, and the rules.

Morning Coffee arrives at seven and is true at seven. By ten it can be wrong
in three ways the reader cannot see: the counterparty a front-page item was
waiting on has replied, a counterparty has said in words that a deal is off,
or a meeting on today's page has moved. Nothing in the product noticed any of
those before Afternoon Tea, because every job ran on a clock and the clock
knows nothing.

So a tick (`note_send.py`) polls every fifteen minutes and asks this module a
cheap question: did anything on the front page move? This module is the pure
half. It reads the last briefing's own output, matches inbound mail and the
calendar against it, and decides what fires, what waits, and what folds into
the next briefing. No network, no clock of its own, no files except the ones a
caller hands it, so every rule here has a planted test sitting past its edge.

Four ideas carry the design, and the evidence behind each is in DESIGN.md.

**The front page is the watch list.** Only items the morning briefing put on
its front page, and only deals in the hotcache, can produce a Note. The 129
folded items wait for Afternoon Tea like they always did. A watcher over
everything is an alarm nobody keeps switched on.

**Two a day, then fold.** Three daily batches beat hourly delivery in the one
randomized trial that measured it, and hourly was no better than nothing. Two
Notes plus the two briefings keeps the day inside that shape. The third event
is not lost: it is written down as folded and Afternoon Tea opens with it.

**Never during a meeting.** A Note that lands mid-call is read at the wrong
moment or not at all. When the calendar says a meeting is in progress the
tick holds everything and tries again in fifteen minutes.

**Every Note holds something to nod at.** A reply arrives with the answer
already drafted, a pass arrives as a question, a moved meeting arrives with
the afternoon re-ranked. A Note that only reports is a notification, and the
product does not send notifications.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

# Poll cadence, shared with the scheduler so the plist and the ledger agree.
TICK_MINUTES = 15

# Ledger rows older than this are dropped. Two days covers a weekend gap.
LEDGER_RETENTION_DAYS = 2

# A calendar event carries no end time in the shape meeting_prep emits, so a
# meeting is taken to run this long for the "hold while in a meeting" rule.
MEETING_MINUTES = 60

# How far a meeting has to move before it counts as moved. Five minutes is
# the same tolerance merge_events uses to call two calendar entries one event.
MOVE_TOLERANCE_S = 300

# Markers bounding the Notes block in the written briefing. Everything between
# them is rewritten whole on every write, which is what makes the patch
# idempotent and keeps it out of the reader's own edits elsewhere.
NOTES_START = "<!-- notes:start -->"
NOTES_END = "<!-- notes:end -->"
NOTES_HEADING = "## Notes"

KINDS = ("reply", "deal", "calendar")

_PREFIX_RE = re.compile(r"^(re|fw|fwd|aw|sv)\s*:\s*", re.IGNORECASE)


def _strip_dashes(text: str) -> str:
    """Em and en dashes out of anything written or mailed (voice rules)."""
    return (text or "").replace(chr(0x2014), ", ").replace(chr(0x2013), "-")


def normalize_subject(subject: str) -> str:
    """Lowercase, whitespace-collapsed, reply prefixes stripped.

    Kept local rather than imported from week_review: that module reads config
    at import and pulls the whole briefing pipeline behind it, and this module
    has to stay importable by a fifteen-minute tick that must not.
    """
    s = (subject or "").strip().lower()
    for _ in range(5):
        m = _PREFIX_RE.match(s)
        if not m:
            break
        s = s[m.end():]
    return re.sub(r"\s+", " ", s).strip()


# ── The watch list ───────────────────────────────────────────────────────────

def watch_key(account: str, counterparty_email: str, subject: str) -> str:
    """The identity of one watched thread: account, who, what.

    Neither provider's thread id reaches the briefing output, so identity is
    built from what does. All three parts are required: a lone email address
    would fire on a counterparty writing about something else, and a lone
    subject would fire on a stranger quoting it.
    """
    return "|".join([
        (account or "").strip().lower(),
        (counterparty_email or "").strip().lower(),
        normalize_subject(subject),
    ])


def watch_list(front_page: list) -> list:
    """The front-page items a reply can move. Items with no counterparty
    address (a vault task, a calendar stub) cannot be replied to and are left
    out rather than matched loosely."""
    out = []
    for item in front_page or []:
        email = (item.get("counterparty_email") or "").strip().lower()
        subject = (item.get("subject") or "").strip()
        if not email or not subject:
            continue
        out.append({
            "key": watch_key(item.get("account", ""), email, subject),
            "account": item.get("account") or "",
            "counterparty_email": email,
            "counterparty_name": item.get("counterparty_name") or "",
            "subject": subject,
            "label": item.get("label") or "",
            "why": item.get("why") or "",
            "age_days": int(item.get("age_days") or 0),
            "due": item.get("due") or "",
            "function": item.get("function") or "",
            "bucket_name": item.get("bucket_name") or "",
        })
    return out


def covers_today(output: dict, today: str) -> bool:
    """Whether a briefing output describes `today`.

    The daily briefings carry `date`. The Week carries `week_start` and speaks
    for the seven days from it, so on a Wednesday the Monday page still counts.
    """
    if not isinstance(output, dict):
        return False
    date = str(output.get("date") or (output.get("meta") or {}).get("date") or "")
    if date:
        return date == today
    start = str(output.get("week_start") or "")
    if not start:
        return False
    try:
        d0 = datetime.fromisoformat(start).date()
        d1 = datetime.fromisoformat(today).date()
    except ValueError:
        return False
    return d0 <= d1 < d0 + timedelta(days=7)


def union_front_page(outputs: list, today: str) -> list:
    """Every front-page item from every briefing that describes today.

    A union, not the newest page: Afternoon Tea's front page is built from the
    morning file's checkbox lines and carries no addresses, so taking only the
    newest page would empty the watch list at 1 PM. Items are deduplicated on
    the watch key; the first sighting wins.
    """
    seen, out = set(), []
    for output in outputs or []:
        if not covers_today(output, today):
            continue
        for item in output.get("front_page") or []:
            key = watch_key(item.get("account", ""), item.get("counterparty_email", ""),
                            item.get("subject", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


# ── Triggers ─────────────────────────────────────────────────────────────────

def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _is_self(addr: str, account_email: str) -> bool:
    return bool(account_email) and account_email.lower() in (addr or "").lower()


def reply_events(inbound: list, watched: list) -> list:
    """A counterparty wrote back on a watched thread.

    `inbound` items carry `subject`, `from`, `from_email`, `account`, `snippet`,
    `received` (ISO UTC) and optionally `message_id`. A match needs the same
    account, the same address and the same normalized subject, all three.
    The event id hashes the FULL message id when one exists (a provider id is
    never truncated) and otherwise the thread key plus the message's own
    arrival time, so two replies in one day are two Notes and one reply seen
    on four ticks is one.
    """
    by_key = {w["key"]: w for w in watched}
    events = []
    for mail in inbound or []:
        key = watch_key(mail.get("account", ""), mail.get("from_email", ""),
                        mail.get("subject", ""))
        item = by_key.get(key)
        if not item:
            continue
        mid = str(mail.get("message_id") or "").strip()
        ident = _digest("reply", mid) if mid else _digest(
            "reply", key, str(mail.get("received") or ""))
        events.append({
            "kind": "reply",
            "id": f"reply:{ident}",
            "received": mail.get("received") or "",
            "item": item,
            "mail": {k: mail.get(k, "") for k in
                     ("subject", "from", "from_email", "account", "snippet",
                      "received", "message_id")},
        })
    return events


def deal_events(inbound: list, deal_headings: list, detect) -> list:
    """A counterparty said, in words, that a hotcache deal is off.

    `detect` is `deal_status.detect_kills`, injected so this module stays free
    of the briefing imports. It is called one message at a time, so each alert
    is attributed to the message that produced it and never to a neighbour.
    Only alerts the scan could tie to a named deal survive: an untied hit is
    exactly the false alarm this watcher must never send, and Afternoon Tea's
    own scan still shows it there for review.
    """
    if not inbound or not deal_headings:
        return []
    events = []
    for m in inbound:
        item = {
            "subject": m.get("subject", ""),
            "snippet": m.get("snippet", ""),
            "from": m.get("from", ""),
            "from_email": m.get("from_email", ""),
            "account": m.get("account", ""),
            "internal": bool(m.get("internal", False)),
        }
        alerts = detect([item], deal_headings) or []
        if not alerts or not alerts[0].get("thread"):
            continue
        alert = alerts[0]
        mid = str(m.get("message_id") or "").strip()
        ident = _digest("deal", mid) if mid else _digest(
            "deal", item["account"], item["from_email"],
            normalize_subject(item["subject"]), str(m.get("received") or ""))
        events.append({
            "kind": "deal",
            "id": f"deal:{ident}",
            "received": m.get("received") or "",
            "thread": alert["thread"],
            "signal": alert.get("signal", ""),
            "mail": {
                "subject": item["subject"],
                "from": item["from"],
                "from_email": item["from_email"],
                "account": item["account"],
                "snippet": item["snippet"],
                "received": m.get("received", ""),
                "message_id": mid,
                "forwarded": bool(alert.get("forwarded")),
            },
        })
    return events


def calendar_snapshot(events: list) -> dict:
    """Today's meetings as `{title-key: {title, start}}` for the next tick.

    Keyed on the title alone because the event dicts carry no provider id and
    a moved meeting keeps its title while changing its start, which is the
    whole thing being detected.
    """
    snap = {}
    for e in events or []:
        start = e.get("dt_utc")
        title = str(e.get("title") or "").strip()
        if not title or not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        snap[title.lower()] = {"title": title, "start": start.isoformat()}
    return snap


def calendar_events(previous: dict, current: dict, now: datetime) -> list:
    """What changed on today's calendar since the last tick.

    Moved: same title, start differs by more than the tolerance. Gone: a title
    the last tick saw with a start still ahead of `now` is not in the window
    any more. A meeting that simply started and fell out of the forward-looking
    window is not gone; it happened. New meetings are not events: the reader
    put them there.
    """
    events = []
    for key, prev in (previous or {}).items():
        try:
            old = datetime.fromisoformat(prev["start"])
        except (KeyError, TypeError, ValueError):
            continue
        cur = (current or {}).get(key)
        if cur is None:
            if old <= now:
                continue
            events.append({
                "kind": "calendar",
                "id": f"calendar:{_digest('gone', key, prev['start'])}",
                "change": "gone",
                "title": prev.get("title") or key,
                "old_start": prev["start"],
                "new_start": "",
            })
            continue
        try:
            new = datetime.fromisoformat(cur["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs((new - old).total_seconds()) <= MOVE_TOLERANCE_S:
            continue
        events.append({
            "kind": "calendar",
            "id": f"calendar:{_digest('moved', key, prev['start'], cur['start'])}",
            "change": "moved",
            "title": cur.get("title") or key,
            "old_start": prev["start"],
            "new_start": cur["start"],
        })
    return events


def in_meeting(events: list, now: datetime) -> bool:
    """Whether the calendar says a meeting is underway at `now`."""
    for e in events or []:
        start = e.get("dt_utc")
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now < start + timedelta(minutes=MEETING_MINUTES):
            return True
    return False


# ── The gates ────────────────────────────────────────────────────────────────

def in_window(now_local: datetime, start: str, end: str) -> bool:
    """Whether a local wall-clock time falls inside the active hours.

    Inclusive of the start, exclusive of the end, so a window of 07:30 to
    18:00 fires at 07:30 and holds at 18:00. A window that cannot be parsed
    reads as closed: a typo in config costs the feature, never a 2 AM Note.
    """
    try:
        sh, sm = (int(p) for p in start.split(":"))
        eh, em = (int(p) for p in end.split(":"))
    except (AttributeError, TypeError, ValueError):
        return False
    minutes = now_local.hour * 60 + now_local.minute
    return (sh * 60 + sm) <= minutes < (eh * 60 + em)


def sent_today(ledger: dict, today: str) -> int:
    return sum(1 for row in (ledger or {}).values()
               if isinstance(row, dict) and row.get("status") == "sent"
               and str(row.get("day") or "") == today)


def select(events: list, ledger: dict, today: str, cap: int,
           meeting_now: bool) -> tuple:
    """Split fresh events into (fire, fold, hold). Pure.

    Anything already in the ledger, sent or folded, is dropped. If a meeting
    is underway everything holds: nothing is written, so the same events come
    back on the next tick. Otherwise the first `cap - sent_today` fire and the
    rest fold, oldest arrival first so the cap is spent on what happened first.
    """
    fresh = [e for e in events if e.get("id") and e["id"] not in (ledger or {})]
    fresh.sort(key=lambda e: str(e.get("received") or e.get("old_start") or ""))
    if not fresh:
        return [], [], []
    if meeting_now:
        return [], [], fresh
    room = max(0, int(cap) - sent_today(ledger, today))
    return fresh[:room], fresh[room:], []


# ── The ledger ───────────────────────────────────────────────────────────────

def prune(rows: dict, now: datetime | None = None) -> dict:
    """Drop rows past retention. Rows without a readable time are dropped too."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LEDGER_RETENTION_DAYS)
    out = {}
    for key, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        try:
            when = datetime.fromisoformat(str(row.get("at")))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            out[key] = row
    return out


def record(rows: dict, event: dict, status: str, day: str, line: str,
           now: datetime | None = None) -> dict:
    """Write one event down as sent or folded. Returns a new dict."""
    now = now or datetime.now(timezone.utc)
    rows = dict(rows or {})
    rows[event["id"]] = {
        "status": status,
        "kind": event.get("kind", ""),
        "day": day,
        "at": now.isoformat(timespec="seconds"),
        "line": line,
    }
    return rows


def notes_today(rows: dict, today: str) -> list:
    """Every Note sent or folded today, oldest first, for Afternoon Tea."""
    out = []
    for row in (rows or {}).values():
        if not isinstance(row, dict) or str(row.get("day") or "") != today:
            continue
        out.append({"status": row.get("status", ""), "kind": row.get("kind", ""),
                    "at": row.get("at", ""), "line": row.get("line", "")})
    out.sort(key=lambda r: r["at"])
    return out


# ── The line in the briefing ─────────────────────────────────────────────────

def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def note_line(event: dict, time_str: str, drafted: bool = False) -> str:
    """The one line the written briefing carries for this event. Code writes
    it, from the event's own data, so it cannot claim more than happened:
    `drafted` is set only after the draft actually exists."""
    kind = event.get("kind")
    if kind == "reply":
        item = event.get("item") or {}
        who = item.get("counterparty_name") or (event.get("mail") or {}).get("from") \
            or item.get("counterparty_email") or "The counterparty"
        line = f"{time_str}: {who} replied on {item.get('subject') or 'the thread'}"
        age = int(item.get("age_days") or 0)
        if age:
            line += f", {_days(age)} after you asked"
        line += ". Reply drafted, waiting for your nod." if drafted else "."
        return _strip_dashes(line)
    if kind == "deal":
        mail = event.get("mail") or {}
        who = mail.get("from") or mail.get("from_email") or "The counterparty"
        return _strip_dashes(
            f"{time_str}: {who} wrote on {mail.get('subject') or 'the thread'} and it "
            f"reads like a pass on {event.get('thread')} (\"{event.get('signal', '')}\"). "
            f"Nothing in your notes changed; the Note asks whether to mark it.")
    if kind == "calendar":
        title = event.get("title") or "A meeting"
        if event.get("change") == "gone":
            return _strip_dashes(
                f"{time_str}: {title}, {event.get('old_time', 'earlier today')}, "
                f"is off the calendar.")
        return _strip_dashes(
            f"{time_str}: {title} moved from {event.get('old_time', '')} to "
            f"{event.get('new_time', '')}.")
    return _strip_dashes(f"{time_str}: something moved.")


def patch_notes(md: str, lines: list) -> str:
    """Rewrite the Notes block in a written briefing. Idempotent.

    The block sits under its own `## Notes` heading, placed before the first
    section heading after the opening so it is the first thing under the
    date. When the markers already exist the block between them is replaced
    whole; nothing outside the markers is touched, which is what keeps a hand
    edit elsewhere in the file safe. An empty `lines` removes the block.
    """
    text = md or ""
    block = ""
    if lines:
        body = "\n".join(f"- {_strip_dashes(l)}" for l in lines)
        block = f"{NOTES_HEADING}\n{NOTES_START}\n{body}\n{NOTES_END}\n"
    start = text.find(NOTES_START)
    end = text.find(NOTES_END)
    if start != -1 and end != -1 and end > start:
        head_start = text.rfind(NOTES_HEADING, 0, start)
        if head_start != -1 and text[head_start:start].strip() == NOTES_HEADING:
            start = head_start
        after = end + len(NOTES_END)
        if text[after:after + 1] == "\n":
            after += 1
        # A removed block also takes the blank line that separated it.
        if not block and text[after:after + 1] == "\n":
            after += 1
        return text[:start] + block + ("\n" if block and text[after:after + 1] != "\n" else "") + text[after:]
    if not block:
        return text
    m = re.search(r"^## ", text, re.M)
    if m is None:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    return text[:m.start()] + block + "\n" + text[m.start():]


def event_file_payload(event: dict, extra: dict | None = None) -> str:
    """The JSON handed to the skill. Datetimes become strings; nothing else
    is transformed, so the skill sees exactly what the tick saw."""
    payload = dict(event)
    payload.update(extra or {})
    return json.dumps(payload, indent=2, default=str)
