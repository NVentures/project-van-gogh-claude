"""Guard against re-introducing cross-platform portability regressions.

Checks that app/ source files honour the cross-platform mandate from CLAUDE.md:
  - text opens always carry encoding="utf-8"
  - rstrip never targets only "\\n" (would leave \\r on Windows)
  - strftime never uses POSIX-only %-I / %-d flags
  - claude subprocess calls use claude_bin() from platform_compat, not a bare "claude" literal
"""
import re
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"

# This test file itself mentions the forbidden patterns in strings/comments.
_SELF = Path(__file__).name

_PY_FILES = sorted(f for f in _APP.glob("*.py") if f.name != _SELF)


# ── helpers ───────────────────────────────────────────────────────────────────

def _violations(files, pattern, exclude_re=None):
    """Return (relpath:line: text) for every line matching pattern."""
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line) and (exclude_re is None or not exclude_re.search(line)):
                hits.append(f"{path.name}:{lineno}: {stripped}")
    return hits


# ── tests ─────────────────────────────────────────────────────────────────────

# open() calls that are plainly text-mode but missing encoding=
# Matches open(...) with 0 or 1 positional arg that does NOT already contain
# encoding= and is NOT a binary-mode open ("rb", "wb", "ab").
_OPEN_RE = re.compile(r'(?<![.\w])open\(')
_BINARY_MODE_RE = re.compile(r"""open\([^)]*['"][rwab]+b['"]""")


def _looks_like_a_string_literal(line: str) -> bool:
    """True when every `open(` on this line sits inside quotes.

    Deliberately narrow: it walks the line tracking whether it is inside a
    string, rather than guessing from the presence of a quote character, so a
    genuine `open(path)` after a string on the same line still counts.
    """
    quote = None
    idx = 0
    while idx < len(line):
        ch = line[idx]
        if quote:
            if ch == "\\":
                idx += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif line.startswith("open(", idx) or line.startswith(" open(", idx):
            return False            # a real call, outside any string
        idx += 1
    return True


def test_open_has_encoding():
    hits = []
    for path in _PY_FILES:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not _OPEN_RE.search(line):
                continue
            if "encoding=" in line:
                continue
            if _BINARY_MODE_RE.search(line):
                continue
            # A regex or a message that happens to contain the word "open("
            # is not a file being opened. Without this the guard fires on a
            # self-harm pattern in quotes.py ("open(?:ing)? your veins") and
            # an audit that cries wolf is one somebody turns off.
            if _looks_like_a_string_literal(line):
                continue
            hits.append(f"{path.name}:{lineno}: {stripped}")
    assert not hits, (
        "open() calls without encoding= found (will mojibake on Windows):\n"
        + "\n".join(hits)
    )


_READ_TEXT_RE = re.compile(r'\.read_text\(\)')
_WRITE_TEXT_RE = re.compile(r'\.write_text\(')


def test_read_write_text_has_encoding():
    """read_text() / write_text() must carry encoding="utf-8".

    write_text args often span multiple lines, so we check the call site line
    plus its continuation lines (up to the next statement) for encoding=.
    For read_text() the check is single-line only (it has no args to carry it).
    """
    hits = []
    for path in _PY_FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped or "'''" in stripped:
                continue
            # read_text() must be on same line
            if _READ_TEXT_RE.search(line):
                hits.append(f"{path.name}:{lineno}: {stripped}")
            # write_text( scan this line plus continuations until unindented line
            elif _WRITE_TEXT_RE.search(line) and "encoding=" not in line:
                # gather continuation lines (next lines more indented or blank)
                combined = line
                for cont in lines[lineno:lineno + 15]:
                    combined += cont
                    if "encoding=" in combined:
                        break
                    if cont.strip() and not cont.startswith(" ") and not cont.startswith("\t"):
                        break
                if "encoding=" not in combined:
                    hits.append(f"{path.name}:{lineno}: {stripped}")
    assert not hits, (
        ".read_text() / .write_text() without encoding= found:\n" + "\n".join(hits)
    )


