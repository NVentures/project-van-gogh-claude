"""An entity page's `business:` may be a bare tag or a YAML list.

The old reader captured the first whitespace-free run after `business:`, so a
page written `business: [zeus]` inferred the tag `[zeus]`, brackets and all.
That is not one of the configured tags, so the dry-run told the user to pass
`--business [zeus]` and an unattended run would have filed the page under a
business that does not exist.
"""
import meeting_ingest


def _page(tmp_path, monkeypatch, frontmatter_line):
    monkeypatch.setattr(meeting_ingest, "PROJECTS", tmp_path)
    (tmp_path / "DeepGrid.md").write_text(
        f"---\ntype: project\n{frontmatter_line}\n---\n\nbody\n", encoding="utf-8")


def test_bare_tag(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, "business: zeus")
    assert meeting_ingest._business_tag_from_entity("DeepGrid", "project") == "zeus"


def test_flow_list_takes_first_tag(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, "business: [zeus]")
    assert meeting_ingest._business_tag_from_entity("DeepGrid", "project") == "zeus"


def test_flow_list_with_several_tags(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, "business: [ zeus, solar ]")
    assert meeting_ingest._business_tag_from_entity("DeepGrid", "project") == "zeus"


def test_quoted_tag(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, 'business: ["zeus"]')
    assert meeting_ingest._business_tag_from_entity("DeepGrid", "project") == "zeus"


def test_empty_list_is_none(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, "business: []")
    assert meeting_ingest._business_tag_from_entity("DeepGrid", "project") is None


def test_inference_votes_on_clean_tags(tmp_path, monkeypatch):
    _page(tmp_path, monkeypatch, "business: [zeus]")
    got = meeting_ingest.infer_business_tag({("DeepGrid", "project")})
    assert got == "zeus"
