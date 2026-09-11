"""Grain provider: registry wiring, request shape, and the mapping from Grain's
recording JSON to the canonical note.

This file is the standing check on the half of the integration that cannot be
eyeballed — Grain returns a *different* shape from Granola (POST reads, a
separate ai_action_items field, camelCase in some wrappers) and every one of
those differences is absorbed here rather than downstream. Fully offline:
requests is monkeypatched.
"""
import pytest

import notetaker
import notetaker_grain as grain


class _Resp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _recording(**over):
    rec = {
        "id": "rec-1",
        "title": "Acme / Us Sync",
        "start_datetime": "2026-05-28T18:00:00Z",
        "end_datetime": "2026-05-28T18:30:00Z",
        "url": "https://grain.com/share/recording/rec-1",
        "participants": [
            {"name": "Dana Reyes", "email": "dana@acme.com"},
            {"name": "Wesley Chang", "email": "w@example.com"},
        ],
        "ai_summary": "## Notes\n\n-   We agreed to ship in June.",
        "ai_action_items": [
            {"text": "Draft the pricing page", "assignee": {"name": "Dana Reyes"}},
            "Circle back on the contract",
        ],
        "owner": {"name": "Wesley Chang"},
    }
    rec.update(over)
    return rec


@pytest.fixture
def use_grain(monkeypatch):
    monkeypatch.setattr(notetaker, "active_name", lambda: "grain")
    monkeypatch.setattr(grain, "api_key", lambda: "tok")


# ── Registry ──────────────────────────────────────────────────────────────────

def test_grain_is_registered_and_reachable_through_the_layer(use_grain):
    assert "grain" in notetaker.provider_names()
    assert notetaker.provider() is grain
    assert notetaker.display_name() == "Grain"
    assert notetaker.env_key() == "GRAIN_API_KEY"
    assert notetaker.configured() is True


def test_active_provider_is_inferred_from_the_single_present_key(monkeypatch):
    # An install with only a Grain key must resolve to Grain with no config edit,
    # and one with only a Granola key must still resolve to Granola.
    import notetaker_granola
    monkeypatch.setattr(notetaker, "notetaker_provider", lambda: "")
    monkeypatch.setattr(grain, "api_key", lambda: "tok")
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: None)
    assert notetaker.active_name() == "grain"

    monkeypatch.setattr(grain, "api_key", lambda: None)
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: "tok")
    assert notetaker.active_name() == "granola"

    # Ambiguous (both keyed) falls back to the default rather than guessing.
    monkeypatch.setattr(grain, "api_key", lambda: "tok")
    assert notetaker.active_name() == notetaker.DEFAULT_PROVIDER


def test_unknown_provider_is_a_named_error():
    with pytest.raises(notetaker.NotetakerError) as e:
        notetaker.provider("otter")
    assert "otter" in str(e.value) and "grain" in str(e.value)


# ── Request shape ─────────────────────────────────────────────────────────────

def test_reads_are_posts_carrying_the_version_header(use_grain, monkeypatch):
    seen = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        seen.update(method=method, url=url, headers=headers, body=json)
        return _Resp({"recordings": [_recording()], "cursor": None})

    monkeypatch.setattr(grain.requests, "request", fake_request)
    list(grain.iter_stubs(since="2026-05-01", limit=5))

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/recordings")
    assert seen["headers"]["Public-Api-Version"] == grain.API_VERSION
    assert seen["headers"]["Authorization"] == "Bearer tok"
    # The window hint is pushed server-side as an ISO instant, widened by a day
    # so a UTC-midnight reading of a local date can only ever over-return.
    assert seen["body"]["filter"]["after_datetime"] == "2026-04-30T00:00:00Z"


def test_paging_stops_when_the_cursor_is_ignored(use_grain, monkeypatch):
    calls = {"n": 0}
    page = {"recordings": [_recording(), _recording(id="rec-2")], "cursor": "STUCK"}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _Resp(page)

    monkeypatch.setattr(grain.requests, "request", fake_request)
    stubs = list(grain.iter_stubs())
    assert [s["id"] for s in stubs] == ["rec-1", "rec-2"]
    assert calls["n"] == 2  # second page adds no new ids -> stop, not forever
    assert calls["n"] < grain.MAX_PAGES


