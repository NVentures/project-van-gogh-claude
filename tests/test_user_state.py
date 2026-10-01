"""app/user_state.py — the stable per-user state dir outside the plugin cache.

Everything runs against a fake state dir (VAN_GOGH_STATE_DIR) and a fake plugin
root (monkeypatched _PLUGIN_ROOT), so the real ~/.config/van-gogh/ and the real
checkout are never touched.
"""
import sys
import pytest
from pathlib import Path

import user_state


def _isolate(tmp_path, monkeypatch):
    """Point the state dir and the (legacy) plugin root at tmp dirs."""
    state = tmp_path / "state"
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.setattr(user_state, "_PLUGIN_ROOT", plugin)
    return state, plugin


def test_state_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "custom"))
    assert user_state.state_dir() == tmp_path / "custom"


def test_state_dir_defaults_to_home_config(tmp_path, monkeypatch):
    monkeypatch.delenv("VAN_GOGH_STATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert user_state.state_dir() == tmp_path / ".config" / "van-gogh"


def test_env_file_migrates_legacy_plugin_root_copy(tmp_path, monkeypatch):
    state, plugin = _isolate(tmp_path, monkeypatch)
    (plugin / ".env").write_text("GRANOLA_API_KEY=abc\n", encoding="utf-8")

    path = user_state.env_file()

    assert path == state / ".env"
    assert path.read_text(encoding="utf-8") == "GRANOLA_API_KEY=abc\n"
    # Legacy copy is left in place (older scheduler jobs may still read it).
    assert (plugin / ".env").exists()


def test_env_file_prefers_existing_state_copy(tmp_path, monkeypatch):
    """Once the state copy exists it is the source of truth — never re-clobbered."""
    state, plugin = _isolate(tmp_path, monkeypatch)
    state.mkdir(parents=True)
    (state / ".env").write_text("KEY=state\n", encoding="utf-8")
    (plugin / ".env").write_text("KEY=legacy\n", encoding="utf-8")

    assert user_state.env_file().read_text(encoding="utf-8") == "KEY=state\n"


def test_pointer_file_migrates_legacy_van_gogh_vault(tmp_path, monkeypatch):
    state, plugin = _isolate(tmp_path, monkeypatch)
    (plugin / ".van-gogh-vault").write_text("/some/vault\n", encoding="utf-8")

    path = user_state.pointer_file()

    assert path == state / "vault-pointer"
    assert path.read_text(encoding="utf-8").strip() == "/some/vault"


def test_missing_everything_returns_nonexistent_paths(tmp_path, monkeypatch):
    """Fresh machine: no state, no legacy — paths come back but exist() is False,
    so callers (config_loader, the MS rotation write-back) can branch cleanly."""
    state, _ = _isolate(tmp_path, monkeypatch)
    assert not user_state.env_file().exists()
    assert not user_state.pointer_file().exists()
    assert not state.exists()  # nothing was created as a side effect


def test_upsert_env_creates_file_and_parent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    path = user_state.upsert_env("MS_GRAPH_REFRESH_TOKEN_OUTLOOK", "tok1")
    assert path.read_text(encoding="utf-8") == "MS_GRAPH_REFRESH_TOKEN_OUTLOOK=tok1\n"


def test_upsert_env_replaces_in_place_and_appends(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    user_state.upsert_env("A", "1")
    user_state.upsert_env("B", "2")
    user_state.upsert_env("A", "3")
    path = user_state.env_file()
    assert path.read_text(encoding="utf-8") == "A=3\nB=2\n"


def test_load_env_reads_state_file_without_override(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    user_state.upsert_env("VAN_GOGH_TEST_KEY", "from-file")
    monkeypatch.setenv("VAN_GOGH_TEST_REAL", "real")
    user_state.upsert_env("VAN_GOGH_TEST_REAL", "stale-file-value")
    monkeypatch.delenv("VAN_GOGH_TEST_KEY", raising=False)

    user_state.load_env()

    import os
    assert os.environ["VAN_GOGH_TEST_KEY"] == "from-file"
    # A real env var always wins over the file (override=False).
    assert os.environ["VAN_GOGH_TEST_REAL"] == "real"


def test_upsert_env_scrubs_legacy_bom(tmp_path, monkeypatch):
    """A .env with a stray BOM (old PowerShell 5.1 write) is read via utf-8-sig
    so the first key parses, and rewritten as plain utf-8 (no BOM)."""
    state, _ = _isolate(tmp_path, monkeypatch)
    state.mkdir(parents=True)
    (state / ".env").write_text("FIRST=1\n", encoding="utf-8-sig")

    path = user_state.upsert_env("SECOND", "2")

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8") == "FIRST=1\nSECOND=2\n"


# ── set-env CLI (used by the install skill's Granola key write) ───────────────

def test_set_env_cli_writes_key_from_env_var(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("VG_ENV_VALUE", "g-key with $pecial 'chars'")

    assert user_state.main(["set-env", "GRANOLA_API_KEY"]) == 0

    assert "GRANOLA_API_KEY written (utf-8, no BOM)" in capsys.readouterr().out
    text = user_state.env_file().read_text(encoding="utf-8")
    assert "GRANOLA_API_KEY=g-key with $pecial 'chars'" in text


def test_set_env_cli_errors_without_value_env_var(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("VG_ENV_VALUE", raising=False)

    assert user_state.main(["set-env", "GRANOLA_API_KEY"]) == 1
    assert "ERROR" in capsys.readouterr().out
    assert not user_state.env_file().exists()


# ── Microsoft refresh-token rotation write-back ───────────────────────────────

def test_ms_rotated_token_persisted_to_env_file(tmp_path, monkeypatch):
    """Entra rotates refresh tokens; the rotation must land in the state .env
    (and the process env) or the stored token eventually goes stale."""
    import os

    import microsoft_client as mc

    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("MS_GRAPH_REFRESH_TOKEN_OUTLOOK", "old-token")  # registers cleanup
    user_state.upsert_env("MS_GRAPH_REFRESH_TOKEN_OUTLOOK", "old-token")

    mc._persist_rotated_token("Outlook", "new-token")

    assert os.environ["MS_GRAPH_REFRESH_TOKEN_OUTLOOK"] == "new-token"
    assert "MS_GRAPH_REFRESH_TOKEN_OUTLOOK=new-token" in user_state.env_file().read_text(
        encoding="utf-8"
    )


def test_ms_rotated_token_skips_file_when_no_env_file(tmp_path, monkeypatch):
    """Cloud routines have real env vars and no .env — rotation must not
    manufacture a state file, only update the process env."""
    import os

    import microsoft_client as mc

    state, _ = _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("MS_GRAPH_REFRESH_TOKEN_OUTLOOK", "old-token")  # registers cleanup

    mc._persist_rotated_token("Outlook", "new-token")

    assert os.environ["MS_GRAPH_REFRESH_TOKEN_OUTLOOK"] == "new-token"
    assert not (state / ".env").exists()


# ── runtime dependency self-sync (the plain-plugin-update path) ───────────────

def _prime_managed_venv(tmp_path, monkeypatch, requirements="requests\n"):
    """State dir with a venv/ that IS the running interpreter's prefix."""
    state, plugin = _isolate(tmp_path, monkeypatch)
    venv = state / "venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(user_state.sys, "prefix", str(venv))
    (plugin / "requirements.txt").write_text(requirements, encoding="utf-8")
    return state, plugin


def _capture_pip(monkeypatch, calls, returncode=0):
    class _Result:
        pass

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        result = _Result()
        result.returncode = returncode
        result.stderr = "boom: no matching distribution" if returncode else ""
        return result

    monkeypatch.setattr(user_state.subprocess, "run", fake_run)


def test_sync_runtime_deps_noop_outside_managed_venv(tmp_path, monkeypatch):
    """Dev checkouts and pytest run their own interpreter — never pip-install."""
    _, plugin = _isolate(tmp_path, monkeypatch)
    (plugin / "requirements.txt").write_text("requests\n", encoding="utf-8")
    calls = []
    _capture_pip(monkeypatch, calls)

    user_state.sync_runtime_deps()

    assert calls == []


def test_sync_runtime_deps_installs_once_then_noops(tmp_path, monkeypatch):
    state, _ = _prime_managed_venv(tmp_path, monkeypatch)
    calls = []
    _capture_pip(monkeypatch, calls)

    user_state.sync_runtime_deps()

    assert len(calls) == 1
    assert calls[0][1:4] == ["-m", "pip", "install"]
    assert (state / "requirements.sha256").exists()

    user_state.sync_runtime_deps()  # marker current → no second install
    assert len(calls) == 1


def test_sync_runtime_deps_resyncs_when_requirements_change(tmp_path, monkeypatch):
    _, plugin = _prime_managed_venv(tmp_path, monkeypatch)
    calls = []
    _capture_pip(monkeypatch, calls)
    user_state.sync_runtime_deps()

    (plugin / "requirements.txt").write_text("requests\nnew-dep\n", encoding="utf-8")
    user_state.sync_runtime_deps()

    assert len(calls) == 2


def test_sync_runtime_deps_failed_install_retries_next_run(tmp_path, monkeypatch, capsys):
    state, _ = _prime_managed_venv(tmp_path, monkeypatch)
    calls = []
    _capture_pip(monkeypatch, calls, returncode=1)

    user_state.sync_runtime_deps()

    assert not (state / "requirements.sha256").exists()  # stale marker → retry
    assert "retry" in capsys.readouterr().err
    user_state.sync_runtime_deps()
    assert len(calls) == 2


# ── plugin-root pointer (user-authored personal skills) ───────────────────────

def test_record_plugin_root_noop_outside_managed_venv(tmp_path, monkeypatch):
    """Dev checkouts and pytest must never clobber a real install's pointer."""
    state, _ = _isolate(tmp_path, monkeypatch)

    user_state.record_plugin_root()

    assert not (state / "plugin-root").exists()


def test_record_plugin_root_writes_and_heals_after_recloned_cache(tmp_path, monkeypatch):
    state, plugin = _prime_managed_venv(tmp_path, monkeypatch)

    user_state.record_plugin_root()
    pointer = state / "plugin-root"
    assert pointer.read_text(encoding="utf-8").strip() == str(plugin)

    # Marketplace update moved the cache: the next skill run re-points it.
    moved = tmp_path / "plugin-v2"
    moved.mkdir()
    monkeypatch.setattr(user_state, "_PLUGIN_ROOT", moved)
    user_state.record_plugin_root()
    assert pointer.read_text(encoding="utf-8").strip() == str(moved)


def test_record_plugin_root_skips_rewrite_when_current(tmp_path, monkeypatch):
    state, _ = _prime_managed_venv(tmp_path, monkeypatch)
    user_state.record_plugin_root()
    pointer = state / "plugin-root"

    def boom(*args, **kwargs):
        raise AssertionError("pointer rewritten although already current")

    monkeypatch.setattr(type(pointer), "write_text", boom)
    user_state.record_plugin_root()


def test_config_loader_import_triggers_plugin_root_record():
    """Personal-skill scripts resolve the plugin via this pointer — the
    self-heal call must stay at the config_loader choke point."""
    source = (Path(__file__).parent.parent / "app" / "config_loader.py").read_text(
        encoding="utf-8"
    )
    assert "user_state.record_plugin_root()" in source


def test_config_loader_import_triggers_dep_sync():
    """config_loader is the choke point every script imports before the
    third-party clients — the self-heal call must stay there."""
    source = (Path(__file__).parent.parent / "app" / "config_loader.py").read_text(
        encoding="utf-8"
    )
    assert "user_state.sync_runtime_deps()" in source


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits: Windows os.chmod only toggles the read-only flag, "
           "so 0o600 is not expressible. Access there is governed by ACLs, "
           "which this assertion cannot describe.")
def test_the_env_file_is_never_world_readable(tmp_path):
    """It holds refresh tokens and API keys. Found in the wild: the default
    macOS umask left a live Maps key readable by every account on the box."""
    import os
    import stat
    env = tmp_path / ".env"
    user_state.upsert_env("GOOGLE_MAPS_API_KEY", "AIzaSyTESTVALUE", env_path=env)
    mode = stat.S_IMODE(os.stat(env).st_mode)
    assert mode == 0o600, f"env written as {oct(mode)}, not 0600"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits: see the note on the test above.")
def test_a_loose_existing_env_file_is_tightened_on_write(tmp_path):
    """The file that already exists with bad permissions is exactly the one
    that needs fixing, so the chmod cannot be creation-only."""
    import os
    import stat
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    os.chmod(env, 0o644)
    user_state.upsert_env("GRAIN_API_KEY", "abc", env_path=env)
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    # And the pre-existing key survived the rewrite.
    assert "EXISTING=1" in env.read_text(encoding="utf-8")
