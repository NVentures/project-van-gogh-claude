#!/usr/bin/env python3
"""
Voice Calibration — Weekly learning loop.

Primary signal: Sent mail analysis — pulls the last 7 days of sent email from
both Gmail accounts (primary + secondary) and the Microsoft account, extracts
voice patterns from the user's actual writing. Works from day one, no draft
history required.

Secondary signal: Draft diffs — if any skill (relationship_radar,
voice_generator, etc.) saved AI drafts to the voice-drafts log this week,
matches each to a sent email and diffs them to find where AI output diverges
from the user's edits.

Appends a dated section to the Voice Snapshot markdown file.

All user-specific values come from config.json via config_loader.
Run as: python app/cos_voice_calibration.py
"""
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from config_loader import (
    extra_spam_fragments,
    google_accounts,
    microsoft_accounts,
    tone_profile_path,
    user_bio_descriptor,
    user_first_name,
    user_name,
    voice_drafts_path,
    voice_guide_path,
    voice_snapshot_path,
)
from google_client import google_client
from microsoft_client import microsoft_client
from claude_cli import run_claude

DRAFTS_LOG = voice_drafts_path()
VOICE_SNAPSHOT = voice_snapshot_path()
VOICE_GUIDE = voice_guide_path()
TONE_PROFILE = tone_profile_path()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

LOOKBACK_DAYS = 7
MAX_SENT_PER_ACCOUNT = 15
BODY_CAP = 600  # chars per email passed to the model

# Generic spam/automation patterns. User-specific additions live in
# config.json email_filters.extra_spam_fragments.
GENERIC_SPAM_FRAGMENTS = [
    "mailer@", "no-reply", "noreply", "notifications@", "newsletter",
    "lemwarmup", "• warmup",
]
SPAM_FRAGMENTS = GENERIC_SPAM_FRAGMENTS + list(extra_spam_fragments())

WARMUP_PATTERNS = ["lemwarmup", "• warmup", "[lemwarmup]"]
CALENDAR_SUBJECTS = ["accepted:", "declined:", "invitation:", "re: re: re: re:"]


# ---------------------------------------------------------------------------
# Email body extraction
# ---------------------------------------------------------------------------

def extract_body(payload: dict) -> str:
    if payload.get("mimeType", "") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        body = extract_body(part)
        if body:
            return body
    return ""


def strip_quoted_reply(body: str) -> str:
    """Remove quoted reply chains (lines starting with > or 'On ... wrote:')."""
    lines = []
    for line in body.splitlines():
        if line.startswith(">"):
            continue
        if re.match(r"^On .{10,80}wrote:$", line.strip()):
            break  # everything after is quoted
        lines.append(line)
    return "\n".join(lines).strip()


def is_noise(subject: str, to_addr: str) -> bool:
    s = (subject or "").lower()
    a = (to_addr or "").lower()
    if any(p in s for p in WARMUP_PATTERNS):
        return True
    if any(p in s for p in CALENDAR_SUBJECTS):
        return True
    if any(f in a for f in SPAM_FRAGMENTS):
        return True
    return False


# ---------------------------------------------------------------------------
# Primary signal: sent mail analysis
# ---------------------------------------------------------------------------

def fetch_sent_emails(gclient, label: str) -> list[dict]:
    """Pull sent emails for the last 7 days, return usable ones with bodies."""
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y/%m/%d")
    try:
        data = gclient.gmail.users().messages().list(
            userId="me",
            q=f"in:sent after:{cutoff}",
            maxResults=MAX_SENT_PER_ACCOUNT,
        ).execute()
    except Exception as e:
        print(f"[voice-calibration] {label}: list failed — {e}")
        return []

    stubs = data.get("messages", [])
    if not stubs:
        return []

    def get_msg(stub):
        mid = stub.get("id")
        if not mid:
            return None
        try:
            return gclient.gmail.users().messages().get(
                userId="me", id=mid, format="full",
            ).execute(http=gclient.new_http())
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        raw = list(pool.map(get_msg, stubs))

    emails = []
    for msg in raw:
        if not msg:
            continue
        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        subject = headers.get("Subject", "")
        to_addr = headers.get("To", "")
        if is_noise(subject, to_addr):
            continue
        body = strip_quoted_reply(extract_body(msg.get("payload", {})))
        if len(body) < 60:  # skip short acks ("sounds good", "thanks", etc.)
            continue
        emails.append({
            "account": label,
            "subject": subject,
            "to": to_addr,
            "body": body[:BODY_CAP],
        })

    print(f"[voice-calibration] {label}: {len(emails)} usable sent emails")
    return emails


