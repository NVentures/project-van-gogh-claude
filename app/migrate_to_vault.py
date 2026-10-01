"""
migrate_to_vault.py — move Project Van Gogh state into the user's vault.

All mutable state (config.json, the rendered briefings, logs/, and the
projects/ workspace memory) lives in a `van-gogh/` folder at the root of the
user's Obsidian vault. This helper copies any pre-existing repo-root copies of
those files into `{vault}/van-gogh/`, deletes the repo-root originals (single
source of truth), and writes the vault pointer file the loader uses to find the
vault (`~/.config/van-gogh/vault-pointer`, see app/user_state.py).

Idempotent: if a destination already exists it is left untouched and the source
(if any) is removed, so re-running is a no-op. Tolerates missing sources — a
fresh install simply has nothing to move.

Run via the installer:
    python app/migrate_to_vault.py --vault "/path/to/Obsidian Vault"
"""

import argparse
import shutil
import sys
from pathlib import Path

import user_state

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_RELPATH = Path("projects") / "Project Van Gogh" / "memory.md"

# Repo-root state that moves into {vault}/van-gogh/. Files and directories alike.
_MIGRATE_NAMES = [
    "config.json",
    "week.md",
    "morning-coffee.md",
    "afternoon-tea.md",
    "logs",
    "projects",
    ".ingest_state.json",
]


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def migrate(vault: Path, repo_root: Path | None = None) -> list[str]:
    """Migrate repo-root state into {vault}/van-gogh/. Returns a summary log.

    `repo_root` defaults to this checkout; tests override it to point at a
    fake repo. The pointer is written to the per-user state dir (tests point
    VAN_GOGH_STATE_DIR at a temp dir).
    """
    vault = Path(vault).expanduser()
    repo_root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    dst_root = vault / "van-gogh"
    dst_root.mkdir(parents=True, exist_ok=True)

    summary: list[str] = []
    for name in _MIGRATE_NAMES:
        src = repo_root / name
        dst = dst_root / name
        if dst.exists():
            # Already migrated. Drop any stale repo-root copy so there's one home.
            if src.exists():
                _remove(src)
                summary.append(f"skip {name} (already in vault) — removed stale repo copy")
            else:
                summary.append(f"skip {name} (already in vault)")
        elif src.exists():
            _copy(src, dst)
            _remove(src)
            summary.append(f"moved {name} -> van-gogh/{name}")
        else:
            summary.append(f"nothing to do for {name}")

    # Seed the workspace memory from the repo template when none exists yet
    # (fresh install: nothing was moved above, so van-gogh/projects/ is empty).
    memory_dst = dst_root / _MEMORY_RELPATH
    memory_template = repo_root / "memory.template.md"
    if memory_dst.exists():
        summary.append(f"skip {_MEMORY_RELPATH.as_posix()} (already present)")
    elif memory_template.exists():
        memory_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(memory_template, memory_dst)
        summary.append(f"seeded {_MEMORY_RELPATH.as_posix()} from memory.template.md")
    else:
        summary.append("no memory.template.md to seed from")

    pointer_path = user_state.state_dir() / "vault-pointer"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(str(vault) + "\n", encoding="utf-8")
    summary.append(f"wrote pointer {pointer_path} -> {vault}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate Van Gogh state into the vault.")
    parser.add_argument("--vault", required=True, help="Absolute path to the Obsidian vault.")
    args = parser.parse_args(argv)

    summary = migrate(Path(args.vault))
    print(f"Migrating Project Van Gogh state into {Path(args.vault).expanduser() / 'van-gogh'}\n")
    for line in summary:
        print(f"  {line}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
