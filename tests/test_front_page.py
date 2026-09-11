"""The front page: what reaches it, in what order, and what it looks like.

Covers P1 through P10 and P12 to P15 of objectives/briefing-front-page.
"""
import random
import re

import copy

import pytest

import config_loader as cl
import briefing_fixtures as bf
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



def _buckets(sections, businesses=None):
    bf.set_businesses(cl, businesses)
    for entries in sections.values():
        for e in entries:
            e["bucket"] = wr.assign_bucket(e)
    return wr.group_by_bucket(sections)


def _built(sections, businesses=None, **kw):
    return wr.build_front_page(_buckets(sections, businesses),
                               "morning-coffee", **kw)


# ── P1 ───────────────────────────────────────────────────────────────────────

def test_fits_one_screen():
    """120 and 300 items both render a front page under 45 lines."""
    for n in (120, 300):
        built = _built(bf.random_sections(seed=11, n_items=n))
        lines = built["front_page_md"].splitlines()
        assert len(lines) < 45, f"{n} items rendered {len(lines)} lines"
        opening = []
        for line in lines:
            if not line.strip():
                break
            opening.append(line)
        text = " ".join(opening)
        assert len(text.split()) < 60, text
        assert text.count(wr._DOT) <= 3


def test_opening_reads_differently_when_nothing_is_open():
    quiet = wr.opening_sentence({"open": 0, "late": 0, "front": 0, "folded": 0})
    busy = wr.opening_sentence({"open": 68, "late": 2, "front": 7, "folded": 61})
    assert quiet != busy
    assert "0" not in quiet


# ── P2 ───────────────────────────────────────────────────────────────────────

def test_conservation():
    """Front count plus every row's count equals open items, 20 fixtures."""
    for seed in range(20):
        built = _built(bf.random_sections(seed=seed, n_items=60))
        rows = sum(r["folded"] for r in built["fold_rows"])
        assert built["counts"]["front"] + rows == built["counts"]["open"], seed


# ── P3 ───────────────────────────────────────────────────────────────────────

def _label_col_offsets(text):
    """Column each item bullet starts in, for every line that has one."""
    return {ln.index(f"{wr._DOT} [ ]") for ln in text.splitlines()
            if f"{wr._DOT} [ ]" in ln}


def test_one_render_style():
    sections = bf.sample_sections()
    buckets = _buckets(sections)
    built = wr.build_front_page(buckets, "morning-coffee")
    ledger = wr.render_buckets_md(buckets, "morning-coffee")

    assert _label_col_offsets(built["front_page_md"]) == _label_col_offsets(ledger)
    assert wr._BAND in built["front_page_md"]
    labels = set(wr._BUCKET_LABELS["morning-coffee"])
    used = {i["label"] for i in built["front_page"]}
    assert used <= labels, used
    # No fourth label anywhere in the item region of the rendered page.
    body = built["front_page_md"].split(wr._BAND)[-1]
    for line in body.splitlines():
        head = line[2:2 + wr._LABEL_COL].strip().rstrip(":")
        if head and not head.startswith(wr._DOT):
            assert head in labels, head


# ── P4 ───────────────────────────────────────────────────────────────────────

