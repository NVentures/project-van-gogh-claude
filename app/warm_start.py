#!/usr/bin/env python3
"""warm_start.py: fill a fresh vault before the first briefing reads it.

A new install scaffolds an empty vault and the first Morning Coffee renders
against it. Every feature that makes a briefing worth reading sits downstream
of two things nothing ever creates automatically: a deal thread in
`wiki/hotcache.md`, and an entity page for a counterparty. Without them there
are no overdue alerts, no kill or re-engage detection, no context pack, so
every reply is drafted from the thread alone, and the relationship radar
reports nothing.

This module proposes both from the user's own sent mail, and applies only what
the human ticks. The confirm gate is the whole design: a false deal (a dead
thread shown as live, or a quiet one shown as moving) is worse than a miss, and
the human is present at install, so asking costs one screen and buys precision
no heuristic can.

STAGES, and why the order is load-bearing:

    scan        week_review over 60 days. It is the source for everything
                below, so it runs first, detached, while the user answers the
                priorities questions.
    priorities  one question per business. Without these, priority_judge never
                runs and nothing on the page carries a reason.
    propose     candidates, staged to logs/ as JSON plus a table to read.
    apply       entity pages FIRST (a deal body wikilinks them), then hotcache
                threads, then the follow-ups ledger.
    ingest      meeting backlog, which infers its business tag per meeting from
                the entity pages apply just wrote.
    verify      re-propose and assert nothing new, which is what proves the
                pass is idempotent rather than merely believed to be.

Everything here is re-runnable: every writer checks for its own output first,
so a second run adds nothing and `verify` says so.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_loader
import contact_capture
import fanout
import hotcache_sync
import meeting_ingest
import user_state
from platform_compat import NO_WINDOW

# ── Where the install keeps its working files ────────────────────────────────
#
# Under the state dir, not the vault: the scan may start before the vault is
# finished, and nothing here is something a vault sync should carry.

def setup_dir():
    return user_state.state_dir() / "setup"


def scan_path():
    """The saved scan snapshot (week_review.py --save-scan)."""
    return setup_dir() / "scan.json"


def scan_meta_path():
    """When the scan started and which process is running it."""
    return setup_dir() / "scan-meta.json"


def staged_dir():
    """Prefetched meeting summaries before filing."""
    return setup_dir() / "staged"


def _job_logs_dir():
    d = config_loader.logs_dir() / "warm-start"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:                                           # noqa: BLE001
        return False
    return True

# Sections the proposal reads, in order of how strongly they imply a live
# thread. cold_monitor is deliberately absent: 7 to 14 days quiet with no
# reply is the weakest signal in the scan, and seeding it as a deal would put
# the reader's whole backlog into hotcache as if they were managing it.
DEAL_SECTIONS = ("waiting_on_user", "inbox_pending", "cold_urgent")

# How many of each to offer. The cap is about the confirm screen, not the
# vault: a table nobody reads gets ticked wholesale, which defeats the gate.
MAX_DEALS = 15
MAX_ENTITIES = 25

# A reply the user owes gets a due date three days out. A cold thread gets
# none: inventing a deadline for a thread the counterparty has gone quiet on
# would manufacture urgency the reader never agreed to.
NEXT_ACTION_DAYS = 3


def _stage_dir(today):
    d = config_loader.logs_dir() / "warm-start" / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def newest_proposal_path():
    """The most recently staged proposal.json across every date folder.

    `propose` stages under today's date. A person who stops at the deals
    question and comes back the next morning (the case the day-one
    reminder exists for) must find yesterday's proposal, not an error.
    """
    root = config_loader.logs_dir() / "warm-start"
    try:
        found = [p for p in root.glob("*/proposal.json") if p.is_file()]
    except OSError:
        return None
    if not found:
        return None
    return max(found, key=lambda p: (p.stat().st_mtime, p.parent.name))


NEED_PROPOSE = {"error": "No staged proposal. Run `propose` first.",
                "need_propose": True,
                "hint": "run `warm_start.py propose`, then apply or verify again"}


def load_proposal(explicit=None):
    """(proposal, path) from `--proposal PATH` or the newest staged one.

    Returns (None, None) when nothing usable exists; the caller prints
    NEED_PROPOSE. An explicit path that cannot be read is also "nothing
    usable": a wrong path must not silently fall back to another proposal,
    since the picked indices belong to a specific file.
    """
    path = Path(explicit) if explicit else newest_proposal_path()
    if path is None:
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except (OSError, ValueError):
        return None, None


def _read_sidecar():
    path = config_loader.full_sidecar_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── scan ─────────────────────────────────────────────────────────────────────

_SCAN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "week_review.py")

# How much of a crashed job's last line is carried into a reply.
_REASON_MAX = 200


def _last_log_line(path) -> str:
    """The last non-empty line of a job's log, for a reply's `reason`.

    A crashed child's traceback ends with the exception's own line, which is
    the one sentence that says why ("RuntimeError: Missing refresh token:
    ..."). Truncated so a reply stays a reply.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:_REASON_MAX]
    return ""


def start_scan(since=60):
    """Spawn week_review detached so the priorities interview can overlap it.

    The scan also saves its snapshot (`--save-scan`) so the first briefings
    can render from it instead of reading the same sixty days of mail again,
    and records when it started so `wait-scan` knows which sidecar is new.
    The child's stdout and stderr go to `<logs>/warm-start/scan.log` (the
    prefetch's neighbour), so a scan that dies at start leaves its traceback
    where `wait-scan` can quote it.
    """
    # -u: unbuffered, so the log is in time order and a crash ends on the
    # exception's own line rather than on stdout flushed after it.
    cmd = [sys.executable, "-u", _SCAN_SCRIPT, "--since", str(since), "--no-extensions",
           "--save-scan", str(scan_path())]
    started = datetime.now(timezone.utc)
    log = _job_logs_dir() / "scan.log"
    with open(log, "ab") as handle:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=handle,
                                stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    try:
        _write_json(scan_meta_path(), {
            "started": started.isoformat(timespec="seconds"),
            "started_ts": started.timestamp(),
            "pid": proc.pid, "since": since, "save_scan": str(scan_path()),
            "log": str(log)})
    except OSError:
        pass
    return proc.pid


def _scan_counts(snapshot):
    sections = (snapshot or {}).get("sections") or {}
    return {k: len(sections.get(k) or []) for k in
            ("waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor",
             "calendar")}


def wait_scan(timeout=600, poll=2.0, sleep=time.sleep, now=time.time):
    """Block until the scan started by `scan` has written its snapshot.

    Ready means the snapshot file is newer than the recorded start. The
    scan process dying first ends the wait at once with that reason, and the
    timeout ends it with the counts still unknown; neither is reported as
    "ready", because a first briefing rendered from a stale or missing scan
    is a confidently empty page.
    """
    meta = _read_json(scan_meta_path()) or {}
    started_ts = float(meta.get("started_ts") or 0)
    pid = meta.get("pid")
    snap = scan_path()
    sidecar = config_loader.full_sidecar_path()
    deadline = now() + max(0, timeout)
    waited = 0.0
    while True:
        # Ready means BOTH files are newer than the start: the snapshot the
        # first briefings render from AND the sidecar `propose` reads. The
        # scan writes the sidecar first and the snapshot last, but a reader
        # must not lean on that order: it checks both.
        snap_fresh = _fresh(snap, started_ts)
        sidecar_fresh = _fresh(sidecar, started_ts)
        if snap_fresh and sidecar_fresh:
            snapshot = _read_json(snap)
            if snapshot is not None:
                return {"ready": True, "scan": str(snap), "sidecar": str(sidecar),
                        "waited_s": round(waited, 1),
                        "since": snapshot.get("since_days"),
                        "counts": _scan_counts(snapshot)}
        if pid and not _pid_alive(pid):
            # A dead scan process with no fresh results is a failed scan,
            # not a slow one; waiting the full timeout tells nobody anything.
            # Its log's last line says why, which "exited" alone never did.
            log = meta.get("log") or str(_job_logs_dir() / "scan.log")
            missing = [name for name, ok in (("snapshot", snap_fresh),
                                             ("sidecar", sidecar_fresh)) if not ok]
            return {"ready": False, "error": "the scan exited without writing "
                                             f"its {' and '.join(missing) or 'results'}",
                    "scan": str(snap), "sidecar": str(sidecar),
                    "waited_s": round(waited, 1), "pid": pid,
                    "reason": _last_log_line(log), "log": log}
        if now() >= deadline:
            return {"ready": False, "timeout": True, "scan": str(snap),
                    "sidecar": str(sidecar), "waited_s": round(waited, 1),
                    "have": {"snapshot": snap_fresh, "sidecar": sidecar_fresh}}
        sleep(poll)
        waited += poll


def _fresh(path, started_ts) -> bool:
    try:
        return path.exists() and path.stat().st_mtime >= started_ts
    except OSError:
        return False


def sidecar_not_ready():
    """Why `propose` cannot run yet, or "" when the sidecar is this scan's.

    "Not yet" is a different answer from "no deals": a sidecar that is
    missing, or older than the start recorded by the last `scan`, means the
    scan has not finished writing, and the caller should wait, not conclude.
    """
    path = config_loader.full_sidecar_path()
    if not path.exists():
        return "the scan has not written its results yet"
    meta = _read_json(scan_meta_path()) or {}
    started_ts = meta.get("started_ts")
    if started_ts and not _fresh(path, float(started_ts)):
        return "the scan has not written its results yet"
    return ""


# ── propose ──────────────────────────────────────────────────────────────────

def _existing_headings():
    """Deal headings already in hotcache, for dedup via match_deal."""
    try:
        path = config_loader.hotcache_path()
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return []
    heading = config_loader.hotcache_active_threads_heading()
    try:
        return [d["heading"] for d in hotcache_sync.parse_deals(lines, heading)]
    except Exception:                                           # noqa: BLE001
        return []


def _last_contact(entry, today):
    """The date of the last touch on this thread, as YYYY-MM-DD."""
    age = entry.get("reply_age_days")
    if age is None:
        age = entry.get("age_days") or 0
    try:
        base = datetime.strptime(today, "%Y-%m-%d")
    except (TypeError, ValueError):
        return today
    return (base - timedelta(days=int(age))).strftime("%Y-%m-%d")


def _business_for(entry):
    text = " ".join(str(entry.get(k) or "") for k in
                    ("subject", "counterparty_name", "counterparty_email"))
    try:
        tag = config_loader.business_tag_for_text(text)
        if tag:
            return tag
        # One real bucket means there is nothing to disambiguate.
        tags = [b.get("tag") for b in config_loader.businesses()
                if b.get("tag") and b.get("tag") != "personal"]
        if len(tags) == 1:
            return tags[0]
    except Exception:                                           # noqa: BLE001
        pass
    return ""


def _skip(entry, seen_emails):
    """True when this thread must never become a deal."""
    email = (entry.get("counterparty_email") or "").strip().lower()
    if not email or email in seen_emails:
        return True
    if entry.get("is_internal"):
        return True
    try:
        if contact_capture.is_robot_address(email):
            return True
        if email in config_loader.account_emails_lower():
            return True
        if email in config_loader.internal_team_emails():
            return True
    except Exception:                                           # noqa: BLE001
        pass
    return False


def propose(sidecar, today, existing_headings=None, active_emails=None):
    """Candidate deals and entity pages from the scan. Writes nothing."""
    existing_headings = existing_headings or []
    active_emails = {e.lower() for e in (active_emails or set())}
    deals, entities, seen = [], [], set()
    counts = {}

    for section in DEAL_SECTIONS:
        for entry in sidecar.get(section) or []:
            email = (entry.get("counterparty_email") or "").strip().lower()
            if email:
                counts[email] = counts.get(email, 0) + 1
            if _skip(entry, seen):
                continue
            if email in active_emails:
                continue
            name = (entry.get("counterparty_name") or "").strip()
            subject = (entry.get("subject") or "").strip()
            party = f"{name} {email} {subject}"
            if hotcache_sync.match_deal(party, existing_headings):
                continue
            seen.add(email)
            org = ""
            try:
                org = contact_capture.org_from_domain(email) or ""
            except Exception:                                   # noqa: BLE001
                org = ""
            waiting = section in ("waiting_on_user", "inbox_pending")
            last = _last_contact(entry, today)
            deals.append({
                "heading": _heading_for(org or name, subject, email),
                "name": name,
                "email": email,
                "org": org,
                "subject": subject,
                "section": section,
                "account": entry.get("source") or "",
                "business": _business_for(entry),
                "stage": "active" if waiting else "outreach",
                "last_contact": last,
                "next_action_due": _due(today) if waiting else "",
            })
            if len(deals) >= MAX_DEALS:
                break
        if len(deals) >= MAX_DEALS:
            break

    # Entity pages: everyone behind a candidate deal, then anyone the user has
    # more than one thread with. One thread is an exchange, not a relationship.
    wanted = {d["email"]: d for d in deals}
    for section in DEAL_SECTIONS + ("cold_monitor",):
        for entry in sidecar.get(section) or []:
            email = (entry.get("counterparty_email") or "").strip().lower()
            if not email or email in wanted:
                continue
            if _skip(entry, set()):
                continue
            if counts.get(email, 0) < 2:
                continue
            wanted[email] = {
                "name": (entry.get("counterparty_name") or "").strip(),
                "email": email,
                "org": _safe_org(email),
                "subject": (entry.get("subject") or "").strip(),
                "business": _business_for(entry),
                "last_contact": _last_contact(entry, today),
            }

    existing_pages = _existing_entity_names()
    for email, row in wanted.items():
        name = row.get("name") or ""
        if not name or len(name.split()) < 2:
            # A bare first name or an address with no display name makes a page
            # nobody can link to and the radar cannot name.
            continue
        if _entity_exists(name, existing_pages):
            continue
        entities.append({
            "name": name,
            "email": email,
            "org": row.get("org") or _safe_org(email),
            "business": row.get("business") or "",
            "threads": counts.get(email, 1),
            "last_contact": row.get("last_contact") or today,
            "last_subject": row.get("subject") or "",
        })
        if len(entities) >= MAX_ENTITIES:
            break

    return {"deals": deals, "entities": entities, "today": today}


def _safe_org(email):
    try:
        return contact_capture.org_from_domain(email) or ""
    except Exception:                                           # noqa: BLE001
        return ""


def _due(today):
    try:
        base = datetime.strptime(today, "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return (base + timedelta(days=NEXT_ACTION_DAYS)).strftime("%Y-%m-%d")


def _heading_for(who, subject, email):
    """`Org: subject`, carrying a token match_deal can resolve later mail on."""
    subj = re.sub(r"^(re|fwd|fw)\s*:\s*", "", subject or "",
                  flags=re.IGNORECASE).strip()
    subj = re.sub(r"\s+", " ", subj)[:60].strip()
    who = (who or "").strip()
    if not who:
        dom = email.split("@")[-1] if "@" in email else ""
        who = dom.split(".")[0] if dom else "Thread"
    return f"{who}: {subj}" if subj else who


def _existing_entity_names():
    try:
        canonical, aliases = meeting_ingest.list_existing_pages()
        return {str(n).lower() for n in list(canonical) + list(aliases)}
    except Exception:                                           # noqa: BLE001
        return set()


def _entity_exists(name, existing):
    return name.lower() in existing


# ── apply ────────────────────────────────────────────────────────────────────

def render_entity_page(row, today):
    """An entity page whose `updated:` is the real last-contact date.

    relationship_radar reads `updated:` when `sources` carries no dated page,
    so stamping today would make every seeded contact look freshly touched and
    the radar would stay silent about exactly the people it exists to surface.
    """
    business = row.get("business") or ""
    tags = [t for t in (business, "person", "email-seeded") if t]
    org_line = f"org: {row['org']}\n" if row.get("org") else ""
    threads = row.get("threads") or 1
    plural = "thread" if threads == 1 else "threads"
    subject = row.get("last_subject") or ""
    last = f' Last subject: "{subject}".' if subject else ""
    return (
        f"---\n"
        f"type: entity\n"
        f"title: {row['name']}\n"
        f"created: {today}\n"
        f"updated: {row.get('last_contact') or today}\n"
        f"sources: []\n"
        f"email: {row['email']}\n"
        f"{org_line}"
        f"business: {business}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"---\n\n"
        f"# {row['name']}\n\n"
        f"Seeded from your mail at setup: {threads} {plural} in the last 60 "
        f"days.{last} Nothing here was read from a meeting yet.\n"
    )


def render_deal_block(row):
    """A hotcache thread carrying BOTH the email and a wikilink.

    Both are load-bearing and they serve different readers:
    `week_review.load_active_thread_emails` scans this section for ADDRESSES to
    decide what counts as a live deal, and
    `relationship_radar.get_hotcache_active_names` scans it for `[[Name]]`.
    A block with only one of them fixes only half the product.
    """
    meta = [f"stage={row['stage']}", f"last_contact={row['last_contact']}"]
    if row.get("next_action_due"):
        meta.append(f"next_action_due={row['next_action_due']}")
    name = row.get("name") or row["email"]
    account = f" via {row['account']}" if row.get("account") else ""
    subject = row.get("subject") or ""
    lines = [
        f"### {row['heading']}",
        "<!-- deal: " + " ".join(meta) + " -->",
        f"- [[{name}]] {row['email']}{account}",
    ]
    if subject:
        lines.append(f'- Last: "{subject}" ({row["last_contact"]})')
    lines.append("- Seeded from your mail at setup; edit or delete freely.")
    return "\n".join(lines) + "\n"


def insert_deals(text, blocks, heading):
    """Insert deal blocks under the Active Threads heading, after any prose."""
    if not blocks:
        return text
    lines = text.splitlines(keepends=True)
    head_re = re.compile(r"^##\s+" + re.escape(heading) + r"\s*$")
    idx = None
    for i, line in enumerate(lines):
        if head_re.match(line.rstrip("\r\n")):
            idx = i
            break
    body = "\n" + "\n".join(blocks)
    if idx is None:
        # No section at all: append one rather than silently dropping the work.
        return text.rstrip("\r\n") + f"\n\n## {heading}\n{body}\n"
    # Past the heading's own comment block, to the first following section or
    # the first existing deal, whichever comes first.
    j = idx + 1
    while j < len(lines) and not lines[j].startswith("## ") \
            and not lines[j].startswith("### "):
        j += 1
    return "".join(lines[:j]) + body.lstrip("\n") + "\n" + "".join(lines[j:])


# The scaffold's sample deal, inside the Active Threads comment. It parses as
# a deal (parse_deals reads any "### " line), so a first briefing could show
# it; apply removes it the first time a real deal is written.
SAMPLE_DEAL_HEADING = "Thread Name"


def _party(row):
    return f"{row.get('name', '')} {row.get('email', '')} {row.get('subject', '')}"


def _shared_distinctive(party_text, heading):
    """The distinctive tokens a party and a heading share (match_deal's own
    arithmetic: generic tokens out, an email reduced to its domain's name)."""
    hs = hotcache_sync
    gen = hs._generic()
    low = party_text.lower()
    who = {t for t in hs._tokens(hs._EMAIL_RE.sub(" ", low)) if t not in gen}
    for em in hs._EMAIL_RE.findall(low):
        dom = em.split("@")[-1]
        sld = dom.split(".")[-2] if dom.count(".") >= 1 else dom
        if sld and sld not in hs._EMAIL_PROVIDERS:
            who.add(sld)
    return {t for t in hs._tokens(heading) if t not in gen} & who


def strong_match(party_text, headings):
    """The heading `party_text` is the same deal as, or None.

    Stricter than match_deal on purpose: a ticked deal is a human's answer,
    and one shared token is not evidence against it. "Bearpeakpower:
    Waterloo" and "Gridprojectsco: Waterloo Project" share only a place
    word and are two deals; a match here needs two shared distinctive
    tokens AND match_deal's own agreement.
    """
    for heading in headings:
        if (len(_shared_distinctive(party_text, heading)) >= 2
                and hotcache_sync.match_deal(party_text, [heading])):
            return heading
    return None


def _drop_sample_deal(text):
    """Remove the scaffold's "### Thread Name" block, nothing else."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() != f"### {SAMPLE_DEAL_HEADING}":
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() \
                and not lines[j].startswith(("### ", "## ", "-->")):
            j += 1
        return "".join(lines[:i] + lines[j:])
    return text


def apply(proposal, deal_idx=None, entity_idx=None, today=None):
    """Write the ticked rows. An empty index list writes nothing, by design.

    A ticked deal is written unless its heading is already in the hot cache
    exactly, or strong_match finds it against a deal that existed BEFORE
    this apply started. Never against another deal written in this same
    batch: the person ticked both, and a shared place word is not a reason
    to drop one of them silently. Every outcome is in the result:
    `written` (headings) and `skipped` ({heading, reason, matched}).
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    deals = proposal.get("deals") or []
    entities = proposal.get("entities") or []
    picked_deals = [deals[i] for i in (deal_idx or []) if 0 <= i < len(deals)]
    picked_entities = [entities[i] for i in (entity_idx or [])
                       if 0 <= i < len(entities)]

    written = {"entities": 0, "deals": 0, "written": [], "skipped": [],
               "sample_removed": False}

    # Entities first: a deal block wikilinks the person, so the page has to
    # exist or the link is red on the reader's very first look at hotcache.
    entities_dir = config_loader.entities_dir()
    for row in picked_entities:
        path = entities_dir / f"{meeting_ingest.safe_filename(row['name'])}.md"
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_entity_page(row, today), encoding="utf-8")
        written["entities"] += 1

    if picked_deals:
        hot = config_loader.hotcache_path()
        try:
            text = hot.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # What was there BEFORE this apply: the only thing a pick is
        # deduplicated against. The scaffold's sample is not a deal.
        before = [h for h in _existing_headings() if h != SAMPLE_DEAL_HEADING]
        had_sample = SAMPLE_DEAL_HEADING in _existing_headings()
        exact = set(before)
        blocks = []
        for row in picked_deals:
            head = row["heading"]
            if head in exact:
                written["skipped"].append({"heading": head, "matched": head,
                                           "reason": "already in the hot cache"})
                continue
            if head in written["written"]:
                written["skipped"].append({"heading": head, "matched": head,
                                           "reason": "ticked twice in this batch"})
                continue
            matched = strong_match(_party(row), before)
            if matched:
                written["skipped"].append({
                    "heading": head, "matched": matched,
                    "reason": "the same deal as one already in the hot cache "
                              "(two shared distinctive tokens)"})
                continue
            blocks.append(render_deal_block(row))
            written["written"].append(head)
        if blocks:
            if had_sample:
                text = _drop_sample_deal(text)
                written["sample_removed"] = True
            heading = config_loader.hotcache_active_threads_heading()
            hot.parent.mkdir(parents=True, exist_ok=True)
            hot.write_text(insert_deals(text, blocks, heading), encoding="utf-8")
            written["deals"] = len(blocks)

    return written


# ── priorities ───────────────────────────────────────────────────────────────

def save_priorities(picks, today=None):
    """Write stated priorities into config.json, backing it up first.

    `picks` is {business_tag: [name, ...]}. Deduped case-insensitively against
    what the bucket already holds, so re-running the interview never doubles a
    priority the user gave last time.

    This is the single highest-leverage field in the whole config: with an
    empty `priorities[]` the judge never runs, nothing on the front page
    carries a reason, and the ranking is pure recency forever.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    path = config_loader.van_gogh_root() / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"config unreadable: {type(exc).__name__}"}

    added = {}
    for entry in config.get("businesses") or []:
        names = [str(n).strip() for n in (picks.get(entry.get("tag")) or [])
                 if str(n).strip()]
        if not names:
            continue
        have = {str(p.get("name", "")).lower()
                for p in entry.get("priorities") or []}
        new = [{"name": n, "detail": "", "added": today}
               for n in names if n.lower() not in have]
        if new:
            entry.setdefault("priorities", []).extend(new)
            added[entry.get("tag")] = [p["name"] for p in new]

    if added:
        backup = path.with_suffix(".json.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    return {"added": added, "backed_up": bool(added)}


# ── ingest ───────────────────────────────────────────────────────────────────

_INGEST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "meeting_ingest.py")

_WROTE_RE = re.compile(r"^\u2713 Wrote (.+?)\s*$", re.MULTILINE)


def _run_backfill(out):
    try:
        r = subprocess.run([sys.executable, _INGEST_SCRIPT, "--backfill"],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=900, creationflags=NO_WINDOW)
        out["backfilled"] = r.returncode == 0
        if r.returncode != 0:
            out["errors"].append(f"backfill exited {r.returncode}")
    except Exception as exc:                                    # noqa: BLE001
        out["errors"].append(f"backfill: {type(exc).__name__}")
    return out


def run_ingest(limit=10):
    """Fill the meeting backlog, then link entity pages to what it filed.

    `--auto` infers a business tag per meeting rather than taking one flag for
    the batch, which matters because each apply writes entity pages the NEXT
    meeting resolves against: a tag computed up front would be stale by the
    second meeting. The backfill runs after, since it reads the source pages
    ingest has just written.

    Under VAN_GOGH_SERIAL=1 this is the one-at-a-time nightly path, exactly
    as it always ran. Otherwise the summaries are read three at a time first
    (`--prefetch`) and filed serially from the staging dir (`--staged`), which
    leaves the vault as the serial path would and takes a third of the time.

    Never raises: a backlog that fails to file costs the vault its history,
    not the install.
    """
    out = {"ingested": False, "backfilled": False, "errors": []}
    if fanout.serial():
        try:
            r = subprocess.run(
                [sys.executable, _INGEST_SCRIPT, "--meeting", "uningested", "--apply",
                 "--auto", "--stub-attendees", "--limit", str(limit)],
                capture_output=True, text=True, encoding="utf-8",
                timeout=3600, creationflags=NO_WINDOW)
            out["ingested"] = r.returncode == 0
            out["ingest_tail"] = (r.stdout or r.stderr or "")[-2000:]
            if r.returncode != 0:
                out["errors"].append(f"ingest exited {r.returncode}")
        except Exception as exc:                                # noqa: BLE001
            out["errors"].append(f"ingest: {type(exc).__name__}")
            return out
        return _run_backfill(out)

    d = staged_dir()
    try:
        r = subprocess.run(
            [sys.executable, _INGEST_SCRIPT, "--prefetch", "--limit", str(limit),
             "--staged-dir", str(d)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=3600, creationflags=NO_WINDOW)
        if r.returncode != 0:
            out["errors"].append(f"prefetch exited {r.returncode}")
            out["ingest_tail"] = (r.stdout or r.stderr or "")[-2000:]
            return out
    except Exception as exc:                                    # noqa: BLE001
        out["errors"].append(f"prefetch: {type(exc).__name__}")
        return out
    finish = finish_ingest(backfill=False)
    out.update({k: finish.get(k) for k in
                ("filed", "failed", "deferred", "summary_only", "pages",
                 "degraded", "counts")})
    out["ingested"] = bool(finish.get("ingested"))
    out["ingest_tail"] = finish.get("ingest_tail", "")
    out["errors"].extend(finish.get("errors") or [])
    if not out["ingested"]:
        return out
    return _run_backfill(out)


def start_ingest(limit=10, days=None):
    """Start the summary prefetch detached and return at once.

    The child outlives this process (start_new_session, stdio to the logs
    dir), so the install can ask the next question while ten transcripts are
    read three at a time. `ingest-finish` files what it staged.
    """
    d = staged_dir()
    d.mkdir(parents=True, exist_ok=True)
    log = _job_logs_dir() / "prefetch.log"
    cmd = [sys.executable, "-u", _INGEST_SCRIPT, "--prefetch", "--limit", str(limit),
           "--staged-dir", str(d)] + (["--since-days", str(days)] if days else [])
    with open(log, "ab") as handle:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=handle,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                creationflags=NO_WINDOW)
    _write_json(d / "prefetch.json", {
        "pid": proc.pid, "limit": limit, "days": days, "log": str(log),
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return {"started": True, "pid": proc.pid, "staged_dir": str(d),
            "log": str(log), "limit": limit}


def finish_ingest(backfill=True):
    """File the staged summaries serially, then backfill. Reports counts.

    `filed` names the pages written this call; `failed` carries the
    prefetch's failures and any page the filing step refused; `deferred` is
    what a usage cap left unread, and when that is non-empty the result is
    `degraded: {"reason": "usage_cap", "deferred": N}` so the install's
    checklist keeps the box pending rather than ticking it.
    """
    d = staged_dir()
    out = {"ingested": False, "backfilled": False, "filed": [], "failed": [],
           "deferred": [], "summary_only": [], "pages": [], "degraded": None,
           "errors": [], "staged_dir": str(d)}
    manifest = _read_json(d / "manifest.json")
    if manifest is None:
        job = _read_json(d / "prefetch.json") or {}
        if _pid_alive(job.get("pid")):
            out["pending"] = True
            out["errors"].append("the prefetch is still running")
        else:
            out["errors"].append("no staged summaries: run ingest-start first")
            if job.get("pid"):
                # The prefetch was started and died without a manifest: its
                # log's last line is the reason.
                out["reason"] = _last_log_line(job.get("log"))
                out["log"] = job.get("log") or ""
        out["counts"] = {"filed": 0, "failed": 0, "deferred": 0, "summary_only": 0}
        return out

    if manifest.get("error"):
        out["errors"].append(manifest["error"])
    out["failed"] = [{"id": r.get("id"), "title": r.get("title"),
                      "error": r.get("error")} for r in manifest.get("failed") or []]
    out["deferred"] = [{"id": r.get("id"), "title": r.get("title")}
                       for r in manifest.get("deferred") or []]
    # Filed, but from the notetaker's own summary: the read ran out of the
    # install's time budget (queued for a full read on the next meeting
    # ingest) or the model could not read it. Passed through so the
    # orchestrator can say so in plain words.
    out["summary_only"] = [{"id": r.get("id"), "title": r.get("title"),
                            "reason": r.get("reason"),
                            "over_budget": bool(r.get("over_budget"))}
                           for r in manifest.get("summary_only") or []]
    out["budget_s"] = manifest.get("budget_s") or 0

    if manifest.get("filed"):
        try:
            r = subprocess.run(
                [sys.executable, _INGEST_SCRIPT, "--staged", str(d), "--apply",
                 "--auto", "--stub-attendees"],
                capture_output=True, text=True, encoding="utf-8",
                timeout=3600, creationflags=NO_WINDOW)
            out["ingested"] = r.returncode == 0
            out["ingest_tail"] = (r.stdout or r.stderr or "")[-2000:]
            pages = [os.path.splitext(os.path.basename(m.strip()))[0]
                     for m in _WROTE_RE.findall(r.stdout or "")]
            out["pages"] = pages
            out["filed"] = list(pages)
            if r.returncode != 0:
                out["errors"].append(f"staged filing exited {r.returncode}")
        except Exception as exc:                                # noqa: BLE001
            out["errors"].append(f"staged filing: {type(exc).__name__}")
    else:
        # Nothing staged to file is still a finished step when nothing was
        # deferred: no meetings, or every one already on disk.
        out["ingested"] = not out["deferred"]

    if out["deferred"]:
        out["degraded"] = {"reason": "usage_cap", "deferred": len(out["deferred"])}
    out["counts"] = {"filed": len(out["filed"]), "failed": len(out["failed"]),
                     "deferred": len(out["deferred"]),
                     "summary_only": len(out["summary_only"])}
    if backfill and out["ingested"]:
        _run_backfill(out)
    return out


# ── first briefing ───────────────────────────────────────────────────────────

BRIEFINGS = ("week", "morning-coffee", "afternoon-tea")

# Week's own window on a first run: resolve_since("auto") floors at 14 days
# when no week.md exists yet. Passed explicitly so the reused 60 day scan is
# cut to what a fresh Week would have read, never rendered whole. Morning
# Coffee passes its own 14 itself; Afternoon Tea windows internally.
WEEK_FIRST_RUN_SINCE = 14


def pick_briefing(now_local):
    """Morning Coffee before noon local, Afternoon Tea from noon on."""
    return "morning-coffee" if now_local.hour < 12 else "afternoon-tea"


def _local_now(now=None):
    tz = config_loader.user_tz()
    if now is None:
        return datetime.now(timezone.utc).astimezone(tz)
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def first_briefing(which, now=None, run=subprocess.run):
    """Prepare one briefing's data from the saved scan. No mail is fetched.

    Runs the briefing's own script with `--reuse-scan`, so the data is what
    that script would have produced after its fetch, and writes the JSON to
    `<setup>/briefing-<name>.json`, which is the path the skill renders from.
    `which="auto"` picks Morning Coffee before noon local and Afternoon Tea
    after, in the timezone config.json names.
    """
    snap = scan_path()
    if not snap.exists():
        return {"error": "No saved scan. Run `warm_start.py scan` and "
                         "`wait-scan` first.", "scan": str(snap)}
    local = _local_now(now)
    if which == "auto":
        which = pick_briefing(local)
    if which not in BRIEFINGS:
        return {"error": f"unknown briefing {which!r}"}
    app = os.path.dirname(os.path.abspath(__file__))
    script = {"week": "week_review.py", "morning-coffee": "morning_coffee.py",
              "afternoon-tea": "afternoon_tea.py"}[which]
    cmd = [sys.executable, os.path.join(app, script), "--reuse-scan", str(snap)]
    if which == "week":
        cmd += ["--since", str(WEEK_FIRST_RUN_SINCE), "--no-extensions"]
    out = {"which": which, "scan": str(snap), "local_time": local.strftime("%H:%M"),
           "data": "", "errors": []}
    try:
        r = run(cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=1800, creationflags=NO_WINDOW)
    except Exception as exc:                                    # noqa: BLE001
        out["errors"].append(f"{which}: {type(exc).__name__}")
        return out
    if r.returncode != 0:
        out["errors"].append(f"{which} exited {r.returncode}: "
                             f"{(r.stderr or '')[-500:]}")
        return out
    try:
        data = json.loads(r.stdout)
    except ValueError:
        out["errors"].append(f"{which} printed no JSON")
        return out
    path = setup_dir() / f"briefing-{which}.json"
    _write_json(path, data)
    out["data"] = str(path)
    if which == "week":
        out["sidecar"] = str(config_loader.full_sidecar_path())
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    out["counts"] = counts
    if which == "afternoon-tea":
        out["open_items"] = len(((data.get("action_items") or {}).get("open")) or [])
    out["errors"].extend(data.get("errors") or [])
    return out


# ── verify ───────────────────────────────────────────────────────────────────

def verify(sidecar, today, picked=None, proposal=None):
    """Re-propose against the vault as it now is. Anything left is new.

    This is the honest idempotency check: it asks the vault, not the state
    file, so a write that silently did not land shows up as a candidate that
    will not go away. `new_deals` cannot witness "every ticked deal landed",
    though: the proposal is capped, and with a subset ticked the cap refills
    with other candidates. So `picked` (indices into `proposal`, the same
    ones apply took) adds `landed` and `missing` by checking each picked
    heading against the hot cache as it is now: exactly, or by strong_match.
    `missing` is empty after a successful apply of those picks.
    """
    import week_review
    now = _existing_headings()
    fresh = propose(sidecar, today,
                    existing_headings=now,
                    active_emails=week_review.load_active_thread_emails())
    out = {"new_deals": len(fresh["deals"]),
           "new_entities": len(fresh["entities"])}
    if picked is not None:
        deals = (proposal or {}).get("deals") or []
        rows = [deals[i] for i in picked if 0 <= i < len(deals)]
        landed, missing = [], []
        real = [h for h in now if h != SAMPLE_DEAL_HEADING]
        for row in rows:
            head = row["heading"]
            if head in real or strong_match(_party(row), real):
                landed.append(head)
            else:
                missing.append(head)
        out.update({"picked": len(rows), "landed": landed, "missing": missing})
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def _summary_md(proposal):
    out = []
    deals = proposal.get("deals") or []
    if deals:
        out.append("Threads that look like live deals:\n")
        for i, d in enumerate(deals):
            due = f", reply due {d['next_action_due']}" if d["next_action_due"] else ""
            biz = d["business"] or "?"
            out.append(f"  [{i}] {d['heading']}")
            out.append(f"      {d['name'] or d['email']} | {biz} | "
                       f"{d['stage']} | last contact {d['last_contact']}{due}")
    else:
        out.append("No candidate deals found in the scan.")
    ents = proposal.get("entities") or []
    out.append("")
    if ents:
        out.append("People worth a page:\n")
        for i, e in enumerate(ents):
            org = f" ({e['org']})" if e["org"] else ""
            n = e["threads"]
            out.append(f"  [{i}] {e['name']}{org} | {n} "
                       f"{'thread' if n == 1 else 'threads'} | "
                       f"last {e['last_contact']}")
    else:
        out.append("No new people to add.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Fill a fresh vault before the first briefing.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").add_argument("--since", type=int, default=60)
    sub.add_parser("propose")
    ap_apply = sub.add_parser("apply")
    ap_apply.add_argument("--deals", default="")
    ap_apply.add_argument("--entities", default="")
    ap_apply.add_argument("--proposal", default="",
                          help="the staged proposal.json the indices refer to "
                               "(default: the newest staged one, any date)")
    ap_prio = sub.add_parser("priorities")
    ap_prio.add_argument("--set", required=True,
                         help='JSON: {"tag": ["priority", ...]}')
    ap_ing = sub.add_parser("ingest")
    ap_ing.add_argument("--limit", type=int, default=10)
    ap_verify = sub.add_parser("verify")
    ap_verify.add_argument("--picked", default="",
                           help="deal indices apply was given, to confirm each landed")
    ap_verify.add_argument("--proposal", default="",
                           help="the staged proposal.json --picked refers to "
                                "(default: the newest staged one, any date)")
    ap_wait = sub.add_parser("wait-scan")
    ap_wait.add_argument("--timeout", type=float, default=600)
    ap_istart = sub.add_parser("ingest-start")
    ap_istart.add_argument("--limit", type=int, default=10)
    ap_istart.add_argument("--days", type=int, default=None,
                           help="every meeting from this many days back, at most --limit")
    sub.add_parser("ingest-finish")
    ap_first = sub.add_parser("first-briefing")
    ap_first.add_argument("--which", required=True,
                          choices=BRIEFINGS + ("auto",))
    ap_first.add_argument("--now", default=None,
                          help="ISO clock to decide --which auto (tests)")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    stage = _stage_dir(today)

    if args.cmd == "scan":
        pid = start_scan(args.since)
        print(json.dumps({"started": True, "pid": pid, "since": args.since}))
        return

    # Neither of these reads the scan, so they run while it is still going:
    # the interview is the whole point of overlapping them.
    if args.cmd == "priorities":
        try:
            picks = json.loads(args.set)
        except ValueError:
            print(json.dumps({"error": "--set must be JSON"}))
            sys.exit(1)
        print(json.dumps(save_priorities(picks, today), indent=2))
        return

    if args.cmd == "ingest":
        print(json.dumps(run_ingest(args.limit), indent=2))
        return

    if args.cmd == "wait-scan":
        result = wait_scan(timeout=args.timeout)
        print(json.dumps(result, indent=2))
        if not result.get("ready"):
            sys.exit(1)
        return

    if args.cmd == "ingest-start":
        print(json.dumps(start_ingest(args.limit, args.days), indent=2))
        return

    if args.cmd == "ingest-finish":
        print(json.dumps(finish_ingest(), indent=2))
        return

    if args.cmd == "first-briefing":
        result = first_briefing(args.which, now=args.now)
        print(json.dumps(result, indent=2))
        if result.get("error"):
            sys.exit(1)
        return

    not_ready = sidecar_not_ready()
    sidecar = None if not_ready else _read_sidecar()
    if sidecar is None:
        print(json.dumps({"error": not_ready or "the scan has not written its results yet",
                          "ready": False,
                          "sidecar": str(config_loader.full_sidecar_path()),
                          "scan": str(scan_path()),
                          "hint": "run `warm_start.py wait-scan` and try again"}))
        sys.exit(1)

    if args.cmd == "propose":
        import week_review
        proposal = propose(sidecar, today,
                           existing_headings=_existing_headings(),
                           active_emails=week_review.load_active_thread_emails())
        proposal_path = stage / "proposal.json"
        proposal_path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
        proposal["summary_md"] = _summary_md(proposal)
        proposal["proposal_path"] = str(proposal_path)
        print(json.dumps(proposal, indent=2))
        return

    if args.cmd == "apply":
        proposal, proposal_path = load_proposal(args.proposal)
        if proposal is None:
            print(json.dumps(NEED_PROPOSE))
            sys.exit(1)

        def _idx(raw):
            return [int(p) for p in raw.split(",") if p.strip().isdigit()]

        written = apply(proposal, _idx(args.deals), _idx(args.entities), today)
        # The indices refer to THIS file; say which one it was.
        written["proposal_path"] = str(proposal_path)
        print(json.dumps(written, indent=2))
        return

    if args.cmd == "verify":
        picked, proposal, proposal_path = None, None, None
        if args.picked.strip():
            picked = [int(p) for p in args.picked.split(",") if p.strip().isdigit()]
            proposal, proposal_path = load_proposal(args.proposal)
            if proposal is None:
                print(json.dumps(NEED_PROPOSE))
                sys.exit(1)
        result = verify(sidecar, today, picked=picked, proposal=proposal)
        if proposal_path is not None:
            result["proposal_path"] = str(proposal_path)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
