#!/usr/bin/env python3
"""notetaker.py — provider-agnostic meeting-notetaker layer.

Van Gogh reads meeting notes from an AI notetaker (Granola, Grain, ...). Exactly
**one provider is active at a time**, chosen by `notetaker.provider` in the vault
config. Every script that wants meetings goes through this module; none of them
know which service is behind it.

Like Granola always was, this layer is independent of the Tier 1 / Tier 2 email
routing: it is a direct public-API call keyed on the active provider's API key in
the state `.env`. With no key, the read helpers return empty so callers degrade
gracefully — a missing notetaker weakens a briefing, it never fails one.

═══════════════════════════════════════════════════════════════════════════════
ADDING A PROVIDER
═══════════════════════════════════════════════════════════════════════════════
Write `app/notetaker_<name>.py` exposing the five-item contract below, add it to
`_REGISTRY`, and add its key to `config.template.json`'s notetaker.provider
comment. Nothing else in the codebase changes — windowing, commitment
extraction, PII stripping, date resolution and output shaping all live here and
are shared by every provider.

A USER can also add a provider without touching the repo: a file named
`notetaker_<name>.py` in `~/.config/van-gogh/extensions/` implementing the same
five-item contract joins the registry automatically (see app/extensions.py).
Built-in names win a collision, and a broken extension file surfaces as an
error where the built-ins would, never as silence.

    NAME          str  — registry key, lowercase ("grain")
    DISPLAY_NAME  str  — human name ("Grain")
    ENV_KEY       str  — state .env variable holding the API key
    KEY_HELP      str  — one line telling the user where to get that key

    api_key() -> str | None
        The configured key, or None.

    iter_stubs(since=None, limit=None) -> Iterator[dict]
        Meeting stubs, NEWEST FIRST, each {"id", "title", "created_at", "owner"}.
        Handles its own pagination and anti-runaway cap. `since` is a
        "YYYY-MM-DD" hint the provider may push server-side; over-returning is
        fine (this module re-filters), under-returning is not. Raises
        NotetakerError on an HTTP/auth failure.

    fetch_note(note_id, include_transcript=False) -> dict
        One meeting, mapped to the CANONICAL NOTE below. Raises NotetakerError.

    latest_id() -> str | None
        Id of the most recent meeting.

═══════════════════════════════════════════════════════════════════════════════
CANONICAL NOTE
═══════════════════════════════════════════════════════════════════════════════
The single shape every provider maps into, and the only shape the rest of the
codebase reads. It is Granola's note shape, kept deliberately, so existing
consumers were unchanged when this layer landed:

    {
      "id":                str,
      "title":             str,
      "created_at":        str   ISO 8601,
      "summary_markdown":  str   the AI summary, markdown, with an
                                 "## Action Items" section when the provider
                                 exposes action items separately,
      "attendees":         [{"name": str, "email": str}],
      "calendar_event":    {"scheduled_start_time": str ISO 8601} | None,
      "transcript":        [{"speaker": {"source": str}, "text": str}],
      "owner":             {"name": str} | None,
      "folder_membership": [{"name": str}],
      "share_url":         str | None,
      "source":            str   provider NAME,
    }
"""

import re
import sys
from datetime import datetime, timedelta, timezone

import user_state
from config_loader import notetaker_provider, user_self_entities, user_tz

user_state.load_env()

USER_TZ = user_tz()

# Current API version header value is provider business; nothing generic here.


class NotetakerError(RuntimeError):
    """An HTTP, auth, or protocol failure talking to a notetaker's API."""


# ── Registry ──────────────────────────────────────────────────────────────────

def _load(module_name):
    import importlib
    return importlib.import_module(module_name)


# name → module path. Import is lazy so a provider whose dependency is missing
# can never break the providers that are actually in use.
_REGISTRY = {
    "granola": "notetaker_granola",
    "grain": "notetaker_grain",
}

DEFAULT_PROVIDER = "granola"


def _external_registry() -> dict:
    """User extension providers: name → file path (see app/extensions.py).

    Re-scanned per call (one listdir) so a freshly DROPPED file works without
    a restart (an EDITED file still needs a new process — imports are cached;
    the Workbench must be restarted to pick up an edit). Names are lowercased
    to match provider()'s lookup, which matters on Windows's case-insensitive
    filesystem. Built-in names win a collision, and any scan failure means
    "no extensions" rather than a broken briefing.
    """
    try:
        import extensions
        return {n.lower(): p for n, p in extensions.notetaker_modules().items()
                if n.lower() not in _REGISTRY}
    except (Exception, SystemExit):
        return {}


