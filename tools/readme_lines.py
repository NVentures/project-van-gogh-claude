#!/usr/bin/env python3
"""Print a README paste line by name, so CI runs what a client pastes.

The README marks each fenced paste block with `<!-- paste: <name> -->` on
the line before it. This prints that block's one command line, which is
what the Codex CI lane executes; the README and CI cannot drift because
there is one copy.

    python tools/readme_lines.py install-codex-mac [--prepare-only]
    python tools/readme_lines.py --list

`--prepare-only` appends that flag to the setup invocation at the end of an
install line (CI must not open a client window); the rest is verbatim.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
_BLOCK = re.compile(r"<!-- paste: ([a-z0-9-]+) -->\n```[a-z]*\n(.*?)\n```", re.S)


def lines() -> dict:
    return {m.group(1): m.group(2).strip() for m in _BLOCK.finditer(README.read_text(encoding="utf-8"))}


def main(argv: list) -> int:
    if not argv or argv[0] == "--list":
        for name in lines():
            print(name)
        return 0
    name = argv[0]
    if name == "--section" and len(argv) > 1:
        # `--section mac|windows` is the Codex install line for that OS.
        name = {"mac": "install-codex-mac", "windows": "install-codex-windows"}.get(argv[1], argv[1])
        argv = [name] + argv[2:]
    found = lines()
    if name not in found:
        print(f"no paste block named {name!r}; known: {', '.join(found)}", file=sys.stderr)
        return 2
    line = found[name]
    if "--prepare-only" in argv[1:]:
        if "setup.ps1" in line:
            line += " -PrepareOnly"
        else:
            line += " --prepare-only"
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
