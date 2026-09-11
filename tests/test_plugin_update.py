"""The published-version check that stops an install drifting silently.

The gap this closes: `claude plugin marketplace update van-gogh` is the only
way a plugin updates, and a machine with marketplace auto-update off never runs
it. Nothing told the user. These tests pin the three properties that make the
notice safe to wire into every skill run — it never reaches the network on a
dev checkout or under pytest, it says nothing when it cannot tell, and it never
raises into a caller that was on its way to render a briefing.
"""
import json

import plugin_update
import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Point the stamp at a temp dir; make any unstubbed fetch a hard failure."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VAN_GOGH_DISABLE_UPDATE_CHECK", raising=False)

    def _no_network(*a, **k):
        raise AssertionError("test reached the network")

    monkeypatch.setattr(plugin_update, "published_version", _no_network)


def _stub_versions(monkeypatch, installed, published):
    monkeypatch.setattr(plugin_update, "installed_version", lambda: installed)
    monkeypatch.setattr(plugin_update, "published_version",
                        lambda *a, **k: published)


# --- version parsing -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("0.44.0", (0, 44, 0)),
    ("0.9.12", (0, 9, 12)),
    (" 0.44.0 ", (0, 44, 0)),
])
def test_parses_zerover_versions(text, expected):
    assert plugin_update.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", None, "1.2", "0.44.0-rc1", "abc", "0.x.0"])
def test_unparseable_versions_are_unknown_not_old(text):
    """None, never a tuple. A guess here nags about an update that isn't there."""
    assert plugin_update.parse_version(text) is None


def test_ordering_is_numeric_not_lexicographic():
    # "0.9.0" > "0.44.0" as strings; the whole check would invert.
    assert plugin_update.parse_version("0.44.0") > plugin_update.parse_version("0.9.0")


# --- check() ---------------------------------------------------------------

def test_flags_an_available_update(monkeypatch):
    _stub_versions(monkeypatch, "0.44.0", "0.45.0")
    record = plugin_update.check(force=True)
    assert record["status"] == "ok"
    assert record["update_available"] is True
    assert record["installed"] == "0.44.0"
    assert record["published"] == "0.45.0"


def test_current_install_reports_no_update(monkeypatch):
    _stub_versions(monkeypatch, "0.44.0", "0.44.0")
    assert plugin_update.check(force=True)["update_available"] is False


def test_ahead_of_published_reports_no_update(monkeypatch):
    """A maintainer running an unreleased build must not be told to downgrade."""
    _stub_versions(monkeypatch, "0.45.0", "0.44.0")
    assert plugin_update.check(force=True)["update_available"] is False


def test_offline_is_unknown_and_silent(monkeypatch):
    """An empty fetch must never read as "you are behind"."""
    _stub_versions(monkeypatch, "0.44.0", "")
    record = plugin_update.check(force=True)
    assert record["status"] == "unknown"
    assert record["update_available"] is False


def test_failed_check_still_stamps(monkeypatch):
    """Otherwise an offline machine re-checks on every single skill run."""
    _stub_versions(monkeypatch, "0.44.0", "")
    plugin_update.check(force=True)
    assert "checked_at" in json.loads(
        plugin_update.stamp_path().read_text(encoding="utf-8"))


def test_throttles_to_one_check_per_window(monkeypatch):
    calls = []

    def _counted(*a, **k):
        calls.append(1)
        return "0.45.0"

    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.44.0")
    monkeypatch.setattr(plugin_update, "published_version", _counted)
    plugin_update.check(force=True)
    plugin_update.check()          # inside the window: must not refetch
    plugin_update.check()
    assert len(calls) == 1


def test_force_bypasses_the_throttle(monkeypatch):
    calls = []

    def _counted(*a, **k):
        calls.append(1)
        return "0.45.0"

    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.44.0")
    monkeypatch.setattr(plugin_update, "published_version", _counted)
    plugin_update.check(force=True)
    plugin_update.check(force=True)
    assert len(calls) == 2


