"""Research that runs while setup finishes: what it may touch, and when.

Three properties are worth more than the rest of this file, and all three are
about restraint rather than capability.

**It cannot reach the user's notes.** The session's working directory is a
staging folder under logs/ and no `--add-dir` is passed at all, so `wiki/` is
not writable by it. A test asserts the absence of that flag, because the
absence is the guarantee.

**A fact without a working source stops being a fact.** The agent is told to
cite everything and the model will sometimes cite a page it did not open. The
demotion to inference is therefore code, and the test plants an uncited fact
rather than asserting on a clean one: a validator proven only against good
input is a validator nobody has tested.

**Nothing is filed without a person.** `apply` is a separate command, refuses
an unconfirmed identity, and never overwrites what a user has written.

Nothing here spawns anything. Both `subprocess.Popen` and `subprocess.run` are
replaced, and so is the `claude` binary lookup: resolving the CLI happens
while the command is being BUILT, before the stubbed spawn is reached, so a
machine with no Claude Code on its PATH (every CI runner) would fail these
assertions for a reason that has nothing to do with the product.
"""

import json
import subprocess
from pathlib import Path

import pytest

import config_loader
import operator_research as orx

TODAY = "2026-09-10"

GOOD = {
    "identity": {
        "full_name": "Nico Johnson", "company": "Suncast", "confidence": "high",
        "anchor_evidence": [{"text": "Named as founder on the company site",
                             "source_url": "https://suncast.example/about"}],
    },
    "person": {
        "title": "Founder and CEO", "summary": "Runs a solar media business.",
        "facts": [{"text": "Hosts a solar industry podcast",
                   "source_url": "https://suncast.example/podcast"}],
    },
    "company": {
        "summary": "Media and advisory for solar developers.",
        "facts": [{"text": "Founded in 2018",
                   "source_url": "https://suncast.example/about"}],
        "industry_terms": ["solar", "interconnection"],
    },
    "persona": {
        "themes": ["Talks about the people side of energy"],
        "style": "Warm, direct, short paragraphs.",
        "inferred_priorities": [{"name": "Grow the audience", "detail": ""}],
    },
    "proposals": {
        "role_description": "solar media founder, advisory, podcasting",
        "keywords": ["suncast", "solar", "podcast"],
        "priorities": [
            {"name": "Grow the audience", "detail": "More listeners per episode."},
            {"name": "Land two advisory clients", "detail": ""},
            {"name": "Hire an editor", "detail": ""},
        ],
    },
    "sources": [
        {"url": "https://suncast.example/about", "title": "About", "used_for": "identity"},
        {"url": "https://suncast.example/podcast", "title": "Podcast", "used_for": "person"},
    ],
    "dossier_md": "## Who he is\n\nA solar media founder.",
}


@pytest.fixture
def staged(monkeypatch, tmp_path):
    """A research root under tmp, with the good proposal already written."""
    root = tmp_path / "operator-research"
    monkeypatch.setattr(orx, "research_root", lambda: root)
    slug = orx._slug("Suncast", "Nico Johnson")
    (root / slug).mkdir(parents=True)
    (root / slug / "proposal.json").write_text(json.dumps(GOOD), encoding="utf-8")
    (root / slug / "status.json").write_text(
        json.dumps({"state": orx.DONE, "slug": slug, "started": "2026-09-10T09:00:00",
                    "finished": "2026-09-10T09:06:00", "pid": 1}), encoding="utf-8")
    return slug


@pytest.fixture
def spawns(monkeypatch, tmp_path):
    """Record every spawn instead of making one. Returns the list."""
    calls = []

    class _Proc:
        pid = 4321
        returncode = 0
        stdout = "done"
        stderr = ""

    def fake_popen(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return _Proc()

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "cwd": kwargs.get("cwd"), "kwargs": kwargs})
        return _Proc()

    monkeypatch.setattr(orx.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(orx.subprocess, "run", fake_run)
    # Resolved while building argv, before the stubs above are reached.
    monkeypatch.setattr(orx, "claude_bin", lambda: "/stub/claude")
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "research")
    return calls


# ── The boundaries, which are flags rather than sentences ────────────────────

