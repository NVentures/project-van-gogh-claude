#!/usr/bin/env python3
"""The advisor: look across everything, suggest work, do it on a yes.

Every briefing answers one question at one hour, and the watcher answers
whether the machinery ran. Nothing asked the question above both: of
everything on the record, what should somebody do next. This does, once a
day, and it finishes the thought: it says what it would do, waits for a yes
or a no, and on a yes does the work and leaves it for the user to check.

    look      gather the situation in code, then let a read-only model
              session read the vault and name at most a few things to do
    check     every suggestion is validated here, in code, before a person
              sees it: its evidence is found in the vault word for word, its
              address is one the vault already holds, its edit still applies
    ask       each survivor is filed as a ticket waiting on a nod, and one
              email lists them with a number each
    answer    a yes or a no arrives as a reply to that email, a press in the
              Workbench, or a line in a terminal
    do        on a yes: a draft in the user's own Drafts folder, a file in
              the deliverables folder, or a small edit to a vault page with
              the old version kept beside it

Five rules carry the design. Each is enforced here and none is left to the
model's good sense, because a prompt instruction is necessary and never
sufficient.

**The session that looks cannot write.** It runs through
`claude_cli.run_claude_readonly`: three tools, Read, Grep and Glob, confined
to the vault. It has no way to send, draft, edit or run anything, so the
worst a bad session can do is suggest something that fails the checks below.

**Evidence is checked against the page, not taken on trust.** A model asked
to quote tidies what it quotes. Every suggestion names a vault page and a
passage, and the passage must be found in that page after whitespace is
collapsed. A suggestion with no evidence that survives is dropped whole.

**Only the user's own words count as a yes.** An email answer is read from
the Sent folder of the account the suggestions were mailed to, never from an
inbox: anyone can put a "Y" in an inbox, and only the account holder can put
one in Sent. The answer is matched by a strict pattern on the leading lines
of the reply. Anything else is ignored, which costs a re-ask and never a
wrong action.

**A yes never sends.** It leaves a draft. Sending that draft from a reply is
a second, separate instruction (`SEND` and the number), and a second,
separate switch (`advisor.allow_send`) that ships off.

**A no is remembered.** A dismissed suggestion keeps its place in the ledger,
so the same one cannot come back the next morning in other words.

It is called by the watcher at the end of each tick (`job_watch.main`), which
is what makes it the layer above everything: the watcher keeps the jobs
running, then asks this what is worth doing. Every path fails open.

CLI:
    advisor.py --tick                 what the watcher runs: answers, work, ask
    advisor.py --propose [--force] [--no-mail] [--dry-run]
    advisor.py --list                 suggestions waiting on an answer, as JSON
    advisor.py --approve ID           yes, and do it now
    advisor.py --dismiss ID           no
    advisor.py --send ID              send the draft a yes produced
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import json
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import run_ledger                                               # noqa: E402

SOURCE = "advisor"
AGENT_FILE = "chief-of-staff.md"
JOB_PROPOSE = "advisor.propose"
JOB_EXECUTE = "advisor.execute"
STATE_NAME = "advisor-state.json"

# kind, as the model names it -> ticket type, as the ledger and the page know
# it. A draft is deliberately NOT the ledger's `email` type: the Workbench
# draws `SEND` on a waiting email ticket, and a yes here writes a draft.
TYPE_FOR = {"draft": "draft", "file": "pptx", "vault": "vault"}
KIND_FOR = {v: k for k, v in TYPE_FOR.items()}

# Reading a vault and thinking is slower than a classification and faster
# than a render. Fifteen minutes is the mechanic's bound for the same shape
# of work.
TIMEOUT_S = 15 * 60

# How long a batch of suggestions keeps listening for a reply. After a week
# the situation it described is gone, and a late "Y 2" would approve
# something nobody remembers the meaning of.
BATCH_DAYS = 7

# Work done per tick. The watcher's tick is meant to be cheap, and a morning
# where five things were approved at once can spread over two ticks.
MAX_EXECUTE_PER_TICK = 3

MAX_EDITS = 5                 # per vault suggestion
MAX_REPLACE_CHARS = 2000
MIN_QUOTE_CHARS = 12          # shorter than this matches by accident
MIN_BODY_CHARS = 20           # a greeting, a sentence and a name
SITUATION_ITEMS = 15

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ── Small helpers ────────────────────────────────────────────────────────────

def _strip_dashes(text) -> str:
    """Em and en dashes out of anything shown, mailed or written."""
    return str(text or "").replace(chr(0x2014), ", ").replace(chr(0x2013), "-")


def _clean(text, limit: int = 0) -> str:
    out = re.sub(r"\s+", " ", _strip_dashes(text)).strip()
    return out[:limit].rstrip() if limit else out


def _norm(text) -> str:
    """Whitespace collapsed, for comparing a quote against its page."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def state_path() -> Path:
    import user_state
    return user_state.state_dir() / STATE_NAME


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, default=str),
                        encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass  # fails open: an unsaved stamp costs one extra look, never a run


def _store():
    import workbench_store
    return workbench_store


def _vault() -> Path:
    return Path(config_loader.vault())


def _vault_file(rel, vault: Path | None = None):
    """A vault-relative path, resolved, or None if it leaves the vault.

    Refuses an absolute path and anything that resolves outside the vault
    after `..` and symlinks are followed. This is the one gate every path the
    model names goes through, for reading evidence and for editing alike.
    """
    try:
        text = str(rel or "").strip()
        if not text or Path(text).is_absolute():
            return None
        root = (vault or _vault()).resolve()
        path = (root / text).resolve()
        if root != path and root not in path.parents:
            return None
        return path
    except (OSError, ValueError):
        return None


def _editable(path: Path, vault: Path) -> bool:
    """Whether a vault edit may touch this file.

    Notes only. Not the product's own folder, which is machinery, and not
    Obsidian's settings; only a markdown file that already exists. A
    suggestion cannot create a page or change anything that is not a note.
    """
    try:
        rel = path.resolve().relative_to(vault.resolve())
    except (OSError, ValueError):
        return False
    if not rel.parts or rel.parts[0] in ("van-gogh", ".obsidian", ".git",
                                         ".trash"):
        return False
    return path.suffix.lower() == ".md" and path.is_file()


