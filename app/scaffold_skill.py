#!/usr/bin/env python3
"""scaffold_skill.py — write a personal skill into ~/.claude/skills/.

The deterministic half of /van-gogh:create-skill: the skill's prose used to
tell Claude to hand-write the files from inline templates, which meant the
templates could drift and could not be tested. This script owns them instead.
Claude still does the thinking (interview, then editing the scaffold's TODO
markers into real behavior); this writes a correct, cross-platform skeleton.

Three modes:
  --kind prose   SKILL.md only (instructions Claude follows directly)
  --kind script  SKILL.md + <name>.py wired to the shared venv and the
                 plugin's app/ modules via the plugin-root pointer
  --fork <shipped-skill>
                 copy a shipped skill's SKILL.md as a personal variant, with
                 every ${CLAUDE_PLUGIN_ROOT} reference rewritten to the
                 plugin-root-pointer form personal skills need — the supported
                 way to CHANGE how a built-in skill behaves without touching
                 the plugin cache

Output is one JSON object on stdout: {"ok": true, "path", "files", "command",
"warnings"} or {"ok": false, "error"} (exit 1). Existing skills are never
overwritten without --force.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from config_loader import force_utf8_io

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# The forms personal skills use to reach the plugin's app/ dir. Shipped
# skills anchor on ${CLAUDE_PLUGIN_ROOT}; personal skills don't get it and
# read the pointer file instead (user_state.record_plugin_root). Three forms
# because shipped skills reference the plugin three ways: a quoted bash path,
# a quoted PowerShell path, and a python -c sys.path one-liner.
_BASH_PLUGIN_REF = '"$(cat "$HOME/.config/van-gogh/plugin-root")/'
_PS_PLUGIN_REF = ('"$(Get-Content "$HOME\\.config\\van-gogh\\plugin-root" -Raw '
                  '| ForEach-Object Trim)\\')
_PY_PLUGIN_EXPR = "os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')"
# Single quotes only, so it survives inside the double-quoted -c string of
# both shells unchanged.
_PY_POINTER_EXPR = (
    "os.path.join("
    "open(os.path.join(os.path.expanduser(os.environ.get('VAN_GOGH_STATE_DIR') or '~/.config/van-gogh'), 'plugin-root'), encoding='utf-8')"  # noqa: E501
    ".read().strip(), 'app')")

_PROSE_TEMPLATE = '''---
name: __NAME__
description: __DESCRIPTION__
---

# __TITLE__

TODO: replace this section with what the skill does, step by step, written as
instructions to Claude.

If you need resolved Van Gogh paths (vault, hotcache, sources, entity dir),
run the script context and use its keys verbatim:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "$(cat "$HOME/.config/van-gogh/plugin-root")/app/skill_context.py"
```

Windows (PowerShell):
```powershell
& "$HOME\\.config\\van-gogh\\venv\\Scripts\\python.exe" "$(Get-Content "$HOME\\.config\\van-gogh\\plugin-root" -Raw | ForEach-Object Trim)\\app\\skill_context.py"
```
'''

_SCRIPT_SKILL_TEMPLATE = '''---
name: __NAME__
description: __DESCRIPTION__
---

# __TITLE__

A Python script does the work; run it and render the result.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_SKILL_DIR}/__NAME__.py"
```

Windows (PowerShell):
```powershell
& "$HOME\\.config\\van-gogh\\venv\\Scripts\\python.exe" "$env:CLAUDE_SKILL_DIR\\__NAME__.py"
```

The script prints JSON to stdout. Render it as:

TODO: describe the JSON keys and how to present them.
'''

_SCRIPT_PY_TEMPLATE = '''"""__NAME__: user skill script for Project Van Gogh."""

import json
import os
import sys
from pathlib import Path

# Personal skills don't get ${CLAUDE_PLUGIN_ROOT}; the installed plugin
# records its location in the state dir (app/user_state.record_plugin_root).
_state = Path(os.environ.get("VAN_GOGH_STATE_DIR", "") or Path.home() / ".config" / "van-gogh")
_plugin_root = Path((_state / "plugin-root").read_text(encoding="utf-8").strip())
sys.path.insert(0, str(_plugin_root / "app"))

from config_loader import cfg, force_utf8_io, vault  # noqa: E402


def main() -> int:
    force_utf8_io()
    # TODO: gather data (config_loader helpers, google_client /
    # microsoft_client, notetaker, vault files under vault()) and build the
    # JSON your SKILL.md describes. Rules, same as the plugin's own code:
    # encoding="utf-8" on all file I/O, pathlib joins, config only via
    # config_loader, no macOS-only shell commands.
    print(json.dumps({"example": str(vault())}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def shipped_skills() -> list[str]:
    """Basenames of the plugin's own skills (fork sources, collision warnings)."""
    root = _PLUGIN_ROOT / "skills"
    try:
        return sorted(p.name for p in root.iterdir()
                      if p.is_dir() and (p / "SKILL.md").exists())
    except OSError:
        return []


