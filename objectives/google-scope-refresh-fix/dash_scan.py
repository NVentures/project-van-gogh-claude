"""Punch item P17: no em or en dash in anything this objective added.

The characters are written as escapes on purpose. A blanket dash sweep run
over a scanner's own source has rewritten its test into a hyphen count before,
and the scanner then reported thousands of dashes in a clean file.

    python3 dash_scan.py --selftest   # prove it finds a planted dash
    python3 dash_scan.py              # scan added lines + untracked files

An untracked file is invisible to `git diff`, and new files are exactly what a
change adds, so they are read whole and listed with their line counts.
"""
import subprocess
import sys
from pathlib import Path

# Written as escapes: a blanket dash sweep over this file must not be able to
# redefine what the scanner looks for.
EM, EN = "\u2014", "\u2013"
REPO = Path(__file__).resolve().parent.parent.parent
ROOT = REPO.parent


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=True).stdout


def _diff_base() -> str:
    """What to diff against: HEAD while the work is uncommitted, else the
    commit that introduced it.

    Once the work is committed, `git diff HEAD` is empty, and a scan over an
    empty input reports a clean pass forever. That is the shape where a gate
    silently stops testing anything. Pass --commit <sha> to scan a landed
    commit, or the scan refuses to report a pass on nothing.
    """
    if "--commit" in sys.argv:
        return sys.argv[sys.argv.index("--commit") + 1] + "^"
    return "HEAD"


def _diff_lines() -> list:
    base = _diff_base()
    target = [] if base == "HEAD" else [sys.argv[sys.argv.index("--commit") + 1]]
    return _git("diff", base, *target).splitlines()


def selftest() -> int:
    planted = [
        ("prose", "the fix " + EM + " permanent " + EM + " lands today", 2),
        # A dash standing in for "no value" does not read as punctuation to
        # the eye, which is the construction that actually slips past review.
        ("table cell", "| Was | 3, 7, " + EN + ", 48 |", 1),
        ("clean", "the fix, permanent, lands today (range 40-60)", 0),
    ]
    ok = True
    for name, line, expect in planted:
        hits = line.count(EM) + line.count(EN)
        print(f"  selftest {name}: {hits} found, {expect} expected")
        ok = ok and hits == expect
    print("SELFTEST", "ok" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if selftest() != 0:
        return 1

    # This file carries the characters it hunts, in its selftest fixtures and
    # its own constants. Exempting exactly itself keeps the proof that the
    # patterns work; deleting the fixtures would throw that proof away.
    me = str(Path(__file__).resolve().relative_to(ROOT))
    added, files = {}, {}
    current = None
    for line in _diff_lines():
        if line.startswith("+++ b/"):
            current = line[6:]
            files.setdefault(current, 0)
        elif line.startswith("+") and not line.startswith("+++") and current:
            files[current] += 1
            if (EM in line or EN in line) and current != me:
                added.setdefault(current, []).append(line.strip())

    untracked = [p for p in _git("ls-files", "--others",
                                 "--exclude-standard").splitlines() if p]
    for rel in untracked:
        path = ROOT / rel
        if not path.is_file():
            continue
        # An unreadable file is a hard error: a silent skip is how a scan
        # reports a clean zero over something it never opened.
        text = path.read_text(encoding="utf-8", errors="strict")
        lines = text.splitlines()
        files[rel] = len(lines)
        if rel == me:
            continue
        for line in lines:
            if EM in line or EN in line:
                added.setdefault(rel, []).append(line.strip())

    print(f"\nscanned {len(files)} files "
          f"({len(untracked)} untracked read whole):")
    for name, count in sorted(files.items()):
        mark = " UNTRACKED" if name in untracked else ""
        print(f"  {count:5d} lines  {name}{mark}")
    total = sum(len(v) for v in added.values())
    for name, lines in added.items():
        for line in lines:
            print(f"  DASH {name}: {line[:100]}")
    scanned = sum(files.values())
    if scanned == 0:
        print("\nRESULT SKIP: nothing to scan. The work is committed, so "
              "`git diff HEAD` is empty. Re-run with --commit <sha> to scan "
              "the commit that landed it. A pass over an empty input would "
              "mean nothing.")
        return 2
    print(f"\nRESULT {'PASS' if total == 0 else 'FAIL'}: {total} dashes in "
          f"{scanned} added or new lines")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
