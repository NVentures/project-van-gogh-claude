"""The escalation: does it look only when looking can help, and only once?

Two properties are worth more than the rest of this file put together, and
both are about restraint rather than capability.

A mechanic that fires on the first failure costs a model call to conclude
"try again", which the watcher already does for free. So every class that
releases itself on its own gets a planted case proving no look happens.

A mechanic that fires twice on one problem is worse than one that never fires:
the spend is unbounded and the second answer is the same as the first. So the
budget gets a planted case on each side of it, and the crash case too, since a
session that dies must still spend its attempt.

Nothing here spawns anything. `subprocess.run` is replaced, and so is the
`claude` binary lookup: resolving the CLI happens while the command is being
BUILT, which is before the stubbed spawn is ever reached, so a machine without
Claude Code on its PATH (every CI runner) failed ten tests here that have
nothing to do with the CLI. Stubbing the spawn alone is not enough when the
argv is assembled from the environment.
"""

import subprocess
from datetime import datetime, timedelta

import pytest

import config_loader
import job_watch
import repair

NOW = datetime(2026, 9, 9, 12, 0)


class _Ok:
    returncode = 0
    stdout = "looked at it"
    stderr = ""


@pytest.fixture
def spawns(monkeypatch, tmp_path):
    """Record every spawn instead of making one. Returns the list."""
    calls = []

    def fake(cmd, **kwargs):
        calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        return _Ok()

    monkeypatch.setattr(repair.subprocess, "run", fake)
    # The CLI path is resolved while building argv, before the stub above is
    # reached. Without this, `claude_bin()` raises FileNotFoundError on any
    # machine that has no Claude Code installed and the assertions below read
    # as product failures instead of a missing binary.
    monkeypatch.setattr(repair, "claude_bin", lambda: "/stub/claude")
    monkeypatch.setattr(repair, "state_path", lambda: tmp_path / "repair.json")
    monkeypatch.setattr(repair, "repairs_dir", lambda: tmp_path / "repairs")
    return calls


# ── The budget ───────────────────────────────────────────────────────────────

def test_one_look_per_job_per_day(spawns):
    assert repair.run("digest-week", "boom", "unknown", now=NOW)["spawned"]
    second = repair.run("digest-week", "boom", "unknown", now=NOW)
    assert not second["spawned"]
    assert "already" in second["why"]
    assert len(spawns) == 1


def test_the_daily_budget_covers_every_job_together(spawns):
    """Six things broken on one morning is one cause with six symptoms.

    Asking the model once per symptom is six answers to the same question, so
    the cap is on looks per day and not on looks per job per day."""
    for i in range(repair.MAX_PER_DAY):
        assert repair.run(f"job-{i}", "boom", "unknown", now=NOW)["spawned"]
    over = repair.run("one-too-many", "boom", "unknown", now=NOW)
    assert not over["spawned"]
    assert len(spawns) == repair.MAX_PER_DAY


def test_the_budget_resets_the_next_day(spawns):
    repair.run("digest-week", "boom", "unknown", now=NOW)
    tomorrow = repair.run("digest-week", "boom", "unknown",
                          now=NOW + timedelta(days=1))
    assert tomorrow["spawned"]


def test_a_crashed_look_still_spends_its_attempt(monkeypatch, tmp_path):
    """Mutation note: move `_stamp` after the spawn in `repair.run` and this
    fails. A session that hangs, crashes, or is killed by a sleeping laptop
    would otherwise be retried at the next tick and every tick after it,
    which is the unbounded loop the budget exists to prevent."""
    monkeypatch.setattr(repair, "state_path", lambda: tmp_path / "repair.json")
    monkeypatch.setattr(repair, "repairs_dir", lambda: tmp_path / "repairs")
    monkeypatch.setattr(repair, "claude_bin", lambda: "/stub/claude")

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, repair.TIMEOUT_S)

    monkeypatch.setattr(repair.subprocess, "run", boom)
    first = repair.run("digest-week", "boom", "unknown", now=NOW)
    assert not first["spawned"]
    assert "timed out" in first["why"]

    monkeypatch.setattr(repair.subprocess, "run", lambda cmd, **kw: _Ok())
    again = repair.run("digest-week", "boom", "unknown", now=NOW)
    assert not again["spawned"], "a crashed look must not be retried all day"


