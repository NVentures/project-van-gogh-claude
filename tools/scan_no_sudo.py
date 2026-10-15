#!/usr/bin/env python3
"""scan_no_sudo.py: nothing reachable from the setup scripts asks for admin.

Walks every file reachable from `setup.sh` and `setup.ps1` (the two scripts,
the `check.*` scripts they call, every `app/*.py` they invoke, and the app
modules those import, transitively) and fails on any executable line that
invokes `sudo`, `brew `, `winget`, `choco`, `Start-Process -Verb RunAs` or
`osascript ... with administrator privileges`.

Comments and Python docstrings are not scanned: the scripts' own headers say
"no sudo" in prose, and a scanner that fails on the sentence forbidding the
thing is one somebody turns off. Prints the files-scanned count and refuses
to pass on 0 files (exit 2, SKIP), so a broken walk can never read as clean.

Usage: python tools/scan_no_sudo.py [--root DIR]
Exit 0 = clean, 1 = hits found, 2 = nothing scanned.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ENTRY_SCRIPTS = ("setup.sh", "setup.ps1")

PATTERNS = (
    ("sudo", re.compile(r"(?<![\w./-])sudo\b")),
    ("brew", re.compile(r"(?<![\w./-])brew\s")),
    ("winget", re.compile(r"(?<![\w./-])winget\b")),
    ("choco", re.compile(r"(?<![\w./-])choco\b")),
    ("RunAs", re.compile(r"Start-Process\b.*-Verb\s+RunAs", re.IGNORECASE)),
    ("admin dialog", re.compile(r"osascript\b.*with administrator privileges")),
)

_SCRIPT_REF = re.compile(r"(?<![\w-])(check\.(?:sh|ps1)|setup\.(?:sh|ps1))\b")
_APP_REF = re.compile(r"app[/\\]+(\w+)\.py\b")


def _strip_hash_comments(text: str):
    """(lineno, code) for sh and ps1: a `#` at line start or after whitespace
    starts a comment."""
    for i, line in enumerate(text.splitlines(), start=1):
        code = re.split(r"(?:^|\s)#", line, maxsplit=1)[0]
        yield i, code


def _strip_python(text: str):
    """(lineno, code) for python: comments and docstrings removed."""
    lines = text.splitlines()
    skip: set[int] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                skip.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    cuts: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                cuts[tok.start[0]] = tok.start[1]
    except (tokenize.TokenError, SyntaxError):
        pass
    for i, line in enumerate(lines, start=1):
        if i in skip:
            continue
        yield i, line[:cuts[i]] if i in cuts else line


def executable_lines(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return list(_strip_python(text))
    return list(_strip_hash_comments(text))


def _python_references(text: str, root: Path) -> list[Path]:
    """Everything a Python file reaches: static imports at any depth, and
    every string it hands to a call that names a script (`"warm_start.py"`,
    `"vault/scaffold.py"`) or an app module (`_try_import("keep_awake")`,
    `import_module("x")`). Over-following is the safe direction: a module
    scanned for nothing costs a read, a module missed hides the sudo."""
    out: list[Path] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(root / "app" / f"{a.name.split('.')[0]}.py" for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(root / "app" / f"{node.module.split('.')[0]}.py")
        elif isinstance(node, ast.Call):
            values = [a for a in node.args] + [k.value for k in node.keywords]
            for value in values:
                strings = [value] if isinstance(value, ast.Constant) else (
                    list(value.elts) if isinstance(value, (ast.List, ast.Tuple)) else [])
                for item in strings:
                    if not (isinstance(item, ast.Constant) and isinstance(item.value, str)):
                        continue
                    name = item.value
                    if name.endswith(".py"):
                        out.append(root / "app" / name)
                        out.append(root / name)
                    elif re.fullmatch(r"\w+", name):
                        out.append(root / "app" / f"{name}.py")
    return out


def references(path: Path, root: Path) -> list[Path]:
    """Files this one reaches: named scripts, app/ scripts, python imports
    and the module and script names passed to calls."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[Path] = []
    for name in _SCRIPT_REF.findall(text):
        out.append(root / name)
    for name in _APP_REF.findall(text):
        out.append(root / "app" / f"{name}.py")
    if path.suffix == ".py":
        out.extend(_python_references(text, root))
    return [p for p in out if p.is_file()]


def reachable(root: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    queue = [root / name for name in ENTRY_SCRIPTS if (root / name).is_file()]
    while queue:
        path = queue.pop(0).resolve()
        if path in seen:
            continue
        seen[path] = None
        queue.extend(references(path, root))
    return list(seen)


def scan(root: Path) -> tuple[list[tuple[str, int, str, str]], int]:
    """(hits, files_scanned); a hit is (relative path, lineno, label, line)."""
    hits = []
    files = reachable(root)
    for path in files:
        for lineno, code in executable_lines(path):
            for label, pattern in PATTERNS:
                if pattern.search(code):
                    hits.append((str(path.relative_to(root)), lineno, label, code.strip()))
    return hits, len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="plugin root holding setup.sh and setup.ps1")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    hits, count = scan(root)
    print(f"scanned {count} file(s) reachable from {', '.join(ENTRY_SCRIPTS)} under {root}")
    if count == 0:
        print("SKIP: nothing scanned (no setup.sh or setup.ps1 at the root), so no verdict")
        return 2
    for rel, lineno, label, line in hits:
        print(f"HIT {label}: {rel}:{lineno}: {line}")
    if hits:
        print(f"FAIL: {len(hits)} privilege escalation(s) found")
        return 1
    print("PASS: no sudo, brew, winget, choco, RunAs or admin dialog reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
