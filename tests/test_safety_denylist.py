"""The real-identity deny list has to be able to fail.

safety_checks.py grades P17 by grepping the shipped tree for REAL_IDENTITIES.
A pattern that matches nothing and a tree that contains nothing both print
"hits: 0", so a silently broken deny list reads exactly like a clean repo. That
is not hypothetical: rounds 1-5 renamed people and left their employers behind
("Spencer Shweky sshweky@kands.com" -> "Spencer Hale shale@kands.com"), the
list had no domain entries, and the final round graded PASS while
ops@deerfield.com was still shipping.

These tests plant each shape the list is supposed to see, so a refactor of _SEP
or the alternation cannot quietly stop matching.

safety_checks.py is a script, not a module: it runs its whole suite at import
and ends in sys.exit(), which would abort the pytest process. So the regex is
lifted out of the source with ast instead of imported.
"""
import ast
import re
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parent.parent
        / "objectives" / "ship-ready-review" / "safety_checks.py")


def _real_identities():
    """Compile REAL_IDENTITIES without executing the rest of the script."""
    ns = {"re": re}
    for node in ast.parse(_SRC.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") in ("_SEP", "REAL_IDENTITIES")):
            exec(compile(ast.Module([node], []), str(_SRC), "exec"), ns)
    return ns["REAL_IDENTITIES"]


RX = _real_identities()

# Every spelling the list must catch. The separator variants matter because the
# same identity is written three ways across a repo.
MUST_FLAG = [
    "Spencer Hale shale@kands.com",     # domain left behind after a rename
    "K and S LLP",
    "k-and-s",
    "K.and.S",
    "ops@deerfield.com",                # domain left behind after a rename
    "Deerfield Partners",
    "Crooked Fork",
    "crooked-fork",
    "CrookedFork is a solar project",
    "pd46energy.com",                   # pre-existing entries, guarded too
    "pd-46-energy",
    "nobel@accretuspartners.com",
    "Palladium Energy",
]

# Fixtures and ordinary prose. A deny list that fires on these is worse than
# useless: every run becomes noise a reader learns to skip.
MUST_NOT_FLAG = [
    "shale@rowanpartners.com",
    "ops@fairview.com",
    "Sunfield is a solar project",
    "wiki/entities/Halcyon.md",
    "Cascade 2.0",
    "x@nomatch.com",
    "acme.com",
    # "k and s" must not match inside ordinary words -- the \b before it is what
    # keeps "check and see" from reading as an identity.
    "check and see",
    "work and study",
    "the deck and slides",
]


@pytest.mark.parametrize("planted", MUST_FLAG)
def test_deny_list_sees_every_planted_identity(planted):
    assert RX.search(planted), f"deny list went blind to {planted!r}"


@pytest.mark.parametrize("innocent", MUST_NOT_FLAG)
def test_deny_list_leaves_fixtures_and_prose_alone(innocent):
    hit = RX.search(innocent)
    assert hit is None, f"{innocent!r} falsely flagged as {hit.group(0)!r}"


def test_the_shipped_tree_carries_none_of_them():
    """The check the punchlist actually grades, run over the scoped tree.

    objectives/ is excluded on purpose: it holds the deny list itself and the
    audit history, which cannot avoid naming what they deny. This file is
    excluded for the same reason -- its plant corpus is the identities.
    """
    root = _SRC.resolve().parent.parent.parent
    me = Path(__file__).resolve()
    scoped = [p for d in ("app", "skills", "tests", "vault") for p in (root / d).rglob("*")
              if p.is_file() and p.suffix in {".py", ".md", ".json", ".js", ".css", ".html"}
              and p.resolve() != me]
    scoped += [root / n for n in ("README.md", "CLAUDE.md", "config.template.json")
               if (root / n).exists()]
    assert scoped, "found no files to scan -- the guard would pass vacuously"

    hits = []
    for p in scoped:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = RX.search(line)
            if m:
                hits.append(f"{p.relative_to(root)}:{i} {m.group(0)!r}")
    assert not hits, "real identities in the shipped tree:\n  " + "\n  ".join(hits)
