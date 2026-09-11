"""The scorecard's edges: the briefings, the scheduler, and the watcher.

Everything here guards a promise made elsewhere. The briefings must be unharmed
by the telemetry hanging off them, the scheduled job must disappear when the
feature is turned off, and the watcher must know the job exists at all, since
a job nobody grades is a job that can stop silently.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import kpi_events
import kpi_report
import kpi_send

APP = Path(__file__).resolve().parent.parent / "app"
BRIEFINGS = ["morning_coffee", "afternoon_tea", "week_review", "week_retro"]


# ── The briefings are unharmed ───────────────────────────────────────────────

@pytest.mark.parametrize("script", BRIEFINGS)
def test_each_briefing_records_exactly_one_snapshot(script):
    """Twice would count one morning as two runs and halve every average."""
    src = (APP / f"{script}.py").read_text(encoding="utf-8")
    assert src.count("kpi_events.record_snapshot(") == 1, \
        f"{script} does not record exactly once"


@pytest.mark.parametrize("script", BRIEFINGS)
def test_each_briefing_imports_the_shared_module(script):
    src = (APP / f"{script}.py").read_text(encoding="utf-8")
    assert re.search(r"^import kpi_events$", src, re.M), \
        f"{script} records without importing the module"


def test_a_raising_recorder_never_costs_a_briefing(monkeypatch, capsys):
    """The telemetry is worth less than the briefing it watches."""
    def boom(*_a, **_k):
        raise RuntimeError("disk is full")

    monkeypatch.setattr(kpi_events, "record", boom)
    kpi_events.record_snapshot("morning-coffee", {"counts": {"open": 1}})
    assert capsys.readouterr().err == ""


def test_the_data_subprocess_does_not_record_a_second_time():
    """week_review feeding another briefing must not add a run of its own.

    Guarded by the same flag the extensions use, so the two can never drift
    apart about what counts as a real run.
    """
    src = (APP / "week_review.py").read_text(encoding="utf-8")
    idx = src.index("kpi_events.record_snapshot(")
    preceding = src[max(0, idx - 400):idx]
    assert "no_extensions" in preceding, \
        "the snapshot is not behind the data-subprocess guard"


# ── No model call anywhere in the path ───────────────────────────────────────

KPI_MODULES = ["kpi_events.py", "kpi_report.py", "kpi_html.py", "kpi_send.py"]


@pytest.mark.parametrize("name", KPI_MODULES)
def test_no_model_call_in_the_kpi_path(name):
    """A weekly job that spawned the CLI would inherit its whole failure
    taxonomy: version rot, usage caps, classifier outages. It must not."""
    src = (APP / name).read_text(encoding="utf-8")
    for banned in ("claude_cli", "run_claude", "anthropic", "claude_bin"):
        assert banned not in src, f"{name} reaches for a model via {banned}"


def test_the_model_call_scanner_can_find_a_planted_one():
    """MUTATION CONTROL: the scan is proven before its zero is trusted."""
    planted = "import claude_cli\n"
    assert any(b in planted for b in ("claude_cli", "run_claude"))


def test_the_scan_actually_read_the_files():
    """A scan over an empty set passes trivially."""
    for name in KPI_MODULES:
        assert len((APP / name).read_text(encoding="utf-8")) > 500


# ── The scheduled job ────────────────────────────────────────────────────────

def test_the_plist_is_weekly_and_names_the_right_script(tmp_path):
    import scheduler_setup as S

    xml = S._kpi_plist_content("/repo", "/py", tmp_path, "friday", 16, 0)
    from xml.dom.minidom import parseString

    parseString(xml)                      # must be valid XML
    # launchd counts weekdays from SUNDAY (Friday is 5); Python counts from
    # Monday (Friday is 4). Both appear in this feature and neither is wrong,
    # so this asserts the launchd number from the shared table rather than a
    # literal, which is what stops the two conventions being conflated.
    assert (f"<key>Weekday</key><integer>{S.LAUNCHD_WEEKDAY['friday']}</integer>"
            in xml)
    assert "<key>Hour</key><integer>16</integer>" in xml
    assert "kpi_send.py" in xml
    assert S.KPI_LABEL in xml
    # StartInterval would fire it every N seconds; this one is a calendar job.
    assert "StartInterval" not in xml


def test_the_job_carries_no_claude_path():
    """It computes in Python, so it must not depend on the CLI being found."""
    import scheduler_setup as S

    xml = S._kpi_plist_content("/repo", "/py", Path("/logs"), "friday", 16, 0)
    assert "claude" not in xml.lower()


def test_the_windows_task_is_weekly():
    import scheduler_setup as S

    ps = S._kpi_task_ps("/repo", Path("/x.vbs"), "friday", 16, 0)
    assert "-Weekly" in ps and "-DaysOfWeek Friday" in ps
    assert S.KPI_TASK in ps


def test_install_and_uninstall_use_the_same_names():
    """A rename that misses one side leaves an orphan firing forever."""
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert src.count("KPI_LABEL") >= 3
    assert src.count("KPI_TASK") >= 3


def test_turning_it_off_removes_the_job_rather_than_leaving_it():
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert "the weekly scorecard is off" in src


def test_neither_optional_job_can_short_circuit_the_other():
    """An early return once meant disabling prep skipped every later job."""
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert "_register_optional_win_task" in src
    assert src.count("_register_optional_win_task(") >= 3


# ── The watcher ──────────────────────────────────────────────────────────────

def test_the_watcher_ignores_the_job_while_the_feature_is_off(monkeypatch):
    import config_loader
    import job_watch

    monkeypatch.setattr(config_loader, "kpi_enabled", lambda: False)
    assert not any(j["name"] == "kpi-email" for j in job_watch.roster())


def test_the_watcher_grades_the_job_when_it_is_on(monkeypatch):
    import config_loader
    import job_watch

    monkeypatch.setattr(config_loader, "kpi_enabled", lambda: True)
    monkeypatch.setattr(config_loader, "kpi_day", lambda: "friday")
    monkeypatch.setattr(config_loader, "kpi_time", lambda: "16:00")
    rows = [j for j in job_watch.roster() if j["name"] == "kpi-email"]
    assert len(rows) == 1
    job = rows[0]
    assert job["days"] == {4}
    assert job["hour"] == 16 and job["minute"] == 0
    assert job["ledger_job"] == kpi_send.JOB_NAME
    # Graded by the artifact, which is written only after the mail is away.
    assert str(job["deliverable"]).endswith("latest.html")


def test_the_deliverable_is_written_after_the_send_not_before():
    """Grading by a file written first would call a failed send delivered.

    Compares the CALL sites inside main(), not the function definition, which
    necessarily appears earlier in the file.
    """
    src = (APP / "kpi_send.py").read_text(encoding="utf-8")
    main_at = src.index("def main(")
    body = src[main_at:]
    send_at = body.index("_deliver(account,")
    write_at = body.index("_write_artifacts(")
    assert write_at > send_at, "the artifact is written before the send"


def test_the_weekday_conventions_are_not_conflated():
    """launchd counts from Sunday, Python from Monday.

    A single off-by-one here would schedule the scorecard on the wrong day and
    look perfectly correct in both files.
    """
    import job_watch
    import scheduler_setup as S

    for day, py_index in job_watch._WEEKDAY_INDEX.items():
        launchd = S.LAUNCHD_WEEKDAY[day]
        assert launchd == (py_index + 1) % 7, day


# ── Config ───────────────────────────────────────────────────────────────────

def test_the_template_and_the_code_agree_on_every_default():
    import json

    import config_loader

    template = json.loads(
        (APP.parent / "config.template.json").read_text(encoding="utf-8"))
    assert template["kpi"] == config_loader.KPI_DEFAULTS


def test_a_junk_day_or_time_falls_back_rather_than_crashing(monkeypatch):
    import config_loader

    monkeypatch.setattr(config_loader, "_kpi",
                        lambda: {"day": "someday", "time": "99:99",
                                 "window_days": "lots"})
    assert config_loader.kpi_day() == "friday"
    assert config_loader.kpi_time() == "16:00"
    assert config_loader.kpi_window_days() == 14


def test_the_window_is_always_even_so_the_halves_match(monkeypatch):
    import config_loader

    monkeypatch.setattr(config_loader, "_kpi", lambda: {"window_days": 21})
    assert config_loader.kpi_window_days() % 2 == 0


def test_the_feature_ships_off():
    import config_loader

    assert config_loader.KPI_DEFAULTS["enabled"] is False
    assert config_loader.KPI_DEFAULTS["share_with_support"] is False


# ── Skill usage ──────────────────────────────────────────────────────────────

def test_usage_tracking_separates_a_human_run_from_a_scheduled_one(
        tmp_path, monkeypatch):
    """A nightly job must not make a skill look like a habit the user formed."""
    import json
    import sys

    import config_loader
    import user_state

    monkeypatch.setattr(config_loader, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: True)
    monkeypatch.setattr(sys, "argv", ["/x/app/relationship_radar.py"])

    monkeypatch.setenv("VAN_GOGH_UNATTENDED", "1")
    user_state.record_skill_use()
    row = json.loads((tmp_path / "skill_usage.json").read_text(encoding="utf-8"))
    assert row["relationship_radar"]["last"]
    assert "attended" not in row["relationship_radar"]

    monkeypatch.delenv("VAN_GOGH_UNATTENDED")
    user_state.record_skill_use()
    row = json.loads((tmp_path / "skill_usage.json").read_text(encoding="utf-8"))
    assert row["relationship_radar"]["attended"]


def test_usage_tracking_never_writes_outside_a_real_install(tmp_path,
                                                            monkeypatch):
    import config_loader
    import user_state

    monkeypatch.setattr(config_loader, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: False)
    user_state.record_skill_use()
    assert not (tmp_path / "skill_usage.json").exists()


def test_every_spotlight_names_a_skill_that_exists():
    """A suggestion pointing at a command that does not exist is worse than
    no suggestion."""
    skills_dir = APP.parent / "skills"
    for entry in kpi_report.SPOTLIGHTS:
        assert (skills_dir / entry["skill"]).is_dir(), \
            f"{entry['skill']} is suggested but not shipped"
        assert entry["command"] == f"/van-gogh:{entry['skill']}"


def test_no_spotlight_text_carries_a_dash():
    em, en = "\u2014", "\u2013"      # escapes: see test_kpi_html.py
    for entry in kpi_report.SPOTLIGHTS:
        for field in ("title", "what", "when"):
            assert em not in entry[field] and en not in entry[field]


def test_no_spotlight_claims_a_day_the_email_cannot_know():
    """The email does not know which day it is being read.

    "Worth a run on a quiet Friday, before the week closes" shipped once on a
    fortnight ending on a Wednesday.
    """
    banned = ("friday", "monday", "tuesday", "wednesday", "thursday",
              "saturday", "sunday", "before the week closes", "today",
              "this morning")
    for entry in kpi_report.SPOTLIGHTS:
        text = f"{entry['what']} {entry['when']}".lower()
        for word in banned:
            assert word not in text, \
                f"{entry['skill']} claims a specific day: {word!r}"


# ── The live control, as a test rather than a one-off command ───────────────

def test_removing_the_event_log_blanks_every_event_sourced_measure():
    """The falsifiable control for the live check, made repeatable.

    Five of the six measures read the events log and nothing else. With no
    events they must ALL report unmeasured: a control that leaves any of them
    answering would launder a green result on the live run.

    KPI 20 is deliberately excluded here. It sources from the vault's meeting
    notes rather than the log, which is why the first version of this control
    passed while one card still reported: the control was aimed at the wrong
    input for that card. Its own control is the next test.
    """
    import kpi_report

    report = kpi_report.compute(window_days=14, rows=[])
    event_sourced = [k for k in report["kpis"] if k["slug"] != "meeting_actions"]
    assert len(event_sourced) == 5
    for row in event_sourced:
        assert row["measured"] is False, f"{row['slug']} answered with no events"
        assert row["reason"], f"{row['slug']} gave no reason"


def test_removing_the_meeting_notes_blanks_the_one_vault_sourced_measure(
        monkeypatch):
    """KPI 20's own control: its input is the vault, not the log."""
    import kpi_report

    monkeypatch.setattr(kpi_report, "_meeting_pages", lambda *a, **k: [])
    report = kpi_report.compute(window_days=14, rows=[])
    assert report["measured_count"] == 0, \
        "a measure answered with both inputs removed"


def test_the_control_can_actually_pass_when_the_inputs_are_there():
    """MUTATION CONTROL for the two controls above.

    A control that reports zero because the report is broken proves nothing,
    so the same call with real inputs must measure something.
    """
    import kpi_events
    import kpi_report
    from datetime import datetime, timedelta

    now = datetime(2026, 9, 9, 16, 0)
    rows = []
    for days in (13, 1):
        rows.append({
            "ts": (now - timedelta(days=days)).isoformat(timespec="seconds"),
            "kind": kpi_events.SNAPSHOT, "briefing": "morning-coffee",
            "counts": {"open": 3, "late": 0, "front": 1},
            "front": [{"key": "k1", "late": 0}],
            "open_keys": ["k1", "k2", "k3"][:3 if days == 13 else 1],
            "late_keys": [],
        })
    rows.append({"ts": (now - timedelta(days=4)).isoformat(timespec="seconds"),
                 "kind": kpi_events.CLOSED, "keys": ["k2", "k3"]})
    report = kpi_report.compute(window_days=14, now=now, rows=rows)
    assert report["measured_count"] >= 1, \
        "the control proves nothing: nothing measures even with real input"