# ── The situation, gathered by code ──────────────────────────────────────────

def agent_rules() -> str:
    """The chief of staff's own file, without its frontmatter.

    Read from disk at call time, so there is one copy of how the agent
    thinks. A second copy written into this module would be the one that
    drifted, and it would be the one the model obeyed.
    """
    path = Path(__file__).resolve().parent.parent / "agents" / AGENT_FILE
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def _rel(path) -> str:
    try:
        return Path(path).resolve().relative_to(_vault().resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def situation(now: datetime | None = None) -> dict:
    """What code already knows, handed to the model so it starts informed.

    Every part is gathered separately and fails separately: a briefing that
    has never rendered costs the situation its front page, not the look.
    """
    now = now or datetime.now()
    out = {"today": now.strftime("%A %Y-%m-%d"), "front_page": [],
           "stopped_moving": [], "scheduled_jobs": "", "already_suggested": [],
           "declined": [], "where_things_are": {}, "accounts": []}
    try:
        import workbench_data
        for item in workbench_data.attention_items()[:SITUATION_ITEMS]:
            out["front_page"].append({
                "who": item.get("counterparty_name"),
                "address": item.get("counterparty_email"),
                "account": item.get("account"),
                "subject": item.get("subject"),
                "summary": _clean(item.get("summary"), 300),
                "suggested_action": _clean(item.get("suggested_action"), 200),
                "days_old": item.get("age_days"),
                "urgency": item.get("urgency"),
            })
    except Exception:                                           # noqa: BLE001
        pass
    try:
        import task_stall
        for row in task_stall.stalled(now)[:10]:
            out["stopped_moving"].append(
                {k: v for k, v in row.items() if not str(k).startswith("_")})
    except Exception:                                           # noqa: BLE001
        pass
    try:
        import job_watch
        out["scheduled_jobs"] = job_watch.summary(now).get("line", "")
    except Exception:                                           # noqa: BLE001
        pass
    try:
        for ticket in _store().by_source(SOURCE):
            bucket = ("declined" if ticket.get("state") == "dismissed"
                      else "already_suggested")
            out[bucket].append(ticket.get("title", ""))
    except Exception:                                           # noqa: BLE001
        pass
    try:
        for key, fn in (("deal_record", config_loader.hotcache_path),
                        ("people_and_companies", config_loader.entities_dir)):
            rel = _rel(fn())
            if rel:
                out["where_things_are"][key] = rel
    except Exception:                                           # noqa: BLE001
        pass
    try:
        out["accounts"] = [a["label"] for a in config_loader.accounts()]
    except Exception:                                           # noqa: BLE001
        pass
    return out


_CONTRACT = """
## What to return

Return ONE JSON object and nothing else. No prose before or after it.

{{"proposals": [
  {{"kind": "draft", "title": "...", "why": "...", "plan": "...",
    "evidence": [{{"path": "wiki/...md", "quote": "copied exactly"}}],
    "to": "name@example.com", "to_name": "...", "account": "...",
    "subject": "...", "brief": "what the message should accomplish"}},
  {{"kind": "file", "title": "...", "why": "...", "plan": "...",
    "evidence": [{{"path": "...", "quote": "..."}}],
    "file_type": "pptx", "done_when": "what finished looks like"}},
  {{"kind": "vault", "title": "...", "why": "...", "plan": "...",
    "evidence": [{{"path": "...", "quote": "..."}}],
    "edits": [{{"path": "wiki/...md", "find": "exact text on the page",
               "replace": "what goes there instead"}}]}}
]}}

- At most {cap} proposals. Fewer is better. `{{"proposals": []}}` is a good
  answer on a quiet day.
- `title` is one line a person would say out loud. `why` is one or two
  sentences. `plan` says exactly what you would do.
- Every `path` is relative to the vault you are reading. Every `quote` and
  every `find` is copied from that file character for character.
- `to` must be an address that appears in the situation below or in a vault
  page you cite. `account` is one of: {accounts}.
- `find` must appear exactly once on its page.
"""


def build_prompt(sit: dict, cap: int) -> str:
    accounts = ", ".join(sit.get("accounts") or []) or "(none configured)"
    return (agent_rules()
            + "\n" + _CONTRACT.format(cap=cap, accounts=accounts)
            + "\n## The situation today\n\n```json\n"
            + json.dumps(sit, indent=2, default=str) + "\n```\n")


# ── Checking a suggestion before a person sees it ────────────────────────────

def _checked_evidence(raw, vault: Path) -> tuple:
    """(evidence that was found on its page, text of every cited page)."""
    kept, texts = [], []
    for entry in (raw if isinstance(raw, list) else [])[:5]:
        if not isinstance(entry, dict):
            continue
        path = _vault_file(entry.get("path"), vault)
        quote = _norm(entry.get("quote"))
        if path is None or not path.is_file() or len(quote) < MIN_QUOTE_CHARS:
            continue
        try:
            page = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        texts.append(page)
        if quote in _norm(page):
            kept.append({"path": path.relative_to(vault.resolve()).as_posix(),
                         "quote": quote[:400]})
    return kept, texts


def _known_addresses(sit: dict, texts: list) -> set:
    known = {str(i.get("address") or "").lower()
             for i in sit.get("front_page", [])}
    for text in texts:
        known |= {m.lower() for m in _ADDRESS_RE.findall(text)}
    known.discard("")
    return known


def _checked_edits(raw, vault: Path) -> tuple:
    """(edits that apply cleanly right now, the reason if any do not).

    All or nothing: a suggestion whose second edit no longer applies would
    leave the page half changed, so one bad edit rejects the suggestion.
    """
    edits = []
    rows = raw if isinstance(raw, list) else []
    if not 1 <= len(rows) <= MAX_EDITS:
        return [], f"a vault edit needs between 1 and {MAX_EDITS} changes"
    for entry in rows:
        if not isinstance(entry, dict):
            return [], "an edit was not readable"
        path = _vault_file(entry.get("path"), vault)
        if path is None or not _editable(path, vault):
            return [], "an edit named a page that may not be changed"
        find = str(entry.get("find") or "")
        replace = _strip_dashes(entry.get("replace"))
        if not find.strip() or find == replace:
            return [], "an edit changed nothing"
        if len(replace) > MAX_REPLACE_CHARS:
            return [], "an edit was too large to be a correction"
        try:
            page = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], "a page could not be read"
        if page.count(find) != 1:
            return [], "the text to change was not found exactly once"
        edits.append({"path": path.relative_to(vault.resolve()).as_posix(),
                      "find": find, "replace": replace})
    return edits, ""


