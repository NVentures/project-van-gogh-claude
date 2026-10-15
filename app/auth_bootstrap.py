"""
auth_bootstrap.py: one-time OAuth onboarding for Tier 1 accounts.

Opens a browser login, detects which account signed in, derives a default
label, and saves the refresh token into the per-user state `.env`
(~/.config/van-gogh/.env, see app/user_state.py, outside the plugin cache so
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

  # Machine-readable result (one JSON object on stdout, for the installer):
  python app/auth_bootstrap.py --provider google --json --expect-email jane@acme.com

The OAuth *app* credentials are read from the state dir
(user_state.state_dir(): ~/.config/van-gogh/ or VAN_GOGH_STATE_DIR):
google_client_secret.json and ms_client_secret.json. The key file installer
(app/companion_key.py) or app/install_oauth_credentials.py puts them there. A
copy under the plugin's config/oauth/ is used only as a fallback when the user
has supplied nothing.

Labels never collide. A second account whose derived label equals an existing
one (two accounts on the same domain, two Gmail addresses) gets a distinct
label, since writing the second token under the first label would silently
disconnect the first account. The signed-in address is stored beside each
token (GOOGLE_ACCOUNT_EMAIL_<LABEL> / MS_GRAPH_ACCOUNT_EMAIL_<LABEL>) so a
re-authorization of the SAME address keeps its label. An explicit --label
always wins.

Environments without a usable browser
─────────────────────────────────────
When run on a machine with no browser (a remote/cloud session, SSH, the web
app), the script switches automatically to a two-step copy-paste flow:

  Google: phase 1 prints an auth URL and a machine-readable block:
      ===VANGOGH_HEADLESS_URL===
      https://accounts.google.com/o/oauth2/auth?...
      ===END===
    The user opens that URL, signs in, and is redirected to a dead
    http://localhost:8080/?code=... page. They copy that full URL back, and
    phase 2 completes the exchange:
      python app/auth_bootstrap.py --provider google \
          --redirect-response "http://localhost:8080/?code=..."

  Microsoft: switches to device-code login, also two-phase so the code is
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

With --json the same facts (and every failure) are one JSON object on stdout:
{"ok", "provider", "email", "label", "env_var", "error_class", "line"}. On a
failure `line` is a single sentence of at most 120 characters that always
names the resume command, so the installer can show it unchanged.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import host
import user_state

# The consent screen is the only place scopes are named (oauth_scopes.py).
from oauth_scopes import GOOGLE_SCOPES, MS_RESERVED_SCOPES, MS_SCOPES  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = str(user_state.env_file())

# Loopback redirect used by the headless copy-paste flow. The page never loads
# (nothing listens here in a remote session) but the browser still puts the
# auth code in the address bar, which is all we need.
HEADLESS_REDIRECT_URI = "http://localhost:8080"

# How long the Google loopback server waits for the browser to come back. With
# no limit a closed tab left the installer hanging forever.
GOOGLE_WAIT_S = 300
# MSAL's own interactive wait.
MS_WAIT_S = 180

RESUME_COMMAND = host.cmd("install-van-gogh")
LINE_MAX = 120

# Microsoft error codes that mean an administrator must approve the app. No
# retry and no second browser window: the user cannot fix these by trying again.
ADMIN_CONSENT_CODES = ("AADSTS65001", "AADSTS90094", "AADSTS650052",
                       "AADSTS50105", "AADSTS650056")

# Plain words for each Google permission (keyed by the scope's last path
# segment; the scope strings themselves live only in oauth_scopes.py), for
# the "you left out X" sentence.
SCOPE_WORDS = {
    "gmail.modify": "reading your Gmail",
    "gmail.send": "sending email",
    "calendar": "your Google Calendar",
    "drive": "Google Drive",
    "contacts": "your contacts",
    "contacts.other.readonly": "contacts Gmail collected",
}


def scope_words(scope: str) -> str:
    key = scope.rsplit("/", 1)[-1]
    return SCOPE_WORDS.get(key, key)


def _find_credential(filename: str) -> str:
    """Resolve the OAuth app credential: the state dir first, then repo config/oauth/."""
    user_path = user_state.state_dir() / filename
    if user_path.exists():
        return str(user_path)
    return str(PROJECT_ROOT / "config" / "oauth" / filename)  # caller checks existence


GOOGLE_CLIENT_SECRET_PATH = _find_credential("google_client_secret.json")
MS_CLIENT_SECRETS_PATH = _find_credential("ms_client_secret.json")
MS_AUTHORITY = "https://login.microsoftonline.com/common"

GOOGLE_PREFIX = "GOOGLE_REFRESH_TOKEN"
MS_PREFIX = "MS_GRAPH_REFRESH_TOKEN"
# The scopes a Microsoft account was granted, stored beside its token so a
# refresh asks for those and never for the live consent list.
MS_SCOPES_PREFIX = "MS_GRAPH_SCOPES"
# The address each token belongs to, stored beside it so a second account on
# the same domain never takes the first one's label.
GOOGLE_EMAIL_PREFIX = "GOOGLE_ACCOUNT_EMAIL"
MS_EMAIL_PREFIX = "MS_GRAPH_ACCOUNT_EMAIL"
# When the provider states how long the refresh token lives (Google does for
# an app whose consent screen is in Testing mode: about 7 days), the moment it
# dies is stored beside the token as an ISO 8601 UTC timestamp, so the daily
# check can say "sign in again by Friday" instead of failing on the day. No
# stamp when the provider states none. Neither name starts with a
# `*_REFRESH_TOKEN_` prefix, so nothing that lists accounts by that prefix
# can read a stamp as an account.
GOOGLE_EXPIRES_PREFIX = "GOOGLE_REFRESH_EXPIRES"
MS_EXPIRES_PREFIX = "MS_GRAPH_REFRESH_EXPIRES"


class SignInFailure(Exception):
    """A sign-in that did not produce a usable token, with its class and detail."""

    def __init__(self, error_class: str, detail: str = "", **extra):
        super().__init__(detail)
        self.error_class = error_class
        self.detail = detail
        self.extra = extra


# ── Output modes ─────────────────────────────────────────────────────────────

_JSON = False
_EXPECT_EMAIL = None


def _say(text: str = "") -> None:
    """Human output; silent in --json mode so stdout stays one JSON object."""
    if not _JSON:
        print(text)


def _emit_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _hint_match(email: str):
    if not _EXPECT_EMAIL:
        return None
    if not email:
        return None
    return email.strip().lower() == _EXPECT_EMAIL.strip().lower()


# Where the resume command is typed. The pre-call checklist drills "never
# into Claude" for the setup line, so every line says this one goes INTO
# Claude; an unqualified "type" would land in Terminal.
RESUME_PHRASE = f"in {host.host_word()}, type {RESUME_COMMAND}"


def _fit_line(before: str, detail: str = "", after: str = "") -> str:
    """`before + detail + after`, in at most LINE_MAX characters. Only the
    variable `detail` is ever shortened, so the sentence around it (which
    carries where to type the resume command) always arrives whole."""
    detail = " ".join((detail or "").split())
    room = LINE_MAX - len(before) - len(after)
    if len(detail) > room:
        cut = detail[:max(room, 0)]
        # Shorten at a word boundary: a clipped word reads as a typo.
        detail = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:")
    line = before + detail + after
    assert len(line) <= LINE_MAX, line
    return line


def failure_line(error_class: str, provider: str, detail: str = "") -> str:
    """The one sentence the installer shows for a failed sign-in. Every line
    ends by saying where to type the resume command."""
    name = {"google": "Google", "microsoft": "Microsoft"}.get(provider, provider)
    again = f" To try again, {RESUME_PHRASE}"
    if error_class == "admin_consent":
        return _fit_line(f"Your IT admin must approve Van Gogh for {name}. "
                         f"Once they have, {RESUME_PHRASE}")
    if error_class == "access_denied":
        return _fit_line(f"The {name} sign-in was cancelled.{again}")
    if error_class == "unverified_app":
        return _fit_line("Google has not approved this account yet. Ask the Van Gogh "
                         f"team to add it. In {host.host_word()}, type {RESUME_COMMAND}")
    if error_class == "missing_scope":
        return _fit_line("The sign-in left out ", detail,
                         f". Tick every box next time: {RESUME_PHRASE}")
    if error_class == "timeout":
        return _fit_line(f"The {name} sign-in got no answer in "
                         f"{GOOGLE_WAIT_S // 60} minutes.{again}")
    if error_class == "no_credential":
        return _fit_line(f"The {name} app credential is missing. Add the key file, "
                         f"then {RESUME_PHRASE}")
    if error_class == "no_refresh_token":
        return _fit_line(f"{name} returned no refresh token. To sign in again, "
                         f"{RESUME_PHRASE}")
    if error_class == "no_browser":
        return _fit_line(f"No browser could open for {name} here. On your own "
                         f"computer, {RESUME_PHRASE}")
    if detail:
        return _fit_line(f"{name} refused the sign-in (", detail, f").{again}")
    return _fit_line(f"{name} refused the sign-in.{again}")


def _record_failure(provider: str, error_class: str, line: str) -> None:
    """One ledger row when an install run is in progress. Fails open."""
    try:
        setup = user_state.state_dir() / "setup"
        run_id_file = setup / "run-id"
        if not run_id_file.exists():
            return
        row = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": run_id_file.read_text(encoding="utf-8").strip(),
            "event": "signin_failed",
            "step": f"signin:{provider}",
            "class": "human",
            "detail": f"{error_class}: {line}",
        }
        with open(setup / "timing.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        return


def _fail(provider: str, error_class: str, detail: str = "", **extra) -> None:
    """Report a failed sign-in in the active output mode and exit 1."""
    line_detail = extra.pop("line_detail", detail)
    line = failure_line(error_class, provider, line_detail)
    _record_failure(provider, error_class, line)
    if _JSON:
        payload = {"ok": False, "provider": provider, "email": "", "label": "",
                   "env_var": "", "error_class": error_class, "line": line,
                   "detail": (detail or "")[:800]}
        payload.update(extra)
        _emit_json(payload)
    else:
        print(f"ERROR: {detail}" if detail else "ERROR: the sign-in failed")
        print(line)
    sys.exit(1)


def _label_suffix(label: str) -> str:
    return label.strip().upper().replace(" ", "_").replace("-", "_")


def _derive_label(email: str) -> str:
    """Propose a short label from an email address. Skills usually refine this."""
    domain = email.split("@")[-1].lower()
    if domain in ("gmail.com", "googlemail.com"):
        return "Personal"
    return domain.split(".")[0].capitalize()


def _env_map(env_file: str) -> dict:
    path = Path(env_file)
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def _unique_label(prefix: str, email_prefix: str, email: str, env_file: str,
                  fallback: str) -> tuple:
    """(label, derived) with `label` free in the env file for this address.

    Taken means a token exists under the label for a DIFFERENT address (or an
    address nobody recorded, which is the older install's token and must not
    be overwritten by guesswork). The same address keeps its label, which is
    the re-authorization path.
    """
    derived = _derive_label(email) if email else fallback
    existing = _env_map(env_file)

    def taken(label: str) -> bool:
        suffix = _label_suffix(label)
        if f"{prefix}_{suffix}" not in existing:
            return False
        stored = existing.get(f"{email_prefix}_{suffix}", "")
        return not (email and stored.lower() == email.lower())

    candidates = [derived]
    domain_word = ""
    if email and "@" in email:
        domain_word = email.split("@")[-1].split(".")[0].capitalize()
    if domain_word and domain_word.lower() != derived.lower():
        candidates.append(f"{derived} {domain_word}")
    for label in candidates:
        if not taken(label):
            return label, derived
    n = 2
    while taken(f"{derived} {n}"):
        n += 1
    return f"{derived} {n}", derived


def _upsert_env(env_file: str, key: str, value: str) -> None:
    user_state.upsert_env(key, value, env_path=Path(env_file))


def _save_refresh_token(env_file: str, env_var: str, token) -> None:
    """Store a sign-in's refresh token, or refuse and leave the old one alone.

    Signing in again for a label that is already connected overwrites its
    token in place, which is the whole re-authorize path. That makes an empty
    result dangerous: written through, it would replace a working token with
    nothing and disconnect the account the user was trying to repair.
    """
    if not token or not str(token).strip():
        print("ERROR: the sign-in finished but returned no refresh token, so "
              "nothing was saved. Any existing connection for this account is "
              "untouched. Run the same command again.")
        sys.exit(1)
    _upsert_env(env_file, env_var, str(token))


def _granted_ms_scopes(result: dict) -> list[str]:
    """The scopes Microsoft says this sign-in was granted, minus MSAL's own."""
    raw = result.get("scope") or ""
    items = raw.split() if isinstance(raw, str) else list(raw)
    return [s for s in items if s and s.lower() not in MS_RESERVED_SCOPES]


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


