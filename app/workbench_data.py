#!/usr/bin/env python3
"""Read side of the Workbench: briefings, sidecars, and the CRM roll-up.

Everything here is pure reading and shaping. No network, no API calls, no
writes. `workbench_serve.py` calls into this for the Briefings and Inbox
sections; `workbench_store.py` calls `attention_items()` to propose tickets.

Two things live here:

1. `render_markdown()` turns a rendered briefing (`week.md`, `morning-coffee.md`,
   `afternoon-tea.md`) into semantic HTML. It emits classes only, never inline
   styles, so `workbench_static/style.css` owns every visual decision (DESIGN.md).
2. The sidecar readers normalize `logs/*_latest.json` into one item shape, so
   the UI and the ticket store never have to know which briefing a row came from.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from config_loader import (
    afternoon_tea_md_path,
    deal_critical_domains,
    logs_dir,
    morning_coffee_md_path,
    weekly_dir,
    workspace_week_md_path,
)

# ── Markdown to HTML ─────────────────────────────────────────────────────────

_H_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_CHECK_RE = re.compile(r"^(\s*)[-*]\s+\[([ xX])\]\s+(.+)$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.+)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_RULE_RE = re.compile(r"^\s*([-=*_])\1{2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|?\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")

# Inline runs, applied to already-escaped text so no markup can be injected.
_INLINE = [
    (re.compile(r"`([^`]+)`"), r'<code>\1</code>'),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]"), r'<a class="wikilink" href="#" data-note="\1">\2</a>'),
    (re.compile(r"\[\[([^\]]+)\]\]"), r'<a class="wikilink" href="#" data-note="\1">\1</a>'),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"),
     r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>'),
]


def _inline(text: str) -> str:
    """Escape, then apply inline markdown. Order matters: code first."""
    out = escape(text)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def _strip_frontmatter(lines: list) -> list:
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1:]
    return lines


_CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\]([+-]?)\s*(.*)$")


def render_markdown(text: str) -> str:
    """Render briefing markdown as semantic HTML.

    Supports headings, bullet and ordered lists (nested by indent), task list
    checkboxes, tables with a header row, blockquotes, callout folds,
    horizontal rules, fenced code, and paragraphs. Checkbox inputs carry a
    zero-based `data-check-index` in appearance order so a caller can map a
    click back to the source line.

    An Obsidian callout (`> [!note]- Title`) becomes a real fold: closed with
    the `-` suffix, open with `+`. That is how the folded half of a briefing
    stays on the page without being on the screen. The check index runs
    straight through a fold, because a checkbox inside one is still the Nth
    checkbox of the file and a click has to map back to the right line.
    """
    parts = []
    index = 0
    for chunk in _split_callouts(_strip_frontmatter(text.splitlines())):
        if chunk[0] == "fold":
            _, title, body, is_open = chunk
            inner, index = _render_lines(body, index)
            attr = " open" if is_open else ""
            parts.append(
                # The flex row is an inner span, never the <summary> itself: giving a
                # summary any display but list-item drops its native toggle in
                # WebKit, so the fold stops opening on a click while still
                # taking focus and still opening on Enter.
                f"<details{attr}><summary><span class=\"sumrow\">"
                f"{_inline(title)}</span></summary>"
                f'<div class="foldbody">{inner}</div></details>')
        else:
            html, index = _render_lines(chunk[1], index)
            parts.append(html)
    return "\n".join(p for p in parts if p)


_TASK_TAIL = re.compile(
    r"^(.*?)((?:\s\u00b7\s[^\u00b7]+){2,})$", re.S)


# Every phrase shape the four briefings actually use for lateness or decay.
# The markdown stays plain text (four surfaces read it, only one has colour),
# so the emphasis is applied here.
#
# This began as the two phrases `_why_for` writes, which is the front page's
# vocabulary and nobody else's. The Week writes "2d overdue" and "overdue since
# 9/1"; Week Retro writes "3 days past", "28 days stale" and "81d since
# contact". None of them matched, so the two pages whose whole subject is decay
# rendered every overdue figure in flat ink. The list below was taken from the
# live vault files, not invented.
#
# Two near misses are deliberately excluded, and both are real lines:
#   "Waiting on a confirm reply: 187 (completion 167, overdue 3, met 3)"
#       a queue census, where the number follows the word
#   "No threads over 14 days cold."
#       an empty state saying the good news
# Marking either would paint a reassurance red, so the digits must lead.
_LATE_RE = re.compile(
    r"(?<!over )(?<!under )(?<!within )("
    r"\b\d+\s*(?:d|days?)\s+(?:late|overdue|past|stale|cold)\b"
    r"|\b\d+\s*(?:d|days?)\s+since\s+contact\b"
    r"|\boverdue\s+since\s+\S+"
    r"|\bdue\s+today\b"
    r"|\bpast\s+due\b"
    r")", re.I)


def _mark_late(html: str) -> str:
    """Give lateness its own colour inside the filing metadata.

    Without this the phrase the ranker sorted the page on renders in the same
    11px muted grey as the word "Legal" beside it: the most consequential fact
    on the row, set at the weight of a filing tag. --red is the system's
    colour for late, and nothing else on the page could reach it.
    """
    return _LATE_RE.sub(r'<span class="late">\1</span>', html)


def _task_label(raw: str) -> str:
    """Split an item into what it says and what files it.

    A front-page item is one flat run: subject, person, date, business,
    function, separated by the same middot. Set at one weight the subject
    competes with its own filing metadata, and a 124-character line wraps
    while the eye has nothing to land on. The subject stays at reading
    weight; everything after the second-to-last separator group sets back.

    Nothing is hidden and nothing is reordered: the same characters in the
    same order, in two ranks instead of one.
    """
    m = _TASK_TAIL.match(raw)
    if not m or not m.group(1).strip():
        # No filing tail, so there are no two ranks to split into. The Week
        # writes its items this way, as one sentence with the lateness inside
        # it, and returning bare `_inline` here meant every "3d overdue" on
        # that page skipped `_mark_late` and rendered in ink. A row with no
        # filing half still has a deadline.
        return _mark_late(_inline(raw))
    # Both halves. The front page puts the due date in the filing tail, but an
    # item written as a sentence carries its own deadline in the subject, and
    # marking only the tail left "3 days late" in ink whenever the writer put it
    # on the left of the middot.
    return (f'<span class="what">{_mark_late(_inline(m.group(1).strip()))}</span>'
            f'<span class="filed">{_mark_late(_inline(m.group(2).strip()))}</span>')


def _split_callouts(lines: list) -> list:
    """Split already-frontmatter-stripped lines into text runs and folds."""
    out: list = []
    buf: list = []
    fold = None
    for line in lines:
        m = _CALLOUT_RE.match(line)
        if m:
            if fold:
                out.append(("fold", fold[0], fold[1], fold[2]))
            elif buf:
                out.append(("text", buf))
                buf = []
            fold = (m.group(3).strip(), [], m.group(2) == "+")
            continue
        if fold is not None:
            if line.startswith(">"):
                fold[1].append(re.sub(r"^>\s?", "", line))
                continue
            if not line.strip():
                fold[1].append("")
                continue
            out.append(("fold", fold[0], fold[1], fold[2]))
            fold = None
        buf.append(line)
    if fold:
        out.append(("fold", fold[0], fold[1], fold[2]))
    elif buf:
        out.append(("text", buf))
    return out


def _render_lines(lines: list, check_index: int = 0) -> tuple:
    """Render one run of markdown lines. Returns (html, next check index)."""
    out: list = []
    list_stack: list = []       # open list tags, innermost last
    in_table = False
    table_head_done = False
    in_code = False
    para: list = []

    def close_lists(to_depth: int = 0) -> None:
        while len(list_stack) > to_depth:
            out.append(f"</{list_stack.pop()}>")

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def close_table() -> None:
        nonlocal in_table, table_head_done
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False
            table_head_done = False

    def close_all() -> None:
        flush_para()
        close_lists()
        close_table()

    for raw in lines:
        line = raw.rstrip()

        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                close_all()
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(escape(raw) + "\n")
            continue

        if line.startswith("|"):
            flush_para()
            close_lists()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not in_table:
                out.append('<div class="tablewrap"><table>')
                out.append("<thead><tr>"
                           + "".join(f"<th>{_inline(c)}</th>" for c in cells)
                           + "</tr></thead><tbody>")
                in_table = True
                continue
            if _TABLE_SEP_RE.match(line):
                table_head_done = True
                continue
            out.append("<tr>"
                       + "".join(f"<td>{_inline(c) or '&#183;'}</td>" for c in cells)
                       + "</tr>")
            continue
        close_table()

        if not line.strip():
            flush_para()
            close_lists()
            continue

        if _RULE_RE.match(line):
            close_all()
            out.append("<hr>")
            continue

        m = _H_RE.match(line)
        if m:
            close_all()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        m = _QUOTE_RE.match(line)
        if m:
            close_all()
            out.append(f"<blockquote>{_inline(m.group(1))}</blockquote>")
            continue

        m = _CHECK_RE.match(line)
        if m:
            flush_para()
            depth = len(m.group(1)) // 2 + 1
            while len(list_stack) > depth:
                out.append(f"</{list_stack.pop()}>")
            while len(list_stack) < depth:
                out.append('<ul class="tasks">')
                list_stack.append("ul")
            done = m.group(2).lower() == "x"
            out.append(
                # The text is the checkbox's <label>, not a sibling <span>:
                # that gives the box an accessible name (it announced as a
                # bare "checkbox, unchecked" before) and makes the whole line
                # a hit target, so the 24px minimum is met without drawing a
                # 24px box.
                f'<li class="task{" done" if done else ""}">'
                f'<input type="checkbox" id="ck{check_index}" '
                f'data-check-index="{check_index}"'
                f'{" checked" if done else ""}>'
                f'<label for="ck{check_index}">{_task_label(m.group(3))}</label></li>'
            )
            check_index += 1
            continue

        for pattern, tag, group in ((_BULLET_RE, "ul", 2), (_ORDERED_RE, "ol", 3)):
            m = pattern.match(line)
            if m:
                flush_para()
                depth = len(m.group(1)) // 2 + 1
                while len(list_stack) > depth:
                    out.append(f"</{list_stack.pop()}>")
                while len(list_stack) < depth:
                    out.append(f"<{tag}>")
                    list_stack.append(tag)
                # `_mark_late` ran only inside the filing half of a front-page
                # task, which is a shape only Morning Coffee and Afternoon Tea
                # emit. The Week and Week Retro write plain bullets, so every
                # "2d overdue" and "81d since contact" on the two pages whose
                # entire subject is decay rendered in flat ink, at the weight of
                # the tag beside it.
                out.append(f"<li>{_mark_late(_inline(m.group(group)))}</li>")
                break
        else:
            close_lists()
            para.append(line.strip())
            continue

    if in_code:
        out.append("</code></pre>")
    close_all()
    return "\n".join(out), check_index


# ── Briefings ────────────────────────────────────────────────────────────────

_RETRO_RE = re.compile(r"^retro-(\d{4})-(\d{2})-(\d{2})\.md$")


def week_retro_md_path() -> Path:
    """The newest `retro-YYYY-MM-DD.md` in the weekly folder.

    Week Retro is the one briefing that writes a NEW dated file each run
    rather than rewriting one path, so its location is a lookup, not a
    constant. Dated by filename, not mtime, for the same reason the legacy
    week reader is: an old retro edited today is still an old week. With no
    retro yet this returns the path the next one would take, so the page
    reads as empty rather than raising.
    """
    folder = weekly_dir()
    dated = []
    for path in folder.glob("retro-*.md"):
        m = _RETRO_RE.match(path.name)
        if m:
            dated.append((m.group(1) + m.group(2) + m.group(3), path))
    return max(dated)[1] if dated else folder / "retro-none.md"


# name -> (display title, markdown path fn, sidecar stem, fresh-for hours)
BRIEFINGS = {
    "morning-coffee": ("Morning Coffee", morning_coffee_md_path, "morning_coffee_latest", 20),
    "afternoon-tea":  ("Afternoon Tea",  afternoon_tea_md_path,  "afternoon_tea_latest",  20),
    "week":           ("The Week",       workspace_week_md_path, "week_review_latest",    24 * 8),
    "week-retro":     ("Week Retro",     week_retro_md_path,     "week_retro_latest",     24 * 8),
}


def sidecar_path(stem: str) -> Path:
    return logs_dir() / f"{stem}.json"


def write_sidecar(stem: str, payload: dict) -> Path | None:
    """Drop a script's JSON output beside the briefing it produced.

    Called by the daily scripts so the Workbench has structured data to read
    without re-running anything. Best-effort by design: a briefing that renders
    but fails to leave a sidecar is still a successful run, so this never raises
    into the caller.
    """
    try:
        path = sidecar_path(stem)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return path
    except (OSError, TypeError, ValueError):
        return None


def read_sidecar(name: str) -> dict:
    """Load a briefing's JSON sidecar. Missing or corrupt reads as empty."""
    entry = BRIEFINGS.get(name)
    if not entry:
        return {}
    for stem in (entry[2], f"{entry[2]}.full"):
        path = sidecar_path(stem)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


