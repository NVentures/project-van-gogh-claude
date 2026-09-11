#!/usr/bin/env python3
"""
Vault Gardener — the deterministic sweep behind /van-gogh:vault-audit.

Walks the vault and reports hygiene findings as JSON; the skill does the
judgment (grades, the one-automation pick, confirm-gated cleanups). Checks:

- **Stale stub sources** — "(stub)" pages written by calendar_stub_check.py
  that are past the grace period with nothing added beyond the template.
  These are the reap candidates.
- **Stub entities** — entity pages that are tagged `stub` or have a near-empty
  body (never fleshed out after creation).
- **Stale project pages** — each business's project_page whose
  `last_refreshed`/`updated` frontmatter (or file mtime) is older than the
  staleness window.
- **Orphan entities** — entity pages no longer wikilinked from the hotcache,
  sources, weekly notes, or any project page.
- **Freshness** — age of each rendered artifact (week.md, morning-coffee.md,
  afternoon-tea.md, hotcache, relationship radar) so the skill can judge
  whether sources are flowing.
- **Log failures** — recent FAIL/error lines from the scheduler/script logs in
  {vault}/van-gogh/logs/.

The sweep writes exactly two files, both under `{vault}/van-gogh/logs/`: the
finding ledger (`vault_audit_ledger.json`, which is what lets a finding be
recognised next week instead of re-reported as new) and the machine copy of
this run (`vault_audit_latest.json`, which the report validator checks the
rendered report against). It touches nothing else, and `--no-write` skips even
those. Deletion is a separate, explicit
mode: `--delete PATH` re-verifies the file is still a pure stub past grace
before moving it into the vault's `.trash/` (one file per call, recoverable in
Obsidian), so the skill's confirm-gated cleanup can never take out a page the
user has since edited. Two guards protect user content: the Notes section must
still be the untouched template placeholder, AND the file's mtime must itself
be past the grace period — any edit anywhere in the file (agenda notes,
attendees, tags) bumps mtime and blocks the reap.

    python app/vault_gardener.py                      # sweep, JSON to stdout
    python app/vault_gardener.py --no-write           # sweep, write nothing
    python app/vault_gardener.py --delete PATH        # verified single-stub delete
    python app/vault_gardener.py --validate-report P  # gate a rendered report

Scan functions take explicit paths and dates so they are unit-testable with a
tmp_path vault and no config or clock.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import audit_evidence
import audit_ledger
import run_ledger
import scheduler_setup
from config_loader import (
    afternoon_tea_md_path,
    entities_dir,
    force_utf8_io,
    hotcache_path,
    logs_dir,
    morning_coffee_md_path,
    project_pages,
    relationship_radar_path,
    resolved_meta,
    sources_dir,
    user_tz,
    van_gogh_root,
    vault,
    weekly_dir,
    workspace_week_md_path,
)

# The Notes-section placeholder calendar_stub_check.py writes into every stub.
# A stub whose Notes section still holds only this line is "pure" — nothing was
# ever added — and eligible to reap once past grace. calendar_stub_check
# imports this constant so the template and the purity check can't drift apart.
STUB_NOTES_PLACEHOLDER = (
    "%% Unrecorded meeting. The recorder missed this. "
    "Add notes or delete if it didn't happen. %%"
)

# Generic tuning constants (kept as code constants — not user-specific).
STUB_GRACE_DAYS = 7        # pure stubs younger than this are left alone
STUB_DATE_KEYS = ("created", "event_date")  # stub-age frontmatter keys
PROJECT_STALE_DAYS = 21    # project page refresh window
ENTITY_STUB_BODY_MIN = 80  # body chars below which an entity counts as a stub
LOG_TAIL_BYTES = 4000      # how much of each log file to inspect
LOG_RECENT_DAYS = 7        # only logs touched this recently are inspected
MAX_LOG_LINES = 20         # cap on reported failure lines
LOG_LINE_MAX = 300         # reported log lines are truncated to this length

_FM_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Target stops at |alias, #heading, and ^block-ref suffixes.
_WIKILINK_RE = re.compile(r"\[\[([^\]|#^]+)")
_TAGS_BLOCK_RE = re.compile(r"^tags:\s*\n((?:[ \t]+-[ \t]*.+\n?)+)", re.MULTILINE)
_LOG_FAIL_RE = re.compile(r"(?i)\b(fail|failed|failure|error|traceback)\b")
# Control characters stripped from reported log lines (log content derives from
# external sources — email subjects, API errors — and is untrusted).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _read_md(path: Path) -> str | None:
    """Read a vault markdown file, or None if it can't be read.

    Strips a UTF-8 BOM (Windows editors add them; a BOM would defeat the
    frontmatter match). A single unreadable or mis-encoded page must never
    kill the whole sweep, so decode errors are treated like read errors.
    """
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except (OSError, UnicodeDecodeError):
        return None


def _frontmatter(text: str) -> str:
    m = _FM_RE.match(text)
    return m.group(1) if m else ""


def _body(text: str) -> str:
    m = _FM_RE.match(text)
    return text[m.end():] if m else text


def _fm_field(fm: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", fm, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _fm_tags(fm: str) -> set:
    """Frontmatter tags in either inline (`tags: [a, b]`) or block form."""
    tags = set(re.findall(r"[\w-]+", _fm_field(fm, "tags")))
    block = _TAGS_BLOCK_RE.search(fm)
    if block:
        tags |= set(re.findall(r"[\w-]+", block.group(1)))
    tags.discard("-")
    return tags


def _fm_date(fm: str, key: str) -> date | None:
    m = _DATE_RE.search(_fm_field(fm, key))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _mtime_date(path: Path) -> date:
    # System-local zone, while `today` uses the user's configured zone; the
    # skew is at most one day near midnight, immaterial against 7/21-day
    # thresholds, and keeps these helpers config-free (testable).
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def _age_days(path: Path, today: date, fm: str = "", keys: tuple = ()) -> int:
    """Days since a page was last touched: the first key in `keys` present in
    the frontmatter wins; falls back to file mtime when none parse."""
    for key in keys:
        d = _fm_date(fm, key)
        if d:
            return (today - d).days
    return (today - _mtime_date(path)).days


# ── Stub sources ──────────────────────────────────────────────────────────────

def _is_stub_source(path: Path, fm: str) -> bool:
    return "(stub)" in path.name or _fm_field(fm, "status") == "unrecorded"


def _is_pure_stub(text: str) -> bool:
    """Nothing added beyond the calendar_stub_check template: the frontmatter
    still says unrecorded and the Notes section holds only the placeholder."""
    fm = _frontmatter(text)
    if _fm_field(fm, "status") != "unrecorded":
        return False
    m = re.search(r"^## Notes\s*$", text, re.MULTILINE)
    if not m:
        return False
    return text[m.end():].strip() == STUB_NOTES_PLACEHOLDER


def _mtime_past_grace(path: Path, today: date, grace_days: int) -> bool:
    """Edit guard: any edit anywhere in the file bumps mtime, so a recently
    modified stub is never reaped even if its Notes section looks untouched
    (the purity check only sees the Notes section — agenda annotations,
    attendee corrections, or added frontmatter would otherwise be invisible)."""
    return (today - _mtime_date(path)).days > grace_days


def scan_stub_sources(src_dir: Path, today: date,
                      grace_days: int = STUB_GRACE_DAYS) -> dict:
    """All stub source pages, with the past-grace-and-untouched reap list."""
    stubs, reap = [], []
    if src_dir.is_dir():
        for path in sorted(src_dir.glob("*.md")):
            text = _read_md(path)
            if text is None:
                continue
            fm = _frontmatter(text)
            if not _is_stub_source(path, fm):
                continue
            age = _age_days(path, today, fm, STUB_DATE_KEYS)
            entry = {
                "path": str(path),
                "name": path.name,
                "age_days": age,
                "pure": _is_pure_stub(text),
            }
            stubs.append(entry)
            if (entry["pure"] and age > grace_days
                    and _mtime_past_grace(path, today, grace_days)):
                reap.append(entry)
    return {"total": len(stubs), "stubs": stubs, "reap_candidates": reap}


def verify_reap_candidate(path: Path, src_dir: Path, today: date,
                          grace_days: int = STUB_GRACE_DAYS) -> tuple[bool, str]:
    """Re-check a delete target immediately before removal."""
    if path.is_symlink():
        return False, "symlink"
    try:
        resolved = path.resolve()
        resolved.relative_to(src_dir.resolve())
    except (ValueError, OSError):
        return False, "outside_sources_dir"
    if not resolved.is_file() or resolved.suffix != ".md":
        return False, "not_a_source_page"
    text = _read_md(resolved)
    if text is None:
        return False, "unreadable"
    fm = _frontmatter(text)
    if not _is_stub_source(resolved, fm):
        return False, "not_a_stub"
    if not _is_pure_stub(text):
        return False, "not_pure"
    if _age_days(resolved, today, fm, STUB_DATE_KEYS) <= grace_days:
        return False, "within_grace"
    if not _mtime_past_grace(resolved, today, grace_days):
        return False, "recently_modified"
    return True, "ok"


# ── Stub entities ─────────────────────────────────────────────────────────────

def scan_stub_entities(ent_dir: Path, today: date) -> list:
    out = []
    if not ent_dir.is_dir():
        return out
    for path in sorted(ent_dir.glob("*.md")):
        text = _read_md(path)
        if text is None:
            continue
        fm = _frontmatter(text)
        body = _body(text).strip()
        if "stub" in _fm_tags(fm) or len(body) < ENTITY_STUB_BODY_MIN:
            out.append({
                "path": str(path),
                "name": _fm_field(fm, "title") or path.stem,
                "age_days": _age_days(path, today, fm, ("updated", "created")),
                "body_chars": len(body),
            })
    return out


# ── Stale project pages ───────────────────────────────────────────────────────

def scan_stale_projects(pages: dict, today: date,
                        stale_days: int = PROJECT_STALE_DAYS) -> list:
    """`pages` maps display_name → Path. Missing pages are reported too."""
    out = []
    for name, path in sorted(pages.items()):
        if not path.is_file():
            out.append({"business": name, "path": str(path),
                        "age_days": None, "missing": True})
            continue
        text = _read_md(path)
        if text is None:
            # Unreadable page: surface it (age unknown) rather than crash.
            out.append({"business": name, "path": str(path),
                        "age_days": None, "missing": False})
            continue
        fm = _frontmatter(text)
        age = _age_days(path, today, fm, ("last_refreshed", "updated"))
        if age > stale_days:
            out.append({"business": name, "path": str(path),
                        "age_days": age, "missing": False})
    return out


# ── Orphan entities ───────────────────────────────────────────────────────────

def _link_targets(files: list) -> set:
    """Every [[wikilink]] target (lowercased) across the given files/dirs.

    Directories are walked recursively (sources/weekly often use subfolders).
    Path-qualified links ([[people/Jane Roe]]) also register their basename.
    """
    targets = set()
    for item in files:
        paths = sorted(item.rglob("*.md")) if item.is_dir() else [item]
        for path in paths:
            if not path.is_file():
                continue
            text = _read_md(path)
            if text is None:
                continue
            for m in _WIKILINK_RE.finditer(text):
                t = m.group(1).strip().lower()
                targets.add(t)
                if "/" in t:
                    targets.add(t.rsplit("/", 1)[-1])
    return targets


def scan_orphan_entities(ent_dir: Path, corpus: list) -> list:
    """Entity pages neither their stem nor title is wikilinked from `corpus`
    (a list of files and/or directories). Advisory: a link the corpus doesn't
    cover (e.g. a note outside the scanned dirs) reads as an orphan."""
    if not ent_dir.is_dir():
        return []
    linked = _link_targets(corpus)
    out = []
    for path in sorted(ent_dir.glob("*.md")):
        text = _read_md(path)
        if text is None:
            continue
        fm = _frontmatter(text)
        names = {path.stem.lower()}
        title = _fm_field(fm, "title")
        if title:
            names.add(title.lower())
        if not (names & linked):
            out.append({"path": str(path), "name": title or path.stem})
    return out


# ── Freshness + logs ──────────────────────────────────────────────────────────

def scan_freshness(artifacts: list, today: date) -> list:
    """`artifacts` is a list of (name, Path). Reports existence and age."""
    out = []
    for name, path in artifacts:
        exists = path.is_file()
        out.append({
            "name": name,
            "path": str(path),
            "exists": exists,
            "age_days": (today - _mtime_date(path)).days if exists else None,
        })
    return out


def scan_log_failures(log_dir: Path, today: date) -> list:
    out = []
    if not log_dir.is_dir():
        return out
    for path in sorted(log_dir.glob("*.log")):
        if (today - _mtime_date(path)).days > LOG_RECENT_DAYS:
            continue
        try:
            with open(path, "rb") as f:
                f.seek(max(0, path.stat().st_size - LOG_TAIL_BYTES))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in tail.splitlines():
            if _LOG_FAIL_RE.search(line):
                clean = _CTRL_RE.sub("", line).strip()[:LOG_LINE_MAX]
                out.append({"log": path.name, "line": clean})
                if len(out) >= MAX_LOG_LINES:
                    return out
    return out


def _trash(path: Path) -> Path:
    """Move a verified stub into the vault's native .trash/ folder.

    Obsidian's "Deleted files" convention — the stub disappears from the
    vault but stays recoverable, so even a reap that slipped past the guards
    is reversible. Collision-safe: an existing name gets a numeric suffix.
    """
    trash_dir = vault() / ".trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    dest = trash_dir / path.name
    n = 1
    while dest.exists():
        dest = trash_dir / f"{path.stem} ({n}){path.suffix}"
        n += 1
    path.rename(dest)
    return dest


# ── Entry point ───────────────────────────────────────────────────────────────

# Words that carry no identity, so a rewording of the same pick is recognised
# as the same pick. Without this the ledger promised "ids stay the same from
# week to week" on a page carrying one item under three ids.
_PICK_STOPWORDS = frozenset("""
a an the of to in on for and or so that this these those your you их its it is
are be been with from by as at than then instead rather into about
""".split())


def _pick_words(candidate: str) -> set:
    """The words that carry a pick's meaning, as a set."""
    words = re.findall(r"[a-z0-9]+", str(candidate).lower())
    return {w for w in words if w not in _PICK_STOPWORDS and len(w) > 2}


