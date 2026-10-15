"""Vincent knows every skill, every job and what he was told, or this fails.

`app/vincent.py` is the part of the chief of staff that is code: the catalog,
the schedule, memory, the hand-off line and the headless turn. Each test here
is one promise that module makes, checked against planted files, a planted
ledger and a planted scheduler, with no clock, no launchd and no model.

Nothing in this file touches the developer's own `~/.claude`, vault or state:
`Path.home`, the ledger path, the memory page and the session file are all
pointed at the test's own temp directory first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import claude_cli
import config_loader
import job_registry
import job_watch
import memory_sync
import run_ledger
import scheduler_setup
import skill_run
import vincent

ROOT = Path(__file__).resolve().parent.parent
EM, EN = chr(0x2014), chr(0x2013)

# A Wednesday morning, after both daily ingests were due. Every stamp in this
# file is written relative to it, so nothing here reads the wall clock.
NOW = datetime(2026, 10, 7, 9, 0)
TODAY = NOW.date().isoformat()


@pytest.fixture(autouse=True)
def sealed(tmp_path, monkeypatch):
    """A home, a ledger, a memory page and a session file of the test's own."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    ledger = tmp_path / "logs" / "runs.jsonl"
    ledger.parent.mkdir()
    monkeypatch.setattr(run_ledger, "runs_path", lambda: ledger)
    memory_page = tmp_path / "vault" / "van-gogh" / "projects" / "memory.md"
    memory_page.parent.mkdir(parents=True)
    monkeypatch.setattr(config_loader, "vangogh_memory_path", lambda: memory_page)
    monkeypatch.setattr(config_loader, "follow_ups_memory_path", lambda: memory_page)
    monkeypatch.setattr(vincent, "_sessions_path",
                        lambda: tmp_path / "vincent-sessions.json")
    monkeypatch.setattr(scheduler_setup, "job_states", lambda platform: _loaded())
    monkeypatch.setattr(job_watch, "_read_state", lambda: {})
    # The check-off is graded by the record it leaves, like any job that
    # writes a file: give it one from this morning.
    import done_judge
    record = ledger.parent / "done_judge_latest.json"
    record.write_text("{}", encoding="utf-8")
    stamp = NOW.replace(hour=5, minute=11).timestamp()
    os.utime(record, (stamp, stamp))
    monkeypatch.setattr(done_judge, "latest_path", lambda: record)
    # A sign-in check is a real network call. No test here may make one.
    monkeypatch.setattr(job_watch, "_auth_recovered", lambda row, srec: False)
    return {"home": home, "ledger": ledger, "memory": memory_page,
            "vault": tmp_path / "vault"}


def _loaded() -> list:
    """What the scheduler would say if every job were installed and loaded."""
    return [{"label": j.mac_label, "kind": j.state_name, "name": j.key,
             "state": "loaded", "path": ""} for j in job_registry.jobs()]


def _run(job: str, hhmm: str, rc: int = 0, detail: str = "", day: str = TODAY) -> dict:
    return {"job": job, "started": f"{day}T{hhmm}:00",
            "finished": f"{day}T{hhmm}:30", "rc": rc, "detail": detail}


def _skill(root: Path, name: str, description: str) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >-\n  {description}\n---\n\n# {name}\n",
        encoding="utf-8")
    return d


def _by_invoke(rows: list) -> dict:
    return {r["invoke"]: r for r in rows if r["invoke"]}


# ── Frontmatter ──────────────────────────────────────────────────────────────

def test_a_folded_description_with_a_colon_in_it_is_read_whole():
    """The shape that breaks a naive `key: value` split, and the reason this
    is parsed by hand: a long description that itself contains `: `."""
    text = ("---\nname: planner\ndescription: >-\n"
            "  Plans the week: reads the calendar, then\n"
            "  writes one page. Use when asked: plan my week.\n"
            "allowed-tools:\n  - Bash(echo *)\n---\n\n# body\ndescription: not this\n")
    fm = vincent.frontmatter(text)
    assert fm["name"] == "planner"
    assert fm["description"] == ("Plans the week: reads the calendar, then "
                                 "writes one page. Use when asked: plan my week.")
    assert "allowed-tools" not in fm, "a list is skipped, never half read"


@pytest.mark.parametrize("raw, want", [
    ('description: "Quoted: with a colon"', "Quoted: with a colon"),
    ("description: 'It''s single quoted'", "It's single quoted"),
    ("description: plain words here", "plain words here"),
    ("description: plain words\n  carried over a line", "plain words carried over a line"),
    ("description: |\n  line one\n  line two", "line one\nline two"),
    ("description: >\n  one\n  two\n\n  three", "one two\nthree"),
])
def test_every_shape_a_description_is_written_in(raw, want):
    assert vincent.frontmatter(f"---\nname: x\n{raw}\n---\n")["description"] == want


def test_a_file_with_no_frontmatter_answers_nothing():
    assert vincent.frontmatter("# Just a page\n") == {}
    assert vincent.frontmatter("---\nname: never closed\n") == {}


# ── P1 to P3: the catalog ────────────────────────────────────────────────────

def _plant_world(home: Path, tmp_path: Path) -> None:
    claude = home / ".claude"
    _skill(claude, "ledger-sweep", "Sweeps the ledger: finds stale rows.")
    (claude / "agents").mkdir(parents=True)
    (claude / "agents" / "scribe.md").write_text(
        "---\nname: scribe\ndescription: Writes things down.\n---\n\n# Scribe\n",
        encoding="utf-8")
    (claude / "commands").mkdir()
    (claude / "commands" / "old-style.md").write_text(
        "---\ndescription: An older command.\n---\nDo it.\n", encoding="utf-8")
    other = tmp_path / "cache" / "notes-plugin"
    _skill(other, "tidy", "Tidies a folder of notes.")
    (other / "agents").mkdir()
    (other / "agents" / "librarian.md").write_text(
        "---\nname: librarian\ndescription: Files notes.\n---\n", encoding="utf-8")
    local_only = tmp_path / "cache" / "local-plugin"
    _skill(local_only, "hidden", "Installed for one project only.")
    own_copy = tmp_path / "cache" / "van-gogh-older"
    _skill(own_copy, "stale-copy", "An older install of this same plugin.")
    plugins = {"version": 2, "plugins": {
        "notes@market": [{"scope": "user", "installPath": str(other)}],
        "gone@market": [{"scope": "user",
                         "installPath": str(tmp_path / "cache" / "deleted")}],
        "local@market": [{"scope": "local", "installPath": str(local_only)}],
        "van-gogh@van-gogh": [{"scope": "user", "installPath": str(own_copy)}],
    }}
    (claude / "plugins").mkdir()
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps(plugins), encoding="utf-8")


def test_the_catalog_holds_one_row_for_each_kind_it_can_find(sealed, tmp_path):
    _plant_world(sealed["home"], tmp_path)
    rows = vincent.catalog()
    got = _by_invoke(rows)

    shipped = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
    assert shipped, "found no shipped skills, so this would pass on nothing"
    for name in shipped:
        assert f"/van-gogh:{name}" in got, name
    ours = [r for r in rows if r["source"] == "van-gogh" and r["kind"] == "skill"]
    assert len(ours) == len(shipped)

    agents = sorted(p.stem for p in (ROOT / "agents").glob("*.md"))
    assert agents and all(a in got for a in agents), agents
    assert got["vincent"]["kind"] == "agent"

    assert got["/ledger-sweep"]["source"] == "personal"
    assert got["/ledger-sweep"]["description"] == "Sweeps the ledger: finds stale rows."
    assert got["scribe"] == {"kind": "agent", "name": "scribe", "invoke": "scribe",
                             "source": "personal",
                             "description": "Writes things down.",
                             "needs_confirm": False}
    assert got["/old-style"]["description"] == "An older command."
    assert got["/notes:tidy"]["source"] == "notes@market"
    assert got["/notes:tidy"]["description"] == "Tidies a folder of notes."
    assert got["notes:librarian"]["kind"] == "agent"

    for row in rows:
        assert {"kind", "name", "invoke", "source", "description",
                "needs_confirm"} <= set(row), row


