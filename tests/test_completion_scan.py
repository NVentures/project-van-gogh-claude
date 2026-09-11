"""completion_scan's meeting-evidence fetch.

The regression this file exists to prevent: for its whole visible history,
completion_scan called ``at.fetch_today_meetings`` — a function that never
existed anywhere — and the fail-open ``except Exception`` turned the
AttributeError into a soft errors entry. The scan ran forever with zero meeting
evidence, and every meeting-driven completion candidate in morning-coffee and
afternoon-tea was silently missing. A phantom call target must fail these tests,
not vanish into the errors list.
"""
import completion_scan as cs
import notetaker


_FIXTURE_MEETING = {
    "title": "Acme Widget Sync",
    "date": "2026-09-08",
    "attendees": ["dana@acme.com", "Wesley Chang"],
    "decisions": [],
    "commitments": [],
}


def test_fetch_returns_meetings_with_no_error(monkeypatch):
    # With the old phantom call, the fixture never came back — the swallowed
    # AttributeError landed in errors instead. Both assertions matter.
    monkeypatch.setattr(notetaker, "fetch_meetings",
                        lambda today, since: [dict(_FIXTURE_MEETING)])
    errors = []
    meetings = cs._fetch_recent_meetings("2026-09-08", "2026-09-07", errors)
    assert meetings == [_FIXTURE_MEETING]
    assert errors == []


def test_fetch_failure_lands_in_errors_not_silence(monkeypatch):
    def boom(today, since):
        raise notetaker.NotetakerError("Granola 429 Rate Limited")

    monkeypatch.setattr(notetaker, "fetch_meetings", boom)
    errors = []
    assert cs._fetch_recent_meetings("2026-09-08", "2026-09-07", errors) == []
    assert len(errors) == 1 and "429" in errors[0]


def test_fetched_meetings_feed_the_signal_matcher(monkeypatch):
    # The fetched shape must actually work as evidence: attendee tokens and
    # title tokens reach the signal set the candidate matchers read.
    # (test_completion_match.py always passes [] for meetings, so nothing else
    # exercises this path.)
    monkeypatch.setattr(notetaker, "fetch_meetings",
                        lambda today, since: [dict(_FIXTURE_MEETING)])
    meetings = cs._fetch_recent_meetings("2026-09-08", "2026-09-07", [])
    sources = cs._signal_sources([], meetings)
    assert len(sources) == 1
    src = sources[0]
    assert {"acme", "widget", "sync"} <= src["subj"]   # title tokens
    assert "dana" in src["id"]                          # attendee local-part
    assert "acme" in src["org"]                         # attendee org handle
    assert src["ev"] == 'met: Acme Widget Sync' 
