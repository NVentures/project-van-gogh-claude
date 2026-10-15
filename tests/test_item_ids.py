"""item_ids.py: one stable id per to-do line.

What these hold: the id is derived so two files agree without looking each
other up; stamping never rewords and a second pass changes zero bytes; a line
keeps its id through anything a person does to it; and a file's own line
endings survive.
"""
import context_pack
import done_judge as dj
import done_judge_fixture as fx
import item_ids
import morning_coffee as mc

HEAD = ("Action Items",)
DAY = "2026-09-20"
EM = chr(0x2014)


def test_a_stamped_line_reads_back_what_was_written():
    cb = item_ids.new_id("Call - 2026-09-20", "Send the deck")
    line = item_ids.stamp_line("- [ ] Send the deck\n", cb, DAY)
    assert line == f"- [ ] Send the deck <!-- cb={cb} opened={DAY} -->\n"
    got = item_ids.parse(line)
    assert (got["cb"], got["opened"], got["text"], got["checked"]) == (
        cb, DAY, "Send the deck", False)


def test_the_id_is_derived_so_filing_twice_gives_the_same_one():
    a = item_ids.new_id("Call - 2026-09-20", "Send the deck")
    assert a == item_ids.new_id("call  -  2026-09-20", "  send   the DECK ")
    assert a != item_ids.new_id("Call - 2026-09-21", "Send the deck")
    assert a != item_ids.new_id("Call - 2026-09-20", "Send the plan")
    assert item_ids.CB_RE.search(f"<!-- cb={a} opened={DAY} -->")


def test_stamping_twice_changes_nothing():
    text = "## Action Items\n\n- [ ] Send the deck\n- [ ] Book the room\n"
    once = item_ids.stamp_block(text, HEAD, DAY, "Call")
    assert once.count("cb=cb-") == 2
    assert item_ids.stamp_block(once, HEAD, "2026-10-01", "Other") == once


def test_a_bare_id_is_adopted_with_its_id_kept():
    line = "- [ ] Send the deck <!-- cb=cb-0123abcd -->\n"
    out = item_ids.stamp_line(line, "cb-ffffffff", DAY)
    assert out == f"- [ ] Send the deck <!-- cb=cb-0123abcd opened={DAY} -->\n"


def test_a_line_edited_by_hand_after_stamping_keeps_its_id():
    text = "## Action Items\n- [ ] Send the deck\n"
    stamped = item_ids.stamp_block(text, HEAD, DAY, "Call")
    cb = item_ids.parse(stamped.splitlines()[1])["cb"]
    edited = stamped.replace("Send the deck", "Send Priya the long deck, v2")
    again = item_ids.stamp_block(edited, HEAD, "2026-10-01", "Call")
    assert again == edited, "an id already on the line is never recomputed"
    assert item_ids.parse(again.splitlines()[1])["cb"] == cb


def test_only_open_checkboxes_with_words_under_the_heading_are_stamped():
    text = ("## Summary\n- [ ] not a to-do\n## Action Items\n"
            "- [ ] real one\n- [x] already done\n- [ ] \n- a bullet\n"
            "### Later\n- [ ] in a subsection\n## Notes\n- [ ] outside\n")
    out = item_ids.stamp_block(text, HEAD, DAY, "Call").splitlines()
    marked = [ln for ln in out if "cb=" in ln]
    assert [ln.split(" <!--")[0] for ln in marked] == [
        "- [ ] real one", "- [ ] in a subsection"]


def test_heading_depth_is_not_assumed():
    for hashes in ("#", "##", "###"):
        text = f"{hashes} Action Items\n- [ ] one\n{hashes} Next\n- [ ] two\n"
        out = item_ids.stamp_block(text, HEAD, DAY, "Call")
        assert out.count("cb=") == 1 and "one <!--" in out, hashes


def test_a_heading_on_the_first_line_of_the_file_is_found():
    out = item_ids.stamp_block("# Action Items\n- [ ] one\n", HEAD, DAY, "C")
    assert out.count("cb=") == 1


