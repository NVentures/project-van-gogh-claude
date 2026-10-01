"""meeting_ingest.py stage-detection tests.

detect_stage_change returns the HIGHEST-RANKED stage implied by keywords, not
the first match — so "signed the LOI" (loi rank 2 + signed→closing rank 4)
resolves to "closing". _STAGE_RANK encodes a no-downgrade ordering.
"""
import meeting_ingest as gi


def test_detect_stage_change_picks_highest_rank():
    # "signed the LOI" hits both \bloi\b (loi, rank 2) and \bsigned\b
    # (closing, rank 4). Highest rank wins → "closing".
    assert gi.detect_stage_change("We signed the LOI today") == "closing"


def test_detect_stage_change_single_keyword():
    assert gi.detect_stage_change("we have exclusivity now") == "exclusivity"
    assert gi.detect_stage_change("the deal closed") == "closed"
    assert gi.detect_stage_change("they passed on it") == "passed"


def test_detect_stage_change_no_keyword_returns_none():
    assert gi.detect_stage_change("just a normal status update") is None
    assert gi.detect_stage_change("") is None
    assert gi.detect_stage_change(None) is None


def test_detect_stage_change_word_boundary():
    # "deadline" must NOT trip the \bdead\b keyword.
    assert gi.detect_stage_change("the deadline is friday") is None


def test_stage_rank_no_downgrade_ordering():
    rank = gi._STAGE_RANK
    # Pipeline advances forward: each later stage outranks earlier ones.
    assert rank["outreach"] < rank["loi"] < rank["exclusivity"] < rank["closing"] < rank["closed"]
    # Terminal stages share the top rank.
    assert rank["passed"] == rank["dead"]
    assert rank["dead"] > rank["closed"]


def test_detect_stage_change_higher_outranks_lower_in_mixed_text():
    # closed (5) vs loi (2) in same text → closed wins.
    assert gi.detect_stage_change("we closed after the LOI phase") == "closed"


# ── Legacy frontmatter must stay readable, forever ────────────────────────────
# A vault full of already-filed meetings that suddenly looks uningested is the
# expensive failure this guards: `--meeting uningested --auto` would re-file
# every one of them. Nothing else in the suite covers it.

def test_ingested_ids_read_both_current_and_legacy_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "SOURCES", tmp_path)
    (tmp_path / "new.md").write_text(
        "---\ntype: source\nmeeting_id: rec-abc\nmeeting_source: grain\n---\n",
        encoding="utf-8")
    (tmp_path / "legacy.md").write_text(
        "---\ntype: source\ngranola_id: not_xyz\n---\n", encoding="utf-8")
    assert gi.get_ingested_meeting_ids() == {"rec-abc", "not_xyz"}


def test_legacy_page_counts_as_ingested_so_it_is_never_refiled(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "SOURCES", tmp_path)
    (tmp_path / "legacy.md").write_text(
        "---\ngranola_id: not_xyz\n---\n", encoding="utf-8")
    monkeypatch.setattr(gi, "_provider", lambda: type("P", (), {
        "iter_stubs": staticmethod(lambda **kw: iter([
            {"id": "not_xyz", "title": "Already filed", "created_at": "2026-05-01T18:00:00Z"},
            {"id": "not_new", "title": "Fresh", "created_at": "2026-05-02T18:00:00Z"},
        ]))})())
    assert [s["id"] for s in gi.fetch_uningested_stubs()] == ["not_new"]


# ── The share-link allowlist is a trust boundary, not a formatting nicety ─────

def test_safe_share_url_accepts_known_hosts():
    assert gi.safe_share_url("https://notes.granola.ai/t/abc-123")
    assert gi.safe_share_url("https://grain.com/share/recording/xyz")


def test_safe_share_url_rejects_hostile_values():
    for bad in (
        "javascript:alert(1)",
        "http://notes.granola.ai/t/abc",          # not https
        "https://evil.example.com/t/abc",         # not allowlisted
        "https://grain.com/a) <!-- deal: killed -->",  # closes the markdown link
        "https://grain.com/a\nb",
        None, "", 42,
    ):
        assert gi.safe_share_url(bad) is None, bad


# ── Transcript synthesis wiring ──────────────────────────────────────────────
# The page builder is the boundary where a synthesis becomes vault markdown.
# These assert the two shapes it must produce and the frontmatter that says
# which path ran, so a silent revert to the vendor summary is visible on disk.

