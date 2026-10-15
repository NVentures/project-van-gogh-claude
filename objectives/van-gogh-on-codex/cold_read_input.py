#!/usr/bin/env python3
"""P23: render exactly what a fresh reader is given for the cold read.

Three documents a client would meet under Codex, concatenated with headings
that name the source: the README's prerequisites, installation and sessions
sections, the shared runtime note (which holds the Codex table), and the
install skill. Nothing else: the reader checks consistency, never the code.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def readme_section() -> str:
    text = ROOT.joinpath("README.md").read_text(encoding="utf-8")
    start = text.index("## Prerequisites")
    end = text.index("## Updates")
    return text[start:end]


if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__import__("os").environ.get("VG_SCRATCH", "/tmp")) / "cold-read-input.md"
    parts = ["# Document 1: README (prerequisites, installation, where to open sessions)\n", readme_section(),
             "\n\n# Document 2: skills/_shared/python-runtime.md\n", ROOT.joinpath("skills/_shared/python-runtime.md").read_text(encoding="utf-8"),
             "\n\n# Document 3: skills/install-van-gogh/SKILL.md\n", ROOT.joinpath("skills/install-van-gogh/SKILL.md").read_text(encoding="utf-8")]
    out.write_text("".join(parts), encoding="utf-8")
    print(out, len("".join(parts).split()), "words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
