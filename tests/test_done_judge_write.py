"""Closing, reopening and expiring, against a planted vault on disk.

Contract V4: a close flips the hotcache line, the meeting page line and the
week file line by id and records exactly one closed event; an undo restores
those three files byte for byte and pins the item in the state file, never in
the vault; a rerun with a model answering done leaves it open; expiry closes
an old item with no due date and no deal, and leaves a dated one and a deal
one alone.
"""
import json

import pytest

import claude_update
import completion_scan
import config_loader
import data_sources
import done_judge as dj
import done_judge_fixture as fx
import item_ids
import run_ledger

DONE = fx.answering(fx.DONE_PLAN)


@pytest.fixture
def v(tmp_path, monkeypatch):
    return fx.make_vault(tmp_path, monkeypatch)


def live(v, runner=DONE, **kw):
    return dj.run(live=True, ctx=fx.ctx(), runner=runner, **kw)


def box(path, cb):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return [item_ids.parse(lines[i])["checked"]
            for i in item_ids.find_lines(lines, cb)]


# ── a close ──────────────────────────────────────────────────────────────────

def test_a_close_flips_all_three_files_by_id_and_records_one_event(v):
    payload = live(v)
    assert box(v.hot, fx.CB_PLAN) == [True]
    assert box(v.review, fx.CB_PLAN) == [True]
    assert box(v.week, fx.CB_PLAN) == [True]
    # Only the box moved on each line: the words and the id are as they were.
    for path, original in ((v.hot, fx.HOTCACHE), (v.review, fx.REVIEW_PAGE),
                           (v.week, fx.WEEK)):
        now = path.read_text(encoding="utf-8").splitlines()
        was = original.splitlines()
        changed = [(a, b) for a, b in zip(was, now) if a != b]
        assert all(a.replace("- [ ]", "- [x]", 1) == b for a, b in changed)
    rows = fx.kpi_rows(v)
    assert len(rows) == 1 and len(rows[0]["keys"]) == 1
    assert payload["closed_n"] == 1
    assert payload["closed"][0]["cb"] == fx.CB_PLAN
    assert payload["closed"][0]["citation"] == fx.PROOF
    assert payload["closed"][0]["reason"] == "strong_cited_evidence"


def test_the_close_goes_through_the_one_writer(v, monkeypatch):
    seen = []
    real = completion_scan.mark_done
    monkeypatch.setattr(completion_scan, "mark_done",
                        lambda items: seen.append(items) or real(items))
    live(v)
    assert len(seen) == 1, "one batch, one call"
    assert {c["cb"] for c in seen[0]} == {fx.CB_PLAN, fx.CB_PERMIT}
    assert all(c["kind"] == "action_item" for c in seen[0])


def test_other_items_and_lines_outside_the_section_are_untouched(v):
    live(v)
    assert box(v.hot, fx.CB_DATED) == [False]
    assert box(v.hot, fx.CB_DEAL) == [False]
    assert box(v.hot, fx.CB_OWED) == [False]
    assert box(v.review, fx.CB_OWED) == [False]
    assert "- [ ] not a to-do, outside the section\n" in v.hot.read_text(
        encoding="utf-8")


def test_a_dry_run_writes_nothing_in_the_vault(v):
    before = fx.tree(v)
    payload = dj.run(live=False, ctx=fx.ctx(), runner=DONE)
    assert fx.tree(v) == before
    assert payload["mode"] == "dry-run" and payload["closed_n"] == 1
    assert not dj.state_path().exists(), "a dry run does not move the state"
    assert json.loads(dj.dry_path().read_text(encoding="utf-8"))["mode"] == "dry-run"
    assert str(v.vault) not in str(dj.dry_path())


