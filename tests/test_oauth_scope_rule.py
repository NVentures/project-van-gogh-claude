"""Scopes are named at consent and nowhere else (app/oauth_scopes.py).

Adding a permission to a consent list must never disconnect an account that
was connected before it. Google keeps that promise by naming no scopes on a
refresh (tests/test_google_refresh_scopes.py). Microsoft cannot: MSAL requires
a list, so an account is refreshed with the scopes IT was granted, stored
beside its token at sign-in, or a frozen baseline for an account that signed
in before they were stored.

The load-bearing test here is the last one: it appends a scope to the live
consent list and proves an existing account's refresh is unchanged. That is
the exact scenario that broke Google, run against the Microsoft path.
"""
import pytest

import auth_bootstrap as ab
import google_client
import microsoft_client as mc
import oauth_scopes


# ── one list, not several ────────────────────────────────────────────────────

def test_every_module_reads_the_same_list():
    assert ab.GOOGLE_SCOPES is oauth_scopes.GOOGLE_SCOPES
    assert google_client.GOOGLE_SCOPES is oauth_scopes.GOOGLE_SCOPES
    assert ab.MS_SCOPES is oauth_scopes.MS_SCOPES
    assert mc.GRAPH_SCOPES is oauth_scopes.MS_SCOPES


def test_the_scope_strings_are_written_down_once():
    """A second literal copy is how the two lists drifted apart before."""
    from pathlib import Path
    app = Path(google_client.__file__).resolve().parent
    for probe in ("auth/gmail.modify", "Mail.ReadWrite"):
        holders = sorted(p.name for p in app.glob("*.py")
                         if probe in p.read_text(encoding="utf-8"))
        assert holders == ["oauth_scopes.py"], (probe, holders)


# ── Microsoft: refresh with what the account was granted ─────────────────────

def test_an_account_refreshes_with_its_stored_grant(monkeypatch):
    monkeypatch.setenv("MS_GRAPH_SCOPES_WORK",
                       "https://graph.microsoft.com/Mail.ReadWrite,"
                       "https://graph.microsoft.com/User.Read")
    assert mc.refresh_scopes("Work") == [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/User.Read",
    ]


def test_an_account_with_no_stored_grant_uses_the_frozen_baseline(monkeypatch):
    monkeypatch.delenv("MS_GRAPH_SCOPES_OLDER", raising=False)
    assert mc.refresh_scopes("Older") == list(oauth_scopes.MS_BASELINE_SCOPES)


def test_msals_own_scopes_are_never_sent_back(monkeypatch):
    """MSAL adds openid/profile/offline_access itself and raises if given them,
    and Entra returns them in the grant, so they are filtered both ways."""
    monkeypatch.setenv("MS_GRAPH_SCOPES_WORK",
                       "openid,profile,offline_access,"
                       "https://graph.microsoft.com/User.Read")
    assert mc.refresh_scopes("Work") == ["https://graph.microsoft.com/User.Read"]
    granted = ab._granted_ms_scopes(
        {"scope": "openid profile offline_access "
                  "https://graph.microsoft.com/Mail.Send"})
    assert granted == ["https://graph.microsoft.com/Mail.Send"]


def test_a_new_consent_scope_leaves_existing_accounts_alone(monkeypatch):
    """THE ONE THAT MATTERS. Adding a scope to the consent list must not change
    what an existing account asks for on refresh.

    MUTATION: make refresh_scopes return GRAPH_SCOPES and this fails, for the
    stored account and the baseline one alike. That mutation is what shipped
    on the Google side, and it disconnected every older account.
    """
    monkeypatch.setenv("MS_GRAPH_SCOPES_WORK",
                       "https://graph.microsoft.com/Mail.ReadWrite")
    monkeypatch.delenv("MS_GRAPH_SCOPES_OLDER", raising=False)
    before = (mc.refresh_scopes("Work"), mc.refresh_scopes("Older"))

    monkeypatch.setattr(oauth_scopes, "MS_SCOPES",
                        list(oauth_scopes.MS_SCOPES) +
                        ["https://graph.microsoft.com/Files.ReadWrite"],
                        raising=True)
    monkeypatch.setattr(mc, "GRAPH_SCOPES", oauth_scopes.MS_SCOPES)

    assert (mc.refresh_scopes("Work"), mc.refresh_scopes("Older")) == before
    for scopes in before:
        assert "Files.ReadWrite" not in " ".join(scopes)


def test_the_baseline_is_frozen_history_not_a_copy_of_today():
    """MS_BASELINE_SCOPES records what accounts signing in before the grant was
    stored actually had. A new scope belongs in MS_SCOPES only; if this ever
    fails, do not edit the baseline, that would reintroduce the bug."""
    assert set(oauth_scopes.MS_BASELINE_SCOPES) <= set(oauth_scopes.MS_SCOPES)
    assert isinstance(oauth_scopes.MS_BASELINE_SCOPES, tuple)


def test_sign_in_stores_the_granted_scopes(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.setattr(ab, "_ms_app", lambda: object())
    monkeypatch.setattr(ab, "_ms_device_phase2", lambda app, label: (
        {"refresh_token": "rt", "scope": "https://graph.microsoft.com/Mail.Send "
                                         "https://graph.microsoft.com/User.Read",
         "id_token_claims": {"preferred_username": "someone@example.com"}},
        "Work"))
    ab.bootstrap_microsoft("Work", str(env), complete_device_code=True)
    written = env.read_text(encoding="utf-8")
    assert "MS_GRAPH_REFRESH_TOKEN_WORK=rt" in written
    assert ("MS_GRAPH_SCOPES_WORK=https://graph.microsoft.com/Mail.Send,"
            "https://graph.microsoft.com/User.Read") in written


# ── re-authorizing must never blank a working token ──────────────────────────

@pytest.mark.parametrize("token", ["", None, "   "])
def test_a_sign_in_that_returns_no_token_refuses_to_save(tmp_path, token):
    """Signing in again for a connected label overwrites its token in place,
    which is the whole re-authorize path. An empty result written through
    would disconnect the account the user was trying to repair."""
    env = tmp_path / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_WORK=the-working-token\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ab._save_refresh_token(str(env), "GOOGLE_REFRESH_TOKEN_WORK", token)
    assert exc.value.code == 1
    assert env.read_text(encoding="utf-8") == (
        "GOOGLE_REFRESH_TOKEN_WORK=the-working-token\n")


def test_re_authorizing_the_same_label_replaces_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_WORK=old\nOTHER_KEY=kept\n", encoding="utf-8")
    ab._save_refresh_token(str(env), "GOOGLE_REFRESH_TOKEN_WORK", "new")
    lines = [ln for ln in env.read_text(encoding="utf-8").splitlines() if ln]
    assert lines == ["GOOGLE_REFRESH_TOKEN_WORK=new", "OTHER_KEY=kept"]


def test_relabelling_carries_the_granted_scopes_with_the_token(tmp_path, capsys):
    """A renamed account must not silently fall back to the baseline list."""
    env = tmp_path / ".env"
    env.write_text("MS_GRAPH_REFRESH_TOKEN_OLD=rt\n"
                   "MS_GRAPH_SCOPES_OLD=https://graph.microsoft.com/User.Read\n",
                   encoding="utf-8")
    ab.relabel("microsoft", "Old", "New", str(env))
    written = env.read_text(encoding="utf-8")
    assert "MS_GRAPH_REFRESH_TOKEN_NEW=rt" in written
    assert ("MS_GRAPH_SCOPES_NEW=https://graph.microsoft.com/User.Read"
            in written)
    assert "_OLD=" not in written
