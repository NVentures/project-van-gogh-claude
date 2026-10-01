#!/usr/bin/env python3
"""Create an email draft. Importable core, shared by the CLI and the Workbench.

This module can create a draft and nothing else. There is no send path in it,
by construction, and `tests/test_draft_email.py` scans its AST to keep it that
way: no `sendMail`, no `messages().send`, no `send_mail`. The one place in this
repo that can put mail on the wire is `digest_send.send_email`, and the
Workbench reaches it only from an explicit human confirmation.

Provider asymmetry worth knowing before you read the return values: Microsoft
Graph gives a draft a real server-side `webLink` that opens that draft. Gmail
does not. `drafts().create` returns ids only, so `draft_gmail` returns the
drafts-folder URL plus the draft id, which is the closest honest equivalent.
Callers should present it as "your drafts folder", not "this draft".
"""

from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^#{1,6}\s+")
_GMAIL_DRAFTS_URL = "https://mail.google.com/mail/u/0/#drafts"


class DraftUnavailable(RuntimeError):
    """No usable credential for this account on this machine.

    Distinct from a draft that was attempted and failed: this one means the
    caller should offer ready-to-paste text instead of retrying.
    """


def plain_body_from_markdown_file(path: Path, strip_frontmatter: bool = False) -> str:
    """Read a body file, optionally flattening vault markdown to plain text.

    Behavior is pinned by `tests/test_outlook_draft.py`: split on the first two
    `---` runs, then drop leading heading markers line by line. Deliberately not
    a markdown parser; it exists to make a vault note readable as an email.
    """
    raw = path.read_text(encoding="utf-8")
    if strip_frontmatter:
        raw = raw.split("---", 2)[-1]
        raw = "\n".join(_HEADING_RE.sub("", ln) for ln in raw.splitlines())
    return raw.strip() + "\n"


def _clean_subject(subject: str) -> str:
    """Reject a subject carrying CR or LF.

    A newline in a header field is how an attacker appends headers of their own
    (a Bcc, a different From). Rejecting beats stripping: a mangled subject the
    user did not write is its own problem.
    """
    subject = subject or ""
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject may not contain a line break")
    return subject.strip()


def _clean_to(to: str) -> str:
    from digest_send import clean_address
    return clean_address(to, "recipient")


def draft_outlook(label: str, to: str, subject: str, body: str) -> dict:
    """Create a draft in an Outlook account's Drafts folder. Never sends."""
    to = _clean_to(to)
    subject = _clean_subject(subject)

    from microsoft_client import microsoft_client

    try:
        client = microsoft_client(label)
    except RuntimeError as exc:
        raise DraftUnavailable(str(exc)) from exc

    draft = client.create_draft({
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
    })
    return {"ok": True, "web_link": draft.get("webLink") or "", "id": draft.get("id") or ""}


def draft_gmail(label: str, to: str, subject: str, body: str) -> dict:
    """Create a draft in a Gmail account's Drafts folder. Never sends.

    `web_link` is the drafts folder, not this draft: Gmail exposes no
    server-side link to an individual draft. The id is returned so a caller can
    say which one it made.
    """
    to = _clean_to(to)
    subject = _clean_subject(subject)

    from digest_send import build_gmail_raw
    from google_client import google_client

    try:
        client = google_client(label)
    except RuntimeError as exc:
        raise DraftUnavailable(str(exc)) from exc

    sender = getattr(client, "email", "") or ""
    raw = build_gmail_raw(sender, to, subject, body)
    draft = client.gmail.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return {"ok": True, "web_link": _GMAIL_DRAFTS_URL, "id": draft.get("id") or ""}


def draft(provider: str, label: str, to: str, subject: str, body: str) -> dict:
    """Draft through whichever provider an account uses."""
    if provider == "microsoft":
        return draft_outlook(label, to, subject, body)
    if provider == "google":
        return draft_gmail(label, to, subject, body)
    raise ValueError(f"unknown provider: {provider!r}")