_RSTRIP_N_RE = re.compile(r"""rstrip\(['"]\\n['"]\)""")

def test_no_rstrip_newline_only():
    """rstrip("\\n") leaves \\r on Windows; use rstrip("\\r\\n") or .rstrip()."""
    hits = _violations(_PY_FILES, _RSTRIP_N_RE)
    assert not hits, (
        'rstrip("\\n") leaves \\r on Windows, use rstrip("\\r\\n"):\n'
        + "\n".join(hits)
    )


_POSIX_STRFTIME_RE = re.compile(r'strftime\([^)]*%-[Id]')

def test_no_posix_strftime_flags():
    """%-I / %-d are POSIX-only; use fmt_hour_minute() from platform_compat."""
    hits = _violations(_PY_FILES, _POSIX_STRFTIME_RE)
    assert not hits, (
        "POSIX-only strftime flags (%-I / %-d) found, use fmt_hour_minute():\n"
        + "\n".join(hits)
    )


_CLAUDE_LITERAL_RE = re.compile(r"""\[['"]claude['"],\s*['"]-p['"]""")

def test_claude_bin_not_literal():
    """\"claude\" must be resolved via claude_bin() so Windows can find the executable."""
    hits = _violations(_PY_FILES, _CLAUDE_LITERAL_RE)
    assert not hits, (
        'Bare ["claude", "-p", ...] found, use [claude_bin(), "-p", ...] '
        "from platform_compat:\n" + "\n".join(hits)
    )


# ── Windows realities that never bite on a developer's Mac ───────────────────

def test_crlf_follow_ups_ledger_parses_identically_to_lf(tmp_path):
    """A ledger edited in Notepad has CRLF endings. It has to parse the same."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    import follow_up_radar as fr
    from datetime import date

    lf = ("## Pending follow-ups\n\n"
          "- [ ] (ACME) Send the widget redline | due: 2026-07-10 | format: email\n"
          "- [x] (ACME) Gadget deck | due: 2026-07-09 | format: ppt\n")
    crlf = lf.replace("\n", "\r\n")
    today = date(2026, 7, 16)

    a = fr.parse_follow_ups(lf, today, 4)
    b = fr.parse_follow_ups(crlf, today, 4)
    assert [(i["title"], i["due"], i["done"], i["status"]) for i in a] == \
           [(i["title"], i["due"], i["done"], i["status"]) for i in b]


def test_vault_path_with_a_space_and_a_non_ascii_character(tmp_path, monkeypatch):
    """OneDrive paths look like "C:\\Users\\x\\OneDrive - Acme Corp\\Vault" and
    plenty of people have an accent in their folder name."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    import config_loader as cl

    vault = tmp_path / "OneDrive - Acmé Corp" / "My Vault"
    (vault / "van-gogh").mkdir(parents=True)
    saved = cl._config
    cl._config = dict(saved)
    cl._config["obsidian"] = dict(cl._config["obsidian"], vault_path=str(vault))
    try:
        assert cl.vault().exists()
        assert cl.van_gogh_root().name == "van-gogh"
        assert str(cl.hotcache_path()).startswith(str(vault))
    finally:
        cl._config = saved


def test_every_bash_block_in_a_skill_has_a_powershell_twin():
    """A macOS-only command block is a Windows user stuck on step one."""
    import re
    skills = Path(__file__).resolve().parents[1] / "skills"
    missing = []
    for md in skills.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        blocks = re.findall(r"```(bash|powershell)", text)
        bash = blocks.count("bash")
        ps = blocks.count("powershell")
        # Not one-for-one: a skill may show several bash forms under one
        # PowerShell equivalent. The failure being hunted is bash with NO
        # PowerShell anywhere in the file.
        if bash and not ps:
            missing.append(md.name)
    assert not missing, f"bash blocks with no PowerShell form: {missing}"
