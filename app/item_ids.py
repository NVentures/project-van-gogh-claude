#!/usr/bin/env python3
"""item_ids.py: one stable id per to-do line, carried on the line itself.

A to-do lives on up to three lines at once: the hotcache action items section
(the item of record), the meeting page it came from, and the week file that
mirrors both. Closing it used to mean finding "the same sentence" in each
file, and a sentence is a poor key: two meetings produce the same words, a
person rewords a line, a briefing shortens it. So every checkbox line carries

    <!-- cb=cb-XXXXXXXX opened=YYYY-MM-DD -->

and a close, an undo and a mirror all address the line by that id. `opened`
is the day the item came into existence (the meeting date), which is what
lets the judge refuse evidence older than the promise it is said to prove.

A leaf module on purpose: no config, no vault paths, no other app import, so
`meeting_ingest`, `completion_scan` and `done_judge` can all depend on it
without a cycle.

Three rules hold it together.

* The id is derived (`new_id`), never minted, so filing the same meeting
  twice yields the same id and the hotcache line and its page line agree
  without either having to look the other up.
* Stamping is idempotent and never rewords: a line that already carries an
  id keeps it whatever a person has since done to the sentence, and a second
  pass over a stamped file changes zero bytes.
* Line endings are the file's own. Files are read and written with
  `newline=""`, because a vault synced from Windows is CRLF and a stamp that
  silently rewrote every line ending would be a thousand-line diff.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

# The full marker, and the bare one the older ledger wrote (id, no date).
CB_RE = re.compile(
    r"<!--\s*cb=(cb-[0-9a-f]{8})\s+opened=(\d{4}-\d{2}-\d{2})\s*-->")
BARE_CB_RE = re.compile(r"<!--\s*cb=(cb-[0-9a-f]{8})\s*-->")
# Either shape, with the whitespace in front of it, for removal.
_ANY_CB_RE = re.compile(
    r"[ \t]*<!--\s*cb=cb-[0-9a-f]{8}(?:\s+opened=\d{4}-\d{2}-\d{2})?\s*-->")
_COMMENT_RE = re.compile(r"<!--.*?-->")

# `(from [[Meeting title - 2026-09-23]])`: where a hotcache line came from,
# and, when the title ends in a date, the day it was opened.
FROM_RE = re.compile(r"\(from \[\[([^\]|]+?)(?:\|[^\]]*)?\]\]\)")
FROM_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\s*\([^)]*\))?\s*$")
CHECKBOX_RE = re.compile(r"^(\s*[-*]\s*\[)([ xX])(\]\s+)(\S.*?)\s*$")
_DUE_RE = re.compile(r"\(due (\d{4}-\d{2}-\d{2})\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# "**Dana Reyes** (dash) task" and "**Dana Reyes:** task": the owner prefix a
# notetaker summary puts on a line and the hotcache copy drops.
_OWNER_RE = re.compile(
    r"^\*\*[^*]{1,60}\*\*\s*[" + chr(0x2014) + chr(0x2013) + r":-]\s*"
    r"|^\*\*[^*]{1,60}:\*\*\s*")


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def new_id(source_title: str, text: str) -> str:
    """The id for one item of one source. Deterministic on purpose."""
    blob = f"{_norm(source_title)}|{_norm(text)}".encode("utf-8")
    return "cb-" + hashlib.sha1(blob).hexdigest()[:8]


def match_key(text: str) -> str:
    """The sentence as two files would both spell it: no owner prefix, no
    markers, no `(from ...)`, no due date. For pairing a page line with its
    hotcache line, never for the id itself."""
    text = _COMMENT_RE.sub(" ", str(text or ""))
    text = FROM_RE.sub(" ", text)
    text = _DUE_RE.sub(" ", text)
    return _norm(_OWNER_RE.sub("", text.strip()))


def _split_eol(line: str) -> tuple:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def parse(line: str):
    """One checkbox line, or None when the line is not one.

    `text` is the sentence a person reads: every HTML comment removed. `core`
    is that sentence without its `(from [[...]])` tail, which is what the id
    is derived from.
    """
    body, _eol = _split_eol(line or "")
    m = CHECKBOX_RE.match(body)
    if not m:
        return None
    rest = m.group(4)
    cb, opened = "", ""
    full = CB_RE.search(rest)
    if full:
        cb, opened = full.group(1), full.group(2)
    else:
        bare = BARE_CB_RE.search(rest)
        if bare:
            cb = bare.group(1)
    text = " ".join(_COMMENT_RE.sub(" ", rest).split())
    source = ""
    src = FROM_RE.search(text)
    if src:
        source = src.group(1).strip()
    core = " ".join(FROM_RE.sub(" ", text).split())
    due = _DUE_RE.search(text)
    return {"checked": m.group(2) != " ", "text": text, "core": core,
            "cb": cb, "opened": opened, "source": source,
            "due": due.group(1) if due else ""}


def stamp_line(line: str, cb: str, opened: str) -> str:
    """The line carrying its id. Idempotent, and it never rewords.

    A line that already has the full marker comes back untouched. A bare
    `cb=` keeps ITS id (the one passed in is ignored) and gains the date.
    Anything that is not a checkbox with words on it comes back untouched.
    """
    body, eol = _split_eol(line)
    if not CHECKBOX_RE.match(body) or CB_RE.search(body):
        return line
    bare = BARE_CB_RE.search(body)
    if bare:
        marker = f"<!-- cb={bare.group(1)} opened={opened} -->"
        return body[:bare.start()] + marker + body[bare.end():] + eol
    return f"{body.rstrip()} <!-- cb={cb} opened={opened} -->{eol}"


def section_lines(lines: list, headings) -> list:
    """Indexes of the lines inside any section whose heading contains one of
    `headings`. A section runs to the next heading of the same or a higher
    level, so its subsections belong to it. Heading depth is not assumed: a
    page may write its headings at one `#` or at three.
    """
    wanted = [str(h).lower() for h in (headings or ()) if str(h).strip()]
    out, level = [], 0
    for i, line in enumerate(lines):
        body, _ = _split_eol(line)
        m = _HEADING_RE.match(body)
        if m:
            depth = len(m.group(1))
            if level and depth <= level:
                level = 0
            if not level and any(w in m.group(2).lower() for w in wanted):
                level = depth
            continue
        if level:
            out.append(i)
    return out


def _stamp(text: str, headings, opened: str, source_title: str = "",
           unique: bool = False, known=None) -> tuple:
    lines = text.splitlines(keepends=True)
    seen = set(re.findall(r"cb=(cb-[0-9a-f]{8})", text)) if unique else set()
    stamped = adopted = 0
    for i in section_lines(lines, headings):
        item = parse(lines[i])
        if not item or item["checked"] or (item["cb"] and item["opened"]):
            continue
        source = item["source"] or source_title
        when = opened
        dated = FROM_DATE_RE.search(item["source"]) if item["source"] else None
        if dated:
            when = dated.group(1)
        if not when:
            continue
        if item["cb"]:
            lines[i] = stamp_line(lines[i], item["cb"], when)
            adopted += 1
            continue
        cb = (known or {}).get(match_key(item["text"])) or new_id(
            source, item["core"])
        if unique:
            n = 2
            while cb in seen:
                cb = new_id(source, f"{item['core']}#{n}")
                n += 1
            seen.add(cb)
        lines[i] = stamp_line(lines[i], cb, when)
        stamped += 1
    return "".join(lines), stamped, adopted


def stamp_block(text: str, headings, opened: str, source_title: str = "",
                unique: bool = False, known=None) -> str:
    """`text` with every open checkbox under `headings` carrying an id.

    `opened` is the date for a line that names none; a line ending in
    `(from [[Title - 2026-09-23]])` takes its own. `unique` salts a second
    line that would otherwise repeat an id already in the text, which is for
    the hotcache, where two lines are two items even when the words match.
    `known` maps `match_key(text)` to an id another file already gave the
    item, so a page adopts its hotcache line's id.
    """
    return _stamp(text, headings, opened, source_title, unique, known)[0]


def read_file(path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_file(path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def backfill(path, headings, default_opened: str, source_title: str = "",
             unique: bool = False, known=None, live: bool = True) -> dict:
    """Stamp one file in place. `{stamped, adopted, changed}`.

    Writes only when a byte changed, so a second pass over a stamped file
    leaves its modified time alone. `live=False` counts and writes nothing.
    A missing or unreadable file is zero of everything.
    """
    try:
        before = read_file(path)
    except (OSError, UnicodeDecodeError):
        return {"stamped": 0, "adopted": 0, "changed": False}
    after, stamped, adopted = _stamp(before, headings, default_opened,
                                     source_title, unique, known)
    changed = after != before
    if changed and live:
        write_file(Path(path), after)
    return {"stamped": stamped, "adopted": adopted, "changed": changed}


def strip(text: str) -> str:
    """`text` without any id marker, for anything shown to a person."""
    return _ANY_CB_RE.sub("", str(text or ""))


def find_lines(lines: list, cb: str) -> list:
    """Indexes of the checkbox lines carrying this id."""
    if not cb:
        return []
    needle = re.compile(r"cb=" + re.escape(cb) + r"(?![0-9a-f])")
    return [i for i, line in enumerate(lines)
            if needle.search(line) and CHECKBOX_RE.match(_split_eol(line)[0])]
