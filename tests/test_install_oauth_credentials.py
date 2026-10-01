"""The user supplies the OAuth app credentials, at install and after a rotation.

The clients read ~/.config/van-gogh/<file> before the copy bundled in the
plugin, so installing there overrides the shipped credential without touching
the plugin cache (a marketplace update re-clones it). These tests pin the
contract the install skill branches on: provider auto-detection, refusal to
write anything malformed, and a status call that says whether the person has
supplied their own yet.
"""
import json
import sys

import pytest

import install_oauth_credentials as ioc

GOOGLE = {"installed": {"client_id": "123.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-fake",
                        "redirect_uris": ["http://localhost"]}}
MICROSOFT = {"client_id": "f783a7e4-1111-2222-3333-444444444444",
             "tenant_id": "common"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point the state dir at a temp dir so no test touches the real one."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ioc.user_state, "state_dir", lambda: tmp_path)
    return tmp_path


def _run(capsys, raw) -> tuple:
    code = ioc.install(raw if isinstance(raw, str) else json.dumps(raw))
    return code, json.loads(capsys.readouterr().out)


# ── provider detection ────────────────────────────────────────────────────────

def test_a_google_desktop_file_is_recognized_without_being_told():
    provider, inner, err = ioc.detect_provider(GOOGLE)
    assert (provider, err) == ("google", "")
    assert inner["client_secret"] == "GOCSPX-fake"


def test_a_microsoft_public_client_is_recognized_without_being_told():
    provider, inner, err = ioc.detect_provider(MICROSOFT)
    assert (provider, err) == ("microsoft", "")
    assert inner["client_id"].startswith("f783a7e4")


def test_a_google_web_client_is_still_read_as_google():
    """Console hands out "web" for some project types; same shape, same owner."""
    provider, _, err = ioc.detect_provider({"web": {"client_id": "w", "client_secret": "s"}})
    assert (provider, err) == ("google", "")


def test_microsoft_is_not_required_to_carry_a_secret():
    """It is a public client by design; demanding one would reject a valid file."""
    assert ioc.validate("microsoft", MICROSOFT) == []


# ── installing ────────────────────────────────────────────────────────────────

def test_installing_google_lands_where_the_clients_look(state, capsys):
    code, out = _run(capsys, GOOGLE)
    assert code == 0 and out["ok"] and out["provider"] == "google"
    landed = state / ioc.GOOGLE_FILENAME
    assert landed.exists() and out["path"] == str(landed)
    assert json.loads(landed.read_text(encoding="utf-8")) == GOOGLE
    assert out["replaced"] is False


def test_installing_microsoft_lands_in_its_own_file(state, capsys):
    _run(capsys, MICROSOFT)
    assert json.loads((state / ioc.MS_FILENAME).read_text(encoding="utf-8")) == MICROSOFT
    assert not (state / ioc.GOOGLE_FILENAME).exists(), "wrote to the wrong provider"


def test_a_rotated_key_replaces_the_stale_one_and_says_so(state, capsys):
    """The stale-credential path: paste the new file, the old one is gone."""
    _run(capsys, GOOGLE)
    rotated = {"installed": {"client_id": "NEW.apps.googleusercontent.com",
                             "client_secret": "GOCSPX-rotated"}}
    code, out = _run(capsys, rotated)
    assert code == 0 and out["replaced"] is True
    on_disk = json.loads((state / ioc.GOOGLE_FILENAME).read_text(encoding="utf-8"))
    assert on_disk["installed"]["client_secret"] == "GOCSPX-rotated"


def test_a_utf8_bom_and_crlf_paste_still_lands_parseable(state, capsys):
    """Windows clipboards and downloads carry both; json.load must not choke."""
    raw = "﻿" + json.dumps(GOOGLE).replace("\n", "\r\n")
    code, _ = ioc.install(raw), capsys.readouterr()
    assert code == 0
    assert json.loads((state / ioc.GOOGLE_FILENAME).read_text(encoding="utf-8")) == GOOGLE


def test_the_secret_value_is_never_printed(state, capsys):
    _, out = _run(capsys, GOOGLE)
    assert "GOCSPX-fake" not in json.dumps(out)
    assert out["has_secret"] is True, "presence is reported, the value is not"


# ── refusing bad input ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,because", [
    ("", "empty paste"),
    ("   ", "whitespace only"),
    ("not json at all", "not JSON"),
    ("[]", "a list, not an object"),
    ('{"nope": 1}', "no recognizable provider"),
    ('{"installed": {"client_id": "x"}}', "Google without a secret"),
    ('{"installed": {"client_secret": "s"}}', "Google without a client_id"),
])
def test_malformed_input_is_refused_and_nothing_is_written(state, capsys, raw, because):
    code = ioc.install(raw)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ok"] is False, because
    assert out["error"]
    assert list(state.iterdir()) == [], f"wrote a file for {because}"


