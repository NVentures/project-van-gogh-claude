#!/usr/bin/env python3
"""done_judge.py: closes the to-dos the evidence shows are finished.

A to-do used to stay open until a person ticked it, and a list nobody ticks
grows until nobody reads it. Once a day this takes every open line in the
hotcache action items section, gathers the cheapest real evidence for that
ONE item, asks a model for a verdict with a verbatim quotation, and then
disbelieves the answer until code has checked the quotation itself.

The costly error is a FALSE DONE: an item marked finished leaves every
briefing and nobody looks at it again. So the model only ever proposes. A
close happens when ALL of these hold, and every one is a line of code in
`validate`, never a sentence in the prompt:

    the quotation is a verbatim part of ONE dated evidence section,
    that section is dated on or after the day the item was opened,
    the kind of evidence is read off the section, not off the model,
    a transcript line counts as the user's only when it is the user's line,
    a quotation from a mail is from its body, not the header this module wrote,
    the mail went to a real recipient and is not the user writing to themself,
    the quotation is not a promise ("will send it Friday"),
    the quotation does not itself say the work is not done,
    the quotation is not the to-do's own words,
    the kind of evidence is strong enough for who owes the item,
    the confidence clears the floor in `auto_close.confidence`,
    a "no longer needed" verdict rests on a logged decision,
    the item was not reopened by hand (an undo pins it open).

Anything else is recorded and left open. There is no confirm step: the human
gate is the undo, `done_judge.py --undo cb-xxxxxxxx`, which puts the lines
back exactly as they were and pins the item so it is never closed again.

Evidence, cheapest first: the "Decisions Made" sections of later meeting
pages, the transcripts of later meetings (fetched live from the notetaker by
meeting id), and mail with its full text (`mail_bodies.py`). Three things are
never evidence: the to-do's own line, the meeting the promise was made in,
and anything dated before the item existed.

Expiry is separate and says so: an item with no due date and no deal, older
than `auto_close.expire_minor_days`, about which a complete search found
nothing at all, is closed as expired. A run whose evidence was incomplete
expires nothing, because absence only means something over a full search.

`completion_scan.mark_done` is the ONE writer of a close, so the scorecard's
promise (only an explicit close counts) stays true without this module
recording anything itself.

CLI:
  --dry-run (default)  judge and print, write nothing in the vault
  --live               stamp ids, close, expire, write the day's record
  --only cb-xxxxxxxx   judge one item now
  --undo cb-xxxxxxxx   reopen one and pin it open
  --why cb-xxxxxxxx    the recorded verdict for one item
  --closed-today       what was closed today
  --backfill-only      stamp ids and stop
  --selftest           the planted controls through the real model
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_cli  # noqa: E402
import claude_update  # noqa: E402
import config_loader  # noqa: E402
import context_pack  # noqa: E402
import failure_class  # noqa: E402
import hotcache_sync  # noqa: E402
import item_ids  # noqa: E402
import mail_bodies  # noqa: E402
import notetaker  # noqa: E402
import run_ledger  # noqa: E402
import user_state  # noqa: E402
from meeting_synthesis import _extract_json_object  # noqa: E402

JUDGE_MODEL = "claude-sonnet-5-5"
JOB_NAME = "judge.done"

VERDICTS = ("done", "moot", "open", "unknowable")
# Evidence that can close something the user owed: their own sent mail, their
# own words on a call, a decision written down after a meeting.
STRONG_CLASSES = frozenset({"sent_mail", "transcript_user", "decision"})
# `draft` is never produced by the pack builder (an unsent draft is not
# admitted at all). It exists so a planted draft can prove it closes nothing.
EVIDENCE_CLASSES = frozenset(STRONG_CLASSES | {
    "inbound_mail", "transcript_other", "draft", "none"})
MAIL_CLASSES = frozenset({"sent_mail", "inbound_mail", "draft"})

ACK_CONFIDENCE = 0.90      # someone else's words need a higher bar
PACK_CAP = 8000
SECTION_CAP = 2000
BATCH_SIZE = 8
MAIL_MAX = 3
MAIL_WINDOW = 1500
MAIL_GRACE_DAYS = 2        # mail from just before the item opened still counts
TRANSCRIPT_MAX = 3
TRANSCRIPT_WINDOW = 1200
TRANSCRIPT_FETCH_CAP = 20  # transcripts fetched from the notetaker per run
REJUDGE_DAYS = 7
MAX_CALLS = 0              # no ceiling on calls: 25 left the rest unread every day
MAX_MINUTES = 60           # the one ceiling a run has; what it leaves goes first next time
QUOTED_WINDOW = 900        # of the older message a reply sits on top of
LOOKBACK_DAYS = 60
MIN_SHARED_TOKENS = 2
COUNTERPARTY_CAP = 6
INBOUND_SENDER_CAP = 60
UNDO_BATCHES = 20
RENDER_CAP = 20
TIMEOUT_S = 300
RC_UNAVAILABLE = 75        # the model could not be reached; nothing written

STAMP_HEADINGS = ("What You Owe", "What Others Owe You", "Action Items")
OWED_TO_ME_HEADINGS = ("owed to", "others owe", "waiting on")

# A quotation that is itself a promise is the commonest way a "done" verdict
# is wrong: "I will send it Friday" is in the sent mail, it is about the
# task, and it proves the opposite of completion.
PROMISE_RE = re.compile(
    r"\b(?:will|going to|plan to|planning to|intend to|aim to)\b"
    r"|\bby (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"eod|eow|end of (?:day|week)|next week|tomorrow)\b", re.I)
# The contraction says the same thing and the word-boundary form above cannot
# see it ("I'll get the survey to you next week").
PROMISE_CONTRACTION_RE = re.compile(r"\b(?:i|we|he|she|they|you)['’]ll\b", re.I)
# A quotation that says the work is NOT finished. A model asked "is it done"
# will sometimes cite the sentence that answers no.
NOT_DONE_RE = re.compile(
    r"\b(?:not yet|have not|haven['’]t|has not|hasn['’]t|did not|"
    r"didn['’]t|not (?:done|signed|sent|finished|decided|agreed|"
    r"submitted|started)|still (?:need|owe|waiting|deciding|open|"
    r"outstanding|pending)|no decision|nothing is agreed|yet to)\b", re.I)
HEADER_RE = re.compile(r"^(?:From|To|Cc|Subject|Sent|Attachments): ", re.M)
DUE_RE = re.compile(r"\(due \d{4}-\d{2}-\d{2}\)|\bdue=\d{4}-\d{2}-\d{2}")

_SPEAKER_RE = re.compile(
    r"^(?:\[(?:\d{1,3}:\d{2}(?::\d{2})?|--:--)\]\s*)?\[([^\]]{1,40})\]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MEETING_ID_RE = re.compile(r"^(?:meeting_id|granola_id):\s*(\S+)", re.M)
_ATTENDEES_RE = re.compile(r"^attendees:\s*\[(.*)\]\s*$", re.M)
_CREATED_RE = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})", re.M)
_DECISIONS_RE = re.compile(
    r"(?:\n|\A)#{1,6}\s*Decisions Made\s*\n(.*?)(?=\n#{1,6}\s|\Z)", re.S)
_CB_ARG_RE = re.compile(r"^cb-[0-9a-f]{8}$")

# Every check in `validate` that can stop a close, by name. The control suite
# disables each one in turn and requires a planted item to close as a result:
# a guard nothing depends on is a guard nobody can tell is working.
GUARDS = (
    "one_section", "not_before_opened", "class_from_section", "user_line",
    "mail_body_not_header", "own_words_not_quote", "real_recipient",
    "not_a_loop", "not_a_promise",
    "not_a_denial", "not_the_task", "class_allowed", "confidence_floor",
    "moot_needs_decision", "pinned_open",
)


# Where a reply stops and the message it answers begins: the mail client's
# own header block ("From: ...", then "Sent:" or "Date:" on the next line),
# or one of the plainer marks. Everything under it was written by someone
# else, however it travels inside a message the user sent.
QUOTE_HEAD_RE = re.compile(
    r"(?:^|\n)[ \t>]*(?:_{5,}|-{5,}|On [^\n]{0,120}wrote:|Sent from my |"
    r"From:[^\n]{1,200}\n[ \t>]*(?:Sent|Date):)", re.IGNORECASE)
QUOTED_NOTE = ("EARLIER IN THIS THREAD (the older message this one was "
               "written on top of; context only, never cite it):")


def split_quoted(body) -> tuple:
    """(what the sender wrote, the older messages quoted beneath it).

    A message with nothing above the quote (a bare forward) is all the
    sender's: forwarding it was the act, and there is no reply to tell apart.
    """
    body = body or ""
    m = QUOTE_HEAD_RE.search(body)
    if not m or not body[:m.start()].strip():
        return body.strip(), ""
    return body[:m.start()].strip(), body[m.start():].strip()


class JudgeUnavailable(RuntimeError):
    """The model could not answer (a cap, a stale CLI, a failed call).

    The run stops and writes nothing: half a run applied is worse than none,
    and the watcher holds the job by the class named in the ledger row.
    """


# ── small helpers ─────────────────────────────────────────────────────────────

def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _day(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    m = _FILE_DATE_RE.search(str(value or ""))
    return m.group(1) if m else ""


def _today() -> str:
    return datetime.now(config_loader.user_tz()).date().isoformat()


def _plus_days(day_str: str, n: int) -> str:
    try:
        return (date.fromisoformat(str(day_str)[:10])
                + timedelta(days=n)).isoformat()
    except ValueError:
        return str(day_str)[:10]


def _age_days(opened: str, today: str) -> int:
    try:
        return (date.fromisoformat(today) - date.fromisoformat(opened)).days
    except ValueError:
        return 0


def _window(text: str, pos: int, width: int) -> str:
    """A slice around `pos`, grown to whole lines so a speaker label is never
    cut off the line it belongs to."""
    start = max(0, pos - width // 2)
    start = text.rfind("\n", 0, start) + 1
    end = text.find("\n", min(len(text), start + width))
    return text[start:end if end >= 0 else len(text)]


def distinctive(text) -> set:
    """Tokens that can identify a task. The generic set is the shared one,
    never a copy: a lone common word is not a match."""
    return hotcache_sync._tokens(str(text or "")) - hotcache_sync._generic()


def shares(tokens: set, text, n: int = MIN_SHARED_TOKENS) -> bool:
    return len(tokens & distinctive(text)) >= n


def _read(path) -> str:
    try:
        return item_ids.read_file(path)
    except (OSError, UnicodeDecodeError):
        return ""


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, default=str)
    os.replace(tmp, path)


# ── paths ─────────────────────────────────────────────────────────────────────

def state_path() -> Path:
    """What was judged and what was pinned. Machine state, so it lives in the
    state directory: a pin written into the vault would sync to a machine
    that never made the decision it records."""
    return user_state.state_dir() / "done_judge_state.json"


def dry_path() -> Path:
    """Where a dry run leaves its record: beside the state, never in the
    vault, because a dry run promises to change nothing there."""
    return user_state.state_dir() / "done_judge_dry.json"


def undo_path() -> Path:
    return config_loader.logs_dir() / "done_judge_undo.json"


def latest_path() -> Path:
    """The day's record, and the file the watcher grades this job by."""
    return config_loader.logs_dir() / "done_judge_latest.json"