def test_an_item_left_open_is_recorded_with_why_and_not_asked_about(v):
    promise = "The revised site plan went to Priya Raman on the 26th"
    payload = live(v, runner=fx.answering({fx.CB_PLAN: {
        "verdict": "done", "evidence_class": "decision", "confidence": 0.5,
        "citation": promise}}))
    assert box(v.hot, fx.CB_PLAN) == [False]
    assert payload["recorded"] == [{"cb": fx.CB_PLAN, "verdict": "open",
                                    "why": "confidence_0.5_below_0.85"}]
    got = dj.why(fx.CB_PLAN)
    assert got["found"] and got["last_verdict"] == "open"
    assert got["citation"] == promise
    assert got["downgrades"] == ["confidence_0.5_below_0.85"]
    assert dj.why("cb-ffffffff") == {"cb": "cb-ffffffff", "found": False}


# ── undo ─────────────────────────────────────────────────────────────────────

def test_undo_restores_the_three_files_byte_for_byte(v):
    before = {p: fx.read(p) for p in (v.hot, v.review, v.week)}
    live(v)
    assert all(fx.read(p) != b for p, b in before.items() if p != v.review) \
        and fx.read(v.review) != before[v.review]
    got = dj.undo(fx.CB_PLAN)
    assert got["restored"] == 3 and got["found"]
    dj.undo(fx.CB_PERMIT)
    assert {p: fx.read(p) for p in before} == before


def test_the_pin_lives_in_the_state_file_and_not_in_the_vault(v):
    live(v)
    dj.undo(fx.CB_PLAN)
    state = json.loads(dj.state_path().read_text(encoding="utf-8"))
    assert state["items"][fx.CB_PLAN]["pinned_open"] is True
    assert str(v.vault) not in str(dj.state_path())
    for name, raw in fx.tree(v).items():
        assert b"pinned" not in raw, name


def test_a_rerun_with_a_model_answering_done_leaves_an_undone_item_open(v):
    live(v)
    dj.undo(fx.CB_PLAN)
    asked = []
    payload = live(v, runner=fx.answering(fx.DONE_PLAN, log=asked), only=fx.CB_PLAN)
    assert asked, "the model was asked again, and still said done"
    assert box(v.hot, fx.CB_PLAN) == [False]
    assert box(v.review, fx.CB_PLAN) == [False]
    assert box(v.week, fx.CB_PLAN) == [False]
    assert payload["closed_n"] == 0
    assert payload["recorded"][0]["why"] == "pinned_open"
    assert len(fx.kpi_rows(v)) == 1, "no second close event"


def test_undo_of_a_line_edited_since_clears_only_the_box(v):
    live(v)
    text = v.hot.read_text(encoding="utf-8")
    v.hot.write_text(text.replace("Send Priya Raman the revised site plan",
                                  "Send Priya the revised plan, with sheet 5"),
                     encoding="utf-8")
    got = dj.undo(fx.CB_PLAN)
    assert "hotcache.md: box cleared (the line was edited since)" in got["notes"]
    line = [ln for ln in v.hot.read_text(encoding="utf-8").splitlines()
            if fx.CB_PLAN in ln][0]
    assert line.startswith("- [ ] Send Priya the revised plan, with sheet 5")
    assert fx.read(v.review) == fx.REVIEW_PAGE.encode()


def test_undo_works_for_an_expired_item_and_pins_it(v):
    live(v)
    assert box(v.hot, fx.CB_PERMIT) == [True]
    dj.undo(fx.CB_PERMIT)
    assert box(v.hot, fx.CB_PERMIT) == [False]
    live(v, runner=fx.answering())
    assert box(v.hot, fx.CB_PERMIT) == [False], "it does not expire again"


def test_undo_of_an_id_this_module_never_closed_still_pins_it(v):
    got = dj.undo(fx.CB_DEAL)
    assert got == {"cb": fx.CB_DEAL, "batch": "", "restored": 0, "notes": [],
                   "pinned": True, "found": False}
    assert dj.read_state()["items"][fx.CB_DEAL]["pinned_open"] is True


def test_windows_line_endings_survive_a_close_and_an_undo(tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch, newline="\r\n")
    before = {p: fx.read(p) for p in (v.hot, v.review, v.week)}
    assert b"\r\n" in before[v.hot]
    live(v)
    closed = fx.read(v.hot)
    assert closed.count(b"\r\n") == before[v.hot].count(b"\r\n")
    assert b"\n" not in closed.replace(b"\r\n", b"")
    dj.undo(fx.CB_PLAN)
    dj.undo(fx.CB_PERMIT)
    assert {p: fx.read(p) for p in before} == before


