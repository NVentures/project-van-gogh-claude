"""Tests for migrate_from_legacy.py — the legacy-clone -> plugin migration.

Everything runs against tmp dirs: VAN_GOGH_STATE_DIR already points at a temp
state dir (conftest), and each test builds a fake legacy clone + vault.
"""
import json

import pytest

import migrate_from_legacy as mig
import user_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Give each test its own empty state dir and no plugin-root fallback, so the
    state .env / vault-pointer never bleed between tests (or from the dev repo)."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(user_state, "_PLUGIN_ROOT", tmp_path / "no-plugin-root")


def _make_clone(tmp_path, *, env=None, vault_pointer=None, show_feeds=None):
    clone = tmp_path / "old-van-gogh"
    (clone / "app").mkdir(parents=True)
    (clone / "app" / "config_loader.py").write_text("# legacy\n", encoding="utf-8")
    if env is not None:
        (clone / ".env").write_text(env, encoding="utf-8")
    if vault_pointer is not None:
        (clone / ".van-gogh-vault").write_text(str(vault_pointer) + "\n", encoding="utf-8")
    if show_feeds is not None:
        body = "SHOW_FEEDS = " + repr(show_feeds) + "\n"
        (clone / "app" / "podcast_transcribe.py").write_text(body, encoding="utf-8")
    return clone


