"""Every to-do meeting_ingest writes carries its id from the first write.

Contract V2: each "- [ ]" under What You Owe, What Others Owe You and Action
Items carries `<!-- cb=cb-XXXXXXXX opened=YYYY-MM-DD -->`; the hotcache line
and its page line share one id; and `opened` is the meeting's date, never the
day the filing happened to run.
"""
import pytest

import ingest_fixture as fx
import item_ids
import meeting_ingest as gi
import meeting_synthesis

EM = chr(0x2014)
TITLE = "Pricing call - 2026-09-18"
HOT = "# Hot Cache\n\n## Test's Action Items\n\n## Notes\n"


def _page(**over):
    args = dict(
        meeting_id="note_x", meeting_source="granola", source_title=TITLE,
        date="2026-09-18", attendees_formatted="[[Sam Ortiz]]",
        attendees_list=["[[Sam Ortiz]]"], share_url=None, business="acme",
        meeting_type="meeting", summary_body="We talked pricing.",
        action_items=["**Test User**: share the forecast",
                      f"**Sam Ortiz** {EM} send the tier sheet"],
        detected_entities=set(), today="2026-10-01", synthesis_md=None)
    args.update(over)
    return gi.build_source_page(**args)


def _boxes(text, headings=gi.STAMP_HEADINGS):
    lines = text.splitlines()
    return [lines[i] for i in item_ids.section_lines(lines, headings)
            if item_ids.parse(lines[i])]


@pytest.fixture
def hot(tmp_path, monkeypatch):
    path = tmp_path / "hotcache.md"
    path.write_text(HOT, encoding="utf-8")
    monkeypatch.setattr(gi, "HOTCACHE", path)
    return path


def test_a_summary_page_stamps_each_action_item_and_nothing_else():
    page = _page()
    boxes = _boxes(page)
    assert len(boxes) == 2
    assert all(item_ids.CB_RE.search(ln) for ln in boxes)
    assert page.count("cb=cb-") == 2, "no id outside the to-do lines"
    assert len({item_ids.parse(ln)["cb"] for ln in boxes}) == 2


def test_opened_is_the_meeting_date_and_never_the_day_it_was_filed():
    page = _page(date="2026-09-18", today="2026-10-01")
    opened = {item_ids.parse(ln)["opened"] for ln in _boxes(page)}
    assert opened == {"2026-09-18"}
    assert "opened=2026-10-01" not in page


def test_a_meeting_with_no_usable_date_is_left_unstamped_not_dated_today():
    page = _page(date="unknown")
    assert "cb=" not in page


def test_the_empty_placeholder_checkbox_gets_no_id():
    page = _page(action_items=[])
    assert "- [ ] \n" in page and "cb=" not in page


def test_a_transcript_page_stamps_both_commitment_lists_and_not_next_steps():
    synth = {"discussed": "Pricing.", "decisions": ["Go with tier two."],
             "my_commitments": ["Share the forecast with Sam Ortiz"],
             "their_commitments": ["Sam Ortiz to send the tier sheet"],
             "next_steps": ["Forecast due Friday"]}
    page = _page(synthesis_md=meeting_synthesis.render_markdown(synth))
    stamped = [ln for ln in page.splitlines() if "cb=" in ln]
    assert [item_ids.parse(ln)["text"] for ln in stamped] == [
        "Share the forecast with Sam Ortiz", "Sam Ortiz to send the tier sheet"]
    assert "- [ ] Forecast due Friday\n" in page, \
        "a restated next step is not a second item"
    assert "- Go with tier two." in page


def test_the_hotcache_line_shares_its_id_with_the_page_line(hot):
    items = ["Share the forecast with Sam Ortiz", "Book the follow up"]
    synth_md = meeting_synthesis.render_markdown(
        {"discussed": "x", "my_commitments": items})
    page_ids = {item_ids.parse(ln)["text"]: item_ids.parse(ln)
                for ln in _boxes(_page(synthesis_md=synth_md))}
    n = gi.promote_user_action_items(items, TITLE, all_items=True,
                                     opened="2026-09-18")
    assert n == 2
    promoted = [item_ids.parse(ln) for ln in
                hot.read_text(encoding="utf-8").splitlines() if "cb=" in ln]
    assert len(promoted) == 2
    for row in promoted:
        twin = page_ids[row["core"]]
        assert row["cb"] == twin["cb"]
        assert row["opened"] == twin["opened"] == "2026-09-18"
        assert row["source"] == TITLE