# ── expiry ───────────────────────────────────────────────────────────────────

def test_expiry_closes_the_old_undated_dealless_item_and_only_that(v):
    payload = live(v, runner=fx.answering())
    expired = [r for r in payload["closed"] if r["kind"] == "expired"]
    assert [r["cb"] for r in expired] == [fx.CB_PERMIT]
    assert expired[0]["reason"] == \
        "expired: no due date, no deal, nothing about it in 30 days"
    assert box(v.hot, fx.CB_PERMIT) == [True]
    assert box(v.hot, fx.CB_DATED) == [False], "it carries a due date"
    assert box(v.hot, fx.CB_DEAL) == [False], "it belongs to a live deal"
    assert payload["expired_n"] == 1 and payload["closed_n"] == 0


def test_an_expiry_is_not_counted_as_finished_work(v):
    live(v, runner=fx.answering())
    assert fx.kpi_rows(v) == []


def test_an_item_one_day_short_of_the_limit_does_not_expire(tmp_path, monkeypatch):
    fresh = fx.HOTCACHE.replace(f"cb={fx.CB_PERMIT} opened=2026-09-01",
                                f"cb={fx.CB_PERMIT} opened=2026-09-11")
    v = fx.make_vault(tmp_path, monkeypatch, hotcache=fresh)
    live(v, runner=fx.answering())          # 29 days old on 2026-10-10
    assert box(v.hot, fx.CB_PERMIT) == [False]
    dj.run(live=True, ctx=fx.ctx(today="2026-10-11"), runner=fx.answering())
    assert box(v.hot, fx.CB_PERMIT) == [True]


def test_a_run_whose_evidence_was_incomplete_expires_nothing(v):
    ctx = fx.ctx()
    ctx.degraded.append("mail (Gmail): RefreshError")
    payload = dj.run(live=True, ctx=ctx, runner=fx.answering())
    assert payload["expired_n"] == 0 and box(v.hot, fx.CB_PERMIT) == [False]
    assert payload["degraded"] == ["mail (Gmail): RefreshError"]


def test_a_machine_with_no_mailbox_signed_in_expires_nothing(v, monkeypatch):
    monkeypatch.setattr(data_sources, "is_tier1", lambda: False)
    ctx = dj.Ctx(today=fx.TODAY)            # nothing handed in: it must look
    payload = dj.run(live=True, ctx=ctx, runner=fx.answering())
    assert payload["degraded"] == ["mail: no signed-in mailbox"]
    assert payload["expired_n"] == 0


def test_expiry_can_be_switched_off(v, monkeypatch):
    monkeypatch.setattr(config_loader, "auto_close_expire_days", lambda: 0)
    assert live(v, runner=fx.answering())["expired_n"] == 0


# ── the day's record, and the three reads the briefings import ───────────────

def test_a_closed_item_appears_in_closed_today_exactly_once(v):
    live(v)
    live(v)                                  # a second run the same day
    rows = dj.closed_today(fx.TODAY)
    assert sorted(r["cb"] for r in rows) == [fx.CB_PLAN, fx.CB_PERMIT]
    assert dj.closed_today("2026-10-11") == []
    assert dj.closed_today() == [] or dj.closed_today() == rows


def test_a_later_run_the_same_day_adds_to_the_record(tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch)
    live(v, runner=fx.answering())           # expires the permit only
    live(v, only=fx.CB_PLAN)                 # then closes the plan
    assert sorted(r["cb"] for r in dj.closed_today(fx.TODAY)) == [
        fx.CB_PLAN, fx.CB_PERMIT]


def test_an_undone_item_leaves_closed_today(v):
    live(v)
    dj.undo(fx.CB_PLAN)
    assert [r["cb"] for r in dj.closed_today(fx.TODAY)] == [fx.CB_PERMIT]


