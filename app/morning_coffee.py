#!/usr/bin/env python3
"""
morning_coffee.py — Daily check-in data pull.

Reads the current week file, re-runs the email scan to detect what got
resolved overnight, updates Obsidian checkboxes in-place, and outputs a
JSON blob for Claude to render as the /morning-coffee briefing.

Usage:
    python3 app/morning_coffee.py [--since 7]
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from platform_compat import NO_WINDOW
import extensions
import kpi_events
import workbench_data
from config_loader import (
    account_labels,
    follow_ups_horizon_days,
    follow_ups_memory_path,
    google_accounts,
    hotcache_path,
    hotcache_action_items_heading,
    morning_coffee_md_path,
    project_pages,
    resolved_meta,
    travel_buffer_minutes,
    travel_enabled,
    user_first_name,
    user_tz,
    weekly_dir,
    workspace_week_md_path,
)
import notetaker
from notetaker import fetch_meetings
import follow_up_radar
from follow_up_radar import collect_nudges as _collect_nudges
from hotcache_sync import sync_hotcache

# Date math (today / snooze comparisons / day rendering) uses the user's zone.
USER_TZ = user_tz()

OBSIDIAN_WEEKLY = str(weekly_dir())
HOTCACHE_PATH = str(hotcache_path())

# Project pages come from config.json businesses[].
OBSIDIAN_PROJECTS = {name: str(path) for name, path in project_pages().items()}

_USER_FIRST = user_first_name()
_ACTION_HEADING = hotcache_action_items_heading()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # repo root — cwd for week_review.py subprocess
WORKSPACE_WEEK_MD = str(workspace_week_md_path())
MORNING_COFFEE_MD = str(morning_coffee_md_path())


# ── Recent meetings (notetaker) ───────────────────────────────────────────────

def last_briefing_date(today_str):
    """Lower bound for the recent-meeting window: the date morning-coffee last
    rendered, so each run picks up calls since the last briefing (and avoids
    overlapping afternoon-tea's same-day coverage). The rendered briefing's
    mtime is the proxy. Falls back to yesterday when no prior briefing exists,
    and never returns a date after today."""
    try:
        mtime = os.path.getmtime(MORNING_COFFEE_MD)
        last = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(USER_TZ).strftime("%Y-%m-%d")
    except OSError:
        last = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return min(last, today_str)


# ── Week file discovery ───────────────────────────────────────────────────────

def parse_hotcache_alerts(today_str):
    """Return overdue/deadline alerts from hotcache <!-- deal: ... --> thread metadata."""
    if not os.path.exists(HOTCACHE_PATH):
        return []
    try:
        text = open(HOTCACHE_PATH, encoding="utf-8").read()
    except OSError:
        return []

    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    alerts = []

    for section in re.split(r"\n(?=### )", text):
        header_m = re.match(r"### (.+)", section)
        if not header_m:
            continue
        meta_m = DEAL_META_RE.search(section)
        if not meta_m:
            continue

        meta = {}
        for pair in meta_m.group(1).split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                meta[k] = v

        flags = []
        if meta.get("next_action_due"):
            try:
                nad = datetime.strptime(meta["next_action_due"], "%Y-%m-%d").date()
                if nad < today:
                    flags.append({"type": "overdue", "days": (today - nad).days, "date": meta["next_action_due"]})
            except ValueError:
                pass

        if meta.get("deadline"):
            try:
                dl = datetime.strptime(meta["deadline"], "%Y-%m-%d").date()
                days_left = (dl - today).days
                if days_left <= 14:
                    flags.append({"type": "deadline", "days_left": days_left, "date": meta["deadline"]})
            except ValueError:
                pass

        if flags:
            alerts.append({
                "thread": header_m.group(1).strip(),
                "stage": meta.get("stage", ""),
                "last_contact": meta.get("last_contact"),
                "flags": flags,
            })

    return alerts


def find_current_week_file():
    """Find the most recent week-*.md within the last 7 days."""
    pattern = os.path.join(OBSIDIAN_WEEKLY, "week-*.md")
    files = glob.glob(pattern)
    if not files:
        return None

    today = datetime.now(timezone.utc).astimezone(USER_TZ).date()
    cutoff = today - timedelta(days=7)

    candidates = []
    for path in files:
        name = os.path.basename(path)
        m = re.match(r"week-(\d{4}-\d{2}-\d{2})\.md", name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff:
            candidates.append((file_date, path))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# ── Week file parsing ─────────────────────────────────────────────────────────

SECTION_HEADINGS = {
    f"## Deals — Waiting on {_USER_FIRST}": "waiting",
    "## Deals — Cold Urgent":               "cold",
    "## Deals — Cold Monitor":              "cold",
    "## Open Tasks by Project":             "tasks",  # legacy
    "## Open Tasks (Obsidian)":             "tasks",  # current
    "## Top Priorities":                    "priorities",
    "## Calendar":                          "calendar",
}

DEAL_META_RE = re.compile(r'<!--\s*deal:\s*(.+?)\s*-->')

# Source label is any configured account label, plus the "Google (X)" form
# week_review emits for non-primary Google calendars.
_SOURCE_LABELS = list(account_labels()) + [f"Google ({a['label']})" for a in google_accounts()]
_LABELS_ALT = "|".join(re.escape(L) for L in _SOURCE_LABELS) or "(?!x)x"
DEAL_RE = re.compile(
    rf"^- \[(.)\] \[({_LABELS_ALT})\] \"(.+?)\" — (.+?) \(([^)]+@[^)]+)\) — (.+)$"
)
TASK_RE = re.compile(
    r"^- \[(.)\] \[(.+?)\] (.+)$"
)
SNOOZE_RE = re.compile(r"\[snooze:(\d{4}-\d{2}-\d{2})\]")
CLASSIFICATION_RE = re.compile(r"<!--\s*urgency:(\S+)\s+intent:(\S+)\s*-->")
MET_RE = re.compile(r"<!--\s*met:(\d{4}-\d{2}-\d{2})\s*-->")
PRIORITY_RE = re.compile(r"^- \[(.)\] \[(.+?)\] \*\*(.+?)\*\*\s*(?:—\s*(.*))?$")
PRIORITY_DATE_RE = re.compile(
    r'\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b'
)


def _priority_mentions_today(title, detail, today_label):
    """Return True if the priority text references today's date or the word TODAY."""
    text = title + " " + (detail or "")
    if "TODAY" in text.upper():
        return True
    for m in PRIORITY_DATE_RE.finditer(text):
        if f"{m.group(1)} {m.group(2)} {m.group(3)}" == today_label:
            return True
    return False


def _calendar_title_match(priority_title, today_calendar):
    """Return True if any today_calendar event shares significant words with priority_title."""
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on", "at",
            "re", "fw", "fwd", "call", "meeting", "update", "check"}
    words = {w.lower() for w in re.findall(r'\b\w{3,}\b', priority_title) if w.lower() not in stop}
    if not words:
        return False
    for evt in today_calendar:
        evt_words = {w.lower() for w in re.findall(r'\b\w{3,}\b', evt.get("title", ""))}
        if words & evt_words:
            return True
    return False


