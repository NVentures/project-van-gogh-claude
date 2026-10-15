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

A draft is a REPLY whenever the mailbox holds the message it answers. Both
providers look for the newest message with this person under this subject and,
when there is one, write the draft into that thread with the original quoted
beneath it. Only when there is none is the draft a new message, and the result
says which happened (`threaded`), so a caller never has to guess.

Every draft also carries the account's email signature, under the text and
above the quoted original, because neither provider adds one to a draft made
through its API. Gmail's is read from the mailbox's own settings. Outlook's
cannot be read by any outside program, so it comes from the account's
`signature` in config.json, which also wins over Gmail's when it is set.
`signed` in the result says whether one went in.
"""

from __future__ import annotations

import html
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


# ── Finding the message a draft answers ──────────────────────────────────────
#
# Every caller hands this module an address and a subject and nothing else, and
# most of them are a model following a skill. So the thread is found here, in
# code, from those two things, and no caller has to remember to pass an id.
# The match is strict on purpose: the same person AND the same subject once the
# Re:/Fw: prefixes are off. A reply filed into the wrong conversation is worse
# than a new message, so anything short of that match stays a new message.

# The address goes inside a provider search string, so anything that is not
# plainly an address is refused rather than escaped (as attendee_mail does).
_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_SUBJECT_PREFIX_RE = re.compile(r"^(re|fw|fwd|aw|antwort)\s*:\s*", re.IGNORECASE)
_LOOKUP_CAP = 25


def subject_core(subject: str) -> str:
    """The subject with its reply prefixes off, lowercased, one space per gap.

    Today's "Re: Re: Kestrel term sheet" is yesterday's "Kestrel term sheet".
    """
    subject = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", subject).strip()
        if stripped == subject.strip():
            break
        subject = stripped
    return re.sub(r"\s+", " ", subject).strip().lower()


def _reply_subject(original: str) -> str:
    original = " ".join((original or "").split())
    return original if original.lower().startswith("re:") else f"Re: {original}"


def _lookup(what: str, notes: list, find, *args):
    """What `find` returns, or None with a line added to `notes` when it fails.

    Nothing looked up here is worth losing the draft over: a failed thread
    lookup is a new message, a failed signature read is an unsigned one, and
    the note says which. It carries the exception's type and never its text:
    the text can quote the address it failed on, and the note is printed and
    stored.
    """
    try:
        return find(*args)
    except Exception as exc:                                    # noqa: BLE001
        notes.append(f"{what} failed ({type(exc).__name__})")
        return None


# ── The signature ────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[A-Za-z/][^>]*>")
# What marks a signature as HTML. A closing tag, a line break or an image, not
# any angle bracket: a typed one may say "Kestrel <Energy>".
_IS_HTML_RE = re.compile(r"</[A-Za-z]+>|<br\b[^>]*>|<img\b", re.IGNORECASE)
_BREAK_RE = re.compile(r"<br\b[^>]*>|</div>|</p>|</tr>|</li>", re.IGNORECASE)
_NAME_LINE_RE = re.compile(r"^[^\W\d_]+(?: [^\W\d_]+)?$")


def _config_signature(label: str) -> str:
    """The `signature` set on this account in config.json, or ""."""
    try:
        from config_loader import accounts
        for account in accounts():
            if str(account.get("label", "")).strip().lower() == label.strip().lower():
                return str(account.get("signature") or "").strip()
    except Exception:                                           # noqa: BLE001
        pass
    return ""


def _text_html(text: str) -> str:
    """Plain text as HTML: escaped, one <br> per line break."""
    return html.escape(text.strip()).replace("\n", "<br>")


def _signature_html(signature: str) -> str:
    """A signature may be typed as plain lines or pasted as HTML."""
    return signature if _IS_HTML_RE.search(signature) else _text_html(signature)


def _signature_text(signature: str) -> str:
    """The signature for the plain-text half of a message."""
    if not _IS_HTML_RE.search(signature):
        return signature.strip()
    text = html.unescape(_TAG_RE.sub("", _BREAK_RE.sub("\n", signature)))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _closing(line: str) -> bool:
    """A line that is only a closing: "Best," or "Thanks so much,"."""
    return line.endswith(",") and len(line.split()) <= 3


def _without_signoff(body: str, signature: str) -> str:
    """The body with the name it ends on removed, when the signature opens
    with that name.

    The writer ends a reply on the user's first name. A signature that starts
    with the same name would put it there twice, so the body's copy goes; and
    when the signature brings its own closing ("Sincerely,"), the body's
    closing goes with it. A body that ends any other way is left alone.
    Measured on a live draft: a looser test, any one shared word, took a
    closing sentence ("Line two.") for the name.
    """
    lines = body.rstrip().splitlines()
    opening = _signature_text(signature).splitlines()[:2]
    if len(lines) < 2 or not _NAME_LINE_RE.match(lines[-1].strip()):
        return body
    # Every word of it, so a short last sentence that happens to share one
    # word with the signature is not mistaken for the name.
    known = set(re.findall(r"\w+", " ".join(opening).lower()))
    if not set(lines[-1].lower().split()) <= known:
        return body
    lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _closing(opening[0].strip()) and _closing(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).rstrip() or body


def _gmail_signature(gmail, sender: str) -> str:
    """The signature saved in Gmail for the address the draft is from."""
    rows = gmail.users().settings().sendAs().list(
        userId="me").execute().get("sendAs") or []
    for row in rows:
        if (row.get("sendAsEmail") or "").lower() == sender.lower():
            return row.get("signature") or ""
    for row in rows:
        if row.get("isPrimary"):
            return row.get("signature") or ""
    return ""


def _outlook_original(client, to: str, subject: str) -> dict | None:
    """The newest message with this person under this subject, or None."""
    core = subject_core(subject)
    if not core or not _ADDRESS_RE.match(to):
        return None
    data = client.messages(
        search=f'"participants:{to}"', top=_LOOKUP_CAP,
        select="id,subject,from,toRecipients,ccRecipients,receivedDateTime,isDraft")
    matches = []
    for msg in data.get("value", []) or []:
        if msg.get("isDraft") or subject_core(msg.get("subject", "")) != core:
            continue
        # The search is a relevance match over the mailbox, so it is checked.
        people = [msg.get("from") or {}]
        people += (msg.get("toRecipients") or []) + (msg.get("ccRecipients") or [])
        addresses = {((p.get("emailAddress") or {}).get("address") or "").lower()
                     for p in people}
        if to.lower() in addresses:
            matches.append(msg)
    return max(matches, key=lambda m: m.get("receivedDateTime") or "",
               default=None)


def _outlook_reply(client, original: dict, to: str, body: str,
                   signature: str = "") -> dict:
    """A reply draft in the original's conversation, our text above the quote."""
    reply = client.post(f"/me/messages/{original['id']}/createReply", None)
    quoted = reply.get("body") or {}
    content = quoted.get("content") or ""
    if (quoted.get("contentType") or "").lower() == "html":
        ours = _outlook_html(body, signature) + "<br>"
        opened = re.search(r"<body[^>]*>", content, re.IGNORECASE)
        cut = opened.end() if opened else 0
        new_body = {"contentType": "HTML",
                    "content": content[:cut] + ours + content[cut:]}
    else:
        ours = body.strip()
        if signature:
            ours = (_without_signoff(ours, signature) + "\n\n"
                    + _signature_text(signature))
        new_body = {"contentType": "Text", "content": ours + "\n\n" + content}
    try:
        # The address is set outright: answering the user's own last message
        # would otherwise address the reply to the user.
        patched = client.patch(f"/me/messages/{reply['id']}", {
            "body": new_body,
            "toRecipients": [{"emailAddress": {"address": to}}],
        })
    except Exception:
        # An empty reply left in Drafts looks like work that was done.
        client.delete(f"/me/messages/{reply['id']}")
        raise
    return {**reply, **(patched or {})}


