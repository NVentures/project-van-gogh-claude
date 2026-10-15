"""The planted controls, and one mutation proof per guard.

Contract V3. A stub answers "done" for every control, citing the sentence a
careless reader would cite. Every known-open control must still come out
open, every known-done control must close, and for each guard in `validate`
switching that ONE guard off must let a planted control close. A guard no
control depends on fails here, because a guard nothing can catch failing is
a guard nobody can tell is working.

No model is called in this file. The same controls go through the real model
in `done_judge.py --selftest`.
"""
import inspect
import json
import re

import pytest

import done_judge as dj

ITEMS, CTX = dj.load_controls()
KINDS = {}
for _item in ITEMS:
    KINDS.setdefault(_item["control_kind"], []).append(_item["id"])
STUB = dj.judge_controls(runner=dj.stub_runner())
ROWS = {r["id"]: r for r in STUB["rows"]}
OPEN_IDS = [r["id"] for r in STUB["rows"] if r["kind"] != "known_done"]
DONE_IDS = [r["id"] for r in STUB["rows"] if r["kind"] == "known_done"]


def closed_when_disabled(guard: str) -> list:
    """The known-open controls that close once `guard` alone is off."""
    report = dj.judge_controls(runner=dj.stub_runner(),
                               disabled=frozenset({guard}))
    return report["false_done"]


def require_a_dependent_control(guard: str) -> list:
    flipped = closed_when_disabled(guard)
    assert flipped, (f"no control depends on the guard {guard!r}: with it "
                     "switched off every known-open control still stays open")
    return flipped


# ── the set itself ───────────────────────────────────────────────────────────

def test_the_fixture_holds_the_required_controls():
    assert len(OPEN_IDS) >= 23
    assert len(KINDS["promise"]) == 10 and len(KINDS["meeting"]) == 10
    for kind in ("owed_promise", "owed_chase", "unsent_draft"):
        assert len(KINDS[kind]) >= 1, kind
    assert len(DONE_IDS) >= 3
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)


def test_the_three_new_kinds_are_what_they_claim_to_be():
    by_id = {i["id"]: i for i in ITEMS}
    promise = by_id[KINDS["owed_promise"][0]]
    chase = by_id[KINDS["owed_chase"][0]]
    draft = by_id[KINDS["unsent_draft"][0]]
    assert promise["dir"] == chase["dir"] == "to_me" and draft["dir"] == "by_me"
    assert promise["control_sections"][0]["mail"]["direction"] == "inbound"
    assert chase["control_sections"][0]["mail"]["direction"] == "sent"
    assert chase["control_sections"][0]["mail"]["from"] in CTX.self_emails
    assert [s["cls"] for s in draft["control_sections"]] == ["draft"]


def test_every_control_names_a_sentence_and_most_can_really_be_cited():
    """The stub cites `damning`. Apart from the two controls whose whole
    point is a citation that is NOT in the evidence, it must be there."""
    absent = []
    for item in ITEMS:
        assert item["control_damning"], item["id"]
        pack, _ = dj.build_pack(item, CTX)
        if dj._norm(item["control_damning"]) not in dj._norm(pack.text):
            absent.append(item["id"])
    assert sorted(absent) == ["ctl-guard-fabricated", "ctl-guard-straddle"]


# ── known open stays open, known done closes ─────────────────────────────────

@pytest.mark.parametrize("cid", OPEN_IDS)
def test_a_known_open_control_stays_open_under_a_stub_that_says_done(cid):
    row = ROWS[cid]
    assert row["verdict"] == "open" and not row["closes"], row


@pytest.mark.parametrize("cid", DONE_IDS)
def test_a_known_done_control_closes(cid):
    row = ROWS[cid]
    assert row["verdict"] == "done" and row["closes"], row
    assert row["evidence_class"] != "none" and row["citation"]


def test_the_stub_really_did_say_done_to_everything():
    """Control for the control: with every guard off, the same stub closes
    every known-open item, so 'open' above is the guards' doing and not a
    stub that quietly answered open."""
    report = dj.judge_controls(runner=dj.stub_runner(),
                               disabled=frozenset(dj.GUARDS))
    assert sorted(report["false_done"]) == sorted(OPEN_IDS)