# ── state ─────────────────────────────────────────────────────────────────────

def read_state() -> dict:
    state = _read_json(state_path(), {})
    state.setdefault("items", {})
    return state


def write_state(state: dict) -> None:
    _write_json(state_path(), state)


def needs_judging(item, ehash, record, today, force=False) -> bool:
    """Only items whose evidence moved, or that have waited a week. This is
    the whole cost model: everything on the first day, a handful after."""
    if force or not record:
        return True
    if record.get("evidence_hash") != ehash:
        return True
    judged = _day(record.get("judged_at") or "")
    if not judged:
        return True
    return _age_days(judged, today) >= REJUDGE_DAYS


# ── the evidence context ──────────────────────────────────────────────────────

class Pack:
    """The evidence for one item: labelled sections and their joined text."""

    def __init__(self):
        self.parts: list = []
        self.sources: list = []

    def add(self, label: str, text: str, source: str) -> None:
        self.parts.append(f"[{label}]\n{text}")
        self.sources.append(source)

    @property
    def text(self) -> str:
        return "\n\n".join(self.parts)

    def is_empty(self) -> bool:
        return not self.parts


class Ctx:
    """Everything one run reads, gathered once. Tests build one by hand."""

    def __init__(self, today=None, user_name=None, user_variants=None,
                 self_emails=None, mail=None, pages=None, fetch_note=None):
        self.today = _day(today) or _today()
        self.user_name = (user_name if user_name is not None
                          else config_loader.cfg()["user"]["full_name"])
        self.user_variants = {str(v).lower() for v in (
            user_variants if user_variants is not None
            else config_loader.user_name_variants())}
        self.self_emails = {str(e).lower() for e in (
            self_emails if self_emails is not None
            else config_loader.account_emails_lower())}
        self.mail = list(mail or [])
        # Mail handed in is the whole corpus; nothing more is fetched.
        self.mail_loaded = mail is not None
        self.pages = pages          # None means "not indexed yet"
        self.fetch_note = fetch_note or notetaker.fetch_note
        self.degraded: list = []
        self.transcripts: dict = {}
        self._tokens: dict = {}
        self.fetches = 0
        self.fetch_capped = False

    def tokens_of(self, text: str) -> set:
        """`distinctive(text)`, worked out once per text per run. Every item
        is compared against every mail and every decision line, so without
        this the same message is tokenised once per open item."""
        got = self._tokens.get(text)
        if got is None:
            got = self._tokens[text] = distinctive(text)
        return got

    # ── meeting pages ────────────────────────────────────────────────────
    def page_index(self) -> list:
        """Every dated meeting page, newest first."""
        if self.pages is not None:
            return self.pages
        rows = []
        try:
            paths = sorted(config_loader.sources_dir().glob("*.md"))
        except OSError:
            paths = []
        for path in paths:
            dated = _FILE_DATE_RE.search(path.stem)
            if not dated:
                continue
            text = _read(path)
            mid = _MEETING_ID_RE.search(text)
            decisions = []
            block = _DECISIONS_RE.search(text)
            if block:
                for line in block.group(1).splitlines():
                    line = line.strip()
                    if line.startswith(("- ", "* ")) and "%%" not in line:
                        decisions.append(line[2:].strip())
            rows.append({"path": str(path), "title": path.stem,
                         "date": dated.group(1),
                         "meeting_id": mid.group(1) if mid else "",
                         "tokens": distinctive(path.stem + " " + text),
                         "decisions": decisions})
        rows.sort(key=lambda r: r["date"], reverse=True)
        self.pages = rows
        return rows

    def transcript_text(self, page: dict) -> str:
        """One meeting's transcript as speaker lines, fetched at most once.

        A fetch that fails marks the run degraded and reads as no transcript.
        Past the per-run cap nothing more is fetched, and that is recorded
        too, because "we did not look" must never read as "nothing there".
        """
        mid = page.get("meeting_id") or ""
        if not mid:
            return ""
        if mid in self.transcripts:
            return self.transcripts[mid]
        if self.fetches >= TRANSCRIPT_FETCH_CAP:
            self.fetch_capped = True
            return None
        self.fetches += 1
        text = ""
        try:
            note = self.fetch_note(mid, include_transcript=True) or {}
            text = notetaker.strip_pii(
                notetaker.flatten_transcript(note.get("transcript"))) or ""
        except Exception as exc:                                # noqa: BLE001
            # The type only: an exception's text quotes what it failed on.
            self.degraded.append(f"transcript: {type(exc).__name__}")
        self.transcripts[mid] = text
        return text

    # ── mail ─────────────────────────────────────────────────────────────
    def load_mail(self, since: str, senders) -> None:
        """Sent mail since `since`, and what `senders` wrote back.

        A mailbox that cannot be read is a degraded run, by name. A machine
        with no signed-in mailbox at all is degraded too: its packs are
        empty because nothing was searched, not because nothing was found.
        """
        import data_sources
        self.mail_loaded = True
        accounts = config_loader.accounts()
        if not accounts or not data_sources.is_tier1():
            self.degraded.append("mail: no signed-in mailbox")
            return
        senders = sorted(set(senders) - self.self_emails)[:INBOUND_SENDER_CAP]
        for account in accounts:
            label = account.get("label", "?")
            # Each read fails on its own: losing what people wrote back must
            # not also lose what the user sent.
            for what, fetch in (
                    ("sent", lambda: mail_bodies.fetch_sent(
                        account, since, self.today)),
                    ("received", lambda: mail_bodies.fetch_inbound_from(
                        account, senders, since, self.today))):
                try:
                    self.mail += fetch()
                except Exception as exc:                        # noqa: BLE001
                    self.degraded.append(
                        f"mail {what} ({label}): {type(exc).__name__}")


# ── the open set ──────────────────────────────────────────────────────────────

def _action_heading() -> str:
    return config_loader.hotcache_action_items_heading()


def _deal_headings(lines) -> list:
    try:
        deals = hotcache_sync.parse_deals(
            lines, config_loader.hotcache_active_threads_heading())
        return [d["heading"] for d in deals]
    except Exception:                                           # noqa: BLE001
        return []


def _page_facts(path: str) -> dict:
    text = _read(path)
    mid = _MEETING_ID_RE.search(text)
    names = []
    block = _ATTENDEES_RE.search(text)
    if block:
        names = [n.strip().strip('"').strip("'").strip("[]")
                 for n in block.group(1).split(",") if n.strip()]
    owed_to_me = set()
    lines = text.splitlines()
    for i in item_ids.section_lines(lines, ("What Others Owe You",)):
        item = item_ids.parse(lines[i])
        if item and item["cb"]:
            owed_to_me.add(item["cb"])
    return {"meeting_id": mid.group(1) if mid else "",
            "attendees": [n.split("|")[0] for n in names if n],
            "owed_to_me": owed_to_me}


def _entity_emails(name: str, self_emails: set) -> list:
    try:
        page = context_pack.find_entity_page(name)
    except Exception:                                           # noqa: BLE001
        page = None
    if not page:
        return []
    return [e.lower() for e in _EMAIL_RE.findall(_read(page))
            if e.lower() not in self_emails]


def stamp_missing(today, live: bool) -> dict:
    """Give every open to-do its id, in the hotcache and on the meeting
    pages its lines came from.

    Returns `{hotcache, pages, text}` where `text` is the hotcache as it
    reads once stamped. On a dry run that text exists only in memory, so the
    open set can still be built from ids nothing has written yet.
    """
    out = {"hotcache": {"stamped": 0, "adopted": 0},
           "pages": {"stamped": 0, "adopted": 0, "files": 0}, "text": ""}
    path = config_loader.hotcache_path()
    before = _read(path)
    if not before:
        return out
    try:
        default = date.fromtimestamp(os.path.getmtime(path)).isoformat()
    except OSError:
        default = _day(today) or _today()
    heading = (_action_heading(),)
    text, stamped, adopted = item_ids._stamp(before, heading, default, "",
                                             unique=True)
    out["hotcache"] = {"stamped": stamped, "adopted": adopted}
    out["text"] = text
    if live and text != before:
        item_ids.write_file(path, text)

    # Each page adopts the id its hotcache line already has, so the two
    # agree even where the page spells the item with its owner in bold.
    by_title: dict = {}
    lines = text.splitlines()
    for i in item_ids.section_lines(lines, heading):
        item = item_ids.parse(lines[i])
        if item and item["cb"] and item["source"] and not item["checked"]:
            by_title.setdefault(item["source"], {})[
                item_ids.match_key(item["text"])] = item["cb"]
    for title, known in by_title.items():
        page = config_loader.sources_dir() / f"{title}.md"
        if not page.exists():
            continue
        page_text = _read(page)
        dated = _FILE_DATE_RE.search(title)
        created = _CREATED_RE.search(page_text)
        opened = (dated.group(1) if dated
                  else created.group(1) if created else "")
        got = item_ids.backfill(page, STAMP_HEADINGS, opened,
                                source_title=title, known=known, live=live)
        out["pages"]["stamped"] += got["stamped"]
        out["pages"]["adopted"] += got["adopted"]
        out["pages"]["files"] += 1 if got["changed"] else 0
    return out