def parse_week_file(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    deals = []       # {line_number, checked, source, subject, name, email, detail, section}
    tasks = []       # {line_number, checked, project, task}
    priorities = []  # {line_number, checked, tag, title, detail}
    current_section = None

    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")

        # Detect section
        for heading, section_key in SECTION_HEADINGS.items():
            if line.startswith(heading):
                current_section = section_key
                break

        if current_section in ("waiting", "cold"):
            m = DEAL_RE.match(line)
            if m:
                checked, source, subject, name, email, detail = m.groups()
                snooze_m = SNOOZE_RE.search(detail)
                snoozed = False
                if snooze_m:
                    snooze_date = datetime.strptime(snooze_m.group(1), "%Y-%m-%d").date()
                    snoozed = snooze_date > datetime.now(timezone.utc).astimezone(USER_TZ).date()
                classification_m = CLASSIFICATION_RE.search(detail)
                urgency = classification_m.group(1) if classification_m else None
                intent = classification_m.group(2) if classification_m else None
                met_m = MET_RE.search(raw)
                met_date = met_m.group(1) if met_m else None
                deals.append({
                    "line_number": i,
                    "checked": checked != " ",
                    "source": source,
                    "subject": subject,
                    "name": name,
                    "email": email.strip().lower(),
                    "detail": detail,
                    "section": current_section,
                    "snoozed": snoozed,
                    "urgency": urgency,
                    "intent": intent,
                    "met_date": met_date,
                })

        if current_section == "tasks":
            m = TASK_RE.match(line)
            if m:
                checked, project, task = m.groups()
                tasks.append({
                    "line_number": i,
                    "checked": checked != " ",
                    "project": project,
                    "task": task.strip(),
                })

        if current_section == "priorities":
            m = PRIORITY_RE.match(line)
            if m:
                checked, tag, title, detail = m.groups()
                priorities.append({
                    "line_number": i,
                    "checked": checked != " ",
                    "tag": tag,
                    "title": title.strip(),
                    "detail": (detail or "").strip(),
                })

    return lines, deals, tasks, priorities


# ── Workspace week.md calendar update ────────────────────────────────────────

_MONTH_ORDER = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def _day_sort_key(day_str):
    # "Mon May 4" -> sort by (month, day)
    parts = day_str.split()
    if len(parts) >= 3:
        return (_MONTH_ORDER.get(parts[1], 0), int(parts[2]))
    return (0, 0)


def _time_sort_key(time_str):
    # Times render as "9:00 AM PDT" (single zone) or "9:00 AM PDT / 12:00 PM EDT"
    # (dual). Match the leading clock time regardless of the zone label/suffix.
    m = re.match(r"\s*(\d{1,2}:\d{2}\s*[AaPp][Mm])", time_str or "")
    if not m:
        return 9999
    try:
        t = datetime.strptime(m.group(1).upper().replace(" ", ""), "%I:%M%p")
        return t.hour * 60 + t.minute
    except Exception:
        return 9999


def update_workspace_calendar(fresh_events):
    """Merge new events from fresh_events into week.md's Calendar table."""
    if not os.path.exists(WORKSPACE_WEEK_MD):
        return 0

    with open(WORKSPACE_WEEK_MD, encoding="utf-8") as f:
        lines = f.readlines()

    # Locate the ## Calendar table
    in_calendar = False
    row_start = None
    row_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "## Calendar":
            in_calendar = True
            continue
        if in_calendar:
            if stripped.startswith("## "):
                row_end = i
                break
            if stripped.startswith("|---"):
                row_start = i + 1
            elif row_start is not None and stripped.startswith("|"):
                row_end = i + 1
    if row_start is None:
        return 0
    if row_end is None:
        row_end = len(lines)

    # Parse existing rows (dedup on day + normalized title)
    existing_rows = []
    existing_keys = set()
    raw_row_count = 0
    for i in range(row_start, row_end):
        stripped = lines[i].strip()
        if not stripped.startswith("|"):
            continue
        raw_row_count += 1
        parts = [p.strip() for p in stripped.split("|")]
        if len(parts) < 5:
            continue
        day, time_, title, source = parts[1], parts[2], parts[3], parts[4]
        key = (day, title.strip().lower())
        if key in existing_keys:
            continue
        existing_rows.append({"day": day, "time": time_, "title": title.strip(), "source": source})
        existing_keys.add(key)

    # Add new events
    added = 0
    for evt in fresh_events:
        key = (evt["day"], evt["title"].strip().lower())
        if key not in existing_keys:
            existing_rows.append({
                "day": evt["day"], "time": evt["time"],
                "title": evt["title"].strip(), "source": evt["source"],
            })
            existing_keys.add(key)
            added += 1

    had_duplicates = raw_row_count > len(existing_rows) - added
    if added == 0 and not had_duplicates:
        return 0

    existing_rows.sort(key=lambda e: (_day_sort_key(e["day"]), _time_sort_key(e["time"])))
    new_table_lines = [
        f"| {r['day']} | {r['time']} | {r['title']} | {r['source']} |\n"
        for r in existing_rows
    ]
    lines[row_start:row_end] = new_table_lines

    with open(WORKSPACE_WEEK_MD, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return added


# ── Fresh email scan ──────────────────────────────────────────────────────────

def run_week_review(since, input_path=None):
    # --no-extensions: this is a data subprocess of the morning briefing;
    # without it a default-scoped extension section would run a second time
    # under the "week" briefing name, doubling any side effects.
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "week_review.py"),
           "--since", str(since), "--week-ahead", "7", "--no-extensions"]
    if input_path:
        cmd.extend(["--input", input_path])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT,
                            creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"week_review.py failed:\n{result.stderr[:500]}")
    return json.loads(result.stdout)


