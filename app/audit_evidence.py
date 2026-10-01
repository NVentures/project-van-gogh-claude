#!/usr/bin/env python3
"""Evidence and scoring for /van-gogh:vault-audit.

The audit used to hand a model some file counts and ask for letter grades. A
fresh install scored a B, because it had no stubs and no orphans: nothing was
wrong with it because nothing was in it. An empty system cannot be healthy,
and a grade that says otherwise is worse than no grade.

So the score is built from evidence instead. Twenty criteria across four
groups, each worth 0, 1, 3 or 5. Eleven are decided here in code from things
that are true on disk; nine are left null for the model to judge, and the
report validator refuses a judged row that cites nothing. Two caps stop a
library of clever skills from covering for a system nobody has connected or
scheduled: Context is capped when the vault cannot answer a question about the
user's own priorities, and Cadence is capped when nothing is installed to run.

The vocabulary in this module (verified, not_found, no_citation) is machine
state. It reaches the JSON and the ledger file, never the rendered report:
`scrub` and the render helpers keep it out, and DESIGN.md is why.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

# ── Points and stages ────────────────────────────────────────────────────────

VALID_POINTS = (0, 1, 3, 5)

# Four groups, five criteria each. The ids are the contract: the score table,
# the sidecar, the validator and the ledger all key on them.
CRITERIA = {
    "C1": ("Context", "The vault holds the user's own goals in their words", "model"),
    "C2": ("Context", "A question about the work can be answered from the vault", "code"),
    "C3": ("Context", "The rendered briefings are current", "code"),
    "C4": ("Context", "Meeting and email history is filed where it can be found", "model"),
    "C5": ("Context", "Finished work is recorded, not just planned work", "code"),
    "N1": ("Connections", "Every configured account actually returned data", "code"),
    "N2": ("Connections", "The notetaker is wired and returning meetings", "model"),
    "N3": ("Connections", "Credentials are current and refresh without a human", "model"),
    "N4": ("Connections", "The vault is the single place the data lands", "model"),
    "N5": ("Connections", "A source that fails says so, in the briefing", "code"),
    "P1": ("Capabilities", "The briefings run end to end and leave artifacts", "code"),
    "P2": ("Capabilities", "The skills in use match the work the user does", "model"),
    "P3": ("Capabilities", "Failures are recorded with enough detail to fix", "model"),
    "P4": ("Capabilities", "Output is trusted enough to act on without checking", "model"),
    "P5": ("Capabilities", "The system has been used across several days", "code"),
    "D1": ("Cadence", "The scheduled jobs are installed and loaded", "code"),
    "D2": ("Cadence", "Those jobs have actually run recently", "code"),
    "D3": ("Cadence", "A failed run is noticed, not just logged", "code"),
    "D4": ("Cadence", "The user reads the output on the cadence it arrives", "model"),
    "D5": ("Cadence", "The audit itself runs, and its pick gets followed up", "code"),
}

GROUPS = ("Context", "Connections", "Capabilities", "Cadence")

# Stage thresholds. Pinned by test at every boundary because an off-by-one here
# renames the user's whole result.
STAGES = (
    (24, "Unproven"),
    (49, "Foundation"),
    (69, "Working"),
    (84, "Compounding"),
    (100, "Leveraged"),
)

CONTEXT_CAP = 10
CADENCE_CAP = 10
PROBE_WINDOW_DAYS = 30
ACCOUNT_SIDECAR_DAYS = 7
RUNS_WINDOW_DAYS = 14
AUDIT_WINDOW_DAYS = 21

_EM, _EN = chr(0x2014), chr(0x2013)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# A long unbroken run of token-ish characters is how a refresh token looks.
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
# The producer's own error format, in afternoon_tea.py: f"Sent mail ({label}): {e}".
# Derived from it rather than guessed, and the closing paren is what keeps
# "Gmail" from matching an error about "Gmail 2".
_ACCOUNT_ERROR_RE = re.compile(
    r"^(?:Sent mail|Inbound mail|Calendar|Drafts?)\s*\(([^)]+)\)\s*:")
# week.md records its own render date in frontmatter; week_review.resolve_since
# reads the same key. File mtime lies here (a vault sync touches every file).
_GENERATED_RE = re.compile(r"^generated:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_FM_DATE_RE = re.compile(r"^(?:updated|created|date):\s*(\d{4}-\d{2}-\d{2})",
                         re.MULTILINE)
_DATE_ANY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

STUB_BODY_MIN = 80


# ── Text safety ──────────────────────────────────────────────────────────────

def scrub(text: str, limit: int = 200) -> str:
    """Make a string safe to render: no dashes, no secrets, no control bytes.

    Evidence strings quote log lines and error messages, which come from email
    subjects and API bodies. Three things must never survive into a report: an
    em or en dash (DESIGN.md forbids them everywhere), anything shaped like a
    credential, and a control character.
    """
    out = str(text or "")
    out = out.replace(_EM, ",").replace(_EN, "-")
    out = _CTRL_RE.sub("", out)
    out = _TOKEN_RE.sub("[redacted]", out)
    out = _EMAIL_RE.sub("[address]", out)
    out = " ".join(out.split())
    return out[:limit]


# ── Small readers ────────────────────────────────────────────────────────────

def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").lstrip("﻿")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path):
    text = _read(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _mtime_date(path: Path) -> date | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def _age_days(path: Path, today: date) -> int | None:
    d = _mtime_date(path)
    return None if d is None else (today - d).days


def week_generated_date(path: Path) -> date | None:
    """week.md's own `generated:` date. None when absent or unparseable."""
    text = _read(path)
    if text is None:
        return None
    m = _GENERATED_RE.search(text[:1200])
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _body_after_frontmatter(text: str) -> str:
    m = re.match(r"^---\n.*?\n---", text, re.DOTALL)
    return text[m.end():] if m else text


def _is_stub_page(text: str) -> bool:
    return len(_body_after_frontmatter(text).strip()) < STUB_BODY_MIN


# ── Evidence records ─────────────────────────────────────────────────────────

