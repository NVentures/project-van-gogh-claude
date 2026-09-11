#!/usr/bin/env python3
"""
voice_generator.py — Tone-of-voice profile generator.

Fetches a lookback window of sent email from the primary + secondary Google
accounts and the Microsoft account. Classifies each thread by counterparty
category and scores tone features via Claude (Haiku, called through the
`claude` CLI). Aggregates into a per-category voice profile. Outputs JSON to
stdout (status/progress lines go to stderr so stdout stays clean JSON).

All user-specific values come from config.json via config_loader.

Usage:
    python app/voice_generator.py [--days 90] > voice_generator_output.json
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from config_loader import (
    extra_spam_fragments,
    google_accounts,
    internal_team_emails,
    microsoft_accounts,
    resolved_meta,
    user_bio_descriptor,
    user_tz,
    voice_classifier_context,
    voice_guide_path,
)
from claude_cli import run_claude

# Sent-mail date bucketing uses the user's configured zone.
USER_TZ = user_tz()

GOOGLE_ACCOUNTS = google_accounts()
MICROSOFT_ACCOUNTS = microsoft_accounts()

INTERNAL_TEAM_EMAILS = internal_team_emails()

# Generic spam patterns. User-specific additions live in
# config.json email_filters.extra_spam_fragments.
GENERIC_SPAM_FRAGMENTS = [
    "noreply", "no-reply", "donotreply", "notifications@",
    "notification@", "alerts@", "mailer@",
]
SPAM_FRAGMENTS = GENERIC_SPAM_FRAGMENTS + list(extra_spam_fragments())

WARMUP_PATTERNS = ["• lemwarmup", "lemwarmup", "• warmup", "[lemwarmup]"]
CALENDAR_NOISE = ["accepted:", "declined:", "canceled:", "invitation:"]

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ── Claude CLI helper ───────────────────────────────────────────────────────

def _claude(prompt, model=HAIKU_MODEL, timeout=120):
    """Call the `claude` CLI. Returns stripped stdout (code fences removed),
    or "" on any failure. Subscription routing is forced by dropping
    ANTHROPIC_API_KEY from the child env (see claude_cli.run_claude)."""
    try:
        r = run_claude(prompt, model, timeout=timeout)
        if r.returncode != 0:
            return ""
        out = r.stdout.strip()
        out = re.sub(r"^```[a-z]*\n?", "", out)
        out = re.sub(r"\n?```$", "", out)
        return out
    except Exception:
        return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_spam(addr):
    a = (addr or "").lower()
    return any(f in a for f in SPAM_FRAGMENTS)


def is_internal(addr):
    return (addr or "").lower() in {e.lower() for e in INTERNAL_TEAM_EMAILS}


def is_warmup(subject):
    s = (subject or "").lower()
    return any(p in s for p in WARMUP_PATTERNS)


def is_calendar_noise(subject):
    s = (subject or "").lower()
    return any(s.startswith(p) for p in CALENDAR_NOISE)


# ── Gmail sent fetch ──────────────────────────────────────────────────────────

def fetch_gmail_sent(account_label, account_email, cutoff_str, max_threads=100):
    """Fetch sent threads from a Google account since cutoff_str (YYYY-MM-DD).

    Uses google_client (direct OAuth).
    """
    from google_client import google_client

    cutoff_q = cutoff_str.replace("-", "/")
    try:
        gclient = google_client(account_label)
        data = gclient.gmail.users().threads().list(
            userId="me",
            q=f"in:sent after:{cutoff_q}",
            maxResults=max_threads,
        ).execute()
    except Exception as e:
        return [], str(e)

    def get_thread(stub):
        tid = stub.get("id")
        if not tid:
            return None
        try:
            thread_data = gclient.gmail.users().threads().get(
                userId="me",
                id=tid,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            return tid, thread_data, stub.get("snippet", "")
        except Exception:
            return None

    # Sequential — avoid nested ThreadPoolExecutor crashes when called from
    # the pool in main().
    fetched = [get_thread(stub) for stub in data.get("threads", [])]

    threads = []
    seen_tids = set()

    for item in fetched:
        if not item:
            continue
        tid, thread_data, snippet = item

        if tid in seen_tids:
            continue
        seen_tids.add(tid)

        messages = thread_data.get("messages", [])
        if not messages:
            continue

        # Find the most recent sent message from this account
        sent_msg = None
        for msg in reversed(messages):
            hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            if "SENT" in msg.get("labelIds", []) or account_email.lower() in hdrs.get("From", "").lower():
                sent_msg = (msg, hdrs)
                break

        if not sent_msg:
            continue

        msg, hdrs = sent_msg
        subject = hdrs.get("Subject", "(No subject)")

        if is_warmup(subject) or is_calendar_noise(subject):
            continue

        to_raw = hdrs.get("To", "")
        to_match = re.search(r"<([^>]+)>", to_raw)
        to_addr = to_match.group(1) if to_match else to_raw.strip()
        to_name = (
            to_raw[: to_raw.index("<")].strip().strip('"')
            if "<" in to_raw else to_addr
        )

        if is_spam(to_addr) or is_internal(to_addr):
            continue

        try:
            dt = parsedate_to_datetime(hdrs.get("Date", "")).astimezone(USER_TZ)
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = ""

        threads.append({
            "thread_id": tid,
            "subject": subject,
            "snippet": snippet[:300],
            "to": to_name or to_addr,
            "to_email": to_addr,
            "account": account_label,
            "date": date_str,
        })

    return threads, None


# ── Outlook sent fetch ────────────────────────────────────────────────────────

def fetch_outlook_sent(label, sent_folder_id, cutoff_str, max_msgs=200):
    """Fetch sent threads from a Microsoft account since cutoff_str.

    Uses microsoft_client (direct OAuth / Graph REST).
    """
    from microsoft_client import microsoft_client

    try:
        mclient = microsoft_client(label)
        data = mclient.folder_messages(
            sent_folder_id,
            filter=f"sentDateTime ge {cutoff_str}T00:00:00Z",
            select="id,subject,sentDateTime,toRecipients,conversationId,bodyPreview",
            top=max_msgs,
        )
    except Exception as e:
        return [], str(e)

    by_conv = {}
    for msg in data.get("value", []):
        cid = msg.get("conversationId")
        if not cid:
            continue
        subject = msg.get("subject", "")
        if is_warmup(subject) or is_calendar_noise(subject):
            continue
        if cid not in by_conv or msg.get("sentDateTime", "") > by_conv[cid].get("sentDateTime", ""):
            by_conv[cid] = msg

    threads = []
    for cid, msg in by_conv.items():
        to_addr = ""
        to_name = ""
        for r in msg.get("toRecipients", []):
            ea = r.get("emailAddress", {})
            addr = ea.get("address", "")
            if not is_spam(addr) and not is_internal(addr):
                to_addr = addr
                to_name = ea.get("name", addr)
                break
        if not to_addr:
            continue

        try:
            dt = datetime.fromisoformat(msg.get("sentDateTime", "").replace("Z", "+00:00")).astimezone(USER_TZ)
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = ""

        threads.append({
            "thread_id": cid,
            "subject": msg.get("subject", "(No subject)"),
            "snippet": msg.get("bodyPreview", "")[:300],
            "to": to_name,
            "to_email": to_addr,
            "account": label,
            "date": date_str,
        })

    return threads, None


# ── Haiku classification + scoring ───────────────────────────────────────────

CLASSIFY_PROMPT = """Analyze these email threads to build a voice/tone profile.
For each thread, return a JSON array with one object:
{
  "thread_id": "<id>",
  "category": "<investor|strategic_partner|client|vendor|legal|government|prospect|media|other>",
  "formality": <1-5>,
  "energy": <1-5>,
  "directness": <1-5>,
  "we_vs_i": "<we|i|mixed>",
  "uses_numbers": <true|false>,
  "greeting_style": "<formal|casual|none>"
}