_LEGACY_WEEK_RE = re.compile(r"^week-(\d{4})-(\d{2})-(\d{2})\.md$")


def legacy_week_path() -> Path | None:
    """The newest `week-YYYY-MM-DD.md` in the configured weekly folder, if any.

    Installs that predate the plugin (the hand-rolled chief-of-staff scripts
    Van Gogh grew out of) write their weekly briefing there instead of into
    `van-gogh/week.md`. Reading it means the Workbench shows real work on those
    machines the moment it opens, rather than an empty page that looks broken.
    Dated by filename, not mtime: an old file edited today is still an old week.
    """
    try:
        folder = weekly_dir()
        dated = []
        for path in folder.glob("week-*.md"):
            m = _LEGACY_WEEK_RE.match(path.name)
            if m:
                dated.append((m.group(1) + m.group(2) + m.group(3), path))
        return max(dated)[1] if dated else None
    except (OSError, KeyError):
        return None


def briefing(name: str) -> dict:
    """One briefing, rendered. Never raises: a missing file is an empty body."""
    title, path_fn, _stem, fresh_hours = BRIEFINGS[name]
    legacy = False
    try:
        path = path_fn()
        if name == "week" and not path.exists():
            fallback = legacy_week_path()
            if fallback is not None:
                path, legacy = fallback, True
    except Exception:
        return {"name": name, "title": title, "html": "", "generated_at": None,
                "stale": True, "exists": False, "path": "", "legacy": False}

    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"name": name, "title": title, "html": "", "generated_at": None,
                "stale": True, "exists": False, "path": str(path), "legacy": False}

    generated = datetime.fromtimestamp(stat.st_mtime)
    return {
        "name": name,
        "title": title,
        "html": render_markdown(text),
        "generated_at": generated.isoformat(timespec="seconds"),
        "stale": datetime.now() - generated > timedelta(hours=fresh_hours),
        "exists": True,
        "path": str(path),
        "legacy": legacy,
    }


