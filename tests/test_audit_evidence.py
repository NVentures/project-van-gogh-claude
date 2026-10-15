"""audit_evidence.py — the evidence rubric behind /van-gogh:vault-audit.

The rule these tests exist to hold: a point has to stand on a file that is
really there. An empty vault must score zero, not a polite B, and deleting the
file a row cited must cost that row its points.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import audit_evidence as ae

TODAY = date(2026, 9, 9)


# ── planting helpers ─────────────────────────────────────────────────────────

def build_paths(root: Path, **over) -> ae.AuditPaths:
    logs = root / "van-gogh" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    kw = dict(
        logs=logs, vault=root, hotcache=root / "wiki" / "hotcache.md",
        entities=root / "wiki" / "entities", sources=root / "wiki" / "sources",
        weekly=root / "wiki" / "weekly",
        week_md=root / "van-gogh" / "week.md",
        coffee_md=root / "van-gogh" / "morning-coffee.md",
        tea_md=root / "van-gogh" / "afternoon-tea.md",
        memory_md=root / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md",
        done_log=root / "wiki" / "weekly" / "done-2026.md",
        report_md=root / "van-gogh" / "vault-audit.md",
        project_pages={}, priorities={}, account_labels=[],
        items_heading="Action Items", alerts_enabled=False,
    )
    kw.update(over)
    return ae.AuditPaths(**kw)


def touch(path: Path, text: str = "x" * 200, days_old: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if days_old:
        when = datetime.combine(TODAY - timedelta(days=days_old),
                                datetime.min.time()).timestamp()
        import os
        os.utime(path, (when, when))
    return path


def plant_sidecar(paths, stem, payload, days_old=0):
    path = paths.logs / f"{stem}.json"
    payload = dict(payload)
    payload.setdefault("today", (TODAY - timedelta(days=days_old)).isoformat())
    touch(path, json.dumps(payload), days_old)
    return path


def plant_runs(paths, rows):
    (paths.logs / "runs.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return rows


def fake_job_states(state="loaded", names=("meeting-ingest", "ingest-workspace")):
    return [{"label": f"com.monet.{n}", "kind": "skill", "name": n,
             "state": state, "path": f"/tmp/{n}.plist"} for n in names]


def hollow_vault(root: Path) -> ae.AuditPaths:
    """Every route present, every page a stub. The install that looks complete
    and answers nothing."""
    paths = build_paths(root,
                        project_pages={"Acme": root / "wiki" / "acme.md"},
                        priorities={"acme": [{"name": "Close the round"}]},
                        account_labels=["Gmail"])
    touch(paths.hotcache, "---\ntitle: hot\n---\n")
    touch(root / "wiki" / "acme.md", "---\ntitle: Acme\n---\n")
    (paths.entities).mkdir(parents=True, exist_ok=True)
    touch(paths.entities / "Jane Roe.md", "---\ntags: [stub]\n---\n")
    (paths.sources).mkdir(parents=True, exist_ok=True)
    return paths


def planted_vault(root: Path) -> ae.AuditPaths:
    """A system that is genuinely working, so every code row can reach 5."""
    paths = build_paths(root,
                        project_pages={"Acme": root / "wiki" / "acme.md"},
                        priorities={"acme": [{"name": "Close the round"}]},
                        account_labels=["Gmail", "Outlook"],
                        items_heading="My Action Items",
                        alerts_enabled=True)
    touch(paths.hotcache,
          "---\ntitle: hot\n---\n\n## My Action Items\n\n"
          "- Call Jane about the Acme close\n"
          "- Send the redline back to counsel\n"
          "- Confirm the Thursday site visit\n", 1)
    touch(root / "wiki" / "acme.md", "# Acme\n" + "real content " * 20)
    touch(paths.entities / "Jane Roe.md", "# Jane Roe\n" + "she runs ops " * 20)
    touch(paths.sources / f"{TODAY.isoformat()} Acme sync.md",
          "# Acme sync\n" + "we agreed terms " * 20, 1)
    body = "\n".join(f"- item {i} that a person would actually read" for i in range(6))
    touch(paths.week_md,
          f"---\ngenerated: {(TODAY - timedelta(days=2)).isoformat()}\n---\n# Week\n{body}\n", 2)
    touch(paths.coffee_md, f"# Coffee\n{body}\n", 1)
    touch(paths.tea_md, f"# Tea\n{body}\n", 1)
    touch(paths.memory_md, f"# memory\n{body}\n", 3)
    touch(paths.done_log,
          f"## Week of {(TODAY - timedelta(days=3)).isoformat()}\n{body}\n", 3)
    for stem in ("morning_coffee_latest", "afternoon_tea_latest", "week_review_latest"):
        plant_sidecar(paths, stem, {
            "errors": [],
            "sent": [{"account": "Gmail"}, {"account": "Outlook"}]}, 1)
    touch(paths.logs / "briefing_pages.json", json.dumps(
        {"week": {"last_attempt": (TODAY - timedelta(days=8)).isoformat()}}), 1)
    (paths.logs / "failures.jsonl").write_text(json.dumps({
        "ts": (TODAY - timedelta(days=5)).isoformat(),
        "component": "scheduled.meeting-ingest", "alerted": True}) + "\n",
        encoding="utf-8")
    plant_runs(paths, [
        {"job": "scheduled.meeting-ingest", "started": (TODAY - timedelta(days=4)).isoformat(),
         "finished": "x", "rc": 0},
        {"job": "scheduled.ingest-workspace", "started": (TODAY - timedelta(days=2)).isoformat(),
         "finished": "x", "rc": 0},
        {"job": "scheduled.meeting-ingest", "started": (TODAY - timedelta(days=1)).isoformat(),
         "finished": "x", "rc": 0},
    ])
    touch(paths.report_md, "# audit")
    return paths


def score_of(paths, jobs=None, runs=None, prior=0, resolved=False):
    jobs = jobs if jobs is not None else fake_job_states()
    runs = runs if runs is not None else []
    rows = ae.collect_evidence(TODAY, paths, jobs, runs)
    probes = ae.run_probes(paths, TODAY)
    score = ae.score_criteria(rows, probes, paths, jobs, runs, TODAY, prior, resolved)
    return ae.apply_caps(score, probes, runs, TODAY), rows, probes


# ── P2: the arithmetic ───────────────────────────────────────────────────────

def test_score_rejects_a_point_outside_the_ladder():
    with pytest.raises(ValueError):
        ae._row("C2", 2, ["ev:x"], "two is not a score")
    with pytest.raises(ValueError):
        ae._row("C2", 4, ["ev:x"], "nor is four")


@pytest.mark.parametrize("total,stage", [
    (0, "Unproven"), (24, "Unproven"), (25, "Foundation"), (49, "Foundation"),
    (50, "Working"), (69, "Working"), (70, "Compounding"), (84, "Compounding"),
    (85, "Leveraged"), (100, "Leveraged"),
])
def test_score_stage_boundaries_are_pinned(total, stage):
    assert ae.stage_for(total) == stage


def _synthetic(subtotals, probes_ok=True, d1=5):
    """A score dict with chosen subtotals, for the cap calibration cases."""
    rows = {}
    for cid, (group, label, judged) in ae.CRITERIA.items():
        rows[cid] = {"id": cid, "group": group, "criterion": label,
                     "judged_by": "code", "points": 0, "evidence": ["ev:x"],
                     "basis": "planted"}
    for group, want in subtotals.items():
        ids = [c for c in ae.CRITERIA if ae.CRITERIA[c][0] == group]
        left = want
        for cid in ids:
            take = max(p for p in ae.VALID_POINTS if p <= left) if left else 0
            rows[cid]["points"] = take
            left -= take
        assert left == 0, f"{want} is not reachable on the 0/1/3/5 ladder"
    rows["D1"]["points"] = d1
    probes = [{"name": n, "verdict": "found_direct" if probes_ok else "not_found",
               "id": f"probe:{n}"}
              for n in ("purpose", "priority", "project", "entity", "source")]
    return rows, probes


@pytest.mark.parametrize("subs,ok_days,expected", [
    # Three strong groups and one weak one: the total cap binds at 69.
    ({"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 10}, 2, 69),
    # Balanced and high, two days of runs: capped at 84 from a raw 88.
    ({"Context": 23, "Connections": 23, "Capabilities": 23, "Cadence": 19}, 2, 84),
    # Balanced but only one day of successful runs: 69.
    ({"Context": 23, "Connections": 23, "Capabilities": 23, "Cadence": 23}, 1, 69),
    # Every group at 25, two days of runs: nothing binds.
    ({"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25}, 2, 100),
])
def test_score_cap_calibration(subs, ok_days, expected):
    rows, probes = _synthetic(subs)
    runs = [{"job": "scheduled.a", "started": (TODAY - timedelta(days=i)).isoformat(),
             "rc": 0} for i in range(1, ok_days + 1)]
    got = ae.apply_caps(rows, probes, runs, TODAY)
    assert got["total_min"] == expected


def test_score_context_cap_fires_when_the_vault_cannot_answer():
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25},
        probes_ok=False)
    got = ae.apply_caps(rows, probes, [], TODAY)
    assert got["subtotals"]["Context"] == ae.CONTEXT_CAP
    assert any(c["scope"] == "Context" for c in got["caps_applied"])


def test_score_cadence_cap_fires_when_nothing_is_installed():
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25}, d1=0)
    got = ae.apply_caps(rows, probes, [], TODAY)
    assert got["subtotals"]["Cadence"] == ae.CADENCE_CAP
    assert any(c["scope"] == "Cadence" for c in got["caps_applied"])


# ── P3: fresh, hollow, planted ───────────────────────────────────────────────

CODE_ROWS = [c for c, m in ae.CRITERIA.items() if m[2] == "code"]


@pytest.mark.parametrize("cid", CODE_ROWS)
def test_fresh_install_scores_zero_on_every_code_row(tmp_path, cid):
    paths = build_paths(tmp_path)
    score, _, _ = score_of(paths, jobs=fake_job_states("not installed"))
    assert score["rows"][cid]["points"] == 0


def test_fresh_install_is_unproven_and_held_down(tmp_path):
    score, _, _ = score_of(build_paths(tmp_path),
                           jobs=fake_job_states("not installed"))
    assert score["stage_min"] == "Unproven"
    assert score["subtotals"]["Context"] <= ae.CONTEXT_CAP
    assert score["subtotals"]["Cadence"] <= ae.CADENCE_CAP
    assert score["total_max"] <= 49
    # A recorded limit must really bind: it either costs the part ceiling
    # points (held_back > 0) or pins it to exactly the ceiling (held_back
    # == 0). What it must never do is hand back headroom it did not have.
    for cap in score["caps_applied"]:
        assert cap.get("held_back", 0) >= 0
        assert cap["capped_to"] <= cap["ceiling"]


@pytest.mark.parametrize("cid", CODE_ROWS)
def test_hollow_install_scores_zero_on_every_code_row(tmp_path, cid):
    """Every route exists, every page is a stub. This is the install that used
    to score a B: nothing wrong with it because nothing is in it."""
    paths = hollow_vault(tmp_path)
    score, _, _ = score_of(paths, jobs=fake_job_states("not installed"))
    assert score["rows"][cid]["points"] == 0


def test_hollow_install_is_unproven(tmp_path):
    score, _, _ = score_of(hollow_vault(tmp_path),
                           jobs=fake_job_states("not installed"))
    assert score["stage_min"] == "Unproven"
    assert score["subtotals"]["Context"] <= ae.CONTEXT_CAP
    assert score["subtotals"]["Cadence"] <= ae.CADENCE_CAP
    assert score["total_max"] <= 49


@pytest.mark.parametrize("cid", CODE_ROWS)
def test_planted_vault_scores_five_on_every_code_row(tmp_path, cid):
    paths = planted_vault(tmp_path)
    runs = [json.loads(line) for line in
            (paths.logs / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    score, _, _ = score_of(paths, runs=runs, prior=2, resolved=True)
    assert score["rows"][cid]["points"] == 5, score["rows"][cid]["basis"]


def test_planted_vault_has_neither_the_context_nor_the_cadence_cap(tmp_path):
    """A working system is not held back by the two structural limits.

    The total limit still applies, and should: nine of the twenty rows are
    judged by the model and stand at zero until it judges them, so the group
    totals really are low. That is what `total_max` is for, and it is why the
    report states a range rather than a single number.
    """
    paths = planted_vault(tmp_path)
    runs = [json.loads(line) for line in
            (paths.logs / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    score, _, _ = score_of(paths, runs=runs, prior=2, resolved=True)
    scopes = {c["scope"] for c in score["caps_applied"]}
    assert "Context" not in scopes and "Cadence" not in scopes
    assert score["total_min"] == 55
    assert score["total_max"] > score["total_min"]


# ── P4: no points without a citation ─────────────────────────────────────────

@pytest.mark.parametrize("cid", CODE_ROWS)
def test_citation_deleting_the_cited_file_costs_that_row_its_points(tmp_path, cid):
    """Per criterion, one at a time. A set-level scan would pass while one row
    quietly kept its points after its evidence was gone."""
    paths = planted_vault(tmp_path)
    runs = [json.loads(line) for line in
            (paths.logs / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    before, records, probes = score_of(paths, runs=runs, prior=2, resolved=True)
    assert before["rows"][cid]["points"] == 5

    # A row may cite either kind of record, so both lists are in scope.
    cited = {r["id"]: r for r in list(records) + list(probes)}
    targets = [cited[i]["target"] for i in before["rows"][cid]["evidence"]
               if i in cited and cited[i]["target"]]
    if not targets:
        # D1 and D2 cite the scheduler and the run log rather than a page.
        targets = [str(paths.logs / "runs.jsonl")] if cid in ("D2",) else []
    if cid == "D1":
        after, _, _ = score_of(paths, jobs=fake_job_states("not installed"),
                               runs=runs, prior=2, resolved=True)
        assert after["rows"]["D1"]["points"] == 0
        return
    assert targets, f"{cid} scored 5 while citing no file at all"
    for target in targets:
        path = Path(target)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            import shutil
            shutil.rmtree(path)
    after, _, _ = score_of(paths, runs=runs if cid != "D2" else [],
                           prior=2, resolved=True)
    assert after["rows"][cid]["points"] < 5


def test_citation_a_row_with_points_and_no_evidence_is_zeroed():
    row = ae._row("C2", 5, [], "claimed without citing anything")
    assert row["points"] == 0
    assert row["basis"] == "no_citation"


def test_citation_evidence_whose_file_vanished_does_not_count(tmp_path):
    paths = build_paths(tmp_path)
    gone = tmp_path / "not-here.md"
    records = [ae.ev("C5", "file", gone, "verified", "claims a file that is gone")]
    assert ae._verified(records, "C5") == []


# ── P5: the five probes ──────────────────────────────────────────────────────

def test_probe_all_five_found_on_a_planted_vault(tmp_path):
    probes = ae.run_probes(planted_vault(tmp_path), TODAY)
    assert [p["verdict"] for p in probes] == ["found_direct"] * 5


def test_probe_none_found_on_an_empty_vault(tmp_path):
    probes = ae.run_probes(build_paths(tmp_path), TODAY)
    assert [p["verdict"] for p in probes] == ["not_found"] * 5


def test_probe_a_moved_project_page_is_not_found(tmp_path):
    """Config still points at the old path. The system cannot find it either,
    so neither does the probe: this is the difference between a keyword search
    and following the route the product actually uses."""
    paths = planted_vault(tmp_path)
    (tmp_path / "wiki" / "acme.md").rename(tmp_path / "wiki" / "acme-renamed.md")
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["project"] == "not_found"
    assert by_name["purpose"] == "not_found"


def test_probe_priority_needs_a_bullet_under_the_configured_heading(tmp_path):
    paths = planted_vault(tmp_path)
    touch(paths.hotcache, "---\ntitle: hot\n---\n\n## My Action Items\n\n"
                          "(nothing here yet, but plenty of words to clear the stub bar)\n", 1)
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["priority"] == "not_found"


def test_probe_priority_ignores_a_bullet_in_a_different_section(tmp_path):
    paths = planted_vault(tmp_path)
    touch(paths.hotcache, "---\ntitle: hot\n---\n\n## My Action Items\n\n"
                          "no items yet, just this sentence to clear the stub bar\n\n"
                          "## Active Threads\n\n- a bullet that belongs to another section\n", 1)
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["priority"] == "not_found"


def test_probe_source_must_be_recent(tmp_path):
    paths = planted_vault(tmp_path)
    for page in paths.sources.glob("*.md"):
        page.unlink()
    old = (TODAY - timedelta(days=60)).isoformat()
    touch(paths.sources / f"{old} Ancient call.md", "# Ancient\n" + "words " * 40, 60)
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["source"] == "not_found"


@pytest.mark.parametrize("found,points", [(5, 5), (4, 3), (3, 3), (2, 1), (1, 1), (0, 0)])
def test_probe_score_ladder(found, points, tmp_path):
    paths = build_paths(tmp_path)
    probes = [{"id": f"probe:{i}", "criterion": "C2", "name": str(i), "target": "",
               "verdict": "found_direct" if i < found else "not_found", "detail": ""}
              for i in range(5)]
    score = ae.score_criteria([], probes, paths, [], [], TODAY)
    assert score["C2"]["points"] == points


def test_probe_week_freshness_reads_the_generated_date_not_the_mtime(tmp_path):
    """A vault sync touches every file. Judging the weekly briefing by mtime
    would call a three week old page fresh the moment it is copied."""
    paths = build_paths(tmp_path)
    stale = (TODAY - timedelta(days=30)).isoformat()
    touch(paths.week_md, f"---\ngenerated: {stale}\n---\n# Week\n" + "content " * 30,
          days_old=0)
    records = ae.collect_evidence(TODAY, paths, [], [])
    week = [r for r in records if r["id"] == "ev:C3:week"][0]
    assert week["verdict"] == "stale"
    assert ae.week_generated_date(paths.week_md) == date.fromisoformat(stale)


# ── P6: per-account evidence, matched exactly ────────────────────────────────

def _tea(errors=(), items=()):
    return {"errors": list(errors),
            "sent": [{"account": a} for a in items]}


def test_account_verified_needs_both_a_clean_run_and_its_own_data(tmp_path):
    got = ae.account_verdicts(_tea(items=["Gmail"]), 1, ["Gmail", "Outlook"])
    assert got == {"Gmail": "verified", "Outlook": "verified_empty"}


def test_account_failure_is_matched_by_the_exact_label(tmp_path):
    """The producer writes `Sent mail (Outlook): ...`, so the label is read out
    of the parentheses and compared whole."""
    errors = [f"Sent mail (Outlook): {RuntimeError('token expired')}"]
    got = ae.account_verdicts(_tea(errors, items=["Gmail"]), 1, ["Gmail", "Outlook"])
    assert got["Outlook"] == "failed"
    assert got["Gmail"] == "verified"


def test_account_labels_sharing_a_prefix_stay_distinct():
    """An error about "Gmail 2" must never condemn "Gmail". A prefix match
    here would make the audit worse than no audit."""
    errors = ["Inbound mail (Gmail 2): connection reset"]
    got = ae.account_verdicts(_tea(errors, items=["Gmail"]), 1, ["Gmail", "Gmail 2"])
    assert got["Gmail 2"] == "failed"
    assert got["Gmail"] == "verified"


def test_account_an_old_sidecar_is_stale_not_verified():
    got = ae.account_verdicts(_tea(items=["Gmail"]), 8, ["Gmail"])
    assert got["Gmail"] == "stale"


def test_account_no_sidecar_at_all_is_unverified():
    assert ae.account_verdicts({}, None, ["Gmail"]) == {"Gmail": "verified_empty"}
    assert ae.account_verdicts(None, None, ["Gmail"]) == {"Gmail": "unverified"}


@pytest.mark.parametrize("verdicts,points", [
    ({"Gmail": "verified", "Outlook": "verified"}, 5),
    ({"Gmail": "verified", "Outlook": "verified_empty"}, 3),
    ({"Gmail": "verified", "Outlook": "failed"}, 1),
    ({"Gmail": "verified_empty", "Outlook": "verified_empty"}, 1),
])
def test_account_score_ladder(verdicts, points, tmp_path):
    paths = build_paths(tmp_path, account_labels=list(verdicts))
    records = [ae.ev("N1", "sidecar", paths.logs, "verified", "a record exists",
                     key="tea sidecar")]
    for label, verdict in verdicts.items():
        records.append(ae.ev("N1", "sidecar", paths.logs, verdict, label,
                             key=f"account {label}"))
    score = ae.score_criteria(records, [], paths, [], [], TODAY)
    assert score["N1"]["points"] == points


# ── P7: cadence from evidence ────────────────────────────────────────────────

def _runs(*specs):
    return [{"job": job, "started": (TODAY - timedelta(days=d)).isoformat(),
             "finished": "x", "rc": rc} for job, d, rc in specs]


def test_cadence_d1_five_only_when_every_job_is_loaded(tmp_path):
    paths = build_paths(tmp_path)
    jobs = fake_job_states("loaded")
    assert ae.score_criteria([], [], paths, jobs, [], TODAY)["D1"]["points"] == 5
    jobs[1]["state"] = "not installed"
    assert ae.score_criteria([], [], paths, jobs, [], TODAY)["D1"]["points"] == 3
    assert ae.score_criteria([], [], paths, fake_job_states("on disk, not loaded"),
                             [], TODAY)["D1"]["points"] == 1
    assert ae.score_criteria([], [], paths, fake_job_states("not installed"),
                             [], TODAY)["D1"]["points"] == 0


def test_cadence_d2_needs_two_days_and_every_loaded_job(tmp_path):
    paths = build_paths(tmp_path)
    jobs = fake_job_states("loaded")
    both = _runs(("scheduled.meeting-ingest", 3, 0),
                 ("scheduled.ingest-workspace", 1, 0))
    assert ae.score_criteria([], [], paths, jobs, both, TODAY)["D2"]["points"] == 5
    # Two days, but one loaded job has never run: not a working cadence.
    one_job = _runs(("scheduled.meeting-ingest", 3, 0),
                    ("scheduled.meeting-ingest", 1, 0))
    assert ae.score_criteria([], [], paths, jobs, one_job, TODAY)["D2"]["points"] == 3


def test_cadence_d2_a_failure_after_the_last_success_downgrades(tmp_path):
    """Order matters, not just counts. Sent, then failed, is a system that has
    stopped working however many successes came before."""
    paths = build_paths(tmp_path)
    jobs = fake_job_states("loaded")
    healthy = _runs(("scheduled.meeting-ingest", 5, 0),
                    ("scheduled.ingest-workspace", 4, 0))
    assert ae.score_criteria([], [], paths, jobs, healthy, TODAY)["D2"]["points"] == 5
    broke = healthy + _runs(("scheduled.meeting-ingest", 1, 1))
    assert ae.score_criteria([], [], paths, jobs, broke, TODAY)["D2"]["points"] == 1


def test_cadence_d2_counts_only_the_recent_window(tmp_path):
    paths = build_paths(tmp_path)
    stale = _runs(("scheduled.meeting-ingest", 30, 0),
                  ("scheduled.ingest-workspace", 29, 0))
    got = ae.score_criteria([], [], paths, fake_job_states(), stale, TODAY)
    assert got["D2"]["points"] == 0


def test_cadence_d3_five_needs_an_alert_then_a_recovery(tmp_path):
    """Three is a log. Five is a loop: something broke, someone was told, and
    the thing worked again."""
    paths = build_paths(tmp_path, alerts_enabled=True)
    runs = _runs(("scheduled.meeting-ingest", 2, 0))
    (paths.logs / "failures.jsonl").write_text(json.dumps({
        "ts": (TODAY - timedelta(days=5)).isoformat(),
        "component": "scheduled.meeting-ingest", "alerted": True}) + "\n",
        encoding="utf-8")
    records = ae.collect_evidence(TODAY, paths, [], runs)
    assert ae.score_criteria(records, [], paths, [], runs, TODAY)["D3"]["points"] == 5

    # Same failure, never alerted: the loop is not closed, so it stays at 3.
    (paths.logs / "failures.jsonl").write_text(json.dumps({
        "ts": (TODAY - timedelta(days=5)).isoformat(),
        "component": "scheduled.meeting-ingest", "alerted": False}) + "\n",
        encoding="utf-8")
    records = ae.collect_evidence(TODAY, paths, [], runs)
    assert ae.score_criteria(records, [], paths, [], runs, TODAY)["D3"]["points"] == 3


def test_cadence_d3_one_when_something_is_logged_but_nobody_is_told(tmp_path):
    paths = build_paths(tmp_path, alerts_enabled=False)
    (paths.logs / "failures.jsonl").write_text("", encoding="utf-8")
    records = ae.collect_evidence(TODAY, paths, [], [])
    assert ae.score_criteria(records, [], paths, [], [], TODAY)["D3"]["points"] == 1


def test_cadence_a_loaded_job_that_never_fires_becomes_a_finding(tmp_path):
    import audit_ledger
    paths = build_paths(tmp_path)
    jobs = fake_job_states("not installed")
    records = ae.collect_evidence(TODAY, paths, jobs, [])
    found = audit_ledger.findings_from_sweep({}, tmp_path, [], records, jobs)
    assert any(f["kind"] == "job" for f in found.values())


def test_probe_priority_reads_through_subheadings(tmp_path):
    """A real action list is organised into subheadings. Ending the section at
    the first one reported an empty list on a hotcache full of tasks, which is
    how this rule was found: the live vault, not a fixture."""
    paths = planted_vault(tmp_path)
    touch(paths.hotcache, "\n".join([
        "---", "title: hot", "---", "",
        "## My Action Items", "", "### Open", "", "#### Overdue", "",
        "- [ ] **acme:** reply to the redline (due 2026-09-01)",
        "- [ ] **acme:** confirm the site visit", "",
        "## Active Threads", "", "- something else entirely", ""]), 1)
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["priority"] == "found_direct"


def test_probe_priority_still_refuses_a_bullet_from_a_later_section(tmp_path):
    """The fix must not go the other way: a bullet under a different top level
    heading is not an answer about action items."""
    paths = planted_vault(tmp_path)
    touch(paths.hotcache, "\n".join([
        "---", "title: hot", "---", "",
        "## My Action Items", "", "### Open", "",
        "nothing here yet, just a sentence to clear the stub bar", "",
        "## Active Threads", "", "- a bullet in another section", ""]), 1)
    by_name = {p["name"]: p["verdict"] for p in ae.run_probes(paths, TODAY)}
    assert by_name["priority"] == "not_found"


def test_a_limit_that_never_bit_is_not_reported(tmp_path):
    """Cold read round 4. Three rounds fixed the WORDING of a limit that never
    applied; the defect was reporting it at all. A ceiling of 10 listed beside
    a part scoring 3 reads as the explanation for a low score whose cause is
    somewhere else entirely."""
    rows, probes = _synthetic(
        {"Context": 1, "Connections": 1, "Capabilities": 1, "Cadence": 1})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    probes = [dict(p, verdict="not_found") for p in probes]
    rows["D1"]["points"] = 0
    got = ae.apply_caps(rows, probes, [], TODAY)
    for cap in got["caps_applied"]:
        assert cap.get("held_back", 0) > 0, f"{cap['scope']} listed but never bit"


def test_a_limit_that_bit_says_what_it_cost(tmp_path):
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    probes = [dict(p, verdict="not_found") for p in probes]
    got = ae.apply_caps(rows, probes, [], TODAY)
    context = [c for c in got["caps_applied"] if c["scope"] == "Context"]
    assert context and context[0]["held_back"] == 15
    assert "15 points lower" in ae.score_md(got)


def test_the_total_limit_names_a_part_by_what_it_can_still_reach(tmp_path):
    """The sentence has to agree with the table beside it, which shows what a
    part can reach once the judged rows are in."""
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 5, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    got = ae.apply_caps(rows, probes, [], TODAY)
    total = [c for c in got["caps_applied"] if c["scope"] == "Total"]
    assert total, "a binding total limit was not reported"
    assert "Connections can reach at most 5" in total[0]["why"]


def test_c3_basis_does_not_read_as_though_every_page_were_current(tmp_path):
    """Cold read round 2: a colon before the list made "2 of the 4 pages are
    current: a, b, c, d" read as all four being current."""
    paths = planted_vault(tmp_path)
    records = ae.collect_evidence(TODAY, paths, [], [])
    basis = ae.score_criteria(records, [], paths, [], [], TODAY)["C3"]["basis"]
    assert "are current:" not in basis
    assert basis.index("end of day retro") < basis.index("are current")


