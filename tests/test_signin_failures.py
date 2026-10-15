"""Sign-in failure paths in auth_bootstrap.py (punch list P29).

Fixtures under tests/fixtures/signin/ are real CAPTURED payloads (each
`_source` names the request that produced it and the values changed): a
Microsoft token-endpoint error body set to the admin-consent class, a Google
authorization-endpoint error redirect set to access_denied, a Google refresh
grant with one scope removed. A test below holds every fixture to that.
The browser is a stub that counts launches; nothing here touches the
network. Every failure must come back as one JSON object whose `line` is at
most 120 characters and names the resume command, and a second account on a
domain must never take the first one's label.
"""
import json
import os
from pathlib import Path

import pytest

import auth_bootstrap as ab
from oauth_scopes import GOOGLE_SCOPES, MS_SCOPES

FIXTURES = Path(__file__).parent / "fixtures" / "signin"
CALENDAR = "https://www.googleapis.com/auth/calendar"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def signin(tmp_path, monkeypatch):
    """A temp state dir holding both app credentials, JSON mode on."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    google = tmp_path / "google_client_secret.json"
    google.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}),
                      encoding="utf-8")
    ms = tmp_path / "ms_client_secret.json"
    ms.write_text(json.dumps({"client_id": "f783a7e4-0000-4024-8af1-000000000000"}),
                  encoding="utf-8")
    monkeypatch.setattr(ab, "GOOGLE_CLIENT_SECRET_PATH", str(google))
    monkeypatch.setattr(ab, "MS_CLIENT_SECRETS_PATH", str(ms))
    monkeypatch.setattr(ab, "_JSON", True)
    monkeypatch.setattr(ab, "_EXPECT_EMAIL", None)
    monkeypatch.setattr(ab, "_has_browser", lambda: True)
    monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)
    return tmp_path


def _run(capsys, fn, *args, **kwargs):
    """Call a bootstrap function; return (exit code or 0, parsed JSON)."""
    code = 0
    try:
        fn(*args, **kwargs)
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr().out
    return code, json.loads(out)


# ── Google stubs ─────────────────────────────────────────────────────────────

class _Creds:
    def __init__(self, granted, refresh_token="rt-1"):
        self.granted_scopes = granted
        self.scopes = GOOGLE_SCOPES
        self.refresh_token = refresh_token


class _FakeFlow:
    """InstalledAppFlow stand-in. `outcome` is a credentials object to return
    or an exception to raise; every call is one browser launch."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.launches = 0
        self.kwargs = None

    def run_local_server(self, **kwargs):
        self.launches += 1
        self.kwargs = kwargs
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _install_flow(monkeypatch, flow):
    from google_auth_oauthlib.flow import InstalledAppFlow
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file",
                        staticmethod(lambda path, scopes: flow))


def _google_access_denied_error():
    """The exact exception oauthlib raises when the loopback redirect carries
    error=access_denied, built from the fixture the way the library does."""
    from oauthlib.oauth2.rfc6749.parameters import parse_authorization_code_response
    url = _fixture("google_access_denied.json")["redirect_url"].replace("http", "https", 1)
    with pytest.raises(Exception) as caught:
        parse_authorization_code_response(url)
    return caught.value


def _timeout_error():
    import google_auth_oauthlib.flow as flow_module
    cls = getattr(flow_module, "WSGITimeoutError", None)
    if cls is None:
        cls = type("WSGITimeoutError", (Exception,), {})
    return cls("Timed out waiting for response from authorization server")


# ── the fixtures are captures ────────────────────────────────────────────────

@pytest.mark.parametrize("name, keys", [
    ("ms_admin_consent.json", ["error", "error_description", "error_codes", "timestamp",
                               "trace_id", "correlation_id", "error_uri"]),
    ("google_token_missing_scope.json", ["access_token", "expires_in", "scope", "token_type"]),
    ("google_access_denied.json", ["redirect_url"]),
    ("ms_consumers_bogus_app.json", ["status", "location", "body"]),
    ("ms_organizations_unknown_app.json", ["status", "location", "body"]),
])
def test_every_fixture_is_a_capture_with_its_structure_kept(name, keys):
    data = _fixture(name)
    assert data["_source"].startswith("CAPTURED 2026-10-01: "), name
    assert [k for k in data if k != "_source"] == keys, name
    assert "invented" in data["_source"] or "id invented" in data["_source"], name


