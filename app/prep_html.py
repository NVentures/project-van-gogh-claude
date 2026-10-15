#!/usr/bin/env python3
"""The prep email: one card per call, built to be read on the way in.

A prep is written as markdown and was mailed through the general converter,
which turns headings into headings and bullets into bullets. For a briefing
that is fine. For five calls with seven sections each it produced one long
column of same-weight text, and the reader's complaint was exact: a wall.

What a prep is read for decides the layout. Three questions, in this order:
which call, who is on it, and what do I want out of it. So:

* the day opens with every call on one screen, time beside title, so the
  shape of the day is seen before any of it is read;
* each call is its own card, with its time in the bar across the top;
* inside a card the sections are labelled in small mono and separated by
  space, never by a rule under every row;
* a person's name is the bold thing on their line, and a first meeting is a
  stated word beside it;
* the win condition is the one block set apart, because it is the one line
  worth reading if nothing else is.

The look is DESIGN.md's kneeboard, as `kpi_html` already carries it into mail:
the B612 stacks, the card with a bar, a 2px radius, no shadow, no icon, nothing
centered. Email is a hostile renderer, so this is nested tables with every
style inline. `PALETTE` is a closed set and amber is not in it: amber means
work waiting on the reader, and a prep is context, not a task.

The parser reads the shape the meeting-prep skill writes and nothing cleverer.
A brief it cannot recognise returns "", and the caller falls back to the
general converter: a plain email is a worse email, a mangled one is a broken
one.
"""

from __future__ import annotations

import re
from html import escape

# The only colours in this email. A test asserts nothing else is used.
PALETTE = {
    "bg": "#F3F4F1",
    "card": "#FFFFFF",
    "wash": "#EEF0EE",
    "text": "#1A1F24",
    "muted": "#5C6670",
    "rule": "#C9CFD3",
    "rule_soft": "#E1E5E8",
    "cyan": "#1F7A99",
}

FONT = "'B612','Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'B612 Mono','SF Mono',Menlo,Consolas,'Courier New',monospace"

WIDTH = 640

# The section a card sets apart. Matched on the heading the skill writes.
WIN = "win condition"
POINTS = "talking points"

_TIME_RE = re.compile(
    r"^(?P<time>\d{1,2}:\d{2}\s*[AP]M(?:\s+[A-Z]{2,4})?"
    r"(?:\s*/\s*\d{1,2}:\d{2}\s*[AP]M(?:\s+[A-Z]{2,4})?)?)\s+(?P<title>.+)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+)$")
_NUMBER_RE = re.compile(r"^\s*(?P<n>\d{1,2})[.)]\s+(?P<text>.+)$")
_CHECK_RE = re.compile(r"^\[[ xX]\]\s*")
_TAG_RE = re.compile(r"^\[(?P<tag>[A-Za-z0-9 _\-]{2,24})\]\s*")
_LEAD_RE = re.compile(r"^(?P<lead>(?:\*\*)?[^:.!?]{2,60}?(?:\*\*)?):\s+(?P<rest>.+)$")
_FIRST_RE = re.compile(r"^First meeting\.?\s*", re.IGNORECASE)
_DATE_LEAD_RE = re.compile(
    r"^(?P<date>(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"(?:\s+(?:to|and)\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?))?)"
    r"[,:]\s+(?P<rest>.+)$")


# ── Reading the brief ────────────────────────────────────────────────────────

def parse(md: str) -> dict:
    """The brief as `{"intro", "meetings", "closing"}`.

    `intro` is `{"title", "lines"}` when the brief opens with a day heading.
    Each meeting is `{"time", "title", "notes", "sections"}` and each section
    `{"name", "lines"}`. `closing` is whatever follows the last section of the
    last call and is not part of it ("Also on the calendar: ...").
    """
    intro, meetings, closing = None, [], []
    block = None
    section = None
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            head = line[3:].strip()
            section = None
            if not meetings and intro is None and head.lower().startswith("today"):
                intro = {"title": head, "lines": []}
                block = intro
                continue
            match = _TIME_RE.match(head)
            block = {"time": match.group("time") if match else "",
                     "title": (match.group("title") if match else head).strip(),
                     "notes": [], "sections": []}
            meetings.append(block)
            continue
        if line.startswith("### "):
            if block is None or block is intro:
                continue
            section = {"name": line[4:].strip(), "lines": []}
            block["sections"].append(section)
            continue
        if line.strip() in ("---", "***", ""):
            if section is not None and section["lines"] and line.strip() == "":
                section["lines"].append("")
            continue
        if block is None:
            continue
        if block is intro:
            intro["lines"].append(line)
        elif section is None:
            block["notes"].append(line)
        elif _is_closing(line, section):
            closing.append(line)
            section = {"name": "", "lines": closing}     # the rest follows it
        else:
            section["lines"].append(line)
    for meeting in meetings:
        meeting["sections"] = [s for s in meeting["sections"] if s["name"]]
    return {"intro": intro, "meetings": meetings, "closing": closing}