def build_open_set(ctx: Ctx, hotcache_text=None, only=None) -> list:
    """Every open, id-carrying line of the hotcache action items section."""
    text = (hotcache_text if hotcache_text is not None
            else _read(config_loader.hotcache_path()))
    if not text:
        return []
    lines = text.splitlines()
    deals = _deal_headings(lines)
    sources_dir = config_loader.sources_dir()
    facts_memo: dict = {}
    emails_memo: dict = {}
    items, seen = [], set()
    owed_to_me = False
    wanted = set(item_ids.section_lines(lines, (_action_heading(),)))
    for i, line in enumerate(lines):
        heading = item_ids._HEADING_RE.match(line)
        if heading:
            owed_to_me = any(h in heading.group(2).lower()
                             for h in OWED_TO_ME_HEADINGS)
            continue
        if i not in wanted:
            continue
        parsed = item_ids.parse(line)
        if not parsed or parsed["checked"] or not parsed["cb"]:
            continue
        cb = parsed["cb"]
        if cb in seen or (only and cb != only):
            continue
        seen.add(cb)
        source_path, facts = "", {"meeting_id": "", "attendees": [],
                                  "owed_to_me": set()}
        if parsed["source"]:
            candidate = sources_dir / f"{parsed['source']}.md"
            if candidate.exists():
                source_path = str(candidate)
                if source_path not in facts_memo:
                    facts_memo[source_path] = _page_facts(source_path)
                facts = facts_memo[source_path]
        direction = context_pack.action_item_owner(line)
        if direction not in ("by_me", "to_me", "third_party"):
            direction = ("to_me" if owed_to_me or cb in facts["owed_to_me"]
                         else "by_me")
        owner = item_ids._OWNER_RE.match(parsed["core"])
        who = (re.sub(r"[*:" + chr(0x2014) + chr(0x2013) + r"\-]", " ",
                      owner.group(0)).strip() if owner else "")
        counterparties = [e.lower() for e in _EMAIL_RE.findall(parsed["text"])
                          if e.lower() not in ctx.self_emails]
        for name in facts["attendees"]:
            if name.lower() in ctx.user_variants:
                continue
            if name not in emails_memo:
                emails_memo[name] = _entity_emails(name, ctx.self_emails)
            counterparties += emails_memo[name]
        counterparties = list(dict.fromkeys(counterparties))[:COUNTERPARTY_CAP]
        core = item_ids._OWNER_RE.sub("", parsed["core"]).strip()
        items.append({
            "id": cb, "text": parsed["text"], "task": core,
            "dir": direction, "who": who,
            "opened": parsed["opened"], "due": parsed["due"],
            "has_due": bool(DUE_RE.search(line)),
            "deal": hotcache_sync.match_deal(core, deals) or "",
            "source_title": parsed["source"], "source_path": source_path,
            "meeting_id": facts["meeting_id"],
            "counterparties": counterparties,
            "tokens": distinctive(core),
        })
    return items


# ── the evidence pack ─────────────────────────────────────────────────────────

def _section(cls, source, day, text, **extra) -> dict:
    sec = {"cls": cls, "source": source, "date": _day(day),
           "text": (text or "").strip()[:SECTION_CAP]}
    sec.update(extra)
    return sec


def _e_decisions(item, ctx: Ctx) -> list:
    """Decisions written down at later meetings that share the item's words.

    The item's own meeting is left out: a decision made in the same hour as
    the promise is the promise, not evidence that it was kept.
    """
    out = []
    for page in ctx.page_index():
        if page["date"] < (item.get("opened") or ""):
            continue
        if page["path"] == item.get("source_path"):
            continue
        for line in page["decisions"]:
            if len(item["tokens"] & ctx.tokens_of(line)) >= MIN_SHARED_TOKENS:
                out.append(_section("decision", f"decisions {page['title']}",
                                    page["date"], line))
    return out[:3]


def _is_user_label(label: str, ctx: Ctx) -> bool:
    label = str(label or "").strip().lower()
    return label == "microphone" or label in ctx.user_variants


def _transcript_cls(text: str, ctx: Ctx) -> str:
    """`transcript_user` when the window holds a line of the user's at all.
    Which line a quotation sits on is settled later, in `validate`."""
    for line in (text or "").splitlines():
        m = _SPEAKER_RE.match(line.strip())
        if m and _is_user_label(m.group(1), ctx):
            return "transcript_user"
    return "transcript_other"


def _e_transcripts(item, ctx: Ctx) -> list:
    """Windows around the item's words in meetings held after it opened."""
    if not item["tokens"]:
        return []
    own = _norm(item.get("task", ""))
    out = []
    for page in ctx.page_index():
        if len(out) >= TRANSCRIPT_MAX:
            break
        if not page["meeting_id"] or page["date"] < (item.get("opened") or ""):
            continue
        if (page["path"] == item.get("source_path")
                or page["meeting_id"] == item.get("meeting_id")):
            continue
        if len(item["tokens"] & page["tokens"]) < MIN_SHARED_TOKENS:
            continue
        text = ctx.transcript_text(page)
        if text is None:
            # A meeting that might hold the answer was not read this run, so
            # "nothing found" is not yet a finding for this item.
            item["incomplete"] = True
            continue
        if not text or not shares(item["tokens"], text[:200000]):
            continue
        hits = []
        for tok in sorted(item["tokens"], key=len, reverse=True):
            if len(tok) < 4:
                continue
            for m in re.finditer(re.escape(tok), text, re.I):
                hits.append(m.start())
                if len(hits) > 40:
                    break
            if len(hits) > 40:
                break
        used = []
        for pos in sorted(hits):
            if any(abs(pos - p) < TRANSCRIPT_WINDOW for p in used):
                continue
            win = _window(text, pos, TRANSCRIPT_WINDOW)
            if own and len(own) >= 20 and own in _norm(win):
                continue              # this window IS the promise
            if not shares(item["tokens"], win):
                continue
            out.append(_section(_transcript_cls(win, ctx),
                                f"transcript {page['title']}", page["date"],
                                win))
            used.append(pos)
            if len(out) >= TRANSCRIPT_MAX or len(used) >= 2:
                break
    return out[:TRANSCRIPT_MAX]


def mail_header(row: dict) -> str:
    """The From/To/Subject block. Text THIS module writes, not the message,
    which is why a quotation found only here closes nothing."""
    head = (f"From: {row.get('from', '')}\n"
            f"To: {', '.join(row.get('to') or [])}\n"
            f"Cc: {', '.join(row.get('cc') or [])}\n"
            f"Subject: {row.get('subject', '')}\n"
            f"Sent: {row.get('date', '')}  ({row.get('direction', '')})\n")
    if row.get("attachment_names"):
        head += f"Attachments: {', '.join(row['attachment_names'])}\n"
    elif row.get("has_attachments"):
        head += "Attachments: yes\n"
    return head


def _e_mail(item, ctx: Ctx) -> list:
    """Mail with this item's counterparties since it opened, plus any mail
    that shares the item's distinctive words.

    Relevance first, then recency: sorting one counterparty's mail by date
    alone hands every item about that person the same three newest messages,
    and all but one of them lose their own proof.
    """
    floor = _plus_days(item.get("opened") or ctx.today, -MAIL_GRACE_DAYS)
    cps = set(item["counterparties"])
    by_cp, by_tok = [], []
    for row in ctx.mail:
        direction = row.get("direction")
        if direction not in ("sent", "inbound"):
            continue                  # a draft is never admitted
        when = _day(row.get("date"))
        if not when or when < floor:
            continue
        parties = ({row.get("from", "")} | set(row.get("to") or [])
                   | set(row.get("cc") or []))
        overlap = len(item["tokens"] & ctx.tokens_of(
            f"{row.get('subject', '')} {row.get('body', '')}"))
        if direction == "inbound" and row.get("from", "") not in cps:
            continue
        if cps & parties:
            by_cp.append((overlap, when, row))
        elif overlap >= MIN_SHARED_TOKENS:
            by_tok.append((overlap, when, row))
    key = lambda r: (r[0], r[1])                                # noqa: E731
    cp = sorted(by_cp, key=key, reverse=True)[:MAIL_MAX]
    tok = sorted(by_tok, key=key, reverse=True)[:MAIL_MAX]
    # One seat is held for the best match by words: a busy counterparty's
    # three messages otherwise fill the pack, and the mail that proves the
    # task but went to someone else is never read.
    picked = cp[:MAIL_MAX - 1] + tok[:1] + cp[MAIL_MAX - 1:] + tok[1:]
    out = []
    for _overlap, when, row in picked[:MAIL_MAX]:
        # What the sender wrote is the evidence. The older messages quoted
        # under it are shown, apart, so the judge can see what was being
        # answered; a quotation lifted from them closes nothing.
        own, quoted = split_quoted(row.get("body") or "")
        pos = 0
        for word in sorted(item["tokens"], key=len, reverse=True):
            hit = own.lower().find(word)
            if hit >= 0:
                pos = hit
                break
        shown = _window(own, pos, MAIL_WINDOW)
        text = shown
        if quoted:
            text += f"\n\n{QUOTED_NOTE}\n{quoted[:QUOTED_WINDOW]}"
        cls = "sent_mail" if row.get("direction") == "sent" else "inbound_mail"
        out.append(_section(
            cls, f"{row.get('direction')} mail {row.get('account')}:"
                 f"{str(row.get('id', ''))[:12]}",
            when, text, mail=row, own=shown if quoted else None))
    return out


