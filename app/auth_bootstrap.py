"""
auth_bootstrap.py: one-time OAuth onboarding for Tier 1 accounts.

Opens a browser login, detects which account signed in, derives a default
label, and saves the refresh token into the per-user state `.env`
(~/.config/van-gogh/.env, see app/user_state.py — outside the plugin cache so
it survives plugin updates) so the Tier 1 clients (google_client.py /
microsoft_client.py) pick it up automatically.

Usage:
  # Connect an account (browser opens; email is auto-detected):
  python app/auth_bootstrap.py --provider google
  python app/auth_bootstrap.py --provider microsoft

  # Force a specific label instead of the auto-derived one:
  python app/auth_bootstrap.py --provider google --label Work

  # Rename a saved account's label afterwards (no re-login):
  python app/auth_bootstrap.py --provider google --relabel OldLabel Work

The OAuth *app* credentials are the user's own registrations, read from
~/.config/van-gogh/google_client_secret.json and ~/.config/van-gogh/ms_client_secret.json.
Step 3 of /van-gogh:install-van-gogh collects them through
app/install_oauth_credentials.py; Step 3c replaces one that has been rotated or
revoked. A copy under the plugin's config/oauth/ is used only as a fallback when
the user has supplied nothing.

Environments without a usable browser
─────────────────────────────────────
When run on a machine with no browser (a remote/cloud session, SSH, the web
app), the script switches automatically to a two-step copy-paste flow:

  Google — phase 1 prints an auth URL and a machine-readable block:
      ===VANGOGH_HEADLESS_URL===
      https://accounts.google.com/o/oauth2/auth?...
      ===END===
    The user opens that URL, signs in, and is redirected to a dead
    http://localhost:8080/?code=... page. They copy that full URL back, and
    phase 2 completes the exchange:
      python app/auth_bootstrap.py --provider google \
          --redirect-response "http://localhost:8080/?code=..."

  Microsoft — switches to device-code login, also two-phase so the code is
    visible before any blocking wait. Phase 1 prints a short code + URL:
      ===VANGOGH_DEVICE_CODE===
      verification_uri=https://microsoft.com/devicelogin
      user_code=ABCD-EFGH
      ===END===
    and exits, so the orchestrator can show them to the user. Phase 2
    (--complete-device-code) loads the saved flow and polls until the user
    finishes signing in:
      python app/auth_bootstrap.py --provider microsoft --complete-device-code

Pass --headless (Google) / --device-code (Microsoft) to force these flows even
when a browser exists.

After each login the script prints a machine-readable block so an orchestrating
skill can read the detected email and chosen label:

  ===VANGOGH_AUTH===
  provider=google
  email=you@example.com
  label=Example
  env_var=GOOGLE_REFRESH_TOKEN_EXAMPLE
  env_file=/path/to/.env
  ===END===
"""

import argparse
import json
import os
import sys
from pathlib import Path

import user_state

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

MS_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/User.Read",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = str(user_state.env_file())

# Loopback redirect used by the headless copy-paste flow. The page never loads
# (nothing listens here in a remote session) but the browser still puts the
# auth code in the address bar, which is all we need.
HEADLESS_REDIRECT_URI = "http://localhost:8080"


def _find_credential(filename: str) -> str:
    """Resolve the shared OAuth app credential: ~/.config/van-gogh/ first, then repo config/oauth/."""
    user_path = Path.home() / ".config" / "van-gogh" / filename
    if user_path.exists():
        return str(user_path)
    return str(PROJECT_ROOT / "config" / "oauth" / filename)  # caller checks existence


GOOGLE_CLIENT_SECRET_PATH = _find_credential("google_client_secret.json")
MS_CLIENT_SECRETS_PATH = _find_credential("ms_client_secret.json")
MS_AUTHORITY = "https://login.microsoftonline.com/common"

GOOGLE_PREFIX = "GOOGLE_REFRESH_TOKEN"
MS_PREFIX = "MS_GRAPH_REFRESH_TOKEN"


def _label_suffix(label: str) -> str:
    return label.strip().upper().replace(" ", "_").replace("-", "_")


def _derive_label(email: str) -> str:
    """Propose a short label from an email address. Skills usually refine this."""
    domain = email.split("@")[-1].lower()
    if domain in ("gmail.com", "googlemail.com"):
        return "Personal"
    return domain.split(".")[0].capitalize()


