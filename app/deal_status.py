#!/usr/bin/env python3
"""deal_status.py: shared counterparty kill/pass detector for every briefing.

A LEAF module: imports only stdlib + hotcache_sync (which is itself stdlib +
config_loader). It must NOT import any briefing (afternoon_tea / week_review /
morning_coffee / week_retro), so the briefings can all import it without a cycle.

The kill scan is the load-bearing, noise-immune part: a strict regex on pass
language (KILL_RE) never fires on a newsletter, so the alert list is robust
regardless of inbox noise. Thread attribution uses the distinctive-token
match_deal (an upgrade over a raw token-overlap), so a lone generic-token
overlap never ties a kill to the wrong deal. Kills are surfaced for human
review, NEVER auto-actioned: a counterparty pass is a human-confirmed status
change, never an automatic dead-mark.
"""
import re

from hotcache_sync import match_deal

# Counterparty "we're out" language. Surfaced for human review, never
# auto-actioned, so erring toward recall (some false positives) is correct.
KILL_PATTERNS = [
    r"not\s+(?:be\s+)?(?:moving|going)\s+forward",
    r"won'?t\s+be\s+(?:moving|going)\s+forward",
    r"will\s+not\s+be\s+(?:moving|going|proceeding)",
    r"decided\s+(?:not\s+to|to\s+pass|against)",
    r"decided\s+not\s+to\s+(?:move|proceed|pursue)",
    r"releas\w*\s+.*\s+from\s+exclusivity",
    r"pass(?:ing)?\s+on\s+(?:the|this|our|your)",
    r"we'?ll\s+pass",
    r"going\s+to\s+pass",
    r"no\s+longer\s+(?:interested|moving|pursuing|proceeding)",
    r"not\s+proceeding",
    r"won'?t\s+be\s+proceeding",
    r"step(?:ping)?\s+away",
    r"withdraw",
    r"terminat",
    r"declin\w*\s+to\s+(?:move|proceed|participate)",
    r"chosen\s+(?:a\s+different|another)",
    r"going\s+(?:in\s+)?a\s+different\s+direction",
    r"pull(?:ing)?\s+out\s+of",
    r"kill(?:ing)?\s+the\s+deal",
]
KILL_RE = re.compile("|".join(KILL_PATTERNS), re.IGNORECASE)

# Positive/neutral status-change language. The email KILL_RE is negative-only,
# but a deal that MOVES (a buyer re-engaging, pivoting to other sites, restarting
# diligence, asking for a blended/package offer) is just as much a status change,
# and it is usually announced verbally in a meeting before any email reflects it.
# An email-only scan is structurally blind to it. Surfaced for review, never
# auto-actioned.
REENGAGE_PATTERNS = [
    r"re-?engag",
    r"re-?start\w*\s+(?:the\s+)?(?:diligence|dd|process)",
    r"resum\w*\s+(?:the\s+)?(?:diligence|dd|q\s*&\s*a|process)",
    r"pivot\w*\s+to",
    r"pursu\w*\s+(?:both|instead|a\s+different)",
    r"package\s+deal",
    r"blended\s+offer",
    r"non-?binding\s+offer",
    r"back\s+(?:at|on)\s+the\s+table",
    r"reviv\w*",
]
# Meeting scan = kills + re-engagement, built FROM KILL_PATTERNS so a new kill
# phrase flows into both scans automatically. Exception: two bare single-word
# kill terms are strong in pre-filtered inbound mail but over-fire on meeting
# summaries, which are dense with contract boilerplate ("termination clause",
# "withdrawal of the application"). For the meeting scan they are context-gated
# to a deal-ending object so a clause discussion does not alert.
_MEETING_BROAD = {r"terminat", r"withdraw"}
_MEETING_GATED = [
    r"terminat\w*\s+(?:the\s+)?(?:deal|agreement|engagement|relationship|negotiation|discussion|loi|partnership|contract)",
    r"withdraw\w*\s+(?:from|its?\b)",
]
STATUS_CHANGE_RE = re.compile(
    "|".join([p for p in KILL_PATTERNS if p not in _MEETING_BROAD]
             + _MEETING_GATED + REENGAGE_PATTERNS),
    re.IGNORECASE,
)


def detect_kills(inbound_items, hotcache_threads):
    """Scan inbound mail for counterparty kill/pass language and tie each hit to
    an open hotcache thread when possible. Surfaced for review, never actioned.

    inbound_items: dicts with keys subject, snippet, from, from_email, account,
    and internal (forwarded flag). Forwarded kills (internal sender, external
    content) are included: a counterparty pass often arrives as an internal
    forward. Attribution uses the distinctive-token match_deal (newsletter-immune,
    no lone-generic false ties), an upgrade over a raw token overlap.

    Returns a list of alert dicts, thread-matched first (highest confidence)."""
    alerts = []
    for item in inbound_items:
        text = f"{item.get('subject', '')} {item.get('snippet', '')}"
        m = KILL_RE.search(text)
        if not m:
            continue
        party = (
            f"{item.get('from', '')} {item.get('from_email', '')} "
            f"{item.get('subject', '')}"
        )
        alerts.append({
            "thread": match_deal(party, hotcache_threads),
            "subject": item.get("subject", ""),
            "from": item.get("from", ""),
            "from_email": item.get("from_email", ""),
            "account": item.get("account", ""),
            "signal": m.group(0).strip(),
            "snippet": item.get("snippet", "")[:300],
            "forwarded": item.get("internal", False),
        })
    # Thread-matched alerts first (highest confidence it's a tracked deal).
    alerts.sort(key=lambda a: a["thread"] is None)
    return alerts


def detect_meeting_status_changes(meetings, hotcache_threads):
    """Scan meeting summaries for deal status-change language (kills AND
    re-engagement/pivots) and tie each hit to an open hotcache thread. A status
    change announced in a meeting never appears in sent or inbound mail, so the
    email kill scan is structurally blind to it. Surfaced for human review,
    never auto-actioned.

    meetings: dicts with keys title, date, text (the meeting summary). Unlike the
    inbound-mail scan, an alert is emitted ONLY when it attributes to an open
    deal: meeting summaries are unfiltered (every meeting in the window), so an
    unattributed kill-word hit would be pure noise, whereas inbound mail is
    already a deal-ish slice. Attribution uses the title PLUS the summary so a
    generically-titled meeting ("Catch Up") still ties to the deal it discusses.

    Returns alert dicts shaped to merge into status_change_alerts, tagged
    source='meeting'."""
    alerts = []
    for m in meetings:
        text = m.get("text") or ""
        if not text:
            continue
        hit = STATUS_CHANGE_RE.search(text)
        if not hit:
            continue
        thread = match_deal(f"{m.get('title', '')} {text}", hotcache_threads)
        if thread is None:
            continue  # unattributed meeting hit = noise; require a tracked deal
        i = hit.start()
        snippet = " ".join(text[max(0, i - 120): i + 180].split())
        alerts.append({
            "thread": thread,
            "subject": m.get("title", ""),
            "from": "Meeting notes",
            "from_email": "",
            "account": "notetaker",
            "signal": hit.group(0).strip(),
            "snippet": snippet,
            "date": m.get("date", ""),
            "forwarded": False,
            "source": "meeting",
        })
    return alerts
