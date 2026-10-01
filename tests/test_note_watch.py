"""The Note's rules: what fires, what folds, what holds, and what gets written.

Every gate here is a suppression rule, and the tests that matter are the ones
sitting just PAST each boundary: a cap of two must fold the third and not the
second, a window ending at 18:00 must hold at 18:00 and fire at 17:59, a
meeting that started 59 minutes ago must hold and one that started 61 minutes
ago must not. Each gate carries a mutation note naming what to delete to
watch its test fail.
"""

from datetime import datetime, timedelta, timezone

import note_watch as nw

UTC = timezone.utc
NOW = datetime(2026, 9, 9, 17, 42, tzinfo=UTC)
TODAY = "2026-09-09"


def item(subject="Duke study results", email="foster@duke.com", account="Outlook",
         name="Foster Lin", age=11, **extra):
    return {"subject": subject, "counterparty_email": email, "account": account,
            "counterparty_name": name, "age_days": age, "label": "Waiting on them",
            "why": "asked 2026-08-29", **extra}


def mail(subject="Re: Duke study results", email="foster@duke.com", account="Outlook",
         mid="AAMkAGI2-long-provider-id-0001", received="2026-09-09T17:42:00+00:00",
         snippet="Results attached.", name="Foster Lin"):
    return {"subject": subject, "from": name, "from_email": email, "account": account,
            "snippet": snippet, "received": received, "message_id": mid}


# ── The watch list ───────────────────────────────────────────────────────────

def test_watch_key_needs_all_three_parts():
    """Account, address and subject together. A lone address is not a thread."""
    a = nw.watch_key("Outlook", "foster@duke.com", "Re: Re: Duke study results")
    b = nw.watch_key("outlook", "FOSTER@duke.com", "duke study results")
    assert a == b
    assert nw.watch_key("Gmail", "foster@duke.com", "Duke study results") != a


def test_items_without_an_address_are_not_watched():
    """A vault task has no counterparty; matching it loosely would fire on
    anyone. Mutation: drop the `if not email` guard and this fails."""
    watched = nw.watch_list([item(), {"subject": "Model the capex pull-forward",
                                      "counterparty_email": ""}])
    assert [w["subject"] for w in watched] == ["Duke study results"]


def test_union_takes_every_page_that_describes_today_and_dedups():
    """Afternoon Tea's page carries no addresses, so the morning page must
    stay in the watch list beside it. Mutation: return only the newest page
    and the morning item disappears."""
    morning = {"date": TODAY, "front_page": [item(subject="Duke study results")]}
    tea = {"date": TODAY, "front_page": [
        {"subject": "Duke study results", "counterparty_email": ""},
        item(subject="Duke study results"),                 # a twin, dropped
        item(subject="Harbor Solar LOI", email="ray@harbor.com")]}
    yesterday = {"date": "2026-09-08", "front_page": [item(subject="stale")]}
    subjects = [i["subject"] for i in nw.union_front_page([yesterday, morning, tea], TODAY)]
    assert subjects == ["Duke study results", "Duke study results", "Harbor Solar LOI"]
    assert sum(1 for i in nw.union_front_page([morning, tea], TODAY)
               if i.get("counterparty_email") == "foster@duke.com") == 1


def test_the_week_speaks_for_seven_days_and_no_more():
    week = {"week_start": "2026-09-07", "front_page": [item(subject="weekly")]}
    assert nw.covers_today(week, "2026-09-07") is True
    assert nw.covers_today(week, "2026-09-13") is True
    assert nw.covers_today(week, "2026-09-14") is False
    assert nw.covers_today({"front_page": [item()]}, TODAY) is False


# ── Trigger 1: a reply on a watched thread ───────────────────────────────────

def test_reply_on_watched_thread_fires():
    events = nw.reply_events([mail()], nw.watch_list([item()]))
    assert len(events) == 1
    assert events[0]["kind"] == "reply"
    assert events[0]["item"]["counterparty_name"] == "Foster Lin"


