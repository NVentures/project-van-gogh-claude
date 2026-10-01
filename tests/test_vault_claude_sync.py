"""The managed context block in {vault}/CLAUDE.md self-syncs from the plugin.

vault/CLAUDE.md (the template) opens with a marker-delimited "Project Van Gogh"
block that makes vault-rooted Claude Code sessions chief-of-staff sessions.
user_state.sync_claude_block_into() keeps the vault copy's block current with
the template while never touching user content outside the markers; scaffold
and the config_loader import-time hook both route through it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import user_state

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "vault" / "CLAUDE.md"


def _template_block() -> str:
    block = user_state._extract_claude_block(TEMPLATE.read_text(encoding="utf-8"))
    assert block is not None
    return block


def _stale_block_file() -> str:
    return (f"{user_state.CLAUDE_BLOCK_BEGIN} old -->\nold managed text\n"
            f"{user_state.CLAUDE_BLOCK_END}\n\n# My vault\n\n| `#acme` | Acme |\n")


def _run_scaffold(tmp_path, vault):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "vault" / "scaffold.py"), str(vault)],
        capture_output=True, text=True,
        env={**os.environ, "VAN_GOGH_STATE_DIR": str(tmp_path / "state")},
    )


def _prime_install(tmp_path, monkeypatch, vault):
    """Simulate a real install: venv gate passes, pointer names `vault`."""
    monkeypatch.setattr(user_state, "state_dir", lambda: tmp_path)
    (tmp_path / "venv").mkdir(exist_ok=True)
    monkeypatch.setattr(user_state.sys, "prefix", str(tmp_path / "venv"))
    (tmp_path / "vault-pointer").write_text(str(vault) + "\n", encoding="utf-8")


def test_template_has_managed_block_and_memory_import():
    text = TEMPLATE.read_text(encoding="utf-8")
    block = user_state._extract_claude_block(text)
    assert block is not None
    assert text.startswith(user_state.CLAUDE_BLOCK_BEGIN)
    # The memory import must escape the spaces in the path — an unescaped
    # "@van-gogh/.../Project Van Gogh/memory.md" silently fails to load.
    assert "@van-gogh/projects/Project\\ Van\\ Gogh/memory.md" in block
    # The wiki-agent spec stays outside the managed block.
    assert "LLM Wiki Agent" in text.split(user_state.CLAUDE_BLOCK_END)[1]


def test_scaffold_creates_claude_md(tmp_path):
    vault = tmp_path / "vault"
    result = _run_scaffold(tmp_path, vault)
    assert result.returncode == 0, result.stderr
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert user_state._extract_claude_block(text) == _template_block()


def test_scaffold_rerun_refreshes_stale_block(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text(_stale_block_file(), encoding="utf-8")
    result = _run_scaffold(tmp_path, vault)
    assert result.returncode == 0, result.stderr
    assert "updated  CLAUDE.md" in result.stdout
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert user_state._extract_claude_block(text) == _template_block()
    assert text.endswith("| `#acme` | Acme |\n")


def test_sync_noop_when_current(tmp_path):
    target = tmp_path / "CLAUDE.md"
    original = _template_block() + "\n\n# My vault\n\nuser notes\n"
    target.write_text(original, encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is False
    assert target.read_text(encoding="utf-8") == original


def test_sync_replaces_stale_block_preserving_user_content(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(_stale_block_file(), encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is True
    text = target.read_text(encoding="utf-8")
    assert user_state._extract_claude_block(text) == _template_block()
    assert text.endswith("# My vault\n\n| `#acme` | Acme |\n")
    assert "old managed text" not in text


def test_sync_inserts_block_into_markerless_file(tmp_path):
    # Installs that predate the managed block have a CLAUDE.md with no markers.
    target = tmp_path / "CLAUDE.md"
    legacy = "# This vault: Wes Second Brain\n\n| `#acme` | Acme |\n"
    target.write_text(legacy, encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is True
    text = target.read_text(encoding="utf-8")
    assert text.startswith(user_state.CLAUDE_BLOCK_BEGIN)
    assert text.endswith(legacy)


def test_sync_inserts_below_yaml_frontmatter(tmp_path):
    # Obsidian requires frontmatter to stay the first bytes of the file.
    target = tmp_path / "CLAUDE.md"
    target.write_text("---\ntags: [meta]\n---\n# Mine\n", encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is True
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\ntags: [meta]\n---\n")
    assert user_state._extract_claude_block(text) == _template_block()
    assert text.endswith("# Mine\n")


def test_sync_tolerates_bom_and_strips_it(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_bytes("﻿# Mine\n".encode("utf-8"))
    assert user_state.sync_claude_block_into(tmp_path) is True
    text = target.read_text(encoding="utf-8")
    assert "﻿" not in text
    assert text.startswith(user_state.CLAUDE_BLOCK_BEGIN)
    assert text.endswith("# Mine\n")


def test_quoted_marker_in_prose_is_not_a_boundary(tmp_path):
    # User notes that mention the marker mid-line must never define the block
    # span — a bare find() here once swallowed everything down to the real
    # end marker.
    target = tmp_path / "CLAUDE.md"
    prose = (f"# Mine\n\nThe `{user_state.CLAUDE_BLOCK_BEGIN}` marker is "
             "managed.\n\n" + _stale_block_file())
    target.write_text(prose, encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is True
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Mine")
    assert "marker is managed" in text
    assert user_state._extract_claude_block(text) == _template_block()


def test_sync_skips_orphaned_marker_debris(tmp_path, capsys):
    # An end marker without a begin (truncated block) can't be merged safely;
    # prepending would leave duplicate marker lines forever.
    target = tmp_path / "CLAUDE.md"
    broken = f"# Mine\n{user_state.CLAUDE_BLOCK_END}\nleftover\n"
    target.write_text(broken, encoding="utf-8")
    assert user_state.sync_claude_block_into(tmp_path) is False
    assert target.read_text(encoding="utf-8") == broken
    assert "orphaned" in capsys.readouterr().err


def test_sync_skips_missing_file(tmp_path):
    assert user_state.sync_claude_block_into(tmp_path) is False
    assert not (tmp_path / "CLAUDE.md").exists()


def test_sync_returns_false_when_template_lacks_markers(tmp_path, monkeypatch):
    # A future template edit that drops the markers must fail safe (no write),
    # not corrupt the vault file.
    plugin = tmp_path / "plugin"
    (plugin / "vault").mkdir(parents=True)
    (plugin / "vault" / "CLAUDE.md").write_text("# no markers\n", encoding="utf-8")
    monkeypatch.setattr(user_state, "_PLUGIN_ROOT", plugin)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    assert user_state.sync_claude_block_into(vault) is False
    assert (vault / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n"


def test_import_hook_syncs_stale_vault_block(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text(_stale_block_file(), encoding="utf-8")
    _prime_install(tmp_path, monkeypatch, vault)
    user_state.sync_vault_claude_block()
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert user_state._extract_claude_block(text) == _template_block()


def test_import_hook_never_raises_on_missing_vault(tmp_path, monkeypatch):
    _prime_install(tmp_path, monkeypatch, tmp_path / "no-such-vault")
    user_state.sync_vault_claude_block()  # missing vault CLAUDE.md → silent no-op


def test_import_hook_survives_undecodable_vault_file(tmp_path, monkeypatch, capsys):
    # UnicodeDecodeError is a ValueError, not an OSError — the hook's contract
    # is that no failure takes down the calling script's import.
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_bytes(b"\xff\xfe bad bytes")
    _prime_install(tmp_path, monkeypatch, vault)
    user_state.sync_vault_claude_block()
    assert "sync skipped" in capsys.readouterr().err


def _write_config(vault, full_name="Ada Lovelace", businesses=None, heading="Ada's Action Items"):
    (vault / "van-gogh").mkdir(parents=True, exist_ok=True)
    if businesses is None:
        businesses = [{"tag": "acme", "display_name": "Acme Corp"},
                      {"tag": "personal", "display_name": "Personal"}]
    config = {
        "user": {"full_name": full_name},
        "businesses": businesses,
        "obsidian": {"hotcache_relpath": "wiki/hotcache.md",
                     "hotcache_action_items_heading": heading},
    }
    (vault / "van-gogh" / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _pristine_vault(tmp_path):
    """A vault whose CLAUDE.md is the untouched template + a stub hotcache."""
    vault = tmp_path / "vault"
    (vault / "wiki").mkdir(parents=True)
    (vault / "CLAUDE.md").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    (vault / "wiki" / "hotcache.md").write_text(
        "# Hot Cache\n\n## [Your Name]'s Action Items\n\n- [ ] First action item\n",
        encoding="utf-8")
    return vault


def test_personalize_fills_pristine_placeholders(tmp_path):
    vault = _pristine_vault(tmp_path)
    _write_config(vault)
    assert user_state.personalize_claude_scaffold(vault) is True
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# This vault: Ada Lovelace Second Brain" in text
    assert "| `#acme` | Acme Corp |\n| `#personal` | Personal |" in text
    assert "#venture-1" not in text
    assert "[Your Name]" not in text
    assert "## Ada's Action Items" in text
    hotcache = (vault / "wiki" / "hotcache.md").read_text(encoding="utf-8")
    assert "## Ada's Action Items" in hotcache
    # Idempotent: a second run finds nothing pristine left to replace.
    assert user_state.personalize_claude_scaffold(vault) is False


def test_personalize_never_touches_user_edits(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    edited = ("# This vault: Wes Second Brain\n\n"
              "| `#mine` | Mine |\n\n## Wes's Action Items\n")
    (vault / "CLAUDE.md").write_text(edited, encoding="utf-8")
    _write_config(vault)
    assert user_state.personalize_claude_scaffold(vault) is False
    assert (vault / "CLAUDE.md").read_text(encoding="utf-8") == edited


def test_personalize_noop_without_config(tmp_path):
    vault = _pristine_vault(tmp_path)
    assert user_state.personalize_claude_scaffold(vault) is False
    assert "[Your Name]" in (vault / "CLAUDE.md").read_text(encoding="utf-8")


def test_personalize_skips_config_template_placeholders(tmp_path):
    # A config.json still carrying the config template's own placeholder
    # values must not be copied into the vault CLAUDE.md.
    vault = _pristine_vault(tmp_path)
    _write_config(vault, full_name="Your Name", businesses=[],
                  heading="Your Action Items")
    assert user_state.personalize_claude_scaffold(vault) is False
    assert "[Your Name]" in (vault / "CLAUDE.md").read_text(encoding="utf-8")


def test_import_hook_personalizes_scaffold(tmp_path, monkeypatch):
    vault = _pristine_vault(tmp_path)
    _write_config(vault)
    _prime_install(tmp_path, monkeypatch, vault)
    user_state.sync_vault_claude_block()
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "| `#acme` | Acme Corp |" in text
    assert "[Your Name]" not in text


def test_scaffold_personalizes_fresh_claude_md(tmp_path):
    vault = tmp_path / "vault"
    _write_config(vault)
    result = _run_scaffold(tmp_path, vault)
    assert result.returncode == 0, result.stderr
    text = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# This vault: Ada Lovelace Second Brain" in text
    assert "| `#acme` | Acme Corp |" in text
    hotcache = (vault / "wiki" / "hotcache.md").read_text(encoding="utf-8")
    assert "## Ada's Action Items" in hotcache


def test_import_hook_ignores_blank_or_relative_pointer(tmp_path, monkeypatch):
    # A blank pointer once resolved to Path('.') and wrote a CLAUDE.md into
    # whatever repo the calling script ran from.
    _prime_install(tmp_path, monkeypatch, tmp_path)
    cwd_claude = Path.cwd() / "CLAUDE.md"
    before = cwd_claude.read_text(encoding="utf-8") if cwd_claude.exists() else None
    for bad in ("", "   \n", "relative/vault"):
        (tmp_path / "vault-pointer").write_text(bad, encoding="utf-8")
        user_state.sync_vault_claude_block()
    after = cwd_claude.read_text(encoding="utf-8") if cwd_claude.exists() else None
    assert before == after
