"""Key file version 2 (punch list P4 and P34): parse, discover, install_all.

A v2 key carries the shared app credentials, the accounts and the client
block beside the companion access; a v1 key keeps working. Discovery finds
browser-renamed copies and lists the newest first. install_all writes the
credentials, ingests the companion, and moves the file into the private
keyfile dir. A credential that already has a working sign-in is never
overwritten (the refresh is stubbed here). Nothing touches the network or
the claude CLI.
"""
import json
import os
import stat
import sys
import time

import pytest

import companion_key as ck
import install_oauth_credentials as ioc
import user_state

GOOGLE = {"installed": {"client_id": "123.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-fake",
                        "redirect_uris": ["http://localhost"]}}
MICROSOFT = {"client_id": "f783a7e4-1111-2222-3333-444444444444", "tenant_id": "common"}
REPO = "NVentures/project-van-gogh-custom-jane-doe"


def _v2(**overrides):
    data = {"kind": "van-gogh-key", "version": 2, "issued": "2026-10-01",
            "repo": REPO, "token": "github_pat_abc123",
            "credentials": {"kind": "van-gogh-credentials",
                            "google_client_secret": GOOGLE,
                            "ms_client_secret": MICROSOFT,
                            "maps_api_key": "AIzaFakeMapsKey00000"},
            "accounts": [{"provider": "google", "email_hint": "jane@acme.com"},
                         {"provider": "microsoft", "email_hint": "jane@corp.com"}],
            "client": {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
                       "timezone": "America/New_York", "notetaker": "granola"},
            "support_email": "team@example.com"}
    data.update(overrides)
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data,
                    encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temp state dir plus temp Downloads/Desktop/cwd for discovery; the
    claude CLI and the refresh probe are stubs."""
    state = tmp_path / "state"
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    downloads = tmp_path / "Downloads"
    desktop = tmp_path / "Desktop"
    cwd = tmp_path / "cwd"
    for d in (downloads, desktop, cwd):
        d.mkdir()
    monkeypatch.setattr(ck, "search_dirs",
                        lambda: [downloads, desktop, cwd, ck.keyfile_dir()])
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    calls = []
    monkeypatch.setattr(ck, "_run_claude",
                        lambda args, token: (calls.append(args), {"ok": True})[1])
    monkeypatch.setattr(ck, "working_sign_in", lambda provider: False)
    return {"state": state, "downloads": downloads, "desktop": desktop,
            "cwd": cwd, "calls": calls}


# ── parse ─────────────────────────────────────────────────────────────────────

def test_v2_key_parses_with_credentials_accounts_and_client(tmp_path):
    key = ck.parse_key_file(_write(tmp_path / "k.json", _v2()))
    assert key["version"] == 2 and key["repo"] == REPO
    assert key["credentials"]["google_client_secret"] == GOOGLE
    assert [a["email_hint"] for a in key["accounts"]] == ["jane@acme.com", "jane@corp.com"]
    assert key["client"]["timezone"] == "America/New_York"
    assert key["support_email"] == "team@example.com"
    assert ioc.validate_bundle(key["credentials"]) == []


def test_v1_key_still_parses_as_version_one(tmp_path):
    key = ck.parse_key_file(_write(tmp_path / "k.json", {
        "kind": "van-gogh-key", "repo": REPO, "token": "t", "issued": "2026-09-09"}))
    assert key["version"] == 1 and key["credentials"] is None
    assert key["accounts"] == [] and key["client"] == {}


def test_v2_credentials_are_checked_by_the_installers_own_validator(tmp_path):
    bad = _v2(credentials={"kind": "van-gogh-credentials",
                           "google_client_secret": {"installed": {"client_secret": "x"}}})
    with pytest.raises(ck.KeyFileError, match="malformed"):
        ck.parse_key_file(_write(tmp_path / "k.json", bad))
    with pytest.raises(ck.KeyFileError, match="bundle"):
        ck.parse_key_file(_write(tmp_path / "k2.json", _v2(credentials={"kind": "other"})))


def test_v2_without_a_companion_is_allowed_but_not_without_everything(tmp_path):
    data = _v2()
    del data["repo"], data["token"]
    key = ck.parse_key_file(_write(tmp_path / "k.json", data))
    assert key["repo"] == "" and key["credentials"] is not None
    with pytest.raises(ck.KeyFileError, match="neither"):
        ck.parse_key_file(_write(tmp_path / "k2.json", {"kind": "van-gogh-key", "version": 2}))


def test_unknown_version_is_refused(tmp_path):
    with pytest.raises(ck.KeyFileError, match="version"):
        ck.parse_key_file(_write(tmp_path / "k.json", _v2(version=3)))


# ── discover ──────────────────────────────────────────────────────────────────

def test_discover_lists_both_downloads_copies_newest_first(home):
    older = _write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2())
    newer = _write(home["downloads"] / "van-gogh-key-jane-doe (1).json", _v2())
    now = time.time()
    os.utime(older, (now - 600, now - 600))
    os.utime(newer, (now, now))
    found = ck.discover()
    assert [p.name for p in found] == ["van-gogh-key-jane-doe (1).json", "van-gogh-key-jane-doe.json"]
    payload = ck.discover_payload()
    assert payload["found"] == 2 and payload["newest"] == str(newer)
    assert "instruction" not in payload


def test_discover_searches_desktop_cwd_and_the_private_dir_and_ignores_others(home):
    _write(home["desktop"] / "van-gogh-key.json", _v2())
    _write(home["cwd"] / "VAN-GOGH-KEY-copy.json", _v2())
    _write(ck.keyfile_dir() / "van-gogh-key.json", _v2())
    _write(home["downloads"] / "notes.json", {})
    _write(home["downloads"] / "van-gogh-key.txt", "x")
    names = sorted(p.name for p in ck.discover())
    assert names == ["VAN-GOGH-KEY-copy.json", "van-gogh-key.json", "van-gogh-key.json"]


def test_discover_with_no_key_file_returns_the_create_your_own_text(home):
    payload = ck.discover_payload()
    assert payload["found"] == 0 and payload["newest"] is None
    assert "Create your own Google and Microsoft app credentials" in payload["instruction"]
    assert ck.OWN_CREDENTIALS_DOC.exists()


# ── install_all ───────────────────────────────────────────────────────────────

def test_install_all_writes_credentials_ingests_companion_and_moves_the_file(home):
    source = _write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2())
    result = ck.install_all(source)
    assert result["ok"] is True and result["mode"] == "v2"
    state = home["state"]
    assert json.loads((state / "google_client_secret.json").read_text()) == GOOGLE
    assert json.loads((state / "ms_client_secret.json").read_text()) == MICROSOFT
    env = user_state.env_file().read_text(encoding="utf-8")
    assert "GOOGLE_MAPS_API_KEY=AIzaFakeMapsKey00000" in env
    assert f"{ck.ENV_TOKEN}=github_pat_abc123" in env
    assert result["companion"]["mode"] == "ingest"
    assert home["calls"][0][:3] == ["plugin", "marketplace", "add"]
    assert result["accounts"][0]["email_hint"] == "jane@acme.com"
    assert result["client"]["full_name"] == "Jane Doe"
    assert result["support_email"] == "team@example.com"
    moved = state / "keyfile" / "van-gogh-key.json"
    assert result["moved_to"] == str(moved)
    assert moved.exists() and not source.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(moved.stat().st_mode) == 0o600
        assert stat.S_IMODE(moved.parent.stat().st_mode) == 0o700
    else:  # Windows carries no POSIX mode bits; access is by ACL
        pass
    assert set(result) >= {"ok", "mode", "credentials", "companion", "accounts",
                           "client", "support_email", "moved_to"}


def test_install_all_from_the_private_dir_is_a_resume_not_a_move(home):
    source = _write(ck.keyfile_dir() / "van-gogh-key.json", _v2())
    result = ck.install_all(source)
    assert result["ok"] is True and result["moved_to"] == str(source)
    assert source.exists()


def test_install_all_v1_installs_the_companion_only(home):
    source = _write(home["downloads"] / "van-gogh-key-jane-doe.json", {
        "kind": "van-gogh-key", "repo": REPO, "token": "t", "issued": "2026-09-09"})
    result = ck.install_all(source)
    assert result["ok"] is True and result["mode"] == "v1"
    assert result["credentials"] == {} and result["companion"]["mode"] == "ingest"
    assert not (home["state"] / "google_client_secret.json").exists()
    assert not source.exists() and (home["state"] / "keyfile" / "van-gogh-key.json").exists()


def test_a_corrupt_key_file_writes_nothing_and_stays_put(home):
    source = _write(home["downloads"] / "van-gogh-key-jane-doe.json", "{not json")
    result = ck.install_all(source)
    assert result["ok"] is False and "not valid JSON" in result["error"]
    assert result["moved_to"] == "" and source.exists()
    assert not (home["state"] / "google_client_secret.json").exists()
    assert not user_state.env_file().exists()
    assert home["calls"] == []


def test_a_differing_credential_with_a_working_sign_in_is_kept(home, monkeypatch):
    state = home["state"]
    existing = {"installed": {"client_id": "old.apps.googleusercontent.com",
                              "client_secret": "GOCSPX-old"}}
    _write(state / "google_client_secret.json", existing)
    monkeypatch.setattr(ck, "working_sign_in", lambda provider: provider == "google")
    source = _write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2())
    result = ck.install_all(source)
    assert result["ok"] is True
    assert json.loads((state / "google_client_secret.json").read_text()) == existing
    assert json.loads((state / "ms_client_secret.json").read_text()) == MICROSOFT
    assert "kept the existing google app credential" in result["warning"]
    assert result["credentials"]["kept"] == [
        {"provider": "google", "path": str(state / "google_client_secret.json")}]


def test_a_differing_credential_whose_refresh_fails_is_replaced(home, monkeypatch):
    state = home["state"]
    _write(state / "google_client_secret.json",
           {"installed": {"client_id": "old", "client_secret": "GOCSPX-old"}})
    monkeypatch.setattr(ck, "working_sign_in", lambda provider: False)
    result = ck.install_all(_write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2()))
    assert result["ok"] is True and "warning" not in result
    assert json.loads((state / "google_client_secret.json").read_text()) == GOOGLE


def test_an_identical_credential_is_rewritten_silently(home, monkeypatch):
    state = home["state"]
    _write(state / "google_client_secret.json", GOOGLE)
    probes = []
    monkeypatch.setattr(ck, "working_sign_in",
                        lambda provider: (probes.append(provider), True)[1])
    result = ck.install_all(_write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2()))
    assert result["ok"] is True and "warning" not in result
    assert probes == []  # no refresh is spent on an identical file


def test_working_sign_in_tries_each_stored_token_and_answers_false_on_doubt(home, monkeypatch):
    user_state.upsert_env("GOOGLE_REFRESH_TOKEN_WORK", "t1")
    user_state.upsert_env("GOOGLE_REFRESH_TOKEN_HOME", "t2")
    user_state.upsert_env("MS_GRAPH_REFRESH_TOKEN_CORP", "t3")
    monkeypatch.undo()  # drop the fixture's stub of working_sign_in
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(home["state"]))
    tried = []

    def probe(provider, label):
        tried.append((provider, label))
        if label == "HOME":
            return True
        raise RuntimeError("expired")
    monkeypatch.setattr(ck, "_refresh_works", probe)
    assert ck.working_sign_in("google") is True
    assert tried == [("google", "WORK"), ("google", "HOME")]
    assert ck.working_sign_in("microsoft") is False


def test_companion_failure_is_reported_but_credentials_stay_installed(home, monkeypatch):
    monkeypatch.setattr(ck, "_run_claude",
                        lambda args, token: {"ok": False, "step": "x", "error": "denied"})
    result = ck.install_all(_write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2()))
    assert result["ok"] is False and result["error"].startswith("companion:")
    assert (home["state"] / "google_client_secret.json").exists()
    assert result["credentials"]["ok"] is True
    assert result["moved_to"].endswith("van-gogh-key.json")


# ── rekey updates the plugin, not just the catalog ────────────────────────────

def _clone(tmp_path, monkeypatch):
    clone = tmp_path / "mp" / ck.PLUGIN_NAME
    (clone / ".git").mkdir(parents=True)
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    monkeypatch.setattr(ck.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(ck, "_rewrite_known_marketplaces", lambda url: True)


def test_rekey_runs_plugin_update_and_reads_its_outcome(home, tmp_path, monkeypatch):
    _clone(tmp_path, monkeypatch)
    calls = []

    def fake(args, token):
        calls.append(args)
        if args[:2] == ["plugin", "update"]:
            return {"ok": True, "stdout": 'Warning: x\n{"outcome": "ok", '
                                         '"updateOutcome": "updated", "newVersion": "0.3.0"}\n'}
        return {"ok": True, "stdout": ""}
    monkeypatch.setattr(ck, "_run_claude", fake)
    key = ck.parse_key_file(_write(tmp_path / "k.json", _v2()))
    result = ck.rekey(key)
    assert result["ok"] is True and result["update_outcome"] == "updated"
    assert result["version"] == "0.3.0"
    assert calls == [["plugin", "marketplace", "update", "van-gogh-custom"],
                     ["plugin", "update", "van-gogh-custom@van-gogh-custom", "--json"]]


def test_rekey_does_not_trust_exit_zero_without_an_outcome(home, tmp_path, monkeypatch):
    _clone(tmp_path, monkeypatch)
    monkeypatch.setattr(ck, "_run_claude", lambda args, token: {"ok": True, "stdout": "done\n"})
    result = ck.rekey(ck.parse_key_file(_write(tmp_path / "k.json", _v2())))
    assert result["ok"] is False and result["step"] == "plugin update"


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_discover_and_install_print_json(home, capsys, monkeypatch):
    monkeypatch.setattr("config_loader.force_utf8_io", lambda: None)
    monkeypatch.setattr(ck, "force_utf8_io", lambda: None)
    assert ck.main(["discover"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] == 0 and "instruction" in payload
    source = _write(home["downloads"] / "van-gogh-key-jane-doe.json", _v2())
    assert ck.main(["install", "--file", str(source)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["mode"] == "v2"
    assert "github_pat_abc123" not in json.dumps(payload)
    assert "GOCSPX-fake" not in json.dumps(payload)
    assert ck.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["keyfile_present"] is True
