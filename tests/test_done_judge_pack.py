"""The evidence pack, the prompt, and the checks on what a model says.

No model is called. Each test hands `validate` an answer a model could give
and reads what code made of it.
"""
import json

import pytest

import config_loader
import done_judge as dj
import done_judge_fixture as fx

ME = "primary@gmail.com"
PRIYA = "priya@brightwater.example"


def item(**over):
    base = {"id": "cb-00000001", "text": "Send Priya Raman the revised site plan",
            "task": "Send Priya Raman the revised site plan", "dir": "by_me",
            "who": "", "opened": "2026-09-20", "due": "", "has_due": False,
            "deal": "", "source_title": "", "source_path": "",
            "meeting_id": "", "counterparties": [PRIYA]}
    base.update(over)
    base["tokens"] = dj.distinctive(base["task"])
    return base


def sent(body, day="2026-09-25", to=(PRIYA,), subject="Site plan", **over):
    row = {"id": "m1", "account": "Gmail", "direction": "sent", "date": day,
           "from": ME, "to": list(to), "cc": [], "subject": subject,
           "body": body, "has_attachments": False, "attachment_names": []}
    row.update(over)
    return row


def answer(citation, verdict="done", cls="sent_mail", confidence=0.95):
    return {"verdict": verdict, "confidence": confidence,
            "evidence_class": cls, "citation": citation}


def judged(it, ctx, raw, **kw):
    pack, sections = dj.build_pack(it, ctx)
    return dj.validate(it, pack, sections, raw, ctx, floor=0.85, **kw)


GOOD = "Priya, the revised site plan is attached, all four sheets."


# ── a clean close, as the baseline everything below departs from ─────────────

def test_a_sent_mail_that_delivers_the_thing_closes_it():
    ctx = fx.ctx(mail=[sent(GOOD)])
    got = judged(item(), ctx, answer(GOOD))
    assert got["verdict"] == "done" and got["evidence_class"] == "sent_mail"
    assert got["reason"] == "strong_cited_evidence"
    assert dj.decide(item(), got) == ("auto", "strong_cited_evidence")
    assert got["done_date"] == "2026-09-25"


# ── the pack ─────────────────────────────────────────────────────────────────

def test_an_item_with_no_evidence_never_reaches_the_model(tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch)
    v.follow_up.unlink()
    calls = []
    payload = dj.run(live=False, ctx=fx.ctx(), runner=fx.answering(log=calls))
    assert calls == [] and payload["calls"] == 0
    assert payload["empty_pack"] == payload["open_items"] == 5


def test_the_id_marker_can_never_be_cited_back_as_evidence():
    body = "Done.\n- [x] Send the plan <!-- cb=cb-00000001 opened=2026-09-20 -->"
    pack, _ = dj.build_pack(item(), fx.ctx(mail=[sent(body)]))
    assert "cb=" not in pack.text and "<!--" not in pack.text


def test_a_mail_section_records_where_its_own_header_ends():
    pack, sections = dj.build_pack(item(), fx.ctx(mail=[sent(GOOD)]))
    sec = sections[0]
    assert sec["text"][:sec["header_len"]].rstrip().endswith("(sent)")
    assert sec["text"][sec["header_len"]:] == GOOD
    assert dj.HEADER_RE.search(sec["text"][:sec["header_len"]])


def test_a_draft_is_never_admitted_to_a_pack():
    row = sent(GOOD, direction="draft")
    assert dj.build_pack(item(), fx.ctx(mail=[row]))[1] == []


def test_inbound_mail_counts_only_from_a_person_the_item_names():
    stranger = sent(GOOD, direction="inbound", **{"from": "x@other.example"})
    assert dj.build_pack(item(), fx.ctx(mail=[stranger]))[1] == []
    named = sent(GOOD, direction="inbound", **{"from": PRIYA})
    assert [s["cls"] for s in dj.build_pack(
        item(), fx.ctx(mail=[named]))[1]] == ["inbound_mail"]


def test_mail_from_before_the_item_existed_is_left_out_of_the_pack():
    assert dj.build_pack(item(), fx.ctx(mail=[sent(GOOD, day="2026-09-10")]))[1] == []
    # Two days of grace: a note sent the evening before the meeting counts.
    assert len(dj.build_pack(item(), fx.ctx(mail=[sent(GOOD, day="2026-09-18")]))[1]) == 1


