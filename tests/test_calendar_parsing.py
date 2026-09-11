"""Parsing tests for the OAuth-migrated calendar/mail helpers.

The migration swapped the data-fetch layer (CLI subprocess → direct-OAuth
clients) but the JSON shapes are identical, so the downstream parsers are
unchanged. These tests pin that contract: they feed the parsers the native
Google Calendar (`summary` / `start.dateTime`) and Microsoft Graph
(`subject` / `start.dateTime`, the shape `calendar_view` returns) event objects
and assert the same normalized output, guarding against a regression in the
parsing code the migration touched around.

conftest.py primes config_loader and puts app/ on sys.path.
"""
import afternoon_tea as at


def test_parse_google_calendar_event():
    # google_client(...).calendar.events().list(...) item shape.
    item = {"summary": "Acme Strategy Sync", "start": {"dateTime": "2026-05-29T10:00:00-07:00"}}
    e = at._parse_calendar_event(item, "summary", ["start", "dateTime"], "Gmail")
    assert e is not None
    assert e["title"] == "Acme Strategy Sync"
    assert e["source"] == "Gmail"
    assert e["time"] != "All day"


def test_parse_outlook_calendar_event():
    # microsoft_client(...).calendar_view(...) value[] shape.
    item = {"subject": "Beta Board Call", "start": {"dateTime": "2026-05-29T17:00:00.0000000"}}
    e = at._parse_calendar_event(item, "subject", ["start", "dateTime"], "Outlook")
    assert e is not None
    assert e["title"] == "Beta Board Call"
    assert e["source"] == "Outlook"


def test_parse_all_day_event():
    item = {"summary": "Company Holiday", "start": {"date": "2026-05-29"}}
    e = at._parse_calendar_event(item, "summary", ["start", "dateTime"], "Gmail")
    assert e is not None
    assert e["time"] == "All day"


def test_parse_calendar_noise_dropped():
    item = {"summary": "Accepted: Lunch", "start": {"dateTime": "2026-05-29T12:00:00Z"}}
    assert at._parse_calendar_event(item, "summary", ["start", "dateTime"], "Gmail") is None


def test_parse_missing_start_returns_none():
    assert at._parse_calendar_event({"summary": "No time"}, "summary", ["start", "dateTime"], "Gmail") is None


def test_detect_completed_items_token_overlap():
    open_items = ["Send Acme widget proposal", "Book dentist appointment"]
    sent_today = [{"subject": "Acme widget proposal v2", "to": "ceo@acme.com", "to_email": "ceo@acme.com"}]
    completed, still_open = at.detect_completed_items(open_items, sent_today, [])
    assert "Send Acme widget proposal" in completed
    assert "Book dentist appointment" in still_open


def test_detect_completed_items_matches_meeting_attendees():
    open_items = ["Follow up with Jordan Rivera"]
    meetings = [{"title": "Quarterly review", "attendees": ["Jordan Rivera"]}]
    completed, still_open = at.detect_completed_items(open_items, [], meetings)
    assert completed == ["Follow up with Jordan Rivera"]
    assert still_open == []