def test_draft_line_geometry():
    """The note and the tag never collide, at the longest configured names.

    The long function name has to actually reach the page. The first version of
    this configured one and then let `assign_function` fall through to Other,
    because the keyword table only knows the default names, so the wrap
    assertion was never exercised on the long string it was written for. Here
    the entry carries the function the way the classifier sets it.
    """
    # Past the boundary on purpose. A 62-character tag renders at exactly the
    # wrap width even with the fix removed, so a fixture that length proves
    # nothing: the test has to fail when the wrap branch is deleted.
    long_biz = [{
        "tag": "verylong",
        "display_name": "Northwind Data Centers And Interconnection Holdings II",
        "project_page": "p.md", "meeting_route": "m", "keywords": ["kestrel"],
        "priorities": [],
    }]
    cl._config = dict(cl._config)
    cl._config["briefing"] = {
        "functions": ["Operations, Facilities And Field Services", "Other"]}
    sections = {"waiting_on_user": [
        {"source": "Outlook", "subject": "Kestrel site walk schedule",
         "counterparty_name": "Ana Beltre", "counterparty_email": "a@b.com",
         "reply_age_days": 3, "body_preview": "site walk schedule",
         "function": "Operations, Facilities And Field Services"}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections, long_biz)
    body = built["front_page_md"]
    flat = re.sub(r"\s+", " ", body)
    assert "NORTHWIND DATA CENTERS AND INTERCONNECTION HOLDINGS II" in flat
    # It may wrap across two lines, which is the point: it must never overrun.
    assert "Operations, Facilities And Field Services" in flat
    for line in body.splitlines():
        assert len(line) <= wr._WRAP, f"{len(line)} chars: {line!r}"

    # And again with a draft note, which is the wider of the two shapes.
    page = wr.front_page(_buckets(sections, long_biz))
    page[0]["draft_note"] = "drafted 06:40, Outlook"
    with_note = wr.render_front_page_md(
        page, wr.opening_counts(_buckets(sections, long_biz), page))
    for line in with_note.splitlines():
        assert len(line) <= wr._WRAP, f"{len(line)} chars: {line!r}"
    flat_note = re.sub(r"\s+", " ", with_note)
    assert "Operations, Facilities And Field Services" in flat_note
    assert "drafted 06:40, Outlook" in flat_note


def test_right_align_never_overruns():
    """Directly, at widths where the tag cannot fit flush right."""
    for right in ("SHORT " + wr._DOT + " Sales",
                  "A VERY LONG BUSINESS NAME INDEED " + wr._DOT
                  + " Operations And Facilities",
                  "NORTHWIND DATA CENTERS AND INTERCONNECTION HOLDINGS II "
                  + wr._DOT + " Operations, Facilities And Field Services"):
        for left in ("", "drafted 06:40, Outlook"):
            for line in wr._right_align(left, right, indent=26):
                assert len(line) <= wr._WRAP, f"{len(line)}: {line!r}"


def test_the_page_groups_by_who_owes_the_move():
    """Your own work first, then what you are chasing.

    The selection stays in rank order; the render groups it. Interleaving the
    two directions made the page a list of unrelated obligations and looked
    ragged, and a reader does their own work before chasing anyone.
    """
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme waiting later", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 2, "due": "2026-09-20"}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": [
        {"source": "G", "subject": "Acme older due today", "counterparty_email": "b@x.com",
         "counterparty_name": "B", "age_days": 12, "due": "2026-09-04"}]}
    built = _built(sections)
    # Selection is still by rank: the sooner item leads the list.
    assert [p["subject"] for p in built["front_page"]] == [
        "Acme older due today", "Acme waiting later"]
    # The render puts the reader's own work above what they are chasing.
    body = built["front_page_md"]
    assert body.index("Acme waiting later") < body.index("Acme older due today")
    assert body.index("Waiting on you") < body.index("Waiting on them")
    # Still only the three labels, still in the same column.
    labels = set(wr._BUCKET_LABELS["morning-coffee"])
    for line in body.split(wr._BAND)[-1].splitlines():
        head = line[2:2 + wr._LABEL_COL].strip().rstrip(":")
        if head and not head.startswith(wr._DOT):
            assert head in labels, head


def test_rank_order_holds_inside_a_group():
    """Grouping must not re-sort within a group."""
    sections = {"waiting_on_user": [
        {"source": "G", "subject": f"Acme due {d}", "counterparty_email": f"{d}@x.com",
         "counterparty_name": "A", "reply_age_days": 2, "due": f"2026-09-{d}"}
        for d in ("14", "06", "09")],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections)
    body = built["front_page_md"]
    assert (body.index("Acme due 06") < body.index("Acme due 09")
            < body.index("Acme due 14"))


# ── P5 ───────────────────────────────────────────────────────────────────────

def test_render_idempotent():
    sections = bf.sample_sections()
    first = _built(sections)
    second = _built(bf.sample_sections())
    for key in ("front_page_md", "fold_rows_md", "folded_md"):
        assert first[key] == second[key], key


