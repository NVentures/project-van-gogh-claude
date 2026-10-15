"""
outlook_draft.py — create an email draft in an Outlook account's Drafts folder.

The single entry point skills use to draft Outlook mail (POST /me/messages via
the Graph client — creates a draft, never sends). The script is also the tier
probe: it loads the state .env itself, so its success or failure is ground
truth for whether this machine has a Graph token for the account. Skills must
always attempt it and fall back to ready-to-paste text only on the machine-
readable "no_token" error — never by guessing from their own environment.

Usage:
    python app/outlook_draft.py --label OUTLOOK --to a@b.c --subject "..." \
        --body-file /path/to/body.txt [--strip-frontmatter]

Output (stdout, JSON):
    {"ok": true,  "web_link": "...", "label": "...", "to": "...", "subject": "..."}
    {"ok": false, "error": "no_token" | "draft_failed", "message": "..."}
Exit code 0 on success, 1 on any failure.
"""

import argparse
import json
import sys
from pathlib import Path

import draft_email


def load_body(path: Path, strip_frontmatter: bool) -> str:
    """Read the draft body; optionally strip YAML frontmatter and heading markers
    from a vault markdown file so it reads as a plain-text email.

    Kept as a name because skills and tests reference it; the logic now lives in
    draft_email so the Workbench and this CLI cannot drift apart."""
    return draft_email.plain_body_from_markdown_file(path, strip_frontmatter)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an Outlook draft via the Graph client (never sends).")
    parser.add_argument("--label", required=True, help="Account label from config (matches MS_GRAPH_REFRESH_TOKEN_<LABEL>)")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", required=True, help="Draft subject")
    parser.add_argument("--body-file", required=True, help="File containing the draft body")
    parser.add_argument(
        "--strip-frontmatter",
        action="store_true",
        help="Treat the body file as vault markdown: drop YAML frontmatter and heading markers",
    )
    args = parser.parse_args()

    body = load_body(Path(args.body_file), args.strip_frontmatter)

    try:
        result = draft_email.draft_outlook(args.label, args.to, args.subject, body)
    except draft_email.DraftUnavailable as exc:
        # No refresh token for this label: the Tier 2 signal skills key off.
        print(json.dumps({"ok": False, "error": "no_token", "message": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "draft_failed", "message": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "web_link": result["web_link"],
                "label": args.label,
                "to": args.to,
                "subject": args.subject,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
