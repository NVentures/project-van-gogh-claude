#!/usr/bin/env python3
"""
completion_scan.py — Detect open items that look done, for confirm-then-checkoff.

The agent runs --since to get candidates with evidence, presents them for one-tap
confirmation, then pipes the confirmed subset to --mark-done. Nothing is written
without explicit confirmation — the human-in-the-loop gate that keeps a fuzzy
signal from silently burying a live item in the strategic files.

Modes:
  --since N      Read-only scan. Pulls recent sent mail (Tier 1: OAuth clients;
                 Tier 2: --input connector data) + notetaker meetings over the last
                 N days (omit = since the last briefing), reads open items from the
                 week file (action items, project tasks, deals), scores each for a
                 completion signal, prints candidates JSON.
  --mark-done    Reads a JSON array of confirmed candidates on stdin and flips each
                 to [x] across its canonical files (week file + hotcache / Obsidian
                 project page / Obsidian week file). Idempotent.

Detection is recall-first but corroborated (confirm-gated): an item surfaces on a
distinctive org handle, a full name, a name+topic overlap, or MIN_SHARED content
words — never on a lone common first name. Reuses morning_coffee + afternoon_tea.
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kpi_events  # noqa: E402
import morning_coffee as mc  # noqa: E402
import notetaker
import afternoon_tea as at  # noqa: E402
from config_loader import afternoon_tea_md_path, morning_coffee_md_path, user_tz  # noqa: E402
from data_sources import is_tier1, load_input, missing_input_error  # noqa: E402

# Date-window math computes against the user's configured zone.
USER_TZ = user_tz()

# An action item / task "looks done" when a single email/meeting clears one of the
# bars in detect_item_candidates (org handle, full name, name+topic, or MIN_SHARED
# content words). A bare single identity token — usually just a common first name
# like "Mark" — is deliberately NOT enough: that thin who-match leaked noise (e.g. a
# "Harbor Land Projects" email checking off an unrelated "Aurora exit messaging" task
# because both name "Mark"). Confirm-gated, so once a bar is cleared it leans toward
# recall; a false positive is one ignored checkbox, a false negative a manual hunt.
MIN_SHARED = 2
# Jaccard floor for matching a confirmed task back to its Obsidian project-page line.
PROJECT_MATCH_THRESHOLD = 0.8

# Generic filler stripped before scoring so matches hinge on meaningful words.
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "your", "our", "you", "into",
    "out", "all", "any", "are", "was", "has", "had", "will", "can", "not",
    "re", "fw", "fwd", "via", "per", "new", "next", "week", "day", "ask", "add", "set",
    "run", "get", "call", "meeting", "update", "check", "follow", "followup", "prep",
    "confirm", "schedule", "review", "send", "plan", "discuss", "share", "intro",
    "make", "need", "energy",
}

# Generic mail domains that are not a meaningful "org" signal for who-matching.
EMAIL_PROVIDERS = {
    "gmail", "outlook", "hotmail", "yahoo", "icloud", "live", "aol", "proton",
    "protonmail", "msn", "me", "mac", "mail",
}


def _tok(text):
    """Tokens of 3+ alphanumerics, lowercased. Digit-inclusive so '900 acre'
    keeps '900' (afternoon_tea._tokens drops digits)."""
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _distinctive(text):
    """Meaningful tokens of text (generic filler removed)."""
    return {t for t in _tok(text) if t not in STOPWORDS}


def _org_tokens(text):
    """Second-level domain token(s) from an email address in text (TLD dropped),
    so 'sam@northwind.com' yields {'northwind'}, the org name an item may reference."""
    out = set()
    m = re.search(r"@([\w.-]+)", text)
    if m:
        for part in m.group(1).lower().split(".")[:-1]:
            if len(part) >= 3 and part not in EMAIL_PROVIDERS:
                out.add(part)
    return out


# ── Signal assembly ───────────────────────────────────────────────────────────

def _signal_sources(sent, meetings):
    """One entry per activity: {'subj', 'id', 'org', 'ev'}.
      subj : subject/title tokens — the topic.
      id   : person-identity tokens — display-name words + email local-part words.
             A lone id token is often just a common first name, so an id match only
             qualifies an item with corroboration (a second id token = full name,
             or a subject overlap). See detect_item_candidates.
      org  : second-level domain token(s), TLD dropped — a distinctive org handle
             (e.g. 'kestrel'). Matching an org named in an item stands on its own.
    Matching id/org/subj to an item means you reached the person/org or hit the
    topic named in it."""
    sources = []
    for m in sent:
        email = m.get("to_email", "") or ""
        sources.append({
            "subj": _tok(m.get("subject", "")),
            "id": _tok(m.get("to", "")) | _tok(email.split("@")[0]),
            "org": _org_tokens(email),
            "ev": f'sent "{m.get("subject", "")}" to {m.get("to") or email or "?"}',
        })
    for mtg in meetings:
        ids, orgs = set(), set()
        for a in mtg.get("attendees", []):
            ids |= _tok(a.split("@")[0])
            orgs |= _org_tokens(a)
        sources.append({
            "subj": _tok(mtg.get("title", "")),
            "id": ids,
            "org": orgs,
            "ev": f'met: {mtg.get("title", "")}',
        })
    return sources


def detect_item_candidates(tasks, sources):
    """Action-item / project-task candidates. A single email/meeting flags an item
    when it clears one of these bars (strong -> weak):
      1. org      : the item names an org you contacted (full & org)        — distinctive handle, stands alone
      2. full name: two+ identity tokens overlap (len(full & id) >= 2)      — e.g. first + last name
      3. name+topic: one identity token AND a subject token overlap         — reached someone named in it about a shared topic
      4. content  : MIN_SHARED subject tokens overlap (len(full & subj))    — topic items with no named person
    A bare single identity-token hit (just a common first name like "Mark") is NOT
    enough on its own — that thin who-match was the noise this gate used to leak.
    Confirm-gated, so once a bar is cleared it leans toward recall."""
    cands = []
    for t in tasks:
        if t["checked"]:
            continue
        full = _distinctive(t["task"])
        if not full:
            continue
        best_ev, best_score, best_n = None, -1, 0
        for s in sources:
            id_hit = full & s["id"]
            org_hit = full & s["org"]
            subj_hit = full & s["subj"]
            qualifies = (
                org_hit                          # 1. distinctive org handle
                or len(id_hit) >= 2              # 2. full-name match
                or (id_hit and subj_hit)         # 3. named person + shared topic
                or len(subj_hit) >= MIN_SHARED   # 4. content overlap
            )
            if not qualifies:
                continue
            shared = id_hit | org_hit | subj_hit
            # Rank org / full-name / content matches above thin name+topic ones.
            score = (3 if org_hit else 0) + len(shared)
            if score > best_score:
                best_score, best_n, best_ev = score, len(shared), s["ev"]
        if best_ev is not None:
            cands.append({
                "kind": "action_item" if t["project"] == "Action Items" else "task",
                "text": t["task"],
                "tag": t["project"],
                "evidence": best_ev,
                "matched_words": best_n,
            })
    return cands


def detect_deal_candidates(deals, sent, meetings):
    """Deal candidates: the counterparty email appears in recent sent recipients
    or meeting attendees. Email is unique, so no fuzzy name matching."""
    by_email = {}
    for m in sent:
        e = (m.get("to_email") or "").lower()
        if e:
            by_email.setdefault(e, []).append(m)
    meeting_ev = {}
    for mtg in meetings:
        for a in mtg.get("attendees", []):
            am = re.search(r"[\w.+-]+@[\w.-]+", a)
            if am:
                meeting_ev.setdefault(am.group(0).lower(), f'met: {mtg.get("title", "")}')

    cands = []
    for d in deals:
        if d.get("checked") or d.get("snoozed"):
            continue
        email = (d.get("email") or "").lower()
        if not email:
            continue
        ev = None
        if email in by_email:
            deal_toks = _tok(d.get("subject", ""))
            best = max(by_email[email],
                       key=lambda m: len(_tok(m.get("subject", "")) & deal_toks))
            ev = f'replied to {best.get("to") or email}: "{best.get("subject", "")}"'
        elif email in meeting_ev:
            ev = meeting_ev[email]
        if ev:
            cands.append({
                "kind": "deal",
                "email": email,
                "subject": d["subject"],
                "name": d["name"],
                "source": d["source"],
                "tag": d["section"],
                "evidence": ev,
            })
    return cands


# ── Scan mode ─────────────────────────────────────────────────────────────────

def _auto_since_days():
    """Window = days since the last briefing (morning-coffee / afternoon-tea), so
    each run only re-examines activity you haven't reviewed yet. Falls back to the
    week file, then today-only."""
    briefings = [str(morning_coffee_md_path()), str(afternoon_tea_md_path())]
    existing = [p for p in briefings if os.path.exists(p)]
    anchor = max(existing, key=os.path.getmtime) if existing else mc.WORKSPACE_WEEK_MD
    if not os.path.exists(anchor):
        return 1
    return mc.compute_since_days(anchor)


def _fetch_recent_meetings(today_str, since_str, errors):
    """Notetaker meetings in the scan window (titles + attendees are all the
    matchers read, so no summaries). Fail-open: an error costs the meeting
    evidence, never the scan — but it lands in errors, not silence.
    """
    try:
        return notetaker.fetch_meetings(today_str, since_str)
    except Exception as e:
        errors.append(f"{notetaker.safe_display_name()}: {e}")
        return []


def run_scan(since_days, input_path=None):
    if since_days is None:
        since_days = _auto_since_days()
    today = datetime.now(timezone.utc).astimezone(USER_TZ)
    today_str = today.strftime("%Y-%m-%d")
    since_str = (today - timedelta(days=max(0, since_days - 1))).strftime("%Y-%m-%d")
    errors = []
    sent = []

    if input_path:
        # Tier 2: sent mail from pre-fetched connector data (its own window)
        try:
            sent = load_input(input_path).get("sent_today", [])
        except Exception as e:
            errors.append(f"Input load: {e}")
    else:
        # Tier 1: direct OAuth fetch, windowed since the last briefing
        n_accts = len(at.GOOGLE_ACCOUNTS) + len(at.MICROSOFT_ACCOUNTS)
        with ThreadPoolExecutor(max_workers=max(2, n_accts)) as pool:
            futs = {}
            for a in at.GOOGLE_ACCOUNTS:
                futs[a["label"]] = pool.submit(
                    at.fetch_gmail_sent_today, at.google_client(a["label"]),
                    a["email"], a["label"], today_str, since_str)
            for a in at.MICROSOFT_ACCOUNTS:
                futs[a["label"]] = pool.submit(
                    at.fetch_outlook_sent_today, a["label"], a["sent_folder_id"], today_str, since_str)
            for label, f in futs.items():
                try:
                    sent.extend(f.result())
                except Exception as e:
                    errors.append(f"Sent mail ({label}): {e}")

    meetings = _fetch_recent_meetings(today_str, since_str, errors)

    if os.path.exists(mc.WORKSPACE_WEEK_MD):
        _, deals, tasks, _ = mc.parse_week_file(mc.WORKSPACE_WEEK_MD)
    else:
        deals, tasks = [], []
        errors.append("week file not found — run /week first")

    sources = _signal_sources(sent, meetings)
    candidates = detect_item_candidates(tasks, sources) + detect_deal_candidates(deals, sent, meetings)
    for i, c in enumerate(candidates, 1):
        c["id"] = i

    print(json.dumps({
        "today": today_str,
        "window_start": since_str,
        "since_days": since_days,
        "sent_count": len(sent),
        "meeting_count": len(meetings),
        "candidates": candidates,
        "errors": errors,
    }, indent=2, default=str))


# ── Mark-done mode ────────────────────────────────────────────────────────────

def _project_line_match(target_text, line_text):
    a = mc._item_tokens(target_text)
    b = mc._item_tokens(line_text)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= PROJECT_MATCH_THRESHOLD


def resolve_front_item(subject, counterparty_email="", tag=None):
    """Turn a front-page item into a candidate `mark_done` can actually match.

    The front page keys an item by (normalized subject, counterparty email).
    `mark_done` matches an action item or task by its RAW week-file text, and a
    deal by counterparty email. Those are different keys, so the briefing page
    cannot hand its own item straight to the marker.

    A deal bridges on email with no guessing. A task has to be found in the week
    file by the same token-overlap rule the project-page match already uses.

    Returns a candidate dict, or None when nothing matched well enough. None is
    the honest answer: these files are the user's own notes, edited by hand, and
    flipping the wrong line is worse than reporting a miss.
    """
    email = (counterparty_email or "").strip().lower()
    if email:
        return {"kind": "deal", "email": email}
    if not os.path.exists(mc.WORKSPACE_WEEK_MD):
        return None
    _lines, _deals, tasks, _ = mc.parse_week_file(mc.WORKSPACE_WEEK_MD)
    best, best_score = None, 0.0
    for t in tasks:
        if t["checked"]:
            continue
        if tag and t["project"] != tag:
            continue
        a, b = mc._item_tokens(subject), mc._item_tokens(t["task"])
        if not a or not b:
            continue
        score = len(a & b) / len(a | b)
        if score > best_score:
            best, best_score = t, score
    if best is None or best_score < PROJECT_MATCH_THRESHOLD:
        return None
    return {"kind": "task", "tag": best["project"], "text": best["task"]}


def mark_done(items):
    """Flip every confirmed item to done across all four surfaces.

    The CLI wraps this; so does the briefing page's tick. Idempotent: only lines
    whose box is still empty are touched.
    """
    result = {"marked": [], "hotcache_synced": [], "project_tasks": [], "deals": [], "errors": []}

    item_targets = {(c.get("tag"), c.get("text"))
                    for c in items if c.get("kind") in ("action_item", "task")}
    task_items = [c for c in items if c.get("kind") == "task"]
    deal_emails = {(c.get("email") or "").lower() for c in items if c.get("kind") == "deal"}

    # 1. Week file — flip every confirmed action item, task, and deal (stable key)
    if os.path.exists(mc.WORKSPACE_WEEK_MD):
        lines, deals, tasks, _ = mc.parse_week_file(mc.WORKSPACE_WEEK_MD)
        modified = False
        for t in tasks:
            if not t["checked"] and (t["project"], t["task"]) in item_targets:
                mc.mark_done(lines, t["line_number"])
                modified = True
                result["marked"].append(t["task"])
        for d in deals:
            if not d["checked"] and (d["email"] or "").lower() in deal_emails:
                mc.mark_done(lines, d["line_number"])
                modified = True
                result["marked"].append(d["subject"])
        if modified:
            with open(mc.WORKSPACE_WEEK_MD, "w", encoding="utf-8") as f:
                f.writelines(lines)
    else:
        result["errors"].append("week file not found")

    # 2. Action items -> hotcache (reuses the Tier 2 sync of week [x] -> hotcache)
    try:
        result["hotcache_synced"] = mc.sync_hotcache_from_week()
    except Exception as e:
        result["errors"].append(f"Hotcache sync: {e}")

    # 3. Project tasks -> Obsidian project page (fuzzy line match)
    for c in task_items:
        path = mc.OBSIDIAN_PROJECTS.get(c.get("tag"))
        if not path or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            pls = f.readlines()
        for i, l in enumerate(pls):
            m = re.match(r"^(\s*)- \[ \] (.+)$", l.rstrip("\r\n"))
            if m and _project_line_match(c.get("text", ""), m.group(2)):
                pls[i] = l.replace("- [ ]", "- [x]", 1)
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(pls)
                result["project_tasks"].append(c.get("text"))
                break

    # 4. Deals -> Obsidian week file (match by counterparty email)
    if deal_emails:
        wf = mc.find_current_week_file()
        if wf and os.path.exists(wf):
            with open(wf, encoding="utf-8") as f:
                wls = f.readlines()
            changed = False
            for i, l in enumerate(wls):
                dm = mc.DEAL_RE.match(l.rstrip("\r\n"))
                if dm and dm.group(1) == " " and dm.group(5).strip().lower() in deal_emails:
                    wls[i] = l.replace("- [ ]", "- [x]", 1)
                    changed = True
                    result["deals"].append(dm.group(5).strip().lower())
            if changed:
                with open(wf, "w", encoding="utf-8") as f:
                    f.writelines(wls)

    # Tell the scorecard what actually closed. This is the ONLY thing the
    # scorecard counts as done: an item that merely stops appearing in a
    # briefing has not been finished, it has just left the list, and treating
    # the two alike would let a tidy-up read as a productive week.
    _record_closures(items, result)

    return result


def _record_closures(items, result) -> None:
    """One content-free row naming the keys that were just ticked off.

    Keyed exactly as the front page keys an item, through the one shared key
    function, so a close can be matched to the moment that item first appeared.
    Fails open: a scorecard row is never worth failing a check-off over.
    """
    try:
        marked = {str(t) for t in (result.get("marked") or [])}
        marked |= {str(t) for t in (result.get("project_tasks") or [])}
        emails = {str(e).lower() for e in (result.get("deals") or [])}
        keys = set()
        for cand in items or []:
            if not isinstance(cand, dict):
                continue
            email = str(cand.get("email") or "").lower()
            text = str(cand.get("text") or "")
            if email and email in emails:
                keys.add(kpi_events.item_key("", email))
            elif text and text in marked:
                keys.add(kpi_events.item_key(text, ""))
        if keys:
            kpi_events.record(kpi_events.CLOSED, keys=sorted(keys))
    except Exception:                                           # noqa: BLE001
        pass


def run_mark_done():
    raw = sys.stdin.read().strip()
    items = json.loads(raw) if raw else []
    if isinstance(items, dict):
        items = items.get("candidates", [])
    print(json.dumps(mark_done(items), indent=2, default=str))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=int, default=None,
                        help="Days back to scan, inclusive of today (omit = since "
                             "the last briefing; pass 1 for today only)")
    parser.add_argument("--mark-done", action="store_true",
                        help="Read confirmed candidates JSON on stdin and flip them to [x]")
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON (skips OAuth fetch)")
    args = parser.parse_args()

    if args.mark_done:
        run_mark_done()
        return
    if not args.input and not is_tier1():
        missing_input_error("completion_scan.py")
        return
    run_scan(args.since, args.input)


if __name__ == "__main__":
    main()