# ── The boundaries, which are flags and not sentences ────────────────────────

def test_the_session_cannot_write_to_the_plugins_own_source(spawns):
    """Rule 1 of the agent file, enforced by the absence of a flag.

    The working directory is the vault and the only --add-dir is the state
    directory, so the plugin root is not writable however the model reads its
    instructions. A prompt instruction is necessary and never sufficient."""
    repair.run("digest-week", "boom", "unknown", now=NOW)
    cmd = spawns[0]["cmd"]
    plugin_root = str(config_loader.repo_root())
    added = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--add-dir"]
    assert plugin_root not in added
    assert all(plugin_root != d for d in added)
    assert spawns[0]["cwd"] != plugin_root


def test_the_agent_file_is_the_sessions_own_definition(spawns):
    """--agent, not a prompt that describes an agent. The rules then arrive as
    the session's system prompt rather than as advice inside a user turn the
    model can weigh against something else."""
    repair.run("digest-week", "boom", "unknown", now=NOW)
    cmd = spawns[0]["cmd"]
    assert "--agent" in cmd
    assert cmd[cmd.index("--agent") + 1] == repair.AGENT


def test_the_agent_name_actually_resolves(spawns):
    """The flag being present is not the same as the name being findable.

    This is the test whose absence let a broken spawn ship. Agents under a
    plugin's `agents/` folder are discovered only when the plugin is LOADED,
    and a session spawned from a script loads nothing, so `--agent
    van-gogh-mechanic` alone exits 1 with "not found" and lists only the
    built-ins. Every guarantee written into the agent file is worth nothing if
    the file is never read.

    So this asserts the loading flag is there AND that it names a directory
    which really contains the agent definition. Mutation: delete the
    `--plugin-dir` pair from `_command` and this fails."""
    repair.run("digest-week", "boom", "unknown", now=NOW)
    cmd = spawns[0]["cmd"]
    assert "--plugin-dir" in cmd, (
        "without it the CLI cannot find the agent and exits 1")
    root = repair.Path(cmd[cmd.index("--plugin-dir") + 1])
    assert (root / "agents" / f"{repair.AGENT}.md").is_file(), (
        f"--plugin-dir points at {root}, which holds no "
        f"agents/{repair.AGENT}.md")


def test_loading_the_plugin_does_not_make_its_source_writable(spawns):
    """The two flags do different jobs and must not be confused.

    `--plugin-dir` decides what the session can FIND. The working directory
    and `--add-dir` decide what it can WRITE. Passing the plugin root to the
    first must not smuggle it into the second, or rule 1 of the agent file
    stops being enforced by anything."""
    repair.run("digest-week", "boom", "unknown", now=NOW)
    cmd = spawns[0]["cmd"]
    plugin_root = cmd[cmd.index("--plugin-dir") + 1]
    added = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--add-dir"]
    assert plugin_root not in added
    assert spawns[0]["cwd"] != plugin_root


def test_slash_commands_are_disabled(spawns):
    """A diagnosis that invoked a briefing skill would re-enter the machinery
    it was called to inspect."""
    repair.run("digest-week", "boom", "unknown", now=NOW)
    assert "--disable-slash-commands" in spawns[0]["cmd"]


