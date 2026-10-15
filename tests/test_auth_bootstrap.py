"""auth_bootstrap.py helper tests.

Covers the pure, testable surface of the OAuth onboarding script: label
derivation, the .env upsert/relabel mutators, redirect-code extraction, and the
two-phase headless flow's PKCE state round-trip (phase 1 persists state + prints
the URL marker; phase 2 loads it, exchanges the pasted code, and clears state).
The network-bound bits (run_local_server, fetch_token, Gmail lookup) are driven
through tiny fakes so no real OAuth call is made.
"""
import json

import auth_bootstrap as ab


# ── label helpers ─────────────────────────────────────────────────────────────

def test_label_suffix_normalises():
    assert ab._label_suffix("  Acme Corp ") == "ACME_CORP"
    assert ab._label_suffix("qx46-energy") == "QX46_ENERGY"


def test_derive_label_personal_for_gmail():
    assert ab._derive_label("someone@gmail.com") == "Personal"
    assert ab._derive_label("someone@googlemail.com") == "Personal"


def test_derive_label_from_company_domain():
    assert ab._derive_label("you@northwindpartners.com") == "Northwindpartners"
    assert ab._derive_label("a@qxcorp.com") == "Qxcorp"


# ── .env mutators ─────────────────────────────────────────────────────────────

def test_upsert_inserts_then_updates(tmp_path):
    env = tmp_path / ".env"
    ab._upsert_env(str(env), "GOOGLE_REFRESH_TOKEN_GMAIL", "tok1")
    assert "GOOGLE_REFRESH_TOKEN_GMAIL=tok1" in env.read_text(encoding="utf-8")
    # Second call updates in place rather than appending a duplicate.
    ab._upsert_env(str(env), "GOOGLE_REFRESH_TOKEN_GMAIL", "tok2")
    body = env.read_text(encoding="utf-8")
    assert "tok1" not in body
    assert body.count("GOOGLE_REFRESH_TOKEN_GMAIL=") == 1
    assert "GOOGLE_REFRESH_TOKEN_GMAIL=tok2" in body


def test_upsert_preserves_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GRANOLA_API_KEY=abc\n", encoding="utf-8")
    ab._upsert_env(str(env), "MS_GRAPH_REFRESH_TOKEN_OUTLOOK", "mtok")
    body = env.read_text(encoding="utf-8")
    assert "GRANOLA_API_KEY=abc" in body
    assert "MS_GRAPH_REFRESH_TOKEN_OUTLOOK=mtok" in body


def test_rename_env_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_GOOGLE=keepme\n", encoding="utf-8")
    assert ab._rename_env_key(str(env), "GOOGLE_REFRESH_TOKEN_GOOGLE", "GOOGLE_REFRESH_TOKEN_GMAIL") is True
    body = env.read_text(encoding="utf-8")
    assert "GOOGLE_REFRESH_TOKEN_GMAIL=keepme" in body
    assert "GOOGLE_REFRESH_TOKEN_GOOGLE=" not in body


