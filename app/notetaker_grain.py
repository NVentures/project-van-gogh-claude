#!/usr/bin/env python3
"""notetaker_grain.py — Grain provider for the notetaker layer.

Implements the five-item provider contract documented in notetaker.py against
Grain's public API (https://developers.grain.com/). Two things differ from
Granola and both are handled here so nothing downstream has to care:

  * **Reads are POSTs.** Grain's v2 read endpoints take a JSON body carrying
    `filter` and `include`, so `_post` is the workhorse and there is no query
    string. Every request also needs a `Public-Api-Version` header.
  * **The summary and the action items are separate fields.** Granola ships one
    markdown blob with an "## Action Items" heading in it, and the whole ingest
    pipeline (meeting_ingest.ACTION_HEADINGS_RE) reads that heading. So
    `_summary_markdown` re-joins Grain's `ai_summary` and `ai_action_items` into
    that one blob rather than teaching every consumer a second shape.

Field names are read defensively (`_pick` accepts snake_case and camelCase, and
both string and object forms of the AI fields): Grain versions its API by date
header, and a wrapper that renames a field must cost precision, not the note.
"""

import os
import sys
from urllib.parse import quote

import requests

import user_state
from notetaker import NotetakerError, widen_since

user_state.load_env()

NAME = "grain"
DISPLAY_NAME = "Grain"
ENV_KEY = "GRAIN_API_KEY"
KEY_HELP = (
    "Get one from Grain: Account settings → Integrations → Personal API "
    "(needs a Starter plan or above; the Free plan has no API access)."
)

BASE_URL = "https://api.grain.com/_/public-api/v2"
API_VERSION = "2025-10-31"
PAGE_SIZE = 50
MAX_PAGES = 25   # Anti-runaway backstop.
TIMEOUT = 30

# What we ask Grain to inline on every recording read. Highlights and media are
# deliberately left off: the summary carries the decision, and the clips are
# weight we would only throw away.
_INCLUDE = {
    "participants": True,
    "ai_summary": True,
    "ai_action_items": True,
    "calendar_event": True,
}


def api_key() -> str | None:
    return os.getenv(ENV_KEY) or None


