"""Companion key file handling (app/companion_key.py): parse/refuse, env
storage, and the bootstrap flows with the claude CLI mocked out."""

import json
import os

import pytest

import companion_key as ck
import user_state


def _key(tmp_path, **overrides):
    data = {"kind": "van-gogh-key",
            "repo": "NVentures/project-van-gogh-custom-test-client",
            "token": "github_pat_abc123", "issued": "2026-09-09"}
    data.update(overrides)
    path = tmp_path / "key.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_key_parses(tmp_path):
    key = ck.parse_key_file(_key(tmp_path))
    assert key["repo"].endswith("test-client")
    assert key["token"] == "github_pat_abc123"


def test_wrong_kind_is_refused(tmp_path):
    with pytest.raises(ck.KeyFileError, match="not a Van Gogh key"):
        ck.parse_key_file(_key(tmp_path, kind="something-else"))


def test_malformed_repo_and_token_are_refused(tmp_path):
    with pytest.raises(ck.KeyFileError, match="repo"):
        ck.parse_key_file(_key(tmp_path, repo="https://github.com/x/y"))
    with pytest.raises(ck.KeyFileError, match="token"):
        ck.parse_key_file(_key(tmp_path, token=""))
    with pytest.raises(ck.KeyFileError, match="token"):
        ck.parse_key_file(_key(tmp_path, token="has space"))


def test_missing_and_invalid_files_are_refused(tmp_path):
    with pytest.raises(ck.KeyFileError, match="cannot read"):
        ck.parse_key_file(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ck.KeyFileError, match="not valid JSON"):
        ck.parse_key_file(bad)


def test_redact_never_leaks_the_token():
    assert "sekrit" not in ck.redact("fatal: auth sekrit failed", "sekrit")


def test_ingest_stores_env_and_calls_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    calls = []
    monkeypatch.setattr(ck, "_run_claude",
                        lambda args, token: (calls.append(args), {"ok": True})[1])
    key = ck.parse_key_file(_key(tmp_path))
    result = ck.ingest(key)
    assert result == {"ok": True, "mode": "ingest", "repo": key["repo"],
                      "plugin": "van-gogh-custom"}
    assert calls[0][:3] == ["plugin", "marketplace", "add"]
    assert "x-access-token:github_pat_abc123@" in calls[0][3]
    assert calls[1] == ["plugin", "install", "van-gogh-custom@van-gogh-custom"]
    env_text = user_state.env_file().read_text(encoding="utf-8")
    assert f"{ck.ENV_REPO}={key['repo']}" in env_text
    assert f"{ck.ENV_TOKEN}=github_pat_abc123" in env_text


def test_failed_marketplace_add_reports_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    monkeypatch.setattr(ck, "_run_claude",
                        lambda args, token: {"ok": False, "step": "x",
                                             "error": "denied"})
    result = ck.ingest(ck.parse_key_file(_key(tmp_path)))
    assert result["ok"] is False and "hint" in result


def test_rekey_without_a_clone_falls_back_to_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    monkeypatch.setattr(ck, "_run_claude", lambda args, token: {"ok": True})
    result = ck.rekey(ck.parse_key_file(_key(tmp_path)))
    assert result["ok"] is True and result["mode"] == "ingest"


def test_status_reads_env_without_exposing_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "marketplaces_dir", lambda: tmp_path / "mp")
    monkeypatch.setenv(ck.ENV_REPO, "NVentures/x")
    monkeypatch.setenv(ck.ENV_TOKEN, "github_pat_abc123")
    result = ck.status()
    assert result["token_present"] is True
    assert "github_pat_abc123" not in json.dumps(result)