def test_rename_env_key_missing_returns_false(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert ab._rename_env_key(str(env), "NOPE", "ALSO_NOPE") is False


# ── redirect-code extraction ──────────────────────────────────────────────────

def test_code_from_redirect_extracts_code():
    url = "http://localhost:8080/?state=xyz&code=4/abcDEF&scope=...mail"
    assert ab._code_from_redirect(url) == "4/abcDEF"


def test_code_from_redirect_raises_without_code():
    import pytest

    with pytest.raises(ValueError):
        ab._code_from_redirect("http://localhost:8080/?state=xyz&error=access_denied")


# ── headless two-phase flow ───────────────────────────────────────────────────

class _FakeFlow:
    """Minimal stand-in for InstalledAppFlow used by the headless helpers."""

    def __init__(self):
        self.redirect_uri = None
        self.credentials = "FAKE_CREDS"
        self.fetched = None

    def authorization_url(self, **kwargs):
        self.auth_kwargs = kwargs
        return "https://accounts.google.com/o/oauth2/auth?fake=1", "STATE123"

    def fetch_token(self, **kwargs):
        self.fetched = kwargs


def test_headless_phase1_persists_state_and_prints_url(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    monkeypatch.setattr(ab, "_google_state_file", lambda: state)
    flow = _FakeFlow()

    ab._google_headless_phase1(flow, "Northwind", "/tmp/.env")

    out = capsys.readouterr().out
    assert "===VANGOGH_HEADLESS_URL===" in out
    assert "https://accounts.google.com/o/oauth2/auth?fake=1" in out
    assert flow.redirect_uri == ab.HEADLESS_REDIRECT_URI
    # PKCE challenge was sent on the auth request.
    assert flow.auth_kwargs["code_challenge_method"] == "S256"

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["label"] == "Northwind"
    assert saved["redirect_uri"] == ab.HEADLESS_REDIRECT_URI
    assert saved["code_verifier"]  # a verifier was stored for phase 2


def test_headless_phase2_exchanges_code_and_clears_state(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "redirect_uri": ab.HEADLESS_REDIRECT_URI,
                "code_verifier": "VERIFIER",
                "state": "STATE123",
                "label": "Northwind",
                "env_file": "/tmp/.env",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "_google_state_file", lambda: state)
    flow = _FakeFlow()

    creds, label = ab._google_headless_phase2(
        flow, "http://localhost:8080/?state=STATE123&code=AUTHCODE", None
    )

    assert creds == "FAKE_CREDS"
    assert label == "Northwind"  # falls back to the saved label when none passed
    assert flow.fetched == {"code": "AUTHCODE", "code_verifier": "VERIFIER"}
    assert not state.exists()  # single-use state file is removed


def test_headless_phase1_then_phase2_round_trip(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    monkeypatch.setattr(ab, "_google_state_file", lambda: state)

    flow1 = _FakeFlow()
    ab._google_headless_phase1(flow1, None, "/tmp/.env")
    verifier = json.loads(state.read_text(encoding="utf-8"))["code_verifier"]

    flow2 = _FakeFlow()
    creds, label = ab._google_headless_phase2(
        flow2, "http://localhost:8080/?code=ROUNDTRIP", "Forced"
    )
    assert creds == "FAKE_CREDS"
    assert label == "Forced"  # explicit label wins over the saved (None) one
    assert flow2.fetched["code"] == "ROUNDTRIP"
    assert flow2.fetched["code_verifier"] == verifier


# ── Microsoft device-code two-phase flow ──────────────────────────────────────

class _FakeMsalApp:
    """Minimal stand-in for an MSAL PublicClientApplication."""

    def __init__(self):
        self.polled = None

    def initiate_device_flow(self, scopes):
        return {
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "device_code": "DEVICE_CODE_BLOB",
            "message": "To sign in, open https://microsoft.com/devicelogin and enter ABCD-EFGH.",
        }

    def acquire_token_by_device_flow(self, flow):
        self.polled = flow
        return {
            "refresh_token": "MS_REFRESH",
            "access_token": "MS_ACCESS",
            "id_token_claims": {"preferred_username": "user@outlook.com"},
        }


def test_ms_device_phase1_surfaces_code_before_polling(monkeypatch, tmp_path, capsys):
    state = tmp_path / "ms.json"
    monkeypatch.setattr(ab, "_ms_state_file", lambda: state)
    app = _FakeMsalApp()

    ab._ms_device_phase1(app, "Outlook", "/tmp/.env")

    out = capsys.readouterr().out
    # The whole point of the fix: the URL + code are printed (and the function
    # returns) WITHOUT polling, so the orchestrator can show them to the user.
    assert "===VANGOGH_DEVICE_CODE===" in out
    assert "verification_uri=https://microsoft.com/devicelogin" in out
    assert "user_code=ABCD-EFGH" in out
    assert app.polled is None  # phase 1 must not block on the poll

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["flow"]["device_code"] == "DEVICE_CODE_BLOB"
    assert saved["label"] == "Outlook"


def test_ms_device_phase1_errors_when_initiate_fails(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(ab, "_ms_state_file", lambda: tmp_path / "ms.json")

    class _BadApp:
        def initiate_device_flow(self, scopes):
            return {"error_description": "bad client"}

    with pytest.raises(SystemExit):
        ab._ms_device_phase1(_BadApp(), None, "/tmp/.env")


def test_ms_device_phase2_polls_saved_flow_and_clears_state(monkeypatch, tmp_path):
    state = tmp_path / "ms.json"
    state.write_text(
        json.dumps(
            {
                "flow": {"device_code": "DEVICE_CODE_BLOB", "interval": 5, "expires_at": 9e18},
                "label": "Outlook",
                "env_file": "/tmp/.env",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "_ms_state_file", lambda: state)
    app = _FakeMsalApp()

    result, label = ab._ms_device_phase2(app, None)

    assert result["refresh_token"] == "MS_REFRESH"
    assert label == "Outlook"  # falls back to the saved label
    assert app.polled["device_code"] == "DEVICE_CODE_BLOB"
    assert not state.exists()  # single-use state file removed


def test_ms_device_phase2_missing_state_exits(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(ab, "_ms_state_file", lambda: tmp_path / "nope.json")
    with pytest.raises(SystemExit):
        ab._ms_device_phase2(_FakeMsalApp(), None)