def fetch_sent_emails_outlook(label: str, sent_folder_id: str) -> list[dict]:
    """Pull sent emails for the last 7 days from a Microsoft account, using bodyPreview."""
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    try:
        data = microsoft_client(label).folder_messages(
            sent_folder_id,
            filter=f"sentDateTime ge {since}T00:00:00Z",
            select="subject,sentDateTime,toRecipients,bodyPreview",
            top=MAX_SENT_PER_ACCOUNT,
        )
    except Exception as e:
        print(f"[voice-calibration] {label} exception: {e}")
        return []

    emails = []
    for msg in data.get("value", []):
        subject = msg.get("subject", "")
        to_addr = ""
        for r in msg.get("toRecipients", []):
            addr = (r.get("emailAddress") or {}).get("address", "")
            if addr:
                to_addr = addr
                break
        if is_noise(subject, to_addr):
            continue
        body = (msg.get("bodyPreview") or "").strip()
        if len(body) < 60:
            continue
        emails.append({
            "account": label,
            "subject": subject,
            "to": to_addr,
            "body": body[:BODY_CAP],
        })

    print(f"[voice-calibration] {label}: {len(emails)} usable sent emails")
    return emails


def analyze_sent_voice(emails: list[dict]) -> str:
    """Single model call: extract voice patterns from all sent emails."""
    if not emails:
        return "(no sent emails found this week)"

    blocks = []
    for i, e in enumerate(emails, 1):
        blocks.append(f"[{i}] To: {e['to']}\nSubject: {e['subject']}\n{e['body']}")
    combined = "\n\n---\n\n".join(blocks)

    prompt = f"""You are analyzing the email writing style of {user_bio_descriptor()}. They run multiple ventures and communicate daily with investors, counterparties, legal counsel, vendors, and their team.

Below are {len(emails)} emails {user_first_name()} sent this week.

{combined}

Extract their voice patterns. Report in this exact format — one concise sentence per item, quoting actual phrases from the emails as evidence:

**Opening style:** How do they typically open? (direct ask, context first, relationship reference, etc.)

**Length and structure:** Typical sentence count. Paragraphs vs. lists. How much framing before the ask.

**Sign-off:** What sign-offs appear and when.

**Tone by recipient:** How formality or warmth shifts by counterparty type.

**Vocabulary habits:** Words or phrases they favor (or conspicuously avoid).

**Structural moves:** Any recurring patterns (e.g., sets context, makes ask, adds next step)."""

    try:
        result = run_claude(prompt, timeout=90)
        return result.stdout.strip() if result.returncode == 0 else "[analysis failed]"
    except Exception:
        return "[analysis failed]"


# ---------------------------------------------------------------------------
# Secondary signal: draft diffs
# ---------------------------------------------------------------------------

def read_draft_entries(since: date) -> list[dict]:
    if not DRAFTS_LOG.exists():
        return []
    text = DRAFTS_LOG.read_text(encoding="utf-8")
    entries = []
    pattern = re.compile(
        r"^## (\d{4}-\d{2}-\d{2}) \| (.+?)\n+> (.+?)\n+---",
        re.MULTILINE | re.DOTALL,
    )
    for m in pattern.finditer(text):
        try:
            entry_date = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if entry_date >= since:
            entries.append({
                "date": entry_date,
                "contact": m.group(2).strip(),
                "draft": m.group(3).strip(),
            })
    return entries


def search_sent_body(contact_name: str, since: date) -> str | None:
    """Search both Gmail accounts for a sent email mentioning the contact."""
    since_str = since.strftime("%Y/%m/%d")
    query = f'in:sent after:{since_str} "{contact_name}"'

    gclients = [google_client(a["label"]) for a in GOOGLE_ACCOUNTS]

    for gclient in gclients:
        try:
            data = gclient.gmail.users().messages().list(
                userId="me", q=query, maxResults=3,
            ).execute()
            msgs = data.get("messages", [])
            if not msgs:
                continue
            mid = msgs[0]["id"]
            msg = gclient.gmail.users().messages().get(
                userId="me", id=mid, format="full",
            ).execute()
            body = extract_body(msg.get("payload", {}))
            if body and body.strip():
                return body
        except Exception:
            continue
    return None


def analyze_diff(draft: str, sent: str, contact: str) -> str:
    prompt = f"""Analyze how a CEO edited this AI-generated email draft before sending.

Contact: {contact}

AI draft:
{draft}

What {user_first_name()} actually sent:
{sent[:600]}

In 2-4 bullet points: what was cut, added, changed, or reworded? Focus on length, tone, structure, sign-off — not content specifics. Be terse."""

    try:
        result = run_claude(prompt, timeout=60)
        return result.stdout.strip() if result.returncode == 0 else "[analysis failed]"
    except Exception:
        return "[analysis failed]"


