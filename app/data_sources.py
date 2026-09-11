"""
data_sources.py — Tier 1 / Tier 2 routing for Van Gogh scripts.

Tier 1 (direct OAuth) is the primary path: scripts call google_client /
  microsoft_client as usual.  Detected automatically when GOOGLE_REFRESH_TOKEN_*
  or MS_GRAPH_REFRESH_TOKEN_* env vars are present and non-empty.  This is the
  only mode that runs unattended, so the scheduler and digests require it.

Tier 2 (connectors) is the fallback, for machines where OAuth consent is
  blocked.  The skill prompt fetches and classifies data via MCP connector
  tools, writes a JSON tempfile, and passes it to the script via --input PATH.
  Scripts read pre-classified results directly and skip all direct API calls.
  A human has to be in a chat session for this to happen at all.

There is a third mode, and it is not this one.  `app/connector_fetch.py` reads
  a Claude connector with nobody present, for sources that have no OAuth client
  to reach for at all: the books, in QuickBooks.  It is deliberately not a way
  to schedule Tier 2.  Mail and calendar keep their OAuth clients because those
  are faster, put no model in the path, and cannot be stopped by a token only a
  browser can renew.  See `skills/_shared/connectors.md`.

Usage in a script
-----------------
    import argparse
    from data_sources import is_tier1, load_input, missing_input_error

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON")
    args = parser.parse_args()

    if args.input:
        data = load_input(args.input)
        # use data["calendar"], data["waiting_on_user"], etc.
        ...
        return
    if not is_tier1():
        missing_input_error("script_name.py")
    # Tier 1: call google_client / microsoft_client as normal.

--input JSON schema
-------------------
All keys are optional; missing keys are treated as empty lists.

Keys consumed by each script:

  week_review / morning_coffee (via subprocess)
  -----------------------------------------------
  calendar        list[CalEvent]   — merged events across all accounts
  waiting_on_user list[Thread]     — they replied, user hasn't responded
  inbox_pending   list[Thread]     — inbound, user hasn't replied
  cold_urgent     list[Thread]     — user sent last, no reply > 14 days
  cold_monitor    list[Thread]     — user sent last, no reply 7-14 days
  filtered_pending list[Thread]    — suppressed by classifier (audit only)

  afternoon_tea
  -----------------------------------------------
  sent_today      list[SentItem]   — emails sent by user today
  calendar_tomorrow list[CalEvent] — tomorrow's calendar events

  week_retro
  -----------------------------------------------
  calendar_week   list[RetroEvent] — calendar events Mon-Fri this week

  meeting_prep
  -----------------------------------------------
  calendar        list[PrepEvent]  — upcoming events (next 7 days) with attendees

  calendar_stub_check
  -----------------------------------------------
  calendar        list[StubEvent]  — yesterday's real meetings (all-day/cancelled/
                                     declined dropped; user excluded from attendees)

Type shapes
-----------
CalEvent: {
  source      str  — configured account label ("Gmail", "Outlook", ...)
  title       str  — event title
  day         str  — formatted day, e.g. "Wed May 28"
  time        str  — "9:00 AM PDT" (user's zone; optional secondary appended) or "All day"
  _sort       str  — ISO datetime string for ordering
}

Thread: {
  source               str
  subject              str
  counterparty_name    str
  counterparty_email   str
  age_days             int   — days since user's last send (cold threads)
  reply_age_days       int   — days since counterparty replied (waiting/inbox)
  body_preview         str   — ~150 chars of the last message
  summary              str   — 1-sentence plain English summary
  intent               str   — action_required|deal_signal|decision_point|
                               awaiting_their_move|intro|fyi|closing
  urgency              str   — high|medium|low
  suggested_action     str   — 1-sentence next action
  is_internal          bool
  allowlisted          bool  — deal-critical sender, never suppress
}

SentItem: {
  source        str
  subject       str
  to_name       str
  to_email      str
  sent_at       str  — ISO datetime
  body_preview  str
}

RetroEvent: {
  subject       str
  date          str  — "YYYY-MM-DD"
  duration_min  int
  organizer     str
}

PrepEvent: {
  source     str   — configured account label
  title      str   — event title
  start      str   — ISO datetime (UTC or with offset), e.g. "2026-05-28T16:00:00Z"
  attendees  list  — [{name: str, email: str}]  (internal team filtered by script)
}

StubEvent: {
  source     str        — configured account label
  title      str        — event title
  attendees  list[str]  — attendee display names (user already excluded)
  agenda     str        — invite description / body preview (optional)
}
"""

import json
import os
import sys
import tempfile
from pathlib import Path

try:
    import user_state
    user_state.load_env()
except ImportError:
    pass


def is_tier1() -> bool:
    """True if at least one Tier 1 OAuth refresh token env var is present and non-empty."""
    for key, val in os.environ.items():
        if key.startswith(("GOOGLE_REFRESH_TOKEN_", "MS_GRAPH_REFRESH_TOKEN_")):
            if val.strip():
                return True
    return False


def load_input(path: str) -> dict:
    """Load and return the Tier 2 pre-fetched data tempfile."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"--input file not found: {path}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def missing_input_error(script_name: str) -> None:
    """Print a self-recovery message and exit 1."""
    input_path = Path(tempfile.gettempdir()) / "van-gogh-input.json"
    sys.stderr.write(
        f"MISSING_INPUT: {script_name} found no Tier 1 OAuth credentials.\n"
        f"\nBest fix: run /van-gogh:install-van-gogh (or /van-gogh:add-account) to\n"
        f"connect the account over OAuth. That is the supported path, and it is the\n"
        f"only one that lets briefings run on a schedule.\n"
        f"\nTier 2 fallback, if OAuth consent is blocked on this machine: fetch the data\n"
        f"using your Gmail / Microsoft 365 connector tools, write the result JSON to\n"
        f"{input_path}, and re-run the same interpreter and script with\n"
        f"--input {input_path}. See app/data_sources.py for the schema.\n"
    )
    sys.exit(1)
