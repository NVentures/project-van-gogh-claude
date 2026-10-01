"""Guard against config.template.json drift.

The committed template must parse and carry every block + obsidian *_relpath
key that config_loader reads, so a fresh `cp config.template.json config.json`
yields a config that satisfies all accessors.
"""
import json
from pathlib import Path

_TEMPLATE = Path(__file__).resolve().parent.parent / "config.template.json"


def _load():
    return json.loads(_TEMPLATE.read_text())


def test_template_parses():
    assert _TEMPLATE.exists()
    cfg = _load()
    assert isinstance(cfg, dict)


def test_top_level_blocks():
    cfg = _load()
    for block in ("user", "accounts", "obsidian", "businesses",
                  "email_filters", "digest", "relationship_radar"):
        assert block in cfg, f"missing top-level block: {block}"


def test_obsidian_relpath_keys():
    # Every *_relpath / heading key config_loader reads must exist so the
    # template stays in sync with the accessors.
    obs = _load()["obsidian"]
    required = {
        "vault_path",
        "hotcache_relpath",
        "sources_relpath",
        "weekly_relpath",
        "entities_relpath",
        "hotcache_action_items_heading",
        "hotcache_active_threads_heading",
        "voice_guide_relpath",
        "tone_profile_relpath",
        "voice_snapshot_relpath",
        "voice_drafts_relpath",
        "relationship_radar_relpath",
    }
    missing = required - set(obs.keys())
    assert not missing, f"obsidian block missing keys: {sorted(missing)}"


def test_notes_shape():
    # The watcher between briefings ships off, and its defaults are the ones
    # the loader falls back to, so the template and the code cannot drift.
    import config_loader
    notes = _load()["notes"]
    assert notes["enabled"] is False
    for key, default in config_loader.NOTE_DEFAULTS.items():
        assert notes[key] == default, key


def test_digest_shape():
    # Opt-in by default, and one cadence entry per briefing digest_send knows.
    digest = _load()["digest"]
    assert digest["enabled"] is False
    for key in ("sender_label", "recipient_email", "briefings"):
        assert key in digest
    assert set(digest["briefings"]) == {
        "morning-coffee", "afternoon-tea", "week", "week-retro"}
    for cadence in digest["briefings"].values():
        assert set(cadence) == {"enabled", "days", "time"}


def test_digest_template_matches_code_defaults():
    # The template's cadences and config_loader's in-code defaults are two
    # copies of the same policy — pin them together so they can't drift.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
    from config_loader import DIGEST_BRIEFING_DEFAULTS
    assert _load()["digest"]["briefings"] == DIGEST_BRIEFING_DEFAULTS


def test_businesses_shape():
    biz = _load()["businesses"]
    assert isinstance(biz, list) and biz
    for b in biz:
        for key in ("tag", "display_name", "project_page", "meeting_route", "keywords"):
            assert key in b, f"business entry missing {key}: {b}"
