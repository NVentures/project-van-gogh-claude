"""--validate-report: the code gate on a model-written report.

The report is written by a model from numbers written by code, so every number
it carries is checked back against the sidecar. These tests are a mutation
matrix: a valid report passes, and each single mutation trips exactly the rule
it should, named by its exact reason string.
"""
import json
from datetime import date
from pathlib import Path

import pytest

import audit_evidence as ae
import audit_ledger as al

TODAY = date(2026, 9, 9)


@pytest.fixture
def scene(tmp_path):
    """A sidecar and a matching report that validates cleanly."""
    vault = tmp_path
    (vault / "wiki").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "acme.md").write_text("# Acme\n" + "content " * 30,
                                            encoding="utf-8")

    rows = {}
    for cid, (group, label, judged) in ae.CRITERIA.items():
        if judged == "code":
            rows[cid] = {"id": cid, "group": group, "criterion": label,
                         "judged_by": "code", "points": 3,
                         "evidence": ["ev:x"], "basis": "planted evidence"}
        else:
            rows[cid] = {"id": cid, "group": group, "criterion": label,
                         "judged_by": "model", "points": None,
                         "evidence": [], "basis": "a hint for the model"}
    probes = [{"id": f"probe:{n}", "name": n, "criterion": "C2",
               "verdict": "found_direct", "target": "", "detail": ""}
              for n in ("purpose", "priority", "project", "entity", "source")]
    score = ae.apply_caps(rows, probes, [], TODAY)

    item = {"abc123def456": {"id": "abc123def456", "kind": "stub_source",
                             "key": "wiki/sources/a.md", "label": "a stub page",
                             "status": "new", "first_seen": TODAY.isoformat(),
                             "last_seen": TODAY.isoformat()}}
    ledger = {"version": 1, "runs": [], "findings": item}

    sidecar = {"today": TODAY.isoformat(),
               "evidence": [{"id": "ev:x"}], "probes": probes,
               "score": score, "ledger": ledger, "previous_pick": {}}
    sidecar_path = tmp_path / "vault_audit_latest.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    # The judged rows, filled the way the skill tells the model to fill them.
    score_block = ae.score_md(score)
    for cid, row in rows.items():
        if row["judged_by"] != "model":
            continue
        label = ae.row_label(cid)
        old = f"| {label} | {row['criterion']} |    |  |"
        new = (f"| {label} | {row['criterion']} | 3 | "
               f"read wiki/acme.md, it is written in their own words |")
        assert old in score_block, old
        score_block = score_block.replace(old, new)

    filled = {cid: 3 for cid in ae.CRITERIA}
    subtotals = {g: 0 for g in ae.GROUPS}
    for cid, points in filled.items():
        subtotals[ae.CRITERIA[cid][0]] += points
    total = sum(subtotals.values())
    for cap in score["caps_applied"]:
        if cap["scope"] == "Total":
            total = min(total, cap["ceiling"])
        elif cap["scope"] in subtotals:
            over = subtotals[cap["scope"]] - cap["ceiling"]
            if over > 0:
                total -= over
    score_block = _retotal(score_block, total)
    scene_total = total

    report = "\n".join([
        "# Vault audit", "", f"Audit date: {TODAY.isoformat()}", "",
        score_block, "",
        al.ledger_md(ledger), "",
        ae.AUTOMATION_BEGIN, "",
        "Candidate: the weekly stub sweep",
        "Eliminate: keep, because the meetings it files are the ones we bill from",
        "Autonomy: L2 because a wrong delete is expensive to undo by hand",
        "KPI: less cost, minutes spent filing each week",
        "Size: S",
        f"Picked: {TODAY.isoformat()}",
        "Refs: abc123def456",
        "", ae.AUTOMATION_END, ""])
    report_path = tmp_path / "vault-audit.md"
    report_path.write_text(report, encoding="utf-8")
    return {"report": report_path, "sidecar": sidecar_path, "vault": vault,
            "text": report, "total": scene_total}


def _retotal(block, total):
    """Replace the whole Total sentence, whichever shape the render used.

    With judged rows still blank the render states a range; once the model
    fills them the report states one number, which is what this fixture is.
    """
    import re
    out, done = [], False
    for line in block.splitlines():
        if line.startswith("**Total ") and not done:
            out.append(f"**Total {total} out of 100. Stage: {ae.stage_for(total)}.**")
            done = True
        else:
            out.append(line)
    assert done, "no Total line to rewrite"
    return "\n".join(out)


def check(scene):
    return al.validate_report(scene["report"], scene["sidecar"], scene["vault"])


def mutate(scene, old, new):
    text = scene["report"].read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"mutation target absent: {old!r}"
    scene["report"].write_text(text.replace(old, new, 1), encoding="utf-8")
    return check(scene)


