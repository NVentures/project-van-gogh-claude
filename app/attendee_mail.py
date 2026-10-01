#!/usr/bin/env python3
"""The live mail with one person, for a prep brief.

A prep written from the vault alone knows what was said in a meeting three
weeks ago and nothing about the email that arrived yesterday. The vault is
filed from meetings; the last word between two people is usually in a mailbox.
So before a call this reads the recent threads with each outside attendee,
straight from the connected mailboxes, and says who wrote last.

Three rules.

**A draft is not a reply.** An unsent draft sits in the same thread as the
message it answers, and counting it would report the user as having written
last on a thread they never answered. Drafts are dropped before anything is
compared.

**"Could not look" is not "nothing there".** A mailbox that failed to read and
a person nobody has written to in two months both produce an empty list. The
result therefore says which accounts were read and which failed, and `checked`
is true only when at least one mailbox answered, so the brief can say the
difference in words.

**It fails open.** A prep is worth more than this section of it. Every
provider error is caught per account and recorded by its type, never its text:
exception text quotes the request it failed on, and this output is mailed.

Usage:
    python app/attendee_mail.py --email someone@example.com [--days 60]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402

# How far back a thread still counts as recent. Two months covers a deal that
# went quiet over a holiday without reaching back into a previous conversation
# that no longer describes where things stand.
WINDOW_DAYS = 60

# Threads shown per person. A prep is read in a minute; the newest three say
# where things stand, and a fourth is history.
MAX_THREADS = 3

# Messages read per mailbox per person. Enough to cover three threads with a
# few messages each; a ceiling, because this runs once per attendee per account.
LIST_CAP = 12

SNIPPET_CHARS = 220

# Strict on purpose: the address is placed inside a provider search string, and
# anything that is not plainly an address is refused rather than escaped.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

YOU, THEM, OTHER = "you", "them", "someone else"


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def _when(value) -> datetime | None:
    """A provider timestamp as an aware UTC datetime, or None."""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        text = str(value).split(".")[0].rstrip("Z")
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _clip(text: str) -> str:
    text = " ".join(html.unescape(text or "").split())
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS].rstrip() + "..."


def _gmail_rows(account: dict, email: str, since: datetime) -> list:
    from google_client import google_client
    gmail = google_client(account["label"]).gmail
    query = (f"(from:{email} OR to:{email} OR cc:{email}) "
             f"after:{since.strftime('%Y/%m/%d')} -in:chats -in:drafts "
             "-in:spam -in:trash")
    listing = gmail.users().messages().list(
        userId="me", q=query, maxResults=LIST_CAP).execute()
    rows = []
    for stub in listing.get("messages", []) or []:
        msg = gmail.users().messages().get(
            userId="me", id=stub["id"], format="metadata",
            metadataHeaders=["From", "Subject"]).execute()
        if "DRAFT" in (msg.get("labelIds") or []):
            continue
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in (msg.get("payload") or {}).get("headers") or []}
        rows.append({
            "thread": str(msg.get("threadId") or stub["id"]),
            "when": _when(msg.get("internalDate")),
            "from": parseaddr(headers.get("from", ""))[1].lower(),
            "subject": headers.get("subject", ""),
            "snippet": msg.get("snippet", ""),
        })
    return rows


def _graph_addresses(msg: dict) -> set:
    out = set()
    sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address")
    if sender:
        out.add(sender.lower())
    for field in ("toRecipients", "ccRecipients"):
        for person in msg.get(field) or []:
            address = (person.get("emailAddress") or {}).get("address")
            if address:
                out.add(address.lower())
    return out


def _graph_rows(account: dict, email: str, since: datetime) -> list:
    from microsoft_client import microsoft_client
    data = microsoft_client(account["label"]).messages(
        search=f'"participants:{email}"', top=LIST_CAP,
        select="id,conversationId,subject,from,toRecipients,ccRecipients,"
               "receivedDateTime,bodyPreview,isDraft")
    rows = []
    for msg in data.get("value", []) or []:
        if msg.get("isDraft"):
            continue
        # The search is a relevance match over the mailbox, so it is checked:
        # a message that does not actually carry this address is not theirs.
        if email not in _graph_addresses(msg):
            continue
        when = _when(msg.get("receivedDateTime"))
        if when is None or when < since:
            continue
        sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "")
        rows.append({
            "thread": str(msg.get("conversationId") or msg.get("id") or ""),
            "when": when,
            "from": sender.lower(),
            "subject": msg.get("subject", ""),
            "snippet": msg.get("bodyPreview", ""),
        })
    return rows


def _threads(rows: list, email: str, mine: set, account: str) -> list:
    """Fold messages into threads, each described by its newest message."""
    by_thread: dict = {}
    for row in rows:
        if row["when"] is None:
            continue
        by_thread.setdefault(row["thread"], []).append(row)
    out = []
    for messages in by_thread.values():
        last = max(messages, key=lambda r: r["when"])
        if last["from"] in mine:
            who = YOU
        elif last["from"] == email:
            who = THEM
        else:
            who = OTHER
        out.append({
            "subject": _clip(last["subject"]) or "(no subject)",
            "last_date": last["when"].date().isoformat(),
            "last_from": who,
            "snippet": _clip(last["snippet"]),
            "account": account,
            "messages_seen": len(messages),
            "_when": last["when"],
        })
    return out


def threads_with(email: str, now: datetime | None = None,
                 days: int = WINDOW_DAYS, accounts: list | None = None) -> dict:
    """The recent mail with one address, across every connected mailbox.

    Returns `checked`, `accounts_read`, `accounts_failed` (account and error
    type), `threads` (newest first, at most MAX_THREADS) and `last_word`: who
    wrote the newest message of the newest thread. Never raises.
    """
    email = (email or "").strip().lower()
    out = {"checked": False, "window_days": days, "accounts_read": [],
           "accounts_failed": [], "threads": [], "last_word": "", "why": ""}
    if not valid_email(email):
        out["why"] = "no usable address for this person"
        return out
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    try:
        accounts = accounts if accounts is not None else config_loader.accounts()
        mine = set(config_loader.account_emails_lower())
    except Exception as exc:                                    # noqa: BLE001
        out["why"] = f"the accounts could not be read ({type(exc).__name__})"
        return out

    threads = []
    for account in accounts:
        label = account.get("label") or ""
        try:
            if account.get("provider") == config_loader.GOOGLE:
                rows = _gmail_rows(account, email, since)
            elif account.get("provider") == config_loader.MICROSOFT:
                rows = _graph_rows(account, email, since)
            else:
                continue
        except Exception as exc:                                # noqa: BLE001
            out["accounts_failed"].append(
                {"account": label, "error": type(exc).__name__})
            continue
        out["accounts_read"].append(label)
        threads += _threads(rows, email, mine, label)

    out["checked"] = bool(out["accounts_read"])
    if not out["checked"]:
        out["why"] = "no mailbox could be read"
    threads.sort(key=lambda t: t["_when"], reverse=True)
    for thread in threads:
        thread.pop("_when", None)
    out["threads"] = threads[:MAX_THREADS]
    if out["threads"]:
        out["last_word"] = out["threads"][0]["last_from"]
    return out


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recent mail with one person.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--days", type=int, default=WINDOW_DAYS)
    args = parser.parse_args(argv)
    print(json.dumps(threads_with(args.email, days=args.days), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