def _same_pick(a: str, b: str) -> bool:
    """Are these two sentences describing the same pick?

    Exact token equality is too strict for prose: the same item rewritten
    keeps its subject and changes its trimmings. Two thirds of the smaller
    vocabulary shared is the same pick; less than that is a new one.
    """
    wa, wb = _pick_words(a), _pick_words(b)
    if not wa or not wb:
        return str(a).strip().lower() == str(b).strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.66


def _pick_key(candidate: str, prev_key: str = "") -> str:
    """A stable identity for one pick, independent of how it was phrased.

    A pick that recurs keeps last week's key, so the findings list can hold
    its promise that an id means the same thing from week to week.
    """
    words = sorted(_pick_words(candidate))
    key = " ".join(words[:12]) or str(candidate).strip().lower()[:80]
    if prev_key and _same_pick(key, prev_key):
        return prev_key
    return key


def _scans_run(src: Path, ent: Path, pages: dict, jobs: list) -> set:
    """Which scans actually looked this run.

    A finding may only be called fixed by the scan that would have found it.
    If the sources folder was unreadable, every stub in it is "not checked",
    not "gone", and the difference is the whole value of the ledger.
    """
    out = set()
    if src.is_dir():
        out.add("stub_source")
    if ent.is_dir():
        out |= {"stub_entity", "orphan_entity"}
    if pages:
        out.add("stale_project")
    if logs_dir().is_dir():
        out.add("log_failure")
    out.add("probe")
    out.add("account")
    if jobs and jobs[0].get("state") != "unsupported":
        out.add("job")
    return out