@pytest.mark.parametrize("status,fragment", [
    (401, "401"), (403, "Starter plan"), (404, "404"), (429, "429"), (500, "500"),
])
def test_http_failures_surface_as_notetaker_errors(use_grain, monkeypatch, status, fragment):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        return _Resp({}, ok=False, status_code=status)

    monkeypatch.setattr(grain.requests, "request", fake_request)
    with pytest.raises(notetaker.NotetakerError) as e:
        list(grain.iter_stubs())
    assert fragment in str(e.value)


def test_missing_key_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(grain, "api_key", lambda: None)
    with pytest.raises(notetaker.NotetakerError) as e:
        list(grain.iter_stubs())
    assert "GRAIN_API_KEY" in str(e.value)


# ── Mapping ───────────────────────────────────────────────────────────────────

def test_map_note_produces_the_canonical_shape():
    note = grain.map_note(_recording())
    assert note["id"] == "rec-1"
    assert note["title"] == "Acme / Us Sync"
    assert note["source"] == "grain"
    assert note["share_url"] == "https://grain.com/share/recording/rec-1"
    assert note["owner"] == {"name": "Wesley Chang"}
    assert notetaker.attendee_names(note) == ["Dana Reyes", "Wesley Chang"]
    # start_datetime populates calendar_event, so note_date prefers the meeting's
    # scheduled time over any write-up timestamp.
    assert note["calendar_event"]["scheduled_start_time"] == "2026-05-28T18:00:00Z"
    assert notetaker.note_date(note) == "2026-05-28"


def test_action_items_land_under_the_heading_the_ingest_pipeline_reads():
    # meeting_ingest.ACTION_HEADINGS_RE is the contract: Grain's separate
    # ai_action_items field has to become an "## Action Items" section or the
    # whole action-item pipeline silently yields nothing for Grain users.
    import meeting_ingest

    summary = grain.map_note(_recording())["summary_markdown"]
    items, remainder = meeting_ingest.extract_action_items(summary)
    assert any("Draft the pricing page" in i for i in items)
    assert any("Circle back on the contract" in i for i in items)
    assert "We agreed to ship in June." in remainder


def test_summary_survives_when_there_are_no_action_items():
    note = grain.map_note(_recording(ai_action_items=[]))
    assert note["summary_markdown"].strip().endswith("We agreed to ship in June.")
    assert "Action Items" not in note["summary_markdown"]


def test_camelcase_and_string_variants_are_absorbed():
    # Wrappers and older API versions rename fields; a rename must cost
    # precision, never the note.
    note = grain.map_note({
        "id": "rec-9",
        "title": "Kickoff",
        "startDatetime": "2026-06-01T15:00:00Z",
        "aiSummary": {"markdown": "Kickoff notes"},
        "aiActionItems": ["Send the deck"],
        "participants": ["Solo Attendee"],
        "shareUrl": "https://grain.com/share/recording/rec-9",
    })
    assert note["created_at"] == "2026-06-01T15:00:00Z"
    assert notetaker.note_date(note) == "2026-06-01"
    assert "Kickoff notes" in note["summary_markdown"]
    assert "Send the deck" in note["summary_markdown"]
    assert note["attendees"] == [{"name": "Solo Attendee", "email": ""}]
    assert note["share_url"] == "https://grain.com/share/recording/rec-9"