def _remove_env_key(env_file: str, key: str) -> bool:
    """Drop `key=...` from the env file. True when a line was removed."""
    path = Path(env_file)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    kept = [line for line in lines if not line.startswith(key + "=")]
    if len(kept) == len(lines):
        return False
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8",
                    newline="\n")
    return True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def refresh_expiry_stamp(seconds) -> str:
    """ISO 8601 UTC timestamp of now plus the stated lifetime, "" when the
    provider stated none (or stated something that is not a positive number)."""
    try:
        lifetime = int(seconds)
    except (TypeError, ValueError):
        return ""
    if lifetime <= 0:
        return ""
    return (_utcnow() + timedelta(seconds=lifetime)).replace(microsecond=0).isoformat()


def _store_refresh_expiry(env_file: str, prefix: str, label: str, seconds) -> str:
    """Write the stamp for this label, or remove a stale one: a new sign-in
    that states no lifetime must not leave the old token's deadline behind."""
    key = f"{prefix}_{_label_suffix(label)}"
    stamp = refresh_expiry_stamp(seconds)
    if stamp:
        _upsert_env(env_file, key, stamp)
    else:
        _remove_env_key(env_file, key)
    return stamp


def _google_token_response(flow) -> dict:
    """The raw token response the flow's session kept (it carries fields the
    Credentials object does not, such as refresh_token_expires_in)."""
    token = getattr(getattr(flow, "oauth2session", None), "token", None)
    return token if isinstance(token, dict) else {}