def run_sweep(today: date, job_states: list | None = None,
              write: bool = True) -> dict:
    """The hygiene scans, plus the evidence score and the finding ledger.

    `job_states` is injected so tests never touch the real scheduler, and so a
    machine with no scheduler at all scores Cadence honestly rather than
    crashing.
    """
    src, ent = sources_dir(), entities_dir()
    pages = project_pages()
    corpus = [hotcache_path(), src, weekly_dir()] + list(pages.values())
    artifacts = [
        ("week.md", workspace_week_md_path()),
        ("morning-coffee.md", morning_coffee_md_path()),
        ("afternoon-tea.md", afternoon_tea_md_path()),
        ("hotcache", hotcache_path()),
        ("relationship-radar", relationship_radar_path()),
    ]
    out = {
        "meta": resolved_meta(),
        "today": today.isoformat(),
        "grace_days": STUB_GRACE_DAYS,
        "project_stale_days": PROJECT_STALE_DAYS,
        "report_path": str(van_gogh_root() / "vault-audit.md"),
        "stub_sources": scan_stub_sources(src, today),
        "stub_entities": scan_stub_entities(ent, today),
        "stale_projects": scan_stale_projects(pages, today),
        "orphan_entities": scan_orphan_entities(ent, corpus),
        "freshness": scan_freshness(artifacts, today),
        "log_failures": scan_log_failures(logs_dir(), today),
    }

    import config_loader
    paths = audit_evidence.paths_from_config(config_loader)
    if job_states is None:
        try:
            job_states = scheduler_setup.job_states(sys.platform)
        except Exception:                                       # noqa: BLE001
            job_states = [{"label": "", "kind": "", "name": "",
                           "state": "unsupported", "path": ""}]
    runs = run_ledger.read_runs(paths.logs / run_ledger.RUNS_NAME)

    ledger_path = logs_dir() / "vault_audit_ledger.json"
    sidecar_path = logs_dir() / "vault_audit_latest.json"
    prev = audit_ledger.load_ledger(ledger_path)
    prev_report = ""
    try:
        prev_report = paths.report_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        prev_report = ""
    previous_pick = audit_ledger.parse_previous_pick(prev_report)
    if previous_pick.get("picked") == today.isoformat():
        # Same day: this is this run's own pick being read back, not last
        # week's. Reporting it as history asks the reader to judge the
        # follow-up on a decision taken minutes ago.
        previous_pick = {}

    records = audit_evidence.collect_evidence(today, paths, job_states, runs)
    probes = audit_evidence.run_probes(paths, today)
    prior_runs = len(prev.get("runs", []))
    # A pick counts as followed up only when the CANDIDATE CHANGED: the same
    # thing picked again is the thing that did not get done. Treating "an
    # audit ran before" as follow-up made the score claim a follow-up on the
    # same page where the pick section said it had not been built.
    prior_pick = ""
    for entry in prev.get("findings", {}).values():
        if entry.get("kind") == "pick":
            prior_pick = entry.get("key", "")
            break
    pick_resolved = bool(prior_pick) and bool(previous_pick.get("candidate")) \
        and not _same_pick(_pick_key(previous_pick["candidate"]), prior_pick)
    score = audit_evidence.score_criteria(records, probes, paths, job_states,
                                          runs, today, prior_runs, pick_resolved)
    score = audit_evidence.apply_caps(score, probes, runs, today)

    current = audit_ledger.findings_from_sweep(out, paths.vault, probes,
                                               records, job_states)
    if previous_pick.get("candidate"):
        key = _pick_key(previous_pick["candidate"], prior_pick)
        ident = audit_ledger.finding_id("pick", key, paths.vault)
        current[ident] = {
            "id": ident, "kind": "pick", "key": key,
            "label": f"Last pick: {previous_pick['candidate']}",
        }

    configured_pages = {audit_ledger._normalize_key(str(v), paths.vault)
                        for v in pages.values()}

    def still_configured(entry):
        if entry.get("kind") != "stale_project":
            return True
        return entry.get("key") in configured_pages

    run_id = f"{today.isoformat()}"
    ledger = audit_ledger.advance(prev, current, _scans_run(src, ent, pages, job_states),
                                  run_id, today, still_configured)
    ledger = audit_ledger.append_run(ledger, run_id, today, score,
                                     run_ledger.now_stamp())

    refs = audit_ledger.open_ids(ledger)[:3] or [p["id"] for p in probes
                                                 if p["verdict"] != "found_direct"][:1]
    out.update({
        "evidence": records,
        "probes": probes,
        "score": score,
        "ledger": ledger,
        "previous_pick": previous_pick,
        "score_md": audit_evidence.score_md(score),
        "ledger_md": audit_ledger.ledger_md(ledger),
        "automation_md": audit_evidence.automation_template(today, refs),
        "sidecar_path": str(sidecar_path),
        "ledger_path": str(ledger_path),
    })

    if write:
        audit_ledger.write_json_atomic(ledger_path, ledger)
        audit_ledger.write_json_atomic(sidecar_path, {
            "today": out["today"],
            "evidence": records,
            "probes": probes,
            "score": score,
            "ledger": ledger,
            "previous_pick": previous_pick,
        })
    return out


