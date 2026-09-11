#!/usr/bin/env python3
"""What kind of failure was that? One vocabulary for every unattended path.

Van Gogh used to treat every failure the same: two attempts, 30 seconds apart,
then 120, then give up until the next calendar slot. That is right for a
network blip and wrong for everything else. A subscription usage cap is a
clock, not a fault, and retrying inside it burns the retry budget for nothing.
An expired login cannot be fixed by trying again either. A CLI too old for the
current model fails identically forever until the binary moves.

So a failure gets a class, and the class decides what happens next:

* ``quota``       a usage cap. Wait for the reset time in the message.
* ``auth``        a login expired. Retrying cannot help; a human must log in.
* ``connector``   a Claude connector (QuickBooks and the like) is unauthorized
                  or missing. Anthropic holds that token and only the user can
                  refresh it, from a browser, so no unattended retry can help.
* ``version``     the CLI is older than the model needs. Update, then retry.
* ``classifier``  an Anthropic-side safety-classifier outage. It flaps, so
                  defer and try later; only a successful run proves recovery.
* ``network``     a transient connection failure. Retry.
* ``unknown``     everything else. Retry, then say so.

The patterns are **captured signatures, not guesses**: each one was written
against the real text a real failing run produced. Widening one is cheap and
narrowing one is not, so they stay high-precision and the classes they cannot
name fall through to ``unknown``, which retries exactly as the old code did.

Ordering is deliberate. ``connector`` is tested first because its message is
about a third party's token and would otherwise be read as the user's own
expired login, which sends them to re-authorize Claude when the thing that
needs reconnecting is QuickBooks. ``version`` is tested before ``quota``
because the stale-CLI message names a version number and a reset-time pattern
could be coaxed into matching one. ``quota`` beats ``auth`` because a message
can say both "usage limit" and "login" while only the cap is actionable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# A Claude connector that is unauthorized or absent. Deliberately narrow: it
# keys on the literal token `connector_fetch` stamps, plus the CLI's own
# phrasing for an expired connector token, so it can never swallow an ordinary
# Google or Microsoft 401. The distinction matters to the reader, who is told
# to reconnect a specific third-party service rather than to sign in again.
CONNECTOR_RE = re.compile(
    r"CONNECTOR-UNAVAILABLE:"
    r"|MCP server \"[^\"]+\" requires re-?authorization"
    r"|connector (?:is )?(?:not connected|needs authorizing)",
    re.I)

# A login that has expired or was never made. Retrying is waste: nothing about
# a second attempt makes a refresh token valid again.
AUTH_RE = re.compile(
    r"Not logged in|OAuth access token has expired|Please run /login"
    r"|invalid_grant|RefreshError|401 Unauthorized|insufficient authentication"
    # A credential that was never there is as much an auth failure as one that
    # expired, and retrying it is just as useless. Without this a machine that
    # has not been authorised at all classifies as "unknown" and gets the
    # transient treatment forever.
    r"|Missing refresh token|is not set\. Run: python app/auth_bootstrap"
    # A token minted before a new scope was added is a live, refreshable
    # credential that simply lacks the permission: Google answers 403 with
    # ACCESS_TOKEN_SCOPE_INSUFFICIENT ("Request had insufficient authentication
    # scopes"). Retrying cannot grant a scope, so this belongs in the no-retry
    # auth class and not in "unknown", where the watcher would re-run it
    # forever. Reaching it requires consenting again, which only a human can do.
    r"|ACCESS_TOKEN_SCOPE_INSUFFICIENT|insufficient authentication scopes"
    r"|insufficientPermissions|Request had insufficient authentication",
    re.I)

# The Anthropic-side safety classifier is unavailable. It flaps: one passing
# call does not prove recovery, which is why the caller defers rather than
# hammering.
CLASSIFIER_RE = re.compile(
    r"cannot determine (?:the )?safety|safety[ -]classifier"
    r"|classifier outage|temporarily unavailable",
    re.I)

# The subscription usage cap. Carries a reset time often enough that
# quota_reset_at can read it; when it does not, the caller holds an hour.
#
# The last three alternatives are wider than the rest on purpose. This pattern
# is also what the priority judge uses to say "the usage limit was reached"
# instead of "the judgment call failed", and that call is allowed to be
# generous: being wrong there costs one softened sentence in a briefing. Being
# wrong here costs an hour of waiting, so the narrow forms above are what the
# reset-time reader keys on, and a bare "quota" only ever means "do not retry
# this now".
QUOTA_RE = re.compile(
    r"out of (?:extra )?usage"
    r"|usage limit (?:reached|hit|exceeded)"
    r"|(?:weekly|5-hour|five-hour) limit (?:reached|hit)"
    r"|reached your (?:weekly|usage) limit"
    r"|resets?(?: at)? \d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r"|rate.?limit"
    r"|\bquota\b"
    r"|429",
    re.I)

# The installed CLI is older than the requested model requires. This is the
# same failure app/claude_update.py heals; the class exists so a watcher can
# hold the job until the update has actually landed rather than re-running it
# against a binary that will fail the same way.
VERSION_RE = re.compile(
    r"does not support this model; version [\d.]+ or newer is required"
    r"|Claude Code [\d.]+ does not support this model"
    r"|Run ['\"`]?claude update",
    re.I)

# A transient connection failure, the one class where the old blind retry was
# already the right answer.
NETWORK_RE = re.compile(
    r"nodename nor servname|Operation timed out|Connection (?:reset|refused)"
    r"|ConnectionError|Max retries exceeded|Temporary failure in name"
    r"|ReadTimeout|ServerDisconnected",
    re.I)

# What a person should do about each class, in their words. Shown in the
# briefing footer and in the audit, so it says what to do and stops.
REMEDY = {
    "auth": "sign in again, then the job runs on its next turn",
    "connector": "reconnect the service in Claude's connector settings, then "
                 "the job runs on its next turn",
    "quota": "nothing to fix, the usage cap resets and the job re-runs itself",
    "version": "nothing to fix, the Claude app updates itself and the job "
               "re-runs",
    "classifier": "nothing to fix, this clears on its own and the job is "
                  "tried again later",
    "network": "nothing to fix, the connection dropped and the job is tried "
               "again",
    "unknown": "check the log for this job in your vault's van-gogh/logs "
               "folder",
}

# Classes no retry can fix. A caller that sees one of these stops after the
# first attempt instead of spending its whole budget on the same wall.
NO_RETRY = frozenset({"quota", "auth", "connector"})


def classify(text: str | None) -> str:
    """Name the failure in `text`, or "" when there is nothing to name.

    Empty input is "" rather than "unknown": nothing was seen, which is not
    the same as something unrecognised.
    """
    if not text:
        return ""
    if CONNECTOR_RE.search(text):
        return "connector"
    if VERSION_RE.search(text):
        return "version"
    if QUOTA_RE.search(text):
        return "quota"
    if AUTH_RE.search(text):
        return "auth"
    if CLASSIFIER_RE.search(text):
        return "classifier"
    if NETWORK_RE.search(text):
        return "network"
    return "unknown"


_RESET_RE = re.compile(
    r"resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def quota_reset_at(text: str | None, now: datetime | None = None):
    """The wall-clock time a quota message says it resets, as a datetime.

    Returns None when the message names no time, which is the caller's signal
    to pick its own hold instead of guessing at one. The returned time is in
    `now`'s own timezone: the message is written in the user's local zone and
    so is every clock this reads it against.

    A time that has already passed today means tomorrow. A cap that resets at
    "3pm" read at 4pm cannot mean an hour ago, and holding until a moment in
    the past is the same as not holding at all.
    """
    if not text:
        return None
    match = _RESET_RE.search(text)
    if not match:
        return None
    now = now or datetime.now()
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()
    if hour == 12:
        hour = 0
    if meridiem == "pm":
        hour += 12
    if hour > 23 or minute > 59:
        return None
    reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return reset


_REQUIRED_RE = re.compile(r"version\s+([\d.]+)\s+or\s+newer\s+is\s+required",
                          re.I)


def required_cli_version(text: str | None) -> str:
    """The CLI version a stale-CLI error demands, or "" if it names none."""
    if not text:
        return ""
    match = _REQUIRED_RE.search(text)
    return match.group(1).rstrip(".") if match else ""


def version_tuple(text: str | None) -> tuple:
    """A dotted version as comparable integers. () when unreadable.

    Compared as numbers, never as strings: "2.1.9" sorts after "2.1.251" as
    text and before it as a version, and holding a job on that comparison
    would release it exactly when it should not.
    """
    if not text:
        return ()
    parts = []
    for chunk in str(text).strip().split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)
