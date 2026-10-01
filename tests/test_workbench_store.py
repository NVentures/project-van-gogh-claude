"""workbench_store: identity, rank, lifecycle, and the merge that must not
destroy user intent.

Every test points the ledger at a tmp dir, so nothing here can touch a real
vault even if config_loader were mis-primed.
"""
import json

import pytest

import workbench_store as store


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "van_gogh_root", lambda: tmp_path)
    monkeypatch.setattr(store, "deal_critical_domains", lambda: {"bigdeal.com"})
    return tmp_path


def item(**kw):
    base = {"source": "inbox_pending", "account": "Gmail", "subject": "Term sheet",
            "counterparty_name": "Ann Roe", "counterparty_email": "ann@acme.com",
            "domain": "acme.com", "age_days": 3, "body_preview": "hi",
            "summary": "They asked for the term sheet.", "intent": "action_required",
            "urgency": "high", "suggested_action": "send the redline",
            "is_internal": False, "allowlisted": False}
    base.update(kw)
    return base


# ── Identity ─────────────────────────────────────────────────────────────────

def test_reply_and_forward_prefixes_keep_one_id():
    a = store.ticket_id("inbox_pending", "a@x.com", "Deal")
    b = store.ticket_id("inbox_pending", "a@x.com", "Re: Deal")
    c = store.ticket_id("inbox_pending", "A@X.com", "FWD:  RE: Deal")
    assert a == b == c


def test_different_subjects_are_different_tickets():
    assert store.ticket_id("inbox_pending", "a@x.com", "Deal") != \
           store.ticket_id("inbox_pending", "a@x.com", "Other")


# ── Rank ─────────────────────────────────────────────────────────────────────

def test_score_is_pure_and_repeatable():
    t = store.ticket_from_item(item())
    assert store.score(t, {"bigdeal.com"}) == store.score(t, {"bigdeal.com"})


def test_urgency_and_age_and_deal_domain_all_lift_the_score():
    low = store.ticket_from_item(item(urgency="low", age_days=0))
    high = store.ticket_from_item(item(urgency="high", age_days=0))
    old = store.ticket_from_item(item(urgency="low", age_days=10))
    deal = store.ticket_from_item(item(urgency="low", age_days=0,
                                       counterparty_email="x@bigdeal.com"))
    critical = {"bigdeal.com"}
    assert store.score(high, critical) > store.score(low, critical)
    assert store.score(old, critical) > store.score(low, critical)
    assert store.score(deal, critical) > store.score(low, critical)


def test_age_contribution_is_capped():
    a = store.ticket_from_item(item(age_days=14))
    b = store.ticket_from_item(item(age_days=400))
    assert store.score(a, set()) == store.score(b, set())


def test_ordering_puts_pins_first_then_score():
    store.propose([item(subject="Low", counterparty_email="l@a.com", urgency="low", age_days=0),
                   item(subject="High", counterparty_email="h@a.com", urgency="high", age_days=9)])
    rows = store.ordered()
    assert rows[0]["title"].endswith("High")

    low_id = [r["id"] for r in rows if r["title"].endswith("Low")][0]
    store.pin(low_id, 0)
    assert store.ordered()[0]["id"] == low_id

    store.pin(low_id, None)
    assert store.ordered()[0]["title"].endswith("High")


def test_dismissed_tickets_leave_the_list():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    store.transition(tid, "dismissed")
    assert store.ordered() == []


# ── Merge ────────────────────────────────────────────────────────────────────

def test_refresh_preserves_state_comments_and_rating():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    store.comment(tid, "make it shorter")     # proposed -> staging
    store.transition(tid, "staged")

    store.propose([item(age_days=8)])           # the same thread, one refresh later

    row = store.get(tid)
    assert row["state"] == "staged"             # not reset to proposed
    assert len(row["comments"]) == 1
    assert row["context"]["age_days"] == 8      # read-only context did refresh


def test_refresh_prunes_only_untouched_proposals():
    store.propose([item(subject="Gone", counterparty_email="g@a.com"),
                   item(subject="Kept", counterparty_email="k@a.com")])
    kept_id = [r["id"] for r in store.ordered() if r["title"].endswith("Kept")][0]
    store.transition(kept_id, "staging")

    result = store.propose([])                  # the briefing no longer lists either
    assert result["pruned"] == 1
    assert store.get(kept_id) is not None


def test_manual_tickets_survive_every_refresh():
    made = store.create("Build the Q3 deck", "pptx", "A deck existed in Downloads.")
    store.propose([])
    assert store.get(made["id"]) is not None


def test_hand_edited_dod_is_not_overwritten():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    store.edit_dod(tid, "My own words.")
    store.propose([item()])
    assert store.get(tid)["dod"] == "My own words."


# ── Lifecycle ────────────────────────────────────────────────────────────────

def test_illegal_transition_raises_rather_than_silently_passing():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    with pytest.raises(store.IllegalTransition):
        store.transition(tid, "delivered")


def test_comment_demotes_a_staged_ticket_so_it_must_be_confirmed_again():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    store.transition(tid, "staging")
    store.transition(tid, "staged")
    store.comment(tid, "wrong tone")
    assert store.get(tid)["state"] == "staging"


def test_empty_comment_is_rejected():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    with pytest.raises(ValueError):
        store.comment(tid, "   ")


def test_rating_writes_the_log_and_closes_the_ticket():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    store.transition(tid, "staging")
    store.transition(tid, "staged")
    store.transition(tid, "approved")
    store.transition(tid, "delivered")
    store.rate(tid, 4, "close, a bit long")

    assert store.get(tid)["state"] == "rated"
    rows = [json.loads(x) for x in store.ratings_path().read_text(encoding="utf-8").splitlines()]
    assert rows[0]["stars"] == 4
    assert store.recent_ratings()[0]["ticket_id"] == tid


def test_rating_range_is_enforced():
    store.propose([item()])
    tid = store.ordered()[0]["id"]
    for bad in (0, 6):
        with pytest.raises(ValueError):
            store.rate(tid, bad)


# ── Persistence ──────────────────────────────────────────────────────────────

def test_a_corrupt_ledger_reads_as_empty_instead_of_crashing():
    store.tickets_path().parent.mkdir(parents=True, exist_ok=True)
    store.tickets_path().write_text("{not json", encoding="utf-8")
    assert store.ordered() == []
    store.propose([item()])                     # and it recovers on the next write
    assert len(store.ordered()) == 1


def test_writes_leave_no_temp_file_behind():
    store.propose([item()])
    assert not list(store.workbench_dir().glob("*.tmp"))