def ev(criterion: str, kind: str, target: str, verdict: str,
       observed: str = "", detail: str = "", key: str = "") -> dict:
    """One evidence record. `target` is a real path when there is one.

    `id` has to be stable across runs so the ledger can carry it, and unique
    within a run so a score row can name exactly which record it stands on.
    """
    ident = f"ev:{criterion}:{key or Path(target).name or kind}"
    return {
        "id": ident,
        "criterion": criterion,
        "kind": kind,
        "target": str(target),
        "verdict": verdict,
        "observed": scrub(observed),
        "detail": scrub(detail),
    }


def _verified(records: list, criterion: str) -> list:
    """Records that back a point: verified, and their file still exists.

    The second half matters. A record is written during collection and read
    during scoring, and between the two a file can be gone. Points must rest
    on something present, so the existence check runs at scoring time.
    """
    out = []
    for r in records:
        if r["criterion"] != criterion or r["verdict"] != "verified":
            continue
        target = r.get("target", "")
        if target and not Path(target).exists():
            continue
        out.append(r)
    return out


# ── Paths bundle ─────────────────────────────────────────────────────────────

class AuditPaths:
    """Everything the collector reads, resolved once by the caller.

    A plain holder rather than config calls inside the collector, so every
    function here is testable against a tmp_path vault with no config and no
    clock.
    """

    def __init__(self, logs: Path, vault: Path, hotcache: Path, entities: Path,
                 sources: Path, weekly: Path, week_md: Path, coffee_md: Path,
                 tea_md: Path, memory_md: Path, done_log: Path,
                 report_md: Path, project_pages: dict | None = None,
                 priorities: dict | None = None,
                 account_labels: list | None = None,
                 items_heading: str = "Action Items",
                 alerts_enabled: bool = False,
                 digest_briefings: dict | None = None):
        self.logs = Path(logs)
        self.vault = Path(vault)
        self.hotcache = Path(hotcache)
        self.entities = Path(entities)
        self.sources = Path(sources)
        self.weekly = Path(weekly)
        self.week_md = Path(week_md)
        self.coffee_md = Path(coffee_md)
        self.tea_md = Path(tea_md)
        self.memory_md = Path(memory_md)
        self.done_log = Path(done_log)
        self.report_md = Path(report_md)
        self.project_pages = dict(project_pages or {})
        self.priorities = dict(priorities or {})
        self.account_labels = list(account_labels or [])
        self.items_heading = items_heading
        self.alerts_enabled = bool(alerts_enabled)
        self.digest_briefings = dict(digest_briefings or {})

    def sidecar(self, stem: str) -> Path:
        return self.logs / f"{stem}.json"


def paths_from_config(config_loader) -> AuditPaths:
    """Build the bundle from the live config. The only config-aware function."""
    businesses = config_loader.businesses()
    return AuditPaths(
        logs=config_loader.logs_dir(),
        vault=config_loader.vault(),
        hotcache=config_loader.hotcache_path(),
        entities=config_loader.entities_dir(),
        sources=config_loader.sources_dir(),
        weekly=config_loader.weekly_dir(),
        week_md=config_loader.workspace_week_md_path(),
        coffee_md=config_loader.morning_coffee_md_path(),
        tea_md=config_loader.afternoon_tea_md_path(),
        memory_md=config_loader.vangogh_memory_path(),
        done_log=config_loader.done_log_path(),
        report_md=config_loader.van_gogh_root() / "vault-audit.md",
        project_pages=config_loader.project_pages(),
        priorities={b.get("tag", ""): list(b.get("priorities", []) or [])
                    for b in businesses},
        account_labels=config_loader.account_labels(),
        items_heading=config_loader.hotcache_action_items_heading(),
        alerts_enabled=config_loader.alerts_enabled(),
        digest_briefings=config_loader.digest_briefings(),
    )


# ── The five retrieval probes ────────────────────────────────────────────────

def _heading_has_bullet(text: str, heading: str) -> bool:
    """Is there at least one bullet under this exact heading?

    The section ends at the next heading of the SAME level or higher, not at
    the next heading of any level: a real action list is organised into
    subheadings ("Open", then "Overdue"), and treating the first subheading as
    the end of the section reported an empty list on a hotcache full of tasks.
    A bullet under a later, unrelated section still does not count.
    """
    m = re.search(rf"^(#{{1,6}})\s*{re.escape(heading)}\s*$", text, re.MULTILINE)
    if not m:
        return False
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s+\S", rest, re.MULTILINE)
    section = rest[:nxt.start()] if nxt else rest
    # Plain bullets and task list items both count as an item.
    return bool(re.search(r"^\s*[-*+]\s+(?:\[[ xX]\]\s*)?\S", section, re.MULTILINE))


def run_probes(paths: AuditPaths, today: date) -> list:
    """Five questions asked of the vault through its own declared routes.

    Each is a question a user would actually ask, answered by following the
    config to a file and reading it, never by a keyword search. `found_direct`
    means the route led somewhere real. A project page moved on disk while
    config still points at the old path is `not_found`, and it should be: the
    system cannot find it either.
    """
    out = []

    def probe(name, found, target, detail):
        out.append({
            "id": f"probe:{name}",
            "criterion": "C2",
            "kind": "probe",
            "name": name,
            "target": str(target),
            "verdict": "found_direct" if found else "not_found",
            "detail": scrub(detail),
        })

    # 1. Purpose: at least one business has a stated priority AND a real page.
    purpose_hit, purpose_target, purpose_detail = False, "", "no business has both a priority and a page"
    for tag, prios in sorted(paths.priorities.items()):
        if not prios:
            continue
        for name, page in sorted(paths.project_pages.items()):
            text = _read(Path(page))
            if text is None or _is_stub_page(text):
                continue
            purpose_hit, purpose_target = True, page
            purpose_detail = f"{name} has {len(prios)} stated priorities and a written page"
            break
        if purpose_hit:
            break
    if not paths.priorities:
        purpose_detail = "no businesses are configured"
    probe("purpose", purpose_hit, purpose_target, purpose_detail)

    # 2. Priority: the hotcache action-items heading exists and holds a bullet.
    hot = _read(paths.hotcache)
    prio_hit = bool(hot) and _heading_has_bullet(hot, paths.items_heading)
    probe("priority", prio_hit, paths.hotcache,
          "the action items heading holds at least one item" if prio_hit
          else "no items found under the configured action items heading")

    # 3. Project: EVERY configured project page exists and is written.
    missing = []
    for name, page in sorted(paths.project_pages.items()):
        text = _read(Path(page))
        if text is None or _is_stub_page(text):
            missing.append(name)
    proj_hit = bool(paths.project_pages) and not missing
    probe("project", proj_hit, paths.vault,
          "every configured project page exists and has content" if proj_hit
          else (f"{len(missing)} configured project pages are missing or empty"
                if paths.project_pages else "no project pages are configured"))

    # 4. Entity: at least one entity page that is not a stub.
    ent_hit, ent_target = False, paths.entities
    if paths.entities.is_dir():
        for page in sorted(paths.entities.glob("*.md")):
            text = _read(page)
            if text is not None and not _is_stub_page(text):
                ent_hit, ent_target = True, page
                break
    probe("entity", ent_hit, ent_target,
          "at least one person or company page is written up" if ent_hit
          else "no written entity pages found")

    # 5. Source: a non-stub source page dated within 30 days.
    src_hit, src_target = False, paths.sources
    if paths.sources.is_dir():
        for page in sorted(paths.sources.rglob("*.md"), reverse=True):
            if "(stub)" in page.name:
                continue
            text = _read(page)
            if text is None or _is_stub_page(text):
                continue
            m = _DATE_ANY_RE.search(page.name) or _FM_DATE_RE.search(text[:400])
            when = None
            if m:
                try:
                    when = date.fromisoformat(m.group(1))
                except ValueError:
                    when = None
            if when is None:
                when = _mtime_date(page)
            if when is not None and (today - when).days <= PROBE_WINDOW_DAYS:
                src_hit, src_target = True, page
                break
    probe("source", src_hit, src_target,
          "a meeting or note from the last month is filed" if src_hit
          else f"no filed notes in the last {PROBE_WINDOW_DAYS} days")

    return out