def rewrite_for_personal(text: str, new_name: str) -> str:
    """Make a shipped SKILL.md runnable as a personal skill.

    Rewrites every form of ${CLAUDE_PLUGIN_ROOT} that shipped skills use
    (quoted bash/PowerShell paths, python -c sys.path one-liners, then a
    catch-all for cd prefixes and prose references) to the pointer-file
    form, points the relative `../_shared/` doc links back at the installed
    plugin's copies (a fork lives in ~/.claude/skills/, where that relative
    path resolves to nothing), and rewrites the frontmatter `name:` so the
    two skills stay distinct in the command list.
    """
    text = text.replace('"${CLAUDE_PLUGIN_ROOT}/', _BASH_PLUGIN_REF)
    text = text.replace('"$env:CLAUDE_PLUGIN_ROOT\\', _PS_PLUGIN_REF)
    text = text.replace(_PY_PLUGIN_EXPR, _PY_POINTER_EXPR)
    # Catch-alls, after the specific forms: `cd $env:CLAUDE_PLUGIN_ROOT`
    # prefixes, unquoted `"${CLAUDE_PLUGIN_ROOT}"`, and prose references.
    text = text.replace('${CLAUDE_PLUGIN_ROOT}',
                        '$(cat "$HOME/.config/van-gogh/plugin-root")')
    text = text.replace('$env:CLAUDE_PLUGIN_ROOT',
                        '$(Get-Content "$HOME\\.config\\van-gogh\\plugin-root" '
                        '-Raw | ForEach-Object Trim)')
    text = text.replace('](../_shared/',
                        '](' + str(_PLUGIN_ROOT / 'skills' / '_shared') + '/')
    return re.sub(r"^name:\s*\S+", f"name: {new_name}", text, count=1,
                  flags=re.MULTILINE)


# Windows refuses (or worse, misroutes) directories with device names.
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}


def scaffold(name: str, kind: str, description: str, fork: str | None,
             dest_root: Path, force: bool) -> dict:
    """Write the skill directory; returns the JSON-able result."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        return {"ok": False,
                "error": f"'{name}' is not kebab-case (lowercase letters, digits, hyphens)"}
    if name in _WINDOWS_RESERVED:
        return {"ok": False,
                "error": f"'{name}' is a reserved device name on Windows; pick another"}

    skill_dir = dest_root / name
    if skill_dir.exists() and not force:
        return {"ok": False, "error": "exists", "path": str(skill_dir),
                "hint": "pass --force to overwrite, or pick another name"}

    warnings = []
    if name in shipped_skills() and name != (fork or ""):
        warnings.append(
            f"'{name}' duplicates the shipped /van-gogh:{name} skill's basename; "
            "the two commands will sit side by side in the command list")
    if fork and (kind != "prose" or description):
        warnings.append("--kind and --description are ignored with --fork; "
                        "the fork keeps the shipped skill's shape (edit its "
                        "frontmatter description so the trigger phrases differ)")

    files: dict[str, str] = {}
    if fork:
        # Membership check, not a bare exists(): --fork feeds a path join, and
        # only names of actually shipped skills belong there.
        if fork not in shipped_skills():
            return {"ok": False,
                    "error": f"no shipped skill named '{fork}' "
                             f"(known: {', '.join(shipped_skills())})"}
        source = _PLUGIN_ROOT / "skills" / fork / "SKILL.md"
        files["SKILL.md"] = rewrite_for_personal(
            source.read_text(encoding="utf-8"), name)
        if "CLAUDE_PLUGIN_ROOT" in files["SKILL.md"]:
            leftovers = sum(1 for line in files["SKILL.md"].splitlines()
                            if "CLAUDE_PLUGIN_ROOT" in line)
            warnings.append(
                f"{leftovers} line(s) still reference CLAUDE_PLUGIN_ROOT after "
                "rewriting; personal skills never get that variable — review "
                "and fix those lines by hand")
    else:
        # Collapsed to one line: a newline in the description would land in
        # the generated YAML frontmatter and inject arbitrary keys there.
        description = " ".join(description.split()) \
            or f"Personal Van Gogh skill. Use when the user types /{name}."
        fill = lambda t: (t.replace("__NAME__", name)  # noqa: E731
                          .replace("__TITLE__", _title(name))
                          .replace("__DESCRIPTION__", description))
        files["SKILL.md"] = fill(_PROSE_TEMPLATE if kind == "prose"
                                 else _SCRIPT_SKILL_TEMPLATE)
        if kind == "script":
            files[f"{name}.py"] = fill(_SCRIPT_PY_TEMPLATE)

    if skill_dir.exists():
        # --force replaces the whole skill: re-scaffolding a script skill as
        # prose (or a fork) must not leave the old <name>.py orphaned beside
        # a SKILL.md that no longer references it.
        import shutil
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (skill_dir / fname).write_text(content, encoding="utf-8", newline="\n")

    return {"ok": True, "path": str(skill_dir),
            "files": sorted(files), "command": f"/{name}",
            "forked_from": fork or "", "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True,
                        help="kebab-case skill name; becomes the /<name> command")
    parser.add_argument("--kind", choices=("prose", "script"), default="prose")
    parser.add_argument("--description", default="",
                        help="frontmatter description (summary + trigger phrases)")
    parser.add_argument("--fork", metavar="SHIPPED_SKILL",
                        help="copy this shipped skill's SKILL.md as the personal variant")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing personal skill of the same name")
    parser.add_argument("--dest", default="",
                        help="skills root (default ~/.claude/skills; tests only)")
    args = parser.parse_args(argv)

    dest_root = Path(args.dest).expanduser() if args.dest \
        else Path.home() / ".claude" / "skills"
    result = scaffold(args.name, args.kind, args.description, args.fork,
                      dest_root, args.force)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