def validate(raw, sit: dict, vault: Path | None = None) -> tuple:
    """(ticket, "") for a suggestion worth showing, or (None, why not).

    This is the guard. The agent file asks for every one of these properties
    in words, and each is checked again here, because the model follows such
    rules most of the time and "most" is not a property a person can rely on
    before answering yes.
    """
    vault = (vault or _vault()).resolve()
    if not isinstance(raw, dict):
        return None, "not a suggestion"
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in TYPE_FOR:
        return None, f"an unknown kind of work: {kind or 'none'}"
    title = _clean(raw.get("title"), 120)
    why = _clean(raw.get("why"), 400)
    plan = _clean(raw.get("plan"), 600)
    if not title or not why or not plan:
        return None, "it did not say what, why and how"

    evidence, texts = _checked_evidence(raw.get("evidence"), vault)
    if not evidence:
        return None, "none of its evidence was found in the vault"

    staged = {"kind": kind, "why": why, "plan": plan, "evidence": evidence}
    who_name = who_email = account = ""
    subject = title
    dod = plan

    if kind == "draft":
        try:
            from send_email import clean_address
            who_email = clean_address(str(raw.get("to") or ""), "recipient").lower()
        except Exception:                                       # noqa: BLE001
            return None, "it named no usable address"
        if who_email not in _known_addresses(sit, texts):
            return None, "its address is not one the vault holds"
        who_name = _clean(raw.get("to_name"), 80)
        subject = _clean(raw.get("subject"), 150)
        brief = _clean(raw.get("brief"), 800)
        if not subject or not brief:
            return None, "a draft needs a subject and a purpose"
        labels = {a["label"].lower(): a["label"] for a in config_loader.accounts()}
        account = labels.get(str(raw.get("account") or "").strip().lower(), "")
        if not account:
            primary = config_loader.primary_account()
            account = primary["label"] if primary else ""
        if not account:
            return None, "no account is configured to draft from"
        staged.update(to=who_email, to_name=who_name, account=account,
                      subject=subject, brief=brief)
        dod = f"Drafted a message to {who_name or who_email}: {subject}."
    elif kind == "file":
        import deliverable
        file_type = str(raw.get("file_type") or "pptx").strip().lower()
        if file_type not in deliverable.BUILDERS:
            return None, f"nothing here can build a {file_type} file yet"
        dod = _clean(raw.get("done_when"), 400) or plan
        staged.update(file_type=file_type, done_when=dod)
    else:
        edits, reason = _checked_edits(raw.get("edits"), vault)
        if not edits:
            return None, reason
        staged["edits"] = edits
        pages = sorted({e["path"] for e in edits})
        dod = "Corrected " + ", ".join(pages) + "."

    store = _store()
    tid = store.ticket_id(SOURCE, who_email, f"{kind} {subject}")
    stamp = _now()
    return {
        "id": tid,
        "type": TYPE_FOR[kind],
        "state": "staged",
        "title": title,
        "source": SOURCE,
        "counterparty_name": who_name,
        "counterparty_email": who_email,
        "account": account,
        "context": {"subject": subject, "body_preview": "", "summary": why,
                    "intent": "", "urgency": "medium",
                    "suggested_action": plan, "age_days": 0},
        "dod": dod,
        "comments": [],
        "staged": staged,
        "delivery": None,
        "rating": None,
        "created_at": stamp,
        "history": [{"at": stamp, "to": "staged",
                     "note": "suggested, waiting on you"}],
    }, ""


# ── Asking ───────────────────────────────────────────────────────────────────

