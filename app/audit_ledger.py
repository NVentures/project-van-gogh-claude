#!/usr/bin/env python3
"""The finding ledger: what the audit found, remembered across runs.

Every week the audit rewrote its report from scratch, so a stub page flagged
in March and still there in September looked equally new both times, and
nothing recorded that a finding had ever been fixed. A report with no memory
cannot tell improvement from repetition.

So each finding gets an id derived from what it is about, not from where it
appeared in a list, and the ledger carries that id forward. A finding that
disappears while its scan ran is resolved; one that comes back is reopened;
one whose scan did not run this time is left alone rather than being called
fixed, which is the mistake that would quietly erase real problems.

The second half of this module is the report validator. The rendered report is
written by a model, and a model asked to copy a number will occasionally
improve it. So the numbers are checked against the sidecar the sweep wrote,
every judged row must cite something that resolves, and a report that fails
says exactly which rule it broke.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import audit_evidence as ae

LEDGER_VERSION = 1
MAX_RUNS = 52
LEDGER_MD_CAP = 40
ARCHIVE_AFTER = 4

STATUS_WORDS = {
    "new": "new this run",
    "open": "still open",
    "reopened": "came back",
    "resolved": "fixed",
    "not_rechecked": "not checked this run",
    "no_longer_applicable": "no longer applies",
    "archived": "archived",
}
# Sort order for the findings table, and, separately, exactly which statuses
# reach it. They are two different questions: using one list for both is how a
# status ended up counted in the summary line and missing from the rows.
_ORDER = ("reopened", "new", "open", "not_rechecked", "resolved")
_SHOWN = _ORDER


# ── Identity ─────────────────────────────────────────────────────────────────

def _normalize_key(key: str, vault: Path | None = None) -> str:
    """A key that means the same thing on two machines and two drives.

    The same vault opened at a different absolute path, or on Windows, must
    produce the same finding id, or every id changes the first time someone
    moves their vault and the whole history is lost.
    """
    text = str(key).replace("\\", "/")
    if vault is not None:
        root = str(Path(vault)).replace("\\", "/").rstrip("/")
        low, rootlow = text.lower(), root.lower()
        if root and low.startswith(rootlow):
            text = text[len(root):]
    return text.strip("/").lower()


def finding_id(kind: str, key: str, vault: Path | None = None) -> str:
    raw = f"{kind}|{_normalize_key(key, vault)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def write_json_atomic(path: Path, payload: dict) -> None:
    """Temp file then replace: a reader never sees a half-written ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".van-gogh-tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                              default=str) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ── Findings from a sweep ────────────────────────────────────────────────────

_DIGITS_RE = re.compile(r"\d+")
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w .@%+-]+){2,}")


def _log_signature(line: str) -> str:
    """Collapse a log line to the shape of its failure, safely.

    Numbers and paths change every run; the failure does not. Same idea as
    `self_anneal.signature_for`, which solves this for exceptions.

    The result becomes a finding key and is written to the ledger file, so it
    goes through the same scrub as anything a person reads. Masking digits
    alone left an address or a credential in a synced file, which is the one
    thing a hygiene tool must never do.
    """
    text = ae.scrub(str(line), limit=300)
    text = _PATH_RE.sub("PATH", text)
    return _DIGITS_RE.sub("N", text)