def all_briefings() -> list:
    return [briefing(name) for name in BRIEFINGS]


# ── Item normalization ───────────────────────────────────────────────────────

# Sidecar list key -> the source label a ticket and the Inbox both use.
ITEM_SOURCES = ("inbox_pending", "waiting_on_user", "cold_urgent", "cold_monitor")

_URGENCY_ORDER = {"high": 3, "medium": 2, "low": 1}


def normalize_item(raw: dict, source: str) -> dict:
    """One shape for every thread row, whichever list it came from.

    `cold_*` rows measure age from the user's last send (`age_days`); the
    inbound lists measure it from the counterparty's reply (`reply_age_days`).
    Collapsing them to a single `age_days` is what lets rank and the Inbox
    treat all four lists alike.
    """
    age = raw.get("age_days")
    if age is None:
        age = raw.get("reply_age_days")
    email = (raw.get("counterparty_email") or "").strip().lower()
    return {
        "source": source,
        "account": raw.get("source") or "",
        "subject": (raw.get("subject") or "").strip(),
        "counterparty_name": (raw.get("counterparty_name") or "").strip(),
        "counterparty_email": email,
        "domain": email.rpartition("@")[2],
        "age_days": int(age or 0),
        "body_preview": raw.get("body_preview") or "",
        "summary": raw.get("summary") or "",
        "intent": raw.get("intent") or "",
        "urgency": (raw.get("urgency") or "low").lower(),
        "suggested_action": raw.get("suggested_action") or "",
        "is_internal": bool(raw.get("is_internal")),
        "allowlisted": bool(raw.get("allowlisted")),
    }