def _load_external(name: str, path):
    """Import an extension provider, converting any failure to NotetakerError.

    A user file can raise anything at import time (SyntaxError, ImportError,
    even SystemExit); callers of provider() are written against
    NotetakerError, so the conversion is what keeps a broken extension inside
    the normal degraded-with-an-error-line path instead of crashing a run.
    """
    import extensions
    try:
        return extensions.load_module(path, f"van_gogh_ext_notetaker_{name}")
    except (Exception, SystemExit) as e:
        raise NotetakerError(
            f"notetaker extension '{name}' failed to load: {e!r}") from e


def provider_names() -> list[str]:
    """Every registered provider name (built-in and extension), sorted."""
    return sorted(set(_REGISTRY) | set(_external_registry()))


def provider(name: str | None = None):
    """The provider module for `name`, or the active one when name is None."""
    name = (name or active_name()).strip().lower()
    if name in _REGISTRY:
        return _load(_REGISTRY[name])
    external = _external_registry()
    if name in external:
        return _load_external(name, external[name])
    raise NotetakerError(
        f"unknown notetaker '{name}'; known providers: {', '.join(provider_names())}"
    )


def active_name() -> str:
    """The active provider name.

    Config wins. With no configured value we infer: if exactly one provider has
    an API key present, that one is active — which is what makes an existing
    Granola-only install keep working with no config change, and a fresh Grain
    install work the moment its key is written. Ambiguous (several keys, or
    none) falls back to DEFAULT_PROVIDER.
    """
    configured = (notetaker_provider() or "").strip().lower()
    if configured:
        # An unregistered name (a typo) degrades to a working default rather
        # than to silence. config_problem() is what makes it visible; returning
        # the bad name here would make every briefing report zero meetings with
        # no way to tell that apart from a quiet week.
        if configured in _REGISTRY or configured in _external_registry():
            return configured
        return DEFAULT_PROVIDER
    keyed = [n for n in provider_names() if _keyed(n)]
    return keyed[0] if len(keyed) == 1 else DEFAULT_PROVIDER


def config_problem() -> str:
    """One line describing a broken `notetaker.provider`, or "" when it is fine.

    Callers append this to their errors list so a typo shows up in the briefing
    itself. Deliberately silent about the *absence* of a notetaker: not using one
    is a normal state, not a problem to nag about.
    """
    configured = (notetaker_provider() or "").strip().lower()
    if configured and configured not in provider_names():
        return (f"unknown notetaker '{configured}' in config.json "
                f"(known: {', '.join(provider_names())}); "
                f"falling back to {DEFAULT_PROVIDER}")
    # A configured EXTENSION provider whose file will not import is the same
    # failure as a typo, one layer down: the filename scan says it exists, so
    # the membership check passes, and without this load attempt configured()
    # would swallow the import error and every briefing would report zero
    # meetings indistinguishable from a quiet week.
    if configured and configured not in _REGISTRY:
        external = _external_registry()
        if configured in external:
            try:
                _load_external(configured, external[configured])
            except NotetakerError as e:
                return str(e)
    return ""


def _keyed(name: str) -> bool:
    try:
        return bool(provider(name).api_key())
    except (Exception, SystemExit):
        # SystemExit included: a user extension calling sys.exit() at import
        # must not kill key inference, which runs on every briefing.
        return False


def available() -> list[dict]:
    """Every provider with its display name and whether a key is present.
    Used by /van-gogh:update-settings and the install skill to show the user
    where they stand."""
    out = []
    for name in provider_names():
        # The attribute reads sit inside the try: an extension provider
        # missing DISPLAY_NAME, or whose api_key() raises, must cost its own
        # row here, not the whole settings screen.
        try:
            mod = provider(name)
            out.append({
                "name": name,
                "display_name": mod.DISPLAY_NAME,
                "env_key": mod.ENV_KEY,
                "key_help": mod.KEY_HELP,
                "configured": bool(mod.api_key()),
                "active": name == active_name(),
            })
        except (Exception, SystemExit):
            continue
    return out


# ── Active-provider passthrough ───────────────────────────────────────────────

def display_name() -> str:
    return provider().DISPLAY_NAME


def safe_display_name() -> str:
    """display_name() that can never raise, for use INSIDE an except block.

    display_name() goes through provider(), which raises on an unregistered
    `notetaker.provider` config value — so formatting an error message with it
    would raise while handling an error and abort the whole briefing. This
    returns the configured string verbatim in that case, which is also the more
    useful thing to print: it shows the user the typo.
    """
    try:
        return provider().DISPLAY_NAME
    except Exception:
        return active_name() or "notetaker"


def env_key() -> str:
    return provider().ENV_KEY


def key_help() -> str:
    return provider().KEY_HELP


def configured() -> bool:
    """True when the active provider has an API key. Callers gate on this the
    way they used to gate on GRANOLA_API_KEY."""
    try:
        return bool(provider().api_key())
    except Exception:
        return False


