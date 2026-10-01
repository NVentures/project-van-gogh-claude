#!/usr/bin/env python3
"""
Follow-up Radar — turn the "Pending follow-ups" ledger into a date-triggered
work list.

The user's open relationship threads live as freeform bullets in the vault
workspace memory ({vault}/van-gogh/projects/Project Van Gogh/memory.md,
"## Pending follow-ups" section — override via follow_ups.memory_path). Each
carries a trigger date ("if unheard by Thu 2026-07-23", "due EOD Fri 7/17"), a
mailbox to watch ("Work Outlook"), and cues like HARD TRIGGER / value-add /
DEFERRED.

This script does the deterministic part: locate the section, split it into
items, extract every date and classify each as a *trigger* (an action the user
owes) or *context* (something already done) by the nearest cue word, then
bucket each item by its earliest trigger against today. It emits JSON; the
/van-gogh:follow-up-radar skill reads that, checks the named inbox for a reply,
and drafts a value-add nudge in the user's voice. Nothing is auto-sent.

Pure parsing (extract_section / parse_item / parse_follow_ups) takes an explicit
`today` and horizon so it is fully unit-testable with no config or clock.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config_loader
from config_loader import (
    force_utf8_io,
    follow_ups_memory_path,
    follow_ups_horizon_days,
    resolved_meta,
    user_tz,
)

# Status buckets, most-actionable first (drives sort + summary order).
STATUS_ORDER = ["overdue", "due", "due_soon", "watch", "scheduled", "dormant"]

_SECTION_RE = re.compile(
    r"(?im)^##\s+Pending follow-ups.*?$(?P<body>.*?)(?=^##\s|\Z)", re.DOTALL
)

# One item per top-level "- " bullet (mid-line "- " is never at line start).
_ITEM_SPLIT_RE = re.compile(r"(?m)^-\s+")

_TITLE_RE = re.compile(r"^\*\*(?P<title>.+?)\*\*")
# The annotated ledger format a deliverable gets filed as:
#   - [ ] (tag) Title | due: 2026-09-12 | format: email
# Legacy prose bullets keep parsing byte-identically; the two shapes coexist in
# one section, because a ledger nobody can hand-edit is a ledger nobody keeps.
_CHECKBOX_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s*")
_TAG_RE = re.compile(r"^\((?P<tag>[^)]{1,40})\)\s*")
_FIELD_RE = re.compile(r"\|\s*(?P<key>due|format)\s*:\s*(?P<val>[^|]+?)\s*(?=\||$)", re.I)
_TOPIC_MD_RE = re.compile(r"\[[^\]]+\]\((?P<file>[^)]+\.md)\)")
_TOPIC_WIKI_RE = re.compile(r"\[\[(?P<name>[^\]|#]+)")
# "Work Outlook", "Acme inbox", "Work Outlook/Gmail" -> hint "Work Outlook".
_INBOX_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+(Outlook|Gmail|inbox|acct)\b")

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MD_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
# The (?!\d) keeps a bare month-YEAR ("November 2026") from parsing as a day.
_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?!\d)\b",
    re.IGNORECASE,
)
_TODAY_RE = re.compile(r"\btoday\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)

# Cue words in the ~40 chars before a date. The nearest cue decides trigger vs
# context; when none is near, default to context (never invent an obligation).
_CUE_RE = re.compile(
    r"(?i)(?P<t>≤|<=|\b(?:by|due|before|until|eod|nudge|prompt|unheard|silent|if|remove after|follow[- ]?up|check[- ]?in)\b|\bnot back\b|\bnot heard\b)"
    r"|(?P<p>\b(?:sent|done|replied|call|kickoff|shipped|emailed|booked|approved|cancelled|canceled|agreed|asking|asked|expected|recap|drafted|delivered|targeting|signed)\b)"
)

# Whole-item cues.
_SUPPRESS_RE = re.compile(
    r"(?i)decided not to send|deferred to|\bdeferred\b|\bdormant\b|do not surface|do not chase|on hold|until then"
)
_HARD_RE = re.compile(r"(?i)hard trigger|hard deadline|ensure completion|do not let it slip|\bcritical\b")
_VALUE_ADD_RE = re.compile(r"(?i)value[- ]add")


def _infer_year(month: int, day: int, today: date) -> date | None:
    """Attach a year to a bare M/D or month-name date.

    Uses the current year, rolling forward when that date is already well in the
    past (>45 days) — bare dates in a follow-up ledger are near-future triggers,
    not last spring. Returns None for an impossible date (e.g. 13/40).
    """
    result = None
    for yr in (today.year, today.year + 1):
        try:
            d = date(yr, month, day)
        except ValueError:
            # e.g. Feb 29 in a non-leap `today.year` — try the next year, which
            # may be a leap year, rather than dropping the date entirely.
            continue
        if d >= today - timedelta(days=45):
            return d
        result = d
    return result


def _date_spans(raw: str, today: date) -> list[tuple[int, int, str]]:
    """Every date token in `raw` as (start, end, iso), skipping the impossible."""
    spans = []
    for m in _ISO_RE.finditer(raw):
        try:
            iso = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue
        spans.append((m.start(), m.end(), iso))
    for m in _MD_RE.finditer(raw):
        mo, d, yr_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        if yr_raw:
            yr = int(yr_raw) + (2000 if int(yr_raw) < 100 else 0)
            try:
                dt = date(yr, mo, d)
            except ValueError:
                continue
        else:
            dt = _infer_year(mo, d, today)
        if dt:
            spans.append((m.start(), m.end(), dt.isoformat()))
    for m in _MONTH_DAY_RE.finditer(raw):
        dt = _infer_year(_MONTHS[m.group(1).lower()], int(m.group(2)), today)
        if dt:
            spans.append((m.start(), m.end(), dt.isoformat()))
    return sorted(spans)


def _extract_dates(raw: str, today: date) -> tuple[list[str], list[str]]:
    """Return (trigger_dates, context_dates) as sorted, de-duped ISO strings.

    Each date is classified by the nearest preceding cue word (trigger cue ->
    trigger; past cue or none -> context). The look-back window is capped at 40
    chars *and* stops at the previous date token, so a cue binds only to the
    date it introduces — "≤ Mon 2026-07-20 ... their 7/7 ask" makes 07-20 a
    trigger without leaking onto 7/7. Literal today/tomorrow are always triggers.
    """
    trigger: set[str] = set()
    context: set[str] = set()

    spans = _date_spans(raw, today)
    prev_end = 0
    for start, end, iso in spans:
        window = raw[max(prev_end, start - 40):start]
        best = None
        for m in _CUE_RE.finditer(window):
            best = m
        (trigger if (best and best.group("t")) else context).add(iso)
        prev_end = end

    if _TODAY_RE.search(raw):
        trigger.add(today.isoformat())
    if _TOMORROW_RE.search(raw):
        trigger.add((today + timedelta(days=1)).isoformat())

    # A date is a trigger if it's ever cued as one, even if also seen as context.
    context -= trigger
    return sorted(trigger), sorted(context)


# Leading verbs the two-word inbox capture can accidentally swallow
# ("Watch Acme inbox" -> "Acme inbox"; "Client One Gmail" is left intact).
_INBOX_STOPWORDS = {"watch", "for", "the", "and", "on", "in", "from", "see", "via"}


def _extract_inbox_hints(raw: str) -> list[str]:
    hints = []
    for m in _INBOX_RE.finditer(raw):
        qualifier = m.group(1).split()
        if qualifier and qualifier[0].lower() in _INBOX_STOPWORDS:
            qualifier = qualifier[1:]
        if not qualifier:
            continue
        phrase = f"{' '.join(qualifier)} {m.group(2)}"
        if phrase not in hints:
            hints.append(phrase)
    return hints


def _extract_topic_files(raw: str) -> list[str]:
    files = [m.group("file") for m in _TOPIC_MD_RE.finditer(raw)]
    files += [f"{m.group('name').strip()}.md" for m in _TOPIC_WIKI_RE.finditer(raw)]
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _status(earliest_trigger: str | None, suppressed: bool, today: date, horizon: int) -> str:
    """Bucket an item. A suppressed item with a future wake date stays dormant
    until that date arrives (e.g. a November check-in), then triggers normally."""
    if earliest_trigger is None:
        return "dormant" if suppressed else "watch"
    trig = date.fromisoformat(earliest_trigger)
    if suppressed and trig > today:
        return "dormant"
    if trig < today:
        return "overdue"
    if trig == today:
        return "due"
    if trig <= today + timedelta(days=horizon):
        return "due_soon"
    return "scheduled"


def parse_item(raw: str, today: date, horizon: int) -> dict:
    raw = raw.strip()

    # Order matters and the naive version is not back-compatible. Strip the
    # checkbox, then the tag, then cut the trailing annotated fields out of
    # the text, and only THEN run the existing title and date extraction on
    # what is left. Run them in any other order and a legacy prose bullet
    # loses its title to the field regex.
    rest = raw
    done = False
    m = _CHECKBOX_RE.match(rest)
    if m:
        done = m.group("mark").lower() == "x"
        rest = rest[m.end():]

    tag = None
    m = _TAG_RE.match(rest)
    if m:
        candidate = m.group("tag").strip()
        # Only a tag the user actually configured is a tag. Anything else is
        # part of the sentence they wrote and stays in the title.
        if candidate in config_loader.business_tags():
            tag = candidate
            rest = rest[m.end():]

    due = fmt = None
    spans = []
    for fm in _FIELD_RE.finditer(rest):
        key = fm.group("key").lower()
        val = fm.group("val").strip()
        if key == "due" and due is None:
            due = val
        elif key == "format" and fmt is None:
            fmt = val
        spans.append(fm.span())
    for s, e in reversed(spans):
        rest = rest[:s] + rest[e:]
    rest = rest.strip()

    # An unparseable due: is offered to the prose date extraction rather than
    # erroring on a typo in a hand-edited line, but it never reaches the title:
    # a user should not see their typo become the name of the item.
    date_text = rest
    if due:
        try:
            due = date.fromisoformat(due).isoformat()
        except ValueError:
            date_text = f"{rest} {due}".strip()
            due = None

    title_m = _TITLE_RE.match(rest)
    title = title_m.group("title").strip() if title_m else rest.split(" \u2014 ", 1)[0][:80].strip()

    trigger_dates, context_dates = _extract_dates(date_text if date_text else raw, today)
    earliest = trigger_dates[0] if trigger_dates else None
    suppressed = bool(_SUPPRESS_RE.search(raw))


    if due and due not in trigger_dates:
        trigger_dates = sorted([due] + trigger_dates)
        earliest = trigger_dates[0]

    return {
        "title": title,
        "done": done,
        "tag": tag,
        "due": due,
        "format": fmt,
        "status": _status(earliest, suppressed, today, horizon),
        "earliest_trigger": earliest,
        "trigger_dates": trigger_dates,
        "context_dates": context_dates,
        "hard_trigger": bool(_HARD_RE.search(raw)),
        "value_add": bool(_VALUE_ADD_RE.search(raw)),
        "suppressed": suppressed,
        "watch_inbox_hints": _extract_inbox_hints(raw),
        "topic_files": _extract_topic_files(raw),
        "raw": raw,
    }


def extract_section(text: str) -> str:
    """Return the body of the "## Pending follow-ups" section, or "" if absent."""
    m = _SECTION_RE.search(text)
    return m.group("body") if m else ""


def parse_follow_ups(text: str, today: date, horizon: int) -> list[dict]:
    section = extract_section(text)
    if not section:
        return []
    # First chunk before the first "- " bullet is the intro note; drop it.
    chunks = _ITEM_SPLIT_RE.split(section)[1:]
    items = []
    for chunk in chunks:
        body = chunk.strip()
        if body:
            items.append(parse_item(body, today, horizon))
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    items.sort(key=lambda it: (order.get(it["status"], 99), it["earliest_trigger"] or "9999"))
    return items


def overdue(items: list, today: date) -> list:
    """The items a nudge is owed on: filed, dated, past due, not done.

    A filter over already-parsed items, deliberately reusing the `overdue`
    status the parser already computed rather than comparing dates a second
    time. Two definitions of overdue that can disagree is how a system starts
    nudging about work that is finished.
    """
    out = []
    for it in items:
        if it.get("done") or it.get("suppressed"):
            continue
        if it.get("status") != "overdue":
            continue
        out.append(it)
    return out


def days_late(item: dict, today: date) -> int:
    """How many days past its trigger an item is. 0 when it has no date."""
    trig = item.get("due") or item.get("earliest_trigger")
    if not trig:
        return 0
    try:
        return max(0, (today - date.fromisoformat(trig)).days)
    except ValueError:
        return 0


def collect_nudges(today_date) -> tuple:
    """Overdue deliverables from the ledger, ready for a briefing to offer.

    Lives here, in the module that owns the ledger, because three briefings
    consume it and two of them used to reference a key nothing emitted: the
    overdue section simply never appeared and nobody could tell, since a
    missing section looks like a quiet day.

    Calls the pure parse functions rather than main(), which exits 1 on a
    missing ledger. A user who has not started one gets an empty list and one
    error line, never a dead briefing.
    """
    try:
        path = follow_ups_memory_path()
        if not path.exists():
            return [], f"Follow-ups ledger not found at {path}"
        items = parse_follow_ups(path.read_text(encoding="utf-8"), today_date,
                                 follow_ups_horizon_days())
        return [
            {
                "title": it["title"],
                "tag": it.get("tag"),
                "due": it.get("due") or it.get("earliest_trigger"),
                "format": it.get("format"),
                "days_late": days_late(it, today_date),
            }
            for it in overdue(items, today_date)
        ], None
    except Exception as e:                                      # noqa: BLE001
        return [], f"Nudges: {e}"


def main() -> None:
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Date-triggered follow-up radar.")
    parser.add_argument("--memory", metavar="PATH", help="MEMORY.md to parse (default: auto-resolve).")
    parser.add_argument("--horizon", type=int, help="Days ahead still counted as due_soon.")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="Override today (testing/backdated runs).")
    args = parser.parse_args()

    # Config is read inside the guard so a machine with no config yet gets one
    # actionable line instead of a stack trace. Reading the file the user
    # explicitly passed does not need config at all, so --memory keeps working
    # on a machine that has not been set up.
    try:
        today = (date.fromisoformat(args.today) if args.today
                 else datetime.now(user_tz()).date())
        horizon = args.horizon if args.horizon is not None else follow_ups_horizon_days()
        mem_path = Path(args.memory) if args.memory else follow_ups_memory_path()
    except Exception as e:                                      # noqa: BLE001
        sys.stderr.write("ERROR: " + config_loader.setup_hint(e) + "\n")
        sys.exit(1)

    if not mem_path.exists():
        sys.stderr.write(
            f"MEMORY_NOT_FOUND: no follow-up ledger at {mem_path}\n"
            "Set follow_ups.memory_path in config.json or pass --memory PATH.\n"
        )
        sys.exit(1)

    text = mem_path.read_text(encoding="utf-8")
    items = parse_follow_ups(text, today, horizon)

    counts = {s: 0 for s in STATUS_ORDER}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1

    out = {
        "meta": resolved_meta(),
        "today": today.isoformat(),
        "horizon_days": horizon,
        "source": str(mem_path),
        "counts": counts,
        "actionable": [it for it in items if it["status"] in ("overdue", "due", "due_soon")],
        "items": items,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write(
        "SUMMARY "
        + " ".join(f"{s}={counts[s]}" for s in STATUS_ORDER)
        + f" (horizon={horizon}d, today={today.isoformat()})\n"
    )


if __name__ == "__main__":
    main()
