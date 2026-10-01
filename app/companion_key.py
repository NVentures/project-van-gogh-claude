#!/usr/bin/env python3
"""companion_key.py — install and re-key the private companion plugin.

Some users receive a personal, private companion plugin (repo
NVentures/project-van-gogh-custom-<name>) alongside the public plugin. Access
is a per-user read-only GitHub token, delivered as a small JSON "key file"
(see KEY_KIND). This script is the ONLY thing that touches that token: skills
pass the key file's PATH and render the JSON this prints, so the secret never
appears in a chat transcript or on a visible command line.

Subcommands:
    ingest --file <key.json>   first-time bootstrap: store the token in the
                               state .env, `claude plugin marketplace add` the
                               tokened URL, install the plugin. Idempotent —
                               re-running with a new key falls through to rekey.
    rekey  --file <key.json>   rotation: update the state .env, rewrite the
                               marketplace clone's remote URL (and the
                               known_marketplaces.json entry, best effort),
                               verify with a marketplace update.
    status                     what this machine has, secrets redacted.

The companion plugin/marketplace name is the same for every user
(van-gogh-custom), so commands are uniformly /van-gogh-custom:<skill>.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import user_state
from config_loader import force_utf8_io
from platform_compat import NO_WINDOW, claude_bin

KEY_KIND = "van-gogh-key"
PLUGIN_NAME = "van-gogh-custom"       # also the marketplace name
ENV_REPO = "VAN_GOGH_CUSTOM_REPO"
ENV_TOKEN = "VAN_GOGH_CUSTOM_TOKEN"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class KeyFileError(ValueError):
    """The key file is missing, malformed, or not a Van Gogh key."""


def parse_key_file(path: Path) -> dict:
    """Validate and return {"repo", "token", "issued"}; raise KeyFileError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise KeyFileError(f"cannot read key file: {e}") from e
    except ValueError as e:
        raise KeyFileError(f"key file is not valid JSON: {e}") from e
    if not isinstance(data, dict) or data.get("kind") != KEY_KIND:
        raise KeyFileError(
            f'not a Van Gogh key file (expected "kind": "{KEY_KIND}")')
    repo = str(data.get("repo") or "").strip()
    token = str(data.get("token") or "").strip()
    if not _REPO_RE.fullmatch(repo):
        raise KeyFileError(f"key file has a malformed repo: {repo!r}")
    if not token or any(c.isspace() for c in token):
        raise KeyFileError("key file has an empty or malformed token")
    return {"repo": repo, "token": token, "issued": data.get("issued", "")}


def tokened_url(repo: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def redact(text: str, token: str) -> str:
    """Never let the token surface in output, even inside an error message."""
    return (text or "").replace(token, "***")


def marketplaces_dir() -> Path:
    return Path.home() / ".claude" / "plugins" / "marketplaces"


def known_marketplaces_path() -> Path:
    return Path.home() / ".claude" / "plugins" / "known_marketplaces.json"


def _run_claude(args: list[str], token: str) -> dict:
    """Run a claude CLI subcommand, output captured and redacted."""
    try:
        result = subprocess.run(
            [claude_bin(), *args], capture_output=True, text=True,
            encoding="utf-8", timeout=300, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return {"ok": False, "step": " ".join(args[:2]),
                "error": "the claude CLI was not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "step": " ".join(args[:2]), "error": "timed out"}
    if result.returncode != 0:
        detail = redact((result.stderr or result.stdout or "").strip(), token)
        return {"ok": False, "step": " ".join(args[:2]), "error": detail[-800:]}
    return {"ok": True}


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
    """Rotate the credential on an existing companion install."""
    _store(key)
    clone = marketplaces_dir() / PLUGIN_NAME
    url = tokened_url(key["repo"], key["token"])
    if not (clone / ".git").exists():
        # No clone yet: this machine never bootstrapped — fall back to ingest.
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
    return {"ok": True, "mode": "rekey", "repo": key["repo"],
            "plugin": PLUGIN_NAME}


def ingest(key: dict) -> dict:
    """First-time bootstrap of the companion plugin."""
    if (marketplaces_dir() / PLUGIN_NAME / ".git").exists():
        return rekey(key)
    _store(key)
    url = tokened_url(key["repo"], key["token"])
    step = _run_claude(["plugin", "marketplace", "add", url], key["token"])
    if not step["ok"]:
        step["hint"] = ("nothing was installed; check the token, then re-run "
                        "with the same key file")
        return step
    step = _run_claude(["plugin", "install",
                        f"{PLUGIN_NAME}@{PLUGIN_NAME}"], key["token"])
    if not step["ok"]:
        step["hint"] = ("the marketplace was added but the install failed; "
                        f"retry: claude plugin install {PLUGIN_NAME}@{PLUGIN_NAME}")
        return step
    return {"ok": True, "mode": "ingest", "repo": key["repo"],
            "plugin": PLUGIN_NAME}


def status() -> dict:
    user_state.load_env()
    clone = marketplaces_dir() / PLUGIN_NAME
    return {
        "ok": True,
        "repo": os.environ.get(ENV_REPO, ""),
        "token_present": bool(os.environ.get(ENV_TOKEN)),
        "marketplace_cloned": (clone / ".git").exists(),
        "plugin": PLUGIN_NAME,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "rekey"):
        p = sub.add_parser(name)
        p.add_argument("--file", required=True, help="path to the key file")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    if args.command == "status":
        result = status()
    else:
        try:
            key = parse_key_file(Path(args.file).expanduser())
        except KeyFileError as e:
            result = {"ok": False, "error": str(e)}
        else:
            result = ingest(key) if args.command == "ingest" else rekey(key)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
