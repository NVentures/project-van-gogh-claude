#!/usr/bin/env python3
"""Keep the `claude` CLI current, so an unattended run never dies on a stale build.

The Claude API rejects a new model id on an old CLI:

    API Error: 400 Claude Code 2.1.139 does not support this model; version
    2.1.251 or newer is required. Run 'claude update' ...

Every Van Gogh briefing goes through `claude -p`, so one stale CLI silently
breaks every scheduled job on that machine until a human notices. The CLI's own
auto-updater does not always run (npm-global installs without write access to
their prefix, `DISABLE_AUTOUPDATER`, a machine that is only ever driven by a
scheduler), which is exactly the population that has nobody watching.

Two layers, both here:

* **Proactive.** The unattended entry points (`digest_send`, `skill_run`) call
  `ensure_current()` before spawning the CLI. It runs `claude update` at most
  once every `MAX_AGE_H` hours, stamped in the state dir, so the cost is one
  ~2s network check per day and the CLI never drifts a version behind.
* **Reactive.** `is_stale_cli_error()` recognizes the 400 above in a child's
  output; `heal_if_stale()` forces the update immediately so the caller's next
  retry runs on a current CLI, turning a hard failure into a slow success.

Every path here **fails open**: an update that cannot run must never be the
reason a briefing does not. Set `VAN_GOGH_DISABLE_CLI_UPDATE=1` to opt out
entirely (a centrally managed CLI the user does not control).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from platform_compat import NO_WINDOW, claude_bin                # noqa: E402

# How long a successful check stays good. One check per machine per day is
# enough to keep pace with the CLI's release cadence without turning every
# scheduled render into a network round-trip.
MAX_AGE_H = 24

# `claude update` downloads and swaps a binary; a slow link needs room, but it
# must never outlive the job that called it.
UPDATE_TIMEOUT_S = 300

# The 400 the API returns for a CLI too old for the requested model, plus the
# CLI's own remediation line. Either alone is enough: the wording of the error
# has changed before, and "run claude update" is the invariant part of it.
_STALE_RE = re.compile(
    r"does not support this model"
    r"|version\s+[\d.]+\s+or\s+newer\s+is\s+required"
    r"|run\s+['\"`]?claude\s+update",
    re.IGNORECASE,
)

# One forced update per process. A retry loop that sees the same stale error on
# every attempt must not run `claude update` once per attempt.
_healed_this_process = False


def is_stale_cli_error(text: str | None) -> bool:
    """True when a child's output is the "your CLI is too old" failure."""
    return bool(text) and bool(_STALE_RE.search(text))


def disabled() -> bool:
    return os.environ.get("VAN_GOGH_DISABLE_CLI_UPDATE", "").strip().lower() in {
        "1", "true", "yes", "on"}


def stamp_path() -> Path:
    import user_state
    return user_state.state_dir() / "claude-cli-update.json"


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


def cli_version(claude: str | None = None) -> str:
    """`claude --version`'s version string, or "" if it cannot be read."""
    try:
        result = subprocess.run(
            [claude or claude_bin(), "--version"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=60, creationflags=NO_WINDOW,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", result.stdout or "")
    return match.group(0) if match else ""


def update_now(claude: str | None = None) -> dict:
    """Run `claude update` once. Never raises; returns a status record.

    ANTHROPIC_API_KEY is dropped for the same reason every other spawn drops
    it: the CLI must route through the user's subscription.
    """
    before = cli_version(claude)
    record: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "version_before": before,
        "version": before,
    }
    if disabled():
        record["status"] = "disabled"
        return record

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            [claude or claude_bin(), "update"],
            capture_output=True, text=True, encoding="utf-8",
            env=env, timeout=UPDATE_TIMEOUT_S, creationflags=NO_WINDOW,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return record

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    record["status"] = "ok" if result.returncode == 0 else "error"
    record["returncode"] = result.returncode
    record["output"] = output[-1000:]
    record["version"] = cli_version(claude) or before
    record["updated"] = bool(record["version"] and record["version"] != before)
    return record


def ensure_current(claude: str | None = None, max_age_h: int = MAX_AGE_H,
                   force: bool = False) -> dict:
    """Update the CLI if it has not been checked in `max_age_h` hours.

    Never raises. The stamp is written for a failed check too, so a machine
    that cannot reach the update server retries once a day rather than on
    every single scheduled job.
    """
    if disabled():
        return {"status": "disabled"}
    if not force:
        stamp = _read_stamp()
        try:
            age_h = (time.time() - float(stamp.get("checked_at", 0))) / 3600
        except (TypeError, ValueError):
            age_h = float("inf")
        if age_h < max_age_h:
            return {"status": "fresh", "age_hours": round(age_h, 2),
                    "version": stamp.get("version", "")}

    record = update_now(claude)
    record["checked_at"] = time.time()
    _write_stamp(record)
    return record


def heal_if_stale(text: str | None, claude: str | None = None) -> bool:
    """On a "CLI too old" error, force an update once per process.

    Returns True when an update ran and the caller should retry. False means
    the failure was something else, or this process already tried to heal it.
    """
    global _healed_this_process
    if _healed_this_process or disabled() or not is_stale_cli_error(text):
        return False
    _healed_this_process = True
    record = ensure_current(claude, force=True)
    record["checked_at"] = record.get("checked_at", time.time())
    return record.get("status") == "ok"


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="check now, ignoring the once-a-day throttle")
    args = parser.parse_args(argv)
    print(json.dumps(ensure_current(force=args.force), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
