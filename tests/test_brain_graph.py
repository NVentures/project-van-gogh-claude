"""brain_graph: wikilink resolution over a throwaway vault.

The interesting cases are all resolution, not drawing: a link that points at
nothing must be dropped rather than inventing a node, and an ambiguous stem must
resolve the same way on every run regardless of walk order.
"""
import pytest

import brain_graph


@pytest.fixture(autouse=True)
def fresh_cache():
    brain_graph._cache["graph"] = None
    brain_graph._cache["at"] = 0.0


def write(root, rel, text=""):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_plain_link_becomes_an_edge(tmp_path):
    write(tmp_path, "a.md", "links to [[b]]")
    write(tmp_path, "b.md")
    g = brain_graph.build(tmp_path)
    assert {"s": "a.md", "t": "b.md"} in g["edges"]
    assert g["note_count"] == 2


def test_link_to_a_note_that_does_not_exist_is_dropped(tmp_path):
    write(tmp_path, "a.md", "links to [[ghost]]")
    g = brain_graph.build(tmp_path)
    assert g["edges"] == []
    assert [n["id"] for n in g["nodes"]] == ["a.md"]


def test_path_qualified_link_resolves_to_that_exact_note(tmp_path):
    write(tmp_path, "a.md", "see [[people/Jane Roe]]")
    write(tmp_path, "people/Jane Roe.md")
    write(tmp_path, "Jane Roe.md")
    g = brain_graph.build(tmp_path)
    assert {"s": "a.md", "t": "people/Jane Roe.md"} in g["edges"]


def test_ambiguous_stem_prefers_the_linking_note_s_own_folder(tmp_path):
    write(tmp_path, "wiki/a.md", "see [[shared]]")
    write(tmp_path, "wiki/shared.md")
    write(tmp_path, "other/shared.md")
    g = brain_graph.build(tmp_path)
    assert {"s": "wiki/a.md", "t": "wiki/shared.md"} in g["edges"]


def test_alias_and_heading_syntax_still_resolve(tmp_path):
    write(tmp_path, "a.md", "[[b|call it something else]] and [[b#a heading]]")
    write(tmp_path, "b.md")
    g = brain_graph.build(tmp_path)
    assert g["edges"] == [{"s": "a.md", "t": "b.md"}]      # deduped to one edge


def test_self_links_are_not_edges(tmp_path):
    write(tmp_path, "a.md", "[[a]]")
    g = brain_graph.build(tmp_path)
    assert g["edges"] == []


def test_machinery_folders_are_skipped(tmp_path):
    write(tmp_path, ".obsidian/plugins/x.md", "[[a]]")
    write(tmp_path, "van-gogh/logs/run.md", "[[a]]")
    write(tmp_path, "a.md")
    g = brain_graph.build(tmp_path)
    assert [n["id"] for n in g["nodes"]] == ["a.md"]


def test_degree_counts_both_directions(tmp_path):
    write(tmp_path, "hub.md")
    write(tmp_path, "one.md", "[[hub]]")
    write(tmp_path, "two.md", "[[hub]]")
    g = brain_graph.build(tmp_path)
    hub = [n for n in g["nodes"] if n["id"] == "hub.md"][0]
    assert hub["degree"] == 2


def test_missing_vault_reports_instead_of_raising(tmp_path):
    g = brain_graph.build(tmp_path / "nope")
    assert g["error"] == "vault not found"
    assert g["nodes"] == []


def test_neighbors_splits_inbound_from_outbound(tmp_path):
    write(tmp_path, "a.md", "[[b]]")
    write(tmp_path, "b.md")
    write(tmp_path, "c.md", "[[a]]")
    g = brain_graph.build(tmp_path)
    n = brain_graph.neighbors("a.md", g)
    assert n["outbound"] == ["b.md"]
    assert n["inbound"] == ["c.md"]


def test_search_requires_every_term(tmp_path):
    write(tmp_path, "deal notes.md")
    write(tmp_path, "deal.md")
    g = brain_graph.build(tmp_path)
    assert brain_graph.search("deal notes", g) == ["deal notes.md"]
    assert set(brain_graph.search("deal", g)) == {"deal.md", "deal notes.md"}
    assert brain_graph.search("   ", g) == []


def test_edges_are_stable_across_builds(tmp_path):
    write(tmp_path, "a.md", "[[b]] [[c]]")
    write(tmp_path, "b.md")
    write(tmp_path, "c.md")
    assert brain_graph.build(tmp_path)["edges"] == brain_graph.build(tmp_path)["edges"]