def test_the_agent_file_is_the_sessions_own_definition(spawns):
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    orx._run(orx._slug("Suncast", "Nico Johnson"), claude="/stub/claude")
    cmd = [c for c in spawns if "--agent" in c["cmd"]][0]["cmd"]
    assert cmd[cmd.index("--agent") + 1] == "operator-research"


def test_the_agent_name_actually_resolves(spawns):
    """--plugin-dir must name the directory that really holds the agent file.

    Without the flag the CLI exits 1 with "--agent not found" and every
    guarantee in the agent file is worth nothing, because the file is never
    read. Asserting the flag is present is not enough: it has to point at a
    directory where agents/<name>.md actually exists.
    """
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    orx._run(orx._slug("Suncast", "Nico Johnson"), claude="/stub/claude")
    cmd = [c for c in spawns if "--agent" in c["cmd"]][0]["cmd"]
    assert "--plugin-dir" in cmd
    named = Path(cmd[cmd.index("--plugin-dir") + 1])
    assert (named / "agents" / "operator-research.md").is_file()


def test_the_session_cannot_reach_the_users_notes(spawns):
    """No --add-dir at all, and the cwd is the staging folder.

    This is the whole containment story: the model may write where it was
    started and nowhere else, so wiki/ is out of reach whatever it decides.
    """
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    slug = orx._slug("Suncast", "Nico Johnson")
    orx._run(slug, claude="/stub/claude")
    call = [c for c in spawns if "--agent" in c["cmd"]][0]
    assert "--add-dir" not in call["cmd"]
    assert Path(call["cwd"]) == orx.staging_dir(slug)


def test_slash_commands_are_disabled(spawns):
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    orx._run(orx._slug("Suncast", "Nico Johnson"), claude="/stub/claude")
    cmd = [c for c in spawns if "--agent" in c["cmd"]][0]["cmd"]
    assert "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_the_background_start_is_detached_and_windowless(spawns):
    """The child outlives the tool call that started it, and flashes nothing.

    Setup starts this and returns immediately; a child tied to that process
    group would die with it. NO_WINDOW by the exact name, per the AST rule in
    test_no_console_window.py.
    """
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    kw = spawns[0]["kwargs"]
    assert kw.get("start_new_session") is True
    assert kw.get("creationflags") == orx.NO_WINDOW
    assert kw.get("stdin") == subprocess.DEVNULL


def test_the_prompt_carries_the_anchor_and_no_secrets(spawns):
    orx.start("Nico Johnson", "Suncast", domains="suncast.com",
              role="CEO", city="Austin", claude="/stub/claude")
    orx._run(orx._slug("Suncast", "Nico Johnson"), claude="/stub/claude")
    prompt = [c for c in spawns if "--agent" in c["cmd"]][0]["cmd"][2]
    for expected in ("Nico Johnson", "Suncast", "suncast.com", "CEO", "Austin"):
        assert expected in prompt
    assert "REFRESH_TOKEN" not in prompt and "client_secret" not in prompt


def test_the_persons_name_stays_out_of_the_process_list(spawns):
    """The child re-reads its inputs from disk rather than taking them on argv.

    `ps` output is readable by every local user, so a name and a company on a
    command line is a small leak that costs nothing to avoid.
    """
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    child = spawns[0]["cmd"]
    assert "Nico Johnson" not in " ".join(child)
    assert child[2] == "_run"


# ── Status: a state nobody set is not evidence of a state ────────────────────

def test_nothing_started_reads_as_nothing_started(monkeypatch, tmp_path):
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "empty")
    assert orx.status()["state"] == orx.NOT_STARTED


def test_a_live_run_reads_as_running(monkeypatch, tmp_path):
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "r")
    slug = "x"
    orx._write_status(slug, {"state": orx.RUNNING, "slug": slug,
                             "started": "2026-09-10T09:00:00", "pid": 1})
    monkeypatch.setattr(orx, "_pid_alive", lambda pid: True)
    assert orx.status(slug)["state"] == orx.RUNNING