def test_the_microsoft_fixture_keeps_the_captured_types_and_names_the_class():
    data = _fixture("ms_admin_consent.json")
    assert isinstance(data["error_codes"], list) and data["error_codes"] == [65001]
    assert data["error_description"].startswith("AADSTS65001: ")
    assert "Trace ID: " in data["error_description"] and "Timestamp: " in data["error_description"]
    assert data["error_uri"].endswith("code=65001")
    assert "suberror" not in data  # not in the captured body; the class is read from the code


def test_the_google_token_fixture_is_the_real_grant_minus_calendar():
    data = _fixture("google_token_missing_scope.json")
    assert isinstance(data["expires_in"], int)
    assert "refresh_token" not in data and "id_token" not in data
    granted = data["scope"].split()
    assert CALENDAR not in granted
    assert set(GOOGLE_SCOPES) - set(granted) == {CALENDAR}


def test_the_google_redirect_fixture_keeps_the_captured_parameter_set():
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(_fixture("google_access_denied.json")["redirect_url"]).query)
    assert list(query) == ["state", "error_subtype", "error"]
    assert query["error"] == ["access_denied"]


# ── the line ─────────────────────────────────────────────────────────────────

CLASSES = ("admin_consent", "access_denied", "unverified_app", "missing_scope",
           "timeout", "no_credential", "no_refresh_token", "no_browser",
           "provider_error")


@pytest.mark.parametrize("provider", ["google", "microsoft"])
@pytest.mark.parametrize("error_class", CLASSES)
def test_every_failure_line_fits_and_names_the_resume_command(provider, error_class):
    long_detail = "your Google Calendar, Google Drive, your contacts, " * 4
    for detail in ("", "AADSTS50020", long_detail):
        line = ab.failure_line(error_class, provider, detail)
        assert "\n" not in line
        assert len(line) <= ab.LINE_MAX, (error_class, len(line), line)
        assert ab.RESUME_COMMAND in line, (error_class, line)
        # The checklist drills "never into Claude" for the setup line, so a
        # bare "type" would land in Terminal: every line says where it goes,
        # and ends on the command with nothing clipped after it.
        assert ("in claude, type " + ab.RESUME_COMMAND) in line.lower(), (error_class, line)
        assert line.endswith(ab.RESUME_COMMAND), (error_class, line)
        assert "test user" not in line and " us " not in line, (error_class, line)
        assert "; run" not in line and " run /" not in line, (error_class, line)


def test_the_unverified_app_line_names_who_to_ask_in_plain_words():
    line = ab.failure_line("unverified_app", "google")
    assert line == ("Google has not approved this account yet. Ask the Van Gogh team to add it. "
                    "In Claude, type /van-gogh:install-van-gogh")
    assert len(line) <= 120


def test_a_long_detail_is_shortened_at_a_word_and_never_the_sentence_around_it():
    line = ab.failure_line("missing_scope", "google", "contacts Gmail collected by the address book")
    assert len(line) <= 120
    assert line.endswith(". Tick every box next time: in Claude, type /van-gogh:install-van-gogh")
    words = line[len("The sign-in left out "):line.index(". Tick every box")].split()
    assert words and all(w in "contacts Gmail collected by the address book".split() for w in words)
    for scope in GOOGLE_SCOPES:  # every single permission fits whole
        phrase = ab.scope_words(scope)
        assert f"left out {phrase}. Tick" in ab.failure_line("missing_scope", "google", phrase)


# ── Google ────────────────────────────────────────────────────────────────────

