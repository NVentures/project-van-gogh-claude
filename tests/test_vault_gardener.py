"""vault_gardener.py — the /van-gogh:vault-audit sweep + verified stub delete.

All scan functions take explicit paths and an explicit `today`, so these tests
build a throwaway vault in tmp_path and never touch config or the clock.
Reap candidacy checks file mtime as an edit guard, so stub factories backdate
mtime to the stub's created date; tests that need "recently touched" simply
skip the backdate (a freshly written file has a current mtime).
"""
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import audit_evidence as ae
import calendar_stub_check
import vault_gardener as vg

TODAY = date(2026, 8, 12)


def backdate(path: Path, d: date) -> None:
    ts = time.mktime((d.year, d.month, d.day, 12, 0, 0, 0, 0, -1))
    os.utime(path, (ts, ts))


def make_stub(src_dir: Path, name: str, created: str, extra_notes: str = "",
              fresh_mtime: bool = False) -> Path:
    """A source stub matching the calendar_stub_check template."""
    event = {"title": name, "attendees": ["Jane Roe"],
             "agenda": "Agenda text", "source": "Gmail"}
    text = calendar_stub_check.build_stub(event, created, created)
    if extra_notes:
        text += f"\n{extra_notes}\n"
    path = src_dir / f"{created} {name} (stub).md"
    path.write_text(text, encoding="utf-8")
    if not fresh_mtime:
        backdate(path, date.fromisoformat(created))
    return path


# ── Stub sources / reap candidates ────────────────────────────────────────────

def test_pure_old_stub_is_reap_candidate(tmp_path):
    make_stub(tmp_path, "Acme sync", "2026-07-01")
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 1
    assert len(result["reap_candidates"]) == 1
    assert result["reap_candidates"][0]["pure"] is True


def test_stub_within_grace_is_not_reaped(tmp_path):
    make_stub(tmp_path, "Acme sync", "2026-08-10")  # 2 days old
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 1
    assert result["reap_candidates"] == []


def test_stub_with_added_notes_is_not_reaped(tmp_path):
    make_stub(tmp_path, "Acme sync", "2026-07-01",
              extra_notes="Actually we discussed the Q3 pricing here.")
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 1
    assert result["stubs"][0]["pure"] is False
    assert result["reap_candidates"] == []


def test_recently_edited_stub_is_not_reaped_even_if_notes_pure(tmp_path):
    """Edit guard: an old created date but a fresh mtime (the user touched the
    file — e.g. annotated the agenda, which purity can't see) blocks the reap."""
    make_stub(tmp_path, "Acme sync", "2026-07-01", fresh_mtime=True)
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["stubs"][0]["pure"] is True
    assert result["reap_candidates"] == []


def test_non_stub_pages_are_ignored(tmp_path):
    (tmp_path / "2026-07-01 Real meeting.md").write_text(
        "---\ntype: source\ntitle: Real meeting\ncreated: 2026-07-01\n---\n\n# Notes\nreal content",
        encoding="utf-8")
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 0


def test_undecodable_file_does_not_kill_the_sweep(tmp_path):
    (tmp_path / "latin1 (stub).md").write_bytes(b"---\nstatus: unrecorded\n---\n\xe9\xff")
    make_stub(tmp_path, "Good stub", "2026-07-01")
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 1  # bad file skipped, good one still scanned


def test_bom_does_not_hide_frontmatter(tmp_path):
    path = make_stub(tmp_path, "Bom stub", "2026-07-01")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    backdate(path, date(2026, 7, 1))
    result = vg.scan_stub_sources(tmp_path, TODAY)
    assert result["total"] == 1
    assert result["reap_candidates"] != []


def test_invalid_frontmatter_date_falls_back_to_mtime():
    assert vg._fm_date("created: 2026-02-30", "created") is None
    assert vg._fm_date("created: not-a-date", "created") is None


def test_template_placeholder_shared_with_calendar_stub_check(tmp_path):
    """The purity check must match what calendar_stub_check actually writes."""
    path = make_stub(tmp_path, "Roundtrip", "2026-07-01")
    assert vg.STUB_NOTES_PLACEHOLDER in path.read_text(encoding="utf-8")


