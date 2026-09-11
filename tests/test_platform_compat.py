"""Behavioral tests for app/platform_compat.py.

test_cross_platform.py proves call sites *use* these helpers; this proves the
helpers are *correct* — chiefly that fmt_hour_minute strips the leading hour
zero portably (the whole reason the helper exists instead of POSIX %-I).
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

# conftest.py puts app/ on sys.path, so import the module flat.
from platform_compat import claude_bin, fmt_hour_minute, fmt_local_time


@pytest.mark.parametrize(
    "h, m, expected",
    [
        (8, 5, "8:05 AM"),    # leading zero on the hour must be stripped
        (9, 0, "9:00 AM"),
        (10, 5, "10:05 AM"),  # two-digit hour: no stray strip of the "10"
        (12, 0, "12:00 PM"),  # noon stays 12, not stripped to ":00 PM"
        (0, 0, "12:00 AM"),   # midnight renders as 12 AM
        (13, 30, "1:30 PM"),
        (23, 59, "11:59 PM"),
    ],
)
def test_fmt_hour_minute(h, m, expected):
    assert fmt_hour_minute(datetime(2026, 5, 29, h, m)) == expected


def test_fmt_local_time_single_zone():
    """A single configured zone renders one clock + its abbreviation, no PT/ET."""
    dt = datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc)  # 16:00 UTC
    # 16:00 UTC in summer = 9 AM Pacific (PDT), 12 PM Eastern (EDT).
    assert fmt_local_time(dt, ZoneInfo("America/Los_Angeles")) == "9:00 AM PDT"
    assert fmt_local_time(dt, ZoneInfo("America/New_York")) == "12:00 PM EDT"
    # Any IANA zone works: the label comes from %Z, not a hardcoded "PT".
    assert fmt_local_time(dt, ZoneInfo("Europe/London")) == "5:00 PM BST"


def test_fmt_local_time_dual_zone():
    """When a secondary zone is given, both render side by side."""
    dt = datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc)
    assert (
        fmt_local_time(dt, ZoneInfo("America/Los_Angeles"), ZoneInfo("America/New_York"))
        == "9:00 AM PDT / 12:00 PM EDT"
    )
    # secondary=None is the single-zone default (no trailing " / ").
    assert fmt_local_time(dt, ZoneInfo("America/Los_Angeles"), None) == "9:00 AM PDT"


def test_claude_bin_returns_path_when_present(monkeypatch):
    monkeypatch.setattr(
        "platform_compat.shutil.which", lambda _: "/usr/local/bin/claude"
    )
    assert claude_bin() == "/usr/local/bin/claude"


def test_claude_bin_raises_when_absent(monkeypatch):
    monkeypatch.setattr("platform_compat.shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="claude"):
        claude_bin()