def _upsert_env(env_file: str, key: str, value: str) -> None:
    user_state.upsert_env(key, value, env_path=Path(env_file))


def _rename_env_key(env_file: str, old_key: str, new_key: str) -> bool:
    path = Path(env_file)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = False
    for i, line in enumerate(lines):
        if line.startswith(old_key + "="):
            lines[i] = new_key + "=" + line.split("=", 1)[1]
            changed = True
            break
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def _has_browser() -> bool:
    """True if a usable web browser is available to open a login window."""
    import webbrowser

    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def _print_result(provider: str, email: str, label: str, env_var: str, env_file: str) -> None:
    print("\n===VANGOGH_AUTH===")
    print(f"provider={provider}")
    print(f"email={email}")
    print(f"label={label}")
    print(f"env_var={env_var}")
    print(f"env_file={env_file}")
    print("===END===")


# ── Google ────────────────────────────────────────────────────────────────────

def _google_email(creds) -> str:
    """Look up the signed-in Gmail address, trusting the system CA bundle.

    Built with an explicit httplib2 client so the call survives a TLS-intercepting
    proxy (see certs.resolve_ca_bundle); the default httplib2 cert bundle would
    fail in that environment.
    """
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    from certs import resolve_ca_bundle

    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(ca_certs=resolve_ca_bundle()))
    svc = build("gmail", "v1", http=http, cache_discovery=False)
    return svc.users().getProfile(userId="me").execute().get("emailAddress", "")


def _code_from_redirect(url: str) -> str:
    """Pull the OAuth authorization code out of a pasted redirect URL."""
    from urllib.parse import parse_qs, urlparse

    code = parse_qs(urlparse(url).query).get("code", [None])[0]
    if not code:
        raise ValueError(f"no 'code' query parameter found in redirect URL: {url}")
    return code


def _auth_state_file(name: str) -> Path:
    """Pending two-phase auth state, kept in the per-user state dir (not the
    shared system temp dir — it holds a PKCE verifier / device-flow handle)."""
    path = user_state.state_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _google_state_file() -> Path:
    return _auth_state_file("google_oauth_state.json")


def _google_headless_phase1(flow, label: str | None, env_file: str) -> None:
    """Print an auth URL the user can open anywhere, persisting PKCE state for phase 2."""
    import base64
    import hashlib
    import secrets

    flow.redirect_uri = HEADLESS_REDIRECT_URI
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    )
    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    _google_state_file().write_text(
        json.dumps(
            {
                "redirect_uri": HEADLESS_REDIRECT_URI,
                "code_verifier": code_verifier,
                "state": state,
                "label": label,
                "env_file": env_file,
            }
        ),
        encoding="utf-8",
    )
    print("\n── Google sign-in (step 1 of 2) ───────────────────────────────────────")
    print("Open this URL in any browser, sign in, and approve access.")
    print("Your browser will then redirect to a 'site can't be reached' page at")
    print("http://localhost:8080/?code=... — that's expected. Copy the FULL URL")
    print("from the address bar and paste it back.")
    print("\n===VANGOGH_HEADLESS_URL===")
    print(auth_url)
    print("===END===")


def _google_headless_phase2(flow, redirect_response: str, label: str | None):
    """Exchange the pasted redirect code for credentials using the saved PKCE state."""
    state_file = _google_state_file()
    if not state_file.exists():
        print("ERROR: no saved OAuth state found. Run the same command without "
              "--redirect-response first to generate the sign-in URL.")
        sys.exit(1)
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    state_file.unlink()
    flow.redirect_uri = saved["redirect_uri"]
    flow.fetch_token(code=_code_from_redirect(redirect_response), code_verifier=saved["code_verifier"])
    return flow.credentials, label or saved.get("label")