def test_google_waits_five_minutes_asks_to_pick_an_account_and_relaxes_scope(
        signin, monkeypatch, capsys):
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES)))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    env = signin / ".env"
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert code == 0 and payload["ok"] is True
    assert flow.kwargs["timeout_seconds"] == 300
    assert "select_account" in flow.kwargs["prompt"]
    assert os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"
    assert payload["label"] == "Acme" and payload["env_var"] == "GOOGLE_REFRESH_TOKEN_ACME"
    text = env.read_text(encoding="utf-8")
    assert "GOOGLE_REFRESH_TOKEN_ACME=rt-1" in text
    assert "GOOGLE_ACCOUNT_EMAIL_ACME=jane@acme.com" in text


def test_google_access_denied_fixture_is_one_window_and_one_line(signin, monkeypatch, capsys):
    flow = _FakeFlow(_google_access_denied_error())
    _install_flow(monkeypatch, flow)
    env = signin / ".env"
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert code == 1 and payload["ok"] is False
    assert payload["error_class"] == "access_denied"
    assert flow.launches == 1
    assert len(payload["line"]) <= 120 and ab.RESUME_COMMAND in payload["line"]
    assert not env.exists()


def test_google_token_missing_a_scope_names_the_box_and_saves_nothing(
        signin, monkeypatch, capsys):
    token = _fixture("google_token_missing_scope.json")
    # A refresh grant carries no refresh_token; the first sign-in does.
    flow = _FakeFlow(_Creds(token["scope"].split(), "1//0gInventedRefreshToken"))
    _install_flow(monkeypatch, flow)
    env = signin / ".env"
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert code == 1
    assert payload["error_class"] == "missing_scope"
    assert payload["missing_scopes"] == [CALENDAR]
    assert "your Google Calendar" in payload["line"]
    assert len(payload["line"]) <= 120 and ab.RESUME_COMMAND in payload["line"]
    assert not env.exists()


def test_google_closed_tab_times_out_instead_of_hanging(signin, monkeypatch, capsys):
    flow = _FakeFlow(_timeout_error())
    _install_flow(monkeypatch, flow)
    code, payload = _run(capsys, ab.bootstrap_google, None, str(signin / ".env"))
    assert code == 1 and payload["error_class"] == "timeout"
    assert flow.launches == 1
    assert "5 minutes" in payload["line"]


def test_google_unverified_app_wording_is_its_own_class():
    text = ("access_denied: Van Gogh has not completed the Google verification "
            "process. The app is currently being tested, and can only be accessed "
            "by developer-approved testers.")
    assert ab.classify_google_text(text, error="access_denied")[0] == "unverified_app"
    assert ab.classify_google_text("access_denied: blocked by admin policy",
                                   error="access_denied")[0] == "admin_consent"
    assert ab.classify_google_text("access_denied", error="access_denied")[0] == "access_denied"


def test_pasted_redirect_carrying_an_error_is_classified_not_crashed(signin, monkeypatch, capsys):
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES)))
    _install_flow(monkeypatch, flow)
    url = _fixture("google_access_denied.json")["redirect_url"]
    code, payload = _run(capsys, ab.bootstrap_google, None, str(signin / ".env"),
                         redirect_response=url)
    assert code == 1 and payload["error_class"] == "access_denied"
    assert flow.launches == 0


def test_expect_email_mismatch_is_reported_not_failed(signin, monkeypatch, capsys):
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES)))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    monkeypatch.setattr(ab, "_EXPECT_EMAIL", "jane@other.com")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(signin / ".env"))
    assert code == 0 and payload["ok"] is True
    assert payload["email_matches_hint"] is False
    monkeypatch.setattr(ab, "_EXPECT_EMAIL", "JANE@acme.com")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(signin / ".env"))
    assert payload["email_matches_hint"] is True


def test_cli_json_and_expect_email_flags(signin, monkeypatch, capsys):
    monkeypatch.setattr(ab, "_JSON", False)
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES)))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    monkeypatch.setattr(ab, "force_utf8_io", lambda: None, raising=False)
    monkeypatch.setattr("config_loader.force_utf8_io", lambda: None)
    code, payload = _run(capsys, ab.main, ["--provider", "google", "--json",
                                           "--expect-email", "jane@acme.com",
                                           "--env-file", str(signin / ".env")])
    assert code == 0 and payload["ok"] is True
    assert payload["email_matches_hint"] is True
    assert set(payload) >= {"ok", "provider", "email", "label", "env_var",
                            "error_class", "line"}


