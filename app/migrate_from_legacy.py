"""
migrate_from_legacy.py — migrate a legacy (pre-plugin) Project Van Gogh install
into the layout the current plugin reads.

The legacy repo (the standalone `git clone` predating the plugin) keeps its
machine state at the *repo root* and resolves some state from Claude Code's
per-project memory:

    <legacy-clone>/.env            OAuth refresh tokens + API keys
    <legacy-clone>/.van-gogh-vault pointer file with the vault's absolute path
    <legacy-clone>/.venv/          uv-managed venv (NOT reused)
    ~/.claude/projects/<slug>/memory/MEMORY.md   "## Pending follow-ups" ledger
    {vault}/van-gogh/…             config.json, briefings, logs (already vault-side)

The plugin instead reads machine state from `~/.config/van-gogh/` and the
follow-up ledger from the vault workspace memory. The plugin's own
self-migration only picks up legacy files sitting at the *plugin cache* root, so
an external clone is never migrated automatically — that gap is what this script
closes.

What it does (idempotent, and NON-destructive — the legacy clone is only read,
never modified or deleted):
  1. copies each key from the legacy repo-root `.env` into `~/.config/van-gogh/.env`
  2. writes the vault pointer to `~/.config/van-gogh/vault-pointer`
  3. copies any repo-root config/briefings/logs leftovers into `{vault}/van-gogh/`
     (only when the vault lacks them) and seeds the workspace memory
  4. adds any missing top-level config blocks (digest, podcasts, …) from the
     current template, preserving every existing value
  5. moves the "## Pending follow-ups" ledger from the legacy per-project
     MEMORY.md into the vault workspace memory (where the plugin looks)
  6. ports the legacy hardcoded podcast SHOW_FEEDS into `podcasts.feeds`

Emits a JSON summary to stdout; the /van-gogh:migrate-from-legacy-van-gogh skill
renders it and handles the few cases that need human judgment.

    python app/migrate_from_legacy.py --legacy-clone "/path/to/old/project-van-gogh"
    python app/migrate_from_legacy.py --legacy-clone "…" --vault "/path/to/Vault" --dry-run
"""

import argparse
import ast
import copy
import json
import re
import shutil
import sys
from pathlib import Path

import user_state
from migrate_to_vault import _MIGRATE_NAMES

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_RELPATH = Path("projects") / "Project Van Gogh" / "memory.md"

# The "## Pending follow-ups" section: heading through the next "## " or EOF.
_FOLLOWUPS_RE = re.compile(r"(?im)^##[ \t]+Pending follow-ups.*?(?=^##[ \t]|\Z)", re.DOTALL)
# A real ledger entry is a top-level "- " bullet (the template placeholder has none).
_BULLET_RE = re.compile(r"(?m)^-[ \t]+\S")


# ── .env secrets ──────────────────────────────────────────────────────────────