def _is_closing(line: str, section: dict) -> bool:
    """The line that leaves the last card: `Also on the calendar: ...`."""
    return section["name"].lower() == POINTS and bool(
        re.match(r"^Also\b", line.strip(), re.IGNORECASE))


# ── Small pieces ─────────────────────────────────────────────────────────────

def _inline(text: str) -> str:
    """Escape, then the two inline marks a brief uses: bold and code."""
    s = escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r'<b style="font-weight:700">\1</b>', s)
    s = re.sub(r"`(.+?)`",
               rf'<span style="font-family:{MONO};font-size:13px">\1</span>', s)
    return s


def _label(text: str) -> str:
    return (f'<div style="font-family:{MONO};font-size:11px;line-height:1.2;'
            f'font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
            f'color:{PALETTE["muted"]};padding:0 0 6px">{escape(text)}</div>')


def _para(html: str, top: int = 0) -> str:
    return (f'<div style="font-family:{FONT};font-size:15px;line-height:1.45;'
            f'color:{PALETTE["text"]};padding:{top}px 0 0">{html}</div>')


def _tag(text: str, colour: str = "muted") -> str:
    return (f'<span style="font-family:{MONO};font-size:11px;font-weight:700;'
            f'letter-spacing:.06em;text-transform:uppercase;'
            f'color:{PALETTE[colour]}">{escape(text)}</span>')


# Sections whose items open with a name: a person, or a deal. Elsewhere a
# colon is just punctuation, and bolding what precedes it made a heading out
# of "From the September call with a colleague".
_NAMED_SECTIONS = ("who", "in the mail", "from ", "context")

# The section is called Context. A brief that still writes the old heading
# is relabelled here, so the email reads the same whichever it was given.
_RENAMED = {"deal context": "Context"}


def _named(section_name: str) -> bool:
    return section_name.strip().lower().startswith(_NAMED_SECTIONS)


def _item(text: str, named: bool = False) -> str:
    """One list item: who or when in bold, then what."""
    text = _CHECK_RE.sub("", text.strip())
    tag = ""
    found = _TAG_RE.match(text)
    if found:
        tag = _tag(found.group("tag")) + "&nbsp; "
        text = text[found.end():]
    dated = _DATE_LEAD_RE.match(text)
    if dated:
        lead = (f'<span style="font-family:{MONO};font-size:12.5px;'
                f'color:{PALETTE["muted"]}">{escape(dated.group("date"))}</span>')
        return _para(f'{tag}{lead}&nbsp; {_inline(dated.group("rest"))}')
    found = _LEAD_RE.match(text)
    if found and (named or found.group("lead").startswith("**")):
        lead = found.group("lead").strip("*")
        rest = found.group("rest")
        first = ""
        if _FIRST_RE.match(rest):
            first = "&nbsp; " + _tag("First meeting", "cyan")
            rest = _FIRST_RE.sub("", rest)
        body = f'<div style="padding:2px 0 0">{_inline(rest)}</div>' if rest.strip() else ""
        return _para(f'{tag}<b style="font-weight:700">{escape(lead)}</b>{first}{body}')
    return _para(tag + _inline(text))