GATHERERS = (_e_decisions, _e_transcripts, _e_mail)


def build_pack(item, ctx: Ctx) -> tuple:
    """(Pack, [section]) for one item, cheapest source first.

    An EMPTY pack is the answer for most items and is never sent to a model:
    a verdict on no evidence is a guess, and a guess that says done is the
    error this module exists to avoid.

    Every section is stripped of the vault's own markers on the way in, so an
    id comment can never be quoted back as evidence, and a mail's header is
    added here, after the strip, so its recorded length is the true one.
    """
    if item.get("control_sections") is not None:
        sections = [dict(s) for s in item["control_sections"]]
    else:
        sections = []
        for gather in GATHERERS:
            try:
                sections.extend(gather(item, ctx) or [])
            except Exception as exc:                            # noqa: BLE001
                ctx.degraded.append(
                    f"{gather.__name__}: {type(exc).__name__}")
    pack = Pack()
    kept, used = [], 0
    for sec in sections:
        body = context_pack.strip_markers(sec.get("text") or "")
        if not body or not sec.get("date"):
            continue                  # an undated source is not evidence
        if sec.get("mail") is not None and "header_len" not in sec:
            head = mail_header(sec["mail"])
            sec["header_len"] = len(head) + 1
            body = head + "\n" + body
        sec["text"] = body
        if used + len(body) > PACK_CAP:
            continue
        sec["label"] = f"{sec['cls']}: {sec['source']} ({sec['date']})"
        pack.add(sec["label"], body, sec["source"])
        kept.append(sec)
        used += len(body)
    return pack, kept


def evidence_hash(item, pack: Pack) -> str:
    blob = (item.get("text", "") + "|" + pack.text).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ── the model ─────────────────────────────────────────────────────────────────

JUDGE_RULES = """RULES. Every one of these is binding.
1. Contact is not completion. An email about the task, a call about the task, or a meeting invite about the task does not finish the task.
2. A meeting happening is not completion. "We discussed it" is not "it is done".
3. A promise to do it later is not completion. "Will send by Friday", "going to draft it", "I plan to review it" are OPEN, never done.
4. moot needs a logged decision that cancels the work. A quiet item is not moot.
5. For an item owed TO {user_name}, only the other party's delivery counts. {user_name} chasing them is not delivery.
6. A draft is never a reply. Unsent text proves nothing.
7. The citation must be copied VERBATIM out of the evidence, character for character, and it must be the sentence that proves the work is finished. Never paraphrase, never invent, never cite the task text itself.
8. In a transcript, lines marked [microphone] are {user_name} speaking. Lines marked [speaker] are someone else.
9. Text under "EARLIER IN THIS THREAD" is the older message a reply was written on top of. Read it to learn what was being answered. Never cite it: it is not the reply.
10. When in doubt, say open."""

JUDGE_SCHEMA = ('{"items": [{"id": "<item id>", "verdict": '
                '"done|moot|open|unknowable", "confidence": 0.0, '
                '"evidence_class": "sent_mail|inbound_mail|transcript_user|'
                'transcript_other|decision|none", "citation": "<verbatim '
                'sentence from the evidence, or empty>", "reasoning": "<one '
                'sentence>", "done_date": "YYYY-MM-DD or empty"}]}')


def build_prompt(items, packs, user_name: str) -> str:
    blocks = []
    for item in items:
        if item.get("dir") == "by_me":
            owed = f"{user_name} owes this"
        else:
            owed = f"{item.get('who') or 'someone else'} owes this to {user_name}"
        blocks.append(
            f"--- ITEM {item['id']}\n"
            f"Task: {item.get('task') or item.get('text', '')}\n"
            f"Direction: {owed}\n"
            f"Opened: {item.get('opened', '')}  Due: {item.get('due') or 'none'}"
            f"  Deal: {item.get('deal') or 'none'}\n"
            f"EVIDENCE (the only text you may cite):\n"
            f"{packs[item['id']].text}\n")
    return (
        "You are auditing a to-do list. For each item below, decide whether "
        "the work is finished, using ONLY the evidence given for that item.\n\n"
        f"{JUDGE_RULES.format(user_name=user_name)}\n\n"
        "A false 'done' is far more costly than a missed one: an item marked "
        "done disappears from every briefing and nobody looks at it again. "
        "Prefer 'open'.\n\n"
        f"{chr(10).join(blocks)}\n"
        "Reply with STRICT JSON ONLY, no prose before or after, one object "
        f"per item, using exactly this shape:\n{JUDGE_SCHEMA}\n"
        "Use an empty string for a citation you cannot copy verbatim from "
        "the evidence. Use plain hyphens and commas, no long dashes.")


def _ask(prompt, runner, model, timeout) -> dict:
    try:
        result = runner(prompt, model=model, timeout=timeout)
    except claude_cli.UsageCap as exc:
        raise JudgeUnavailable(str(exc)) from exc
    except Exception as exc:                                    # noqa: BLE001
        raise JudgeUnavailable(
            f"{type(exc).__name__}: {str(exc)[:300]}") from exc
    out = getattr(result, "stdout", "") or ""
    err = getattr(result, "stderr", "") or ""
    if getattr(result, "returncode", 1) != 0:
        raise JudgeUnavailable((err or out).strip()[:500]
                               or "the model call failed")
    if failure_class.QUOTA_RE.search(out[:600]) and "{" not in out:
        raise JudgeUnavailable(out.strip()[:500])
    data = _extract_json_object(out)
    return data if isinstance(data, dict) else {}


def judge_batch(items, packs, runner=None, model=None, timeout=TIMEOUT_S,
                user_name="") -> dict:
    """Raw verdicts by item id for ONE batch.

    An item the model left out is asked about once more, through this same
    function, because a batched call does drop items and an omission must
    not read as an answer. A failed call raises `JudgeUnavailable`: the
    caller's policy on a cap or an outage is to stop, never to guess.
    """
    if not items:
        return {}
    runner = runner or claude_cli.run_claude
    model = model or JUDGE_MODEL

    def once(batch):
        data = _ask(build_prompt(batch, packs, user_name), runner, model,
                    timeout)
        rows = {}
        for row in (data.get("items") or []):
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row
        return rows

    out = once(items)
    missing = [i for i in items if i["id"] not in out]
    if missing:
        for cid, row in once(missing).items():
            row["recovered"] = True
            out.setdefault(cid, row)
    return out


# ── post-validation ───────────────────────────────────────────────────────────

def _locate(sections, citation):
    """The ONE section whose own text contains the citation, or None.

    Checking the whole pack is not enough: a citation that straddles two
    sections is a splice of two sources, and a splice is a fabrication.
    """
    q = _norm(citation)
    if len(q) < 8:
        return None
    for sec in sections:
        if q in _norm(sec["text"]):
            return sec
    return None


def _citation_speaker(sec, citation) -> str:
    """The transcript speaker whose line the citation sits on."""
    q = _norm(citation)
    speaker = ""
    for line in (sec.get("text") or "").splitlines():
        m = _SPEAKER_RE.match(line.strip())
        rest = line.strip()[m.end():] if m else line.strip()
        if m:
            speaker = m.group(1)
        r = _norm(rest)
        if r and (r in q or q[:60] in r):
            return speaker
    return ""


def _speaker_matches_who(speaker: str, who: str) -> bool:
    first = (str(speaker or "").strip().lower().split() or [""])[0]
    return bool(first) and first in str(who or "").lower()


def class_policy(item, evidence_class, sec, speaker, floor) -> tuple:
    """(allowed, reason, confidence needed) for this kind of evidence about
    an item owed in this direction.

    The user's own artifact closes what the user owed. Someone else's words
    close it only as an acknowledgement from a person the item names, at a
    higher bar. What someone else owed is closed only by that person
    delivering: the user's own mail about it is a chase, never a delivery.
    """
    direction = item.get("dir")
    row = (sec or {}).get("mail") or {}
    from_cp = (row.get("from") or "").lower() in set(
        item.get("counterparties") or [])
    if direction == "by_me":
        if evidence_class in STRONG_CLASSES:
            return True, "strong_cited_evidence", floor
        if evidence_class == "inbound_mail" and from_cp:
            return True, "counterparty_ack", max(floor, ACK_CONFIDENCE)
    elif direction == "to_me":
        if evidence_class == "inbound_mail" and from_cp:
            return True, "counterparty_delivered_mail", max(
                floor, ACK_CONFIDENCE)
        if (evidence_class == "transcript_other"
                and _speaker_matches_who(speaker, item.get("who"))):
            return True, "counterparty_delivered_transcript", max(
                floor, ACK_CONFIDENCE)
    return False, f"weak_class:{evidence_class}", floor


