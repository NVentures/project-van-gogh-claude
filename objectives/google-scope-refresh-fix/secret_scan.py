"""Punch item P18: no credential and no client identity in what this leaves behind.

Two different risks, one scan.

Secrets: the live probe holds real tokens in memory and the proof folder is
written from real runs, so both are scanned for token shapes.

Identity: the client whose install surfaced this bug must not be named in the
code, the tests or the release notes. A test fixture carrying a real company
name is the same defect that failed test_safety_denylist on the inherited
0.75.1 work.

    python3 secret_scan.py --selftest
    python3 secret_scan.py
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ROOT = REPO.parent
PROOF = Path.home() / "Downloads" / "proof-google-scope-refresh-fix"

SECRETS = [
    ("google refresh token", re.compile(r"1//0[A-Za-z0-9_\-]{20,}")),
    ("google access token", re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}")),
    ("microsoft token", re.compile(r"\b0\.A[A-Za-z0-9_\-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("client secret", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}")),
]
# The client's own identity, from the report that opened this bug.
IDENTITY = re.compile(r"suncast|nico@|nico\b", re.I)


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
    planted = {
        "google refresh token": "GOOGLE_REFRESH_TOKEN_X=1//0gABCDEFGHIJKLMNOPQRSTUVWX",
        "google access token": "Bearer ya29.a0ABCDEFGHIJKLMNOPQRSTUVWXYZ012",
        "microsoft token": "refresh=0.AXkAbCdEfGhIjKlMnOpQrStUvWxYz0123456789ab",
        "jwt": "id_token=eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY",
        "client secret": "client_secret=GOCSPX-abcdefghijkl",
    }
    ok = True
    for name, rx in SECRETS:
        hit = bool(rx.search(planted[name]))
        print(f"  selftest {name}: {'found' if hit else 'MISSED'}")
        ok = ok and hit
    clean = "scopes=None and a label like Personal, no credentials here"
    if any(rx.search(clean) for _, rx in SECRETS):
        print("  selftest clean line: FALSE POSITIVE")
        ok = False
    else:
        print("  selftest clean line: correctly silent")
    ident = bool(IDENTITY.search("the Suncast install reported it"))
    print(f"  selftest identity: {'found' if ident else 'MISSED'}")
    print("SELFTEST", "ok" if ok and ident else "FAIL")
    return 0 if (ok and ident) else 1


def _scan_tree(root: Path, label: str, patterns, checked):
    """Scan a tree, skipping only this scanner itself.

    A scanner holding planted fixtures of what it hunts always flags itself,
    and "fixing" that by deleting the fixtures removes the only proof the
    patterns work. So exactly one file is exempt, by resolved path, and it is
    the file whose fixtures the selftest just exercised.
    """
    hits = []
    me = Path(__file__).resolve()
    if not root.exists():
        print(f"  {label}: not present")
        return hits
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.resolve() == me:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            print(f"  BINARY or unreadable, not scanned: {path}")
            continue
        checked.append(path)
        for i, line in enumerate(text.splitlines(), 1):
            for name, rx in patterns:
                if rx.search(line):
                    hits.append(f"{path}:{i} {name}")
    return hits


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if selftest() != 0:
        return 1

    checked = []
    hits = _scan_tree(HERE, "objective folder", SECRETS, checked)
    hits += _scan_tree(PROOF, "proof folder", SECRETS, checked)
    print(f"\nscanned {len(checked)} files for credentials")

    ident = []
    current = None
    scanned_diff_lines = 0
    for line in _diff_lines():
        scanned_diff_lines += 1
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            if IDENTITY.search(line) and "secret_scan" not in current:
                ident.append(f"{current}: {line.strip()[:80]}")
    for rel in _git("ls-files", "--others", "--exclude-standard").splitlines():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if IDENTITY.search(line):
                ident.append(f"{rel}:{i} {line.strip()[:80]}")

    for h in hits + ident:
        print("  HIT", h)
    if not checked and not scanned_diff_lines:
        print("\nRESULT SKIP: nothing to scan. Re-run with --commit <sha> "
              "once the work is committed. A pass over an empty input would "
              "mean nothing.")
        return 2
    total = len(hits) + len(ident)
    print(f"\nRESULT {'PASS' if total == 0 else 'FAIL'}: "
          f"{len(hits)} credential hits, {len(ident)} identity hits")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