def test_same_counterparty_different_subject_does_not_fire():
    """Foster writing about lunch is not the Duke study. Mutation: build the
    key from account and address only and this fails."""
    events = nw.reply_events([mail(subject="Lunch Thursday?")], nw.watch_list([item()]))
    assert events == []


def test_same_subject_from_a_stranger_does_not_fire():
    events = nw.reply_events([mail(email="stranger@else.com")], nw.watch_list([item()]))
    assert events == []


def test_reply_event_id_uses_the_full_provider_id():
    """Graph ids share a long prefix; a truncated key collapses every message
    onto one row. Two ids differing only at the end must stay distinct."""
    a = nw.reply_events([mail(mid="AAMkAGI2NjhmYmEwLTRiNTYt-X-0001")], nw.watch_list([item()]))
    b = nw.reply_events([mail(mid="AAMkAGI2NjhmYmEwLTRiNTYt-X-0002")], nw.watch_list([item()]))
    assert a[0]["id"] != b[0]["id"]


def test_reply_seen_on_four_ticks_is_one_event_id():
    ids = {nw.reply_events([mail()], nw.watch_list([item()]))[0]["id"] for _ in range(4)}
    assert len(ids) == 1


# ── Trigger 2: a pass, in words, on a named deal ─────────────────────────────

def _detect(items, headings):
    """Stand-in for deal_status.detect_kills with its real contract: a list
    of alerts, `thread` None when the hit could not be tied to a deal."""
    out = []
    for i in items:
        text = f"{i['subject']} {i['snippet']}".lower()
        if "not moving forward" in text:
            thread = "Northwind add-on" if "northwind" in text else None
            out.append({"thread": thread, "subject": i["subject"], "from": i["from"],
                        "from_email": i["from_email"], "account": i["account"],
                        "signal": "not moving forward", "snippet": i["snippet"]})
    return out


def test_deal_pass_tied_to_a_thread_fires():
    events = nw.deal_events(
        [mail(subject="Northwind add-on LOI", snippet="We are not moving forward.")],
        ["Northwind add-on"], _detect)
    assert len(events) == 1 and events[0]["thread"] == "Northwind add-on"


def test_deal_pass_that_ties_to_no_deal_is_suppressed():
    """The precision floor. A pass on nothing named is Afternoon Tea's to
    show for review, never a Note. Mutation: drop the `thread` check."""
    events = nw.deal_events(
        [mail(subject="Newsletter", snippet="We are not moving forward with print.")],
        ["Northwind add-on"], _detect)
    assert events == []


def test_deal_alert_is_attributed_to_its_own_message():
    """Two messages, one hit: the event must carry the hitting message, not
    its neighbour. Guarded by scanning one message at a time."""
    quiet = mail(subject="Hello", snippet="All well.", mid="id-quiet")
    hit = mail(subject="Northwind add-on", snippet="not moving forward", mid="id-hit")
    events = nw.deal_events([quiet, hit], ["Northwind add-on"], _detect)
    assert [e["mail"]["message_id"] for e in events] == ["id-hit"]


# ── Trigger 3: today's calendar changed ──────────────────────────────────────

def cal(title, minutes_from_now):
    return {"title": title, "dt_utc": NOW + timedelta(minutes=minutes_from_now)}


def test_meeting_moved_more_than_tolerance_fires():
    before = nw.calendar_snapshot([cal("Harbor Solar call", 60)])
    after = nw.calendar_snapshot([cal("Harbor Solar call", 66)])
    events = nw.calendar_events(before, after, NOW)
    assert len(events) == 1 and events[0]["change"] == "moved"


def test_meeting_moved_within_tolerance_is_not_a_move():
    """Five minutes is merge_events' own tolerance; sitting ON it is quiet."""
    before = nw.calendar_snapshot([cal("Harbor Solar call", 60)])
    after = nw.calendar_snapshot([cal("Harbor Solar call", 65)])
    assert nw.calendar_events(before, after, NOW) == []


def test_future_meeting_that_vanished_is_gone():
    before = nw.calendar_snapshot([cal("Harbor Solar call", 60)])
    events = nw.calendar_events(before, {}, NOW)
    assert len(events) == 1 and events[0]["change"] == "gone"


