"""The judgment layer: reads meaning, and cannot hurt anyone when it breaks.

Two things are being proven here. First, that it actually understands a
priority written as an intention, which is the failure that motivated it: a
person writes "grow the pipeline" and the email says "redline attached, need
signature by Friday". Those share no words and mean the same thing. Second,
and more important, that every way it can fail leaves the reader seeing MORE
rather than less.
"""
import json
import os
import shutil
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config_loader as cl                                      # noqa: E402
import priority_judge as pj                                     # noqa: E402

BUCKET = {
    "display_name": "Power Market",
    "priorities": [{"name": "grow the pipeline"}, {"name": "keep the team happy"}],
}
ITEMS = [
    {"subject": "Redline attached, need signature by Friday",
     "counterparty_name": "Dana", "body_preview": "Final markup on the QX deal."},
    {"subject": "Your monthly newsletter is here",
     "counterparty_name": "Marketing", "body_preview": "Ten trends to watch."},
]


def _reply(rows):
    return CompletedProcess([], 0, stdout=json.dumps(rows), stderr="")


# ── The model cannot invent anything ─────────────────────────────────────────

def test_invented_priority_is_discarded():
    run = lambda *a, **k: _reply([                              # noqa: E731
        {"id": 0, "relevant": True, "priority": "a priority nobody wrote",
         "reason": "r"}])
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    # Still relevant (every rail leans toward showing), but the invented
    # heading is gone.
    assert out["verdicts"][0]["relevant"] is True
    assert out["verdicts"][0]["priority"] == ""


def test_verbatim_priority_survives():
    run = lambda *a, **k: _reply([                              # noqa: E731
        {"id": 0, "relevant": True, "priority": "grow the pipeline", "reason": "r"}])
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"][0]["priority"] == \
        "grow the pipeline"


def test_out_of_range_id_is_discarded():
    run = lambda *a, **k: _reply([{"id": 99, "relevant": True}])  # noqa: E731
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"] == {}


def test_unjudged_item_gets_no_verdict():
    # An item the model skipped must have no verdict at all, so the caller
    # treats it as unjudged rather than as judged-irrelevant.
    run = lambda *a, **k: _reply([{"id": 0, "relevant": True}])  # noqa: E731
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert 1 not in out["verdicts"]


# ── Every failure mode fails safe ────────────────────────────────────────────

def test_non_zero_exit_is_degraded():
    run = lambda *a, **k: CompletedProcess([], 1, stdout="", stderr="boom")  # noqa: E731
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_timeout_is_degraded():
    def run(*a, **k):
        raise TimeoutError("timed out")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_unparseable_output_is_degraded():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 0, stdout="I would be happy to help with that!", stderr="")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_quota_wall_is_degraded_and_says_so():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 1, stdout="You're out of extra usage, resets 3:40pm", stderr="")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True
    assert "usage limit" in out["degraded_reason"]


def test_degraded_reason_is_written_for_a_person():
    run = lambda *a, **k: CompletedProcess([], 1, stdout="", stderr="boom")  # noqa: E731
    reason = pj.judge_bucket(BUCKET, ITEMS, run=run)["degraded_reason"]
    assert reason and reason[0].islower() and "Exception" not in reason


# ── Prose in front of the JSON ───────────────────────────────────────────────

def test_prose_before_the_json_is_tolerated():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 0, stdout='Sure, here you go:\n[{"id": 0, "relevant": true}]\n', stderr="")
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"][0]["relevant"] is True


# ── No work, no call ─────────────────────────────────────────────────────────