def test_the_most_relevant_mail_wins_over_the_most_recent():
    rows = [sent(f"Unrelated note number {n}.", day=f"2026-10-0{n}", id=f"x{n}")
            for n in range(1, 6)] + [sent(GOOD, day="2026-09-21", id="proof")]
    _, sections = dj.build_pack(item(), fx.ctx(mail=rows))
    assert len(sections) == dj.MAIL_MAX
    assert GOOD in sections[0]["text"]


def page(title, day, decisions=(), meeting_id="", path=None):
    return {"path": path or f"/v/{title}.md", "title": title, "date": day,
            "meeting_id": meeting_id, "decisions": list(decisions),
            "tokens": dj.distinctive(title + " " + " ".join(decisions)
                                     + " revised site plan priya raman")}


def test_a_decision_from_the_items_own_meeting_is_not_evidence():
    ctx = fx.ctx()
    ctx.pages = [page("Own", "2026-09-20", [fx.PROOF], path="/v/own.md")]
    assert dj.build_pack(item(source_path="/v/own.md"), ctx)[1] == []
    ctx.pages = [page("Later", "2026-09-27", [fx.PROOF])]
    assert [s["cls"] for s in dj.build_pack(item(), ctx)[1]] == ["decision"]


def test_a_decision_needs_two_distinctive_words_in_common():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", ["The plan for lunch is settled."])]
    assert dj.build_pack(item(), ctx)[1] == []


def _talk(*lines):
    return {"transcript": [{"speaker": {"source": who}, "text": text}
                           for who, text in lines]}


def test_a_transcript_window_knows_when_it_holds_the_users_own_line():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda mid, include_transcript=False: _talk(
        ("speaker", "Did the revised site plan go to Priya Raman?"),
        ("microphone", "Yes, I sent Priya Raman the revised site plan on Monday."))
    _, sections = dj.build_pack(item(), ctx)
    assert [s["cls"] for s in sections] == ["transcript_user"]
    assert "[microphone] Yes, I sent" in sections[0]["text"]
    assert ctx.fetches == 1


def test_an_empty_transcript_is_no_evidence_and_no_error():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda mid, include_transcript=False: {"transcript": []}
    it = item()
    assert dj.build_pack(it, ctx)[1] == []
    assert ctx.degraded == [] and not it.get("incomplete")


def test_the_meeting_the_promise_was_made_in_is_never_fetched_as_proof():
    ctx = fx.ctx()
    ctx.pages = [page("Own", "2026-09-20", meeting_id="n1")]
    ctx.fetch_note = lambda *a, **k: pytest.fail("fetched the item's own meeting")
    assert dj.build_pack(item(meeting_id="n1"), ctx)[1] == []


def test_a_window_that_only_repeats_the_task_is_the_promise_not_proof():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda mid, include_transcript=False: _talk(
        ("microphone", "Action for me: send Priya Raman the revised site plan."))
    assert dj.build_pack(item(), ctx)[1] == []


def test_a_transcript_that_cannot_be_fetched_marks_the_run_degraded():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]

    def boom(mid, include_transcript=False):
        raise RuntimeError("401 for key sk-secret-value at 12 Elm Street")

    ctx.fetch_note = boom
    assert dj.build_pack(item(), ctx)[1] == []
    assert ctx.degraded == ["transcript: RuntimeError"], \
        "the type only: an exception's text quotes what it failed on"


def test_past_the_fetch_cap_an_item_is_marked_as_not_fully_searched(monkeypatch):
    monkeypatch.setattr(dj, "TRANSCRIPT_FETCH_CAP", 0)
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda *a, **k: pytest.fail("fetched past the cap")
    it = item()
    dj.build_pack(it, ctx)
    assert it["incomplete"] is True and ctx.fetch_capped
    results = {it["id"]: {"verdict": "unknowable", "had_evidence": False,
                          "searched": True}}
    old = dict(it, opened="2026-08-01")
    assert dj.expire_candidates([old], results, {"items": {}}, fx.TODAY, []) == []


# ── the checks on an answer ──────────────────────────────────────────────────

def test_a_citation_that_is_not_in_the_evidence_is_refused():
    ctx = fx.ctx(mail=[sent("Priya, working on it.")])
    got = judged(item(), ctx, answer(GOOD))
    assert got["verdict"] == "open"
    assert "citation_not_in_one_section" in got["downgrades"]
    assert judged(item(), ctx, answer(""))["downgrades"] == ["empty_citation"]


