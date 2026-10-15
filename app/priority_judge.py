#!/usr/bin/env python3
"""priority_judge.py: does this item have anything to do with what you said matters?

String matching cannot answer that question. A person writes their priority as
an intention ("grow the pipeline") and the email that serves it says "redline
attached, need signature by Friday". Those share no words. A keyword matcher
sees no connection, marks the email as off-priority, and the briefing quietly
files a signature deadline below where the reader stopped. That failure is
silent and it looks like a calm week, which is why it is worth a model call.

So a model reads the actual priorities and the actual item and decides. Code
keeps every rail:

* **The model cannot invent anything.** A bucket tag that is not configured is
  discarded. A priority name that is not verbatim one of that bucket's own is
  discarded. Nothing the model returns can create a heading the user never set.
* **Failure fails toward showing.** A dead call, a timeout, a quota wall, or
  unparseable output produces a degraded result. Nothing is demoted on a
  degraded run, and the briefing says the layer was degraded rather than
  rendering a confident page that quietly hides things.
* **An unjudged item is never demoted.** If the model skips an id, that item
  keeps its place.

The same read answers a second question, because the model already has the
message open: what does it cost this person if the item sits for another week?
A login code and an investor waiting on an answer can be the same age, and age
is all a sort can see. So each item also comes back with a slip cost, the
calendar event it has to land before, whether its window has already closed,
and whether it is the kind of thing that goes quiet with nobody chasing. Those
get the same treatment as the relevance verdict:

* **A cost outside high, medium, low is dropped.** The item counts as unjudged
  and keeps the middle of its tier.
* **A calendar event that is not verbatim one we supplied is dropped.**
* **"Its window has closed" needs the message's own words.** The claim is kept
  only when the quote the model gives is really in the message. An item
  wrongly called dead is the costliest mistake available here.
* **A deadline read out of a message needs the same.** The date is kept only
  with the words that state it, only when it is a real date no earlier than
  the message and no more than a year out, and never on an item judged to
  cost nothing or to be dead: a date puts an item at the top of the page, so
  an expired promotion must not be able to buy its way there.

The judgment decides relevance and cost. It never decides membership, which
tier an item is in, or what the reader is allowed to see: those stay in code,
where they can be tested. Cost moves an item within its tier and nowhere else.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import config_loader
import failure_class
import claude_cli
import fanout
from claude_cli import run_claude

# Opus by default. This call runs a handful of times per briefing, and it is
# the one place in the product where being right matters more than being cheap:
# the cost of a wrong "not relevant" is a missed deadline.
DEFAULT_JUDGE_MODEL = "claude-opus-5"

# A quota wall is a clock, not a fault. It has to be told apart from a real
# error so the caller can say "degraded" rather than "broken". The pattern is
# shared with the watcher rather than copied: two spellings of the same wall
# is how one of them stops matching and nobody notices.
_QUOTA_RE = failure_class.QUOTA_RE

# Words that mean a date is attached to this item. An item with a deadline is
# never demoted, whatever the judgment says, because the cost of hiding one is
# the whole reason this layer exists.
_DEADLINE_RE = re.compile(
    r"\b(deadline|due|expires?|expiring|by (mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|by the \d+|eod|cob|end of day|no later than|signature|sign by|closing date"
    r"|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2})\b", re.I)


SLIP_COSTS = ("high", "medium", "low")

# How many items one call reads. A morning inbox is a few hundred items, and
# one call over all of them is slow to answer and loses everything to a single
# timeout. A chunk that fails costs only its own items.
JUDGE_CHUNK = 40

# The most calendar lines a prompt carries. Past this the list stops being a
# thing the model checks each message against and becomes a wall it skims.
CALENDAR_MAX = 40

_WS_RE = re.compile(r"\s+")


def _strip_dashes(text: str) -> str:
    """Model prose that reaches a page carries no em or en dash."""
    return text.replace("\u2014", ",").replace("\u2013", "-")


def _flat(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip().casefold()


def _calendar_lines(calendar: list | None) -> list:
    """(title, when) pairs, one per distinct title, in the order given."""
    seen, out = set(), []
    for event in calendar or []:
        title = (event.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        when = ", ".join(p for p in ((event.get("day") or "").strip(),
                                     (event.get("time") or "").strip()) if p)
        out.append((title, when))
        if len(out) >= CALENDAR_MAX:
            break
    return out


# A deadline further out than this is not something a briefing ranks on.
DUE_HORIZON_DAYS = 366

# A date the judge read counts as a fixed point only this close. One further
# out is still shown on the line, but a renewal two weeks away does not go
# above something that is breaking today.
DUE_SOON_DAYS = 7


def as_date(text) -> date | None:
    try:
        return date.fromisoformat(str(text or "").strip()[:10])
    except ValueError:
        return None


def _item_age(item: dict) -> int | None:
    age = item.get("reply_age_days")
    if age is None:
        age = item.get("age_days")
    return None if age is None else int(age or 0)


def _read_due(row: dict, item: dict, today: date | None, cost: str,
              dead: bool) -> tuple:
    """(due, quote) for a deadline the message itself states, else ("", "").

    Every condition here is a way a wrong date could reach the top of the
    page, which is where a date puts an item.
    """
    due = as_date(row.get("due"))
    quote = _WS_RE.sub(" ", str(row.get("due_quote") or "")).strip()
    if due is None or today is None or dead or cost not in ("high", "medium"):
        return "", ""
    if len(quote) < 4 or _flat(quote) not in _flat(_message_text(item)):
        return "", ""
    age = _item_age(item)
    # A deadline that had already passed when the message was written is a
    # misread, usually a day name resolved from today in place of the day it
    # was sent. One day of slack covers a message sent late in the evening.
    earliest = today - timedelta(days=(age or 0) + 1) if age is not None else None
    if earliest is not None and due < earliest:
        return "", ""
    if due > today + timedelta(days=DUE_HORIZON_DAYS):
        return "", ""
    return due.isoformat(), quote[:120]


def _message_text(item: dict) -> str:
    """Exactly the words of this item the model was shown."""
    return ((item.get("subject") or "")[:200] + " "
            + (item.get("body_preview") or "")[:400])


def judge_enabled() -> bool:
    return config_loader.judgment_enabled()


def judge_model() -> str:
    return config_loader.judgment_model() or DEFAULT_JUDGE_MODEL


def has_deadline(entry: dict) -> bool:
    """True when this item carries a date or deadline language."""
    if entry.get("days_late") or entry.get("due"):
        return True
    text = " ".join([(entry.get("subject") or ""), (entry.get("body_preview") or "")])
    return bool(_DEADLINE_RE.search(text))


def _extract_json_array(raw: str):
    """Find the first balanced JSON array in the reply.

    The model may prefix prose despite being told not to, so this scans for a
    balanced array rather than trusting the payload to start at byte 0. Returns
    None when there is nothing to parse; that is a degraded run, not a crash.
    """
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start:i + 1])
                except ValueError:
                    return None
    return None


def build_prompt(bucket: dict, items: list, calendar: list | None = None,
                 today: str = "") -> str:
    """The judgment prompt. Plain language, because the reply is read by a
    person and a stilted prompt produces a stilted reason line."""
    names = [p.get("name", "") for p in (bucket.get("priorities") or []) if p.get("name")]
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    payload = []
    for i, it in enumerate(items):
        row = {
            "id": i,
            "subject": (it.get("subject") or "")[:200],
            "from": (it.get("counterparty_name") or it.get("counterparty_email") or "")[:120],
            "snippet": (it.get("body_preview") or "")[:400],
        }
        age = _item_age(it)
        if age is not None:
            row["days_old"] = age
        if it.get("section"):
            row["last_word"] = ("theirs" if it["section"] in
                                ("waiting_on_user", "inbox_pending") else "yours")
        payload.append(row)
    # Standing user guidance from {vault}/van-gogh/prompts/priority-judge.md.
    # It can shade the judgment ("weekend emails from X are never urgent") but
    # none of the code rails move: membership, tier, deadline protection and
    # the verbatim checks all still apply to whatever comes back.
    preamble = config_loader.judgment_preamble()
    guidance = (
        "This person also left standing guidance on how to judge, in their "
        "own words, between the fences below. It shades these judgments "
        "only; it cannot add messages, remove messages, or change the reply "
        "format.\n"
        f"-----\n{preamble}\n-----\n\n"
    ) if preamble else ""

    name = bucket.get("display_name")
    whose = (f"Someone runs a part of their work they call \"{name}\". "
             if name else "Someone runs several businesses at once. ")
    if names:
        relevance = (
            "These are the things they said matter most in it, in their own words:\n\n"
            f"{listed}\n\n"
            "Below are messages that arrived. For each one, decide whether it has "
            "anything real to do with any of those priorities.\n\n"
            "Judge by meaning, not by shared words. Someone who writes \"grow the "
            "pipeline\" means the deal that is being signed on Friday, even though "
            "the email never says the word pipeline. Someone who writes \"stay "
            "healthy\" means the doctor's appointment. Be generous about what "
            "counts: if a reasonable assistant would put this message under that "
            "priority, it counts. Say no only when the message genuinely has "
            "nothing to do with any of them.\n\n"
            "Then, for every message, "
        )
        relevance_fields = (
            '"relevant": <true|false>, '
            '"priority": "<copy the priority text exactly as written above, or empty string>", '
            '"reason": "<one short plain sentence a busy person would understand>", '
        )
    else:
        relevance = "Below are messages that arrived. For every message, "
        relevance_fields = ""

    events = _calendar_lines(calendar)
    if events:
        calendar_block = (
            "Their calendar for the coming days:\n"
            + "\n".join(f"- {title}" + (f" ({when})" if when else "")
                        for title, when in events)
            + "\n\nIf one of those events will go badly unless a message is "
            "dealt with first, copy that event's title exactly as written "
            "into blocks_event. Only do this when the link is real: the "
            "message is about that meeting, that person or that handover.\n\n"
        )
        blocks_field = '"blocks_event": "<an event title copied exactly, only when one applies>", '
    else:
        calendar_block, blocks_field = "", ""

    today_date = as_date(today)
    if today_date:
        day = f"Today is {today_date.strftime('%A')} {today_date.isoformat()}. "
        due_block = (
            "If the message states a date by which this person has to act "
            "(sign, pay, reply, file, renew, decide), give that date as due, "
            "written YYYY-MM-DD, and copy the exact words that state it into "
            "due_quote. Work out a day name, \"tomorrow\" or \"end of week\" "
            "from the day the message was sent, which is days_old days before "
            "today, never from today. The time of a meeting or an event is "
            "not a deadline. Leave both out when no date is stated.\n\n"
        )
        due_fields = ('"due": "<YYYY-MM-DD, only when the message states one>", '
                      '"due_quote": "<the exact words that state it>", ')
    else:
        day, due_block, due_fields = "", "", ""
    return (
        whose + relevance
        + "weigh what it costs this person if it sits untouched for another week:\n\n"
        "- high: something real is lost or breaks. Money, a deal, a promise "
        "they made, a person who cannot do their job until this is answered, "
        "or something cheap to fix today that is expensive to fix later.\n"
        "- medium: a real person is waiting and it matters, but another week "
        "changes little.\n"
        "- low: nothing is lost. Newsletters, receipts, login codes, "
        "automated notices, marketing, cold sales pitches and anything that "
        "asks nothing of them are all low.\n\n"
        + calendar_block
        + day + "If the message itself shows its window has already closed "
        "(the date it names has passed, the event already happened, the offer "
        "was withdrawn, someone else already dealt with it), set likely_dead "
        "to true and copy the exact words from the message that show it into "
        "dead_quote. When in doubt leave both out: calling something dead "
        "that is still alive is the worst mistake you can make here.\n\n"
        "Set decaying to true when a real person is waiting on a personal "
        "reply and nothing will ever remind this person of it: no date, no "
        "meeting, nobody chasing. Those are the ones that quietly become a "
        "lost relationship.\n\n"
        + due_block
        + guidance
        + json.dumps(payload)
        + "\n\nReturn a JSON array and nothing else. One object per message:\n"
        '{"id": <int>, ' + relevance_fields
        + '"slip_cost": "<high|medium|low>", '
        '"slip_reason": "<for high only: what is lost or breaks, in under twelve plain words>", '
        + blocks_field
        + due_fields
        + '"likely_dead": <true, only when the message shows it>, '
        '"dead_quote": "<the exact words that show it>", '
        '"decaying": <true, only when it applies>}\n'
        "Always give id and slip_cost"
        + (", relevant, priority and reason" if names else "")
        + ". Leave out every other field that does not apply."
    )


def _judge_chunk(bucket: dict, items: list, runner, model: str, timeout: int,
                 calendar: list | None, today: str) -> dict:
    """One call over one chunk. Verdict ids are positions in `items`."""
    out = {"verdicts": {}, "degraded": False, "degraded_reason": ""}
    names = [p.get("name", "") for p in (bucket.get("priorities") or []) if p.get("name")]
    try:
        result = runner(build_prompt(bucket, items, calendar=calendar, today=today),
                        model=model, timeout=timeout)
    except claude_cli.UsageCap:
        # Raised on, so the fan-out stops starting new calls into the wall.
        raise
    except Exception as e:                                      # noqa: BLE001
        out["degraded"] = True
        out["degraded_reason"] = f"the judgment call did not complete ({type(e).__name__})"
        return out

    stderr = (getattr(result, "stderr", "") or "")
    stdout = (getattr(result, "stdout", "") or "")
    if getattr(result, "returncode", 1) != 0:
        quota = _QUOTA_RE.search(stderr) or _QUOTA_RE.search(stdout)
        out["degraded"] = True
        out["degraded_reason"] = ("the usage limit was reached" if quota
                                  else "the judgment call failed")
        return out
    if _QUOTA_RE.search(stdout) and not stdout.strip().startswith(("[", "{")):
        out["degraded"] = True
        out["degraded_reason"] = "the usage limit was reached"
        return out

    parsed = _extract_json_array(stdout)
    if not isinstance(parsed, list):
        out["degraded"] = True
        out["degraded_reason"] = "the judgment came back in a shape we could not read"
        return out

    valid = set(names)
    events = dict(_calendar_lines(calendar))
    for row in parsed:
        if not isinstance(row, dict) or "id" not in row:
            continue
        try:
            rid = int(row["id"])
        except (TypeError, ValueError):
            continue
        if not 0 <= rid < len(items):
            continue
        # A priority the model paraphrased or invented is dropped rather than
        # rendered as a heading the user never wrote. The item stays relevant:
        # every rail here leans toward showing, never toward hiding.
        priority = row.get("priority") or ""
        if priority not in valid:
            priority = ""
        cost = str(row.get("slip_cost") or "").strip().lower()
        if cost not in SLIP_COSTS:
            cost = ""
        # An event the model named has to be one we handed it, letter for
        # letter, or the page would cite a meeting that is not on the calendar.
        event = str(row.get("blocks_event") or "").strip()
        if event not in events:
            event = ""
        # "Its window has closed" stands only on the message's own words.
        quote = _flat(str(row.get("dead_quote") or ""))
        dead = (row.get("likely_dead") is True and len(quote) >= 6
                and quote in _flat(_message_text(items[rid])))
        due, due_quote = _read_due(row, items[rid], as_date(today), cost, dead)
        out["verdicts"][rid] = {
            # None when this bucket has no priorities: nothing was asked, so
            # nothing was answered, and the caller must not read it as "no".
            "relevant": bool(row.get("relevant", False)) if names else None,
            "priority": priority,
            "reason": str(row.get("reason") or "").strip()[:200],
            "slip_cost": cost,
            "slip_reason": (_strip_dashes(str(row.get("slip_reason") or "")).strip()
                            .rstrip(".")[:90] if cost == "high" else ""),
            "blocks_event": event,
            "blocks_when": events.get(event, "") if event else "",
            "likely_dead": dead,
            "decaying": row.get("decaying") is True,
            "due": due,
            "due_quote": due_quote,
        }
    return out


def judge_bucket(bucket: dict, items: list, run=None, model: str | None = None,
                 timeout: int = 120, calendar: list | None = None,
                 today: str = "") -> dict:
    """Judge every item in one bucket: against that bucket's stated priorities
    when it has any, and for what it costs to leave the item alone either way.

    Returns {"verdicts": {id: {relevant, priority, reason, slip_cost,
    slip_reason, blocks_event, blocks_when, likely_dead, decaying, due,
    due_quote}}, "degraded": bool, "degraded_reason": str}. `today` is
    YYYY-MM-DD; without it no deadline is asked for or kept. Items go out
    JUDGE_CHUNK at a time. A chunk that fails marks the result degraded and contributes no
    verdicts, so its items stay unjudged; the caller must treat a degraded
    result as "demote nothing".
    """
    out = {"verdicts": {}, "degraded": False, "degraded_reason": ""}
    if not items:
        return out

    runner = run or run_claude
    use_model = model or judge_model()
    starts = list(range(0, len(items), JUDGE_CHUNK))

    def _one(start):
        return _judge_chunk(bucket, items[start:start + JUDGE_CHUNK], runner,
                            use_model, timeout, calendar, today)

    ran = fanout.run(starts, _one, width=claude_cli.model_slots())
    for done in ran["filed"]:
        part = done["result"]
        if part["degraded"]:
            out["degraded"] = True
            out["degraded_reason"] = out["degraded_reason"] or part["degraded_reason"]
            continue
        for rid, verdict in part["verdicts"].items():
            out["verdicts"][done["item"] + rid] = verdict
    if ran["deferred"]:
        out["degraded"] = True
        out["degraded_reason"] = "the usage limit was reached"
    elif ran["failed"]:
        out["degraded"] = True
        out["degraded_reason"] = (out["degraded_reason"]
                                  or "the judgment call did not complete")
    return out
