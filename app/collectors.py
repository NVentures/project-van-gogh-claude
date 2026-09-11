#!/usr/bin/env python3
"""collectors.py: the one shared source-collector surface the briefings compose.

This is the anti-drift layer. Every chief-of-staff briefing (morning-coffee,
afternoon-tea, /week, week-retro) should consult the same set of sources; a
coverage audit found Granola and the kill scan had silently fallen out of some
of them. To keep that from re-opening, each briefing declares a `COLLECTORS_USED`
set naming the source categories it consults, and test_briefing_coverage.py
asserts each briefing covers every source its role requires.

collectors owns the genuinely shared, leaf-delegating accessors as real
functions (meetings, kill_scan, deal_match, hotcache_read) and IMPORTS from the
leaves (hotcache_sync for the deal matcher + hotcache parser, deal_status for
the kill scan, notetaker for the meeting API); it never copies their logic. The
mail / calendar / obsidian collectors differ per briefing in window and account,
so collectors defines only their canonical NAMES (the coverage contract); each
briefing wires its own fetchers for those.

Which notetaker the meeting collectors read from (Granola, Grain, ...) is
notetaker.py's business, not this module's — see its provider registry.

Leaf order: hotcache_sync, deal_status, and notetaker import nothing from here,
so there is no cycle.
"""
import os
from datetime import datetime, timedelta, timezone

import notetaker
from config_loader import hotcache_active_threads_heading, user_tz
from hotcache_sync import match_deal, parse_deals
from deal_status import detect_kills, detect_meeting_status_changes

# How many meeting stubs one collector pass will walk. A briefing's window is
# days, not hundreds of meetings; the cap is an anti-runaway backstop.
_STUB_CAP = 200

# Canonical collector names. The COVERAGE manifest and each briefing's
# COLLECTORS_USED reference these, so a typo cannot silently weaken coverage.
SENT_MAIL = "sent_mail"
INBOUND_MAIL = "inbound_mail"
MEETINGS = "meetings"
CALENDAR = "calendar"
HOTCACHE_READ = "hotcache_read"
OBSIDIAN_TASKS = "obsidian_tasks"
KILL_SCAN = "kill_scan"
DEAL_MATCH = "deal_match"

ALL = {
    SENT_MAIL, INBOUND_MAIL, MEETINGS, CALENDAR,
    HOTCACHE_READ, OBSIDIAN_TASKS, KILL_SCAN, DEAL_MATCH,
}


def _list_meetings(window_days, errors=None):
    """Recent meeting stubs (id, title, date) over the window, from whichever
    notetaker is active. Shared by `meetings` (title cross-ref) and
    `meeting_texts` (content scan).

    Fail-open: returns [] if no notetaker is configured or any error occurs (a
    meeting cross-ref is a bonus, never a blocker). The provider filters
    server-side on `since`; the client-side cutoff below is the backstop for a
    provider whose filter is loose, and the walk stops at the first stub older
    than the window since stubs arrive newest-first.
    """
    problem = notetaker.config_problem()
    if problem and errors is not None:
        errors.append(f"Notetaker config: {problem}")
    if not notetaker.configured():
        return []
    tz = user_tz()
    today_local = datetime.now(timezone.utc).astimezone(tz)
    cutoff = (today_local - timedelta(days=window_days)).strftime("%Y-%m-%d")
    out = []
    walked = 0
    try:
        for stub in notetaker.iter_stubs(since=cutoff, limit=_STUB_CAP):
            walked += 1
            title = (stub.get("title") or "").strip()
            nid = stub.get("id")
            date = notetaker.local_date(stub.get("created_at") or "", tz)
            if not date:
                continue
            if date < cutoff:  # newest-first: nothing behind this is in range
                break
            if not title or not nid:
                continue
            out.append({"id": nid, "title": title, "date": date})
        else:
            # Fell off the end of the stub stream without ever seeing one older
            # than the cutoff. If we also hit the cap, the window is truncated
            # and the caller has no way to tell — say so, exactly as the old
            # hasMore warning did. Never silently dropped.
            if walked >= _STUB_CAP and errors is not None:
                errors.append(
                    f"{notetaker.safe_display_name()} meetings: window of "
                    f"{window_days}d exceeded the {_STUB_CAP}-meeting cap; "
                    "older meetings not pulled"
                )
    except Exception as e:
        # Deliberately broad, and the docstring's promise depends on it: the
        # provider contract only requires NotetakerError for HTTP/auth failures,
        # so a malformed payload can still surface a TypeError from deep inside
        # a provider's mapping. A meeting cross-ref is a bonus; it must never be
        # the thing that takes a briefing down.
        if errors is not None:
            errors.append(f"{notetaker.safe_display_name()} meetings: {e}")
        return out


def meetings(window_days, errors=None):
    """Recent meeting titles + dates over the window (the met_since title
    cross-ref). Fail-open. See _list_meetings."""
    return [{"title": m["title"], "date": m["date"]} for m in _list_meetings(window_days, errors)]


def meeting_texts(window_days, errors=None, max_fetch=30):
    """Recent meetings WITH summary content, for the deal status-change scan (a
    kill or pivot announced verbally in a meeting is invisible to the email
    scan). Stub listings carry no summary, so each note is fetched by id; the
    transcript is intentionally NOT pulled (the summary carries the decision;
    the transcript is noise + cost). Bounded by max_fetch. Fail-open per
    meeting: a fetch error drops that meeting's text to '' rather than failing
    the briefing."""
    if not notetaker.configured():
        return []
    out = []
    for m in _list_meetings(window_days, errors)[:max_fetch]:
        text = ""
        try:
            note = notetaker.fetch_note(m["id"])
            text = notetaker.summary_of(note).strip()
        except Exception as e:  # broad on purpose — see _list_meetings
            if errors is not None:
                errors.append(f"{notetaker.safe_display_name()} meeting {m['id']} summary: {e}")
        out.append({"title": m["title"], "date": m["date"], "text": text})
    return out


def kill_scan(inbound_items, hotcache_threads):
    """Counterparty kill/pass scan over inbound mail (delegates to
    deal_status.detect_kills)."""
    return detect_kills(inbound_items, hotcache_threads)


def meeting_status_scan(meeting_items, hotcache_threads):
    """Deal status-change scan over notetaker meeting summaries (kills AND
    re-engagement/pivots, attributed to open deals). Delegates to
    deal_status.detect_meeting_status_changes. Output merges into the same
    status_change_alerts channel the email kill scan feeds, tagged
    source='meeting'."""
    return detect_meeting_status_changes(meeting_items, hotcache_threads)


def deal_match(party_text, headings):
    """Distinctive-token deal attribution (delegates to hotcache_sync.match_deal)."""
    return match_deal(party_text, headings)


def hotcache_read(path):
    """Active-threads deals (heading, meta_idx, fields{stage,last_contact,...})
    from a hotcache file, via the shared hotcache_sync.parse_deals under the
    configured active-threads heading. Returns [] if the file is missing
    (fail-open)."""
    if not os.path.exists(path):
        return []
    try:
        heading = hotcache_active_threads_heading()
    except Exception:
        heading = "Active Threads"
    with open(path, encoding="utf-8") as f:
        return parse_deals(f.readlines(), heading)