def test_a_plugin_whose_folder_is_gone_is_reported_missing_not_empty(sealed, tmp_path):
    _plant_world(sealed["home"], tmp_path)
    rows = vincent.catalog()
    gone = [r for r in rows if r["name"] == "gone"]
    assert gone == [{"kind": "plugin", "name": "gone", "invoke": "",
                     "source": "gone@market", "description": "", "missing": True,
                     "needs_confirm": False}]
    line = vincent._catalog_line(gone[0], 200)
    assert "missing" in line and "gone" in line


def test_only_what_is_installed_for_the_user_and_is_not_this_plugin(sealed, tmp_path):
    _plant_world(sealed["home"], tmp_path)
    invokes = set(_by_invoke(vincent.catalog()))
    assert "/local:hidden" not in invokes, "a project-scoped install is not the user's"
    assert not any("stale-copy" in i for i in invokes), "its own older copy is not a second plugin"


def test_a_shipped_description_is_the_one_in_its_file():
    got = _by_invoke(vincent.catalog())
    text = (ROOT / "skills" / "suggest" / "SKILL.md").read_text(encoding="utf-8")
    front = text[4:text.index("\n---", 4)]
    block = front.split("description: >-\n", 1)[1].split("\nallowed-tools:", 1)[0]
    want = " ".join(line.strip() for line in block.splitlines())
    assert got["/van-gogh:suggest"]["description"] == want


def test_two_skills_with_one_name_are_both_kept(sealed):
    _skill(sealed["home"] / ".claude", "meeting-prep", "My own prep, shorter.")
    rows = [r for r in vincent.catalog() if r["name"] == "meeting-prep"]
    assert sorted(r["invoke"] for r in rows) == ["/meeting-prep",
                                                 "/van-gogh:meeting-prep"]
    assert {r["source"] for r in rows} == {"personal", "van-gogh"}


def test_a_skill_added_a_moment_ago_is_in_the_next_call(sealed):
    before = set(_by_invoke(vincent.catalog()))
    assert "/just-added" not in before
    _skill(sealed["home"] / ".claude", "just-added", "Did not exist a call ago.")
    after = _by_invoke(vincent.catalog())
    assert after["/just-added"]["description"] == "Did not exist a call ago."
    assert set(after) - before == {"/just-added"}


def test_a_scheduled_skill_carries_its_cadence_words():
    got = _by_invoke(vincent.catalog())
    assert re.match(r"daily \d\d:\d\d, (on|off)$", got["/van-gogh:ingest-workspace"]["cadence"])
    assert re.match(r"every 60 min, (on|off)$", got["/van-gogh:meeting-ingest"]["cadence"])
    assert got["/van-gogh:release-notes"]["cadence"] == ""


def test_every_job_maps_to_a_skill_or_says_why_not():
    """A job nobody can ask for by name is a job Vincent cannot explain."""
    shipped = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    keys = [j.key for j in job_registry.jobs()]
    assert keys
    for key in keys:
        skill = vincent.skill_of_job(key)
        if key in vincent.JOBS_WITHOUT_A_SKILL:
            assert skill == ""
        else:
            assert skill in shipped, f"{key} maps to {skill!r}, which is not a skill"


# ── P4: the schedule ─────────────────────────────────────────────────────────

ROW_KEYS = {"key", "title", "cadence", "on", "scheduler", "skill", "last_run",
            "last_run_when", "last_rc", "status", "detail", "hold",
            "needs_reader", "watcher_restarts_it"}


def test_one_row_for_every_job_in_the_registry():
    runs = [_run("scheduled.meeting-ingest", "08:31"),
            _run("scheduled.ingest-workspace", "04:51"),
            _run("judge.done", "05:11"),
            _run("watch.tick", "08:45")]
    out = vincent.schedule(NOW, runs=runs, job_rows=_loaded())
    assert out["read_ok"] is True
    keys = [j.key for j in job_registry.jobs()]
    assert len(keys) >= 17
    assert [r["key"] for r in out["rows"]] == keys
    for row in out["rows"]:
        assert set(row) == ROW_KEYS
        assert row["scheduler"] == "loaded"
    by = {r["key"]: r for r in out["rows"]}
    ingest = by["meeting-ingest"]
    assert ingest["on"] is True and ingest["status"] == "ok"
    assert ingest["last_run"] == f"{TODAY}T08:31:00" and ingest["last_rc"] == 0
    assert ingest["title"] == job_registry.by_key("meeting-ingest").title
    assert ingest["cadence"] == job_registry.by_key("meeting-ingest").resolved_cadence().describe()
    off = [r for r in out["rows"] if not r["on"]]
    assert off and all(r["status"] == "off" for r in off)
    assert out["needs_attention"] == []


