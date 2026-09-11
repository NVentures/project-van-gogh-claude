#!/usr/bin/env python3
"""notetaker_granola.py — Granola provider for the notetaker layer.

Implements the five-item provider contract documented in notetaker.py against
Granola's public API (https://docs.granola.ai/introduction). Nothing here knows
about briefings, vaults or windows: it fetches and maps, and notetaker.py does
the rest.
"""

import os
import sys
from urllib.parse import quote

import requests

import user_state
from notetaker import NotetakerError, widen_since

user_state.load_env()

NAME = "granola"
DISPLAY_NAME = "Granola"
ENV_KEY = "GRANOLA_API_KEY"
KEY_HELP = "Get one from Granola: Settings → Personal API Keys."

BASE_URL = "https://public-api.granola.ai/v1"
PAGE_SIZE = 30   # Granola's /notes endpoint hard-caps page_size at 30.
MAX_PAGES = 25   # Anti-runaway backstop (25 * 30 = 750 notes).
TIMEOUT = 30


def api_key() -> str | None:
    return os.getenv(ENV_KEY) or None


def _headers() -> dict:
    key = api_key()
    if not key:
        raise NotetakerError(f"{ENV_KEY} is not set. {KEY_HELP}")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _get(path: str, params: dict | None = None) -> dict:
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=_headers(),
                         params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise NotetakerError(f"Granola HTTP error: {e}")
    if r.status_code == 401:
        raise NotetakerError(f"Granola 401 Unauthorized — check {ENV_KEY}")
    if r.status_code == 404:
        raise NotetakerError(f"Granola 404 Not Found — {path}")
    if r.status_code == 429:
        raise NotetakerError("Granola 429 Rate Limited — slow down or wait")
    if not r.ok:
        raise NotetakerError(f"Granola {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise NotetakerError(f"Granola returned non-JSON: {e}")


def iter_stubs(since: str | None = None, limit: int | None = None):
    """Note stubs, newest first. `since` becomes the server-side created_after
    filter; notetaker.py re-filters, so a loose server filter is harmless.

    Pages until: a page adds nothing new (which is what makes an unsupported or
    wrong cursor param safe rather than infinite), the API hands back no cursor,
    the caller's limit is met, or the page cap trips.
    """
    seen: set[str] = set()
    cursor = None
    yielded = 0
    for _page in range(MAX_PAGES):
        params: dict = {"page_size": PAGE_SIZE}
        if since:
            params["created_after"] = widen_since(since)
        if cursor:
            params["cursor"] = cursor
        body = _get("/notes", params=params)

        new_this_page = 0
        for stub in body.get("notes", []):
            sid = stub.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            new_this_page += 1
            yield {
                "id": sid,
                "title": (stub.get("title") or "").strip(),
                "created_at": stub.get("created_at") or "",
                "owner": (stub.get("owner") or {}).get("name"),
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        if new_this_page == 0:
            return
        cursor = (body.get("next_cursor") or body.get("next_page_token")
                  or body.get("cursor"))
        if not cursor:
            return
    else:
        # Exhausted the page cap with more pages still to come. A truncated
        # window that looks complete is the worst outcome here, so it gets said
        # out loud rather than inferred from a short list.
        sys.stderr.write(
            f"{DISPLAY_NAME}: hit the {MAX_PAGES}-page cap; older meetings in "
            "the window may be omitted.\n"
        )


def latest_id() -> str | None:
    for stub in iter_stubs(limit=1):
        return stub["id"]
    return None


def fetch_note(note_id: str, include_transcript: bool = False) -> dict:
    """One Granola note, mapped to the canonical note shape.

    The transcript is opt-in: it is huge, almost always noise, and costs a
    materially larger response. Callers that want it ask for it.
    """
    params = {"include": "transcript"} if include_transcript else None
    raw = _get(f"/notes/{quote(str(note_id), safe='')}", params=params)
    return map_note(raw)


def map_note(raw: dict) -> dict:
    """Granola's note JSON → the canonical note. Granola's shape *is* the
    canonical shape, so this is mostly a pass-through with the source stamp."""
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Meeting",
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "summary_markdown": raw.get("summary_markdown") or raw.get("summary_text") or "",
        "attendees": [
            {"name": (a or {}).get("name") or "", "email": (a or {}).get("email") or ""}
            for a in (raw.get("attendees") or [])
        ],
        "calendar_event": raw.get("calendar_event"),
        "transcript": raw.get("transcript") or [],
        "owner": raw.get("owner"),
        "folder_membership": raw.get("folder_membership") or [],
        # Granola puts the share link in the summary footer, not a field;
        # meeting_ingest.py pulls it out with SHARE_URL_RE.
        "share_url": None,
        "source": NAME,
    }