# ── P6 ───────────────────────────────────────────────────────────────────────

def test_late_never_folded():
    """Twelve late items with a cap of seven: all twelve show."""
    sections = {"waiting_on_user": [
        {"source": "Gmail", "subject": f"Acme late item {i}",
         "counterparty_name": "X", "counterparty_email": f"x{i}@y.com",
         "reply_age_days": 5, "days_late": i + 1, "body_preview": "acme"}
        for i in range(12)], "inbox_pending": [], "cold_urgent": [],
        "cold_monitor": []}
    built = _built(sections, cap=7)
    assert len(built["front_page"]) == 12
    assert built["counts"]["late"] == 12
    assert "12 of your 12 open items are late" in built["front_page_md"]


def test_cap_bounds_non_late_items():
    sections = bf.random_sections(seed=3, n_items=200)
    built = _built(sections, cap=7)
    non_late = [i for i in built["front_page"] if not i["days_late"]]
    assert len(non_late) <= 7


def test_cap_is_clamped():
    sections = bf.sample_sections()
    for cap, expected in ((0, 3), (99, 9)):
        built = _built(sections, cap=cap)
        non_late = [i for i in built["front_page"] if not i["days_late"]]
        assert len(non_late) <= expected


# ── P7 ───────────────────────────────────────────────────────────────────────

def test_rank_order():
    """Shuffled ten times, the same page in the same order."""
    sections = bf.random_sections(seed=7, n_items=80)
    baseline = None
    for i in range(10):
        shuffled = {k: list(v) for k, v in sections.items()}
        rng = random.Random(i)
        for v in shuffled.values():
            rng.shuffle(v)
        page = _built(shuffled)["front_page"]
        signature = [(p["subject"], p["why"]) for p in page]
        if baseline is None:
            baseline = signature
        assert signature == baseline, i


def test_rank_tiers():
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme late", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 2, "days_late": 3},
        {"source": "G", "subject": "Acme dated", "counterparty_email": "b@x.com",
         "counterparty_name": "B", "reply_age_days": 2, "due": "2026-09-09"},
        {"source": "G", "subject": "Acme relevant widget deal",
         "counterparty_email": "c@x.com", "counterparty_name": "C",
         "reply_age_days": 9, "priority_matched": "Close the widget deal"},
        {"source": "G", "subject": "Acme plain", "counterparty_email": "d@x.com",
         "counterparty_name": "D", "reply_age_days": 20},
    ], "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    page = _built(sections)["front_page"]
    assert [p["subject"] for p in page] == [
        "Acme late", "Acme dated", "Acme relevant widget deal", "Acme plain"]


# ── P8 ───────────────────────────────────────────────────────────────────────

def test_stale_never_front():
    sections = {"waiting_on_user": [], "inbox_pending": [],
                "cold_urgent": [], "cold_monitor": [
        {"source": "G", "subject": "Acme very old thing",
         "counterparty_email": "old@x.com", "counterparty_name": "Old",
         "age_days": 90},
        {"source": "G", "subject": "Acme recent thing",
         "counterparty_email": "new@x.com", "counterparty_name": "New",
         "age_days": 9}]}
    built = _built(sections)
    subjects = [p["subject"] for p in built["front_page"]]
    assert "Acme very old thing" not in subjects
    assert "Acme recent thing" in subjects
    assert built["counts"]["stale"] == 1
    # Counted in its row, not deleted.
    assert sum(r["folded"] for r in built["fold_rows"]) == 1


def test_stale_with_a_date_still_ranks():
    sections = {"waiting_on_user": [], "inbox_pending": [],
                "cold_urgent": [], "cold_monitor": [
        {"source": "G", "subject": "Acme old but dated",
         "counterparty_email": "old@x.com", "counterparty_name": "Old",
         "age_days": 90, "due": "2026-09-05"}]}
    built = _built(sections)
    assert [p["subject"] for p in built["front_page"]] == ["Acme old but dated"]