def build_email_sets(fresh):
    waiting  = {e["counterparty_email"].lower(): e for e in fresh["waiting_on_user"]}
    cold     = {e["counterparty_email"].lower(): e for e in fresh["cold_urgent"] + fresh["cold_monitor"]}
    recent   = {e["counterparty_email"].lower(): e for e in fresh.get("recent", [])}
    return waiting, cold, recent


# ── Task resolution check ─────────────────────────────────────────────────────

def is_task_done_in_obsidian(project, task_text):
    if project == "Action Items":
        return is_hotcache_item_done(task_text)
    path_template = OBSIDIAN_PROJECTS.get(project)
    if not path_template:
        return False
    path = os.path.expanduser(path_template)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return bool(re.search(r"- \[[xX]\]\s*" + re.escape(task_text), content))


# ── Previously-reported completed items ──────────────────────────────────────

def load_previously_completed():
    """Return a set of raw description keys stored in morning-coffee.md frontmatter."""
    if not os.path.exists(MORNING_COFFEE_MD):
        return set()
    seen = set()
    with open(MORNING_COFFEE_MD, encoding="utf-8") as f:
        content = f.read()
    # Extract the completed_keys YAML list from frontmatter
    m = re.search(r"^completed_keys:\n((?:  - .+\n)*)", content, re.MULTILINE)
    if not m:
        return seen
    for line in m.group(1).splitlines():
        key = line.strip().lstrip("- ").strip()
        if key:
            seen.add(key)
    return seen