# ── Evidence collection ──────────────────────────────────────────────────────

def _sidecar_date(payload: dict, path: Path) -> date | None:
    """The date a sidecar says it is for, falling back to file mtime."""
    if isinstance(payload, dict):
        raw = str(payload.get("today", ""))[:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return _mtime_date(path)


def account_verdicts(payload: dict, sidecar_age: int | None,
                     labels: list) -> dict:
    """Per-account verdict from one afternoon-tea sidecar.

    Matching is exact, never fuzzy. The error strings the producer writes are
    shaped `Sent mail (Outlook): ...`, so the label is read out of the
    parentheses and compared whole. A prefix match would let an error about
    "Gmail 2" condemn "Gmail", which is the class of bug that makes an audit
    worse than no audit.

    Five verdicts, because they mean different things to a person: `failed`
    (we tried and it broke), `verified` (data came back), `verified_empty`
    (it worked and there was nothing, which scores like unverified but is not
    a fault), `stale` (the evidence is too old to stand on) and `unverified`.
    """
    out = {label: "unverified" for label in labels}
    if not isinstance(payload, dict):
        return out

    failed = set()
    for line in payload.get("errors", []) or []:
        m = _ACCOUNT_ERROR_RE.match(str(line).strip())
        if m:
            named = m.group(1).strip()
            for label in labels:
                if named == label:
                    failed.add(label)

    seen = {label: 0 for label in labels}
    for key in ("sent", "inbound_today", "deal_inbound", "items",
                "waiting_on_user", "inbox_pending"):
        for item in payload.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            acct = str(item.get("account", "") or item.get("source", ""))
            if acct in seen:
                seen[acct] += 1

    stale = sidecar_age is not None and sidecar_age > ACCOUNT_SIDECAR_DAYS
    for label in labels:
        if label in failed:
            out[label] = "failed"
        elif stale:
            out[label] = "stale"
        elif seen[label] > 0:
            out[label] = "verified"
        else:
            out[label] = "verified_empty"
    return out


def collect_evidence(today: date, paths: AuditPaths, job_states: list,
                     runs: list) -> list:
    """Walk the disk once and write down what is actually there."""
    records = []
    add = records.append

    # ── C3: are the rendered briefings current? ──────────────────────────────
    # week.md is dated by its own frontmatter, not its mtime: a vault sync
    # touches every file and would report a three week old briefing as fresh.
    week_gen = week_generated_date(paths.week_md)
    week_age = (today - week_gen).days if week_gen else _age_days(paths.week_md, today)
    for name, path, age, limit in (
        ("hotcache", paths.hotcache, _age_days(paths.hotcache, today), 7),
        ("week", paths.week_md, week_age, 9),
        ("morning coffee", paths.coffee_md, _age_days(paths.coffee_md, today), 4),
        ("afternoon tea", paths.tea_md, _age_days(paths.tea_md, today), 4),
    ):
        text = _read(Path(path))
        if text is None:
            add(ev("C3", "file", path, "missing", f"{name} has never been written",
                   key=name))
        elif _is_stub_page(text):
            # A recent file is not a recent briefing. An empty page written a
            # minute ago is the emptiest possible result, not the freshest.
            add(ev("C3", "file", path, "verified_empty",
                   f"{name} exists but has nothing in it", key=name))
        elif age is None:
            add(ev("C3", "file", path, "missing", f"{name} has never been written",
                   key=name))
        elif age <= limit:
            add(ev("C3", "file", path, "verified",
                   f"{name} was written {age} days ago", key=name))
        else:
            add(ev("C3", "file", path, "stale",
                   f"{name} is {age} days old", key=name))

    # ── C5: is finished work recorded, not just planned work? ────────────────
    done_text = _read(paths.done_log)
    done_recent = False
    if done_text:
        for m in _DATE_ANY_RE.finditer(done_text):
            try:
                if (today - date.fromisoformat(m.group(1))).days <= 14:
                    done_recent = True
                    break
            except ValueError:
                continue
    add(ev("C5", "file", paths.done_log,
           "verified" if done_recent else ("stale" if done_text else "missing"),
           "completed work was logged in the last two weeks" if done_recent
           else "no completed work logged in the last two weeks",
           key="done log"))
    mem_age = _age_days(paths.memory_md, today)
    add(ev("C5", "file", paths.memory_md,
           "verified" if (mem_age is not None and mem_age <= 30) else
           ("stale" if mem_age is not None else "missing"),
           f"project memory was updated {mem_age} days ago" if mem_age is not None
           else "no project memory file", key="memory"))

    # ── N1 and N5: the briefing sidecars ─────────────────────────────────────
    tea_path = paths.sidecar("afternoon_tea_latest")
    tea = _read_json(tea_path)
    tea_age = None
    if isinstance(tea, dict):
        when = _sidecar_date(tea, tea_path)
        tea_age = (today - when).days if when else None
        add(ev("N1", "sidecar", tea_path, "verified",
               f"the end of day run left a record {tea_age} days ago",
               key="tea sidecar"))
    else:
        add(ev("N1", "sidecar", tea_path, "missing",
               "the end of day run has never left a record", key="tea sidecar"))

    verdicts = account_verdicts(tea if isinstance(tea, dict) else {},
                                tea_age, paths.account_labels)
    for label, verdict in sorted(verdicts.items()):
        add(ev("N1", "sidecar", tea_path if isinstance(tea, dict) else "",
               verdict,
               {"verified": f"{label} returned mail",
                "verified_empty": f"{label} connected but returned nothing",
                "failed": f"{label} reported an error",
                "stale": f"{label} has no recent evidence",
                "unverified": f"{label} has never been seen working"}[verdict],
               key=f"account {label}"))

    present_sidecars = 0
    with_errors_key = 0
    for stem in ("morning_coffee_latest", "afternoon_tea_latest", "week_review_latest"):
        path = paths.sidecar(stem)
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        present_sidecars += 1
        if "errors" in payload:
            with_errors_key += 1
    add(ev("N5", "sidecar", paths.logs,
           "verified" if (present_sidecars and with_errors_key == present_sidecars)
           else ("verified_empty" if present_sidecars else "missing"),
           f"{with_errors_key} of {present_sidecars} runs recorded their own problems"
           if present_sidecars else "no runs have left a record",
           key="error reporting"))
    failures = paths.logs / "failures.jsonl"
    add(ev("N5", "log", failures, "verified" if failures.is_file() else "missing",
           "a failure log exists" if failures.is_file() else "no failure log",
           key="failure log"))
    pages = paths.logs / "briefing_pages.json"
    add(ev("N5", "file", pages, "verified" if pages.is_file() else "missing",
           "the briefing web pages are tracked" if pages.is_file()
           else "no record of published briefing pages", key="pages"))

    # ── P1 and P5: did the briefings run, and across how many days? ──────────
    for name, stem, md in (
        ("morning coffee", "morning_coffee_latest", paths.coffee_md),
        ("afternoon tea", "afternoon_tea_latest", paths.tea_md),
        ("week", "week_review_latest", paths.week_md),
    ):
        side = paths.sidecar(stem)
        ok = _read_json(side) is not None and Path(md).is_file()
        add(ev("P1", "sidecar", side if side.is_file() else md,
               "verified" if ok else "missing",
               f"{name} ran and left both a page and a record" if ok
               else f"{name} has not completed a full run", key=name))

    active_days = set()
    for stem in ("morning_coffee_latest", "afternoon_tea_latest", "week_review_latest"):
        path = paths.sidecar(stem)
        payload = _read_json(path)
        if isinstance(payload, dict):
            when = _sidecar_date(payload, path)
            if when:
                active_days.add(when)
    pages_payload = _read_json(pages)
    if isinstance(pages_payload, dict):
        for entry in pages_payload.values():
            if isinstance(entry, dict):
                raw = str(entry.get("last_attempt", ""))[:10]
                try:
                    active_days.add(date.fromisoformat(raw))
                except ValueError:
                    pass
    for row in runs:
        raw = str(row.get("started", ""))[:10]
        try:
            active_days.add(date.fromisoformat(raw))
        except ValueError:
            pass
    if done_text:
        for m in _DATE_ANY_RE.finditer(done_text):
            try:
                active_days.add(date.fromisoformat(m.group(1)))
            except ValueError:
                continue
    span = ((max(active_days) - min(active_days)).days if len(active_days) > 1 else 0)
    add(ev("P5", "log", paths.logs,
           "verified" if active_days else "missing",
           f"the system was used on {len(active_days)} separate days over {span} days"
           if active_days else "no record of the system being used",
           detail=f"days={len(active_days)} span={span}", key="usage"))

    # ── D1: what the OS scheduler holds ──────────────────────────────────────
    for row in job_states:
        state = row.get("state", "")
        add(ev("D1", "scheduler", row.get("path", ""),
               {"loaded": "verified", "installed": "verified",
                "on disk, not loaded": "stale",
                "not installed": "missing",
                "unsupported": "unverified"}.get(state, "unverified"),
               f"{row.get('name', 'a job')} is {state}",
               key=row.get("label") or row.get("name") or "scheduler"))

    # ── D2 and D3: did they run, and does a failure get noticed? ─────────────
    recent = [r for r in runs
              if _within(r.get("started", ""), today, RUNS_WINDOW_DAYS)]
    ok_runs = [r for r in recent if r.get("rc") == 0]
    bad_runs = [r for r in recent if r.get("rc") not in (0, None)]
    ok_dates = _dates_of(ok_runs)
    add(ev("D2", "runs", paths.logs / "runs.jsonl",
           "verified" if ok_dates else ("failed" if bad_runs else "missing"),
           f"{len(ok_runs)} successful runs on {len(ok_dates)} separate days"
           if ok_dates else ("runs were attempted and failed" if bad_runs
                             else "no runs recorded"),
           detail=f"ok={len(ok_runs)} dates={len(ok_dates)} failed={len(bad_runs)}",
           key="runs"))

    rows_complete = all(("finished" in r and "rc" in r) for r in runs) if runs else False
    add(ev("D3", "runs", paths.logs / "runs.jsonl",
           "verified" if (rows_complete and paths.alerts_enabled) else
           ("verified_empty" if (runs or (paths.logs / "runs.jsonl").is_file())
            else "missing"),
           "each run records how it ended" + (" and alerts are on"
                                              if paths.alerts_enabled else
                                              " but nobody is alerted")
           if runs else "no runs recorded", key="run detail"))
    failures_log = paths.logs / "failures.jsonl"
    healed = _healed_after_alert(failures_log, runs)
    add(ev("D3", "log", failures_log,
           "verified" if healed else
           ("verified_empty" if failures_log.is_file() else "missing"),
           "a failure was alerted and the job later succeeded" if healed
           else ("failures are being written down, none has been followed by a "
                 "success yet" if failures_log.is_file()
                 else "no failure log"),
           key="recovery"))

    # ── D3 and D5: is anything watching the jobs, and did it catch one? ──────
    #
    # A re-run that worked is the strongest cadence evidence there is: it
    # means a job failed, something noticed without being asked, and the work
    # got done anyway. That is the whole loop, observed rather than assumed.
    watch_log = paths.logs / "job_watch.jsonl"
    watch_ticks = _read_ticks(watch_log)
    last_tick = _last_tick_stamp(watch_ticks)
    tick_age_h = None
    if last_tick:
        tick_age_h = (datetime.now() - last_tick).total_seconds() / 3600
    add(ev("D3", "log", watch_log,
           "verified" if (tick_age_h is not None and tick_age_h <= 2) else
           ("stale" if tick_age_h is not None else "missing"),
           "your scheduled jobs were checked within the last two hours"
           if (tick_age_h is not None and tick_age_h <= 2)
           else (f"your scheduled jobs have not been checked for "
                 f"{tick_age_h:.0f} hours, so if one stopped, nothing "
                 "restarted it"
                 if tick_age_h is not None
                 else "nothing checks whether your scheduled jobs ran, so "
                      "run /van-gogh:install-van-gogh to set that up"),
           key="watcher"))
    rescued = _watch_rescued(watch_ticks, runs)
    add(ev("D3", "log", watch_log,
           "verified" if rescued else
           ("verified_empty" if watch_ticks else "missing"),
           f"{rescued} failed once but was restarted and finished"
           if rescued
           else ("your jobs were checked and none needed restarting"
                 if watch_ticks
                 else "nothing checks whether your jobs ran"),
           key="rescue"))

    # ── D5: does the audit itself run, and get followed up? ──────────────────
    add(ev("D5", "file", paths.report_md,
           "verified" if paths.report_md.is_file() else "missing",
           "an audit report exists" if paths.report_md.is_file()
           else "no audit has been written", key="report"))

    return records


def _read_ticks(path: Path) -> list:
    """The watcher's tick rows. A torn line is skipped, never raised on."""
    out = []
    text = _read(path)
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _last_tick_stamp(ticks: list):
    for row in reversed(ticks):
        try:
            return datetime.fromisoformat(str(row.get("ts", "")))
        except ValueError:
            continue
    return None


def _watch_rescued(ticks: list, runs: list) -> str:
    """A job the watcher re-ran that then succeeded, or "".

    The same shape as _healed_after_alert one level up: not "a job was
    re-run", which proves only that something tried, but "a job was re-run
    and the next thing that happened was a success".
    """
    for row in ticks:
        stamp = str(row.get("ts", ""))
        for job in row.get("kicked", []) or []:
            for run in runs:
                if run.get("rc") != 0:
                    continue
                if not str(run.get("job", "")).endswith(str(job)):
                    continue
                if str(run.get("started", "")) > stamp:
                    return str(job)
    return ""


def _within(stamp: str, today: date, days: int) -> bool:
    try:
        return 0 <= (today - date.fromisoformat(str(stamp)[:10])).days <= days
    except ValueError:
        return False


def _dates_of(rows: list) -> set:
    out = set()
    for row in rows:
        try:
            out.add(date.fromisoformat(str(row.get("started", ""))[:10]))
        except ValueError:
            continue
    return out


def _healed_after_alert(failures_path: Path, runs: list) -> bool:
    """Was an alerted failure followed by a success of the same component?

    This is the difference between a log and a loop. Recording a failure is
    cheap; noticing it, telling someone, and then seeing the thing work again
    is the property worth five points.
    """
    text = _read(failures_path)
    if not text:
        return False
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict) or not row.get("alerted"):
            continue
        component, when = row.get("component", ""), str(row.get("ts", ""))
        for run in runs:
            if run.get("rc") != 0 or run.get("job") != component:
                continue
            if str(run.get("started", "")) > when:
                return True
    return False