def test_bucket_with_no_priorities_is_still_read_for_cost():
    """Cost needs no stated priority. With none set, relevance is not asked
    and comes back None, which the caller must not read as "not relevant"."""
    prompts = []
    def run(prompt, **k):
        prompts.append(prompt)
        return _reply([{"id": 0, "slip_cost": "high", "relevant": False}])
    out = pj.judge_bucket({"display_name": "X", "priorities": []}, ITEMS, run=run)
    assert len(prompts) == 1
    assert "matter most" not in prompts[0] and '"relevant"' not in prompts[0]
    assert out["verdicts"][0]["relevant"] is None
    assert out["verdicts"][0]["slip_cost"] == "high"


def test_no_items_never_calls_the_model():
    calls = []
    def run(*a, **k):
        calls.append(1)
        return _reply([])
    pj.judge_bucket(BUCKET, [], run=run)
    assert calls == []


# ── The deadline detector ────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Countersignature needed by Friday",
    "Contract expires 2026-09-30",
    "Please return by EOD",
    "Deadline for the filing",
    "Docs due 9/30",
])
def test_deadline_language_is_detected(subject):
    assert pj.has_deadline({"subject": subject}) is True


@pytest.mark.parametrize("subject", [
    "Quick question about the office",
    "Newsletter: ten trends to watch",
])
def test_ordinary_subjects_are_not_deadlines(subject):
    assert pj.has_deadline({"subject": subject}) is False


def test_days_late_counts_as_a_deadline():
    assert pj.has_deadline({"subject": "anything", "days_late": 3}) is True


# ── Live: the case the whole layer exists for ────────────────────────────────

# CI has no `claude` binary, so this skips there with a reason rather than
# failing for the absence of a tool the product legitimately requires. It runs
# on any machine that has Claude Code installed, which is every machine this
# actually ships to.
@pytest.mark.skipif(
    os.environ.get("VAN_GOGH_SKIP_LIVE") == "1" or shutil.which("claude") is None,
    reason="needs the claude CLI on PATH (absent in CI)")
def test_live_vague_priority_is_understood():
    """A priority written as an intention, matched against an email that
    shares none of its words. A string matcher scores zero here; that is the
    entire reason this layer was built, so it is checked against the real
    model rather than a stub."""
    out = pj.judge_bucket(BUCKET, ITEMS, model=pj.DEFAULT_JUDGE_MODEL, timeout=180)
    assert out["degraded"] is False, out["degraded_reason"]
    assert out["verdicts"][0]["relevant"] is True, "signature deadline read as off-priority"
    assert out["verdicts"][1]["relevant"] is False, "newsletter read as on-priority"
    assert out["verdicts"][0]["reason"], "a demotion or a keep must be explainable"


# ── Standing user guidance (the vault preamble) ──────────────────────────────

def test_preamble_reaches_the_prompt(monkeypatch):
    monkeypatch.setattr(cl, "judgment_preamble",
                        lambda: "Weekend emails from vendors never count.")
    prompt = pj.build_prompt(BUCKET, ITEMS)
    assert "Weekend emails from vendors never count." in prompt
    assert "standing guidance" in prompt


def test_no_preamble_leaves_the_prompt_unchanged(monkeypatch):
    monkeypatch.setattr(cl, "judgment_preamble", lambda: "")
    assert "standing guidance" not in pj.build_prompt(BUCKET, ITEMS)


def test_preamble_reads_the_vault_file_and_is_capped():
    target = cl.judgment_preamble_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text("guidance here\n", encoding="utf-8")
        assert cl.judgment_preamble() == "guidance here"
        target.write_text("x" * 10000, encoding="utf-8")
        assert len(cl.judgment_preamble()) == cl.JUDGMENT_PREAMBLE_MAX_CHARS
    finally:
        target.unlink(missing_ok=True)


def test_missing_preamble_file_is_empty_not_fatal():
    assert not cl.judgment_preamble_path().exists()
    assert cl.judgment_preamble() == ""


# ── Slip cost: what it costs to leave an item alone ──────────────────────────
#
# The reply shapes below are the ones a live run over a real inbox returned
# (most rows are just an id and a cost; the optional fields appear only when
# they apply). The words are invented.

