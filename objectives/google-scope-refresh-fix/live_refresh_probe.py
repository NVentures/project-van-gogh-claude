"""Live probe for punch items P2 and P8. Reads real tokens, prints none.

Run from the plugin root:
    .venv/bin/python objectives/google-scope-refresh-fix/live_refresh_probe.py

Section 1 (Google) is a control plus the real thing, in one run:
  CONTROL  credentials built the OLD way, with the consent list plus one valid
           scope this token was never granted, must fail with invalid_scope.
           That is the client's condition, reproduced against real Google.
  FIXED    google_client._credentials() must refresh, and a Calendar list
           call must answer.
Section 2 (Microsoft) refreshes through the real client and makes one read.

Exit 0 only if every expectation held. The control's error text is saved
beside this file so the unit tests can use a verbatim capture.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "app"))

import config_loader  # noqa: E402
import google_client as gc  # noqa: E402

# A real, valid Google scope that Van Gogh has never requested, so no Van Gogh
# token has been granted it.
NEVER_GRANTED = "https://www.googleapis.com/auth/youtube.readonly"


def _google(label: str) -> bool:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import oauth_scopes

    ok = True
    client_id, client_secret = gc._oauth_app_creds()
    old_way = Credentials(
        token=None,
        refresh_token=gc._refresh_token_for(label),
        client_id=client_id,
        client_secret=client_secret,
        token_uri=gc.TOKEN_URI,
        scopes=list(oauth_scopes.GOOGLE_SCOPES) + [NEVER_GRANTED],
    )
    try:
        old_way.refresh(Request())
        print("CONTROL  FAIL: the old construction refreshed, so this probe "
              "cannot show the bug")
        ok = False
    except RefreshError as e:
        text = f"{type(e).__name__}: {e}"
        hit = "invalid_scope" in text
        print(f"CONTROL  {'ok' if hit else 'FAIL'}: old construction + 1 "
              f"ungranted scope -> {text}")
        ok = ok and hit
        if hit:
            (HERE / "captured_invalid_scope.txt").write_text(
                text + "\n", encoding="utf-8")

    try:
        creds = gc._credentials(label)
        creds.refresh(Request())
        n = len(gc.GoogleClient(label).calendar.calendarList()
                .list(maxResults=5).execute().get("items", []))
        print(f"FIXED    ok: google_client._credentials() refreshed; Calendar "
              f"list answered with {n} calendars (scopes passed: {creds.scopes})")
        ok = ok and creds.scopes is None
    except Exception as e:
        print(f"FIXED    FAIL: {type(e).__name__}")
        ok = False
    return ok


def _microsoft(label: str) -> bool:
    import microsoft_client as mc
    try:
        m = mc.microsoft_client(label)
        used = mc.refresh_scopes(label)
        me = m.get("/me", {"$select": "id"})
        print(f"MS       ok: refreshed with {len(used)} scopes "
              f"({'stored' if mc.stored_scopes(label) else 'baseline'} path); "
              f"Graph /me answered: {bool(me.get('id'))}")
        return bool(me.get("id"))
    except Exception as e:
        print(f"MS       FAIL: {type(e).__name__}")
        return False


def main() -> int:
    ok = True
    googles = config_loader.google_accounts()
    microsofts = config_loader.microsoft_accounts()
    print(f"accounts: {len(googles)} Google, {len(microsofts)} Microsoft")
    if not googles:
        print("FAIL: no Google account configured, nothing was probed")
        return 1
    for a in googles:
        print(f"-- Google account label={a['label']}")
        ok = _google(a["label"]) and ok
    for a in microsofts:
        print(f"-- Microsoft account label={a['label']}")
        ok = _microsoft(a["label"]) and ok
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