# --- cached_notice() -------------------------------------------------------

def test_notice_is_empty_with_no_stamp():
    assert plugin_update.cached_notice() == ""


def test_notice_names_both_versions_and_the_command(monkeypatch):
    _stub_versions(monkeypatch, "0.44.0", "0.45.0")
    plugin_update.check(force=True)
    notice = plugin_update.cached_notice()
    assert "0.45.0" in notice and "0.44.0" in notice
    assert plugin_update.UPDATE_COMMAND in notice


def test_notice_is_empty_when_current(monkeypatch):
    _stub_versions(monkeypatch, "0.44.0", "0.44.0")
    plugin_update.check(force=True)
    assert plugin_update.cached_notice() == ""


def test_notice_reads_no_network(monkeypatch):
    """cached_notice runs on every skill import; it must never fetch."""
    _stub_versions(monkeypatch, "0.44.0", "0.45.0")
    plugin_update.check(force=True)
    monkeypatch.setattr(plugin_update, "published_version",
                        lambda *a, **k: pytest.fail("cached_notice fetched"))
    assert plugin_update.cached_notice() != ""


# --- opt-out ---------------------------------------------------------------

def test_disable_env_silences_check_and_notice(monkeypatch):
    _stub_versions(monkeypatch, "0.44.0", "0.45.0")
    plugin_update.check(force=True)
    monkeypatch.setenv("VAN_GOGH_DISABLE_UPDATE_CHECK", "1")
    assert plugin_update.check(force=True)["status"] == "disabled"
    assert plugin_update.cached_notice() == ""


# --- check_quietly() -------------------------------------------------------

def test_check_quietly_no_ops_outside_the_managed_venv(monkeypatch):
    """The pytest/dev-checkout gate. Without it the suite hits the network."""
    import user_state
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: False)
    monkeypatch.setattr(plugin_update, "check",
                        lambda **k: pytest.fail("checked outside managed venv"))
    plugin_update.check_quietly()


def test_check_quietly_swallows_everything(monkeypatch):
    """It runs at config_loader import; a raise here breaks every script."""
    import user_state
    monkeypatch.setattr(user_state, "_running_in_managed_venv", lambda: True)

    def _boom(**k):
        raise RuntimeError("network on fire")

    monkeypatch.setattr(plugin_update, "check", _boom)
    plugin_update.check_quietly()  # must not raise


# --- apply_update() --------------------------------------------------------

def test_apply_reports_missing_cli_instead_of_raising(monkeypatch):
    """A Desktop session may have no `claude` on PATH; the notice still stood."""
    import platform_compat

    def _missing():
        raise FileNotFoundError("The 'claude' CLI was not found on PATH.")

    monkeypatch.setattr(platform_compat, "claude_bin", _missing)
    record = plugin_update.apply_update()
    assert record["status"] == "no_cli"


def test_apply_runs_the_sanctioned_command(monkeypatch):
    """Never a git pull inside the plugin cache — that is what a re-clone undoes."""
    seen = {}

    def _fake_run(cmd, **kwargs):
        import subprocess
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "updated", "")

    monkeypatch.setattr(plugin_update.subprocess, "run", _fake_run)
    monkeypatch.setattr(plugin_update, "check", lambda **k: {"status": "ok"})
    record = plugin_update.apply_update(claude="/usr/local/bin/claude")
    assert record["status"] == "ok"
    assert seen["cmd"][1:] == ["plugin", "marketplace", "update", "van-gogh"]


# ── Release notes (CHANGELOG.md → whats_new) ──────────────────────────────────

_CHANGELOG = """# What's new in Van Gogh

Intro for maintainers, never shown to users.

## 0.47.0 (2026-09-08)

- New: Grain support.

## 0.46.0 (2026-09-01)

- Fixed: a thing.

## 0.45.0 (2026-08-20)

- New: an older thing.
"""


