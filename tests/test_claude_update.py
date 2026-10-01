"""The CLI self-update that keeps unattended runs off a stale `claude` build.

The failure this guards against, seen live on a scheduled chain:

    API Error: 400 Claude Code 2.1.139 does not support this model; version
    2.1.251 or newer is required. Run 'claude update' ...

Every briefing shells out to `claude -p`, so one stale CLI breaks every
scheduled job on the machine until a human notices.
"""
import subprocess

import claude_cli
import claude_update
import pytest

# run_claude resolves the CLI through platform_compat.claude_bin before it ever
# reaches subprocess. CI has no claude on PATH, so every test below that drives
# run_claude stubs this instead of stubbing subprocess alone.
_FAKE_CLI = "/usr/local/bin/claude"

# The verbatim error text from the live failure. If the module's detection
# regex ever stops matching this string, the whole feature is inert.
LIVE_ERROR = (
    "API Error: 400 Claude Code 2.1.139 does not support this model; version "
    "2.1.251 or newer is required. Run 'claude update', or update the Claude "
    "desktop app, then try again."
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Point the stamp at a temp dir and reset the per-process heal latch."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VAN_GOGH_DISABLE_CLI_UPDATE", raising=False)
    monkeypatch.setattr(claude_update, "_healed_this_process", False)
    monkeypatch.setattr(claude_update, "claude_bin", lambda: "claude")


def _completed(args, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, rc, stdout, stderr)


def test_detects_the_live_stale_cli_error():
    assert claude_update.is_stale_cli_error(LIVE_ERROR)


@pytest.mark.parametrize("text", [
    "API Error: 500 Internal server error",
    "Credit balance is too low",
    "",
    None,
])
def test_ordinary_failures_are_not_stale_cli_errors(text):
    assert not claude_update.is_stale_cli_error(text)


def test_update_now_runs_claude_update_and_reports_the_new_version(monkeypatch):
    versions = iter(["2.1.139 (Claude Code)", "2.1.260 (Claude Code)"])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "--version":
            return _completed(cmd, stdout=next(versions))
        return _completed(cmd, stdout="Successfully updated")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = claude_update.update_now()

    assert ["claude", "update"] in calls
    assert record["status"] == "ok"
    assert record["version_before"] == "2.1.139"
    assert record["version"] == "2.1.260"
    assert record["updated"] is True


def test_update_failure_is_reported_not_raised(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[1] == "--version":
            return _completed(cmd, stdout="2.1.139")
        raise OSError("no write access to the npm prefix")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = claude_update.update_now()
    assert record["status"] == "error"
    assert "OSError" in record["error"]


def test_ensure_current_throttles_to_one_check_per_day(monkeypatch):
    runs = []
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: runs.append(1) or {"status": "ok"})

    assert claude_update.ensure_current()["status"] == "ok"
    second = claude_update.ensure_current()

    assert second["status"] == "fresh"
    assert len(runs) == 1, "a second check inside the window must not re-run"
    assert claude_update.stamp_path().exists()


def test_ensure_current_rechecks_once_the_stamp_is_stale(monkeypatch):
    runs = []
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: runs.append(1) or {"status": "ok"})
    claude_update.ensure_current()
    claude_update.ensure_current(max_age_h=0)
    assert len(runs) == 2


def test_a_failed_check_still_stamps(monkeypatch):
    """Otherwise an offline machine re-runs `claude update` on every job."""
    runs = []
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: runs.append(1) or {"status": "error"})
    claude_update.ensure_current()
    claude_update.ensure_current()
    assert len(runs) == 1


def test_disable_env_opts_out_entirely(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_DISABLE_CLI_UPDATE", "1")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
    assert claude_update.ensure_current()["status"] == "disabled"
    assert claude_update.heal_if_stale(LIVE_ERROR) is False


def test_heal_runs_once_per_process(monkeypatch):
    runs = []
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: runs.append(1) or {"status": "ok"})
    assert claude_update.heal_if_stale(LIVE_ERROR) is True
    assert claude_update.heal_if_stale(LIVE_ERROR) is False, (
        "a retry loop seeing the same stale error must not re-run claude update")
    assert len(runs) == 1


