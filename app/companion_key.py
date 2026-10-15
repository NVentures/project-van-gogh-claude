#!/usr/bin/env python3
"""companion_key.py: find, install and re-key the client's key file.

A client receives one small JSON "key file" (see KEY_KIND). Version 1 carried
only the private companion plugin's access (repo NVentures/project-van-gogh-
custom-<name> plus a per-user read-only GitHub token). Version 2 also carries
the shared OAuth app credentials (the Google Desktop client, the Microsoft
public client id, the Maps key), the accounts the client is expected to sign
in with, and who they are, so an install needs no cloud console at all. This
script is the ONLY thing that touches the file: skills pass its PATH and
render the JSON this prints, so no secret appears in a chat transcript or on
a visible command line.

Subcommands:
    discover                   list candidate key files (Downloads, Desktop,
                               the current folder, the private keyfile dir),
                               newest first. With none found the JSON carries
                               the create-your-own instructions.
    install --file <key.json>  install everything the file carries: the
                               credentials bundle, the companion plugin when
                               repo/token are present, then move the file into
                               <state_dir>/keyfile/ (owner-only) for resumes.
    ingest --file <key.json>   companion only, first-time bootstrap: store the
                               token in the state .env, `claude plugin
                               marketplace add` the tokened URL, install the
                               plugin. Idempotent: re-running with a new key
                               falls through to rekey.
    rekey  --file <key.json>   rotation: update the state .env, rewrite the
                               marketplace clone's remote URL (and the
                               known_marketplaces.json entry, best effort),
                               refresh the marketplace, then update the plugin
                               and read the CLI's own verdict.
    status                     what this machine has, secrets redacted.

The companion plugin/marketplace name is the same for every user
(van-gogh-custom), so commands are uniformly /van-gogh-custom:<skill>.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import host
import install_oauth_credentials as ioc
import user_state
from config_loader import force_utf8_io
from platform_compat import NO_WINDOW, claude_bin

KEY_KIND = "van-gogh-key"
PLUGIN_NAME = "van-gogh-custom"       # also the marketplace name
PLUGIN_ID = f"{PLUGIN_NAME}@{PLUGIN_NAME}"
ENV_REPO = "VAN_GOGH_CUSTOM_REPO"
ENV_TOKEN = "VAN_GOGH_CUSTOM_TOKEN"

KEYFILE_NAME = "van-gogh-key.json"
# A browser renames a second download "van-gogh-key-jane-doe (1).json", and a
# dev may hand over "van-gogh-key.json": anything starting with the stem and
# ending in .json is a candidate.
KEYFILE_GLOB_RE = re.compile(r"^van-gogh-key.*\.json$", re.IGNORECASE)

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
OWN_CREDENTIALS_DOC = (_PLUGIN_ROOT / "skills" / "install-van-gogh" / "references"
                       / "own-credentials.md")

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class KeyFileError(ValueError):
    """The key file is missing, malformed, or not a Van Gogh key."""


def keyfile_dir() -> Path:
    """`<state_dir>/keyfile`: where an installed key file lives, owner-only."""
    return user_state.state_dir() / "keyfile"


def _parse_companion(data: dict, required: bool) -> tuple:
    repo = str(data.get("repo") or "").strip()
    token = str(data.get("token") or "").strip()
    if not repo and not token and not required:
        return "", ""
    if not _REPO_RE.fullmatch(repo):
        raise KeyFileError(f"key file has a malformed repo: {repo!r}")
    if not token or any(c.isspace() for c in token):
        raise KeyFileError("key file has an empty or malformed token")
    return repo, token


def parse_key_file(path: Path) -> dict:
    """Validate a v1 or v2 key file; raise KeyFileError on anything wrong.

    Returns {"version", "repo", "token", "issued", "credentials", "accounts",
    "client", "support_email"}. A v1 file has version 1, no credentials, and
    empty accounts/client. A v2 file's credentials are validated with the
    installer's own bundle check so the two parsers can never disagree.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise KeyFileError(f"cannot read key file: {e}") from e
    except ValueError as e:
        raise KeyFileError(f"key file is not valid JSON: {e}") from e
    if not isinstance(data, dict) or data.get("kind") != KEY_KIND:
        raise KeyFileError(
            f'not a Van Gogh key file (expected "kind": "{KEY_KIND}")')
    version = data.get("version", 1)
    if version not in (1, 2):
        raise KeyFileError(f"key file version {version!r} is not supported")
    if version == 1:
        repo, token = _parse_companion(data, required=True)
        return {"version": 1, "repo": repo, "token": token,
                "issued": data.get("issued", ""), "credentials": None,
                "accounts": [], "client": {}, "support_email": ""}

    credentials = data.get("credentials")
    repo, token = _parse_companion(data, required=False)
    if credentials is None and not repo:
        raise KeyFileError("key file carries neither credentials nor a companion repo")
    if credentials is not None:
        if not isinstance(credentials, dict) or credentials.get("kind") != ioc.BUNDLE_KIND:
            raise KeyFileError(
                f'key file credentials must be a "{ioc.BUNDLE_KIND}" bundle')
        problems = ioc.validate_bundle(credentials)
        if problems:
            raise KeyFileError("key file credentials are malformed: " + "; ".join(problems))
    accounts = data.get("accounts") or []
    if not isinstance(accounts, list) or not all(isinstance(a, dict) for a in accounts):
        raise KeyFileError("key file accounts must be a list of objects")
    client = data.get("client") or {}
    if not isinstance(client, dict):
        raise KeyFileError("key file client must be an object")
    return {"version": 2, "repo": repo, "token": token,
            "issued": data.get("issued", ""), "credentials": credentials,
            "accounts": [{"provider": str(a.get("provider") or ""),
                          "email_hint": str(a.get("email_hint") or "")} for a in accounts],
            "client": client,
            "support_email": str(data.get("support_email") or "")}