# ── The draft ledger ─────────────────────────────────────────────────────────
#
# The front page drafts a reply for every item on it, every morning. Without a
# ledger that is a fresh draft per item per run: yesterday's edits lost, the
# Drafts folder filling with near-duplicates. The ledger makes a rerun a no-op.

import json
import re

TERMINAL_STAGES = {"dead", "lost", "closed", "won", "terminated", "passed"}


def _ledger_path():
    from config_loader import logs_dir
    return logs_dir() / "draft_ledger.json"


def load_ledger() -> dict:
    """Read the ledger. A missing or unreadable one is an empty one: losing a
    ledger costs a duplicate draft, and refusing to run costs the briefing."""
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_ledger(ledger: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)


# ── The drafts block ─────────────────────────────────────────────────────────
# Rendered by code, not by the model. A reply that is written and waiting is a
# fact the ledger already holds: who it is to, what it is about, when it was
# written, and whether it has been sent. Asking the model to describe that was
# asking it to restate a record it could paraphrase, reorder or quietly drop,
# and the reader could not tell which had happened. So the block is built here
# and pasted verbatim, the same rule the front page already follows.

DRAFTS_HEADING = "## Drafts"


def _draft_sort_key(entry: dict) -> tuple:
    """Unsent first, then oldest first. Stable across reruns."""
    return (1 if entry.get("sent_at") else 0,
            str(entry.get("created") or ""),
            str(entry.get("subject") or ""))


def render_drafts_md(drafts: list, mode: str = "md") -> str:
    """The Drafts block, or "" when nothing is drafted.

    `mode` is "md" for the written file, where each draft is a hyperlink to
    the reply in its own Drafts folder, and "terminal" for the chat render,
    where a URL on its own line is noise the reader cannot click.

    Empty in, empty out: a briefing with no drafts renders no heading, rather
    than a heading over nothing.
    """
    if mode not in ("md", "terminal"):
        raise ValueError(f"unknown mode {mode!r}")
    rows = [d for d in (drafts or []) if isinstance(d, dict)]
    if not rows:
        return ""

    out = [DRAFTS_HEADING, ""]
    waiting = sum(1 for d in rows if not d.get("sent_at"))
    if waiting == len(rows):
        lead = ("One reply is written and waiting for your nod."
                if waiting == 1 else
                f"{waiting} replies are written and waiting for your nod.")
    elif waiting:
        lead = (f"{waiting} of {len(rows)} replies are still waiting for "
                "your nod.")
    else:
        lead = ("The reply written here has been sent."
                if len(rows) == 1 else
                f"All {len(rows)} replies written here have been sent.")
    out += [lead, ""]

    for entry in sorted(rows, key=_draft_sort_key):
        who = (entry.get("counterparty_name") or entry.get("counterparty")
               or "").strip()
        subject = str(entry.get("subject") or "").strip()
        link = str(entry.get("web_link") or "").strip()
        # The subject carries the link, so the line reads as a sentence and
        # not as a URL with a label. A draft with no web_link (no credential
        # for that account) still gets its line, just without the link: the
        # reader needs to know the reply exists either way.
        label = subject or "the reply"
        # A sent reply keeps its line and loses its link. The link points at a
        # Drafts folder, and once the message is sent it is not there any more,
        # so the link would lead somewhere the reply has left. The line still
        # says it was sent, which is the fact the reader needs.
        linkable = bool(link) and not entry.get("sent_at")
        subject_md = f"[{label}]({link})" if linkable and mode == "md" else label
        head = f"**{who}**, {subject_md}" if who else subject_md
        tail = (f"sent {entry['sent_at']}" if entry.get("sent_at")
                else "waiting for your nod")
        account = str(entry.get("label") or "").strip()
        parts = [p for p in (tail, account) if p]
        out.append(f"- {head}: {', '.join(parts)}.")
    out.append("")
    return "\n".join(out)