def test_a_session_that_died_is_not_still_running(monkeypatch, tmp_path):
    """A killed session never writes its own ending.

    Reporting `running` forever because nobody set `failed` is the empty-set
    pass: the absence of a result is not a result. The dead pid is what turns
    it into the truth.
    """
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "r")
    slug = "x"
    orx._write_status(slug, {"state": orx.RUNNING, "slug": slug,
                             "started": "2026-09-10T09:00:00", "pid": 999999})
    monkeypatch.setattr(orx, "_pid_alive", lambda pid: False)
    out = orx.status(slug)
    assert out["state"] == orx.FAILED
    assert "stopped" in out["reason"]


def test_a_truncated_proposal_is_a_failure_not_a_result(monkeypatch, tmp_path, spawns):
    """Plant the broken file, do not just assert on a good one."""
    slug = orx._slug("Suncast", "Nico Johnson")
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    orx.staging_dir(slug).mkdir(parents=True, exist_ok=True)
    (orx.staging_dir(slug) / "proposal.json").write_text(
        '{"identity": {"full_na', encoding="utf-8")
    rc = orx._run(slug, claude="/stub/claude")
    assert rc == 1
    assert orx.status(slug)["state"] == orx.FAILED


def test_a_session_that_wrote_nothing_is_a_failure(spawns):
    slug = orx._slug("Suncast", "Nico Johnson")
    orx.start("Nico Johnson", "Suncast", claude="/stub/claude")
    rc = orx._run(slug, claude="/stub/claude")
    assert rc == 1
    assert "wrote nothing" in orx.status(slug)["reason"]


def test_a_missing_cli_fails_the_run_not_the_install(monkeypatch, tmp_path):
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "r")

    def boom():
        raise FileNotFoundError("claude")

    monkeypatch.setattr(orx, "claude_bin", boom)
    out = orx.start("Nico Johnson", "Suncast")
    assert out["ok"] is False
    assert "PATH" in out["why"]


# ── Validation: an uncited fact is demoted, never laundered ──────────────────

def test_a_fact_whose_source_was_never_read_becomes_inference():
    """Plant one uncited fact beside a cited one and check where each lands."""
    raw = json.loads(json.dumps(GOOD))
    raw["person"]["facts"].append(
        {"text": "Sits on four public boards",
         "source_url": "https://nowhere.example/invented"})
    clean = orx._validate(raw)
    kept = [f["text"] for f in clean["person"]["facts"]]
    assert "Hosts a solar industry podcast" in kept
    assert "Sits on four public boards" not in kept
    assert any("Unverified: Sits on four public boards" == t
               for t in clean["persona"]["themes"])
    assert clean["unsourced"] == 1


def test_a_proposal_with_no_identity_is_refused():
    with pytest.raises(ValueError):
        orx._validate({"person": {"facts": []}})


def test_an_unknown_confidence_reads_as_low():
    raw = json.loads(json.dumps(GOOD))
    raw["identity"]["confidence"] = "pretty sure"
    assert orx._validate(raw)["identity"]["confidence"] == "low"


def test_dashes_never_survive_validation():
    raw = json.loads(json.dumps(GOOD))
    raw["person"]["summary"] = "Runs a business — a good one – mostly."
    clean = orx._validate(raw)
    assert "—" not in clean["person"]["summary"]
    assert "–" not in clean["person"]["summary"]


# ── apply: the only path that writes what a user reads ───────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A scaffolded vault, with config.json, index and log, under tmp."""
    root = tmp_path / "vault"
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "van-gogh" / "projects" / "Project Van Gogh").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "# Wiki Index\n\n## Sources\n\n## Entities\n\n## Projects\n",
        encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# Operation Log\n\n", encoding="utf-8")
    (root / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md").write_text(
        "# Memory\n\n## Active stack\n\nStuff.\n\n## Pending follow-ups\n\n- one\n",
        encoding="utf-8")
    config = {
        "user": {"full_name": "Nico Johnson", "first_name": "Nico",
                 "role_description": ""},
        "obsidian": {"vault_path": str(root), "sources_relpath": "wiki/sources",
                     "entities_relpath": "wiki/entities"},
        "businesses": [
            {"tag": "personal", "display_name": "Personal", "keywords": [],
             "priorities": []},
            {"tag": "suncast", "display_name": "Suncast", "keywords": ["Solar"],
             "priorities": []},
        ],
    }
    (root / "van-gogh" / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_loader, "_config", config)
    return root