def findings_from_sweep(sweep: dict, vault: Path, probes: list,
                        records: list, job_states: list) -> dict:
    """Everything worth carrying to next week, keyed by id."""
    out = {}

    def add(kind, key, label):
        ident = finding_id(kind, key, vault)
        out[ident] = {"id": ident, "kind": kind,
                      "key": _normalize_key(key, vault),
                      "label": ae.scrub(label)}

    for entry in sweep.get("stub_sources", {}).get("reap_candidates", []):
        add("stub_source", entry["path"],
            f"{entry['name']} is an empty placeholder from {entry['age_days']} days ago")
    for entry in sweep.get("stub_entities", []):
        add("stub_entity", entry["path"], f"{entry['name']} has no write-up")
    for entry in sweep.get("stale_projects", []):
        age = entry.get("age_days")
        add("stale_project", entry["path"],
            f"{entry['business']} page " + ("does not exist" if entry.get("missing")
                                            else f"has not been refreshed in {age} days"))
    for entry in sweep.get("orphan_entities", []):
        add("orphan_entity", entry["path"], f"{entry['name']} is linked from nowhere")
    for entry in sweep.get("log_failures", []):
        add("log_failure", f"{entry['log']}|{_log_signature(entry['line'])}",
            f"{entry['log']} recorded: {entry['line']}")
    for probe in probes:
        if probe["verdict"] != "found_direct":
            add("probe", probe["name"], f"Cannot answer: {probe['detail']}")
    for record in records:
        if record["criterion"] == "N1" and record["verdict"] == "failed":
            add("account", record["id"], record["observed"])
    for row in job_states:
        if row.get("state") in ("not installed", "on disk, not loaded"):
            add("job", row.get("label", ""),
                f"{row.get('name', 'a job')} is {row.get('state')}")
    return out


# ── Advancing the ledger ─────────────────────────────────────────────────────