def test_transcript_maps_to_canonical_turns(use_grain, monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        assert method == "GET" and url.endswith("/transcript")
        return _Resp({"transcript": [
            {"speaker": "Dana Reyes", "text": "Morning.", "start": 0},
            {"speaker": {"name": "Wesley Chang"}, "text": "Morning.", "start": 1200},
            {"speaker": "Dana Reyes", "text": "   "},  # blank turn is dropped
        ]})

    monkeypatch.setattr(grain.requests, "request", fake_request)
    turns = grain.fetch_transcript("rec-1")
    assert turns == [
        {"speaker": {"source": "Dana Reyes"}, "text": "Morning."},
        {"speaker": {"source": "Wesley Chang"}, "text": "Morning."},
    ]
    assert notetaker.flatten_transcript(turns) == "[Dana Reyes] Morning.\n[Wesley Chang] Morning."


def test_fetch_note_keeps_the_note_when_the_transcript_fails(use_grain, monkeypatch):
    # The summary is what the vault stores; a transcript endpoint that 500s must
    # cost the transcript, not the meeting.
    def fake_request(method, url, headers=None, json=None, timeout=None):
        if method == "GET":
            return _Resp({}, ok=False, status_code=500)
        return _Resp(_recording())

    monkeypatch.setattr(grain.requests, "request", fake_request)
    note = grain.fetch_note("rec-1", include_transcript=True)
    assert note["title"] == "Acme / Us Sync"
    assert note["transcript"] == []


# ── End-to-end through the layer ──────────────────────────────────────────────

def test_fetch_meetings_reads_grain_through_the_shared_window_logic(use_grain, monkeypatch):
    listing = {"recordings": [
        _recording(id="in", start_datetime="2026-05-28T18:00:00Z"),
        _recording(id="old", start_datetime="2026-05-01T18:00:00Z"),
    ], "cursor": None}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        if url.endswith("/recordings"):
            return _Resp(listing)
        return _Resp(_recording(id="in"))

    monkeypatch.setattr(grain.requests, "request", fake_request)
    meetings = notetaker.fetch_meetings("2026-05-29", "2026-05-23")
    assert [m["date"] for m in meetings] == ["2026-05-28"]
    # The shared commitment/decision scan runs over Grain summaries too.
    assert any("agreed to ship" in d for d in meetings[0]["decisions"])


# ── active_name(): config must beat inference ────────────────────────────────
# The inference branch is covered above. This is the branch a user relies on
# when BOTH keys exist — per skills/update-settings/SKILL.md that is exactly
# when they are told to pin one. A flipped precedence would read the wrong
# notetaker for every such user and pass the whole suite.

def test_configured_provider_beats_a_contradicting_key(monkeypatch):
    import notetaker_granola
    monkeypatch.setattr(notetaker, "notetaker_provider", lambda: "grain")
    monkeypatch.setattr(grain, "api_key", lambda: None)
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: "tok")
    assert notetaker.active_name() == "grain"

    monkeypatch.setattr(notetaker, "notetaker_provider", lambda: "granola")
    monkeypatch.setattr(grain, "api_key", lambda: "tok")
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: None)
    assert notetaker.active_name() == "granola"


# ── The window hint may only ever over-return ────────────────────────────────
# Callers compute `since` in the USER's zone; the APIs read a bare date as UTC
# midnight. East of UTC an un-widened hint silently withholds the first hours
# of the window, and the newest-first break means nobody finds out.

def test_window_hint_is_widened_so_it_cannot_under_return():
    assert notetaker.widen_since("2026-05-23") == "2026-05-22"
    assert notetaker.widen_since(None) is None
    assert notetaker.widen_since("not-a-date") == "not-a-date"


def test_grain_sends_the_widened_instant(use_grain, monkeypatch):
    seen = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        seen.update(body=json)
        return _Resp({"recordings": [], "cursor": None})

    monkeypatch.setattr(grain.requests, "request", fake_request)
    list(grain.iter_stubs(since="2026-05-23"))
    assert seen["body"]["filter"]["after_datetime"] == "2026-05-22T00:00:00Z"


# ── A malformed payload must be rejected at the boundary ─────────────────────

def test_map_note_rejects_a_non_object_recording():
    with pytest.raises(notetaker.NotetakerError):
        grain.map_note(["not", "an", "object"])


def test_owner_never_carries_an_email(use_grain):
    note = grain.map_note(_recording(owner={"email": "dana@acme.com"}))
    # No name -> no owner at all, rather than an unstripped email in shape_note.
    assert note["owner"] is None
    assert "dana@acme.com" not in str(notetaker.shape_note(note))
