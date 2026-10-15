"""Adoption of installs that predate the checklist (P28), the machine-only
finisher the nudge job calls (P27's orchestrator half), the stamp wiring into
the update path, the import-time gate, and the small subcommands (`skip`,
`_job`, the nudge wrappers).

The two listings under tests/fixtures/adopt/ are a sanitised inventory of a
real install (file and key names kept, every value a placeholder); a temp
install is BUILT from a listing, so the counts adopt reports are re-counted
from a tree that really exists rather than copied from the fixture.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import setup_flow as sf
import setup_state as ss

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "adopt"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def listing(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_fixtures_are_sanitised_and_keep_the_real_shape():
    complete = listing("complete-listing.json")
    stuck = listing("stuck-listing.json")
    text = json.dumps(complete) + json.dumps(stuck)
    for word in ("PLACEHOLDER", "placeholder@example"):
        assert word in text
    assert "@gmail" not in text and "@pd46" not in text
    assert complete["vault"]["wiki_sources_md_count"] > 0
    assert complete["vault"]["hotcache_active_deal_headings"] > 0
    assert complete["scheduler"]["loaded_labels"] and stuck["scheduler"]["loaded_labels"] == []
    assert {k for k in complete if k != "_about"} == {k for k in stuck if k != "_about"}
    # Every value in the state env is a placeholder, so the fixture carries no secret.
    for key, value in complete["state_dir"]["env"].items():
        assert value.startswith("PLACEHOLDER"), key


class OldInstall:
    """A temp install built from a listing: state dir, vault, loaded jobs."""

    def __init__(self, tmp_path, monkeypatch, name):
        self.listing = listing(name)
        self.state = tmp_path / "state"
        self.home = tmp_path / "home"
        self.vault = self.home / "Second Brain"
        for d in (self.state, self.home / "Downloads", self.vault / "van-gogh"):
            d.mkdir(parents=True)
        monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(self.state))
        # The scheduler is a stub in these tests, so they may read it.
        monkeypatch.setenv("VAN_GOGH_SCHEDULER_SYNC", "1")
        monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(tmp_path / "logs"))
        # The nudge job is a stub here, so the wrapper may install and remove
        # it even though the state dir is overridden (see _nudge_job).
        monkeypatch.setenv("VAN_GOGH_NUDGE_JOB", "1")
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("USERPROFILE", str(self.home))
        monkeypatch.chdir(tmp_path)
        sd = self.listing["state_dir"]
        (self.state / ".env").write_text(
            "".join(f"{k}={v}\n" for k, v in sd["env"].items()), encoding="utf-8")
        (self.state / "google_client_secret.json").write_text(
            json.dumps(sd["google_client_secret"]), encoding="utf-8")
        (self.state / "ms_client_secret.json").write_text(
            json.dumps(sd["ms_client_secret"]), encoding="utf-8")
        (self.state / "vault-pointer").write_text(str(self.vault), encoding="utf-8")
        (self.state / "plugin-root").write_text(str(PLUGIN_ROOT), encoding="utf-8")
        v = self.listing["vault"]
        cfg = json.loads((PLUGIN_ROOT / "config.template.json").read_text(encoding="utf-8"))
        cfg["user"].update(v["config"]["user"])
        cfg["accounts"] = [{**a, "sent_folder_id": ""} for a in v["config"]["accounts"]]
        cfg["obsidian"].update(v["config"]["obsidian"])
        cfg["obsidian"]["vault_path"] = str(self.vault)
        cfg["notetaker"] = v["config"]["notetaker"]
        cfg["businesses"] = [{"tag": t, "display_name": t.title(), "project_page": f"wiki/projects/{t}.md",
                              "meeting_route": f"20-{t}/Meetings", "keywords": [], "priorities": []}
                             for t in v["config"]["businesses"]]
        cfg["digest"]["enabled"] = v["config"]["digest_enabled"]
        (self.vault / "van-gogh" / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        for entry in v["van_gogh_tree"]:
            p = self.vault / "van-gogh" / entry
            if entry.endswith("/"):
                p.mkdir(parents=True, exist_ok=True)
            elif not p.exists():
                p.write_text(f"# {entry}\n", encoding="utf-8")
        for entry in v["logs_tree"]:
            (self.vault / "van-gogh" / "logs" / entry).write_text("", encoding="utf-8")
        (self.vault / "CLAUDE.md").write_text("# vault\n", encoding="utf-8")
        src = self.vault / "wiki" / "sources"
        src.mkdir(parents=True)
        for i in range(v["wiki_sources_md_count"]):
            (src / f"2026-01-01 Meeting {i}.md").write_text("---\nmeeting_id: x\n---\n", encoding="utf-8")
        ent = self.vault / "wiki" / "entities"
        ent.mkdir(parents=True)
        for i in range(v["wiki_entities_md_count"]):
            (ent / f"Person {i}.md").write_text("# Person\n", encoding="utf-8")
        hot = ["# Hotcache", "", "## Active Threads", ""]
        for i in range(v["hotcache_active_deal_headings"]):
            hot += [f"### Deal {i}", "- stage: live", ""]
        hot += ["## Key Numbers", "", "### not a deal", "", "## Placeholder's Action Items", ""]
        (self.vault / "wiki" / "hotcache.md").write_text("\n".join(hot), encoding="utf-8")
        self.loaded = set(self.listing["scheduler"]["loaded_labels"])
        expected = {"com.monet.job-watch", "com.monet.ingest-workspace", "com.monet.meeting-ingest"}

        def jobs_check():
            missing = expected - self.loaded
            if missing:
                return ss.OPEN, f"{len(missing)} of {len(expected)} jobs not loaded"
            return ss.DONE, f"{len(expected)} jobs loaded"

        monkeypatch.setitem(ss.CHECKS, "jobs", jobs_check)
        monkeypatch.setitem(ss.CHECKS, "python", lambda: (ss.DONE, "ready"))
        # The plugin check asks the real CLI when it is on PATH; this temp
        # home has no plugins, so the pointer (written above) is the witness.
        monkeypatch.setattr(ss, "_claude_on_path", lambda: None)
        self.nudge_calls = []
        world = self

        def nudge_main(argv):
            world.nudge_calls.append(list(argv))
            if argv[:1] == ["run"] and ss.load() is None:
                ss.log_event("nudge", sent=0, reason="ran, 0 to send")
                print(json.dumps({"sent": 0, "reason": "ran, 0 to send"}))
                return 0
            print(json.dumps({"ok": True, "state": "loaded"}))
            return 0

        monkeypatch.setitem(sys.modules, "setup_nudge", types.SimpleNamespace(main=nudge_main))
        self.children = []
        monkeypatch.setattr(sf, "_run_script_raw", self.child)
        self.ingest_reply = {"filed": [], "failed": [], "deferred": [], "ingested": True,
                             "errors": [], "counts": {"filed": 0, "failed": 0, "deferred": 0}}

    def child(self, name, args, timeout=600, input_text=None, cwd=None):
        self.children.append((name, list(args)))
        if name == "warm_start.py" and args[0] == "ingest-finish":
            return {"rc": 0, "json": self.ingest_reply, "text": json.dumps(self.ingest_reply)}
        if name == "warm_start.py" and args[0] == "ingest-start":
            return {"rc": 0, "json": {"started": True, "pid": 5}, "text": ""}
        return {"rc": 0, "json": {}, "text": "OK"}


@pytest.fixture
def complete(tmp_path, monkeypatch):
    return OldInstall(tmp_path, monkeypatch, "complete-listing.json")


@pytest.fixture
def stuck(tmp_path, monkeypatch):
    return OldInstall(tmp_path, monkeypatch, "stuck-listing.json")


# ── P28 ──────────────────────────────────────────────────────────────────────

def test_stuck_install_adopts_with_exactly_the_jobs_box_open(stuck):
    out = sf.adopt()
    assert out["adopted"] is True and out["stamp"] is False
    assert out["open"] == ["jobs"]
    data = ss.load()
    assert data is not None and data["adopted_from"] == "pre-checklist install"
    assert [b["key"] for b in ss.open_boxes(data)] == ["jobs"]
    assert ss.box(data, "meetings")["status"] == ss.DONE
    assert ss.box(data, "deals")["status"] == ss.DONE
    assert "schedule your briefings" in out["line"] and ss.RESUME_COMMAND in out["line"]
    assert ["job", "install"] in stuck.nudge_calls
    assert data["nudges"]["stall_started"]
    assert ss.read_stamp() is None


def test_complete_install_adopts_silently_with_the_stamp_and_zero_nudges(complete):
    out = sf.adopt()
    assert out["adopted"] is True and out["stamp"] is True and out["open"] == []
    assert ss.read_stamp()["adopted"] is True
    assert not ss.exists()
    assert complete.nudge_calls == [], "0 nudges"
    # The nudge job, when it fires on an adopted-complete install, logs a
    # "ran, 0 to send" row through the nudge module.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sf.main(["nudge"]) == 0
    assert complete.nudge_calls == [["run"]]
    assert any(r.get("reason") == "ran, 0 to send" for r in ss.read_ledger())


def test_adopt_counts_are_re_counted_from_the_tree(complete):
    v = complete.listing["vault"]
    pages = len(list((complete.vault / "wiki" / "sources").glob("*.md")))
    assert pages == v["wiki_sources_md_count"]
    headings = sf._seeded_headings()
    assert len(headings) == v["hotcache_active_deal_headings"]
    assert "not a deal" not in headings, "a heading outside Active Threads is not a deal"
    sf.adopt()
    boxes = ss.read_stamp()["boxes"]
    assert boxes["meetings"] == ss.DONE and boxes["deals"] == ss.DONE
    assert boxes["briefings"] == ss.DONE and boxes["accounts"] == ss.DONE


def test_adopt_runs_once_per_plugin_version(complete):
    assert sf.adopt()["adopted"] is True
    again = sf.adopt()
    assert again["adopted"] is False and "already adopted" in again["reason"]
    stamp = json.loads((complete.state / "setup" / "adopted.json").read_text(encoding="utf-8"))
    assert stamp["version"] == sf._plugin_version() != ""


def test_adopt_leaves_an_install_in_progress_alone(stuck):
    ss.save(ss.new("live-run"))
    out = sf.adopt()
    assert out["adopted"] is False and "in progress" in out["reason"]
    assert ss.load()["run_id"] == "live-run"


def test_adopt_with_no_install_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "empty"))
    out = sf.adopt()
    assert out["adopted"] is False and "nothing installed" in out["reason"]
    assert not (tmp_path / "empty" / "setup").exists()


def _key_file(home: Path, google: dict | None, ms: dict | None) -> Path:
    creds = {"kind": "van-gogh-credentials"}
    if google:
        creds["google_client_secret"] = google
    if ms:
        creds["ms_client_secret"] = ms
    path = home / "Downloads" / "van-gogh-key.json"
    path.write_text(json.dumps({"kind": "van-gogh-key", "version": 2, "issued": "2026-10-01",
                                "credentials": creds, "accounts": [], "client": {},
                                "support_email": "team@example.com"}), encoding="utf-8")
    return path


def _google(client_id: str) -> dict:
    return {"installed": {"client_id": client_id, "client_secret": "s3cr3t",
                          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                          "token_uri": "https://oauth2.googleapis.com/token",
                          "redirect_uris": ["http://localhost"]}}


def test_adopt_writes_a_missing_credential(complete, monkeypatch):
    import companion_key
    (complete.state / "ms_client_secret.json").unlink()
    _key_file(complete.home, None, {"client_id": "new-ms-id", "tenant_id": "common"})
    monkeypatch.setattr(companion_key, "_refresh_works",
                        lambda p, l: pytest.fail("no refresh is needed for a missing credential"))
    out = sf.adopt()
    assert out["adopted"] is True
    written = json.loads((complete.state / "ms_client_secret.json").read_text(encoding="utf-8"))
    assert written["client_id"] == "new-ms-id"
    assert not (complete.home / "Downloads" / "van-gogh-key.json").exists()
    assert (complete.state / "keyfile" / "van-gogh-key.json").exists()


def test_adopt_keeps_a_differing_credential_whose_sign_in_works(complete, monkeypatch):
    import companion_key
    before = (complete.state / "google_client_secret.json").read_text(encoding="utf-8")
    _key_file(complete.home, _google("different-id.apps"), None)
    probes = []
    monkeypatch.setattr(companion_key, "_refresh_works",
                        lambda p, l: probes.append((p, l)) or True)
    out = sf.adopt()
    assert probes == [("google", "PERSONAL")]
    assert (complete.state / "google_client_secret.json").read_text(encoding="utf-8") == before
    assert any("kept the existing google app credential" in w for w in out["warnings"]), out


def test_adopt_replaces_a_differing_credential_whose_refresh_fails(complete, monkeypatch):
    import companion_key
    _key_file(complete.home, _google("different-id.apps"), None)
    monkeypatch.setattr(companion_key, "_refresh_works", lambda p, l: False)
    out = sf.adopt()
    assert out["adopted"] is True
    after = json.loads((complete.state / "google_client_secret.json").read_text(encoding="utf-8"))
    assert after["installed"]["client_id"] == "different-id.apps"
    assert not any("kept" in w for w in out["warnings"])


def test_the_update_path_invokes_adopt(complete, monkeypatch):
    import plugin_update
    from test_plugin_update import _UPDATED, _fake_cli, _stub_versions
    _stub_versions(monkeypatch, "0.80.2", "0.81.0")
    _fake_cli(monkeypatch, _UPDATED)
    called = []
    monkeypatch.setattr(sf, "adopt", lambda quiet=False, force=False: called.append(quiet) or {"ok": True})
    record = plugin_update.apply_update(claude="/usr/local/bin/claude")
    assert record["status"] == "ok"
    assert called == [True]
    assert record["adopt"] == {"ok": True}


def test_the_update_path_survives_an_adopt_that_raises(complete, monkeypatch):
    import plugin_update
    from test_plugin_update import _UPDATED, _fake_cli, _stub_versions
    _stub_versions(monkeypatch, "0.80.2", "0.81.0")
    _fake_cli(monkeypatch, _UPDATED)

    def boom(**k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sf, "adopt", boom)
    record = plugin_update.apply_update(claude="/usr/local/bin/claude")
    assert record["status"] == "ok" and record["adopt"] == {"ok": False, "error": "RuntimeError"}


def test_import_time_gate_is_a_file_check_and_never_raises(complete, monkeypatch):
    import user_state
    calls = []
    monkeypatch.setattr(sf, "adopt", lambda quiet=False, force=False: calls.append(1) or {})
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: False)
    sf.adopt_quietly()
    assert calls == [], "a dev checkout or pytest never adopts"
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: True)
    sf.adopt_quietly()
    assert calls == [1]
    (complete.state / "setup").mkdir(exist_ok=True)
    (complete.state / "setup" / "adopted.json").write_text(
        json.dumps({"version": sf._plugin_version()}), encoding="utf-8")
    sf.adopt_quietly()
    assert calls == [1], "stamped for this version: a file read, nothing else"
    (complete.state / "setup" / "adopted.json").write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")

    def boom(**k):
        raise RuntimeError("no")

    monkeypatch.setattr(sf, "adopt", boom)
    sf.adopt_quietly()  # must not raise
    monkeypatch.setenv("VAN_GOGH_UNATTENDED", "1")
    monkeypatch.setattr(sf, "adopt", lambda quiet=False, force=False: calls.append(2) or {})
    sf.adopt_quietly()
    assert calls == [1, 2], "unattended still adopts, silently"


def test_config_loader_import_calls_the_gate(monkeypatch):
    src = (PLUGIN_ROOT / "app" / "config_loader.py").read_text(encoding="utf-8")
    assert "setup_flow.adopt_quietly()" in src


# ── P27's orchestrator half: finish_machine_boxes ────────────────────────────

def _open_install(install):
    data = ss.new("r-nudge")
    for key in ("plugin", "python", "credentials", "accounts", "vault"):
        ss.set_status(data, key, ss.DONE)
    ss.set_status(data, "jobs", ss.OPEN)
    ss.save(data)
    return data


def test_finish_machine_boxes_zero_meetings_is_done_with_zero_filed(stuck):
    data = _open_install(stuck)
    done = sf.finish_machine_boxes(data)
    assert "meetings" in done
    assert ss.box(data, "meetings")["status"] == ss.DONE
    assert ss.box(data, "meetings")["detail"] == "0 filed"
    assert any(r.get("detail") == "0 filed" for r in ss.read_ledger())
    assert ss.box(data, "jobs")["status"] == ss.OPEN, "a human box is never finished here"
    assert ss.box(data, "deals")["status"] == ss.OPEN


def test_finish_machine_boxes_sign_in_error_leaves_the_box_open_and_named(stuck):
    stuck.ingest_reply = {"filed": [], "failed": [{"id": "m1", "error": "401 Unauthorized"}],
                          "deferred": [], "ingested": False, "errors": [],
                          "counts": {"filed": 0, "failed": 1, "deferred": 0}}
    data = _open_install(stuck)
    done = sf.finish_machine_boxes(data)
    assert "meetings" not in done
    assert ss.box(data, "meetings")["status"] == ss.OPEN
    assert ss.box(data, "meetings")["detail"].startswith("notetaker sign-in failed (401")


def test_finish_machine_boxes_three_meetings_filed(stuck):
    stuck.ingest_reply = {"filed": ["A", "B", "C"], "failed": [], "deferred": [], "ingested": True,
                          "errors": [], "counts": {"filed": 3, "failed": 0, "deferred": 0}}
    data = _open_install(stuck)
    assert "meetings" in sf.finish_machine_boxes(data)
    assert ss.box(data, "meetings")["detail"] == "3 filed"


def test_finish_machine_boxes_without_a_notetaker_key_is_not_applicable(stuck):
    env = stuck.state / ".env"
    env.write_text("\n".join(l for l in env.read_text(encoding="utf-8").splitlines()
                             if not l.startswith("GRANOLA_API_KEY")) + "\n", encoding="utf-8")
    data = _open_install(stuck)
    assert "meetings" in sf.finish_machine_boxes(data)
    assert ss.box(data, "meetings")["status"] == ss.NOT_APPLICABLE
    assert stuck.children == []


def test_finish_machine_boxes_restarts_a_prefetch_that_never_ran(stuck):
    stuck.ingest_reply = {"filed": [], "failed": [], "deferred": [], "ingested": False,
                          "errors": ["no staged summaries: run ingest-start first"],
                          "counts": {"filed": 0, "failed": 0, "deferred": 0}}
    data = _open_install(stuck)
    assert "meetings" not in sf.finish_machine_boxes(data)
    assert ss.box(data, "meetings")["status"] in (ss.OPEN, ss.PENDING)
    assert ("warm_start.py", sf.INGEST_START) in stuck.children


# ── skip, _job, nudge wrappers, prepare resilience ───────────────────────────

def test_skip_marks_a_box_and_refuses_the_two_that_can_never_be_skipped(stuck):
    data = _open_install(stuck)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sf.main(["skip", "--box", "accounts"]) == 1
    assert json.loads(buf.getvalue())["ok"] is False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sf.main(["skip", "--box", "deals"]) == 0
    out = json.loads(buf.getvalue())
    assert out["ok"] and "deals" not in out["open"]
    assert ss.box(ss.load(), "deals")["status"] == ss.SKIPPED
    assert "deals" in ss.load()["answers"]["skipped"]


def test_skipping_the_last_open_box_finishes_the_install(stuck):
    data = _open_install(stuck)
    for key in ("deals", "meetings", "briefings"):
        ss.set_status(data, key, ss.DONE)
    ss.save(data)
    out = sf.skip_box("jobs")
    assert out["ok"] and out["finish"]["complete"] is True
    assert ss.read_stamp() is not None and not ss.exists()
    assert ["job", "remove"] in stuck.nudge_calls


def test_nudge_wrappers_map_onto_setup_nudge_and_report_absence(stuck, monkeypatch):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sf.main(["nudge", "--maybe"])
        sf.main(["nudge-job", "status"])
    assert stuck.nudge_calls[-2:] == [["run", "--maybe"], ["job", "status"]]
    monkeypatch.setitem(sys.modules, "setup_nudge", None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sf.main(["nudge"])
    assert json.loads(buf.getvalue()) == {"ok": False, "error": "nudges not installed"}


def test_job_wrapper_runs_detached_work_and_writes_pid_and_result(tmp_path, monkeypatch):
    state = tmp_path / "s"
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    r = subprocess.run([sys.executable, str(sf.APP_DIR / "setup_flow.py"), "_job", "demo", "--",
                        sys.executable, "-c", "print('hello from the job')"],
                       capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    result = json.loads((state / "setup" / "jobs" / "demo.result.json").read_text(encoding="utf-8"))
    assert result["rc"] == 0 and "hello from the job" in result["stdout_tail"]
    assert (state / "setup" / "jobs" / "demo.pid").read_text(encoding="utf-8").strip().isdigit()


def test_prepare_survives_a_failed_nudge_job_install(stuck, monkeypatch):
    monkeypatch.setitem(sys.modules, "setup_nudge", types.SimpleNamespace(
        main=lambda argv: (_ for _ in ()).throw(FileNotFoundError("launchctl"))))
    monkeypatch.setitem(sys.modules, "companion_key", types.SimpleNamespace(
        discover=lambda: [], install_all=lambda p: {"ok": False}))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert sf.main(["prepare", "--run-id", "r-ci"]) == 0
    out = json.loads(buf.getvalue())
    assert out["ok"] is True and out["nudge_job"] is False
    assert ss.load()["nudges"]["job"].startswith("not installed")


def test_report_reads_a_saved_ledger_and_fails_on_a_degraded_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "s"))
    led = tmp_path / "timing.jsonl"
    rows = [
        {"ts": "2026-10-01T09:00:00-04:00", "run_id": "r1", "event": "start", "class": "machine"},
        {"ts": "2026-10-01T09:00:10-04:00", "run_id": "r1", "event": "job_begin", "step": "vault",
         "class": "machine", "detail": "migrate_to_vault.py:--vault"},
        {"ts": "2026-10-01T09:00:40-04:00", "run_id": "r1", "event": "job_end", "step": "vault",
         "class": "machine", "detail": "migrate_to_vault.py:--vault"},
        {"ts": "2026-10-01T09:01:00-04:00", "run_id": "r1", "event": "prompt_shown", "step": "jobs",
         "prompt": "schedule", "class": "human"},
        {"ts": "2026-10-01T09:01:20-04:00", "run_id": "r1", "event": "answer_received", "step": "jobs",
         "prompt": "schedule", "class": "human"},
        {"ts": "2026-10-01T09:01:30-04:00", "run_id": "r1", "event": "signin_failed", "step": "signin:google",
         "class": "human", "detail": "access_denied: cancelled"},
        {"ts": "2026-10-01T09:02:00-04:00", "run_id": "r1", "event": "dispatch", "step": "briefings",
         "phase": "begin", "class": "model"},
        {"ts": "2026-10-01T09:04:00-04:00", "run_id": "r1", "event": "dispatch", "step": "briefings",
         "phase": "end", "class": "model"},
        {"ts": "2026-10-01T09:05:00-04:00", "run_id": "r1", "event": "done", "class": "machine"},
    ]
    led.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    m = sf._measure(rows, "r1")
    assert (m["machine_s"], m["human_s"], m["model_s"], m["total_s"]) == (30.0, 20.0, 120.0, 300.0)
    assert m["signin_failures"] == ["access_denied: cancelled"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sf.main(["report", "--ledger", str(led)])
    out = json.loads(buf.getvalue())
    assert rc == 1 and out["line"] == "TOTAL 05:00 of 15:00 INCOMPLETE"
    ss.write_stamp({"run_id": "r1", "boxes": {k: ss.DONE for k in ss.KEYS}, "answers": {}})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sf.main(["report", "--ledger", str(led)])
    out = json.loads(buf.getvalue())
    assert rc == 0 and out["line"] == "TOTAL 05:00 of 15:00" and out["total"] == "05:00"
    ss.remove_stamp()
    data = ss.new("r1")
    data["degraded"] = {"box": "meetings", "reason": "usage cap"}
    for k in ss.KEYS:
        ss.set_status(data, k, ss.DONE)
    ss.set_status(data, "meetings", ss.PENDING, "6 filed, 4 deferred")
    ss.save(data)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sf.main(["report", "--ledger", str(led)])
    out = json.loads(buf.getvalue())
    assert rc == 1 and "DEGRADED" in out["line"]


def test_no_dash_in_the_orchestrator_or_its_tests():
    em, en = chr(0x2014), chr(0x2013)
    for p in (PLUGIN_ROOT / "app" / "setup_flow.py", Path(__file__),
              PLUGIN_ROOT / "tests" / "test_setup_flow_e2e.py",
              PLUGIN_ROOT / "skills" / "install-van-gogh" / "SKILL.md",
              PLUGIN_ROOT / "skills" / "uninstall-van-gogh" / "SKILL.md",
              PLUGIN_ROOT / "objectives" / "fifteen-minute-install" / "cold_read.md",
              PLUGIN_ROOT / "objectives" / "fifteen-minute-install" / "cold_read_plant.py"):
        text = p.read_text(encoding="utf-8")
        assert em not in text and en not in text, p


# ── P27 (three meetings, through the real filing path) ───────────────────────

def _real_ingest_finish(install, monkeypatch, vault):
    """Stand in for the `warm_start.py ingest-finish` CHILD only: the notetaker
    is Lane B's three-note FakeProvider and the model is stubbed, while the
    prefetch and the staged filing are the real meeting_ingest functions
    writing into `vault`. The subprocess boundary is the one thing replaced."""
    import ingest_fixture as fx
    import meeting_ingest as gi
    import warm_start

    def child(name, args, timeout=600, input_text=None, cwd=None):
        install.children.append((name, list(args)))
        if name == "warm_start.py" and args[0] == "ingest-finish":
            staged = install.state / "setup" / "staged"
            staged.mkdir(parents=True, exist_ok=True)
            with fx.prepared(gi, vault, monkeypatch):
                manifest = gi.run_prefetch(3, staged)
                ns = fx.nightly_args(3)
                ns.staged = str(staged)
                result = gi.run_staged(ns, staged)
            filed = [p.stem for p in sorted((vault / "wiki" / "sources").glob("*.md"))
                     if not p.name.startswith("catalog")]
            reply = {"filed": filed, "failed": manifest.get("failed") or [],
                     "deferred": manifest.get("deferred") or [],
                     "ingested": bool(result), "errors": [],
                     "counts": {"filed": len(filed), "failed": 0, "deferred": 0}}
            return {"rc": 0, "json": reply, "text": json.dumps(reply)}
        return {"rc": 0, "json": {}, "text": "OK"}

    monkeypatch.setattr(sf, "_run_script_raw", child)


def test_three_meetings_are_really_filed_into_the_vault(tmp_path, monkeypatch):
    install = OldInstall(tmp_path, monkeypatch, "stuck-listing.json")
    vault = tmp_path / "ingest-vault"
    _real_ingest_finish(install, monkeypatch, vault)
    data = _open_install(install)
    assert not (vault / "wiki" / "sources").exists()
    done = sf.finish_machine_boxes(data)
    assert "meetings" in done
    assert ss.box(data, "meetings")["detail"] == "3 filed"
    pages = sorted(p.name for p in (vault / "wiki" / "sources").glob("*.md")
                   if not p.name.startswith("catalog"))
    assert pages == ["Acme widget pricing call - 2026-09-29.md",
                     "Harbor Energy site walk - 2026-09-30.md",
                     "Weekly internal sync - 2026-09-28.md"]
    for name in pages:
        text = (vault / "wiki" / "sources" / name).read_text(encoding="utf-8")
        assert "meeting_id:" in text and len(text) > 200


# ── P31: the stamp check runs from the choke point on EVERY command ──────────

def _listing(monkeypatch):
    """The scheduler listing, stubbed: the exact names of every enabled
    registry row, so nothing here reads the real launchd session."""
    import job_registry
    names = {j.mac_label if sys.platform == "darwin" else j.win_task
             for j in job_registry.jobs() if j.is_enabled()}
    assert names, "the fixture config enables at least one job"
    state = {"names": set(names), "calls": 0}

    def listing(platform=None):
        state["calls"] += 1
        return state["names"]

    monkeypatch.setattr(sf, "_scheduler_listing", listing)
    return state


def _managed(monkeypatch):
    import user_state
    from datetime import datetime, timezone
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: True)
    clock = {"t": datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(sf, "NOW", lambda: clock["t"])
    return clock


def test_every_command_runs_the_local_check_and_the_refresh_at_most_once_a_day(complete, monkeypatch):
    from datetime import timedelta
    clock = _managed(monkeypatch)
    monkeypatch.setenv("VAN_GOGH_UNATTENDED", "1")
    state = _listing(monkeypatch)
    refreshes = []
    monkeypatch.setattr(sf, "refresh_state", lambda p, l: refreshes.append((p, l)) or sf.WORKS)
    assert sf.stamp_check_quietly() is None, "no stamp: nothing runs"
    assert state["calls"] == 0, "no stamp: the scheduler is not even listed"
    assert sf.adopt()["stamp"] is True
    out = sf.stamp_check_quietly()
    assert out["already_installed"] is True and out["live"] is True
    assert refreshes == [("google", "Personal")] and state["calls"] == 1
    # Same day: the local check still runs, the network is not touched.
    out = sf.stamp_check_quietly()
    assert out["already_installed"] is True and out["live"] is False
    assert refreshes == [("google", "Personal")] and state["calls"] == 2
    clock["t"] += timedelta(hours=25)
    monkeypatch.setattr(sf, "refresh_state", lambda p, l: refreshes.append((p, l)) or sf.REJECTED)
    out = sf.stamp_check_quietly()
    assert out["already_installed"] is False and out["reopened"] == ["accounts"]
    assert len(refreshes) == 2
    assert ss.exists() and ["job", "install"] in complete.nudge_calls
    assert sf.stamp_check_quietly() is None, "a checklist is open: the nudge owns it now"


def test_an_unloaded_job_reopens_the_box_the_same_day_with_the_network_off(complete, monkeypatch):
    import time as _time
    _managed(monkeypatch)
    state = _listing(monkeypatch)
    monkeypatch.setattr(sf, "refresh_state", lambda p, l: sf.WORKS)
    assert sf.adopt()["stamp"] is True
    assert sf.stamp_check_quietly()["live"] is True, "the day's live check is fresh"

    def network_off(*a, **k):
        raise AssertionError("the local check must not touch the network")

    monkeypatch.setattr(sf, "refresh_state", network_off)
    monkeypatch.setattr(sf, "_run_script_raw", network_off)
    victim = sorted(state["names"])[0]
    state["names"].discard(victim)
    t0 = _time.monotonic()
    out = sf.stamp_check_quietly()
    elapsed = _time.monotonic() - t0
    assert elapsed < 5
    assert out["already_installed"] is False and out["reopened"] == ["jobs"]
    assert "already installed" not in out["line"] and ss.RESUME_COMMAND in out["line"]
    data = ss.load()
    assert ss.box(data, "jobs")["status"] == ss.OPEN
    assert ss.box(data, "jobs")["detail"].startswith("1 of ")
    assert [b["key"] for b in ss.open_boxes(data)] == ["jobs"]
    assert ["job", "install"] in complete.nudge_calls


def test_the_choke_point_calls_the_stamp_check_last_and_every_time(complete, monkeypatch):
    _managed(monkeypatch)
    _listing(monkeypatch)
    sf.adopt()
    seen = []

    def fake_verify(live=False, now=None, local_checks=None):
        seen.append((live, sorted(local_checks)))
        return {"ok": True}

    monkeypatch.setattr(sf, "verify_stamp", fake_verify)
    for _ in range(3):
        sf.stamp_check_quietly()
    assert seen == [(True, ["accounts", "credentials", "jobs", "vault"])] * 3
    src = (PLUGIN_ROOT / "app" / "config_loader.py").read_text(encoding="utf-8")
    assert src.count("setup_flow.stamp_check_quietly()") == 1
    assert src.count("setup_flow.adopt_quietly()") == 1
    # Both hooks read the job table, which imports config_loader back, so they
    # must sit after the last definition in the module.
    last_def = max(src.rfind("\ndef "), src.rfind("\nclass "))
    assert src.index("setup_flow.adopt_quietly()") > last_def
    assert src.index("setup_flow.stamp_check_quietly()") > last_def


def test_the_stamp_check_never_raises(complete, monkeypatch):
    _managed(monkeypatch)
    _listing(monkeypatch)
    sf.adopt()

    def boom(**k):
        raise RuntimeError("no")

    monkeypatch.setattr(sf, "verify_stamp", boom)
    assert sf.stamp_check_quietly() is None


def test_the_job_listing_matches_exact_names_from_one_call(monkeypatch):
    """`com.monet.week` must not read as loaded because `com.monet.week-retro`
    is; and the scheduler is listed once per process."""
    monkeypatch.setenv("VAN_GOGH_SCHEDULER_SYNC", "1")   # the scheduler is a stub here
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "PID\tStatus\tLabel\n-\t0\tcom.monet.week-retro\n"
                                                    "123\t0\tcom.monet.job-watch\n", "")

    monkeypatch.setattr(sf, "_LISTING", {})
    monkeypatch.setattr(sf.subprocess, "run", run)
    names = sf._scheduler_listing("darwin")
    assert "com.monet.week-retro" in names and "com.monet.week" not in names
    sf._scheduler_listing("darwin")
    assert len(calls) == 1 and calls[0] == ["launchctl", "list"]
    monkeypatch.setattr(sys, "platform", "darwin")
    row = lambda key, label: types.SimpleNamespace(key=key, mac_label=label, win_task=label,
                                                   is_enabled=lambda: True)
    monkeypatch.setitem(sys.modules, "job_registry", types.SimpleNamespace(
        jobs=lambda: [row("week", "com.monet.week"), row("week-retro", "com.monet.week-retro")]))
    assert sf.check_jobs_one_listing() == (ss.OPEN, "1 of 2 jobs not loaded")
    monkeypatch.setattr(sf, "_LISTING", {"darwin": None})
    assert sf.check_jobs_one_listing() == (None, "could not read the scheduler")


# ── P31: the refresh is three-valued, and offline is not "revoked" ───────────

@pytest.mark.parametrize("rc,text,expected", [
    (0, "OK [Personal] gmail: jane@example.com", "works"),
    (1, "google.auth.exceptions.RefreshError: ('invalid_grant: Token has been expired or revoked.', "
        "{'error': 'invalid_grant'})", "rejected"),
    (1, "RuntimeError: invalid_grant - AADSTS700082: The refresh token has expired due to inactivity.", "rejected"),
    (1, "RuntimeError: invalid_client - AADSTS7000215: Invalid client secret provided.", "rejected"),
    (1, "RuntimeError: unauthorized_client - the client is not authorized", "rejected"),
    (1, "RuntimeError: Missing refresh token: env var GOOGLE_REFRESH_TOKEN_PERSONAL is not set.", "rejected"),
    (1, "google.auth.exceptions.TransportError: HTTPSConnectionPool(host='oauth2.googleapis.com', "
        "port=443): Max retries exceeded (nodename nor servname provided)", "unknown"),
    (1, "requests.exceptions.ConnectionError: Connection refused", "unknown"),
    (1, "requests.exceptions.ProxyError: 407 Proxy Authentication Required", "unknown"),
    (1, "HTTP 503 Service Unavailable from https://login.microsoftonline.com", "unknown"),
    (1, "Traceback (most recent call last): KeyError: 'installed'", "unknown"),
    (1, "", "unknown"),
])
def test_refresh_state_reads_the_clients_real_output_three_ways(monkeypatch, rc, text, expected):
    monkeypatch.setattr(sf, "_run_script_raw",
                        lambda name, args, *a, **k: {"rc": rc, "json": {}, "text": text})
    assert sf.refresh_state("google", "Personal") == expected


@pytest.mark.parametrize("exc", [OSError("network is unreachable"),
                                 subprocess.TimeoutExpired("x", 60)])
def test_refresh_state_is_unknown_when_nothing_answers(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(sf, "_run_script_raw", boom)
    assert sf.refresh_state("microsoft", "Work") == "unknown"


def test_offline_with_the_live_check_due_is_not_told_to_sign_in_again(complete, monkeypatch):
    """The evaluator's scenario: network off, clock 25 hours on. Still
    "already installed", nothing reopened, no nudge job, and the live stamp
    not advanced, so the next command tries the refresh again."""
    from datetime import timedelta
    clock = _managed(monkeypatch)
    _listing(monkeypatch)
    monkeypatch.setattr(sf, "refresh_state", lambda p, l: sf.WORKS)
    assert sf.adopt()["stamp"] is True
    assert sf.stamp_check_quietly()["live"] is True
    stamp_before = sf._live_check_path().read_text(encoding="utf-8")
    attempts = []

    def offline(name, args, timeout=600, input_text=None, cwd=None):
        attempts.append(name)
        raise OSError("network is unreachable")

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(sf, "refresh_state", _real_refresh_state)   # the real three-way read
        mp.setattr(sf, "_run_script_raw", offline)
        clock["t"] += timedelta(hours=25)
        out = sf.stamp_check_quietly()
        assert attempts == ["google_client.py"], "the refresh was tried through the real client"
        assert out["already_installed"] is True and out["line"] == "already installed"
        assert out["live"] is False and "reopened" not in out
        assert not ss.exists(), "nothing reopened"
        assert complete.nudge_calls == [], "no nudge job"
        assert sf._live_check_path().read_text(encoding="utf-8") == stamp_before, "stamp not advanced"
        out = sf.stamp_check_quietly()
        assert attempts == ["google_client.py"] * 2, "the next command tries again"
        # The provider answers and refuses: now, and only now, the box reopens.
        mp.setattr(sf, "_run_script_raw", lambda *a, **k: {
            "rc": 1, "json": {}, "text": "RefreshError: ('invalid_grant: Token has been expired or revoked.')"})
        out = sf.stamp_check_quietly()
        assert out["already_installed"] is False and out["reopened"] == ["accounts"]
        assert ["job", "install"] in complete.nudge_calls


_real_refresh_state = sf.refresh_state


def test_an_expired_sign_in_reopens_accounts_on_the_local_check_alone(complete, monkeypatch):
    _managed(monkeypatch)
    _listing(monkeypatch)
    monkeypatch.setattr(sf, "refresh_state", lambda p, l: sf.WORKS)
    assert sf.adopt()["stamp"] is True
    assert sf.stamp_check_quietly()["live"] is True

    def network_off(*a, **k):
        raise AssertionError("the local check must not touch the network")

    monkeypatch.setattr(sf, "refresh_state", network_off)
    monkeypatch.setattr(sf, "_run_script_raw", network_off)
    env = complete.state / ".env"
    base = env.read_text(encoding="utf-8")
    # A future stamp and a missing stamp are both a working sign-in.
    env.write_text(base + "GOOGLE_REFRESH_EXPIRES_PERSONAL=2999-01-01T00:00:00Z\n", encoding="utf-8")
    assert sf.stamp_check_quietly()["already_installed"] is True
    env.write_text(base + "GOOGLE_REFRESH_EXPIRES_PERSONAL=2020-03-09T12:00:00+00:00\n", encoding="utf-8")
    out = sf.stamp_check_quietly()
    assert out["already_installed"] is False and out["reopened"] == ["accounts"]
    assert ss.box(ss.load(), "accounts")["detail"] == "the Google sign-in for Personal expired on March 9"
    assert "sign in to your accounts" in out["line"]