def _outlook_html(body: str, signature: str) -> str:
    out = f"<div>{_text_html(_without_signoff(body, signature))}</div>"
    if signature:
        out += f"<br><div>{_signature_html(signature)}</div>"
    return out


def draft_outlook(label: str, to: str, subject: str, body: str) -> dict:
    """Create a draft in an Outlook account's Drafts folder. Never sends.

    A reply inside the conversation when the mailbox holds the original, a new
    message when it does not; `threaded` says which.
    """
    to = _clean_to(to)
    subject = _clean_subject(subject)

    from microsoft_client import microsoft_client

    try:
        client = microsoft_client(label)
    except RuntimeError as exc:
        raise DraftUnavailable(str(exc)) from exc

    notes = []
    signature = _config_signature(label)
    original = _lookup("thread lookup", notes, _outlook_original, client, to, subject)
    if original:
        draft = _outlook_reply(client, original, to, body, signature)
    else:
        draft = client.create_draft({
            "subject": subject,
            "body": ({"contentType": "HTML",
                      "content": _outlook_html(body, signature)} if signature
                     else {"contentType": "Text", "content": body}),
            "toRecipients": [{"emailAddress": {"address": to}}],
        })
    return {"ok": True, "web_link": draft.get("webLink") or "",
            "id": draft.get("id") or "", "threaded": bool(original),
            "signed": bool(signature), "thread_note": "; ".join(notes)}


def _gmail_headers(msg: dict) -> dict:
    return {h.get("name", "").lower(): " ".join((h.get("value") or "").split())
            for h in (msg.get("payload") or {}).get("headers") or []}


