#!/usr/bin/env python3
"""Em and en dash scanner over every line ADDED since the pinned base commit.

Contract item P18, first half. Reads the pin from punchlist.md (the line that
starts "Slug:" names "Base commit (pin): `<sha>`"), or from --since. Scans the
"+" lines of `git diff <pin>` for tracked files AND every line of every
untracked file (a diff-based scan is blind to the new files a feature adds),
across the whole internal repo. Refuses to report PASS when 0 lines were
scanned, and proves itself on a planted instance before trusting its own 0
(--selftest plants one in a temp copy of a real target file).

Exit 0 PASS, 1 FAIL (hits listed), 2 SKIP (nothing scanned, or git error).

The two characters are built with chr() so this file carries neither of them
and needs no exemption from its own scan.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EM = chr(0x2014)
EN = chr(0x2013)
HERE = Path(__file__).resolve()
PLUGIN_ROOT = HERE.parents[2]
REPO_ROOT = PLUGIN_ROOT.parent

# Binary or generated files never hold prose we wrote.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".pyc", ".ico",
                 ".woff", ".woff2", ".ttf", ".zip", ".gz"}


def pin_from_punchlist() -> str:
    text = (HERE.parent / "punchlist.md").read_text(encoding="utf-8")
    m = re.search(r"Base commit \(pin\): `([0-9a-f]{7,40})`", text)
    if not m:
        raise SystemExit("punchlist.md names no base commit pin")
    return m.group(1)


def _git(args: list, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-400:]}")
    return r.stdout


def added_lines(since: str, root: Path) -> list:
    """[(path, lineno, text)] for every added line since `since`.

    Paths are repo-root relative; untracked files contribute every line.
    """
    out = []
    diff = _git(["diff", "-U0", "--no-color", since, "--"], root)
    path = None
    lineno = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            name = raw[4:]
            path = None if name == "/dev/null" else name[2:] if name.startswith("b/") else name
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
            continue
        if path is None or raw.startswith("---") or raw.startswith("diff "):
            continue
        if raw.startswith("+"):
            out.append((path, lineno, raw[1:]))
            lineno += 1
    untracked = _git(["ls-files", "--others", "--exclude-standard"], root).splitlines()
    for rel in untracked:
        p = root / rel
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SystemExit(f"cannot read untracked file {rel}: {exc}")
        for i, line in enumerate(text.splitlines(), start=1):
            out.append((rel, i, line))
    return out


def find_hits(lines: list, exempt: set) -> list:
    hits = []
    for path, lineno, text in lines:
        if path in exempt:
            continue
        if EM in text or EN in text:
            hits.append((path, lineno, text.strip()[:100]))
    return hits


def selftest(since: str) -> int:
    """Plant one em dash in a temp clone's real target file and expect a hit."""
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        subprocess.run(["git", "clone", "-q", "--no-local", str(REPO_ROOT), str(clone)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", since],
                       check=True, capture_output=True)
        target = clone / "project-van-gogh" / "README.md"
        target.write_text(target.read_text(encoding="utf-8")
                          + "\nplanted " + EM + " line\n", encoding="utf-8")
        new = clone / "project-van-gogh" / "planted-new-file.md"
        new.write_text("untracked " + EN + " here\n", encoding="utf-8")
        hits = find_hits(added_lines(since, clone), set())
        paths = {h[0] for h in hits}
        ok = ("project-van-gogh/README.md" in paths
              and "project-van-gogh/planted-new-file.md" in paths)
        print(f"selftest: {'found both planted instances' if ok else 'MISSED a plant'}: {sorted(paths)}")
        return 0 if ok else 1


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=None, help="base commit (default: the punch list pin)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--exempt", action="append", default=[],
                    help="repo-relative path to exempt (a scanner holding the characters as fixtures)")
    args = ap.parse_args(argv)
    since = args.since or pin_from_punchlist()
    if args.selftest:
        return selftest(since)
    exempt = set(args.exempt)
    try:
        lines = added_lines(since, REPO_ROOT)
    except RuntimeError as exc:
        print(f"SKIP: {exc}")
        return 2
    if not lines:
        print(f"SKIP: 0 added lines since {since}; a scan over nothing proves nothing")
        return 2
    hits = find_hits(lines, exempt)
    files = {p for p, _, _ in lines}
    print(f"scanned {len(lines)} added lines in {len(files)} files since {since}")
    if hits:
        for path, lineno, text in hits:
            print(f"FAIL {path}:{lineno}: {text}")
        print(f"FAIL: {len(hits)} dash hits")
        return 1
    print("PASS: 0 em or en dashes in added lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
