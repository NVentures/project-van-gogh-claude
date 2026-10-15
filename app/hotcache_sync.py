#!/usr/bin/env python3
"""hotcache_sync.py - shared hotcache write-back for morning-coffee + afternoon-tea.

Keeps the active-threads deal metadata honest from the mail both briefings
already pull, so the stage section cannot silently rot (a deal tagged
stage=dead while it is actively negotiating, on a hotcache that is days stale).
Three jobs, all SAFE-direction only:

  A. refresh `last_contact=` to the most-recent matched mail date.
  B. REVIVAL: a deal tagged stage=dead with mail NEWER than its recorded
     last_contact (within RECENT_DAYS) flips dead->active and raises an alert.
  C. staleness guard: report days since the `updated:` frontmatter date, and
     always bump it to today.

It NEVER auto-marks a live deal dead (kill/pass detection stays a human-confirmed
alert in afternoon_tea.detect_status_changes), and NEVER revives a deal flagged
`no_revive=1`, a dead parent whose dependent workstreams (e.g. a financing or PPA
that survived an acquisition pass) are still moving. Revival also requires mail
STRICTLY NEWER than the recorded last_contact, so a deal a human just marked dead
(last_contact set to that day) cannot be resurrected by pre-death mail.

Config-driven: the hotcache path and active-threads heading come from
config_loader, and the generic-token set is extended with the user's own name,
internal team names, and business names so the match gate generalizes per install.
Stdlib + config_loader only.
"""
import os
import re
from datetime import datetime

try:
    from config_loader import (
        businesses,
        hotcache_active_threads_heading,
        hotcache_path,
        internal_team_names,
        user_name_variants,
        user_self_entities,
    )
except Exception:  # keep the module importable in a bare test env
    hotcache_path = None
    hotcache_active_threads_heading = None
    businesses = internal_team_names = user_name_variants = user_self_entities = lambda: []

STALE_DAYS = 3        # banner if the hotcache `updated:` date is older than this
RECENT_DAYS = 7       # a dead deal only revives on mail newer than this window

# Base generic tokens that collide across unrelated threads. A lone shared one is
# not a match (require a distinctive token of len>=4, or two shared tokens).
# Person and company names are NOT hardcoded here; they come from config below,
# so the gate generalizes to any user.
_BASE_GENERIC = {
    "loan", "deal", "term", "sheet", "call", "the", "and", "for", "project",
    "projects", "energy", "power", "capital", "partners", "group", "fund",
    "update", "meeting", "follow", "review", "questions", "agreement", "draft",
    "site", "land", "team", "new", "you", "your", "our", "with", "from",
    "buyer", "passed", "dead", "strategy", "proposal", "program", "response",
    "expansion", "acquisition", "infrastructure", "solutions", "data", "center",
    "intro", "engagement", "personal", "requested", "invitation", "updated",
    "signature", "checklist", "summary", "comments", "closing", "report",
    "equity", "guaranty", "control", "estoppels", "exhibits", "schedules",
    # Meeting-title / subject topic words: descriptive words that recur across
    # unrelated meetings and threads, so a lone one is never a deal-identity
    # match. Without these a Granola meeting cross-ref over-resolves on titles
    # like "Agreement Discussion" or "Compliance Check-In".
    "compliance", "check", "checkin", "discussion", "sync", "introduction",
    "kickoff", "alert", "event", "estimate", "estimates", "geotech",
    # universally common first names: a lone first-name overlap is not a deal
    # match (deal threads are named by company/deal, not person).
    "john", "mike", "michael", "chris", "christopher", "matt", "matthew",
    "james", "robert", "william", "david", "dave", "mark", "ryan", "ben",
    "dan", "danny", "steve", "tom", "tim", "sarah", "maria", "anna", "alex",
    "sam", "andrew", "spencer",
}

# Free email providers: a sender's provider domain is not a distinctive org handle.
_EMAIL_PROVIDERS = {
    "gmail", "outlook", "hotmail", "yahoo", "icloud", "live", "aol", "proton",
    "protonmail", "msn", "me", "mac", "mail",
}

_EMAIL_RE = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+")
_DEAL_META_RE = re.compile(r"<!--\s*deal:\s*(.*?)\s*-->")
_UPDATED_RE = re.compile(r"^updated:\s*(\d{4}-\d{2}-\d{2})\s*$")

_GENERIC_CACHE = None


def _tokens(text):
    return set(re.findall(r"\b[a-z]{3,}\b", text.lower()))


def _generic():
    """Base generic set extended with config-derived name tokens (user, team,
    self-entities, business names). Cached; degrades to the base set if config
    is unavailable (e.g. a bare unit-test environment)."""
    global _GENERIC_CACHE
    if _GENERIC_CACHE is not None:
        return _GENERIC_CACHE
    gen = set(_BASE_GENERIC)
    try:
        for s in user_name_variants():
            gen |= _tokens(s)
        for s in internal_team_names():
            gen |= _tokens(s)
        for s in user_self_entities():
            gen |= _tokens(s)
        for b in businesses():
            gen |= _tokens(b.get("display_name", ""))
    except Exception:
        pass
    _GENERIC_CACHE = gen
    return gen