def _has_browser() -> bool:
    """True if a usable web browser is available to open a login window."""
    import webbrowser

    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def _print_result(provider: str, email: str, label: str, env_var: str, env_file: str,
                  derived: str | None = None) -> None:
    if _JSON:
        payload = {"ok": True, "provider": provider, "email": email, "label": label,
                   "env_var": env_var, "env_file": env_file, "error_class": "",
                   "line": "", "label_derived": derived or label,
                   "label_adjusted": bool(derived) and derived != label}
        if _EXPECT_EMAIL:
            payload["email_matches_hint"] = _hint_match(email)
        _emit_json(payload)
        return
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
    shared system temp dir: it holds a PKCE verifier / device-flow handle)."""
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
        prompt="select_account consent",
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
    if _JSON:
        _emit_json({"ok": True, "provider": "google", "pending": "redirect_response",
                    "auth_url": auth_url, "error_class": "", "line": ""})
        return
    print("\n── Google sign-in (step 1 of 2) ───────────────────────────────────────")
    print("Open this URL in any browser, sign in, and approve access.")
    print("Your browser will then redirect to a 'site can't be reached' page at")
    print("http://localhost:8080/?code=... (that is expected). Copy the FULL URL")
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


def _redirect_error(url: str) -> str:
    """The `error` query value of a pasted redirect URL, "" when none."""
    from urllib.parse import parse_qs, urlparse

    try:
        return parse_qs(urlparse(url).query).get("error", [""])[0]
    except Exception:  # noqa: BLE001
        return ""


def classify_google_error(exc) -> tuple:
    """(error_class, detail) for an exception out of the Google flow."""
    return classify_google_text(str(exc), type(exc).__name__,
                                str(getattr(exc, "error", "") or ""),
                                str(getattr(exc, "description", "") or ""))


def classify_google_text(text: str, name: str = "", error: str = "",
                         description: str = "") -> tuple:
    low = (text or "").lower()
    if name == "WSGITimeoutError" or "timed out" in low:
        return "timeout", "no response from the browser within the wait"
    if error == "access_denied" or "access_denied" in low:
        blob = (description + " " + text).lower()
        if ("verification" in blob or "test user" in blob or "org_internal" in blob
                or "unverified" in blob):
            return "unverified_app", text
        if any(w in blob for w in ("admin", "policy", "blocked by your organ",
                                   "organization's policies")):
            return "admin_consent", text
        return "access_denied", text
    if "invalid_client" in low or "deleted_client" in low or "unauthorized_client" in low:
        return "provider_error", text
    return "provider_error", text


def _granted_google_scopes(creds) -> list:
    """What Google says was granted, read from the token response the library
    kept (granted_scopes); older libraries carry nothing and the check is
    skipped rather than invented."""
    granted = getattr(creds, "granted_scopes", None)
    if isinstance(granted, str):
        granted = granted.split()
    return list(granted or [])


def _missing_google_scopes(creds) -> list:
    granted = _granted_google_scopes(creds)
    if not granted:
        return []
    return [s for s in GOOGLE_SCOPES if s not in granted]


def bootstrap_google(label: str | None, env_file: str, headless: bool = False,
                     redirect_response: str | None = None) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(GOOGLE_CLIENT_SECRET_PATH):
        _fail("google", "no_credential",
              f"shared Google app credential not found at {GOOGLE_CLIENT_SECRET_PATH}; "
              "it arrives with the key file, you do not need to create your own Google Cloud project")

    # An unticked box on Google's consent screen returns fewer scopes than were
    # asked for, and oauthlib treats that as an error ("Scope has changed")
    # unless told to relax. Relax it, then compare the granted set ourselves
    # below and name the box in plain words.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH, scopes=GOOGLE_SCOPES)

    if redirect_response is not None:
        # Phase 2 of the copy-paste flow: complete the exchange and fall through to save.
        pasted_error = _redirect_error(redirect_response)
        if pasted_error:
            error_class, detail = classify_google_text(
                f"{pasted_error}: {redirect_response}", error=pasted_error)
            _fail("google", error_class, detail)
        try:
            creds, label = _google_headless_phase2(flow, redirect_response, label)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            error_class, detail = classify_google_error(exc)
            _fail("google", error_class, detail)
    else:
        if not headless and not _has_browser():
            headless = True
        if headless:
            # Phase 1: print the URL and stop. The orchestrator re-invokes with
            # --redirect-response once the user has pasted the redirect URL.
            _google_headless_phase1(flow, label, env_file)
            return
        _say("\nA browser window will open so you can sign in with Google.")
        _say("If it does NOT open automatically, copy the URL printed below into any browser.\n")
        try:
            creds = flow.run_local_server(
                port=0,
                prompt="select_account consent",
                access_type="offline",
                open_browser=True,
                timeout_seconds=GOOGLE_WAIT_S,
                authorization_prompt_message=(
                    "" if _JSON else
                    "If your browser did not open, sign in at this URL:\n\n    {url}\n"),
            )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            error_class, detail = classify_google_error(exc)
            _fail("google", error_class, detail)

    missing = _missing_google_scopes(creds)
    if missing:
        words = ", ".join(scope_words(s) for s in missing)
        _fail("google", "missing_scope",
              f"the sign-in did not grant: {words}; nothing was saved, sign in again "
              "and leave every box ticked",
              line_detail=(scope_words(missing[0]) if len(missing) == 1
                           else f"{len(missing)} permissions"),
              missing_scopes=missing)

    # Save the token BEFORE the email lookup so a network/SSL hiccup can never
    # leave us having completed OAuth but lost the refresh token.
    email = ""
    try:
        email = _google_email(creds)
    except Exception as exc:  # noqa: BLE001
        _say(f"Warning: token saved, but the email lookup failed ({type(exc).__name__}). "
             "You can fix the label later with --relabel.")
    derived = None
    if not label:
        label, derived = _unique_label(GOOGLE_PREFIX, GOOGLE_EMAIL_PREFIX, email,
                                       env_file, "Google")
    env_var = f"{GOOGLE_PREFIX}_{_label_suffix(label)}"
    if not creds.refresh_token or not str(creds.refresh_token).strip():
        _fail("google", "no_refresh_token",
              "the sign-in finished but returned no refresh token, so nothing was saved; "
              "any existing connection for this account is untouched")
    _save_refresh_token(env_file, env_var, creds.refresh_token)
    if email:
        _upsert_env(env_file, f"{GOOGLE_EMAIL_PREFIX}_{_label_suffix(label)}", email)
    _store_refresh_expiry(env_file, GOOGLE_EXPIRES_PREFIX, label,
                          _google_token_response(flow).get("refresh_token_expires_in"))

    summary = f"Connected {email} and saved its token" if email else "Saved the token (email lookup unavailable)"
    _say(f"\n{summary} under label '{label}'.")
    _print_result("google", email, label, env_var, env_file, derived)


# ── Microsoft ───────────────────────────────────────────────────────────────

def _ms_app():
    import msal

    if not os.path.exists(MS_CLIENT_SECRETS_PATH):
        _fail("microsoft", "no_credential",
              f"shared Microsoft app credential not found at {MS_CLIENT_SECRETS_PATH}; "
              "it arrives with the key file")
    with open(MS_CLIENT_SECRETS_PATH, encoding="utf-8") as f:
        client_id = json.load(f)["client_id"]
    return msal.PublicClientApplication(client_id, authority=MS_AUTHORITY)


def _ms_state_file() -> Path:
    return _auth_state_file("ms_device_flow.json")


def is_admin_consent(result: dict) -> bool:
    """True when Microsoft's error says an administrator has to approve the app."""
    if not isinstance(result, dict):
        return False
    text = " ".join(str(result.get(k) or "") for k in
                    ("error", "error_description", "suberror", "error_uri"))
    if any(code in text for code in ADMIN_CONSENT_CODES):
        return True
    codes = result.get("error_codes") or []
    if any(f"AADSTS{c}" in ADMIN_CONSENT_CODES for c in codes):
        return True
    if str(result.get("suberror") or "") == "consent_required":
        return True
    if result.get("error") == "interaction_required" and "consent" in text.lower():
        return True
    return False


