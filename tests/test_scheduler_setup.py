"""app/scheduler_setup.py — the cross-platform background-scheduler setup.

CI-safe: only the pure command-construction helpers and the platform dispatch
are exercised — never a real launchctl / schtasks / Register-ScheduledTask.
The regression this module exists to prevent is install and uninstall drifting
apart on the label/task names, so that consistency is asserted directly.
"""
import plistlib
import subprocess
from pathlib import Path

import scheduler_setup


def test_plist_renders_valid_xml_with_escaped_command():
    content = scheduler_setup._plist_content(
        "meeting-ingest", "/repo path", "/venv/bin/python",
        "/usr/local/bin/claude", Path("/logs"))
    data = plistlib.loads(content.encode("utf-8"))
    assert data["Label"] == "com.monet.meeting-ingest"
    command = data["ProgramArguments"][2]
    # Through skill_run.py, never a bare `claude -p`: that is what buys the
    # scheduled run its retries, failure ledger and CLI freshness check.
    # Built with Path, not a literal, so the assertion holds on Windows too:
    # _plist_content renders the script path with the platform separator.
    script = Path("/repo path") / "app" / "skill_run.py"
    assert command == (f'cd "/repo path" && "/venv/bin/python" '
                       f'"{script}" meeting-ingest '
                       f'--claude "/usr/local/bin/claude"')
    assert data["StartCalendarInterval"] == {"Hour": 4, "Minute": 30}
    assert data["StandardOutPath"].endswith("meeting-ingest.log")
    assert data["StandardErrorPath"].endswith("meeting-ingest.err")


def test_scheduled_jobs_never_spawn_the_cli_directly():
    """Both OS command lines must go through skill_run.py.

    A job that spawns `claude -p` itself has no retry, leaves no trace when it
    fails, and never checks whether the CLI is current, which is how a stale
    build silently 400s every scheduled briefing on a machine.
    """
    for skill in scheduler_setup.SKILLS:
        mac = scheduler_setup._plist_content(
            skill, "/repo", "/venv/bin/python", "/bin/claude", Path("/logs"))
        win = scheduler_setup._task_inner(
            skill, r"C:\repo", r"C:\venv\python.exe", r"C:\bin\claude.cmd",
            Path(r"C:\logs"))
        for command in (mac, win):
            assert "skill_run.py" in command
            assert f'-p "/van-gogh:{skill}"' not in command


def test_mac_label_and_plist_path_agree():
    for skill in scheduler_setup.SKILLS:
        label = scheduler_setup.MAC_LABEL.format(skill=skill)
        assert scheduler_setup._launch_agent_path(skill).name == f"{label}.plist"


def test_register_ps_command_uses_win_task_name_and_settings():
    for skill in scheduler_setup.SKILLS:
        vbs = Path(r"C:\state\scheduler") / f"VanGogh-{skill}.vbs"
        cmd = scheduler_setup._register_task_ps(skill, r"C:\plugin", vbs)
        assert f"'{scheduler_setup.WIN_TASK.format(skill=skill)}'" in cmd
        assert "-StartWhenAvailable" in cmd
        assert "4:30am" in cmd
        # The action launches the hidden wscript wrapper, never cmd.exe
        # directly — a cmd.exe action flashes a console window on every fire.
        assert "-Execute 'wscript.exe'" in cmd
        assert "cmd.exe" not in cmd
        assert f'//B //Nologo "{vbs}"' in cmd


def test_task_vbs_runs_hidden_with_doubled_quotes():
    inner = scheduler_setup._task_inner(
        "meeting-ingest", r"C:\plugin", r"C:\venv\python.exe",
        r"C:\bin\claude.cmd", Path(r"C:\logs"))
    assert "skill_run.py" in inner and "meeting-ingest" in inner
    assert '--claude "C:\\bin\\claude.cmd"' in inner
    assert '1>> "' in inner and 'meeting-ingest.log"' in inner
    vbs = scheduler_setup._vbs_content(inner)
    # Window style 0 (hidden); wait and propagate the exit code so Task
    # Scheduler keeps overlap protection and a truthful Last Run Result.
    assert vbs.rstrip().endswith('", 0, True)')
    assert vbs.startswith(
        'WScript.Quit CreateObject("WScript.Shell").Run("cmd.exe /c ""')
    assert '""C:\\venv\\python.exe""' in vbs


