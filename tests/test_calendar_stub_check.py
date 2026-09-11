"""Regression tests for calendar_stub_check.py's main() wiring.

The Tier 1 branch of main() shipped with `fetch_all(day_pt)`: a NameError,
since the variable is `day_local` — and no test exercised that branch, so it
crashed on every Tier 1 run. This pins the fixed wiring: main() without
--input must reach fetch_all with the computed target day.

conftest.py primes config_loader and puts app/ on sys.path.
"""
import calendar_stub_check as csc


def test_main_tier1_passes_day_local_to_fetch_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(csc, "is_tier1", lambda: True)
    monkeypatch.setattr(csc, "fetch_all",
                        lambda day: (seen.setdefault("day", day), ([], []))[1])
    monkeypatch.setattr(csc, "load_recorded", lambda: [])
    monkeypatch.setattr("sys.argv",
                        ["calendar_stub_check.py", "--day", "2026-08-11", "--dry-run"])
    csc.main()  # must not raise NameError
    assert seen["day"].strftime("%Y-%m-%d") == "2026-08-11"