def test_degraded_keeps_dated_first():
    """A dated item outranks every undated one, degraded judgment or not."""
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme dated widget", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 1, "due": "2026-09-06",
         "judged_relevant": None},
        {"source": "G", "subject": "Acme ancient widget deal",
         "counterparty_email": "b@x.com", "counterparty_name": "B",
         "reply_age_days": 25, "priority_matched": "Close the widget deal",
         "judged_relevant": True},
    ], "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections)
    assert built["front_page"][0]["subject"] == "Acme dated widget"
    ledger = wr.render_buckets_md(_buckets(sections), "morning-coffee",
                                  judgment={"degraded": True,
                                            "degraded_reasons": ["it did not run"]})
    assert "Heads up" in ledger


# ── P9 ───────────────────────────────────────────────────────────────────────

def test_no_invented_headings():
    built = _built(bf.random_sections(seed=5, n_items=90))
    allowed = {b["display_name"].upper() for b in bf.FOUR_BUSINESSES}
    allowed.add("COULDN'T PLACE THESE")
    for row in built["fold_rows"]:
        assert row["display_name"].upper() in allowed, row["display_name"]
    for item in built["front_page"]:
        assert item["bucket_name"].upper() in allowed


def test_file_under_sticks():
    """A keyword added to a business places the item that had no home."""
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Zamboni resurfacing quote",
         "counterparty_email": "z@x.com", "counterparty_name": "Z",
         "reply_age_days": 3}], "inbox_pending": [], "cold_urgent": [],
        "cold_monitor": []}
    built = _built(sections)
    assert built["front_page"][0]["bucket_tag"] == "unassigned"

    widened = [dict(b) for b in bf.FOUR_BUSINESSES]
    widened[2]["keywords"] = list(widened[2]["keywords"]) + ["zamboni"]
    again = _built({k: [dict(e) for e in v] for k, v in sections.items()}, widened)
    assert again["front_page"][0]["bucket_tag"] == "cedar"


# ── P10 ──────────────────────────────────────────────────────────────────────