def test_windows_line_endings_survive(tmp_path):
    path = tmp_path / "hot.md"
    raw = b"## Action Items\r\n\r\n- [ ] Send the deck\r\n- [ ] Book it\r\n"
    path.write_bytes(raw)
    got = item_ids.backfill(path, HEAD, DAY, "Call")
    after = path.read_bytes()
    assert got == {"stamped": 2, "adopted": 0, "changed": True}
    assert after.count(b"\r\n") == raw.count(b"\r\n")
    assert b"\n" not in after.replace(b"\r\n", b"")
    assert after.count(b" -->\r\n") == 2


def test_a_second_backfill_changes_zero_bytes(tmp_path):
    path = tmp_path / "hot.md"
    path.write_text("## Action Items\n- [ ] Send the deck\n"
                    "- [ ] old <!-- cb=cb-0123abcd -->\n", encoding="utf-8")
    first = item_ids.backfill(path, HEAD, DAY, "Call")
    assert (first["stamped"], first["adopted"]) == (1, 1)
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    second = item_ids.backfill(path, HEAD, "2026-10-01", "Call")
    assert second == {"stamped": 0, "adopted": 0, "changed": False}
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp, "an unchanged file is not rewritten"


def test_a_dry_backfill_counts_and_writes_nothing(tmp_path):
    path = tmp_path / "hot.md"
    path.write_text("## Action Items\n- [ ] Send the deck\n", encoding="utf-8")
    before = path.read_bytes()
    got = item_ids.backfill(path, HEAD, DAY, "Call", live=False)
    assert got["stamped"] == 1 and got["changed"] and path.read_bytes() == before


def test_a_missing_file_is_zero_of_everything(tmp_path):
    assert item_ids.backfill(tmp_path / "none.md", HEAD, DAY) == {
        "stamped": 0, "adopted": 0, "changed": False}


def test_the_same_words_on_two_hotcache_lines_get_two_ids():
    text = "## Action Items\n- [ ] Call the bank\n- [ ] Call the bank\n"
    out = item_ids.stamp_block(text, HEAD, DAY, "", unique=True)
    ids = [item_ids.parse(ln)["cb"] for ln in out.splitlines()[1:]]
    assert len(set(ids)) == 2
    assert item_ids.stamp_block(out, HEAD, DAY, "", unique=True) == out


def test_opened_comes_from_the_dated_meeting_the_line_names():
    text = ("## Action Items\n"
            "- [ ] Send it (from [[Pricing call - 2026-08-14]])\n"
            "- [ ] Dedupe (from [[Sync - 2026-08-15 (ab12)]])\n"
            "- [ ] No source\n")
    lines = item_ids.stamp_block(text, HEAD, "2026-10-01", "").splitlines()
    assert [item_ids.parse(ln)["opened"] for ln in lines[1:]] == [
        "2026-08-14", "2026-08-15", "2026-10-01"]


def test_a_line_with_no_date_to_take_is_left_alone():
    text = "## Action Items\n- [ ] No source and no default\n"
    assert item_ids.stamp_block(text, HEAD, "", "") == text


def test_a_page_adopts_the_id_its_hotcache_line_already_has():
    hot = item_ids.stamp_block(
        "## Action Items\n- [ ] Send the deck (from [[Call - 2026-09-20]])\n",
        HEAD, "", "", unique=True)
    cb = item_ids.parse(hot.splitlines()[1])["cb"]
    known = {item_ids.match_key("Send the deck (from [[Call - 2026-09-20]])"): cb}
    page = f"## Action Items\n- [ ] **Test User** {EM} Send the deck\n"
    out = item_ids.stamp_block(page, HEAD, DAY, "Call - 2026-09-20", known=known)
    assert item_ids.parse(out.splitlines()[1])["cb"] == cb