def test_human_output_is_the_default(signin, monkeypatch, capsys):
    monkeypatch.setattr(ab, "_JSON", False)
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES)))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    ab.bootstrap_google(None, str(signin / ".env"))
    out = capsys.readouterr().out
    assert "===VANGOGH_AUTH===" in out and "label=Acme" in out


# ── labels never collide ─────────────────────────────────────────────────────

def test_second_account_on_the_same_domain_gets_its_own_label(signin, monkeypatch, capsys):
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=first-token\n"
                   "GOOGLE_ACCOUNT_EMAIL_ACME=first@acme.com\n", encoding="utf-8")
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES), "second-token"))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "second@acme.com")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert code == 0
    assert payload["label"] == "Acme 2" and payload["label_adjusted"] is True
    assert payload["label_derived"] == "Acme"
    text = env.read_text(encoding="utf-8")
    assert "GOOGLE_REFRESH_TOKEN_ACME=first-token" in text
    assert "GOOGLE_REFRESH_TOKEN_ACME_2=second-token" in text


def test_same_address_signing_in_again_keeps_its_label(signin, monkeypatch, capsys):
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=old\n"
                   "GOOGLE_ACCOUNT_EMAIL_ACME=jane@acme.com\n", encoding="utf-8")
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES), "new"))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "Jane@Acme.com")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert payload["label"] == "Acme" and payload["label_adjusted"] is False
    assert "GOOGLE_REFRESH_TOKEN_ACME=new" in env.read_text(encoding="utf-8")


def test_a_token_with_no_recorded_address_is_never_overwritten_by_guess(signin, monkeypatch, capsys):
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_PERSONAL=older-install\n", encoding="utf-8")
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES), "new"))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@gmail.com")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert payload["label"] == "Personal Gmail"
    assert "GOOGLE_REFRESH_TOKEN_PERSONAL=older-install" in env.read_text(encoding="utf-8")


def test_explicit_label_always_wins(signin, monkeypatch, capsys):
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=old\n"
                   "GOOGLE_ACCOUNT_EMAIL_ACME=other@acme.com\n", encoding="utf-8")
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES), "new"))
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    code, payload = _run(capsys, ab.bootstrap_google, "Acme", str(env))
    assert payload["label"] == "Acme"
    assert "GOOGLE_REFRESH_TOKEN_ACME=new" in env.read_text(encoding="utf-8")


def test_relabel_carries_the_recorded_address(signin, capsys, monkeypatch):
    monkeypatch.setattr(ab, "_JSON", False)
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=t\nGOOGLE_ACCOUNT_EMAIL_ACME=jane@acme.com\n",
                   encoding="utf-8")
    ab.relabel("google", "Acme", "Work", str(env))
    text = env.read_text(encoding="utf-8")
    assert "GOOGLE_REFRESH_TOKEN_WORK=t" in text
    assert "GOOGLE_ACCOUNT_EMAIL_WORK=jane@acme.com" in text


# ── Microsoft ─────────────────────────────────────────────────────────────────

class _FakeMsApp:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.launches = 0

    def acquire_token_interactive(self, scopes, prompt, timeout):
        self.launches += 1
        assert scopes == MS_SCOPES and prompt == "select_account"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def initiate_device_flow(self, scopes):
        return {"user_code": "ABCD-EFGH", "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900, "device_code": "blob", "message": "open and enter"}


def _install_ms(monkeypatch, app):
    monkeypatch.setattr(ab, "_ms_app", lambda: app)


def test_microsoft_admin_consent_fixture_opens_one_window_and_prints_the_real_error(
        signin, monkeypatch, capsys):
    body = _fixture("ms_admin_consent.json")
    app = _FakeMsApp([body, body])
    _install_ms(monkeypatch, app)
    env = signin / ".env"
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(env))
    assert code == 1 and payload["error_class"] == "admin_consent"
    assert app.launches == 1
    assert "AADSTS65001" in payload["detail"]
    assert len(payload["line"]) <= 120 and ab.RESUME_COMMAND in payload["line"]
    assert "IT admin" in payload["line"]
    assert not env.exists()