def bootstrap_google(label: str | None, env_file: str, headless: bool = False,
                     redirect_response: str | None = None) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(GOOGLE_CLIENT_SECRET_PATH):
        print(f"ERROR: shared Google app credential not found at {GOOGLE_CLIENT_SECRET_PATH}")
        print("This file is provided during onboarding. Ask your Van Gogh administrator for it —")
        print("you do NOT need to create your own Google Cloud project.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH, scopes=GOOGLE_SCOPES)

    if redirect_response is not None:
        # Phase 2 of the copy-paste flow: complete the exchange and fall through to save.
        creds, label = _google_headless_phase2(flow, redirect_response, label)
    else:
        if not headless and not _has_browser():
            headless = True
        if headless:
            # Phase 1: print the URL and stop. The orchestrator re-invokes with
            # --redirect-response once the user has pasted the redirect URL.
            _google_headless_phase1(flow, label, env_file)
            return
        print("\nA browser window will open so you can sign in with Google.")
        print("If it does NOT open automatically, copy the URL printed below into any browser.\n")
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
            open_browser=True,
            authorization_prompt_message="If your browser did not open, sign in at this URL:\n\n    {url}\n",
        )

    # Save the token BEFORE the email lookup so a network/SSL hiccup can never
    # leave us having completed OAuth but lost the refresh token.
    email = ""
    try:
        email = _google_email(creds)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: token saved, but the email lookup failed ({exc}). "
              "You can fix the label later with --relabel.")
    label = label or (_derive_label(email) if email else "Google")
    env_var = f"{GOOGLE_PREFIX}_{_label_suffix(label)}"
    _upsert_env(env_file, env_var, creds.refresh_token)

    summary = f"Connected {email} and saved its token" if email else "Saved the token (email lookup unavailable)"
    print(f"\n{summary} under label '{label}'.")
    _print_result("google", email, label, env_var, env_file)


# ── Microsoft ───────────────────────────────────────────────────────────────

def _ms_app():
    import msal

    if not os.path.exists(MS_CLIENT_SECRETS_PATH):
        print(f"ERROR: shared Microsoft app credential not found at {MS_CLIENT_SECRETS_PATH}")
        print("This file is provided during onboarding. Ask your Van Gogh administrator for it.")
        sys.exit(1)
    with open(MS_CLIENT_SECRETS_PATH, encoding="utf-8") as f:
        client_id = json.load(f)["client_id"]
    return msal.PublicClientApplication(client_id, authority=MS_AUTHORITY)


def _ms_state_file() -> Path:
    return _auth_state_file("ms_device_flow.json")


def _ms_interactive(app):
    """Try the browser sign-in (two attempts). Returns the token result or None."""
    print("\nA browser window will open so you can sign in with Microsoft.")
    for attempt in (1, 2):
        try:
            # Returns once the user finishes (or after timeout). Only a hard
            # failure to launch the window raises — that's what we retry.
            result = app.acquire_token_interactive(scopes=MS_SCOPES, prompt="select_account", timeout=180)
            if result and "access_token" in result:
                return result
        except Exception as e:  # noqa: BLE001
            print(f"Browser sign-in attempt {attempt} failed: {e}")
        if attempt == 1:
            print("Retrying the sign-in window once...\n")
    return None


def _ms_device_phase1(app, label: str | None, env_file: str) -> None:
    """Initiate device-code login, print the URL + code, persist the flow, and exit.

    Critically this does NOT poll — it returns immediately so the orchestrator
    can show the user the code before the (blocking) wait happens in phase 2.
    """
    flow = app.initiate_device_flow(scopes=MS_SCOPES)
    if "user_code" not in flow:
        print(f"ERROR initiating device flow: {flow.get('error_description')}")
        sys.exit(1)
    _ms_state_file().write_text(
        json.dumps({"flow": flow, "label": label, "env_file": env_file}), encoding="utf-8"
    )
    print("\n── Microsoft sign-in (step 1 of 2) ────────────────────────────────────")
    print(flow.get("message", "Open the URL below and enter the code."))
    print("\n===VANGOGH_DEVICE_CODE===")
    print(f"verification_uri={flow.get('verification_uri', 'https://microsoft.com/devicelogin')}")
    print(f"user_code={flow['user_code']}")
    print(f"expires_in={flow.get('expires_in', 900)}")
    print("===END===")


def _ms_device_phase2(app, label: str | None):
    """Load the saved device flow and poll until the user completes sign-in."""
    state_file = _ms_state_file()
    if not state_file.exists():
        print("ERROR: no saved device-code flow found. Run the same command without "
              "--complete-device-code first to get the sign-in code.")
        sys.exit(1)
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    state_file.unlink()
    print("Waiting for you to finish signing in...")
    result = app.acquire_token_by_device_flow(saved["flow"])  # blocks: polls until done/expiry
    return result, (label or saved.get("label"))


