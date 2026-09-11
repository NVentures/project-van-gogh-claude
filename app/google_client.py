"""
google_client.py — authenticated Google API service clients built from refresh
tokens supplied via environment variables.

Each client wraps `google-api-python-client` and exposes the native Gmail /
Calendar / Drive service objects, so callers use the standard
`.users().threads().list(**params).execute()` surface that the raw API expects —
the returned JSON shapes are the native API shapes.

Token sources (resolved per call, env first):
  - OAuth app id/secret:  GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET,
    else ~/.config/van-gogh/google_client_secret.json (Desktop client download).
  - Per-account refresh token:  GOOGLE_REFRESH_TOKEN_<LABEL>
    (LABEL is the account label upper-cased, spaces/dashes -> underscores;
    matches what auth_bootstrap.py prints).

Usage:
    from google_client import google_client
    g = google_client("GMAIL")
    threads = g.gmail.users().threads().list(userId="me", q="in:sent").execute()
    events = g.calendar.events().list(calendarId="primary", maxResults=50).execute()
"""

import json
import os
from functools import lru_cache

import user_state
from certs import prime_ca_env, resolve_ca_bundle

# Local tokens live in the per-user state .env (~/.config/van-gogh/.env);
# cloud routines set real env vars instead, which always win (override=False).
user_state.load_env()

# Trust the OS CA bundle so token refresh + API calls survive a TLS-intercepting
# proxy (no-op on a normal machine).
prime_ca_env()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOGLE_CLIENT_SECRET_PATH = (
    os.path.expanduser("~/.config/van-gogh/google_client_secret.json")
    if os.path.exists(os.path.expanduser("~/.config/van-gogh/google_client_secret.json"))
    else os.path.join(_PROJECT_ROOT, "config", "oauth", "google_client_secret.json")
)
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Must match (or be a superset of) the scopes auth_bootstrap.py requests.
GOOGLE_SCOPES = [
    # gmail.modify covers reading, drafting and labelling but NOT sending:
    # drafts.send and messages.send both require gmail.send (or compose).
    # Without it the page's stamp fails with a 403 the moment it is pressed.
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    # Contact capture (app/contact_capture.py) needs both: the .other.readonly
    # scope to READ the "Other contacts" bucket Gmail auto-collects, and the
    # full contacts scope to WRITE the promoted contact. copyOtherContactToMy-
    # ContactsGroup requires both at once, and "Other contacts" is read-only,
    # so a promotion is always a copy followed by an update.
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.other.readonly",
]


def _label_suffix(label: str) -> str:
    return label.strip().upper().replace(" ", "_").replace("-", "_")


def _oauth_app_creds() -> tuple[str, str]:
    """Return (client_id, client_secret) for the shared Desktop OAuth app."""
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if cid and csec:
        return cid, csec
    if os.path.exists(GOOGLE_CLIENT_SECRET_PATH):
        with open(GOOGLE_CLIENT_SECRET_PATH, encoding="utf-8") as f:
            installed = json.load(f)["installed"]
        return installed["client_id"], installed["client_secret"]
    raise RuntimeError(
        "Google OAuth app credentials not found. Set GOOGLE_OAUTH_CLIENT_ID and "
        f"GOOGLE_OAUTH_CLIENT_SECRET, or place the Desktop client JSON at {GOOGLE_CLIENT_SECRET_PATH}."
    )


def _refresh_token_for(label: str) -> str:
    var = f"GOOGLE_REFRESH_TOKEN_{_label_suffix(label)}"
    token = os.environ.get(var)
    if not token:
        raise RuntimeError(
            f"Missing refresh token: env var {var} is not set. "
            f"Run: python app/auth_bootstrap.py --provider google --label {label}"
        )
    return token


def _credentials(label: str):
    from google.oauth2.credentials import Credentials

    client_id, client_secret = _oauth_app_creds()
    return Credentials(
        token=None,
        refresh_token=_refresh_token_for(label),
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=GOOGLE_SCOPES,
    )


class GoogleClient:
    """Lazily-built Gmail / Calendar / Drive / People services for one label.

    google-api-python-client refreshes the access token transparently on first
    use of each service, using the refresh token in the credentials.
    """

    def __init__(self, label: str):
        self.label = label
        self._creds = _credentials(label)
        self._gmail = None
        self._calendar = None
        self._drive = None
        self._people = None

    def _new_authorized_http(self):
        import google_auth_httplib2
        import httplib2

        # Explicit httplib2 client with the resolved CA bundle so both the
        # transparent token refresh and the API calls trust a proxy CA.
        return google_auth_httplib2.AuthorizedHttp(
            self._creds, http=httplib2.Http(ca_certs=resolve_ca_bundle())
        )

    def _build(self, name: str, version: str):
        from googleapiclient.discovery import build

        return build(name, version, http=self._new_authorized_http(), cache_discovery=False)

    def new_http(self):
        """A fresh AuthorizedHttp for use inside a worker thread.

        `httplib2.Http` is NOT thread-safe: concurrent requests on one Http
        corrupt its shared connection state, which crashes the interpreter
        (native segfault) or deadlocks. The cached `.gmail`/`.calendar`/`.drive`
        services each hold a single Http, so a `ThreadPoolExecutor` that calls
        the same service from multiple threads must give each request its own
        Http via `.execute(http=client.new_http())`. Reuses the shared, already
        token-refreshed credentials — the token is refreshed by the initial
        serial `.list()` call before any pool starts, so pooled requests do not
        normally race a refresh (google-auth does not lock refresh; if the token
        expires mid-pool, concurrent refreshes can race but google tolerates the
        duplicate).
        """
        return self._new_authorized_http()

    @property
    def gmail(self):
        if self._gmail is None:
            self._gmail = self._build("gmail", "v1")
        return self._gmail

    @property
    def calendar(self):
        if self._calendar is None:
            self._calendar = self._build("calendar", "v3")
        return self._calendar

    @property
    def drive(self):
        if self._drive is None:
            self._drive = self._build("drive", "v3")
        return self._drive

    @property
    def people(self):
        """The People API service, for contact capture.

        Built like the others, but note the API is picky in one way that
        matters: mutations for a single user must be sent sequentially, so
        nothing here may be driven from a thread pool the way Gmail reads are.
        """
        if self._people is None:
            self._people = self._build("people", "v1")
        return self._people


@lru_cache(maxsize=None)
def google_client(label: str = "GMAIL") -> GoogleClient:
    """Return a cached GoogleClient for the given account label."""
    return GoogleClient(label)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Smoke-test a Google account's refresh token.")
    parser.add_argument("--label", default="GMAIL", help="Account label (default: GMAIL)")
    args = parser.parse_args()

    g = google_client(args.label)
    profile = g.gmail.users().getProfile(userId="me").execute()
    print(f"OK [{args.label}] gmail: {profile.get('emailAddress')} ({profile.get('messagesTotal')} messages)")
    cal = g.calendar.calendarList().get(calendarId="primary").execute()
    print(f"OK [{args.label}] calendar primary: {cal.get('summary')}")