def test_microsoft_interaction_required_with_consent_text_is_admin_consent():
    assert ab.is_admin_consent({"error": "interaction_required",
                               "error_description": "the administrator has not consented",
                               "suberror": "consent_required"})
    assert not ab.is_admin_consent({"error": "access_denied", "error_description": "cancelled"})


def test_microsoft_cancelled_sign_in_is_not_retried(signin, monkeypatch, capsys):
    app = _FakeMsApp([{"error": "access_denied",
                       "error_description": "AADSTS65004: User declined to consent."},
                      {"error": "access_denied"}])
    _install_ms(monkeypatch, app)
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(signin / ".env"))
    assert payload["error_class"] == "access_denied" and app.launches == 1


def test_microsoft_other_provider_errors_are_quoted_not_discarded(signin, monkeypatch, capsys):
    app = _FakeMsApp([{"error": "invalid_request", "error_codes": [50020],
                       "error_description": "AADSTS50020: User account from identity "
                                            "provider does not exist in tenant."}])
    _install_ms(monkeypatch, app)
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(signin / ".env"))
    assert payload["error_class"] == "provider_error" and app.launches == 1
    assert "AADSTS50020" in payload["detail"] and "AADSTS50020" in payload["line"]


def test_microsoft_window_that_fails_to_launch_is_retried_exactly_once(
        signin, monkeypatch, capsys):
    app = _FakeMsApp([RuntimeError("could not open browser"), RuntimeError("again")])
    _install_ms(monkeypatch, app)
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(signin / ".env"))
    assert app.launches == 2
    # Both windows failed: the device-code fallback is offered, as before.
    assert payload["ok"] is True and payload["pending"] == "device_code"


def test_microsoft_success_stores_address_and_matches_hint(signin, monkeypatch, capsys):
    app = _FakeMsApp([{"access_token": "a", "refresh_token": "r",
                       "scope": "Mail.ReadWrite openid",
                       "id_token_claims": {"preferred_username": "jane@corp.com"}}])
    _install_ms(monkeypatch, app)
    monkeypatch.setattr(ab, "_EXPECT_EMAIL", "jane@corp.com")
    env = signin / ".env"
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(env))
    assert code == 0 and payload["label"] == "Corp"
    assert payload["email_matches_hint"] is True
    text = env.read_text(encoding="utf-8")
    assert "MS_GRAPH_REFRESH_TOKEN_CORP=r" in text
    assert "MS_GRAPH_ACCOUNT_EMAIL_CORP=jane@corp.com" in text


# ── refresh-token expiry stamps ──────────────────────────────────────────────
# Field name per Google's and Microsoft's token-response documentation
# (`refresh_token_expires_in`, seconds). No sign-in can be captured without a
# person, so these responses are the documented shape, not a capture.

from datetime import datetime, timezone

FIXED_NOW = datetime(2026, 10, 2, 6, 0, 0, tzinfo=timezone.utc)


class _Session:
    def __init__(self, token):
        self.token = token


def _google_sign_in(signin, monkeypatch, capsys, token_response, env_text=""):
    monkeypatch.setattr(ab, "_utcnow", lambda: FIXED_NOW)
    flow = _FakeFlow(_Creds(list(GOOGLE_SCOPES), "rt-new"))
    flow.oauth2session = _Session(token_response)
    _install_flow(monkeypatch, flow)
    monkeypatch.setattr(ab, "_google_email", lambda creds: "jane@acme.com")
    env = signin / ".env"
    if env_text:
        env.write_text(env_text, encoding="utf-8")
    code, payload = _run(capsys, ab.bootstrap_google, None, str(env))
    assert code == 0 and payload["label"] == "Acme"
    return env.read_text(encoding="utf-8")