DEDICATED = {i["id"]: i["control_guard"] for i in ITEMS if i["control_guard"]}
EXPECT = {
    "one_section": "citation_not_in_one_section",
    "not_before_opened": "source_predates_item",
    "class_from_section": "class_corrected_from_sent_mail",
    "user_line": "citation_not_on_a_user_line",
    "mail_body_not_header": "citation_is_mail_metadata",
    "own_words_not_quote": "citation_is_quoted_text",
    "real_recipient": "mail_has_no_real_recipient",
    "not_a_loop": "mail_is_a_loop",
    "not_a_promise": "citation_is_a_promise",
    "not_a_denial": "citation_says_not_done",
    "not_the_task": "citation_is_the_task",
    "class_allowed": "weak_class:",
    "confidence_floor": "confidence_",
    "moot_needs_decision": "moot_without_a_decision",
    "pinned_open": "pinned_open",
}


@pytest.mark.parametrize("cid", sorted(DEDICATED))
def test_each_control_is_stopped_by_the_guard_it_was_planted_for(cid):
    want = EXPECT[DEDICATED[cid]]
    assert any(d.startswith(want) for d in ROWS[cid]["downgrades"]), ROWS[cid]


# ── one mutation per guard ───────────────────────────────────────────────────

def test_the_guard_list_is_exactly_the_guards_in_validate():
    """A guard added to `validate` without being listed would get no
    mutation test below, and would be the one nobody can vouch for."""
    src = inspect.getsource(dj.validate)
    used = set(re.findall(r'\bon\("([a-z_]+)"\)', src))
    assert used == set(dj.GUARDS)
    assert len(dj.GUARDS) == len(set(dj.GUARDS))
    assert set(EXPECT) == set(dj.GUARDS)
    assert set(DEDICATED.values()) == set(dj.GUARDS), \
        "every guard has a control planted for it by name"


@pytest.mark.parametrize("guard", dj.GUARDS)
def test_switching_one_guard_off_closes_a_planted_control(guard):
    flipped = require_a_dependent_control(guard)
    # The control planted for this guard is among them: the flip is this
    # guard's own, not a side effect of some other control.
    planted = {cid for cid, g in DEDICATED.items() if g == guard}
    assert planted & set(flipped), (guard, flipped)


def test_a_guard_no_control_depends_on_fails_the_suite():
    """MUTATION CONTROL for the test above: a name no control depends on
    must be reported, or that test could never fail."""
    assert closed_when_disabled("a_guard_with_no_control") == []
    with pytest.raises(AssertionError, match="no control depends"):
        require_a_dependent_control("a_guard_with_no_control")


def test_a_promise_spelled_as_a_contraction_is_still_a_promise():
    assert not dj.PROMISE_RE.search("I'll get the survey to you next week")
    assert dj.PROMISE_CONTRACTION_RE.search("I'll get the survey to you next week")
    assert dj.PROMISE_CONTRACTION_RE.search("we" + chr(0x2019) + "ll send it")
    assert not dj.PROMISE_CONTRACTION_RE.search("The hill was steep")


# ── the selftest's own verdict ───────────────────────────────────────────────

def test_selftest_passes_on_a_stub_that_says_done():
    lines = []
    assert dj.selftest(runner=dj.stub_runner(), out=lines.append) == 0
    assert lines[-1] == "selftest: PASS"
    assert "0 false done" in lines[-2]


def test_selftest_fails_a_judge_that_answers_open_to_everything():
    """The known-done controls are what make the selftest falsifiable in
    this direction: a judge that closes nothing is as broken as a reckless
    one, and zero false done is exactly what it would report."""
    lines = []
    assert dj.selftest(runner=dj.stub_runner("open"), out=lines.append) == 1
    assert lines[-1] == "selftest: FAIL"
    assert "0 false done" in lines[-2] and "0 closed" in lines[-2]


def test_selftest_fails_when_a_known_open_control_closes(monkeypatch):
    real = dj.validate
    monkeypatch.setattr(dj, "validate", lambda *a, **k: real(
        *a, **{**k, "disabled": frozenset({"not_a_promise"})}))
    lines = []
    assert dj.selftest(runner=dj.stub_runner(), out=lines.append) == 1
    assert any(ln.startswith("FALSE DONE: ctl-promise-01") for ln in lines)


def test_selftest_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    import config_loader
    monkeypatch.setattr(config_loader, "logs_dir", lambda: tmp_path / "logs")
    dj.selftest(runner=dj.stub_runner(), out=lambda _line: None)
    assert list(tmp_path.rglob("*")) == []


def test_the_controls_do_not_bend_to_a_lowered_floor(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "auto_close_confidence", lambda: 0.5)
    report = dj.judge_controls(runner=dj.stub_runner())
    assert report["false_done"] == []


def test_the_fixture_file_is_plain_json_with_no_long_dash():
    raw = dj.CONTROLS_PATH.read_text(encoding="utf-8")
    json.loads(raw)
    assert chr(0x2014) not in raw and chr(0x2013) not in raw