def test_a_citation_spliced_across_two_sources_is_refused():
    ctx = fx.ctx(mail=[sent("Priya, the revised site plan", id="a"),
                       sent("is attached, all four sheets. Priya Raman.",
                            id="b", day="2026-09-26")])
    pack, sections = dj.build_pack(item(), ctx)
    assert len(sections) == 2
    got = dj.validate(item(), pack, sections,
                      answer("the revised site plan is attached"), ctx, floor=0.85)
    assert got["verdict"] == "open"
    assert "citation_not_in_one_section" in got["downgrades"]


def test_evidence_dated_before_the_item_is_refused():
    it = item()
    sec = {"cls": "sent_mail", "source": "s", "date": "2026-09-19",
           "text": GOOD, "mail": sent(GOOD, day="2026-09-19")}
    it["control_sections"] = [sec]
    got = judged(it, fx.ctx(), answer(GOOD))
    assert got["verdict"] == "open"
    assert "source_predates_item" in got["downgrades"]


def test_the_section_decides_the_class_not_the_model():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda mid, include_transcript=False: _talk(
        ("speaker", "The revised site plan went over to Priya Raman already."))
    got = judged(item(), ctx, answer(
        "The revised site plan went over to Priya Raman already.", cls="sent_mail"))
    assert got["evidence_class"] == "transcript_other"
    assert got["model_class"] == "sent_mail"
    assert "class_corrected_from_sent_mail" in got["downgrades"]
    assert got["verdict"] == "open"


def test_someone_elses_line_does_not_close_what_the_user_owed():
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    ctx.fetch_note = lambda mid, include_transcript=False: _talk(
        ("microphone", "Let me look at the revised site plan for Priya Raman."),
        ("speaker", "Great, so the revised site plan for Priya Raman is done."))
    got = judged(item(), ctx, answer(
        "Great, so the revised site plan for Priya Raman is done.",
        cls="transcript_user"))
    assert "citation_not_on_a_user_line" in got["downgrades"]
    assert got["verdict"] == "open"


def test_a_named_speaker_who_is_the_user_counts_as_the_user():
    ctx = fx.ctx()
    assert dj._is_user_label("Test User", ctx) and dj._is_user_label("microphone", ctx)
    assert not dj._is_user_label("speaker", ctx)
    assert not dj._is_user_label("Priya Raman", ctx)


def test_a_citation_that_lives_only_in_the_mail_header_is_refused():
    ctx = fx.ctx(mail=[sent("Priya, more soon.",
                            subject="Revised site plan attached and final")])
    got = judged(item(), ctx, answer(
        "Subject: Revised site plan attached and final"))
    assert got["verdict"] == "open"
    assert "citation_is_mail_metadata" in got["downgrades"]


def test_mail_the_user_sent_only_to_themself_proves_nothing():
    ctx = fx.ctx(mail=[sent(GOOD, to=(ME,))])
    got = judged(item(counterparties=[ME]), ctx, answer(GOOD))
    assert "mail_has_no_real_recipient" in got["downgrades"]
    assert got["verdict"] == "open"


def test_a_citation_that_is_the_tasks_own_words_is_refused_both_ways():
    task = "Send Priya Raman the revised site plan"
    ctx = fx.ctx(mail=[sent(f"Open items:\n{task}\nand also the venue.")])
    exact = judged(item(), ctx, answer(task))
    assert "citation_is_the_task" in exact["downgrades"]
    # Shorter than the task, and contained in it.
    part = judged(item(), ctx, answer("Priya Raman the revised site plan"))
    assert "citation_is_the_task" in part["downgrades"]
    # Longer than the task, and containing it.
    more = judged(item(), ctx, answer(f"Open items:\n{task}"))
    assert "citation_is_the_task" in more["downgrades"]


def test_the_floor_in_config_is_the_floor(monkeypatch):
    ctx = fx.ctx(mail=[sent(GOOD)])
    pack, sections = dj.build_pack(item(), ctx)
    monkeypatch.setattr(config_loader, "auto_close_confidence", lambda: 0.97)
    low = dj.validate(item(), pack, sections, answer(GOOD, confidence=0.95), ctx)
    assert low["verdict"] == "open"
    assert low["downgrades"][-1] == "confidence_0.95_below_0.97"
    monkeypatch.setattr(config_loader, "auto_close_confidence", lambda: 0.9)
    assert dj.validate(item(), pack, sections,
                       answer(GOOD, confidence=0.95), ctx)["verdict"] == "done"


