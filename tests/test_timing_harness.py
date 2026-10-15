"""Hermetic tests for the fifteen-minute-install timing harness.

No network, no tokens, no model calls. The harness module is loaded from
objectives/fifteen-minute-install/ by path; the live pipeline is never run.
Covered: the ledger shape the live run writes, the floors failing on a
fabricated under-floor ledger, --compare failing on a slow fast arm and on
differing page hashes, and the isolation assertion raising on a config that
points at a vault outside the temp one (the lab-notes trap: a copied config
whose path field still points home).
"""

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HARNESS = _REPO / "objectives" / "fifteen-minute-install" / "timing_harness.py"


def _load():
    spec = importlib.util.spec_from_file_location("timing_harness", _HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


th = _load()

NO_DASH = (chr(0x2014), chr(0x2013))


def _good_ledger(arm, total, pages=None):
    """A ledger shaped like the one run_pipeline writes, floors passing."""
    stages = {}
    share = {"scan": 0.4, "propose": 0.05, "apply": 0.05, "ingest": 0.4,
             "first_briefing_data": 0.1}
    for name in th.STAGES:
        stages[name] = {"seconds": round(total * share[name], 1), "rc": 0,
                        "counts": {}, "error": None}
    stages["scan"]["counts"] = {"threads_scanned": 120, "sections": {}, "since_days": 60}
    stages["propose"]["counts"] = {"deals": 4, "entities": 6}
    stages["apply"]["counts"] = {"deals_written": 4, "entities_written": 6}
    stages["ingest"]["counts"] = {"available": 10, "filed": 10}
    stages["first_briefing_data"]["counts"] = {"threads": 80}
    led = {
        "version": th.LEDGER_VERSION, "arm": arm, "code_sha": "abc123",
        "started": "2026-10-01T18:00:00", "python": sys.executable,
        "temp": {"state_dir": "/tmp/s", "vault": "/tmp/v", "kept": False},
        "isolation": {}, "real_hashes": {"before": {}, "after": {}, "unchanged": True},
        "stages": stages, "total_seconds": round(total, 1), "wall_seconds": total + 20,
        "floors": {}, "pages": pages if pages is not None else {"a.md": "h1", "b.md": "h2"},
        "errors": [],
    }
    checked, failures = th.check_floors(led)
    led["floors"] = {"checked": checked, "failures": failures, "ok": not failures}
    return led


# ── ledger shape ─────────────────────────────────────────────────────────────

def test_ledger_shape_and_floors_pass_on_a_good_run():
    led = _good_ledger("serial", 1200)
    for key in ("version", "arm", "code_sha", "started", "stages", "total_seconds",
                "floors", "pages", "real_hashes", "isolation", "errors"):
        assert key in led
    assert list(led["stages"]) == list(th.STAGES)
    for name in th.STAGES:
        row = led["stages"][name]
        assert set(row) == {"seconds", "rc", "counts", "error"}
        assert isinstance(row["seconds"], float)
        assert row["rc"] == 0
    assert led["floors"]["ok"] is True
    assert led["floors"]["failures"] == []
    assert len(led["floors"]["checked"]) == 3
    assert abs(led["total_seconds"] - sum(s["seconds"] for s in led["stages"].values())) < 0.6


def test_harness_source_has_no_dashes():
    text = _HARNESS.read_text(encoding="utf-8")
    for ch in NO_DASH:
        assert ch not in text


# ── floors ───────────────────────────────────────────────────────────────────

def test_floors_fail_on_an_under_floor_ledger():
    led = _good_ledger("serial", 1200)
    led["stages"]["scan"]["counts"]["threads_scanned"] = th.FLOOR_THREADS - 1
    led["stages"]["propose"]["counts"]["deals"] = 0
    led["stages"]["ingest"]["counts"]["filed"] = 7
    checked, failures = th.check_floors(led)
    assert len(checked) == 3
    joined = "\n".join(failures)
    assert f"threads scanned {th.FLOOR_THREADS - 1} < {th.FLOOR_THREADS}" in joined
    assert "deals proposed 0 < 1" in joined
    assert "meetings filed 7 != 10" in joined


def test_floor_meetings_is_min_of_limit_and_available():
    led = _good_ledger("serial", 1200)
    led["stages"]["ingest"]["counts"] = {"available": 3, "filed": 3}
    _, failures = th.check_floors(led)
    assert failures == []
    led["stages"]["ingest"]["counts"] = {"available": 3, "filed": 2}
    _, failures = th.check_floors(led)
    assert failures == ["meetings filed 2 != 3"]


def test_floor_unknown_available_is_a_failure_not_a_pass():
    led = _good_ledger("serial", 1200)
    led["stages"]["ingest"]["counts"] = {"available": None, "filed": 10}
    _, failures = th.check_floors(led)
    assert any("unknown" in f for f in failures)


def test_floor_nonzero_stage_rc_fails():
    led = _good_ledger("serial", 1200)
    led["stages"]["apply"]["rc"] = 2
    _, failures = th.check_floors(led)
    assert "stage apply rc=2" in failures


# ── compare ──────────────────────────────────────────────────────────────────

def test_compare_passes_on_a_fast_arm_within_targets():
    serial = _good_ledger("serial", 1200)
    fast = _good_ledger("fast", 300)
    lines, failures = th.compare(serial, fast)
    assert failures == []
    assert any(line.startswith("total") for line in lines)
    assert any(line.startswith("scan") for line in lines)


def test_compare_fails_on_a_slow_fast_arm_over_six_minutes():
    serial = _good_ledger("serial", 1200)
    fast = _good_ledger("fast", th.FAST_MAX_SECONDS + 1)
    _, failures = th.compare(serial, fast)
    assert any(f"> {th.FAST_MAX_SECONDS}s" in f for f in failures)


def test_compare_fails_when_fast_is_more_than_half_of_serial():
    serial = _good_ledger("serial", 500)
    fast = _good_ledger("fast", 300)   # under 6:00 but over half of 500
    _, failures = th.compare(serial, fast)
    assert any("half of serial" in f for f in failures)
    assert not any(f"> {th.FAST_MAX_SECONDS}s" in f for f in failures)


def test_compare_fails_on_differing_page_hashes_and_on_missing_pages():
    serial = _good_ledger("serial", 1200, pages={"a.md": "h1", "b.md": "h2"})
    fast = _good_ledger("fast", 300, pages={"a.md": "h1", "b.md": "DIFFERENT"})
    _, failures = th.compare(serial, fast)
    assert any("differs: ['b.md']" in f for f in failures)

    fast2 = _good_ledger("fast", 300, pages={"a.md": "h1", "c.md": "h3"})
    _, failures = th.compare(serial, fast2)
    assert any("only in serial: ['b.md']" in f for f in failures)
    assert any("only in fast: ['c.md']" in f for f in failures)


def test_compare_fails_when_either_ledger_failed_its_floors():
    serial = _good_ledger("serial", 1200)
    fast = _good_ledger("fast", 300)
    fast["floors"] = {"checked": [], "failures": ["deals proposed 0 < 1"], "ok": False}
    _, failures = th.compare(serial, fast)
    assert any("fast ledger failed its floors" in f for f in failures)


def test_compare_refuses_two_ledgers_of_the_same_arm():
    a = _good_ledger("serial", 1200)
    b = _good_ledger("serial", 300)
    _, failures = th.compare(a, b)
    assert any("expected arms serial then fast" in f for f in failures)


def test_compare_cli_exits_nonzero_on_failure(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_good_ledger("serial", 1200)), encoding="utf-8")
    b.write_text(json.dumps(_good_ledger("fast", 900)), encoding="utf-8")
    assert th.main(["--compare", str(a), str(b)]) == 1
    assert "COMPARE FAIL" in capsys.readouterr().out
    b.write_text(json.dumps(_good_ledger("fast", 200)), encoding="utf-8")
    assert th.main(["--compare", str(a), str(b)]) == 0


