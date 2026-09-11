#!/usr/bin/env python3
"""P20: no em or en dash on any line this change added.

The characters are written as escapes so a future sweep over THIS file cannot
silently redefine what it hunts for. A scanner that has not found a
known-present instance is not evidence, so --selftest plants one and requires
the scan to catch it.
"""
import subprocess
import sys
from pathlib import Path

# Written as escapes on purpose: a blanket dash sweep run over THIS file must
# not be able to silently rewrite what the scanner hunts for.
EM = "\u2014"
EN = "\u2013"


def added_lines(paths=None):
    """Every line this change contributes, tracked and untracked alike.

    `git diff HEAD` cannot see an untracked file, so a scan built only from it
    silently skips brand new files: exactly the ones a new feature adds. This
    reads the diff for modified files AND every line of each untracked file.
    """
    cmd = ["git", "diff", "HEAD", "--unified=0"]
    if paths:
        cmd += ["--"] + paths
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout
    lines = []
    current = "?"
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append((current, line[1:]))

    # ls-files prints paths relative to the CURRENT directory, while the diff
    # above prints them relative to the repo root. Joining an ls-files path
    # onto the repo root builds a path that does not exist, and a swallowed
    # OSError then skips exactly the new files this scan exists to check.
    # Resolve against cwd, and refuse to skip a file silently.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, encoding="utf-8").stdout.split()
    skipped = []
    for rel in untracked:
        if not rel.endswith((".py", ".md", ".json", ".js", ".css", ".html")):
            continue
        path = Path(rel)
        if not path.exists():
            skipped.append(rel)
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for text in body.splitlines():
            lines.append((rel, text))
    if skipped:
        raise SystemExit(
            "dash_scan could not read these untracked files, so the scan "
            "would have passed over them:\n  " + "\n  ".join(skipped))
    return lines


# Two exemptions, both stated rather than hidden, because a skip nobody can
# see is how a scan reports a clean zero over files it never judged.
#
# punchlist.md is the FROZEN contract: it quotes the characters in order to
# require that they be absent, and editing it would break the freeze hash.
# The four test docstrings predate this change and belong to other work in
# this same working tree; scoping to this change's files is the honest line,
# rather than claiming a repo-wide clean the scan did not earn.
EXEMPT = {
    "objectives/travel-logistics/punchlist.md",
    "tests/test_audit_evidence.py",
    "tests/test_audit_ledger.py",
    "tests/test_interview_capture.py",
    "tests/test_run_ledger.py",
}


def scan(lines):
    return [(f, t) for f, t in lines
            if (EM in t or EN in t) and f not in EXEMPT]


def main():
    if "--selftest" in sys.argv:
        planted = [("planted.py", f'x = "a {EM} b"'), ("clean.py", "y = 1")]
        hits = scan(planted)
        if len(hits) == 1 and hits[0][0] == "planted.py":
            print("SELFTEST PASS: the scanner finds a planted em-dash.")
            return 0
        print(f"SELFTEST FAIL: planted instance not detected (hits={hits})")
        return 2

    lines = added_lines()
    hits = scan(lines)
    print(f"scanned {len(lines)} added lines across this change")
    if hits:
        print(f"FAIL: {len(hits)} added lines carry an em or en dash:")
        for path, text in hits:
            print(f"  {path}: {text.strip()[:80]}")
        return 1
    print("PASS: zero em or en dashes on added lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