def test_someone_elses_acknowledgement_needs_the_higher_bar():
    thanks = "Thanks, got the revised site plan, it all looks right."
    row = sent(thanks, direction="inbound", **{"from": PRIYA, "to": [ME]})
    ctx = fx.ctx(mail=[row])
    assert judged(item(), ctx, answer(thanks, cls="inbound_mail",
                                      confidence=0.88))["verdict"] == "open"
    ok = judged(item(), ctx, answer(thanks, cls="inbound_mail", confidence=0.92))
    assert ok["verdict"] == "done" and ok["reason"] == "counterparty_ack"


def test_what_someone_else_owes_closes_only_on_their_delivery():
    owed = item(dir="to_me", who="Priya Raman",
                text="Priya Raman to send the survey",
                task="Priya Raman to send the survey")
    chase = "Priya, any news on the survey you were sending?"
    got = judged(owed, fx.ctx(mail=[sent(chase)]), answer(chase))
    assert got["verdict"] == "open" and "weak_class:sent_mail" in got["downgrades"]
    here = "The survey is attached, signed by the surveyor."
    row = sent(here, direction="inbound", **{"from": PRIYA, "to": [ME]})
    done = judged(owed, fx.ctx(mail=[row]), answer(here, cls="inbound_mail"))
    assert done["verdict"] == "done"
    assert done["reason"] == "counterparty_delivered_mail"


def test_a_named_speaker_delivering_on_a_call_closes_what_they_owed():
    owed = item(dir="to_me", who="Priya Raman",
                text="Priya Raman to send the revised site plan",
                task="Priya Raman to send the revised site plan")
    ctx = fx.ctx()
    ctx.pages = [page("Later", "2026-09-27", meeting_id="n2")]
    line = "I sent the revised site plan over this morning, check your inbox."
    ctx.fetch_note = lambda mid, include_transcript=False: _talk(
        ("Priya Raman", line))
    got = judged(owed, ctx, answer(line, cls="transcript_other"))
    assert got["verdict"] == "done"
    assert got["reason"] == "counterparty_delivered_transcript"
    # The same words from an unnamed far side are not attributable to her.
    ctx2 = fx.ctx()
    ctx2.pages = [page("Later", "2026-09-27", meeting_id="n3")]
    ctx2.fetch_note = lambda mid, include_transcript=False: _talk(("speaker", line))
    assert judged(owed, ctx2, answer(line, cls="transcript_other"))["verdict"] == "open"


def test_moot_without_a_logged_decision_stays_open():
    maybe = "Priya, we may drop the site plan altogether."
    got = judged(item(), fx.ctx(mail=[sent(maybe)]), answer(maybe, verdict="moot"))
    assert got["verdict"] == "open"
    assert "moot_without_a_decision" in got["downgrades"]
    ctx = fx.ctx()
    line = "The revised site plan for Priya Raman is cancelled, the site is sold."
    ctx.pages = [page("Later", "2026-09-27", [line])]
    moot = judged(item(), ctx, answer(line, verdict="moot", cls="decision"))
    assert moot["verdict"] == "moot"
    assert dj.decide(item(), moot) == ("auto", "decision_cancels_it")


def test_a_pinned_item_stays_open_whatever_the_evidence():
    ctx = fx.ctx(mail=[sent(GOOD)])
    got = judged(item(), ctx, answer(GOOD), record={"pinned_open": True})
    assert got["verdict"] == "open" and got["downgrades"][-1] == "pinned_open"
    assert dj.decide(item(), got) == ("record", "pinned_open")


def test_nonsense_from_the_model_reads_as_open():
    ctx = fx.ctx(mail=[sent(GOOD)])
    got = judged(item(), ctx, {"verdict": "finished", "confidence": "high",
                               "evidence_class": "vibes", "citation": GOOD})
    assert got["verdict"] == "open"
    assert got["downgrades"][:3] == ["bad_verdict", "bad_confidence",
                                     "bad_evidence_class"]


def test_a_done_date_outside_the_items_life_is_repaired():
    ctx = fx.ctx(mail=[sent(GOOD)])
    for bad in ("2026-01-01", "2027-01-01", ""):
        got = judged(item(), ctx, {**answer(GOOD), "done_date": bad})
        assert got["done_date"] == "2026-09-25"
        assert "done_date_repaired" in got["downgrades"]
    kept = judged(item(), ctx, {**answer(GOOD), "done_date": "2026-09-26"})
    assert kept["done_date"] == "2026-09-26"