def parse_env_file(path: Path) -> dict:
    """Parse a KEY=VALUE `.env` (utf-8-sig, first `=` splits, skip blanks/#)."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def migrate_secrets(legacy_clone: Path, force: bool, dry_run: bool) -> dict:
    """Copy legacy repo-root `.env` keys into the state-dir `.env`."""
    legacy_env = legacy_clone / ".env"
    incoming = parse_env_file(legacy_env)
    existing = parse_env_file(user_state.env_file())
    migrated, skipped = [], []
    for key, value in incoming.items():
        if key in existing and not force:
            skipped.append(key)
            continue
        if not dry_run:
            user_state.upsert_env(key, value)
        migrated.append(key)
    return {
        "source": str(legacy_env) if legacy_env.exists() else None,
        "migrated": sorted(migrated),
        "skipped_existing": sorted(skipped),
    }


# ── vault pointer + repo-root leftovers ───────────────────────────────────────

def resolve_vault(explicit: str | None, legacy_clone: Path) -> Path | None:
    """Vault path: explicit flag > legacy `.van-gogh-vault` > current pointer."""
    if explicit:
        return Path(explicit).expanduser()
    legacy_pointer = legacy_clone / ".van-gogh-vault"
    if legacy_pointer.exists():
        text = legacy_pointer.read_text(encoding="utf-8-sig").strip()
        if text:
            return Path(text).expanduser()
    current = user_state.pointer_file()
    if current.exists():
        text = current.read_text(encoding="utf-8-sig").strip()
        if text:
            return Path(text).expanduser()
    return None


def write_pointer(vault: Path, dry_run: bool) -> str:
    pointer = user_state.state_dir() / "vault-pointer"
    if not dry_run:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(vault) + "\n", encoding="utf-8", newline="\n")
    return str(pointer)


def consolidate_vault_state(legacy_clone: Path, vault: Path, dry_run: bool) -> list:
    """Copy repo-root state leftovers into {vault}/van-gogh/ (copy, never move).

    Legacy installs keep config/briefings in the vault already, so this is
    usually a no-op; it only rescues a repo-root copy the vault is missing.
    """
    dst_root = vault / "van-gogh"
    summary = []
    if not dry_run:
        dst_root.mkdir(parents=True, exist_ok=True)
    for name in _MIGRATE_NAMES:
        src = legacy_clone / name
        dst = dst_root / name
        if not src.exists():
            continue
        if dst.exists():
            summary.append(f"kept vault {name} (legacy repo-root copy left in place)")
            continue
        if not dry_run:
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        summary.append(f"copied {name} -> van-gogh/{name}")

    memory_dst = dst_root / _MEMORY_RELPATH
    template = _PLUGIN_ROOT / "memory.template.md"
    if not memory_dst.exists() and template.exists():
        if not dry_run:
            memory_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template, memory_dst)
        summary.append(f"seeded {_MEMORY_RELPATH.as_posix()} from template")
    return summary


# ── config.json reconcile + podcast feeds ─────────────────────────────────────

def _template_config() -> dict:
    path = _PLUGIN_ROOT / "config.template.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _legacy_show_feeds(legacy_clone: Path) -> dict:
    """Extract the hardcoded SHOW_FEEDS dict from the legacy podcast script.

    The legacy build baked the podcast feed registry into
    app/podcast_transcribe.py; the plugin reads it from config.podcasts.feeds.
    Parse it with ast (never exec the legacy source).
    """
    src = legacy_clone / "app" / "podcast_transcribe.py"
    if not src.exists():
        return {}
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError):
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SHOW_FEEDS" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return {}
            if isinstance(value, dict):
                return {str(k): str(v) for k, v in value.items() if isinstance(v, str)}
    return {}


def reconcile_config(vault: Path, legacy_clone: Path, dry_run: bool) -> dict:
    """Add missing top-level blocks + legacy podcast feeds to the vault config."""
    config_path = vault / "van-gogh" / "config.json"
    if not config_path.exists():
        return {"config_path": str(config_path), "found": False,
                "blocks_added": [], "podcast_feeds_added": {}}

    config = json.loads(config_path.read_text(encoding="utf-8"))
    template = _template_config()
    blocks_added = []
    for key, default in template.items():
        if key not in config:
            config[key] = copy.deepcopy(default)
            blocks_added.append(key)

    feeds = _legacy_show_feeds(legacy_clone)
    podcasts = config.setdefault("podcasts", {})
    existing_feeds = podcasts.setdefault("feeds", {})
    feeds_added = {}
    for key, url in feeds.items():
        if key not in existing_feeds:
            existing_feeds[key] = url
            feeds_added[key] = url

    if not dry_run and (blocks_added or feeds_added):
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8", newline="\n")
    return {"config_path": str(config_path), "found": True,
            "blocks_added": blocks_added, "podcast_feeds_added": feeds_added}


# ── follow-up ledger ──────────────────────────────────────────────────────────

def _legacy_memory_path(legacy_clone: Path) -> Path | None:
    """Best-effort location of the legacy per-project MEMORY.md ledger.

    Mirrors the legacy resolver: Claude Code names the project dir by replacing
    `:` `\\` `/` in the clone's absolute path with `-`, plus a glob fallback on
    a dir whose name ends with the clone folder name.
    """
    projects = Path.home() / ".claude" / "projects"
    slug = re.sub(r"[:\\/]", "-", str(legacy_clone))
    candidate = projects / slug / "memory" / "MEMORY.md"
    if candidate.exists():
        return candidate
    base = legacy_clone.name
    if projects.exists():
        for d in sorted(projects.glob("*")):
            m = d / "memory" / "MEMORY.md"
            if d.is_dir() and d.name.endswith(base) and m.exists():
                return m
    return None


def _extract_followups(text: str) -> str | None:
    m = _FOLLOWUPS_RE.search(text)
    return m.group(0).rstrip() if m else None


def migrate_follow_up_ledger(legacy_clone: Path, vault: Path, dry_run: bool) -> dict:
    """Move the "## Pending follow-ups" ledger into the vault workspace memory."""
    result = {"legacy_memory": None, "status": "no_legacy_ledger", "entries": 0}
    legacy_mem = _legacy_memory_path(legacy_clone)
    if not legacy_mem:
        return result
    result["legacy_memory"] = str(legacy_mem)
    legacy_section = _extract_followups(legacy_mem.read_text(encoding="utf-8-sig"))
    if not legacy_section or not _BULLET_RE.search(legacy_section):
        result["status"] = "no_legacy_ledger"  # section absent or only the placeholder
        return result
    result["entries"] = len(_BULLET_RE.findall(legacy_section))

    vault_mem = vault / "van-gogh" / _MEMORY_RELPATH
    vault_text = vault_mem.read_text(encoding="utf-8-sig") if vault_mem.exists() else ""
    vault_section = _extract_followups(vault_text)

    if vault_section and _BULLET_RE.search(vault_section):
        # The vault ledger already has real entries — a merge needs judgment.
        result["status"] = "conflict_both_have_entries"
        result["legacy_section"] = legacy_section
        return result

    new_block = legacy_section.rstrip() + "\n"
    if vault_section is not None:
        # Replace the empty template placeholder section with the real ledger.
        new_text = _FOLLOWUPS_RE.sub(lambda _m: new_block, vault_text, count=1)
        result["status"] = "replaced_placeholder"
    elif vault_text.strip():
        new_text = vault_text.rstrip() + "\n\n" + new_block
        result["status"] = "appended"
    else:
        new_text = new_block
        result["status"] = "created"

    if not dry_run:
        vault_mem.parent.mkdir(parents=True, exist_ok=True)
        vault_mem.write_text(new_text, encoding="utf-8", newline="\n")
    return result


# ── orchestration ─────────────────────────────────────────────────────────────

def run_migration(legacy_clone: Path, vault_arg: str | None,
                  force: bool, dry_run: bool) -> dict:
    legacy_clone = Path(legacy_clone).expanduser().resolve()
    if not legacy_clone.exists():
        return {"error": f"legacy clone not found: {legacy_clone}"}
    looks_legacy = (legacy_clone / "app" / "config_loader.py").exists() or \
                   (legacy_clone / ".env").exists() or \
                   (legacy_clone / ".van-gogh-vault").exists()
    if not looks_legacy:
        return {"error": f"{legacy_clone} does not look like a Van Gogh clone "
                         "(no app/config_loader.py, .env, or .van-gogh-vault)"}

    vault = resolve_vault(vault_arg, legacy_clone)
    if vault is None:
        return {"error": "could not determine the vault path — pass --vault "
                         "(no .van-gogh-vault in the clone and no existing pointer)"}

    secrets = migrate_secrets(legacy_clone, force, dry_run)
    pointer = write_pointer(vault, dry_run)
    vault_state = consolidate_vault_state(legacy_clone, vault, dry_run)
    config = reconcile_config(vault, legacy_clone, dry_run)
    ledger = migrate_follow_up_ledger(legacy_clone, vault, dry_run)

    manual = [
        "Verify OAuth still works by running a briefing (e.g. /van-gogh:morning-coffee). "
        "If a token was rejected, re-connect with /van-gogh:add-account.",
        "Turn on marketplace auto-update for the van-gogh plugin (/plugin -> Marketplaces) "
        "so updates land automatically — the legacy /update-van-gogh command is gone.",
        "Use the /van-gogh:<name> command names now (e.g. /van-gogh:five-fifteen), "
        "not the bare /five-fifteen form.",
        "Once briefings run cleanly, the legacy clone can be deleted — this "
        "migration only read from it, nothing was moved out of it.",
    ]
    if ledger.get("status") == "conflict_both_have_entries":
        manual.insert(0, "The vault memory already has a Pending follow-ups ledger with "
                         "entries; the legacy ledger was left unmerged — reconcile the two "
                         "by hand (see follow_ups.legacy_section in this report).")

    return {
        "dry_run": dry_run,
        "legacy_clone": str(legacy_clone),
        "vault": str(vault),
        "secrets": secrets,
        "pointer": pointer,
        "vault_state": vault_state,
        "config": config,
        "follow_ups": ledger,
        "manual_next_steps": manual,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate a legacy Van Gogh install into the plugin layout.")
    parser.add_argument("--legacy-clone", required=True,
                        help="Path to the old standalone project-van-gogh checkout.")
    parser.add_argument("--vault", default=None,
                        help="Vault path (default: read the clone's .van-gogh-vault, else the current pointer).")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite secrets that already exist in the state .env.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing anything.")
    args = parser.parse_args(argv)

    result = run_migration(args.legacy_clone, args.vault, args.force, args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