# ── Scoring ──────────────────────────────────────────────────────────────────

def _tier(n: int, five: int, three: int, one: int) -> int:
    """The 0/1/3/5 ladder. There is no 2 and no 4, deliberately: a scale with
    a middle invites a shrug, and every point here has to name what it saw."""
    if n >= five:
        return 5
    if n >= three:
        return 3
    if n >= one:
        return 1
    return 0


def _share(got: int, total: int) -> int:
    """Score a "how many of mine passed" row by fraction, not by raw count.

    Two rows counting different numbers of things must agree on what half
    means, or the report shows the same fraction scoring 1 in one row and 3
    in another with no reason a reader can see.
    """
    if not total or got <= 0:
        return 0
    fraction = got / total
    if fraction >= 1:
        return 5
    if fraction >= 0.6:
        return 3
    return 1


def _row(cid: str, points, evidence_ids: list, basis: str) -> dict:
    group, label, judged_by = CRITERIA[cid]
    if points is not None and points not in VALID_POINTS:
        raise ValueError(f"{cid}: {points} is not one of {VALID_POINTS}")
    # A point with nothing behind it is the failure this whole design exists
    # to prevent, so it is corrected here rather than reported.
    if points and not evidence_ids:
        points, basis = 0, "no_citation"
    return {"id": cid, "group": group, "criterion": label,
            "judged_by": judged_by, "points": points,
            "evidence": list(evidence_ids), "basis": basis}