def test_closed_today_accepts_a_date_or_a_string(v):
    from datetime import date
    live(v)
    assert dj.closed_today(date(2026, 10, 10)) == dj.closed_today("2026-10-10")


def test_every_rendered_line_carries_its_id_and_how_to_reopen_it(v):
    live(v)
    md = dj.render_closed_md(dj.closed_today(fx.TODAY))
    lines = md.splitlines()
    assert lines[0] == "**Closed today (1)**" and "**Expired (1)**" in lines
    items = [ln for ln in lines if ln.startswith("- ")]
    assert len(items) == 2
    for line, cb in zip(items, (fx.CB_PLAN, fx.CB_PERMIT)):
        assert cb in line
    # finished work is ticked; an expired item was given up on and is not
    assert items[0].startswith("- [x] ") and not items[1].startswith("- [x] ")
    assert f'decision: "{fx.PROOF}"' in items[0]
    assert "cb=" not in md and "<!--" not in md
    # one plain way to put an item back, with a real id, and what it means
    assert f"`/van-gogh:done-judge --undo {fx.CB_PLAN}`" in lines[-1]
    assert "stays open for good" in lines[-1]
    # a briefing is not a terminal: no shell lines in it
    assert "python" not in md and "$HOME" not in md and "Get-Content" not in md
    assert chr(0x2014) not in md and chr(0x2013) not in md


def test_nothing_closed_renders_as_nothing():
    assert dj.render_closed_md([]) == ""
    assert dj.render_closed_md(None) == ""
    assert dj.render_closed_md([{"text": "no id"}]) == ""


def test_a_long_day_is_capped_and_says_where_the_rest_is():
    rows = [{"cb": f"cb-{n:08x}", "text": f"Item {n}", "kind": "evidence",
             "evidence_class": "sent_mail", "citation": "sent"}
            for n in range(25)]
    md = dj.render_closed_md(rows)
    assert md.splitlines()[0] == "**Closed today (25)**"
    assert md.count("- [x] ") == dj.RENDER_CAP
    assert "- and 5 more, each with its id, in " in md


def test_the_sidecar_is_what_the_watcher_grades(v):
    live(v)
    path = dj.latest_path()
    assert path == v.logs / "done_judge_latest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "live" and data["date"] == fx.TODAY
    assert isinstance(data["closed"], list) and isinstance(data["closed_n"], int)
    assert data["closed_n"] + data["expired_n"] == len(data["closed"])
    for row in data["closed"]:
        assert {"cb", "text", "reason", "kind"} <= set(row)


# ── a run that fails is not a quiet day ──────────────────────────────────────

@pytest.fixture
def cli(v, monkeypatch):
    monkeypatch.setattr(data_sources, "is_tier1", lambda: False)
    monkeypatch.setattr(claude_update, "ensure_current", lambda *a, **k: None)
    monkeypatch.setattr(config_loader, "auto_close_enabled", lambda: True)
    return v


def judge_rows():
    return [r for r in run_ledger.read_runs() if r.get("job") == dj.JOB_NAME]


def test_a_model_that_stops_answering_writes_nothing_and_exits_75(
        cli, monkeypatch, capsys):
    import claude_cli
    monkeypatch.setattr(claude_cli, "run_claude", lambda *a, **k: fx.Reply(
        "You're out of extra usage. Resets 3:40pm", returncode=1))
    before = {p: fx.read(p) for p in (cli.hot, cli.review, cli.week)}
    rc = dj.main(["--live", "--today", fx.TODAY])
    assert rc == dj.RC_UNAVAILABLE == 75
    assert {p: fx.read(p) for p in before} == before
    assert not dj.state_path().exists() and not dj.latest_path().exists()
    rows = judge_rows()
    assert len(rows) == 1 and rows[0]["rc"] == 75
    assert rows[0]["error_class"] == "quota"
    assert "did not run" in capsys.readouterr().err