def _lines(lines: list, named: bool = False) -> str:
    """A section's body: items spaced apart, prose as paragraphs."""
    out = []
    for line in lines:
        if not line.strip():
            continue
        bullet = _BULLET_RE.match(line)
        # A person written as a line of their own, `**Name**: ...`, is an
        # item whether or not it carries a bullet.
        if bullet:
            html = _item(bullet.group("text"), named)
        elif line.lstrip().startswith("**") and _LEAD_RE.match(line.strip()):
            html = _item(line.strip(), named)
        else:
            html = _para(_inline(line.strip()))
        out.append(f'<tr><td style="padding:0 0 10px">{html}</td></tr>')
    if not out:
        return ""
    return ('<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0">' + "".join(out) + "</table>")


def _points(lines: list) -> str:
    """Talking points: the numeral in mono in its own column."""
    rows = []
    for line in lines:
        if not line.strip():
            continue
        found = _NUMBER_RE.match(line)
        number = found.group("n") if found else ""
        text = found.group("text") if found else line.strip()
        rows.append(
            f'<tr><td width="26" valign="top" style="font-family:{MONO};'
            f'font-size:12.5px;line-height:21.75px;font-weight:700;'
            f'color:{PALETTE["cyan"]};padding:0 0 10px">{escape(number)}</td>'
            f'<td valign="top" style="padding:0 0 10px">{_para(_inline(text))}</td></tr>')
    if not rows:
        return ""
    return ('<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0">' + "".join(rows) + "</table>")


def _win(lines: list) -> str:
    """The one block set apart: a wash, a rule down its left edge."""
    text = " ".join(line.strip() for line in lines if line.strip())
    if not text:
        return ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"><tr><td style="background:{PALETTE["wash"]};'
        f'border-left:3px solid {PALETTE["cyan"]};padding:12px 14px">'
        + _label("Win condition")
        + f'<div style="font-family:{FONT};font-size:15px;line-height:1.45;'
          f'font-weight:700;color:{PALETTE["text"]}">{_inline(text)}</div>'
        + "</td></tr></table>")


def _section(section: dict) -> str:
    name = _RENAMED.get(section["name"].strip().lower(), section["name"].strip())
    low = name.lower()
    if low == WIN:
        body = _win(section["lines"])
        return f'<tr><td style="padding:6px 0 18px">{body}</td></tr>' if body else ""
    body = (_points(section["lines"]) if low == POINTS
            else _lines(section["lines"], _named(name)))
    if not body:
        return ""
    return f'<tr><td style="padding:0 0 8px">{_label(name)}{body}</td></tr>'


def _card(bar_left: str, bar_right: str, inner: str) -> str:
    """A kneeboard card: a bar across the top, the content beneath."""
    right = (f'<td align="right" style="font-family:{MONO};font-size:11px;'
             f'font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
             f'color:{PALETTE["muted"]};padding:9px 16px 9px 8px;white-space:nowrap">'
             f'{escape(bar_right)}</td>') if bar_right else ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{PALETTE["card"]};border:1px solid '
        f'{PALETTE["rule"]};border-radius:2px"><tr><td style="border-bottom:1px solid '
        f'{PALETTE["rule_soft"]}"><table role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-family:{MONO};font-size:11px;font-weight:700;'
        f'letter-spacing:.1em;text-transform:uppercase;color:{PALETTE["text"]};'
        f'padding:9px 8px 9px 16px">{escape(bar_left)}</td>{right}</tr></table></td></tr>'
        f'<tr><td style="padding:16px 16px 8px">{inner}</td></tr></table>')


def _people(meeting: dict) -> str:
    for section in meeting["sections"]:
        if section["name"].lower().startswith("who"):
            count = sum(1 for line in section["lines"]
                        if _BULLET_RE.match(line) or line.lstrip().startswith("**"))
            if count:
                return f"{count} {'person' if count == 1 else 'people'}"
    return ""


_WHEN_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M")


def _when(meeting: dict) -> tuple:
    """`(bar text, notes left over)`. A single prep carries its day and time
    on the line under the title rather than in the heading."""
    if meeting["time"]:
        return meeting["time"], meeting["notes"]
    for i, note in enumerate(meeting["notes"]):
        if _WHEN_RE.search(note) and len(note) < 80:
            when = " ".join(note.replace("|", " ").split())
            return when, meeting["notes"][:i] + meeting["notes"][i + 1:]
    return "Call", meeting["notes"]