def test_google_sign_in_that_states_a_lifetime_stores_an_expiry_stamp(signin, monkeypatch, capsys):
    text = _google_sign_in(signin, monkeypatch, capsys,
                           {"access_token": "a", "refresh_token": "rt-new",
                            "refresh_token_expires_in": 604799})
    assert "GOOGLE_REFRESH_EXPIRES_ACME=2026-10-09T05:59:59+00:00" in text
    stamp = next(l.split("=", 1)[1] for l in text.splitlines()
                 if l.startswith("GOOGLE_REFRESH_EXPIRES_ACME="))
    assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0


def test_google_sign_in_that_states_no_lifetime_stores_none_and_removes_an_old_one(
        signin, monkeypatch, capsys):
    text = _google_sign_in(
        signin, monkeypatch, capsys, {"access_token": "a", "refresh_token": "rt-new"},
        env_text=("GOOGLE_REFRESH_TOKEN_ACME=old\nGOOGLE_ACCOUNT_EMAIL_ACME=jane@acme.com\n"
                  "GOOGLE_REFRESH_EXPIRES_ACME=2026-09-01T00:00:00+00:00\nOTHER=kept\n"))
    assert "GOOGLE_REFRESH_EXPIRES" not in text
    assert "GOOGLE_REFRESH_TOKEN_ACME=rt-new" in text and "OTHER=kept" in text


def test_google_re_sign_in_rewrites_the_stamp(signin, monkeypatch, capsys):
    text = _google_sign_in(
        signin, monkeypatch, capsys,
        {"access_token": "a", "refresh_token": "rt-new", "refresh_token_expires_in": "3600"},
        env_text=("GOOGLE_REFRESH_TOKEN_ACME=old\nGOOGLE_ACCOUNT_EMAIL_ACME=jane@acme.com\n"
                  "GOOGLE_REFRESH_EXPIRES_ACME=2026-09-01T00:00:00+00:00\n"))
    assert text.count("GOOGLE_REFRESH_EXPIRES_ACME=") == 1
    assert "GOOGLE_REFRESH_EXPIRES_ACME=2026-10-02T07:00:00+00:00" in text


def _ms_sign_in(signin, monkeypatch, capsys, extra, env_text=""):
    monkeypatch.setattr(ab, "_utcnow", lambda: FIXED_NOW)
    result = {"access_token": "a", "refresh_token": "r", "scope": "Mail.ReadWrite",
              "id_token_claims": {"preferred_username": "jane@corp.com"}}
    result.update(extra)
    _install_ms(monkeypatch, _FakeMsApp([result]))
    env = signin / ".env"
    if env_text:
        env.write_text(env_text, encoding="utf-8")
    code, payload = _run(capsys, ab.bootstrap_microsoft, None, str(env))
    assert code == 0 and payload["label"] == "Corp"
    return env.read_text(encoding="utf-8")


def test_microsoft_sign_in_that_states_a_lifetime_stores_an_expiry_stamp(
        signin, monkeypatch, capsys):
    text = _ms_sign_in(signin, monkeypatch, capsys, {"refresh_token_expires_in": 86400})
    assert "MS_GRAPH_REFRESH_EXPIRES_CORP=2026-10-03T06:00:00+00:00" in text


def test_microsoft_sign_in_that_states_no_lifetime_stores_none_and_removes_an_old_one(
        signin, monkeypatch, capsys):
    text = _ms_sign_in(
        signin, monkeypatch, capsys, {},
        env_text=("MS_GRAPH_REFRESH_TOKEN_CORP=old\nMS_GRAPH_ACCOUNT_EMAIL_CORP=jane@corp.com\n"
                  "MS_GRAPH_REFRESH_EXPIRES_CORP=2026-09-01T00:00:00+00:00\n"))
    assert "MS_GRAPH_REFRESH_EXPIRES" not in text
    assert "MS_GRAPH_REFRESH_TOKEN_CORP=r" in text


@pytest.mark.parametrize("stated", [None, "", "soon", 0, -5])
def test_a_lifetime_that_is_not_a_positive_number_is_no_stamp(stated):
    assert ab.refresh_expiry_stamp(stated) == ""


