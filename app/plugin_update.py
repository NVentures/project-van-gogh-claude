#!/usr/bin/env python3
"""Tell the user when the installed plugin is behind the published one.

Updates themselves have exactly one entry point — `claude plugin marketplace
update van-gogh` — and nothing here changes that. What was missing is the
*signal*: a machine whose marketplace auto-update is off drifts silently, and
the only way to notice was to go read the repo. This module closes that gap
and stops there. It never pulls, never writes inside the plugin cache, and
never mutates an install on its own.

**Why it works from the Claude Desktop app.** The version lives in a file the
marketplace repo serves over plain HTTPS, and that repo is public, so the check
is one unauthenticated GET. It needs no `git`, no `gh`, no GitHub sign-in, and
no `claude` binary on PATH — the four things a Desktop session is least likely
to have. The read path is therefore identical in Desktop, in a terminal, and
under a scheduler, which is the whole point.

Two layers, mirroring claude_update:

* **Cheap.** `cached_notice()` is a single file read with no network at all.
  `config_loader` calls it at import, so every skill run can surface a pending
  notice for free.
* **Refreshing.** `check()` fetches the published version at most once every
  `MAX_AGE_H` hours, stamped in the state dir, with a short timeout so the one
  run a day that pays for it barely notices.

Every path **fails open**: a machine that is offline, behind a proxy, or
pointed at a moved repo must render its briefing exactly as before and say
nothing. Set `VAN_GOGH_DISABLE_UPDATE_CHECK=1` to opt out entirely (a centrally
managed install where the user does not control plugin versions).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from platform_compat import NO_WINDOW                            # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# The published manifest. Reading the repo over raw HTTPS (rather than asking
# git or gh) is what keeps this working in a Desktop session with no tooling.
MANIFEST_URL = (
    "https://raw.githubusercontent.com/NVentures/project-van-gogh-claude"
    "/main/.claude-plugin/plugin.json"
)

# The published release notes, read the same unauthenticated way as the
# manifest. Only the pending-update path fetches this (the user asked "what
# would I get?"); the after-update path reads the LOCAL copy the plugin cache
# already carries, which costs no network at all.
CHANGELOG_URL = (
    "https://raw.githubusercontent.com/NVentures/project-van-gogh-claude"
    "/main/CHANGELOG.md"
)

# How long a fresh update's notes keep appearing in briefings before going
# quiet. A window, not a consumed-once flag, because one briefing run can
# involve several script invocations (morning-coffee shells out to
# week_review); a flag consumed by the inner one would vanish before the
# outer one rendered it.
# Set to "1" by the unattended entry points (digest_send.py, skill_run.py) in
# the child environment: release notes belong in the first chat session after
# an update, never in an emailed digest or a published page.
UNATTENDED_ENV = "VAN_GOGH_UNATTENDED"

# The sanctioned way to actually apply an update. Named here so the notice can
# quote it; `apply_update()` runs exactly this and nothing else.
UPDATE_COMMAND = "claude plugin marketplace update van-gogh"

# One check per machine per day. The plugin ships far less often than that, and
# the check is only ever a nicety — never worth a second of a briefing.
MAX_AGE_H = 24

# Short on purpose: this runs on the way to a briefing the user is waiting for.
# A slow or captive network must cost a beat, not a hang.
FETCH_TIMEOUT_S = 5

# `claude plugin marketplace update` re-clones the marketplace; give it room,
# but never let it outlive the session that asked for it.
APPLY_TIMEOUT_S = 180


def disabled() -> bool:
    return os.environ.get("VAN_GOGH_DISABLE_UPDATE_CHECK", "").strip().lower() in {
        "1", "true", "yes", "on"}


def stamp_path() -> Path:
    import user_state
    return user_state.state_dir() / "plugin-update.json"


def _read_stamp() -> dict:
    try:
        return json.loads(stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_stamp(record: dict) -> None:
    try:
        path = stamp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        pass  # fails open: a stamp we cannot write only costs an extra check


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """`"0.44.0"` -> `(0, 44, 0)`. None when it is not a version at all.

    ZeroVer keeps this to three integers forever (see CLAUDE.md), so there is
    no pre-release or build metadata to reason about. Anything that does not
    parse is treated as unknown rather than as "older", because guessing wrong
    here nags the user about an update that does not exist.
    """
    if not text:
        return None
    parts = str(text).strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _version_from_manifest(text: str) -> str:
    try:
        return str(json.loads(text).get("version", "")).strip()
    except (ValueError, AttributeError):
        return ""


def installed_version() -> str:
    """The running plugin's version, or "" if the manifest cannot be read."""
    manifest = _PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return _version_from_manifest(manifest.read_text(encoding="utf-8"))
    except OSError:
        return ""