def test_apply_writes_the_pages_the_briefings_read(staged, vault):
    out = orx.apply(staged, priorities=[GOOD["proposals"]["priorities"][0]],
                    today=TODAY)
    assert out["ok"], out
    assert (vault / "wiki" / "entities" / "Nico Johnson.md").is_file()
    assert (vault / "wiki" / "entities" / "Suncast.md").is_file()
    source = vault / "wiki" / "sources" / f"Operator research Suncast {TODAY}.md"
    assert source.is_file(), sorted(p.name for p in (vault / "wiki" / "sources").iterdir())
    body = source.read_text(encoding="utf-8")
    assert "https://suncast.example/about" in body
    index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[Nico Johnson]]" in index and "[[Suncast]]" in index
    log = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "operator research" in log


def test_a_sourced_fact_is_a_fact_and_an_uncited_one_is_labelled(staged, vault):
    raw = json.loads(json.dumps(GOOD))
    raw["person"]["facts"].append({"text": "Sits on four public boards",
                                   "source_url": "https://nowhere.example/x"})
    (orx.staging_dir(staged) / "proposal.json").write_text(
        json.dumps(raw), encoding="utf-8")
    orx.apply(staged, priorities=[], today=TODAY)
    page = (vault / "wiki" / "entities" / "Nico Johnson.md").read_text(encoding="utf-8")
    facts, inferred = page.split("## Inferred, unverified")
    assert "Hosts a solar industry podcast" in facts
    assert "Sits on four public boards" not in facts
    assert "Sits on four public boards" in inferred


def test_the_profile_loads_in_every_session(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh"
           / "memory.md").read_text(encoding="utf-8")
    assert orx.MEMORY_BEGIN in mem and "## Operator profile" in mem
    # Above the ledger, which is parsed by the follow-up radar: a block landing
    # inside that heading would be read as ledger entries.
    assert mem.index(orx.MEMORY_END) < mem.index("## Pending follow-ups")


def test_running_it_twice_leaves_one_profile_and_one_index_line(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    orx.apply(staged, priorities=[], today=TODAY)
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh"
           / "memory.md").read_text(encoding="utf-8")
    assert mem.count(orx.MEMORY_BEGIN) == 1
    assert mem.count("## Operator profile") == 1
    index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    assert index.count("- [[Nico Johnson]]") == 1
    log = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("] operator research |") == 1


def test_a_second_run_refreshes_the_page_instead_of_duplicating_it(staged, vault):
    """Found by reading a real page, not by a gate.

    The first apply writes a whole page carrying no marker. A guard that only
    asked whether the marker was present therefore saw none on the second run
    and appended a second copy of every section, so the page grew a duplicate
    Overview, Key Facts and Inferred block each time it ran.
    """
    orx.apply(staged, priorities=[], today=TODAY)
    orx.apply(staged, priorities=[], today=TODAY)
    orx.apply(staged, priorities=[], today=TODAY)
    page = (vault / "wiki" / "entities" / "Nico Johnson.md").read_text(encoding="utf-8")
    assert page.count("## Key Facts") == 1
    assert page.count("## Overview") == 1
    assert page.count("Hosts a solar industry podcast") == 1


def test_a_page_the_user_has_written_on_is_appended_to(staged, vault):
    page = vault / "wiki" / "entities" / "Nico Johnson.md"
    page.write_text("---\ntype: entity\ntitle: Nico Johnson\n---\n\n"
                    "# Nico Johnson\n\nMet him at a conference. Good guy.\n",
                    encoding="utf-8")
    orx.apply(staged, priorities=[], today=TODAY)
    body = page.read_text(encoding="utf-8")
    assert "Met him at a conference" in body
    assert f"## Operator research ({TODAY})" in body


def test_only_the_priorities_the_user_picked_are_written(staged, vault):
    picked = [GOOD["proposals"]["priorities"][0],
              GOOD["proposals"]["priorities"][2]]
    out = orx.apply(staged, priorities=picked, today=TODAY)
    config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    bucket = [b for b in config["businesses"] if b["tag"] == "suncast"][0]
    names = [p["name"] for p in bucket["priorities"]]
    assert names == ["Grow the audience", "Hire an editor"]
    assert all(p["added"] == TODAY for p in bucket["priorities"])
    assert "Land two advisory clients" not in names
    assert out["config_changes"]["priorities_added"] == names