def test_fold_flags():
    """Both flags fire on a planted fixture and neither on its control."""
    planted = {"waiting_on_user": [
        {"source": "G", "subject": "Acme gadget line update",
         "counterparty_email": "a@x.com", "counterparty_name": "A",
         "reply_age_days": 2, "priority_matched": "Ship the gadget line"},
        {"source": "G", "subject": "Acme filler one", "counterparty_email": "f@x.com",
         "counterparty_name": "F", "reply_age_days": 4}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    bf.set_businesses(cl, None)
    cl._config["businesses"] = [
        {"tag": "acme", "display_name": "Acme", "project_page": "p.md",
         "meeting_route": "m", "keywords": ["acme"],
         "priorities": [{"name": "Close the widget deal"},
                        {"name": "Ship the gadget line"}]}]
    alerts = {"newly_cold": [{"bucket": "acme", "counterparty_name": "Ray Okafor",
                              "age_days": 31}]}
    buckets = _buckets(planted, cl._config["businesses"])
    built = wr.build_front_page(buckets, "morning-coffee", alerts=alerts, cap=3)
    flags = [f for r in built["fold_rows"] for f in r["flags"]]
    assert any("nothing moving" in f for f in flags), flags
    assert any("quiet 31 days" in f for f in flags), flags
    assert all(len(r["flags"]) <= 2 for r in built["fold_rows"])

    control = wr.build_front_page(buckets, "morning-coffee", alerts={}, cap=3)
    control_flags = [f for r in control["fold_rows"] for f in r["flags"]]
    assert not any("quiet" in f for f in control_flags), control_flags


def test_flag_cap_is_two():
    bf.set_businesses(cl, None)
    cl._config["businesses"] = [
        {"tag": "acme", "display_name": "Acme", "project_page": "p.md",
         "meeting_route": "m", "keywords": ["acme"],
         "priorities": [{"name": "One"}, {"name": "Two"}, {"name": "Three"}]}]
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme thing", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 3}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    buckets = _buckets(sections, cl._config["businesses"])
    built = wr.build_front_page(buckets, "morning-coffee", cap=3)
    assert all(len(r["flags"]) <= 2 for r in built["fold_rows"])


# ── P12 ──────────────────────────────────────────────────────────────────────

def test_fold_functions_conserve():
    for seed in (1, 2, 3):
        built = _built(bf.random_sections(seed=seed, n_items=100))
        for row in built["fold_rows"]:
            assert sum(f["count"] for f in row["functions"]) == row["folded"], row


# ── P14 and P15 ──────────────────────────────────────────────────────────────

def test_open_function_slice():
    """Every business, not just the one with no priorities set.

    The first version of this pinned `tag == "cedar"`, the one fixture business
    with no priorities, which is the only case where the bug it was meant to
    catch cannot appear: a priority-matched item sits in its priority group, so
    filtering the groups by function dropped it. A row said "Sales 12" and the
    slice behind it pasted 5.
    """
    sections = bf.random_sections(seed=9, n_items=120)
    buckets = _buckets(sections)
    built = wr.build_front_page(buckets, "morning-coffee")
    keys = {tuple(i["key"]) for i in built["front_page"]}

    with_priorities = [r for r in built["fold_rows"]
                       if any(b["priorities"] for b in bf.FOUR_BUSINESSES
                              if b["tag"] == r["tag"])]
    assert with_priorities, "the fixture must include a business with priorities"

    for row in built["fold_rows"]:
        whole = wr.fold_slice(buckets, keys, row["tag"])
        for fn in row["functions"]:
            part = wr.fold_slice(buckets, keys, row["tag"], function=fn["name"])
            pasted = len(re.findall(r"\[ \]", part))
            assert pasted == fn["count"], (
                f'{row["tag"]} {fn["name"]}: row says {fn["count"]}, '
                f"slice pastes {pasted}")
            assert part.splitlines()[0].strip() == fn["name"]
            assert part.strip()
            for other in row["functions"]:
                if other["name"] != fn["name"]:
                    assert other["name"] not in part
        # The whole fold holds every folded item exactly once.
        assert len(re.findall(r"\[ \]", whole)) == row["folded"]


def test_unknown_slice_is_empty():
    buckets = _buckets(bf.sample_sections())
    assert wr.fold_slice(buckets, set(), "nope") == ""
    assert wr.fold_slice(buckets, set(), "cedar", function="Nope") == ""


def test_late_items_can_outgrow_the_page():
    """The one bound the page cannot hold, stated rather than hidden.

    P6 says a late item is never folded, so the page grows with the late count.
    At around fifteen late items it stops fitting a screen. That is the
    contract choosing correctness over compactness, and it is measured here so
    nobody rediscovers it as a surprise.
    """
    sections = {"waiting_on_user": [
        {"source": "G", "subject": f"Acme overdue deliverable number {i}",
         "counterparty_name": "Someone With A Name",
         "counterparty_email": f"p{i}@x.com", "reply_age_days": 9,
         "days_late": i + 1} for i in range(25)],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections, cap=7)
    assert len(built["front_page"]) == 25
    assert len(built["front_page_md"].splitlines()) > 45


# ── Found by a cold read of the rendered page, kept fixed here ───────────────

def test_opening_never_claims_nothing_is_late_beside_an_old_thread():
    """A reader counts a month of silence as late, whatever `days_late` says.

    The sentence used to open "Nothing is late" on a page showing a signature
    page that had been waiting thirty days, and to say those items were due
    "today" when five of seven were due next week. Both read as the briefing
    contradicting itself.
    """
    for counts in ({"open": 100, "late": 0, "front": 7, "folded": 93},
                   {"open": 3, "late": 0, "front": 1, "folded": 2},
                   {"open": 1, "late": 0, "front": 1, "folded": 0}):
        text = wr.opening_sentence(counts)
        assert "Nothing is late" not in text, text
        assert "today" not in text, text
    late = wr.opening_sentence({"open": 68, "late": 2, "front": 7, "folded": 61})
    assert "2 of your 68 open items are late" in late
    assert "today" not in late


def test_an_undrafted_reply_renders_no_note():
    """"reply not drafted" down all seven rows is a column of noise."""
    sections = {"waiting_on_user": [
        {"source": "Gmail", "subject": "Acme widget renewal", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 3}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections)
    assert "not drafted" not in built["front_page_md"]

    buckets = _buckets(sections)
    page = wr.front_page(buckets)
    page[0]["draft_note"] = "drafted 06:40, Gmail"
    counts = wr.opening_counts(buckets, page)
    with_note = wr.render_front_page_md(page, counts)
    # The note now rides on the item line in both renders, so it wraps like
    # any other text and the raw string can carry a newline mid-phrase.
    assert "drafted 06:40, Gmail" in re.sub(r"\s+", " ", with_note)
    assert "replies drafted" in with_note
    md = wr.render_front_page_md(page, counts, mode="md")
    assert "drafted 06:40, Gmail" in md


def test_a_signature_item_still_says_so():
    sections = {"inbox_pending": [
        {"source": "Outlook", "subject": "Acme signature page",
         "counterparty_email": "a@x.com", "counterparty_name": "A",
         "reply_age_days": 2}],
        "waiting_on_user": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections)
    assert "signature only" in re.sub(r"\s+", " ", built["front_page_md"])


def test_a_label_prints_once_per_group():
    """The label heads its group and does not repeat down every row."""
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme first", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 1, "due": "2026-09-05"},
        {"source": "G", "subject": "Acme third", "counterparty_email": "c@x.com",
         "counterparty_name": "C", "reply_age_days": 1, "due": "2026-09-07"}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": [
        {"source": "G", "subject": "Acme second", "counterparty_email": "b@x.com",
         "counterparty_name": "B", "age_days": 10, "due": "2026-09-06"}]}
    built = _built(sections)
    assert [p["subject"] for p in built["front_page"]] == [
        "Acme first", "Acme second", "Acme third"]
    body = built["front_page_md"]
    labelled = [ln[2:2 + wr._LABEL_COL].strip().rstrip(":")
                for ln in body.splitlines()
                if ln[2:2 + wr._LABEL_COL].strip().rstrip(":")
                in set(wr._BUCKET_LABELS["morning-coffee"])]
    assert labelled == ["Waiting on you", "Waiting on them"], labelled


def test_an_item_opened_today_still_shows_its_age():
    """Zero is an age. It used to render as nothing at all."""
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme arrived this morning",
         "counterparty_email": "a@x.com", "counterparty_name": "A",
         "reply_age_days": 0}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    buckets = _buckets(sections)
    folded = wr.render_folded_md(buckets, set())
    assert "today" in folded, folded
    # On the front page the same item reads "open now", which is the same fact
    # in the words that line uses.
    built = wr.build_front_page(buckets, "morning-coffee")
    assert built["front_page"][0]["why"] == "open now"
    line = wr._item_line({"subject": "S", "age_days": 0})
    assert "today" in line
    assert "1 day ago" in wr._item_line({"subject": "S", "age_days": 1})
    assert "2 days ago" in wr._item_line({"subject": "S", "age_days": 2})
    # A late item says how late it is, not how old it is.
    assert "today" not in wr._item_line({"subject": "S", "age_days": 0,
                                         "days_late": 3})