# ── Cold read round 3 ────────────────────────────────────────────────────────

def test_p5_does_not_award_full_marks_for_hand_work_alone(tmp_path):
    """The report scored 5 out of 5 for "the system has been used across
    several days" on a system that, two rows later, had never completed a run.
    Working in the vault by hand is evidence about the person, not the tool."""
    paths = planted_vault(tmp_path)
    by_hand = ae.score_criteria(ae.collect_evidence(TODAY, paths, [], []), [],
                                paths, [], [], TODAY)
    assert by_hand["P5"]["points"] < 5
    assert "by hand" in by_hand["P5"]["basis"]

    runs = [json.loads(line) for line in
            (paths.logs / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    with_runs = ae.score_criteria(ae.collect_evidence(TODAY, paths, [], runs), [],
                                  paths, [], runs, TODAY)
    assert with_runs["P5"]["points"] == 5
    assert "by hand" not in with_runs["P5"]["basis"]


@pytest.mark.parametrize("got,total,points", [
    (4, 4, 5), (2, 2, 5), (3, 4, 3), (2, 4, 1), (1, 2, 1), (0, 4, 0), (0, 2, 0),
])
def test_share_rows_agree_on_what_a_fraction_is_worth(got, total, points):
    """C3 and C5 scored 2 of 4 as 1 and 1 of 2 as 3, the same half, different
    numbers, no reason on the page."""
    assert ae._share(got, total) == points


def test_c3_and_c5_score_the_same_on_the_same_fraction(tmp_path):
    paths = build_paths(tmp_path)
    half_c3 = [ae.ev("C3", "file", touch(tmp_path / f"c3-{i}.md"), "verified", "x",
                     key=str(i)) for i in range(2)]
    half_c5 = [ae.ev("C5", "file", touch(tmp_path / "c5-0.md"), "verified", "x",
                     key="0")]
    score = ae.score_criteria(half_c3 + half_c5, [], paths, [], [], TODAY)
    assert score["C3"]["points"] == score["C5"]["points"]


def test_d5_says_plainly_when_the_last_pick_was_not_acted_on(tmp_path):
    """D5 claimed "the last pick was followed up" on the same page where the
    pick section said it had not been built."""
    paths = planted_vault(tmp_path)
    records = ae.collect_evidence(TODAY, paths, [], [])
    not_acted = ae.score_criteria(records, [], paths, [], [], TODAY,
                                  prior_audit_runs=1, prior_pick_resolved=False)
    assert "has not been acted on yet" in not_acted["D5"]["basis"]
    assert "1 previous audit on record" in not_acted["D5"]["basis"]
    assert "1 previous audits" not in not_acted["D5"]["basis"]

    acted = ae.score_criteria(records, [], paths, [], [], TODAY,
                              prior_audit_runs=2, prior_pick_resolved=True)
    assert "the last pick was acted on" in acted["D5"]["basis"]
    assert "2 previous audits on record" in acted["D5"]["basis"]


def test_score_table_says_what_each_group_means(tmp_path):
    """Cold read round 4 and 5: the table opened with codes and no key. It now
    names each group in a sentence, and labels its rows with those words."""
    rows, probes = _synthetic(
        {"Context": 5, "Connections": 5, "Capabilities": 5, "Cadence": 5})
    md = ae.score_md(ae.apply_caps(rows, probes, [], TODAY))
    for group in ae.GROUPS:
        assert f"{group} is what" in md or f"{group} is whether" in md


def test_score_rows_are_labelled_in_words_not_codes(tmp_path):
    """Cold read round 5: the rows were C1 to D5, and only C spelled its
    group, so the page handed the reader a key it did not carry."""
    rows, probes = _synthetic(
        {"Context": 5, "Connections": 5, "Capabilities": 5, "Cadence": 5})
    md = ae.score_md(ae.apply_caps(rows, probes, [], TODAY))
    for cid in ae.CRITERIA:
        assert f"| {cid} |" not in md, f"{cid} rendered as a bare code"
    assert "| Context 1 |" in md and "| Cadence 5 |" in md


@pytest.mark.parametrize("cid", list(ae.CRITERIA))
def test_every_row_label_round_trips(cid):
    assert ae.cid_for_label(ae.row_label(cid)) == cid


def test_a_capped_part_shows_where_its_number_came_from(tmp_path):
    """Cold read round 7 called this the report's worst line: the Context rows
    added to 15, the parts table said 10, and the reason sat two blocks lower.
    Adding the column and reading the table must give the same answer."""
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    probes = [dict(p, verdict="not_found") for p in probes]
    md = ae.score_md(ae.apply_caps(rows, probes, [], TODAY))
    assert "| Context | 25 from the rows above, held to 10 |" in md


def test_an_uncapped_part_is_just_its_number(tmp_path):
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    md = ae.score_md(ae.apply_caps(rows, probes, [], TODAY))
    assert "| Connections | 25 |" in md


def test_the_total_says_what_was_actually_reachable(tmp_path):
    """Cold read round 8: "out of 100" measured against a number the caps made
    unreachable, so the score read worse than the run could possibly do."""
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    probes = [dict(p, verdict="not_found") for p in probes]
    # Both limits bite here: Context loses 15, and the total limit of 69 is
    # lower still, so 69 is what this run could actually have reached.
    md = ae.score_md(ae.apply_caps(rows, probes, [], TODAY))
    assert "The most this run could have scored is 69" in md


def test_an_uncapped_run_says_nothing_about_a_ceiling(tmp_path):
    rows, probes = _synthetic(
        {"Context": 25, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    runs = [{"job": "a", "started": (TODAY - timedelta(days=i)).isoformat(), "rc": 0}
            for i in (1, 2)]
    md = ae.score_md(ae.apply_caps(rows, probes, runs, TODAY))
    assert "The most this run could have scored" not in md


def test_one_limit_is_reported_once_however_many_reasons_it_has(tmp_path):
    """Two reasons for the same total limit appended two identical rows, so
    the reader was told the same thing twice and any sum over the list
    double-counted it."""
    # Both reasons for the total limit fire at once: a part under 15, and
    # fewer than two days of successful runs.
    rows, probes = _synthetic(
        {"Context": 13, "Connections": 25, "Capabilities": 25, "Cadence": 25})
    for cid in ae.CRITERIA:
        rows[cid]["judged_by"] = "code"
    got = ae.apply_caps(rows, probes, [], TODAY)
    totals = [c for c in got["caps_applied"] if c["scope"] == "Total"]
    assert len(totals) == 1, "one limit, one row"
    assert " and " in totals[0]["why"], "both reasons should be stated in it"


def test_a_limit_that_binds_the_ceiling_does_not_claim_it_cut_the_rows(tmp_path):
    """Cold read round 9: the table read "10 from the rows above, held to 5"
    on a limit of 10, because the points a limit costs a part's CEILING were
    rendered as though they had been taken off its ROWS."""
    paths = build_paths(tmp_path)
    score, _, _ = score_of(paths, jobs=fake_job_states("not installed"))
    parts = [l for l in ae.score_md(score).splitlines()
             if l.startswith("| Context |")]
    assert len(parts) == 1
    row = parts[0]
    assert "from the rows above" not in row, row
    assert f"cannot go above {ae.CONTEXT_CAP}" in row, row


def test_the_reachable_ceiling_is_never_above_what_the_run_can_reach(tmp_path):
    """"The most this run could have scored is 95" appeared on a run whose own
    limits put its maximum at 49."""
    paths = build_paths(tmp_path)
    score, _, _ = score_of(paths, jobs=fake_job_states("not installed"))
    md = ae.score_md(score)
    import re
    m = re.search(r"could have scored is (\d+)", md)
    assert m, md
    assert int(m.group(1)) <= score["total_max"], md