# ── the control ──────────────────────────────────────────────────────────────

def test_a_valid_report_passes(scene):
    got = check(scene)
    assert got["ok"] is True, got["reasons"]


# ── one mutation per reason ──────────────────────────────────────────────────

def test_missing_marker(scene):
    got = mutate(scene, ae.SCORE_BEGIN, "")
    assert "missing_marker:score" in got["reasons"] and not got["ok"]


def test_missing_ledger_marker(scene):
    got = mutate(scene, ae.LEDGER_END, "")
    assert "missing_marker:ledger" in got["reasons"]


def test_missing_automation_marker(scene):
    got = mutate(scene, ae.AUTOMATION_BEGIN, "")
    assert "missing_marker:automation" in got["reasons"]


def test_score_mismatch_on_a_code_row(scene):
    """The model improved a number the code decided."""
    got = mutate(scene, "| Context 2 | A question about the work can be answered from the vault | 3 |",
                 "| Context 2 | A question about the work can be answered from the vault | 5 |")
    assert "score_mismatch:C2" in got["reasons"]


def test_model_points_invalid(scene):
    got = mutate(scene, "| Context 1 | The vault holds the user's own goals in their words | 3 |",
                 "| Context 1 | The vault holds the user's own goals in their words | 4 |")
    assert "model_points_invalid:C1" in got["reasons"]


def test_citation_unresolved(scene):
    got = mutate(scene, "read wiki/acme.md, it is written in their own words",
                 "it looks fine to me")
    assert "citation_unresolved:C1" in got["reasons"]


def test_missing_basis(scene):
    got = mutate(scene, "| Context 1 | The vault holds the user's own goals in their words | 3 | "
                        "read wiki/acme.md, it is written in their own words |",
                 "| Context 1 | The vault holds the user's own goals in their words | 3 |  |")
    assert "missing_basis:C1" in got["reasons"]


def test_total_mismatch(scene):
    got = mutate(scene, f"**Total {scene['total']} out of 100", "**Total 77 out of 100")
    assert any(r.startswith("total_mismatch:") for r in got["reasons"])


def test_stage_mismatch(scene):
    got = mutate(scene, f"Stage: {ae.stage_for(scene['total'])}.", "Stage: Leveraged.")
    assert any(r.startswith("stage_mismatch:") for r in got["reasons"])


def test_ledger_row_missing(scene):
    got = mutate(scene, "abc123def456 | a stub page", "         | a stub page")
    assert "ledger_row_missing:abc123def456" in got["reasons"]


def test_dash_found(scene):
    got = mutate(scene, "# Vault audit", f"# Vault audit {chr(0x2014)} weekly")
    assert any(r.startswith("dash_found:") for r in got["reasons"])


def test_developer_word(scene):
    got = mutate(scene, "# Vault audit", "# Vault audit (sidecar copy)")
    assert "developer_word:sidecar" in got["reasons"]


def test_audit_date_mismatch(scene):
    got = mutate(scene, f"Audit date: {TODAY.isoformat()}", "Audit date: 2026-01-01")
    assert "audit_date_mismatch:2026-01-01" in got["reasons"]


def test_missing_audit_date(scene):
    got = mutate(scene, f"Audit date: {TODAY.isoformat()}", "")
    assert "missing_audit_date" in got["reasons"]


def test_citation_needs_path_for_full_marks(scene):
    """Five is the strongest claim in the report, so it must point at a file
    a reader can open, not at an internal identifier."""
    got = mutate(scene, "| Context 1 | The vault holds the user's own goals in their words | 3 | "
                        "read wiki/acme.md, it is written in their own words |",
                 "| Context 1 | The vault holds the user's own goals in their words | 5 | "
                 "see finding abc123def456 |")
    assert "citation_needs_path:C1" in got["reasons"]


def test_report_unreadable(tmp_path, scene):
    got = al.validate_report(tmp_path / "nope.md", scene["sidecar"], scene["vault"])
    assert got["reasons"] == ["report_unreadable"]


def test_sidecar_unreadable(tmp_path, scene):
    got = al.validate_report(scene["report"], tmp_path / "nope.json", scene["vault"])
    assert got["reasons"] == ["sidecar_unreadable"]


# ── P13: the eliminate-first block ───────────────────────────────────────────

def test_automation_missing_candidate(scene):
    got = mutate(scene, "Candidate: the weekly stub sweep", "Candidate: ")
    assert "automation_missing_candidate" in got["reasons"]


def test_automation_eliminate_no_verdict(scene):
    got = mutate(scene, "Eliminate: keep, because the meetings it files are the ones we bill from",
                 "Eliminate: this one seems worth doing on the whole, probably")
    assert "automation_eliminate_no_verdict" in got["reasons"]