def test_a_failed_run_reads_differently_from_a_quiet_day(cli, monkeypatch):
    import claude_cli
    assert dj.judge_note(fx.TODAY) == "", "no run at all is not a failed run"
    monkeypatch.setattr(claude_cli, "run_claude", lambda *a, **k: fx.Reply(
        "You're out of extra usage. Resets 3:40pm", returncode=1))
    dj.main(["--live"])
    today = run_ledger.now_stamp()[:10]
    note = dj.judge_note(today)
    assert note == ("The check-off did not run today: the Claude usage limit "
                    "was reached. Nothing was closed.")
    assert dj.closed_today(today) == []
    # A quiet day: the run worked and found nothing to close.
    monkeypatch.setattr(claude_cli, "run_claude", fx.answering())
    assert dj.main(["--live"]) == 0
    assert dj.judge_note(today) == ""
    assert judge_rows()[-1]["rc"] == 0


def test_a_failure_part_way_through_leaves_everything_as_it_was(
        tmp_path, monkeypatch):
    words = ["amberline", "birchwood", "cedarfall", "dunmore", "elmhurst",
             "fernbank", "glenrock", "hazelton", "ivybridge", "juniper",
             "kestrel", "larkspur", "marigold", "nettleby", "oakhaven",
             "pinewick", "quillfeather", "rowanlea", "sagebrook", "thistledown"]
    tasks = "".join(
        f"- [ ] Deliver the {w} ledger packet <!-- cb=cb-bbbb{n:04x} "
        f"opened=2026-09-20 -->\n" for n, w in enumerate(words))
    v = fx.make_vault(tmp_path, monkeypatch,
                      hotcache=f"## {fx.HEADING}\n\n{tasks}")
    v.follow_up.write_text(
        "## Decisions Made\n\n" + "".join(
            f"- The {w} ledger packet was delivered and countersigned.\n"
            for w in words), encoding="utf-8")
    calls = []

    def third_call_fails(prompt, model=None, timeout=None):
        calls.append(prompt)
        if len(calls) == 3:
            return fx.Reply("Claude Code 2.0.1 does not support this model; "
                            "version 2.1.0 or newer is required", returncode=1)
        return fx.answering(default={
            "verdict": "done", "evidence_class": "decision",
            "citation": "ledger packet was delivered and countersigned"})(prompt)

    before = fx.tree(v)
    with pytest.raises(dj.JudgeUnavailable, match="newer is required"):
        dj.run(live=True, ctx=fx.ctx(), runner=third_call_fails)
    assert len(calls) == 3, "two batches had already answered done"
    assert fx.tree(v) == before
    assert not dj.state_path().exists()


def test_the_run_cap_leaves_the_rest_for_tomorrow(tmp_path, monkeypatch):
    tasks = "".join(
        f"- [ ] Deliver the amberline ledger packet number {n} "
        f"<!-- cb=cb-cccc{n:04x} opened=2026-09-20 -->\n" for n in range(20))
    v = fx.make_vault(tmp_path, monkeypatch,
                      hotcache=f"## {fx.HEADING}\n\n{tasks}")
    v.follow_up.write_text(
        "## Decisions Made\n\n- The amberline ledger packet was delivered.\n",
        encoding="utf-8")
    calls = []
    payload = dj.run(live=True, ctx=fx.ctx(), max_calls=2,
                     runner=fx.answering(default={}, log=calls))
    assert len(calls) == payload["calls"] == 2
    assert payload["judged"] == 16 and payload["capped"] == 4
    assert len(dj.read_state()["items"]) == 16, "the unreached are not marked judged"


def test_with_no_cap_named_every_item_is_read(tmp_path, monkeypatch):
    """Twenty five calls used to be the ceiling, and a ledger longer than
    that left the same tail unread every morning."""
    tasks = "".join(
        f"- [ ] Deliver the amberline ledger packet number {n} "
        f"<!-- cb=cb-dddd{n:04x} opened=2026-09-20 -->\n" for n in range(30 * 8))
    v = fx.make_vault(tmp_path, monkeypatch,
                      hotcache=f"## {fx.HEADING}\n\n{tasks}")
    v.follow_up.write_text(
        "## Decisions Made\n\n- The amberline ledger packet was delivered.\n",
        encoding="utf-8")
    calls = []
    payload = dj.run(live=False, ctx=fx.ctx(),
                     runner=fx.answering(default={}, log=calls))
    assert dj.MAX_CALLS == 0
    assert len(calls) == 30 and payload["capped"] == 0
    assert payload["judged"] == 240