def test_heal_ignores_unrelated_failures(monkeypatch):
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: pytest.fail("updated on a non-stale error"))
    assert claude_update.heal_if_stale("API Error: 529 overloaded") is False


def test_run_claude_updates_and_retries_on_a_stale_cli(monkeypatch):
    """The classification path heals itself: bad CLI, update, retry, answer."""
    updates = []
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: updates.append(1) or {"status": "ok"})
    replies = iter([
        _completed(["claude"], rc=1, stderr=LIVE_ERROR),
        _completed(["claude"], rc=0, stdout='{"ok": true}'),
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(replies))
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: _FAKE_CLI)

    result = claude_cli.run_claude("classify these")

    assert len(updates) == 1
    assert result.returncode == 0 and result.stdout == '{"ok": true}'


def test_run_claude_does_not_retry_an_ordinary_failure(monkeypatch):
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: pytest.fail("updated on a non-stale error"))
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(1) or _completed(
                            ["claude"], rc=1, stderr="API Error: 529 overloaded"))
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: _FAKE_CLI)

    result = claude_cli.run_claude("classify these")

    assert len(calls) == 1, "only the stale-CLI failure is retried"
    assert result.returncode == 1


def test_scheduled_skill_run_keeps_the_cli_current(monkeypatch):
    """The unattended entry point checks before it spends a run."""
    import skill_run

    checked = []
    monkeypatch.setattr(skill_run.claude_update, "ensure_current",
                        lambda claude=None: checked.append(claude))
    monkeypatch.setattr(skill_run, "with_retries",
                        lambda fn, **kwargs: fn())
    monkeypatch.setattr(skill_run, "_spawn",
                        lambda skill, claude, args, timeout: "done")

    skill_run.run_skill("morning-coffee", claude="/bin/claude")
    assert checked == ["/bin/claude"]


def test_skill_run_heals_a_stale_cli_before_the_retry(monkeypatch):
    import skill_run

    healed = []
    monkeypatch.setattr(skill_run.claude_update, "heal_if_stale",
                        lambda text, claude=None: healed.append(text) or True)
    monkeypatch.setattr(skill_run.config_loader, "repo_root", lambda: ".")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _completed(["claude"], rc=1, stderr=LIVE_ERROR))

    with pytest.raises(RuntimeError):
        skill_run._spawn("morning-coffee", "/bin/claude", "", 10)
    assert healed and claude_update.is_stale_cli_error(healed[0])


def test_digest_render_heals_a_stale_cli_before_the_retry(monkeypatch):
    import digest_send

    healed = []
    monkeypatch.setattr(digest_send.claude_update, "heal_if_stale",
                        lambda text, claude=None: healed.append(text) or True)
    monkeypatch.setattr(digest_send.config_loader, "repo_root", lambda: ".")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _completed(["claude"], rc=1, stdout=LIVE_ERROR))

    with pytest.raises(RuntimeError):
        digest_send.render_briefing("morning-coffee", claude="/bin/claude")
    assert healed and claude_update.is_stale_cli_error(healed[0])


def test_cli_version_survives_a_missing_binary(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("claude")))
    assert claude_update.cli_version() == ""


def test_module_main_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(claude_update, "update_now",
                        lambda claude=None: {"status": "ok", "version": "2.1.260"})
    assert claude_update.main([]) == 0
    assert '"status": "ok"' in capsys.readouterr().out


def test_update_spawn_is_bounded_and_subscription_routed(monkeypatch):
    """No unbounded spawn, no console-window flash, no API key in the child."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-the-child")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen[cmd[1]] = kwargs
        return _completed(cmd, stdout="2.1.260")

    monkeypatch.setattr(subprocess, "run", fake_run)
    claude_update.update_now()

    assert seen["update"]["timeout"] == claude_update.UPDATE_TIMEOUT_S
    assert seen["update"]["creationflags"] == claude_update.NO_WINDOW
    assert "ANTHROPIC_API_KEY" not in seen["update"]["env"]