def test_the_owner_prefix_the_hotcache_drops_does_not_change_the_id(hot):
    item = f"**Test User** {EM} share the forecast"
    page_line = [ln for ln in _boxes(_page(action_items=[item]))][0]
    gi.promote_user_action_items([item], TITLE, opened="2026-09-18")
    hot_line = [ln for ln in hot.read_text(encoding="utf-8").splitlines()
                if "cb=" in ln][0]
    assert hot_line.startswith("- [ ] share the forecast (from [[")
    assert item_ids.parse(hot_line)["cb"] == item_ids.parse(page_line)["cb"]


def test_filing_the_same_meeting_again_adds_no_second_line(hot):
    items = ["Share the forecast with Sam Ortiz"]
    gi.promote_user_action_items(items, TITLE, all_items=True, opened="2026-09-18")
    once = hot.read_text(encoding="utf-8")
    gi.promote_user_action_items(items, TITLE, all_items=True, opened="2026-09-18")
    assert hot.read_text(encoding="utf-8") == once


def test_a_line_filed_before_ids_existed_is_not_duplicated(hot):
    legacy = f"- [ ] Share the forecast with Sam Ortiz (from [[{TITLE}]])"
    hot.write_text(HOT.replace("## Notes", legacy + "\n\n## Notes"),
                   encoding="utf-8")
    before = hot.read_text(encoding="utf-8")
    gi.promote_user_action_items(["Share the forecast with Sam Ortiz"], TITLE,
                                 all_items=True, opened="2026-09-18")
    assert hot.read_text(encoding="utf-8") == before


def test_with_no_date_given_the_dated_title_supplies_it(hot):
    gi.promote_user_action_items(["Book the room"], TITLE, all_items=True)
    line = [ln for ln in hot.read_text(encoding="utf-8").splitlines()
            if "Book the room" in ln][0]
    assert item_ids.parse(line)["opened"] == "2026-09-18"


def test_with_no_date_anywhere_the_line_goes_in_without_one(hot):
    gi.promote_user_action_items(["Book the room"], "Undated chat",
                                 all_items=True, opened="unknown")
    line = [ln for ln in hot.read_text(encoding="utf-8").splitlines()
            if "Book the room" in ln][0]
    assert line == "- [ ] Book the room (from [[Undated chat]])"


# ── the real filing path, end to end ─────────────────────────────────────────

def test_a_nightly_filing_stamps_every_to_do_and_pairs_every_hotcache_line(
        tmp_path, monkeypatch):
    out = fx.run_nightly(gi, tmp_path / "vault", monkeypatch)
    pages = {name: text for name, text in out["files"].items()
             if name.startswith("wiki/sources/") and "catalog" not in name}
    assert len(pages) == 3

    by_title, boxes = {}, 0
    for name, text in pages.items():
        title = name.rsplit("/", 1)[1][:-3]
        for line in _boxes(text):
            item = item_ids.parse(line)
            if not item["text"]:
                continue
            boxes += 1
            assert item["cb"] and item["opened"], line
            assert item["opened"] in title, "opened is the meeting's own date"
            assert item["opened"] != fx.FROZEN_DAY
            by_title.setdefault(title, {})[item_ids.match_key(item["text"])] = item
        assert text.count("cb=cb-") == len(
            [ln for ln in _boxes(text) if "cb=" in ln]), "none elsewhere"
    assert boxes >= 3, "the fixture must actually produce to-dos"

    hot = [item_ids.parse(ln) for ln in
           out["files"]["wiki/hotcache.md"].splitlines() if "(from [[" in ln]
    hot = [h for h in hot if h]
    assert hot, "the fixture must promote at least one item"
    for row in hot:
        twin = by_title[row["source"]][item_ids.match_key(row["text"])]
        assert row["cb"] == twin["cb"], row["text"]
        assert row["opened"] == twin["opened"]