def validate(item, pack, sections, raw, ctx: Ctx, record=None, floor=None,
             disabled=frozenset()) -> dict:
    """The model's answer, believed only as far as code can check it.

    Every check that can stop a close is named in `GUARDS` and runs behind
    `on(name)`. `disabled` exists for one caller, the control suite, which
    switches each check off in turn to prove a planted item then closes.
    """
    def on(name: str) -> bool:
        return name not in disabled

    if floor is None:
        floor = config_loader.auto_close_confidence()
    record = record or {}
    today = ctx.today
    downgrades = []
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        verdict = "open"
        downgrades.append("bad_verdict")
    try:
        confidence = round(float(raw.get("confidence") or 0.0), 3)
    except (TypeError, ValueError):
        confidence = 0.0
        downgrades.append("bad_confidence")
    confidence = min(max(confidence, 0.0), 1.0)
    model_class = str(raw.get("evidence_class") or "none").strip().lower()
    if model_class not in EVIDENCE_CLASSES:
        model_class = "none"
        downgrades.append("bad_evidence_class")
    citation = str(raw.get("citation") or "").strip()
    reasoning = str(raw.get("reasoning") or "").strip()[:400]
    done_date = _day(raw.get("done_date") or "")
    evidence_class = model_class
    sec, speaker, reason = None, "", ""

    def stop(name: str) -> None:
        nonlocal verdict
        verdict = "open"
        downgrades.append(name)

    if verdict in ("done", "moot"):
        sec = _locate(sections, citation) if citation else None
        if sec is None:
            if on("one_section"):
                stop("empty_citation" if not citation
                     else "citation_not_in_one_section")
            elif sections:
                sec = sections[0]

    if verdict in ("done", "moot") and sec is not None:
        if on("not_before_opened") and sec["date"] < (item.get("opened") or ""):
            stop("source_predates_item")
    if verdict in ("done", "moot") and sec is not None:
        # The section is the authority on what KIND of evidence a quotation
        # is. The model may name a class; it does not get to promote a line
        # from a call into a sent email.
        if on("class_from_section"):
            if sec["cls"] != model_class:
                downgrades.append(f"class_corrected_from_{model_class}")
            evidence_class = sec["cls"]
        if evidence_class in ("transcript_user", "transcript_other"):
            speaker = _citation_speaker(sec, citation)
            if (evidence_class == "transcript_user" and on("user_line")
                    and not _is_user_label(speaker, ctx)):
                evidence_class = "transcript_other"
                downgrades.append("citation_not_on_a_user_line")
        if evidence_class in MAIL_CLASSES:
            body_from = int(sec.get("header_len") or 0)
            q = _norm(citation)
            if (on("mail_body_not_header") and body_from
                    and q in _norm(sec["text"][:body_from])
                    and q not in _norm(sec["text"][body_from:])):
                # A subject names a deliverable whether or not it was
                # delivered ("Re: send me the deck").
                stop("citation_is_mail_metadata")
            own = sec.get("own")
            if (on("own_words_not_quote") and own is not None
                    and q not in _norm(sec["text"][:body_from])
                    and q not in _norm(context_pack.strip_markers(own))):
                # The words are in the message, under the reply: they belong
                # to whoever wrote the older mail. "I have sent the vouchers"
                # quoted beneath a "thanks" is the other side's sentence.
                stop("citation_is_quoted_text")
            row = sec.get("mail") or {}
            rcpts = [a.lower() for a in
                     (row.get("to") or []) + (row.get("cc") or [])]
            if (on("real_recipient") and row.get("direction") == "sent"
                    and (not rcpts
                         or all(a in ctx.self_emails for a in rcpts))):
                stop("mail_has_no_real_recipient")
            if (on("not_a_loop") and row.get("direction") == "inbound"
                    and (row.get("from") or "").lower() in ctx.self_emails):
                stop("mail_is_a_loop")

    if verdict == "done" and citation and on("not_a_promise") and (
            PROMISE_RE.search(citation)
            or PROMISE_CONTRACTION_RE.search(citation)):
        stop("citation_is_a_promise")
    if verdict == "done" and citation and on("not_a_denial") and \
            NOT_DONE_RE.search(citation):
        stop("citation_says_not_done")
    if verdict in ("done", "moot") and citation and on("not_the_task"):
        q = _norm(citation)
        for own in (_norm(item.get("task")), _norm(item.get("text"))):
            if own and len(q) >= 8 and (q in own or own in q):
                stop("citation_is_the_task")
                break

    needed = floor
    if verdict in ("done", "moot"):
        if verdict == "moot":
            if on("moot_needs_decision") and evidence_class != "decision":
                stop("moot_without_a_decision")
            else:
                reason = "decision_cancels_it"
        else:
            allowed, why, needed = class_policy(item, evidence_class, sec,
                                                speaker, floor)
            if allowed:
                reason = why
            elif on("class_allowed"):
                stop(why)
            else:
                reason = "strong_cited_evidence"
    if verdict in ("done", "moot") and on("confidence_floor") \
            and confidence < needed:
        stop(f"confidence_{confidence}_below_{needed}")
    if verdict in ("done", "moot") and on("pinned_open") \
            and record.get("pinned_open"):
        stop("pinned_open")

    if verdict in ("done", "moot"):
        lo = item.get("opened") or ""
        hi = _plus_days(today, 1)
        if not done_date or done_date < lo or done_date > hi:
            done_date = (sec or {}).get("date") or today
            downgrades.append("done_date_repaired")
    else:
        done_date, reason = "", ""
        if sec is None:
            evidence_class = "none"

    return {
        "id": item["id"], "verdict": verdict, "confidence": confidence,
        "evidence_class": evidence_class, "model_class": model_class,
        "citation": citation, "reasoning": reasoning, "done_date": done_date,
        "source": (sec or {}).get("source", ""), "reason": reason,
        "downgrades": downgrades, "had_evidence": bool(sections),
    }


def decide(item, result, record=None, floor=None) -> tuple:
    """auto | record, and why. There is no third answer: nothing here asks.

    `validate` has already applied every check, the pin and the floor among
    them; this reads its verdict. The two are kept apart so a caller can show
    a recorded verdict without having to re-derive why it was not applied.
    """
    if result.get("verdict") in ("done", "moot"):
        return "auto", result.get("reason") or "strong_cited_evidence"
    downgrades = result.get("downgrades") or []
    return "record", (downgrades[-1] if downgrades else result.get("verdict"))


def expire_candidates(items, results, state, today, degraded) -> list:
    """Items to close as expired: no due date, no deal, old enough, not
    pinned, and a complete search found nothing about them.

    A degraded run expires nothing. Absence is only evidence over a full
    search, and a mailbox that could not be read is not a full search.
    """
    days = config_loader.auto_close_expire_days()
    if not days or degraded:
        return []
    out = []
    for item in items:
        result = results.get(item["id"]) or {}
        record = (state.get("items") or {}).get(item["id"]) or {}
        if record.get("pinned_open") or item.get("has_due") or item.get("deal"):
            continue
        if _age_days(item.get("opened") or today, today) < days:
            continue
        if result.get("had_evidence") or result.get("verdict") not in (
                "unknowable", "open"):
            continue
        if not result.get("searched") or item.get("incomplete"):
            continue                  # capped or skipped: nobody looked
        out.append(item)
    return out


def expiry_reason() -> str:
    days = config_loader.auto_close_expire_days()
    return (f"expired: no due date, no deal, nothing about it in {days} days")


# ── writing: pre-image, close, undo ───────────────────────────────────────────

def _targets(item) -> list:
    paths = [str(config_loader.hotcache_path())]
    if item.get("source_path"):
        paths.append(str(item["source_path"]))
    paths.append(str(config_loader.workspace_week_md_path()))
    return [p for p in dict.fromkeys(paths) if os.path.exists(p)]


def _shots(item, key: str, into=None) -> list:
    """The lines carrying this item's id, per file. Called once before the
    close (`before`) and once after (`after`)."""
    shots = into if into is not None else []
    known = {(s["path"], s["line_number"]): s for s in shots}
    for path in _targets(item):
        lines = _read(path).splitlines(keepends=True)
        for i in item_ids.find_lines(lines, item["id"]):
            shot = known.get((path, i))
            if shot is None and into is None:
                shot = {"path": path, "line_number": i}
                shots.append(shot)
            if shot is not None:
                shot[key] = lines[i]
    return shots


def close_items(closes, live: bool, today: str) -> dict:
    """Close a batch through the one writer and keep what an undo needs.

    `closes` is `[(item, result)]`. Returns `{closed, failed, batch}` where
    `closed` rows are the day's record. Nothing is written when not live.
    """
    out = {"closed": [], "failed": [], "batch": ""}
    if not closes:
        return out
    rows, cands = [], []
    for item, result in closes:
        expired = str(result.get("reason", "")).startswith("expired")
        rows.append({
            "cb": item["id"], "text": item_ids.strip(item["text"]),
            "reason": result.get("reason", ""),
            "kind": "expired" if expired else "evidence",
            "evidence_class": result.get("evidence_class", "none"),
            "citation": result.get("citation", ""),
            "source": result.get("source", ""),
            "done_date": result.get("done_date") or today,
            "opened": item.get("opened", ""),
        })
        cands.append({"kind": "action_item", "tag": "Action Items",
                      "text": item["text"], "cb": item["id"],
                      "source_path": item.get("source_path") or "",
                      "reason": result.get("reason", ""),
                      # An expiry is not finished work, and the scorecard
                      # counts only finished work.
                      "kpi": not expired})
    if not live:
        out["closed"] = rows
        return out

    import completion_scan            # lazy: it imports the briefings
    shots = {item["id"]: _shots(item, "before") for item, _ in closes}
    marked = completion_scan.mark_done(cands)
    done = set(marked.get("by_id") or [])
    entries = []
    for (item, _result), row in zip(closes, rows):
        if item["id"] not in done:
            out["failed"].append(item["id"])
            continue
        _shots(item, "after", into=shots[item["id"]])
        entries.append({"cb": item["id"], "text": row["text"],
                        "reason": row["reason"], "kind": row["kind"],
                        "targets": shots[item["id"]]})
        out["closed"].append(row)
    if entries:
        out["batch"] = write_undo(entries, today)
    return out