def test_a_row_count_says_which_direction_it_means():
    """"(3 waiting)" printed the right number and no direction.

    A reader has "Waiting on you" and "Waiting on them" side by side inches
    above the row, so a bare "waiting" beside a number has two readings that
    imply completely different days. The count has only ever meant waiting on
    the reader, and it says so.
    """
    built = _built(bf.random_sections(seed=15, n_items=90), cap=3)
    rows = re.sub(r"\s+", " ", built["fold_rows_md"])
    assert "waiting)" not in rows, rows
    live = [r for r in built["fold_rows"] if r["folded"]]
    saw_some, saw_none = False, False
    for i, row in enumerate(live):
        # Scope to this row: a function name and count can repeat verbatim in
        # another business, so a whole-text search proves the wrong thing.
        start = rows.index(row["display_name"].upper())
        end = (rows.index(live[i + 1]["display_name"].upper())
               if i + 1 < len(live) else len(rows))
        slice_ = rows[start:end]
        for fn in row["functions"]:
            if fn["waiting"]:
                saw_some = True
                assert (f"{fn['name']} {fn['count']} ({fn['waiting']} on you)"
                        in slice_), (row["tag"], fn, slice_)
            else:
                # Nothing on the reader carries no bracket at all.
                saw_none = True
                assert f"{fn['name']} {fn['count']} (" not in slice_, (row["tag"], fn)
    assert saw_some and saw_none, "the fixture must show both shapes"