# ── status, which the skill branches on ───────────────────────────────────────

def test_status_reports_needed_until_the_user_supplies_their_own(state, capsys):
    ioc.status()
    before = json.loads(capsys.readouterr().out)
    # The bundled copy may or may not exist in a given checkout; either way the
    # user has not supplied one, so both must still be flagged as needed.
    assert before["needs_google"] and before["needs_microsoft"]
    assert before["google"]["source"] in ("bundled", "missing")

    _run(capsys, GOOGLE)
    _run(capsys, MICROSOFT)
    ioc.status()
    after = json.loads(capsys.readouterr().out)
    assert after["google"]["source"] == "user" and after["google"]["valid"]
    assert after["microsoft"]["source"] == "user" and after["microsoft"]["valid"]
    assert not after["needs_google"] and not after["needs_microsoft"]


def test_status_flags_a_corrupt_installed_file_as_needing_replacement(state, capsys):
    (state / ioc.GOOGLE_FILENAME).write_text("{ truncated", encoding="utf-8")
    ioc.status()
    out = json.loads(capsys.readouterr().out)
    assert out["google"]["source"] == "user"
    assert out["google"]["valid"] is False
    assert out["needs_google"] is True, "a corrupt file must not read as satisfied"


def test_status_never_prints_a_secret(state, capsys):
    _run(capsys, GOOGLE)
    ioc.status()
    assert "GOCSPX-fake" not in capsys.readouterr().out


# ── how it is written, not just what ──────────────────────────────────────────

@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows has no POSIX mode bits; st_mode is always 0o666 "
                           "and the file inherits the user profile ACL instead")
def test_the_installed_credential_ends_up_private(state, capsys):
    """End state only. This does NOT prove the window is closed: a plain
    write_text followed by chmod also ends at 0600 and still exposes the secret
    in between. The test below is the one that catches that."""
    import stat
    _run(capsys, GOOGLE)
    mode = stat.S_IMODE((state / ioc.GOOGLE_FILENAME).stat().st_mode)
    assert mode & 0o077 == 0, f"group/other can read the credential: {oct(mode)}"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows has no POSIX mode bits; st_mode is always 0o666 "
                           "and the file inherits the user profile ACL instead")
def test_the_secret_is_never_world_readable_even_briefly(state, capsys, monkeypatch):
    """The real guard. write_text creates at the umask default (0644 here) and
    only tightens afterwards, so the secret is readable by any local user until
    the chmod lands. Asserting on the final mode cannot see that window, so this
    samples the mode at the moment of replace, while the bytes are on disk."""
    import stat
    seen = {}
    real_replace = ioc.os.replace

    def spy(src, dst):
        seen["mode"] = stat.S_IMODE(ioc.os.stat(src).st_mode)
        return real_replace(src, dst)

    monkeypatch.setattr(ioc.os, "replace", spy)
    _run(capsys, GOOGLE)
    assert seen["mode"] & 0o077 == 0, f"temp file was readable: {oct(seen['mode'])}"


def test_a_failed_write_leaves_the_previous_credential_intact(state, capsys, monkeypatch):
    """A rotation that dies mid-write must not destroy the only working key.
    Without the temp-file hop the target is truncated in place and all auth
    breaks, with nothing to fall back to."""
    _run(capsys, GOOGLE)
    target = state / ioc.GOOGLE_FILENAME
    before = target.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(ioc.os, "replace", boom)
    code, out = _run(capsys, {"installed": {"client_id": "n", "client_secret": "s"}})
    assert code == 1 and out["ok"] is False and "could not write" in out["error"]
    assert target.read_text(encoding="utf-8") == before, "clobbered the good credential"
    leftovers = [p.name for p in state.iterdir() if p.name.endswith(".van-gogh-tmp")]
    assert leftovers == [], f"left a temp file behind: {leftovers}"
