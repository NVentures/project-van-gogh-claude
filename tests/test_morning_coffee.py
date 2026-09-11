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
    monkeypatch.setattr(mc, "MORNING_COFFEE_MD", str(tmp_path / "absent.md"))
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