def score_criteria(records: list, probes: list, paths: AuditPaths,
                   job_states: list, runs: list, today: date,
                   prior_audit_runs: int = 0,
                   prior_pick_resolved: bool = False) -> dict:
    """Twenty rows. Eleven decided here, nine left for the model."""
    rows = {}

    def code(cid, points, ids, basis):
        rows[cid] = _row(cid, points, ids, basis)

    def model(cid, hint):
        rows[cid] = _row(cid, None, [], hint)

    # ── Context ──────────────────────────────────────────────────────────────
    model("C1", "Read the project pages and priorities: are these the user's "
                "own words about their own goals, or a template?")
    found = [p for p in probes if p["verdict"] == "found_direct"]
    code("C2", _tier(len(found), 5, 3, 1), [p["id"] for p in found],
         f"{len(found)} of {len(probes)} questions could be answered from the vault")

    fresh = _verified(records, "C3")
    # C3 and C5 both grade "how many of my own checks passed", so they share
    # one ladder. They used to disagree: 1 of 2 scored 3 while 2 of 4 scored 1.
    # The list has to sit before the count, not after a colon. Written the
    # other way round, "2 of the 4 pages this checks are current: the
    # hotcache, the weekly briefing, ..." reads as though all four were
    # current, which the freshness table above it flatly contradicts.
    code("C3", _share(len(fresh), 4), [r["id"] for r in fresh],
         f"Of the hotcache, the weekly briefing, the morning briefing and the "
         f"end of day retro, {len(fresh)} of those 4 are current")

    model("C4", "Open two recent meetings and one recent email thread: is each "
                "filed where someone would look for it a month from now?")

    c5 = _verified(records, "C5")
    # Scored on the same share as C3, so two rows showing the same fraction
    # cannot come out with different numbers and no stated reason.
    code("C5", _share(len(c5), 2), [r["id"] for r in c5],
         f"{len(c5)} of the 2 records of finished work, the done log and the "
         f"project memory, {'is' if len(c5) == 1 else 'are'} current")

    # ── Connections ──────────────────────────────────────────────────────────
    acct_records = [r for r in records
                    if r["criterion"] == "N1" and r["id"].startswith("ev:N1:account ")]
    verified = [r for r in acct_records if r["verdict"] == "verified"]
    failed = [r for r in acct_records if r["verdict"] == "failed"]
    tea_present = any(r["id"] == "ev:N1:tea sidecar" and r["verdict"] == "verified"
                      for r in records)
    if acct_records and len(verified) == len(acct_records):
        n1, n1_ids = 5, [r["id"] for r in verified]
    elif verified and not failed:
        n1, n1_ids = 3, [r["id"] for r in verified]
    elif tea_present:
        n1, n1_ids = 1, ["ev:N1:tea sidecar"]
    else:
        n1, n1_ids = 0, []
    code("N1", n1, n1_ids,
         f"{len(verified)} of {len(acct_records)} accounts returned data"
         if acct_records else "no accounts are configured")

    model("N2", "Does the meeting notetaker return meetings, and do those "
                "meetings reach the vault?")
    model("N3", "Have the credentials refreshed without anyone re-authorising "
                "them recently? Judge from the briefings' own error lines, "
                "never by reading the stored credentials.")
    model("N4", "Is the vault the one place this data lands, or is some of it "
                "still only in an inbox or a notes app?")

    n5_ids = [r["id"] for r in _verified(records, "N5")]
    code("N5", _tier(len(n5_ids), 3, 2, 1), n5_ids,
         f"{len(n5_ids)} of 3 error-reporting paths are in place")

    # ── Capabilities ─────────────────────────────────────────────────────────
    p1 = _verified(records, "P1")
    code("P1", _tier(len(p1), 3, 2, 1), [r["id"] for r in p1],
         f"{len(p1)} of 3 briefings completed a full run")

    model("P2", "Do the skills in use match the work this person actually "
                "does, or are some shipped skills simply unused?")
    model("P3", "Read the recorded failures: does each one carry enough to fix "
                "it, or only that something broke?")
    model("P4", "Is the output trusted enough to act on, or does the user "
                "re-check it every time?")

    usage = [r for r in records if r["id"] == "ev:P5:usage" and r["verdict"] == "verified"]
    days = span = 0
    for r in usage:
        m = re.search(r"days=(\d+) span=(\d+)", r.get("detail", ""))
        if m:
            days, span = int(m.group(1)), int(m.group(2))
    ran = len([r for r in runs if r.get("rc") == 0])
    if days >= 3 and span >= 7 and ran:
        p5 = 5
    elif days >= 2 and ran:
        p5 = 3
    elif days >= 3 and span >= 7:
        # The vault is being worked in, but by hand: that is evidence about
        # the person, not about the system, so it cannot earn full marks.
        p5 = 1
    else:
        p5 = 1 if days else 0
    code("P5", p5, [r["id"] for r in usage],
         f"worked in on {days} separate days over {span} days"
         + ("" if ran else ", though all of it by hand rather than by a run "
                           "the system completed itself"))

    # ── Cadence ──────────────────────────────────────────────────────────────
    expected = [r for r in job_states if r.get("state") != "unsupported"]
    loaded = [r for r in expected if r.get("state") in ("loaded", "installed")]
    on_disk = [r for r in expected if r.get("state") == "on disk, not loaded"]
    d1_ids = [f"ev:D1:{r.get('label') or r.get('name')}" for r in loaded]
    if expected and len(loaded) == len(expected):
        d1 = 5
    elif loaded:
        d1 = 3
    elif on_disk:
        d1, d1_ids = 1, [f"ev:D1:{r.get('label') or r.get('name')}" for r in on_disk]
    else:
        d1, d1_ids = 0, []
    code("D1", d1, d1_ids,
         f"{len(loaded)} of {len(expected)} scheduled jobs are installed and loaded"
         if expected else "this computer has no scheduler Van Gogh can use")

    recent = [r for r in runs if _within(r.get("started", ""), today, RUNS_WINDOW_DAYS)]
    ok_runs = [r for r in recent if r.get("rc") == 0]
    ok_dates = _dates_of(ok_runs)
    bad_runs = [r for r in recent if r.get("rc") not in (0, None)]
    # A failure AFTER the last success is a system that has stopped working,
    # whatever the count of earlier successes says.
    last_ok = max((str(r.get("started", "")) for r in ok_runs), default="")
    broke_after = any(str(r.get("started", "")) > last_ok for r in bad_runs) if last_ok else bool(bad_runs)
    every_loaded_ran = bool(loaded) and all(
        any(r.get("job", "").endswith(job.get("name", "\0")) and r.get("rc") == 0
            for r in ok_runs) for job in loaded)
    if len(ok_dates) >= 2 and every_loaded_ran and not broke_after:
        d2 = 5
    elif ok_runs and not broke_after:
        d2 = 3
    elif ok_runs or bad_runs:
        d2 = 1
    else:
        d2 = 0
    code("D2", d2, ["ev:D2:runs"] if (ok_runs or bad_runs) else [],
         f"{len(ok_runs)} successful runs across {len(ok_dates)} days"
         + (", but the most recent attempt failed" if broke_after else "")
         if (ok_runs or bad_runs) else "nothing has run")

    detail_ok = any(r["id"] == "ev:D3:run detail" and r["verdict"] == "verified"
                    for r in records)
    healed = any(r["id"] == "ev:D3:recovery" and r["verdict"] == "verified"
                 for r in records)
    # Tier 1 is "anything is being written down at all", so it asks whether a
    # log exists, not whether a log says something good. Reading it off the
    # verified-only set made a present-but-quiet log score zero.
    logged = [r for r in records
              if r["criterion"] == "D3"
              and r["verdict"] in ("verified", "verified_empty")
              and (not r.get("target") or Path(r["target"]).exists())]
    # A watcher that re-ran a failed job and saw it work is the same evidence
    # as an alert that was followed by a success, and it is better evidence,
    # because nobody had to read anything for it to happen.
    rescued = any(r["id"] == "ev:D3:rescue" and r["verdict"] == "verified"
                  for r in records)
    watching = any(r["id"] == "ev:D3:watcher" and r["verdict"] == "verified"
                   for r in records)
    d3_ids = [r["id"] for r in _verified(records, "D3")] or [r["id"] for r in logged]
    noticed = healed or rescued
    d3 = (5 if (detail_ok and noticed)
          else (3 if (detail_ok or watching)
                else (1 if logged else 0)))
    code("D3", d3, d3_ids if d3 else [],
         ("a job failed once but was restarted and finished" if rescued
          else "failures are recorded, alerted, and recovery is visible")
         if d3 == 5
         else (("your jobs are checked regularly, so one that stops gets "
                "restarted" if watching
                else "runs record how they ended and alerts are on")
               if d3 == 3
               else ("something is being logged" if d3 else "nothing is logged")))

    model("D4", "Does the user read these on the cadence they arrive, or do "
                "they pile up unread?")

    report_seen = any(r["id"] == "ev:D5:report" and r["verdict"] == "verified"
                      for r in records)
    if prior_audit_runs >= 2 and prior_pick_resolved:
        d5 = 5
    elif report_seen and prior_audit_runs >= 1:
        d5 = 3
    elif report_seen:
        d5 = 1
    else:
        d5 = 0
    audits = ("1 previous audit on record" if prior_audit_runs == 1
              else f"{prior_audit_runs} previous audits on record")
    code("D5", d5, ["ev:D5:report"] if report_seen else [],
         audits + (" and the last pick was acted on" if prior_pick_resolved
                   else ", and the last pick has not been acted on yet")
         if report_seen else "this is the first audit")

    return {cid: rows[cid] for cid in CRITERIA}