def _gmail_original(gmail, to: str, subject: str) -> dict | None:
    """The newest message with this person under this subject, or None.

    Returns what a reply needs: the thread id, the original's own Message-ID
    header and References (the mail headers, never Gmail's API id, which a
    recipient's mail client cannot follow), its subject, sender and date.
    """
    core = subject_core(subject)
    if not core or not _ADDRESS_RE.match(to):
        return None
    words = " ".join(re.findall(r"\w+", core))
    listing = gmail.users().messages().list(
        userId="me", maxResults=_LOOKUP_CAP,
        q=(f"(from:{to} OR to:{to} OR cc:{to}) subject:({words}) "
           "-in:chats -in:drafts -in:spam -in:trash")).execute()
    # Newest first, so the first message that passes is the one to answer.
    for stub in listing.get("messages", []) or []:
        msg = gmail.users().messages().get(
            userId="me", id=stub["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date", "Message-ID",
                             "References"]).execute()
        if "DRAFT" in (msg.get("labelIds") or []):
            continue
        headers = _gmail_headers(msg)
        if subject_core(headers.get("subject", "")) != core:
            continue
        return {
            "id": stub["id"],
            "thread_id": msg.get("threadId") or "",
            "message_id": headers.get("message-id", ""),
            "references": headers.get("references", ""),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
        }
    return None


def _gmail_part(payload: dict, mime: str) -> str:
    """The first part of this type in a message, or "" when it has none."""
    import base64
    if (payload.get("mimeType") or "") == mime:
        data = (payload.get("body") or {}).get("data") or ""
        if data:
            return base64.urlsafe_b64decode(
                data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    for part in payload.get("parts") or []:
        text = _gmail_part(part, mime)
        if text:
            return text
    return ""


_GMAIL_QUOTE_STYLE = ("margin:0 0 0 .8ex;border-left:1px #ccc solid;"
                      "padding-left:1ex")


def _gmail_quote(gmail, original: dict) -> dict:
    """The original as a quoted block, the way a mail client appends it.

    Gmail's API writes exactly the bytes it is given, so without this the
    person receiving the reply would get it with nothing beneath it. Returned
    as `text` and `html`, one per half of the message. A half the original
    does not have is "", and the draft still sits in its thread.
    """
    payload = gmail.users().messages().get(
        userId="me", id=original["id"], format="full").execute().get("payload") or {}
    plain = _gmail_part(payload, "text/plain").strip()
    rich = _gmail_part(payload, "text/html").strip() or (
        _text_html(plain) if plain else "")
    wrote = f"On {original['date']}, {original['from']} wrote:"
    out = {"text": "", "html": ""}
    if plain:
        quoted = "\n".join(f"> {line}" if line else ">"
                           for line in plain.splitlines())
        out["text"] = f"\n\n{wrote}\n{quoted}\n"
    if rich:
        out["html"] = (f'<br><div class="gmail_quote"><div>{html.escape(wrote)}'
                       f'<br></div><blockquote class="gmail_quote" '
                       f'style="{_GMAIL_QUOTE_STYLE}">{rich}</blockquote></div>')
    return out


def draft_gmail(label: str, to: str, subject: str, body: str) -> dict:
    """Create a draft in a Gmail account's Drafts folder. Never sends.

    A reply inside the thread when the mailbox holds the original, a new
    message when it does not; `threaded` says which.

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
    notes = []
    signature = _config_signature(label) or _lookup(
        "reading the signature", notes, _gmail_signature, client.gmail, sender) or ""
    original = _lookup("thread lookup", notes, _gmail_original,
                       client.gmail, to, subject)
    quote = (original and _lookup("quoting the original", notes, _gmail_quote,
                                  client.gmail, original)) or {"text": "", "html": ""}
    # Plain text unless there is a signature to carry: a signature is HTML,
    # and then the message is sent in both forms.
    rich = None
    body = body.rstrip()
    if signature:
        body = _without_signoff(body, signature)
        rich = (f"<div>{_text_html(body)}</div><br>"
                f"<div>{_signature_html(signature)}</div>{quote['html']}")
        body += "\n\n" + _signature_text(signature)
    body += quote["text"] or "\n"
    message = {}
    headers = {}
    if original:
        subject = _reply_subject(original["subject"])
        message["threadId"] = original["thread_id"]
        if original["message_id"]:
            headers["In-Reply-To"] = original["message_id"]
            headers["References"] = " ".join(
                p for p in (original["references"], original["message_id"]) if p)
    message["raw"] = build_gmail_raw(sender, to, subject, body, html=rich,
                                     headers=headers)
    draft = client.gmail.users().drafts().create(
        userId="me", body={"message": message}
    ).execute()
    return {"ok": True, "web_link": _GMAIL_DRAFTS_URL, "id": draft.get("id") or "",
            "threaded": bool(original), "signed": bool(signature),
            "thread_note": "; ".join(notes)}


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


def draft_key(account_label: str, counterparty: str, subject: str) -> str:
    """(account, counterparty, normalized subject), the identity of one reply.

    Normalized so today's "Re: Re: Kestrel term sheet" is yesterday's "Kestrel
    term sheet": a reply thread renames itself every round trip, and keying on
    the raw subject would draft the same message again every morning.
    """
    return "|".join([
        (account_label or "").strip().lower(),
        (counterparty or "").strip().lower(),
        subject_core(subject),
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
      {"ok": True, "web_link": ..., "id": ...}  drafted just now; `threaded`
          is True for a reply inside the original thread and False for a new
          message, with `thread_note` saying why when a lookup failed;
          `signed` is True when the account's email signature went in
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
        "threaded": bool(result.get("threaded")),
        "signed": bool(result.get("signed")),
        "thread_note": result.get("thread_note", ""),
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