def published_version(timeout: float = FETCH_TIMEOUT_S) -> str:
    """The version on the marketplace's main branch, or "" if unreachable."""
    try:
        import requests
        response = requests.get(MANIFEST_URL, timeout=timeout)
        if response.status_code != 200:
            return ""
        return _version_from_manifest(response.text)
    except Exception:
        # Deliberately broad: DNS, TLS, proxies and captive portals all raise
        # their own types, and none of them may reach the caller.
        return ""


def check(max_age_h: int = MAX_AGE_H, force: bool = False) -> dict:
    """Compare installed against published, at most once per `max_age_h`.

    Never raises. The stamp is written for a failed check too, so a machine
    with no route to GitHub retries once a day rather than on every skill run.
    """
    if disabled():
        return {"status": "disabled", "update_available": False}

    if not force:
        stamp = _read_stamp()
        try:
            age_h = (time.time() - float(stamp.get("checked_at", 0))) / 3600
        except (TypeError, ValueError):
            age_h = float("inf")
        if age_h < max_age_h:
            stamp["status"] = "fresh"
            stamp["age_hours"] = round(age_h, 2)
            return stamp

    local = installed_version()
    remote = published_version()
    local_parsed = parse_version(local)
    remote_parsed = parse_version(remote)

    record: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "checked_at": time.time(),
        "installed": local,
        "published": remote,
        "update_command": UPDATE_COMMAND,
    }
    if local_parsed is None or remote_parsed is None:
        # Offline, or a manifest we could not parse. Say nothing to the user.
        record["status"] = "unknown"
        record["update_available"] = False
    else:
        record["status"] = "ok"
        record["update_available"] = remote_parsed > local_parsed
    _write_stamp(record)
    return record


def cached_notice() -> str:
    """One line for the user when an update is pending, else "".

    Pure file read, no network — safe to call on every skill run. Reads only
    what the last `check()` wrote, so a machine that has never checked (or is
    offline, or opted out) simply says nothing.
    """
    if disabled():
        return ""
    stamp = _read_stamp()
    if not stamp.get("update_available"):
        return ""
    installed = stamp.get("installed") or "?"
    published = stamp.get("published") or "?"
    return (f"Van Gogh {published} is available (you have {installed}). "
            f"Run `{UPDATE_COMMAND}` to update.")


# ── Release notes ─────────────────────────────────────────────────────────────

import re

_ENTRY_RE = re.compile(r"^## (\d+\.\d+\.\d+)[^\n]*$", re.MULTILINE)


def parse_changelog(text: str) -> list[tuple[str, str]]:
    """CHANGELOG.md text -> [(version, entry_markdown)], newest first.

    The entry keeps its own `## version (date)` heading so a renderer can show
    several at once and stay legible. Anything before the first heading (the
    file's intro and format rules) is dropped: it is written for maintainers,
    never for the user.
    """
    matches = list(_ENTRY_RE.finditer(text or ""))
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries.append((m.group(1), text[m.start():end].strip()))
    return entries