def stage_for(total: int) -> str:
    for ceiling, name in STAGES:
        if total <= ceiling:
            return name
    return STAGES[-1][1]


def apply_caps(score: dict, probes: list, runs: list, today: date) -> dict:
    """Subtotals, then the caps that stop a lopsided system scoring well.

    A vault nobody can retrieve from, or a system nothing is scheduled to run,
    is not rescued by a rich skill library. The caps say so in numbers rather
    than in a paragraph the reader can skip.
    """
    subtotals = {g: 0 for g in GROUPS}
    unknown = {g: 0 for g in GROUPS}
    for row in score.values():
        if row["points"] is None:
            unknown[row["group"]] += 5
        else:
            subtotals[row["group"]] += row["points"]

    caps = []
    probe_by_name = {p["name"]: p["verdict"] for p in probes}

    def _limit(group, ceiling, why):
        """Hold a part down, and record it only if it really held it down.

        A part is capped against its FULL possible score, judged rows
        included, because that is the number the limit exists to stop.

        Two quantities get recorded, because they are different and mixing
        them printed "10 from the rows above, held to 5" on a limit of 10:
        `held_back` is what the limit costs the part's ceiling, and
        `rows_total` is what its filled rows actually add up to right now.
        A limit that binds exactly costs the ceiling nothing, so `held_back`
        is 0 and the reachable denominator is left alone.
        """
        possible = subtotals[group] + unknown[group]
        rows_total = subtotals[group]
        if subtotals[group] > ceiling:
            subtotals[group] = ceiling
        unknown[group] = min(unknown[group], max(0, ceiling - subtotals[group]))
        # A limit that pins a part to exactly its ceiling still binds it: the
        # part cannot go above the limit, and a reader told only "up to 10"
        # is never told a limit is the reason it stops there.
        if possible >= ceiling:
            caps.append({"scope": group, "ceiling": ceiling, "why": why,
                         "held_back": possible - ceiling,
                         "rows_total": rows_total,
                         "capped_to": subtotals[group]})

    if (probe_by_name.get("purpose") != "found_direct"
            or probe_by_name.get("priority") != "found_direct"):
        _limit("Context", CONTEXT_CAP,
               "the vault cannot answer a question about your own goals "
               "or your own priorities")
    if score["D1"]["points"] == 0:
        _limit("Cadence", CADENCE_CAP, "nothing is installed to run on its own")

    total_min = sum(subtotals.values())
    total_max = total_min + sum(unknown.values())

    ok_dates = _dates_of([r for r in runs
                          if r.get("rc") == 0
                          and _within(r.get("started", ""), today, RUNS_WINDOW_DAYS)])
    ceiling = 100
    reasons = []
    # Judged against what each part can still reach, not what it holds now:
    # nine rows are unfilled at this point, and limiting a part on a number
    # that is about to change is how the sentence and the table came apart.
    lowest_group = min(subtotals[g] + unknown[g] for g in GROUPS)

    def _weak(bar):
        """Name the weakest part and its score, against the same numbers the
        reader can see in the parts table.

        Three earlier attempts all failed the same way: they described the
        state of the calculation rather than the state of the system, so the
        sentence disagreed with the table printed beside it.
        """
        worst = min(GROUPS, key=lambda g: subtotals[g] + unknown[g])
        return (f"{worst} can reach at most "
                f"{subtotals[worst] + unknown[worst]}, under {bar}")

    if lowest_group < 10:
        ceiling, reasons = 49, [_weak(10)]
    elif lowest_group < 15 or len(ok_dates) < 2:
        ceiling = 69
        reasons = ([_weak(15)] if lowest_group < 15 else []) + (
            ["fewer than two days of successful runs"] if len(ok_dates) < 2 else [])
    elif lowest_group < 20:
        ceiling, reasons = 84, [_weak(20)]
    if ceiling < 100 and total_max > ceiling:
        # One limit, one row. Two reasons used to append two identical rows,
        # so the reader was told the same thing twice and any sum over the
        # list double-counted it.
        caps.append({"scope": "Total", "ceiling": ceiling,
                     "why": " and ".join(reasons),
                     "held_back": total_max - ceiling})
        total_min = min(total_min, ceiling)
        total_max = min(total_max, ceiling)

    return {
        "rows": score,
        "subtotals": subtotals,
        "unknown": unknown,
        "caps_applied": caps,
        "total_min": total_min,
        "total_max": total_max,
        "stage_min": stage_for(total_min),
        "stage_max": stage_for(total_max),
    }


