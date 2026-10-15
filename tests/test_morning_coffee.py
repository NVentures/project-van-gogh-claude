"""morning_coffee.py pure-logic tests.

Importing morning_coffee runs module-level config accessors — conftest's primed
FIXTURE makes that deterministic with no config.json.
"""
import os

import notetaker
import morning_coffee as mc


def test_build_email_sets_keys_by_lowercased_email():
    fresh = {
        "waiting_on_user": [{"counterparty_email": "Alice@X.com", "reply_age_days": 2}],
        "cold_urgent": [{"counterparty_email": "BOB@y.com", "age_days": 20}],
        "cold_monitor": [{"counterparty_email": "carol@z.com", "age_days": 9}],
    }
    waiting, cold, recent = mc.build_email_sets(fresh)
    assert "alice@x.com" in waiting
    assert "bob@y.com" in cold and "carol@z.com" in cold
    assert recent == {}


def test_cold_lookup_tolerates_carried_entry_without_age_days():
    # Carried-forward cold entries (from load_prior_week_state) have no age_days.
    # The morning_coffee classifier must read age_days with .get(), not [] —
    # otherwise it KeyErrors on every run that carries a prior-week cold thread.
    fresh = {
        "waiting_on_user": [],
        "cold_urgent": [{"counterparty_email": "dan@law.com", "carried": True}],  # no age_days
        "cold_monitor": [],
    }
    _, cold, _ = mc.build_email_sets(fresh)
    entry = cold.get("dan@law.com")
    assert entry is not None
    # This is the exact access pattern at the call site — must not raise.
    assert entry.get("age_days") is None


def test_last_briefing_date_falls_back_to_yesterday(tmp_path, monkeypatch):
    # No prior briefing file → window starts yesterday (last ~24h of calls).
    # Pinned to an established vault: a FIRST run widens to 14 days instead,
    # which is its own test below.
    monkeypatch.setattr(mc, "MORNING_COFFEE_MD", str(tmp_path / "absent.md"))
    monkeypatch.setattr(mc, "first_run", lambda: False)
    assert mc.last_briefing_date("2026-06-04") == "2026-06-03"


def test_last_briefing_date_uses_prior_render_mtime(tmp_path, monkeypatch):
    # A prior render in the past → window opens at that render's date ("since
    # last briefing"), so a multi-day gap still captures every intervening call.
    from datetime import datetime, timezone

    f = tmp_path / "morning-coffee.md"
    f.write_text("briefing", encoding="utf-8")
    epoch = 1_749_000_000  # a fixed past instant; exact date derived below
    os.utime(f, (epoch, epoch))
    monkeypatch.setattr(mc, "MORNING_COFFEE_MD", str(f))
    expected = (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .astimezone(mc.USER_TZ)
        .strftime("%Y-%m-%d")
    )
    got = mc.last_briefing_date("2026-06-04")
    assert got == expected      # the render's mtime date, not today
    assert got <= "2026-06-04"  # never past today


def test_fetch_meetings_no_key_returns_empty(monkeypatch):
    # Without a Granola key the shared fetch degrades to [] (no network call),
    # so both morning_coffee and afternoon_tea stay green in CI.
    monkeypatch.setattr(notetaker, "configured", lambda: False)
    assert notetaker.fetch_meetings("2026-06-04", since_str="2026-06-01") == []


# ── First run ────────────────────────────────────────────────────────────────
# A first briefing has no prior page to diff against. Sections that describe a
# CHANGE must stay quiet rather than report the absence of history as news.

def test_first_run_line_states_what_was_read():
    line = mc.build_first_run_line(since=14, accounts=2, meetings=3)
    assert "14 days" in line
    assert "2 accounts" in line
    assert "3 calls" in line
    # Past tense, per DESIGN.md.
    assert line.startswith("First morning. Read ")
    # It must say WHY nothing is marked as moved, or the reader reads the
    # silence as "nothing happened" rather than "there is no baseline yet".
    assert "no earlier page" in line


def test_first_run_line_pluralizes_off_its_own_counts():
    one = mc.build_first_run_line(since=14, accounts=1, meetings=1)
    assert "1 account." not in one   # never the bare wrong plural
    assert "1 account and 1 call" in one
    none = mc.build_first_run_line(since=14, accounts=2, meetings=0)
    # No calls at all: the clause is dropped, never rendered as "0 calls".
    assert "call" not in none
    assert "0" not in none


def test_first_run_line_carries_no_dashes():
    line = mc.build_first_run_line(since=14, accounts=2, meetings=2)
    assert "\u2014" not in line and "\u2013" not in line


def test_last_briefing_date_widens_to_fourteen_days_on_a_first_run(monkeypatch):
    """One day would show a single call and imply that was all there was, on a
    machine whose notetaker holds weeks of history nobody has seen."""
    monkeypatch.setattr(mc, "MORNING_COFFEE_MD", "/nonexistent/morning-coffee.md")
    monkeypatch.setattr(mc, "first_run", lambda: True)
    assert mc.last_briefing_date("2026-09-16") == "2026-09-02"
    # An established vault whose file was deleted keeps the one-day fallback.
    monkeypatch.setattr(mc, "first_run", lambda: False)
    assert mc.last_briefing_date("2026-09-16") == "2026-09-15"