# ── page normalization ───────────────────────────────────────────────────────

def test_normalize_page_strips_dates_times_and_run_ids():
    a = ("---\ncreated: 2026-10-01\nupdated: 2026-10-01T18:02:11-07:00\n---\n"
         "Run 20261001-180000-a1b2 at 6:02 PM filed this.  \n")
    b = ("---\ncreated: 2026-10-02\nupdated: 2026-10-02T09:15:00-07:00\n---\n"
         "Run 20261002-091500-ffff at 9:15 AM filed this.\n")
    assert th.normalize_page(a) == th.normalize_page(b)
    assert "2026" not in th.normalize_page(a)
    assert th.normalize_page("x: 2026-10-01\n") != th.normalize_page("y: 2026-10-01\n")


def test_page_hashes_and_meeting_count_read_wiki_sources(tmp_path):
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "Call - 2026-10-01.md").write_text(
        "---\nmeeting_id: not_abc\ncreated: 2026-10-01\n---\n# Call\n", encoding="utf-8")
    (sources / "catalog-2026-10.md").write_text("## Sources\n- Call\n", encoding="utf-8")
    hashes = th.page_hashes(tmp_path)
    assert set(hashes) == {th.page_key("Call - 2026-10-01.md"), th.page_key("catalog-2026-10.md")}
    # Keys are hashes, never titles: this ledger ships in the public mirror.
    assert not any(k.endswith(".md") for k in hashes)
    assert th.count_meeting_pages(tmp_path) == 1
    assert th.count_meeting_pages(tmp_path / "nowhere") == 0