def test_the_clock_is_the_one_ceiling_and_says_what_it_left(tmp_path, monkeypatch):
    tasks = "".join(
        f"- [ ] Deliver the amberline ledger packet number {n} "
        f"<!-- cb=cb-eeee{n:04x} opened=2026-09-20 -->\n" for n in range(24))
    v = fx.make_vault(tmp_path, monkeypatch,
                      hotcache=f"## {fx.HEADING}\n\n{tasks}")
    v.follow_up.write_text(
        "## Decisions Made\n\n- The amberline ledger packet was delivered.\n",
        encoding="utf-8")
    ticks = iter(range(0, 10 ** 6, 40 * 60))        # forty minutes a look
    calls = []
    payload = dj.run(live=False, ctx=fx.ctx(), clock=lambda: next(ticks),
                     runner=fx.answering(default={}, log=calls))
    assert len(calls) == 2, "the first call always runs; the third is past the hour"
    assert payload["judged"] == 16 and payload["capped"] == 8


def test_an_unchanged_item_is_not_judged_twice(v):
    calls = []
    runner = fx.answering(default={}, log=calls)
    live(v, runner=runner)
    live(v, runner=runner)
    assert len(calls) == 1
    again = dj.run(live=True, ctx=fx.ctx(today="2026-10-17"), runner=runner)
    assert len(calls) == 2 and again["calls"] == 1, "a week later it is"


# ── the one writer, by id ────────────────────────────────────────────────────

def cand(cb, text="x", **kw):
    return {"kind": "action_item", "tag": "Action Items", "text": text,
            "cb": cb, **kw}


def test_the_same_words_on_two_lines_closes_only_the_one_with_the_id(
        tmp_path, monkeypatch):
    twin = (f"## {fx.HEADING}\n\n"
            "- [ ] Call the bank about the outgoing wire for the Marlowe closing statement <!-- cb=cb-dddd0001 opened=2026-09-20 -->\n"
            "- [ ] Call the bank about the outgoing wire for the Marlowe closing statement <!-- cb=cb-dddd0002 opened=2026-09-25 -->\n")
    v = fx.make_vault(tmp_path, monkeypatch, hotcache=twin)
    v.week.write_text(
        "## Open Tasks (Obsidian)\n\n"
        "- [ ] [Action Items] Call the bank about the outgoing wire for the Marlowe closing statement <!-- cb=cb-dddd0001 opened=2026-09-20 -->\n"
        "- [ ] [Action Items] Call the bank about the outgoing wire for the Marlowe closing statement <!-- cb=cb-dddd0002 opened=2026-09-25 -->\n",
        encoding="utf-8")
    got = completion_scan.mark_done([cand("cb-dddd0001",
                                          "Call the bank about the outgoing wire for the Marlowe closing statement")])
    assert got["by_id"] == ["cb-dddd0001"]
    assert box(v.hot, "cb-dddd0001") == [True]
    assert box(v.hot, "cb-dddd0002") == [False], \
        "the text sync must not close the twin"
    assert box(v.week, "cb-dddd0001") == [True]
    assert box(v.week, "cb-dddd0002") == [False]
    assert got["hotcache_synced"] == []
    # The sentence is long enough that the text sync DOES pair the ticked
    # week line with the open twin (the two differ only in their ids), so
    # this passes because the stray tick was put back, not by luck.
    import morning_coffee as mc
    assert mc.sync_hotcache_from_week() != []


def test_a_line_reworded_by_hand_still_closes_by_its_id(v):
    text = v.hot.read_text(encoding="utf-8")
    v.hot.write_text(text.replace("Send Priya Raman the revised site plan",
                                  "Email Priya the plan (rev C)"),
                     encoding="utf-8")
    got = completion_scan.mark_done([cand(
        fx.CB_PLAN, "Send Priya Raman the revised site plan",
        source_path=str(v.review))])
    assert got["by_id"] == [fx.CB_PLAN]
    assert box(v.hot, fx.CB_PLAN) == box(v.review, fx.CB_PLAN) == [True]