def load_ledger(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        return {"version": LEDGER_VERSION, "runs": [], "findings": {}}
    data.setdefault("version", LEDGER_VERSION)
    data.setdefault("runs", [])
    data.setdefault("findings", {})
    return data


def advance(prev: dict, current: dict, scans_run: set, run_id: str,
            today: date, still_configured=None) -> dict:
    """Carry last run's findings forward against this run's.

    The rule that matters: a finding whose scan did not run is `not_rechecked`
    and keeps its previous state. Calling it fixed because we did not look is
    how a broken system reports itself healthy.
    """
    findings = {}
    prev_findings = prev.get("findings", {}) or {}

    for ident, item in current.items():
        old = prev_findings.get(ident)
        entry = dict(item)
        if old is None:
            entry.update({"status": "new", "first_seen": today.isoformat(),
                          "last_seen": today.isoformat(), "resolved_on": None,
                          "resolved_streak": 0,
                          "history": [{"run": run_id, "status": "new"}]})
        else:
            was = old.get("status", "open")
            status = "reopened" if was in ("resolved", "archived") else (
                "open" if was in ("new", "open", "reopened", "not_rechecked") else "open")
            entry.update({
                "status": status,
                "first_seen": old.get("first_seen", today.isoformat()),
                "last_seen": today.isoformat(),
                "resolved_on": None,
                "resolved_streak": 0,
                "history": (old.get("history", []) + [{"run": run_id, "status": status}])[-20:],
            })
        findings[ident] = entry

    for ident, old in prev_findings.items():
        if ident in findings:
            continue
        entry = dict(old)
        kind = entry.get("kind", "")
        if still_configured is not None and not still_configured(entry):
            entry["status"] = "no_longer_applicable"
            entry["resolved_on"] = entry.get("resolved_on") or today.isoformat()
        elif kind not in scans_run:
            # The scan did not run. Say so; do not guess.
            entry["status"] = "not_rechecked"
        else:
            streak = int(entry.get("resolved_streak", 0)) + 1
            entry["resolved_streak"] = streak
            entry["resolved_on"] = entry.get("resolved_on") or today.isoformat()
            entry["status"] = "archived" if streak >= ARCHIVE_AFTER else "resolved"
        entry["history"] = (entry.get("history", [])
                            + [{"run": run_id, "status": entry["status"]}])[-20:]
        findings[ident] = entry

    runs = [r for r in prev.get("runs", []) if r.get("run") != run_id]
    return {"version": LEDGER_VERSION, "runs": runs, "findings": findings}


def append_run(ledger: dict, run_id: str, today: date, score: dict,
               ts: str) -> dict:
    """Add this run to the trail, replacing a same-day rerun rather than
    stacking a second row for the same day."""
    row = {
        "run": run_id,
        "date": today.isoformat(),
        "ts": ts,
        "total_min": score["total_min"],
        "total_max": score["total_max"],
        "stage_min": score["stage_min"],
        "open": sum(1 for f in ledger["findings"].values()
                    if f.get("status") in ("new", "open", "reopened")),
    }
    runs = [r for r in ledger.get("runs", []) if r.get("run") != run_id]
    runs.append(row)
    ledger["runs"] = runs[-MAX_RUNS:]
    return ledger


def open_ids(ledger: dict) -> list:
    return sorted(ident for ident, f in ledger.get("findings", {}).items()
                  if f.get("status") in ("new", "open", "reopened"))


def shown_ids(ledger: dict) -> list:
    """The ids ledger_md renders, in the order it renders them. The validator
    reads this so it only ever asks the report for rows the table showed."""
    findings = [f for f in ledger.get("findings", {}).values()
                if f.get("status") in _SHOWN]
    order = {s: i for i, s in enumerate(_ORDER)}
    findings.sort(key=lambda f: (order.get(f.get("status", "open"), 99),
                                 f.get("first_seen", ""), f.get("id", "")))
    return [f.get("id", "") for f in findings]


def ledger_md(ledger: dict, limit: int = LEDGER_MD_CAP) -> str:
    """The findings list a person reads. Archived rows stay in the file and
    out of the report: the point of archiving is that nobody needs to see it."""
    # Only the rows a reader can actually see are counted. Counting a hidden
    # row made the summary line disagree with the table underneath it, which
    # is the fastest way to teach someone the numbers cannot be trusted.
    findings = [f for f in ledger.get("findings", {}).values()
                if f.get("status") in _SHOWN]
    counts = {}
    for f in findings:
        counts[f.get("status", "open")] = counts.get(f.get("status", "open"), 0) + 1

    order = {s: i for i, s in enumerate(_ORDER)}
    findings.sort(key=lambda f: (order.get(f.get("status", "open"), 99),
                                 f.get("first_seen", ""), f.get("id", "")))

    # Every status the summary line mentions gets at least one visible row.
    # Sorting by status alone put "not checked this run" last, so the header
    # announced three findings the table then cut, and the reader could not
    # find a single one of them.
    if len(findings) > limit:
        head, tail = findings[:limit], findings[limit:]
        shown = {f.get("status") for f in head}
        for state in _ORDER:
            if not counts.get(state) or state in shown:
                continue
            promoted = next((f for f in tail if f.get("status") == state), None)
            if promoted is None:
                continue
            tail.remove(promoted)
            tail.insert(0, head.pop())
            head.append(promoted)
            shown.add(state)
        head.sort(key=lambda f: (order.get(f.get("status", "open"), 99),
                                 f.get("first_seen", ""), f.get("id", "")))
        findings = head + tail

    out = [ae.LEDGER_BEGIN, ""]
    if not findings:
        out += ["Nothing outstanding.", "", ae.LEDGER_END]
        return "\n".join(out)

    present = [s for s in _ORDER if counts.get(s)]
    if len(present) == 1:
        # A breakdown with one side is not a breakdown.
        out.append(f"{len(findings)} findings, all "
                   f"{STATUS_WORDS.get(present[0], present[0])}.")
    else:
        summary = ", ".join(f"{counts[s]} {STATUS_WORDS.get(s, s)}"
                            for s in present)
        out.append(f"{len(findings)} findings: {summary}.")
    out.append("")
    out.append("Ids stay the same from week to week, so a finding that comes "
               "back is the same one, not a new one.")
    out.append("")
    # On a first run every row was first seen today, so the column says
    # nothing while looking like it does. Show it only once it distinguishes
    # an old problem from a new one.
    seen = {f.get("first_seen", "") for f in findings}
    dated = len(seen) > 1
    out.append("| Id | Finding | State |" + (" First seen |" if dated else ""))
    out.append("|---|---|---|" + ("---|" if dated else ""))
    for f in findings[:limit]:
        row = (f"| {f['id']} | {ae.scrub(f.get('label', ''))} | "
               f"{STATUS_WORDS.get(f.get('status', 'open'), 'still open')} |")
        out.append(row + (f" {f.get('first_seen', '')} |" if dated else ""))
    if len(findings) > limit:
        hidden = findings[limit:]
        kinds = {}
        for f in hidden:
            kinds[f.get("kind", "other")] = kinds.get(f.get("kind", "other"), 0) + 1
        # Say what the cut hid, by kind. Without this a reader counts a
        # category in the table, gets a smaller number than the findings
        # section states, and has no way to know the table was partial.
        words = {"stub_entity": "with no write-up",
                 "orphan_entity": "linked from nowhere",
                 "stub_source": "empty meeting placeholders",
                 "stale_project": "project pages out of date",
                 "log_failure": "recorded failures",
                 "probe": "questions the vault could not answer",
                 "account": "accounts with a problem",
                 "job": "jobs not installed",
                 "pick": "earlier picks"}
        parts = [f"{n} {words.get(k, k)}"
                 for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])]
        out.append("")
        out.append(f"{len(hidden)} more are not shown here: "
                   + ", ".join(parts) + ". They are all in the ledger file, "
                   "so any count in this report includes them.")
    out += ["", ae.LEDGER_END]
    return "\n".join(out)


