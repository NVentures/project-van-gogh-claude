#!/usr/bin/env python3
"""
Notetaker API Fetch

Fetches meeting notes from whichever notetaker is active (Granola, Grain, ...)
and emits compact JSON to stdout. Stays dumb on purpose: no schema mapping, no
Markdown rendering, no file writes. Claude (the orchestrator) consumes the JSON
and does the wiki-side work.

Provider selection, the canonical note shape, and the shared PII/date/transcript
handling all live in app/notetaker.py; this file is only a CLI over them.

USAGE:
    # Which notetaker am I talking to, and does the key work?
    python3 app/meeting_fetch.py --check

    # List recent meetings (id, date, title, owner)
    python3 app/meeting_fetch.py --list --limit 5

    # Fetch a single meeting by ID
    python3 app/meeting_fetch.py --meeting not_abcdef1234567

    # Fetch the most recent meeting (resolved server-side)
    python3 app/meeting_fetch.py --meeting latest

    # Read from a specific provider regardless of config
    python3 app/meeting_fetch.py --list --source grain

    # Trim payload to specific fields (token-thrifty default already set)
    python3 app/meeting_fetch.py --meeting latest \\
        --fields title,date,attendees,summary,transcript

API DOCS: Granola https://docs.granola.ai/introduction
          Grain   https://developers.grain.com/
"""

import argparse
import json
import sys
from typing import NoReturn

import notetaker
from config_loader import force_utf8_io
from notetaker import (
    ALL_FIELDS,
    DEFAULT_FIELDS,
    NotetakerError,
    filter_fields,
    local_date,
    shape_note,
)

# Named exit codes (values are stable; callers may branch on them)
EXIT_NO_API_KEY = 2
EXIT_REQUEST_FAILED = 3
EXIT_UNAUTHORIZED = 4
EXIT_NOT_FOUND = 5
EXIT_RATE_LIMITED = 6
EXIT_HTTP_ERROR = 7
EXIT_NO_NOTES = 8
EXIT_BAD_FIELDS = 9
EXIT_UNKNOWN_SOURCE = 14


def _fail(message: str, code: int) -> NoReturn:
    sys.stderr.write(f"ERROR: {message}\n")
    sys.exit(code)


def _die(err: NotetakerError) -> NoReturn:
    """Map a provider error onto this CLI's stable exit codes. The provider
    raises one exception type with a human message; the codes are what scripts
    and skills branch on, so the mapping lives here rather than in the providers.
    """
    text = str(err)
    if "is not set" in text:
        _fail(f"{text}\nSet it in ~/.config/van-gogh/.env", EXIT_NO_API_KEY)
    for marker, code in (("401", EXIT_UNAUTHORIZED), ("403", EXIT_UNAUTHORIZED),
                         ("404", EXIT_NOT_FOUND), ("429", EXIT_RATE_LIMITED)):
        if marker in text:
            _fail(text, code)
    if "HTTP error" in text:
        _fail(text, EXIT_REQUEST_FAILED)
    _fail(text, EXIT_HTTP_ERROR)


def _provider(args):
    """The provider module this invocation targets, honouring --source."""
    try:
        return notetaker.provider(args.source)
    except NotetakerError as e:
        _fail(str(e), EXIT_UNKNOWN_SOURCE)


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_check(args):
    """Round-trip the active provider and report where the user stands."""
    result = notetaker.check()
    result["available"] = notetaker.available()
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(0 if result["ok"] else 1)


def cmd_list(args):
    mod = _provider(args)
    try:
        stubs = list(mod.iter_stubs(since=args.since, limit=args.limit))
    except NotetakerError as e:
        _die(e)
    rows = [
        {
            "id": s.get("id"),
            "title": s.get("title") or "Untitled",
            "date": local_date(s.get("created_at") or ""),
            "created_at": s.get("created_at"),
            "owner": s.get("owner"),
        }
        for s in stubs
    ]
    json.dump({"source": mod.NAME, "notes": rows, "hasMore": len(rows) >= args.limit},
              sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_meeting(args):
    mod = _provider(args)
    note_id = args.meeting
    try:
        if note_id == "latest":
            note_id = mod.latest_id()
            if not note_id:
                _fail(f"no meetings found in {mod.DISPLAY_NAME}", EXIT_NO_NOTES)
        note = mod.fetch_note(note_id, include_transcript=True)
    except NotetakerError as e:
        _die(e)

    shaped = shape_note(note)
    if args.fields:
        requested = [f.strip() for f in args.fields.split(",") if f.strip()]
        unknown = [f for f in requested if f not in ALL_FIELDS]
        if unknown:
            _fail(f"unknown fields: {unknown}\nValid: {ALL_FIELDS}", EXIT_BAD_FIELDS)
        shaped = filter_fields(shaped, requested)

    json.dump(shaped, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Fetch meeting notes from the active notetaker's public API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub_group = parser.add_mutually_exclusive_group(required=True)
    sub_group.add_argument(
        "--check",
        action="store_true",
        help="Report the active provider, whether its key works, and what else is registered",
    )
    sub_group.add_argument(
        "--list",
        action="store_true",
        help="List recent meetings (id, title, date, owner)",
    )
    sub_group.add_argument(
        "--meeting",
        metavar="ID",
        help="Fetch a single meeting by ID, or 'latest' for the most recent",
    )

    parser.add_argument(
        "--source",
        metavar="NAME",
        help=(
            "Read from this notetaker instead of the configured one. "
            f"Registered: {', '.join(notetaker.provider_names())}"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max meetings to return for --list (default 10)",
    )
    parser.add_argument(
        "--since",
        help="ISO date for --list (e.g. 2026-04-01); meetings on or after it",
    )
    parser.add_argument(
        "--fields",
        help=(
            "Comma-separated field whitelist for --meeting output. "
            f"Default: {','.join(DEFAULT_FIELDS)}. Valid: {','.join(ALL_FIELDS)}"
        ),
        default=",".join(DEFAULT_FIELDS),
    )

    args = parser.parse_args()

    if args.check:
        cmd_check(args)
    elif args.list:
        cmd_list(args)
    else:
        cmd_meeting(args)


if __name__ == "__main__":
    main()