def _make_vault(tmp_path, config=None, memory=None):
    vault = tmp_path / "Vault"
    vg = vault / "van-gogh"
    (vg / "projects" / "Project Van Gogh").mkdir(parents=True)
    if config is not None:
        (vg / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if memory is not None:
        (vg / "projects" / "Project Van Gogh" / "memory.md").write_text(memory, encoding="utf-8")
    return vault


# ── secrets ───────────────────────────────────────────────────────────────────

def test_secrets_copied_into_state_env(tmp_path):
    vault = _make_vault(tmp_path)
    clone = _make_clone(
        tmp_path,
        env="GOOGLE_REFRESH_TOKEN_GMAIL=abc\nGRANOLA_API_KEY=xyz\n# comment\n\n",
        vault_pointer=vault,
    )
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["secrets"]["migrated"] == ["GOOGLE_REFRESH_TOKEN_GMAIL", "GRANOLA_API_KEY"]
    written = mig.parse_env_file(user_state.env_file())
    assert written["GOOGLE_REFRESH_TOKEN_GMAIL"] == "abc"
    assert written["GRANOLA_API_KEY"] == "xyz"


def test_existing_secret_not_clobbered_without_force(tmp_path):
    vault = _make_vault(tmp_path)
    user_state.upsert_env("GRANOLA_API_KEY", "already-here")
    clone = _make_clone(tmp_path, env="GRANOLA_API_KEY=new\n", vault_pointer=vault)
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["secrets"]["skipped_existing"] == ["GRANOLA_API_KEY"]
    assert mig.parse_env_file(user_state.env_file())["GRANOLA_API_KEY"] == "already-here"
    # --force overwrites.
    out2 = mig.run_migration(clone, None, force=True, dry_run=False)
    assert out2["secrets"]["migrated"] == ["GRANOLA_API_KEY"]
    assert mig.parse_env_file(user_state.env_file())["GRANOLA_API_KEY"] == "new"


# ── pointer + vault resolution ────────────────────────────────────────────────

def test_pointer_written_from_legacy_vault_file(tmp_path):
    vault = _make_vault(tmp_path)
    clone = _make_clone(tmp_path, env="", vault_pointer=vault)
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["vault"] == str(vault)
    assert (user_state.state_dir() / "vault-pointer").read_text(encoding="utf-8").strip() == str(vault)


def test_missing_vault_is_an_error(tmp_path):
    clone = _make_clone(tmp_path, env="K=v")  # no .van-gogh-vault, no pointer
    (user_state.state_dir() / "vault-pointer").unlink(missing_ok=True)
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert "error" in out and "vault path" in out["error"]


def test_not_a_clone_is_an_error(tmp_path):
    empty = tmp_path / "random"
    empty.mkdir()
    out = mig.run_migration(empty, None, force=False, dry_run=False)
    assert "error" in out and "does not look like" in out["error"]


# ── config reconcile ──────────────────────────────────────────────────────────

def test_missing_blocks_added_existing_preserved(tmp_path):
    # A legacy config with clients[] but no digest/podcasts blocks.
    legacy_cfg = {
        "user": {"full_name": "J"},
        "clients": [{"tag": "acme", "keywords": ["acme"]}],
        "follow_ups": {"memory_path": "", "horizon_days": 4},
    }
    vault = _make_vault(tmp_path, config=legacy_cfg)
    clone = _make_clone(tmp_path, env="", vault_pointer=vault)
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    added = out["config"]["blocks_added"]
    assert "digest" in added and "podcasts" in added
    saved = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert saved["clients"] == legacy_cfg["clients"]      # preserved verbatim
    assert saved["follow_ups"]["horizon_days"] == 4        # preserved
    assert "feeds" in saved["podcasts"]                    # seeded from template


def test_legacy_show_feeds_ported_into_config(tmp_path):
    vault = _make_vault(tmp_path, config={"user": {}})
    clone = _make_clone(tmp_path, env="", vault_pointer=vault,
                        show_feeds={"currents": "https://feeds.example.com/currents.rss"})
    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["config"]["podcast_feeds_added"] == {"currents": "https://feeds.example.com/currents.rss"}
    saved = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert saved["podcasts"]["feeds"]["currents"] == "https://feeds.example.com/currents.rss"


# ── follow-up ledger ──────────────────────────────────────────────────────────

_LEGACY_LEDGER = """# Memory

## Pending follow-ups (remove when resolved)
- **Deck** — owe Alex the deck; due Mon 2026-07-27. Watch Work Outlook.
- **Billing** — nudge Sam today.

## Other section
stuff
"""


def _seed_legacy_memory(clone, tmp_path, body):
    # Place a MEMORY.md where the resolver's glob fallback will find it: a
    # projects dir whose name ends with the clone's folder name.
    import re
    home_projects = tmp_path / "home" / ".claude" / "projects"
    slug = re.sub(r"[:\\/]", "-", str(clone.resolve()))
    d = home_projects / slug / "memory"
    d.mkdir(parents=True)
    (d / "MEMORY.md").write_text(body, encoding="utf-8")
    return home_projects


def test_ledger_replaces_empty_template_placeholder(tmp_path, monkeypatch):
    placeholder = "# Memory\n\n## Pending follow-ups (remove when resolved)\n_placeholder, no bullets._\n"
    vault = _make_vault(tmp_path, config={"user": {}}, memory=placeholder)
    clone = _make_clone(tmp_path, env="", vault_pointer=vault)
    monkeypatch.setattr(mig.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    _seed_legacy_memory(clone, tmp_path, _LEGACY_LEDGER)

    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["follow_ups"]["status"] == "replaced_placeholder"
    assert out["follow_ups"]["entries"] == 2
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md").read_text(encoding="utf-8")
    assert "owe Alex the deck" in mem
    assert "_placeholder, no bullets._" not in mem


def test_ledger_conflict_when_vault_already_has_entries(tmp_path, monkeypatch):
    vault_mem = "# Memory\n\n## Pending follow-ups\n- **Existing** — keep me. due 2026-08-01\n"
    vault = _make_vault(tmp_path, config={"user": {}}, memory=vault_mem)
    clone = _make_clone(tmp_path, env="", vault_pointer=vault)
    monkeypatch.setattr(mig.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    _seed_legacy_memory(clone, tmp_path, _LEGACY_LEDGER)

    out = mig.run_migration(clone, None, force=False, dry_run=False)
    assert out["follow_ups"]["status"] == "conflict_both_have_entries"
    assert "legacy_section" in out["follow_ups"]
    # Vault memory is left untouched on conflict.
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md").read_text(encoding="utf-8")
    assert "owe Alex the deck" not in mem
    assert "keep me" in mem


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(tmp_path):
    vault = _make_vault(tmp_path, config={"user": {}})
    clone = _make_clone(tmp_path, env="GRANOLA_API_KEY=xyz\n", vault_pointer=vault)
    out = mig.run_migration(clone, None, force=False, dry_run=True)
    assert out["dry_run"] is True
    assert out["secrets"]["migrated"] == ["GRANOLA_API_KEY"]
    # Nothing actually written.
    assert "GRANOLA_API_KEY" not in mig.parse_env_file(user_state.env_file())
    saved = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert "digest" not in saved