def bootstrap_microsoft(label: str | None, env_file: str, device_code: bool = False,
                        complete_device_code: bool = False) -> None:
    app = _ms_app()

    if complete_device_code:
        result, label = _ms_device_phase2(app, label)
    else:
        # No browser → skip the interactive attempt (it would burn a long timeout
        # before failing) and go straight to device-code phase 1.
        if not device_code and not _has_browser():
            device_code = True
        if device_code:
            _ms_device_phase1(app, label, env_file)
            return  # phase 1 exits here; orchestrator re-invokes --complete-device-code
        result = _ms_interactive(app)
        if result is None:
            # Browser sign-in didn't launch — fall back to device-code phase 1 so
            # the code still gets surfaced (rather than failing outright).
            print("\nFalling back to device-code sign-in.")
            _ms_device_phase1(app, label, env_file)
            return

    if "refresh_token" not in result:
        print(f"ERROR: no refresh token returned: {result.get('error_description', result)}")
        sys.exit(1)

    claims = result.get("id_token_claims", {})
    email = claims.get("preferred_username") or claims.get("email") or ""
    label = label or (_derive_label(email) if email else "Outlook")
    env_var = f"{MS_PREFIX}_{_label_suffix(label)}"
    _upsert_env(env_file, env_var, result["refresh_token"])

    print(f"\nConnected {email} and saved its token under label '{label}'.")
    _print_result("microsoft", email, label, env_var, env_file)


# ── Relabel (no re-login) ─────────────────────────────────────────────────────

def relabel(provider: str, old_label: str, new_label: str, env_file: str) -> None:
    prefix = GOOGLE_PREFIX if provider == "google" else MS_PREFIX
    old_key = f"{prefix}_{_label_suffix(old_label)}"
    new_key = f"{prefix}_{_label_suffix(new_label)}"
    if _rename_env_key(env_file, old_key, new_key):
        print(f"Renamed {old_key} -> {new_key} in {env_file}")
        print("\n===VANGOGH_RELABEL===")
        print(f"old_env_var={old_key}")
        print(f"new_env_var={new_key}")
        print(f"label={new_label}")
        print("===END===")
    else:
        print(f"ERROR: {old_key} not found in {env_file}")
        sys.exit(1)


def main() -> None:
    from config_loader import force_utf8_io
    force_utf8_io()
    parser = argparse.ArgumentParser(description="OAuth onboarding for Van Gogh Tier 1 accounts.")
    parser.add_argument("--provider", required=True, choices=["google", "microsoft"])
    parser.add_argument("--label", default=None, help="Force a label instead of the auto-derived one.")
    parser.add_argument("--relabel", nargs=2, metavar=("OLD", "NEW"),
                        help="Rename a saved account's label without re-logging-in.")
    parser.add_argument("--device-code", action="store_true",
                        help="Microsoft only: use device-code login instead of the browser. Phase 1 "
                             "prints the URL + code and exits; finish with --complete-device-code.")
    parser.add_argument("--complete-device-code", action="store_true",
                        help="Microsoft device-code phase 2: poll for the sign-in started by a prior "
                             "--device-code (or auto-headless) run, then save the token.")
    parser.add_argument("--headless", action="store_true",
                        help="Google only: force the copy-paste flow (auto-enabled when no browser is found). "
                             "Phase 1 prints the sign-in URL; re-run with --redirect-response to finish.")
    parser.add_argument("--redirect-response", default=None, metavar="URL",
                        help="Google copy-paste flow, phase 2: the full redirect URL copied from the browser "
                             "address bar after sign-in.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Token file (default: {DEFAULT_ENV_FILE})")
    args = parser.parse_args()

    if args.relabel:
        relabel(args.provider, args.relabel[0], args.relabel[1], args.env_file)
    elif args.provider == "google":
        bootstrap_google(args.label, args.env_file, headless=args.headless,
                         redirect_response=args.redirect_response)
    else:
        bootstrap_microsoft(args.label, args.env_file, device_code=args.device_code,
                            complete_device_code=args.complete_device_code)


if __name__ == "__main__":
    main()
