"""Guard against bare text-mode ``open()`` calls in ``app/``.

On Windows ``open()`` defaults to the locale codec (cp1252), so a UTF-8 vault
file with em-dashes is mis-decoded and downstream regexes silently fail to
match — which is exactly how the morning-coffee briefing once came back empty.
The CLAUDE.md rule is unambiguous: always read/write text with
``encoding="utf-8"``. This test fails if any builtin ``open()`` in ``app/``
opens in text mode without an explicit ``encoding`` argument.
"""
import ast
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _is_binary_mode(call: ast.Call) -> bool:
    """True if the call's mode string contains 'b' (binary — no encoding allowed)."""
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _bare_open_offenders(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        # Only the builtin open(...) — not Path.open(...) (an Attribute call).
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        if _is_binary_mode(node):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        offenders.append(node.lineno)
    return offenders


def test_app_open_calls_specify_utf8():
    offenders = []
    for path in sorted(_APP_DIR.glob("*.py")):
        for lineno in _bare_open_offenders(path):
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "Text-mode open() without encoding=\"utf-8\" found (cross-platform bug):\n"
        + "\n".join(offenders)
    )


def test_scan_covers_app():
    files = list(_APP_DIR.glob("*.py"))
    assert len(files) > 10, f"expected many app scripts, got {len(files)}"