def test_a_time_is_rendered_by_code_in_both_zones(monkeypatch):
    monkeypatch.setattr(config_loader, "user_tz",
                        lambda: ZoneInfo("America/Los_Angeles"))
    monkeypatch.setattr(config_loader, "user_secondary_tz",
                        lambda: ZoneInfo("America/New_York"))
    stamp = datetime(2026, 10, 10, 4, 40, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert vincent.when(stamp) == "Sat Oct 10, 4:40 AM PT / 7:40 AM ET"
    winter = datetime(2026, 1, 5, 23, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert vincent.when(winter) == "Mon Jan 5, 11:05 PM PT / 2:05 AM ET"
    monkeypatch.setattr(config_loader, "user_secondary_tz", lambda: None)
    assert vincent.when(stamp) == "Sat Oct 10, 4:40 AM PT"
    monkeypatch.setattr(config_loader, "user_tz", lambda: ZoneInfo("Europe/London"))
    assert vincent.when(stamp).endswith("12:40 PM BST"), "no US shorthand elsewhere"
    assert vincent.when("not a stamp") == ""
    assert vincent.when("") == ""


def test_a_ledger_stamp_gets_a_rendered_time():
    out = vincent.schedule(NOW, runs=[_run("scheduled.meeting-ingest", "08:31")],
                           job_rows=_loaded())
    row = next(r for r in out["rows"] if r["key"] == "meeting-ingest")
    assert re.match(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d\d [AP]M \S+",
                    row["last_run_when"]), row["last_run_when"]


def test_the_watcher_itself_is_graded_by_its_own_last_tick():
    fresh = vincent.schedule(NOW, runs=[_run("watch.tick", "08:45")], job_rows=_loaded())
    row = next(r for r in fresh["rows"] if r["key"] == "job-watch")
    assert row["status"] == "ok" and not row["needs_reader"]

    quiet = vincent.schedule(NOW, runs=[_run("watch.tick", "04:00")], job_rows=_loaded())
    row = next(r for r in quiet["rows"] if r["key"] == "job-watch")
    assert row["status"] == "stopped" and row["needs_reader"]
    assert "job-watch" in [a["key"] for a in quiet["needs_attention"]]


# ── P5: reading never acts ───────────────────────────────────────────────────

def _forbid(monkeypatch) -> list:
    """Make every way the watcher can change the world raise instead."""
    called = []

    def boom(name):
        def inner(*a, **k):
            called.append(name)
            raise AssertionError(f"{name} was reached")
        return inner

    for name in ("kick", "_write_state", "record_tick", "_escalate",
                 "_alert_on_stuck", "_prune_state"):
        monkeypatch.setattr(job_watch, name, boom(name))
    monkeypatch.setattr(job_watch.subprocess, "run", boom("subprocess.run"))
    monkeypatch.setattr(job_watch.subprocess, "Popen", boom("subprocess.Popen"))
    return called


def test_reading_the_schedule_starts_nothing_and_writes_nothing(sealed, monkeypatch):
    called = _forbid(monkeypatch)
    # Due at 04:50, never ran: exactly the row the watcher would restart.
    out = vincent.schedule(NOW, runs=[_run("watch.tick", "08:45")], job_rows=_loaded())
    assert out["read_ok"] is True, out["error"]
    row = next(r for r in out["rows"] if r["key"] == "ingest-workspace")
    assert row["status"] == "missing"
    assert row["watcher_restarts_it"] is True, "the watcher would re-run it"
    assert called == [], "and reading about that reached none of its writers"
    assert not sealed["ledger"].exists(), "no run row was written"


def test_the_same_fixture_makes_the_watcher_itself_act(monkeypatch):
    """The mutation proof: the patches above are not inert. The tick, handed
    the same due and missing job, reaches the restart and is stopped there."""
    monkeypatch.setattr(job_watch, "evaluate", lambda **kw: _orig_evaluate(
        now=kw.get("now"), state=kw.get("state"),
        runs=[_run("watch.tick", "08:45")], job_rows=_loaded()))
    called = _forbid(monkeypatch)
    with pytest.raises(AssertionError):
        job_watch.tick(now=NOW)
    assert called[:1] == ["kick"], called


_orig_evaluate = job_watch.evaluate


def test_the_state_handed_to_the_hold_policy_is_a_copy(monkeypatch):
    """`actionable` writes holds into the state it is given. Vincent must hand
    it a copy, or reading the schedule would quietly edit the watcher's memory."""
    real = {"slots": {}}
    monkeypatch.setattr(job_watch, "_read_state", lambda: real)
    runs = [_run("scheduled.meeting-ingest", "08:31", rc=1,
                 detail="You've hit your usage limit. It resets at 3pm.")]
    out = vincent.schedule(NOW, runs=runs, job_rows=_loaded())
    row = next(r for r in out["rows"] if r["key"] == "meeting-ingest")
    assert row["status"] == "failed"
    assert "usage limit" in row["hold"], row
    assert real == {"slots": {}}, "the watcher's own state was written to"


# ── P6: attention first ──────────────────────────────────────────────────────

def test_a_failed_job_is_what_the_situation_leads_with(sealed):
    sealed["ledger"].write_text("\n".join(json.dumps(r) for r in [
        _run("watch.tick", "08:45"),
        _run("scheduled.ingest-workspace", "04:51"),
        _run("judge.done", "05:11"),
        _run("scheduled.meeting-ingest", "08:31", rc=1,
             detail="RuntimeError: the notetaker key was rejected"),
    ]) + "\n", encoding="utf-8")
    sit = vincent.situation(NOW)
    assert sit["schedule_read_ok"] is True
    assert [a["key"] for a in sit["needs_attention"]] == ["meeting-ingest"]
    sentence = sit["needs_attention"][0]["sentence"]
    assert sentence.startswith("The filing of your meeting notes failed")
    assert "the notetaker key was rejected" in sentence
    assert sit["opening"].startswith("The filing of your meeting notes failed")
    assert set(sit) >= {"catalog", "schedule", "memory", "needs_attention"}


def test_what_needs_the_reader_is_said_before_what_the_system_is_handling(sealed):
    sealed["ledger"].write_text("\n".join(json.dumps(r) for r in [
        _run("watch.tick", "08:45"),
        _run("judge.done", "05:11"),
        _run("scheduled.meeting-ingest", "08:31", rc=1,
             detail="RuntimeError: the notetaker key was rejected"),
        _run("scheduled.ingest-workspace", "04:51", rc=1,
             detail="invalid_grant: Token has been expired or revoked."),
    ]) + "\n", encoding="utf-8")
    sit = vincent.situation(NOW)
    assert len(sit["needs_attention"]) == 2
    first = sit["opening"].split(". ")[0]
    assert "sync of your project notes" in first, sit["opening"]
    assert "needs you to sign in" in sit["opening"]


def test_a_quiet_day_is_an_empty_list_and_says_everything_ran(sealed):
    sealed["ledger"].write_text("\n".join(json.dumps(r) for r in [
        _run("watch.tick", "08:45"),
        _run("scheduled.meeting-ingest", "08:31"),
        _run("scheduled.ingest-workspace", "04:51"),
        _run("judge.done", "05:11"),
    ]) + "\n", encoding="utf-8")
    sit = vincent.situation(NOW)
    assert sit["needs_attention"] == []
    assert sit["schedule_read_ok"] is True and sit["schedule_error"] == ""
    assert sit["opening"].startswith("Everything scheduled has run: 4 of the 4 jobs")


def test_a_ledger_that_cannot_be_read_is_null_and_named_never_all_clear(sealed):
    sealed["ledger"].mkdir()          # a folder where the file should be
    sit = vincent.situation(NOW)
    assert sit["needs_attention"] is None
    assert sit["schedule_read_ok"] is False
    assert "could not be read" in sit["schedule_error"]
    assert "Everything scheduled" not in sit["opening"]
    assert "cannot tell you" in sit["opening"]
    page = vincent.pages(sit)[0]
    assert "NEEDS ATTENTION: unknown" in page


def test_no_ledger_yet_is_a_new_install_not_a_fault(sealed):
    assert not sealed["ledger"].exists()
    out = vincent.schedule(datetime(2026, 10, 7, 4, 0), job_rows=_loaded())
    assert out["read_ok"] is True and out["error"] == ""


# ── P7: memory, read ─────────────────────────────────────────────────────────

SECRETS = {
    "token=": "token=abcdef0123456789abcdef",
    "Bearer ": "Authorization: Bearer abcdef0123456789abcdefgh",
    "sk-": "sk-abcdefghijklmnopqrstuvwxyz012345",
    "AKIA": "AKIAABCDEFGHIJKLMNOP",
    "password:": "password: correcthorsebatterystaple",
}


def test_memory_carries_the_page_the_ledger_and_the_chat_memory(sealed, monkeypatch):
    sealed["memory"].write_text(
        "# Memory\n\nThe lender call moved to Thursdays.\n\n"
        "## Pending follow-ups\n\n"
        "- [ ] (ACME) Send the term sheet | due: 2026-10-01 | format: email\n",
        encoding="utf-8")
    folder = (sealed["home"] / ".claude" / "projects"
              / memory_sync.project_slug(config_loader.vault()) / "memory")
    folder.mkdir(parents=True)
    (folder / "MEMORY.md").write_text("- [Prep](prep.md): how prep is wanted\n",
                                      encoding="utf-8")
    (folder / "prep.md").write_text("Prep goes out the night before.\n",
                                    encoding="utf-8")
    assert config_loader.memory_sync_enabled() is False, "the mirror is off here"
    assert memory_sync.discover_memory_dirs() == [], "so the mirror reads nothing"

    mem = vincent.memory(NOW.date())
    assert "The lender call moved to Thursdays." in mem["text"]
    assert [f["title"] for f in mem["follow_ups"]] == ["Send the term sheet"]
    assert mem["follow_ups"][0]["status"] == "overdue"
    assert len(mem["project_memory"]) == 1, "and Vincent still reads it"
    files = {f["name"]: f["text"] for f in mem["project_memory"][0]["files"]}
    assert list(files)[0] == "MEMORY.md", "the index first"
    assert "Prep goes out the night before." in files["prep.md"]
    assert mem["errors"] == []


@pytest.mark.parametrize("shape", sorted(SECRETS))
def test_a_secret_in_memory_never_reaches_the_situation(sealed, shape):
    planted = SECRETS[shape]
    value = planted.split()[-1].split("=")[-1]
    sealed["memory"].write_text(f"# Memory\n\nA note.\n{planted}\nAfter it.\n",
                                encoding="utf-8")
    folder = (sealed["home"] / ".claude" / "projects"
              / memory_sync.project_slug(config_loader.vault()) / "memory")
    folder.mkdir(parents=True)
    (folder / "MEMORY.md").write_text(f"kept\n{planted}\n", encoding="utf-8")

    mem = vincent.memory(NOW.date())
    blob = json.dumps(mem)
    assert value not in blob, f"the {shape} value survived"
    assert mem["redactions"] >= 2, "one in the page, one in the chat memory"
    assert "A note." in mem["text"] and "After it." in mem["text"]
    assert value not in "\n".join(vincent.pages(_sit_with(mem)))


def _sit_with(mem: dict) -> dict:
    return {"generated_when": "", "opening": "", "needs_attention": [],
            "schedule_error": "", "schedule": [],
            "waiting_on_you": {"suggestions": [], "stalled": []},
            "memory": mem, "catalog": []}


def test_a_missing_memory_page_is_empty_not_an_error(sealed):
    assert not sealed["memory"].exists()
    mem = vincent.memory(NOW.date())
    assert mem["text"] == "" and mem["remembered"] == [] and mem["errors"] == []


# ── P8: memory, written ──────────────────────────────────────────────────────

def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_remembering_writes_one_dated_line_in_its_own_block(sealed):
    sealed["memory"].write_text(
        "# Memory\n\nStanding context.\n\n## Pending follow-ups\n\n"
        "- [ ] (ACME) Send the term sheet | due: 2026-10-01 | format: email\n",
        encoding="utf-8")
    other = sealed["vault"] / "wiki" / "note.md"
    other.parent.mkdir(parents=True)
    other.write_text("untouched\n", encoding="utf-8")
    before = _tree(sealed["vault"])

    out = vincent.remember("Prep is wanted two hours before each call.", NOW.date())
    assert out["ok"] and out["added"] is True

    after = _tree(sealed["vault"])
    changed = [k for k in after if after[k] != before.get(k)]
    assert changed == [str(sealed["memory"].relative_to(sealed["vault"]))]
    assert set(after) == set(before), "no file was created beside it"

    text = sealed["memory"].read_text(encoding="utf-8")
    block = text.split(vincent.MEMORY_BEGIN, 1)[1].split(vincent.MEMORY_END, 1)[0]
    assert block.count("\n- ") == 1
    assert f"- {TODAY}: Prep is wanted two hours before each call." in block
    assert text.index(vincent.MEMORY_END) < text.index("## Pending follow-ups"), \
        "above the ledger, never inside it"
    assert "Standing context." in text

    mem = vincent.memory(NOW.date())
    assert mem["remembered"] == [f"{TODAY}: Prep is wanted two hours before each call."]
    assert [f["title"] for f in mem["follow_ups"]] == ["Send the term sheet"], \
        "the radar still reads exactly its own entries"


def test_the_same_fact_twice_leaves_one_line(sealed):
    vincent.remember("Prep is wanted two hours before each call.", NOW.date())
    once = sealed["memory"].read_text(encoding="utf-8")
    again = vincent.remember("  prep is wanted   TWO hours before each call  ", NOW.date())
    assert again["ok"] and again["added"] is False
    assert sealed["memory"].read_text(encoding="utf-8") == once
    assert len(vincent.memory(NOW.date())["remembered"]) == 1

    vincent.remember("The lender call is on Thursdays.", NOW.date())
    assert len(vincent.memory(NOW.date())["remembered"]) == 2
    text = sealed["memory"].read_text(encoding="utf-8")
    assert text.count(vincent.MEMORY_BEGIN) == 1 and text.count(vincent.MEMORY_END) == 1


def test_a_fact_is_cleaned_before_it_is_written(sealed):
    out = vincent.remember(
        "My key is sk-abcdefghijklmnopqrstuvwxyz012345" + EM + "keep it\nsafe", NOW.date())
    text = sealed["memory"].read_text(encoding="utf-8")
    assert out["redactions"] == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in text
    assert EM not in text and EN not in text
    line = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(line) == 1, "a fact is one line however it was typed"
    assert vincent.remember("   ", NOW.date())["ok"] is False


# ── P10: one chief of staff ──────────────────────────────────────────────────

def _front(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return text[4:text.index("\n---", 4)]


def test_the_agent_file_is_one_the_cli_can_load():
    path = ROOT / "agents" / "vincent.md"
    front = _front(path)
    assert re.search(r"(?m)^name: vincent$", front)
    assert "description: >-" in front
    fm = vincent.frontmatter(path.read_text(encoding="utf-8"))
    assert fm["name"] == vincent.AGENT
    for situation in ("ran", "remembered", "skill"):
        assert situation in fm["description"], "the description is a routing rule"
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert "Skill" in tools and "Agent" in tools and "Bash" in tools
    text = path.read_text(encoding="utf-8")
    assert EM not in text and EN not in text
    assert "```bash" in text and "```powershell" in text, "both platforms"


def test_the_old_name_is_gone_from_everything_that_ships():
    import advisor
    assert advisor.AGENT_FILE == "advisor.md"
    assert (ROOT / "agents" / advisor.AGENT_FILE).exists()
    assert re.search(r"(?m)^name: advisor$", _front(ROOT / "agents" / "advisor.md"))
    assert "chief of staff" not in advisor.agent_rules().split("\n", 3)[0].lower()

    old = "chief-of-" + "staff"
    banned = (old + ".md", "name: " + old)
    scoped = [p for d in ("agents", "app", "skills", "tests")
              for p in (ROOT / d).rglob("*")
              if p.is_file() and p.suffix in {".py", ".md", ".json", ".js"}]
    scoped += [ROOT / n for n in ("README.md", "FOUR-CS.md", "CLAUDE.md")]
    assert len(scoped) > 100, "found too few files for this to mean anything"
    hits = [f"{p.relative_to(ROOT)}: {b}" for p in scoped for b in banned
            if b in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == []
    assert not (ROOT / "agents" / (old + ".md")).exists()


def test_exactly_one_agent_calls_itself_the_chief_of_staff():
    titled = [p.name for p in (ROOT / "agents").glob("*.md")
              if "# The chief of staff" in p.read_text(encoding="utf-8")]
    assert titled == ["vincent.md"]


# ── P12: the skill and the routing table ─────────────────────────────────────

def test_the_skill_reads_the_situation_then_the_agent_file():
    text = (ROOT / "skills" / "vincent" / "SKILL.md").read_text(encoding="utf-8")
    fm = vincent.frontmatter(text)
    assert fm["name"] == "vincent" and "/van-gogh:vincent" in fm["description"]
    assert text.count('app/vincent.py" --situation --page 1') == 1
    assert text.count("app\\vincent.py\" --situation --page 1") == 1
    assert "${CLAUDE_PLUGIN_ROOT}/agents/vincent.md" in text
    assert text.index("--situation") < text.index("agents/vincent.md")
    assert EM not in text and EN not in text


def test_the_four_cs_page_gives_vincent_a_cadence_of_conversation():
    page = (ROOT / "FOUR-CS.md").read_text(encoding="utf-8")
    row = re.search(r"(?m)^\| vincent \|[^|]*\| ([^|]*) \|$", page)
    assert row and row.group(1) == "Whenever you talk to it"


def test_the_vault_routes_chief_of_staff_questions_to_vincent_first():
    page = (ROOT / "vault" / "CLAUDE.md").read_text(encoding="utf-8")
    table = page.split("| Intent | Skill |\n|---|---|\n", 1)[1]
    first = table.splitlines()[0]
    assert "chief of staff" in first and first.rstrip().endswith("`/van-gogh:vincent` |")


# ── P13: the hand-off line ───────────────────────────────────────────────────

def test_a_skill_hand_off_is_read():
    got = vincent.parse_handoff("Handing to: /van-gogh:meeting-prep\n\nOn it.")
    assert got == {"kind": "skill", "target": "/van-gogh:meeting-prep",
                   "plugin": "van-gogh", "skill": "meeting-prep"}


def test_a_personal_skill_hand_off_is_read():
    got = vincent.parse_handoff("Sure.\nHanding to: /follow-up-radar\n")
    assert got["kind"] == "skill" and got["plugin"] == "" and got["skill"] == "follow-up-radar"


def test_an_agent_hand_off_is_read():
    got = vincent.parse_handoff("Handing to: bookkeeper-controller")
    assert got == {"kind": "agent", "target": "bookkeeper-controller",
                   "plugin": "", "skill": ""}


def test_nobody_is_a_hand_off_of_its_own_kind():
    got = vincent.parse_handoff("Handing to: nobody\nWhich bagels do you mean?")
    assert got["kind"] == "nobody"


def test_no_line_is_no_hand_off():
    assert vincent.parse_handoff("Morning Coffee ran at 7:00 AM PT.") is None
    assert vincent.parse_handoff("I am handing to the mechanic shortly.") is None
    assert vincent.parse_handoff("") is None
    assert vincent.parse_handoff(None) is None


def test_two_different_lines_are_no_hand_off_at_all():
    text = "Handing to: /van-gogh:week\nHanding to: /van-gogh:meeting-prep\n"
    assert vincent.parse_handoff(text) is None, "ambiguous, so code acts on neither"
    same = "Handing to: /van-gogh:week\nAnd again.\nHanding to: /van-gogh:week\n"
    assert vincent.parse_handoff(same)["skill"] == "week"


@pytest.mark.parametrize("line, target", [
    ("**Handing to:** `/van-gogh:week`", "/van-gogh:week"),
    ("> Handing to: /van-gogh:week.", "/van-gogh:week"),
    ("handing to: van-gogh-mechanic", "van-gogh-mechanic"),
])
def test_markup_around_the_line_does_not_hide_it(line, target):
    assert vincent.parse_handoff(line)["target"] == target


@pytest.mark.parametrize("line", [
    "Handing to: the meeting prep skill",
    "Handing to: /van-gogh:meeting-prep and then /van-gogh:week",
    "Handing to: /van-gogh:meeting-prep --today; rm -rf",
])
def test_a_line_that_names_nothing_exact_is_not_acted_on(line):
    assert vincent.parse_handoff(line) is None


def test_the_hand_off_command_checks_the_name_and_prints_the_line(capsys, sealed):
    assert vincent.main(["--handoff", "/van-gogh:meeting-prep"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Handing to: /van-gogh:meeting-prep"
    assert vincent.parse_handoff(out)["skill"] == "meeting-prep"
    assert "waits for a yes" not in out

    assert vincent.main(["--handoff", "/van-gogh:uninstall-van-gogh"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Handing to: /van-gogh:uninstall-van-gogh\n")
    assert "waits for a yes" in out and "Start nothing" in out.replace("\n", " ")

    for agent in ("advisor", "van-gogh:advisor"):
        assert vincent.main(["--handoff", agent]) == 0
        assert capsys.readouterr().out.startswith("Handing to: advisor\n")

    assert vincent.main(["--handoff", "/van-gogh:bagel-poller"]) == 1
    out = capsys.readouterr().out
    assert "Handing to:" not in out and "Nothing here is called" in out
    _skill(sealed["home"] / ".claude", "juice-order",
           "Works out how many boxes of oranges a juice stand should order.")
    assert vincent.main(["--handoff", "nobody"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Handing to: nobody\n")
    assert "- /juice-order: Works out how many boxes of oranges" in out, \
        "nobody is never offered without every other skill beside it"
    assert "/van-gogh:meeting-prep" not in out.split("\n\n", 2)[-1], \
        "Van Gogh's own are on page 1 already"
    assert len(out) < 30_000

    _skill(sealed["home"] / ".claude", "weekly-numbers", "My numbers.")
    assert vincent.main(["--handoff", "/weekly-numbers"]) == 0
    assert capsys.readouterr().out.startswith("Handing to: /weekly-numbers\n")


# ── P14: what needs a yes ────────────────────────────────────────────────────

MUST_ASK = {"add-account", "remove-account", "update-settings",
            "update-digest-preferences", "install-van-gogh",
            "uninstall-van-gogh", "migrate-from-legacy-van-gogh"}


def test_the_skills_that_change_things_are_marked_in_code():
    assert MUST_ASK <= vincent.NEEDS_CONFIRM
    shipped = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert vincent.NEEDS_CONFIRM <= shipped, "every one of them is a real skill"
    got = _by_invoke(vincent.catalog())
    for name in vincent.NEEDS_CONFIRM:
        assert got[f"/van-gogh:{name}"]["needs_confirm"] is True, name
    assert got["/van-gogh:morning-coffee"]["needs_confirm"] is False
    assert "ASK FIRST" in vincent._catalog_line(got["/van-gogh:uninstall-van-gogh"], 80)
    assert "ASK FIRST" not in vincent._catalog_line(got["/van-gogh:morning-coffee"], 80)


def test_a_personal_copy_of_one_is_held_to_the_same_rule(sealed):
    _skill(sealed["home"] / ".claude", "uninstall-van-gogh", "My own uninstaller.")
    assert _by_invoke(vincent.catalog())["/uninstall-van-gogh"]["needs_confirm"] is True


def test_nothing_that_needs_a_yes_can_run_with_nobody_there():
    shipped = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert set(vincent.HEADLESS_RUNNABLE) <= shipped
    assert not set(vincent.HEADLESS_RUNNABLE) & vincent.NEEDS_CONFIRM
    for skill in vincent.NEEDS_CONFIRM:
        assert not vincent.runs_headless(vincent.parse_handoff(f"Handing to: /van-gogh:{skill}"))
    assert vincent.runs_headless(vincent.parse_handoff("Handing to: /van-gogh:meeting-prep"))
    assert not vincent.runs_headless(vincent.parse_handoff("Handing to: /meeting-prep")), \
        "a personal skill of the same name is not the one that was vetted"
    assert not vincent.runs_headless(vincent.parse_handoff("Handing to: van-gogh-mechanic"))
    assert not vincent.runs_headless(vincent.parse_handoff("Handing to: nobody"))
    assert not vincent.runs_headless(None)


# ── The headless turn: P19, P20, P21, P27 ────────────────────────────────────

class _Turn:
    """Stands in for the CLI: records each call, answers with the next reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, root, **kw):
        self.calls.append({"prompt": prompt, "root": root, **kw})
        reply = self.replies.pop(0) if self.replies else "Nothing more."
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return subprocess.CompletedProcess([], reply[0], reply[1], reply[2])
        return subprocess.CompletedProcess([], 0, reply, "")


@pytest.fixture
def turn(monkeypatch):
    def install(*replies):
        t = _Turn(*replies)
        monkeypatch.setattr(claude_cli, "run_claude_readonly", t)
        return t
    return install


@pytest.fixture
def skills(monkeypatch):
    """Records which skills code ran, and lets a test choose what they print."""
    ran = []
    box = {"out": "The brief.", "raise": None}

    def fake(skill, claude=None, args="", capture_stdout=False, timeout_s=0):
        ran.append({"skill": skill, "timeout_s": timeout_s, "args": args})
        if box["raise"]:
            raise box["raise"]
        return box["out"] if capture_stdout else None

    monkeypatch.setattr(skill_run, "run_skill", fake)
    box["ran"] = ran
    return box


def test_a_handed_off_skill_that_fails_is_never_reported_as_a_result(turn, skills, sealed):
    turn("Handing to: /van-gogh:meeting-prep\n\nGetting your prep ready.")
    skills["raise"] = RuntimeError(
        "claude -p /van-gogh:meeting-prep exited 1: Traceback (most recent call last):\n"
        "  File \"meeting_prep.py\", line 12\nValueError: the calendar sign-in was rejected")
    out = vincent.answer("prep my next meeting", "workbench:morning-coffee", now=NOW)
    assert skills["ran"][0]["skill"] == "meeting-prep"
    assert "did not finish" in out["reply"]
    assert "ValueError: the calendar sign-in was rejected" in out["reply"]
    assert out["ran"] == "", "nothing ran to completion"
    assert "Traceback" not in out["reply"], "one line, not the stack"
    row = json.loads(sealed["ledger"].read_text(encoding="utf-8").splitlines()[-1])
    assert row["job"] == "vincent.meeting-prep" and row["rc"] == 1


def test_a_skill_that_printed_nothing_is_not_a_result_either(turn, skills):
    turn("Handing to: /van-gogh:week")
    skills["out"] = "   \n"
    out = vincent.answer("my week", "workbench:week", now=NOW)
    assert "printed nothing" in out["reply"] and out["ran"] == ""


def test_a_skill_that_worked_is_shown_under_the_reply(turn, skills, sealed):
    turn("Handing to: /van-gogh:meeting-prep\n\nGetting your prep ready.")
    skills["out"] = "PREP\nLender call at 10:00 AM PT / 1:00 PM ET.\n"
    out = vincent.answer("get me ready for my next call", "workbench:morning-coffee", now=NOW)
    assert out["ran"] == "meeting-prep"
    assert out["reply"].startswith("Handing to: /van-gogh:meeting-prep")
    assert "Lender call at 10:00 AM PT / 1:00 PM ET." in out["reply"]
    assert skills["ran"][0]["timeout_s"] == vincent.SKILL_RUN_TIMEOUT_S
    row = json.loads(sealed["ledger"].read_text(encoding="utf-8").splitlines()[-1])
    assert row["job"] == "vincent.meeting-prep" and row["rc"] == 0


@pytest.mark.parametrize("reply, said", [
    ("Handing to: /van-gogh:meeting-ingest\n\nFiling them.", "needs a terminal"),
    ("Handing to: van-gogh-mechanic\n\nShall I?", "needs a terminal"),
    ("Handing to: /van-gogh:uninstall-van-gogh\n\nAre you sure?", "Nothing was changed"),
    ("Handing to: /meeting-prep\n\nYour own copy.", "needs a terminal"),
])
def test_code_runs_nothing_that_is_not_on_the_list(turn, skills, sealed, reply, said):
    turn(reply)
    out = vincent.answer("do it", "workbench:week", now=NOW)
    assert skills["ran"] == [], "code started a skill it must not"
    assert said in out["reply"]
    assert "claude --agent vincent" in out["reply"]
    assert not sealed["ledger"].exists(), "and left no run row"


def test_a_hand_off_that_ends_in_a_question_is_a_proposal_and_is_not_run(turn, skills, sealed):
    """Vincent writes the line and asks when the work waits for a yes. The
    line is the same either way, so the closing question is what code reads."""
    t = turn("Handing to: /van-gogh:afternoon-tea\n\nAfternoon Tea failed once this "
             "morning. Do you want me to run it again?\nSOURCES: none",
             "Handing to: /van-gogh:afternoon-tea\n\nRunning it again now.")
    asked = vincent.answer("afternoon tea died, sort it out", "workbench:week", now=NOW)
    assert skills["ran"] == [], "a question is not a yes"
    assert "Nothing has started" in asked["reply"] and asked["ran"] == ""
    assert not sealed["ledger"].exists()
    went = vincent.answer("yes", "workbench:week", now=NOW)
    assert [r["skill"] for r in skills["ran"]] == ["afternoon-tea"]
    assert went["ran"] == "afternoon-tea"
    assert t.calls[1]["resume"], "the yes lands in the same conversation"
    assert vincent.is_asking("Shall I?") and vincent.is_asking("**Shall I go ahead?**")
    assert not vincent.is_asking("Is it late? No. Running it now.")
    assert not vincent.is_asking("")


def test_a_plain_answer_runs_nothing_and_adds_nothing(turn, skills):
    turn("Morning Coffee is switched off. It last ran Wed Sep 30.")
    out = vincent.answer("did morning coffee run?", "workbench:morning-coffee", now=NOW)
    assert out["reply"] == "Morning Coffee is switched off. It last ran Wed Sep 30."
    assert out["handoff"] is None and skills["ran"] == []


def test_nobody_is_answered_with_the_question_and_nothing_else(turn, skills):
    turn("Handing to: nobody\n\nWhich bagels do you mean?")
    out = vincent.answer("do the thing with the bagels", "workbench:week", now=NOW)
    assert out["handoff"]["kind"] == "nobody" and skills["ran"] == []
    assert "terminal" not in out["reply"]


def test_an_empty_message_costs_no_call(turn, skills):
    t = turn("should not be asked")
    assert vincent.answer("   ", "workbench:week", now=NOW)["reply"] == ""
    assert t.calls == []


def test_the_turn_is_the_read_only_call_with_vincent_as_its_rules(turn, skills):
    t = turn("All quiet.")
    vincent.answer("anything wrong?", "workbench:week", now=NOW)
    call = t.calls[0]
    assert call["agent"] == "vincent"
    assert Path(call["plugin_dir"]) == ROOT
    assert call["timeout"] == vincent.VINCENT_TIMEOUT_S
    assert call["root"] == config_loader.vault()
    assert "THE SITUATION" in call["prompt"] and "anything wrong?" in call["prompt"]
    assert "SCHEDULE (" in call["prompt"], "the situation travels in the message"


def test_the_argv_of_a_turn_can_read_and_nothing_else(monkeypatch, skills):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, "All quiet.", "")

    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(config_loader, "workbench_chat_model", lambda: "")
    vincent.answer("anything wrong?", "workbench:week", now=NOW)
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--restricted" in cmd and "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--agent") + 1] == "vincent"
    assert Path(cmd[cmd.index("--plugin-dir") + 1]) == ROOT
    assert "--session-id" in cmd and "--resume" not in cmd
    assert "--model" not in cmd, "an empty setting leaves the choice to the agent file"
    for banned in ("bypassPermissions", "--add-dir", "Bash", "Write", "Edit", "Skill"):
        assert banned not in " ".join(cmd), banned
    assert "anything wrong?" in seen["kw"]["input"], "the message goes in on stdin"
    assert "ANTHROPIC_API_KEY" not in seen["kw"]["env"]


def test_the_chat_model_setting_is_honoured(monkeypatch, skills):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "All quiet.", "")

    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(config_loader, "workbench_chat_model", lambda: "claude-sonnet-5-5")
    vincent.answer("anything wrong?", "workbench:week", now=NOW)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"


def test_the_plain_read_only_call_is_unchanged_for_its_other_callers(monkeypatch):
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    assert claude_cli.readonly_cmd() == [
        "claude", "-p", "--model", claude_cli.HAIKU_MODEL, *claude_cli._READ_ONLY_FLAGS]
    resumed = claude_cli.readonly_cmd(None, "vincent", "/p", session_id="a", resume="b")
    assert resumed[:8] == ["claude", "-p", "--agent", "vincent", "--plugin-dir", "/p",
                           "--resume", "b"]
    assert "--session-id" not in resumed, "one or the other, never both"
    assert resumed[-len(claude_cli._READ_ONLY_FLAGS):] == claude_cli._READ_ONLY_FLAGS


def test_a_second_message_continues_the_first_conversation(turn, skills):
    t = turn("Two failed: the filing and the sync.", "The filing, then.")
    first = vincent.answer("which jobs failed today?", "workbench:week", now=NOW)
    second = vincent.answer("re-run the first one", "workbench:week", now=NOW)
    assert t.calls[0]["session_id"] and t.calls[0]["resume"] is None
    assert t.calls[1]["resume"] == t.calls[0]["session_id"]
    assert t.calls[1]["session_id"] is None
    assert first["session"] == second["session"]


def test_a_new_day_or_another_page_is_a_new_conversation(turn, skills):
    t = turn("One.", "Two.", "Three.")
    vincent.answer("which jobs failed today?", "workbench:week", now=NOW)
    vincent.answer("re-run the first one", "workbench:week", now=NOW, day="2026-10-08")
    vincent.answer("re-run the first one", "workbench:morning-coffee", now=NOW)
    assert t.calls[1]["resume"] is None, "yesterday is not carried into today"
    assert t.calls[2]["resume"] is None, "another page is another conversation"
    ids = [c["session_id"] for c in t.calls]
    assert len(set(ids)) == 3 and all(ids)


def test_a_conversation_that_cannot_be_resumed_starts_again(turn, skills):
    t = turn("First.", (1, "", "No conversation found with session ID"),
             "Fresh start.", "Third.")
    vincent.answer("hello", "workbench:week", now=NOW)
    out = vincent.answer("and again", "workbench:week", now=NOW)
    assert out["reply"] == "Fresh start."
    assert t.calls[1]["resume"] and t.calls[2]["resume"] is None
    assert t.calls[2]["session_id"] != t.calls[0]["session_id"]
    vincent.answer("third", "workbench:week", now=NOW)
    assert t.calls[3]["resume"] == t.calls[2]["session_id"], \
        "the fresh conversation is the one that is kept"


def test_a_turn_that_runs_out_of_time_says_so(turn, skills):
    turn(subprocess.TimeoutExpired("claude", vincent.VINCENT_TIMEOUT_S))
    out = vincent.answer("everything, at length", "workbench:week", now=NOW)
    assert "was stopped" in out["reply"] and skills["ran"] == []
    assert vincent.VINCENT_TIMEOUT_S <= 600


def test_a_turn_that_fails_says_so_and_runs_nothing(turn, skills):
    turn((1, "", "You've hit your usage limit. It resets at 3pm."))
    out = vincent.answer("hello", "workbench:week", now=NOW)
    assert out["reply"].startswith("Vincent did not answer.")
    assert "usage limit" in out["reply"] and skills["ran"] == []


def test_a_huge_skill_output_is_cut_and_says_it_was(turn, skills):
    turn("Handing to: /van-gogh:week\n\nHere is your week.")
    skills["out"] = "line of the week briefing\n" * 8000        # about 200 KB
    assert len(skills["out"]) >= 200_000
    out = vincent.answer("my week", "workbench:week", now=NOW)
    assert len(out["reply"]) <= vincent.REPLY_CAP
    assert "Shortened" in out["reply"] and "207,999" in out["reply"]
    assert out["reply"].startswith("Handing to: /van-gogh:week")
    assert out["ran"] == "week"


def test_a_huge_reply_with_no_skill_is_cut_too(turn, skills):
    turn("word " * 5000)
    out = vincent.answer("tell me everything", "workbench:week", now=NOW)
    assert len(out["reply"]) <= vincent.REPLY_CAP and "Shortened" in out["reply"]


# ── P20: never sends ─────────────────────────────────────────────────────────

_SEND_RE = re.compile(r"send_email\.py|digest_send|\bimport send_email\b|"
                      r"(?<![\w-])--send\b|\.send_email\(")


def _executable_lines(path: Path) -> list:
    """The lines of a file that can run: code, or the inside of a fence."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        out, in_doc = [], False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if not (in_doc is False and stripped.count('"""') == 2 and len(stripped) > 3):
                    in_doc = not in_doc
                continue
            if in_doc or stripped.startswith("#"):
                continue
            out.append(line)
        return out
    out, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            out.append(line)
    return out


def test_nothing_vincent_runs_can_send(tmp_path):
    targets = [ROOT / "app" / "vincent.py", ROOT / "agents" / "vincent.md",
               ROOT / "skills" / "vincent" / "SKILL.md"]
    scanned = 0
    for path in targets:
        lines = _executable_lines(path)
        assert lines, f"{path.name} gave the scan nothing to read"
        scanned += len(lines)
        hits = [ln for ln in lines if _SEND_RE.search(ln)]
        assert hits == [], f"{path.name}: {hits}"
    assert scanned > 300

    # The scan is not blind: the same pattern finds each thing it hunts, in a
    # real target's own text, in code and inside a fence.
    code = tmp_path / "planted.py"
    code.write_text((ROOT / "app" / "vincent.py").read_text(encoding="utf-8")
                    + "\nimport send_email\n", encoding="utf-8")
    assert [ln for ln in _executable_lines(code) if _SEND_RE.search(ln)]
    page = tmp_path / "planted.md"
    page.write_text((ROOT / "agents" / "vincent.md").read_text(encoding="utf-8")
                    + '\n```bash\npython app/send_email.py --to x\n```\n',
                    encoding="utf-8")
    assert [ln for ln in _executable_lines(page) if _SEND_RE.search(ln)]
    prose = tmp_path / "prose.md"
    prose.write_text("He never runs send_email.py for you.\n", encoding="utf-8")
    assert _executable_lines(prose) == [], "a sentence that forbids it is not a call"


def test_the_agent_file_says_never_send_and_offers_a_draft():
    text = (ROOT / "agents" / "vincent.md").read_text(encoding="utf-8")
    assert "You never send anything" in text
    assert "offer to leave a\ndraft" in text or "offer to leave a draft" in text


# ── The text pages ───────────────────────────────────────────────────────────

def test_the_first_page_holds_what_a_first_reply_needs(sealed, tmp_path):
    _plant_world(sealed["home"], tmp_path)
    for i in range(120):
        _skill(sealed["home"] / ".claude", f"bulk-{i:03d}", "Filler skill. " * 20)
    vincent.remember("Prep is wanted two hours before each call.", NOW.date())
    sealed["ledger"].write_text(json.dumps(_run("watch.tick", "08:45")) + "\n",
                                encoding="utf-8")
    sit = vincent.situation(NOW)
    pages = vincent.pages(sit)
    assert len(pages) >= 3, "this much does not fit one page"
    first = pages[0]
    assert f"Page 1 of {len(pages)}." in first
    assert "OPENING\n" in first and "NEEDS ATTENTION" in first
    assert f"SCHEDULE ({len(job_registry.jobs())} jobs)" in first
    assert "Prep is wanted two hours before each call." in first
    assert "/van-gogh:meeting-prep" in first and "- vincent [agent]" in first
    assert len(first) < 30_000, "a session's shell tool would cut it"
    for page in pages[1:]:
        assert len(page) < vincent.PAGE_BUDGET + 600

    whole = "\n".join(pages)
    for row in sit["catalog"]:
        marker = row["invoke"] or f"plugin {row['name']}"
        assert marker in whole, f"{marker} fell off the pages"
    assert "End of the situation." in pages[-1]
    assert "Read every page before" in pages[0]


def test_the_command_line_prints_a_page_and_refuses_one_that_is_not_there(capsys, sealed):
    assert vincent.main(["--situation", "--page", "1"]) == 0
    assert "VINCENT SITUATION" in capsys.readouterr().out
    assert vincent.main(["--situation", "--page", "99"]) == 1
    assert "pages" in capsys.readouterr().err
    assert vincent.main(["--catalog"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)
    assert vincent.main(["--schedule"]) == 0
    assert json.loads(capsys.readouterr().out)["read_ok"] is True
    assert vincent.main(["--remember", "The lender call is on Thursdays."]) == 0
    assert json.loads(capsys.readouterr().out)["added"] is True
    assert vincent.main([]) == 1


# ── What is set up, and what is not ──────────────────────────────────────────

import setup_flow  # noqa: E402
import setup_state  # noqa: E402
import schedule_plan  # noqa: E402


@pytest.fixture
def setup_world(tmp_path, monkeypatch):
    """A state folder of the test's own, with no check that touches the real
    scheduler. A test plants a checklist or a stamp in it."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.setattr(setup_flow, "quiet_checks", lambda: {})
    return state


def _checklist(done=(), skipped=(), not_needed=()):
    data = setup_state.new("20261007-090000-ab12", "darwin", "0.84.0")
    for key in done:
        setup_state.set_status(data, key, setup_state.DONE)
    for key in skipped:
        setup_state.set_status(data, key, setup_state.SKIPPED, "by choice")
    for key in not_needed:
        setup_state.set_status(data, key, setup_state.NOT_APPLICABLE, "no notetaker")
    setup_state.save(data)
    return data


def test_a_working_vault_with_no_setup_record_is_not_called_unfinished(setup_world):
    sit = vincent.situation(NOW)
    setup = sit["install"]["setup"]
    assert setup["state"] == "complete" and setup["unrecorded"] is True
    assert "Setup" not in sit["opening"] and "set up" not in sit["opening"]
    page = vincent.pages(sit)[0]
    assert "SETUP: finished, before setup kept a record of its steps" in page
    assert "You run setup yourself" not in page


def test_an_unfinished_setup_leads_the_opening_and_names_the_next_step(setup_world):
    _checklist(done=("plugin", "python", "credentials", "accounts", "vault"))
    sit = vincent.situation(NOW)
    setup = sit["install"]["setup"]
    assert setup["state"] == "incomplete"
    assert setup["open"] == ["Background jobs scheduled", "Deals and people seeded",
                             "Meetings filed", "First briefings shown"]
    lead = ("Setup is not finished: 4 of 9 steps are open. Next: schedule your briefings. "
            "I can take you through it now.")
    assert sit["opening"] == lead, \
        "nothing has been scheduled yet, so no job is reported as not having run"
    page = vincent.pages(sit)[0]
    assert "SETUP: not finished (4 of 9 steps open)" in page
    assert "- [done] Accounts signed in" in page
    assert "- [open] Background jobs scheduled" in page
    assert "You run setup yourself." in page and "--setup-rules" in page
    assert page.index("SETUP:") < page.index("NEEDS ATTENTION")
    # The schedule step is still open, so no job is listed as due or failed.
    assert "NEEDS ATTENTION: setup. Nothing has been scheduled yet" in page
    assert "SCHEDULE: nothing is scheduled yet." in page
    assert "today:" not in page and "did not run" not in page


def test_once_the_schedule_step_is_done_the_jobs_are_listed_again(setup_world):
    _checklist(done=[k for k in setup_state.KEYS if k != "deals"])
    page = vincent.pages(vincent.situation(NOW))[0]
    assert "SCHEDULE (18 jobs)" in page
    assert "Nothing has been scheduled yet" not in page


def test_one_open_step_is_said_in_the_singular_and_the_jobs_follow_once_scheduled(setup_world):
    _checklist(done=[k for k in setup_state.KEYS if k != "deals"])
    sit = vincent.situation(NOW)
    lead = ("Setup is not finished: 1 of 9 step is open. Next: tick your real deals. "
            "I can take you through it now.")
    assert sit["opening"].startswith(lead + " ")
    assert sit["opening"][len(lead) + 1:] == vincent._jobs_sentence(vincent.schedule(NOW)), \
        "the schedule step is done, so the jobs are reported after setup"


def test_a_step_that_happens_outside_claude_is_never_offered_as_something_to_run(setup_world):
    _checklist()
    sit = vincent.situation(NOW)
    assert sit["install"]["setup"]["before_claude"] is True
    assert "happens outside Claude" in sit["opening"]
    assert "take you through" not in sit["opening"]
    page = vincent.pages(sit)[0]
    assert "You cannot run it from here." in page and "You run setup yourself" not in page
    brief = vincent.setup_brief()
    assert "SETUP RULES" not in brief, "no rules are handed over for a step he cannot run"


def test_the_setup_record_is_reported_as_stored_and_never_written(setup_world, monkeypatch):
    """A sign-in the provider rejected is reopened in the checklist by the
    product's own daily check. Its token is still on disk, so a quick local
    look says "done". Vincent reports what the file says, and does not look
    again: found on a live run, where the re-check hid a rejected sign-in
    and called the install one step from finished."""
    data = _checklist(done=setup_state.KEYS)
    setup_state.set_status(data, "accounts", setup_state.OPEN, "sign-in no longer works")
    setup_state.save(data)
    monkeypatch.setattr(setup_flow, "quiet_checks",
                        lambda: {"accounts": lambda: (setup_state.DONE, "1 signed in")})
    monkeypatch.setattr(setup_state, "CHECKS",
                        {"accounts": lambda: (setup_state.DONE, "1 signed in")})
    path = setup_state.checklist_path()
    before = path.read_bytes()
    sit = vincent.situation(NOW)
    setup = sit["install"]["setup"]
    assert setup["state"] == "incomplete" and setup["open"] == ["Accounts signed in"]
    assert sit["opening"].startswith(
        "Setup is not finished: 1 of 9 step is open. Next: sign in to your accounts.")
    assert "- [open] Accounts signed in (sign-in no longer works)" in vincent.pages(sit)[0]
    assert path.read_bytes() == before


def test_a_finished_setup_says_what_was_skipped(setup_world):
    setup_state.write_stamp({
        "run_id": "r1", "plugin_version": "0.84.0",
        "boxes": {**{k: setup_state.DONE for k in setup_state.KEYS},
                  "deals": setup_state.SKIPPED, "meetings": setup_state.NOT_APPLICABLE}})
    sit = vincent.situation(NOW)
    assert sit["install"]["setup"]["state"] == "complete"
    assert "Setup" not in sit["opening"]
    page = vincent.pages(sit)[0]
    assert "SETUP: finished\n" in page
    assert "- [skipped by choice] Deals and people seeded" in page
    assert "- [not needed here] Meetings filed" in page
    assert page.count("[done]") == 7


def test_a_computer_with_no_vault_gets_an_answer_about_setup_and_nothing_invented(
        setup_world, monkeypatch):
    def no_vault():
        raise FileNotFoundError("no vault pointer")

    monkeypatch.setattr(config_loader, "cfg", no_vault)
    sit = vincent.situation(NOW)
    inst = sit["install"]
    assert inst["setup"]["state"] == "not_started" and inst["has_vault"] is False
    assert sit["opening"] == ("Van Gogh is not set up on this computer yet. "
                              "I can take you through setup now.")
    assert inst["accounts"] == [] and inst["runs_by_itself"] == []
    page = vincent.pages(sit)[0]
    assert "SETUP: never started on this computer" in page
    assert "WHAT IS SET UP: nothing yet." in page
    assert "NEEDS ATTENTION: setup." in page
    assert "could not be read" not in page


def test_what_is_set_up_lists_every_job_once_on_or_off(setup_world, monkeypatch):
    tpl = json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))
    cfg = schedule_plan.setup_defaults(tpl)
    cfg["accounts"] = [{"label": "Work", "email": "ada@example.com", "provider": "google",
                        "is_primary": True}]
    monkeypatch.setattr(config_loader, "cfg", lambda: cfg)
    inst = vincent.install()
    on = [r["job"] for r in inst["runs_by_itself"]]
    off = [r["job"] for r in inst["switched_off"]]
    assert len(on) == 13 and len(off) == 5
    assert sorted(on + off) == sorted(j.key for j in job_registry.jobs())
    assert set(schedule_plan.WITH_SCHEDULE) <= set(on)
    assert [a["email"] for a in inst["accounts"]] == ["ada@example.com"]
    lines = "\n".join(vincent._install_lines(inst))
    assert "- runs by itself (13):" in lines and "- available and switched off (5):" in lines
    assert "  - Morning Coffee [digest-morning-coffee]: weekdays at 7:00 AM" in lines
    assert "  - Finance brief [finance-email]: the week's numbers, by email; would run" in lines
    assert "- accounts (1): Work ada@example.com" in lines
    assert "- notetaker: none connected" in lines
    # With everything switched off, the four that come with a schedule are
    # off too: nothing runs.
    monkeypatch.setattr(config_loader, "cfg", lambda: schedule_plan.nothing(cfg))
    quiet = vincent.install()
    assert quiet["runs_by_itself"] == [] and len(quiet["switched_off"]) == 18


def test_the_setup_rules_are_the_install_skills_page_with_every_path_written_out():
    rules = vincent.setup_rules()
    root = vincent.plugin_root().as_posix()
    assert rules.startswith("SETUP RULES, printed by code.")
    assert "CLAUDE_PLUGIN_ROOT" not in rules
    assert "Under Codex, before anything else" not in rules and "codex-first-chat" not in rules
    assert (ROOT / "skills" / "install-van-gogh" / "SKILL.md").read_text(
        encoding="utf-8").count("Under Codex, before anything else") == 1
    assert "disable-model-invocation" not in rules and "allowed-tools" not in rules
    assert f'"{root}/app/setup_flow.py" next' in rules
    assert f"]({root}/skills/install-van-gogh/references/" in rules
    assert "](references/" not in rules
    assert "--answer-file" in rules
    # A here-document on a command is refused by the permission check on a
    # machine that has not allowed every shell command. Neither the rules nor
    # the skill may hand an answer over that way.
    skill = (ROOT / "skills" / "install-van-gogh" / "SKILL.md").read_text(encoding="utf-8")
    for text in (rules, skill):
        assert "<<'" not in text and "--answer-stdin" not in text
    assert EM not in rules and EN not in rules


def test_the_brief_for_the_setup_line_carries_the_situation_and_the_rules(setup_world):
    _checklist(done=("plugin", "python", "credentials"))
    brief = vincent.setup_brief()
    assert brief.startswith("--- THE SITUATION, GATHERED BY CODE JUST NOW ---")
    assert "--- END OF THE SITUATION ---" in brief
    assert "SETUP: not finished (6 of 9 steps open)" in brief
    assert "they have already asked to be set up" in brief
    assert brief.index("END OF THE SITUATION") < brief.index("SETUP RULES, printed by code.")
    assert "COMMANDS ON THIS COMPUTER: the plugin folder is " \
        f"{vincent.plugin_root().as_posix()} " in brief


def test_the_brief_on_a_finished_install_hands_over_no_rules(setup_world):
    setup_state.write_stamp({"run_id": "r1", "plugin_version": "0.84.0",
                             "boxes": {k: setup_state.DONE for k in setup_state.KEYS}})
    brief = vincent.setup_brief()
    assert "SETUP: finished" in brief
    assert "SETUP RULES" not in brief and "already asked to be set up" not in brief
    assert "Open as your rules say" in brief


def test_the_agent_file_tells_vincent_how_to_set_up_and_how_to_change_the_schedule():
    text = (ROOT / "agents" / "vincent.md").read_text(encoding="utf-8")
    for heading in ("## Setup\n", "## Changing the schedule\n"):
        assert text.count(heading) == 1, heading
    assert text.index("## Setup\n") < text.index("## Handing work over\n")
    setup = text[text.index("## Setup\n"):text.index("## Changing the schedule\n")]
    change = text[text.index("## Changing the schedule\n"):text.index("## Handing work over\n")]
    assert setup.count("/app/vincent.py\" --setup-rules") == 1      # the bash form
    assert setup.count("\\app\\vincent.py\" --setup-rules") == 1    # the PowerShell form
    assert "no `Handing to:` line" in setup
    assert "schedule_plan.py\" change --dry-run" in change
    assert "\\app\\schedule_plan.py\" change --dry-run" in change
    assert "schedule_plan.py\" show" in change and "\\app\\schedule_plan.py\" show" in change
    assert "On a yes, run the same command without `--dry-run`" in change
    assert "update-settings` is what changes it" not in text, \
        "the old answer to a schedule request sent the person to another skill"
    assert EM not in text and EN not in text