def test_a_broken_install_still_gets_looked_at(monkeypatch, tmp_path):
    """The switch must fail toward ON.

    Reading it needs the config, which needs the vault pointer. A machine
    whose install never finished has neither, and that is one of the faults
    most worth being called about, so an unreadable config must not read as
    "switched off"."""
    monkeypatch.setattr(repair, "state_path", lambda: tmp_path / "repair.json")
    monkeypatch.setattr(repair, "repairs_dir", lambda: tmp_path / "repairs")
    monkeypatch.setattr(repair, "claude_bin", lambda: "/stub/claude")
    monkeypatch.setattr(repair.subprocess, "run", lambda cmd, **kw: _Ok())

    def no_config():
        raise FileNotFoundError("Vault pointer not found")

    monkeypatch.setattr(config_loader, "repair_agent_enabled", no_config)
    assert repair.run("digest-week", "boom", "unknown", now=NOW)["spawned"]


def test_the_switch_turns_it_off(monkeypatch, spawns):
    monkeypatch.setattr(config_loader, "repair_agent_enabled", lambda: False)
    out = repair.run("digest-week", "boom", "unknown", now=NOW)
    assert not out["spawned"]
    assert "off" in out["why"]
    assert not spawns


# ── When the watcher escalates, and when it must not ─────────────────────────

def _row(status="failed", why="", error_class="unknown", detail="TypeError: x"):
    return {"job": "digest-week", "title": "your Week briefing",
            "status": status, "_why_not": why, "error_class": error_class,
            "detail": detail}


@pytest.fixture
def looks(monkeypatch):
    """Record what the watcher asks the mechanic to look at."""
    calls = []
    monkeypatch.setattr(repair, "run",
                        lambda job, detail="", error_class="", now=None,
                        claude=None, dry_run=False:
                        (calls.append({"job": job, "class": error_class})
                         or {"spawned": True, "why": "looked at it"}))
    return calls


GAVE_UP = ("was restarted three times today and still did not work, so it "
           "has stopped trying for today. ")
SAME_TWICE = ("failed twice with the same error, so restarting it again will "
              "not help and it has stopped trying for today. ")


def test_a_job_still_being_retried_is_not_escalated(looks):
    """Below the cap the watcher is still re-running it, and a re-run is free.
    Looking here would cost a model call to conclude "try again"."""
    job_watch._escalate([_row(why="")], NOW)
    assert not looks


@pytest.mark.parametrize("why", [GAVE_UP, SAME_TWICE])
def test_both_give_up_sentences_escalate(looks, why):
    """The two ways the watcher gives up are the two ways in. They are matched
    rather than re-derived so the pair cannot disagree about what stuck means."""
    job_watch._escalate([_row(why=why)], NOW)
    assert [c["job"] for c in looks] == ["digest-week"]


@pytest.mark.parametrize("klass", ["auth", "quota", "version"])
def test_the_classes_that_release_themselves_are_never_escalated(looks, klass):
    """Each of these is either a clock or a login.

    A usage cap resets on its own. A stale CLI clears once the binary lands.
    An expired login has nothing to diagnose: the watcher's own sentence
    already names the fix and only the user can do it. Spending a model call
    on any of the three buys nothing."""
    job_watch._escalate([_row(why=GAVE_UP, error_class=klass)], NOW)
    assert not looks


def test_the_escalation_never_raises(monkeypatch):
    """Fails open like every other path here: the worst thing it may do is
    nothing, because a watcher that can wedge a briefing is worse than none."""
    def boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(repair, "run", boom)
    assert job_watch._escalate([_row(why=GAVE_UP)], NOW) == []


def test_the_tick_records_what_it_looked_at_and_what_it_declined(tmp_path,
                                                                 monkeypatch):
    """A decision NOT to look is recorded too.

    "already looked at this today" in the log is the difference between a
    budget working and a feature that silently never fires again."""
    log = tmp_path / "job_watch.jsonl"
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: log)
    rows = [_row(why=GAVE_UP)]
    rows[0]["_looked"] = "already looked at this today"
    job_watch.record_tick(rows, [], NOW, looked=["your Week briefing"])

    import json
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["looked"] == ["your Week briefing"]
    assert record["looked_why"]["your Week briefing"] == "already looked at this today"
