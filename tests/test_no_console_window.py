"""Every subprocess spawn in app/ must pass creationflags=NO_WINDOW.

On Windows, a console-subsystem child spawned from a window-less parent
(Claude Code's hidden tool shell, Task Scheduler) allocates its own conhost
window, which flashes in the foreground — reported by Windows users on every
skill run. platform_compat.NO_WINDOW suppresses it and is a no-op elsewhere;
this test keeps a new call site from silently reintroducing the flash.

Enforced per app/*.py file, via AST:
- every ``subprocess.<spawn>(...)`` call passes ``creationflags=NO_WINDOW``
  (the exact name, not just any value — ``creationflags=0`` would defeat it);
- no ``from subprocess import run`` / ``import subprocess as sp`` aliasing
  that would dodge the attribute match above;
- no ``os.system`` / ``os.popen`` / ``os.spawn*`` / ``os.startfile`` calls —
  those cannot take creationflags at all.
"""
import ast
import subprocess
import sys
from pathlib import Path

import platform_compat

APP_DIR = Path(__file__).resolve().parent.parent / "app"

_SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}
_OS_SPAWNERS = {"system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe", "startfile"}


def _is_no_window(value: ast.expr) -> bool:
    """The creationflags value must be the NO_WINDOW name (any qualification)."""
    if isinstance(value, ast.Name):
        return value.id == "NO_WINDOW"
    if isinstance(value, ast.Attribute):
        return value.attr == "NO_WINDOW"
    return False


def _offenders(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            bad = sorted(a.name for a in node.names if a.name in _SPAWNERS)
            if bad:
                yield node.lineno, f"from subprocess import {', '.join(bad)} dodges the check"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" and alias.asname:
                    yield node.lineno, "import subprocess as ... dodges the check"
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        if func.value.id == "os" and func.attr in _OS_SPAWNERS:
            yield node.lineno, f"os.{func.attr} spawns a console window and takes no creationflags"
        if func.value.id == "subprocess" and func.attr in _SPAWNERS:
            flags = [kw.value for kw in node.keywords if kw.arg == "creationflags"]
            if not flags:
                yield node.lineno, f"subprocess.{func.attr} without creationflags=NO_WINDOW"
            elif not _is_no_window(flags[0]):
                yield node.lineno, f"subprocess.{func.attr} creationflags is not NO_WINDOW"


def test_every_subprocess_spawn_passes_no_window():
    offenders = []
    for path in sorted(APP_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{path.name}:{line} — {why}" for line, why in _offenders(tree)]
    assert not offenders, (
        "spawns that would flash a console window on Windows:\n  "
        + "\n  ".join(offenders))


def test_no_window_value_matches_platform():
    """Pins the attribute name: a typo in getattr(\"CREATE_NO_WINDOW\") would
    silently fall back to 0 on Windows and re-introduce the flash."""
    expected = 0x08000000 if sys.platform == "win32" else 0
    assert platform_compat.NO_WINDOW == expected
    if sys.platform == "win32":
        assert platform_compat.NO_WINDOW == subprocess.CREATE_NO_WINDOW
