"""
send_email.py — send one email from a configured account (Google or Microsoft).

The single sanctioned entry point for actually SENDING mail. The outbound-mail
convention is drafts-first: skills and sessions prepare drafts for the user's
review (app/outlook_draft.py for Outlook) and never send on their own
initiative. Run this script only on an explicit, per-message instruction from
the user to send — and never send by importing digest_send or hand-rolling
API calls instead (digest_send.py imports its mail primitives from here).

Like outlook_draft.py, the script is its own tier probe: it loads the state
.env via the OAuth clients, so its machine-readable "no_token" error is ground
truth for whether this machine can send from the account.

Usage:
    python app/send_email.py --label GMAIL --to a@b.c --subject "..." \
        --body-file /path/to/body.txt [--strip-frontmatter]

Output (stdout, JSON):
    {"ok": true,  "from": "...", "to": "...", "subject": "...", "label": "..."}
    {"ok": false, "error": "unknown_account" | "no_token" | "send_failed", "message": "..."}
Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

import config_loader


def clean_address(addr: str, what: str) -> str:
    """Return the bare addr-spec, refusing anything that could smuggle extra
    MIME headers (CR/LF) or that doesn't parse as a single address."""
    if "\r" in addr or "\n" in addr:
        raise RuntimeError(f"invalid {what} address (control characters): {addr!r}")
    parsed = parseaddr(addr)[1]
    if not parsed or "@" not in parsed:
        raise RuntimeError(f"invalid {what} address: {addr!r}")
    return parsed


def build_gmail_raw(sender: str, to: str, subject: str, body: str,
                    html: str | None = None) -> str:
    if html is None:
        msg = MIMEText(body, "plain", "utf-8")
    else:
        # Plain part first, HTML last — clients prefer the last alternative.
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def build_graph_message(to: str, subject: str, body: str,
                        html: str | None = None) -> dict:
    if html is None:
        graph_body = {"contentType": "Text", "content": body}
    else:
        graph_body = {"contentType": "HTML", "content": html}
    return {
        "subject": subject,
        "body": graph_body,
        "toRecipients": [{"emailAddress": {"address": to}}],
    }


def send_email(account: dict, to: str, subject: str, body: str,
               html: str | None = None) -> None:
    if account["provider"] == "google":
        from google_client import google_client

        raw = build_gmail_raw(account["email"], to, subject, body, html=html)
        google_client(account["label"]).gmail.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
    elif account["provider"] == "microsoft":
        from microsoft_client import microsoft_client

        microsoft_client(account["label"]).send_mail(
            build_graph_message(to, subject, body, html=html))
    else:
        raise RuntimeError(f"unknown sender provider: {account['provider']!r}")


def send_draft(account: dict, draft_id: str) -> None:
    """Send a draft that already exists in the account's Drafts folder, by id.

    The briefing page's stamp presses this, not `send_email`. The difference
    matters: `draft_email.py` records only the provider's draft id, never the
    body, so re-sending the briefing's original text would discard any edit the
    user made in Gmail or Outlook and leave the original draft sitting there.
    Sending by id sends what the user actually has.

    Raises on failure, like `send_email`. A lost response is NOT retried by any
    caller: the draft may already be gone, and a duplicate is worse than a
    report that says to check Sent.
    """
    if not draft_id:
        raise RuntimeError("no draft id")
    if account["provider"] == "google":
        from google_client import google_client

        try:
            google_client(account["label"]).gmail.users().drafts().send(
                userId="me", body={"id": draft_id}
            ).execute()
        except Exception as exc:                              # noqa: BLE001
            # A token minted before gmail.send was requested has no send
            # permission, and Google reports that as a bare 403. Left alone it
            # reads as "the send failed" when the truth is "this install was
            # never allowed to send", which is a re-consent, not a retry.
            if _is_missing_send_scope(exc):
                raise RuntimeError(
                    "This Google account was authorized before Van Gogh could "
                    "send, so its token has no send permission. Run "
                    "/van-gogh:install-van-gogh to re-authorize it. The draft "
                    "is untouched in Drafts."
                ) from exc
            raise
    elif account["provider"] == "microsoft":
        from microsoft_client import microsoft_client

        microsoft_client(account["label"]).send_draft(draft_id)
    else:
        raise RuntimeError(f"unknown sender provider: {account['provider']!r}")


def _is_missing_send_scope(exc: Exception) -> bool:
    """True when Google refused for lack of the send scope, not for anything else.

    Matched on the status AND the reason: a 403 alone is also what a suspended
    account and a rate limit look like, and telling someone to re-consent when
    the real problem is a quota wastes the one action they were given.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status != 403:
        return False
    text = str(exc).lower()
    return ("insufficient" in text or "scope" in text
            or "request had insufficient authentication" in text)


def account_for_label(label: str) -> dict | None:
    """The full config account entry for `label` (case-insensitive), or None."""
    for a in config_loader.accounts():
        if a.get("label", "").lower() == label.lower() and a.get("email"):
            return a
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one email from a configured account. Drafts-first: "
                    "run only on an explicit user instruction to send.")
    parser.add_argument("--label", required=True,
                        help="Account label from the config accounts block")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body-file", required=True,
                        help="File containing the plain-text body")
    parser.add_argument(
        "--strip-frontmatter",
        action="store_true",
        help="Treat the body file as vault markdown: drop YAML frontmatter and heading markers",
    )
    args = parser.parse_args()

    account = account_for_label(args.label)
    if account is None:
        print(json.dumps({
            "ok": False, "error": "unknown_account",
            "message": f"no configured account has label {args.label!r}; "
                       "see the config accounts block",
        }))
        return 1

    from outlook_draft import load_body

    body = load_body(Path(args.body_file), args.strip_frontmatter)
    try:
        to = clean_address(args.to, "recipient")
        send_email(account, to, args.subject, body)
    except RuntimeError as exc:
        error = "no_token" if "Missing refresh token" in str(exc) else "send_failed"
        print(json.dumps({"ok": False, "error": error, "message": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "send_failed", "message": str(exc)}))
        return 1

    print(json.dumps({
        "ok": True,
        "from": account["email"],
        "to": to,
        "subject": args.subject,
        "label": account["label"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