def attention_items(sidecar: dict | None = None) -> list:
    """Every thread the week sidecar says is open, normalized and deduped.

    Deduped on (counterparty_email, subject): the same thread can legitimately
    appear in two lists across a refresh boundary, and the earlier source in
    ITEM_SOURCES wins because it is the more actionable framing.
    """
    data = read_sidecar("week") if sidecar is None else sidecar
    seen: dict = {}
    for source in ITEM_SOURCES:
        for raw in data.get(source) or []:
            if not isinstance(raw, dict):
                continue
            item = normalize_item(raw, source)
            key = (item["counterparty_email"], item["subject"].lower())
            if key not in seen:
                seen[key] = item
    return list(seen.values())


# ── Inbox / CRM roll-up ──────────────────────────────────────────────────────

_CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "live.com",
    "msn.com", "comcast.net", "verizon.net", "sbcglobal.net",
}


def crm_groups(items: list | None = None) -> list:
    """Group open threads by counterparty organization.

    Consumer mailboxes are their own group keyed by address, since two people
    at gmail.com are not one relationship. Sorted by peak urgency, then by the
    freshest thread, so whoever is waiting longest on something urgent is top.
    """
    rows = attention_items() if items is None else items
    groups: dict = {}
    critical = {d.lower() for d in deal_critical_domains()}

    for item in rows:
        domain = item["domain"]
        if not domain:
            key, label = item["counterparty_email"] or "unknown", item["counterparty_name"] or "Unknown"
        elif domain in _CONSUMER_DOMAINS:
            key, label = item["counterparty_email"], item["counterparty_name"] or item["counterparty_email"]
        else:
            key, label = domain, domain
        group = groups.setdefault(key, {
            "key": key,
            "label": label,
            "domain": domain,
            "logo_domain": "" if (not domain or domain in _CONSUMER_DOMAINS) else domain,
            "deal_critical": domain in critical,
            "people": [],
            "threads": [],
        })
        person = item["counterparty_name"] or item["counterparty_email"]
        if person and person not in group["people"]:
            group["people"].append(person)
        group["threads"].append(item)

    out = []
    for group in groups.values():
        group["threads"].sort(key=lambda t: (-_URGENCY_ORDER.get(t["urgency"], 0), -t["age_days"]))
        group["count"] = len(group["threads"])
        group["max_urgency"] = max(
            (t["urgency"] for t in group["threads"]),
            key=lambda u: _URGENCY_ORDER.get(u, 0),
            default="low",
        )
        group["oldest_days"] = max((t["age_days"] for t in group["threads"]), default=0)
        out.append(group)

    out.sort(key=lambda g: (
        -int(g["deal_critical"]),
        -_URGENCY_ORDER.get(g["max_urgency"], 0),
        -g["oldest_days"],
        g["label"].lower(),
    ))
    return out
