"""The golden corpus for Codex installs: what every scheduled job renders when
the install's client is Codex, frozen as literals.

Same contract as tests/test_scheduler_golden.py, which stays byte for byte
what it was before the Codex port (its hash is pinned below): a Claude
install's jobs never change because a Codex one exists. Under Codex the only
differences are the binary the `--claude` flag carries (the name stays, so
nothing downstream learns a new flag) and `VAN_GOGH_HOST=codex` in the
job's own environment, because launchd and Task Scheduler start from nothing
and the host file may be absent on a machine that only ever runs jobs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import job_registry
import scheduler_setup as s
import test_scheduler_golden as g

MAC = s._Ctx(g.MAC_REPO, g.MAC_PY, "/bin/codex", g.MAC_LOGS, "codex")
WIN = s._Ctx(g.WIN_REPO, g.WIN_PY, r"C:\bin\codex.exe", g.WIN_LOGS, "codex")

# sha256 of tests/test_scheduler_golden.py as the port merged into main
# (a40b99b). The Claude corpus is the floor this port stands on: the port
# itself never touched it (its base-commit hash was dd25cbf2...), and the one
# change since the base is main's own done-judge row (61cd796). A new Claude
# row moves this pin on purpose, and gets its Codex row below in the same
# change. Moved in 0.91.0 by meeting filing going hourly (a host-neutral
# cadence change; the Codex rows render from the same registry).
CLAUDE_GOLDEN_SHA256 = "98c74e693309cbacafff43004b875a08b3c34f1115b86cd1d0fba39aa0877f1d"


def test_the_claude_golden_corpus_is_untouched():
    text = (Path(__file__).resolve().parent / "test_scheduler_golden.py").read_bytes()
    # A Windows checkout may carry CRLF; the pin is over the file's content.
    assert hashlib.sha256(text.replace(b"\r\n", b"\n")).hexdigest() == CLAUDE_GOLDEN_SHA256


def test_default_context_is_claude_and_renders_as_before():
    ctx = s._Ctx(g.MAC_REPO, g.MAC_PY, g.MAC_CLAUDE, g.MAC_LOGS)
    assert ctx.host == "claude"
    job = g._row("meeting-ingest")
    assert s._job_command(job, ctx) == (
        'cd "/repo" && VAN_GOGH_SCHEDULED=1 "/venv/bin/python" '
        f'"{g._script(g.MAC_REPO, "skill_run.py")}" meeting-ingest --claude "/bin/claude"')
    assert "VAN_GOGH_HOST" not in s._job_command(job, ctx)
    assert "VAN_GOGH_HOST" not in s._inner_for(job, s._Ctx(g.WIN_REPO, g.WIN_PY, g.WIN_CLAUDE, g.WIN_LOGS))


SKILL_ROWS = ("meeting-ingest", "ingest-workspace", "calendar-stub-check",
              "relationship-radar", "five-fifteen", "voice-calibration", "voice-generator")
DIGEST_ROWS = ("morning-coffee", "afternoon-tea", "week", "week-retro")
SENDER_ROWS = {"finance-email": "finance_send.py", "prep-email": "prep_send.py",
               "note": "note_send.py", "done-judge": "done_judge.py"}
# A sender row's own arguments, when it has any (the check-off runs live).
SENDER_TAILS = {"done-judge": " --live"}


def _mac(script, tail):
    return (f'cd "/repo" && VAN_GOGH_SCHEDULED=1 VAN_GOGH_HOST=codex "/venv/bin/python" '
            f'"{g._script(g.MAC_REPO, script)}"{tail} --claude "/bin/codex"')


def _win(script, tail, key):
    return (f'set VAN_GOGH_SCHEDULED=1&& set VAN_GOGH_HOST=codex&& "C:\\venv\\python.exe" '
            f'"{g._script(g.WIN_REPO, script)}"{tail} --claude "C:\\bin\\codex.exe" '
            f'1>> "{g._log(g.WIN_LOGS, key + ".log")}" 2>> "{g._log(g.WIN_LOGS, key + ".err")}"')


@pytest.mark.parametrize("key", SKILL_ROWS)
def test_skill_rows_under_codex(key):
    job = g._row(key)
    assert job.needs_claude
    assert s._job_command(job, MAC) == _mac("skill_run.py", f" {key}")
    assert s._inner_for(job, WIN) == _win("skill_run.py", f" {key}", key)


@pytest.mark.parametrize("briefing", DIGEST_ROWS)
def test_digest_rows_under_codex(briefing):
    job = g._row(f"digest-{briefing}")
    assert job.needs_claude
    assert s._job_command(job, MAC) == _mac("digest_send.py", f" {briefing}")
    assert s._inner_for(job, WIN) == _win("digest_send.py", f" {briefing}", f"digest-{briefing}")


@pytest.mark.parametrize("key,script", sorted(SENDER_ROWS.items()))
def test_sender_rows_under_codex(key, script):
    job = g._row(key)
    assert job.needs_claude
    tail = SENDER_TAILS.get(key, "")
    assert s._job_command(job, MAC) == _mac(script, tail)
    assert s._inner_for(job, WIN) == _win(script, tail, key)


def test_every_client_spawning_row_is_frozen_here():
    frozen = set(SKILL_ROWS) | {f"digest-{b}" for b in DIGEST_ROWS} | set(SENDER_ROWS)
    live = {j.key for j in job_registry.jobs() if j.needs_claude}
    assert live == frozen, f"rows added or removed: {live ^ frozen}"


def test_a_pure_python_row_gets_the_host_and_no_binary():
    job = g._row("job-watch")
    assert not job.needs_claude
    assert s._job_command(job, MAC) == (
        'cd "/repo" && VAN_GOGH_SCHEDULED=1 VAN_GOGH_HOST=codex "/venv/bin/python" '
        f'"{g._script(g.MAC_REPO, "job_watch.py")}"')
    assert "--claude" not in s._inner_for(job, WIN) and "VAN_GOGH_HOST=codex&&" in s._inner_for(job, WIN)


def test_the_plist_and_task_carry_the_same_command():
    from xml.sax.saxutils import escape
    job = g._row("meeting-ingest")
    assert escape(s._job_command(job, MAC)) in s._plist_for(job, MAC)
    assert "VAN_GOGH_HOST=codex" in s._plist_for(job, MAC)


def test_context_for_commands_reads_the_host(monkeypatch, tmp_path):
    import host
    monkeypatch.setenv(host.HOST_ENV, "codex")
    monkeypatch.setattr(s, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(s, "_digest_python", lambda: "/venv/bin/python")
    monkeypatch.setattr(s, "_logs_dir", lambda: tmp_path)
    monkeypatch.setattr(s, "claude_bin", lambda: "/bin/codex")
    ctx = s._ctx_for_commands()
    assert (ctx.host, ctx.claude) == ("codex", "/bin/codex")