def test_vbs_path_lives_in_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    assert scheduler_setup._vbs_path("VanGogh-meeting-ingest") == \
        tmp_path / "scheduler" / "VanGogh-meeting-ingest.vbs"


def test_win_install_writes_utf16_vbs_and_registers_it(tmp_path, monkeypatch, capsys):
    """cmd_install on win32: the .vbs lands in the state dir as UTF-16 LE with
    BOM (the only Unicode encoding WSH parses — utf-8 breaks non-ASCII paths),
    exact CRLF content, and the registered action points at that same path."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_setup, "claude_bin", lambda: r"C:\bin\claude.cmd")
    monkeypatch.setattr(scheduler_setup, "_repo_root", lambda: r"C:\plugin")
    monkeypatch.setattr(scheduler_setup, "_logs_dir", lambda: tmp_path / "logs")
    runs = []
    monkeypatch.setattr(
        scheduler_setup, "_run",
        lambda cmd: (runs.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    assert scheduler_setup.cmd_install("win32") == 0
    for skill in scheduler_setup.SKILLS:
        vbs = tmp_path / "scheduler" / f"VanGogh-{skill}.vbs"
        raw = vbs.read_bytes()
        assert raw.startswith(b"\xff\xfe")  # UTF-16 LE BOM
        content = raw.decode("utf-16")
        assert content == scheduler_setup._vbs_content(
            scheduler_setup._task_inner(
                skill, r"C:\plugin", scheduler_setup._digest_python(),
                r"C:\bin\claude.cmd", tmp_path / "logs"))
        assert content.endswith("\r\n") and "\r\r" not in content
        registered = [c for c in runs if c[0] == "powershell" and f"VanGogh-{skill}" in c[-1]]
        assert registered and str(vbs) in registered[0][-1]


def test_win_uninstall_removes_vbs_files_and_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    from config_loader import DIGEST_BRIEFING_DEFAULTS
    vbs_dir = tmp_path / "scheduler"
    vbs_dir.mkdir(parents=True)
    names = [scheduler_setup.WIN_TASK.format(skill=s) for s in scheduler_setup.SKILLS] + \
            [scheduler_setup.DIGEST_WIN_TASK.format(briefing=b) for b in DIGEST_BRIEFING_DEFAULTS]
    for name in names:
        (vbs_dir / f"{name}.vbs").write_text("stub", encoding="utf-16")
    monkeypatch.setattr(
        scheduler_setup, "_run",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert scheduler_setup.cmd_uninstall("win32") == 0
    assert not vbs_dir.exists()  # every launcher removed, empty dir cleaned up


def test_ps_quote_escapes_single_quotes():
    assert scheduler_setup._ps_quote("it's") == "'it''s'"


def test_install_and_uninstall_share_the_same_names():
    """The names install registers are exactly the ones uninstall removes —
    both sides read the same module constants; a rename cannot half-apply."""
    assert scheduler_setup.SKILLS == ("meeting-ingest", "ingest-workspace")
    assert scheduler_setup.MAC_LABEL == "com.monet.{skill}"
    assert scheduler_setup.WIN_TASK == "VanGogh-{skill}"
    # The watcher is not a skill (it runs job_watch.py, not skill_run.py), so
    # it sits outside SKILLS and every loop over skill jobs. That is exactly
    # why it needs pinning here: a job installed by a code path of its own,
    # and removed by another, is the one that gets left behind running.
    assert scheduler_setup.WATCH_LABEL == "com.monet.job-watch"
    assert scheduler_setup.WATCH_TASK == "VanGogh-job-watch"


def test_uninstall_actually_removes_the_watcher(tmp_path, monkeypatch, capsys):
    """Behaviour, not source text: the watcher's plist must be gone afterwards.

    It survives an uninstall if either half forgets it, and a watcher left
    behind wakes up every half hour forever on a machine whose owner removed
    Van Gogh. An earlier version of this test grepped the function source and
    passed on a code path that never touched the file."""
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    watch = agents / f"{scheduler_setup.WATCH_LABEL}.plist"
    watch.write_text("<plist/>", encoding="utf-8")
    monkeypatch.setattr(scheduler_setup, "_watch_launch_agent_path", lambda: watch)
    monkeypatch.setattr(scheduler_setup, "_launch_agent_path",
                        lambda skill: agents / f"com.monet.{skill}.plist")
    monkeypatch.setattr(scheduler_setup, "_digest_launch_agent_path",
                        lambda b: agents / f"com.monet.digest-{b}.plist")
    monkeypatch.setattr(scheduler_setup, "remove_legacy_jobs", lambda p: None)
    monkeypatch.setattr(scheduler_setup, "_run",
                        lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))

    scheduler_setup.cmd_uninstall("darwin")
    assert not watch.exists()
    assert scheduler_setup.WATCH_LABEL in capsys.readouterr().out


def test_the_watcher_runs_on_an_interval_not_a_clock_time():
    """It checks whether the clock-time jobs did their work, so it cannot be
    scheduled like one of them: a watcher that woke once a day could not
    notice anything within a day."""
    plist = scheduler_setup._watch_plist_content(
        "/repo", "/py", scheduler_setup.Path("/logs"))
    assert "<key>StartInterval</key>" in plist
    assert f"<integer>{scheduler_setup.WATCH_INTERVAL_MIN * 60}</integer>" in plist
    assert "StartCalendarInterval" not in plist
    # RunAtLoad is what catches up a laptop that was shut at 07:00.
    assert "<key>RunAtLoad</key>" in plist
    assert "job_watch.py" in plist


def test_legacy_job_is_removed_on_mac(tmp_path, monkeypatch, capsys):
    """A skill rename must take its old scheduled job with it. Left behind, the
    launchd agent keeps firing daily against a skill name that no longer exists
    — a silent daily failure on every already-installed machine."""
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    stale = agents / "com.monet.granola-ingest.plist"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(scheduler_setup.Path, "home", staticmethod(lambda: tmp_path.parent))
    monkeypatch.setattr(scheduler_setup, "_launch_agent_path", lambda skill: stale
                        if skill == "granola-ingest" else agents / f"com.monet.{skill}.plist")

    calls = []
    monkeypatch.setattr(scheduler_setup, "_run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))

    scheduler_setup.remove_legacy_jobs("darwin")
    assert not stale.exists()
    assert ["launchctl", "unload", str(stale)] in calls
    assert "legacy job" in capsys.readouterr().out


def test_legacy_removal_is_a_noop_on_a_fresh_machine(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scheduler_setup, "_launch_agent_path",
                        lambda skill: tmp_path / f"com.monet.{skill}.plist")
    scheduler_setup.remove_legacy_jobs("darwin")
    assert capsys.readouterr().out == ""


def test_legacy_job_is_removed_on_windows(tmp_path, monkeypatch, capsys):
    """The win32 branch has the extra moving part — it must delete the scheduled
    task AND the wscript wrapper. A missed .vbs unlink leaves the same stale
    daily failure the darwin test exists to prevent."""
    vbs = tmp_path / "VanGogh-granola-ingest.vbs"
    vbs.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(scheduler_setup, "_vbs_path", lambda task: tmp_path / f"{task}.vbs")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scheduler_setup, "_run", fake_run)
    scheduler_setup.remove_legacy_jobs("win32")

    assert ["schtasks", "/delete", "/tn", "VanGogh-granola-ingest", "/f"] in calls
    assert not vbs.exists()
    assert "legacy task" in capsys.readouterr().out


def test_legacy_task_names_are_removed_on_windows(tmp_path, monkeypatch, capsys):
    """The retired scripts/register-scheduled-tasks.ps1 registered its hourly
    job under a literal name that predates the WIN_TASK template. Deleting the
    script without retiring the task would leave an orphaned hourly job pointing
    at a file that no longer exists."""
    monkeypatch.setattr(scheduler_setup, "_vbs_path", lambda task: tmp_path / f"{task}.vbs")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scheduler_setup, "_run", fake_run)
    scheduler_setup.remove_legacy_jobs("win32")

    assert ["schtasks", "/delete", "/tn", "Van Gogh - Granola Auto Ingest", "/f"] in calls
    assert "legacy task" in capsys.readouterr().out


def test_legacy_cleanup_is_reachable_without_a_reinstall():
    """The whole point of the cleanup is machines that update via the
    marketplace and never re-run the install skill. If it is only wired into
    cmd_install/cmd_uninstall it never runs where it is needed."""
    import inspect

    import user_state

    assert hasattr(scheduler_setup, "remove_legacy_jobs"), "must be public to call"
    src = inspect.getsource(user_state.sync_legacy_scheduler_jobs)
    assert "remove_legacy_jobs" in src
    # The stamp hashes BOTH legacy tuples: drop one from the digest and adding
    # a new entry to it would never re-fire the cleanup on stamped machines.
    assert "LEGACY_TASK_NAMES" in src
    import config_loader
    assert "sync_legacy_scheduler_jobs" in inspect.getsource(config_loader)[:4000]


def test_renamed_skills_stay_in_the_legacy_list():
    """Entries are never removed: machines update from arbitrarily old versions,
    so the cleanup has to keep working years later."""
    assert "granola-ingest" in scheduler_setup.LEGACY_SKILLS
    assert "Van Gogh - Granola Auto Ingest" in scheduler_setup.LEGACY_TASK_NAMES
    assert not set(scheduler_setup.LEGACY_SKILLS) & set(scheduler_setup.SKILLS)


def test_linux_is_unsupported_noop(capsys):
    assert scheduler_setup.cmd_install("linux") == 0
    assert "UNSUPPORTED" in capsys.readouterr().out
    assert scheduler_setup.cmd_uninstall("linux") == 0
    assert "UNSUPPORTED" in capsys.readouterr().out


def test_install_missing_claude_is_clear_error(monkeypatch, capsys):
    def boom():
        raise FileNotFoundError("The 'claude' CLI was not found on PATH.")

    monkeypatch.setattr(scheduler_setup, "claude_bin", boom)
    assert scheduler_setup.cmd_install("darwin") == 1
    assert "claude" in capsys.readouterr().out


# ── Digest jobs (sync-digests) ─────────────────────────────────────────────────

def test_note_plist_runs_on_an_interval_and_carries_claude():
    """The Note tick is a poller: an interval, never a clock time, and the
    resolved CLI path travels in the command because launchd's PATH is bare."""
    content = scheduler_setup._note_plist_content(
        "/repo path", "/venv/bin/python", "/usr/local/bin/claude", Path("/logs"))
    data = plistlib.loads(content.encode("utf-8"))
    assert data["Label"] == scheduler_setup.NOTE_LABEL
    assert data["StartInterval"] == scheduler_setup.NOTE_INTERVAL_MIN * 60
    assert "StartCalendarInterval" not in data
    assert data["RunAtLoad"] is True
    command = data["ProgramArguments"][2]
    assert str(Path("/repo path") / "app" / "note_send.py") in command
    assert '--claude "/usr/local/bin/claude"' in command
    assert data["StandardOutPath"].endswith("note.log")


