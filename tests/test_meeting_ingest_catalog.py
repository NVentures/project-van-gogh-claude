"""The monthly catalog is where a new source page gets listed.

index.md is a curated hub whose headings were restyled ("● Source
catalogs"), so the substring "Sources" stopped matching there. The append
returned the text unchanged and the caller printed a checkmark regardless,
so every ingest went unlisted with no signal at all.
"""
import importlib


def _mod():
    return importlib.import_module("meeting_ingest")


def test_restyled_index_heading_does_not_match_sources():
    """The live index heading is 'Source catalogs', singular, so no match."""
    mi = _mod()
    index_text = (
        "# Wiki Index\n\n"
        '## <span style="color:#FFD93D">● Source catalogs</span>\n\n'
        "- [[catalog-2026-07]] : this month's sources\n"
    )
    out = mi.append_under_heading(index_text, "Sources", "- [[New]] : x `#p`")
    assert out == index_text, "expected no match against '● Source catalogs'"


def test_update_index_writes_the_month_catalog(tmp_path, monkeypatch):
    mi = _mod()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    catalog = sources / "catalog-2026-09.md"
    catalog.write_text(
        "---\ntype: catalog\n---\n\n# Sources: 2026-09\n\n## Sources\n"
        "- [[Old Meeting - 2026-09-01]] : older entry `#solar`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mi, "SOURCES", sources)
    monkeypatch.setattr(mi, "INDEX", tmp_path / "wiki" / "index.md")

    problem = mi.update_index(
        "Founders Catch-up - 2026-09-18", "Reviewed the counteroffer",
        "solar", [], "2026-09-18",
    )

    assert problem == ""
    body = catalog.read_text(encoding="utf-8")
    assert "- [[Founders Catch-up - 2026-09-18]] : Reviewed the counteroffer `#solar`" in body
    assert "- [[Old Meeting - 2026-09-01]]" in body, "must not clobber existing entries"


def test_update_index_creates_the_catalog_on_month_rollover(tmp_path, monkeypatch):
    """Nothing else creates a month's catalog, so the first ingest must.

    Every fixture here used to be September with the file already present,
    so the first of October filed ten pages and listed none of them.
    """
    mi = _mod()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    september = sources / "catalog-2026-09.md"
    september.write_text("# Sources: 2026-09\n\n## Sources\n- [[Old]] : x `#p`\n", encoding="utf-8")
    monkeypatch.setattr(mi, "SOURCES", sources)
    monkeypatch.setattr(mi, "INDEX", tmp_path / "wiki" / "index.md")

    # A September meeting filed on the first of October lands in October's.
    problem = mi.update_index("T - 2026-09-30", "s", "solar", [], "2026-10-01")

    assert problem == ""
    october = sources / "catalog-2026-10.md"
    body = october.read_text(encoding="utf-8")
    assert body.startswith("---\ntype: catalog\ntitle: Sources 2026-10\n---\n")
    assert body.count("## Sources\n") == 1
    assert "- [[T - 2026-09-30]] : s `#solar`" in body
    assert "[[T - 2026-09-30]]" not in september.read_text(encoding="utf-8")

    # The second ingest of the month appends, it does not start the file over.
    assert mi.update_index("U - 2026-10-01", "t", "solar", [], "2026-10-01") == ""
    body = october.read_text(encoding="utf-8")
    assert "[[T - 2026-09-30]]" in body and "[[U - 2026-10-01]]" in body
    assert body.count("## Sources\n") == 1


def test_update_index_reports_a_missing_sources_folder(tmp_path, monkeypatch):
    """A failed listing must return a reason, never a silent success."""
    mi = _mod()
    sources = tmp_path / "wiki" / "sources"
    monkeypatch.setattr(mi, "SOURCES", sources)
    monkeypatch.setattr(mi, "INDEX", tmp_path / "wiki" / "index.md")

    problem = mi.update_index("T - 2026-09-18", "s", "solar", [], "2026-09-18")
    assert "catalog-2026-09.md" in problem
    assert not sources.exists()


def test_update_index_reports_a_catalog_with_no_sources_heading(tmp_path, monkeypatch):
    mi = _mod()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "catalog-2026-09.md").write_text("# Sources: 2026-09\n", encoding="utf-8")
    monkeypatch.setattr(mi, "SOURCES", sources)
    monkeypatch.setattr(mi, "INDEX", tmp_path / "wiki" / "index.md")

    problem = mi.update_index("T - 2026-09-18", "s", "solar", [], "2026-09-18")
    assert "Sources" in problem


def test_update_index_is_idempotent(tmp_path, monkeypatch):
    mi = _mod()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    catalog = sources / "catalog-2026-09.md"
    catalog.write_text("# Sources: 2026-09\n\n## Sources\n", encoding="utf-8")
    monkeypatch.setattr(mi, "SOURCES", sources)
    monkeypatch.setattr(mi, "INDEX", tmp_path / "wiki" / "index.md")

    for _ in range(2):
        assert mi.update_index("T - 2026-09-18", "s", "solar", [], "2026-09-18") == ""
    assert catalog.read_text(encoding="utf-8").count("[[T - 2026-09-18]]") == 1