CALENDAR = [
    {"title": "Laptop handoff", "day": "Tue Oct 13", "time": "9:00 AM PDT / 12:00 PM EDT"},
    {"title": "Board prep", "day": "Thu Oct 15", "time": "All day"},
]
NO_PRIORITIES = {"display_name": "Power Market", "priorities": []}
MAIL = [
    {"subject": "Admin rights on the new laptop", "counterparty_name": "Sam",
     "body_preview": "Can you approve admin rights before the handoff?"},
    {"subject": "Offer valid through October 9", "counterparty_name": "Vendor",
     "body_preview": "This pricing is valid through October 9 only."},
    {"subject": "Your login code", "counterparty_name": "App",
     "body_preview": "Use 104938 to sign in."},
]


def _costs(rows, items=MAIL, calendar=CALENDAR):
    run = lambda *a, **k: _reply(rows)                          # noqa: E731
    return pj.judge_bucket(NO_PRIORITIES, items, run=run, calendar=calendar,
                           today="2026-10-12")["verdicts"]


def test_the_bare_row_a_live_run_returns_is_read():
    out = _costs([{"id": 2, "slip_cost": "low"}])
    assert out[2] == {"relevant": None, "priority": "", "reason": "",
                      "slip_cost": "low", "slip_reason": "", "blocks_event": "",
                      "blocks_when": "", "likely_dead": False, "decaying": False,
                      "due": "", "due_quote": ""}


@pytest.mark.parametrize("given", ["urgent", "", None, 3, "HIGHEST"])
def test_a_cost_outside_the_three_is_dropped(given):
    assert _costs([{"id": 0, "slip_cost": given}])[0]["slip_cost"] == ""


def test_cost_is_read_whatever_its_case():
    assert _costs([{"id": 0, "slip_cost": " High "}])[0]["slip_cost"] == "high"


def test_a_row_with_no_cost_is_unjudged_for_cost():
    assert _costs([{"id": 0, "decaying": True}])[0]["slip_cost"] == ""


def test_an_event_copied_exactly_survives_with_its_day():
    out = _costs([{"id": 0, "slip_cost": "high", "blocks_event": "Laptop handoff"}])
    assert out[0]["blocks_event"] == "Laptop handoff"
    assert out[0]["blocks_when"].startswith("Tue Oct 13")


@pytest.mark.parametrize("invented", ["the laptop handoff", "Laptop Handoff",
                                      "Quarterly review"])
def test_an_event_that_is_not_on_the_calendar_is_dropped(invented):
    out = _costs([{"id": 0, "slip_cost": "high", "blocks_event": invented}])
    assert out[0]["blocks_event"] == "" and out[0]["blocks_when"] == ""


def test_no_calendar_means_no_event_is_asked_for_or_kept():
    prompts = []
    def run(prompt, **k):
        prompts.append(prompt)
        return _reply([{"id": 0, "slip_cost": "high", "blocks_event": "Laptop handoff"}])
    out = pj.judge_bucket(NO_PRIORITIES, MAIL, run=run)
    assert "blocks_event" not in prompts[0] and "calendar" not in prompts[0]
    assert out["verdicts"][0]["blocks_event"] == ""


def test_the_calendar_and_the_date_reach_the_prompt():
    prompt = pj.build_prompt(NO_PRIORITIES, MAIL, calendar=CALENDAR,
                             today="2026-10-12")
    assert "- Laptop handoff (Tue Oct 13, 9:00 AM PDT / 12:00 PM EDT)" in prompt
    assert "Today is Monday 2026-10-12." in prompt


def test_the_prompt_says_who_wrote_last_and_how_long_ago():
    prompt = pj.build_prompt(NO_PRIORITIES, [
        {"subject": "A", "section": "waiting_on_user", "reply_age_days": 4},
        {"subject": "B", "section": "cold_monitor", "age_days": 31}])
    assert '"days_old": 4, "last_word": "theirs"' in prompt
    assert '"days_old": 31, "last_word": "yours"' in prompt