def test_note_label_and_task_are_pinned_for_symmetry():
    """Install and uninstall reach the job by these two names; a rename that
    touches one and not the other leaves a job nobody can remove."""
    assert scheduler_setup.NOTE_LABEL == "com.monet.note"
    assert scheduler_setup.NOTE_TASK == "VanGogh-note"
    assert scheduler_setup._note_launch_agent_path().name == "com.monet.note.plist"
    ps = scheduler_setup._note_task_ps(r"C:\repo", Path(r"C:\x\note.vbs"))
    assert "'VanGogh-note'" in ps
    assert f"-Minutes {scheduler_setup.NOTE_INTERVAL_MIN}" in ps
    inner = scheduler_setup._note_inner(r"C:\repo", r"C:\py.exe", r"C:\claude.cmd",
                                        Path(r"C:\logs"))
    assert "note_send.py" in inner and "note.err" in inner


def test_digest_plist_renders_weekday_intervals():
    content = scheduler_setup._digest_plist_content(
        "morning-coffee", "/repo path", "/venv/bin/python", "/usr/local/bin/claude",
        Path("/logs"), ["monday", "tuesday", "wednesday", "thursday", "friday"], 7, 0)
    data = plistlib.loads(content.encode("utf-8"))
    assert data["Label"] == "com.monet.digest-morning-coffee"
    command = data["ProgramArguments"][2]
    assert '"/venv/bin/python"' in command
    assert 'digest_send.py" morning-coffee --claude "/usr/local/bin/claude"' in command
    intervals = data["StartCalendarInterval"]
    assert [i["Weekday"] for i in intervals] == [1, 2, 3, 4, 5]
    assert all(i["Hour"] == 7 and i["Minute"] == 0 for i in intervals)
    assert data["StandardOutPath"].endswith("digest-morning-coffee.log")