def _due(now: datetime, state: dict) -> tuple:
    """(may the day's look happen now, why not)."""
    if state.get("day") == now.date().isoformat():
        return False, "already looked today"
    hour, minute = (int(p) for p in config_loader.advisor_time().split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False, "before the time it is set to look"
    return True, ""


def _mail(subject: str, body: str) -> None:
    """One email to the user, from the digest sender to the digest recipient."""
    import digest_html
    from send_email import send_email
    account = config_loader.digest_sender_account()
    to = config_loader.digest_recipient_email()
    if not account or not to:
        raise RuntimeError("no configured sender or recipient")
    try:
        page = digest_html.md_to_email_html(body)
    except Exception:                                           # noqa: BLE001
        page = None
    # Not retried: a lost response after the server accepted the message is
    # indistinguishable from a failure, and two copies is the worse outcome.
    send_email(account, to, subject, body, html=page)


def reply_account() -> dict | None:
    """The configured account the suggestions are mailed TO, or None.

    A reply is only ever read from this account's Sent folder. When the
    recipient is an address this install holds no sign-in for, there is no
    Sent folder to read and replies cannot be honoured, so the email says to
    answer in the Workbench instead.
    """
    try:
        to = (config_loader.digest_recipient_email() or "").strip().lower()
        for account in config_loader.accounts():
            if (account.get("email") or "").strip().lower() == to:
                return account
    except Exception:                                           # noqa: BLE001
        pass
    return None


def _count_word(n: int) -> str:
    return {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}.get(n, str(n))


def batch_code(ids: list) -> str:
    """The reference a list of suggestions travels under.

    Derived from the ids in the order they are numbered, so the same list
    always carries the same reference and a reference can only ever mean one
    numbering. That is what makes it safe for a briefing and the suggestions
    email to show the same list: whichever one is replied to, `Y 2` means the
    same ticket. A random code minted at send time could not promise that,
    because a briefing is rendered in one process and mailed by another.
    """
    digest = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()
    return "VG-" + digest[:6].upper()


def register_batch(ids: list, now: datetime | None = None,
                   state: dict | None = None) -> str:
    """Start (or keep) listening for answers to this numbered list.

    Returns its reference, or "" when replies cannot be read on this machine.
    Idempotent: registering the same list again only moves its date forward.
    """
    account = reply_account()
    if not ids or account is None:
        return ""
    own = state is None
    state = _read_state() if own else state
    code = batch_code(ids)
    batch = state.setdefault("batches", {}).setdefault(code, {"seen": []})
    batch.update(at=(now or datetime.now()).isoformat(timespec="seconds"),
                 label=account["label"], items=list(ids))
    _write_state(state)
    return code


def how_to_answer(n: int, code: str, allow_send: bool) -> list:
    """The closing lines under any list of suggestions, on any surface."""
    lines = []
    if code:
        example = "Y 1" if n == 1 else f"Y 1 {n}"
        lines.append(
            f"A reply to the email this arrived in is how to answer: "
            f"`{example}` is a yes, `N {n}` is a no, and `Y ALL` takes "
            f"everything. It has to be the first line, with this reference "
            f"left in the reply: {code}. The Workbench takes the same "
            "answers, where KICK OFF is a yes and Dismiss is a no.")
    else:
        lines.append("These are answered in the Workbench, where KICK OFF is "
                     "a yes and Dismiss is a no, or in a chat with "
                     "/van-gogh:suggest.")
    lines.append("")
    lines.append("A yes leaves a draft, a file or a corrected page for you to "
                 "check. Nothing is sent to anyone.")
    if code and allow_send:
        lines.append("Once a draft is ready, a reply of `SEND` and its "
                     "number sends it as it stands in your Drafts folder.")
    return lines


def email_body(tickets: list, code: str, allow_send: bool) -> str:
    n = len(tickets)
    lines = [f"{_count_word(n)} thing{'s' if n != 1 else ''} I could take off "
             "your plate today. Nothing below has been done yet.", ""]
    for i, ticket in enumerate(tickets, 1):
        staged = ticket.get("staged") or {}
        lines.append(f"**{i}. {ticket['title']}**")
        lines.append("")
        lines.append(f"Why: {staged.get('why', '')}")
        lines.append("")
        lines.append(f"What I would do: {staged.get('plan', '')}")
        for edit in staged.get("edits") or []:
            lines.append("")
            lines.append(f"In {edit['path']}, this:")
            lines.append(f"> {_clean(edit['find'], 300)}")
            lines.append("")
            lines.append("would become:")
            lines.append(f"> {_clean(edit['replace'], 300)}")
        for ev in (staged.get("evidence") or [])[:2]:
            lines.append("")
            lines.append(f"From {ev['path']}: \"{_clean(ev['quote'], 220)}\"")
        lines.append("")
    lines += how_to_answer(n, code, allow_send)
    return "\n".join(lines)


def propose(now: datetime | None = None, force: bool = False,
            dry_run: bool = False, mail: bool = True, run=None) -> dict:
    """The day's look. Never raises.

    Returns `{"asked": n, "why": str, "dropped": [reasons], "tickets": [...]}`
    so a decision not to look is as visible as a look.
    """
    now = now or datetime.now()
    out = {"asked": 0, "why": "", "dropped": [], "tickets": []}
    try:
        state = _read_state()
        if not force:
            ok, why = _due(now, state)
            if not ok:
                out["why"] = why
                return out
        if not dry_run:
            # The stamp goes first. A look that hangs or dies still spends
            # the day's attempt, or every half hour from then on would look
            # again and mail again.
            state["day"] = now.date().isoformat()
            _write_state(state)

        started = run_ledger.now_stamp()
        cap = config_loader.advisor_daily_cap()
        sit = situation(now)
        prompt = build_prompt(sit, cap)
        if run is None:
            import claude_cli
            import deliverable
            model = config_loader.advisor_model() or deliverable.OPUS_MODEL

            def run(text):
                return claude_cli.run_claude_readonly(
                    text, _vault(), model=model, timeout=TIMEOUT_S)
        result = run(prompt)
        if getattr(result, "returncode", 1) != 0:
            tail = ((result.stderr or "") + (result.stdout or ""))[-400:].strip()
            out["why"] = "the look itself failed"
            if not dry_run:
                run_ledger.record_run(JOB_PROPOSE, started,
                                      run_ledger.now_stamp(), 1, tail)
            return out

        import deliverable
        try:
            payload = deliverable.extract_json(result.stdout)
        except deliverable.PlanInvalid as exc:
            out["why"] = f"its answer could not be read: {exc}"
            if not dry_run:
                run_ledger.record_run(JOB_PROPOSE, started,
                                      run_ledger.now_stamp(), 1, out["why"])
            return out

        vault = _vault()
        store = _store()
        seen = set()
        for raw in (payload.get("proposals") or [])[:cap * 3]:
            if len(out["tickets"]) >= cap:
                break
            ticket, reason = validate(raw, sit, vault)
            if ticket is None:
                out["dropped"].append(reason)
                continue
            if ticket["id"] in seen or store.get(ticket["id"]) is not None:
                out["dropped"].append("it was already suggested or declined")
                continue
            seen.add(ticket["id"])
            if not dry_run and store.add_suggestion(ticket) is None:
                out["dropped"].append("it was already suggested or declined")
                continue
            out["tickets"].append(ticket)

        out["asked"] = len(out["tickets"])
        if not out["asked"]:
            out["why"] = "nothing was worth suggesting"
        elif mail and not dry_run:
            out["why"] = _send_batch(out["tickets"], now, state)
        if not dry_run:
            run_ledger.record_run(
                JOB_PROPOSE, started, run_ledger.now_stamp(), 0,
                f"suggested {out['asked']}, dropped {len(out['dropped'])}")
        return out
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = f"could not look: {type(exc).__name__}"
        return out


def _send_batch(tickets: list, now: datetime, state: dict) -> str:
    """Mail the list and remember which number means which ticket."""
    # Registered before the send. An email that went out with a reference
    # nothing is listening for would turn the user's yes into silence.
    code = register_batch([t["id"] for t in tickets], now, state)
    n = len(tickets)
    subject = (f"{_count_word(n)} suggestion{'s' if n != 1 else ''} "
               "waiting on you" + (f" [{code}]" if code else ""))
    try:
        _mail(subject, email_body(tickets, code,
                                  config_loader.advisor_allow_send()))
    except Exception as exc:                                    # noqa: BLE001
        return (f"the suggestions were filed but the email could not be "
                f"sent: {type(exc).__name__}")
    return ""


# ── Reading the answer ───────────────────────────────────────────────────────

# One answer per line: a verb, then numbers or ALL. Nothing else on the line.
# Deliberately narrow. "Sure, go ahead with the first one" is a yes to a
# person and nothing to this, which costs the user a second, shorter reply.
# Reading it as a yes would cost them an action they did not ask for.
_COMMAND_RE = re.compile(
    r"^\s*(y|yes|n|no|send)\b[\s:,.]*"
    r"(all|\d{1,2}(?:\s*(?:,|and|&|\s)\s*\d{1,2})*)?\s*[.!]?\s*$",
    re.IGNORECASE)


def parse_commands(text: str) -> list:
    """The answers on the leading lines of a reply, as (verb, target).

    Reading stops at the first line that is not an answer. That is what keeps
    the quoted original underneath, which is full of examples like `Y 1 3`,
    from being read as the user's own words: the user's lines come first, and
    the first line of anything quoted is never an answer.
    """
    out = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        match = _COMMAND_RE.match(line)
        if not match:
            break
        verb = match.group(1).lower()
        verb = {"y": "yes", "n": "no"}.get(verb, verb)
        target = (match.group(2) or "").lower()
        if target == "all":
            out.append((verb, "all"))
        elif target:
            out.append((verb, [int(x) for x in re.findall(r"\d+", target)]))
        else:
            out.append((verb, None))
    return out


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup or "")
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))


