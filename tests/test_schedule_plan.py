"""The schedule a person reads is the schedule the computer runs, or this fails.

`app/schedule_plan.py` shows every job that can run by itself and is the one
place a change to that table is checked. Each test is one promise: the table
agrees with `job_registry` on every row, a change a model wrote from a
person's words is checked by code, and what cannot be placed is refused by
name while everything else still applies.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import config_loader
import job_registry
import schedule_plan as sp

ROOT = Path(__file__).resolve().parent.parent
EM, EN = chr(0x2014), chr(0x2013)


@pytest.fixture
def tpl() -> dict:
    return json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))


def _on(cfg: dict) -> set:
    return {r["key"] for r in sp.table(cfg) if r["on"] and not r["fixed"]}


def _row(cfg: dict, key: str) -> dict:
    return next(r for r in sp.table(cfg) if r["key"] == key)


# ── the table is the registry ────────────────────────────────────────────────

def test_every_registry_row_has_a_name_and_nothing_else_does():
    keys = [j.key for j in job_registry.jobs()]
    assert len(keys) == 18 and len(set(keys)) == 18
    assert set(sp.NAMES) == set(keys)
    assert set(sp.SETUP_ON) <= set(keys) and not set(sp.SETUP_ON) & set(sp.FIXED)
    assert set(sp.ALIASES.values()) <= set(keys)


def test_a_job_added_to_the_registry_without_a_name_stops_the_table(monkeypatch, tpl):
    real = job_registry.jobs()
    extra = copy.copy(real[0])
    object.__setattr__(extra, "key", "brand-new-job")
    monkeypatch.setattr(job_registry, "jobs", lambda: real + [extra])
    with pytest.raises(sp.PlanError, match="brand-new-job"):
        sp.table(tpl)


@pytest.mark.parametrize("build", ["template", "defaults", "changed"])
def test_on_and_time_agree_with_what_the_scheduler_would_install(monkeypatch, tpl, build):
    """The table reads a config dict by its own map of where each switch
    lives. The scheduler reads the same config through `job_registry`. For
    every row the two must agree on whether it is on and when it runs, or a
    person is shown one schedule and given another."""
    cfg = tpl
    if build != "template":
        cfg = sp.setup_defaults(tpl)
    if build == "changed":
        cfg, done, refused, _ = sp.apply_changes(cfg, [
            {"job": "coffee", "time": "6:30", "days": ["monday", "wednesday"]},
            {"job": "finance", "on": True, "days": "thursday", "time": "4pm"},
            {"job": "contacts", "on": True, "time": "05:45"},
            {"job": "stub check", "on": True, "days": "weekdays", "time": "5:10"},
            {"job": "radar", "days": ["tuesday"], "time": "07:05"},
            {"job": "tea", "on": False}, {"job": "notes", "on": True},
            {"job": "workbench", "on": False}, {"job": "prep", "mode": "each"}])
        assert not refused and len(done) == 9
    monkeypatch.setattr(config_loader, "cfg", lambda: cfg)
    rows = {r["key"]: r for r in sp.table(cfg)}
    checked = timers = 0
    for job in job_registry.jobs():
        row = rows[job.key]
        assert row["on"] == job.is_enabled(), (build, job.key)
        cad = job.resolved_cadence()
        if cad.kind == "clock":
            clock = sp._clock(f"{cad.hour:02d}:{cad.minute:02d}")
            assert f"at {clock}" in row["when"], (build, job.key, row["when"])
            assert sp._days_words(cad.days) in row["when"], (build, job.key, row["when"])
            checked += 1
        elif cad.kind == "interval" and cad.interval_min and job.key in sp.FIXED:
            n = cad.interval_min
            assert row["when"] == ("every hour" if n == 60 else f"every {n} minutes"), \
                (build, job.key, row["when"])
            timers += 1
    assert checked == 13, "every clock job was compared"
    assert timers == 2, "meeting filing (hourly) and the watcher were compared"


def test_the_template_ships_with_nothing_on_and_the_defaults_turn_on_twelve(tpl):
    assert _on(tpl) == set()
    assert not sp.any_on(sp.table(tpl))
    d = sp.setup_defaults(tpl)
    assert _on(d) == set(sp.SETUP_ON)
    text = sp.render(sp.table(d))
    assert "ON (13)" in text and "OFF (5)" in text
    assert _row(d, "prep-email")["when"] == "one email each morning at 6:15 AM"
    assert json.dumps(tpl) == json.dumps(
        json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))), \
        "the config it was handed is not changed"


def test_fixed_rows_are_listed_as_on_only_when_something_else_is(tpl):
    off = sp.render(sp.table(tpl))
    assert "ON (0)" in off and "OFF (18)" in off
    one, done, _, _ = sp.apply_changes(tpl, [{"job": "Morning Coffee", "on": True}])
    text = sp.render(sp.table(one))
    assert "ON (5)" in text, "the one briefing and the four that come with any schedule"


def test_nothing_turns_every_switch_off(tpl):
    n = sp.nothing(sp.setup_defaults(tpl))
    assert _on(n) == set()
    assert n["digest"]["enabled"] is False
    assert n["workbench"]["autostart"] is False


def test_the_rendered_table_has_no_dash_and_marks_what_changed(tpl):
    d = sp.setup_defaults(tpl)
    text = sp.render(sp.table(d), ("digest-week",))
    assert EM not in text and EN not in text
    marked = [ln for ln in text.splitlines() if ln.endswith("(changed)")]
    assert len(marked) == 1 and marked[0].strip().startswith("Week ")
    for name, _what in sp.NAMES.values():
        assert name in text


# ── a change is checked by code ──────────────────────────────────────────────

@pytest.mark.parametrize("said, key", [
    ("coffee", "digest-morning-coffee"), ("Morning Coffee", "digest-morning-coffee"),
    ("morning-coffee", "digest-morning-coffee"), ("the week retro", "digest-week-retro"),
    ("Week", "digest-week"), ("meeting prep", "prep-email"), ("prep-email", "prep-email"),
    ("my finance brief", "finance-email"), ("Relationship radar", "relationship-radar"),
    ("tone profile refresh", "voice-generator"), ("workbench", "workbench"),
    ("Watcher between briefings", "note"), ("client reports", "five-fifteen"),
    ("the morning coffee briefing", "digest-morning-coffee"),
    ("week retro email", "digest-week-retro"), ("my weekly finance brief", "finance-email"),
])
def test_a_persons_word_for_a_job_finds_the_job(said, key):
    assert sp.resolve_job(said) == key


@pytest.mark.parametrize("said", ["the bagel poller", "", "briefing", "check"])
def test_a_word_that_names_no_job_or_more_than_one_is_refused(said):
    with pytest.raises(sp.PlanError):
        sp.resolve_job(said)


@pytest.mark.parametrize("said, want", [
    ("6:30", "06:30"), ("06:30", "06:30"), ("6:30 pm", "18:30"), ("6pm", "18:00"),
    ("12am", "00:00"), ("12 PM", "12:00"), ("noon", "12:00"), ("13", "13:00"),
    ("7.15am", "07:15"), ("23:59", "23:59"),
])
def test_times_a_person_writes_are_read(said, want):
    assert sp.parse_time(said) == want


@pytest.mark.parametrize("said", ["25:00", "13pm", "6:75", "soon", "", None, "0am"])
def test_a_time_that_is_not_a_time_is_refused(said):
    with pytest.raises(sp.PlanError):
        sp.parse_time(said)


@pytest.mark.parametrize("said, want", [
    ("weekdays", list(sp.WEEKDAYS)), ("every day", list(sp.WEEK)),
    (["tues", "Thursday"], ["tuesday", "thursday"]),
    ("monday and friday", ["monday", "friday"]),
    ("Mondays", ["monday"]), (["weekends"], ["saturday", "sunday"]),
    (["friday", "monday"], ["monday", "friday"]),
])
def test_days_a_person_writes_are_read_in_week_order(said, want):
    assert sp.parse_days(said) == want


@pytest.mark.parametrize("said", ["someday", [], ["t"], "the 5th"])
def test_a_day_that_is_not_a_day_is_refused(said):
    with pytest.raises(sp.PlanError):
        sp.parse_days(said)


def test_what_cannot_be_placed_is_refused_by_name_and_the_rest_still_applies(tpl):
    d = sp.setup_defaults(tpl)
    new, done, refused, touched = sp.apply_changes(d, [
        {"job": "coffee", "time": "6:30"},
        {"job": "bagel poller", "on": True},
        {"job": "radar", "time": "25:00"},
        {"job": "meeting notes filing", "on": False},
        {"job": "workbench", "time": "9"},
        {"job": "finance", "days": ["monday", "friday"]},
        {"job": "tea", "colour": "blue"},
        {"job": "week"},
        "not a change at all",
    ])
    assert done == ["Morning Coffee: on, weekdays at 6:30 AM."]
    assert touched == ["digest-morning-coffee"]
    assert refused == [
        'I could not tell which job "bagel poller" is.',
        '"25:00" is not a time I can read.',
        "Meeting notes filing comes with the schedule and has no setting of its own.",
        "Workbench has no time to set, it can only be on or off.",
        "Finance brief runs one day a week, so it needs exactly one day.",
        'Afternoon Tea has no setting called "colour".',
        "The change for Week did not say what to change.",
        "One change was not in a form I can read.",
    ]
    # A refused change leaves its job exactly as it was.
    for key in ("relationship-radar", "workbench", "finance-email", "digest-afternoon-tea"):
        assert _row(new, key) == _row(d, key), key
    assert json.dumps(d) == json.dumps(sp.setup_defaults(tpl)), "the input is not changed"


def test_a_change_that_fails_halfway_changes_nothing_for_that_job(tpl):
    d = sp.setup_defaults(tpl)
    new, done, refused, _ = sp.apply_changes(
        d, [{"job": "coffee", "days": ["saturday"], "time": "nonsense"}])
    assert not done and len(refused) == 1
    assert new["digest"]["briefings"]["morning-coffee"] == d["digest"]["briefings"]["morning-coffee"]
    # Meeting prep sets its fields one after another, so a bad later field
    # would leave an earlier one written unless the whole change is undone.
    assert d["meeting_prep"]["mode"] == "daily"
    new, done, refused, _ = sp.apply_changes(
        d, [{"job": "prep", "mode": "each", "lead_minutes": "soon"}])
    assert not done and refused == ['"soon" is not a number of minutes.']
    assert new["meeting_prep"] == d["meeting_prep"]


def test_turning_one_briefing_on_under_a_master_that_is_off_turns_on_only_that_one(tpl):
    """The template has every briefing marked enabled under a master switch
    that is off. Flipping the master for one briefing must not bring the
    other three with it."""
    n = sp.nothing(sp.setup_defaults(tpl))
    assert tpl["digest"]["briefings"]["week"]["enabled"] is True
    fresh, done, refused, _ = sp.apply_changes(tpl, [{"job": "coffee", "on": True}])
    assert not refused
    assert _on(fresh) == {"digest-morning-coffee"}
    again, _, _, _ = sp.apply_changes(n, [{"job": "retro", "time": "16:00"}])
    assert _on(again) == {"digest-week-retro"}
    assert _row(again, "digest-week-retro")["when"] == "Fridays at 4:00 PM"


def test_meeting_prep_has_two_ways_to_arrive(tpl):
    d = sp.setup_defaults(tpl)
    each, done, refused, _ = sp.apply_changes(d, [{"job": "prep", "mode": "before each call"}])
    assert done == ["Meeting prep: on, 60 minutes before each call."] and not refused
    lead, done, _, _ = sp.apply_changes(d, [{"job": "prep", "lead_minutes": 30}])
    assert lead["meeting_prep"]["mode"] == "each"
    assert done == ["Meeting prep: on, 30 minutes before each call."]
    daily, done, _, _ = sp.apply_changes(each, [{"job": "prep", "time": "7am"}])
    assert daily["meeting_prep"]["mode"] == "daily"
    assert done == ["Meeting prep: on, one email each morning at 7:00 AM."]
    _, done, refused, _ = sp.apply_changes(d, [{"job": "prep", "mode": "weekly"},
                                               {"job": "prep", "days": "monday"},
                                               {"job": "prep", "lead_minutes": 2},
                                               {"job": "radar", "mode": "daily"}])
    assert not done and len(refused) == 4
    off, done, _, _ = sp.apply_changes(d, [{"job": "prep", "on": False}])
    assert done == ["Meeting prep: off."] and off["meeting_prep"]["mode"] == "daily"


# ── writing ──────────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch, tpl):
    root = tmp_path / "vault" / "van-gogh"
    root.mkdir(parents=True)
    cfg = sp.setup_defaults(tpl)
    (root / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_loader, "van_gogh_root", lambda: root)
    monkeypatch.delenv("VAN_GOGH_STATE_DIR", raising=False)
    calls = []

    class Done:
        returncode = 0
        stdout = "scheduled\n"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        return Done()

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    return {"root": root, "calls": calls, "cfg": cfg}


def _cli(monkeypatch, capsys, argv, stdin=""):
    import io
    monkeypatch.setattr(sp.sys, "stdin", io.StringIO(stdin))
    rc = sp.main(argv)
    return rc, json.loads(capsys.readouterr().out)


def test_a_dry_run_writes_nothing_and_starts_nothing(vault, monkeypatch, capsys):
    before = (vault["root"] / "config.json").read_bytes()
    rc, out = _cli(monkeypatch, capsys, ["change", "--dry-run"],
                   json.dumps({"changes": [{"job": "coffee", "time": "6:30"}]}))
    assert rc == 0 and out["ok"] and out["written"] is False
    assert out["done"] == ["Morning Coffee: on, weekdays at 6:30 AM."]
    assert "(changed)" in out["table"]
    assert (vault["root"] / "config.json").read_bytes() == before
    assert not (vault["root"] / "config.json.bak").exists()
    assert vault["calls"] == []


def test_a_change_keeps_a_copy_writes_and_resyncs_the_scheduler_once(vault, monkeypatch, capsys):
    before = (vault["root"] / "config.json").read_bytes()
    rc, out = _cli(monkeypatch, capsys, ["change"],
                   json.dumps({"changes": [{"job": "coffee", "time": "6:30"},
                                           {"job": "bagels", "on": True}]}))
    assert rc == 0 and out["written"] is True and out["scheduler"] == "ok"
    assert out["refused"] == ['I could not tell which job "bagels" is.']
    assert (vault["root"] / "config.json.bak").read_bytes() == before
    cfg = json.loads((vault["root"] / "config.json").read_text(encoding="utf-8"))
    assert cfg["digest"]["briefings"]["morning-coffee"]["time"] == "06:30"
    assert len(vault["calls"]) == 1
    assert vault["calls"][0][-2:] == [str(sp.APP_DIR / "scheduler_setup.py"), "install"]
    assert not list(vault["root"].glob("*.tmp"))


def test_a_change_with_nothing_placeable_writes_nothing(vault, monkeypatch, capsys):
    before = (vault["root"] / "config.json").read_bytes()
    rc, out = _cli(monkeypatch, capsys, ["change"],
                   json.dumps({"changes": [{"job": "bagels", "on": True}]}))
    assert rc == 1 and out["ok"] is False and out["written"] is False
    assert (vault["root"] / "config.json").read_bytes() == before
    assert vault["calls"] == []
    rc, out = _cli(monkeypatch, capsys, ["change"], "this is not json")
    assert rc == 1 and "not readable" in out["error"]


def test_a_scheduler_that_reports_a_problem_is_said_and_the_settings_stay_written(
        vault, monkeypatch, capsys):
    class Bad:
        returncode = 1
        stdout = ""
        stderr = "launchctl: boom"

    monkeypatch.setattr(sp.subprocess, "run", lambda cmd, **kw: Bad())
    rc, out = _cli(monkeypatch, capsys, ["change"],
                   json.dumps({"changes": [{"job": "tea", "on": False}]}))
    assert out["written"] is True
    assert out["scheduler"] != "ok" and "boom" not in json.dumps(out)
    cfg = json.loads((vault["root"] / "config.json").read_text(encoding="utf-8"))
    assert cfg["digest"]["briefings"]["afternoon-tea"]["enabled"] is False


def test_show_prints_the_table_as_it_stands(vault, monkeypatch, capsys):
    rc, out = _cli(monkeypatch, capsys, ["show"])
    assert rc == 0 and len(out["rows"]) == 18
    assert "ON (13)" in out["table"]
    assert vault["calls"] == []


def test_a_scratch_state_folder_never_resyncs_the_real_scheduler(vault, monkeypatch, capsys):
    """The jobs live in the real scheduler whatever the state folder is. A
    harness run that resynced them would point every job on the machine at a
    scratch vault."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(vault["root"].parent))
    rc, out = _cli(monkeypatch, capsys, ["change"],
                   json.dumps({"changes": [{"job": "coffee", "time": "6:30"}]}))
    assert out["written"] is True and out["scheduler"] == sp.SCHEDULER_SKIPPED
    assert vault["calls"] == []
    monkeypatch.setenv("VAN_GOGH_SCHEDULER_SYNC", "1")
    rc, out = _cli(monkeypatch, capsys, ["change"],
                   json.dumps({"changes": [{"job": "coffee", "time": "6:45"}]}))
    assert out["scheduler"] == "ok" and len(vault["calls"]) == 1


def test_the_changes_can_arrive_on_the_command_line(vault, monkeypatch, capsys):
    """A here-document on a command is asked about by the permission check;
    a plain argument is not. A schedule holds nothing secret."""
    before = (vault["root"] / "config.json").read_bytes()
    rc, out = _cli(monkeypatch, capsys, [
        "change", "--dry-run", "--changes",
        json.dumps({"changes": [{"job": "Week", "days": ["tuesday"]}]})])
    assert rc == 0 and out["done"] == ["Week: on, Tuesdays at 6:30 AM."]
    assert (vault["root"] / "config.json").read_bytes() == before
    rc, out = _cli(monkeypatch, capsys, ["change", "--dry-run", "--changes", "[1, 2]"])
    assert rc == 1 and out["refused"]