def close_item(item, result, live: bool, today=None) -> dict:
    """One close. See `close_items`."""
    return close_items([(item, result)], live, _day(today) or _today())


def read_undo() -> dict:
    book = _read_json(undo_path(), {})
    book.setdefault("batches", {})
    book.setdefault("order", [])
    return book


def write_undo(entries, today: str) -> str:
    book = read_undo()
    n = sum(1 for b in book["order"] if b.startswith(f"dj-{today}")) + 1
    bid = f"dj-{today}-{n:02d}"
    book["batches"][bid] = {"date": today, "items": entries}
    book["order"].append(bid)
    for old in book["order"][:-UNDO_BATCHES]:
        book["batches"].pop(old, None)
    book["order"] = book["order"][-UNDO_BATCHES:]
    _write_json(undo_path(), book)
    return bid


_UNTICK_RE = re.compile(r"^(\s*[-*]\s*\[)[xX](\])")


def undo(cb: str) -> dict:
    """Reopen one item and pin it open.

    Byte for byte when the line is as the close left it. When someone has
    edited the line since, ONLY the box is cleared: their edit is newer than
    the close and outranks it. Either way the item is pinned in the state
    file, so no later run closes it again whatever a model says.
    """
    book = read_undo()
    entry, bid = None, ""
    for candidate in reversed(book["order"]):
        for row in (book["batches"].get(candidate) or {}).get("items") or []:
            if row.get("cb") == cb and not row.get("undone"):
                entry, bid = row, candidate
                break
        if entry:
            break
    notes, restored = [], 0
    if entry is not None:
        for shot in entry.get("targets") or []:
            path = shot.get("path") or ""
            lines = _read(path).splitlines(keepends=True)
            found = item_ids.find_lines(lines, cb)
            if not found:
                notes.append(f"{os.path.basename(path)}: line gone")
                continue
            hint = shot.get("line_number")
            idx = hint if hint in found else found[0]
            if shot.get("after") is not None and lines[idx] == shot["after"] \
                    and shot.get("before") is not None:
                lines[idx] = shot["before"]
                how = "restored exactly"
            else:
                lines[idx] = _UNTICK_RE.sub(r"\1 \2", lines[idx], count=1)
                how = "box cleared (the line was edited since)"
            item_ids.write_file(path, "".join(lines))
            restored += 1
            notes.append(f"{os.path.basename(path)}: {how}")
        entry["undone"] = True
        _write_json(undo_path(), book)
    else:
        # Not one of this module's closes, or older than the undo book. The
        # box is still cleared wherever the id sits, and the pin still set:
        # "never close this again" must work for any item.
        for path in _targets({"id": cb, "source_path": ""}):
            lines = _read(path).splitlines(keepends=True)
            hits = 0
            for i in item_ids.find_lines(lines, cb):
                cleared = _UNTICK_RE.sub(r"\1 \2", lines[i], count=1)
                hits += cleared != lines[i]
                lines[i] = cleared
            if hits:
                item_ids.write_file(path, "".join(lines))
                restored += 1
                notes.append(f"{os.path.basename(path)}: box cleared")

    state = read_state()
    record = state["items"].setdefault(cb, {})
    record.update({"pinned_open": True, "applied_at": None,
                   "last_verdict": "open"})
    write_state(state)

    latest = _read_json(latest_path(), {})
    if latest.get("closed"):
        latest["closed"] = [r for r in latest["closed"] if r.get("cb") != cb]
        latest["closed_n"] = sum(1 for r in latest["closed"]
                                 if r.get("kind") != "expired")
        latest["expired_n"] = sum(1 for r in latest["closed"]
                                  if r.get("kind") == "expired")
        _write_json(latest_path(), latest)
    return {"cb": cb, "batch": bid, "restored": restored, "notes": notes,
            "pinned": True, "found": entry is not None}


# ── the three reads the briefings import ──────────────────────────────────────

def closed_today(today=None) -> list:
    """What this module closed on `today`: `[{cb, text, reason, kind,
    evidence_class, citation, ...}]`. Empty when nothing was, or when the
    day's record is for another day. An item reopened since is not in it.
    """
    day = _day(today) or _today()
    latest = _read_json(latest_path(), {})
    if latest.get("date") != day:
        return []
    return [r for r in latest.get("closed") or []
            if isinstance(r, dict) and r.get("cb")]


def undo_command(cb: str = "cb-xxxxxxxx") -> tuple:
    """The reopen command for macOS and Linux, and for Windows."""
    posix = ('"$HOME/.config/van-gogh/venv/bin/python" '
             '"$(cat "$HOME/.config/van-gogh/plugin-root")/app/done_judge.py" '
             f'--undo {cb}')
    windows = ('& "$HOME\\.config\\van-gogh\\venv\\Scripts\\python.exe" '
               '"$(Get-Content "$HOME\\.config\\van-gogh\\plugin-root")'
               f'\\app\\done_judge.py" --undo {cb}')
    return posix, windows


def render_closed_md(rows) -> str:
    """The "Closed today" block, written by code because it carries ids a
    person will act on. Every line names its id and how to reopen it. ""
    when there is nothing to show.
    """
    rows = [r for r in rows or [] if isinstance(r, dict) and r.get("cb")]
    if not rows:
        return ""
    closed = [r for r in rows if r.get("kind") != "expired"]
    expired = [r for r in rows if r.get("kind") == "expired"]
    out = []

    def block(title, group, describe, mark):
        if not group:
            return
        out.append(f"**{title} ({len(group)})**")
        for row in group[:RENDER_CAP]:
            out.append(f"- {mark}{row.get('text', '')} "
                       f"({row['cb']}{describe(row)})")
        if len(group) > RENDER_CAP:
            out.append(f"- and {len(group) - RENDER_CAP} more, each with its "
                       "id, in `van-gogh/logs/done_judge_latest.json`")
        out.append("")

    def why(row):
        cite = " ".join(str(row.get("citation") or "").split())
        if len(cite) > 160:
            cite = cite[:157].rstrip() + "..."
        kind = str(row.get("evidence_class") or "").replace("_", " ")
        return f', {kind}: "{cite}"' if cite else (f", {kind}" if kind else "")

    # A finished item is ticked. An expired one was given up on, not done, so
    # it carries no tick: the two must not look alike on the page.
    block("Closed today", closed, why, "[x] ")
    block("Expired", expired,
          lambda row: ", no due date, no deal and nothing about it since "
                      f"{row.get('opened') or 'it was opened'}", "")
    import host
    undo = host.cmd("done-judge --undo")
    out.append(f"To put one back, type `{undo}` and its "
               f"id, for example `{undo} {rows[0]['cb']}`. "
               "An item you put back stays open for good.")
    return "\n".join(out)


def judge_note(today=None) -> str:
    """One sentence when the last check-off run failed, else "".

    Read from the run ledger, so it is true whether or not the failed run
    managed to leave a file: a day on which nothing closed because the run
    died must not read like a day on which nothing was finished.
    """
    day = _day(today) or _today()
    try:
        since = date.fromisoformat(day) - timedelta(days=1)
        rows = [r for r in run_ledger.read_runs(since=since)
                if r.get("job") == JOB_NAME]
    except Exception:                                           # noqa: BLE001
        return ""
    if not rows:
        return ""
    last = rows[-1]
    try:
        failed = int(last.get("rc", 0)) != 0
    except (TypeError, ValueError):
        failed = True
    if not failed:
        return ""
    cls = str(last.get("error_class") or "unknown")
    words = {"quota": "the Claude usage limit was reached",
             "version": "the Claude command line needs updating",
             "auth": "a sign-in has expired",
             "classifier": "Claude's safety check was unavailable",
             "network": "the network was unreachable"}.get(
                 cls, "the run failed")
    when = "today" if str(last.get("started", ""))[:10] == day else "yesterday"
    return f"The check-off did not run {when}: {words}. Nothing was closed."


# ── controls ──────────────────────────────────────────────────────────────────

CONTROLS_PATH = (Path(__file__).resolve().parent.parent / "tests" / "fixtures"
                 / "done_judge" / "controls.json")
_HEAD_ADDR_RE = re.compile(r"^(From|To|Cc): (.*)$", re.M)


def load_controls(path=None) -> tuple:
    """(items, ctx) from the planted controls.

    Known-open items carry evidence that looks like completion and is not;
    known-done items carry evidence that is. Sections are completed the way
    the real pack builder would have built them: a mail section gets its
    header length and its addresses read off its own header, and a
    transcript section gets its class from whose lines it holds.
    """
    data = _read_json(path or CONTROLS_PATH, {})
    user = data.get("user") or {}
    ctx = Ctx(today=data.get("today") or "2026-10-01",
              user_name=user.get("name", "the user"),
              user_variants=user.get("variants") or [],
              self_emails=user.get("emails") or [], mail=[], pages=[])
    items = []
    for row in data.get("controls") or []:
        sections = []
        for raw in row.get("sections") or []:
            sec = dict(raw)
            text = sec.get("text") or ""
            if sec.get("cls") in MAIL_CLASSES and "header_len" not in sec:
                cut = text.find("\n\n")
                sec["header_len"] = cut + 2 if cut >= 0 else 0
                head = text[:max(cut, 0)]
                people = {k.lower(): _EMAIL_RE.findall(v)
                          for k, v in _HEAD_ADDR_RE.findall(head)}
                direction = ("sent" if "(sent)" in head
                             else "inbound" if "(inbound)" in head
                             else "draft")
                sec.setdefault("mail", {
                    "direction": direction,
                    "from": (people.get("from") or [""])[0].lower(),
                    "to": [a.lower() for a in people.get("to") or []],
                    "cc": [a.lower() for a in people.get("cc") or []]})
            if sec.get("cls") == "transcript":
                sec["cls"] = _transcript_cls(text, ctx)
            sections.append(sec)
        items.append({
            "id": row["id"], "text": row["text"], "task": row["text"],
            "dir": row.get("dir", "by_me"), "who": row.get("who", ""),
            "opened": row.get("opened", "2026-07-20"), "due": "",
            "has_due": False, "deal": "", "source_title": "",
            "source_path": "", "meeting_id": "",
            "counterparties": [c.lower() for c in
                               row.get("counterparties") or []],
            "tokens": distinctive(row["text"]),
            "control_sections": sections,
            "control_kind": row.get("kind", "unknown"),
            "control_guard": row.get("guard", ""),
            "control_damning": row.get("damning", ""),
            "control_stub": row.get("stub") or {},
            "control_pinned": bool(row.get("pinned")),
        })
    return items, ctx