def test_a_row_says_its_share_not_a_total():
    """A bare "CEDAR 18" was read as everything Cedar has open.

    Three of five cold readers did exactly that and then computed a different
    grand total for the whole briefing. One disclaimer line above the block did
    not reach them, because a reader reads the row, not the preamble. The row
    carries both numbers now, so it cannot be read as a total.
    """
    built = _built(bf.random_sections(seed=12, n_items=90))
    rows = built["fold_rows_md"]
    promoted = {}
    for item in built["front_page"]:
        promoted[item["bucket_tag"]] = promoted.get(item["bucket_tag"], 0) + 1
    checked = 0
    for row in built["fold_rows"]:
        if not row["folded"]:
            continue
        assert row["total"] == row["folded"] + promoted.get(row["tag"], 0)
        if row["total"] != row["folded"]:
            checked += 1
            assert f"{row['folded']} of {row['total']}" in rows, row["tag"]
        else:
            # Nothing promoted from this business, so there is nothing to say.
            assert f"{row['folded']} of" not in rows.split(
                row["display_name"].upper(), 1)[-1][:40]
    assert checked, "the fixture must promote from at least one business"
    assert "more" in built["folded_md"]
    # The explanation line is gone: the rows carry it themselves now.
    assert "not repeated here" not in rows


def test_the_opening_says_who_owes_the_next_move():
    """A page can be mostly other people's move, and must not claim otherwise.

    Rank is by date and ignores direction, so a page legitimately carries
    items the counterparty owes. Saying all seven "need you first" was false
    about most of them, and the reader can see the labels underneath.
    """
    sections = {"waiting_on_user": [
        {"source": "G", "subject": "Acme redline for you", "counterparty_email": "a@x.com",
         "counterparty_name": "A", "reply_age_days": 2, "due": "2026-09-05"}],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": [
        {"source": "G", "subject": "Acme term sheet on them", "counterparty_email": "b@x.com",
         "counterparty_name": "B", "age_days": 9, "due": "2026-09-06"},
        {"source": "G", "subject": "Acme quote on them", "counterparty_email": "c@x.com",
         "counterparty_name": "C", "age_days": 11, "due": "2026-09-07"}]}
    built = _built(sections)
    assert built["counts"]["on_you"] == 1
    assert built["counts"]["on_them"] == 2
    flat = re.sub(r"\s+", " ", built["front_page_md"])
    assert "1 need" not in flat
    assert "one needs a reply from you" in flat
    assert "2 are waiting on someone else" in flat
    assert "need you first" not in flat


def test_a_page_entirely_on_other_people_says_so():
    sections = {"waiting_on_user": [], "inbox_pending": [], "cold_urgent": [],
                "cold_monitor": [
        {"source": "G", "subject": f"Acme thing {i} on them",
         "counterparty_email": f"c{i}@x.com", "counterparty_name": "C",
         "age_days": 9 + i, "due": f"2026-09-0{i + 1}"} for i in range(3)]}
    built = _built(sections)
    assert built["counts"]["on_you"] == 0
    flat = re.sub(r"\s+", " ", built["front_page_md"])
    assert "every one of them is waiting on someone else" in flat


