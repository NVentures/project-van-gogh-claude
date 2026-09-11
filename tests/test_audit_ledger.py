"""audit_ledger.py — findings remembered across runs, and the report gate.

The transition that matters most is `not_rechecked`: a finding whose scan did
not run must keep its state rather than be quietly called fixed. Reporting a
problem as solved because nobody looked is the failure that would make the
whole ledger untrustworthy.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import audit_evidence as ae
import audit_ledger as al

TODAY = date(2026, 9, 9)
ALL_SCANS = {"stub_source", "stub_entity", "orphan_entity", "stale_project",
             "log_failure", "probe", "account", "job", "pick"}


def _headline_count(md: str) -> int:
    """The number the summary line claims, whichever way it is phrased."""
    import re
    m = re.search(r"^(\d+) findings", md, re.MULTILINE)
    assert m, f"no summary line in:\n{md}"
    return int(m.group(1))


def finding(kind="stub_source", key="wiki/sources/a.md", label="a stub"):
    ident = al.finding_id(kind, key)
    return {ident: {"id": ident, "kind": kind, "key": key, "label": label}}


def empty():
    return {"version": 1, "runs": [], "findings": {}}


# ── P9: identity ─────────────────────────────────────────────────────────────

def test_id_is_stable_for_the_same_finding():
    assert al.finding_id("stub_source", "a/b.md") == al.finding_id("stub_source", "a/b.md")


def test_id_differs_by_kind_and_by_key():
    assert al.finding_id("stub_source", "a.md") != al.finding_id("stub_entity", "a.md")
    assert al.finding_id("stub_source", "a.md") != al.finding_id("stub_source", "b.md")


def test_id_survives_the_vault_moving_and_a_windows_path():
    """The same vault at two absolute roots, one with backslashes, is the same
    vault. If ids changed when someone moved their vault, every finding would
    look new and the whole history would be lost."""
    a = al.finding_id("stub_source", "/Users/x/Vault/wiki/sources/a.md",
                      Path("/Users/x/Vault"))
    b = al.finding_id("stub_source", r"D:\Vault\wiki\sources\a.md",
                      Path(r"D:\Vault"))
    c = al.finding_id("stub_source", "/mnt/other/Vault/WIKI/Sources/A.md",
                      Path("/mnt/other/Vault"))
    assert a == b == c


def test_write_json_atomic_leaves_no_temp_file(tmp_path):
    target = tmp_path / "ledger.json"
    al.write_json_atomic(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert list(tmp_path.iterdir()) == [target]


# ── P9: the six-run walk ─────────────────────────────────────────────────────

def test_status_walk_across_six_runs():
    """new, open, resolved, reopened, not_rechecked, no longer applicable."""
    item = finding()
    ident = next(iter(item))

    r1 = al.advance(empty(), item, ALL_SCANS, "r1", TODAY)
    assert r1["findings"][ident]["status"] == "new"

    r2 = al.advance(r1, item, ALL_SCANS, "r2", TODAY)
    assert r2["findings"][ident]["status"] == "open"
    assert r2["findings"][ident]["first_seen"] == TODAY.isoformat()

    r3 = al.advance(r2, {}, ALL_SCANS, "r3", TODAY)
    assert r3["findings"][ident]["status"] == "resolved"

    r4 = al.advance(r3, item, ALL_SCANS, "r4", TODAY)
    assert r4["findings"][ident]["status"] == "reopened"
    assert r4["findings"][ident]["first_seen"] == TODAY.isoformat()

    # The scan did not run this time. The finding keeps its state.
    r5 = al.advance(r4, {}, ALL_SCANS - {"stub_source"}, "r5", TODAY)
    assert r5["findings"][ident]["status"] == "not_rechecked"

    # The business was removed from config, so the finding cannot apply.
    r6 = al.advance(r5, {}, ALL_SCANS, "r6", TODAY, lambda e: False)
    assert r6["findings"][ident]["status"] == "no_longer_applicable"


def test_a_finding_is_archived_after_four_clean_runs():
    item = finding()
    ident = next(iter(item))
    ledger = al.advance(empty(), item, ALL_SCANS, "r0", TODAY)
    for i in range(1, 5):
        ledger = al.advance(ledger, {}, ALL_SCANS, f"r{i}", TODAY)
        expected = "archived" if i >= al.ARCHIVE_AFTER else "resolved"
        assert ledger["findings"][ident]["status"] == expected
    assert ident in ledger["findings"]
    assert ident not in al.ledger_md(ledger)


def test_not_rechecked_does_not_count_toward_archiving():
    item = finding()
    ident = next(iter(item))
    ledger = al.advance(empty(), item, ALL_SCANS, "r0", TODAY)
    for i in range(1, 6):
        ledger = al.advance(ledger, {}, ALL_SCANS - {"stub_source"}, f"r{i}", TODAY)
    assert ledger["findings"][ident]["status"] == "not_rechecked"


# ── P10: idempotency ─────────────────────────────────────────────────────────

def test_idempotent_second_run_produces_the_same_ids_and_no_new(tmp_path):
    item = finding()
    r1 = al.advance(empty(), item, ALL_SCANS, "2026-09-09", TODAY)
    r2 = al.advance(r1, item, ALL_SCANS, "2026-09-10", TODAY)
    assert set(r1["findings"]) == set(r2["findings"])
    assert sum(1 for f in r2["findings"].values() if f["status"] == "new") == 0


def test_idempotent_a_settled_ledger_stops_changing(tmp_path):
    """Runs 2 and 3 over an unchanged vault are identical.

    Run 1 to run 2 legitimately differs, because a finding stops being new the
    second time it is seen. From then on nothing may move except the run row,
    or a reader could never tell a real change from bookkeeping noise.
    """
    item = finding()
    score = {"total_min": 10, "total_max": 20, "stage_min": "Unproven"}
    first = al.advance(empty(), item, ALL_SCANS, "2026-09-08", TODAY)
    second = al.append_run(al.advance(first, item, ALL_SCANS, "2026-09-09", TODAY),
                           "2026-09-09", TODAY, score, "2026-09-09T07:00:00")
    third = al.append_run(al.advance(second, item, ALL_SCANS, "2026-09-09", TODAY),
                          "2026-09-09", TODAY, score, "2026-09-09T08:00:00")

    def mask(L):
        return json.dumps({**L,
                           "runs": [{**r, "ts": ""} for r in L["runs"]],
                           "findings": {k: {**v, "history": [], "last_seen": ""}
                                        for k, v in L["findings"].items()}},
                          sort_keys=True)
    assert mask(second) == mask(third)
    assert all(f["status"] == "open" for f in third["findings"].values())


def test_history_a_same_day_rerun_replaces_its_own_row():
    score = {"total_min": 1, "total_max": 2, "stage_min": "Unproven"}
    ledger = al.append_run(empty(), "2026-09-09", TODAY, score, "07:00")
    ledger = al.append_run(ledger, "2026-09-09", TODAY, score, "08:00")
    assert len(ledger["runs"]) == 1
    assert ledger["runs"][0]["ts"] == "08:00"


def test_history_is_capped():
    score = {"total_min": 1, "total_max": 2, "stage_min": "Unproven"}
    ledger = empty()
    for i in range(al.MAX_RUNS + 10):
        ledger = al.append_run(ledger, f"run-{i}", TODAY, score, str(i))
    assert len(ledger["runs"]) == al.MAX_RUNS
    assert ledger["runs"][-1]["run"] == f"run-{al.MAX_RUNS + 9}"


def test_load_ledger_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    assert al.load_ledger(path) == {"version": 1, "runs": [], "findings": {}}


# ── P11: the rendered findings list ──────────────────────────────────────────

def test_md_carries_its_markers_and_a_count_line():
    ledger = al.advance(empty(), finding(), ALL_SCANS, "r1", TODAY)
    out = al.ledger_md(ledger)
    assert out.startswith(ae.LEDGER_BEGIN) and out.rstrip().endswith(ae.LEDGER_END)
    assert "1 findings, all new this run." in out


def test_md_orders_reopened_first_then_new_then_open():
    current = {}
    for kind, key in (("stub_source", "a.md"), ("stub_entity", "b.md"),
                      ("orphan_entity", "c.md")):
        current.update(finding(kind, key, f"{kind} finding"))
    r1 = al.advance(empty(), current, ALL_SCANS, "r1", TODAY)
    r2 = al.advance(r1, current, ALL_SCANS, "r2", TODAY)
    gone = dict(list(current.items())[:2])
    r3 = al.advance(r2, gone, ALL_SCANS, "r3", TODAY)
    r4 = al.advance(r3, current, ALL_SCANS, "r4", TODAY)
    body = al.ledger_md(r4)
    reopened = body.index("came back")
    still_open = body.index("still open")
    assert reopened < still_open


def test_md_caps_the_table_and_says_how_many_more():
    current = {}
    for i in range(50):
        current.update(finding("stub_source", f"wiki/sources/{i}.md", f"stub {i}"))
    ledger = al.advance(empty(), current, ALL_SCANS, "r1", TODAY)
    out = al.ledger_md(ledger, limit=40)
    assert out.count("| stub ") == 40
    assert "10 more are not shown here" in out


def test_md_says_so_when_there_is_nothing_outstanding():
    assert "Nothing outstanding." in al.ledger_md(empty())


def test_md_carries_no_dashes_even_when_a_finding_does():
    label = f"a finding with an {chr(0x2014)} and an {chr(0x2013)} in it"
    item = finding(label=label)
    ledger = al.advance(empty(), item, ALL_SCANS, "r1", TODAY)
    out = al.ledger_md(ledger)
    assert chr(0x2014) not in out and chr(0x2013) not in out


def test_scrub_is_proven_on_a_planted_dash():
    """The scanner has to find a dash it planted itself before its zero counts."""
    planted = f"before {chr(0x2014)} after {chr(0x2013)} end"
    assert chr(0x2014) in planted and chr(0x2013) in planted
    cleaned = ae.scrub(planted)
    assert chr(0x2014) not in cleaned and chr(0x2013) not in cleaned


# ── P14: the pick carried forward ────────────────────────────────────────────

def test_pick_is_parsed_out_of_the_previous_report():
    report = "\n".join([
        "# Audit", ae.AUTOMATION_BEGIN, "",
        "Candidate: the weekly stub sweep",
        "Eliminate: keep, because the meetings it files are the ones we bill from",
        "Autonomy: L2 because a wrong delete is expensive to undo",
        "KPI: less cost, minutes spent filing per week",
        "Size: S", "Picked: 2026-09-02", "Refs: ev:C2:x", "", ae.AUTOMATION_END])
    got = al.parse_previous_pick(report)
    assert got["candidate"] == "the weekly stub sweep"
    assert got["verdict"] == "keep"
    assert got["picked"] == "2026-09-02"
    assert "less cost" in got["kpi"]


def test_pick_parse_returns_nothing_when_there_is_no_previous_report():
    assert al.parse_previous_pick("") == {}
    assert al.parse_previous_pick("# just a heading") == {}


def test_md_hides_the_first_seen_column_on_a_first_run():
    """Cold read round 1: every row said the audit date, so the column looked
    like history and carried none."""
    ledger = al.advance(empty(), finding(), ALL_SCANS, "r1", TODAY)
    assert "First seen" not in al.ledger_md(ledger)


def test_md_shows_first_seen_once_there_is_a_history():
    older = finding("stub_source", "old.md", "an older stub")
    ledger = al.advance(empty(), older, ALL_SCANS, "r1", date(2026, 8, 1))
    ledger["findings"][next(iter(older))]["first_seen"] = "2026-08-01"
    both = {**older, **finding("stub_entity", "new.md", "a newer one")}
    ledger = al.advance(ledger, both, ALL_SCANS, "r2", TODAY)
    out = al.ledger_md(ledger)
    assert "First seen" in out and "2026-08-01" in out


def test_md_shows_every_status_it_counts():
    """Cold read round 2: `not_rechecked` was counted in the summary line and
    filtered out of the table, so the headline promised a row that was not
    there. Any status the count includes must have a row a reader can see."""
    item = finding()
    ident = next(iter(item))
    ledger = al.advance(empty(), item, ALL_SCANS, "r1", TODAY)
    ledger = al.advance(ledger, {}, ALL_SCANS - {"stub_source"}, "r2", TODAY)
    assert ledger["findings"][ident]["status"] == "not_rechecked"

    out = al.ledger_md(ledger)
    rows = [l for l in out.splitlines() if l.startswith("| ") and ident in l]
    assert len(rows) == 1, "a counted finding has no row in the table"
    assert _headline_count(out) == 1


@pytest.mark.parametrize("status", ["new", "open", "reopened", "resolved",
                                    "not_rechecked"])
def test_md_row_count_always_equals_the_headline_count(status):
    """One assertion per status, so no single one can go missing quietly."""
    ident = "abc123abc123"
    ledger = {"version": 1, "runs": [], "findings": {ident: {
        "id": ident, "kind": "stub_source", "key": "a.md", "label": "a stub",
        "status": status, "first_seen": TODAY.isoformat(),
        "last_seen": TODAY.isoformat()}}}
    out = al.ledger_md(ledger)
    rows = [l for l in out.splitlines() if l.startswith(f"| {ident} ")]
    assert len(rows) == 1, f"{status} is counted but has no row"
    assert _headline_count(out) == len(rows)


def test_md_does_not_report_a_breakdown_with_one_side():
    """Cold read round 4: "46 findings: 46 still open." reads as a split and
    is not one."""
    current = {}
    for i in range(3):
        current.update(finding("stub_source", f"{i}.md", f"stub {i}"))
    ledger = al.advance(empty(), current, ALL_SCANS, "r1", TODAY)
    out = al.ledger_md(ledger)
    assert "3 findings, all new this run." in out
    assert "3 findings: 3 " not in out


def test_md_says_what_the_cut_hid_by_kind():
    """Cold read round 7: the findings section said 23 pages with no write-up
    and the visible table held 21, because the tail was cut. A reader counting
    the table has to be told it is partial, and in what."""
    current = {}
    for i in range(45):
        current.update(finding("stub_entity", f"person-{i:02d}.md", f"Person {i}"))
    for i in range(5):
        current.update(finding("orphan_entity", f"orphan-{i}.md", f"Orphan {i}"))
    ledger = al.advance(empty(), current, ALL_SCANS, "r1", TODAY)
    out = al.ledger_md(ledger, limit=40)
    assert "10 more are not shown here" in out
    assert "with no write-up" in out
    assert "any count in this report includes them" in out


def test_md_says_nothing_about_a_cut_when_nothing_was_cut():
    ledger = al.advance(empty(), finding(), ALL_SCANS, "r1", TODAY)
    assert "not shown here" not in al.ledger_md(ledger, limit=40)


def test_md_never_cuts_the_only_row_of_a_status_it_announces():
    """Cold read round 8: the header said "3 not checked this run" and the
    table showed 40 rows all reading "still open", because sorting by status
    put the announced rows last and the cut took them first."""
    current = {}
    for i in range(60):
        current.update(finding("stub_entity", f"person-{i:02d}.md", f"Person {i}"))
    ledger = al.advance(empty(), current, ALL_SCANS, "r1", TODAY)
    ledger = al.advance(ledger, current, ALL_SCANS, "r2", TODAY)

    # One finding stops being rechecked while sixty stay open.
    stale = finding("pick", "an earlier pick", "Last pick: something")
    ledger["findings"].update({k: {**v, "status": "not_rechecked",
                                   "first_seen": TODAY.isoformat(),
                                   "last_seen": TODAY.isoformat()}
                               for k, v in stale.items()})
    out = al.ledger_md(ledger, limit=40)
    assert "not checked this run" in out.split("<!-- ledger:begin -->")[1].split("\n\n")[1]
    rows = [l for l in out.splitlines() if l.startswith("| ")]
    assert any("not checked this run" in r for r in rows), \
        "a status named in the summary has no visible row"