def test_the_readers_that_show_a_task_drop_the_marker():
    line = f"Send the deck (due 2026-10-01) <!-- cb=cb-0123abcd opened={DAY} -->"
    assert mc.TASK_TRAIL_RE.sub(" ", line).strip() == "Send the deck"
    assert context_pack.strip_markers("- [ ] " + line) == \
        "- [ ] Send the deck (due 2026-10-01)"
    assert item_ids.strip("- [ ] " + line) == "- [ ] Send the deck (due 2026-10-01)"


def test_find_lines_matches_the_whole_id_on_a_checkbox_only():
    lines = ["- [ ] a <!-- cb=cb-0123abcd opened=2026-09-20 -->\n",
             "prose mentioning cb=cb-0123abcd\n",
             "- [ ] b <!-- cb=cb-0123abcde -->\n"]
    assert item_ids.find_lines(lines, "cb-0123abcd") == [0]
    assert item_ids.find_lines(lines, "") == []


# ── the planted vault (contract V2) ──────────────────────────────────────────

LEGACY = f"""## {fx.HEADING}

- [ ] Send Priya Raman the revised site plan (from [[{fx.REVIEW}]])
- [ ] Renew the parking permit <!-- cb=cb-0123abcd -->
- [ ] Renew the parking permit
- [x] Finished already

### Owed to Test

- [ ] **Rosa Calder:** Send the boundary survey (from [[{fx.REVIEW}]])

## Key Numbers

- [ ] outside the section
"""


def _open_without_id(text):
    lines = text.splitlines()
    return [lines[i] for i in item_ids.section_lines(lines, (fx.HEADING,))
            if (item_ids.parse(lines[i]) or {}).get("checked") is False
            and not item_ids.CB_RE.search(lines[i])]


def test_backfill_on_a_planted_vault_leaves_no_open_box_without_an_id(
        tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch, hotcache=LEGACY)
    v.review.write_text(
        fx.REVIEW_PAGE.replace(f" <!-- cb={fx.CB_PLAN} opened=2026-09-20 -->", "")
        .replace(f" <!-- cb={fx.CB_OWED} opened=2026-09-20 -->", ""),
        encoding="utf-8")
    assert len(_open_without_id(v.hot.read_text(encoding="utf-8"))) == 4

    got = dj.stamp_missing(fx.TODAY, live=True)
    text = v.hot.read_text(encoding="utf-8")
    assert _open_without_id(text) == []
    assert got["hotcache"] == {"stamped": 3, "adopted": 1}
    assert "cb=cb-0123abcd opened=" in text, "the bare id was kept"
    assert "- [x] Finished already\n" in text
    assert "- [ ] outside the section\n" in text

    # The meeting page's two lines carry the ids of their hotcache lines,
    # and the dates of the meeting, not of the day the backfill ran.
    hot_ids = {item_ids.match_key(item_ids.parse(ln)["text"]): item_ids.parse(ln)
               for ln in text.splitlines() if "(from [[" in ln}
    page = [item_ids.parse(ln) for ln in
            v.review.read_text(encoding="utf-8").splitlines() if "cb=" in ln]
    assert len(page) == 2
    for row in page:
        twin = hot_ids[item_ids.match_key(row["text"])]
        assert row["cb"] == twin["cb"]
        assert row["opened"] == twin["opened"] == "2026-09-20"

    before = fx.tree(v)
    again = dj.stamp_missing(fx.TODAY, live=True)
    assert fx.tree(v) == before, "a second backfill changes zero bytes"
    assert again["hotcache"] == {"stamped": 0, "adopted": 0}


def test_a_dry_backfill_of_the_vault_writes_nothing_and_still_yields_ids(
        tmp_path, monkeypatch):
    v = fx.make_vault(tmp_path, monkeypatch, hotcache=LEGACY)
    before = fx.tree(v)
    got = dj.stamp_missing(fx.TODAY, live=False)
    assert fx.tree(v) == before
    items = dj.build_open_set(fx.ctx(), hotcache_text=got["text"])
    assert len(items) == 4 and all(i["id"] and i["opened"] for i in items)