def _gmail_text(payload: dict) -> str:
    """The plain text of a Gmail message, falling back to its HTML part."""
    plain, rich = [], []

    def walk(part):
        data = (part.get("body") or {}).get("data")
        mime = part.get("mimeType", "")
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
                text = raw.decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                text = ""
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                rich.append(_html_to_text(text))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return "\n".join(plain) if plain else "\n".join(rich)


def fetch_replies(account: dict, code: str) -> list:
    """Messages in this account's SENT folder carrying the reference.

    Matched anywhere in the message, not only the subject: a briefing that
    carries the list keeps its own subject, and the reference rides in the
    text that a reply quotes underneath.

    Sent, never the inbox. A message in the Sent folder was sent by whoever
    holds the account; a message in the inbox was sent by whoever wanted to.
    Returns `[{"id", "text"}]` and raises on a provider error, so the caller
    can tell "no reply yet" from "could not look".
    """
    out = []
    if account["provider"] == "google":
        from google_client import google_client
        gmail = google_client(account["label"]).gmail
        listing = gmail.users().messages().list(
            userId="me", q=f'in:sent "{code}"', maxResults=20).execute()
        for stub in listing.get("messages", []) or []:
            msg = gmail.users().messages().get(
                userId="me", id=stub["id"], format="full").execute()
            if "SENT" not in (msg.get("labelIds") or []):
                continue
            out.append({"id": str(stub["id"]),
                        "text": _gmail_text(msg.get("payload") or {})})
    elif account["provider"] == "microsoft":
        from microsoft_client import microsoft_client
        data = microsoft_client(account["label"]).folder_messages(
            "sentitems", search=f'"{code}"',
            select="id,subject,body,bodyPreview", top=20)
        for msg in data.get("value", []) or []:
            body = msg.get("body") or {}
            text = body.get("content") or msg.get("bodyPreview") or ""
            if (body.get("contentType") or "").lower() == "html":
                text = _html_to_text(text)
            out.append({"id": str(msg.get("id") or ""), "text": text})
    return out


def _targets(target, items: list) -> list:
    """Ticket ids an answer points at. A bare yes or no only means something
    when there is exactly one thing it could be about."""
    if target == "all":
        return list(items)
    if target is None:
        return list(items) if len(items) == 1 else []
    return [items[n - 1] for n in target if 1 <= n <= len(items)]


def apply_answer(verb: str, tid: str, via: str, by_reply: bool = False) -> dict:
    """Act on one answer about one ticket. Never raises.

    Returns `{"id", "title", "did", "ok"}` with `did` a sentence for the
    result email. A yes only marks the ticket approved; the work happens in
    `execute`, which is the one place that claims a ticket before touching
    anything.
    """
    store = _store()
    ticket = store.get(tid) or {}
    row = {"id": tid, "title": ticket.get("title", tid), "ok": False, "did": ""}
    try:
        state = ticket.get("state")
        if verb == "yes":
            if state != "staged":
                row["did"] = "was already answered"
                return row
            store.transition(tid, "approved", f"yes, {via}")
            row.update(ok=True, did="approved")
        elif verb == "no":
            if state not in ("staged", "failed"):
                row["did"] = "was already answered"
                return row
            store.transition(tid, "dismissed", f"no, {via}")
            row.update(ok=True, did="dropped, and it will not be suggested again")
        elif verb == "send":
            row.update(send_draft(tid, by_reply=by_reply))
    except Exception as exc:                                    # noqa: BLE001
        row["did"] = f"could not be done: {type(exc).__name__}"
    return row


def collect_answers(now: datetime | None = None, fetch=None) -> list:
    """Read every open batch's replies and act on them. Never raises."""
    now = now or datetime.now()
    fetch = fetch or fetch_replies
    done = []
    try:
        state = _read_state()
        batches = state.get("batches") or {}
        for code in list(batches):
            batch = batches[code]
            try:
                age = now - datetime.fromisoformat(batch.get("at", ""))
            except ValueError:
                age = timedelta(days=BATCH_DAYS + 1)
            if age > timedelta(days=BATCH_DAYS):
                del batches[code]
                continue
            account = None
            for candidate in config_loader.accounts():
                if candidate.get("label") == batch.get("label"):
                    account = candidate
            if account is None:
                continue
            try:
                messages = fetch(account, code)
            except Exception:                                   # noqa: BLE001
                continue        # could not look; the batch stays open
            for message in messages:
                if message["id"] in batch.get("seen", []):
                    continue
                # Claimed before acting: a message whose answer was acted on
                # and then crashed before being recorded would be acted on
                # again, and for SEND that is a second email.
                batch.setdefault("seen", []).append(message["id"])
                _write_state(state)
                for verb, target in parse_commands(message["text"]):
                    for tid in _targets(target, batch.get("items", [])):
                        done.append(apply_answer(verb, tid, "by email reply",
                                                 by_reply=True))
        state["batches"] = batches
        _write_state(state)
    except Exception:                                           # noqa: BLE001
        pass
    return done