def judge_controls(runner=None, model=None, disabled=frozenset(),
                   path=None, floor=0.85) -> dict:
    """Run the controls through a runner and validate every answer.

    The floor is fixed here rather than read from config: the controls
    measure the judge, and a user who lowered their own floor must not be
    able to make the measurement pass.
    """
    items, ctx = load_controls(path)
    packs, sections_of, results = {}, {}, {}
    for item in items:
        pack, sections = build_pack(item, ctx)
        packs[item["id"]], sections_of[item["id"]] = pack, sections
    calls = 0
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        raw = judge_batch(batch, packs, runner=runner, model=model,
                          user_name=ctx.user_name)
        calls += 1
        for item in batch:
            row = raw.get(item["id"]) or {"verdict": "open"}
            record = {"pinned_open": True} if item["control_pinned"] else {}
            results[item["id"]] = validate(
                item, packs[item["id"]], sections_of[item["id"]], row, ctx,
                record=record, floor=floor, disabled=disabled)
    rows = []
    for item in items:
        result = results[item["id"]]
        closes = decide(item, result)[0] == "auto"
        rows.append({"id": item["id"], "kind": item["control_kind"],
                     "guard": item["control_guard"], "closes": closes,
                     "verdict": result["verdict"],
                     "confidence": result["confidence"],
                     "evidence_class": result["evidence_class"],
                     "citation": result["citation"],
                     "downgrades": result["downgrades"]})
    known_done = [r for r in rows if r["kind"] == "known_done"]
    known_open = [r for r in rows if r["kind"] != "known_done"]
    return {"rows": rows, "calls": calls,
            "known_open": len(known_open), "known_done": len(known_done),
            "false_done": [r["id"] for r in known_open if r["closes"]],
            "done_closed": [r["id"] for r in known_done if r["closes"]]}


def stub_runner(verdict: str = "done"):
    """A runner that answers `verdict` for every control, citing the
    sentence each control names as its most damning. No model is called."""
    by_id = {i["id"]: i for i in load_controls()[0]}

    class _Reply:
        returncode = 0
        stderr = ""

        def __init__(self, text):
            self.stdout = text

    def run(prompt, model=None, timeout=None):
        rows = []
        for cid in re.findall(r"^--- ITEM (\S+)$", prompt, re.M):
            item = by_id.get(cid) or {}
            sections = item.get("control_sections") or [{}]
            row = {"id": cid, "verdict": verdict, "confidence": 0.97,
                   "evidence_class": sections[0].get("cls", "none"),
                   "citation": item.get("control_damning", ""),
                   "reasoning": "stub", "done_date": ""}
            row.update(item.get("control_stub") or {})
            rows.append(row)
        return _Reply(json.dumps({"items": rows}))

    return run


def selftest(runner=None, model=None, out=print) -> int:
    """The planted controls through the model. Exit 0 only when no known-open
    control closes AND at least two known-done controls do: a judge that
    answers open to everything must fail this as surely as a reckless one.
    Writes nothing anywhere.
    """
    report = judge_controls(runner=runner, model=model or _model())
    for row in report["rows"]:
        mark = ("CLOSES" if row["closes"] else "open  ")
        out(f"  {mark} {row['id']:<28} {row['kind']:<12} "
            f"{row['verdict']:<10} {row['confidence']:<5} "
            f"{', '.join(row['downgrades'][-2:])}")
    ok = not report["false_done"] and len(report["done_closed"]) >= 2
    out(f"selftest: {report['known_open']} known-open controls, "
        f"{len(report['false_done'])} false done; "
        f"{report['known_done']} known-done controls, "
        f"{len(report['done_closed'])} closed; {report['calls']} model calls; "
        f"model {model or _model()}")
    if report["false_done"]:
        out(f"FALSE DONE: {', '.join(report['false_done'])}")
    out("selftest: PASS" if ok else "selftest: FAIL")
    return 0 if ok else 1


# ── the run ───────────────────────────────────────────────────────────────────

def _model() -> str:
    try:
        return config_loader.auto_close_model() or JUDGE_MODEL
    except Exception:                                           # noqa: BLE001
        return JUDGE_MODEL


FRESH_CLASSES = frozenset({"sent_mail", "inbound_mail", "decision"})


def queue_key(item, sections) -> tuple:
    """Sort key for the judging queue: the newest mail or decision first,
    then the oldest item. A meeting that merely shares words with a task is
    not fresh proof, and there is a new one of those every day."""
    newest = max([s["date"] for s in sections
                  if s.get("cls") in FRESH_CLASSES and s.get("date")] or [""])
    try:
        fresh = date.fromisoformat(newest).toordinal()
    except ValueError:
        fresh = 0
    return (-fresh, item.get("opened") or "")


def run(today=None, live=False, only=None, max_calls=MAX_CALLS, runner=None,
        ctx=None, out=print, clock=None) -> dict:
    """One pass over the open set. Returns the day's record.

    Every batch is judged before anything is applied, so a model that stops
    answering part way through costs the whole run and never half of one.
    """
    ctx = ctx or Ctx(today)
    today = ctx.today
    model = _model()
    # A hot cache that exists and cannot be opened is not an empty to-do
    # list. Read as one, a locked vault would report "nothing closed today"
    # every morning, which is the quiet day this job must never fake.
    hot = config_loader.hotcache_path()
    if os.path.exists(hot):
        try:
            with open(hot, encoding="utf-8") as f:
                f.read(1)
        except OSError as exc:
            raise JudgeUnavailable(
                f"the vault could not be read ({type(exc).__name__})") from exc
    stamped = stamp_missing(today, live)
    items = build_open_set(ctx, hotcache_text=stamped["text"] or None,
                           only=only)
    state = read_state()

    if items and not ctx.mail_loaded:
        floor_day = max(min(i["opened"] or today for i in items),
                        _plus_days(today, -LOOKBACK_DAYS))
        senders = [e for i in items for e in i["counterparties"]]
        ctx.load_mail(_plus_days(floor_day, -MAIL_GRACE_DAYS), senders)

    packs, sections_of, results, queue = {}, {}, {}, []
    empty = unchanged = 0
    for item in items:
        record = state["items"].get(item["id"]) or {}
        pack, sections = build_pack(item, ctx)
        if not sections:
            empty += 1
            results[item["id"]] = {
                "id": item["id"], "verdict": "unknowable", "confidence": 0.0,
                "evidence_class": "none", "citation": "", "reasoning": "",
                "done_date": "", "source": "", "reason": "",
                "downgrades": ["no_evidence"], "had_evidence": False,
                "searched": True, "evidence_hash": ""}
            continue
        ehash = evidence_hash(item, pack)
        if not needs_judging(item, ehash, record, today, force=bool(only)):
            unchanged += 1
            continue
        packs[item["id"]], sections_of[item["id"]] = pack, sections
        item["evidence_hash"] = ehash
        queue.append(item)

    # Freshest proof first. Oldest first meant the backlog filled every run,
    # and the to-do whose answer went out last night, which is the one on
    # this morning's briefing, was read last or not at all.
    queue.sort(key=lambda it: queue_key(it, sections_of[it["id"]]))
    batches = [queue[i:i + BATCH_SIZE] for i in range(0, len(queue), BATCH_SIZE)]
    cap = int(max_calls or 0) if int(max_calls or 0) > 0 else len(batches)
    clock = clock or time.monotonic
    started = clock()
    raw_by_id, calls = {}, 0
    for batch in batches[:cap]:
        if calls and (clock() - started) / 60.0 >= MAX_MINUTES:
            cap = calls
            break
        raw_by_id.update(judge_batch(batch, packs, runner=runner, model=model,
                                     user_name=ctx.user_name))
        calls += 1
    capped = sum(len(b) for b in batches[cap:])

    floor = config_loader.auto_close_confidence()
    closes = []
    for batch in batches[:cap]:
        for item in batch:
            record = state["items"].get(item["id"]) or {}
            row = raw_by_id.get(item["id"])
            if row is None:
                row = {"verdict": "open"}
                omitted = True
            else:
                omitted = False
            result = validate(item, packs[item["id"]],
                              sections_of[item["id"]], row, ctx,
                              record=record, floor=floor)
            if omitted:
                result["downgrades"].append("model_omitted_id")
            result["searched"] = True
            result["evidence_hash"] = item["evidence_hash"]
            results[item["id"]] = result
            if decide(item, result, record, floor)[0] == "auto":
                closes.append((item, result))

    closing = {item["id"] for item, _ in closes}
    expiring = [i for i in expire_candidates(items, results, state, today,
                                             ctx.degraded)
                if i["id"] not in closing]
    reason = expiry_reason()
    for item in expiring:
        closes.append((item, {"reason": reason, "evidence_class": "none",
                              "citation": "", "source": "",
                              "done_date": today}))

    applied = close_items(closes, live, today)

    # The run's own day, not the wall clock's: "judged a week ago" is read
    # back against `today`, and two clocks in one comparison is how a test
    # written today goes red next week with no code change.
    stamp = f"{today}T{datetime.now().strftime('%H:%M:%S')}"
    closed_ids = {r["cb"] for r in applied["closed"]}
    if live:
        for item in items:
            result = results.get(item["id"])
            if not result:
                continue
            record = state["items"].setdefault(item["id"], {})
            record.update({
                "judged_at": stamp, "evidence_hash": result["evidence_hash"],
                "last_verdict": result["verdict"],
                "confidence": result["confidence"],
                "evidence_class": result["evidence_class"],
                "citation": result["citation"][:400],
                "reasoning": result["reasoning"],
                "downgrades": result["downgrades"],
                "text": item_ids.strip(item["text"])[:200],
            })
            if item["id"] in closed_ids:
                row = next(r for r in applied["closed"]
                           if r["cb"] == item["id"])
                record.update({"applied_at": stamp, "reason": row["reason"],
                               "batch": applied["batch"]})
        state["last_run"] = {"date": today, "finished": stamp, "calls": calls}
        write_state(state)

    payload = {
        "date": today, "mode": "live" if live else "dry-run",
        "finished": stamp, "model": model,
        "vault": str(config_loader.vault()),
        "open_items": len(items), "judged": len(queue) - capped,
        "empty_pack": empty, "unchanged": unchanged, "calls": calls,
        "capped": capped, "degraded": sorted(set(ctx.degraded)),
        "transcripts_fetched": ctx.fetches,
        "transcript_cap_hit": ctx.fetch_capped,
        "stamped": {"hotcache": stamped["hotcache"],
                    "pages": stamped["pages"]},
        "closed": applied["closed"],
        "closed_n": sum(1 for r in applied["closed"] if r["kind"] != "expired"),
        "expired_n": sum(1 for r in applied["closed"]
                         if r["kind"] == "expired"),
        "failed": applied["failed"], "batch": applied["batch"],
        "recorded": [{"cb": cid, "verdict": r["verdict"],
                      "why": (r["downgrades"] or [""])[-1]}
                     for cid, r in sorted(results.items())
                     if cid not in closed_ids and r.get("had_evidence")][:200],
    }
    if live:
        previous = _read_json(latest_path(), {})
        if previous.get("date") == today:
            # A second run the same day adds to the day's record: the
            # briefing shows what closed today, not what closed last.
            seen = {r["cb"] for r in payload["closed"]}
            payload["closed"] = [r for r in previous.get("closed") or []
                                 if r.get("cb") not in seen] + payload["closed"]
            payload["closed_n"] = sum(1 for r in payload["closed"]
                                      if r["kind"] != "expired")
            payload["expired_n"] = sum(1 for r in payload["closed"]
                                       if r["kind"] == "expired")
        _write_json(latest_path(), payload)
    else:
        try:
            _write_json(dry_path(), payload)
        except OSError:
            pass
    return payload


