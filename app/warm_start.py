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
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_loader
import contact_capture
import hotcache_sync
import meeting_ingest
from platform_compat import NO_WINDOW

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


def _read_sidecar():
    path = config_loader.full_sidecar_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── scan ─────────────────────────────────────────────────────────────────────

def start_scan(since=60):
    """Spawn week_review detached so the priorities interview can overlap it."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "week_review.py")
    cmd = [sys.executable, script, "--since", str(since), "--no-extensions"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return proc.pid


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


def apply(proposal, deal_idx=None, entity_idx=None, today=None):
    """Write the ticked rows. An empty index list writes nothing, by design."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    deals = proposal.get("deals") or []
    entities = proposal.get("entities") or []
    picked_deals = [deals[i] for i in (deal_idx or []) if 0 <= i < len(deals)]
    picked_entities = [entities[i] for i in (entity_idx or [])
                       if 0 <= i < len(entities)]

    written = {"entities": 0, "deals": 0}

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
        existing = _existing_headings()
        blocks = []
        for row in picked_deals:
            party = f"{row.get('name','')} {row['email']} {row.get('subject','')}"
            if hotcache_sync.match_deal(party, existing):
                continue
            blocks.append(render_deal_block(row))
            existing.append(row["heading"])
        if blocks:
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

def run_ingest(limit=10):
    """Fill the meeting backlog, then link entity pages to what it filed.

    `--auto` infers a business tag per meeting rather than taking one flag for
    the batch, which matters because each apply writes entity pages the NEXT
    meeting resolves against: a tag computed up front would be stale by the
    second meeting. The backfill runs after, since it reads the source pages
    ingest has just written.

    Never raises: a backlog that fails to file costs the vault its history,
    not the install.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "meeting_ingest.py")
    out = {"ingested": False, "backfilled": False, "errors": []}
    try:
        r = subprocess.run(
            [sys.executable, script, "--meeting", "uningested", "--apply",
             "--auto", "--stub-attendees", "--limit", str(limit)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=3600, creationflags=NO_WINDOW)
        out["ingested"] = r.returncode == 0
        out["ingest_tail"] = (r.stdout or r.stderr or "")[-2000:]
        if r.returncode != 0:
            out["errors"].append(f"ingest exited {r.returncode}")
    except Exception as exc:                                    # noqa: BLE001
        out["errors"].append(f"ingest: {type(exc).__name__}")
        return out

    try:
        r = subprocess.run([sys.executable, script, "--backfill"],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=900, creationflags=NO_WINDOW)
        out["backfilled"] = r.returncode == 0
        if r.returncode != 0:
            out["errors"].append(f"backfill exited {r.returncode}")
    except Exception as exc:                                    # noqa: BLE001
        out["errors"].append(f"backfill: {type(exc).__name__}")
    return out


# ── verify ───────────────────────────────────────────────────────────────────

def verify(sidecar, today):
    """Re-propose against the vault as it now is. Anything left is new.

    This is the honest idempotency check: it asks the vault, not the state
    file, so a write that silently did not land shows up as a candidate that
    will not go away.
    """
    import week_review
    fresh = propose(sidecar, today,
                    existing_headings=_existing_headings(),
                    active_emails=week_review.load_active_thread_emails())
    return {"new_deals": len(fresh["deals"]),
            "new_entities": len(fresh["entities"])}


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
    ap_prio = sub.add_parser("priorities")
    ap_prio.add_argument("--set", required=True,
                         help='JSON: {"tag": ["priority", ...]}')
    ap_ing = sub.add_parser("ingest")
    ap_ing.add_argument("--limit", type=int, default=10)
    sub.add_parser("verify")
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

    sidecar = _read_sidecar()
    if sidecar is None:
        print(json.dumps({"error": "No scan output yet. Run `warm_start.py scan` "
                                   "and wait for it to finish."}))
        sys.exit(1)

    if args.cmd == "propose":
        import week_review
        proposal = propose(sidecar, today,
                           existing_headings=_existing_headings(),
                           active_emails=week_review.load_active_thread_emails())
        (stage / "proposal.json").write_text(
            json.dumps(proposal, indent=2), encoding="utf-8")
        proposal["summary_md"] = _summary_md(proposal)
        print(json.dumps(proposal, indent=2))
        return

    if args.cmd == "apply":
        try:
            proposal = json.loads((stage / "proposal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(json.dumps({"error": "No staged proposal. Run `propose` first."}))
            sys.exit(1)

        def _idx(raw):
            return [int(p) for p in raw.split(",") if p.strip().isdigit()]

        written = apply(proposal, _idx(args.deals), _idx(args.entities), today)
        print(json.dumps(written, indent=2))
        return

    if args.cmd == "verify":
        print(json.dumps(verify(sidecar, today), indent=2))


if __name__ == "__main__":
    main()