def test_decide_only_ever_answers_auto_or_record():
    for verdict in dj.VERDICTS:
        tier, _why = dj.decide(item(), {"verdict": verdict, "downgrades": []})
        assert tier == ("auto" if verdict in ("done", "moot") else "record")


# ── the delta rule ───────────────────────────────────────────────────────────

def test_only_moved_evidence_or_a_week_brings_an_item_back():
    it = item()
    assert dj.needs_judging(it, "h1", {}, fx.TODAY)
    fresh = {"evidence_hash": "h1", "judged_at": "2026-10-08T05:10:00"}
    assert not dj.needs_judging(it, "h1", fresh, fx.TODAY)
    assert dj.needs_judging(it, "h2", fresh, fx.TODAY), "the evidence moved"
    assert dj.needs_judging(it, "h1", fresh, fx.TODAY, force=True)
    old = {"evidence_hash": "h1", "judged_at": "2026-10-03T05:10:00"}
    assert dj.needs_judging(it, "h1", old, fx.TODAY), "a week has passed"
    assert dj.needs_judging(it, "h1", {"evidence_hash": "h1"}, fx.TODAY)


def test_the_hash_moves_when_the_evidence_does():
    a, _ = dj.build_pack(item(), fx.ctx(mail=[sent(GOOD)]))
    b, _ = dj.build_pack(item(), fx.ctx(mail=[sent(GOOD + " Thanks.")]))
    assert dj.evidence_hash(item(), a) == dj.evidence_hash(item(), a)
    assert dj.evidence_hash(item(), a) != dj.evidence_hash(item(), b)


# ── the model call ───────────────────────────────────────────────────────────

def _packs(it):
    pack, _ = dj.build_pack(it, fx.ctx(mail=[sent(GOOD)]))
    return {it["id"]: pack}


def test_the_prompt_names_the_user_and_carries_only_that_items_evidence():
    it = item()
    prompt = dj.build_prompt([it], _packs(it), "Test User")
    assert "Test User owes this" in prompt and GOOD in prompt
    assert "[microphone] are Test User speaking" in prompt
    assert "{user_name}" not in prompt and "cb=" not in prompt
    assert chr(0x2014) not in prompt and chr(0x2013) not in prompt
    owed = item(dir="to_me", who="Priya Raman")
    assert "Priya Raman owes this to Test User" in dj.build_prompt(
        [owed], _packs(owed), "Test User")


def test_an_item_the_model_left_out_is_asked_about_once_more():
    a, b = item(id="cb-0000000a"), item(id="cb-0000000b")
    packs = {**_packs(a), **_packs(b)}
    prompts = []

    def runner(prompt, model=None, timeout=None):
        prompts.append(prompt)
        first = len(prompts) == 1
        ids = ["cb-0000000a"] if first else ["cb-0000000b"]
        return fx.Reply(json.dumps({"items": [
            {"id": i, "verdict": "open"} for i in ids]}))

    out = dj.judge_batch([a, b], packs, runner=runner)
    assert set(out) == {"cb-0000000a", "cb-0000000b"}
    assert len(prompts) == 2 and "cb-0000000a" not in prompts[1]
    assert out["cb-0000000b"]["recovered"] is True


def test_an_item_omitted_twice_stays_open_and_says_why(tmp_path, monkeypatch):
    fx.make_vault(tmp_path, monkeypatch)
    calls = []
    payload = dj.run(live=False, ctx=fx.ctx(), runner=fx.answering(log=calls))
    assert len(calls) == 2, "asked, then asked once more, then stopped"
    assert payload["closed_n"] == 0
    assert payload["recorded"] == [
        {"cb": fx.CB_PLAN, "verdict": "open", "why": "model_omitted_id"}]


def test_prose_around_the_json_does_not_lose_the_answer():
    it = item()
    wrapped = ("Here is my assessment.\n```json\n" + json.dumps(
        {"items": [{"id": it["id"], "verdict": "open"}]}) + "\n```\nDone.")
    out = dj.judge_batch([it], _packs(it),
                         runner=lambda p, model=None, timeout=None: fx.Reply(wrapped))
    assert out[it["id"]]["verdict"] == "open"


@pytest.mark.parametrize("reply", [
    fx.Reply("", returncode=1),
    fx.Reply("You're out of extra usage. Resets 3:40pm"),
])
def test_a_model_that_cannot_answer_stops_the_run(reply):
    it = item()
    with pytest.raises(dj.JudgeUnavailable):
        dj.judge_batch([it], _packs(it),
                       runner=lambda p, model=None, timeout=None: reply)