def match_deal(party_text, headings):
    """Return the deal heading party_text belongs to, or None. Distinctive-token
    tiering (newsletter-immune): a single shared generic token never qualifies;
    needs one distinctive token of len>=4 or two distinctive shared tokens. Email
    addresses reduce to their second-level domain (a free provider is ignored);
    the local part is dropped."""
    gen = _generic()
    low = party_text.lower()
    who = {t for t in _tokens(_EMAIL_RE.sub(" ", low)) if t not in gen}
    for em in _EMAIL_RE.findall(low):
        dom = em.split("@")[-1]
        sld = dom.split(".")[-2] if dom.count(".") >= 1 else dom
        if sld and sld not in _EMAIL_PROVIDERS:
            who.add(sld)
    for heading in headings:
        shared = ({t for t in _tokens(heading) if t not in gen}) & who
        if any(len(t) >= 4 for t in shared):
            return heading
        if len(shared) >= 2:
            return heading
    return None


def parse_deals(lines, active_threads_heading):
    """Return [{heading, meta_idx, fields{}}] for each ### deal under the
    configured active-threads section. fields preserves source key order."""
    deals = []
    in_active = False
    current = None
    head_prefix = f"## {active_threads_heading}"
    for i, line in enumerate(lines):
        if line.startswith(head_prefix):
            in_active = True
            continue
        if in_active and line.startswith("## "):
            in_active = False
        if not in_active:
            continue
        h = re.match(r"^### (.+)", line)
        if h:
            current = {"heading": h.group(1).strip(), "meta_idx": None, "fields": {}}
            deals.append(current)
            continue
        m = _DEAL_META_RE.search(line)
        if m and current is not None and current["meta_idx"] is None:
            fields = {}
            for kv in m.group(1).split():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    fields[k] = v
            current["meta_idx"] = i
            current["fields"] = fields
    return deals


def _render_meta(fields):
    inner = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"<!-- deal: {inner} -->\n"


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes")


def sync_hotcache(mail_events, today_str, path=None, active_threads_heading=None,
                  write=True):
    """Refresh last_contact, revive dead-but-active deals, bump the staleness date.

    mail_events: iterable of {"date": "YYYY-MM-DD", "party": "<name> <email> <subject>"}.
    path / active_threads_heading default to the config values. Returns a change
    report; writes the file in place when write=True and anything changed. An empty
    mail_events still runs the staleness guard (bumps `updated:`).
    """
    if path is None:
        path = str(hotcache_path()) if hotcache_path else ""
    if active_threads_heading is None:
        active_threads_heading = (
            hotcache_active_threads_heading() if hotcache_active_threads_heading
            else "Active Threads"
        )
    result = {
        "last_contact_updates": [], "revivals": [], "matched_events": 0,
        "stale_days": 0, "stale": False, "updated_bumped": False,
    }
    if not path or not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    try:
        today = datetime.strptime(today_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return result

    # C: staleness from the frontmatter `updated:` line, then bump to today.
    for i, line in enumerate(lines[:15]):
        m = _UPDATED_RE.match(line)
        if not m:
            continue
        try:
            upd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            result["stale_days"] = (today - upd).days
            result["stale"] = result["stale_days"] > STALE_DAYS
        except ValueError:
            pass
        if m.group(1) != today_str:
            lines[i] = f"updated: {today_str}\n"
            result["updated_bumped"] = True
        break

    deals = parse_deals(lines, active_threads_heading)
    headings = [d["heading"] for d in deals]

    # Most-recent matched mail date per deal heading.
    latest = {}
    for ev in mail_events:
        try:
            d = datetime.strptime(ev.get("date", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        heading = match_deal(ev.get("party", ""), headings)
        if not heading:
            continue
        result["matched_events"] += 1
        if heading not in latest or d > latest[heading]:
            latest[heading] = d

    for deal in deals:
        if deal["meta_idx"] is None or deal["heading"] not in latest:
            continue
        newest = latest[deal["heading"]]
        fields = deal["fields"]
        old_lc = None
        if fields.get("last_contact"):
            try:
                old_lc = datetime.strptime(fields["last_contact"], "%Y-%m-%d").date()
            except ValueError:
                old_lc = None

        # B: revival, computed against the OLD last_contact, before refresh.
        revived = False
        if (fields.get("stage") == "dead"
                and not _truthy(fields.get("no_revive", ""))
                and (old_lc is None or newest > old_lc)
                and (today - newest).days <= RECENT_DAYS):
            fields["stage"] = "active"
            revived = True
            result["revivals"].append({
                "deal": deal["heading"],
                "newest_mail": newest.isoformat(),
                "was_last_contact": old_lc.isoformat() if old_lc else None,
            })

        # A: refresh last_contact forward only.
        changed = revived
        if old_lc is None or newest > old_lc:
            result["last_contact_updates"].append({
                "deal": deal["heading"],
                "from": old_lc.isoformat() if old_lc else None,
                "to": newest.isoformat(),
            })
            fields["last_contact"] = newest.isoformat()
            changed = True

        if changed:
            lines[deal["meta_idx"]] = _render_meta(fields)

    if write and (result["updated_bumped"] or result["last_contact_updates"]
                  or result["revivals"]):
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return result