# ── Rendering ────────────────────────────────────────────────────────────────
#
# Everything below is read by a person, so none of the machine vocabulary above
# reaches it. DESIGN.md's written-voice rules are enforced by a test that
# scans these strings for developer words.

SCORE_BEGIN, SCORE_END = "<!-- score:begin -->", "<!-- score:end -->"
LEDGER_BEGIN, LEDGER_END = "<!-- ledger:begin -->", "<!-- ledger:end -->"
AUTOMATION_BEGIN, AUTOMATION_END = "<!-- automation:begin -->", "<!-- automation:end -->"

KPI_BUCKETS = ("more customers", "more value per customer", "less cost")
AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3", "L4")


def row_label(cid: str) -> str:
    """How a row is named on the page: its group and its number in that group.

    The internal ids are C1 to D5, and only C spells its group. Printing them
    handed the reader a key the page did not carry, so the label is built from
    words that are on the page already.
    """
    group = CRITERIA[cid][0]
    n = [c for c in CRITERIA if CRITERIA[c][0] == group].index(cid) + 1
    return f"{group} {n}"


def cid_for_label(label: str) -> str:
    """The internal id behind a rendered row label. Inverse of `row_label`."""
    for cid in CRITERIA:
        if row_label(cid) == label.strip():
            return cid
    return label.strip()


