#!/usr/bin/env python3
"""
install_oauth_credentials.py — put the OAuth *app* credentials where the clients look.

The user supplies these at install time (and again if a key is ever rotated or
revoked). They are the app registration, not the user's mailbox tokens: the
Google Desktop-client JSON downloaded from Cloud Console, and the Microsoft
public-client id from an Entra app registration.

Both land in ~/.config/van-gogh/, which every client checks BEFORE the copy
bundled in the plugin's config/oauth/ (see auth_bootstrap._find_credential,
google_client.GOOGLE_CLIENT_SECRET_PATH, microsoft_client.MS_CLIENT_SECRET_PATH).
So installing here overrides the bundled credential without touching the plugin
cache, which a marketplace update re-clones.

Modes:
  --file PATH      Read the JSON from a file the user downloaded.
  --stdin          Read the JSON from stdin (the paste flow).
  --status         Report what is installed, for which provider, from where.

Provider is auto-detected from the JSON shape, so the caller never has to ask
the user which file they are handing over:
  {"kind": "van-gogh-credentials", ...}   -> the bundle, all three at once
  {"installed": {...}} or {"web": {...}}  -> Google
  {"client_id": "...", ...}               -> Microsoft

The bundle is the normal install path: one paste carrying both app credentials
and the Maps key, generated per client by tools/credentials_bundle.py. The
single-provider shapes stay because a rotation of one credential should not
make the user re-paste all three, and because a file downloaded straight from
Cloud Console still has to work.

The Maps key is not a file: API keys live in the state `.env` beside
GRAIN_API_KEY, written through user_state.upsert_env. A bundle carrying no
maps_api_key is valid -- travel then runs without live traffic rather than
failing.

Every mode prints a single JSON object on stdout and exits 0 on success, 1 on
failure, so a skill branches on machine-readable output rather than prose.
Secret VALUES are never printed -- only whether one is present.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import user_state  # noqa: E402

GOOGLE_FILENAME = "google_client_secret.json"
MS_FILENAME = "ms_client_secret.json"

BUNDLE_KIND = "van-gogh-credentials"
MAPS_ENV_KEY = "GOOGLE_MAPS_API_KEY"

# The plugin's bundled fallback, used only when nothing is installed per-user.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _emit(payload: dict, ok: bool) -> int:
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


def _write_private(target: Path, text: str) -> None:
    """Write a credential so it is never briefly readable, never half-written.

    Two things a plain write_text gets wrong for this file. It creates at the
    umask default (0644 here), so the Google client secret is world-readable
    until a following chmod tightens it. And it truncates in place, so a crash
    or a full disk mid-write leaves a corrupt credential -- read at import by
    every OAuth client, so it breaks all auth, and during a rotation the user
    has just overwritten their only good copy.

    So: create the temp file with 0600 already set, then os.replace, which is
    atomic on POSIX and Windows alike (same approach as user_state._atomic_write,
    which does not set a mode).
    """
    tmp = target.with_name(target.name + ".van-gogh-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def detect_provider(data):
    """Classify a pasted credential.

    Returns (provider, inner, error). On success provider is "google" or
    "microsoft" and inner is the dict holding client_id; on failure provider is
    None and error explains what was wrong. inner is always a dict so callers
    never have to narrow a union before reading it.
    """
    if not isinstance(data, dict):
        return None, {}, "the JSON must be an object, not a list or bare value"
    if data.get("kind") == BUNDLE_KIND:
        return "bundle", data, ""
    for key in ("installed", "web"):
        inner = data.get(key)
        if isinstance(inner, dict):
            return "google", inner, ""
    if isinstance(data.get("client_id"), str):
        return "microsoft", data, ""
    return None, {}, (
        "unrecognized shape: expected a Google client file with an "
        '"installed" (or "web") object, or a Microsoft file with a top-level '
        '"client_id"'
    )


def validate(provider: str, inner: dict) -> list:
    """Return a list of human-readable problems; empty means valid."""
    problems = []
    if not inner.get("client_id"):
        problems.append(f'{provider} credential is missing "client_id"')
    # Google signs in with the client secret; Microsoft is a public client and
    # deliberately has none, so requiring one there would reject a valid file.
    if provider == "google" and not inner.get("client_secret"):
        problems.append('Google credential is missing "client_secret"')
    return problems


def validate_bundle(data: dict) -> list:
    """Return a list of human-readable problems with a credential bundle.

    A bundle must carry at least one credential, and every credential it does
    carry must be well formed. Partial bundles are deliberately allowed: a
    client who only needs Google should not be forced to hold a Microsoft
    registration, and the Maps key is optional everywhere.
    """
    problems = []
    present = 0
    for field, provider in (("google_client_secret", "google"),
                            ("ms_client_secret", "microsoft")):
        inner_doc = data.get(field)
        if inner_doc is None:
            continue
        present += 1
        if not isinstance(inner_doc, dict):
            problems.append(f'"{field}" must be an object')
            continue
        found, inner, error = detect_provider(inner_doc)
        if found != provider:
            problems.append(f'"{field}" does not hold a {provider} credential'
                            + (f": {error}" if error else ""))
            continue
        problems.extend(validate(provider, inner))

    maps_key = data.get("maps_api_key")
    if maps_key is not None:
        present += 1
        if not isinstance(maps_key, str) or not maps_key.strip():
            problems.append('"maps_api_key" must be a non-empty string')
        elif any(c.isspace() for c in maps_key.strip()):
            problems.append('"maps_api_key" must not contain whitespace')

    if not present:
        problems.append(
            'the bundle carries nothing: expected at least one of '
            '"google_client_secret", "ms_client_secret", "maps_api_key"')
    return problems


def _install_bundle(data: dict) -> int:
    """Write every credential a bundle carries, or none of them.

    Validated up front so a bundle with a malformed Microsoft block does not
    land the Google half and then fail: a half-applied credential set is the
    state that produces a confusing "auth works for one account" support call.
    """
    problems = validate_bundle(data)
    if problems:
        return _emit({"ok": False, "provider": "bundle",
                      "error": "; ".join(problems)}, False)

    installed = []
    for field, provider, filename in (
            ("google_client_secret", "google", GOOGLE_FILENAME),
            ("ms_client_secret", "microsoft", MS_FILENAME)):
        inner_doc = data.get(field)
        if inner_doc is None:
            continue
        target = user_state.state_dir() / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        replaced = target.exists()
        try:
            _write_private(target, json.dumps(inner_doc, indent=2) + "\n")
        except OSError as exc:
            return _emit({
                "ok": False,
                "provider": "bundle",
                "error": f"could not write {target}: {exc}",
                "installed": installed,
            }, False)
        installed.append({"provider": provider, "path": str(target),
                          "replaced": replaced})

    maps_key = data.get("maps_api_key")
    maps_installed = False
    if isinstance(maps_key, str) and maps_key.strip():
        try:
            user_state.upsert_env(MAPS_ENV_KEY, maps_key.strip())
        except OSError as exc:
            return _emit({
                "ok": False,
                "provider": "bundle",
                "error": f"could not write the Maps key: {exc}",
                "installed": installed,
            }, False)
        maps_installed = True
        installed.append({"provider": "maps", "path": str(user_state.env_file()),
                          "replaced": False})

    return _emit({
        "ok": True,
        "provider": "bundle",
        "issued": data.get("issued", ""),
        "installed": installed,
        "google": any(i["provider"] == "google" for i in installed),
        "microsoft": any(i["provider"] == "microsoft" for i in installed),
        "maps": maps_installed,
        "state_dir": str(user_state.state_dir()),
    }, True)


def _maps_key_present() -> bool:
    """True if a Maps key is readable. A real env var wins over the file, the
    same precedence user_state.load_env sets, so a cloud routine that exports
    one is not reported as missing."""
    if os.environ.get(MAPS_ENV_KEY, "").strip():
        return True
    path = user_state.env_file()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith(MAPS_ENV_KEY + "="):
            return bool(line.split("=", 1)[1].strip())
    return False


def _resolved_source(filename: str) -> dict:
    """Where the clients would read this credential from right now."""
    user_path = user_state.state_dir() / filename
    bundled = PROJECT_ROOT / "config" / "oauth" / filename
    if user_path.exists():
        return {"source": "user", "path": str(user_path)}
    if bundled.exists():
        return {"source": "bundled", "path": str(bundled)}
    return {"source": "missing", "path": None}


def _summarize(filename: str, provider: str) -> dict:
    """Describe an installed credential without ever printing a secret."""
    info = {"provider": provider, **_resolved_source(filename)}
    if not info["path"]:
        info["valid"] = False
        return info
    try:
        data = json.loads(Path(info["path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        info["valid"] = False
        info["error"] = f"could not read: {exc}"
        return info
    found, inner, _ = detect_provider(data)
    if found != provider:
        info["valid"] = False
        info["error"] = "file does not hold a credential for this provider"
        return info
    info["valid"] = not validate(provider, inner)
    info["client_id"] = inner.get("client_id", "")
    info["has_secret"] = bool(inner.get("client_secret"))
    if provider == "microsoft":
        info["tenant_id"] = data.get("tenant_id", "")
    return info


def status() -> int:
    google = _summarize(GOOGLE_FILENAME, "google")
    ms = _summarize(MS_FILENAME, "microsoft")
    maps = _maps_key_present()
    return _emit({
        "ok": True,
        "google": google,
        "microsoft": ms,
        # A skill reads this to decide whether to prompt: "user" means the
        # person supplied their own, which is the state install aims for.
        "needs_google": google["source"] != "user" or not google["valid"],
        "needs_microsoft": ms["source"] != "user" or not ms["valid"],
        # Travel's live drive times need this; everything else runs without it,
        # so a skill treats a missing one as a note, never a blocked install.
        "maps": {"present": maps},
        "needs_maps": not maps,
        "state_dir": str(user_state.state_dir()),
    }, True)


def install(raw: str) -> int:
    # A BOM survives a Windows clipboard paste and a file read that did not ask
    # for utf-8-sig. json.loads rejects it, and the user is told their file is
    # not JSON when it is, so strip it here where both entry points meet.
    raw = (raw or "").lstrip("﻿").strip()
    if not raw:
        return _emit({"ok": False, "error": "no JSON was provided"}, False)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _emit({
            "ok": False,
            "error": f"not valid JSON: {exc}",
            "hint": "paste the whole file, including the outer { and }",
        }, False)

    provider, inner, error = detect_provider(data)
    if provider is None:
        return _emit({"ok": False, "error": error}, False)
    if provider == "bundle":
        return _install_bundle(data)

    problems = validate(provider, inner)
    if problems:
        return _emit({"ok": False, "provider": provider, "error": "; ".join(problems)}, False)

    filename = GOOGLE_FILENAME if provider == "google" else MS_FILENAME
    target = user_state.state_dir() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    replaced = target.exists()

    # Re-serialized rather than copied verbatim so a file with a UTF-8 BOM or
    # CRLF endings lands in the shape json.load expects on every platform.
    try:
        _write_private(target, json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        return _emit({
            "ok": False,
            "provider": provider,
            "error": f"could not write {target}: {exc}",
        }, False)

    return _emit({
        "ok": True,
        "provider": provider,
        "path": str(target),
        "replaced": replaced,
        "client_id": inner.get("client_id", ""),
        "has_secret": bool(inner.get("client_secret")),
    }, True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to the downloaded credential JSON")
    g.add_argument("--stdin", action="store_true", help="read the JSON from stdin")
    g.add_argument("--status", action="store_true", help="report what is installed")
    args = ap.parse_args(argv)

    if args.status:
        return status()
    if args.stdin:
        return install(sys.stdin.read())

    path = Path(args.file).expanduser()
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return _emit({"ok": False, "error": f"could not read {path}: {exc}"}, False)
    return install(raw)


if __name__ == "__main__":
    sys.exit(main())