def tokened_url(repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def redact(text: str, token: str) -> str:
    """Never let the token surface in output, even inside an error message."""
    if not token:
        return text or ""
    return (text or "").replace(token, "***")


def marketplaces_dir() -> Path:
    return Path.home() / ".claude" / "plugins" / "marketplaces"


def known_marketplaces_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "known_marketplaces.json"


def _run_claude(args: list[str], token: str) -> dict:
    """Run a claude CLI subcommand, output captured and redacted. The reply
    carries `stdout` so a caller can read the CLI's own JSON verdict."""
    try:
        result = subprocess.run(
            [claude_bin(), *args], capture_output=True, text=True,
            encoding="utf-8", timeout=300, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"ok": False, "step": " ".join(args[:2]),
                "error": "the claude CLI was not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "step": " ".join(args[:2]), "error": "timed out"}
    if result.returncode != 0:
        detail = redact((result.stderr or result.stdout or "").strip(), token)
        return {"ok": False, "step": " ".join(args[:2]), "error": detail[-800:]}
    return {"ok": True, "stdout": redact(result.stdout or "", token)}


def _rewrite_known_marketplaces(url: str) -> bool:
    """Point known_marketplaces.json's entry at the new URL. Best effort:
    the git remote (rewritten separately) is what updates actually use."""
    path = known_marketplaces_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get(PLUGIN_NAME)
        if not isinstance(entry, dict):
            return False
        source = entry.get("source")
        if isinstance(source, dict) and "url" in source:
            source["url"] = url
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
    except (OSError, ValueError):
        pass
    return False


def _store(key: dict) -> None:
    user_state.upsert_env(ENV_REPO, key["repo"])
    user_state.upsert_env(ENV_TOKEN, key["token"])


def rekey(key: dict) -> dict:
    """Rotate the credential on an existing companion install.

    Refreshing the marketplace only re-clones the catalog; the installed
    plugin stays where it was until `claude plugin update` moves it, and the
    verdict is the CLI's own `updateOutcome`, never its exit code (the same
    pattern as plugin_update.apply_update).
    """
    if not host.is_claude():
        return _codex_skip(key)
    _store(key)
    clone = marketplaces_dir() / PLUGIN_NAME
    url = tokened_url(key["repo"], key["token"])
    if not (clone / ".git").exists():
        # No clone yet: this machine never bootstrapped, fall back to ingest.
        return ingest(key)
    result = subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", url],
        capture_output=True, text=True, encoding="utf-8",
        creationflags=NO_WINDOW)
    if result.returncode != 0:
        return {"ok": False, "step": "git remote set-url",
                "error": redact(result.stderr.strip(), key["token"])[-800:]}
    _rewrite_known_marketplaces(url)
    step = _run_claude(["plugin", "marketplace", "update", PLUGIN_NAME],
                       key["token"])
    if not step["ok"]:
        step["hint"] = ("the new key was stored but the fetch failed; "
                        "check the token has read access to " + key["repo"])
        return step
    step = _run_claude(["plugin", "update", PLUGIN_ID, "--json"], key["token"])
    if not step["ok"]:
        step["hint"] = ("the marketplace was refreshed but the plugin update failed; "
                        f"retry: claude plugin update {PLUGIN_ID}")
        return step
    from plugin_update import _first_json_object
    reply = _first_json_object(step.get("stdout", ""))
    outcome = reply.get("updateOutcome")
    if reply.get("outcome") != "ok" or outcome not in ("updated", "up_to_date"):
        return {"ok": False, "step": "plugin update",
                "error": "the CLI did not report a plugin update outcome",
                "hint": f"retry: claude plugin update {PLUGIN_ID}"}
    return {"ok": True, "mode": "rekey", "repo": key["repo"],
            "plugin": PLUGIN_NAME, "update_outcome": outcome,
            "version": str(reply.get("newVersion") or "")}


CODEX_COMPANION_NOTE = ("Your private Van Gogh skills need Claude, so they were not "
                        "installed under Codex; everything else in the key file was.")


def _codex_skip(key: dict) -> dict:
    """Under Codex the companion plugin is not installed (out of scope for
    the Codex port): its access is kept, so a later move to Claude installs
    it, and the step reports ok with one line instead of failing setup."""
    _store(key)
    return {"ok": True, "mode": "skipped", "repo": key["repo"],
            "plugin": PLUGIN_NAME, "note": CODEX_COMPANION_NOTE}


def ingest(key: dict) -> dict:
    """First-time bootstrap of the companion plugin."""
    if not host.is_claude():
        return _codex_skip(key)
    if (marketplaces_dir() / PLUGIN_NAME / ".git").exists():
        return rekey(key)
    _store(key)
    url = tokened_url(key["repo"], key["token"])
    step = _run_claude(["plugin", "marketplace", "add", url], key["token"])
    if not step["ok"]:
        step["hint"] = ("nothing was installed; check the token, then re-run "
                        "with the same key file")
        return step
    step = _run_claude(["plugin", "install", PLUGIN_ID], key["token"])
    if not step["ok"]:
        step["hint"] = ("the marketplace was added but the install failed; "
                        f"retry: claude plugin install {PLUGIN_ID}")
        return step
    return {"ok": True, "mode": "ingest", "repo": key["repo"],
            "plugin": PLUGIN_NAME}


# ── Discovery ────────────────────────────────────────────────────────────────

def search_dirs() -> list[Path]:
    """Where a key file is looked for, in the order a person would say them."""
    home = Path.home()
    return [home / "Downloads", home / "Desktop", Path.cwd(), keyfile_dir()]


def discover(extra_dirs=()) -> list[Path]:
    """Candidate key files, newest first (by modification time)."""
    seen = set()
    found = []
    for folder in [*search_dirs(), *(Path(d) for d in extra_dirs)]:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file() or not KEYFILE_GLOB_RE.match(entry.name):
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            found.append((mtime, entry))
    found.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in found]