def print_run(payload, out=print) -> None:
    dry = payload["mode"] != "live"
    out(f"vault: {payload['vault']}")
    out(f"mode: {payload['mode']}  model: {payload['model']}  "
        f"date: {payload['date']}")
    out(f"open items: {payload['open_items']}  with evidence and judged: "
        f"{payload['judged']}  no evidence: {payload['empty_pack']}  "
        f"unchanged since last run: {payload['unchanged']}  "
        f"not reached: {payload['capped']}")
    out(f"calls: {payload['calls']}")
    if payload["degraded"]:
        out("evidence incomplete, so nothing expires this run: "
            + "; ".join(payload["degraded"]))
    verb = "would close" if dry else "closed"
    for row in payload["closed"]:
        if row["kind"] == "expired":
            out(f"  {verb} (expired) {row['cb']}  {row['text'][:90]}")
        else:
            out(f"  {verb} {row['cb']} [{row['evidence_class']}] "
                f"{row['text'][:70]} :: \"{row['citation'][:90]}\"")
    for row in payload["recorded"][:40]:
        out(f"  left open {row['cb']}  "
            f"{row['why'] or 'the model found no proof it is finished'}")
    out(f"{verb}: {payload['closed_n']}  "
        f"{'would expire' if dry else 'expired'}: {payload['expired_n']}")
    if dry:
        out("Nothing was written in the vault. Re-run with --live to apply.")


def why(cb: str) -> dict:
    """The recorded verdict for one item, from the state file."""
    record = (read_state().get("items") or {}).get(cb)
    if not record:
        return {"cb": cb, "found": False}
    keys = ("text", "last_verdict", "confidence", "evidence_class",
            "citation", "reasoning", "downgrades", "judged_at", "applied_at",
            "reason", "pinned_open")
    return {"cb": cb, "found": True, **{k: record.get(k) for k in keys}}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Close the to-dos the evidence shows are finished.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true",
                      help="apply: stamp ids, close, expire, write the record")
    mode.add_argument("--dry-run", action="store_true",
                      help="judge and print, write nothing in the vault "
                           "(the default)")
    ap.add_argument("--only", metavar="CB", help="judge this one item now")
    ap.add_argument("--undo", metavar="CB",
                    help="reopen this item and pin it open")
    ap.add_argument("--why", metavar="CB",
                    help="print the recorded verdict for this item")
    ap.add_argument("--closed-today", action="store_true",
                    help="print what was closed today")
    ap.add_argument("--selftest", action="store_true",
                    help="run the planted controls through the real model")
    ap.add_argument("--backfill-only", action="store_true",
                    help="stamp ids and stop (writes only with --live)")
    ap.add_argument("--today", metavar="YYYY-MM-DD",
                    help="treat this as today's date")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS,
                    help="model calls allowed in one run (default: no limit)")
    ap.add_argument("--claude", metavar="PATH",
                    help="path to the claude CLI (the scheduler passes it)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    return ap.parse_args(argv)


def _use_claude(path) -> None:
    """Make the CLI the scheduler resolved the one this process finds.

    launchd hands a job a PATH too sparse to find the binary, and every
    model call here goes through `claude_cli`, which looks on PATH.
    """
    if path:
        folder = os.path.dirname(os.path.abspath(path))
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def main(argv=None) -> int:
    args = parse_args(argv)
    for flag in (args.only, args.undo, args.why):
        if flag and not _CB_ARG_RE.match(flag):
            print(f"ERROR: {flag!r} is not an item id (cb- and 8 characters)",
                  file=sys.stderr)
            return 2
    # ANTHROPIC_API_KEY is never used: model calls go through the CLI on the
    # user's subscription, and `claude_cli` drops the key from the child.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    _use_claude(args.claude)

    # The hand tools. None of them is a run of the job, so none records one.
    if args.closed_today:
        rows = closed_today(args.today)
        if args.json:
            print(json.dumps({"date": _day(args.today) or _today(),
                              "closed": rows, "note": judge_note(args.today)},
                             indent=2, default=str))
        else:
            print(render_closed_md(rows) or "Nothing was closed today.")
            note = judge_note(args.today)
            if note:
                print(note)
        return 0
    if args.why:
        got = why(args.why)
        if args.json:
            print(json.dumps(got, indent=2, default=str))
        elif not got["found"]:
            print(f"{args.why}: no verdict recorded.")
        else:
            for key, value in got.items():
                if key not in ("cb", "found") and value not in (None, "", []):
                    print(f"{key}: {value}")
        return 0
    if args.undo:
        got = undo(args.undo)
        if args.json:
            print(json.dumps(got, indent=2, default=str))
        else:
            for note in got["notes"]:
                print(f"  {note}")
            print(f"{args.undo} is open again and will stay open."
                  if got["restored"] else
                  f"{args.undo} was not found on any line; it is pinned "
                  "open all the same.")
        return 0
    if args.selftest:
        try:
            claude_update.ensure_current(args.claude)
        except Exception:                                       # noqa: BLE001
            pass
        try:
            return selftest()
        except JudgeUnavailable as exc:
            print(f"selftest: the model could not be reached: {exc}",
                  file=sys.stderr)
            return RC_UNAVAILABLE
    if args.backfill_only:
        got = stamp_missing(args.today, args.live)
        got.pop("text", None)
        got["mode"] = "live" if args.live else "dry-run"
        print(json.dumps(got, indent=2))
        return 0

    live = bool(args.live)
    if live and not config_loader.auto_close_enabled() and not args.only:
        print("Auto close is off (auto_close.enabled). Nothing was done.")
        return 0

    # A dry run records nothing in the ledger: a row saying the job ran
    # would satisfy the briefings waiting on it, and it did not run.
    started = run_ledger.now_stamp()
    if live:
        run_ledger.mark_started(JOB_NAME)
    try:
        if live:
            run_ledger.wait_for_dependencies("done-judge")
        try:
            claude_update.ensure_current(args.claude)
        except Exception:                                       # noqa: BLE001
            pass
        payload = run(today=args.today, live=live, only=args.only,
                      max_calls=args.max_calls)
    except JudgeUnavailable as exc:
        detail = str(exc) or "the model could not be reached"
        if live:
            run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(),
                                  RC_UNAVAILABLE, detail)
        print(f"ERROR: the check-off did not run: {detail}", file=sys.stderr)
        return RC_UNAVAILABLE
    except Exception as exc:                                    # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        if live:
            run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(),
                                  1, detail)
            try:
                import self_anneal
                self_anneal.record_failure(JOB_NAME, detail)
            except Exception:                                   # noqa: BLE001
                pass
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1

    if live:
        run_ledger.record_run(
            JOB_NAME, started, run_ledger.now_stamp(), 0,
            f"closed {payload['closed_n']}, expired {payload['expired_n']}, "
            f"calls {payload['calls']}, not reached {payload['capped']}")
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print_run(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