def test_meeting_that_started_and_fell_out_of_the_window_is_not_gone():
    """The calendar fetch is forward-looking, so every meeting falls out once
    it starts. That is the meeting happening, not the meeting cancelled.
    Mutation: drop the `old <= now` guard and this fails."""
    before = nw.calendar_snapshot([cal("Harbor Solar call", -1)])
    assert nw.calendar_events(before, {}, NOW) == []


def test_new_meeting_is_not_an_event():
    after = nw.calendar_snapshot([cal("Surprise sync", 30)])
    assert nw.calendar_events({}, after, NOW) == []


# ── The gates ────────────────────────────────────────────────────────────────

def test_in_meeting_boundaries_sit_past_the_edge():
    assert nw.in_meeting([cal("x", -59)], NOW) is True
    assert nw.in_meeting([cal("x", -61)], NOW) is False
    assert nw.in_meeting([cal("x", 1)], NOW) is False
    assert nw.in_meeting([cal("x", 0)], NOW) is True


def test_window_fires_at_start_and_holds_at_end():
    assert nw.in_window(datetime(2026, 9, 9, 7, 30), "07:30", "18:00") is True
    assert nw.in_window(datetime(2026, 9, 9, 7, 29), "07:30", "18:00") is False
    assert nw.in_window(datetime(2026, 9, 9, 17, 59), "07:30", "18:00") is True
    assert nw.in_window(datetime(2026, 9, 9, 18, 0), "07:30", "18:00") is False


def test_unparseable_window_reads_as_closed():
    """A typo in config costs the feature, never a 2 AM Note."""
    assert nw.in_window(datetime(2026, 9, 9, 12, 0), "seven", "18:00") is False


def ev(i, received=None):
    return {"id": f"reply:{i}", "kind": "reply",
            "received": received or f"2026-09-09T1{i}:00:00+00:00"}


def test_cap_folds_the_third_and_not_the_second():
    fire, fold, hold = nw.select([ev(1), ev(2), ev(3)], {}, TODAY, cap=2, meeting_now=False)
    assert [e["id"] for e in fire] == ["reply:1", "reply:2"]
    assert [e["id"] for e in fold] == ["reply:3"]
    assert hold == []


def test_cap_counts_what_already_went_out_today():
    """One sent this morning plus a cap of two leaves room for exactly one.
    Mutation: make sent_today return 0 and this fails."""
    ledger = nw.record({}, ev(0), "sent", TODAY, "line", now=NOW)
    fire, fold, _ = nw.select([ev(1), ev(2)], ledger, TODAY, cap=2, meeting_now=False)
    assert len(fire) == 1 and len(fold) == 1


def test_folded_rows_do_not_consume_the_cap():
    ledger = nw.record({}, ev(0), "folded", TODAY, "line", now=NOW)
    fire, _, _ = nw.select([ev(1), ev(2)], ledger, TODAY, cap=2, meeting_now=False)
    assert len(fire) == 2


def test_yesterdays_sent_rows_do_not_count_today():
    ledger = nw.record({}, ev(0), "sent", "2026-09-08", "line", now=NOW)
    fire, _, _ = nw.select([ev(1), ev(2)], ledger, TODAY, cap=2, meeting_now=False)
    assert len(fire) == 2


def test_meeting_holds_everything_and_writes_nothing():
    fire, fold, hold = nw.select([ev(1), ev(2), ev(3)], {}, TODAY, cap=2, meeting_now=True)
    assert fire == [] and fold == [] and len(hold) == 3


def test_ledgered_events_never_come_back():
    """Sent or folded, an id in the ledger is done. Four ticks, one Note."""
    ledger = {}
    total = 0
    for _ in range(4):
        fire, fold, _ = nw.select([ev(1)], ledger, TODAY, cap=2, meeting_now=False)
        for e in fire:
            ledger = nw.record(ledger, e, "sent", TODAY, "line", now=NOW)
        total += len(fire)
    assert total == 1


