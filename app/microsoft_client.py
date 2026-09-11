"""
microsoft_client.py — authenticated Microsoft Graph client built from refresh
tokens supplied via environment variables.

Wraps MSAL (public client / device-code app) for token handling and issues raw
Graph REST calls, returning the native JSON shapes (`{"value": [...]}`) the Graph
API emits — so parsing code (`data.get("value", [])`) consumes them directly.

Token sources (resolved per call, env first):
  - OAuth app id:  MS_OAUTH_CLIENT_ID, else ~/.config/van-gogh/ms_client_secret.json.
  - Per-account refresh token:  MS_GRAPH_REFRESH_TOKEN_<LABEL>
    (LABEL upper-cased, spaces/dashes -> underscores; matches auth_bootstrap.py).

Usage:
    from microsoft_client import microsoft_client
    m = microsoft_client("OUTLOOK")
    events = m.calendar_view("2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z", top=200)
    msgs = m.get("/me/messages", {"$top": 50, "$filter": "..."})
    m.send_mail({"message": {...}, "saveToSentItems": True})
"""

import json
import os
import time
from functools import lru_cache

import requests

import user_state
from certs import prime_ca_env

# Local tokens live in the per-user state .env (~/.config/van-gogh/.env);
# cloud routines set real env vars instead, which always win (override=False).
user_state.load_env()

# Trust the OS CA bundle so requests + MSAL survive a TLS-intercepting proxy
# (no-op on a normal machine). Sets SSL_CERT_FILE / REQUESTS_CA_BUNDLE if unset.
prime_ca_env()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/common"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MS_CLIENT_SECRET_PATH = (
    os.path.expanduser("~/.config/van-gogh/ms_client_secret.json")
    if os.path.exists(os.path.expanduser("~/.config/van-gogh/ms_client_secret.json"))
    else os.path.join(_PROJECT_ROOT, "config", "oauth", "ms_client_secret.json")
)

GRAPH_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/User.Read",
]


def _label_suffix(label: str) -> str:
    return label.strip().upper().replace(" ", "_").replace("-", "_")


def _client_id() -> str:
    cid = os.environ.get("MS_OAUTH_CLIENT_ID")
    if cid:
        return cid
    if os.path.exists(MS_CLIENT_SECRET_PATH):
        with open(MS_CLIENT_SECRET_PATH, encoding="utf-8") as f:
            return json.load(f)["client_id"]
    raise RuntimeError(
        "Microsoft OAuth client id not found. Set MS_OAUTH_CLIENT_ID, or place the "
        f"app config at {MS_CLIENT_SECRET_PATH}."
    )


def _refresh_token_for(label: str) -> str:
    var = f"MS_GRAPH_REFRESH_TOKEN_{_label_suffix(label)}"
    token = os.environ.get(var)
    if not token:
        raise RuntimeError(
            f"Missing refresh token: env var {var} is not set. "
            f"Run: python app/auth_bootstrap.py --provider microsoft --label {label}"
        )
    return token


def _persist_rotated_token(label: str, token: str) -> None:
    """Save a rotated refresh token so the stored one never goes stale.

    Entra rotates refresh tokens and expires the old ones; keeping the rotation
    in memory only would eventually break auth on the next process. Updates the
    process env and, when a state .env exists (local Tier 1 installs: cloud
    routines use real env vars and have no file), writes it back. Best-effort:
    a state-dir write failure must never fail a data call.
    """
    var = f"MS_GRAPH_REFRESH_TOKEN_{_label_suffix(label)}"
    os.environ[var] = token
    try:
        env_path = user_state.env_file()
        if env_path.exists():
            user_state.upsert_env(var, token, env_path=env_path)
    except OSError:
        pass


class MicrosoftClient:
    """Graph REST client for one account label, with transparent token refresh."""

    def __init__(self, label: str):
        import msal

        self.label = label
        self._app = msal.PublicClientApplication(_client_id(), authority=AUTHORITY)
        self._refresh = _refresh_token_for(label)
        self._access_token = None
        self._expires_at = 0.0

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        result = self._app.acquire_token_by_refresh_token(self._refresh, scopes=GRAPH_SCOPES)
        if "access_token" not in result:
            raise RuntimeError(
                f"Token refresh failed for {self.label}: "
                f"{result.get('error')} - {result.get('error_description')}"
            )
        self._access_token = result["access_token"]
        self._expires_at = time.time() + result.get("expires_in", 3600)
        rotated = result.get("refresh_token")
        if rotated and rotated != self._refresh:
            self._refresh = rotated
            _persist_rotated_token(self.label, rotated)
        return self._access_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    # ── Generic verbs (faithful to Graph REST) ────────────────────────────────

    def get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(GRAPH_BASE + path, headers=self._headers(), params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json_body: dict | None = None) -> dict:
        headers = {**self._headers(), "Content-Type": "application/json"}
        r = requests.post(GRAPH_BASE + path, headers=headers, json=json_body, timeout=60)
        r.raise_for_status()
        return r.json() if r.text else {}

    def patch(self, path: str, json_body: dict | None = None) -> dict:
        headers = {**self._headers(), "Content-Type": "application/json"}
        r = requests.patch(GRAPH_BASE + path, headers=headers, json=json_body, timeout=60)
        r.raise_for_status()
        return r.json() if r.text else {}

    def delete(self, path: str) -> None:
        r = requests.delete(GRAPH_BASE + path, headers=self._headers(), timeout=60)
        # 404 means the event is already gone — idempotent delete, not an error.
        if r.status_code != 404:
            r.raise_for_status()

    # ── Convenience wrappers (the Graph endpoints the scripts use) ────────────

    def calendar_view(self, start: str, end: str, top: int = 200) -> dict:
        return self.get(
            "/me/calendarView",
            {"startDateTime": start, "endDateTime": end, "$top": top},
        )

    def messages(self, **odata) -> dict:
        """List /me/messages. Pass OData options without the '$' prefix,
        e.g. messages(filter="...", top=999, select="subject", orderby="receivedDateTime desc")."""
        return self.get("/me/messages", {f"${k}": v for k, v in odata.items()})

    def folder_messages(self, folder_id: str, **odata) -> dict:
        return self.get(
            f"/me/mailFolders/{folder_id}/messages",
            {f"${k}": v for k, v in odata.items()},
        )

    def send_mail(self, message: dict, save_to_sent: bool = True) -> dict:
        return self.post("/me/sendMail", {"message": message, "saveToSentItems": save_to_sent})

    def create_draft(self, message: dict) -> dict:
        """Create a draft in the Drafts folder (POST /me/messages, never sends)."""
        return self.post("/me/messages", message)

    def send_draft(self, message_id: str) -> dict:
        """Send a draft that already exists, by id.

        This is what the briefing page's stamp presses. It matters that it is
        by id and not a fresh compose: the user may have edited the draft in
        Outlook since it was written, and sending a copy of the original text
        would silently discard that edit and leave the draft behind.
        """
        return self.post(f"/me/messages/{message_id}/send", None)


@lru_cache(maxsize=None)
def microsoft_client(label: str = "OUTLOOK") -> MicrosoftClient:
    """Return a cached MicrosoftClient for the given account label."""
    return MicrosoftClient(label)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Smoke-test a Microsoft account's refresh token.")
    parser.add_argument("--label", default="OUTLOOK", help="Account label (default: OUTLOOK)")
    args = parser.parse_args()

    m = microsoft_client(args.label)
    me = m.get("/me")
    print(f"OK [{args.label}] graph /me: {me.get('userPrincipalName') or me.get('mail')} ({me.get('displayName')})")
