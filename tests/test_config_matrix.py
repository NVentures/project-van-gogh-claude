"""P11: configs written by many different people, not one.

Every client writes their config differently. These are the shapes that break
a loader tuned on one example: no buckets, forty buckets, empty priority
lists, duplicate tags, and names with apostrophes, accents, emoji and regex
metacharacters in them. A priority string is fed to a matcher, so an
unescaped "(" in someone's bucket name is a crash waiting for the one client
who writes that way.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config_loader as cl                                      # noqa: E402
import week_review as wr                                        # noqa: E402


def _prime(businesses):
    saved = cl._config
    cl._config = dict(saved)
    cl._config["businesses"] = businesses
    cl._config["judgment"] = {"enabled": False, "model": ""}
    return saved


@pytest.fixture
def config():
    saved = cl._config
    yield _prime
    cl._config = saved


def _b(tag, name, keywords, priorities):
    return {"tag": tag, "display_name": name, "project_page": "p.md",
            "meeting_route": "m", "keywords": keywords,
            "priorities": [{"name": p} for p in priorities]}


def test_no_buckets_at_all(config):
    config([])
    assert cl.businesses() == []
    assert cl.business_priorities("anything") == []
    assert wr.group_by_bucket({"waiting_on_user": [], "inbox_pending": [],
                               "cold_urgent": [], "cold_monitor": []}) == []


def test_one_bucket_one_priority(config):
    config([_b("solo", "Solo", ["solo"], ["Do the thing"])])
    assert [p["name"] for p in cl.business_priorities("solo")] == ["Do the thing"]


def test_forty_buckets(config):
    config([_b(f"b{i}", f"Business {i}", [f"kw{i}"], [f"Priority {i}"])
            for i in range(40)])
    assert len(cl.businesses()) == 40
    assert cl.business_priorities("b39")[0]["name"] == "Priority 39"


def test_bucket_with_empty_priority_list(config):
    config([_b("empty", "Empty", ["empty"], [])])
    assert cl.business_priorities("empty") == []


def test_bucket_missing_the_priorities_key_entirely(config):
    b = _b("nokey", "No Key", ["nokey"], [])
    del b["priorities"]
    config([b])
    assert cl.business_priorities("nokey") == []


def test_duplicate_tags_resolve_to_the_first(config):
    # Two entries with one tag is a config mistake, not a crash. First wins,
    # deterministically, so two runs never disagree.
    config([_b("dup", "First", ["first"], ["First priority"]),
            _b("dup", "Second", ["second"], ["Second priority"])])
    assert cl.business_priorities("dup")[0]["name"] == "First priority"


@pytest.mark.parametrize("name,priority", [
    ("O'Brien Holdings", "Close O'Brien's renewal"),
    ("Café Nord", "Renouveler le contrat café"),
    ("Zürich AG", "Schließe den Vertrag"),
    ("Rocket 🚀 Labs", "Ship 🚀 v2"),
    ("Smith (Holdings) Ltd.", "Close Smith (Holdings)"),
    ("A+B Partners", "Grow A+B revenue"),
    ("Q[1] Push", "Hit Q[1] number"),
    ("50% Club", "Reach 50% margin"),
    ("Back\\slash Co", "Fix Back\\slash billing"),
])
def test_hostile_names_load_and_match_without_crashing(config, name, priority):
    # Every one of these ends up inside a regex or a tokenizer somewhere.
    config([_b("x", name, ["kw"], [priority])])
    assert cl.business_priorities("x")[0]["name"] == priority
    assert wr.match_priority({"subject": "kw something"},
                             cl.business_priorities("x")) in (priority, "")
    buckets = wr.group_by_bucket({"waiting_on_user": [{"subject": "kw thing"}],
                                  "inbox_pending": [], "cold_urgent": [],
                                  "cold_monitor": []})
    rendered = wr.render_buckets_md(buckets, "week")
    assert name.upper() in rendered


def test_hostile_priority_text_does_not_break_the_gate(config):
    config([_b("x", "X", ["kw"], ["Close (the) [big] deal +50%"])])
    out = {"cold_urgent": [{"subject": "kw unrelated", "counterparty_email": "a@b.com",
                            "age_days": 5}],
           "cold_monitor": [], "filtered_pending": []}
    wr.gate_cold_promotion(out, active_emails=set())
    assert len(out["cold_urgent"]) + len(out["cold_monitor"]) == 1


def test_very_long_priority_name_renders(config):
    long_name = "Close " + "the very long deal " * 20
    config([_b("x", "X", ["kw"], [long_name])])
    buckets = wr.group_by_bucket({"waiting_on_user": [{"subject": "kw thing"}],
                                  "inbox_pending": [], "cold_urgent": [],
                                  "cold_monitor": []})
    assert wr.render_buckets_md(buckets, "week")
