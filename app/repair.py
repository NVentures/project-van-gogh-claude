#!/usr/bin/env python3
"""Call the mechanic when a re-run cannot fix it.

The watcher can re-run a job, and hold one whose failure class it recognises.
What it could never do was look. A job failing the same way every morning with
a class of "unknown" got three kicks, a give-up message, and then repeated
that forever, because nothing in the product had ever read a log file.

This is the escalation. It spawns a headless Claude session as the mechanic
agent, hands it the job and the error, and lets it diagnose and repair inside
the boundaries the agent file sets out. One per stuck job per day.

Four rules carry the design, and each is a boundary rather than an instruction:

**The agent is only reached at the end.** By the time this runs, the watcher
has re-run the job to its cap and the class is one no hold applies to. A
diagnosis on the first failure would fire on every transient blip and cost a
model call to conclude "try again", which the watcher already does for free.

**Once per job per day, and cheap to prove.** The stamp is written before the
spawn, not after: a crash mid-session must cost one attempt, not an unbounded
loop of them. A repair that runs every thirty minutes is a worse failure than
the one it was called about.

**It fails open, like everything else here.** Every path swallows its own
errors. The worst thing this may do is nothing, because the alternative is a
watcher that can wedge a briefing.

**The plugin's own source is off limits, structurally.** `--add-dir` is not
passed for the plugin root, so the vault and the state directory are the only
places the session can write, whatever the model decides it would like to do.
The agent file says the same thing in words; the flag is what makes it true.

CLI:
    repair.py --job digest-morning-coffee --detail "..."
    repair.py --job digest-week --dry-run     print the command, spawn nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import run_ledger                                               # noqa: E402
from platform_compat import NO_WINDOW, claude_bin               # noqa: E402

AGENT = "van-gogh-mechanic"
JOB_NAME = "repair.mechanic"
STATE_NAME = "repair-state.json"

# A diagnosis is reading and thinking, not rendering: it has no calendar to
# fetch and no mail to send. Fifteen minutes is long enough to read every
# ledger on the machine twice and short enough that a wedged session cannot
# hold a launchd slot until the next one.
TIMEOUT_S = 15 * 60

# How many separate jobs may be investigated in one day. A morning where six
# things broke at once is one cause with six symptoms, and asking the model
# six times is six answers to the same question.
MAX_PER_DAY = 2


def state_path() -> Path:
    import user_state
    return user_state.state_dir() / STATE_NAME


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, default=str),
                        encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass  # fails open: a stamp we cannot save costs one extra look


def repairs_dir() -> Path:
    """Where the mechanic files its notes. Machinery, not notes the user keeps."""
    return config_loader.logs_dir() / "repairs"


def already_today(job: str, now: datetime, state: dict | None = None) -> bool:
    """Has this job been investigated today, and is the daily budget spent?

    Both questions in one call because both answers mean the same thing to the
    caller: do not spawn. Keeping them separate meant a caller could check one
    and forget the other, and the one it forgot was the budget.
    """
    state = state if state is not None else _read_state()
    today = now.date().isoformat()
    day = state.get("day")
    if day != today:
        return False
    if job in (state.get("jobs") or []):
        return True
    return len(state.get("jobs") or []) >= MAX_PER_DAY


def _stamp(job: str, now: datetime, state: dict) -> None:
    """Record the attempt BEFORE spawning.

    Written first on purpose. If the session crashes, hangs, or the machine
    sleeps through it, the attempt is still spent: a crash that left no stamp
    would be retried at the next tick and every tick after it, which is the
    unbounded loop this exists to prevent.
    """
    today = now.date().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["jobs"] = []
    state.setdefault("jobs", [])
    if job not in state["jobs"]:
        state["jobs"].append(job)
    state["last"] = now.isoformat(timespec="seconds")
    _write_state(state)


def prompt_for(job: str, detail: str, error_class: str = "") -> str:
    """What the mechanic is told. Facts only, and no instructions it already has.

    Deliberately short. Everything about how to work, what may be changed and
    what may not is in the agent file, which the CLI loads as the session's
    own definition. Repeating it here would create a second copy that drifts,
    and the copy in the prompt is the one that would win.
    """
    lines = [
        f"The scheduled job `{job}` is stuck. The watcher re-ran it to its "
        "cap and it failed the same way each time, so a re-run is not the "
        "answer.",
        "",
        f"Failure class as the watcher graded it: {error_class or 'unknown'}.",
        "",
        "What it recorded, verbatim:",
        "```",
        (detail or "(nothing was recorded)")[:2000],
        "```",
        "",
        "Diagnose it, repair it if the cause is inside what you are allowed "
        "to change, and file your note. If you cannot determine the cause, "
        "say what you ruled out.",
    ]
    return "\n".join(lines)


def _command(prompt: str, claude: str | None = None) -> list:
    """The argv for one mechanic session.

    Every boundary here is a flag rather than a sentence in the prompt, for
    the reason `claude_cli` gives at length: a prompt instruction is necessary
    and never sufficient.

    - `--agent van-gogh-mechanic` makes the agent file the session's own
      definition, so its rules arrive as the system prompt rather than as
      advice inside a user turn the model may weigh against something else.
    - `--plugin-dir` is what makes that name resolvable at all, and it is not
      optional. Agents under a plugin's `agents/` folder are discovered by
      Claude Code only when the plugin is LOADED, and a session spawned from a
      script loads nothing: without this flag the CLI exits 1 with "--agent
      'van-gogh-mechanic' not found" and lists only the built-ins. Every
      guarantee in the agent file is worth nothing if the file is never read,
      so `test_the_agent_name_actually_resolves` asserts the flag is present
      AND names the directory that holds the file.
    - The working directory is the VAULT, and `--add-dir` adds only the state
      directory. The plugin root is not passed, so the session cannot write to
      the plugin's own source. That is rule 1 of the agent file, enforced.
    - `--permission-mode bypassPermissions` because nobody is at a terminal to
      answer a prompt, and a denied call fails the run silently.
    - `--disable-slash-commands` because a diagnosis that invokes a briefing
      skill would re-enter the machinery it was called to inspect.
    """
    return [
        claude or claude_bin(), "-p", prompt,
        "--agent", AGENT,
        # The plugin's own root, so the session can find agents/<AGENT>.md.
        # Loading the plugin does NOT make its source writable: what a session
        # may write is decided by the working directory and --add-dir, and
        # neither of those is the plugin root.
        "--plugin-dir", str(_plugin_root()),
        "--add-dir", str(_state_dir()),
        "--permission-mode", "bypassPermissions",
        "--disable-slash-commands",
    ]


def _state_dir() -> Path:
    import user_state
    return user_state.state_dir()


def _plugin_root() -> Path:
    """Where this file lives, which is the plugin root.

    Derived from `__file__` rather than read from the `plugin-root` pointer:
    the pointer is per-user state that a broken install may not have written,
    and a missing pointer is one of the faults the mechanic is called about.
    The module doing the spawning always knows where it is.
    """
    return Path(__file__).resolve().parent.parent


def run(job: str, detail: str = "", error_class: str = "",
        now: datetime | None = None, claude: str | None = None,
        dry_run: bool = False) -> dict:
    """Investigate one stuck job. Never raises.

    Returns `{"spawned": bool, "why": str}`: the caller writes that into its
    own tick record, so a decision not to look is as visible as a look.
    """
    now = now or datetime.now()
    result = {"spawned": False, "why": ""}
    try:
        # Reading the switch needs the config, which needs the vault pointer.
        # A machine whose install never finished has neither, and that is one
        # of the faults most worth being called about, so a config that cannot
        # be read must not read as "switched off": the default is on, and the
        # only thing that turns it off is a config that says so out loud.
        try:
            if not config_loader.repair_agent_enabled():
                result["why"] = "the repair agent is switched off"
                return result
        except Exception:                                       # noqa: BLE001
            pass

        state = _read_state()
        if already_today(job, now, state):
            result["why"] = "already looked at this today"
            return result

        prompt = prompt_for(job, detail, error_class)
        cmd = _command(prompt, claude)
        if dry_run:
            result["why"] = "dry run"
            result["command"] = cmd
            return result

        # The stamp goes first, before anything that can throw. An earlier
        # version created the notes folder first, which reads harmlessly and
        # is not: resolving that path needs the vault pointer, so on a machine
        # whose install never finished it raised, the stamp was never written,
        # and every tick from then on re-entered this path. The budget is only
        # a budget if it is spent before the work, not after it.
        _stamp(job, now, state)
        try:
            repairs_dir().mkdir(parents=True, exist_ok=True)
        except Exception:                                       # noqa: BLE001
            # No vault to file a note in. The session can still diagnose, and
            # "the vault pointer is missing" is exactly the kind of finding
            # worth having, so this does not abort the look.
            pass

        started = run_ledger.now_stamp()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env["VAN_GOGH_UNATTENDED"] = "1"
        # Where the session runs, and therefore what it may write to. The
        # vault when there is one; the state directory when the pointer is
        # missing, which is one of the faults worth being called about, so it
        # must not be the fault that stops the call.
        try:
            cwd = str(config_loader.vault())
        except Exception:                                       # noqa: BLE001
            cwd = str(_state_dir())
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            creationflags=NO_WINDOW,
        )
        rc = proc.returncode
        tail = ((proc.stderr or proc.stdout or "").strip())[-500:]
        run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), rc,
                              f"{job}: {tail}" if rc else f"{job}: looked at it")
        result["spawned"] = rc == 0
        result["why"] = "looked at it" if rc == 0 else f"the look itself failed: {tail[:200]}"
        return result
    except subprocess.TimeoutExpired:
        run_ledger.record_run(JOB_NAME, run_ledger.now_stamp(),
                              run_ledger.now_stamp(), 1,
                              f"{job}: timed out after {TIMEOUT_S}s")
        result["why"] = "the look timed out"
        return result
    except Exception as exc:                                    # noqa: BLE001
        result["why"] = f"could not look: {type(exc).__name__}"
        return result


def latest_notes(limit: int = 3) -> list:
    """The most recent repair notes, newest first. For the briefing footer."""
    try:
        files = sorted(repairs_dir().glob("*.md"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in files[:limit]]
    except OSError:
        return []


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask the mechanic to look at a stuck job.")
    parser.add_argument("--job", required=True, help="the job that is stuck")
    parser.add_argument("--detail", default="", help="what it recorded")
    parser.add_argument("--class", dest="error_class", default="",
                        help="the failure class the watcher graded")
    parser.add_argument("--claude", default=None,
                        help="absolute path to the claude CLI")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the command, spawn nothing")
    args = parser.parse_args(argv)

    out = run(args.job, args.detail, args.error_class,
              claude=args.claude, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
