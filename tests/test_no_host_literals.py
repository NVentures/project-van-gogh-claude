"""No module but app/host.py may spell a client.

Van Gogh runs under Claude Code and under OpenAI Codex, and the port holds
only while every host difference stays inside `app/host.py`. This test reads
the syntax tree of every other app module and fails on the shapes that
leaked before the seam existed: a subprocess list that starts with a client
binary's name, a `-p` or `exec` or `plugin` or `auth` word right after a
resolver call, and a string that names a client's home folder, its
instructions file, or a skill with the client's own prefix.

Docstrings are not scanned (an Expr whose value is a string constant), and
comments are not in the tree, so a module may still explain itself.

Three modules are Claude-only by decision (the claude.ai connectors have no
Codex equivalent and Claude Code's memory folders are Claude's), and each
one must say so in code with `host.is_claude()`, which this test checks for
rather than exempting the file.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# Claude-only by decision: these may keep Claude's own words, and each must
# branch on host.is_claude() so the Codex path is a sentence, never a crash.
CLAUDE_ONLY = {"connector_fetch.py", "connector_lookup.py", "memory_sync.py"}

BINARIES = {"claude", "codex"}
WORDS_AFTER_RESOLVER = {"-p", "exec", "plugin", "auth"}
PATH_FRAGMENTS = ("~/.claude", "~/.codex", ".claude/", ".codex/", "CLAUDE.md",
                  "AGENTS.md", "/van-gogh:", "$van-gogh:")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def offenders_in(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    skip = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in BINARIES:
                found.append(f"{path.name}:{node.lineno} subprocess list starts with "
                             f"{first.value!r}")
            elif len(node.elts) > 1 and not isinstance(first, ast.Constant):
                second = node.elts[1]
                if isinstance(second, ast.Constant) and second.value in WORDS_AFTER_RESOLVER:
                    found.append(f"{path.name}:{node.lineno} client word "
                                 f"{second.value!r} after a resolver")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            for frag in PATH_FRAGMENTS:
                if frag in node.value:
                    found.append(f"{path.name}:{node.lineno} string names {frag!r}")
                    break
    return found


def _claude_only_branches(path: Path) -> bool:
    return bool(re.search(r"\bhost\.is_claude\(", path.read_text(encoding="utf-8")))


def test_no_module_but_host_spells_a_client():
    offenders = []
    for path in sorted(APP.glob("*.py")):
        if path.name == "host.py":
            continue
        if path.name in CLAUDE_ONLY:
            assert _claude_only_branches(path), (
                f"{path.name} is Claude-only by decision and must branch on "
                "host.is_claude() so the Codex path is a sentence")
            continue
        offenders.extend(offenders_in(path))
    assert not offenders, (
        "A client is spelled outside app/host.py. Route it through host.* "
        "(bin, cmd, skill_cmd, completion_cmd, readonly_cmd, agent_cmd, "
        "personal_skills_dir, instructions_filename):\n" + "\n".join(offenders))


def test_every_claude_only_module_exists():
    for name in CLAUDE_ONLY:
        assert (APP / name).exists(), f"{name} named in CLAUDE_ONLY but absent"


def test_the_guard_finds_a_planted_literal():
    """A scanner that has never found anything proves nothing."""
    planted = (
        "def f(claude_bin):\n"
        "    a = ['codex', 'exec', 'x']\n"
        "    b = [claude_bin(), '-p', 'x']\n"
        "    c = 'open ~/.claude/skills'\n"
        "    d = 'type /van-gogh:week'\n"
        "    return a, b, c, d\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "planted.py"
        p.write_text(planted, encoding="utf-8")
        found = offenders_in(p)
    kinds = "\n".join(found)
    assert "starts with 'codex'" in kinds
    assert "client word '-p'" in kinds
    assert "'~/.claude'" in kinds
    assert "'/van-gogh:'" in kinds
    assert len(found) == 4, found


def test_a_docstring_is_not_an_offence():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.py"
        p.write_text('"""Run claude -p /van-gogh:week from ~/.claude."""\n'
                     "def f():\n    '''type /van-gogh:week'''\n    return 1\n",
                     encoding="utf-8")
        assert offenders_in(p) == []