def iter_stubs(since: str | None = None, limit: int | None = None):
    return provider().iter_stubs(since=since, limit=limit)


def fetch_note(note_id: str, include_transcript: bool = False) -> dict:
    return provider().fetch_note(note_id, include_transcript=include_transcript)


def latest_id() -> str | None:
    return provider().latest_id()


# ── Shared note handling (every provider gets this free) ──────────────────────

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(\+?1?[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")


def strip_pii(text):
    """Redact emails and phone numbers. Non-strings pass through untouched."""
    if not isinstance(text, str):
        return text
    return PHONE_RE.sub("[phone removed]", EMAIL_RE.sub("[email removed]", text))


def note_date(note: dict, tz=None) -> str | None:
    """The meeting's calendar date as YYYY-MM-DD.

    Prefers the calendar event's scheduled start over created_at: a note written
    up the morning after a late call belongs to the call's date, not the
    write-up's. Falls back to a raw date prefix if the timestamp won't parse, so
    a malformed field costs precision, never the note.
    """
    cal = note.get("calendar_event") or {}
    start = cal.get("scheduled_start_time") or note.get("created_at")
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        return dt.astimezone(tz or timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return start[:10] if isinstance(start, str) else None


def widen_since(day: str | None) -> str | None:
    """A local-timezone window start → a server-side filter hint that cannot
    under-return.

    Callers compute `since` in the USER's timezone; providers push it to an API
    that reads a bare date as UTC midnight. For any user east of UTC, local day
    D begins BEFORE D T00:00Z, so an un-widened hint makes the server withhold
    the first hours of the window — and because stubs arrive newest-first and
    the walk breaks on the first out-of-window date, the client never learns
    anything was held back. Backing the hint off one day makes the filter
    provably loose, which is the direction notetaker.py's provider contract
    requires: "over-returning is fine (this module re-filters), under-returning
    is not."
    """
    if not day:
        return None
    try:
        d = datetime.strptime(day[:10], "%Y-%m-%d") - timedelta(days=1)
    except ValueError:
        return day
    return d.strftime("%Y-%m-%d")


def local_date(raw: str, tz=None) -> str | None:
    """An ISO 8601 timestamp as a YYYY-MM-DD date in the user's zone."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz or USER_TZ).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def attendee_names(note: dict) -> list[str]:
    """Attendee display names, blanks dropped."""
    out = []
    for a in note.get("attendees") or []:
        name = ((a or {}).get("name") or "").strip()
        if name:
            out.append(name)
    return out


def flatten_transcript(transcript) -> str:
    """Canonical transcript turns → one "[Speaker] text" line per turn."""
    if not transcript:
        return ""
    lines = []
    for entry in transcript:
        text = ((entry or {}).get("text") or "").strip()
        if not text:
            continue
        speaker = ((entry or {}).get("speaker") or {}).get("source") or ""
        lines.append(f"[{speaker}] {text}" if speaker else text)
    return "\n".join(lines)


def summary_of(note: dict) -> str:
    """The note's summary markdown, whichever key the provider filled."""
    return note.get("summary_markdown") or note.get("summary_text") or ""


ALL_FIELDS = [
    "id", "title", "date", "attendees", "summary", "transcript",
    "calendar_event", "created_at", "updated_at", "owner", "folder_membership",
    "share_url", "source",
]

DEFAULT_FIELDS = [
    "id", "title", "date", "attendees", "summary", "transcript", "calendar_event",
]


def shape_note(note: dict) -> dict:
    """Canonical note → the compact, PII-stripped contract that skills consume."""
    return {
        "id": note.get("id"),
        "title": note.get("title") or "Untitled Meeting",
        "date": note_date(note),
        "attendees": attendee_names(note),
        "summary": strip_pii(summary_of(note)),
        "transcript": strip_pii(flatten_transcript(note.get("transcript"))),
        "calendar_event": note.get("calendar_event"),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "owner": (note.get("owner") or {}).get("name"),
        "folder_membership": [
            (f or {}).get("name") for f in (note.get("folder_membership") or [])
        ],
        "share_url": note.get("share_url"),
        "source": note.get("source"),
    }


def filter_fields(shaped: dict, fields) -> dict:
    return {k: shaped.get(k) for k in fields if k in shaped}


# ── Decision / commitment extraction ──────────────────────────────────────────
# Decision markers (counterparty or shared) vs commitment markers (something the
# user owns). Self-entity names anchor commitments; build the alternation from a
# parts list so an empty self_entities config can't collapse into an empty
# alternative that matches every line.
_DECISION_RE = re.compile(r"decided|agreed|will move|approved|confirmed", re.I)
_commit_parts = [re.escape(e) for e in user_self_entities()] + [
    r"\bfollow.?up\b", r"\baction item\b", r"\bI will\b", r"\bwe will\b",
]
_COMMIT_RE = re.compile("|".join(_commit_parts), re.I)


def _split_summary(summary: str) -> tuple[list[str], list[str]]:
    decisions, commitments = [], []
    for line in summary.splitlines():
        stripped = line.strip().lstrip("-* ")
        if _DECISION_RE.search(stripped):
            decisions.append(stripped)
        if _COMMIT_RE.search(stripped):
            commitments.append(stripped)
    return decisions, commitments


def fetch_meetings(today_str: str, since_str: str | None = None,
                   include_summary: bool = False) -> list[dict]:
    """Meetings created between since_str and today_str (inclusive), in the
    user's timezone, with title, attendees, decisions and commitments.

    since_str defaults to today_str (today only). Returns [] when the active
    provider has no API key. Raises NotetakerError on an HTTP/auth failure so
    callers can surface it in their error list.

    include_summary: when True each meeting also carries a transient
    "summary_text" (the full summary markdown) for the deal status-change scan.
    The full note is already fetched, so this adds no API calls. Callers that
    serialize meetings should pop it after use to keep the output slim.
    """
    since_str = since_str or today_str
    problem = config_problem()
    if problem:
        sys.stderr.write(f"van-gogh: {problem}\n")
    if not configured():
        return []

    # Stubs arrive newest-first; stop as soon as one predates the window, since
    # nothing behind it can be in range. A stub with an unparseable timestamp is
    # skipped rather than ending the walk — one bad record must not truncate the
    # window.
    wanted = []
    for stub in iter_stubs(since=since_str):
        sid = stub.get("id")
        date = local_date(stub.get("created_at") or "")
        if not date:
            continue
        if date < since_str:
            break
        if sid and since_str <= date <= today_str:
            wanted.append((sid, date))

    meetings = []
    for note_id, date in wanted:
        try:
            note = fetch_note(note_id)
        except Exception as e:
            # Broad on purpose, matching the old _fetch_full_note contract: a
            # provider may only promise NotetakerError for HTTP failures, but a
            # malformed payload can raise anything out of its mapping. One bad
            # note must not cost the whole briefing — but it is not free either,
            # so say which meeting went missing rather than dropping it in
            # silence.
            sys.stderr.write(f"{safe_display_name()}: dropped meeting {note_id}: {e}\n")
            continue
        if not note:
            continue
        summary = summary_of(note)
        decisions, commitments = _split_summary(summary)
        meeting = {
            "title": note.get("title") or "(Untitled)",
            "date": date,
            "attendees": attendee_names(note),
            "decisions": decisions[:5],
            "commitments": commitments[:5],
        }
        if include_summary:
            meeting["summary_text"] = summary
        meetings.append(meeting)
    return meetings


# ── Diagnostics ───────────────────────────────────────────────────────────────

def check() -> dict:
    """One round-trip against the active provider, shaped for a skill to render.

    This is the "did I wire the key up right?" probe — it is the only place that
    distinguishes *no key* from *bad key* from *working but empty*, so a user
    switching notetakers gets a specific answer instead of an empty briefing.
    """
    problem = config_problem()
    name = active_name()
    try:
        mod = provider(name)
    except (Exception, SystemExit) as e:
        # Broader than NotetakerError: this is the diagnostic probe, and an
        # extension provider can raise anything at import time — the probe
        # must render the reason, never crash on it.
        return {"ok": False, "provider": name, "reason": str(e) or repr(e)}

    out = {
        "ok": False,
        "provider": name,
        "display_name": mod.DISPLAY_NAME,
        "env_key": mod.ENV_KEY,
        "configured": bool(mod.api_key()),
        "meetings_seen": 0,
        "latest": None,
        "reason": "",
    }
    if problem:
        out["config_problem"] = problem
    if not out["configured"]:
        out["reason"] = (f"{problem}. " if problem else "") + \
            f"{mod.ENV_KEY} is not set in the state .env. {mod.KEY_HELP}"
        return out
    try:
        stubs = list(mod.iter_stubs(limit=3))
    except NotetakerError as e:
        out["reason"] = str(e)
        return out
    out["ok"] = True
    out["meetings_seen"] = len(stubs)
    if stubs:
        out["latest"] = {
            "id": stubs[0].get("id"),
            "title": stubs[0].get("title"),
            "date": local_date(stubs[0].get("created_at") or ""),
        }
    else:
        out["reason"] = (
            f"{mod.DISPLAY_NAME} answered but returned no meetings — the key works; "
            "this account just has nothing recorded yet."
        )
    return out


if __name__ == "__main__":
    import json
    from config_loader import force_utf8_io

    force_utf8_io()
    result = check()
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(0 if result["ok"] else 1)
