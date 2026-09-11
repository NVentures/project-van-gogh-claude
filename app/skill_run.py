#!/usr/bin/env python3
"""Run a Van Gogh skill headlessly, with retries.

Two callers:

* the scheduler, which used to build a bare `bash -lc claude -p ...` command
  line with no Python anywhere in the chain, so a failed overnight run had no
  way to retry and no way to leave a trace;
* the Workbench job runner, which needs the same spawn plus the skill's stdout
  back (a Brain query is an answer, not a side effect).

This is the agentic path: the child may use tools and write files, which is the
whole point of asking a skill to do something. The non-agentic classification
path is `claude_cli.run_claude` and the two must not be confused.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_update                                            # noqa: E402
import config_loader                                            # noqa: E402
import run_ledger                                               # noqa: E402
from platform_compat import NO_WINDOW, claude_bin               # noqa: E402
from self_anneal import with_retries                            # noqa: E402

SKILL_TIMEOUT_S = 30 * 60
SKILL_ATTEMPTS = 2
SKILL_BACKOFF_S = (30, 120)


def _spawn(skill: str, claude: str | None, args: str, timeout_s: int) -> str:
    prompt = f"/van-gogh:{skill}"
    if args:
        prompt = f"{prompt} {args}"
    # Strip ANTHROPIC_API_KEY so the CLI routes through the subscription.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Unattended marker: release notes (meta.whats_new) are a chat moment and
    # must never render in a scheduled run (see plugin_update.whats_new).
    env["VAN_GOGH_UNATTENDED"] = "1"
    # bypassPermissions: nobody is present to answer a prompt, and a denied
    # tool call fails the run silently rather than loudly.
    result = subprocess.run(
        [claude or claude_bin(), "-p", prompt,
         "--permission-mode", "bypassPermissions"],
        cwd=str(config_loader.repo_root()),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_s,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        # A CLI too old for the current model fails every run on this machine
        # until it is updated. Heal it here so the retry lands on a current
        # CLI instead of repeating the same 400.
        claude_update.heal_if_stale(
            (result.stdout or "") + (result.stderr or ""), claude)
        raise RuntimeError(
            f"claude -p {prompt} exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[-2000:]}"
        )
    return result.stdout or ""


def run_skill(skill: str, claude: str | None = None, args: str = "",
              capture_stdout: bool = False,
              timeout_s: int = SKILL_TIMEOUT_S) -> str | None:
    """Run one skill headlessly. Returns its stdout when asked, else None.

    `claude` is the absolute CLI path the scheduler resolved at sync time,
    because launchd and Task Scheduler hand a job a PATH too sparse to rely on
    at fire time. Manual runs fall back to a PATH lookup.

    Nothing is watching a scheduled run, so the CLI is brought up to date first
    (throttled to once a day) rather than left to drift into the "does not
    support this model" 400 that would break every skill on the machine.
    """
    claude_update.ensure_current(claude)
    out = with_retries(
        lambda: _spawn(skill, claude, args, timeout_s),
        attempts=SKILL_ATTEMPTS,
        backoff_s=SKILL_BACKOFF_S,
        component=f"scheduled.{skill}",
    )
    return out if capture_stdout else None


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Van Gogh skill headlessly.")
    parser.add_argument("skill", help="Skill name without the /van-gogh: prefix.")
    parser.add_argument("--claude", default=None, help="Absolute path to the claude CLI.")
    parser.add_argument("--args", default="", help="Arguments appended to the prompt.")
    parser.add_argument("--print", action="store_true", help="Echo the skill's stdout.")
    parsed = parser.parse_args(argv)

    started = run_ledger.now_stamp()
    rc, detail, out = 0, "", None
    try:
        out = run_skill(parsed.skill, parsed.claude, parsed.args,
                        capture_stdout=parsed.print)
    except Exception as exc:                                    # noqa: BLE001
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        rc, detail = 1, f"{type(exc).__name__}: {exc}"[:500]
    finally:
        # Every exit path leaves a row: a scheduled run that fails silently is
        # indistinguishable from one that never fired, and the audit has to
        # tell those apart.
        run_ledger.record_run(f"scheduled.{parsed.skill}", started,
                              run_ledger.now_stamp(), rc, detail)
    if rc:
        return rc
    if parsed.print and out:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