# ── config rewrite ───────────────────────────────────────────────────────────

def test_build_config_rewrites_every_path_and_disables_every_feature(tmp_path):
    real_vault = tmp_path / "real-vault"
    temp_vault = tmp_path / "temp-vault"
    real_vault.mkdir()
    temp_vault.mkdir()
    real_cfg = {
        "user": {"full_name": "Jane Doe"},
        "obsidian": {"vault_path": str(real_vault), "sources_relpath": "wiki/sources"},
        "memory_sync": {"enabled": True, "source_dirs": [str(real_vault / "wiki" / "x"),
                                                         "/elsewhere/notes"]},
        "digest": {"enabled": True, "channels": {"email": True}},
        "cadence": {"relationship-radar": {"enabled": True, "days": ["monday"]}},
        "workbench": {"autostart": True},
        "nested": {"deep": {"path": "~/Somewhere/file.md"}},
    }
    cfg = th.build_config(real_cfg, real_vault, temp_vault)
    assert cfg["obsidian"]["vault_path"] == str(temp_vault)
    assert cfg["obsidian"]["sources_relpath"] == "wiki/sources"
    assert cfg["memory_sync"]["source_dirs"][0] == str(temp_vault / "wiki" / "x")
    assert cfg["memory_sync"]["source_dirs"][1].startswith(str(temp_vault / "redirected"))
    assert cfg["nested"]["deep"]["path"].startswith(str(temp_vault / "redirected"))
    for section, key in th.FEATURE_FLAGS_OFF:
        assert cfg[section][key] is False, (section, key)
    for skill in th.CADENCE_SKILLS:
        assert cfg["cadence"][skill]["enabled"] is False
    assert cfg["cadence"]["relationship-radar"]["days"] == ["monday"]
    assert cfg["digest"]["channels"] == {"email": True}
    assert real_cfg["obsidian"]["vault_path"] == str(real_vault)  # input untouched
    text = json.dumps(cfg)
    assert str(real_vault) not in text.replace(str(temp_vault), "")


# ── isolation ────────────────────────────────────────────────────────────────

def test_check_isolation_raises_when_any_path_points_home(tmp_path):
    temp_vault = tmp_path / "v"
    temp_state = tmp_path / "s"
    good = {"vault": str(temp_vault), "van_gogh_root": str(temp_vault / "van-gogh"),
            "logs_dir": str(temp_vault / "van-gogh" / "logs"),
            "state_dir": str(temp_state), "env_file": str(temp_state / ".env")}
    assert th.check_isolation(good, temp_vault, temp_state) == good
    for key in good:
        bad = dict(good)
        bad[key] = str(tmp_path / "home" / "Second Brain")
        with pytest.raises(th.IsolationError) as exc:
            th.check_isolation(bad, temp_vault, temp_state)
        assert key in str(exc.value)


def test_probe_child_resolves_the_vault_from_the_config_not_the_pointer(tmp_path):
    """A real child python, config pointing home: the assertion must raise."""
    home = tmp_path / "home-vault"
    home.mkdir()
    temp_vault = tmp_path / "temp-vault"
    temp_state = tmp_path / "temp-state"
    (temp_vault / "van-gogh").mkdir(parents=True)
    temp_state.mkdir()
    (temp_state / "vault-pointer").write_text(str(temp_vault) + "\n", encoding="utf-8")
    (temp_vault / "van-gogh" / "config.json").write_text(
        json.dumps({"user": {"full_name": "T"}, "obsidian": {"vault_path": str(home)}}),
        encoding="utf-8")
    env = th.child_env(temp_state, "serial")
    assert env["VAN_GOGH_SERIAL"] == "1"
    assert env["VAN_GOGH_UNATTENDED"] == "1"
    assert env["VAN_GOGH_SCHEDULED"] == ""
    paths = th.probe_paths(env)
    assert paths["state_dir"] == str(temp_state)
    with pytest.raises(th.IsolationError):
        th.check_isolation(paths, temp_vault, temp_state)


