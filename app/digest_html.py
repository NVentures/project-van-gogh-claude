"""Render briefing markdown as email-safe HTML for the digest sends.

Email clients (Gmail especially) strip <head>/<style> blocks, so every
element carries inline styles. Checkboxes are rendered as glyphs, not
<input> elements, which email clients handle inconsistently.
"""
from __future__ import annotations

import re
from html import escape

_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
         "Arial,sans-serif")
_BORDER = "#d0d7de"
_MUTED = "#8b949e"
_SHADE = "#f6f8fa"

_HR = f'<hr style="border:none;border-top:1px solid {_BORDER};margin:14px 0">'
_H_SIZES = {1: "1.5em", 2: "1.25em", 3: "1.1em", 4: "1em"}
_INDENT_PX = 12
_CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\]([+-]?)\s*(.*)$")


def _strip_frontmatter(lines: list[str]) -> list[str]:
    # Only strip a leading fence pair when the block actually looks like
    # YAML frontmatter (key: value lines) — a briefing that merely opens
    # with a --- divider must not lose its top section.
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                block = [ln.strip() for ln in lines[1:i] if ln.strip()]
                if block and all(re.match(r"^[A-Za-z0-9_-]+:", ln)
                                 for ln in block):
                    return lines[i + 1:]
                break
    return lines


def _inline(text: str) -> str:
    """Escape one text segment and apply inline markdown (bold/italic/code).

    Applied per segment — never on assembled HTML — so a stray ** or
    backtick in one table cell can't wrap tags spanning into the next.
    """
    s = escape(text)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        r'<a href="\2" style="color:#0969da;text-decoration:underline">\1</a>',
        s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    # Italic requires non-space bounds so literal asterisks in prose
    # ("*.py", "3 * 4") pass through untouched.
    s = re.sub(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])",
               r"<i>\1</i>", s)
    s = re.sub(
        r"`(.+?)`",
        rf'<code style="background:{_SHADE};padding:1px 5px;border-radius:3px;'
        r'font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px">'
        r"\1</code>",
        s)
    return s


def _heading(level: int, text: str) -> str:
    style = f"font-size:{_H_SIZES[level]};margin:18px 0 8px"
    if level == 2:
        style += f";border-bottom:1px solid {_BORDER};padding-bottom:4px"
    return f'<h{level} style="{style}">{_inline(text)}</h{level}>'


def md_to_email_html(md: str) -> str:
    """Render briefing markdown as email-safe HTML (inline styles only)."""
    # Per-line only (no DOTALL): a stray <!-- inside briefing content —
    # which quotes external email text — must not swallow later lines.
    md = re.sub(r"<!--.*?-->", "", md)
    lines = _strip_frontmatter(md.splitlines())

    out: list[str] = []
    in_fold = False
    in_table = False
    table_row = 0
    table_cols = 0

    for line in lines:
        # A fold. An email client cannot open one, so the fold becomes a
        # shaded block carrying its title: the reader still gets everything,
        # visibly separated from the front page above it.
        m = _CALLOUT_RE.match(line)
        if m:
            if in_fold:
                out.append("</div>")
            out.append(
                f'<div style="background:{_SHADE};border:1px solid {_BORDER};'
                'border-radius:5px;padding:10px 14px;margin:12px 0">'
                f'<div style="font-weight:600;margin-bottom:4px">'
                f"{_inline(m.group(3).strip())}</div>")
            in_fold = True
            continue
        if in_fold:
            if line.startswith(">"):
                line = re.sub(r"^>\s?", "", line)
            elif line.strip():
                out.append("</div>")
                in_fold = False

        # Table rows
        if line.lstrip().startswith("|"):
            stripped = line.strip()
            if not in_table:
                out.append('<table style="border-collapse:collapse;margin:10px 0">')
                in_table = True
                table_row = 0
            if re.match(r"^\|[\s\-:|]+\|$", stripped):  # separator row
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Normalize body rows to the header's width — a stray | inside
            # cell content (e.g. an email subject) must not shift columns.
            if table_row == 0:
                table_cols = len(cells)
            elif len(cells) > table_cols:
                cells = cells[:table_cols - 1] + \
                    [" | ".join(cells[table_cols - 1:])]
            elif len(cells) < table_cols:
                cells = cells + [""] * (table_cols - len(cells))
            cell_style = (f"border:1px solid {_BORDER};padding:5px 10px;"
                          "font-size:14px")
            if table_row == 0:
                row = "".join(
                    f'<th style="{cell_style};background:{_SHADE};'
                    f'text-align:left">{_inline(c)}</th>' for c in cells)
            else:
                shade = f";background:{_SHADE}" if table_row % 2 == 0 else ""
                row = "".join(
                    f'<td style="{cell_style}{shade}">{_inline(c)}</td>'
                    for c in cells)
            out.append(f"<tr>{row}</tr>")
            table_row += 1
            continue
        if in_table:
            out.append("</table>")
            in_table = False

        # Headings
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            out.append(_heading(len(m.group(1)), m.group(2)))
            continue

        # Checked checkbox — must match before generic bullet
        m = re.match(r"^(\s*)-\s+\[x\]\s+(.+)$", line, re.IGNORECASE)
        if m:
            ml = len(m.group(1)) * _INDENT_PX
            out.append(f'<div style="margin:3px 0 3px {ml}px;color:{_MUTED}">'
                       f'&#9745;&nbsp;<s>{_inline(m.group(2))}</s></div>')
            continue

        # Unchecked checkbox
        m = re.match(r"^(\s*)-\s+\[ \]\s+(.+)$", line)
        if m:
            ml = len(m.group(1)) * _INDENT_PX
            out.append(f'<div style="margin:3px 0 3px {ml}px">'
                       f'&#9744;&nbsp;{_inline(m.group(2))}</div>')
            continue

        # Bullet (markdown dash or terminal-residue •)
        m = re.match(r"^(\s*)[-•]\s+(.+)$", line)
        if m:
            ml = len(m.group(1)) * _INDENT_PX
            out.append(f'<div style="margin:2px 0 2px {ml}px">'
                       f'&#8226; {_inline(m.group(2))}</div>')
            continue

        # Horizontal rule / ASCII or box-drawing divider
        if re.match(r"^[-=]{3,}$", line.strip()) or \
                re.match(r"^[─━═]{3,}$", line.strip()):
            out.append(_HR)
            continue

        # Blank line
        if not line.strip():
            out.append('<div style="height:6px"></div>')
            continue

        # Default paragraph (leading spaces — e.g. checkbox continuation
        # lines in week.md — become a left margin, which HTML would collapse)
        m = re.match(r"^(\s*)(.*)$", line)
        ml = len(m.group(1)) * _INDENT_PX
        indent = f";margin-left:{ml}px" if ml else ""
        out.append(f'<p style="margin:5px 0{indent}">{_inline(m.group(2))}</p>')

    if in_table:
        out.append("</table>")
    if in_fold:
        out.append("</div>")

    body = "\n".join(out)
    return (f'<div style="max-width:680px;margin:0 auto;padding:20px 24px;'
            f"font-family:{_FONT};font-size:15px;line-height:1.55;"
            f'color:#24292f">\n{body}\n</div>')
