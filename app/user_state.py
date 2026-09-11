"""
user_state.py — the stable per-user state directory outside the plugin cache.

Skills run from the installed plugin cache, and a marketplace update re-clones
that directory — anything stored there is disposable. Per-user machine state
(`.env` with OAuth refresh tokens + API keys, the vault pointer, and the shared
venv) therefore lives in `~/.config/van-gogh/`, which survives plugin updates
and reinstalls. The same directory already holds the optional per-user OAuth
app credential overrides (see google_client.py / microsoft_client.py).

Layout:
    ~/.config/van-gogh/
      .env            — refresh tokens + API keys (written by auth_bootstrap.py)
      vault-pointer   — absolute path to the user's Obsidian vault
      plugin-root     — absolute path to the installed plugin cache (for
                        user-authored personal skills; see record_plugin_root)
      venv/           — shared Python venv the skills invoke
      scheduler/      — wscript .vbs launchers for the Windows scheduled
                        tasks (written by scheduler_setup.py, removed on
                        uninstall)
      extensions/     — user-authored extension modules (briefing sections,
                        notetaker providers; see app/extensions.py). Created
                        by the user, never by the plugin.

Legacy installs kept `.env` and `.van-gogh-vault` at the plugin root. Both are
self-migrated (copied, not moved) into the state dir on first access, so a
plain `/plugin update` after this version is safe with no manual step. The
legacy copies are left in place for older scheduler jobs still pointing at a
stale plugin cache; the state-dir copy is the source of truth from then on.

Tests set VAN_GOGH_STATE_DIR to a temp dir so the suite never reads or writes
the real `~/.config/van-gogh/`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from platform_compat import NO_WINDOW

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    """`~/.config/van-gogh` (override with VAN_GOGH_STATE_DIR). Not auto-created."""
    override = os.environ.get("VAN_GOGH_STATE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "van-gogh"


def extensions_dir() -> Path:
    """`~/.config/van-gogh/extensions` — user-authored extension modules.

    Machine-local on purpose: extension files are executable code, and code
    must never arrive through vault sync (a half-synced module or a
    "conflicted copy" would be imported into an unattended digest run).
    Not auto-created; a user opts in by making the directory themselves.
    """
    return state_dir() / "extensions"


def _with_legacy_migration(path: Path, legacy: Path) -> Path:
    """Return `path`, first copying the legacy plugin-root file over if needed."""
    if not path.exists() and legacy.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, path)
    return path


def env_file() -> Path:
    """The live `.env` (tokens + API keys), self-migrating the legacy copy."""
    return _with_legacy_migration(state_dir() / ".env", _PLUGIN_ROOT / ".env")


def pointer_file() -> Path:
    """The vault pointer, self-migrating the legacy `.van-gogh-vault`."""
    return _with_legacy_migration(state_dir() / "vault-pointer", _PLUGIN_ROOT / ".van-gogh-vault")


def _running_in_managed_venv() -> bool:
    """Gate for the import-time self-heal hooks: only real installs qualify.

    Dev checkouts and pytest run their own interpreter, so they must never
    touch a real install's state or vault.
    """
    return Path(sys.prefix).resolve() == (state_dir() / "venv").resolve()


def sync_legacy_scheduler_jobs() -> None:
    """Remove scheduled jobs orphaned by a skill rename.

    A renamed skill leaves its launchd agent / scheduled task behind, firing
    daily into a slash command that no longer exists. scheduler_setup knows how
    to remove them, but it only runs from the install and uninstall skills — and
    an existing install receives a rename through a marketplace auto-update,
    which never re-runs install. So the cleanup hangs off the same import-time
    choke point as the venv sync: stamped by the legacy set, it costs one small
    file read per run and re-fires only when a new rename lands.

    Fails open in every direction. A stale scheduled job is a daily annoyance;
    an exception here would break every script on the machine.
    """
    if not _running_in_managed_venv():
        return
    if sys.platform not in ("darwin", "win32"):
        return
    try:
        import scheduler_setup  # local: scheduler_setup imports config_loader

        digest = hashlib.sha256(",".join(
            scheduler_setup.LEGACY_SKILLS + scheduler_setup.LEGACY_TASK_NAMES
        ).encode("utf-8")).hexdigest()
        marker = state_dir() / "legacy-jobs.sha256"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
            return
        scheduler_setup.remove_legacy_jobs(sys.platform)
        marker.write_text(digest, encoding="utf-8")
    except Exception:
        return


def sync_runtime_deps() -> None:
    """Re-sync the shared venv when the plugin's requirements.txt changes.

    Called at config_loader import time, so the first skill run after a plain
    `claude plugin marketplace update` self-heals its dependencies — the plugin
    update is the only update entry point; there is no update skill. No-ops
    unless the running interpreter *is* the managed venv, and costs one small
    file read per run while requirements are unchanged.
    """
    if not _running_in_managed_venv():
        return
    requirements = _PLUGIN_ROOT / "requirements.txt"
    if not requirements.exists():
        return
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = state_dir() / "requirements.sha256"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest:
        return
    print("van-gogh: plugin requirements changed; re-syncing venv dependencies...",
          file=sys.stderr)
    # Output must be captured: with NO_WINDOW on Windows the child gets a
    # hidden console, so uncaptured pip errors would vanish without a trace.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(requirements)],
        capture_output=True, text=True, encoding="utf-8",
        creationflags=NO_WINDOW,
    )
    if result.returncode == 0:
        marker.write_text(digest + "\n", encoding="utf-8")
    else:
        # Offline or a transient pip failure: the marker stays stale so the
        # next run retries; missing imports (if any) surface on their own.
        print("van-gogh: dependency sync failed; will retry on the next run\n"
              + (result.stderr or "").strip()[-2000:],
              file=sys.stderr)


# BEGIN is deliberately a prefix (no closing "-->"): the opening marker line
# carries free-form explanatory prose that is part of the managed block.
CLAUDE_BLOCK_BEGIN = "<!-- van-gogh:context:begin"
CLAUDE_BLOCK_END = "<!-- van-gogh:context:end -->"


def _find_marker(text: str, marker: str, start: int = 0) -> int:
    """Index of `marker` at the start of a line, or -1.

    Line-anchored so prose that merely quotes a marker (docs teach users the
    marker names) can never be mistaken for the real block boundary — a bare
    `find` there would make the replace span swallow user content.
    """
    pos = text.find(marker, start)
    while pos > 0 and text[pos - 1] != "\n":
        pos = text.find(marker, pos + 1)
    return pos


def _extract_claude_block(text: str) -> str | None:
    """Return the managed block (marker lines inclusive), or None if absent."""
    begin = _find_marker(text, CLAUDE_BLOCK_BEGIN)
    if begin == -1:
        return None
    end = _find_marker(text, CLAUDE_BLOCK_END, begin)
    if end == -1:
        return None
    return text[begin : end + len(CLAUDE_BLOCK_END)]


def sync_claude_block_into(vault: Path) -> bool:
    """Sync the plugin's managed CLAUDE.md context block into `{vault}/CLAUDE.md`.

    The vault CLAUDE.md is the context carrier for chief-of-staff sessions
    opened in the vault; the marker-delimited block at its top is owned by the
    plugin template (vault/CLAUDE.md) while everything outside the markers is
    the user's. Missing vault file → no-op (vault/scaffold.py owns first
    creation). Marker-less file (installs predating the block) → the template
    block is inserted at the top (below any YAML frontmatter, which Obsidian
    requires to stay first). Returns True if the file was written. The write
    normalizes the file's line endings to LF (one-time churn on CRLF files);
    utf-8-sig tolerates a stray BOM the same way upsert_env does.
    """
    template = _PLUGIN_ROOT / "vault" / "CLAUDE.md"
    target = vault / "CLAUDE.md"
    if not template.exists() or not target.exists():
        return False
    block = _extract_claude_block(template.read_text(encoding="utf-8"))
    if block is None:
        return False
    current = target.read_text(encoding="utf-8-sig")
    existing = _extract_claude_block(current)
    if existing == block:
        return False
    if existing is None:
        # A lone/orphaned marker line (end without begin, truncated block)
        # means the file is in a state we can't merge safely — prepending
        # would leave duplicate marker debris. Skip and say so.
        if _find_marker(current, CLAUDE_BLOCK_BEGIN) != -1 or \
                _find_marker(current, CLAUDE_BLOCK_END) != -1:
            print(f"van-gogh: {target} has orphaned van-gogh:context markers; "
                  "not syncing — remove the stray marker lines to re-enable",
                  file=sys.stderr)
            return False
        insert_at = 0
        if current.startswith("---\n"):
            fence = current.find("\n---\n", 3)
            if fence != -1:
                insert_at = fence + len("\n---\n")
        updated = (current[:insert_at] + block + "\n\n" + current[insert_at:])
    else:
        updated = current.replace(existing, block, 1)
    # Atomic write, so a crash mid-write can never truncate the user's file.
    _atomic_write(target, updated)
    return True


# Verbatim placeholder fragments from the scaffold section of vault/CLAUDE.md
# (and the hotcache stub). personalize_claude_scaffold() replaces them from
# config.json only while they are still byte-identical to the template — the
# moment the user edits one, it stops matching and becomes theirs.
_SCAFFOLD_NAME_PLACEHOLDER = "# This vault: [Your Name] Second Brain"
_SCAFFOLD_TAG_ROWS_PLACEHOLDER = (
    "| `#venture-1` | Your first venture or role |\n"
    "| `#venture-2` | Your second venture or role |"
)
_SCAFFOLD_ACTION_HEADING_PLACEHOLDER = "## [Your Name]'s Action Items"


def _atomic_write(target: Path, text: str) -> None:
    """Temp-file + os.replace: atomic on POSIX and Windows."""
    tmp = target.with_name(target.name + ".van-gogh-tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def personalize_claude_scaffold(vault: Path) -> bool:
    """Fill still-pristine scaffold placeholders from the vault config.json.

    The user-owned section of the vault CLAUDE.md (below the managed block)
    ships with template placeholders — "[Your Name]", the #venture-1/#venture-2
    Business Tags rows — and the hotcache stub ships the placeholder action
    items heading. Config.json already knows the real values by the time
    scaffold runs, so leaving the placeholders in place makes the instructions
    contradict the config (real tags live in config.json, the table says
    #venture-1). Each replacement fires only while the file text is still
    byte-identical to the shipped template AND the config value is real (not
    the config template's own placeholder), so a user edit is never touched
    and re-runs are no-ops.

    Reads config.json directly: config_loader imports this module, so the
    usual "always go through config_loader" rule would be a circular import
    here. Returns True if any file changed; never raises on a missing or
    malformed config (fresh installs scaffold before config in some flows).
    """
    import json

    try:
        config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False

    full_name = (config.get("user") or {}).get("full_name", "").strip()
    businesses = [
        b for b in (config.get("businesses") or [])
        if b.get("tag") and b.get("display_name")
    ]
    obsidian = config.get("obsidian") or {}
    heading = obsidian.get("hotcache_action_items_heading", "").strip()

    replacements: list[tuple[str, str]] = []
    if full_name and full_name != "Your Name":
        replacements.append((_SCAFFOLD_NAME_PLACEHOLDER,
                             f"# This vault: {full_name} Second Brain"))
    if businesses:
        rows = "\n".join(f"| `#{b['tag']}` | {b['display_name']} |" for b in businesses)
        replacements.append((_SCAFFOLD_TAG_ROWS_PLACEHOLDER, rows))
    if heading and heading != "Your Action Items":
        replacements.append((_SCAFFOLD_ACTION_HEADING_PLACEHOLDER, f"## {heading}"))
    if not replacements:
        return False

    changed = False
    hotcache = vault / obsidian.get("hotcache_relpath", "wiki/hotcache.md")
    for target in (vault / "CLAUDE.md", hotcache):
        if not target.exists():
            continue
        try:
            text = target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        updated = text
        for placeholder, replacement in replacements:
            updated = updated.replace(placeholder, replacement)
        if updated != text:
            _atomic_write(target, updated)
            changed = True
    return changed


def sync_vault_claude_block() -> None:
    """Keep the vault CLAUDE.md's managed block current with the plugin.

    Called at config_loader import time — same lifecycle as sync_runtime_deps,
    so the first skill run after a marketplace update refreshes the block.
    Same venv gate (dev checkouts and pytest never touch a real vault), and a
    failure must never take down the calling script — hence the broad except
    (a non-UTF-8 CLAUDE.md raises UnicodeDecodeError, which is not an OSError).
    """
    if not _running_in_managed_venv():
        return
    try:
        pointer = pointer_file()
        if not pointer.exists():
            return
        pointed = pointer.read_text(encoding="utf-8-sig").strip()
        # An empty or relative pointer would resolve against the caller's cwd
        # and write a CLAUDE.md into whatever repo the script runs from.
        if not pointed or not Path(pointed).is_absolute():
            return
        if sync_claude_block_into(Path(pointed)):
            print("van-gogh: refreshed the managed context block in the vault CLAUDE.md",
                  file=sys.stderr)
        if personalize_claude_scaffold(Path(pointed)):
            print("van-gogh: filled vault CLAUDE.md scaffold placeholders from config.json",
                  file=sys.stderr)
    except Exception as exc:
        print(f"van-gogh: vault CLAUDE.md sync skipped ({exc})", file=sys.stderr)


def record_plugin_root() -> None:
    """Record the installed plugin cache path for user-authored personal skills.

    Skills a user writes in ~/.claude/skills/ (scaffolded by
    /van-gogh:create-skill) don't get ${CLAUDE_PLUGIN_ROOT}, so their scripts
    locate the plugin's app/ modules via this pointer — the plugin-root sibling
    of vault-pointer. Called at config_loader import time, so every installed
    skill run self-heals the pointer after a marketplace update re-clones the
    cache. Same gate as sync_runtime_deps: no-ops unless the running
    interpreter *is* the managed venv, so dev checkouts and pytest never
    clobber a real install's pointer.
    """
    if not _running_in_managed_venv():
        return
    pointer = state_dir() / "plugin-root"
    value = str(_PLUGIN_ROOT)
    if pointer.exists() and pointer.read_text(encoding="utf-8").strip() == value:
        return
    pointer.write_text(value + "\n", encoding="utf-8", newline="\n")


def record_skill_use() -> None:
    """Note that this script ran, so the scorecard can spot what goes unused.

    The weekly scorecard closes with one Van Gogh skill the reader has not
    touched lately. Answering "not touched lately" needs a record, and the
    cheapest honest one is the name of the script that is running right now.

    Two things are deliberately recorded. `last` is any run at all; `attended`
    is a run with a human present, which is the one that means "they chose to
    use it". A scheduled job firing meeting-ingest every night must never make
    meeting-ingest look like a habit the user has picked up.

    Same gate as record_plugin_root, so a dev checkout and pytest never write
    into a real install. Fails open: a scorecard is worth less than the script
    it is watching.
    """
    if not _running_in_managed_venv():
        return
    try:
        import json
        from datetime import datetime

        stem = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else ""
        if not stem or stem.startswith("-"):
            return
        import config_loader

        path = config_loader.logs_dir() / "skill_usage.json"
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}

        now = datetime.now().isoformat(timespec="seconds")
        row = data.get(stem) if isinstance(data.get(stem), dict) else {}
        row["last"] = now
        if not os.environ.get("VAN_GOGH_UNATTENDED"):
            row["attended"] = now
        data[stem] = row

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:                                           # noqa: BLE001
        pass


def load_env() -> None:
    """Load the state-dir `.env` into the process env (real env vars win).

    utf-8-sig strips a stray UTF-8 BOM so the first key parses (see
    meeting_ingest.py); override=False means cloud routines that set real env
    vars are never clobbered by a stale file.
    """
    from dotenv import load_dotenv

    load_dotenv(env_file(), override=False, encoding="utf-8-sig")


def upsert_env(key: str, value: str, env_path: Path | None = None) -> Path:
    """Set `key=value` in the `.env` file (in place), creating it if needed.

    Reads with utf-8-sig so a stray BOM (from an old PowerShell 5.1 write) is
    scrubbed rather than glued to the first key; always writes plain utf-8.

    The file is left readable only by its owner. It holds refresh tokens and
    API keys, and the default umask on macOS makes a new file world readable,
    so without this every account on the machine can read them. The chmod runs
    on every write, not just creation, because a file that already exists with
    loose permissions is exactly the one that needs tightening.
    """
    path = Path(env_path) if env_path is not None else env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and some network filesystems do not carry POSIX modes. The
        # write already succeeded; failing the whole call here would cost the
        # user their credential over a permission bit that does not exist.
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI used by the install skill: `user_state.py set-env <KEY>`.

    The value is read from the VG_ENV_VALUE environment variable (never a
    command-line argument) so pasted secrets are neither shell-interpolated
    nor visible in the process list.
    """
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_set = sub.add_parser("set-env")
    p_set.add_argument("key", help="the .env key to set (value from $VG_ENV_VALUE)")
    args = parser.parse_args(argv)

    value = os.environ.get("VG_ENV_VALUE")
    if value is None:
        print("ERROR: set the VG_ENV_VALUE environment variable to the value to write")
        return 1
    upsert_env(args.key, value)
    print(f"{args.key} written (utf-8, no BOM)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