def test_dead_stands_when_the_quote_is_in_the_message():
    out = _costs([{"id": 1, "slip_cost": "low", "likely_dead": True,
                   "dead_quote": "valid  through october 9"}])
    assert out[1]["likely_dead"] is True


@pytest.mark.parametrize("row", [
    {"likely_dead": True},                                       # no quote
    {"likely_dead": True, "dead_quote": "expired last week"},    # not in it
    {"likely_dead": True, "dead_quote": "valid"},                # too short
    {"likely_dead": "true", "dead_quote": "valid through October 9"},
    {"dead_quote": "valid through October 9"},                   # never said
])
def test_dead_without_the_messages_own_words_is_dropped(row):
    out = _costs([{"id": 1, "slip_cost": "low", **row}])
    assert out[1]["likely_dead"] is False


def test_a_quote_from_another_message_proves_nothing():
    # The words are real, but they are in message 1, not message 0.
    out = _costs([{"id": 0, "slip_cost": "low", "likely_dead": True,
                   "dead_quote": "valid through October 9"}])
    assert out[0]["likely_dead"] is False


def test_the_reason_is_kept_for_high_only_and_carries_no_dash():
    dash = chr(0x2014)
    out = _costs([
        {"id": 0, "slip_cost": "high",
         "slip_reason": f"Tuesday handoff breaks{dash}no admin rights."},
        {"id": 1, "slip_cost": "medium", "slip_reason": "someone is waiting"}])
    assert out[0]["slip_reason"] == "Tuesday handoff breaks,no admin rights"
    assert out[1]["slip_reason"] == ""


def test_decaying_must_be_a_real_true():
    out = _costs([{"id": 0, "slip_cost": "medium", "decaying": True},
                  {"id": 1, "slip_cost": "medium", "decaying": "yes"}])
    assert out[0]["decaying"] is True and out[1]["decaying"] is False


# ── A deadline the message itself states ─────────────────────────────────────
#
# A date puts an item at the top of the page, so every way a wrong one could
# get there is closed in code. TODAY is a Monday.

TODAY = "2026-10-12"
DATED = [
    {"subject": "Renewal", "counterparty_name": "Vendor", "reply_age_days": 6,
     "body_preview": "Your plan renews on 10/25/26 unless you cancel."},
    {"subject": "Signature", "counterparty_name": "Dana", "reply_age_days": 9,
     "body_preview": "Need this signed by Friday please."},
    {"subject": "Sale", "counterparty_name": "Shop", "reply_age_days": 5,
     "body_preview": "Pricing valid through October 9 only."},
    {"subject": "No age on this one", "counterparty_name": "X",
     "body_preview": "Please file by 10/20."},
]


def _due(row, items=DATED, today=TODAY):
    run = lambda *a, **k: _reply([row])                          # noqa: E731
    v = pj.judge_bucket(NO_PRIORITIES, items, run=run, today=today)["verdicts"]
    return v[row["id"]]["due"], v[row["id"]]["due_quote"]


def test_a_stated_date_is_kept_with_the_words_that_state_it():
    assert _due({"id": 0, "slip_cost": "high", "due": "2026-10-25",
                 "due_quote": "renews on  10/25/26"}) == \
        ("2026-10-25", "renews on 10/25/26")


def test_a_date_that_passed_after_the_message_was_sent_is_kept():
    """Sent nine days ago, "by Friday" was the 9th. Late, and really late."""
    assert _due({"id": 1, "slip_cost": "medium", "due": "2026-10-09",
                 "due_quote": "signed by Friday"})[0] == "2026-10-09"


