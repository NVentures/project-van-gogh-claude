#!/usr/bin/env python3
"""setup_state.py: the install checklist, the one file that says whether an
install is finished.

`<state_dir>/setup/checklist.json` exists while an install is not finished.
Nine boxes, one per step. A box is ticked by re-checking disk, never by
trusting what a step claimed: `verify()` re-derives every box it knows how to
check. When every box is resolved (done, skipped by the client, or not
applicable) the orchestrator writes the completion stamp and deletes this
file; its absence plus the stamp is what "already installed" means, and the
stamp is only a hint that `verify_stamp` re-checks against disk.

Every lane reads and writes the checklist through this module only. Nothing
here imports the vault config at module scope: this file is read by a
session-start hook and by a nudge job on a machine that may have no vault yet.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import host
import user_state  # noqa: E402

VERSION = 1
TOTAL_MINUTES = 15
RESUME_COMMAND = host.cmd("install-van-gogh")

# (key, title, needs, next action in the client's words, minutes it takes)
BOXES = (
    ("plugin", "Plugin installed", "machine", "install the plugin", 1),
    ("python", "Python environment", "machine", "set up Python", 1),
    ("credentials", "App credentials", "machine", "install your key file", 1),
    ("accounts", "Accounts signed in", "human", "sign in to your accounts", 2),
    ("vault", "Vault and settings", "human", "confirm your settings", 1),
    ("jobs", "Background jobs scheduled", "human", "schedule your briefings", 1),
    ("deals", "Deals and people seeded", "human", "tick your real deals", 3),
    ("meetings", "Meetings filed", "machine", "file your meetings", 3),
    ("briefings", "First briefings shown", "machine", "see your first briefings", 3),
)
KEYS = tuple(b[0] for b in BOXES)
NEVER_SKIPPED = ("accounts", "vault")

OPEN = "open"
PENDING = "pending"
DONE = "done"
SKIPPED = "skipped"
NOT_APPLICABLE = "not_applicable"
STATUSES = (OPEN, PENDING, DONE, SKIPPED, NOT_APPLICABLE)
RESOLVED = (DONE, SKIPPED, NOT_APPLICABLE)


class ChecklistError(ValueError):
    """A write that would make the checklist lie."""


# ── paths ────────────────────────────────────────────────────────────────────

def setup_dir() -> Path:
    return user_state.state_dir() / "setup"


def checklist_path() -> Path:
    return setup_dir() / "checklist.json"


def timing_path() -> Path:
    return setup_dir() / "timing.jsonl"


def stamp_path() -> Path:
    return setup_dir() / "complete.json"


def run_id_path() -> Path:
    return setup_dir() / "run-id"


def keyfile_dir() -> Path:
    return user_state.state_dir() / "keyfile"


def staged_dir() -> Path:
    return setup_dir() / "staged"


def logs_dir() -> Path:
    """Logs for anything that runs with no vault. Never under Documents."""
    override = os.environ.get("VAN_GOGH_SETUP_LOGS")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VanGogh" / "logs"
    return Path.home() / "Library" / "Logs" / "VanGogh"


# ── the file ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".checklist-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def new(run_id: str, platform: str = "", plugin_version: str = "") -> dict:
    """A fresh checklist, every box open. Not saved."""
    return {
        "version": VERSION,
        "run_id": run_id,
        "plugin_version": plugin_version,
        "platform": platform or sys.platform,
        "started": _now(),
        "updated": _now(),
        "boxes": [
            {"key": key, "title": title, "needs": needs, "status": OPEN,
             "verified": "", "detail": "", "pending": None, "jobs": []}
            for key, title, needs, _action, _minutes in BOXES
        ],
        "answers": {},
        "degraded": None,
        "nudges": {"stall_started": "", "sent": [], "runs": [],
                   "stop_reminders": False, "quiet": False},
        "adopted_from": None,
    }


def exists() -> bool:
    return checklist_path().exists()


def load() -> dict | None:
    """The checklist, or None when there is none (or it is unreadable)."""
    try:
        data = json.loads(checklist_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("boxes"), list):
        return None
    return data


# What a meeting prep draws on: the vault and the meetings filed into it.
CONTEXT_BOXES = ("vault", "meetings")
# An install left open longer than this is abandoned, not running: it must
# not hold preps back for good.
CONTEXT_HOLD_H = 24


def context_ready(now: datetime | None = None) -> bool:
    """False only while an install is running and has not yet filed the vault
    and the meetings a prep is built from. No checklist means no install in
    progress (finished, or older than the checklist), which is ready."""
    data = load()
    if data is None:
        return True
    try:
        started = datetime.fromisoformat(str(data.get("started")))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ((now or datetime.now(timezone.utc)) - started).total_seconds() > CONTEXT_HOLD_H * 3600:
            return True
    except ValueError:
        return True
    try:
        return all(box(data, k)["status"] in RESOLVED for k in CONTEXT_BOXES)
    except KeyError:
        return True


def save(data: dict, touch: bool = True) -> Path:
    """Write the checklist atomically. `updated` is stamped unless `touch` is
    false: the nudge job writes its own bookkeeping without touching it,
    because `updated` is how it tells the client's activity from its own."""
    _assert_shape(data)
    if touch:
        data["updated"] = _now()
    path = checklist_path()
    _atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path


