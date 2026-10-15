"""Prep email selection, subjects and the sent-ledger.

The tests that matter here are the negative ones. A prep that fails to send is
a missing email; a prep that sends four times, or sends for a call already
underway, is why someone turns the feature off. So every suppression rule gets
a planted case sitting just past its boundary, not on it.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import prep_email  # noqa: E402

UTC = timezone.utc


def evt(title, minutes_out, attendees=("Sarah Whitfield",), eid=None, now=None):
    """A calendar event `minutes_out` from now, in the shape merge_events emits."""
    now = now or datetime.now(UTC)
    start = now + timedelta(minutes=minutes_out)
    return {
        "id": eid or f"{title}-{minutes_out}",
        "title": title,
        "dt_utc": start,
        "_sort": start.isoformat(),
        "time": "10:00 AM PT / 1:00 PM ET",
        "attendees": [{"name": n, "email": f"{n.split()[0].lower()}@duke.com"}
                      for n in attendees],
    }


# ── The expiry rule: the whole feature is one comparison ──────────────────────

def test_meeting_already_started_is_never_prepped():
    """A brief for a call in progress is worse than none: the reader trusts it."""
    now = datetime.now(UTC)
    past = evt("Duke Energy PPA", -5, now=now)
    assert prep_email.due([past], {}, now=now, lead_minutes=60) == []


def test_meeting_exactly_now_is_not_prepped():
    """Boundary sits ON the exclusion, so a zero-minute event must not pass."""
    now = datetime.now(UTC)
    starting = evt("Duke Energy PPA", 0, now=now)
    assert prep_email.due([starting], {}, now=now, lead_minutes=60) == []


def test_meeting_inside_window_is_prepped():
    now = datetime.now(UTC)
    soon = evt("Duke Energy PPA", 45, now=now)
    assert len(prep_email.due([soon], {}, now=now, lead_minutes=60)) == 1


def test_meeting_past_the_window_waits_for_a_later_tick():
    """61 minutes out with a 60 minute lead: past the boundary, not on it."""
    now = datetime.now(UTC)
    far = evt("Duke Energy PPA", 61, now=now)
    assert prep_email.due([far], {}, now=now, lead_minutes=60) == []


# ── The ledger: a meeting sits in the window for four consecutive ticks ───────

def test_sent_once_is_not_sent_again():
    now = datetime.now(UTC)
    meeting = evt("Duke Energy PPA", 50, now=now)
    rows = prep_email.mark_sent({}, meeting, now=now)
    assert prep_email.due([meeting], rows, now=now, lead_minutes=60) == []


def test_four_consecutive_ticks_send_exactly_one():
    """The real shape: same meeting, four ticks, one email."""
    now = datetime.now(UTC)
    start = now + timedelta(minutes=58)
    rows, sent = {}, 0
    for tick in range(4):
        at = now + timedelta(minutes=15 * tick)
        meeting = {"id": "duke-1", "title": "Duke Energy PPA", "dt_utc": start,
                   "_sort": start.isoformat(), "time": "10:00 AM PT",
                   "attendees": [{"name": "Sarah Whitfield"}]}
        for due in prep_email.due([meeting], rows, now=at, lead_minutes=60):
            sent += 1
            rows = prep_email.mark_sent(rows, due, now=at)
    assert sent == 1


def test_event_key_never_truncates_a_provider_id():
    """Graph ids share a long constant prefix: a truncated key collapses them."""
    shared = "AAMkAGI2NjhmYmEwLTRiNTYtNDE0ZC05NGNkLTFiMjliNTBmNmM4Ywe"
    a = {"id": shared + "AAAA", "title": "Duke", "dt_utc": datetime.now(UTC)}
    b = {"id": shared + "BBBB", "title": "AES", "dt_utc": datetime.now(UTC)}
    assert prep_email.event_key(a) != prep_email.event_key(b)


def test_event_without_id_still_gets_a_stable_key():
    meeting = {"title": "Duke Energy PPA", "_sort": "2026-09-10T17:00:00+00:00"}
    assert prep_email.event_key(meeting) == prep_email.event_key(dict(meeting))


def test_prune_drops_stale_rows_and_keeps_fresh_ones():
    now = datetime.now(UTC)
    rows = {
        "old": (now - timedelta(days=5)).isoformat(),
        "fresh": (now - timedelta(hours=2)).isoformat(),
        "garbage": "not a timestamp",
    }
    pruned = prep_email.prune(rows, now=now)
    assert "fresh" in pruned
    assert "old" not in pruned
    assert "garbage" not in pruned


# ── Internal meetings ────────────────────────────────────────────────────────

def test_internal_meeting_is_skipped_by_default():
    """meeting_prep strips teammates, so an empty attendee list IS internal."""
    now = datetime.now(UTC)
    internal = evt("Team standup", 30, attendees=(), now=now)
    assert prep_email.due([internal], {}, now=now, lead_minutes=60,
                          include_internal=False) == []


def test_internal_meeting_is_included_when_asked():
    now = datetime.now(UTC)
    internal = evt("Team standup", 30, attendees=(), now=now)
    assert len(prep_email.due([internal], {}, now=now, lead_minutes=60,
                              include_internal=True)) == 1


# ── Subjects ─────────────────────────────────────────────────────────────────

def test_subject_is_counterparty_first():
    now = datetime.now(UTC)
    subject = prep_email.subject_for(evt("Duke Energy PPA Review", 55, now=now))
    assert subject == "Duke Energy PPA Review Meeting Prep @10:00am PT"
    assert subject.startswith("Duke")


def test_generic_title_falls_back_to_the_attendee():
    """'Weekly sync Meeting Prep' names nothing; the counterparty does."""
    now = datetime.now(UTC)
    subject = prep_email.subject_for(evt("Weekly sync", 55, now=now))
    assert "Sarah Whitfield" in subject
    assert "Weekly" not in subject


def test_generic_title_with_several_attendees_counts_the_rest():
    now = datetime.now(UTC)
    meeting = evt("Weekly sync", 55, attendees=("Sarah Whitfield", "Marcus Bell"),
                  now=now)
    assert "Sarah Whitfield +1" in prep_email.subject_for(meeting)


def test_distinctive_tokens_rejects_only_generic_words():
    assert prep_email.distinctive_tokens("Weekly sync") == []
    assert prep_email.distinctive_tokens("Duke Energy PPA") == ["Duke", "Energy"]


def test_subject_carries_no_dashes():
    """The subject is built in code, where a prompt rule cannot reach."""
    now = datetime.now(UTC)
    subject = prep_email.subject_for(
        evt(f"Duke {chr(0x2014)} Acme {chr(0x2013)} PPA", 55, now=now))
    assert chr(0x2014) not in subject and chr(0x2013) not in subject


def test_the_daily_subject_is_the_series_name_and_the_date():
    assert prep_email.daily_subject(now=datetime(2026, 10, 1, 6, 15)) == \
        "Daily Meeting Prep: Oct 01, 26"
    assert prep_email.daily_subject(3, now=datetime(2027, 1, 12, 6, 15)) == \
        "Daily Meeting Prep: Jan 12, 27"
    assert prep_email.daily_subject(now=datetime(2030, 9, 30, 23, 59)) == \
        "Daily Meeting Prep: Sep 30, 30"


def test_every_month_is_three_letters_whatever_the_machine_speaks():
    names = [prep_email.daily_subject(now=datetime(2026, m, 5)).split(": ")[1].split(" ")[0]
             for m in range(1, 13)]
    assert names == ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── Daily mode ───────────────────────────────────────────────────────────────

def test_daily_is_due_after_the_send_time_and_only_once():
    now = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    assert prep_email.daily_due(now=now, rows={}, daily_time="07:00")
    rows = prep_email.mark_daily_sent({}, now=now)
    assert not prep_email.daily_due(now=now, rows=rows, daily_time="07:00")


def test_daily_is_not_due_before_the_send_time():
    now = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    assert not prep_email.daily_due(now=now, rows={}, daily_time="07:00")


def test_todays_events_drops_finished_calls():
    """Pinned to mid-morning, never the wall clock.

    Run after 22:00 local, an event 120 minutes ahead falls on TOMORROW, so
    `todays_events` correctly drops it and the test failed for a reason that
    had nothing to do with the code. A suite that only passes before ten at
    night is a suite nobody trusts at midnight.
    """
    now = datetime.now(UTC).replace(hour=17, minute=0, second=0, microsecond=0)
    events = [evt("Done", -30, now=now), evt("Upcoming", 120, now=now)]
    titles = [e["title"] for e in prep_email.todays_events(events, now=now)]
    assert titles == ["Upcoming"]


# ── Config ───────────────────────────────────────────────────────────────────

def test_lead_minutes_is_clamped_to_what_a_15_minute_tick_can_honour(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "_prep", lambda: {"lead_minutes": 5})
    assert config_loader.prep_lead_minutes() == 15
    monkeypatch.setattr(config_loader, "_prep", lambda: {"lead_minutes": 9999})
    assert config_loader.prep_lead_minutes() == 240


def test_unknown_mode_reads_as_the_default(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "_prep", lambda: {"mode": "weekly"})
    assert config_loader.prep_mode() == "each"


def test_feature_is_off_until_asked(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "_prep", lambda: {})
    assert config_loader.prep_email_enabled() is False


def test_invalid_daily_time_reads_as_the_default(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "_prep", lambda: {"daily_time": "99:99"})
    assert config_loader.prep_daily_time() == "06:15"


def test_no_two_default_jobs_share_a_minute():
    """The whole shipped schedule, checked for collisions in one place.

    Morning Coffee, Week and the finance brief all used to fire at Monday
    07:00: three headless renders at once, each doing a full mail scan across
    every account and competing for the same quota. If one hit a cap the
    watcher held it, so the result was a partial Monday with no obvious cause.

    Asserted over the defaults rather than per pair, so a NEW job that lands on
    an occupied minute fails here without anyone remembering to add a case.
    """
    import config_loader as c

    slots = {}
    for name, spec in c.DIGEST_BRIEFING_DEFAULTS.items():
        for day in spec["days"]:
            slots.setdefault((day, spec["time"]), []).append(name)
    slots.setdefault((c.FINANCE_DEFAULTS["day"], c.FINANCE_DEFAULTS["time"]),
                     []).append("finance")
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        slots.setdefault((day, c.PREP_DEFAULTS["daily_time"]),
                         []).append("daily-prep")

    clashes = {slot: names for slot, names in slots.items() if len(names) > 1}
    assert not clashes, f"these default jobs share a minute: {clashes}"


def test_the_daily_prep_does_not_land_in_the_same_minute_as_a_briefing():
    """06:15, not 07:00, which is when Morning Coffee arrives.

    Two emails in the same minute read as one duplicate, and the prep is the
    one worth having read first. Asserted against the briefing default rather
    than a literal, so moving one and not the other fails here.
    """
    import config_loader
    prep = config_loader.PREP_DEFAULTS["daily_time"]
    coffee = config_loader.DIGEST_BRIEFING_DEFAULTS["morning-coffee"]["time"]
    assert prep != coffee, "the daily prep collides with Morning Coffee"
    assert prep < coffee, "the prep should arrive before the briefing it precedes"