def classify_ms_result(result) -> tuple:
    """(error_class, detail) for a Microsoft result carrying no access token."""
    if not isinstance(result, dict) or not result:
        return "timeout", "the sign-in window closed or timed out with no answer"
    description = str(result.get("error_description") or result.get("error") or "")
    if is_admin_consent(result):
        return "admin_consent", description
    error = str(result.get("error") or "")
    if error == "access_denied":
        return "access_denied", description
    if error in ("timeout", "broker_error") or "timed out" in description.lower():
        return "timeout", description
    return "provider_error", description


def _ms_interactive(app):
    """The browser sign-in. Returns the token result, or the provider's error
    result, or None when the window never produced anything.

    One window for an answer Microsoft gave (consent needed, cancelled, any
    AADSTS code): trying again cannot change it. A second window only when
    the first produced nothing at all (the launch failed or timed out).
    """
    _say("\nA browser window will open so you can sign in with Microsoft.")
    last = None
    for attempt in (1, 2):
        try:
            result = app.acquire_token_interactive(scopes=MS_SCOPES, prompt="select_account",
                                                   timeout=MS_WAIT_S)
        except Exception as e:  # noqa: BLE001
            _say(f"Browser sign-in attempt {attempt} failed: {type(e).__name__}: {e}")
            result = None
        if result and "access_token" in result:
            return result
        if result and result.get("error"):
            return result  # Microsoft answered; a retry would only repeat it
        last = result
        if attempt == 1:
            _say("Retrying the sign-in window once...\n")
    return last


