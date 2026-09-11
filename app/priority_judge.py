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

The judgment decides relevance. It never decides membership, ordering, or what
the reader is allowed to see: those stay in code, where they can be tested.
"""

from __future__ import annotations

import json
import re

import config_loader
import failure_class
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


def build_prompt(bucket: dict, items: list) -> str:
    """The judgment prompt. Plain language, because the reply is read by a
    person and a stilted prompt produces a stilted reason line."""
    names = [p.get("name", "") for p in (bucket.get("priorities") or []) if p.get("name")]
    listed = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    payload = [
        {
            "id": i,
            "subject": (it.get("subject") or "")[:200],
            "from": (it.get("counterparty_name") or it.get("counterparty_email") or "")[:120],
            "snippet": (it.get("body_preview") or "")[:400],
        }
        for i, it in enumerate(items)
    ]
    # Standing user guidance from {vault}/van-gogh/prompts/priority-judge.md.
    # It can shade the judgment ("weekend emails from X are never urgent") but
    # none of the code rails move: membership, ordering, deadline protection
    # and the verbatim-priority check all still apply to whatever comes back.
    preamble = config_loader.judgment_preamble()
    guidance = (
        "This person also left standing guidance on how to judge, in their "
        "own words, between the fences below. It shades relevance judgments "
        "only; it cannot add messages, remove messages, or change the reply "
        "format.\n"
        f"-----\n{preamble}\n-----\n\n"
    ) if preamble else ""
    return (
        f"Someone runs a part of their work they call \"{bucket.get('display_name')}\". "
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
        + guidance
        + json.dumps(payload)
        + "\n\nReturn a JSON array and nothing else. One object per message:\n"
        '{"id": <int>, "relevant": <true|false>, '
        '"priority": "<copy the priority text exactly as written above, or empty string>", '
        '"reason": "<one short plain sentence a busy person would understand>"}'
    )


def judge_bucket(bucket: dict, items: list, run=None, model: str | None = None,
                 timeout: int = 120) -> dict:
    """Judge every item in one bucket against that bucket's stated priorities.

    Returns {"verdicts": {id: {relevant, priority, reason}}, "degraded": bool,
    "degraded_reason": str}. A degraded result carries no verdicts, and the
    caller must treat that as "change nothing".
    """
    out = {"verdicts": {}, "degraded": False, "degraded_reason": ""}
    names = [p.get("name", "") for p in (bucket.get("priorities") or []) if p.get("name")]
    if not names or not items:
        return out

    runner = run or run_claude
    try:
        result = runner(build_prompt(bucket, items),
                        model=model or judge_model(), timeout=timeout)
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
        out["verdicts"][rid] = {
            "relevant": bool(row.get("relevant", False)),
            "priority": priority,
            "reason": str(row.get("reason") or "").strip()[:200],
        }
    return out