Business context (use to map audiences to the right verticals):
%(context)s

Category definitions:
- investor: LPs, VCs, lenders, family offices, capital partners, equity funds
- strategic_partner: JV partners, co-developers, referral partners, advisors
- client: paying customers across the user's product and service lines
- vendor: contractors, consultants, service providers, suppliers
- legal: attorneys, counsel, law firms, compliance/regulatory counsel
- government: utilities, grid operators, permitting, city/county/regulatory
- prospect: cold outreach, first-touch, early-stage exploration
- media: press, journalists, conference organizers, publications
- other: anything that doesn't fit the above

Score definitions:
- formality: 1=very casual ("hey"), 5=very formal ("Dear Mr. X / Regards")
- energy: 1=flat/measured, 5=highly enthusiastic (exclamation marks, momentum language)
- directness: 1=soft/hedgy (lots of context, soft close), 5=direct CTA (short context, explicit ask)
- we_vs_i: which pronoun dominates
- uses_numbers: true if any specific data point, metric, or precise figure appears
- greeting_style: formal (Dear/Hello [full name]), casual (Hi/Hey [first name]), none (no greeting)

Threads:
"""


def classify_and_score_batch(threads):
    """Classify and score a batch of up to 10 threads via the claude CLI."""
    lines = []
    for t in threads:
        lines.append(
            f"thread_id: {t['thread_id']}\n"
            f"to: {t['to']} <{t['to_email']}>\n"
            f"subject: {t['subject']}\n"
            f"snippet: {t['snippet'][:200]}\n"
        )

    prompt = (
        (CLASSIFY_PROMPT % {"context": voice_classifier_context()})
        + "\n---\n".join(lines)
        + "\n\nReturn ONLY the JSON array."
    )

    raw = _claude(prompt, model=HAIKU_MODEL, timeout=120)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


# ── Aggregation ───────────────────────────────────────────────────────────────

def _median(values):
    if not values:
        return 3.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return round((s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2), 1)


def aggregate(threads, scores_map):
    """Group threads by category and compute aggregate tone stats."""
    by_cat = {}

    for t in threads:
        score = scores_map.get(t["thread_id"])
        if not score:
            continue
        cat = score.get("category", "other")
        by_cat.setdefault(cat, {"threads": [], "scores": []})
        by_cat[cat]["threads"].append(t)
        by_cat[cat]["scores"].append(score)

    result = {}
    for cat, data in by_cat.items():
        n = len(data["threads"])
        if n < 3:
            continue

        scores = data["scores"]
        formality = _median([s.get("formality") for s in scores if s.get("formality")])
        energy = _median([s.get("energy") for s in scores if s.get("energy")])
        directness = _median([s.get("directness") for s in scores if s.get("directness")])

        we_ct = sum(1 for s in scores if s.get("we_vs_i") == "we")
        i_ct = sum(1 for s in scores if s.get("we_vs_i") == "i")
        dominant_pron = "we" if we_ct >= i_ct else "i"

        pct_numbers = round(sum(1 for s in scores if s.get("uses_numbers")) / n, 2)

        greeting_counts = {}
        for s in scores:
            g = s.get("greeting_style", "none")
            greeting_counts[g] = greeting_counts.get(g, 0) + 1
        dominant_greeting = max(greeting_counts, key=greeting_counts.get)

        # Best examples: pick threads with the longest snippets (most signal)
        examples = sorted(
            [t for t in data["threads"] if t.get("snippet")],
            key=lambda t: len(t["snippet"]),
            reverse=True,
        )[:3]

        result[cat] = {
            "sample_size": n,
            "median_formality": formality,
            "median_energy": energy,
            "median_directness": directness,
            "dominant_we_vs_i": dominant_pron,
            "pct_uses_numbers": pct_numbers,
            "dominant_greeting": dominant_greeting,
            "example_subjects": [t["subject"] for t in data["threads"][:5]],
            "example_snippets": [
                {"subject": t["subject"], "snippet": t["snippet"], "account": t["account"]}
                for t in examples
            ],
        }

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate tone-of-voice profile from sent email.")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (default: 90)")
    parser.add_argument("--corpus", action="store_true", help="Also emit the raw deduped thread corpus (used by /voice-bootstrap for device extraction)")
    parser.add_argument("--voice-file", default=str(voice_guide_path()), help="Path to write the voice profile JSON to (defaults to the configured voice guide path)")
    args = parser.parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    errors = []

    # Persona descriptor available to downstream consumers / prompts.
    persona = user_bio_descriptor()

    print(f"Fetching {args.days} days of sent email since {cutoff}...", file=sys.stderr)

    all_threads = []
    n_accts = len(GOOGLE_ACCOUNTS) + len(MICROSOFT_ACCOUNTS)
    with ThreadPoolExecutor(max_workers=max(2, n_accts)) as pool:
        futures = {}
        for a in GOOGLE_ACCOUNTS:
            futures[a["label"]] = pool.submit(fetch_gmail_sent, a["label"], a["email"], cutoff)
        for a in MICROSOFT_ACCOUNTS:
            futures[a["label"]] = pool.submit(fetch_outlook_sent, a["label"], a["sent_folder_id"], cutoff)
        for label, fut in futures.items():
            threads, err = fut.result()
            all_threads.extend(threads)
            if err:
                errors.append(f"{label}: {err}")

    print(f"Fetched {len(all_threads)} threads raw.", file=sys.stderr)

    # Deduplicate cross-account threads by (subject, recipient)
    seen_keys = set()
    deduped = []
    for t in all_threads:
        key = (t["subject"].lower().strip(), t["to_email"].lower())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(t)

    deduped.sort(key=lambda t: t.get("date", ""), reverse=True)
    print(f"After dedup: {len(deduped)} threads. Classifying...", file=sys.stderr)

    scores_map = {}
    batch_size = 10
    for i in range(0, len(deduped), batch_size):
        batch = deduped[i : i + batch_size]
        results = classify_and_score_batch(batch)
        for r in results:
            if r and r.get("thread_id"):
                scores_map[r["thread_id"]] = r
        print(f"  Classified {min(i + batch_size, len(deduped))}/{len(deduped)}", file=sys.stderr)

    by_category = aggregate(deduped, scores_map)

    output = {
        "meta": resolved_meta(),
        "generated": datetime.now(timezone.utc).isoformat(),
        "persona": persona,
        "window_days": args.days,
        "total_threads": len(deduped),
        "classified": len(scores_map),
        "by_category": by_category,
        "errors": errors,
    }

    if args.corpus:
        output["corpus"] = [
            {
                "subject": t["subject"],
                "snippet": t["snippet"],
                "to": t["to"],
                "account": t["account"],
                "category": (scores_map.get(t["thread_id"]) or {}).get("category", "other"),
            }
            for t in deduped
        ]

    rendered = json.dumps(output, indent=2, default=str)

    if args.voice_file:
        try:
            from pathlib import Path
            path = Path(args.voice_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            print(f"Wrote voice profile to {path}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to write voice file: {e}", file=sys.stderr)

    print(rendered)


if __name__ == "__main__":
    main()
