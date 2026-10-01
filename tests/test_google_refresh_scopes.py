"""A Google token refresh must name no scopes. Ever.

The failure this file exists for: the client was built with the plugin's full
scope list, google-auth copied that list into every refresh request, and
Google rejects a refresh that names a scope the token was never granted. A
release added two scopes for one optional feature, and every account connected
before it lost Gmail and Calendar together.

Two tests, because they catch different mistakes:

  * the first drives the REAL google-auth refresh from the REAL constructor
    and reads the request that would have gone to Google. Nothing about the
    library's behaviour is assumed;
  * the second reads the source, so the rule also holds for a construction
    site nobody has written yet.

MUTATION (proven, see objectives/google-scope-refresh-fix): put
`scopes=GOOGLE_SCOPES` back in google_client._credentials and both fail.
"""
import ast
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

import google_client

APP = Path(google_client.__file__).resolve().parent


class _Response:
    status = 200
    headers = {}
    data = json.dumps({
        "access_token": "access-token-for-the-test",
        "expires_in": 3600,
        "token_type": "Bearer",
        # What Google sends back for a token granted two of the six: the
        # response describes the grant, the request must not constrain it.
        "scope": "https://www.googleapis.com/auth/gmail.modify "
                 "https://www.googleapis.com/auth/calendar",
    }).encode("utf-8")


def test_the_refresh_request_carries_no_scope_field(monkeypatch):
    sent = []

    def transport(url, method="GET", body=None, headers=None, **kwargs):
        sent.append({"url": url, "method": method, "body": body})
        return _Response()

    # The resolver runs before any stubbed call is reached, so it is stubbed
    # too: a CI machine has no OAuth app credentials and no token.
    monkeypatch.setattr(google_client, "_oauth_app_creds",
                        lambda: ("client-id", "client-secret"))
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN_OLDER_ACCOUNT", "refresh-token")

    creds = google_client._credentials("Older Account")
    creds.refresh(transport)

    assert len(sent) == 1, "expected exactly one token request"
    body = sent[0]["body"]
    fields = parse_qs(body.decode("utf-8") if isinstance(body, bytes) else body)
    assert fields["grant_type"] == ["refresh_token"]
    assert fields["refresh_token"] == ["refresh-token"]
    assert "scope" not in fields, (
        "the refresh named scopes. Google rejects the whole refresh when the "
        "token was never granted one of them, which disconnects every account "
        f"connected before the list last grew: {fields.get('scope')}")
    assert creds.token == "access-token-for-the-test"


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            yield name, node


# Position of `scopes` in google.oauth2.credentials.Credentials.__init__:
# token, refresh_token, id_token, token_uri, client_id, client_secret, scopes.
_SCOPES_POSITION = 6


def test_no_code_builds_google_credentials_with_scopes():
    files = sorted(APP.glob("*.py"))
    assert len(files) > 50, "found too few files, the guard would pass vacuously"
    builders, problems = [], []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, node in _calls(tree):
            where = f"{path.name}:{node.lineno}"
            if name in ("from_authorized_user_file", "from_authorized_user_info"):
                problems.append(f"{where} {name}: reads stored scopes back "
                                "into the refresh")
            if name != "Credentials":
                continue
            builders.append(path.name)
            if len(node.args) > _SCOPES_POSITION:
                problems.append(f"{where} passes scopes by position")
            kw = {k.arg: k.value for k in node.keywords}
            if None in kw:
                problems.append(f"{where} uses **kwargs, scopes cannot be checked")
            scopes = kw.get("scopes")
            if not (isinstance(scopes, ast.Constant) and scopes.value is None):
                problems.append(f"{where} must pass scopes=None explicitly")
    assert builders == ["google_client.py"], (
        "Google credentials are built in exactly one place so this rule has "
        f"one place to hold. Found: {builders}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("source,expected", [
    ("Credentials(token=None, scopes=SCOPES)", "explicitly"),
    ("Credentials(None, 'r', None, 'u', 'c', 's', SCOPES)", "position"),
    ("Credentials(token=None)", "explicitly"),
])
def test_the_source_guard_can_fail(source, expected):
    """A guard that has never been seen to fail proves nothing. Same checks,
    planted violations."""
    node = next(n for name, n in _calls(ast.parse(source)) if name == "Credentials")
    found = []
    if len(node.args) > _SCOPES_POSITION:
        found.append("position")
    scopes = {k.arg: k.value for k in node.keywords}.get("scopes")
    if not (isinstance(scopes, ast.Constant) and scopes.value is None):
        found.append("explicitly")
    assert expected in found
