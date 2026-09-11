#!/usr/bin/env python3
"""Prep emails: the one-pager for a call, mailed before the call.

`/van-gogh:meeting-prep` has always been able to write a good brief. What it
could not do was arrive. It ran when the user thought to run it, which is the
one moment a prep is least useful: you already know you have the call, and you
are already in it.

So this polls. Every fifteen minutes it asks a cheap question, "is there a
meeting close enough to prep", and mails the answer. Two shapes, because two
people asked for opposite things:

    each     one email per meeting, `lead_minutes` ahead (default 60)
    daily    one email at `daily_time` covering every meeting that day

Three ideas carry the design.

**The window has to be wider than the work.** The brief is written by the model,
not by `meeting_prep.py`, which only gathers. That means a headless `claude -p`
render, and `digest_send` already learned what those cost: a thirty minute
timeout, two attempts, backoff between them. An hour of lead is not generosity,
it is the margin that lets a bad render still land before the call.

**Sent once, ever.** A meeting sits inside a 60 minute window for four
consecutive ticks. Without a ledger keyed on the event, the user gets four
copies of the same prep, which is how a helpful feature becomes one people turn
off. The ledger is the feature.

**It fails open, like everything else unattended here.** A prep that cannot be
built costs the user that prep. It never costs them the tick, the next meeting,
or a mailbox full of errors.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config_loader

# Poll cadence. Fifteen minutes is the coarsest tick that still lets an "each"
# prep land in a 15 minute spread around its lead time; at 30 the same promise
# has a 30 minute spread, which is the difference between "about an hour before"
# being true and being a rounding claim.
TICK_MINUTES = 15

# Ledger rows older than this are dropped. Two days covers a weekend gap while
# keeping the file small enough to rewrite whole on every tick.
LEDGER_RETENTION_DAYS = 2

# Matches digest_send: a headless render is slow and sometimes needs a retry.
RENDER_TIMEOUT_S = 30 * 60
RENDER_ATTEMPTS = 2
RENDER_BACKOFF_S = (30, 120)

# Title words that do not identify a meeting. A subject built from "Weekly sync"
# tells the reader nothing, so when a title has no distinctive token left after
# this filter the counterparty name from the attendee list is used instead.
#
# Deliberately a local set, not an import: the deal-matching stoplist this
# mirrors lives in another repo, and copying a stoplist across repos is how the
# two silently diverge. This one is scoped to calendar titles only.
GENERIC_TITLE_TOKENS = {
    "meeting", "call", "sync", "syncup", "standup", "check", "checkin",
    "touchbase", "catchup", "chat", "discussion", "intro", "kickoff",
    "weekly", "biweekly", "monthly", "daily", "quarterly", "recurring",
    "update", "updates", "review", "session", "block", "hold", "placeholder",
    "zoom", "teams", "meet", "webex", "hangout", "phone", "conference",
    "and", "the", "with", "for", "our", "your", "team", "internal", "external",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _strip_dashes(text: str) -> str:
    """Em and en dashes out of anything mailed.

    The interface voice rules bar them, and a subject line is the one string a
    prompt instruction never touches: it is built here, in code.
    """
    return (text or "").replace(chr(0x2014), ", ").replace(chr(0x2013), "-")


def distinctive_tokens(title: str) -> list:
    """Title words that actually name this meeting.

    A token has to be longer than three characters to count, because a two or
    three letter fragment matches too much to identify anything.
    """
    out = []
    for raw in _TOKEN_RE.findall(title or ""):
        token = raw.lower()
        if len(token) > 3 and token not in GENERIC_TITLE_TOKENS:
            out.append(raw)
    return out


def subject_for(event: dict, now: datetime | None = None) -> str:
    """`Duke Energy PPA Meeting Prep @10:00am PT`.

    Counterparty first: it is the word the eye catches on a lock screen, the
    word searched for weeks later, and the half that survives truncation. The
    time is local only, because this is the user reading their own calendar and
    the subject field is where space is tightest. Both zones stay in the body.
    """
    title = _strip_dashes(str(event.get("title") or "").strip())
    if not distinctive_tokens(title):
        # A generic title names nothing. Fall back to who is on the call.
        names = [a.get("name") or "" for a in event.get("attendees") or []]
        names = [n for n in names if n]
        if names:
            title = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"
    title = title or "Meeting"
    when = str(event.get("time") or "").strip()
    # `time` arrives as "10:00 AM PT / 1:00 PM ET"; the subject takes the first.
    local = when.split("/")[0].strip()
    local = local.replace(" AM", "am").replace(" PM", "pm")
    return f"{title} Meeting Prep @{local}" if local else f"{title} Meeting Prep"


def daily_subject(count: int, now: datetime | None = None) -> str:
    """`Meeting Prep: 3 calls today`. Plural handled, zero never mails."""
    now = now or datetime.now()
    if count == 1:
        return "Meeting Prep: 1 call today"
    return f"Meeting Prep: {count} calls today"


# ── Ledger ───────────────────────────────────────────────────────────────────

def ledger_path() -> Path:
    return config_loader.logs_dir() / "prep-sent.json"


def _read_ledger() -> dict:
    try:
        data = json.loads(ledger_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_ledger(rows: dict) -> None:
    """Best effort: a ledger that cannot be written must not wedge the tick.

    The cost of failing here is a duplicate prep on the next tick, which is
    strictly better than a crash that stops every future one.
    """
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def event_key(event: dict) -> str:
    """A stable id for one meeting.

    Prefers the provider's own event id. Falls back to title plus start instant,
    which is what merge_events already uses to dedupe the same meeting arriving
    from two accounts. Never truncates the provider id: Graph ids share a long
    constant prefix, so a truncated key collapses unrelated meetings onto one
    row and one prep would suppress every other.
    """
    raw = str(event.get("id") or "").strip()
    if raw:
        return raw
    return f"{event.get('title') or ''}|{event.get('_sort') or event.get('dt_utc') or ''}"


def prune(rows: dict, now: datetime | None = None) -> dict:
    """Drop rows past retention. Unparseable timestamps are dropped too."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LEDGER_RETENTION_DAYS)
    out = {}
    for key, sent_at in (rows or {}).items():
        try:
            when = datetime.fromisoformat(str(sent_at))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            out[key] = sent_at
    return out