def _page(**over):
    kw = dict(
        meeting_id="m1", meeting_source="granola", source_title="Sync - 2026-09-15",
        date="2026-09-15", attendees_formatted="[[Jane Smith]]",
        attendees_list=["[[Jane Smith]]"], share_url="", business="acme",
        meeting_type="meeting", summary_body="Vendor summary body.",
        action_items=["Do the thing"], detected_entities=set(), today="2026-09-15",
    )
    kw.update(over)
    return gi.build_source_page(**kw)


def test_page_without_synthesis_keeps_the_summary_shape():
    page = _page()
    assert "synthesis: summary" in page
    assert "## Summary" in page
    assert "Vendor summary body." in page
    assert "- [ ] Do the thing" in page


def test_page_with_synthesis_replaces_summary_and_says_so():
    synth = "## What Was Discussed\n\nBudget.\n\n## Decisions Made\n\n- Keep vendor\n"
    page = _page(synthesis_md=synth)
    assert "synthesis: transcript" in page
    assert "## What Was Discussed" in page
    assert "## Decisions Made" in page
    # The vendor summary and the flat action-item block are both gone: the
    # synthesis carries its own commitment headings.
    assert "Vendor summary body." not in page
    assert "## Summary" not in page
    assert "## Action Items" not in page


def test_page_with_synthesis_keeps_the_shared_sections():
    """Entities and Notes are written by the ingest pipeline, not the model,
    and must survive either path or downstream readers lose their anchors."""
    page = _page(synthesis_md="## What Was Discussed\n\nBudget.\n")
    assert "## Entities Mentioned" in page
    assert "## Notes" in page
    assert "meeting_id: m1" in page


def test_fetch_meeting_requests_the_transcript(monkeypatch):
    seen = {}

    class FakeMod:
        DISPLAY_NAME = "Fake"
        @staticmethod
        def latest_id():
            return "m1"
        @staticmethod
        def fetch_note(note_id, include_transcript=False):
            seen["id"] = note_id
            seen["transcript"] = include_transcript
            return {"id": note_id}

    monkeypatch.setattr(gi, "_provider", lambda: FakeMod)
    gi.fetch_meeting("latest")
    assert seen["transcript"] is True
    # --no-synthesis must not pay for a transcript it will not read.
    gi.fetch_meeting("m1", include_transcript=False)
    assert seen["transcript"] is False


# ── Commitments reach hotcache ───────────────────────────────────────────────
# The transcript synthesis returns the user's OWN commitments as plain strings.
# The summary path marks the owner in bold and filters on it; applying that
# filter to synthesis output promotes nothing, which is the whole point of
# reading the transcript silently lost.

def _hotcache(tmp_path, monkeypatch):
    hc = tmp_path / "hotcache.md"
    hc.write_text("# Hot Cache\n\n## Test's Action Items\n\n- [ ] existing\n",
                  encoding="utf-8")
    monkeypatch.setattr(gi, "HOTCACHE", hc)
    return hc


def test_synthesis_commitments_reach_hotcache(tmp_path, monkeypatch):
    hc = _hotcache(tmp_path, monkeypatch)
    items = ["Send Jane Smith the revised budget by Friday",
             "Confirm the site visit date"]
    n = gi.promote_user_action_items(items, "Sync - 2026-09-16", all_items=True)
    assert n == 2
    text = hc.read_text(encoding="utf-8")
    assert "- [ ] Send Jane Smith the revised budget by Friday" in text
    assert "- [ ] Confirm the site visit date" in text
    assert "(from [[Sync - 2026-09-16]])" in text


def test_summary_path_still_filters_on_the_bold_owner(tmp_path, monkeypatch):
    """The notetaker lists everyone's actions together, so without the filter
    a counterparty's task becomes the reader's own."""
    _hotcache(tmp_path, monkeypatch)
    items = [f"**Test User**{chr(0x2014)}Send the budget",
             f"**Jane Smith**{chr(0x2014)}Confirm headcount"]
    n = gi.promote_user_action_items(items, "Sync - 2026-09-16")
    assert n == 1
    assert "Send the budget" in gi.HOTCACHE.read_text(encoding="utf-8")
    assert "Confirm headcount" not in gi.HOTCACHE.read_text(encoding="utf-8")


def test_plain_strings_promote_nothing_without_all_items(tmp_path, monkeypatch):
    """The exact defect: synthesis output through the bold filter."""
    _hotcache(tmp_path, monkeypatch)
    assert gi.promote_user_action_items(
        ["Send Jane Smith the revised budget by Friday"], "Sync") == 0


