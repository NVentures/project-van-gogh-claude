"""Granola meeting cross-reference (week_review._meeting_resolved) and the
confirm-gated met_candidates hand-off into morning_coffee.

The cross-ref tags a thread "likely handled" only when a meeting title shares a
distinctive token AND the meeting is dated on/after the last email; met state is
a heuristic that surfaces a confirm candidate, never an auto-[x].
"""
import os
import tempfile
from datetime import date

import week_review as wr
import morning_coffee as mc

TODAY = date(2026, 6, 8)
# The week-file deal format uses an em-dash separator; held as a codepoint so
# this test file adds no literal em-dash to the no-dash sweep.
EMDASH = chr(0x2014)


def test_same_day_meeting_resolves():
    # Meeting SAME day as the email (reply_age 7 on a 2026-06-08 run -> last email
    # 2026-06-01); a distinctive token ('brock'/'felt') matches -> resolved.
    entry = {"counterparty_name": "Owen Trask", "counterparty_email": "owen@traskcapital.example",
             "subject": "Felt Capital term sheet", "reply_age_days": 7}
    res = wr._meeting_resolved(
        entry, [{"title": "Owen Trask / Felt Capital sync", "date": "2026-06-01"}], TODAY)
    assert res is not None and res["date"] == "2026-06-01"


def test_pre_email_meeting_does_not_resolve():
    # Only meeting PREDATES the email -> NOT resolved, even with a distinctive
    # token match: a meeting before the email cannot have addressed it.
    entry = {"counterparty_name": "Kevin Alder", "counterparty_email": "kalder@northstar.example",
             "subject": "Northstar advisor update", "reply_age_days": 2}
    res = wr._meeting_resolved(
        entry, [{"title": "Kevin Alder Northstar advisor update", "date": "2026-06-03"}], TODAY)
    assert res is None


def test_generic_token_only_does_not_resolve():
    # The only shared tokens are generic ('project'/'review') -> NOT a match.
    entry = {"counterparty_name": "Some Person", "counterparty_email": "p@example.com",
             "subject": "Project review call", "reply_age_days": 0}
    res = wr._meeting_resolved(
        entry, [{"title": "Project review meeting", "date": "2026-06-08"}], TODAY)
    assert res is None


def test_reply_age_zero_boundary_resolves():
    # last email == today; a same-day distinctive meeting resolves. Proves the
    # explicit None-check, not `reply_age or age`.
    entry = {"counterparty_name": "Fairview Partners", "counterparty_email": "ops@fairview.com",
             "subject": "Fairview LOI", "reply_age_days": 0}
    res = wr._meeting_resolved(
        entry, [{"title": "Fairview LOI review", "date": "2026-06-08"}], TODAY)
    assert res is not None


def test_reply_age_none_falls_back_to_age_days():
    entry = {"counterparty_name": "Lakeshore Holdings", "counterparty_email": "deals@lakeshore.com",
             "subject": "Lakeshore PPA", "reply_age_days": None, "age_days": 10}
    res = wr._meeting_resolved(
        entry, [{"title": "Lakeshore PPA kickoff", "date": "2026-06-02"}], TODAY)
    assert res is not None


def test_most_recent_matching_meeting_wins():
    entry = {"counterparty_name": "Fairview Partners", "counterparty_email": "ops@fairview.com",
             "subject": "Fairview LOI", "reply_age_days": 7}
    meetings = [{"title": "Fairview LOI review", "date": "2026-06-04"},
                {"title": "Fairview LOI signing", "date": "2026-06-07"}]
    res = wr._meeting_resolved(entry, meetings, TODAY)
    assert res is not None and res["date"] == "2026-06-07"


def test_empty_meetings_returns_none():
    entry = {"counterparty_name": "Owen Trask", "counterparty_email": "owen@traskcapital.example",
             "subject": "Felt Capital term sheet", "reply_age_days": 7}
    assert wr._meeting_resolved(entry, [], TODAY) is None


def test_met_token_survives_week_file_to_morning_coffee():
    # <!-- met:DATE --> survives the week-file -> morning_coffee hop as a confirm
    # candidate, and is NOT auto-marked done (the file is byte-identical after).
    heading = f"## Deals {EMDASH} Waiting on {mc._USER_FIRST}"
    met_line = (
        f'- [ ] [Outlook] "Felt Capital term sheet" {EMDASH} Owen Trask '
        f"(owen@traskcapital.example) {EMDASH} replied 7d ago <!-- met:2026-06-08 -->"
    )
    plain_line = (
        f'- [ ] [Gmail] "Cold lead" {EMDASH} No Meeting '
        f"(nm@example.com) {EMDASH} replied 3d ago"
    )
    week_md = f"{heading}\n{met_line}\n{plain_line}\n"

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(week_md)
        tmp_path = f.name
    try:
        before = open(tmp_path, encoding="utf-8").read()
        _lines, deals, _tasks, _priorities = mc.parse_week_file(tmp_path)

        met_deals = [d for d in deals if d["met_date"]]
        assert len(met_deals) == 1 and met_deals[0]["met_date"] == "2026-06-08"
        assert met_deals[0]["checked"] is False

        # Email IS still open -> main() takes the confirm branch.
        cand = mc.met_candidate(met_deals[0], {"reply_age_days": 7})
        assert cand is not None and cand["met_date"] == "2026-06-08"

        non_met = [d for d in deals if not d["met_date"]]
        assert non_met and mc.met_candidate(non_met[0], {"reply_age_days": 3}) is None

        # parse + candidate-build never flips the [ ] box (only the sent-reply
        # path calls mark_done), so the file is byte-identical.
        after = open(tmp_path, encoding="utf-8").read()
        assert before == after
    finally:
        os.unlink(tmp_path)
