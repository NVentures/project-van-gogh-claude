"""Priority buckets: membership is code's job, wording is the model's.

The bug these tests pin is a live one. An install kept surfacing an RFP the
user was not leading instead of the two deals he had said, repeatedly, that he
was closing. Every test here is about membership, ordering, or a demotion, and
none of them lets the classifier decide any of the three.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config_loader as cl                                      # noqa: E402
import week_review as wr                                        # noqa: E402

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
BAND = chr(0x2500)
DOT = chr(0x00B7)


def _entry(subject, **kw):
    e = {"subject": subject, "counterparty_email": "x@y.com", "counterparty_name": ""}
    e.update(kw)
    return e


# ── Bucket assignment ────────────────────────────────────────────────────────

def test_deterministic_keyword_wins_over_classifier(monkeypatch):
    # The classifier says BETA; the configured keyword says ACME. Config wins.
    monkeypatch.setattr(wr, "_classify_batch",
                        lambda items: {0: {"id": 0, "bucket": "BETA", "summary": "s"}})
    kept, _ = wr.classify_threads([_entry("The widget rollout")])
    assert kept[0]["bucket"] == "ACME"


def test_classifier_fills_only_an_unassigned_gap(monkeypatch):
    monkeypatch.setattr(wr, "_classify_batch",
                        lambda items: {0: {"id": 0, "bucket": "BETA", "summary": "s"}})
    kept, _ = wr.classify_threads([_entry("Nothing matches any keyword here")])
    assert kept[0]["bucket"] == "BETA"


def test_classifier_tag_not_in_config_is_discarded(monkeypatch):
    # A tag the model invented would render a heading nobody configured.
    monkeypatch.setattr(wr, "_classify_batch",
                        lambda items: {0: {"id": 0, "bucket": "INVENTED", "summary": "s"}})
    kept, _ = wr.classify_threads([_entry("Nothing matches any keyword here")])
    assert kept[0]["bucket"] == "unassigned"


def test_classification_failure_leaves_deterministic_bucket(monkeypatch):
    # Fail open: a dead classifier must not cost the bucket a keyword already knew.
    monkeypatch.setattr(wr, "_classify_batch", lambda items: {})
    kept, _ = wr.classify_threads([_entry("The widget rollout")])
    assert kept[0]["bucket"] == "ACME"
    assert kept[0]["priority_matched"] == "Close the widget deal"


def test_priorities_appear_in_the_classifier_prompt():
    prompt = wr._build_classifier_prompt([{"id": 0, "subject": "x"}])
    assert "Close the widget deal" in prompt
    assert "ACME" in prompt and "BETA" in prompt
    assert '"priority_matched"' in prompt


# ── The fourth demotion ──────────────────────────────────────────────────────

def _gate(entry):
    out = {"cold_urgent": [entry], "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    return out


def test_unplaceable_item_is_never_demoted():
    # An item that matched no bucket is one WE could not categorize. Demoting
    # it hides real work to cover a gap in a keyword list, which is the same
    # failure the other guards exist to prevent. It renders in its own "Not
    # sorted" section instead, so it is not competing with the priorities.
    out = _gate(_entry("Some RFP that matches no keyword", age_days=5))
    assert len(out["cold_urgent"]) == 1
    assert out["cold_monitor"] == []


def test_in_bucket_item_missing_every_priority_is_demoted():
    # Demoted only when SOMETHING in that bucket did match. One item that
    # matched proves the priorities are readable, so a second item that did
    # not is genuinely off-priority rather than a wording gap.
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Acme invoice question", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    assert [e["subject"] for e in out["cold_urgent"]] == ["Widget deal redline"]
    assert [e["subject"] for e in out["cold_monitor"]] == ["Acme invoice question"]


def test_bucket_where_nothing_matched_demotes_nothing():
    # THE GUARD. A client writes priorities as intentions ("grow the
    # pipeline") and the matcher sees no connection to anything in the inbox.
    # Demoting on that reading would hand back an empty urgent section that
    # looks like a quiet week. Hide nothing instead.
    out = {"cold_urgent": [_entry("Acme invoice question", age_days=5),
                           _entry("Acme shipping notice", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    assert len(out["cold_urgent"]) == 2
    assert out["cold_monitor"] == []


def test_item_with_a_deadline_is_never_demoted():
    # The cost of hiding a signature deadline is the reason this layer exists.
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Countersignature needed by Friday", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    subjects = [e["subject"] for e in out["cold_urgent"]]
    assert "Countersignature needed by Friday" in subjects


def test_deadline_guard_fires_independently_of_the_blind_guard():
    # A bucket where something DID match, so the blind guard is off, and a
    # dated item that matches nothing. Only the deadline guard can save it.
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Unrelated Acme thing due 2026-09-30", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    assert len(out["cold_urgent"]) == 2


def test_a_demotion_always_says_why():
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Acme invoice question", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    reason = out["cold_monitor"][0]["demoted_because"]
    # Names the business the way the user wrote it, not by its tag: a lowercase
    # tag mid-sentence reads as a string that leaked out of the code.
    assert "Close the widget deal" in reason
    assert "Acme Corp" in reason and "ACME" not in reason


def test_degraded_judgment_demotes_nothing():
    # A dead model call must never produce a confident, thinner page.
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Acme invoice question", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set(),
                           judgment={"degraded": True, "degraded_reasons": ["x"]})
    assert len(out["cold_urgent"]) == 2


def test_in_bucket_item_matching_a_priority_stays_red():
    out = _gate(_entry("Widget deal redline", age_days=5))
    assert len(out["cold_urgent"]) == 1


def test_allowlisted_item_survives_the_demotion():
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Acme invoice question", age_days=5,
                                  allowlisted=True)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    assert len(out["cold_urgent"]) == 2


def test_bucket_with_no_priorities_set_is_not_demoted_for_it():
    # BETA has no priorities in the fixture. Demoting its items would punish
    # the user for a setting they never made.
    out = _gate(_entry("Beta gadget timing", age_days=5))
    assert len(out["cold_urgent"]) == 1


def test_live_hotcache_counterparty_survives_the_demotion():
    out = {"cold_urgent": [_entry("Unmatched subject", counterparty_email="live@deal.com",
                                  age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails={"live@deal.com"})
    assert len(out["cold_urgent"]) == 1


def test_install_with_no_buckets_keeps_its_red_section(monkeypatch):
    monkeypatch.setattr(cl, "businesses", lambda: [])
    out = _gate(_entry("Anything at all", age_days=5))
    assert len(out["cold_urgent"]) == 1


# ── Grouping ─────────────────────────────────────────────────────────────────

def _output():
    return {
        "waiting_on_user": [_entry("Widget deal redline", days_late=3)],
        "inbox_pending": [_entry("Acme gadget line timing")],
        "cold_urgent": [],
        "cold_monitor": [_entry("Random vendor pitch", age_days=30)],
    }


def test_group_by_bucket_shape():
    buckets = wr.group_by_bucket(_output())
    assert [b["tag"] for b in buckets] == ["ACME", "BETA", "unassigned"]
    assert buckets[0]["display_name"] == "Acme Corp"
    assert [p["name"] for p in buckets[0]["priorities"]] == [
        "Close the widget deal", "Ship the gadget line"]


def test_every_item_lands_in_exactly_one_bucket():
    out = _output()
    buckets = wr.group_by_bucket(out)
    total_in = sum(len(out[s]) for s in
                   ("waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor"))
    subjects = [i["subject"] for b in buckets for i in b["items"]]
    assert len(subjects) == total_in == len(set(subjects))


def test_configured_bucket_with_no_items_is_still_emitted():
    # A venture with nothing open is information. Omitting it reads as "no
    # news" when it may mean "nothing is moving".
    buckets = wr.group_by_bucket({"waiting_on_user": [], "inbox_pending": [],
                                  "cold_urgent": [], "cold_monitor": []})
    assert [b["tag"] for b in buckets] == ["ACME", "BETA"]


def test_unassigned_bucket_is_omitted_when_empty():
    buckets = wr.group_by_bucket({"waiting_on_user": [_entry("Widget deal redline")],
                                  "inbox_pending": [], "cold_urgent": [],
                                  "cold_monitor": []})
    assert "unassigned" not in [b["tag"] for b in buckets]


# ── Rendering ────────────────────────────────────────────────────────────────

GOLDEN_TERMINAL = f"""{BAND * 46}
ACME CORP
{BAND * 46}