_SUBJECT_PREFIX_RE = re.compile(r"^(re|fw|fwd|aw|antwort)\s*:\s*", re.IGNORECASE)


def draft_key(account_label: str, counterparty: str, subject: str) -> str:
    """(account, counterparty, normalized subject), the identity of one reply.

    Normalized so today's "Re: Re: Kestrel term sheet" is yesterday's "Kestrel
    term sheet": a reply thread renames itself every round trip, and keying on
    the raw subject would draft the same message again every morning.
    """
    subject = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", subject).strip()
        if stripped == subject.strip():
            break
        subject = stripped
    subject = re.sub(r"\s+", " ", subject).strip().lower()
    return "|".join([
        (account_label or "").strip().lower(),
        (counterparty or "").strip().lower(),
        subject,
    ])


def counterparty_is_terminal(counterparty: str) -> bool:
    """True when this counterparty's hotcache deal is dead or closed.

    Drafting a reply into a deal the user already killed is the one draft that
    is worse than no draft, so this leans toward saying yes only on an explicit
    terminal stage and never guesses from silence.
    """
    if not (counterparty or "").strip():
        return False
    try:
        from collectors import hotcache_read
        from config_loader import hotcache_path
        from hotcache_sync import match_deal
        deals = hotcache_read(str(hotcache_path()))
    except Exception:
        return False
    headings = [d.get("heading", "") for d in deals]
    try:
        hit = match_deal(counterparty, headings)
    except Exception:
        return False
    if not hit:
        return False
    for deal in deals:
        if deal.get("heading") == hit:
            stage = str((deal.get("fields") or {}).get("stage", "")).strip().lower()
            return stage in TERMINAL_STAGES
    return False


def draft_once(provider: str, label: str, to: str, subject: str, body: str,
               counterparty: str = "") -> dict:
    """Draft this reply unless the ledger already holds it, or the deal is dead.

    Return shapes, all JSON-serializable and all meaningful to a caller:
      {"ok": True, "existing": True, ...}  already drafted, no API call made
      {"ok": True, "web_link": ..., "id": ...}  drafted just now
      {"no_token": True, "reason": ...}    no credential for this account
      {"skipped": "terminal_deal"}         the deal behind it is dead
    """
    counterparty = counterparty or to
    if counterparty_is_terminal(counterparty):
        return {"skipped": "terminal_deal", "counterparty": counterparty}
    key = draft_key(label, counterparty, subject)
    ledger = load_ledger()
    if key in ledger:
        return {"ok": True, "existing": True, "key": key, **ledger[key]}
    try:
        result = draft(provider, label, to, subject, body)
    except DraftUnavailable as exc:
        return {"no_token": True, "reason": str(exc), "key": key}
    from datetime import datetime
    ledger[key] = {
        "web_link": result.get("web_link", ""),
        "id": result.get("id", ""),
        "provider": provider,
        "label": label,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # The subject and counterparty are what the briefing page matches on to
        # place a stamp beside the right row. The BODY is deliberately still not
        # stored: sending goes by draft id so the user's own edits are what go
        # out, and a copy here would only ever be the stale version.
        "subject": subject,
        "counterparty": counterparty,
        "sent_at": "",
    }
    save_ledger(ledger)
    return {"ok": True, "key": key, **ledger[key]}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Create one email draft. Never sends.")
    parser.add_argument("--provider", required=True, choices=["google", "microsoft"])
    parser.add_argument("--label", required=True, help="account label from config")
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True,
                        help="path to a UTF-8 file holding the draft body")
    parser.add_argument("--counterparty", default="",
                        help="name used for the ledger key and the dead-deal check")
    args = parser.parse_args(argv)

    body = Path(args.body_file).read_text(encoding="utf-8")
    try:
        result = draft_once(args.provider, args.label, args.to, args.subject,
                            body, counterparty=args.counterparty)
    except Exception as exc:                                    # noqa: BLE001
        result = {"error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") or result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
