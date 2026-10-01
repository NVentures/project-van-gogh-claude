"""Punch item P9: the real entry point, against real accounts, with a control.

Two COPIES of the plugin, both carrying one extra never-granted scope in the
consent list. That reproduces the client's condition: a stored token that
predates a scope the code now knows about.

    CONTROL  copy with the pre-fix refresh (scopes=GOOGLE_SCOPES, MSAL sent
             the live list). Its week_review run must report invalid_scope.
    FIXED    copy as shipped. No invalid_scope, no auth error, calendar
             populated.

If the control passes, the test is not falsifiable and the fixed run proves
nothing, so a control that fails to fail is itself a FAIL here.

Only counts and error lines are printed. Mailbox content never leaves the run.

    .venv/bin/python objectives/google-scope-refresh-fix/live_end_to_end.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
NEVER_GRANTED = "https://www.googleapis.com/auth/youtube.readonly"


def _build_copy(dest: Path, pre_fix: bool) -> Path:
    """A plugin copy with the extra scope, optionally with the bug put back."""
    shutil.copytree(REPO / "app", dest / "app")
    scopes = dest / "app" / "oauth_scopes.py"
    text = scopes.read_text(encoding="utf-8")
    anchor = '    "https://www.googleapis.com/auth/contacts.other.readonly",\n'
    assert text.count(anchor) == 1
    scopes.write_text(
        text.replace(anchor, anchor + f'    "{NEVER_GRANTED}",\n'),
        encoding="utf-8")

    if pre_fix:
        client = dest / "app" / "google_client.py"
        text = client.read_text(encoding="utf-8")
        anchor = "        scopes=None,\n"
        assert text.count(anchor) == 1
        client.write_text(text.replace(anchor, "        scopes=GOOGLE_SCOPES,\n"),
                          encoding="utf-8")
        ms = dest / "app" / "microsoft_client.py"
        text = ms.read_text(encoding="utf-8")
        anchor = "scopes=refresh_scopes(self.label))"
        assert text.count(anchor) == 1
        ms.write_text(text.replace(anchor, "scopes=GRAPH_SCOPES)"),
                      encoding="utf-8")
    return dest


def _run(copy: Path, label: str) -> dict:
    out = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), str(copy / "app" / "week_review.py")],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "PYTHONPATH": str(copy / "app")})
    body = out.stdout
    start = body.find("{")
    if start < 0:
        print(f"  {label}: no JSON on stdout, rc={out.returncode}")
        print(f"  stderr tail: {out.stderr.strip()[-300:]}")
        return {}
    try:
        return json.loads(body[start:])
    except json.JSONDecodeError as e:
        print(f"  {label}: JSON did not parse ({e})")
        return {}


def _report(name: str, data: dict) -> tuple:
    errors = data.get("errors", []) or []
    scope_hits = [e for e in errors if re.search(r"invalid_scope", e, re.I)]
    auth_hits = [e for e in errors if "needs you to sign in again" in e]
    events = len(data.get("calendar", []) or [])
    print(f"  {name}: {len(errors)} errors, {len(scope_hits)} invalid_scope, "
          f"{len(auth_hits)} sign-in, {events} calendar events")
    for e in errors[:6]:
        print(f"    error: {e[:160]}")
    return scope_hits, auth_hits, events


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="vg-e2e-") as tmp:
        tmp = Path(tmp)
        print("CONTROL (pre-fix refresh + 1 never-granted scope)")
        data = _run(_build_copy(tmp / "control", pre_fix=True), "control")
        scope_hits, _, _ = _report("control", data)
        if not scope_hits:
            print("  FAIL: the control did not reproduce the bug, so the "
                  "fixed run below proves nothing")
            ok = False

        print("FIXED (as shipped, same extra scope)")
        data = _run(_build_copy(tmp / "fixed", pre_fix=False), "fixed")
        scope_hits, auth_hits, events = _report("fixed", data)
        if scope_hits:
            ok = False
            print("  FAIL: still rejecting the refresh over scopes")
        if auth_hits:
            ok = False
            print("  FAIL: an account was reported as needing a sign-in")
        if events == 0:
            ok = False
            print("  FAIL: no calendar events, the fetch did not work")

    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