def _meeting(meeting: dict) -> str:
    when, left = _when(meeting)
    title = (f'<div style="font-family:{FONT};font-size:18px;line-height:1.3;'
             f'font-weight:700;color:{PALETTE["text"]};padding:0 0 14px">'
             f'{_inline(meeting["title"])}</div>')
    notes = "".join(
        f'<div style="font-family:{FONT};font-size:15px;line-height:1.45;'
        f'color:{PALETTE["muted"]};padding:0 0 12px">{_inline(n.strip())}</div>'
        for n in left if n.strip() and not _is_date_only(n))
    sections = "".join(_section(s) for s in meeting["sections"])
    inner = (title + notes + '<table role="presentation" width="100%" cellpadding="0" '
             'cellspacing="0" border="0">' + sections + "</table>")
    return _card(when, _people(meeting), inner)


def _is_date_only(line: str) -> bool:
    """`Thu Oct 1` under a call heading: the email already carries the date."""
    return bool(re.match(r"^[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}(,\s*\d{4})?$",
                         line.strip()))


def _agenda(meetings: list) -> str:
    rows = []
    for meeting in meetings:
        rows.append(
            f'<tr><td valign="top" style="font-family:{MONO};font-size:12.5px;'
            f'line-height:1.35;color:{PALETTE["muted"]};padding:0 0 2px">'
            f'{escape(meeting["time"])}</td></tr>'
            f'<tr><td valign="top" style="font-family:{FONT};font-size:15px;'
            f'line-height:1.35;font-weight:700;color:{PALETTE["text"]};'
            f'padding:0 0 12px">{_inline(meeting["title"])}</td></tr>')
    inner = ('<table role="presentation" width="100%" cellpadding="0" '
             'cellspacing="0" border="0">' + "".join(rows) + "</table>")
    count = len(meetings)
    return _card("Today", f"{count} {'call' if count == 1 else 'calls'}", inner)


# ── The whole email ──────────────────────────────────────────────────────────

def render(md: str, date_label: str = "") -> str:
    """The brief as an email, or "" when it is not a shape this can lay out."""
    brief = parse(md)
    meetings = [m for m in brief["meetings"] if m["sections"]]
    if not meetings:
        return ""
    gap = '<tr><td style="height:16px;line-height:16px;font-size:0">&nbsp;</td></tr>'
    blocks = []

    head = (f'<div style="font-family:{MONO};font-size:11px;font-weight:700;'
            f'letter-spacing:.1em;text-transform:uppercase;color:{PALETTE["muted"]};'
            f'padding:0 0 6px">{"Daily meeting prep" if brief["intro"] else "Meeting prep"}</div>'
            f'<div style="font-family:{FONT};font-size:26px;line-height:1.1;'
            f'font-weight:700;color:{PALETTE["text"]}">'
            f'{escape(date_label or "Today")}</div>')
    intro = brief["intro"]
    lead = " ".join(l.strip() for l in (intro["lines"] if intro else []) if l.strip())
    if lead:
        head += (f'<div style="font-family:{FONT};font-size:15px;line-height:1.45;'
                 f'color:{PALETTE["text"]};padding:12px 0 0">{_inline(lead)}</div>')
    blocks.append(f'<tr><td style="padding:0 2px 4px">{head}</td></tr>')

    if len(meetings) > 1:
        blocks.append(f"<tr><td>{_agenda(meetings)}</td></tr>")
    for meeting in meetings:
        blocks.append(f"<tr><td>{_meeting(meeting)}</td></tr>")

    closing = [line for line in brief["closing"] if line.strip()]
    if closing:
        text = "<br>".join(_inline(_BULLET_RE.sub(r"\g<text>", line).strip())
                           for line in closing)
        blocks.append(
            f'<tr><td style="font-family:{FONT};font-size:13px;line-height:1.45;'
            f'color:{PALETTE["muted"]};padding:0 2px">{text}</td></tr>')

    body = gap.join(blocks)
    return (
        f'<div style="background:{PALETTE["bg"]};margin:0;padding:24px 12px">'
        # Fluid up to WIDTH. A fixed width attribute is what a phone clips, so
        # the table is 100% with a cap, and Outlook, which ignores the cap, is
        # handed a fixed-width wrapper it alone can see.
        f'<!--[if mso]><table role="presentation" width="{WIDTH}" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:100%;max-width:{WIDTH}px">'
        f"{body}</table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</div>")
