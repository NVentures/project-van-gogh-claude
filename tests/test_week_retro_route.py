"""Week Retro is a Workbench briefing like the other three.

It was a first-class briefing everywhere else (its own skill, script,
scheduler entry, digest name and phase label) while the Workbench table did
not list it, so `/briefing/week-retro` answered 404. These pin the route and
the one thing that makes it different: it writes a NEW dated file each run,
so its path is a lookup rather than a constant.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import briefing_html  # noqa: E402
import workbench_data  # noqa: E402


def test_the_workbench_serves_every_briefing_the_product_has():
    """briefing_html names four; the server must serve the same four."""
    assert set(workbench_data.BRIEFINGS) == set(briefing_html.BRIEFINGS)


def test_week_retro_is_routed():
    assert "week-retro" in workbench_data.BRIEFINGS
    title, path_fn, stem, _fresh = workbench_data.BRIEFINGS["week-retro"]
    assert title == "Week Retro"
    assert stem == "week_retro_latest"


def test_the_newest_dated_retro_wins(tmp_path, monkeypatch):
    for name in ("retro-2026-08-28.md", "retro-2026-09-04.md", "retro-2026-07-03.md"):
        (tmp_path / name).write_text("# Week Retro\n", encoding="utf-8")
    monkeypatch.setattr(workbench_data, "weekly_dir", lambda: tmp_path)
    assert workbench_data.week_retro_md_path().name == "retro-2026-09-04.md"


def test_dated_by_filename_not_mtime(tmp_path, monkeypatch):
    """An old retro edited today is still an old week."""
    old = tmp_path / "retro-2026-07-03.md"
    new = tmp_path / "retro-2026-09-04.md"
    new.write_text("# new\n", encoding="utf-8")
    old.write_text("# old, touched later\n", encoding="utf-8")
    import os
    os.utime(old, (2 ** 31 - 1, 2 ** 31 - 1))
    monkeypatch.setattr(workbench_data, "weekly_dir", lambda: tmp_path)
    assert workbench_data.week_retro_md_path().name == "retro-2026-09-04.md"


def test_no_retro_yet_reads_as_empty_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench_data, "weekly_dir", lambda: tmp_path)
    path = workbench_data.week_retro_md_path()
    assert not path.exists(), "a path that is not there, rather than a raise"
    info = workbench_data.briefing("week-retro")
    assert info["exists"] is False


def test_a_stray_name_is_not_mistaken_for_a_retro(tmp_path, monkeypatch):
    (tmp_path / "retro-notes.md").write_text("x", encoding="utf-8")
    (tmp_path / "retro-2026-09-04.md").write_text("y", encoding="utf-8")
    monkeypatch.setattr(workbench_data, "weekly_dir", lambda: tmp_path)
    assert workbench_data.week_retro_md_path().name == "retro-2026-09-04.md"