def test_a_usage_cap_raised_by_the_cli_layer_stops_the_run():
    import claude_cli
    it = item()

    def capped(prompt, model=None, timeout=None):
        raise claude_cli.UsageCap("usage limit reached, resets 3pm")

    with pytest.raises(dj.JudgeUnavailable, match="resets 3pm"):
        dj.judge_batch([it], _packs(it), runner=capped)


def test_the_model_is_sonnet_unless_config_names_another(monkeypatch):
    assert dj.JUDGE_MODEL == "claude-sonnet-5-5"
    monkeypatch.setattr(config_loader, "auto_close_model", lambda: "")
    assert dj._model() == "claude-sonnet-5-5"
    monkeypatch.setattr(config_loader, "auto_close_model", lambda: "other-model")
    assert dj._model() == "other-model"


# ── a reply is read apart from the message it answers ────────────────────────

QUOTED = ("\n\n From: Priya Raman <priya@example.com>\n"
          " Sent: Thursday, September 24, 2026 9:02 AM\n To: Me\n"
          " Subject: Site plan\n\n I mailed the stamped sheets to you "
          "yesterday. Can you send the revised site plan?")


def test_what_he_wrote_is_kept_apart_from_what_he_was_answering():
    ctx = fx.ctx(mail=[sent(GOOD + QUOTED)])
    _pack, sections = dj.build_pack(item(), ctx)
    sec = sections[0]
    assert sec["own"] == GOOD and "stamped sheets" not in sec["own"]
    assert dj.QUOTED_NOTE in sec["text"] and "stamped sheets" in sec["text"]


def test_a_sentence_quoted_beneath_his_reply_closes_nothing():
    ctx = fx.ctx(mail=[sent("Thanks Priya, noted." + QUOTED)])
    theirs = "I mailed the stamped sheets to you yesterday."
    got = judged(item(), ctx, answer(theirs))
    assert got["verdict"] == "open"
    assert "citation_is_quoted_text" in got["downgrades"]
    loose = judged(item(), ctx, answer(theirs),
                   disabled=frozenset({"own_words_not_quote"}))
    assert loose["verdict"] == "done", "the guard is what stopped it"


def test_his_own_sentence_above_a_quote_still_closes_it():
    ctx = fx.ctx(mail=[sent(GOOD + QUOTED)])
    assert judged(item(), ctx, answer(GOOD))["verdict"] == "done"


def test_a_reply_with_nothing_quoted_and_a_bare_forward_are_all_his():
    assert dj.split_quoted("Thanks, got it.") == ("Thanks, got it.", "")
    bare = " From: A <a@example.com>\n Sent: Monday\n hello"
    assert dj.split_quoted(bare)[1] == ""
    _pack, sections = dj.build_pack(item(), fx.ctx(mail=[sent(GOOD)]))
    assert sections[0]["own"] is None, "nothing quoted, nothing to tell apart"


def test_the_rule_about_quoted_text_is_in_the_prompt():
    assert "Never cite it: it is not the reply" in dj.JUDGE_RULES


def test_mail_that_went_to_someone_else_keeps_a_seat_beside_a_busy_counterparty():
    busy = [sent(f"Priya, note {n} on the revised site plan schedule.",
                 id=f"b{n}", day=f"2026-09-2{n + 2}") for n in range(4)]
    proof = sent("Sending the revised site plan to the surveyor now.",
                 id="other", to=("surveyor@example.com",), day="2026-09-22")
    _pack, sections = dj.build_pack(item(), fx.ctx(mail=busy + [proof]))
    ids = [s["mail"]["id"] for s in sections]
    assert "other" in ids and len(ids) == dj.MAIL_MAX


# ── the queue ────────────────────────────────────────────────────────────────

def test_the_newest_mail_or_decision_is_judged_first_and_a_meeting_is_not_news():
    def sec(cls, day):
        return {"cls": cls, "date": day}
    old = {"id": "a", "opened": "2026-06-01"}
    new = {"id": "b", "opened": "2026-10-08"}
    mid = {"id": "c", "opened": "2026-09-01"}
    order = sorted([(old, [sec("transcript_other", "2026-10-09")]),
                    (mid, [sec("decision", "2026-10-02")]),
                    (new, [sec("sent_mail", "2026-10-09")])],
                   key=lambda p: dj.queue_key(*p))
    assert [p[0]["id"] for p in order] == ["b", "c", "a"]