def already_sent(rows: dict, event: dict) -> bool:
    return event_key(event) in (rows or {})


def mark_sent(rows: dict, event: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = dict(rows or {})
    rows[event_key(event)] = now.isoformat(timespec="seconds")
    return rows


# ── Selection ────────────────────────────────────────────────────────────────

def has_external(event: dict) -> bool:
    """Whether anyone outside the team is on the call.

    `meeting_prep` already strips the user and internal teammates out of the
    attendee list, so a non-empty list is exactly the external set.
    """
    return bool(event.get("attendees"))


def due(events: list, rows: dict, now: datetime | None = None,
        lead_minutes: int | None = None, include_internal: bool | None = None) -> list:
    """Meetings to prep on this tick, in start order.

    A meeting qualifies when it starts inside the lead window, has not started
    yet, has not already been sent, and has someone external on it. The upper
    bound is the window and the lower bound is now: a call already underway is
    never prepped, because a brief for a meeting in progress is worse than none.
    """
    now = now or datetime.now(timezone.utc)
    lead = lead_minutes if lead_minutes is not None else config_loader.prep_lead_minutes()
    internal = (include_internal if include_internal is not None
                else config_loader.prep_include_internal())
    horizon = now + timedelta(minutes=lead)
    out = []
    for event in events or []:
        start = event.get("dt_utc")
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if not (now < start <= horizon):
            continue
        if not internal and not has_external(event):
            continue
        if already_sent(rows, event):
            continue
        out.append(event)
    out.sort(key=lambda e: e["dt_utc"])
    return out


def todays_events(events: list, now: datetime | None = None,
                  include_internal: bool | None = None) -> list:
    """Every still-upcoming meeting today, for the daily digest.

    Past meetings are dropped even in daily mode: an email that opens with a
    call which finished an hour ago reads as stale for the ones that have not.
    """
    now = now or datetime.now(timezone.utc)
    internal = (include_internal if include_internal is not None
                else config_loader.prep_include_internal())
    try:
        local_today = now.astimezone().date()
    except (ValueError, OSError):
        local_today = now.date()
    out = []
    for event in events or []:
        start = event.get("dt_utc")
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            continue
        try:
            if start.astimezone().date() != local_today:
                continue
        except (ValueError, OSError):
            continue
        if not internal and not has_external(event):
            continue
        out.append(event)
    out.sort(key=lambda e: e["dt_utc"])
    return out


def daily_due(now: datetime | None = None, rows: dict | None = None,
              daily_time: str | None = None) -> bool:
    """Whether this tick is the one that sends the daily digest.

    True when the configured send time falls inside the tick that just closed,
    and the digest has not already gone out today. Keyed on the local date so a
    machine asleep at the send time mails on its first tick after waking rather
    than skipping the day.
    """
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.astimezone()
    raw = daily_time or config_loader.prep_daily_time()
    try:
        hour, minute = (int(p) for p in raw.split(":"))
    except (TypeError, ValueError):
        return False
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < target:
        return False
    key = f"daily:{now.date().isoformat()}"
    return key not in (rows or {})


def mark_daily_sent(rows: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.astimezone()
    rows = dict(rows or {})
    rows[f"daily:{now.date().isoformat()}"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return rows