# ── The report validator ─────────────────────────────────────────────────────
#
# The report is written by a model from a sidecar written by code. Everything
# the code already decided is checked back against the sidecar, so a number
# that drifted in transcription is caught rather than shipped. Every reason is
# an exact string, because a test asserts on it and a fix loop reads it.

_MARKERS = {
    "score": (ae.SCORE_BEGIN, ae.SCORE_END),
    "ledger": (ae.LEDGER_BEGIN, ae.LEDGER_END),
    "automation": (ae.AUTOMATION_BEGIN, ae.AUTOMATION_END),
}
_ROW_RE = re.compile(
    r"^\|\s*(?:([CNPD]\d)|(Context|Connections|Capabilities|Cadence)\s+(\d))"
    r"\s*\|([^|]*)\|([^|]*)\|(.*)\|\s*$", re.MULTILINE)
_TOTAL_RE = re.compile(r"\*\*Total\s+(\d+)\s+out of 100")
_STAGE_RE = re.compile(r"Stage:\s*([A-Za-z]+)")
_AUDIT_DATE_RE = re.compile(r"^Audit date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_KPI_RE = re.compile(r"^KPI:\s*(.+)$", re.MULTILINE)
_SIZE_RE = re.compile(r"^Size:\s*([SML])\s*$", re.MULTILINE)
_PICKED_RE = re.compile(r"^Picked:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
_CAND_RE = re.compile(r"^Candidate:[ \t]*(.*)$", re.MULTILINE)
_ELIM_RE = re.compile(r"^Eliminate:\s*(.+)$", re.MULTILINE)
_AUTON_RE = re.compile(r"^Autonomy:\s*(L[0-4])\b(.*)$", re.MULTILINE)
_REFS_RE = re.compile(r"^Refs:\s*(.+)$", re.MULTILINE)
_PREV_RE = re.compile(r"^Previous pick:\s*(.+)$", re.MULTILINE)
_ID_RE = re.compile(r"\b([0-9a-f]{12})\b")
# A vault-relative path. Real vault filenames carry spaces ("Nobel-Carina 1-1
# - 2026-09-08.md"), so a segment may hold them, but a segment may not START
# with one: that is what stops "read wiki/acme.md" dragging the word "read"
# into the citation. Directory segments stay space-free, which keeps the match
# anchored on a real folder name rather than running backwards through prose.
_PATH_CITE_RE = re.compile(
    r"(?:[\w.-]+[/\\])+[\w.-][^/\\|]*?\.(?:md|json|jsonl)"
    r"|(?:^|(?<=[\s(]))[\w.-]+\.(?:md|json|jsonl)")
_EVID_RE = re.compile(r"\b((?:ev|probe):[A-Za-z0-9_ .:-]+)")
_DEV_WORDS = (
    "verified_empty", "not_rechecked", "no_longer_applicable", "found_direct",
    "no_citation", "judged_by", "sidecar", "json", "null", "traceback",
    "boolean", "enum", "rc=", "jsonl", "subtotals", "caps_applied",
)
# An evidence tag inside a sentence: "(probe:source)", "(ev:N5:failure log)".
# Legal in the machine copy, never in a line a person reads.
_EV_IN_PROSE_RE = re.compile(r"(?<![\w:])(?:ev|probe):[A-Za-z0-9_ .-]+")
_STOP_VERDICTS = ("stop", "keep")
_OUTCOMES = ("built", "not built", "stopped", "dropped")


def _eliminate_verdict(body: str) -> str:
    """Which verdict an Eliminate line gave, wherever the word sits in it.

    "keep, because the notes it files are the ones we bill from" is how a
    person writes this, so the verdict is the first of the two words that
    appears, not whatever word happens to end the line.
    """
    positions = [(body.find(v), v) for v in _STOP_VERDICTS
                 if re.search(rf"\b{v}\b", body)]
    return min(positions)[1] if positions else ""


def _between(text: str, begin: str, end: str) -> str:
    i = text.find(begin)
    j = text.find(end, i + 1) if i >= 0 else -1
    return text[i + len(begin):j] if (i >= 0 and j > i) else ""


def validate_report(report_path: Path, sidecar_path: Path,
                    vault: Path) -> dict:
    """Exit-0-or-not for a rendered audit report. Reasons are exact strings."""
    reasons = []
    try:
        text = Path(report_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"ok": False, "reasons": ["report_unreadable"]}
    try:
        side = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {"ok": False, "reasons": ["sidecar_unreadable"]}

    for name, (begin, end) in _MARKERS.items():
        if begin not in text or end not in text:
            reasons.append(f"missing_marker:{name}")
    if reasons:
        return {"ok": False, "reasons": reasons}

    # No dash anywhere, and no machine vocabulary anywhere.
    for i, line in enumerate(text.splitlines(), 1):
        if chr(0x2014) in line or chr(0x2013) in line:
            reasons.append(f"dash_found:{i}")
    low = text.lower()
    for word in _DEV_WORDS:
        if word in low:
            reasons.append(f"developer_word:{word}")
    outside_findings = text.replace(_between(text, ae.LEDGER_BEGIN, ae.LEDGER_END), "")
    if _EV_IN_PROSE_RE.search(outside_findings):
        found = _EV_IN_PROSE_RE.search(outside_findings).group(0)
        reasons.append(f"evidence_tag_in_prose:{found.split(':')[0]}")

    audit_date = _AUDIT_DATE_RE.search(text)
    if not audit_date:
        reasons.append("missing_audit_date")
    elif audit_date.group(1) != str(side.get("today", "")):
        reasons.append(f"audit_date_mismatch:{audit_date.group(1)}")

    # ── the score table ──────────────────────────────────────────────────────
    score = side.get("score", {})
    rows = score.get("rows", {})
    block = _between(text, ae.SCORE_BEGIN, ae.SCORE_END)
    found = {}
    for m in _ROW_RE.finditer(block):
        found[m.group(1) or ae.cid_for_label(f"{m.group(2)} {m.group(3)}")] = m
    if len(found) != len(ae.CRITERIA):
        reasons.append(f"score_row_count:{len(found)}")

    known_ev = {e["id"] for e in side.get("evidence", [])}
    known_ev |= {p["id"] for p in side.get("probes", [])}
    known_findings = set(side.get("ledger", {}).get("findings", {}))

    recomputed = {}
    for cid, meta in ae.CRITERIA.items():
        m = found.get(cid)
        if m is None:
            reasons.append(f"score_row_missing:{cid}")
            continue
        cell = m.group(5).strip()
        expected = rows.get(cid, {})
        if expected.get("judged_by") == "code":
            if cell != str(expected.get("points")):
                reasons.append(f"score_mismatch:{cid}")
            recomputed[cid] = expected.get("points") or 0
            continue
        # A judged row: the model filled it, so check it hard.
        if cell not in ("0", "1", "3", "5"):
            reasons.append(f"model_points_invalid:{cid}")
            continue
        points = int(cell)
        recomputed[cid] = points
        basis = m.group(6).strip()
        if not basis:
            reasons.append(f"missing_basis:{cid}")
            continue
        if _EV_IN_PROSE_RE.search(basis):
            # The reader cannot look this up and would not know to try.
            reasons.append(f"evidence_tag_in_prose:{cid}")
        if points > 0:
            cites_id = bool(_ID_RE.search(basis) and _ID_RE.search(basis).group(1)
                            in known_findings)
            cites_ev = any(t in known_ev for t in _EVID_RE.findall(basis))
            paths = [p for p in _PATH_CITE_RE.findall(basis)
                     if (Path(vault) / p.strip()).exists()]
            if not (cites_id or cites_ev or paths):
                reasons.append(f"citation_unresolved:{cid}")
            elif points == 5 and not paths:
                # Full marks are the strongest claim in the report, so they
                # have to point at a file a reader can open.
                reasons.append(f"citation_needs_path:{cid}")

    # ── total and stage ──────────────────────────────────────────────────────
    total_m = _TOTAL_RE.search(block)
    if not total_m:
        reasons.append("missing_total")
    else:
        claimed = int(total_m.group(1))
        subtotals = {g: 0 for g in ae.GROUPS}
        for cid, points in recomputed.items():
            subtotals[ae.CRITERIA[cid][0]] += points
        expected_total = sum(subtotals.values())
        for cap in score.get("caps_applied", []):
            if cap.get("scope") == "Total":
                expected_total = min(expected_total, cap.get("ceiling", 100))
            elif cap.get("scope") in subtotals:
                over = subtotals[cap["scope"]] - cap.get("ceiling", 100)
                if over > 0:
                    expected_total -= over
        if claimed != expected_total:
            reasons.append(f"total_mismatch:{claimed}/{expected_total}")
        stage_m = _STAGE_RE.search(block)
        want_stage = ae.stage_for(claimed)
        if not stage_m:
            reasons.append("missing_stage")
        elif stage_m.group(1) != want_stage:
            reasons.append(f"stage_mismatch:{stage_m.group(1)}")

    for cap in score.get("caps_applied", []):
        if str(cap.get("ceiling")) not in block:
            reasons.append(f"cap_not_stated:{cap.get('scope')}")

    # ── the findings list ────────────────────────────────────────────────────
    ledger_block = _between(text, ae.LEDGER_BEGIN, ae.LEDGER_END)
    # Only the rows the renderer actually emitted are required. ledger_md caps
    # the table and says how many it left out, so demanding every open id would
    # fail a report that pasted ledger_md verbatim exactly as instructed. The
    # cut has to follow the renderer's own ordering, not id order, or the check
    # asks for rows the table never showed.
    for ident in shown_ids(side.get("ledger", {}))[:LEDGER_MD_CAP]:
        if ident not in ledger_block:
            reasons.append(f"ledger_row_missing:{ident}")

    # ── the one pick ─────────────────────────────────────────────────────────
    reasons += _validate_automation(text, side, known_ev, known_findings)

    return {"ok": not reasons, "reasons": sorted(set(reasons))}


def _validate_automation(text: str, side: dict, known_ev: set,
                         known_findings: set) -> list:
    """The eliminate-first block. Every rule here exists because the old
    section could only ever say "build this"."""
    reasons = []
    block = _between(text, ae.AUTOMATION_BEGIN, ae.AUTOMATION_END)

    cand = _CAND_RE.search(block)
    # An unfilled template line is the commonest way this block ships wrong,
    # so a blank value has to fail as loudly as a missing line.
    if not cand or len(cand.group(1).split()) < 2:
        reasons.append("automation_missing_candidate")

    elim = _ELIM_RE.search(block)
    verdict = ""
    if not elim:
        reasons.append("automation_missing_eliminate")
    else:
        body = elim.group(1).strip().lower()
        verdict = _eliminate_verdict(body)
        if not verdict:
            reasons.append("automation_eliminate_no_verdict")
        elif len(body.split()) < 9:
            reasons.append("automation_eliminate_no_reason")

    auton = _AUTON_RE.search(block)
    if verdict == "keep":
        if not auton:
            reasons.append("automation_missing_autonomy")
        elif len(auton.group(2).split()) < 4:
            reasons.append("automation_autonomy_no_justification")
    elif verdict == "stop" and auton:
        reasons.append("automation_stop_has_autonomy")
    if verdict == "stop" and "One Thing To Stop Doing" not in text:
        reasons.append("automation_stop_wrong_title")

    kpi = _KPI_RE.search(block)
    if not kpi:
        reasons.append("automation_missing_kpi")
    else:
        body = kpi.group(1).strip().lower()
        if not any(b in body for b in ae.KPI_BUCKETS):
            reasons.append("automation_kpi_not_in_list")
        elif len(body.split()) < 4:
            reasons.append("automation_kpi_no_metric")

    if not _SIZE_RE.search(block):
        reasons.append("automation_missing_size")

    picked = _PICKED_RE.search(block)
    if not picked:
        reasons.append("automation_missing_picked")
    elif picked.group(1) != str(side.get("today", "")):
        reasons.append(f"automation_picked_stale:{picked.group(1)}")

    refs = _REFS_RE.search(block)
    open_count = len(open_ids(side.get("ledger", {})))
    if not refs:
        reasons.append("automation_missing_refs")
    else:
        body = refs.group(1).strip()
        resolves = (any(t in known_ev for t in _EVID_RE.findall(body))
                    or any(i in known_findings for i in _ID_RE.findall(body)))
        if body.lower() == "none":
            if open_count:
                reasons.append("automation_refs_none_but_findings_open")
        elif not resolves:
            reasons.append("automation_refs_unresolved")

    prior = side.get("previous_pick") or {}
    if prior.get("candidate"):
        prev = _PREV_RE.search(text)
        if not prev:
            reasons.append("automation_missing_previous_pick")
        elif not any(o in prev.group(1).lower() for o in _OUTCOMES):
            reasons.append("automation_previous_pick_no_outcome")
    return reasons


def parse_previous_pick(report_text: str) -> dict:
    """Read last week's pick out of last week's report, so this week can ask
    whether it happened. A pick nobody follows up on is a wish."""
    if not report_text:
        return {}
    block = _between(report_text, ae.AUTOMATION_BEGIN, ae.AUTOMATION_END)
    if not block:
        return {}
    cand = _CAND_RE.search(block)
    kpi = _KPI_RE.search(block)
    picked = _PICKED_RE.search(block)
    elim = _ELIM_RE.search(block)
    verdict = _eliminate_verdict(elim.group(1).strip().lower()) if elim else ""
    return {
        "candidate": ae.scrub(cand.group(1).strip()) if cand else "",
        "verdict": verdict,
        "kpi": ae.scrub(kpi.group(1).strip()) if kpi else "",
        "picked": picked.group(1) if picked else "",
    }