def test_parse_changelog_drops_the_intro_and_keeps_headings():
    entries = plugin_update.parse_changelog(_CHANGELOG)
    assert [v for v, _ in entries] == ["0.47.0", "0.46.0", "0.45.0"]
    assert entries[0][1].startswith("## 0.47.0")
    assert "Grain support" in entries[0][1]
    assert "maintainers" not in entries[0][1]


def test_entries_between_picks_exactly_the_unseen_versions():
    entries = plugin_update.parse_changelog(_CHANGELOG)
    notes = plugin_update.entries_between(entries, "0.45.0", "0.47.0")
    assert "0.47.0" in notes and "0.46.0" in notes
    assert "0.45.0" not in notes  # already seen
    # Unparseable lower bound degrades to just the current version's entry.
    only_current = plugin_update.entries_between(entries, "not-a-version", "0.47.0")
    assert "0.46.0" not in only_current and "0.47.0" in only_current


def test_whats_new_is_silent_on_a_fresh_install(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path",
                        lambda: tmp_path / "whats-new.json")
    assert plugin_update.whats_new() == ""  # everything is new; say nothing
    # ...but the visit was stamped, so the NEXT update has a "previous".
    stamp = json.loads((tmp_path / "whats-new.json").read_text(encoding="utf-8"))
    assert stamp["version"] == "0.47.0" and stamp["previous"] is None


def test_whats_new_surfaces_unseen_entries_after_an_update(monkeypatch, tmp_path):
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": "0.45.0", "first_seen": 1.0,
                                 "previous": "0.44.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    notes = plugin_update.whats_new()
    assert "0.47.0" in notes and "0.46.0" in notes and "0.45.0" not in notes
    # Stable across repeated calls in the same window (morning_coffee shells
    # out to week_review: an inner call must not consume the outer one's notes).
    assert plugin_update.whats_new() == notes


def test_whats_new_goes_quiet_once_marked_shown(monkeypatch, tmp_path):
    """Shown exactly once: the briefing that renders the notes stamps them."""
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": "0.46.0", "first_seen": 1.0,
                                 "previous": "0.45.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.46.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    assert plugin_update.whats_new() != ""
    plugin_update.mark_notes_shown()
    assert plugin_update.whats_new() == ""
    # ...until the NEXT update: a new version transition clears the flag.
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "update_log_path",
                        lambda: tmp_path / "update-log.jsonl")
    assert plugin_update.whats_new() != ""


def test_whats_new_is_silent_and_unconsumed_in_unattended_runs(monkeypatch, tmp_path):
    """Digests and scheduled runs never carry release notes, and must not
    burn the one showing before the user has a chat session."""
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": "0.47.0", "first_seen": 1.0,
                                 "previous": "0.45.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    monkeypatch.setenv(plugin_update.UNATTENDED_ENV, "1")
    assert plugin_update.whats_new() == ""
    monkeypatch.delenv(plugin_update.UNATTENDED_ENV)
    assert plugin_update.whats_new() != ""  # the chat session still gets them


def test_unattended_detection_still_writes_the_update_log(monkeypatch, tmp_path):
    """A digest run that detects the update logs it durably even though it
    shows nothing — /van-gogh:release-notes must not wait for a human run."""
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": "0.45.0", "first_seen": 1.0,
                                 "previous": "0.44.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    monkeypatch.setattr(plugin_update, "update_log_path",
                        lambda: tmp_path / "update-log.jsonl")
    monkeypatch.setenv(plugin_update.UNATTENDED_ENV, "1")
    assert plugin_update.whats_new() == ""
    assert len(plugin_update.read_update_log()) == 1


def test_mark_notes_shown_without_a_stamp_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path",
                        lambda: tmp_path / "whats-new.json")
    plugin_update.mark_notes_shown()  # must not raise or create the file
    assert not (tmp_path / "whats-new.json").exists()