def test_relabel_carries_the_expiry_stamp(signin, capsys, monkeypatch):
    monkeypatch.setattr(ab, "_JSON", False)
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=t\n"
                   "GOOGLE_REFRESH_EXPIRES_ACME=2026-10-09T05:59:59+00:00\n", encoding="utf-8")
    ab.relabel("google", "Acme", "Work", str(env))
    text = env.read_text(encoding="utf-8")
    assert "GOOGLE_REFRESH_EXPIRES_WORK=2026-10-09T05:59:59+00:00" in text
    assert "GOOGLE_REFRESH_EXPIRES_ACME" not in text


def test_an_expiry_stamp_is_never_listed_as_an_account(signin, monkeypatch):
    """Everything that lists accounts does it by the `*_REFRESH_TOKEN_` prefix.
    The stamps (and the recorded addresses) must fall outside it."""
    import re

    import companion_key
    token_prefixes = ("GOOGLE_REFRESH_TOKEN_", "MS_GRAPH_REFRESH_TOKEN_")
    for prefix in (ab.GOOGLE_EXPIRES_PREFIX, ab.MS_EXPIRES_PREFIX,
                   ab.GOOGLE_EMAIL_PREFIX, ab.MS_EMAIL_PREFIX):
        assert not (prefix + "_ACME").startswith(token_prefixes)
    env = signin / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN_ACME=t\n"
                   "GOOGLE_REFRESH_EXPIRES_ACME=2026-10-09T05:59:59+00:00\n"
                   "MS_GRAPH_REFRESH_TOKEN_CORP=r\n"
                   "MS_GRAPH_REFRESH_EXPIRES_CORP=2026-10-03T06:00:00+00:00\n", encoding="utf-8")
    assert companion_key._labels_with_tokens("google") == ["ACME"]
    assert companion_key._labels_with_tokens("microsoft") == ["CORP"]
    # Every enumeration in app/ names the full token prefix, never the bare
    # `GOOGLE_REFRESH_` / `MS_GRAPH_REFRESH_` stem that would swallow a stamp.
    app = Path(ab.__file__).resolve().parent
    scanned, loose = 0, []
    for path in sorted(app.glob("*.py")):
        scanned += 1
        for match in re.finditer(r'startswith\(\(?\s*f?"((?:GOOGLE|MS_GRAPH)_REFRESH_[A-Z_]*)"',
                                 path.read_text(encoding="utf-8")):
            if not match.group(1).endswith("_REFRESH_TOKEN_"):
                loose.append((path.name, match.group(1)))
    assert scanned > 50 and loose == []


# ── the ledger ────────────────────────────────────────────────────────────────

def test_a_failure_lands_in_the_install_ledger_when_a_run_is_in_progress(
        signin, monkeypatch, capsys):
    setup = signin / "setup"
    setup.mkdir()
    (setup / "run-id").write_text("20261001-170000-a1b2\n", encoding="utf-8")
    app = _FakeMsApp([_fixture("ms_admin_consent.json")])
    _install_ms(monkeypatch, app)
    _run(capsys, ab.bootstrap_microsoft, None, str(signin / ".env"))
    rows = [json.loads(line) for line in
            (setup / "timing.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event"] == "signin_failed"
    assert rows[0]["run_id"] == "20261001-170000-a1b2"
    assert rows[0]["step"] == "signin:microsoft"
    assert rows[0]["detail"].startswith("admin_consent: ")


def test_no_ledger_row_outside_an_install_run(signin, monkeypatch, capsys):
    app = _FakeMsApp([_fixture("ms_admin_consent.json")])
    _install_ms(monkeypatch, app)
    _run(capsys, ab.bootstrap_microsoft, None, str(signin / ".env"))
    assert not (signin / "setup" / "timing.jsonl").exists()


def test_missing_credential_is_a_classed_failure_with_no_window(signin, monkeypatch, capsys):
    monkeypatch.setattr(ab, "GOOGLE_CLIENT_SECRET_PATH", str(signin / "nope.json"))
    code, payload = _run(capsys, ab.bootstrap_google, None, str(signin / ".env"))
    assert code == 1 and payload["error_class"] == "no_credential"
    assert ab.RESUME_COMMAND in payload["line"]