def remove() -> bool:
    try:
        checklist_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _assert_shape(data: dict) -> None:
    keys = [b.get("key") for b in data.get("boxes", [])]
    if keys != list(KEYS):
        raise ChecklistError(f"checklist boxes are {keys}, expected {list(KEYS)}")
    for b in data["boxes"]:
        if b.get("status") not in STATUSES:
            raise ChecklistError(f"box {b.get('key')} has status {b.get('status')!r}")
        if b["status"] == SKIPPED and b["key"] in NEVER_SKIPPED:
            raise ChecklistError(f"box {b['key']} can never be skipped")


# ── boxes ────────────────────────────────────────────────────────────────────

def box(data: dict, key: str) -> dict:
    for b in data["boxes"]:
        if b["key"] == key:
            return b
    raise KeyError(key)


def set_status(data: dict, key: str, status: str, detail: str = "",
               pending=None) -> dict:
    """Change one box. Refuses a skip on a box that can never be skipped."""
    if status not in STATUSES:
        raise ChecklistError(f"unknown status {status!r}")
    if status == SKIPPED and key in NEVER_SKIPPED:
        raise ChecklistError(f"box {key} can never be skipped")
    b = box(data, key)
    b["status"] = status
    b["detail"] = detail
    b["pending"] = pending
    b["verified"] = _now() if status in RESOLVED else ""
    return b


def open_boxes(data: dict) -> list:
    return [b for b in data["boxes"] if b["status"] not in RESOLVED]


def resolved(data: dict) -> bool:
    return not open_boxes(data)


def first_open(data: dict) -> dict | None:
    for b in data["boxes"]:
        if b["status"] not in RESOLVED:
            return b
    return None


def minutes_left(data: dict) -> int:
    per = {b[0]: b[4] for b in BOXES}
    return sum(per[b["key"]] for b in open_boxes(data))


