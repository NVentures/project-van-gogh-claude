"""Work that stopped moving: is it found, and is it said in a usable way?

The grading half is easy and the wording half is where this earns its place.
A line that says "3 items waiting" is not information, because the reader
cannot tell whose move it is without going and looking, and going and looking
is the trip the line exists to save them. So the direction is asserted as
hard as the threshold is.
"""

from datetime import datetime, timedelta

import pytest

import task_stall

NOW = datetime(2026, 9, 9, 12, 0)


def ticket(state, days_ago, title="Reply to Sarah Whitfield"):
    stamp = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"title": title, "state": state, "created_at": stamp,
            "history": [{"at": stamp, "to": state, "note": ""}]}


@pytest.fixture
def store(monkeypatch):
    """Plant the ticket ledger, and return the dict to fill in."""
    data = {"tickets": {}, "pins": []}
    import workbench_store
    monkeypatch.setattr(workbench_store, "_read", lambda: data)
    monkeypatch.setattr(workbench_store, "tickets_path",
                        lambda: task_stall.Path("/x/tickets.json"))
    return data


# ── The threshold ────────────────────────────────────────────────────────────

def test_a_fresh_draft_is_not_stalled(store):
    store["tickets"]["a"] = ticket("staged", 1)
    assert task_stall.stalled_tickets(NOW) == []


def test_a_draft_nobody_read_for_a_week_is_stalled(store):
    store["tickets"]["a"] = ticket("staged", 9)
    rows = task_stall.stalled_tickets(NOW)
    assert len(rows) == 1
    assert rows[0]["days"] == 9


def test_the_boundary_sits_past_the_threshold_not_on_it(store):
    """A test written for a limit must sit past it, not on it: a case exactly
    on the boundary passes with the comparison deleted."""
    store["tickets"]["a"] = ticket("staged", task_stall.TICKET_STALL_DAYS - 1)
    assert task_stall.stalled_tickets(NOW) == []
    store["tickets"]["a"] = ticket("staged", task_stall.TICKET_STALL_DAYS + 1)
    assert len(task_stall.stalled_tickets(NOW)) == 1


def test_work_the_machine_still_owns_is_never_stalled(store):
    """A ticket mid-build is working, not stuck, however long it takes.

    `failed` is deliberately excluded too: that is a job-watch question, and
    reporting it here would say the same thing twice in one briefing, in two
    different voices."""
    for state in ("staging", "running", "verifying", "failed", "dismissed",
                  "rated", "approved"):
        store["tickets"] = {"a": ticket(state, 30)}
        assert task_stall.stalled_tickets(NOW) == [], state


def test_every_waiting_state_is_reachable(store):
    """Each state in WAITING_ON_USER must actually produce a row.

    A state listed but unreachable is a rule nobody tested; each also needs
    its own plain-words phrase, since the state name is a ledger key the
    reader has never seen."""
    for state in task_stall.WAITING_ON_USER:
        store["tickets"] = {"a": ticket(state, 20)}
        rows = task_stall.stalled_tickets(NOW)
        assert len(rows) == 1, state
        assert state not in rows[0]["what"], f"{state} leaked its ledger name"
        assert rows[0]["what"], state


# ── The wording ──────────────────────────────────────────────────────────────

def test_a_quiet_day_says_nothing(monkeypatch):
    """Most days. A surface that speaks every day teaches the reader to skip
    it, including on the day it matters."""
    monkeypatch.setattr(task_stall, "stalled", lambda now=None: [])
    assert task_stall.summary(NOW) == ""


def test_the_line_names_the_items_rather_than_counting_them(monkeypatch):
    rows = [{"kind": "ticket", "title": "Reply to Sarah Whitfield", "days": 9,
             "whose_move": "yours", "what": "has a draft ready that nobody "
             "has read for 9 days", "source": "/x"}]
    monkeypatch.setattr(task_stall, "stalled", lambda now=None: rows)
    line = task_stall.summary(NOW)
    assert "Reply to Sarah Whitfield" in line
    assert "1 item" not in line


def test_every_count_says_which_way_it_points(monkeypatch):
    """A bare count beside a number leaves the direction to the reader, and
    that shape has shipped three times in this product. The line must say
    whose move it is, in the same breath."""
    rows = [{"kind": "ticket", "title": f"Item {i}", "days": 20 - i,
             "whose_move": "yours", "what": "is waiting", "source": "/x"}
            for i in range(5)]
    monkeypatch.setattr(task_stall, "stalled", lambda now=None: rows)
    line = task_stall.summary(NOW)
    assert "waiting on you" in line
    assert "2 more" in line, "the unshown remainder must still be counted"


def test_the_worst_one_leads(monkeypatch):
    rows = [{"kind": "ticket", "title": "newer", "days": 8, "whose_move":
             "yours", "what": "x", "source": "/x"},
            {"kind": "commitment", "title": "oldest", "days": 40,
             "whose_move": "yours", "what": "y", "source": "/y"}]
    monkeypatch.setattr(task_stall, "stalled_tickets", lambda now=None: [rows[0]])
    monkeypatch.setattr(task_stall, "stalled_commitments",
                        lambda today=None, days=None: [rows[1]])
    assert task_stall.stalled(NOW)[0]["title"] == "oldest"


def test_no_dash_reaches_the_line(monkeypatch):
    rows = [{"kind": "ticket", "title": "Q3 board deck", "days": 9,
             "whose_move": "yours", "what": "was finished and never rated for "
             "9 days", "source": "/x"}]
    monkeypatch.setattr(task_stall, "stalled", lambda now=None: rows)
    line = task_stall.summary(NOW)
    assert "\u2014" not in line and "\u2013" not in line


# ── Failing open ─────────────────────────────────────────────────────────────

def test_a_broken_ledger_costs_the_line_not_the_briefing(monkeypatch):
    import workbench_store

    def boom():
        raise OSError("gone")

    monkeypatch.setattr(workbench_store, "_read", boom)
    assert task_stall.stalled_tickets(NOW) == []
    assert task_stall.stalled(NOW) == []


def test_the_footer_line_never_raises(monkeypatch):
    monkeypatch.setattr(task_stall, "stalled",
                        lambda now=None: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        task_stall.summary(NOW)
    # config_loader is the caller that must swallow it, and does.
    import config_loader
    assert config_loader._task_stall_line() == ""