def test_digest_label_and_plist_path_agree():
    from config_loader import DIGEST_BRIEFING_DEFAULTS
    for briefing in DIGEST_BRIEFING_DEFAULTS:
        label = scheduler_setup.DIGEST_MAC_LABEL.format(briefing=briefing)
        assert scheduler_setup._digest_launch_agent_path(briefing).name == f"{label}.plist"


def test_digest_ps_command_has_weekly_trigger():
    vbs = Path(r"C:\state\scheduler\VanGogh-digest-week.vbs")
    cmd = scheduler_setup._digest_task_ps("week", r"C:\plugin", vbs, ["monday"], 7, 0)
    assert "'VanGogh-digest-week'" in cmd
    assert "-Weekly -DaysOfWeek Monday -At 7:00" in cmd
    assert "-StartWhenAvailable" in cmd
    assert "-Execute 'wscript.exe'" in cmd
    assert "cmd.exe" not in cmd
    assert f'//B //Nologo "{vbs}"' in cmd


def test_digest_inner_command_line():
    inner = scheduler_setup._digest_inner(
        "week", r"C:\plugin", r"C:\venv\Scripts\python.exe", r"C:\bin\claude.cmd",
        Path(r"C:\logs"))
    assert "digest_send.py" in inner and " week " in inner
    assert r'--claude "C:\bin\claude.cmd"' in inner
    assert '2>> "' in inner and 'digest-week.err"' in inner


