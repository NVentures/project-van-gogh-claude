"""The pptx lifecycle: proposed -> staging -> staged -> approved -> delivered.

These exercise the workers rather than the HTTP layer, because the workers hold
the rules that matter: a build only ever follows a human's nod, and a failure
lands somewhere the user can see it instead of wedging the ticket in `running`.
"""
import json

import pytest

import deliverable
import workbench_serve as serve
import workbench_store as store


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "van_gogh_root", lambda: tmp_path)
    monkeypatch.setattr(store, "deal_critical_domains", lambda: set())
    monkeypatch.setattr(deliverable, "van_gogh_root", lambda: tmp_path)
    return tmp_path


def plan(n=2):
    return {"title": "Cascade 2.0", "subtitle": "Thesis",
            "slides": [{"heading": f"S{i}", "bullets": [f"Point {i}."]} for i in range(n)]}


def staged_ticket(n=2):
    ticket = store.create("Board deck", "pptx", "Built the board deck.")
    store.transition(ticket["id"], "staging", "planning")
    store.transition(ticket["id"], "staged", "planned", staged=deliverable.validate_plan(plan(n)))
    return store.get(ticket["id"])


# ── Staging ───────────────────────────────────────────────────────────────────

def test_staging_plans_and_stops(monkeypatch):
    """The whole point of the staged state: a plan exists, no file does."""
    monkeypatch.setattr(deliverable, "stage_plan", lambda *a, **k: deliverable.validate_plan(plan(3)))
    ticket = store.create("Board deck", "pptx", "Built the board deck.")
    store.transition(ticket["id"], "staging", "planning")

    serve._stage_worker(serve._job_new("stage", "x"), ticket["id"])

    after = store.get(ticket["id"])
    assert after["state"] == "staged"
    assert len(after["staged"]["slides"]) == 3
    assert not list(deliverable.deliverables_dir().glob("*.pptx")), \
        "staging must never write a file: the nod comes first"


def test_a_model_failure_lands_on_failed_with_the_reason(monkeypatch):
    def boom(*a, **k):
        raise deliverable.PlanInvalid("no JSON object in the model's reply")
    monkeypatch.setattr(deliverable, "stage_plan", boom)
    ticket = store.create("Board deck", "pptx", "d")
    store.transition(ticket["id"], "staging", "planning")

    serve._stage_worker(serve._job_new("stage", "x"), ticket["id"])

    after = store.get(ticket["id"])
    assert after["state"] == "failed"
    assert "no JSON object" in after["history"][-1]["note"]


# ── Building ──────────────────────────────────────────────────────────────────

def test_an_approved_ticket_builds_and_verifies():
    ticket = staged_ticket(2)
    store.transition(ticket["id"], "approved", "approved by hand")

    serve._build_worker(serve._job_new("build", "x"), ticket["id"])

    after = store.get(ticket["id"])
    assert after["state"] == "delivered"
    path = after["delivery"]["path"]
    assert path.endswith(".pptx")
    from pathlib import Path
    assert Path(path).exists()
    assert "3 slides" in after["delivery"]["detail"]


def test_the_lifecycle_passes_through_running_and_verifying():
    ticket = staged_ticket()
    store.transition(ticket["id"], "approved", "approved by hand")
    serve._build_worker(serve._job_new("build", "x"), ticket["id"])
    states = [h["to"] for h in store.get(ticket["id"])["history"]]
    assert states[-3:] == ["running", "verifying", "delivered"]


def test_a_ticket_with_nothing_staged_fails_rather_than_building_blank():
    ticket = store.create("Board deck", "pptx", "d")
    store.transition(ticket["id"], "approved", "approved by hand")

    serve._build_worker(serve._job_new("build", "x"), ticket["id"])

    after = store.get(ticket["id"])
    assert after["state"] == "failed"
    assert "nothing was staged" in after["history"][-1]["note"]