def score_md(score: dict) -> str:
    """The score table, exactly as it appears in the report.

    Code rows arrive filled. Model rows arrive blank on purpose: the model
    fills them in the report and the validator checks each one cites something.
    """
    rows = score["rows"]
    out = [SCORE_BEGIN, "",
           "Twenty checks, each worth 0, 1, 3 or 5, in four groups. Context "
           "is what the vault holds. Connections is what reaches it. "
           "Capabilities is what it can do. Cadence is whether any of it "
           "happens without you.",
           "",
           "| # | What was checked | Score | On what basis |",
           "|---|---|---|---|"]
    for cid in CRITERIA:
        row = rows[cid]
        points = "  " if row["points"] is None else str(row["points"])
        basis = row["basis"] if row["judged_by"] == "code" else ""
        out.append(f"| {row_label(cid)} | {row['criterion']} | {points} | "
                   f"{scrub(basis)} |")
    out.append("")
    held = {c["scope"]: c for c in score["caps_applied"] if c["scope"] in GROUPS}
    out.append("| Part | Score |")
    out.append("|---|---|")
    for group in GROUPS:
        got = score["subtotals"][group]
        unknown = score["unknown"][group]
        cap = held.get(group)
        if cap and cap.get("rows_total", got) > got:
            # The rows really were cut down: show the subtraction, so adding
            # the column and reading the table give the same answer.
            cell = f"{cap['rows_total']} from the rows above, held to {got}"
        elif cap:
            # The rows already sit under the limit, so the limit binds what
            # this part can still reach, not the number printed here.
            cell = f"{got}, and cannot go above {cap['ceiling']}"
        else:
            cell = str(got) + (f" (up to {got + unknown} once judged)"
                               if unknown else "")
        out.append(f"| {group} | {cell} |")
    out.append("")
    # The denominator has to be one this run could actually reach. With a part
    # held to 10, "out of 100" measures against a number the scoring rules
    # make unreachable, so the score reads worse than the run could do.
    from_parts = 100 - sum(cap.get("held_back", 0)
                           for cap in score["caps_applied"]
                           if cap["scope"] in GROUPS)
    hard_ceiling = min([cap["ceiling"] for cap in score["caps_applied"]
                        if cap["scope"] == "Total"] or [100])
    # A part limit removes what is available; a total limit is an absolute
    # ceiling. They do not add up, so take whichever binds first.
    reachable = min(from_parts, hard_ceiling, score["total_max"]
                    if score["total_max"] > score["total_min"] else 100)
    ceiling_note = ("" if reachable >= 100 else
                    f" The most this run could have scored is {reachable}, "
                    f"because of the limits below.")

    if score["total_min"] == score["total_max"]:
        out.append(f"**Total {score['total_min']} out of 100. "
                   f"Stage: {score['stage_min']}.**" + ceiling_note)
    else:
        tail = (f", at best {score['stage_max']}.**"
                if score["stage_max"] != score["stage_min"] else ".**")
        out.append(f"**Total {score['total_min']} out of 100, rising to at most "
                   f"{score['total_max']} once the judged rows are filled. "
                   f"Stage: {score['stage_min']}" + tail + ceiling_note)
    if score["caps_applied"]:
        out.append("")
        out.append("Limits that changed the score:")
        for cap in score["caps_applied"]:
            scope = "The total" if cap["scope"] == "Total" else cap["scope"]
            cost = cap.get("held_back")
            out.append(f"- {scope} was held to {cap['ceiling']}"
                       + (f", {cost} points lower than it would have been, "
                          if cost else ", ")
                       + f"because {cap['why']}.")
    out.append("")
    out.append(SCORE_END)
    return "\n".join(out)


def automation_template(today: date, refs: list) -> str:
    """The blank the model fills for the one pick.

    Eliminate before automate: the first question is whether the thing should
    happen at all. A tool that only ever proposes building something will
    propose building something forever.
    """
    ref = ", ".join(refs[:3]) if refs else "none"
    return "\n".join([
        AUTOMATION_BEGIN,
        "",
        "Candidate: ",
        "Eliminate: ",
        "Autonomy: ",
        "KPI: ",
        "Size: ",
        f"Picked: {today.isoformat()}",
        f"Refs: {ref}",
        "",
        AUTOMATION_END,
    ])
