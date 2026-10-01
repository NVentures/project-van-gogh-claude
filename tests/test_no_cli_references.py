"""Guard against re-introducing the retired `mgc` / `gws` CLI references.

The project fetches Gmail/Calendar/Outlook data through the direct-OAuth
clients (`app/google_client.py`, `app/microsoft_client.py`), not by shelling
out to the `gws` (Google Workspace) or `mgc` (Microsoft Graph) CLIs. These
tests fail if any source, doc, config, or test file mentions those CLIs again,
or resurrects the per-script subprocess scaffolding that wrapped them.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories/files to scan. Excludes the gitignored user config.json, the
# managed venv, and lockfiles — none of which are committed source.
_SCAN_GLOBS = (
    "app/*.py",
    "tests/*.py",
    "skills/**/*.md",
    "*.md",
    "config.template.json",
    "tasks.md",
    "tasks_plan.md",
)
# Note: the projects/ workspace memory now lives in the vault (van-gogh/),
# outside this repo, so it is no longer scanned here.

# This test file legitimately contains the strings it forbids elsewhere.
_SELF = Path(__file__).name

_CLI_RE = re.compile(r"\b(mgc|gws)\b", re.IGNORECASE)
_IDENT_RE = re.compile(
    r"SECONDARY_GWS_ENV|MGC_ENV|run_gws|google_secondary_config_dir"
    r"|GOOGLE_WORKSPACE_CLI_CONFIG_DIR"
)


def _scanned_files():
    seen = set()
    for pattern in _SCAN_GLOBS:
        for path in _REPO_ROOT.glob(pattern):
            if path.is_file() and path.name != _SELF and path not in seen:
                seen.add(path)
                yield path


def test_no_cli_name_references():
    """No file may mention the `mgc` or `gws` CLI by name."""
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _CLI_RE.search(line):
                rel = path.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "Retired CLI names found:\n" + "\n".join(offenders)


def test_no_retired_cli_scaffolding_identifiers():
    """The subprocess scaffolding that wrapped the CLIs must stay gone."""
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _IDENT_RE.search(line):
                rel = path.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "Retired CLI scaffolding found:\n" + "\n".join(offenders)


def test_scan_actually_covers_files():
    """Sanity: the glob set resolves to real files (catches a broken scan)."""
    files = list(_scanned_files())
    assert len(files) > 10, f"expected to scan many files, got {len(files)}"
    names = {f.name for f in files}
    assert "afternoon_tea.py" in names
    assert "google_client.py" in names