@pytest.mark.parametrize("row", [
    {"due": "2026-10-25"},                                       # no quote
    {"due": "2026-10-25", "due_quote": "renews next month"},     # not in it
    {"due": "2026-10-25", "due_quote": "on"},                    # too short
    {"due": "October 25", "due_quote": "renews on 10/25/26"},    # not a date
    {"due": "2026-13-45", "due_quote": "renews on 10/25/26"},    # not a date
    {"due_quote": "renews on 10/25/26"},                         # no date
    {"due": "2026-09-25", "due_quote": "renews on 10/25/26"},    # before it was sent
    {"due": "2028-10-25", "due_quote": "renews on 10/25/26"},    # over a year out
])
def test_a_date_that_cannot_be_stood_behind_is_dropped(row):
    assert _due({"id": 0, "slip_cost": "high", **row}) == ("", "")


def test_the_day_before_it_was_sent_is_the_earliest_a_date_may_be():
    row = {"id": 0, "slip_cost": "high", "due_quote": "renews on 10/25/26"}
    assert _due({**row, "due": "2026-10-05"})[0] == "2026-10-05"    # 6 days old, 1 slack
    assert _due({**row, "due": "2026-10-04"})[0] == ""


def test_a_year_out_is_the_latest_a_date_may_be():
    row = {"id": 0, "slip_cost": "high", "due_quote": "renews on 10/25/26"}
    assert _due({**row, "due": "2027-10-13"})[0] == "2027-10-13"
    assert _due({**row, "due": "2027-10-14"})[0] == ""


@pytest.mark.parametrize("cost", ["low", "", "urgent"])
def test_an_item_that_costs_nothing_cannot_carry_a_date(cost):
    """The expired promotion: a real date, really in the message, on mail
    that asks nothing of anyone. It must not reach the top of the page."""
    assert _due({"id": 2, "slip_cost": cost, "due": "2026-10-09",
                 "due_quote": "valid through October 9"}) == ("", "")


def test_a_dead_item_cannot_carry_a_date():
    assert _due({"id": 2, "slip_cost": "high", "due": "2026-10-09",
                 "due_quote": "valid through October 9", "likely_dead": True,
                 "dead_quote": "valid through October 9"}) == ("", "")


def test_a_quote_from_another_message_dates_nothing():
    assert _due({"id": 1, "slip_cost": "high", "due": "2026-10-25",
                 "due_quote": "renews on 10/25/26"}) == ("", "")


def test_an_item_with_no_age_is_bounded_by_the_horizon_only():
    assert _due({"id": 3, "slip_cost": "high", "due": "2026-10-20",
                 "due_quote": "file by 10/20"})[0] == "2026-10-20"


def test_without_today_no_date_is_asked_for_or_kept():
    prompts = []
    def run(prompt, **k):
        prompts.append(prompt)
        return _reply([{"id": 0, "slip_cost": "high", "due": "2026-10-25",
                        "due_quote": "renews on 10/25/26"}])
    out = pj.judge_bucket(NO_PRIORITIES, DATED, run=run)
    assert "due_quote" not in prompts[0] and "Today is" not in prompts[0]
    assert out["verdicts"][0]["due"] == ""


def test_the_prompt_names_the_weekday_and_asks_for_the_date():
    prompt = pj.build_prompt(NO_PRIORITIES, DATED, today=TODAY)
    assert "Today is Monday 2026-10-12." in prompt
    assert '"due_quote"' in prompt and "never from today" in prompt


# ── Many items: read a chunk at a time ───────────────────────────────────────

def _many(n):
    return [{"subject": f"Message {i}", "body_preview": ""} for i in range(n)]


def _chunk_ids(prompt):
    payload = pj._extract_json_array(prompt[prompt.index('[{"id"'):])
    return [(row["id"], row["subject"]) for row in payload]


