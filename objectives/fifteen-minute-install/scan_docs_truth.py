#!/usr/bin/env python3
"""Docs truth scanner: contract item P16.

Reads the README, every SKILL.md, the plugin CLAUDE.md and the pre-call
checklist template, and fails on any of these stale claims:

  time      an install time claim ("about 10 minutes", "takes about N
            minutes") that no saved ledger backs. A claim is backed only when
            evidence/run-1/report.json exists and its total is at or under
            the claimed number of minutes.
  installer a package manager the install no longer uses in install prose
            (brew install, Homebrew, winget install, choco install).
  steps     a numbered reference to a step of the old install skill from
            another skill or the dev guide ("install Step 8", "Step 4b of
            install").
  obsidian  the README prerequisites row for Obsidian not saying optional.
  ghost     a docstring naming tools/credentials_bundle.py while no such
            file exists.
  entry     the README install section not showing the one pasted line
            (setup.sh).

Prints the files scanned (refuses to pass on 0) and every hit with its file
and line. --selftest plants one instance of every rule in a temp copy and
expects each to be found before any live 0 is trusted.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
PLUGIN_ROOT = HERE.parents[2]
EVIDENCE = HERE.parent / "evidence" / "run-1" / "report.json"

_WORDS = {"five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60}
TIME_RE = re.compile(r"(?i)\b(?:about|around|roughly|takes|under|in)\s+(\d{1,3}|five|ten|fifteen|twenty|thirty|forty|sixty)\s*(?:minutes|min)\b")
INSTALLER_RE = re.compile(r"(?i)\b(brew install|homebrew|winget install|choco install)\b")
STEPS_RE = re.compile(r"(?i)\b(?:install(?:-van-gogh)?\s+(?:skill\s+)?step\s+\d+[a-z]?|step\s+\d+[a-z]?\s+of\s+(?:the\s+)?install)\b")
GHOST_RE = re.compile(r"tools/credentials_bundle\.py")
OBSIDIAN_ROW_RE = re.compile(r"^\|\s*\[Obsidian\]", re.M)


def _docs(root: Path) -> list:
    files = [root / "README.md", root / "CLAUDE.md"]
    files += sorted((root / "skills").glob("*/SKILL.md"))
    files += sorted((root / "skills").glob("*/references/*.md"))
    # The pre-call checklist lives at the internal repo root (the plugin's
    # parent), never under the plugin; a lookup under the plugin root found
    # nothing and skipped it silently, which is how an unbacked time claim
    # in it got past round 1.
    for tpl in (root / "templates" / "pre-call-checklist.md",
                root.parent / "templates" / "pre-call-checklist.md"):
        if tpl.exists():
            files.append(tpl)
    return [f for f in files if f.exists()]


def _backed_minutes(root: Path) -> int | None:
    path = root / "objectives" / "fifteen-minute-install" / "evidence" / "run-1" / "report.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    total = data.get("total_seconds")
    if not isinstance(total, (int, float)):
        return None
    return int(total // 60) + (1 if total % 60 else 0)


def scan(root: Path) -> tuple:
    """Returns (files_scanned, hits) where a hit is (rule, path, lineno, text)."""
    hits = []
    docs = _docs(root)
    backed = _backed_minutes(root)
    for path in docs:
        rel = (path.relative_to(root).as_posix() if root in path.parents
               else "templates/" + path.name)
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        install_prose = rel in ("README.md", "skills/install-van-gogh/SKILL.md",
                                "templates/pre-call-checklist.md")
        for i, line in enumerate(lines, start=1):
            if install_prose:
                m = TIME_RE.search(line)
                if m and re.search(r"(?i)install|set ?up|setup", line):
                    claimed = _WORDS.get(m.group(1).lower()) or int(m.group(1))
                    if backed is None or claimed < backed:
                        hits.append(("time", rel, i, line.strip()[:120]))
                if INSTALLER_RE.search(line) and not line.lstrip().startswith(("<!--", "#")):
                    # Allowed when the line says the installer is NOT used.
                    if not re.search(r"(?i)\bno (?:longer|sudo|homebrew)|never|not needed|without\b", line):
                        hits.append(("installer", rel, i, line.strip()[:120]))
            if rel != "skills/install-van-gogh/SKILL.md" and STEPS_RE.search(line):
                hits.append(("steps", rel, i, line.strip()[:120]))
        if rel == "README.md":
            m = OBSIDIAN_ROW_RE.search(text)
            if m:
                row = text[m.start():text.find("\n", m.start())]
                if "optional" not in row.lower():
                    hits.append(("obsidian", rel, text[:m.start()].count("\n") + 1, row[:120]))
            if "setup.sh" not in text:
                hits.append(("entry", rel, 0, "README never shows the one pasted line (setup.sh)"))
    for py in sorted((root / "app").glob("*.py")):
        text = py.read_text(encoding="utf-8")
        if GHOST_RE.search(text) and not (root / "tools" / "credentials_bundle.py").exists() \
                and not (root.parent / "tools" / "credentials_bundle.py").exists():
            lineno = text[:GHOST_RE.search(text).start()].count("\n") + 1
            hits.append(("ghost", py.relative_to(root).as_posix(), lineno,
                         "names tools/credentials_bundle.py, which does not exist"))
    return len(docs), hits


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "skills" / "week").mkdir(parents=True)
        (root / "skills" / "install-van-gogh").mkdir(parents=True)
        (root / "app").mkdir()
        (root / "README.md").write_text(
            "# x\nThe install takes about 10 minutes.\nRun brew install python first.\n"
            "| [Obsidian](https://obsidian.md) | Second Brain vault | obsidian.md |\n",
            encoding="utf-8")
        (root / "CLAUDE.md").write_text("see install Step 4b for research\n", encoding="utf-8")
        (root / "skills" / "week" / "SKILL.md").write_text("as in install Step 8\n", encoding="utf-8")
        (root / "skills" / "install-van-gogh" / "SKILL.md").write_text("ok\n", encoding="utf-8")
        (root / "app" / "x.py").write_text('"""generated per client by tools/credentials_bundle.py."""\n',
                                           encoding="utf-8")
        n, hits = scan(root)
        rules = {h[0] for h in hits}
        expected = {"time", "installer", "steps", "obsidian", "ghost", "entry"}
        missing = expected - rules
        print(f"selftest: scanned {n} docs, rules fired {sorted(rules)}"
              + (f", MISSED {sorted(missing)}" if missing else ""))
        return 0 if not missing else 1


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(PLUGIN_ROOT))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    n, hits = scan(Path(args.root))
    if n == 0:
        print("SKIP: 0 docs scanned")
        return 2
    print(f"scanned {n} docs under {args.root}")
    backed = _backed_minutes(Path(args.root))
    print("ledger-backed install time: " + (f"{backed} min" if backed else "none yet"))
    for rule, rel, lineno, text in hits:
        print(f"FAIL [{rule}] {rel}:{lineno}: {text}")
    if hits:
        print(f"FAIL: {len(hits)} stale claims")
        return 1
    print("PASS: 0 stale claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