def own_credentials_instructions() -> str:
    """The create-your-own text for a machine with no key file at all."""
    try:
        return OWN_CREDENTIALS_DOC.read_text(encoding="utf-8")
    except OSError:
        return ("No key file was found. Create your own Google and Microsoft "
                "app registrations and install them with "
                "app/install_oauth_credentials.py.")


def discover_payload(extra_dirs=()) -> dict:
    candidates = discover(extra_dirs)
    payload = {"ok": True, "found": len(candidates),
               "candidates": [str(p) for p in candidates],
               "newest": str(candidates[0]) if candidates else None,
               "searched": [str(d) for d in [*search_dirs(), *(Path(d) for d in extra_dirs)]]}
    if not candidates:
        payload["instruction"] = own_credentials_instructions()
    return payload


# ── Install everything ───────────────────────────────────────────────────────

def _refresh_works(provider: str, label: str) -> bool:
    """Make the one real call that proves a sign-in works: refresh it."""
    user_state.load_env()
    if provider == "microsoft":
        import microsoft_client
        microsoft_client.MicrosoftClient(label)._token()
        return True
    import google_auth_httplib2
    import httplib2
    import google_client
    from certs import resolve_ca_bundle
    creds = google_client._credentials(label)
    creds.refresh(google_auth_httplib2.Request(
        httplib2.Http(ca_certs=resolve_ca_bundle())))
    return bool(creds.valid)


_PREFIXES = {"google": "GOOGLE_REFRESH_TOKEN_", "microsoft": "MS_GRAPH_REFRESH_TOKEN_"}


def _labels_with_tokens(provider: str) -> list[str]:
    prefix = _PREFIXES[provider]
    path = user_state.env_file()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    labels = []
    for line in lines:
        if line.startswith(prefix) and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                labels.append(key[len(prefix):])
    return labels