def test_age_days_first_key_wins_and_mtime_fallback(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("x", encoding="utf-8")
    fm = "created: 2026-08-01\nevent_date: 2026-07-01"
    assert vg._age_days(p, TODAY, fm, ("created", "event_date")) == 11
    today = date.today()
    backdate(p, today - timedelta(days=10))
    assert vg._age_days(p, today) == 10


# ── Verified delete ───────────────────────────────────────────────────────────

def test_delete_verification_accepts_valid_candidate(tmp_path):
    path = make_stub(tmp_path, "Old stub", "2026-07-01")
    ok, reason = vg.verify_reap_candidate(path, tmp_path, TODAY)
    assert (ok, reason) == (True, "ok")


def test_delete_verification_refuses_edited_stub(tmp_path):
    path = make_stub(tmp_path, "Edited stub", "2026-07-01", extra_notes="notes")
    ok, reason = vg.verify_reap_candidate(path, tmp_path, TODAY)
    assert (ok, reason) == (False, "not_pure")


def test_delete_verification_refuses_within_grace(tmp_path):
    path = make_stub(tmp_path, "Fresh stub", "2026-08-10")
    ok, reason = vg.verify_reap_candidate(path, tmp_path, TODAY)
    assert (ok, reason) == (False, "within_grace")


def test_delete_verification_refuses_recently_modified(tmp_path):
    path = make_stub(tmp_path, "Touched stub", "2026-07-01", fresh_mtime=True)
    ok, reason = vg.verify_reap_candidate(path, tmp_path, TODAY)
    assert (ok, reason) == (False, "recently_modified")


def test_delete_verification_refuses_outside_sources_dir(tmp_path):
    src = tmp_path / "sources"
    src.mkdir()
    outside = make_stub(tmp_path, "Escapee", "2026-07-01")
    ok, reason = vg.verify_reap_candidate(outside, src, TODAY)
    assert (ok, reason) == (False, "outside_sources_dir")


def test_delete_verification_refuses_non_stub_source(tmp_path):
    real = tmp_path / "2026-07-01 Real.md"
    real.write_text("---\ntype: source\ncreated: 2026-07-01\n---\nnotes",
                    encoding="utf-8")
    backdate(real, date(2026, 7, 1))
    assert vg.verify_reap_candidate(real, tmp_path, TODAY) == (False, "not_a_stub")


def test_delete_verification_refuses_missing_and_non_md(tmp_path):
    assert vg.verify_reap_candidate(
        tmp_path / "gone.md", tmp_path, TODAY)[1] == "not_a_source_page"
    txt = tmp_path / "x.txt"
    txt.write_text("x", encoding="utf-8")
    assert vg.verify_reap_candidate(txt, tmp_path, TODAY) == (False, "not_a_source_page")


def test_delete_verification_refuses_symlink(tmp_path):
    target = make_stub(tmp_path, "Real stub", "2026-07-01")
    link = tmp_path / "link (stub).md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    assert vg.verify_reap_candidate(link, tmp_path, TODAY) == (False, "symlink")


def test_main_delete_moves_valid_stub_to_trash(tmp_path, monkeypatch, capsys):
    # An anciently dated stub stays past grace under the real clock.
    src = tmp_path / "sources"
    src.mkdir()
    path = make_stub(src, "Ancient stub", "2020-01-01")
    monkeypatch.setattr(vg, "sources_dir", lambda: src)
    monkeypatch.setattr(vg, "vault", lambda subpath="": tmp_path / subpath
                        if subpath else tmp_path)
    monkeypatch.setattr("sys.argv", ["vault_gardener.py", "--delete", str(path)])
    with pytest.raises(SystemExit) as e:
        vg.main()
    assert e.value.code == 0
    assert not path.exists()
    out = json.loads(capsys.readouterr().out)
    assert out["deleted"] is True and out["reason"] == "ok"
    trashed = Path(out["trashed_to"])
    assert trashed.parent == tmp_path / ".trash" and trashed.is_file()


def test_main_delete_refuses_edited_stub_and_exits_1(tmp_path, monkeypatch, capsys):
    path = make_stub(tmp_path, "Edited", "2020-01-01", extra_notes="real notes")
    monkeypatch.setattr(vg, "sources_dir", lambda: tmp_path)
    monkeypatch.setattr("sys.argv", ["vault_gardener.py", "--delete", str(path)])
    with pytest.raises(SystemExit) as e:
        vg.main()
    assert e.value.code == 1
    assert path.exists()
    assert json.loads(capsys.readouterr().out)["deleted"] is False


def test_main_rejects_delete_with_today_override(tmp_path, monkeypatch):
    path = make_stub(tmp_path, "Target", "2026-08-11")
    monkeypatch.setattr(vg, "sources_dir", lambda: tmp_path)
    monkeypatch.setattr("sys.argv", ["vault_gardener.py", "--delete", str(path),
                                     "--today", "2099-01-01"])
    with pytest.raises(SystemExit) as e:
        vg.main()
    assert e.value.code == 2  # argparse error
    assert path.exists()


# ── Stub entities ─────────────────────────────────────────────────────────────

def test_stub_entities_by_tag_and_by_empty_body(tmp_path):
    (tmp_path / "Tagged.md").write_text(
        "---\ntitle: Tagged Person\ntags: [person, stub]\n---\n\n"
        "A body long enough that only the tag makes this a stub. " * 3,
        encoding="utf-8")
    (tmp_path / "Empty.md").write_text(
        "---\ntitle: Empty Person\ntags: [person]\n---\n\n# Empty Person\n",
        encoding="utf-8")
    (tmp_path / "Full.md").write_text(
        "---\ntitle: Full Person\ntags: [person]\n---\n\n"
        "A real dossier with plenty of substantive content about this person. " * 3,
        encoding="utf-8")
    names = {e["name"] for e in vg.scan_stub_entities(tmp_path, TODAY)}
    assert names == {"Tagged Person", "Empty Person"}


def test_stub_entities_block_style_tags(tmp_path):
    (tmp_path / "Blocky.md").write_text(
        "---\ntitle: Blocky Person\ntags:\n  - person\n  - stub\n---\n\n"
        "A body long enough that only the block-form tag makes this a stub. " * 3,
        encoding="utf-8")
    assert [e["name"] for e in vg.scan_stub_entities(tmp_path, TODAY)] == ["Blocky Person"]


# ── Stale projects ────────────────────────────────────────────────────────────

def test_stale_projects_frontmatter_date_and_missing(tmp_path):
    fresh = tmp_path / "Fresh.md"
    fresh.write_text("---\nlast_refreshed: 2026-08-10\n---\nbody", encoding="utf-8")
    stale = tmp_path / "Stale.md"
    stale.write_text("---\nlast_refreshed: 2026-06-01\n---\nbody", encoding="utf-8")
    pages = {"Fresh Co": fresh, "Stale Co": stale,
             "Ghost Co": tmp_path / "Ghost.md"}
    out = vg.scan_stale_projects(pages, TODAY)
    by_name = {o["business"]: o for o in out}
    assert set(by_name) == {"Stale Co", "Ghost Co"}
    assert by_name["Ghost Co"]["missing"] is True
    assert by_name["Stale Co"]["age_days"] == 72


def test_stale_projects_survives_undecodable_page(tmp_path):
    bad = tmp_path / "Bad.md"
    bad.write_bytes(b"\xff\xfe garbage")
    out = vg.scan_stale_projects({"Bad Co": bad}, TODAY)
    assert out == [{"business": "Bad Co", "path": str(bad),
                    "age_days": None, "missing": False}]


# ── Orphan entities ───────────────────────────────────────────────────────────

def test_orphan_detection_by_stem_and_title(tmp_path):
    ents = tmp_path / "entities"
    ents.mkdir()
    (ents / "Jane Roe.md").write_text("---\ntitle: Jane Roe\n---\nbody", encoding="utf-8")
    (ents / "John Smith.md").write_text("---\ntitle: Johnny Smith\n---\nbody", encoding="utf-8")
    (ents / "Nobody.md").write_text("---\ntitle: Nobody\n---\nbody", encoding="utf-8")
    hotcache = tmp_path / "hotcache.md"
    hotcache.write_text("Talked to [[Jane Roe]] and [[Johnny Smith|John]].", encoding="utf-8")
    orphans = vg.scan_orphan_entities(ents, [hotcache])
    assert [o["name"] for o in orphans] == ["Nobody"]


def test_orphan_detection_handles_link_variants(tmp_path):
    """Block refs, path-qualified links, quoted titles, and subfolder corpus
    files must all register as inbound links."""
    ents = tmp_path / "entities"
    ents.mkdir()
    (ents / "Jane Roe.md").write_text('---\ntitle: "Jane Roe"\n---\nbody', encoding="utf-8")
    (ents / "Bob King.md").write_text("---\ntitle: Bob King\n---\nbody", encoding="utf-8")
    sources = tmp_path / "sources"
    sub = sources / "2026" / "07"
    sub.mkdir(parents=True)
    (sub / "note.md").write_text(
        "See [[Jane Roe^block12]] and [[people/Bob King]].", encoding="utf-8")
    assert vg.scan_orphan_entities(ents, [sources]) == []


# ── Freshness + logs ──────────────────────────────────────────────────────────

def test_freshness_reports_ages_and_missing_artifacts(tmp_path):
    existing = tmp_path / "week.md"
    existing.write_text("x", encoding="utf-8")
    today = date.today()
    backdate(existing, today - timedelta(days=5))
    out = vg.scan_freshness(
        [("week.md", existing), ("morning-coffee.md", tmp_path / "nope.md")],
        today)
    assert out[0]["exists"] is True and out[0]["age_days"] == 5
    assert out[1]["exists"] is False and out[1]["age_days"] is None


def test_log_failures_picks_fail_lines_only(tmp_path):
    log = tmp_path / "scheduler.log"
    log.write_text("2026-08-11 OK ran fine\n2026-08-12 FAIL: digest send crashed\n",
                   encoding="utf-8")
    out = vg.scan_log_failures(tmp_path, date.today())
    assert len(out) == 1
    assert "FAIL" in out[0]["line"]


def test_log_failures_skips_old_logs(tmp_path):
    log = tmp_path / "old.log"
    log.write_text("FAIL: ancient crash\n", encoding="utf-8")
    today = date.today()
    backdate(log, today - timedelta(days=vg.LOG_RECENT_DAYS + 3))
    assert vg.scan_log_failures(tmp_path, today) == []


def test_log_failures_only_scans_tail(tmp_path):
    log = tmp_path / "big.log"
    log.write_text("FAIL early\n" + ("all quiet on this line\n" * 400),
                   encoding="utf-8")
    assert vg.scan_log_failures(tmp_path, date.today()) == []


def test_log_failures_caps_at_max_lines(tmp_path):
    log = tmp_path / "busy.log"
    log.write_text("\n".join(f"{i} FAIL boom" for i in range(30)), encoding="utf-8")
    out = vg.scan_log_failures(tmp_path, date.today())
    assert len(out) == vg.MAX_LOG_LINES


def test_log_failures_sanitizes_and_truncates(tmp_path):
    log = tmp_path / "noisy.log"
    log.write_text("FAIL \x1b[31mred\x1b[0m " + "x" * 400 + "\n", encoding="utf-8")
    out = vg.scan_log_failures(tmp_path, date.today())
    assert len(out) == 1
    assert "\x1b" not in out[0]["line"]
    assert len(out[0]["line"]) <= vg.LOG_LINE_MAX


# ── Sweep wiring (primed config fixture) ──────────────────────────────────────

def test_run_sweep_shape_under_fixture():
    out = vg.run_sweep(TODAY)
    assert set(out) >= {"meta", "today", "report_path", "grace_days",
                        "stub_sources", "stub_entities", "stale_projects",
                        "orphan_entities", "freshness", "log_failures"}
    assert out["report_path"].endswith("vault-audit.md")
    assert {a["name"] for a in out["freshness"]} == {
        "week.md", "morning-coffee.md", "afternoon-tea.md",
        "hotcache", "relationship-radar"}
    # The fixture's two businesses have no project pages on disk.
    assert all(p["missing"] for p in out["stale_projects"])


# ── The evidence sweep: shape, writes, and leaks ──────────────────────────────

@pytest.fixture
def swept(tmp_path, monkeypatch):
    """A real vault tree the sweep can run against, with config pointed at it."""
    import config_loader as cl
    vault = tmp_path / "vault"
    for sub in ("wiki/sources", "wiki/entities", "wiki/weekly", "van-gogh/logs"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "hotcache.md").write_text(
        "---\ntitle: hot\n---\n\n## Test's Action Items\n\n- do the thing\n",
        encoding="utf-8")
    (vault / "wiki" / "acme.md").write_text("# Acme\n" + "content " * 40,
                                            encoding="utf-8")
    monkeypatch.setattr(cl, "vault", lambda sub="": vault / sub if sub else vault)
    monkeypatch.setattr(cl, "van_gogh_root", lambda: vault / "van-gogh")
    monkeypatch.setattr(cl, "logs_dir", lambda: vault / "van-gogh" / "logs")
    monkeypatch.setattr(cl, "hotcache_path", lambda: vault / "wiki" / "hotcache.md")
    monkeypatch.setattr(cl, "sources_dir", lambda: vault / "wiki" / "sources")
    monkeypatch.setattr(cl, "entities_dir", lambda: vault / "wiki" / "entities")
    monkeypatch.setattr(cl, "weekly_dir", lambda: vault / "wiki" / "weekly")
    monkeypatch.setattr(cl, "project_pages", lambda: {"Acme": vault / "wiki" / "acme.md"})
    monkeypatch.setattr(vg, "logs_dir", lambda: vault / "van-gogh" / "logs")
    monkeypatch.setattr(vg, "van_gogh_root", lambda: vault / "van-gogh")
    monkeypatch.setattr(vg, "vault", lambda sub="": vault / sub if sub else vault)
    monkeypatch.setattr(vg, "sources_dir", lambda: vault / "wiki" / "sources")
    monkeypatch.setattr(vg, "entities_dir", lambda: vault / "wiki" / "entities")
    monkeypatch.setattr(vg, "weekly_dir", lambda: vault / "wiki" / "weekly")
    monkeypatch.setattr(vg, "hotcache_path", lambda: vault / "wiki" / "hotcache.md")
    monkeypatch.setattr(vg, "project_pages", lambda: {"Acme": vault / "wiki" / "acme.md"})
    return vault


JOBS = [{"label": "com.monet.meeting-ingest", "kind": "skill",
         "name": "meeting-ingest", "state": "not installed", "path": "/x.plist"}]


def tree(root):
    """Relative paths of every file under `root`, always slash-separated.

    `str(Path)` renders the OS separator, so on Windows this returned
    "van-gogh\\logs\\x.json" while every assertion below is written with
    forward slashes. That is a bug in the helper, not in the product: the
    comparison is about WHICH files were written, not about how this platform
    spells a path. `as_posix()` makes the set portable.
    """
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ── P1: the output shape ─────────────────────────────────────────────────────

def test_sweep_carries_the_evidence_keys(swept):
    out = vg.run_sweep(TODAY, job_states=JOBS, write=False)
    for key in ("evidence", "probes", "score", "ledger", "score_md",
                "ledger_md", "automation_md", "sidecar_path", "ledger_path"):
        assert key in out, key
    assert out["evidence"] and out["probes"]
    assert len(out["score"]["rows"]) == 20
    assert set(out["score"]["subtotals"]) == set(ae.GROUPS)
    for cid, row in out["score"]["rows"].items():
        assert row["points"] in (0, 1, 3, 5, None), cid
        assert row["judged_by"] in ("code", "model"), cid
        assert isinstance(row["evidence"], list), cid
    assert out["score"]["stage_min"] in [name for _, name in ae.STAGES]


# ── P21: exactly two files, and nothing else ─────────────────────────────────

def test_writes_exactly_the_ledger_and_the_sidecar(swept):
    before = tree(swept)
    out = vg.run_sweep(TODAY, job_states=JOBS, write=True)
    after = tree(swept)
    added = after - before
    assert added == {"van-gogh/logs/vault_audit_ledger.json",
                     "van-gogh/logs/vault_audit_latest.json"}
    assert added, "the normal run must actually write something"
    assert Path(out["ledger_path"]).is_file()
    assert Path(out["sidecar_path"]).is_file()


def test_writes_nothing_with_no_write(swept):
    before = tree(swept)
    vg.run_sweep(TODAY, job_states=JOBS, write=False)
    assert tree(swept) == before


def test_writes_leave_no_temporary_file_behind(swept):
    vg.run_sweep(TODAY, job_states=JOBS, write=True)
    assert not [p for p in swept.rglob("*van-gogh-tmp*")]


def test_captures_are_never_reported_as_findings(swept):
    """A capture is Van Gogh's own bookkeeping, not a vault page the user
    forgot to write up. Reporting it would train the reader to ignore the
    findings list."""
    captures = swept / "van-gogh" / "logs" / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    (captures / "voice-bootstrap-2026-09-09.md").write_text(
        "---\nskill: voice-bootstrap\nstatus: in_progress\n---\n", encoding="utf-8")
    out = vg.run_sweep(TODAY, job_states=JOBS, write=False)
    blob = json.dumps(out)
    assert "voice-bootstrap-2026-09-09" not in blob
    for entry in out["stub_entities"] + out["orphan_entities"]:
        assert "captures" not in entry["path"]


# ── P22: no secrets reach any surface a person or a log can see ──────────────

def test_no_secret_reaches_the_report_surfaces(swept):
    token = "1//0eXpLANTEDrefreshTOKENvalue9876543210abcdefGHIJK"
    address = "someone@example.com"
    (swept / "van-gogh" / "logs" / "afternoon_tea_latest.json").write_text(
        json.dumps({"today": TODAY.isoformat(),
                    "errors": [f"Sent mail (Gmail): invalid_grant for {token}"],
                    "sent": [{"account": "Gmail"}]}),
        encoding="utf-8")
    (swept / "van-gogh" / "logs" / "digest-week.log").write_text(
        f"ERROR: refresh failed for {address} using {token}\n", encoding="utf-8")
    out = vg.run_sweep(TODAY, job_states=JOBS, write=True)

    surfaces = [out["score_md"], out["ledger_md"], out["automation_md"],
                json.dumps(out["evidence"]), json.dumps(out["score"]),
                json.dumps(out["ledger"]),
                Path(out["sidecar_path"]).read_text(encoding="utf-8"),
                Path(out["ledger_path"]).read_text(encoding="utf-8")]
    for surface in surfaces:
        assert token not in surface
        assert address not in surface


def test_scrub_removes_a_planted_token_and_address():
    token = "1//0eXpLANTEDrefreshTOKENvalue9876543210abcdefGHIJK"
    planted = f"invalid_grant for {token} on someone@example.com"
    assert token in planted and "someone@example.com" in planted
    cleaned = ae.scrub(planted)
    assert token not in cleaned and "someone@example.com" not in cleaned
    assert "[redacted]" in cleaned and "[address]" in cleaned


# ── P10 at the sweep level: a second run adds nothing new ────────────────────

def test_two_sweeps_on_an_unchanged_vault_report_nothing_new(swept):
    stub = swept / "wiki" / "sources" / "2026-07-01 Acme sync (stub).md"
    make_stub(swept / "wiki" / "sources", "Acme sync", "2026-07-01")
    first = vg.run_sweep(TODAY, job_states=JOBS, write=True)
    second = vg.run_sweep(TODAY, job_states=JOBS, write=True)
    assert set(first["ledger"]["findings"]) == set(second["ledger"]["findings"])
    assert sum(1 for f in second["ledger"]["findings"].values()
               if f["status"] == "new") == 0
    assert len(second["ledger"]["runs"]) == 1


# ── Cold read round 3: one pick is one finding, however it is worded ─────────

def test_a_reworded_pick_is_the_same_finding(swept):
    """The ledger promises "ids stay the same from week to week". Keying the
    pick on its sentence broke that on the page that states it: one recurring
    item arrived as three ids with three phrasings."""
    a = "Filling in your ventures and their priorities in the settings, so " \
        "the briefing describes your actual week."
    b = "Filling in the ventures and priorities that Van Gogh works from, " \
        "so the morning briefing describes your actual week."
    assert vg._same_pick(vg._pick_key(a), vg._pick_key(b))
    assert vg._pick_key(b, vg._pick_key(a)) == vg._pick_key(a)

    different = "Keeping 23 thin people pages tidy by hand."
    assert not vg._same_pick(vg._pick_key(a), vg._pick_key(different))
    assert vg._pick_key(different, vg._pick_key(a)) != vg._pick_key(a)


def test_a_pick_repeated_unchanged_is_not_counted_as_followed_up(swept):
    """Picking the same thing again is the thing that did not get done."""
    report = swept / "van-gogh" / "vault-audit.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    block = "\n".join([
        "# Audit", ae.AUTOMATION_BEGIN, "",
        "Candidate: filling in the ventures and their priorities",
        "Eliminate: keep, because the vault underneath it is already real",
        "Autonomy: L1 because a draft is cheaper to correct than a send",
        "KPI: less cost, minutes per week spent re-checking the briefing",
        "Size: S", f"Picked: {(TODAY - timedelta(days=7)).isoformat()}",
        "Refs: none", "", ae.AUTOMATION_END])
    report.write_text(block, encoding="utf-8")

    first = vg.run_sweep(TODAY, job_states=JOBS, write=True)
    picks = [f for f in first["ledger"]["findings"].values() if f["kind"] == "pick"]
    assert len(picks) == 1

    # Same pick, reworded. Still one finding, still not acted on.
    report.write_text(block.replace(
        "Candidate: filling in the ventures and their priorities",
        "Candidate: filling in your ventures and their current priorities"),
        encoding="utf-8")
    second = vg.run_sweep(TODAY, job_states=JOBS, write=True)
    picks2 = [f for f in second["ledger"]["findings"].values() if f["kind"] == "pick"]
    assert len(picks2) == 1, "a rewording created a second finding"
    assert second["score"]["rows"]["D5"]["points"] <= 3
    assert "has not been acted on yet" in second["score"]["rows"]["D5"]["basis"]


def test_todays_own_pick_is_not_reported_back_as_history(swept):
    """Cold read round 6: re-running on one day produced "picked 2026-09-09.
    Not built." inside a report dated 2026-09-09. A decision taken minutes ago
    has no follow-up to judge."""
    report = swept / "van-gogh" / "vault-audit.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    block = "\n".join([
        "# Audit", ae.AUTOMATION_BEGIN, "",
        "Candidate: filling in the ventures and their priorities",
        "Eliminate: keep, because the vault underneath it is already real",
        "Autonomy: L1 because a draft is cheaper to correct than a send",
        "KPI: less cost, minutes per week spent re-checking the briefing",
        "Size: S", "Picked: {picked}", "Refs: none", "", ae.AUTOMATION_END])

    report.write_text(block.format(picked=TODAY.isoformat()), encoding="utf-8")
    same_day = vg.run_sweep(TODAY, job_states=JOBS, write=False)
    assert same_day["previous_pick"] == {}

    earlier = (TODAY - timedelta(days=7)).isoformat()
    report.write_text(block.format(picked=earlier), encoding="utf-8")
    last_week = vg.run_sweep(TODAY, job_states=JOBS, write=False)
    assert last_week["previous_pick"]["picked"] == earlier