def elapsed(data: dict) -> str:
    try:
        started = datetime.fromisoformat(data["started"])
    except (KeyError, ValueError):
        return "00:00"
    secs = int((datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    secs = max(secs, 0)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def one_line(data: dict | None = None) -> str:
    """The nudge line every surface prints. Empty when nothing is open.

    Under 120 characters, names how many boxes are open, the ONE next action
    in the client's words, and where to type the resume command.
    """
    if data is None:
        data = load()
    if data is None:
        return ""
    nxt = first_open(data)
    if nxt is None:
        return ""
    left = len(open_boxes(data))
    action = {b[0]: b[3] for b in BOXES}[nxt["key"]]
    if nxt["key"] in ("plugin", "python"):
        # Before the plugin exists there is no /van-gogh command to type;
        # the setup line in a terminal is the only way back in.
        line = (f"Van Gogh setup: {left} of {len(KEYS)} install steps left. Next: paste "
                "your checklist's setup line in Terminal (PowerShell on Windows).")
    else:
        line = (f"Van Gogh setup: {left} of {len(KEYS)} install steps left. "
                f"Next: {action}. In {host.host_word()}, type {RESUME_COMMAND}")
    assert len(line) <= 120, line
    return line


def tick_list(data: dict) -> list:
    mark = {DONE: "[x]", SKIPPED: "[s]", NOT_APPLICABLE: "[-]",
            PENDING: "[~]", OPEN: "[ ]"}
    return [f"{mark[b['status']]} {b['title']}"
            + (f" ({b['detail']})" if b.get("detail") else "")
            for b in data["boxes"]]


# ── verifying from disk ──────────────────────────────────────────────────────

def _claude_on_path() -> str | None:
    """The client's binary when it can be found (host.bin), else None."""
    import host
    try:
        return host.bin()
    except FileNotFoundError:
        return None


def _plugin_listed(claude: str) -> bool | None:
    """True/False from the client's `plugin list --json`; None when the CLI
    could not answer (then the pointer is the fallback)."""
    import subprocess
    import host
    from platform_compat import NO_WINDOW
    try:
        r = subprocess.run(host.plugin_list_argv(claude), capture_output=True,
                           text=True, encoding="utf-8", timeout=30,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return host.plugin_listed(r.stdout or "")


def _check_plugin() -> tuple:
    # The CLI is the authority when it is on PATH: the plugin-root pointer is
    # written by any script run, so it survives an uninstall and would tick
    # the box on a machine where setup.sh just said the plugin is missing.
    claude = _claude_on_path()
    if claude:
        listed = _plugin_listed(claude)
        if listed is False:
            return OPEN, f"plugin not installed ({host.bin_word()} plugin list)"
        if listed is True:
            root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
            version = ""
            try:
                version = json.loads((Path(root) / ".claude-plugin" / "plugin.json")
                                     .read_text(encoding="utf-8")).get("version", "")
            except (OSError, ValueError, TypeError):
                pass
            return DONE, f"van-gogh {version}".strip()
    pointer = user_state.state_dir() / "plugin-root"
    try:
        root = Path(pointer.read_text(encoding="utf-8").strip())
    except OSError:
        root = None
    if root and (root / ".claude-plugin" / "plugin.json").exists():
        try:
            version = json.loads((root / ".claude-plugin" / "plugin.json")
                                 .read_text(encoding="utf-8")).get("version", "")
        except (OSError, ValueError):
            version = ""
        return DONE, f"van-gogh {version}".strip()
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root and (Path(plugin_root) / ".claude-plugin" / "plugin.json").exists():
        return DONE, "van-gogh"
    return OPEN, "plugin not found"


def _venv_python() -> Path:
    venv = user_state.state_dir() / "venv"
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _check_python() -> tuple:
    py = _venv_python()
    if not py.exists():
        return OPEN, "no Python environment"
    import subprocess
    from platform_compat import NO_WINDOW
    try:
        r = subprocess.run(
            [str(py), "-c",
             "import requests, dotenv, google.oauth2, googleapiclient, msal; print('ok')"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return OPEN, "Python environment does not run"
    if r.returncode == 0 and "ok" in (r.stdout or ""):
        return DONE, "ready"
    return OPEN, "Python environment is missing packages"


def _check_credentials() -> tuple:
    sd = user_state.state_dir()
    found = []
    for name, provider in (("google_client_secret.json", "Google"),
                           ("ms_client_secret.json", "Microsoft")):
        path = sd / name
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        inner = doc.get("installed") or doc.get("web") or doc
        if isinstance(inner, dict) and inner.get("client_id"):
            found.append(provider)
    if found:
        return DONE, " and ".join(found)
    return OPEN, "no app credentials installed"


def _env_keys() -> list:
    try:
        lines = user_state.env_file().read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    keys = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if v.strip():
                keys.append(k.strip())
    return keys


def _env_items() -> dict:
    try:
        lines = user_state.env_file().read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if v.strip():
                out[k.strip()] = v.strip()
    return out


_TOKEN_KEYS = (("GOOGLE_REFRESH_TOKEN_", "GOOGLE_REFRESH_EXPIRES_", "Google"),
               ("MS_GRAPH_REFRESH_TOKEN_", "MS_GRAPH_REFRESH_EXPIRES_", "Microsoft"))


def _check_accounts(now: datetime | None = None) -> tuple:
    """A token is present, and none has passed the expiry the provider stated
    at sign-in (`*_REFRESH_EXPIRES_<LABEL>`, ISO 8601 UTC, written beside the
    token; a Google app in Testing mode states 7 days). A token with no stamp
    counts as present. No network."""
    env = _env_items()
    now = now or datetime.now(timezone.utc)
    signed_in = 0
    for token_prefix, expires_prefix, provider in _TOKEN_KEYS:
        for key in env:
            if not key.startswith(token_prefix):
                continue
            signed_in += 1
            label = key[len(token_prefix):]
            raw = env.get(expires_prefix + label, "")
            if not raw:
                continue
            try:
                when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when <= now:
                return OPEN, (f"the {provider} sign-in for {label.title()} expired on "
                              f"{when.strftime('%B')} {when.day}")
    if signed_in:
        return DONE, f"{signed_in} signed in"
    return OPEN, "no account signed in"


def _check_vault() -> tuple:
    try:
        vault = Path(user_state.pointer_file().read_text(encoding="utf-8").strip())
    except OSError:
        return OPEN, "no vault yet"
    config = vault / "van-gogh" / "config.json"
    try:
        cfg = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return OPEN, "settings not written"
    name = (cfg.get("user") or {}).get("full_name", "")
    if not name or name == "Your Name":
        return OPEN, "settings not confirmed"
    if not (vault / host.instructions_filename()).exists():
        return OPEN, "vault not scaffolded"
    return DONE, str(vault)


def scheduler_allowed() -> bool:
    """Whether this run may read or change the computer's real scheduler.

    The jobs live in the REAL per-user scheduler whatever the state folder
    is. A run against an overridden state folder (a test, a harness, a
    rehearsal) is looking at somebody else's jobs: resyncing them would
    point every job on the machine at a scratch vault, and reading them
    would tick this install's schedule step from another install's jobs.
    `VAN_GOGH_SCHEDULER_SYNC=1` forces it for a run that means it. The same
    rule the setup reminder job follows.
    """
    return not os.environ.get("VAN_GOGH_STATE_DIR") or \
        os.environ.get("VAN_GOGH_SCHEDULER_SYNC") == "1"


def _check_jobs() -> tuple:
    """Every enabled registry row loaded. Imported lazily: scheduler_setup
    needs the vault config, which a half-done install may not have."""
    if not scheduler_allowed():
        return None, "the scheduler belongs to another install"
    try:
        import job_registry
        import scheduler_setup
        states = {s["name"]: s["state"]
                  for s in scheduler_setup.job_states(sys.platform)}
        enabled = [j.key for j in job_registry.jobs() if j.is_enabled()]
    except Exception as exc:                                    # noqa: BLE001
        return None, f"could not read the scheduler ({type(exc).__name__})"
    if not enabled:
        return None, "no jobs enabled"
    missing = [k for k in enabled if states.get(k) not in ("loaded", "installed")]
    if missing:
        return OPEN, f"{len(missing)} of {len(enabled)} jobs not loaded"
    return DONE, f"{len(enabled)} jobs loaded"


def _check_briefings() -> tuple:
    try:
        vault = Path(user_state.pointer_file().read_text(encoding="utf-8").strip())
    except OSError:
        return None, "no vault yet"
    root = vault / "van-gogh"
    have = [n for n in ("week.md", "morning-coffee.md", "afternoon-tea.md")
            if (root / n).exists() and (root / n).stat().st_size > 0]
    if "week.md" in have and len(have) >= 2:
        return DONE, ", ".join(have)
    return None, "not rendered yet"


# A checker returns (status, detail). A status of None means "no opinion": the
# box keeps whatever the orchestrator recorded (meetings and deals are judged
# by the step that ran them, with the vault as the witness).
CHECKS = {
    "plugin": _check_plugin,
    "python": _check_python,
    "credentials": _check_credentials,
    "accounts": _check_accounts,
    "vault": _check_vault,
    "jobs": _check_jobs,
    "briefings": _check_briefings,
}


def verify(data: dict, checks: dict | None = None, reopen: bool = True) -> list:
    """Re-check every box from disk. Returns the keys whose status changed.

    A checker that says DONE ticks an open or pending box. A checker that says
    OPEN reopens a done box (a job unloaded, a token revoked) when `reopen` is
    true. A box the client skipped, or that was found not applicable, is left
    alone: the client's answer is a fact about them, not about disk. Checkers
    never raise; a failing checker counts as no opinion.
    """
    checks = CHECKS if checks is None else checks
    changed = []
    for b in data["boxes"]:
        fn = checks.get(b["key"])
        if fn is None or b["status"] in (SKIPPED, NOT_APPLICABLE):
            continue
        try:
            status, detail = fn()
        except Exception:                                       # noqa: BLE001
            continue
        if status is None:
            continue
        if status == DONE and b["status"] != DONE:
            set_status(data, b["key"], DONE, detail)
            changed.append(b["key"])
        elif status == OPEN and b["status"] == DONE and reopen:
            set_status(data, b["key"], OPEN, detail)
            changed.append(b["key"])
        elif status == DONE:
            b["detail"] = detail
            b["verified"] = _now()
    return changed


# ── ledger and stamp ─────────────────────────────────────────────────────────

def log_event(event: str, run_id: str = "", **fields) -> None:
    """Append one row to the timing ledger. Never raises."""
    row = {"ts": _now(), "run_id": run_id, "event": event}
    row.update(fields)
    try:
        timing_path().parent.mkdir(parents=True, exist_ok=True)
        with open(timing_path(), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def read_ledger(path: Path | None = None) -> list:
    path = path or timing_path()
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def write_stamp(record: dict) -> Path:
    record = dict(record)
    record.setdefault("completed", _now())
    _atomic_write(stamp_path(), json.dumps(record, indent=2) + "\n")
    return stamp_path()


def read_stamp() -> dict | None:
    try:
        return json.loads(stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def remove_stamp() -> bool:
    try:
        stamp_path().unlink()
        return True
    except FileNotFoundError:
        return False


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("line")
    sub.add_parser("verify")
    args = parser.parse_args(argv)
    data = load()
    if args.command == "line":
        print(one_line(data))
        return 0
    if data is None:
        print(json.dumps({"exists": False, "stamp": read_stamp() is not None}))
        return 0
    if args.command == "verify":
        changed = verify(data)
        save(data)
        print(json.dumps({"exists": True, "changed": changed,
                          "open": [b["key"] for b in open_boxes(data)],
                          "line": one_line(data)}))
        return 0
    print(json.dumps({"exists": True, "run_id": data.get("run_id"),
                      "lines": tick_list(data),
                      "open": [b["key"] for b in open_boxes(data)],
                      "total": len(KEYS), "line": one_line(data),
                      "elapsed": elapsed(data)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