# ── Doing the work ───────────────────────────────────────────────────────────

_REFUSALS = ("i can't", "i cannot", "i'm unable", "i am unable", "unable to",
             "as an ai", "i don't have", "i do not have", "no information")

_REPLY_PROMPT = """You are writing an email for {name} to send. Write only the
body of the message: no subject line, no notes to the author, no placeholder
in brackets. Every word you write goes into a draft exactly as written.

To: {to_name} <{to}>
Subject: {subject}

What this message should accomplish:
{brief}

What is on the record, each line copied from {name}'s own notes:
{evidence}
{pack}{voice}{comments}
Rules:
- Use only what is given above. If a date, a figure or a name is not there,
  write the message without it.
- Short. A few sentences, the way a busy person writes to someone they know.
- No em-dashes or en-dashes. Use a comma, a colon or two sentences.
- Do not restate a time or a deadline from the record as if it were still
  agreed unless the record says so plainly.
- Sign off with the first name only: {name}.
"""


def _write_reply(ticket: dict, run=None) -> str:
    """The body of the draft, or raise with the reason."""
    staged = ticket.get("staged") or {}
    name = config_loader.user_first_name() or "the user"
    evidence = "\n".join(f"- {e['quote']}" for e in staged.get("evidence") or [])

    pack_text = ""
    try:
        import context_pack
        pack = context_pack.pack_for(staged.get("to_name") or "",
                                     staged.get("to") or "")
        if not context_pack.is_empty(pack):
            rows = [f"- Still open, promised by {name}: {_clean(c.get('item'), 300)}"
                    for c in (pack.get("open_commitments") or [])[:6]]
            rows += [f"- From the deal record: {_clean(d.get('text'), 400)}"
                     for d in (pack.get("deal_context") or [])[:3]]
            if rows:
                pack_text = "\nAlso on the record:\n" + "\n".join(rows) + "\n"
    except Exception:                                           # noqa: BLE001
        pack_text = ""

    voice = ""
    try:
        guide = Path(config_loader.voice_guide_path())
        if guide.is_file():
            voice = ("\nHow " + name + " writes, from their own voice guide:\n"
                     + guide.read_text(encoding="utf-8")[:2500] + "\n")
    except Exception:                                           # noqa: BLE001
        voice = ""

    notes = [_clean(c.get("text")) for c in ticket.get("comments") or []]
    comments = ""
    if any(notes):
        comments = ("\nThe user asked for these changes. They take priority:\n"
                    + "\n".join(f"- {n}" for n in notes if n) + "\n")

    prompt = _REPLY_PROMPT.format(
        name=name, to=staged.get("to", ""), to_name=staged.get("to_name", ""),
        subject=staged.get("subject", ""), brief=staged.get("brief", ""),
        evidence=evidence, pack=pack_text, voice=voice, comments=comments)
    if run is None:
        import claude_cli
        import deliverable

        def run(text):
            return claude_cli.run_claude(text, model=deliverable.OPUS_MODEL,
                                         timeout=180)
    result = run(prompt)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("the writer could not be reached")
    body = _strip_dashes(result.stdout or "").strip()
    low = body.lower()
    # A refusal is not empty, so "did it return something" proves nothing.
    if (len(body) < MIN_BODY_CHARS or any(m in low[:200] for m in _REFUSALS)
            or re.match(r"^(pass|fail)\s*:", low)
            or re.search(r"\[[A-Za-z ]{3,30}\]", body)):
        raise RuntimeError("the writer did not produce a usable message")
    return body


def _do_draft(ticket: dict, run=None) -> dict:
    import draft_email
    staged = ticket["staged"]
    account = None
    for candidate in config_loader.accounts():
        if candidate["label"] == staged.get("account"):
            account = candidate
    if account is None:
        raise RuntimeError("the account it was to be drafted from is gone")
    body = _write_reply(ticket, run)
    result = draft_email.draft_once(account["provider"], account["label"],
                                    staged["to"], staged["subject"], body,
                                    counterparty=staged["to"])
    if result.get("skipped") == "terminal_deal":
        raise RuntimeError("the deal behind this is closed, so nothing was drafted")
    if result.get("no_token"):
        raise RuntimeError(f"{account['label']} needs signing in again before "
                           "a draft can be written")
    if not result.get("ok"):
        raise RuntimeError("the draft could not be written")
    where = "was already in" if result.get("existing") else "is in"
    return {"kind": "draft", "draft_id": result.get("id", ""),
            "draft_key": result.get("key", ""), "label": account["label"],
            "web_link": result.get("web_link", ""),
            "detail": f"A draft to {staged.get('to_name') or staged['to']} "
                      f"{where} your {account['label']} Drafts folder."}


def _do_file(ticket: dict, run=None) -> dict:
    import deliverable
    store = _store()
    plan = deliverable.stage_plan(ticket, ratings=store.recent_ratings(),
                                  **({"run": run} if run else {}))
    out = deliverable.output_path(ticket)
    out.parent.mkdir(parents=True, exist_ok=True)
    deliverable.build(ticket.get("type"), plan, out)
    detail = deliverable.verify(ticket.get("type"), plan, out)
    return {"kind": "file", "path": str(out), "detail": detail}


def backups_dir() -> Path:
    return config_loader.logs_dir() / "advisor" / "backups"