def test_oldest_arrival_spends_the_cap_first():
    late = ev(1, received="2026-09-09T16:00:00+00:00")
    early = ev(2, received="2026-09-09T09:00:00+00:00")
    fire, fold, _ = nw.select([late, early], {}, TODAY, cap=1, meeting_now=False)
    assert fire[0]["id"] == "reply:2" and fold[0]["id"] == "reply:1"


# ── The ledger ───────────────────────────────────────────────────────────────

def test_prune_keeps_two_days_and_drops_the_unreadable():
    rows = {
        "keep": {"status": "sent", "at": (NOW - timedelta(days=1)).isoformat()},
        "old": {"status": "sent", "at": (NOW - timedelta(days=3)).isoformat()},
        "junk": {"status": "sent", "at": "not a time"},
        "notdict": "x",
    }
    assert set(nw.prune(rows, now=NOW)) == {"keep"}


def test_notes_today_lists_only_today_oldest_first():
    ledger = nw.record({}, ev(2), "sent", TODAY, "second", now=NOW)
    ledger = nw.record(ledger, ev(1), "folded", TODAY, "first", now=NOW - timedelta(hours=1))
    ledger = nw.record(ledger, ev(0), "sent", "2026-09-08", "yesterday", now=NOW - timedelta(days=1))
    assert [r["line"] for r in nw.notes_today(ledger, TODAY)] == ["first", "second"]


# ── The line and the block ───────────────────────────────────────────────────

def test_reply_line_claims_a_draft_only_when_told_one_exists():
    event = nw.reply_events([mail()], nw.watch_list([item()]))[0]
    plain = nw.note_line(event, "10:42 AM PT / 1:42 PM ET")
    drafted = nw.note_line(event, "10:42 AM PT / 1:42 PM ET", drafted=True)
    assert plain == ("10:42 AM PT / 1:42 PM ET: Foster Lin replied on Duke study "
                     "results, 11 days after you asked.")
    assert drafted.endswith("Reply drafted, waiting for your nod.")


def test_lines_carry_no_dashes():
    dashed = "Duke study " + chr(0x2014) + " results"
    event = nw.reply_events([mail(subject=dashed)], nw.watch_list([item(subject=dashed)]))[0]
    line = nw.note_line(event, "10:42 AM PT / 1:42 PM ET", drafted=True)
    assert chr(0x2014) not in line and chr(0x2013) not in line


MD = """# Morning Coffee

Today is one decision. Nothing closed overnight.

Monday 7 September. 8 of your 137 open items come first.

## SUGGESTED FOCUS TASK

Settle the indemnity position once.

## DO TODAY

- [ ] Countersign the Harbor Solar LOI
"""


def test_patch_inserts_before_the_first_section_and_is_idempotent():
    once = nw.patch_notes(MD, ["10:42 AM PT / 1:42 PM ET: Foster Lin replied."])
    assert once.index("## Notes") < once.index("## SUGGESTED FOCUS TASK")
    assert once.count(nw.NOTES_START) == 1
    assert "- [ ] Countersign the Harbor Solar LOI" in once
    twice = nw.patch_notes(once, ["10:42 AM PT / 1:42 PM ET: Foster Lin replied."])
    assert twice == once


def test_patch_rewrites_the_block_whole_and_touches_nothing_else():
    once = nw.patch_notes(MD, ["first"])
    edited = once.replace("- [ ] Countersign", "- [x] Countersign")
    again = nw.patch_notes(edited, ["first", "second"])
    assert "- first\n- second" in again
    assert "- [x] Countersign" in again
    assert again.count("## Notes") == 1


def test_patch_with_no_lines_removes_the_block():
    once = nw.patch_notes(MD, ["first"])
    gone = nw.patch_notes(once, [])
    assert nw.NOTES_START not in gone and "## Notes" not in gone
    assert gone == MD


def test_patch_appends_when_there_is_no_section_heading():
    out = nw.patch_notes("# Title\n\nJust prose.\n", ["a line"])
    assert out.endswith(f"- a line\n{nw.NOTES_END}\n")
