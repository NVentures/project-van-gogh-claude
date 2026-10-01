"""P14 and P31: what already worked still renders exactly as it did.

The snapshots in tests/snapshots/ were frozen from the commit BEFORE the front
page existed, by objectives/briefing-front-page/freeze_snapshots.py. They are
never regenerated from the code they grade: a reference rebuilt from the run
being graded proves nothing.
"""
from pathlib import Path

import copy

import pytest

import config_loader as cl
import briefing_fixtures as bf
import digest_html
import week_review as wr


@pytest.fixture(autouse=True)
def _restore_config():
    """These tests swap the business list; put the suite's fixture back after.

    config_loader._config is module state primed once by conftest, so a test
    that edits it leaks into every test that runs later.
    """
    saved = copy.deepcopy(cl._config)
    yield
    cl._config = saved


SNAP = Path(__file__).resolve().parent / "snapshots"


def _buckets():
    bf.set_businesses(cl)
    sections = bf.sample_sections()
    for entries in sections.values():
        for e in entries:
            e["bucket"] = wr.assign_bucket(e)
    return wr.group_by_bucket(sections)


def test_buckets_md_unchanged():
    buckets = _buckets()
    for briefing in ("week", "afternoon-tea"):
        for mode in ("terminal", "md"):
            frozen = (SNAP / f"buckets_{briefing}_{mode}.txt").read_text(encoding="utf-8")
            assert wr.render_buckets_md(buckets, briefing, mode=mode) == frozen, \
                f"{briefing}/{mode} drifted from the pre-change render"


def test_digest_html_unchanged():
    buckets = _buckets()
    for name in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
        md = (f"# {name}\n\nA sentence about the day.\n\n"
              + wr.render_buckets_md(
                  buckets, "week" if name != "afternoon-tea" else "afternoon-tea",
                  mode="md"))
        frozen = (SNAP / f"digest_{name}.html").read_text(encoding="utf-8")
        assert digest_html.md_to_email_html(md) == frozen, name


def test_fold_body_unchanged():
    """The ledger render is untouched, and the fold is one partition of it.

    `by_function=False` is byte-equal to the frozen pre-change render. The fold
    (`by_function=True`) groups the SAME items by function and nothing else,
    because a fold that put priority-matched items under a priority heading and
    the rest under function headings showed the reader two different partitions
    of one set: the business row said "Sales 3" and the heading behind it held
    one item. The priority a folded item belongs to still shows, on the item
    line rather than as a heading.
    """
    buckets = _buckets()
    frozen = (SNAP / "buckets_week_md.txt").read_text(encoding="utf-8")
    allowed = set(wr.briefing_functions())
    for bucket in buckets:
        plain = "\n".join(wr._render_one_bucket(bucket, "week", mode="md"))
        assert plain.strip() in frozen or not bucket["items"], bucket["tag"]
        folded = "\n".join(wr._render_one_bucket(bucket, "week", mode="md",
                                                 by_function=True))
        for pname in [p["name"] for p in bucket.get("priorities") or []]:
            if pname in plain and any(
                    e.get("priority_matched") == pname for e in bucket["items"]):
                assert f"on {pname}" in folded, pname
        assert wr._LABEL_EVERYTHING_ELSE not in folded
        # Every heading in a fold is a configured function and nothing else.
        for line in folded.splitlines():
            if line and not line.startswith(("- ", "**", " ")):
                assert line.strip() in allowed, line


def test_fold_headings_match_the_row_counts():
    """The number in the row is the number behind the heading it opens."""
    import briefing_fixtures as _bf
    sections = _bf.random_sections(seed=14, n_items=140)
    for entries in sections.values():
        for e in entries:
            e["bucket"] = wr.assign_bucket(e)
    buckets = wr.group_by_bucket(sections)
    built = wr.build_front_page(buckets, "morning-coffee")
    keys = {tuple(i["key"]) for i in built["front_page"]}
    names = set(wr.briefing_functions())
    for row in built["fold_rows"]:
        body = wr.fold_slice(buckets, keys, row["tag"])
        sections, current = {}, None
        for line in body.splitlines():
            if line.strip() in names and line == line.strip():
                current = line.strip()
                sections[current] = 0
            elif current and "[ ]" in line:
                sections[current] += 1
        for fn in row["functions"]:
            assert fn["name"] in sections, f"{row['tag']}: no {fn['name']} heading"
            assert sections[fn["name"]] == fn["count"], (
                f"{row['tag']} {fn['name']}: row says {fn['count']}, "
                f"fold shows {sections[fn['name']]}")
        assert set(sections) == {f["name"] for f in row["functions"]}


def test_fold_body_keeps_every_item():
    buckets = _buckets()
    for bucket in buckets:
        folded = "\n".join(wr._render_one_bucket(bucket, "week", mode="md",
                                                 by_function=True))
        for entry in bucket["items"]:
            assert entry["subject"] in folded, entry["subject"]