def main() -> None:
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Vault hygiene sweep.")
    parser.add_argument("--delete", metavar="PATH",
                        help="Verified delete of one stale stub source page.")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="Override today (testing/backdated sweeps).")
    parser.add_argument("--validate-report", metavar="PATH", dest="validate_report",
                        help="Check a rendered vault-audit.md against this run's "
                             "own numbers. Exits 1 and names every broken rule.")
    parser.add_argument("--no-write", action="store_true", dest="no_write",
                        help="Sweep without writing the ledger or the sidecar.")
    args = parser.parse_args()

    if args.validate_report:
        result = audit_ledger.validate_report(
            Path(args.validate_report),
            logs_dir() / "vault_audit_latest.json",
            vault())
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0 if result["ok"] else 1)

    if args.delete and args.today:
        # A forged "today" would bypass the grace window on a delete.
        parser.error("--delete always uses the real clock; drop --today.")

    today = (date.fromisoformat(args.today) if args.today
             else datetime.now(user_tz()).date())

    if args.delete:
        target = Path(args.delete)
        ok, reason = verify_reap_candidate(target, sources_dir(), today)
        trashed_to = None
        if ok:
            try:
                # Move the resolved path (the verified file and the moved file
                # must be the same filesystem entry) into the vault's native
                # .trash/, so a reaped stub stays recoverable in Obsidian.
                trashed_to = str(_trash(target.resolve()))
            except OSError:
                ok, reason = False, "trash_failed"
        print(json.dumps({"deleted": ok, "reason": reason, "path": str(target),
                          "trashed_to": trashed_to}, ensure_ascii=False))
        sys.exit(0 if ok else 1)

    out = run_sweep(today, write=not args.no_write)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write(
        "SUMMARY "
        f"stubs={out['stub_sources']['total']} "
        f"reap={len(out['stub_sources']['reap_candidates'])} "
        f"stub_entities={len(out['stub_entities'])} "
        f"stale_projects={len(out['stale_projects'])} "
        f"orphans={len(out['orphan_entities'])} "
        f"log_failures={len(out['log_failures'])} "
        f"score={out['score']['total_min']}/{out['score']['total_max']} "
        f"stage={out['score']['stage_min']}\n"
    )


if __name__ == "__main__":
    main()