def test_a_page_entirely_on_the_reader_says_nothing_extra():
    sections = {"waiting_on_user": [
        {"source": "G", "subject": f"Acme thing {i}", "counterparty_email": f"a{i}@x.com",
         "counterparty_name": "A", "reply_age_days": 3 + i} for i in range(3)],
        "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
    built = _built(sections)
    assert built["counts"]["on_them"] == 0
    flat = re.sub(r"\s+", " ", built["front_page_md"])
    assert "waiting on someone else" not in flat


def test_the_opening_stays_short_with_the_split():
    for n in (120, 300):
        built = _built(bf.random_sections(seed=11, n_items=n))
        opening = built["front_page_md"].split("\n\n", 1)[0]
        assert len(opening.split()) < 60, opening


# ── the rows are terminal-only ───────────────────────────────────────────────

def test_no_skill_writes_the_fold_rows_into_the_file():
    """`folded_md` opens with the same business names the rows list.

    Writing both put NORTHWIND DC, HARBOR SOLAR and PROJECT ZEUS on the page
    twice, once as a bullet and once as the fold header right beneath it. The
    rows exist for the terminal, which has no folds and needs a ledger view;
    on a surface that folds, the fold headers are that view.

    Five files said this, and four of them said the opposite of the fifth,
    which is how the duplicate shipped. This pins the rule in one place.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "skills"
    offenders = []
    for md in sorted(root.rglob("SKILL.md")) + [root / "_shared" / "front-page.md"]:
        text = md.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "fold_rows_file_md" not in line:
                continue
            # A line may only mention it to forbid it or to describe the key.
            if any(w in line for w in ("do NOT", "does not go", "NOT written")):
                continue
            offenders.append(f"{md.relative_to(root.parent)}:{i}: {line.strip()}")
    assert not offenders, (
        "these lines route the rows into a folding surface:\n  "
        + "\n  ".join(offenders))


def test_the_dailies_open_with_a_read():
    """Morning Coffee and Afternoon Tea opened on a counts sentence alone.

    That sentence is arithmetic by design (two sentences, three named numbers,
    every one counted), which is right for a number and wrong for a day. The
    read is the written part above it, and its one load-bearing rule is that
    every claim traces to a field, because an invented sentence at the top of
    a briefing is worse than no sentence: everything under it is true, so the
    reader cannot tell which line was the guess.
    """
    from pathlib import Path
    skills = Path(__file__).resolve().parent.parent / "skills"
    shared = (skills / "_shared" / "front-page.md").read_text(encoding="utf-8")
    assert "## The read" in shared
    assert "Every claim traces to a field" in shared
    for name in ("morning-coffee", "afternoon-tea"):
        text = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        assert "THE READ" in text, name
        assert '"The read"' in text, f"{name} must point at the shared rules"


def test_the_read_never_becomes_the_counts_sentence_twice():
    """The code already writes the counts line inside front_page_md."""
    from pathlib import Path
    skills = Path(__file__).resolve().parent.parent / "skills"
    for name in ("morning-coffee", "afternoon-tea"):
        text = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        assert "do not write one of your\nown above it" in text or \
               "do not write one above it" in text or \
               "do not write one of your own above it" in text, name


# ── the suggested focus task ─────────────────────────────────────────────────

def test_the_dailies_and_the_week_carry_a_focus_task():
    """Every rank tier on the front page is reactive: late, then due, then
    oldest. That answers "what is on fire" and never "what would move a
    business forward". Work that compounds has no deadline and nobody chases
    it, which is exactly why it cannot reach a page built on deadlines.
    """
    from pathlib import Path
    skills = Path(__file__).resolve().parent.parent / "skills"
    shared = (skills / "_shared" / "front-page.md").read_text(encoding="utf-8")
    assert "## The suggested focus task" in shared
    for phrase in ("What compounds", "priorities", "quietly stuck",
                   "Every claim traces to a field"):
        assert phrase in shared, phrase
    # It must refuse rather than invent one.
    assert "render no card" in shared

    for name in ("morning-coffee", "afternoon-tea"):
        t = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        assert "SUGGESTED FOCUS TASK" in t, name
        assert '"The suggested focus\ntask"' in t or '"The suggested focus task"' in t
    week = (skills / "week" / "SKILL.md").read_text(encoding="utf-8")
    assert "ONE BIG ROCK" in week, "the week gets one at a week's scale"


def test_the_focus_task_reads_the_whole_ledger_not_the_front_page():
    """The front page is the eight items that shouted loudest, which is the
    exact set a focus task is supposed to look past."""
    from pathlib import Path
    skills = Path(__file__).resolve().parent.parent / "skills"
    shared = (skills / "_shared" / "front-page.md").read_text(encoding="utf-8")
    assert "`buckets` carries" in shared
    assert "not the front page" in shared