def _do_vault(ticket: dict) -> dict:
    """Apply the edits the user said yes to, exactly, or change nothing.

    Checked again at this moment rather than trusted from the morning: the
    page may have been edited since, and a find that now matches twice or not
    at all means the yes was given to a different page than the one on disk.
    Every page is copied aside first, and every write is held in memory until
    all of them have been checked, so a failure leaves the vault untouched.
    """
    vault = _vault().resolve()
    edits, reason = _checked_edits(ticket["staged"].get("edits"), vault)
    if not edits:
        raise RuntimeError(f"the page changed since this was suggested "
                           f"({reason}), so nothing was edited")
    pending = {}
    for edit in edits:
        path = vault / edit["path"]
        text = pending.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
        if text.count(edit["find"]) != 1:
            raise RuntimeError("two of its changes overlapped, so nothing "
                               "was edited")
        pending[path] = text.replace(edit["find"], edit["replace"])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    keep = backups_dir() / f"{stamp}-{ticket['id']}"
    for path in pending:
        target = keep / path.relative_to(vault)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for path, text in pending.items():
        path.write_text(text, encoding="utf-8")
    pages = sorted(p.relative_to(vault).as_posix() for p in pending)
    return {"kind": "vault", "pages": pages, "backup": str(keep),
            "detail": f"Corrected {', '.join(pages)}. The earlier version is "
                      "kept in the logs folder."}


def execute(tid: str, run=None) -> dict:
    """Do the work one approved suggestion describes. Never raises.

    Returns `{"id", "title", "ok", "did"}`. The move to `running` is the
    claim: the ledger refuses it for a ticket that is not `approved`, so a
    Workbench press and a watcher tick that both reach the same ticket
    cannot both do the work.
    """
    store = _store()
    ticket = store.get(tid) or {}
    row = {"id": tid, "title": ticket.get("title", tid), "ok": False, "did": ""}
    if ticket.get("source") != SOURCE:
        row["did"] = "is not one of the advisor's suggestions"
        return row
    started = run_ledger.now_stamp()
    try:
        ticket = store.transition(tid, "running", "doing it")
    except Exception:                                           # noqa: BLE001
        row["did"] = "was not waiting to be done"
        return row
    try:
        kind = KIND_FOR.get(ticket.get("type"), "")
        if kind == "draft":
            delivery = _do_draft(ticket, run)
        elif kind == "file":
            delivery = _do_file(ticket, run)
        elif kind == "vault":
            delivery = _do_vault(ticket)
        else:
            raise RuntimeError("an unknown kind of work")
        delivery["at"] = _now()
        store.transition(tid, "delivered", delivery["detail"], delivery=delivery)
        row.update(ok=True, did=delivery["detail"])
        run_ledger.record_run(JOB_EXECUTE, started, run_ledger.now_stamp(), 0,
                              f"{tid}: {kind}")
    except Exception as exc:                                    # noqa: BLE001
        # The exception's own text only when this module wrote it. A provider
        # error can quote an address or a token, and this sentence is mailed.
        reason = (str(exc) if isinstance(exc, RuntimeError)
                  else f"it stopped on a {type(exc).__name__}")
        try:
            store.transition(tid, "failed", reason)
        except Exception:                                       # noqa: BLE001
            pass
        row["did"] = f"Could not be done: {reason}."
        run_ledger.record_run(JOB_EXECUTE, started, run_ledger.now_stamp(), 1,
                              f"{tid}: {type(exc).__name__}")
    return row


def run_approved(limit: int = MAX_EXECUTE_PER_TICK) -> list:
    """Do every approved suggestion, oldest first, up to `limit`."""
    done = []
    try:
        waiting = [t for t in _store().by_source(SOURCE)
                   if t.get("state") == "approved"]
        waiting.sort(key=lambda t: t.get("created_at", ""))
        for ticket in waiting[:limit]:
            done.append(execute(ticket["id"]))
    except Exception:                                           # noqa: BLE001
        pass
    return done


def send_draft(tid: str, by_reply: bool = False) -> dict:
    """Send the draft a yes produced. `{"ok", "did"}`; never raises.

    Three things must all be true, and each has its own sentence when it is
    not. An answer that arrived by email also needs `advisor.allow_send`,
    because nobody is present to see what is about to go; a press in the
    Workbench or a line typed in a terminal is the user, there, saying so.
    Sent by draft id, so what goes is what is in the Drafts folder now,
    including anything the user changed. Never retried.
    """
    out = {"ok": False, "did": ""}
    try:
        import draft_email
        import send_email
        store = _store()
        ticket = store.get(tid) or {}
        delivery = ticket.get("delivery") or {}
        if ticket.get("source") != SOURCE or delivery.get("kind") != "draft":
            out["did"] = "has no draft to send"
            return out
        if by_reply and not config_loader.advisor_allow_send():
            out["did"] = ("was not sent: sending from a reply is switched "
                          "off. The draft is in your Drafts folder.")
            return out
        if delivery.get("sent_at"):
            out["did"] = "was already sent"
            return out
        account = send_email.account_for_label(delivery.get("label") or "")
        if not account or not delivery.get("draft_id"):
            out["did"] = "was not sent: the draft could not be found"
            return out
        # Stamped before the send. A send whose response is lost may have
        # gone, and a ticket that still looks unsent would send it twice.
        stamp = _now()
        store.attach(tid, delivery={**delivery, "sent_at": stamp})
        send_email.send_draft(account, delivery["draft_id"])
        try:
            ledger = draft_email.load_ledger() or {}
            key = delivery.get("draft_key")
            if key in ledger:
                ledger[key]["sent_at"] = stamp
                draft_email.save_ledger(ledger)
        except Exception:                                       # noqa: BLE001
            pass
        out.update(ok=True, did="was sent as it stood in your Drafts folder")
    except Exception as exc:                                    # noqa: BLE001
        out["did"] = ("may not have been sent: check your Sent folder "
                      f"before trying again ({type(exc).__name__})")
    return out


# ── What a briefing carries ──────────────────────────────────────────────────

