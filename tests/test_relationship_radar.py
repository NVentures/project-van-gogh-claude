"""relationship_radar.py entity-parsing tests.

parse_entity reads YAML-ish frontmatter (title/tags/sources/updated), computes
days_elapsed from the most recent source date, and returns None when:
  - title is in SKIP_NAMES (internal team / self / radar_skip_entity_names), or
  - tags intersect SKIP_TAGS, or
  - the contact is fresher than YELLOW_DAYS (30).
Dates are computed relative to today so the test is calendar-stable.
"""
from datetime import date, timedelta

import relationship_radar as rr


def _entity(tmp_path, name, title, tags, source_dates, updated=None):
    """Write a tmp entity .md and return its Path."""
    src_lines = ", ".join(f'"{d} note"' for d in source_dates)
    fm = [
        "---",
        f"title: {title}",
        f"tags: [{', '.join(tags)}]",
        f"sources: [{src_lines}]",
    ]
    if updated:
        fm.append(f"updated: {updated}")
    fm.append("---")
    body = (
        "\n\nMet with this contact about a potential partnership and follow up. "
        "They asked for a revised proposal by end of month.\n"
    )
    p = tmp_path / f"{name}.md"
    p.write_text("\n".join(fm) + body, encoding="utf-8")
    return p


def test_parse_entity_cold_contact(tmp_path):
    # 45 days ago → past YELLOW_DAYS (30), should surface.
    old = (date.today() - timedelta(days=45)).isoformat()
    older = (date.today() - timedelta(days=90)).isoformat()
    p = _entity(tmp_path, "Bob Smith", "Bob Smith", ["person", "contact"],
                [older, old])
    result = rr.parse_entity(p)
    assert result is not None
    assert result["name"] == "Bob Smith"
    assert result["days_elapsed"] == 45  # max date used
    assert result["last_contact"] == old


def test_parse_entity_skipped_by_name(tmp_path):
    old = (date.today() - timedelta(days=45)).isoformat()
    # "Acme Corp" is in radar_skip_entity_names → None.
    p = _entity(tmp_path, "Acme Corp", "Acme Corp", ["person"], [old])
    assert rr.parse_entity(p) is None


def test_parse_entity_skipped_by_tag(tmp_path):
    old = (date.today() - timedelta(days=45)).isoformat()
    # "stub" is in radar_skip_tags → None.
    p = _entity(tmp_path, "Some Org", "Some Org", ["stub", "org"], [old])
    assert rr.parse_entity(p) is None


def test_parse_entity_too_recent_returns_none(tmp_path):
    # 5 days ago < YELLOW_DAYS (30) → not cold, None.
    recent = (date.today() - timedelta(days=5)).isoformat()
    p = _entity(tmp_path, "Fresh Contact", "Fresh Contact", ["person"], [recent])
    assert rr.parse_entity(p) is None


def test_parse_entity_no_frontmatter_returns_none(tmp_path):
    p = tmp_path / "NoFrontmatter.md"
    p.write_text("# Just a body\nno frontmatter here", encoding="utf-8")
    assert rr.parse_entity(p) is None


def test_extract_last_contact_picks_max_date():
    sources = ["2026-01-15 first", "2026-03-20 latest", "2026-02-10 middle"]
    assert rr.extract_last_contact(sources, None) == date(2026, 3, 20)


def test_extract_last_contact_falls_back_to_updated():
    # No dates in sources → fall back to `updated`.
    assert rr.extract_last_contact(["no dates here"], "2026-04-01") == date(2026, 4, 1)


def test_extract_last_contact_none_when_empty():
    assert rr.extract_last_contact([], None) is None
