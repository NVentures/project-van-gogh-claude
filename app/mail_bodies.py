#!/usr/bin/env python3
"""mail_bodies.py: sent and received mail WITH its text, for evidence.

Every other mail read in the product returns headers and a snippet, because a
briefing only needs to know that a thread exists. Judging whether a to-do is
finished needs the sentence that finished it, and that sentence is in the
body. This is the one place a message body is fetched for that purpose.

Read-only by construction: it lists and gets, and nothing here can draft,
send, label or delete.

One row shape for both providers:

    {id, account, direction, date, from, to[], cc[], subject, body,
     has_attachments, attachment_names[]}

`direction` is "sent" or "inbound", `date` is the local YYYY-MM-DD, and every
address is lowercased. Both fetchers RAISE on a provider error, so a caller
can tell "nothing was sent" from "the mailbox could not be read"; the judge
records the second as a degraded run and closes nothing on absence.

`html_to_text` and `gmail_text` are the same two functions `advisor.py` keeps
privately. They are repeated here rather than imported so the advisor, which
acts on what it reads, does not come to depend on a module written for a
different job; if a third caller appears, that is the moment to share them.
"""
from __future__ import annotations

import base64
import html as html_lib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import getaddresses

from config_loader import user_tz

_ADDR_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BODY_CAP = 20000


def html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup or "")
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))


def gmail_text(payload: dict) -> str:
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
                rich.append(html_to_text(text))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return "\n".join(plain) if plain else "\n".join(rich)


def _addresses(value) -> list:
    return [a.lower() for _n, a in getaddresses([str(value or "")])
            if _ADDR_RE.fullmatch(a or "")]


def _attachment_names(payload: dict) -> list:
    names = []

    def walk(part):
        if part.get("filename"):
            names.append(str(part["filename"]))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    return names


def _clean(body: str) -> str:
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:BODY_CAP]


def _gmail_row(msg: dict, label: str, direction: str) -> dict:
    payload = msg.get("payload") or {}
    hdrs = {h.get("name", "").lower(): h.get("value", "")
            for h in payload.get("headers") or []}
    try:
        when = datetime.fromtimestamp(int(msg.get("internalDate")) / 1000,
                                      timezone.utc).astimezone(user_tz())
        day = when.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        day = ""
    names = _attachment_names(payload)
    sender = _addresses(hdrs.get("from"))
    return {"id": str(msg.get("id") or ""), "account": label,
            "direction": direction, "date": day,
            "from": sender[0] if sender else "",
            "to": _addresses(hdrs.get("to")), "cc": _addresses(hdrs.get("cc")),
            "subject": hdrs.get("subject", ""),
            "body": _clean(gmail_text(payload)),
            "has_attachments": bool(names), "attachment_names": names}


def _gmail_fetch(account: dict, query: str, direction: str, cap: int) -> list:
    from google_client import google_client
    client = google_client(account["label"])
    stubs, token = [], None
    while len(stubs) < cap:
        page = client.gmail.users().messages().list(
            userId="me", q=query, maxResults=min(100, cap - len(stubs)),
            pageToken=token).execute()
        stubs.extend(page.get("messages") or [])
        token = page.get("nextPageToken")
        if not token:
            break

    def get(stub):
        # One http object per thread: the shared one is not thread safe.
        return client.gmail.users().messages().get(
            userId="me", id=stub["id"], format="full").execute(
                http=client.new_http())

    with ThreadPoolExecutor(max_workers=8) as pool:
        messages = list(pool.map(get, stubs[:cap]))
    rows = []
    for msg in messages:
        if direction == "sent" and "SENT" not in (msg.get("labelIds") or []):
            continue
        if "DRAFT" in (msg.get("labelIds") or []):
            # A draft is never a reply. Unsent text proves nothing.
            continue
        rows.append(_gmail_row(msg, account["label"], direction))
    return rows


def _graph_people(entries) -> list:
    out = []
    for entry in entries or []:
        addr = ((entry or {}).get("emailAddress") or {}).get("address") or ""
        if addr:
            out.append(addr.lower())
    return out


def _graph_row(msg: dict, label: str, direction: str) -> dict:
    body = msg.get("body") or {}
    text = body.get("content") or msg.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = html_to_text(text)
    stamp = msg.get("sentDateTime") or msg.get("receivedDateTime") or ""
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        day = when.astimezone(user_tz()).strftime("%Y-%m-%d")
    except ValueError:
        day = stamp[:10]
    sender = _graph_people([msg.get("from")])
    return {"id": str(msg.get("id") or ""), "account": label,
            "direction": direction, "date": day,
            "from": sender[0] if sender else "",
            "to": _graph_people(msg.get("toRecipients")),
            "cc": _graph_people(msg.get("ccRecipients")),
            "subject": msg.get("subject") or "",
            "body": _clean(text),
            "has_attachments": bool(msg.get("hasAttachments")),
            "attachment_names": []}


_GRAPH_SELECT = ("id,subject,sentDateTime,receivedDateTime,from,toRecipients,"
                 "ccRecipients,body,bodyPreview,hasAttachments,isDraft")


def fetch_sent(account: dict, since_str: str, today_str: str,
               cap: int = 200) -> list:
    """What this account sent from `since_str` to `today_str`, with bodies."""
    if account.get("provider") == "google":
        return _gmail_fetch(
            account, f"in:sent after:{since_str.replace('-', '/')}", "sent", cap)
    if account.get("provider") == "microsoft":
        from microsoft_client import microsoft_client
        data = microsoft_client(account["label"]).folder_messages(
            account.get("sent_folder_id") or "sentitems",
            filter=(f"sentDateTime ge {since_str}T00:00:00Z and "
                    f"sentDateTime le {today_str}T23:59:59Z"),
            select=_GRAPH_SELECT, top=cap)
        return [_graph_row(m, account["label"], "sent")
                for m in data.get("value") or [] if not m.get("isDraft")]
    return []


def fetch_inbound_from(account: dict, senders, since_str: str, today_str: str,
                       cap: int = 100) -> list:
    """What these senders wrote to this account in the window, with bodies.

    An empty sender list is an empty answer and never a call: an inbox read
    with no sender filter is the whole inbox, which is not evidence about
    anyone in particular.
    """
    senders = sorted({str(s).lower() for s in senders or []
                      if _ADDR_RE.fullmatch(str(s))})
    if not senders:
        return []
    if account.get("provider") == "google":
        rows = []
        for i in range(0, len(senders), 20):
            who = " OR ".join(senders[i:i + 20])
            rows += _gmail_fetch(
                account,
                f"from:({who}) after:{since_str.replace('-', '/')} -in:sent",
                "inbound", cap - len(rows))
            if len(rows) >= cap:
                break
        return rows[:cap]
    if account.get("provider") == "microsoft":
        from microsoft_client import microsoft_client
        client = microsoft_client(account["label"])
        rows = []
        for sender in senders:
            if len(rows) >= cap:
                break
            data = client.messages(
                filter=(f"receivedDateTime ge {since_str}T00:00:00Z and "
                        f"from/emailAddress/address eq '{sender}'"),
                select=_GRAPH_SELECT, top=min(25, cap - len(rows)))
            rows += [_graph_row(m, account["label"], "inbound")
                     for m in data.get("value") or [] if not m.get("isDraft")]
        return rows[:cap]
    return []