def test_automation_eliminate_no_reason(scene):
    got = mutate(scene, "Eliminate: keep, because the meetings it files are the ones we bill from",
                 "Eliminate: keep")
    assert "automation_eliminate_no_reason" in got["reasons"]


def test_automation_missing_autonomy_when_kept(scene):
    got = mutate(scene, "Autonomy: L2 because a wrong delete is expensive to undo by hand", "")
    assert "automation_missing_autonomy" in got["reasons"]


def test_automation_autonomy_needs_a_why_not_lower(scene):
    got = mutate(scene, "Autonomy: L2 because a wrong delete is expensive to undo by hand",
                 "Autonomy: L2")
    assert "automation_autonomy_no_justification" in got["reasons"]


def test_automation_stop_must_not_carry_autonomy(scene):
    """If the answer is to stop doing it, there is no autonomy level to set."""
    text = scene["report"].read_text(encoding="utf-8")
    text = text.replace(
        "Eliminate: keep, because the meetings it files are the ones we bill from",
        "Eliminate: stop, nobody has read these filings in the last four months")
    scene["report"].write_text(text, encoding="utf-8")
    got = check(scene)
    assert "automation_stop_has_autonomy" in got["reasons"]


def test_automation_stop_needs_the_stop_title(scene):
    text = scene["report"].read_text(encoding="utf-8")
    text = text.replace(
        "Eliminate: keep, because the meetings it files are the ones we bill from",
        "Eliminate: stop, nobody has read these filings in the last four months")
    text = text.replace("Autonomy: L2 because a wrong delete is expensive to undo by hand", "")
    scene["report"].write_text(text, encoding="utf-8")
    got = check(scene)
    assert "automation_stop_wrong_title" in got["reasons"]


def test_automation_kpi_not_in_list(scene):
    got = mutate(scene, "KPI: less cost, minutes spent filing each week",
                 "KPI: general vibes about the process improving")
    assert "automation_kpi_not_in_list" in got["reasons"]


def test_automation_kpi_needs_a_metric(scene):
    got = mutate(scene, "KPI: less cost, minutes spent filing each week", "KPI: less cost")
    assert "automation_kpi_no_metric" in got["reasons"]


def test_automation_missing_size(scene):
    got = mutate(scene, "Size: S", "Size: enormous")
    assert "automation_missing_size" in got["reasons"]


def test_automation_picked_must_be_this_run(scene):
    got = mutate(scene, f"Picked: {TODAY.isoformat()}", "Picked: 2026-08-01")
    assert "automation_picked_stale:2026-08-01" in got["reasons"]


def test_automation_refs_unresolved(scene):
    got = mutate(scene, "Refs: abc123def456", "Refs: some notes I made")
    assert "automation_refs_unresolved" in got["reasons"]


def test_automation_refs_none_is_refused_while_findings_are_open(scene):
    got = mutate(scene, "Refs: abc123def456", "Refs: none")
    assert "automation_refs_none_but_findings_open" in got["reasons"]


# ── P14: the previous pick ───────────────────────────────────────────────────

def test_pick_previous_outcome_is_required(scene):
    side = json.loads(scene["sidecar"].read_text(encoding="utf-8"))
    side["previous_pick"] = {"candidate": "last week's sweep", "verdict": "keep",
                             "kpi": "less cost", "picked": "2026-09-02"}
    scene["sidecar"].write_text(json.dumps(side), encoding="utf-8")
    got = check(scene)
    assert "automation_missing_previous_pick" in got["reasons"]


def test_pick_previous_outcome_must_name_a_real_outcome(scene):
    side = json.loads(scene["sidecar"].read_text(encoding="utf-8"))
    side["previous_pick"] = {"candidate": "last week's sweep", "verdict": "keep",
                             "kpi": "less cost", "picked": "2026-09-02"}
    scene["sidecar"].write_text(json.dumps(side), encoding="utf-8")
    text = scene["report"].read_text(encoding="utf-8")
    scene["report"].write_text(
        text.replace("Candidate:", "Previous pick: we talked about it\n\nCandidate:"),
        encoding="utf-8")
    got = check(scene)
    assert "automation_previous_pick_no_outcome" in got["reasons"]


def test_pick_previous_outcome_accepted(scene):
    side = json.loads(scene["sidecar"].read_text(encoding="utf-8"))
    side["previous_pick"] = {"candidate": "last week's sweep", "verdict": "keep",
                             "kpi": "less cost", "picked": "2026-09-02"}
    scene["sidecar"].write_text(json.dumps(side), encoding="utf-8")
    text = scene["report"].read_text(encoding="utf-8")
    scene["report"].write_text(
        text.replace("Candidate:", "Previous pick: built, it runs on Fridays\n\nCandidate:"),
        encoding="utf-8")
    got = check(scene)
    assert got["ok"] is True, got["reasons"]


