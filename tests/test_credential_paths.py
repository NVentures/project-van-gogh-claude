"""Every OAuth app-credential lookup honours VAN_GOGH_STATE_DIR.

Three modules resolved the per-user credential by spelling out
~/.config/van-gogh/ themselves, so a test, a timing harness or an install run
pointed at another state dir read the developer's real credential (or found
none) while every other state file followed the override. Each must resolve
through user_state.state_dir(), keeping the bundled config/oauth/ fallback.
"""
import importlib
import json
import os

import pytest

MODULES = ("google_client", "microsoft_client", "auth_bootstrap")
FILES = {"google_client": "google_client_secret.json",
         "microsoft_client": "ms_client_secret.json",
         "auth_bootstrap": None}


@pytest.fixture
def reloaded(monkeypatch, tmp_path):
    """Reload a module with the state dir pointed at tmp_path; reload again
    on the way out so later tests see the suite's own state dir."""
    def _reload(name):
        monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
        return importlib.reload(importlib.import_module(name))
    yield _reload
    monkeypatch.undo()
    for name in MODULES:
        importlib.reload(importlib.import_module(name))


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_google_client_reads_the_override_state_dir(reloaded, tmp_path):
    mod = reloaded("google_client")
    assert mod.GOOGLE_CLIENT_SECRET_PATH.endswith(
        os.path.join("config", "oauth", "google_client_secret.json"))
    _write(tmp_path / "google_client_secret.json",
           {"installed": {"client_id": "x", "client_secret": "y"}})
    mod = reloaded("google_client")
    assert mod.GOOGLE_CLIENT_SECRET_PATH == str(tmp_path / "google_client_secret.json")
    assert mod._oauth_app_creds() == ("x", "y")


def test_microsoft_client_reads_the_override_state_dir(reloaded, tmp_path, monkeypatch):
    monkeypatch.delenv("MS_OAUTH_CLIENT_ID", raising=False)
    mod = reloaded("microsoft_client")
    assert mod.MS_CLIENT_SECRET_PATH.endswith(
        os.path.join("config", "oauth", "ms_client_secret.json"))
    _write(tmp_path / "ms_client_secret.json", {"client_id": "abc", "tenant_id": "common"})
    mod = reloaded("microsoft_client")
    assert mod.MS_CLIENT_SECRET_PATH == str(tmp_path / "ms_client_secret.json")
    assert mod._client_id() == "abc"


def test_auth_bootstrap_reads_the_override_state_dir(reloaded, tmp_path):
    mod = reloaded("auth_bootstrap")
    assert mod.GOOGLE_CLIENT_SECRET_PATH.endswith(
        os.path.join("config", "oauth", "google_client_secret.json"))
    assert mod.MS_CLIENT_SECRETS_PATH.endswith(
        os.path.join("config", "oauth", "ms_client_secret.json"))
    _write(tmp_path / "google_client_secret.json", {"installed": {"client_id": "x"}})
    _write(tmp_path / "ms_client_secret.json", {"client_id": "abc"})
    mod = reloaded("auth_bootstrap")
    assert mod.GOOGLE_CLIENT_SECRET_PATH == str(tmp_path / "google_client_secret.json")
    assert mod.MS_CLIENT_SECRETS_PATH == str(tmp_path / "ms_client_secret.json")


def test_no_module_spells_out_the_home_config_path():
    """The literal is the bug: a hand-spelled ~/.config/van-gogh can never
    follow the override."""
    for name in MODULES:
        source = open(importlib.import_module(name).__file__, encoding="utf-8").read()
        assert 'expanduser("~/.config/van-gogh' not in source, name
        assert 'Path.home() / ".config" / "van-gogh"' not in source, name