def _headers() -> dict:
    key = api_key()
    if not key:
        raise NotetakerError(f"{ENV_KEY} is not set. {KEY_HELP}")
    return {
        "Authorization": f"Bearer {key}",
        "Public-Api-Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _raise_for(r, what: str):
    if r.status_code == 401:
        raise NotetakerError(f"Grain 401 Unauthorized — check {ENV_KEY}")
    if r.status_code == 403:
        raise NotetakerError(
            "Grain 403 Forbidden — the token is valid but this workspace has no "
            "API access (Grain requires a Starter plan or above)."
        )
    if r.status_code == 404:
        raise NotetakerError(f"Grain 404 Not Found — {what}")
    if r.status_code == 429:
        raise NotetakerError("Grain 429 Rate Limited — 300 requests/minute per token")
    if not r.ok:
        raise NotetakerError(f"Grain {r.status_code}: {r.text[:200]}")


def _request(method: str, path: str, json_body: dict | None = None):
    try:
        r = requests.request(method, f"{BASE_URL}{path}", headers=_headers(),
                             json=json_body, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise NotetakerError(f"Grain HTTP error: {e}")
    _raise_for(r, path)
    return r


def _post(path: str, json_body: dict | None = None) -> dict:
    r = _request("POST", path, json_body or {})
    try:
        return r.json()
    except ValueError as e:
        raise NotetakerError(f"Grain returned non-JSON: {e}")


def _pick(obj: dict, *names, default=None):
    """First present, non-None value among `names`. Grain's own API is
    snake_case; wrappers and older versions camelCase it."""
    for n in names:
        if isinstance(obj, dict) and obj.get(n) is not None:
            return obj[n]
    return default


def _as_datetime(day: str | None) -> str | None:
    """A YYYY-MM-DD window hint → the ISO 8601 instant Grain's filter wants."""
    day = widen_since(day)
    if not day:
        return None
    return f"{day}T00:00:00Z" if len(day) == 10 else day


def iter_stubs(since: str | None = None, limit: int | None = None):
    """Recording stubs, newest first.

    `since` becomes `filter.after_datetime`; notetaker.py re-filters, so a loose
    server filter is harmless. Pages on Grain's opaque `cursor` and stops when a
    page adds nothing new — the same guard that keeps an unsupported cursor from
    looping forever.
    """
    seen: set[str] = set()
    cursor = None
    yielded = 0
    after = _as_datetime(since)

    for _page in range(MAX_PAGES):
        body: dict = {"include": {"participants": True}, "limit": PAGE_SIZE}
        if after:
            body["filter"] = {"after_datetime": after}
        if cursor:
            body["cursor"] = cursor
        data = _post("/recordings", body)

        recordings = _pick(data, "recordings", "data", default=[]) or []
        new_this_page = 0
        for rec in recordings:
            rid = _pick(rec, "id", "recording_id", "recordingId")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            new_this_page += 1
            yield {
                "id": rid,
                "title": (_pick(rec, "title", default="") or "").strip(),
                "created_at": _started_at(rec) or "",
                "owner": _owner_name(rec),
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        if new_this_page == 0:
            return
        cursor = _pick(data, "cursor", "next_cursor", "nextCursor")
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
    """One Grain recording, mapped to the canonical note shape.

    The transcript is a second endpoint and opt-in for the same reason it is on
    Granola: it is large and almost always noise. A transcript fetch that fails
    costs the transcript, not the note — the summary is what the vault stores.
    """
    raw = _post(f"/recordings/{quote(str(note_id), safe='')}", {"include": dict(_INCLUDE)})
    note = map_note(raw)
    if include_transcript:
        try:
            note["transcript"] = fetch_transcript(note_id)
        except NotetakerError:
            note["transcript"] = []
    return note


def fetch_transcript(recording_id: str) -> list[dict]:
    """Grain's transcript endpoint → canonical `[{"speaker": {"source"}, "text"}]`."""
    r = _request("GET", f"/recordings/{quote(str(recording_id), safe='')}/transcript")
    try:
        data = r.json()
    except ValueError as e:
        raise NotetakerError(f"Grain transcript returned non-JSON: {e}")
    entries = data if isinstance(data, list) else (
        _pick(data, "transcript", "sections", "segments", default=[]) or []
    )
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        text = (_pick(e, "text", default="") or "").strip()
        if not text:
            continue
        speaker = _pick(e, "speaker", "speaker_name", "speakerName", default="") or ""
        if isinstance(speaker, dict):
            speaker = _pick(speaker, "name", "source", default="") or ""
        out.append({"speaker": {"source": speaker}, "text": text})
    return out


# ── Mapping ───────────────────────────────────────────────────────────────────

def _started_at(rec: dict) -> str | None:
    return _pick(rec, "start_datetime", "startDatetime", "started_at", "created_at")


def _owner_name(rec: dict):
    owner = _pick(rec, "owner", "creator")
    if isinstance(owner, dict):
        # Name only. An email here would flow unstripped into shape_note's
        # `owner`, which strip_pii never touches.
        return _pick(owner, "name")
    return owner if isinstance(owner, str) else None


def _text_of(value) -> str:
    """Grain's AI fields arrive as a string, or as an object wrapping one."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(_pick(value, "markdown", "text", "summary", "content", default="")).strip()
    return ""


def _action_item_line(item) -> str:
    """One ai_action_items entry → a markdown bullet, keeping the assignee when
    Grain supplies one so meeting_ingest's assignee flattening still has a name
    to attach the item to."""
    if isinstance(item, str):
        text = item.strip()
        assignee = ""
    elif isinstance(item, dict):
        text = _text_of(_pick(item, "text", "title", "description", "item", default=""))
        assignee = _pick(item, "assignee", "owner", "participant", default="") or ""
        if isinstance(assignee, dict):
            assignee = _pick(assignee, "name", "email", default="") or ""
    else:
        return ""
    if not text:
        return ""
    return f"-   **{assignee}** — {text}" if assignee else f"-   {text}"


def _summary_markdown(rec: dict) -> str:
    """Grain's separate summary + action-item fields → the one markdown blob the
    rest of the pipeline expects, with action items under the "## Action Items"
    heading that meeting_ingest.ACTION_HEADINGS_RE matches."""
    summary = _text_of(_pick(rec, "ai_summary", "aiSummary", "summary", "notes"))
    items = _pick(rec, "ai_action_items", "aiActionItems", "action_items", default=[]) or []
    if isinstance(items, (str, dict)):
        items = [items]
    lines = [line for line in (_action_item_line(i) for i in items) if line]
    if not lines:
        return summary
    block = "## Action Items\n" + "\n".join(lines)
    return f"{summary}\n\n{block}\n" if summary else f"{block}\n"


def _participants(rec: dict) -> list[dict]:
    people = _pick(rec, "participants", "attendees", default=[]) or []
    out = []
    for p in people:
        if isinstance(p, str):
            out.append({"name": p, "email": ""})
            continue
        if not isinstance(p, dict):
            continue
        out.append({
            "name": (_pick(p, "name", "display_name", "displayName", default="") or "").strip(),
            "email": (_pick(p, "email", default="") or "").strip(),
        })
    return [p for p in out if p["name"] or p["email"]]


def map_note(raw: dict) -> dict:
    """A Grain recording → the canonical note shape."""
    rec = _pick(raw, "recording", default=raw) or raw
    if not isinstance(rec, dict):
        raise NotetakerError("Grain returned a recording that is not an object")
    owner_name = _owner_name(rec)
    started = _started_at(rec)
    cal = _pick(rec, "calendar_event", "calendarEvent")
    if isinstance(cal, dict):
        cal = {"scheduled_start_time": _pick(
            cal, "scheduled_start_time", "start_datetime", "startDatetime",
            "start_time", default=started)}
    elif started:
        # Grain's start_datetime *is* the scheduled start; keep the canonical
        # calendar_event populated so note_date() prefers it over created_at.
        cal = {"scheduled_start_time": started}
    else:
        cal = None

    return {
        "id": _pick(rec, "id", "recording_id", "recordingId"),
        "title": _pick(rec, "title", default="") or "Untitled Meeting",
        "created_at": started,
        "updated_at": _pick(rec, "updated_datetime", "updatedDatetime", "updated_at"),
        "summary_markdown": _summary_markdown(rec),
        "attendees": _participants(rec),
        "calendar_event": cal,
        "transcript": [],  # second endpoint; fetch_note fills it on request
        "owner": ({"name": owner_name} if owner_name else None),
        "folder_membership": [
            {"name": _pick(t, "name", default="")}
            for t in (_pick(rec, "teams", default=[]) or [])
            if isinstance(t, dict)
        ],
        "share_url": _pick(rec, "url", "share_url", "shareUrl"),
        "source": NAME,
    }