# ── Since-days auto-detection ─────────────────────────────────────────────────

def compute_since_days(week_file_path, max_days=7):
    """Return days since the week file was last modified, capped at max_days."""
    try:
        delta = datetime.now().timestamp() - os.path.getmtime(week_file_path)
        return max(1, min(int(delta / 86400) + 1, max_days))
    except Exception:
        return max_days


# ── Hotcache resolution check ─────────────────────────────────────────────────

def is_hotcache_item_done(task_text):
    """True if the item is marked [x] in hotcache or no longer present."""
    if not os.path.exists(HOTCACHE_PATH):
        return False
    with open(HOTCACHE_PATH, encoding="utf-8") as f:
        content = f.read()
    if re.search(r"- \[[xX]\]\s*" + re.escape(task_text), content):
        return True
    fingerprint = task_text[:50].strip()
    return fingerprint not in content


# ── Hotcache sync from week-file manual check-offs ───────────────────────────

WEEK_ACTION_ITEM_RE = re.compile(r"^- \[([ xX])\] \[Action Items\] (.+)$")


def _item_tokens(text):
    """Word tokens (len>=3, lowercased), markdown bold stripped — for fuzzy item matching."""
    return set(re.findall(r"[a-z0-9]{3,}", text.replace("**", " ").lower()))


def sync_hotcache_from_week():
    """Propagate manually-checked [Action Items] from the workspace week file into
    hotcache's configured action-items section (- [ ] -> - [x]). Mirrors explicit
    user check-offs only — never an email/calendar heuristic — so it is safe to
    auto-write to the load-bearing hotcache. Best-match by token Jaccard with a
    0.8 floor to avoid colliding near-duplicate items. Returns synced item texts."""
    synced = []
    if not (os.path.exists(WORKSPACE_WEEK_MD) and os.path.exists(HOTCACHE_PATH)):
        return synced

    checked_items = []
    with open(WORKSPACE_WEEK_MD, encoding="utf-8") as f:
        for raw in f:
            m = WEEK_ACTION_ITEM_RE.match(raw.rstrip("\r\n"))
            if m and m.group(1).lower() == "x":
                checked_items.append(m.group(2).strip())
    if not checked_items:
        return synced

    with open(HOTCACHE_PATH, encoding="utf-8") as f:
        hc_lines = f.readlines()
    start, end = None, len(hc_lines)
    for i, line in enumerate(hc_lines):
        if line.startswith("## " + _ACTION_HEADING):
            start = i
        elif start is not None and line.startswith("## "):
            end = i
            break
    if start is None:
        return synced

    used = set()
    for item in checked_items:
        item_toks = _item_tokens(item)
        if not item_toks:
            continue
        best_i, best_score = None, 0.0
        for i in range(start, end):
            if i in used:
                continue
            hm = re.match(r"^- \[ \] (.+)$", hc_lines[i].rstrip("\r\n"))
            if not hm:
                continue
            hc_toks = _item_tokens(hm.group(1))
            if not hc_toks:
                continue
            score = len(item_toks & hc_toks) / len(item_toks | hc_toks)
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= 0.8:
            hc_lines[best_i] = hc_lines[best_i].replace("- [ ]", "- [x]", 1)
            used.add(best_i)
            synced.append(item)

    if synced:
        with open(HOTCACHE_PATH, "w", encoding="utf-8") as f:
            f.writelines(hc_lines)
    return synced


# ── Age extraction ────────────────────────────────────────────────────────────

def extract_age(detail_str):
    """Extract the numeric age from a detail string like '11d cold' or 'replied 3d ago'."""
    m = re.search(r"(\d+)d", detail_str)
    return int(m.group(1)) if m else None


# ── Week file update ──────────────────────────────────────────────────────────

def mark_done(lines, line_number):
    lines[line_number] = lines[line_number].replace("- [ ]", "- [x]", 1)


