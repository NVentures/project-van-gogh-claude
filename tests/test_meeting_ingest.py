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