Close the widget deal
  Late:               {DOT} [ ] Widget deal redline {DOT} 3 days late

Ship the gadget line
  Waiting on you:     {DOT} [ ] Acme gadget line timing

{BAND * 46}
COULDN'T PLACE THESE {DOT} tell me where they belong and I will file them next time
{BAND * 46}

  Waiting on them:    {DOT} [ ] Random vendor pitch {DOT} 30 days ago
"""

GOLDEN_MD = f"""{BAND * 46}
ACME CORP
{BAND * 46}

Close the widget deal
**Late**
- [ ] Widget deal redline {DOT} 3 days late

Ship the gadget line
**Waiting on you**
- [ ] Acme gadget line timing

{BAND * 46}
COULDN'T PLACE THESE {DOT} tell me where they belong and I will file them next time
{BAND * 46}

**Waiting on them**
- [ ] Random vendor pitch {DOT} 30 days ago
"""


def test_golden_terminal_render():
    assert wr.render_buckets_md(wr.group_by_bucket(_output()),
                                "morning-coffee") == GOLDEN_TERMINAL


def test_golden_md_render():
    assert wr.render_buckets_md(wr.group_by_bucket(_output()),
                                "morning-coffee", mode="md") == GOLDEN_MD


def test_render_is_stable_across_runs():
    buckets = wr.group_by_bucket(_output())
    assert (wr.render_buckets_md(buckets, "week")
            == wr.render_buckets_md(wr.group_by_bucket(_output()), "week"))


def test_overdue_sorts_before_due_and_most_overdue_first():
    out = {"waiting_on_user": [
        _entry("Widget deal late a little", days_late=2),
        _entry("Widget deal late a lot", days_late=9),
    ], "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert rendered.index("late a lot") < rendered.index("late a little")


def test_long_title_wraps_and_is_never_truncated():
    title = "Widget deal " + " ".join(f"word{i}" for i in range(40))
    out = {"waiting_on_user": [_entry(title)], "inbox_pending": [],
           "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "word39" in rendered
    assert all(len(line) <= 90 for line in rendered.splitlines())


def test_afternoon_tea_uses_its_own_label_vocabulary():
    out = {"waiting_on_user": [_entry("Widget deal redline", done_today=True)],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "afternoon-tea")
    assert "Done today:" in rendered
    assert "Overdue:" not in rendered


def test_unknown_briefing_raises():
    import pytest
    with pytest.raises(ValueError):
        wr.render_buckets_md([], "nonsense")


def test_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        wr.render_buckets_md([], "week", mode="nonsense")


def test_no_em_or_en_dash_in_any_rendered_string():
    # The band (U+2500) and the middle dot (U+00B7) are legitimate and must
    # survive; a naive "no unusual characters" scan would flag them.
    for briefing in ("morning-coffee", "afternoon-tea", "week"):
        for mode in ("terminal", "md"):
            rendered = wr.render_buckets_md(wr.group_by_bucket(_output()), briefing, mode)
            assert EM_DASH not in rendered and EN_DASH not in rendered
            assert BAND in rendered and DOT in rendered


# ── Priority matching ────────────────────────────────────────────────────────
#
# Priority names are written as sentences ("Close QX Corp"), so the verb has to
# come off before anything is counted, and the distinctive part is often short.
# Both halves of that are easy to get wrong in opposite directions.

PRIOS = [
    {"name": "Close QX Corp"},
    {"name": "Close Northwind"},
    {"name": "Build the AB pipeline"},
]


def test_short_distinctive_token_still_matches():
    # "MT" is two characters and load-bearing. A length filter drops it and
    # the item silently falls into Other signal.
    assert wr.match_priority({"subject": "QX Corp credit memo"}, PRIOS) == "Close QX Corp"


def test_substring_collision_does_not_match():
    # "mt" appears inside "amount". Tokenizing both sides is what prevents it.
    assert wr.match_priority({"subject": "Invoice amount discrepancy"}, PRIOS) == ""


def test_the_verb_alone_never_matches():
    # Every deal email says "closing". If the verb counted, everything would
    # match the first priority in the list.
    assert wr.match_priority({"subject": "Closing thoughts on the quarter"}, PRIOS) == ""


def test_one_long_distinctive_token_is_enough():
    assert wr.match_priority({"subject": "Northwind redline v4"}, PRIOS) == "Close Northwind"


def test_priority_of_only_stopwords_never_matches():
    assert wr.match_priority({"subject": "anything"}, [{"name": "Close the"}]) == ""


def test_empty_priority_list_matches_nothing():
    assert wr.match_priority({"subject": "QX Corp"}, []) == ""


# ── The contradiction a cold reader caught ───────────────────────────────────
#
# A reader was handed three rendered briefings and no knowledge of the build.
# The first thing they found: the page said "nothing moving on this" under
# "Build the referral pipeline" while "Referral pipeline intro from the
# conference" sat nine lines below under a heading saying it matched nothing.
# Same words. Their verdict was that one provable contradiction poisons every
# other claim on the page, and they would go back to their inbox.

def test_an_item_quoting_a_priority_is_never_filed_as_unplaceable(monkeypatch):
    monkeypatch.setattr(cl, "businesses", lambda: [{
        "tag": "deals", "display_name": "Deals", "project_page": "p",
        "meeting_route": "m", "keywords": ["northwind"],   # deliberately narrow
        "priorities": [{"name": "Build the referral pipeline"}],
    }])
    entry = _entry("Referral pipeline intro from the conference", age_days=16)
    assert wr.assign_bucket(entry) == "deals"


def test_a_starving_priority_is_never_printed_above_its_own_item(monkeypatch):
    monkeypatch.setattr(cl, "businesses", lambda: [{
        "tag": "deals", "display_name": "Deals", "project_page": "p",
        "meeting_route": "m", "keywords": ["northwind"],
        "priorities": [{"name": "Build the referral pipeline"}],
    }])
    out = {"waiting_on_user": [],
           "inbox_pending": [],
           "cold_urgent": [_entry("Referral pipeline intro from the conference",
                                  age_days=16)],
           "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "nothing moving on this" not in rendered
    assert "Referral pipeline intro" in rendered


def test_a_fresh_install_shows_no_empty_scaffolding(monkeypatch):
    # Day one: no priorities anywhere, no keywords. The only real item was
    # being pushed below two empty headings, one of which announced that
    # nothing was moving.
    monkeypatch.setattr(cl, "businesses", lambda: [{
        "tag": "personal", "display_name": "Personal", "project_page": "p",
        "meeting_route": "m", "keywords": [], "priorities": []}])
    out = {"waiting_on_user": [_entry("Contract needs your signature by Friday",
                                      days_late=2)],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "morning-coffee")
    assert "nothing moving on this" not in rendered
    lines = [l for l in rendered.splitlines() if l.strip()]
    assert "signature" in lines[3].lower() or "signature" in rendered.split("\n\n")[1]
    assert "update-settings" in rendered      # the nudge, once, at the foot


def test_an_item_says_who_it_is_from_and_how_long_it_has_sat():
    out = {"waiting_on_user": [_entry("Widget deal redline",
                                      counterparty_name="Dana Ruiz",
                                      reply_age_days=6)],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "from Dana Ruiz" in rendered
    assert "6 days ago" in rendered


# ── What the second cold read caught ─────────────────────────────────────────

def test_day_one_briefing_does_not_open_by_announcing_failure(monkeypatch):
    # With nothing configured there was nothing to place against, so heading
    # the page "couldn't place these" reads as a broken tool, above the one
    # item that justifies the install.
    monkeypatch.setattr(cl, "businesses", lambda: [{
        "tag": "personal", "display_name": "Personal", "project_page": "p",
        "meeting_route": "m", "keywords": [], "priorities": []}])
    out = {"waiting_on_user": [_entry("Contract needs your signature by Friday",
                                      days_late=2)],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "morning-coffee")
    assert "COULDN'T PLACE" not in rendered.upper()
    assert "TODAY" in rendered
    assert "Nothing is set up yet" in rendered


def test_the_footer_never_names_a_section_the_reader_cannot_see(monkeypatch):
    # It named "Personal" on a page that had no Personal section, which is a
    # string leaking out of the code.
    monkeypatch.setattr(cl, "businesses", lambda: [
        {"tag": "deals", "display_name": "Deals", "project_page": "p",
         "meeting_route": "m", "keywords": ["widget"],
         "priorities": [{"name": "Close the widget deal"}]},
        {"tag": "personal", "display_name": "Personal", "project_page": "p",
         "meeting_route": "m", "keywords": ["school"], "priorities": []}])
    out = {"waiting_on_user": [_entry("Widget deal redline")],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "PERSONAL" not in rendered.upper()
    assert "Personal" not in rendered


def test_a_bucket_without_priorities_gets_no_empty_group_label(monkeypatch):
    # "Everything else here" only means something when there are priorities
    # for these to be other than.
    monkeypatch.setattr(cl, "businesses", lambda: [{
        "tag": "board", "display_name": "Board Seat", "project_page": "p",
        "meeting_route": "m", "keywords": ["board"], "priorities": []}])
    out = {"waiting_on_user": [_entry("Board packet for October")],
           "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "Everything else" not in rendered
    assert "Board packet" in rendered


def test_the_third_tier_says_what_it_actually_is():
    # It was "Worth a look", which a 40-day-old newsletter was landing under.
    # Then "Older, still open", which claimed to be about age and was not: the
    # label is assigned by section, and those sections mean the user sent last
    # and got no reply, so a ten day old item carried it beside a twenty one
    # day old item reading "Waiting on you". Once one label is a lie the reader
    # stops trusting the other two.
    out = {"waiting_on_user": [], "inbox_pending": [], "cold_urgent": [],
           "cold_monitor": [_entry("Widget deal old thread", age_days=30)]}
    rendered = wr.render_buckets_md(wr.group_by_bucket(out), "week")
    assert "Worth a look" not in rendered
    assert "Older, still open" not in rendered
    assert "Waiting on them" in rendered


def test_the_third_label_is_about_who_holds_it_not_age():
    """The younger item and the older one must not swap labels.

    This is the shape the label used to get wrong: the ten day old thread the
    user sent and heard nothing back on, beside the twenty one day old thread
    the counterparty is waiting on the user for.
    """
    young_no_reply = _entry("Widget deal ten day silence", age_days=10)
    old_owes_reply = _entry("Widget deal three week ask", age_days=21)
    old_owes_reply["reply_age_days"] = 21
    out = {"waiting_on_user": [old_owes_reply], "inbox_pending": [],
           "cold_urgent": [], "cold_monitor": [young_no_reply]}
    buckets = wr.group_by_bucket(out)
    labels = {e["subject"]: wr._label_for(e, "week")
              for b in buckets for e in b["items"]}
    assert labels["Widget deal ten day silence"] == "Waiting on them"
    assert labels["Widget deal three week ask"] == "Waiting on you"


def test_a_demotion_reason_says_it_is_about_a_move():
    """Its absence on the next item must not read as the opposite verdict.

    Worded as a flat judgment ("not one of your priorities"), the note looked
    like a per-item verdict, so the item beside it carrying no note read as
    "this one IS a priority". The note only exists for an item that was moved
    down, and it has to say that.
    """
    # Two items, one relevant: a bucket where nothing at all matched is
    # treated as blind and demotes nothing, so it would never produce a reason.
    out = {"cold_urgent": [_entry("Widget deal redline", age_days=5),
                           _entry("Acme invoice question", age_days=5)],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    reason = out["cold_monitor"][0]["demoted_because"]
    assert reason.startswith("dropped down")
    assert "Acme Corp" in reason and "Close the widget deal" in reason


def test_the_note_fires_for_every_item_that_fails_the_same_test():
    """Silence has to mean "this IS about a priority", not "nobody checked".

    The note only ever stamped an item demoted OUT of urgent, so an equally
    non-priority item that arrived in monitor on its own carried nothing, and a
    reader took the silence as the opposite verdict. Two of five cold reads
    found the pair sitting one line apart.
    """
    demoted = _entry("Acme invoice question", age_days=5)
    already_there = _entry("Acme parking policy", age_days=20)
    relevant = _entry("Widget deal redline", age_days=5)
    on_priority = _entry("Widget deal signature page", age_days=25)
    out = {"cold_urgent": [relevant, demoted], "filtered_pending": [],
           "cold_monitor": [already_there, on_priority]}
    wr.gate_cold_promotion(out, active_emails=set())

    by_subject = {e["subject"]: e for e in out["cold_monitor"]}
    assert by_subject["Acme invoice question"].get("demoted_because")
    assert by_subject["Acme parking policy"].get("demoted_because"), \
        "an item already in monitor carried no note, so its silence said nothing"
    # And an item that IS about a stated priority stays silent, which is what
    # makes the silence readable.
    assert not by_subject["Widget deal signature page"].get("demoted_because")
    for entry in out["cold_monitor"]:
        why = entry.get("demoted_because") or ""
        if why:
            assert "Acme Corp" in why and "widget deal" in why.lower()


# ── The judgment reaches every open item, priorities or not ──────────────────

def _verdicts(rows):
    import json
    from subprocess import CompletedProcess
    seen = []
    def run(prompt, **k):
        seen.append(prompt)
        return CompletedProcess([], 0, stdout=json.dumps(rows), stderr="")
    return run, seen


def _no_priorities(monkeypatch, enabled=True):
    monkeypatch.setattr(cl, "businesses", lambda: [
        {"tag": "acme", "display_name": "Acme", "keywords": ["acme"]}])
    monkeypatch.setattr(cl, "business_priorities", lambda tag: [])
    monkeypatch.setattr(cl, "judgment_enabled", lambda: enabled)
    monkeypatch.setattr(cl, "judgment_preamble", lambda: "")


def test_cost_is_written_onto_items_in_a_bucket_with_no_priorities(monkeypatch):
    _no_priorities(monkeypatch)
    entries = [_entry("Acme admin rights", body_preview="before the handoff"),
               _entry("Acme login code")]
    run, seen = _verdicts([
        {"id": 0, "slip_cost": "high", "slip_reason": "handoff breaks",
         "blocks_event": "Laptop handoff", "decaying": True},
        {"id": 1, "slip_cost": "low"}])
    summary = wr.apply_priority_judgment(
        entries, judge=run, calendar=[{"title": "Laptop handoff", "day": "Tue Oct 13"}],
        today="2026-10-12", sections={id(entries[0]): "waiting_on_user"})
    assert summary["costed"] == 2 and summary["judged"] == 0
    assert summary["buckets"] == [] and summary["degraded"] is False
    assert entries[0]["slip_cost"] == "high"
    assert entries[0]["slip_reason"] == "handoff breaks"
    assert entries[0]["blocks_event"] == "Laptop handoff"
    assert entries[0]["blocks_when"] == "Tue Oct 13"
    assert entries[0]["decaying"] is True
    assert entries[1]["slip_cost"] == "low"
    # Relevance was never asked, so nothing may read as "judged irrelevant".
    assert "judged_relevant" not in entries[0] and "judged_relevant" not in entries[1]
    assert '"last_word": "theirs"' in seen[0] and "Today is Monday" in seen[0]


def test_the_unsorted_pile_is_read_for_cost_too(monkeypatch):
    _no_priorities(monkeypatch)
    entries = [_entry("Your login code")]
    run, seen = _verdicts([{"id": 0, "slip_cost": "low"}])
    wr.apply_priority_judgment(entries, judge=run)
    assert entries[0]["bucket"] == wr._BUCKET_UNASSIGNED
    assert entries[0]["slip_cost"] == "low"
    assert "several businesses" in seen[0]


def test_deadline_language_outranks_a_low_cost_verdict(monkeypatch):
    _no_priorities(monkeypatch)
    entries = [_entry("Acme countersignature needed by Friday"),
               _entry("Acme newsletter")]
    run, _ = _verdicts([{"id": 0, "slip_cost": "low"}, {"id": 1, "slip_cost": "low"}])
    summary = wr.apply_priority_judgment(entries, judge=run)
    assert "slip_cost" not in entries[0]
    assert entries[1]["slip_cost"] == "low"
    assert summary["costed"] == 1


def test_an_event_on_a_low_cost_item_is_not_carried(monkeypatch):
    """An invitation is about its meeting; it is not needed before it."""
    _no_priorities(monkeypatch)
    entries = [_entry("Acme invitation")]
    run, _ = _verdicts([{"id": 0, "slip_cost": "low", "blocks_event": "Board prep"}])
    wr.apply_priority_judgment(entries, judge=run,
                               calendar=[{"title": "Board prep", "day": "Thu Oct 15"}])
    assert "blocks_event" not in entries[0]


def test_judgment_switched_off_reads_nothing(monkeypatch):
    _no_priorities(monkeypatch, enabled=False)
    entries = [_entry("Acme anything")]
    run, seen = _verdicts([{"id": 0, "slip_cost": "high"}])
    summary = wr.apply_priority_judgment(entries, judge=run)
    assert seen == [] and summary["costed"] == 0 and "slip_cost" not in entries[0]


def test_a_failed_judgment_writes_nothing_and_says_so(monkeypatch):
    _no_priorities(monkeypatch)
    from subprocess import CompletedProcess
    entries = [_entry("Acme anything")]
    run = lambda *a, **k: CompletedProcess([], 1, stdout="", stderr="boom")  # noqa: E731
    summary = wr.apply_priority_judgment(entries, judge=run)
    assert summary["degraded"] is True and summary["degraded_reasons"]
    assert "slip_cost" not in entries[0]


def test_upcoming_events_drops_what_already_happened():
    calendar = [{"title": "Past", "_sort": "2026-10-09T15:00:00Z"},
                {"title": "Today", "_sort": "2026-10-12T00:00:00.0000000"},
                {"title": "All day later", "_sort": "2026-10-14"},
                {"title": "No date"}]
    assert [e["title"] for e in wr.upcoming_events(calendar, "2026-10-12")] == \
        ["Today", "All day later"]
    assert wr.upcoming_events(None, "2026-10-12") == []


# ── A date the message states becomes the item's own ─────────────────────────

def _dated(monkeypatch, row, **entry_kw):
    _no_priorities(monkeypatch)
    entries = [_entry("Acme renewal", reply_age_days=6,
                      body_preview="Your plan renews on 10/25/26.", **entry_kw)]
    run, _ = _verdicts([{"id": 0, "slip_cost": "high",
                         "due_quote": "renews on 10/25/26", **row}])
    summary = wr.apply_priority_judgment(entries, judge=run, today="2026-10-12")
    return entries[0], summary


def test_a_coming_date_becomes_due(monkeypatch):
    entry, summary = _dated(monkeypatch, {"due": "2026-10-25"})
    assert entry["due"] == "2026-10-25" and "days_late" not in entry
    assert entry["due_quote"] == "renews on 10/25/26"
    assert summary["dated"] == 1


def test_a_date_that_is_today_is_due_not_late(monkeypatch):
    entry, _ = _dated(monkeypatch, {"due": "2026-10-12"})
    assert entry["due"] == "2026-10-12" and "days_late" not in entry


def test_a_passed_date_becomes_days_late(monkeypatch):
    entry, summary = _dated(monkeypatch, {"due": "2026-10-09"})
    assert entry["days_late"] == 3 and "due" not in entry
    assert summary["dated"] == 1


@pytest.mark.parametrize("already", [{"due": "2026-11-01"}, {"days_late": 2}])
def test_a_read_date_never_replaces_one_a_producer_set(monkeypatch, already):
    entry, summary = _dated(monkeypatch, {"due": "2026-10-25"}, **already)
    assert {k: entry[k] for k in already} == already
    assert "due_quote" not in entry and summary["dated"] == 0


def test_a_read_date_puts_the_item_in_the_dated_tier(monkeypatch):
    """Through the real ranking: the dated item goes above an older, costly,
    undated one, and a passed date goes above both."""
    _no_priorities(monkeypatch)
    entries = [_entry("Acme old and costly", reply_age_days=12, counterparty_email="a@y.com"),
               _entry("Acme renewal", reply_age_days=6, counterparty_email="b@y.com",
                      body_preview="Your plan renews on 10/19/26."),
               _entry("Acme signature", reply_age_days=9, counterparty_email="c@y.com",
                      body_preview="Need this signed by Friday please.")]
    run, _ = _verdicts([
        {"id": 0, "slip_cost": "high", "slip_reason": "deal stalls"},
        {"id": 1, "slip_cost": "medium", "due": "2026-10-19",
         "due_quote": "renews on 10/19/26"},
        {"id": 2, "slip_cost": "medium", "due": "2026-10-09",
         "due_quote": "signed by Friday"}])
    wr.apply_priority_judgment(entries, judge=run, today="2026-10-12")
    page = wr.front_page(wr.group_by_bucket(
        {"waiting_on_user": entries, "inbox_pending": [], "cold_urgent": [],
         "cold_monitor": []}))
    assert [(i["subject"], i["why"]) for i in page] == [
        ("Acme signature", "3 days late"),
        ("Acme renewal", "due 2026-10-19"),
        ("Acme old and costly", "deal stalls")]


def test_a_read_date_further_than_a_week_out_ranks_by_cost(monkeypatch):
    """A renewal 13 days away must not sit above something breaking today.
    The date is still on its line."""
    _no_priorities(monkeypatch)
    entries = [_entry("Acme renewal", reply_age_days=6, counterparty_email="b@y.com",
                      body_preview="Your plan renews on 10/25/26."),
               _entry("Acme breaking today", reply_age_days=0, counterparty_email="a@y.com"),
               _entry("Acme payroll", reply_age_days=0, counterparty_email="c@y.com",
                      body_preview="Payroll is due on 10/19/26.")]
    run, _ = _verdicts([
        {"id": 0, "slip_cost": "medium", "due": "2026-10-25",
         "due_quote": "renews on 10/25/26"},
        {"id": 1, "slip_cost": "high", "slip_reason": "data exposed"},
        {"id": 2, "slip_cost": "medium", "due": "2026-10-19",
         "due_quote": "due on 10/19/26"}])
    wr.apply_priority_judgment(entries, judge=run, today="2026-10-12")
    assert entries[0].get("due_far") is True and "due_far" not in entries[2]
    page = wr.front_page(wr.group_by_bucket(
        {"waiting_on_user": entries, "inbox_pending": [], "cold_urgent": [],
         "cold_monitor": []}))
    assert [(i["subject"], i["why"]) for i in page] == [
        ("Acme payroll", "due 2026-10-19"),
        ("Acme breaking today", "data exposed"),
        ("Acme renewal", "due 2026-10-25")]


def test_a_date_a_producer_set_keeps_its_tier_however_far_out():
    """Only a date the judge read is held to the week. A follow-up's own due
    date ranks as it always did."""
    far = _entry("Follow up", reply_age_days=1, due="2026-12-01", section="waiting_on_user")
    costly = _entry("Costly", reply_age_days=1, slip_cost="high",
                    counterparty_email="z@y.com", section="waiting_on_user")
    page = wr.front_page([{"tag": "acme", "display_name": "Acme", "priorities": [],
                           "items": [costly, far]}])
    assert [i["subject"] for i in page] == ["Follow up", "Costly"]