def test_no_priorities_picked_writes_none(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    bucket = [b for b in config["businesses"] if b["tag"] == "suncast"][0]
    assert bucket["priorities"] == []


def test_keywords_merge_without_case_duplicates(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    bucket = [b for b in config["businesses"] if b["tag"] == "suncast"][0]
    lowered = [k.lower() for k in bucket["keywords"]]
    assert len(lowered) == len(set(lowered))
    assert "Solar" in bucket["keywords"] and "solar" not in bucket["keywords"]
    assert "suncast" in bucket["keywords"]


def test_a_role_description_they_already_wrote_is_never_overwritten(staged, vault):
    config_loader._config["user"]["role_description"] = "whatever I said at setup"
    raw = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    raw["user"]["role_description"] = "whatever I said at setup"
    (vault / "van-gogh" / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    out = orx.apply(staged, priorities=[], today=TODAY)
    config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert config["user"]["role_description"] == "whatever I said at setup"
    assert "role_description" not in out["config_changes"]


def test_an_empty_role_description_is_filled(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    config = json.loads((vault / "van-gogh" / "config.json").read_text(encoding="utf-8"))
    assert config["user"]["role_description"] == GOOD["proposals"]["role_description"]


def test_settings_are_backed_up_before_they_are_changed(staged, vault):
    orx.apply(staged, priorities=[], today=TODAY)
    backup = vault / "van-gogh" / "config.json.bak"
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["user"]["role_description"] == ""


def test_an_unconfirmed_identity_files_nothing(staged, vault):
    raw = json.loads(json.dumps(GOOD))
    raw["identity"]["confidence"] = "low"
    (orx.staging_dir(staged) / "proposal.json").write_text(
        json.dumps(raw), encoding="utf-8")
    out = orx.apply(staged, priorities=[], today=TODAY)
    assert out["ok"] is False
    assert not (vault / "wiki" / "entities" / "Nico Johnson.md").exists()
    assert not (vault / "van-gogh" / "config.json.bak").exists()


def test_force_files_an_unconfirmed_identity_anyway(staged, vault):
    raw = json.loads(json.dumps(GOOD))
    raw["identity"]["confidence"] = "low"
    (orx.staging_dir(staged) / "proposal.json").write_text(
        json.dumps(raw), encoding="utf-8")
    assert orx.apply(staged, priorities=[], force=True, today=TODAY)["ok"]
    assert (vault / "wiki" / "entities" / "Nico Johnson.md").is_file()


def test_nothing_written_carries_a_dash(staged, vault):
    """Proven on a planted dash first, so a passing scan means something."""
    raw = json.loads(json.dumps(GOOD))
    raw["person"]["summary"] = "Runs a business — a good one."
    raw["proposals"]["priorities"][0]["detail"] = "More – listeners."
    (orx.staging_dir(staged) / "proposal.json").write_text(
        json.dumps(raw), encoding="utf-8")
    out = orx.apply(staged, priorities=orx.read_proposal(staged)["proposals"]["priorities"],
                    today=TODAY)
    assert out["ok"]
    for path in out["written"]:
        body = Path(path).read_text(encoding="utf-8")
        assert "—" not in body, path
        assert "–" not in body, path


def test_the_scan_above_can_actually_see_a_dash(tmp_path):
    """The control for the test above: prove the assertion is not vacuous."""
    planted = tmp_path / "planted.md"
    planted.write_text("a — b", encoding="utf-8")
    assert "—" in planted.read_text(encoding="utf-8")


def test_show_renders_a_summary_a_person_can_answer(staged, vault):
    out = orx.show(staged)
    assert out["ok"]
    md = out["summary_md"]
    assert "Nico Johnson" in md and "high" in md
    assert "0. Grow the audience" in md
    assert "—" not in md and "–" not in md


def test_show_says_so_when_there_is_nothing_to_show(monkeypatch, tmp_path):
    monkeypatch.setattr(orx, "research_root", lambda: tmp_path / "nope")
    assert orx.show()["ok"] is False


# ── Sentences a person reads, found by reading them ──────────────────────────
#
# Both of these came out of a cold read of a real run, not out of a gate. The
# pages were correct and every fact carried its link; the sentences were still
# wrong in a way no assertion existed for, which is the failure mode a gate
# suite cannot express. They are tests now so they cannot come back.

@pytest.mark.parametrize("title,company,expected", [
    # The live defect: the model's title already carries the company, and
    # appending "at <company>" named it twice in nine words.
    ("Founder, SunCast Media and host of the SunCast podcast", "SunCast Media",
     "**Nico Johnson**, Founder, SunCast Media and host of the SunCast podcast."),
    ("Founder and CEO", "SunCast Media",
     "**Nico Johnson**, Founder and CEO at SunCast Media."),
    ("Partner", "", "**Nico Johnson**, Partner."),
    ("", "Acme", "**Nico Johnson** at Acme."),
    ("", "", "**Nico Johnson**, role and company not confirmed."),
])
def test_the_identity_line_never_says_the_company_twice(title, company, expected):
    ident = {"full_name": "Nico Johnson", "company": company}
    assert orx._identity_line(ident, title) == expected


def test_the_identity_line_is_the_same_sentence_in_both_places(staged, vault):
    """One helper, two callers, so the summary and the profile cannot drift."""
    orx.apply(staged, priorities=[], today=TODAY)
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh"
           / "memory.md").read_text(encoding="utf-8")
    summary = orx.show(staged)["summary_md"]
    line = orx._identity_line(GOOD["identity"], GOOD["person"]["title"])
    assert line in mem and line in summary


def test_an_inference_is_not_labelled_twice(staged, vault):
    """The heading already says inferred; the model labels it again."""
    raw = json.loads(json.dumps(GOOD))
    raw["persona"]["style"] = "Inference. Conversational and direct."
    (orx.staging_dir(staged) / "proposal.json").write_text(
        json.dumps(raw), encoding="utf-8")
    orx.apply(staged, priorities=[], today=TODAY)
    page = (vault / "wiki" / "entities" / "Nico Johnson.md").read_text(encoding="utf-8")
    assert "Writes and speaks: Conversational and direct." in page
    assert "Inference." not in page
    mem = (vault / "van-gogh" / "projects" / "Project Van Gogh"
           / "memory.md").read_text(encoding="utf-8")
    assert "**How they write.** Conversational and direct." in mem


def test_a_style_note_without_the_label_is_left_alone():
    assert orx._unhedge("Warm and direct.") == "Warm and direct."


# ── The agent file itself ────────────────────────────────────────────────────

def test_the_agent_file_parses_and_cannot_run_commands():
    """The tool list is the containment, so it is asserted rather than trusted.

    Parsed without PyYAML on purpose: it is not in requirements.txt, so a
    yaml-gated version of this test would silently skip on exactly the
    machines (CI, a fresh install) where nobody is watching. A guard that can
    be skipped is not a guard.
    """
    path = Path(__file__).resolve().parent.parent / "agents" / "operator-research.md"
    text = path.read_text(encoding="utf-8")
    front = text.split("---")[1]
    fields = {}
    for line in front.splitlines():
        if line[:1].isspace() or ": " not in line:
            continue
        key, value = line.split(": ", 1)
        fields[key.strip()] = value.strip()
    assert fields.get("name") == "operator-research"
    tools = [t.strip() for t in fields.get("tools", "").split(",")]
    assert "Bash" not in tools
    assert "WebSearch" in tools and "WebFetch" in tools
    assert "\u2014" not in text and "\u2013" not in text


def test_the_agent_file_is_still_valid_yaml_where_yaml_exists():
    """Belt and braces: when PyYAML is installed, check the whole block.

    The hand parser above covers the fields that carry the guarantees. This
    catches the shape that has broken shipped skills before, an unquoted
    description with a bare colon in it, which only a real parser sees.
    """
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parent.parent / "agents" / "operator-research.md"
    meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
    assert isinstance(meta, dict) and meta.get("name") == "operator-research"