# ── Cold read round 1: an evidence tag reached a sentence a person reads ─────

def test_evidence_tag_in_a_basis_is_refused(scene):
    """A cold reader hit "(probe:source)" mid-sentence and could not look it
    up. The id is fine in the findings list; inside prose it is the tool's own
    bookkeeping printed to the customer."""
    got = mutate(scene, "read wiki/acme.md, it is written in their own words",
                 "read wiki/acme.md and probe:source agrees")
    assert "evidence_tag_in_prose:C1" in got["reasons"]


def test_evidence_tag_anywhere_else_in_the_report_is_refused(scene):
    got = mutate(scene, "# Vault audit", "# Vault audit\n\nSee ev:N5:failure log.")
    assert any(r.startswith("evidence_tag_in_prose:") for r in got["reasons"])


def test_a_finding_id_in_the_findings_list_is_still_fine(scene):
    """The fix must not go too far: the findings table is where ids belong."""
    got = check(scene)
    assert got["ok"] is True, got["reasons"]


def test_a_cited_path_with_spaces_in_it_resolves(scene):
    """Real vault filenames carry spaces, and a citation naming one must count.

    A path regex whose segments could not hold a space refused every citation
    in a vault whose meeting pages are named "Acme sync 1-1 - 2026-09-08.md",
    so nine judged rows could never be cited at all.
    """
    vault = scene["vault"]
    (vault / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "sources" / "Acme sync 1-1 - 2026-09-08.md").write_text(
        "# a meeting\n", encoding="utf-8")

    text = scene["text"].replace(
        "read wiki/acme.md, it is written in their own words",
        "wiki/sources/Acme sync 1-1 - 2026-09-08.md carries a written summary")
    scene["report"].write_text(text, encoding="utf-8")

    out = al.validate_report(scene["report"], scene["sidecar"], vault)
    assert out["ok"], out["reasons"]


def test_prose_before_a_cited_path_is_not_dragged_into_it():
    """The words around a citation stay out of it, spaces in paths or not."""
    assert al._PATH_CITE_RE.findall("read wiki/acme.md today") == ["wiki/acme.md"]
    assert al._PATH_CITE_RE.findall("no path here at all") == []
    assert al._PATH_CITE_RE.findall(
        "see wiki/goals.md and wiki/p/Acme Holdings.md now"
    ) == ["wiki/goals.md", "wiki/p/Acme Holdings.md"]


def test_only_the_findings_the_table_showed_are_required(scene):
    """ledger_md caps its table, so the check may only ask for rows it drew.

    Requiring every open id failed a report that pasted ledger_md verbatim
    exactly as the skill instructs, which is a gate no correct report passes.
    """
    ledger = json.loads(scene["sidecar"].read_text(encoding="utf-8"))
    findings = ledger["ledger"]["findings"]
    for i in range(al.LEDGER_MD_CAP + 6):
        ident = f"{i:012x}"
        findings[ident] = {"id": ident, "kind": "stub_source",
                           "key": f"wiki/sources/{i}.md",
                           "label": f"stub page {i}", "status": "open",
                           "first_seen": TODAY.isoformat(),
                           "last_seen": TODAY.isoformat()}
    scene["sidecar"].write_text(json.dumps(ledger), encoding="utf-8")

    shown = al.shown_ids(ledger["ledger"])
    assert len(shown) > al.LEDGER_MD_CAP, "fixture must overflow the cap"

    block = al.ledger_md(ledger["ledger"])
    assert "not shown here" in block, "fixture must be truncated"

    text = _swap_block(scene["text"], ae.LEDGER_BEGIN, ae.LEDGER_END, block)
    scene["report"].write_text(text, encoding="utf-8")

    out = al.validate_report(scene["report"], scene["sidecar"], scene["vault"])
    assert out["ok"], out["reasons"]


def test_a_finding_the_table_did_show_is_still_required(scene):
    """The cap narrows the check, it does not switch it off."""
    block = al.ledger_md(json.loads(
        scene["sidecar"].read_text(encoding="utf-8"))["ledger"])
    gutted = block.replace("abc123def456", "xxxxxxxxxxxx")
    text = _swap_block(scene["text"], ae.LEDGER_BEGIN, ae.LEDGER_END, gutted)
    scene["report"].write_text(text, encoding="utf-8")

    out = al.validate_report(scene["report"], scene["sidecar"], scene["vault"])
    assert "ledger_row_missing:abc123def456" in out["reasons"]


def _swap_block(text, begin, end, replacement):
    head = text.split(begin)[0]
    tail = text.split(end, 1)[1]
    return head + replacement + tail