def _ms_device_phase1(app, label: str | None, env_file: str) -> None:
    """Initiate device-code login, print the URL + code, persist the flow, and exit.

    Critically this does NOT poll: it returns immediately so the orchestrator
    can show the user the code before the (blocking) wait happens in phase 2.
    """
    flow = app.initiate_device_flow(scopes=MS_SCOPES)
    if "user_code" not in flow:
        _fail("microsoft", "provider_error",
              f"could not start device-code sign-in: {flow.get('error_description')}",
              line_detail=str(flow.get("error") or "device code"))
    _ms_state_file().write_text(
        json.dumps({"flow": flow, "label": label, "env_file": env_file}), encoding="utf-8"
    )
    if _JSON:
        _emit_json({"ok": True, "provider": "microsoft", "pending": "device_code",
                    "verification_uri": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
                    "user_code": flow["user_code"], "expires_in": flow.get("expires_in", 900),
                    "error_class": "", "line": ""})
        return
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
    _say("Waiting for you to finish signing in...")
    result = app.acquire_token_by_device_flow(saved["flow"])  # blocks: polls until done/expiry
    return result, (label or saved.get("label"))


def bootstrap_microsoft(label: str | None, env_file: str, device_code: bool = False,
                        complete_device_code: bool = False) -> None:
    app = _ms_app()

    if complete_device_code:
        result, label = _ms_device_phase2(app, label)
    else:
        # No browser: skip the interactive attempt (it would burn a long timeout
        # before failing) and go straight to device-code phase 1.
        if not device_code and not _has_browser():
            device_code = True
        if device_code:
            _ms_device_phase1(app, label, env_file)
            return  # phase 1 exits here; orchestrator re-invokes --complete-device-code
        result = _ms_interactive(app)
        if result is None:
            # Browser sign-in never produced anything: fall back to device-code
            # phase 1 so the code still gets surfaced (rather than failing outright).
            _say("\nFalling back to device-code sign-in.")
            _ms_device_phase1(app, label, env_file)
            return

    succeeded = (isinstance(result, dict) and not result.get("error")
                 and ("access_token" in result or "refresh_token" in result))
    if not succeeded:
        error_class, detail = classify_ms_result(result)
        code = ""
        if isinstance(result, dict):
            codes = result.get("error_codes") or []
            code = f"AADSTS{codes[0]}" if codes else str(result.get("error") or "")
        _fail("microsoft", error_class, detail, line_detail=code)

    if "refresh_token" not in result:
        _fail("microsoft", "no_refresh_token",
              f"no refresh token returned: {result.get('error_description', '')}")

    claims = result.get("id_token_claims", {})
    email = claims.get("preferred_username") or claims.get("email") or ""
    derived = None
    if not label:
        label, derived = _unique_label(MS_PREFIX, MS_EMAIL_PREFIX, email, env_file, "Outlook")
    env_var = f"{MS_PREFIX}_{_label_suffix(label)}"
    _save_refresh_token(env_file, env_var, result["refresh_token"])
    granted = _granted_ms_scopes(result)
    if granted:
        _upsert_env(env_file, f"{MS_SCOPES_PREFIX}_{_label_suffix(label)}",
                    ",".join(granted))
    if email:
        _upsert_env(env_file, f"{MS_EMAIL_PREFIX}_{_label_suffix(label)}", email)
    _store_refresh_expiry(env_file, MS_EXPIRES_PREFIX, label,
                          result.get("refresh_token_expires_in"))

    _say(f"\nConnected {email} and saved its token under label '{label}'.")
    _print_result("microsoft", email, label, env_var, env_file, derived)