def local_changelog() -> list[tuple[str, str]]:
    """Release notes shipped inside the installed plugin. No network: the
    marketplace re-clone that delivered the new version delivered these too."""
    try:
        return parse_changelog(
            (_PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    except OSError:
        return []


def published_changelog(timeout: float = FETCH_TIMEOUT_S) -> list[tuple[str, str]]:
    """Release notes on the marketplace's main branch, or [] if unreachable."""
    try:
        import requests
        response = requests.get(CHANGELOG_URL, timeout=timeout)
        if response.status_code != 200:
            return []
        return parse_changelog(response.text)
    except Exception:
        return []  # same deliberate breadth as published_version


def entries_between(entries: list[tuple[str, str]], since: str | None,
                    until: str) -> str:
    """The entries newer than `since` up to and including `until`, as one
    markdown block. An unparseable bound degrades to just `until`'s entry
    rather than to everything or nothing."""
    until_v = parse_version(until)
    if until_v is None:
        return ""
    since_v = parse_version(since)
    picked = []
    for version, body in entries:  # newest first
        v = parse_version(version)
        if v is None or v > until_v:
            continue
        if since_v is not None and v <= since_v:
            break
        picked.append(body)
        if since_v is None:
            break  # no lower bound: only the current version's entry
    return "\n\n".join(picked)


def whats_new_stamp_path() -> Path:
    import user_state
    return user_state.state_dir() / "whats-new.json"


def update_log_path() -> Path:
    """The vault's durable record of applied updates. One JSON line per
    detected version change: when, from, to, and the notes that were shown.
    Vault-shaped state lives in the vault (next to briefing_pages.json), and
    it is what lets /van-gogh:release-notes re-show a past update after the
    briefing footer has gone quiet."""
    from config_loader import logs_dir  # lazy: config_loader imports this module
    return logs_dir() / "update-log.jsonl"


def _log_update(previous: str, installed: str, notes_md: str) -> None:
    """Append the transition to the vault log. Fails open in every direction:
    a machine with no vault yet (mid-install) or an unwritable disk loses the
    log line, never the briefing."""
    try:
        path = update_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "from": previous,
            "to": installed,
            "notes_md": notes_md,
        }, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_update_log() -> list[dict]:
    """Every recorded update, oldest first. [] when the log does not exist,
    and a corrupt line is skipped rather than poisoning the rest."""
    try:
        text = update_log_path().read_text(encoding="utf-8")
    except Exception:
        return []
    entries = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("to"):
            entries.append(record)
    return entries


def whats_new() -> str:
    """Release notes the user has not seen yet, else "".

    Pure local reads, safe on every skill run. The first run after the plugin
    cache refreshes sees the installed version differ from the stamped one,
    records the transition, and returns the entries in between; the same text
    keeps returning until a briefing actually renders it in a chat session and
    calls mark_notes_shown(), then goes quiet for good. Unattended runs (the
    digest scheduler; the env var is set by digest_send.py and skill_run.py)
    always get "": release notes are a chat moment, not digest or page
    content, and an unattended run must never consume them before the user
    has had a session to see them in. A machine's first run ever stamps
    silently: on a fresh install everything is new, and the install skill
    already does the introductions.
    """
    if disabled():
        return ""
    unattended = bool(os.environ.get(UNATTENDED_ENV))
    installed = installed_version()
    if parse_version(installed) is None:
        return ""
    path = whats_new_stamp_path()
    try:
        stamp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = None

    now = time.time()
    if stamp is None:
        record = {"version": installed, "first_seen": now, "previous": None}
    elif stamp.get("version") != installed:
        record = {"version": installed, "first_seen": now,
                  "previous": stamp.get("version")}
    else:
        record = stamp

    if record is not stamp:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError:
            pass  # fails open: an unwritable stamp repeats the notes, never breaks
        if record.get("previous") is not None:
            # The one moment the update is detected: give the vault its
            # durable line, with the exact notes the briefing will show.
            _log_update(record["previous"], installed, entries_between(
                local_changelog(), record["previous"], installed))

    if record.get("previous") is None:
        return ""  # fresh install: nothing is "new", it is all new
    # An unattended run still stamps the transition above (the vault's update
    # log must not wait for a human), but never carries the notes and never
    # counts as the showing.
    if unattended or record.get("shown"):
        return ""
    return entries_between(local_changelog(), record.get("previous"), installed)


def mark_notes_shown() -> None:
    """Record that the pending release notes reached a chat session.

    Called by a briefing script when its own output carries a non-empty
    whats_new — the render that follows is the one showing. From then on
    whats_new() is quiet for this version; /van-gogh:release-notes re-shows
    from the vault's update log on request. Fails open: an unwritable stamp
    repeats the notes next session rather than breaking the briefing.
    """
    path = whats_new_stamp_path()
    try:
        stamp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(stamp, dict) or stamp.get("shown"):
        return
    stamp["shown"] = True
    stamp["shown_at"] = time.time()
    try:
        path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    except OSError:
        pass


def release_notes_payload() -> dict:
    """Everything /van-gogh:release-notes needs, in one read-only pass.

    `last_update` is the newest vault-log entry (the scoped from -> to set the
    briefing showed); "" fields degrade gracefully. `current_entry_md` is the
    running version's own changelog entry, the fallback for a machine that has
    never recorded an update (fresh install, or the log predates this feature).
    """
    log = read_update_log()
    installed = installed_version()
    entries = local_changelog()
    current_entry_md = ""
    for version, body in entries:
        if version == installed:
            current_entry_md = body
            break
    return {
        "installed": installed,
        "last_update": log[-1] if log else None,
        "update_count": len(log),
        "current_entry_md": current_entry_md,
        "changelog_versions": [v for v, _ in entries],
    }


def check_quietly(max_age_h: int = MAX_AGE_H) -> None:
    """Refresh the stamp on a real install; do nothing anywhere else.

    Called at `config_loader` import, next to the other self-heal hooks. Same
    gate they use: no-ops unless the running interpreter *is* the managed venv,
    so dev checkouts and pytest never reach the network or touch a real
    install's state.
    """
    try:
        import user_state
        if not user_state._running_in_managed_venv():
            return
        check(max_age_h=max_age_h)
    except Exception:
        pass  # fails open: a check must never be why a briefing did not render


def apply_update(claude: str | None = None) -> dict:
    """Run the one sanctioned update command. Never raises.

    This is the only thing in the codebase that applies a plugin update, and it
    shells out to the marketplace CLI rather than touching the plugin cache —
    a `git pull` in there is what a marketplace re-clone would silently undo.

    Needs the `claude` binary on PATH, which a Desktop session may not have.
    That is why it is a separate, explicit call: the *check* never depends on
    it, so a Desktop user still gets the notice and can update from the
    /plugin menu even when this path is unavailable.
    """
    from platform_compat import claude_bin
    record: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "version_before": installed_version(),
        "command": UPDATE_COMMAND,
    }
    try:
        binary = claude or claude_bin()
    except FileNotFoundError as exc:
        record["status"] = "no_cli"
        record["error"] = str(exc)
        return record

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            [binary, "plugin", "marketplace", "update", "van-gogh"],
            capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=APPLY_TIMEOUT_S, creationflags=NO_WINDOW,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return record

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    record["status"] = "ok" if result.returncode == 0 else "error"
    record["returncode"] = result.returncode
    record["output"] = output[-1000:]
    # The running process still holds the old cache on disk; re-check so the
    # stamp reflects reality rather than the version this process booted with.
    record["check"] = check(force=True)
    return record


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="check now, ignoring the once-a-day throttle")
    parser.add_argument("--apply", action="store_true",
                        help=f"run `{UPDATE_COMMAND}` after checking")
    parser.add_argument("--release-notes", action="store_true",
                        help="print the release-notes payload (no network)")
    args = parser.parse_args(argv)

    if args.release_notes:
        print(json.dumps(release_notes_payload(), indent=2,
                         ensure_ascii=False, default=str))
        return 0
    if args.apply:
        print(json.dumps(apply_update(), indent=2, default=str))
        return 0
    record = check(force=args.force)
    if args.force and record.get("update_available"):
        # The user asked right now, so one extra GET for "here is what you
        # would get" is worth it. Never fetched on the quiet daily path.
        record["pending_notes_md"] = entries_between(
            published_changelog(), record.get("installed"),
            record.get("published") or "")
    print(json.dumps(record, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