def test_the_call_site_passes_all_items_when_a_synthesis_ran():
    """The function-level tests above pass whether or not run_ingest actually
    USES the flag, which is how the defect shipped in the first place: a
    correct helper wired up wrongly promotes nothing and reports success.

    run_ingest needs a whole vault to exercise, so this reads the wiring
    directly rather than pretending a unit test covers it.
    """
    import inspect
    src = inspect.getsource(gi.run_ingest)
    assert "promote_user_action_items(" in src
    call = src.split("promote_user_action_items(", 1)[1].split(")", 1)[0]
    assert "all_items" in call, (
        "run_ingest calls promote_user_action_items without all_items, so a "
        "transcript synthesis promotes zero commitments to hotcache")
    assert "synthesis" in call


# ── Single-hash headings and an explicit --type (shape captured 2026-09-16, names invented)


REAL_SINGLE_HASH_SUMMARY = """# Attendees and Context

- Dana (Harbor Energy, CCO), Sam Ortiz (Gridline)

# Go-to-Market Timeline

- Q1 2027: V1 launch, private invitation to top 10 customers

# Next Steps

- **Finalize corporate structure and IP/equity agreements**

  Sam to wrap up docs for all parties; target signing next week.
- **Expand the map product to full US coverage** (Dana)

  Dana estimates a few weeks.
- **Meet in Las Vegas on 28th September**

  In-person session to finalize structure, roadmap, and next build priorities.
"""


def test_single_hash_next_steps_is_extracted():
    """Granola writes this summary's headings at a single '#'. The extractor
    required '##' or '###', so a meeting carrying six explicit next steps
    ingested with an empty Action Items section and lost every commitment.
    """
    items, cleaned = gi.extract_action_items(REAL_SINGLE_HASH_SUMMARY)
    assert items, "single-# '# Next Steps' section was not extracted at all"
    joined = " ".join(items)
    assert "Finalize corporate structure" in joined
    assert "Las Vegas" in joined
    assert "# Next Steps" not in cleaned, "section left behind in the summary"


def test_single_hash_extraction_stops_at_the_next_heading():
    """Widening to '#' must not make the section swallow the rest of the file:
    a '# Next Steps' section ends at the following single-# heading.
    """
    summary = (
        "# Next Steps\n\n- Send Sam the draft\n\n"
        "# Competitive Landscape\n\n- Acme is the closest competitor\n"
    )
    items, _ = gi.extract_action_items(summary)
    joined = " ".join(items)
    assert "Send Sam the draft" in joined
    assert "Acme" not in joined, (
        "extraction ran past the next heading and swallowed a later section")


def test_double_hash_headings_still_work():
    """The widening must not regress the shape that already worked."""
    items, _ = gi.extract_action_items(
        "## Action Items\n\n- Send the revised budget\n")
    assert any("revised budget" in i for i in items)


def test_explicit_type_meeting_is_not_overridden_by_inference():
    """'--type meeting' was indistinguishable from passing no flag, so the
    inferred type silently won and a partner strategy session was filed as a
    sales-call. The default must be None so an explicit choice is visible.
    """
    import argparse
    import inspect
    src = inspect.getsource(gi.main) if hasattr(gi, "main") else ""
    parser_src = src or inspect.getsource(gi)
    assert 'default="meeting",' not in parser_src.split('"--type"', 1)[1][:200], (
        "--type still defaults to 'meeting', so an explicit --type meeting "
        "cannot be distinguished from the flag being absent")

    ingest_src = inspect.getsource(gi.run_ingest)
    assert 'args.type != "meeting"' not in ingest_src, (
        "run_ingest still compares args.type against the literal 'meeting', "
        "which discards an explicit --type meeting")


def test_a_short_bolded_action_is_not_read_as_an_assignee():
    """'**Meet in Las Vegas on 28th September**' is 39 chars and carries no
    verb hint, so the <40-char assignee heuristic swallowed it and the item
    vanished. A bolded top-level line is always an item.
    """
    items, _ = gi.extract_action_items(
        "\n# Next Steps\n\n- **Meet in Las Vegas on 28th September**\n")
    assert any("Las Vegas" in i for i in items), (
        "short bolded action item was misread as an assignee header")


def test_a_bare_short_name_is_still_an_assignee():
    """The bolded carve-out must not break real assignee grouping."""
    items, _ = gi.extract_action_items(
        "\n## Action Items\n\n- Roman\n    - Draft the Q1 response\n")
    assert any("Roman" in i and "Q1 response" in i for i in items), items