def test_parse_cadence_validates():
    import pytest
    days, hour, minute = scheduler_setup._parse_cadence(
        {"days": ["Monday", "friday"], "time": "13:05"})
    assert (days, hour, minute) == (["monday", "friday"], 13, 5)
    for bad in ({"days": ["someday"], "time": "07:00"},
                {"days": [], "time": "07:00"},
                {"days": ["monday"], "time": "25:00"},
                {"days": ["monday"], "time": "7"},):
        with pytest.raises(ValueError):
            scheduler_setup._parse_cadence(bad)


def test_sync_digests_disabled_removes_and_reports(monkeypatch, capsys, tmp_path):
    # Fixture digest.enabled is False: sync must not install anything.
    # Belt-and-braces: point the install path at tmp_path anyway so a fixture
    # change can never write into the developer's real ~/Library/LaunchAgents.
    removed = []
    monkeypatch.setattr(scheduler_setup, "_remove_digest_job",
                        lambda platform, briefing, quiet_missing=False: removed.append(briefing))
    monkeypatch.setattr(scheduler_setup, "_digest_launch_agent_path",
                        lambda briefing: tmp_path / f"{briefing}.plist")
    monkeypatch.setattr(scheduler_setup, "_run",
                        lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert scheduler_setup.cmd_sync_digests("darwin") == 0
    assert sorted(removed) == ["afternoon-tea", "morning-coffee", "week", "week-retro"]
    assert "digests disabled" in capsys.readouterr().out


def test_sync_digests_unsupported_platform(capsys):
    assert scheduler_setup.cmd_sync_digests("linux") == 0
    assert "UNSUPPORTED" in capsys.readouterr().out


def _digest_config(briefings):
    import config_loader as cl
    return {**cl._config, "digest": {"enabled": True, "briefings": briefings}}


def test_sync_digests_installs_valid_and_removes_invalid(monkeypatch, capsys, tmp_path):
    """One valid + one invalid cadence: the valid one installs, the invalid one
    errors AND has its (possibly stale) job removed, exit code is 1."""
    import config_loader as cl
    saved = cl._config
    removed = []
    runs = []
    try:
        cl._config = _digest_config({
            "week": {"enabled": True, "days": ["monday"], "time": "07:00"},
            "morning-coffee": {"enabled": True, "days": ["someday"], "time": "07:00"},
            "afternoon-tea": {"enabled": False},
            "week-retro": {"enabled": False},
        })
        monkeypatch.setattr(scheduler_setup, "claude_bin", lambda: "/bin/claude")
        monkeypatch.setattr(scheduler_setup, "_repo_root", lambda: "/repo")
        monkeypatch.setattr(scheduler_setup, "_logs_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(scheduler_setup, "_digest_python", lambda: "/venv/bin/python")
        monkeypatch.setattr(scheduler_setup, "_digest_launch_agent_path",
                            lambda briefing: tmp_path / f"{briefing}.plist")
        monkeypatch.setattr(
            scheduler_setup, "_remove_digest_job",
            lambda platform, briefing, quiet_missing=False: removed.append(briefing))
        monkeypatch.setattr(
            scheduler_setup, "_run",
            lambda cmd: (runs.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
        assert scheduler_setup.cmd_sync_digests("darwin") == 1
    finally:
        cl._config = saved
    out = capsys.readouterr().out
    assert "ERROR: morning-coffee" in out and "job removed" in out
    assert "morning-coffee" in removed  # invalid cadence job cleaned up
    assert (tmp_path / "week.plist").exists()  # valid one written
    assert "com.monet.digest-week scheduled" in out


def test_sync_digests_warns_on_unknown_briefing_key(monkeypatch, capsys, tmp_path):
    import config_loader as cl
    saved = cl._config
    try:
        cl._config = _digest_config({"morning_coffee": {"enabled": True}})  # typo key
        monkeypatch.setattr(scheduler_setup, "claude_bin", lambda: "/bin/claude")
        monkeypatch.setattr(scheduler_setup, "_repo_root", lambda: "/repo")
        monkeypatch.setattr(scheduler_setup, "_logs_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(scheduler_setup, "_digest_python", lambda: "/venv/bin/python")
        monkeypatch.setattr(scheduler_setup, "_digest_launch_agent_path",
                            lambda briefing: tmp_path / f"{briefing}.plist")
        monkeypatch.setattr(scheduler_setup, "_run",
                            lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))
        scheduler_setup.cmd_sync_digests("darwin")
    finally:
        cl._config = saved
    assert "WARN: unknown briefing 'morning_coffee'" in capsys.readouterr().out


def test_digest_names_are_pinned_for_install_uninstall_symmetry(monkeypatch):
    """sync-digests installs and uninstall/status remove/inspect the same
    names — both sides read these module constants; a rename cannot half-apply."""
    assert scheduler_setup.DIGEST_MAC_LABEL == "com.monet.digest-{briefing}"
    assert scheduler_setup.DIGEST_WIN_TASK == "VanGogh-digest-{briefing}"
    from config_loader import DIGEST_BRIEFING_DEFAULTS
    removed = []
    monkeypatch.setattr(
        scheduler_setup, "_remove_digest_job",
        lambda platform, briefing, quiet_missing=False: removed.append(briefing))
    monkeypatch.setattr(scheduler_setup, "_launch_agent_path",
                        lambda skill: Path("/nonexistent") / f"{skill}.plist")
    scheduler_setup.cmd_uninstall("darwin")
    assert sorted(removed) == sorted(DIGEST_BRIEFING_DEFAULTS)
