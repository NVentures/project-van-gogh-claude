"""week_retro.py pure-logic tests: action-item parsing, calendar gaps, routing.

parse_action_items only scans inside a `## Action Items` section.
calendar_meeting_gaps flags events whose title tokens overlap < 0.4 with every
granola source title. guess_business delegates to config_loader.business_for_text.
"""
import week_retro as wrt


def test_parse_action_items_open_vs_done():
    text = """\
## Summary

Some preamble.

## Action Items

- [ ] open one
- [x] done one
- [ ] open two
- [X] done two

## Notes

- [ ] this is outside the section and ignored
"""
    open_items, done_items = wrt.parse_action_items(text)
    assert open_items == ["open one", "open two"]
    assert done_items == ["done one", "done two"]


def test_parse_action_items_empty_when_no_section():
    text = "## Other\n- [ ] not in action items\n"
    open_items, done_items = wrt.parse_action_items(text)
    assert open_items == []
    assert done_items == []


def test_calendar_meeting_gaps_flags_unmatched_event():
    events = [
        {"subject": "Acme Widget Strategy Sync"},  # matches source below
        {"subject": "Dentist appointment reminder"},  # no source match → gap
    ]
    sources = [{"title": "Acme Widget Strategy Sync"}]
    gaps = wrt.calendar_meeting_gaps(events, sources)
    subjects = [g["subject"] for g in gaps]
    assert "Dentist appointment reminder" in subjects
    assert "Acme Widget Strategy Sync" not in subjects


def test_calendar_meeting_gaps_matching_title_not_flagged():
    events = [{"subject": "Beta Labs Gadget Review Meeting"}]
    sources = [{"title": "Beta Labs Gadget Review Meeting"}]
    gaps = wrt.calendar_meeting_gaps(events, sources)
    assert gaps == []


def test_calendar_meeting_gaps_all_gaps_when_no_sources():
    events = [{"subject": "Random standup"}]
    gaps = wrt.calendar_meeting_gaps(events, [])
    assert len(gaps) == 1


def test_guess_business_routing():
    # business_for_text matches keywords from FIXTURE businesses.
    assert wrt.guess_business("Acme widget roadmap") == "Acme Corp"
    assert wrt.guess_business("Beta gadget launch") == "Beta Labs"
    assert wrt.guess_business("unrelated personal errand") == "Unknown"


def test_business_for_text_direct():
    # week_retro imports business_for_text from config_loader; same routing.
    assert wrt.business_for_text("a widget thing") == "Acme Corp"
    assert wrt.business_for_text("nothing here") is None