# ── Relabel (no re-login) ─────────────────────────────────────────────────────

def relabel(provider: str, old_label: str, new_label: str, env_file: str) -> None:
    prefix = GOOGLE_PREFIX if provider == "google" else MS_PREFIX
    old_key = f"{prefix}_{_label_suffix(old_label)}"
    new_key = f"{prefix}_{_label_suffix(new_label)}"
    email_prefix = GOOGLE_EMAIL_PREFIX if provider == "google" else MS_EMAIL_PREFIX
    if provider != "google":
        # The granted-scope list travels with its token, or the renamed
        # account would fall back to the baseline list.
        _rename_env_key(env_file,
                        f"{MS_SCOPES_PREFIX}_{_label_suffix(old_label)}",
                        f"{MS_SCOPES_PREFIX}_{_label_suffix(new_label)}")
    # The expiry stamp belongs to the token, so it is renamed with it.
    expires_prefix = GOOGLE_EXPIRES_PREFIX if provider == "google" else MS_EXPIRES_PREFIX
    _rename_env_key(env_file,
                    f"{expires_prefix}_{_label_suffix(old_label)}",
                    f"{expires_prefix}_{_label_suffix(new_label)}")
    # The recorded address travels too, or the next sign-in reads the renamed
    # label as free and a second account could take it.
    _rename_env_key(env_file,
                    f"{email_prefix}_{_label_suffix(old_label)}",
                    f"{email_prefix}_{_label_suffix(new_label)}")
    if _rename_env_key(env_file, old_key, new_key):
        if _JSON:
            _emit_json({"ok": True, "provider": provider, "old_env_var": old_key,
                        "new_env_var": new_key, "label": new_label,
                        "error_class": "", "line": ""})
            return
        print(f"Renamed {old_key} -> {new_key} in {env_file}")
        print("\n===VANGOGH_RELABEL===")
        print(f"old_env_var={old_key}")
        print(f"new_env_var={new_key}")
        print(f"label={new_label}")
        print("===END===")
    else:
        _fail(provider, "provider_error", f"{old_key} not found in {env_file}",
              line_detail="no such label")


def main(argv=None) -> None:
    global _JSON, _EXPECT_EMAIL
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
    parser.add_argument("--json", action="store_true",
                        help="Print one JSON object on stdout (success and failure alike).")
    parser.add_argument("--expect-email", default=None, metavar="HINT",
                        help="The address the key file expects; the JSON reports email_matches_hint.")
    args = parser.parse_args(argv)

    _JSON = bool(args.json)
    _EXPECT_EMAIL = args.expect_email

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