def test_a_build_failure_never_wedges_the_ticket_in_running(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(deliverable, "build", boom)
    ticket = staged_ticket()
    store.transition(ticket["id"], "approved", "approved by hand")

    serve._build_worker(serve._job_new("build", "x"), ticket["id"])

    after = store.get(ticket["id"])
    assert after["state"] == "failed", "a dead runner must land on failed"
    assert "disk full" in after["history"][-1]["note"]


def test_a_deck_that_fails_verification_is_not_delivered(monkeypatch):
    monkeypatch.setattr(deliverable, "verify",
                        lambda *a, **k: (_ for _ in ()).throw(
                            deliverable.PlanInvalid("the deck holds no text")))
    ticket = staged_ticket()
    store.transition(ticket["id"], "approved", "approved by hand")

    serve._build_worker(serve._job_new("build", "x"), ticket["id"])

    assert store.get(ticket["id"])["state"] == "failed"


# ── The nod ───────────────────────────────────────────────────────────────────

def test_a_comment_sends_a_staged_plan_back_for_re_staging():
    # The user saying it is wrong must invalidate the plan, not sit beside it.
    ticket = staged_ticket()
    store.comment(ticket["id"], "too long, cut it to five")
    assert store.get(ticket["id"])["state"] == "staging"


def test_a_missing_ticket_is_named_rather_than_crashing():
    serve._build_worker(serve._job_new("build", "x"), "no-such-ticket")
    # store.get returns None for a missing id; the worker must not AttributeError.
    assert any(j["state"] == "failed" for j in serve.jobs_list())


# ── The seam: the graph reaches the deck ──────────────────────────────────────

def test_the_vaults_record_reaches_the_plan(monkeypatch):
    """Part A feeding Part B: a deck argues from what the vault knows."""
    import context_pack
    monkeypatch.setattr(context_pack, "pack_for", lambda n, e="", **k: {
        "open_commitments": [{"item": "Send the revised model", "source": "x.md"}],
        "deal_context": [], "meetings": [], "entity_page": "e.md",
        "graph": {"node": None, "neighbors": []}})

    seen = {}

    def capture(ticket, ratings=None, **kw):
        seen["pack"] = ticket.get("context_pack")
        return deliverable.validate_plan(plan())
    monkeypatch.setattr(deliverable, "stage_plan", capture)

    ticket = store.create("Board deck", "pptx", "d")
    store.transition(ticket["id"], "staging", "planning",
                     counterparty_name="Ann Roe")
    serve._stage_worker(serve._job_new("stage", "x"), ticket["id"])

    assert seen["pack"], "the stager must see the vault's record"
    assert seen["pack"]["open_commitments"][0]["item"] == "Send the revised model"
    # Read back through the LEDGER, not the in-memory ticket: assigning to the
    # dict `store.get` returned persists nothing, and a demo run is what caught
    # that. The staged ticket the user opens must carry its own evidence.
    stored = store.get(ticket["id"])["context_pack"]
    assert stored["open_commitments"][0]["item"] == "Send the revised model"


def test_an_empty_pack_is_not_attached(monkeypatch):
    # An empty pack must not reach the model dressed as evidence.
    import context_pack
    monkeypatch.setattr(context_pack, "pack_for", lambda n, e="", **k: {
        "open_commitments": [], "deal_context": [], "meetings": [],
        "entity_page": None, "graph": {"node": None, "neighbors": []}})

    seen = {}

    def capture(ticket, ratings=None, **kw):
        seen["pack"] = ticket.get("context_pack")
        return deliverable.validate_plan(plan())
    monkeypatch.setattr(deliverable, "stage_plan", capture)

    ticket = store.create("Board deck", "pptx", "d")
    store.transition(ticket["id"], "staging", "planning", counterparty_name="Nobody")
    serve._stage_worker(serve._job_new("stage", "x"), ticket["id"])
    assert not seen["pack"]


def test_a_broken_pack_does_not_kill_the_deck(monkeypatch):
    # Losing the evidence costs the deck its grounding, never the whole run.
    import context_pack
    monkeypatch.setattr(context_pack, "pack_for",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vault gone")))
    monkeypatch.setattr(deliverable, "stage_plan",
                        lambda *a, **k: deliverable.validate_plan(plan()))

    ticket = store.create("Board deck", "pptx", "d")
    store.transition(ticket["id"], "staging", "planning", counterparty_name="Ann Roe")
    serve._stage_worker(serve._job_new("stage", "x"), ticket["id"])
    assert store.get(ticket["id"])["state"] == "staged"