def test_whats_new_fails_open_without_a_changelog(monkeypatch, tmp_path):
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": "0.47.0", "first_seen": 1e18,
                                 "previous": "0.45.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog", lambda: [])
    assert plugin_update.whats_new() == ""


# ── Vault update log + /van-gogh:release-notes payload ────────────────────────

def _prime_transition(monkeypatch, tmp_path, from_v="0.45.0", to_v="0.47.0"):
    """A machine mid-update: stamp says from_v, plugin says to_v."""
    stamp = tmp_path / "whats-new.json"
    stamp.write_text(json.dumps({"version": from_v, "first_seen": 1.0,
                                 "previous": "0.44.0"}), encoding="utf-8")
    monkeypatch.setattr(plugin_update, "installed_version", lambda: to_v)
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path", lambda: stamp)
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    log = tmp_path / "update-log.jsonl"
    monkeypatch.setattr(plugin_update, "update_log_path", lambda: log)
    return log


def test_detected_update_writes_one_durable_vault_line(monkeypatch, tmp_path):
    log = _prime_transition(monkeypatch, tmp_path)
    notes = plugin_update.whats_new()
    entries = plugin_update.read_update_log()
    assert len(entries) == 1
    assert entries[0]["from"] == "0.45.0" and entries[0]["to"] == "0.47.0"
    assert entries[0]["notes_md"] == notes  # the log records what was shown
    # Repeat calls in the window re-show notes but never re-log the update.
    plugin_update.whats_new()
    assert len(plugin_update.read_update_log()) == 1
    assert log.exists()


def test_fresh_install_logs_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "whats_new_stamp_path",
                        lambda: tmp_path / "whats-new.json")
    monkeypatch.setattr(plugin_update, "update_log_path",
                        lambda: tmp_path / "update-log.jsonl")
    plugin_update.whats_new()
    assert plugin_update.read_update_log() == []


def test_unwritable_vault_costs_the_log_line_not_the_notes(monkeypatch, tmp_path):
    _prime_transition(monkeypatch, tmp_path)

    def boom():
        raise OSError("no vault yet")

    monkeypatch.setattr(plugin_update, "update_log_path", boom)
    notes = plugin_update.whats_new()
    assert "0.47.0" in notes  # the briefing still gets its footer


def test_release_notes_payload_shapes_the_last_update(monkeypatch, tmp_path):
    _prime_transition(monkeypatch, tmp_path)
    plugin_update.whats_new()  # records the transition
    payload = plugin_update.release_notes_payload()
    assert payload["installed"] == "0.47.0"
    assert payload["update_count"] == 1
    assert payload["last_update"]["from"] == "0.45.0"
    assert "0.46.0" in payload["last_update"]["notes_md"]
    assert payload["current_entry_md"].startswith("## 0.47.0")
    assert payload["changelog_versions"][0] == "0.47.0"


def test_release_notes_payload_degrades_with_no_log(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_update, "installed_version", lambda: "0.47.0")
    monkeypatch.setattr(plugin_update, "update_log_path",
                        lambda: tmp_path / "missing.jsonl")
    monkeypatch.setattr(plugin_update, "local_changelog",
                        lambda: plugin_update.parse_changelog(_CHANGELOG))
    payload = plugin_update.release_notes_payload()
    assert payload["last_update"] is None and payload["update_count"] == 0
    assert "Grain support" in payload["current_entry_md"]  # the fallback


def test_read_update_log_skips_corrupt_lines(monkeypatch, tmp_path):
    log = tmp_path / "update-log.jsonl"
    log.write_text('{"ts":"t","from":"0.45.0","to":"0.46.0","notes_md":"x"}\n'
                   'not json at all\n'
                   '{"ts":"t2","from":"0.46.0","to":"0.47.0","notes_md":"y"}\n',
                   encoding="utf-8")
    monkeypatch.setattr(plugin_update, "update_log_path", lambda: log)
    entries = plugin_update.read_update_log()
    assert [e["to"] for e in entries] == ["0.46.0", "0.47.0"]
