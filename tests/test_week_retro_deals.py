"""week-retro deal coverage (offline).

Verifies the --no-classify deal shape is consumed correctly (entries carry no
summary), deals_this_week populates from a synthetic week_review JSON, and the
hotcache deal-state read works via the shared collectors.hotcache_read.
"""
import os
import tempfile
from pathlib import Path

import week_retro as wr

# Synthetic week_review JSON in the --no-classify shape (no entry has a summary).
WK = {
    "waiting_on_user": [
        {"counterparty_name": "Owen Trask", "subject": "Ridgeline DC",
         "reply_age_days": 3, "met_since": "2026-06-08"},
        {"counterparty_name": "Spencer Hale", "subject": "Meridian punch list",
         "reply_age_days": 1},
    ],
    "inbox_pending": [
        {"counterparty_name": "Karen Lindqvist", "subject": "Meridian closing",
         "reply_age_days": 0},
    ],
    "cold_urgent": [
        {"counterparty_name": "Adam Voss", "subject": "Solstice JDA", "age_days": 20},
    ],
    "cold_monitor": [],
    "status_change_alerts": [
        {"thread": "Northwind Term Sheet", "signal": "releasing from exclusivity",
         "from": "Alex Reed"},
    ],
}


def test_no_classify_entries_carry_no_summary():
    assert all(
        "summary" not in e
        for sec in ("waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor")
        for e in WK[sec]
    )


def test_summarize_week_deals():
    d = wr.summarize_week_deals(WK)
    assert len(d["died_or_at_risk"]) == 1
    assert len(d["likely_handled_met"]) == 1
    assert d["likely_handled_met"][0]["name"] == "Owen Trask"
    assert d["waiting_on_user_count"] == 2
    assert d["inbox_pending_count"] == 1
    assert d["cold_count"] == 1  # urgent + monitor


def test_summarize_empty_wk_no_crash():
    d0 = wr.summarize_week_deals({})
    assert d0["waiting_on_user_count"] == 0 and d0["died_or_at_risk"] == []


def test_hotcache_deal_state(monkeypatch):
    hotcache_md = (
        "---\nupdated: 2026-06-08\n---\n\n## Active Threads\n\n"
        "### Meridian Dev Loan\n<!-- deal: stage=active last_contact=2026-06-07 -->\nnotes\n\n"
        "### Fairview LOI\n<!-- deal: stage=dead last_contact=2026-06-01 -->\nnotes\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(hotcache_md)
        tmp = f.name
    try:
        monkeypatch.setattr(wr, "HOTCACHE_PATH", Path(tmp))
        state, errs = wr.fetch_hotcache_deal_state()
    finally:
        os.unlink(tmp)

    assert len(state) == 2 and not errs
    assert any(
        s["deal"] == "Meridian Dev Loan" and s["stage"] == "active"
        and s["last_contact"] == "2026-06-07"
        for s in state
    )
