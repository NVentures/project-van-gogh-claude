"""context_pack: the evidence a draft is written from.

The interesting cases are all about what must NOT reach a drafter. A pack that
quietly returns a hub note, a commitment with no provenance, or a non-empty
shell for a stranger is worse than a pack that returns nothing, because the
caller's fallback only fires on a truthful empty.
"""
import pytest

import brain_graph
import config_loader
import context_pack


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A throwaway vault, wired into the config accessors context_pack reads."""
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    fake_vault = lambda subpath="": (tmp_path / subpath if subpath else tmp_path)
    monkeypatch.setattr(config_loader, "vault", fake_vault)
    monkeypatch.setattr(brain_graph, "vault", fake_vault)
    for name, rel in (("entities_dir", "wiki/entities"), ("sources_dir", "wiki/sources")):
        monkeypatch.setattr(context_pack, name, (lambda r: (lambda: tmp_path / r))(rel))
    monkeypatch.setattr(context_pack, "hotcache_path", lambda: tmp_path / "wiki" / "hotcache.md")
    brain_graph._cache["graph"] = None
    brain_graph._cache["at"] = 0.0
    return tmp_path


def write(root, rel, text=""):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── The empty contract ────────────────────────────────────────────────────────

def test_a_stranger_yields_an_empty_pack(vault):
    pack = context_pack.pack_for("Nobody Whatsoever")
    assert context_pack.is_empty(pack)
    assert pack["sources"] == []


def test_a_blank_name_never_searches(vault):
    write(vault, "wiki/entities/Someone.md", "a page")
    pack = context_pack.pack_for("")
    assert context_pack.is_empty(pack)
    assert pack["entity_page"] is None


def test_one_entity_page_alone_makes_the_pack_non_empty(vault):
    write(vault, "wiki/entities/Dana Reyes.md", "VP at Acme")
    pack = context_pack.pack_for("Dana Reyes")
    assert not context_pack.is_empty(pack)
    assert pack["entity_page"].endswith("Dana Reyes.md")
    assert pack["entity_page"] in pack["sources"]


# ── Provenance ────────────────────────────────────────────────────────────────

def test_every_open_commitment_carries_the_meeting_it_was_made_on(vault):
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test to send Dana the revised term sheet\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert pack["open_commitments"], "the action item should have been picked up"
    for c in pack["open_commitments"]:
        assert c["item"]
        assert c["source"].endswith("Call with Dana Reyes.md")


def test_every_pack_entry_is_traceable_to_a_file(vault):
    write(vault, "wiki/entities/Dana Reyes.md", "VP at Acme")
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test to send Dana the deck\n")
    write(vault, "wiki/hotcache.md", "### Acme deal\nDana Reyes wants pricing.\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert pack["sources"], "a non-empty pack must name its sources"
    for path in pack["sources"]:
        assert (vault / path.replace(str(vault) + "/", "")).exists() or path.startswith(str(vault))
    for entry in pack["deal_context"]:
        assert entry["source"].endswith("hotcache.md")


def test_a_resolved_action_item_is_not_an_open_commitment(vault):
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- [x] Test already sent Dana the deck\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert pack["open_commitments"] == []


# ── Graph neighbourhood ───────────────────────────────────────────────────────

def test_the_neighbourhood_reaches_notes_the_thread_never_mentions(vault):
    write(vault, "wiki/entities/Dana Reyes.md", "Works on [[Acme Substation]]")
    write(vault, "wiki/entities/Acme Substation.md", "A 40 MW interconnect")
    pack = context_pack.pack_for("Dana Reyes")
    labels = [n["label"] for n in pack["graph"]["neighbors"]]
    assert "Acme Substation" in labels


def test_a_hub_note_is_dropped_from_the_neighbourhood(vault):
    # The hub is linked by enough notes to pass HUB_DEGREE, so it distinguishes
    # nothing about Dana and must not crowd out the note that does.
    write(vault, "wiki/entities/Dana Reyes.md", "At [[Hub]] and [[Acme Substation]]")
    write(vault, "wiki/entities/Acme Substation.md", "A 40 MW interconnect")
    write(vault, "wiki/entities/Hub.md", "index")
    for i in range(60):  # literal, not HUB_DEGREE-derived: see the cap test below
        write(vault, f"wiki/sources/note-{i}.md", "see [[Hub]]")
    assert context_pack.HUB_DEGREE < 60, "fixture must sit past the cap, not on it"
    pack = context_pack.pack_for("Dana Reyes")
    labels = [n["label"] for n in pack["graph"]["neighbors"]]
    assert "Hub" not in labels, "a note everything links to says nothing about anyone"
    assert "Acme Substation" in labels


def test_the_neighbourhood_is_capped(vault):
    # 30 is a literal so that mutating NEIGHBOR_LIMIT fails this test rather
    # than resizing the fixture along with it.
    assert context_pack.NEIGHBOR_LIMIT < 30, "fixture must sit past the cap, not on it"
    links = " ".join(f"[[n{i}]]" for i in range(30))
    write(vault, "wiki/entities/Dana Reyes.md", links)
    for i in range(30):
        write(vault, f"wiki/entities/n{i}.md", "leaf")
    pack = context_pack.pack_for("Dana Reyes")
    assert len(pack["graph"]["neighbors"]) == context_pack.NEIGHBOR_LIMIT
    assert len(pack["graph"]["neighbors"]) < 30


def test_a_counterparty_with_no_note_returns_no_neighbourhood(vault):
    write(vault, "wiki/entities/Someone Else.md", "unrelated")
    assert context_pack.graph_neighborhood("Nobody Whatsoever") == {"node": None, "neighbors": []}


# ── Extraction parity ─────────────────────────────────────────────────────────

def test_meeting_prep_uses_the_same_functions(vault):
    import meeting_prep
    assert meeting_prep.find_entity_page is context_pack.find_entity_page
    assert meeting_prep.find_meeting_sources is context_pack.find_meeting_sources
    assert meeting_prep.extract_action_items is context_pack.extract_action_items
    assert meeting_prep.extract_hotcache_snippets is context_pack.extract_hotcache_snippets


# ── The vault's machine marker ────────────────────────────────────────────────

MARKER = '<!-- ai: dir={d} deal="" cb=cb-1234 -->'


def test_the_machine_marker_never_reaches_the_pack(vault):
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test to send Dana the deck " + MARKER.format(d="by_me") + "\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert pack["open_commitments"], "a by_me item is the user's own commitment"
    for c in pack["open_commitments"]:
        assert "<!--" not in c["item"], "raw metadata must never be quotable into an email"
        assert "cb-1234" not in c["item"]
        assert "dir=" not in c["item"]
    for m in pack["meetings"]:
        for item in m["action_items"]:
            assert "<!--" not in item


def test_someone_elses_commitment_is_not_the_users_to_owe(vault):
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n"
          "- Test to send Dana the deck " + MARKER.format(d="by_me") + "\n"
          "- Dana to send Test the redlines, cc Test " + MARKER.format(d="to_me") + "\n")
    pack = context_pack.pack_for("Dana Reyes")
    texts = " ".join(c["item"] for c in pack["open_commitments"])
    assert "the deck" in texts
    assert "redlines" not in texts, "a to_me item is owed TO the user, not BY them"


def test_a_third_party_commitment_is_excluded_too(vault):
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test and Dana wait on legal " + MARKER.format(d="third_party") + "\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert pack["open_commitments"] == []


def test_an_unmarked_item_keeps_the_benefit_of_the_doubt(vault):
    # The marker is a recent convention; older notes carry none. Filtering on
    # its absence would drop real commitments silently.
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test to send Dana the old deck\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert len(pack["open_commitments"]) == 1


def test_owner_is_read_from_the_marker(vault):
    assert context_pack.action_item_owner("- x " + MARKER.format(d="by_me")) == "by_me"
    assert context_pack.action_item_owner("- x " + MARKER.format(d="to_me")) == "to_me"
    assert context_pack.action_item_owner("- x " + MARKER.format(d="third_party")) == "third_party"
    assert context_pack.action_item_owner("- x with no marker at all") == ""


def test_no_section_of_the_pack_leaks_a_marker(vault):
    # Found live: stripping the action-item path left hotcache untouched and 768
    # markers reached the pack. Assert over the WHOLE payload so a section added
    # later inherits the guarantee rather than re-learning it.
    import json
    write(vault, "wiki/entities/Dana Reyes.md", "VP at Acme " + MARKER.format(d="by_me"))
    write(vault, "wiki/sources/Call with Dana Reyes.md",
          "## Action Items\n- Test to send Dana the deck " + MARKER.format(d="by_me") + "\n")
    # All five families the real vault actually uses. The live leak was `deal:`,
    # found only after `ai:` was fixed, so a fixture carrying one family cannot
    # prove the strip.
    write(vault, "wiki/hotcache.md",
          "### Acme deal\n"
          "<!-- deal: stage=live last_contact=2026-09-04 -->\n"
          "<!-- synth: v=2 -->\n"
          "<!-- ae: x=1 -->\n"
          "<!-- tasks: n=3 -->\n"
          "- Dana Reyes wants pricing " + MARKER.format(d="by_me") + "\n")
    pack = context_pack.pack_for("Dana Reyes")
    assert not context_pack.is_empty(pack), "fixture must exercise every section"
    assert pack["deal_context"], "the hotcache section is the one that leaked"
    payload = json.dumps(pack)
    assert "<!--" not in payload
    assert "cb-1234" not in payload
    assert "dir=" not in payload
    assert "stage=live" not in payload
    # The prose around the stripped markers must survive.
    assert "wants pricing" in payload
