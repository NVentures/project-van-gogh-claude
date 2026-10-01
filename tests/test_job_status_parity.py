"""`job_states` must report every job, on both platforms.

This is not a cosmetic listing. `job_watch.evaluate` looks a job up by label in
what `job_states` returns, and a job whose label is absent grades
`not_scheduled` -- which the watcher reads as "nothing to do here" and never
kicks. So a row missing from this function is not a job reported quietly; it is
a job the watcher is blind to.

Before the job registry, the function was four hand-written blocks and it had
drifted in three places at once:

* macOS omitted the prep poller,
* Windows omitted the prep poller, the finance brief and the scorecard,
* and nothing anywhere would have noticed, because the only tests over this
  area asserted the names install and uninstall share, not the names status
  reports.

The failure is stated here in its own terms rather than as a generic parity
assertion, so a future reader sees which jobs went ungraded and on which OS
rather than a diff of two sets.
"""

import pytest

import job_registry
import scheduler_setup


def _reported(platform, monkeypatch):
    """Every label `job_states` reports, with every job forced installed.

    Both OS probes are stubbed: `launchctl list` is made to print every label
    the registry knows, and `schtasks /query` to succeed for anything. What is
    under test is which rows the function BUILDS, never what the machine has.
    """
    labels = "\n".join(job.mac_label for job in job_registry.jobs())

    class _Res:
        returncode = 0
        stdout = labels
        stderr = ""

    monkeypatch.setattr(scheduler_setup, "_run", lambda cmd: _Res())
    monkeypatch.setattr(scheduler_setup.Path, "exists", lambda self: True)
    return {row["label"] for row in scheduler_setup.job_states(platform)}


def _enabled_everything(monkeypatch):
    """Turn on every opt-in feature, so no row is absent for being off."""
    import config_loader
    for name in ("prep_email_enabled", "notes_enabled", "finance_enabled",
                 "contact_capture_enabled", "digest_enabled"):
        monkeypatch.setattr(config_loader, name, lambda: True)
    monkeypatch.setattr(config_loader, "digest_briefings", lambda: {
        b: {"enabled": True, "days": ["monday"], "time": "07:00"}
        for b in ("morning-coffee", "afternoon-tea", "week", "week-retro")})


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_every_registry_row_is_reported(platform, monkeypatch):
    """The invariant, both platforms: nothing scheduled goes unreported."""
    _enabled_everything(monkeypatch)
    reported = _reported(platform, monkeypatch)
    expected = {job.mac_label if platform == "darwin" else job.win_task
                for job in job_registry.jobs()}
    missing = expected - reported
    assert not missing, (
        f"{platform}: these jobs are installed but never reported, so the "
        f"watcher grades them not_scheduled and never kicks them: "
        f"{sorted(missing)}")


def test_the_prep_poller_is_reported_on_macos(monkeypatch):
    """The macOS half of the original defect, named.

    The prep poller was installed by `cmd_install`, removed by `cmd_uninstall`
    and reported by neither `job_states` nor anything else on macOS.
    """
    _enabled_everything(monkeypatch)
    assert "com.monet.prep-email" in _reported("darwin", monkeypatch)


@pytest.mark.parametrize("task", ["VanGogh-prep-email", "VanGogh-finance-email"])
def test_the_windows_omissions_are_reported(task, monkeypatch):
    """The Windows half: prep and finance were both missing from the loop.

    The scorecard was the third, and is fixed by removal rather than by
    addition: it stopped being a job of its own and now renders inside Week
    Retro, so there is no `VanGogh-kpi-email` to report.
    """
    _enabled_everything(monkeypatch)
    assert task in _reported("win32", monkeypatch)


def test_an_unsupported_platform_still_answers(capsys):
    """Linux has no scheduler, and must say so rather than return nothing."""
    rows = scheduler_setup.job_states("linux")
    assert [r["state"] for r in rows] == ["unsupported"]