def working_sign_in(provider: str) -> bool:
    """True when at least one stored token for this provider still refreshes.
    Every doubt answers False, which replaces the credential: a credential
    nobody can sign in with is worth nothing to keep."""
    for label in _labels_with_tokens(provider):
        try:
            if _refresh_works(provider, label):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _differs(existing: Path, incoming: dict) -> bool:
    try:
        current = json.loads(existing.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return True
    return current != incoming


def _credentials_to_keep(bundle: dict) -> tuple:
    """(providers to keep, warning lines). A differing credential with a
    working sign-in is kept, because replacing it would invalidate every
    token minted under it; one whose sign-ins are dead is replaced."""
    keep = []
    warnings = []
    for field, provider, filename in ioc.BUNDLE_FIELDS:
        incoming = bundle.get(field)
        if incoming is None:
            continue
        existing = user_state.state_dir() / filename
        if not existing.exists() or not _differs(existing, incoming):
            continue
        if working_sign_in(provider):
            keep.append(provider)
            warnings.append(
                f"kept the existing {provider} app credential: it differs from the "
                "key file's but an account signed in with it still works")
    return tuple(keep), warnings


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _move_into_private(source: Path) -> Path:
    """Move the key file to <state_dir>/keyfile/van-gogh-key.json, dir 0700,
    file 0600. A file already there (a resume) is left in place."""
    folder = keyfile_dir()
    _private_dir(folder)
    target = folder / KEYFILE_NAME
    source = Path(source)
    if source.resolve() != target.resolve():
        shutil.move(str(source), str(target))
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def install_all(path: Path) -> dict:
    """Install everything a key file carries and move it into the private dir.

    Returns {"ok", "mode", "credentials", "companion", "accounts", "client",
    "support_email", "moved_to"} plus `warnings` (a list of lines) and
    `error` when something failed. The file is moved only when the
    credentials step did not fail, so a corrupt or refused file stays where
    the person can see it.
    """
    try:
        key = parse_key_file(Path(path))
    except KeyFileError as exc:
        return {"ok": False, "mode": "", "error": str(exc), "credentials": {},
                "companion": {}, "accounts": [], "client": {},
                "support_email": "", "moved_to": ""}

    result = {"ok": True, "mode": f"v{key['version']}", "credentials": {},
              "companion": {}, "accounts": key["accounts"], "client": key["client"],
              "support_email": key["support_email"], "moved_to": "",
              "warnings": []}

    if key["credentials"] is not None:
        keep, warnings = _credentials_to_keep(key["credentials"])
        cred = ioc.install_bundle(key["credentials"], skip=keep)
        result["credentials"] = cred
        result["warnings"].extend(warnings)
        if not cred.get("ok"):
            result["ok"] = False
            result["error"] = f"credentials: {cred.get('error', 'install failed')}"
            return result

    if key["repo"] and key["token"]:
        companion = ingest(key)
        result["companion"] = companion
        if not companion.get("ok"):
            result["ok"] = False
            result["error"] = (f"companion: {companion.get('error', 'install failed')}"
                               + (f" ({companion['hint']})" if companion.get("hint") else ""))

    try:
        result["moved_to"] = str(_move_into_private(Path(path)))
    except OSError as exc:
        result["warnings"].append(f"could not move the key file into the private folder: "
                                  f"{type(exc).__name__}")
    if result["warnings"]:
        result["warning"] = " ".join(result["warnings"])
    return result


def status() -> dict:
    user_state.load_env()
    clone = marketplaces_dir() / PLUGIN_NAME
    return {
        "ok": True,
        "repo": os.environ.get(ENV_REPO, ""),
        "token_present": bool(os.environ.get(ENV_TOKEN)),
        "marketplace_cloned": (clone / ".git").exists(),
        "plugin": PLUGIN_NAME,
        "keyfile_present": (keyfile_dir() / KEYFILE_NAME).exists(),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "rekey", "install"):
        p = sub.add_parser(name)
        p.add_argument("--file", required=True, help="path to the key file")
    p = sub.add_parser("discover")
    p.add_argument("--dir", action="append", default=[],
                   help="an extra folder to search (repeatable)")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    if args.command == "status":
        result = status()
    elif args.command == "discover":
        result = discover_payload(args.dir)
    elif args.command == "install":
        result = install_all(Path(args.file).expanduser())
    else:
        try:
            key = parse_key_file(Path(args.file).expanduser())
        except KeyFileError as e:
            result = {"ok": False, "error": str(e)}
        else:
            if not key["repo"]:
                result = {"ok": False, "error": "this key file carries no companion repo; "
                                                "use `install --file` instead"}
            else:
                result = ingest(key) if args.command == "ingest" else rekey(key)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