def test_a_long_list_goes_out_in_chunks_and_every_verdict_lands_on_its_item():
    items = _many(pj.JUDGE_CHUNK * 2 + 15)
    sizes = []
    def run(prompt, **k):
        rows = _chunk_ids(prompt)
        sizes.append(len(rows))
        # The cost names the item it was given for, so a verdict filed
        # against the wrong position is caught below.
        return _reply([{"id": i, "slip_cost": "high", "slip_reason": subject}
                       for i, subject in rows])
    out = pj.judge_bucket(NO_PRIORITIES, items, run=run)
    assert sorted(sizes) == [15, pj.JUDGE_CHUNK, pj.JUDGE_CHUNK]
    assert out["degraded"] is False
    assert sorted(out["verdicts"]) == list(range(len(items)))
    for i, item in enumerate(items):
        assert out["verdicts"][i]["slip_reason"] == item["subject"]


def test_one_failed_chunk_costs_only_its_own_items_and_says_so():
    items = _many(pj.JUDGE_CHUNK * 3)
    def run(prompt, **k):
        rows = _chunk_ids(prompt)
        if rows[0][1] == f"Message {pj.JUDGE_CHUNK}":
            return CompletedProcess([], 1, stdout="", stderr="boom")
        return _reply([{"id": i, "slip_cost": "low"} for i, _ in rows])
    out = pj.judge_bucket(NO_PRIORITIES, items, run=run)
    assert out["degraded"] is True and out["degraded_reason"]
    lost = set(range(pj.JUDGE_CHUNK, pj.JUDGE_CHUNK * 2))
    assert set(out["verdicts"]) == set(range(len(items))) - lost


def test_a_usage_cap_stops_the_remaining_chunks(monkeypatch):
    import claude_cli
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    calls = []
    def run(prompt, **k):
        calls.append(1)
        raise claude_cli.UsageCap("out of extra usage")
    out = pj.judge_bucket(NO_PRIORITIES, _many(pj.JUDGE_CHUNK * 4), run=run)
    assert calls == [1]
    assert out["degraded_reason"] == "the usage limit was reached"
    assert out["verdicts"] == {}


# ── Live: cost, on the real model ────────────────────────────────────────────

@pytest.mark.skipif(
    os.environ.get("VAN_GOGH_SKIP_LIVE") == "1" or shutil.which("claude") is None,
    reason="needs the claude CLI on PATH (absent in CI)")
def test_live_a_blocked_handoff_costs_more_than_a_login_code():
    out = pj.judge_bucket(NO_PRIORITIES, MAIL, model=pj.DEFAULT_JUDGE_MODEL,
                          timeout=180, calendar=CALENDAR, today="2026-10-12")
    assert out["degraded"] is False, out["degraded_reason"]
    v = out["verdicts"]
    assert v[0]["slip_cost"] == "high", "a blocked handoff was not read as costly"
    assert v[0]["blocks_event"] == "Laptop handoff"
    assert v[2]["slip_cost"] == "low", "a login code was read as costing something"
    assert v[1]["likely_dead"] is True, "an offer three days past was read as live"


@pytest.mark.skipif(
    os.environ.get("VAN_GOGH_SKIP_LIVE") == "1" or shutil.which("claude") is None,
    reason="needs the claude CLI on PATH (absent in CI)")
def test_live_a_day_name_is_worked_out_from_when_the_message_was_sent():
    out = pj.judge_bucket(NO_PRIORITIES, DATED, model=pj.DEFAULT_JUDGE_MODEL,
                          timeout=180, today=TODAY)
    assert out["degraded"] is False, out["degraded_reason"]
    v = out["verdicts"]
    # Only the day name is asserted. Whether an auto-renewal costs enough to
    # carry its date is the model's call and it varies (measured 11 of 12),
    # so pinning it here would make a publish gate out of a coin toss.
    # Sent Saturday the 3rd; its Friday is the 9th, not this coming one.
    assert v[1]["due"] == "2026-10-09", f"by Friday resolved to {v[1]['due']!r}"
    assert v[2]["due"] == "", "an expired sale was given a deadline"