def test_selftest_passes_with_a_fabricated_home(tmp_path):
    home = tmp_path / "home-vault"
    home.mkdir()
    lines = []
    assert th.selftest(home_vault=home, log=lines.append) is True
    assert any("raised as expected" in ln for ln in lines)
    assert any("control passed" in ln for ln in lines)


def test_fast_arm_env_has_no_serial_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    env = th.child_env(tmp_path, "fast")
    assert "VAN_GOGH_SERIAL" not in env


def test_tree_state_reports_dirty_and_count_from_git(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(th, "REPO", tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    state = th.tree_state()
    assert state == {"dirty": True, "modified_files": 1}
    monkeypatch.setattr(th, "REPO", tmp_path / "not-a-repo-anywhere")
    assert th.tree_state()["dirty"] is None


def test_repo_flag_refuses_a_checkout_without_the_pipeline(tmp_path, capsys):
    assert th.main(["--repo", str(tmp_path), "--arm", "serial"]) == 2
    assert "no app/warm_start.py" in capsys.readouterr().out


def test_json_from_skips_prose_and_finds_the_first_object():
    out = "Warning: no stdin\n{\"started\": true, \"pid\": 42, \"s\": \"}\"}\ntrailing"
    assert th._json_from(out) == {"started": True, "pid": 42, "s": "}"}
    assert th._json_from("nothing here") is None


def test_page_structure_ignores_the_model_prose_and_keeps_what_code_decides():
    """Two arms synthesize different words, headings may even differ when one
    fell back to the vendor summary; the identity (type, title, meeting,
    attendees) must match and no title or name may appear in the result."""
    a = th.page_structure(
        "---\ntype: source\ntitle: Call with Alice\nmeeting_id: m1\nmeeting_source: granola\n"
        "attendees: [Alice Ng]\nbusiness: acme\nsynthesis: transcript\n---\n# Call with Alice\n"
        "## Summary\nAlice said [[Alice Ng]] will send it.\n## Action Items\n- x\n")
    b = th.page_structure(
        "---\ntype: source\ntitle: Call with Alice\nmeeting_id: m1\nmeeting_source: granola\n"
        "attendees: [Alice Ng]\nbusiness: other\nsynthesis: summary\n---\n# Call with Alice\n"
        "## Notes\nQuite different wording, no links.\n")
    assert a["identity"] == b["identity"]
    assert a["synthesis"] == "transcript" and b["synthesis"] == "summary"
    assert a["sections"] != b["sections"]
    assert "Alice" not in json.dumps(a) and "m1" not in json.dumps(a)
    c = th.page_structure(
        "---\ntype: source\ntitle: Call with Alice\nmeeting_id: m2\nmeeting_source: granola\n"
        "attendees: [Alice Ng]\n---\n# Call\n")
    assert c["identity"] != a["identity"]


def test_compare_uses_structure_when_both_ledgers_carry_it(tmp_path):
    base = {"arm": "serial", "total_seconds": 500.0, "stages": {}, "floors": {"ok": True},
            "pages": {"k1": "contentA"},
            "pages_structure": {"k1": {"identity": "i", "synthesis": "transcript", "sections": "s1"}}}
    fast = {"arm": "fast", "total_seconds": 200.0, "stages": {}, "floors": {"ok": True},
            "pages": {"k1": "contentB"},
            "pages_structure": {"k1": {"identity": "i", "synthesis": "summary", "sections": "s2"}}}
    lines, failures = th.compare(base, fast)
    assert "page compare mode: structure" in "\n".join(lines)
    assert not any("differs" in f for f in failures)
    del base["pages_structure"]
    lines2, failures2 = th.compare(base, fast)
    assert "page compare mode: content" in "\n".join(lines2)
    assert any("differs" in f for f in failures2)


def test_ledgers_never_carry_the_home_folder():
    """The evidence folder ships in the public mirror; a username in a path
    is identity leakage. The writer folds the home prefix to "~"."""
    from pathlib import Path
    home = str(Path.home())
    sample = {"repo": home + "/x", "stages": [{"path": home + "/y"}], "n": 3, "pages": {"k": "v"}}
    out = th.tilde_paths(sample)
    assert out == {"repo": "~/x", "stages": [{"path": "~/y"}], "n": 3, "pages": {"k": "v"}}
    evidence = Path(th.__file__).resolve().parent / "evidence"
    found = [f.name for f in evidence.glob("*.json") if home in f.read_text(encoding="utf-8")]
    assert found == [], found