def test_an_id_in_the_hotcache_but_missing_from_its_page_still_closes(v):
    # The page's own line was deleted by hand; only the other party's is left.
    v.review.write_text(fx.REVIEW_PAGE.replace(
        f"- [ ] Send Priya Raman the revised site plan "
        f"<!-- cb={fx.CB_PLAN} opened=2026-09-20 -->\n", ""), encoding="utf-8")
    page_before = fx.read(v.review)
    assert fx.CB_PLAN.encode() not in page_before
    payload = live(v)
    assert payload["closed"][0]["cb"] == fx.CB_PLAN and payload["failed"] == []
    assert box(v.hot, fx.CB_PLAN) == [True] and box(v.week, fx.CB_PLAN) == [True]
    assert fx.read(v.review) == page_before, "no line there carries the id"
    assert dj.undo(fx.CB_PLAN)["restored"] == 2


def test_an_id_with_no_open_hotcache_line_closes_nothing_anywhere(v):
    got = completion_scan.mark_done([cand("cb-eeee0001",
                                          source_path=str(v.review))])
    assert got["by_id"] == [] and got["marked"] == []
    assert got["errors"] == ["cb-eeee0001: no open hotcache line"]
    assert fx.kpi_rows(v) == []


def test_an_item_without_an_id_still_closes_the_old_way(v):
    got = completion_scan.mark_done([{
        "kind": "action_item", "tag": "Action Items",
        "text": f"Send Priya Raman the revised site plan (from [[{fx.REVIEW}]])"}])
    assert got["by_id"] == [] and len(got["marked"]) == 1
    assert box(v.week, fx.CB_PLAN) == [True]


# ── edge cases of the vault itself ───────────────────────────────────────────

def test_a_vault_with_no_hotcache_is_a_clean_empty_run(tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch, hotcache=None)
    assert not v.hot.exists()
    calls = []
    payload = dj.run(live=True, ctx=fx.ctx(), runner=fx.answering(log=calls))
    assert payload["open_items"] == 0 and payload["closed"] == [] and calls == []
    assert not v.hot.exists(), "it is not created either"


def test_the_open_set_reads_direction_deal_and_source(v):
    items = {i["id"]: i for i in dj.build_open_set(fx.ctx())}
    assert set(items) == {fx.CB_PLAN, fx.CB_PERMIT, fx.CB_DATED, fx.CB_DEAL,
                          fx.CB_OWED}
    assert items[fx.CB_PLAN]["dir"] == "by_me"
    assert items[fx.CB_PLAN]["source_path"] == str(v.review)
    assert items[fx.CB_PLAN]["meeting_id"] == "note_review"
    assert items[fx.CB_OWED]["dir"] == "to_me"
    assert items[fx.CB_OWED]["who"] == "Rosa Calder"
    assert items[fx.CB_DEAL]["deal"] == "Marlowe Finch refinancing"
    assert items[fx.CB_DATED]["has_due"] and not items[fx.CB_PERMIT]["has_due"]
    assert items[fx.CB_PERMIT]["deal"] == ""


def test_a_counterpartys_address_comes_off_their_entity_page(v):
    (v.vault / "wiki" / "entities" / "Priya Raman.md").write_text(
        "# Priya Raman\n\npriya@brightwater.example\nprimary@gmail.com\n",
        encoding="utf-8")
    items = {i["id"]: i for i in dj.build_open_set(fx.ctx())}
    assert items[fx.CB_PLAN]["counterparties"] == ["priya@brightwater.example"], \
        "the user's own address is never a counterparty"


# ── the command line ─────────────────────────────────────────────────────────

def test_a_malformed_id_is_refused_before_anything_runs(cli, capsys):
    assert dj.main(["--undo", "plan"]) == 2
    assert "is not an item id" in capsys.readouterr().err