def briefing(now: datetime | None = None) -> dict:
    """The suggestions section for Morning Coffee and Afternoon Tea.

    Returns `{"md": str, "waiting": [{"n", "id", "title", "kind"}]}`. `md` is
    "" when nothing is waiting and nothing was done today, which is most
    days, and a section that appears every day teaches the reader to skip it.

    Written by code, not by the model rendering the briefing, because it
    carries numbers that mean something: `Y 2` in a reply is acted on, so the
    2 printed here has to be the 2 that was registered. Never raises.
    """
    out = {"md": "", "waiting": []}
    try:
        now = now or datetime.now()
        mine = _store().by_source(SOURCE)
        rows = sorted((t for t in mine if t.get("state") == "staged"),
                      key=lambda t: t.get("created_at", ""))
        today = now.date().isoformat()
        done = [t for t in mine if t.get("state") in ("delivered", "rated")
                and str((t.get("delivery") or {}).get("at", ""))[:10] == today]
        if not rows and not done:
            return out

        lines = ["## Suggested", ""]
        if rows:
            n = len(rows)
            lines += [f"{_count_word(n)} thing{'s' if n != 1 else ''} I could "
                      "take off your plate. Nothing below has been done yet.", ""]
            for i, ticket in enumerate(rows, 1):
                staged = ticket.get("staged") or {}
                lines.append(f"{i}. **{ticket['title']}**")
                lines.append(f"   Why: {staged.get('why', '')}")
                lines.append(f"   What I would do: {staged.get('plan', '')}")
                out["waiting"].append({"n": i, "id": ticket["id"],
                                       "title": ticket["title"],
                                       "kind": KIND_FOR.get(ticket.get("type"))})
            lines.append("")
            # Replies are only read while the daily look is on, because the
            # same tick does both. Promising a reply will be read when
            # nothing reads it is worse than not offering one.
            code = ""
            if config_loader.advisor_enabled():
                code = register_batch([t["id"] for t in rows], now)
            lines += how_to_answer(n, code, config_loader.advisor_allow_send())
        if done:
            if rows:
                lines.append("")
            lines.append("Done today on your yes:")
            lines.append("")
            for ticket in done:
                detail = (ticket.get("delivery") or {}).get("detail", "")
                lines.append(f"- **{ticket['title']}** {detail}")
        out["md"] = "\n".join(lines)
    except Exception:                                           # noqa: BLE001
        return {"md": "", "waiting": []}
    return out


# ── The tick the watcher runs ────────────────────────────────────────────────

def _restage() -> None:
    """A comment on a suggestion is a note for the work, not a new plan.

    The ledger drops a commented ticket back to `staging`, which for a deck
    means "being planned again". Nothing re-plans a suggestion, so it would
    sit there for ever. The comment is kept and read when the work is done.
    """
    try:
        store = _store()
        for ticket in store.by_source(SOURCE):
            if ticket.get("state") == "staging":
                store.transition(ticket["id"], "staged", "comment noted")
    except Exception:                                           # noqa: BLE001
        pass


def results_body(answers: list, done: list) -> str:
    lines = []
    for row in done:
        mark = "Done" if row["ok"] else "Not done"
        lines.append(f"**{row['title']}**")
        lines.append("")
        lines.append(row["did"] if not row["ok"] else f"{mark}. {row['did']}")
        lines.append("")
    quiet = [r for r in answers if not (r["ok"] and r["did"] == "approved")]
    for row in quiet:
        lines.append(f"**{row['title']}** {row['did']}.")
        lines.append("")
    return "\n".join(lines).strip()


def tick(now: datetime | None = None, fetch=None) -> dict:
    """Answers, then work, then the day's look. Never raises."""
    now = now or datetime.now()
    out = {"answers": [], "done": [], "asked": 0, "why": ""}
    try:
        if not config_loader.advisor_enabled():
            out["why"] = "the advisor is switched off"
            return out
        _restage()
        out["answers"] = collect_answers(now, fetch)
        out["done"] = run_approved()
        if out["done"] or any(not (r["ok"] and r["did"] == "approved")
                              for r in out["answers"]):
            body = results_body(out["answers"], out["done"])
            if body:
                try:
                    _mail("What happened to your suggestions", body)
                except Exception:                               # noqa: BLE001
                    pass
        look = propose(now)
        out["asked"], out["why"] = look["asked"], look["why"]
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = f"could not run: {type(exc).__name__}"
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def waiting() -> list:
    """Suggestions still waiting on an answer, oldest first, for a terminal."""
    rows = [t for t in _store().by_source(SOURCE) if t.get("state") == "staged"]
    rows.sort(key=lambda t: t.get("created_at", ""))
    return [{"id": t["id"], "title": t["title"], "kind": KIND_FOR.get(t["type"]),
             **{k: (t.get("staged") or {}).get(k)
                for k in ("why", "plan", "evidence", "edits", "to", "subject")}}
            for t in rows]


def main(argv: list | None = None) -> int:
    config_loader.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Suggest work, and do it on a yes.")
    parser.add_argument("--tick", action="store_true",
                        help="read answers, do approved work, make the day's look")
    parser.add_argument("--propose", action="store_true", help="look now")
    parser.add_argument("--force", action="store_true",
                        help="look even if today's look already happened")
    parser.add_argument("--no-mail", action="store_true",
                        help="file the suggestions without emailing them")
    parser.add_argument("--dry-run", action="store_true",
                        help="look and check, but file and mail nothing")
    parser.add_argument("--list", action="store_true",
                        help="print the suggestions waiting on an answer")
    parser.add_argument("--approve", metavar="ID", help="yes, and do it now")
    parser.add_argument("--dismiss", metavar="ID", help="no")
    parser.add_argument("--send", metavar="ID",
                        help="send the draft a yes produced")
    args = parser.parse_args(argv)

    if args.list:
        out = {"waiting": waiting()}
    elif args.approve:
        answer = apply_answer("yes", args.approve, "in a terminal")
        out = execute(args.approve) if answer["ok"] else answer
    elif args.dismiss:
        out = apply_answer("no", args.dismiss, "in a terminal")
    elif args.send:
        out = {"id": args.send, **send_draft(args.send)}
    elif args.propose:
        out = propose(force=args.force, dry_run=args.dry_run,
                      mail=not args.no_mail)
    elif args.tick:
        out = tick()
    else:
        parser.print_help()
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