def synthesize_draft_patterns(diffs: list[dict]) -> str:
    if len(diffs) < 2:
        return ""
    entries = "\n\n".join(
        f"Contact: {d['contact']}\n{d['analysis']}" for d in diffs
    )
    prompt = f"""Summarize recurring edit patterns from a CEO's AI draft revisions this week.

{entries}

Synthesize 3-5 recurring patterns. Only include patterns that appear across multiple contacts: tone, length, structure, sign-off, vocabulary. Skip one-offs. Be terse."""

    try:
        result = run_claude(prompt, timeout=60)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Voice Snapshot init + append
# ---------------------------------------------------------------------------

def ensure_voice_snapshot():
    if VOICE_SNAPSHOT.exists():
        return

    voice_guide_str = str(VOICE_GUIDE)
    tone_profile_str = str(TONE_PROFILE)

    voice_text = VOICE_GUIDE.read_text(encoding="utf-8") if VOICE_GUIDE.exists() else ""
    m = re.search(r"## Paste-Ready Prompt Version\n\n(.+?)(?=\n---|\Z)", voice_text, re.DOTALL)
    base_prompt = m.group(1).strip() if m else f"(see {voice_guide_str})"

    anti_prompt = ""
    if TONE_PROFILE.exists():
        tp_text = TONE_PROFILE.read_text(encoding="utf-8")
        am = re.search(r"(## Anti-Prompt.+?)(?=\n---|\Z)", tp_text, re.DOTALL)
        if am:
            anti_prompt = "\n\n" + am.group(1).strip()

    content = f"""---
title: Voice Snapshot
updated: {date.today().isoformat()}
tags: [voice, cos]
---

# Voice Snapshot

Current calibration of {user_name()}'s voice for AI-generated content.
Seeded from `{voice_guide_str}` and `{tone_profile_str}` (the /voice-generator per-audience profile). Updated weekly by `/voice-calibration`.

## Base Voice Guide

{base_prompt}{anti_prompt}

## Calibration Log

(Weekly patterns appended here each Sunday by `/voice-calibration`.)
"""
    VOICE_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    VOICE_SNAPSHOT.write_text(content, encoding="utf-8")
    print("[voice-calibration] created Voice Snapshot.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = date.today()
    since = today - timedelta(days=LOOKBACK_DAYS)

    ensure_voice_snapshot()

    # Sent mail voice analysis — every configured account
    all_emails = []
    for a in GOOGLE_ACCOUNTS:
        print(f"[voice-calibration] fetching sent mail — {a['label']} Gmail...")
        all_emails += fetch_sent_emails(google_client(a["label"]), a["label"])
    for a in MICROSOFT_ACCOUNTS:
        print(f"[voice-calibration] fetching sent mail — {a['label']}...")
        all_emails += fetch_sent_emails_outlook(a["label"], a["sent_folder_id"])

    print(f"[voice-calibration] analyzing voice from {len(all_emails)} sent emails...")
    voice_analysis = analyze_sent_voice(all_emails)

    # Secondary: draft diffs
    print(f"[voice-calibration] reading AI drafts since {since}...")
    draft_entries = read_draft_entries(since)
    print(f"[voice-calibration] {len(draft_entries)} draft entries found")

    diffs = []
    for entry in draft_entries:
        print(f"[voice-calibration] searching sent mail for: {entry['contact']}")
        sent = search_sent_body(entry["contact"], entry["date"])
        if not sent:
            print(f"[voice-calibration] no match for {entry['contact']} — skipped")
            continue
        analysis = analyze_diff(entry["draft"], sent, entry["contact"])
        diffs.append({
            "contact": entry["contact"],
            "date": entry["date"].isoformat(),
            "analysis": analysis,
        })

    draft_pattern_summary = synthesize_draft_patterns(diffs)

    # Build week section
    lines = [
        f"\n### Week of {today.strftime('%Y-%m-%d')}",
        f"Sent emails analyzed: {len(all_emails)} | AI drafts reviewed: {len(draft_entries)} | Matched: {len(diffs)}",
        "",
        "**Voice patterns from sent mail:**",
        voice_analysis,
    ]

    if diffs:
        lines += ["", "**AI draft diffs:**"]
        for d in diffs:
            lines.append(f"\n**{d['contact']}** ({d['date']}):")
            lines.append(d["analysis"])
        if draft_pattern_summary:
            lines += ["", "**AI divergence patterns:**", draft_pattern_summary]

    lines.append("")

    snapshot = VOICE_SNAPSHOT.read_text(encoding="utf-8")
    snapshot = re.sub(
        r"(updated:\s*)\d{4}-\d{2}-\d{2}",
        f"\\g<1>{today.isoformat()}",
        snapshot,
        count=1,
    )
    VOICE_SNAPSHOT.write_text(snapshot + "\n" + "\n".join(lines), encoding="utf-8")

    print("[voice-calibration] done. appended to Voice Snapshot.md")
    print(f"SUMMARY sent={len(all_emails)} drafts={len(draft_entries)} matched={len(diffs)}")


if __name__ == "__main__":
    main()
