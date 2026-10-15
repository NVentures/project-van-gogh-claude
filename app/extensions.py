"""extensions.py — user-authored extension modules, loaded from outside the repo.

Users extend Van Gogh without touching the plugin cache (a marketplace update
re-clones it) by dropping Python files into `~/.config/van-gogh/extensions/`
(user_state.extensions_dir; machine-local on purpose — executable code must
never arrive through vault sync). Discovery is a filename scan; placing a file
in a Van-Gogh-owned directory IS the registration.

Two extension kinds today, told apart by filename prefix:

  briefing_section_<name>.py — one extra section in the briefings. Contract:

      TITLE: str                     # section heading shown in the briefing
      BRIEFINGS: set | None          # optional subset of KNOWN_BRIEFINGS;
                                     # absent = all four
      def collect(ctx: dict) -> str  # markdown; "" = quiet (section omitted);
                                     # raise on failure
      # ctx = {"briefing": str, "today": "YYYY-MM-DD", "vault": str,
      #        "meta": config_loader.resolved_meta()}

  notetaker_<name>.py — an external meeting-notetaker provider implementing
      the exact five-item contract documented in app/notetaker.py. Consumed by
      notetaker.py's registry; this module only finds and imports the file.

Everything here fails open. A broken extension degrades a briefing with one
line in its errors list (mirroring notetaker.config_problem), never crashes
it — the unattended digest scheduler runs these same code paths.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import user_state

# The guards below catch SystemExit alongside Exception on purpose: a user
# module calling sys.exit() would otherwise sail through `except Exception`
# and kill an unattended briefing — with exit code 0 if it called
# sys.exit(0), which the digest scheduler reads as success.
_GUARDED = (Exception, SystemExit)

KNOWN_BRIEFINGS = {"morning-coffee", "afternoon-tea", "week", "week-retro"}

# A runaway extension must not swamp the briefing (the digest emails it).
MAX_SECTION_CHARS = 8000

_SECTION_PREFIX = "briefing_section_"
_NOTETAKER_PREFIX = "notetaker_"


def _scan(prefix: str) -> dict[str, Path]:
    """name → path for extension files matching `prefix`, sorted by name."""
    out: dict[str, Path] = {}
    try:
        entries = sorted(user_state.extensions_dir().iterdir())
    except OSError:
        return out
    for path in entries:
        if path.is_file() and path.suffix == ".py" and path.name.startswith(prefix):
            name = path.stem[len(prefix):]
            if name:
                out[name] = path
    return out


def notetaker_modules() -> dict[str, Path]:
    """External notetaker providers: name → file path."""
    return _scan(_NOTETAKER_PREFIX)


def load_module(path: Path, alias: str):
    """Import an extension file under a stable module alias (cached).

    The alias namespaces extension modules (van_gogh_ext_*) so a user file
    can never shadow an app/ module in sys.modules. A failed import is
    evicted so the next run retries instead of returning a half-initialized
    module forever. Caching is per-process with no mtime check: a freshly
    DROPPED file is seen on the next scan, but an EDITED file needs a new
    process — one-shot briefing scripts always get one; the long-running
    Workbench needs a restart to pick up an edit.

    Module-level print() is redirected to stderr: briefing scripts print
    JSON to stdout, and one stray print from a user module would corrupt
    every consumer of that stream.
    """
    mod = sys.modules.get(alias)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load extension file {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    try:
        with contextlib.redirect_stdout(sys.stderr):
            spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(alias, None)
        raise
    return mod


def _wanted_briefings(mod, name: str, errors: list):
    """Normalize a module's BRIEFINGS declaration; None means "all".

    A bare string is treated as one name (set("week") would silently become
    {'w','e','k'} and disable the section everywhere), and unknown names are
    reported — a typo like "morning_coffee" must not read as a quiet section.
    """
    wanted = getattr(mod, "BRIEFINGS", None)
    if wanted is None:
        return None
    if isinstance(wanted, str):
        wanted = {wanted}
    wanted = {str(w) for w in wanted}
    unknown = wanted - KNOWN_BRIEFINGS
    if unknown:
        errors.append(
            f"Extension '{name}': unknown briefing name(s) in BRIEFINGS: "
            f"{', '.join(sorted(unknown))} (known: {', '.join(sorted(KNOWN_BRIEFINGS))})")
    return wanted


def briefing_sections(briefing: str, errors: list) -> list[dict]:
    """User briefing sections for `briefing`: [{"name", "title", "md"}].

    Each module is imported and called inside its own try/except; a failure
    costs that one section and appends one line to `errors`, never the run.
    """
    sections: list[dict] = []
    files = _scan(_SECTION_PREFIX)
    if not files:
        return sections
    # Imported lazily so this module stays importable before config exists.
    from config_loader import resolved_meta, user_tz, vault

    today = datetime.now(timezone.utc).astimezone(user_tz()).strftime("%Y-%m-%d")
    ctx = None
    for name, path in files.items():
        try:
            mod = load_module(path, f"van_gogh_ext_section_{name}")
            wanted = _wanted_briefings(mod, name, errors)
            if wanted is not None and briefing not in wanted:
                continue
            if ctx is None:
                ctx = {"briefing": briefing, "today": today,
                       "vault": str(vault()), "meta": resolved_meta()}
            # Deep copy: one module mutating ctx["meta"] must not poison the
            # context every later module sees. Stdout redirect: same JSON
            # channel rule as load_module.
            with contextlib.redirect_stdout(sys.stderr):
                md = mod.collect(copy.deepcopy(ctx))
            if md is None:
                md = ""
            if not isinstance(md, str):
                raise TypeError("collect() must return markdown text")
            md = md.strip()
            if not md:
                continue
            if len(md) > MAX_SECTION_CHARS:
                md = md[:MAX_SECTION_CHARS].rstrip() + "\n\n*(section truncated)*"
            title = str(getattr(mod, "TITLE", "") or name.replace("_", " ").title())
            sections.append({"name": name, "title": title, "md": md})
        except _GUARDED as e:                                   # noqa: BLE001
            errors.append(f"Extension '{name}': {e!r}")
    return sections


def attach(output: dict, briefing: str) -> None:
    """Populate output["extra_sections"] for a briefing, failing open.

    The key is always set (the front-page contract: keys exist on every
    branch), and any failure lands in output["errors"] as one line.
    """
    errors = output.setdefault("errors", [])
    try:
        output["extra_sections"] = briefing_sections(briefing, errors)
    except _GUARDED as e:                                       # noqa: BLE001
        output["extra_sections"] = []
        errors.append(f"Extensions: {e!r}")