def met_candidate(deal, entry):
    """A met-since deal still waiting on the user: a confirm candidate, never an
    auto-[x]. Returns the candidate dict or None. Deliberately does NOT touch
    mark_done (a same-day meeting is a heuristic, not the sent-reply proof)."""
    if not deal.get("met_date"):
        return None
    return {
        "source": deal["source"],
        "subject": deal["subject"],
        "name": deal["name"],
        "email": deal["email"],
        "met_date": deal["met_date"],
        "reply_age_days": entry.get("reply_age_days"),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run_daily_waiting():
    """--daily mode: skip week file, return waiting_on_user threads with reply_age_days >= 2."""
    today = datetime.now(timezone.utc).astimezone(USER_TZ).strftime("%Y-%m-%d")
    output = {"meta": resolved_meta(), "today": today, "mode": "daily", "waiting_on_user": [], "errors": []}
    try:
        fresh = run_week_review(14)
    except Exception as e:
        output["errors"].append(f"Email scan failed: {e}")
        print(json.dumps(output, indent=2))
        return

    waiting = [
        {
            "source": e.get("source", ""),
            "subject": e.get("subject", ""),
            "name": e.get("counterparty_name", ""),
            "email": e.get("counterparty_email", ""),
            "reply_age_days": e.get("reply_age_days", 0),
        }
        for e in fresh.get("waiting_on_user", [])
        if (e.get("reply_age_days") or 0) >= 2
    ]
    waiting.sort(key=lambda x: x["reply_age_days"], reverse=True)
    output["waiting_on_user"] = waiting
    print(json.dumps(output, indent=2))





def _build_front_page_section(output: dict) -> None:
    """Fill the five front-page keys plus the draft and page notes.

    Fail-open in one direction only: on any error the keys stay empty and the
    full ledger below is untouched, so a bad selection costs the short page and
    never the briefing.
    """
    try:
        import week_review as wr
        alerts = {"newly_cold": output.get("newly_cold") or []}
        active = wr.load_active_thread_emails()
        output.update(wr.build_front_page(
            output.get("buckets") or [], "morning-coffee",
            alerts=alerts, active_emails=active, heading="DO TODAY"))
    except Exception as e:                                      # noqa: BLE001
        output["errors"].append(f"Front page: {e}")

    # Which of those replies are already drafted. Read only: the drafting
    # itself is the skill's step, because writing in someone's voice is the
    # one part of this that is not a rule.
    try:
        from draft_email import draft_key, load_ledger
        ledger = load_ledger()
        done = []
        for item in output.get("front_page") or []:
            if item.get("action") != "email reply":
                continue
            who = item.get("counterparty_name") or item.get("counterparty_email")
            key = draft_key(item.get("account", ""), who, item.get("subject", ""))
            if key in ledger:
                item["draft_note"] = (
                    f"drafted {ledger[key].get('created', '')}, "
                    f"{ledger[key].get('label', '')}".strip().rstrip(","))
                # The ledger keys on the email, which is what makes a rerun
                # find the same draft. The front page carries the human name,
                # and the block is read by a person, so carry it through.
                done.append({"key": key, **ledger[key],
                             "counterparty_name":
                                 item.get("counterparty_name") or ""})
        output["drafts_done"] = done
        # Rendered here so the skill pastes it rather than describing it.
        from draft_email import render_drafts_md
        output["drafts_md"] = render_drafts_md(done, mode="terminal")
        output["drafts_file_md"] = render_drafts_md(done, mode="md")
        if done:
            # Re-render so the page shows what is already waiting.
            counts = output.get("counts") or {}
            output["front_page_md"] = wr.render_front_page_md(
                output["front_page"], counts, briefing="morning-coffee",
                heading="DO TODAY")
    except Exception as e:                                      # noqa: BLE001
        output["errors"].append(f"Draft ledger: {e}")

    try:
        import briefing_html
        output["page_note"] = briefing_html.page_line("morning-coffee")
    except Exception:                                           # noqa: BLE001
        output["page_note"] = ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=int, default=None,
                        help="Days back for email scan (auto-detects from week file mtime if not specified)")
    parser.add_argument("--daily", action="store_true",
                        help="Quick waiting-on-user scan only, no week file required")
    parser.add_argument("--input", metavar="PATH",
                        help="Tier 2: pre-fetched connector data JSON (passed through to week_review.py)")
    args = parser.parse_args()

    if args.daily:
        run_daily_waiting()
        return

    _travel_commit = None

    output = {
        "meta": resolved_meta(),
        "today": datetime.now(timezone.utc).astimezone(USER_TZ).strftime("%Y-%m-%d"),
        "week_file": None,
        "completed": [],
        "completed_keys": [],  # all-time completed keys for morning-coffee.md frontmatter
        "still_open_deals": [],
        "still_open_tasks": [],
        "today_calendar": [],
        "top_priorities_today": [],
        "slipping": [],
        "newly_cold": [],
        "hotcache_alerts": [],
        "hotcache_synced": [],
        "hotcache_sync": {},
        "recent_meetings": [],
        "recent_meetings_since": None,
        "met_candidates": [],   # met since last touch, confirm-gated, never auto-[x]
        "status_change_alerts": [],  # counterparty kill/pass from week_review; review only
        "buckets": [],          # per-business grouping, decided by code not the model
        "buckets_md": "",       # the rendered bucket section, pasted verbatim
        # The front page: at most seven items chosen by code, then one summary
        # row per business with the rest folded behind it. Emitted empty here
        # so every early-return branch below still carries the keys.
        "front_page": [],
        "front_page_md": "",
        "fold_rows_md": "",
        "front_page_file_md": "",
        "fold_rows_file_md": "",
        "folded_md": "",
        "counts": {"open": 0, "late": 0, "front": 0, "folded": 0, "stale": 0},
        "fold_rows": [],
        "drafts_done": [],      # replies already sitting in a Drafts folder
        "drafts_md": "",        # the finished Drafts block, terminal render
        "drafts_file_md": "",   # the same block, with links, for the file
        "page_note": "",        # one line when the web page did not update
        "nudges": [],           # overdue deliverables: offer to draft, never send
        "travel_md": "",        # the finished Travel block, terminal render
        "travel_file_md": "",   # the same block, with links, for the file
        "travel": [],           # flight notices: link, forecast, leave-by
                                # (app/travel.py). Emitted empty here so every
                                # early return below still carries the key.
        "extra_sections": [],   # user extension sections (app/extensions.py);
                                # emitted empty so early returns carry the key
        "errors": [],
    }

    # Sync hotcache from manually-checked week-file Action Items (deterministic;
    # mirrors explicit user ticks only). Runs before the week-file gate.
    try:
        output["hotcache_synced"] = sync_hotcache_from_week()
    except Exception as e:
        output["errors"].append(f"Hotcache sync failed: {e}")

    # Find week file
    week_file = find_current_week_file()
    if not week_file:
        output["errors"].append(
            "No week file found in wiki/weekly/ within the last 7 days. Run /week first."
        )
        print(json.dumps(output, indent=2))
        return

    output["week_file"] = week_file

    since = args.since if args.since is not None else compute_since_days(week_file)

    # Parse week file
    try:
        lines, deals, tasks, priorities = parse_week_file(week_file)
    except Exception as e:
        output["errors"].append(f"Failed to parse week file: {e}")
        print(json.dumps(output, indent=2))
        return

    # Load previously-reported completed items so we don't re-surface them
    previously_completed = load_previously_completed()

    # Surface already-checked items (manually marked or auto-marked by a prior run)
    for deal in deals:
        if deal["checked"]:
            desc = f"[{deal['source']}] \"{deal['subject']}\" — {deal['name']}"
            if desc not in previously_completed:
                output["completed"].append({
                    "type": "deal",
                    "description": desc,
                    "detail": "manually marked done",
                })
    for task in tasks:
        if task["checked"]:
            desc = f"[{task['project']}] {task['task']}"
            if desc not in previously_completed:
                output["completed"].append({
                    "type": "task",
                    "description": desc,
                    "detail": "manually marked done",
                })

    # Fresh email scan
    try:
        fresh = run_week_review(max(since, 14), input_path=args.input)
    except Exception as e:
        output["errors"].append(f"Email scan failed: {e}")
        fresh = None

    # Keep the hotcache active-threads metadata honest: refresh last_contact, revive
    # dead-but-active deals, bump the staleness date. Runs after the check-off sync;
    # empty events still bump `updated:`.
    mail_events = []
    if fresh:
        # Counterparty kill/pass alerts from the hub (review only, never auto-killed).
        output["status_change_alerts"] = fresh.get("status_change_alerts", [])
        # week_review already grouped and rendered the buckets over the same
        # sections; re-rendering here would only risk the two disagreeing.
        output["buckets"] = fresh.get("buckets", [])
        output["buckets_md"] = fresh.get("buckets_md", "")
        today_date = datetime.now(timezone.utc).astimezone(USER_TZ).date()
        for bucket in ("waiting_on_user", "inbox_pending", "cold_urgent",
                       "cold_monitor", "recent"):
            for e in fresh.get(bucket, []):
                age = e.get("reply_age_days")
                if age is None:
                    age = e.get("age_days") or 0
                ev_date = (today_date - timedelta(days=age)).strftime("%Y-%m-%d")
                party = (
                    f"{e.get('counterparty_name', '')} "
                    f"{e.get('counterparty_email', '')} {e.get('subject', '')}"
                )
                mail_events.append({"date": ev_date, "party": party})
    nudges, nudge_err = _collect_nudges(
        datetime.now(timezone.utc).astimezone(USER_TZ).date())
    output["nudges"] = nudges
    if nudge_err:
        output["errors"].append(nudge_err)

    try:
        output["hotcache_sync"] = sync_hotcache(mail_events, output["today"])
    except Exception as e:
        output["errors"].append(f"Hotcache sync failed: {e}")

    file_modified = False

    if fresh:
        fresh_waiting, fresh_cold, fresh_recent = build_email_sets(fresh)

        # Classify deals
        for deal in deals:
            if deal.get("snoozed"):
                continue
            if deal["checked"]:
                continue  # already done

            email = deal["email"]

            if deal["section"] == "waiting":
                if email not in fresh_waiting:
                    # Looks resolved (user replied). No silent checkoff — the
                    # completion scan surfaces this as a confirm-first candidate.
                    continue
                entry = fresh_waiting[email]
                output["still_open_deals"].append({
                    "source": deal["source"],
                    "subject": deal["subject"],
                    "name": deal["name"],
                    "email": email,
                    "section": "waiting",
                    "reply_age_days": entry.get("reply_age_days"),
                    "urgency": deal.get("urgency"),
                    "intent": deal.get("intent"),
                    "met_date": deal.get("met_date"),
                })
                # A meeting since the last email is a HEURISTIC that the deal may
                # be handled, so surface it for one-tap confirm, never an auto-[x].
                # mark_done is NOT called here; the sent-reply branch above is the
                # only high-confidence auto-check.
                cand = met_candidate(deal, entry)
                if cand:
                    output["met_candidates"].append(cand)

            elif deal["section"] == "cold":
                if email in fresh_recent or email in fresh_waiting:
                    # Looks resolved (follow-up sent / they replied). No silent
                    # checkoff — surfaced as a confirm-first completion candidate.
                    continue
                # Still cold — check if slipping
                week_age = extract_age(deal["detail"])
                fresh_entry = fresh_cold.get(email)
                # Carried-forward entries from the prior week (load_prior_week_state)
                # have no age_days, so use .get() — bracket access KeyErrors on them.
                current_age = fresh_entry.get("age_days") if fresh_entry else None

                still_open = {
                    "source": deal["source"],
                    "subject": deal["subject"],
                    "name": deal["name"],
                    "email": email,
                    "section": "cold",
                    "age_days": current_age or week_age,
                    "met_date": deal.get("met_date"),
                }
                output["still_open_deals"].append(still_open)

                if week_age and current_age and current_age > week_age + 1:
                    output["slipping"].append({
                            "source": deal["source"],
                            "subject": deal["subject"],
                            "name": deal["name"],
                            "age_days_now": current_age,
                            "age_days_monday": week_age,
                        })

        # Detect deals that became cold since Monday (in fresh_cold but not in week.md)
        week_emails = {d["email"] for d in deals}
        for email, entry in fresh_cold.items():
            if email not in week_emails:
                output["newly_cold"].append({
                    "source": entry.get("source"),
                    "subject": entry.get("subject"),
                    "name": entry.get("counterparty_name"),
                    "email": email,
                    "age_days": entry.get("age_days"),
                })
        output["newly_cold"].sort(key=lambda x: x.get("age_days") or 0, reverse=True)

        # Calendar: today only
        today_str = output["today"]
        output["today_calendar"] = [
            e for e in fresh.get("calendar", [])
            if today_str in e.get("_sort", "") or today_str in e.get("day", "")
               or e.get("day", "").startswith(
                   (lambda d: d.strftime("%a %b") + f" {d.day}")(datetime.now(timezone.utc).astimezone(USER_TZ))
               )
        ]
        # Fallback: filter by day label matching today
        if not output["today_calendar"]:
            _d = datetime.now(timezone.utc).astimezone(USER_TZ)
            today_label = _d.strftime("%a %b") + f" {_d.day}"
            output["today_calendar"] = [
                e for e in fresh.get("calendar", [])
                if e.get("day", "") == today_label
            ]

        # Update workspace week.md with any new calendar events
        if fresh.get("calendar"):
            update_workspace_calendar(fresh["calendar"])

    # Cross-reference Top Priorities against the live calendar
    _d = datetime.now(timezone.utc).astimezone(USER_TZ)
    today_label = _d.strftime("%a %b") + f" {_d.day}"
    for p in priorities:
        if p["checked"]:
            continue
        mentions_today = _priority_mentions_today(p["title"], p["detail"], today_label)
        confirmed = _calendar_title_match(p["title"], output["today_calendar"]) if mentions_today else None
        output["top_priorities_today"].append({
            "tag": p["tag"],
            "title": p["title"],
            "detail": p["detail"],
            "calendar_today": mentions_today,
            "calendar_confirmed": confirmed,
        })

    # Classify tasks
    for task in tasks:
        if task["checked"]:
            continue

        if is_task_done_in_obsidian(task["project"], task["task"]):
            mark_done(lines, task["line_number"])
            file_modified = True
            desc = f"[{task['project']}] {task['task']}"
            if desc not in previously_completed:
                output["completed"].append({
                    "type": "task",
                    "description": desc,
                    "detail": "marked complete in Obsidian",
                })
        else:
            output["still_open_tasks"].append({
                "project": task["project"],
                "task": task["task"],
            })

    # Write updated week file if any changes
    if file_modified:
        try:
            with open(week_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            output["errors"].append(f"Failed to write updated week file: {e}")

    output["slipping"].sort(key=lambda x: x["age_days_now"], reverse=True)
    output["hotcache_alerts"] = parse_hotcache_alerts(output["today"])

    # Recent meetings since the last briefing — surfaces fresh
    # commitments/decisions from calls between the prior morning-coffee and now.
    since_date = last_briefing_date(output["today"])
    output["recent_meetings_since"] = since_date
    try:
        output["recent_meetings"] = fetch_meetings(output["today"], since_str=since_date)
    except Exception as e:
        output["errors"].append(f"{notetaker.safe_display_name()} fetch failed: {e}")

    # Travel notices, from today's calendar. Never raises: travel.collect
    # returns its own error string so a failed lookup costs the reader a
    # flight notice, not the rest of the briefing.
    if travel_enabled():
        # Imported here, not at module load: a missing or broken travel module
        # must cost the reader the travel section and nothing else. At the top
        # of the file an ImportError takes down the whole briefing.
        try:
            import travel
            # The FULL calendar, not today's slice: the flights link fires days
            # ahead and the forecast two days out, so a today-only view would
            # only ever produce the departure-morning notice.
            _all_events = (fresh or {}).get("calendar", []) if fresh else []
            _travel = travel.collect(
                _all_events, output["today"], USER_TZ,
                travel_buffer_minutes(),
            )
            output["travel"] = _travel["notices"]
            # Rendered here so the skill pastes it rather than describing it.
            output["travel_md"] = travel.render_travel_md(
                _travel["notices"], mode="terminal")
            output["travel_file_md"] = travel.render_travel_md(
                _travel["notices"], mode="md")
            if _travel["error"]:
                output["errors"].append(_travel["error"])
            # Held until the JSON is actually printed, below. The ledger means
            # "the reader was told", so it must not be written before they were.
            _travel_commit = _travel.get("commit")
        except Exception as e:
            output["errors"].append(f"Travel notices unavailable: {e}")

    # The front page, last, so the selection reads the finished alert sets:
    # a deal going quiet or a status change is a flag on a business row or an
    # item on the page, not a section of its own any more.
    _build_front_page_section(output)

    # Build cumulative completed_keys: prior + new (for frontmatter persistence)
    new_keys = {item["description"] for item in output["completed"]}
    output["completed_keys"] = sorted(previously_completed | new_keys)

    # User extension sections (fail open; see app/extensions.py).
    extensions.attach(output, "morning-coffee")

    # This output's meta carries the pending release notes; the render that
    # follows is their one showing (see plugin_update.mark_notes_shown).
    if (output.get("meta") or {}).get("whats_new"):
        import plugin_update
        plugin_update.mark_notes_shown()

    # Leave a machine-readable copy for the Workbench (app/workbench_serve.py).
    workbench_data.write_sidecar("morning_coffee_latest", output)

    # And one content-free row for the weekly scorecard (app/kpi_events.py).
    kpi_events.record_snapshot("morning-coffee", output)

    print(json.dumps(output, indent=2, default=str))

    # The briefing is now out. Only now is it true that the reader was shown
    # today's travel notices, so only now is it honest to record them.
    if _travel_commit is not None:
        try:
            _travel_commit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
