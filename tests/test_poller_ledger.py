"""The pollers write down that they ticked, including on the quiet ticks.

A clock job is graded by its slot and its deliverable. A poller has neither: on
a day with no calls the prep poller correctly sends nothing and leaves no file,
so the only evidence it is still alive is a run row saying it ran.

Both pollers were missing that row, in different ways and with the same effect:

* `prep_send` never called `run_ledger` at all, while `job_watch.roster`
  declared `ledger_job: "prep.email"`. It graded `no_history` forever.
* `note_send` recorded only when a Note actually fired, which is the one case
  where the poller is obviously alive. A quiet tick -- the normal case -- wrote
  nothing, so a dead poller and a quiet afternoon were indistinguishable.

The test that matters is the quiet one. A tick that sends something and records
it proves nothing about the failure this guards against.
"""

import pathlib
import tempfile

import pytest

import job_registry
import run_ledger


@pytest.fixture
def ledger(monkeypatch):
    """A throwaway runs.jsonl, so nothing here touches the real one."""
    logs = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(run_ledger.config_loader, "logs_dir", lambda: logs)
    return logs


def _jobs_in(rows):
    return [row["job"] for row in rows]


# ── The prep poller ──────────────────────────────────────────────────────────

def test_a_quiet_prep_tick_still_records(ledger, monkeypatch):
    """Nothing due is the normal case, and it must leave evidence."""
    import prep_send

    monkeypatch.setattr(prep_send.config_loader, "prep_email_enabled", lambda: True)
    monkeypatch.setattr(prep_send.config_loader, "prep_mode", lambda: "each")
    monkeypatch.setattr(prep_send, "_events", lambda: [])

    assert prep_send.main([]) == 0
    assert prep_send.TICK_JOB_NAME in _jobs_in(run_ledger.read_runs())


def test_a_failing_prep_tick_records_the_failure(ledger, monkeypatch, capsys):
    """A tick that died must be distinguishable from one that had no work."""
    import prep_send

    monkeypatch.setattr(prep_send.config_loader, "prep_email_enabled", lambda: True)
    monkeypatch.setattr(prep_send.config_loader, "prep_mode", lambda: "each")

    def boom():
        raise RuntimeError("calendar unreachable")

    monkeypatch.setattr(prep_send, "_events", boom)

    assert prep_send.main([]) == 0          # the poller never wedges
    rows = run_ledger.read_runs()
    assert [r["rc"] for r in rows if r["job"] == prep_send.TICK_JOB_NAME] == [1]


def test_a_prep_dry_run_records_nothing(ledger, monkeypatch):
    """A person looking is not the poller running.

    Recording a dry run would make a hand-invoked `--dry-run` look like
    evidence of liveness, which is the opposite of what the row is for.
    """
    import prep_send

    monkeypatch.setattr(prep_send.config_loader, "prep_email_enabled", lambda: True)
    monkeypatch.setattr(prep_send.config_loader, "prep_mode", lambda: "each")
    monkeypatch.setattr(prep_send, "_events", lambda: [])

    assert prep_send.main(["--dry-run"]) == 0
    assert run_ledger.read_runs() == []


# ── The Note tick ────────────────────────────────────────────────────────────

def test_a_quiet_note_tick_still_records(ledger, monkeypatch):
    """The per-event note.<kind> rows are silent exactly when all is well."""
    import note_send

    monkeypatch.setattr(note_send, "tick",
                        lambda claude=None, dry_run=False: {
                            "fired": [], "folded": [], "held": 0})

    assert note_send.main([]) == 0
    assert note_send.TICK_JOB_NAME in _jobs_in(run_ledger.read_runs())


def test_a_failing_note_tick_records_the_failure(ledger, monkeypatch, capsys):
    import note_send

    def boom(claude=None, dry_run=False):
        raise RuntimeError("inbox unreachable")

    monkeypatch.setattr(note_send, "tick", boom)

    assert note_send.main([]) == 0
    rows = run_ledger.read_runs()
    assert [r["rc"] for r in rows if r["job"] == note_send.TICK_JOB_NAME] == [1]


def test_a_note_dry_run_records_nothing(ledger, monkeypatch):
    import note_send

    monkeypatch.setattr(note_send, "tick",
                        lambda claude=None, dry_run=False: {
                            "fired": [], "folded": [], "held": 0,
                            "watching": 0, "inbound": 0, "meeting_now": None})

    assert note_send.main(["--dry-run"]) == 0
    assert run_ledger.read_runs() == []


# ── The invariant ────────────────────────────────────────────────────────────

def test_every_registry_row_names_a_ledger_job_its_script_writes():
    """A row's `ledger_job` must be a name its own script actually records.

    This is the clause that would have caught the prep bug on the day it was
    written: the roster declared `prep.email` while nothing anywhere wrote that
    string, so the job graded `no_history` forever while working fine.

    Two spellings count, because both are real. A script may hold the name in a
    module constant (`prep.tick`, `contact.capture`), or build it with an
    f-string over the argument that distinguishes one job from another
    (`scheduled.{skill}`, `digest.{briefing}`). The f-string form is checked by
    reconstructing the prefix rather than by looking for the finished string,
    which no source file contains.
    """
    app = pathlib.Path(__file__).resolve().parent.parent / "app"
    for job in job_registry.jobs():
        if not job.ledger_job:
            continue
        src = (app / job.script).read_text(encoding="utf-8")
        literal = f'"{job.ledger_job}"' in src or f"'{job.ledger_job}'" in src
        # `digest.morning-coffee` is written as f"digest.{briefing}"; the part
        # before the first dot is what appears in the source.
        prefix = job.ledger_job.split(".", 1)[0]
        built = f'f"{prefix}.{{' in src
        assert literal or built, (
            f"{job.key}: roster grades ledger_job={job.ledger_job!r} but "
            f"{job.script} never writes that name, so the job grades "
            f"no_history forever")


def test_the_ledger_name_check_rejects_a_name_nothing_writes():
    """MUTATION CONTROL for the test above.

    Without this, the two-spelling rule could pass for everything by being too
    permissive, which is exactly the shape of the bug it exists to catch. A row
    naming a ledger job its script never writes must fail.
    """
    app = pathlib.Path(__file__).resolve().parent.parent / "app"
    src = (app / "prep_send.py").read_text(encoding="utf-8")
    invented = "prep.email"          # the name the roster used to declare
    assert f'"{invented}"' not in src and f"'{invented}'" not in src
    assert f'f"prep.{{' not in src, (
        "prep_send builds its ledger name with an f-string, so the control "
        "below proves nothing")


def test_the_skill_jobs_record_through_skill_run():
    """The two background skills record via skill_run, not in their own files.

    Stated separately so the invariant above is not quietly satisfied by a
    coincidence: `scheduled.<skill>` is built by an f-string in skill_run.py,
    which is the shared unattended entry point for both of them.
    """
    app = pathlib.Path(__file__).resolve().parent.parent / "app"
    src = (app / "skill_run.py").read_text(encoding="utf-8")
    assert 'f"scheduled.{parsed.skill}"' in src
    for job in job_registry.jobs():
        if job.script == "skill_run.py":
            assert job.ledger_job == f"scheduled.{job.key}"
