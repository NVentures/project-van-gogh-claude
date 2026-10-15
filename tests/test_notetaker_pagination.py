"""notetaker.fetch_meetings walks the active provider's stub stream until the
window is covered, and the provider's paging guards degrade safely (no infinite
loop) when the cursor is unsupported. Fully offline: requests is monkeypatched.
"""
import notetaker
import notetaker_granola


class _Resp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _stub(nid, date):
    # created_at is parsed as UTC then shifted to the user tz; use midday UTC so
    # the calendar date is tz-stable for the fixture's America/Los_Angeles.
    return {"id": nid, "created_at": f"{date}T18:00:00.000Z"}


def _full(nid):
    return {"id": nid, "title": f"Note {nid}", "summary_markdown": "we agreed to ship",
            "attendees": []}


def _use_granola(monkeypatch):
    """Pin the active provider to Granola with a key, regardless of the
    developer's real environment or config."""
    monkeypatch.setattr(notetaker, "active_name", lambda: "granola")
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: "x")
    monkeypatch.setattr(notetaker_granola, "PAGE_SIZE", 2)


def test_fetch_meetings_paginates_until_window_covered(monkeypatch):
    _use_granola(monkeypatch)

    # Page 1 (newest): two in-window notes + a next_cursor. Page 2: one in-window,
    # one before the window (older) — the walk must stop inside page 2.
    pages = [
        {"notes": [_stub("a", "2026-05-29"), _stub("b", "2026-05-28")], "next_cursor": "C2"},
        {"notes": [_stub("c", "2026-05-24"), _stub("d", "2026-05-20")]},  # d is before window
    ]

    calls = {"list": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/notes"):
            idx = 1 if (params or {}).get("cursor") == "C2" else 0
            calls["list"] += 1
            return _Resp(pages[idx])
        nid = url.rsplit("/", 1)[-1]
        return _Resp(_full(nid))

    monkeypatch.setattr(notetaker_granola.requests, "get", fake_get)
    meetings = notetaker.fetch_meetings("2026-05-29", "2026-05-23")
    titles = sorted(m["title"] for m in meetings)
    assert titles == ["Note a", "Note b", "Note c"]  # d (05-20) excluded
    assert calls["list"] == 2  # paged once, then stopped at the older page


def test_fetch_meetings_stops_when_cursor_unsupported(monkeypatch):
    # If the API ignores the cursor and returns the same page forever, the
    # no-new-ids guard must stop it — never an infinite loop.
    _use_granola(monkeypatch)
    same_page = {"notes": [_stub("a", "2026-05-29"), _stub("b", "2026-05-28")],
                 "next_cursor": "STUCK"}

    calls = {"list": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/notes"):
            calls["list"] += 1
            return _Resp(same_page)
        return _Resp(_full(url.rsplit("/", 1)[-1]))

    monkeypatch.setattr(notetaker_granola.requests, "get", fake_get)
    meetings = notetaker.fetch_meetings("2026-05-29", "2026-05-23")
    assert sorted(m["title"] for m in meetings) == ["Note a", "Note b"]
    assert calls["list"] == 2  # second call adds no new ids -> stop, not forever
    assert calls["list"] < notetaker_granola.MAX_PAGES


def test_fetch_meetings_empty_without_a_configured_provider(monkeypatch):
    monkeypatch.setattr(notetaker, "active_name", lambda: "granola")
    monkeypatch.setattr(notetaker_granola, "api_key", lambda: None)
    assert notetaker.fetch_meetings("2026-05-29", "2026-05-23") == []
