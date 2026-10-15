"""The five capabilities that used to wait to be asked, on a schedule.

Each is a skill run headlessly, so two things have to hold beyond the job
table's own guard: the settings cannot schedule something by accident, and
each skill says what to do when there is nobody to ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config_loader
import job_registry
import voice_generator

SKILLS = Path(__file__).resolve().parent.parent / "skills"
FIVE = sorted(job_registry.SCHEDULED_SKILLS)


def _with(monkeypatch, cadence):
    monkeypatch.setattr(config_loader, "cfg", lambda: {"cadence": cadence})


def test_there_are_five_and_every_one_ships_off(monkeypatch):
    assert FIVE == ["calendar-stub-check", "five-fifteen", "relationship-radar",
                    "voice-calibration", "voice-generator"]
    assert set(config_loader.SKILL_SCHEDULE_DEFAULTS) == set(FIVE)
    monkeypatch.setattr(config_loader, "cfg", lambda: {})
    for skill in FIVE:
        assert config_loader.skill_schedule(skill)["enabled"] is False
        assert job_registry.by_key(skill).is_enabled() is False


@pytest.mark.parametrize("value", ["true", 1, "yes", None, {}])
def test_only_a_literal_true_switches_one_on(monkeypatch, value):
    _with(monkeypatch, {"relationship-radar": {"enabled": value}})
    assert config_loader.skill_schedule("relationship-radar")["enabled"] is False


def test_a_setting_retimes_the_job(monkeypatch):
    _with(monkeypatch, {"relationship-radar": {
        "enabled": True, "days": ["Wednesday", "friday"], "time": "6:05"}})
    job = job_registry.by_key("relationship-radar")
    cadence = job.resolved_cadence()
    assert job.is_enabled()
    assert (cadence.days, cadence.hour, cadence.minute) == (("wednesday", "friday"), 6, 5)


@pytest.mark.parametrize("bad", [
    {"days": ["someday"], "time": "25:00"},
    {"days": "monday", "time": "soon"},
    {"days": [], "time": None},
    "not a block",
])
def test_a_bad_setting_costs_the_chosen_time_never_the_job(monkeypatch, bad):
    _with(monkeypatch, {"relationship-radar": bad})
    spec = config_loader.skill_schedule("relationship-radar")
    assert spec["days"] == ["monday"] and spec["time"] == "05:20"
    job_registry.by_key("relationship-radar").resolved_cadence()


def test_every_day_of_the_week_is_one_daily_trigger(monkeypatch):
    monkeypatch.setattr(config_loader, "cfg", lambda: {})
    cadence = job_registry.by_key("calendar-stub-check").resolved_cadence()
    assert cadence.days == () and cadence.describe() == "daily 05:05"


def test_a_stray_key_schedules_nothing(monkeypatch):
    _with(monkeypatch, {"uninstall-van-gogh": {"enabled": True, "days": ["monday"],
                                               "time": "05:00"}})
    assert config_loader.skill_schedule("uninstall-van-gogh") == {
        "enabled": False, "days": [], "time": "00:00"}
    assert job_registry.by_key("uninstall-van-gogh") is None


def test_each_runs_the_skill_it_is_named_for():
    for skill in FIVE:
        job = job_registry.by_key(skill)
        assert job.script == "skill_run.py" and job.args == (skill,)
        assert job.needs_claude and job.ledger_job == f"scheduled.{skill}"
        assert (SKILLS / skill / "SKILL.md").exists()


def test_no_two_of_them_and_no_ingest_share_a_minute(monkeypatch):
    """Two headless renders against one vault at the same instant is the
    collision the ingests were moved apart to avoid."""
    monkeypatch.setattr(config_loader, "cfg", lambda: {})
    slots = {}
    for key in FIVE + ["meeting-ingest", "ingest-workspace"]:
        cadence = job_registry.by_key(key).resolved_cadence()
        for day in (cadence.days or tuple(job_registry.LAUNCHD_WEEKDAY)):
            slot = (day, cadence.hour, cadence.minute)
            assert slot not in slots, f"{key} and {slots[slot]} both fire at {slot}"
            slots[slot] = key


@pytest.mark.parametrize("skill", FIVE)
def test_each_skill_says_what_to_do_when_nobody_is_there(skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "## When nobody is there" in text
    section = text.split("## When nobody is there", 1)[1]
    assert "meta.unattended" in section
    assert "nobody can answer" in section


def test_the_scheduled_client_report_never_sends():
    section = (SKILLS / "five-fifteen" / "SKILL.md").read_text(
        encoding="utf-8").split("## When nobody is there", 1)[1]
    assert "Nothing is sent, on any path." in section


def test_the_deliverables_are_the_files_those_skills_write(monkeypatch, tmp_path):
    for skill, accessor in (("relationship-radar", "relationship_radar_path"),
                            ("voice-calibration", "voice_snapshot_path"),
                            ("voice-generator", "tone_profile_path")):
        monkeypatch.setattr(config_loader, accessor, lambda a=accessor: tmp_path / a)
        assert job_registry.by_key(skill).deliverable() == tmp_path / accessor
    for skill in ("calendar-stub-check", "five-fifteen"):
        assert job_registry.by_key(skill).deliverable is None


# ── The tone profile that is about to be replaced ────────────────────────────

def test_the_previous_tone_profile_is_kept_beside_the_new_one(monkeypatch, tmp_path):
    profile = tmp_path / "tone-profile.md"
    monkeypatch.setattr(voice_generator, "tone_profile_path", lambda: profile)
    assert voice_generator.keep_previous_profile() == ""      # nothing to keep
    profile.write_text("the one the user checked", encoding="utf-8")
    kept = voice_generator.keep_previous_profile()
    assert Path(kept).name == "tone-profile.previous.md"
    assert Path(kept).read_text(encoding="utf-8") == "the one the user checked"
    assert profile.read_text(encoding="utf-8") == "the one the user checked"


def test_keeping_the_previous_profile_never_stops_a_run(monkeypatch):
    def boom():
        raise OSError("disk")
    monkeypatch.setattr(voice_generator, "tone_profile_path", boom)
    assert voice_generator.keep_previous_profile() == ""