def test_live_only_closes_one_item_and_undo_puts_it_back(cli, monkeypatch, capsys):
    import claude_cli
    monkeypatch.setattr(claude_cli, "run_claude", DONE)
    before = {p: fx.read(p) for p in (cli.hot, cli.review, cli.week)}
    assert dj.main(["--live", "--only", fx.CB_PLAN, "--today", fx.TODAY]) == 0
    assert box(cli.hot, fx.CB_PLAN) == [True]
    assert box(cli.hot, fx.CB_PERMIT) == [False], "only the one asked for"
    assert f"closed {fx.CB_PLAN} [decision]" in capsys.readouterr().out
    assert dj.main(["--undo", fx.CB_PLAN]) == 0
    assert {p: fx.read(p) for p in before} == before
    assert "is open again and will stay open" in capsys.readouterr().out


def test_closed_today_and_why_print_from_the_record(cli, monkeypatch, capsys):
    import claude_cli
    monkeypatch.setattr(claude_cli, "run_claude", DONE)
    dj.main(["--live", "--only", fx.CB_PLAN, "--today", fx.TODAY])
    capsys.readouterr()
    assert dj.main(["--closed-today", "--today", fx.TODAY]) == 0
    assert f"--undo {fx.CB_PLAN}" in capsys.readouterr().out
    assert dj.main(["--why", fx.CB_PLAN]) == 0
    out = capsys.readouterr().out
    assert "last_verdict: done" in out and fx.PROOF in out
    assert dj.main(["--closed-today", "--today", "2026-01-01"]) == 0
    assert "Nothing was closed today." in capsys.readouterr().out


def test_the_default_is_a_dry_run_that_records_no_run(cli, monkeypatch, capsys):
    import claude_cli
    monkeypatch.setattr(claude_cli, "run_claude", DONE)
    before = fx.tree(cli)
    assert dj.main(["--today", fx.TODAY, "--max-calls", "3"]) == 0
    out = capsys.readouterr().out
    assert fx.tree(cli) == before
    assert f"vault: {cli.vault}" in out and "calls: 1" in out
    assert "Nothing was written in the vault." in out
    assert judge_rows() == [], "a dry run is not a run of the job"


def test_switched_off_the_scheduled_run_does_nothing(cli, monkeypatch, capsys):
    monkeypatch.setattr(config_loader, "auto_close_enabled", lambda: False)
    before = fx.tree(cli)
    assert dj.main(["--live"]) == 0
    assert fx.tree(cli) == before
    assert "Auto close is off" in capsys.readouterr().out


def test_backfill_only_reports_counts_and_writes_only_when_live(
        tmp_path, monkeypatch, capsys):
    v = fx.make_vault(tmp_path, monkeypatch,
                      hotcache=f"## {fx.HEADING}\n\n- [ ] Call the bank\n")
    before = fx.read(v.hot)
    assert dj.main(["--backfill-only"]) == 0
    assert json.loads(capsys.readouterr().out)["hotcache"]["stamped"] == 1
    assert fx.read(v.hot) == before
    assert dj.main(["--backfill-only", "--live"]) == 0
    assert b"cb=cb-" in fx.read(v.hot)


def test_the_cli_path_from_the_scheduler_is_put_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    binary = tmp_path / "bin" / "claude"
    dj._use_claude(str(binary))
    import os
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")


def test_a_vault_that_cannot_be_read_is_a_failed_run_never_a_quiet_day(v, monkeypatch):
    """An unreadable hot cache used to read as an empty to-do list."""
    import builtins
    hot = str(dj.config_loader.hotcache_path())
    real_open = builtins.open

    def locked(path, *a, **k):
        if str(path) == hot:
            raise PermissionError(1, "Operation not permitted", hot)
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", locked)
    rc = dj.main(["--live"])
    monkeypatch.setattr(builtins, "open", real_open)
    assert rc == dj.RC_UNAVAILABLE
    today = run_ledger.now_stamp()[:10]
    assert dj.judge_note(today).startswith("The check-off did not run today")
    assert dj.closed_today(today) == []
