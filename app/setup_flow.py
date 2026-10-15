#!/usr/bin/env python3
"""setup_flow.py: the deterministic install orchestrator.

The install skill is a loop: call `next`, branch on `kind`, call `next` again.
Everything that decides what happens lives here, with the checklist
(`setup_state`) as the only state, so a crash, a closed laptop or a new
session resumes at the first open box and asks nothing twice. Every box is
ticked by re-checking disk, never by trusting what a step claimed.

Subcommands (one JSON object on stdout each):

  prepare --run-id ID     the setup script calls this after the venv exists:
                          plugin, python and credentials boxes, key file moved
                          into the private folder, nudge job installed
  next [--answer-file P]  run machine steps until a human or a join is needed;
                          prints prompt | wait | dispatch | error | done
  status                  the checklist as a tick list
  report [--ledger PATH]  machine, human and model time; exits 0 only when
                          complete, under 15:00 and not degraded
  finish                  re-verify, write the stamp, delete the checklist,
                          remove the nudge job, print the closing text
  reset                   delete the checklist and the stamp (keeps the ledger)
  verify-stamp [--live]   the under-5-seconds check behind "already installed"
  adopt                   build a checklist from disk for an older install
  nudge, nudge-job        thin wrappers over setup_nudge (another lane's module)
  _job NAME -- cmd...     hidden wrapper for detached work

Secrets never reach a command line: a notetaker key arrives inside the
answer object, which is read from a file in the private setup folder and
deleted in the same breath (`--answer-file`), or from stdin (`--answer-stdin`).
The file is the form the skill uses: a here-document on a command is refused
by the permission check on a machine whose owner has not allowed every shell
command, and that is every client. The key is validated by one real call, and is written to `.env`
only after it validates. Nothing here prints a token or a key.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schedule_plan  # noqa: E402
import host                                                     # noqa: E402
import setup_state as ss  # noqa: E402
import user_state  # noqa: E402
from platform_compat import NO_WINDOW  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = APP_DIR.parent

RESUME = ss.RESUME_COMMAND
TARGET_SECONDS = ss.TOTAL_MINUTES * 60
AUTH_TIMEOUT_S = 320
SCRIPT_TIMEOUT_S = 600
WAIT_SCAN_SLICE_S = 20
POLL_SECONDS = 15
BELL_AFTER_S = 30
KEEPAWAKE_MAX_S = 45 * 60
KEEPAWAKE_POLL_S = 5
MAX_DISPATCHES = 2
DEFAULT_VAULT_NAME = "van-gogh-vault"
PROMPT_IDS = ("notetaker", "confirm", "schedule", "priorities", "deals")
# (label, what picking it changes in the briefings)
GENERIC_PRIORITIES = (
    ("Close the deals already in motion",
     "Briefings rank replies on live deals first."),
    ("Bring in new business",
     "Briefings put new introductions and first replies near the top."),
    ("Hire or build the team",
     "Briefings flag candidates and team threads ahead of routine mail."),
)

# Who a client asks once setup is over, and how to reach him.
VINCENT_COMMAND = host.cmd("vincent")
VINCENT_LINE = (f"Your chief of staff is Vincent. In any {host.host_word()} session, type "
                f"{VINCENT_COMMAND} and ask for what you need.")

# The later command for every box a client may leave undone.
LATER = {"deals": host.cmd("warm-start"),
         "jobs": VINCENT_COMMAND,
         "meetings": host.cmd("meeting-ingest")}

# Where the resume command is typed. The setup line is pasted into Terminal
# and the checklist drills "never into Claude" for it, so a bare "type X"
# lands in the wrong window: every line that names the command says where.
IN_CLAUDE = f"in {host.host_word()}, type {RESUME}"
# The skill that takes a notetaker key after the install (it asks for it).
SETTINGS_COMMAND = host.cmd("update-settings")
_SETUP_LINE = ("Paste your checklist's setup line in Terminal (PowerShell on Windows); "
               f"it reopens setup in {host.host_word()}.")

# Every error line the orchestrator can print, one template each. The cold
# read renders this table, and a test holds each line to 120 characters.
ERROR_LINES = {
    "no_key_file": f"Your key file is not in Downloads. Save it there, then {IN_CLAUDE}",
    "no_credentials": ("No key file yet. Ask the Van Gogh team for it, save it in Downloads, "
                       f"then {IN_CLAUDE}"),
    # No /van-gogh command can run before the plugin and Python exist, so
    # these two name the setup line and not the command.
    "no_python": f"No Python yet. {_SETUP_LINE}",
    "no_plugin": f"No plugin yet. {_SETUP_LINE}",
    "no_account": f"No account is signed in yet. To sign in, {IN_CLAUDE}",
    "smoke": ("Van Gogh could not read your {name} account just now. To sign in again, "
              + IN_CLAUDE),
    "signin_later": f"Sign-in is not finished. To come back to it, {IN_CLAUDE}",
    "settings": f"Settings did not save. To retry, {IN_CLAUDE}",
    "jobs": f"The background jobs did not install. To retry, {IN_CLAUDE}",
    "scan": f"The mail scan stopped early. To run it again, {IN_CLAUDE}",
    "accounts_config": ("Your accounts could not be written to the settings. "
                        f"To sign in again, {IN_CLAUDE}"),
    "propose": f"Deals could not be read from the scan. To retry, {IN_CLAUDE}",
    "apply": f"The ticked deals could not be saved. To retry, {IN_CLAUDE}",
    "outstanding": "{n} ticked {what} not saved. To retry, " + IN_CLAUDE,
    "meetings": f"The meeting notes were not filed. To try again, {IN_CLAUDE}",
    "briefings": f"The first briefings were not written. To retry, {IN_CLAUDE}",
    "timeout": f"A step took too long and was stopped. To continue, {IN_CLAUDE}",
}

# One line per sign-in failure class (auth_bootstrap's `error_class`).
SIGNIN_LINES = {
    "admin_consent": "Your IT admin must approve Van Gogh for {name} first. Once they have, " + IN_CLAUDE,
    "access_denied": "The {name} sign-in was cancelled. To try again, " + IN_CLAUDE,
    "unverified_app": "{name} blocked this account. Ask the Van Gogh team to add you, then " + IN_CLAUDE,
    "missing_scope": ("The {name} sign-in left a permission unticked. Tick every box next time: "
                      + IN_CLAUDE),
    "timeout": "The {name} sign-in got no answer in time. To try again, " + IN_CLAUDE,
    "no_credential": ("Save the key file we sent you into your Downloads folder, then "
                      + IN_CLAUDE),
    "no_refresh_token": "{name} did not finish the sign-in. To sign in again, " + IN_CLAUDE,
    "other": "{name} refused the sign-in. To try again, " + IN_CLAUDE,
}
PROVIDER_NAMES = {"google": "Google", "microsoft": "Microsoft"}

# Why a question is being asked again. The reason leads the question.
REASONS = {
    "bad_choice": "That was not one of the choices.",
    "empty_key": "The {name} key was empty. Paste it as your reply.",
    "bad_key": ("That {name} key did not work. Paste it again, or pick \"None, or another "
                "notetaker\"."),
    "updated": "Updated.",
    "say_change": "Say what to change, or choose Yes, continue.",
    "missing_one": "Still missing: {what}. What is it?",
    "missing_many": "Still missing: {what}. What are they?",
    "ask_details": "First, tell me {what}.",
    "ask_city": "For your timezone, the city you live in is enough.",
    "timezone": ("I could not place {answer}. Type the largest city near you that shares your "
                 "clock, for example Chicago, London or Singapore."),
    "folder_create": ("The folder {path} could not be created. Choose Change something and give "
                      "another folder."),
    "folder_write": ("The folder {path} cannot be written to. Choose Change something and give "
                     "another folder."),
    "paste_address": "That was not a web address. Paste the full address of the page you ended on.",
}
NOTETAKER_NONE = "None, or another notetaker"

# Injectable clock: tests set setup_flow.NOW to a callable.
NOW = None


class StepError(Exception):
    """A step cannot continue; `line` is the one line the client reads."""

    def __init__(self, box: str, line: str):
        super().__init__(line)
        self.box = box
        self.line = line


class Degraded(Exception):
    """A usage cap interrupted the step. `reset` is the clock time the cap's
    own message named ("3:40 PM"), or "" when it named none."""

    def __init__(self, reason: str, reset: str = ""):
        super().__init__(reason)
        self.reset = reset


# ── clock, ledger, output ────────────────────────────────────────────────────

def _now() -> datetime:
    if NOW is not None:
        t = NOW()
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    return datetime.now(timezone.utc).astimezone()


def _iso(t: datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def _log(data: dict | None, event: str, **fields) -> None:
    fields.setdefault("ts", _iso())
    ss.log_event(event, run_id=(data or {}).get("run_id", ""), **fields)


def _elapsed(data: dict) -> str:
    try:
        started = datetime.fromisoformat(data["started"])
    except (KeyError, ValueError):
        return "00:00"
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return _mmss((_now() - started).total_seconds())


def _mmss(seconds: float) -> str:
    secs = max(int(seconds), 0)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def _hint_line(text: str, command: bool = True) -> str:
    """One line, at most 120 characters. With `command` it carries the
    resume command and where to type it."""
    text = " ".join(str(text).split())
    tail = f" To retry, {IN_CLAUDE}"
    if command and RESUME not in text:
        text = text + tail
    if len(text) > 120:
        text = text[:120 - len(tail)].rstrip() + tail if command else text[:120].rstrip()
    return text


def error_line(key: str, **values) -> str:
    template = ERROR_LINES[key]
    return _hint_line(template.format(**values), command=RESUME in template)


def settings_line(support: str = "") -> str:
    """The settings read-back failure, with the help address when one is
    known and the whole line still fits."""
    base = error_line("settings")
    longer = f"{base}; if it repeats, write to {support}"
    return longer if support and len(longer) <= 120 else base


def signin_line(provider: str, error_class: str = "", support: str = "") -> str:
    name = PROVIDER_NAMES.get(provider, provider)
    if error_class == "unverified_app" and support:
        # "once approved": the client waits for the answer, then retries.
        line = f"{name} blocked this account. Write to {support}; once approved, {IN_CLAUDE}"
        if len(line) <= 120:
            return line
    template = SIGNIN_LINES.get(error_class) or SIGNIN_LINES["other"]
    return _hint_line(template.format(name=name))


def no_browser_line(support: str = "") -> str:
    """No browser opened AND the by-link flow could not start: nothing the
    client can retry, so it names who to write to. It names its OWN cause
    (the link), so it never reads as a second remedy for "no browser"."""
    head = "The sign-in link could not be made on this computer. "
    line = f"{head}Write to {support} and we will sign you in another way."
    if support and len(line) <= 120:
        return line
    return f"{head}Ask the Van Gogh team to sign you in another way."


def all_error_lines(support: str = "") -> list:
    """Every error line with sample values, for the cold read and the test
    that holds them to 120 characters."""
    lines = [error_line(k, name="Google", n=2, what="deals were") for k in ERROR_LINES
             if k != "settings"]
    lines.append(settings_line(support))
    lines.append(no_browser_line(support))
    for provider in ("google", "microsoft"):
        lines += [signin_line(provider, cls, support) for cls in SIGNIN_LINES]
    return lines


_TZ_WORDS = {}
for _zone, _words in (
        ("America/New_York", ("et", "est", "edt", "eastern", "eastern time")),
        ("America/Chicago", ("ct", "cst", "cdt", "central", "central time")),
        ("America/Denver", ("mt", "mst", "mdt", "mountain", "mountain time")),
        ("America/Los_Angeles", ("pt", "pst", "pdt", "pacific", "pacific time")),
        ("Etc/GMT", ("gmt",)), ("UTC", ("utc",))):
    for _w in _words:
        _TZ_WORDS[_w] = _zone


def resolve_timezone(text) -> str:
    """What a person types, as an IANA zone: a common US name or
    abbreviation, an exact zone name, or a city matched against the last
    segment of every zone ("new york" gives America/New_York). "" when
    nothing matches or several different clocks do."""
    from zoneinfo import ZoneInfo, available_timezones
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    if raw.lower() in _TZ_WORDS:
        return _TZ_WORDS[raw.lower()]
    zones = available_timezones()
    by_lower = {z.lower(): z for z in zones}
    exact = by_lower.get(raw.lower().replace(" ", "_"))
    if exact and "/" in exact:
        return exact

    def norm(s: str) -> str:
        return re.sub(r"[\s_\-.]+", "", s.lower())

    key = norm(raw)
    matches = [z for z in zones
               if "/" in z and not z.startswith(("Etc/", "posix/", "right/", "SystemV/"))
               and norm(z.rsplit("/", 1)[1]) == key]
    if not matches:
        return exact or ""      # a bare zone name with no city twin ("Japan")
    now = datetime.now(timezone.utc)
    if len({ZoneInfo(z).utcoffset(now) for z in matches}) > 1:
        return ""
    return sorted(matches, key=lambda z: (z.count("/"), len(z), z))[0]


# ── subprocess plumbing (every child goes through these two) ─────────────────

def _python() -> str:
    return sys.executable


# The checklist the running `next` is working on, so a child's time can be
# written to the ledger without threading `data` through every helper.
_CURRENT = {"data": None, "step": ""}


def _run_script(name: str, args: list, timeout: int = SCRIPT_TIMEOUT_S,
                input_text: str | None = None, cwd: str | None = None,
                cls: str | None = "machine") -> dict:
    """Run a sibling script and return {"rc", "json", "text"}.

    `json` is the first balanced object on stdout, or {} when there is none.
    Never raises on a nonzero exit: the caller reads `rc`. Raises only when
    the interpreter itself cannot be started or the child times out. Each
    call is one `job_begin`/`job_end` pair of class `cls` in the ledger
    (`None` skips the pair: the sign-in child is timed as a human interval).
    """
    detail = name + (":" + str(args[0]) if args else "")
    data = _CURRENT.get("data")
    if cls and data is not None:
        _log(data, "job_begin", step=_CURRENT.get("step", ""),
             **{"class": cls, "detail": detail})
    try:
        return _run_script_raw(name, args, timeout, input_text, cwd)
    finally:
        if cls and data is not None:
            _log(data, "job_end", step=_CURRENT.get("step", ""),
                 **{"class": cls, "detail": detail})


def _run_script_raw(name: str, args: list, timeout: int = SCRIPT_TIMEOUT_S,
                    input_text: str | None = None, cwd: str | None = None) -> dict:
    script = APP_DIR / name if not name.startswith("vault/") else PLUGIN_ROOT / name
    cmd = [_python(), str(script), *[str(a) for a in args]]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, input=input_text,
                       stdin=None if input_text is not None else subprocess.DEVNULL,
                       env=env, cwd=cwd, creationflags=NO_WINDOW)
    text = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    return {"rc": r.returncode, "json": _first_json(r.stdout or ""), "text": text}


def _spawn_detached(cmd: list, log_name: str) -> int:
    """Start a child that outlives this process. Returns its pid."""
    logs = ss.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / f"{log_name}.log", "a", encoding="utf-8")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW, start_new_session=(sys.platform != "win32"))
    out.close()
    return p.pid


def _first_json(text: str) -> dict:
    """The first balanced JSON object in `text`, or {}. String-aware scan."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        pass
                    break
        start = text.find("{", start + 1)
    return {}


def _cap_reset(result: dict) -> str:
    """The reset time a usage-limit message names, as a clock ("3:40 PM").
    "" when the child's output names none."""
    try:
        import failure_class
        when = failure_class.quota_reset_at(result.get("text") or "")
    except Exception:                                           # noqa: BLE001
        return ""
    return _clock(when) if when else ""


def _capped(result: dict) -> bool:
    """A usage cap in a child's reply: its own `degraded` flag, or the
    cap text classified by failure_class."""
    j = result.get("json") or {}
    if j.get("degraded"):
        return True
    try:
        import failure_class
        # is_usage_cap, not classify == "quota": another provider's "usage
        # limit exceeded" is that provider's problem, never the Claude cap.
        return failure_class.is_usage_cap(result.get("text") or "")
    except Exception:                                           # noqa: BLE001
        return False


# ── optional lane modules (imported lazily, stubbed in tests) ────────────────

def _try_import(name: str):
    try:
        return __import__(name)
    except Exception:                                           # noqa: BLE001
        return None


NUDGE_SKIPPED = "nudge job skipped (state dir overridden)"


def _nudge_job(action: str) -> dict:
    """Install or remove the standalone nudge job. Fails open.

    The job lives in the REAL per-user scheduler whatever the state dir is,
    so a run against an overridden state dir (a test, a harness, a
    --prepare-only rehearsal) must never install or remove it: that once
    registered com.monet.setup-nudge on a developer's machine from a temp
    dir. VAN_GOGH_NUDGE_JOB=1 forces it for a run that means it."""
    if action in ("install", "remove") and os.environ.get("VAN_GOGH_STATE_DIR") \
            and os.environ.get("VAN_GOGH_NUDGE_JOB") != "1":
        return {"ok": False, "skipped": True, "error": NUDGE_SKIPPED}
    mod = _try_import("setup_nudge")
    if mod is None:
        return {"ok": False, "error": "nudges not installed"}
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.main(["job", action])
        reply = _first_json(buf.getvalue())
        ok = rc in (0, None) and (reply.get("ok", True) is not False)
        return {"ok": ok, "state": reply.get("state", ""), "output": buf.getvalue()[-500:]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _keep_awake(action: str) -> None:
    """`start` makes sure the long-lived holder is running; `stop` ends it;
    `bell` rings. A `next` call is short-lived, so an inhibitor bound to its
    own pid would end a second later: the holder (`_keepawake`) is a detached
    child bound to ITSELF that lives while the checklist is open."""
    if action == "start":
        start_keepawake_holder()
    elif action == "stop":
        stop_keepawake_holder()
    elif action == "bell":
        mod = _try_import("keep_awake")
        if mod is not None:
            try:
                mod.bell()
            except Exception:                                   # noqa: BLE001
                pass


def _keepawake_pid_path() -> Path:
    return ss.setup_dir() / "keepawake.pid"


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    try:
        from platform_compat import pid_alive
        return pid_alive(pid)
    except Exception:                                           # noqa: BLE001
        return False


def start_keepawake_holder() -> int:
    """Start `_keepawake` detached unless one is already running. Returns
    the holder's pid (0 when nothing could be started). Never raises."""
    pid = _read_pid(_keepawake_pid_path())
    if pid and _pid_alive(pid):
        return pid
    try:
        pid = _spawn_detached([_python(), str(APP_DIR / "setup_flow.py"), "_keepawake"],
                              "setup-keepawake")
    except Exception:                                           # noqa: BLE001
        return 0
    try:
        _keepawake_pid_path().parent.mkdir(parents=True, exist_ok=True)
        _keepawake_pid_path().write_text(str(pid) + "\n", encoding="utf-8")
    except OSError:
        pass
    return pid


def stop_keepawake_holder() -> bool:
    """End the holder (finish, reset, a terminal error). Never raises."""
    path = _keepawake_pid_path()
    pid = _read_pid(path)
    ended = False
    if pid and _pid_alive(pid):
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            ended = True
        except (OSError, ValueError):
            pass
    try:
        path.unlink()
    except OSError:
        pass
    return ended


def keepawake_loop(max_seconds: int = KEEPAWAKE_MAX_S, poll: float = KEEPAWAKE_POLL_S,
                   clock=time.monotonic, sleep=time.sleep) -> str:
    """The holder's body: inhibit sleep while the checklist is open and
    unresolved, for at most `max_seconds`. Returns why it stopped."""
    mod = _try_import("keep_awake")
    if mod is not None:
        try:
            mod.start(os.getpid())
        except Exception:                                       # noqa: BLE001
            pass
    deadline = clock() + max_seconds
    why = "timeout"
    try:
        while clock() < deadline:
            data = ss.load()
            if data is None:
                why = "checklist gone"
                break
            if ss.resolved(data):
                why = "checklist resolved"
                break
            sleep(poll)
    finally:
        if mod is not None:
            try:
                mod.stop()
            except Exception:                                   # noqa: BLE001
                pass
    return why


def _cmd_keepawake(args) -> int:
    try:
        _keepawake_pid_path().parent.mkdir(parents=True, exist_ok=True)
        _keepawake_pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")
    except OSError:
        pass
    why = keepawake_loop()
    try:
        if _read_pid(_keepawake_pid_path()) == os.getpid():
            _keepawake_pid_path().unlink()
    except OSError:
        pass
    print(json.dumps({"ok": True, "stopped": why}))
    return 0


# ── the checklist, the key file, the config ──────────────────────────────────

def _plugin_version() -> str:
    try:
        return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        return ""


def _new_run_id() -> str:
    return _now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _read_run_id() -> str:
    try:
        return ss.run_id_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _keyfile_path() -> Path | None:
    d = ss.keyfile_dir()
    try:
        files = sorted(p for p in d.iterdir() if p.suffix == ".json")
    except OSError:
        return None
    return files[0] if files else None


def _keyfile() -> dict:
    """The key file's non-secret blocks: accounts hints, client, support."""
    path = _keyfile_path()
    if path is None:
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"accounts": doc.get("accounts") or [],
            "client": doc.get("client") or {},
            "support_email": doc.get("support_email") or ""}


def _vault_path() -> Path | None:
    try:
        raw = user_state.pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(os.path.expanduser(raw)) if raw else None


def _config_path() -> Path | None:
    v = _vault_path()
    return v / "van-gogh" / "config.json" if v else None


def _read_config() -> dict:
    p = _config_path()
    if p is None:
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8", newline="\n")


def _vault_ready() -> bool:
    return ss._check_vault()[0] == ss.DONE


def _client_tz():
    from zoneinfo import ZoneInfo
    name = ((_read_config().get("user") or {}).get("timezone")
            or (_keyfile().get("client") or {}).get("timezone") or "")
    try:
        return ZoneInfo(name) if name else _now().tzinfo or timezone.utc
    except Exception:                                           # noqa: BLE001
        return _now().tzinfo or timezone.utc


def _tag(company: str) -> str:
    tag = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-")
    return tag or "business"


def checks() -> dict:
    return dict(ss.CHECKS)


def _load_or_new(run_id: str = "") -> dict:
    data = ss.load()
    if data is None:
        data = ss.new(run_id or _read_run_id() or _new_run_id(),
                      sys.platform, _plugin_version())
        data["started"] = _iso()
        data["updated"] = _iso()
        ss.save(data)
        _log(data, "start", **{"class": "machine"})
    data.setdefault("answers", {})
    data.setdefault("progress", {})
    return data


def _verify(data: dict) -> list:
    return ss.verify(data, checks=checks())


def _set(data: dict, key: str, status: str, detail: str = "", pending=None) -> None:
    ss.set_status(data, key, status, detail, pending)
    ss.save(data)


def _answers(data: dict) -> dict:
    return data.setdefault("answers", {})


def _progress(data: dict) -> dict:
    return data.setdefault("progress", {})


def _degrade(data: dict, box: str, reason: str, **counts) -> None:
    data["degraded"] = {"box": box, "reason": reason, "at": _iso(), **counts}
    ss.set_status(data, box, ss.PENDING, f"waiting on the {host.host_word()} usage limit: {reason}")
    ss.save(data)
    _log(data, "degraded", step=box, **{"class": "machine", "detail": reason})


# ── prompts ──────────────────────────────────────────────────────────────────

def _prompt(data: dict, box: str, prompt: dict, line: str = "", reason: str = "") -> dict:
    """Emit a prompt. `reason` is why a question is being asked again: it
    leads the question itself, so the client reads it where they answer."""
    if reason:
        sep = "\n" if "\n" in prompt["question"] else " "
        prompt = {**prompt, "question": f"{reason}{sep}{prompt['question']}"}
        line = reason
    b = ss.box(data, box)
    b["pending"] = {"prompt": prompt["id"], "shown": _iso()}
    ss.save(data)
    _log(data, "prompt_shown", step=box, prompt=prompt["id"], **{"class": "human"})
    _arm_bell(data, prompt["id"])
    prompt = {**prompt, "answer_file": str(answer_file_path())}
    return {"kind": "prompt", "box": box, "prompt": prompt,
            "line": line or f"{b['title']}: one question", "elapsed": _elapsed(data)}


def _wait(data: dict, box: str, on: str, say: str) -> dict:
    prog = _progress(data)
    if not prog.get("waiting"):
        prog["waiting"] = {"on": on, "since": _iso()}
        ss.save(data)
        _log(data, "job_begin", step=box, **{"class": "machine", "detail": f"wait:{on}"})
    return {"kind": "wait", "box": box, "on": on, "say": say,
            "poll_seconds": POLL_SECONDS, "elapsed": _elapsed(data)}


def _end_wait(data: dict, box: str) -> None:
    prog = _progress(data)
    w = prog.pop("waiting", None)
    if w:
        _log(data, "job_end", step=box, **{"class": "machine", "detail": f"wait:{w['on']}"})
        ss.save(data)


def _take_answer(data: dict, answer: dict | None) -> tuple:
    """Match a stdin answer to the pending prompt. Returns (box, id, value)."""
    if not answer:
        return None, None, None
    pid = answer.get("id")
    for b in data["boxes"]:
        p = b.get("pending") or {}
        if p.get("prompt") == pid:
            b["pending"] = None
            ss.save(data)
            _log(data, "answer_received", step=b["key"], prompt=pid,
                 **{"class": "human"})
            return b["key"], pid, answer.get("value")
    return None, pid, answer.get("value")


def _arm_bell(data: dict, prompt_id: str) -> None:
    """A detached timer that rings only if this prompt is still unanswered
    after BELL_AFTER_S. The answer clears `pending`, which is the cancel."""
    try:
        _spawn_detached([_python(), str(APP_DIR / "setup_flow.py"), "_bell",
                         "--prompt", prompt_id, "--after", str(BELL_AFTER_S)],
                        "setup-bell")
    except Exception:                                           # noqa: BLE001
        pass


def bell_due(data: dict | None, prompt_id: str, now: datetime | None = None,
             after: int = BELL_AFTER_S) -> bool:
    """True when `prompt_id` has been waiting more than `after` seconds."""
    if not data:
        return False
    now = now or _now()
    for b in data["boxes"]:
        p = b.get("pending") or {}
        if p.get("prompt") != prompt_id:
            continue
        try:
            shown = datetime.fromisoformat(p["shown"])
        except (KeyError, ValueError):
            return False
        if shown.tzinfo is None:
            shown = shown.replace(tzinfo=timezone.utc)
        return (now - shown).total_seconds() > after
    return False


def _cmd_bell(args) -> int:
    """The detached timer behind `_arm_bell`: sleep, then ring only if the
    prompt is still the pending one (an answer clears it)."""
    time.sleep(args.after + 1)
    if not bell_due(ss.load(), args.prompt, after=args.after):
        return 0
    _keep_awake("bell")
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except OSError:
        pass
    return 0


# ── prepare ──────────────────────────────────────────────────────────────────

def _install_keyfile(data: dict) -> dict:
    """Find the key file, install everything in it, move it to the private
    folder (companion_key does the move). Records the non-secret blocks."""
    ck = _try_import("companion_key")
    if ck is None or not hasattr(ck, "discover"):
        return {"ok": False, "error": "no key file support"}
    try:
        found = ck.discover()
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    if not found:
        return {"ok": False, "error": "no key file"}
    path = found[0] if isinstance(found, (list, tuple)) else found
    try:
        res = ck.install_all(Path(path))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    if not isinstance(res, dict):
        return {"ok": False, "error": "bad install result"}
    kf = _answers(data).setdefault("keyfile", {})
    kf["accounts"] = [{"provider": a.get("provider"), "email_hint": a.get("email_hint", "")}
                      for a in (res.get("accounts") or [])]
    kf["client"] = {k: v for k, v in (res.get("client") or {}).items()
                    if k in ("full_name", "first_name", "company", "timezone", "notetaker",
                             "role", "city")}
    kf["support_email"] = res.get("support_email") or ""
    kf["mode"] = res.get("mode", "")
    ss.save(data)
    return {"ok": bool(res.get("ok", True)), "moved_to": str(res.get("moved_to") or "")}


def _hints(data: dict) -> dict:
    """Key-file blocks: from the checklist first, else re-read the file."""
    kf = _answers(data).get("keyfile") or {}
    if kf.get("accounts") or kf.get("client"):
        return kf
    return _keyfile()


def cmd_prepare(args) -> int:
    run_id = args.run_id or _read_run_id() or _new_run_id()
    ss.setup_dir().mkdir(parents=True, exist_ok=True)
    if not _read_run_id():
        ss.run_id_path().write_text(run_id + "\n", encoding="utf-8")
    if ss.load() is None and ss.read_stamp() is not None:
        res = verify_stamp(live=False)
        if res.get("already_installed"):
            return _emit({"ok": True, "already_installed": True, "run_id": run_id})
    data = _load_or_new(run_id)
    if args.platform:
        data["platform"] = args.platform
    _log(data, "step_begin", step="prepare", **{"class": "machine"})
    _CURRENT["data"] = data
    for key_name in ("plugin", "python"):
        _log(data, "step_begin", step=key_name, **{"class": "machine"})
    _verify(data)
    for key_name in ("plugin", "python"):
        _log(data, "step_end", step=key_name, **{"class": "machine",
                                                 "detail": ss.box(data, key_name)["status"]})
    _log(data, "step_begin", step="credentials", **{"class": "machine"})
    cred = ss.box(data, "credentials")
    key = {"ok": False}
    if cred["status"] != ss.DONE or not _keyfile_path():
        key = _install_keyfile(data)
        _verify(data)
    _log(data, "step_end", step="credentials", **{"class": "machine",
                                                  "detail": ss.box(data, "credentials")["status"]})
    _CURRENT["data"] = None
    # Under Codex, every command the model runs reads the plugin root from
    # Codex's own config; written now (root only, the vault roots follow at
    # the vault step) so the install skill's first command already has it.
    user_state.sync_codex_block()
    nudge = _nudge_job("install")
    data.setdefault("nudges", {})["job"] = (
        "installed" if nudge.get("ok") else
        NUDGE_SKIPPED if nudge.get("skipped") else
        f"not installed ({nudge.get('error') or nudge.get('state') or 'scheduler unavailable'})")
    ss.save(data)
    _log(data, "step_end", step="prepare", **{"class": "machine"})
    out = {"ok": True, "run_id": data["run_id"],
           "lines": ss.tick_list(data),
           "open": [b["key"] for b in ss.open_boxes(data)],
           "keyfile": key.get("ok", False), "nudge_job": nudge.get("ok", False)}
    if nudge.get("skipped"):
        out["nudge_job_note"] = NUDGE_SKIPPED
    if not key.get("ok") and ss.box(data, "credentials")["status"] != ss.DONE:
        out["keyfile_error"] = key.get("error", "")
    return _emit(out)


# ── the step machine ─────────────────────────────────────────────────────────

def step_machine_box(data: dict, key: str):
    """plugin, python, credentials: done by `prepare`, re-verified here."""
    _verify(data)
    b = ss.box(data, key)
    if b["status"] == ss.DONE:
        return None
    if key == "credentials":
        res = _install_keyfile(data)
        _verify(data)
        if ss.box(data, key)["status"] == ss.DONE:
            return None
        if res.get("error") == "no key file":
            raise StepError(key, error_line("no_key_file"))
        raise StepError(key, error_line("no_credentials"))
    if key == "python":
        raise StepError(key, error_line("no_python"))
    raise StepError(key, error_line("no_plugin"))


def _providers_to_sign_in(data: dict) -> list:
    hints = (_hints(data).get("accounts") or [])
    wanted = [(a.get("provider"), a.get("email_hint") or "") for a in hints
              if a.get("provider") in ("google", "microsoft")]
    if not wanted:
        wanted = [("google", ""), ("microsoft", "")]
    return wanted


def _run_signin(data: dict, provider: str, hint: str, extra: list) -> dict:
    """One auth_bootstrap call, timed as a human interval."""
    argv = ["--provider", provider, "--json", *extra]
    if hint:
        argv += ["--expect-email", hint]
    _log(data, "prompt_shown", step="accounts", prompt=f"signin:{provider}",
         **{"class": "human"})
    try:
        r = _run_script("auth_bootstrap.py", argv, timeout=AUTH_TIMEOUT_S, cls=None)
    except subprocess.TimeoutExpired:
        r = {"rc": 1, "json": {"ok": False, "error_class": "timeout"}, "text": ""}
    _log(data, "answer_received", step="accounts", prompt=f"signin:{provider}",
         **{"class": "human"})
    return r.get("json") or {}


def signin_link_prompt_spec(provider: str, j: dict) -> dict:
    """Sign in with no browser on this computer: a link (Google) or a short
    code (Microsoft), finished from any device."""
    not_now = {"label": "Not now",
               "description": "Setup stops here and nothing is lost; you can come back to it."}
    if provider == "google":
        return {
            "id": "signin_link", "multi": False, "header": "Sign in",
            "question": ("No browser opened on this computer, so sign in to Google by link.\n"
                         "1. Open this address in a browser. One on this computer is easiest, "
                         "because you copy an address back from it:\n"
                         f"{j.get('auth_url', '')}\n"
                         "2. Sign in and approve.\n"
                         "3. The page you end on looks like an error (\"This site can't be "
                         "reached\"). That is expected. Copy the full address of that page.\n"
                         "4. Choose \"I signed in\"; you paste the address next."),
            "free_text": "paste the full address of the page you ended on",
            "options": [{"label": "I signed in",
                         "description": "You paste the copied address next."},
                        not_now]}
    return {
        "id": "signin_link", "multi": False, "header": "Sign in",
        "question": ("No browser opened on this computer, so sign in to Microsoft with a code.\n"
                     f"1. On any device, go to {j.get('verification_uri', 'https://microsoft.com/devicelogin')}\n"
                     f"2. Type this code: {j.get('user_code', '')}\n"
                     "3. Sign in and approve, then come back here."),
        "options": [{"label": "Done, I signed in",
                     "description": "Van Gogh checks the sign-in and carries on."},
                    not_now]}


def _link_prompt(data: dict, slot: str, provider: str, hint: str, j: dict, reason: str = "") -> dict:
    _answers(data)["signin_pending"] = {"slot": slot, "provider": provider, "hint": hint}
    ss.save(data)
    return _prompt(data, "accounts", signin_link_prompt_spec(provider, j),
                   line="No browser here: sign in by link", reason=reason)


def _take_signin(data: dict, slot: str, provider: str, hint: str, j: dict, hinted: bool):
    """Act on one sign-in reply: store the account, or return a prompt
    (by-link sign-in, address mismatch), or raise the one line to show."""
    ans = _answers(data)
    done = ans.setdefault("accounts_done", [])
    accounts = ans.setdefault("accounts", [])
    support = _hints(data).get("support_email") or ""
    if j.get("ok") and j.get("pending"):
        return _link_prompt(data, slot, provider, hint, j)
    if not j.get("ok"):
        cls = j.get("error_class", "")
        if cls == "no_browser":
            j2 = _run_signin(data, provider, hint,
                             ["--headless"] if provider == "google" else ["--device-code"])
            if j2.get("ok") and j2.get("pending"):
                return _link_prompt(data, slot, provider, hint, j2)
            _log(data, "error", step="accounts", **{"class": "machine", "detail": cls})
            raise StepError("accounts", no_browser_line(support))
        if cls == "no_credential" and not ans.get("credential_retry"):
            # The line says "save the key file into Downloads": make that
            # true by installing a key file found there and trying once more.
            ans["credential_retry"] = True
            if _install_keyfile(data).get("ok"):
                return _take_signin(data, slot, provider, hint,
                                    _run_signin(data, provider, hint, []), hinted)
        ans.pop("credential_retry", None)
        if not hinted and accounts:
            done.append(slot)
            ans.setdefault("accounts_skipped", []).append(provider)
            ss.save(data)
            return None
        _log(data, "error", step="accounts", **{"class": "machine", "detail": cls})
        raise StepError("accounts", signin_line(provider, cls, support))
    ans.pop("credential_retry", None)
    account = {"provider": j.get("provider", provider), "email": j.get("email", ""),
               "label": j.get("label", ""), "env_var": j.get("env_var", ""), "slot": slot}
    if hint and j.get("email_matches_hint") is False:
        ans["mismatch"] = {"slot": slot, "account": account, "hint": hint}
        ss.save(data)
        return _prompt(data, "accounts", mismatch_prompt_spec(account["email"], hint),
                       line="One account signed in under a different address")
    _put_account(accounts, account)
    done.append(slot)
    ss.save(data)
    return None


def _finish_link_signin(data: dict, pending: dict, value, hinted: bool):
    """The answer to `signin_link`: exchange the pasted address (Google) or
    collect the approved code (Microsoft)."""
    provider, slot, hint = pending["provider"], pending["slot"], pending.get("hint", "")
    if _label_of(value).startswith("not now"):
        raise StepError("accounts", error_line("signin_later"))
    if provider == "google":
        address = value.get("address") if isinstance(value, dict) else value
        address = str(address or "").strip()
        if not address.lower().startswith("http"):
            j = _run_signin(data, provider, hint, ["--headless"])
            if j.get("ok") and j.get("pending"):
                return _link_prompt(data, slot, provider, hint, j, REASONS["paste_address"])
            return _take_signin(data, slot, provider, hint, j, hinted)
        j = _run_signin(data, provider, hint, ["--redirect-response", address])
    else:
        j = _run_signin(data, provider, hint, ["--complete-device-code"])
    return _take_signin(data, slot, provider, hint, j, hinted)


def step_accounts(data: dict, answer=None):
    ans = _answers(data)
    done = ans.setdefault("accounts_done", [])
    accounts = ans.setdefault("accounts", [])
    hinted = bool(_hints(data).get("accounts"))
    if answer is not None:
        pid, value = answer
        if pid == "account_mismatch":
            # Use it anyway, or sign in again.
            pending = ans.pop("mismatch", None) or {}
            if pending and _label_of(value) != "sign in again":
                _put_account(accounts, pending["account"])
                done.append(pending["slot"])
            ss.save(data)
        elif pid == "signin_link":
            pending = ans.pop("signin_pending", None) or {}
            ss.save(data)
            if pending:
                out = _finish_link_signin(data, pending, value, hinted)
                if out is not None:
                    return out
    for i, (provider, hint) in enumerate(_providers_to_sign_in(data)):
        slot = f"{provider}:{i}"
        if slot in done:
            continue
        out = _take_signin(data, slot, provider, hint,
                           _run_signin(data, provider, hint, []), hinted)
        if out is not None:
            return out
    ans.pop("accounts_redo", None)
    ans.pop("signin_pending", None)
    ss.save(data)
    _verify(data)
    if ss.box(data, "accounts")["status"] != ss.DONE:
        raise StepError("accounts", error_line("no_account"))
    _start_background(data)
    return None


def mismatch_prompt_spec(email: str, hint: str) -> dict:
    return {
        "id": "account_mismatch", "multi": False, "header": "Account",
        "question": (f"You signed in as {email}, but the key file expected {hint}. "
                     "Use this account anyway?"),
        "options": [
            {"label": "Use this account anyway", "description": f"Briefings read {email}."},
            {"label": "Sign in again",
             "description": f"Open the sign-in window again and pick {hint}."}]}


def _put_account(accounts: list, account: dict) -> None:
    """Add an account, or replace the one signed in again for the same slot
    (so the primary account stays first)."""
    for i, existing in enumerate(accounts):
        if existing.get("slot") and existing.get("slot") == account.get("slot"):
            accounts[i] = account
            return
    accounts.append(account)


def _label_of(value) -> str:
    if isinstance(value, dict):
        value = value.get("label") or value.get("value") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip().lower()


def _start_background(data: dict) -> None:
    """Start the scan, the research and the summaries, each once, the first
    time the vault config exists for them to write under."""
    if not _vault_ready():
        return
    ans = _answers(data)
    started = ans.setdefault("jobs_started", [])
    hints = _hints(data)
    client = hints.get("client") or {}
    deals = ss.box(data, "deals")
    if "scan" not in started:
        try:
            r = _run_script("warm_start.py", ["scan", "--since", "60"])
            j = r.get("json") or {}
            deals.setdefault("jobs", []).append(
                {"name": "scan", "pid": j.get("pid"), "started": _iso()})
            started.append("scan")
            _log(data, "job_begin", step="deals", **{"class": "machine", "detail": "scan"})
        except (OSError, subprocess.SubprocessError):
            pass
    fields = ans.get("confirm_fields") or {}
    name = (fields.get("full_name") or client.get("full_name")
            or (_read_config().get("user") or {}).get("full_name", ""))
    company = fields.get("company") or client.get("company") or ""
    if "research" not in started and name and name != "Your Name" and not company:
        # Nothing to read up on: the priorities prompt keeps its three
        # generic options rather than a guess about the wrong company.
        started.append("research")
        ans["research"] = "skipped"
        _log(data, "job_begin", step="deals", **{"class": "machine",
                                                 "detail": "research skipped: no company"})
    if "research" not in started and name and name != "Your Name":
        argv = ["start", "--name", name, "--company", company]
        for flag, key in (("--role", "role"), ("--city", "city")):
            if client.get(key):
                argv += [flag, str(client[key])]
        domains = sorted({a["email"].split("@", 1)[1] for a in ans.get("accounts", [])
                          if "@" in a.get("email", "")})
        if domains:
            argv += ["--domains", ",".join(domains)]
        try:
            r = _run_script("operator_research.py", argv)
            deals.setdefault("jobs", []).append(
                {"name": "research", "pid": (r.get("json") or {}).get("pid"),
                 "started": _iso()})
            started.append("research")
            _log(data, "job_begin", step="deals", **{"class": "model", "detail": "research"})
        except (OSError, subprocess.SubprocessError):
            pass
    if "summaries" not in started and ans.get("notetaker") not in (None, "", "none"):
        try:
            r = _run_script("warm_start.py", INGEST_START)
            ss.box(data, "meetings").setdefault("jobs", []).append(
                {"name": "prefetch", "pid": (r.get("json") or {}).get("pid"),
                 "started": _iso()})
            started.append("summaries")
            ss.set_status(data, "meetings", ss.PENDING, "summaries being written")
            _log(data, "job_begin", step="meetings", **{"class": "model",
                                                        "detail": "summaries"})
        except (OSError, subprocess.SubprocessError):
            pass
    ss.save(data)


NOTETAKER_ENV = {"granola": "GRANOLA_API_KEY", "grain": "GRAIN_API_KEY"}


def _notetaker_module(provider: str):
    return __import__(f"notetaker_{provider}")


def validate_notetaker_key(provider: str, key: str) -> dict:
    """One real call through the provider module with the key in the
    process env. Nothing is written. Returns {"ok", "error"}."""
    env_key = NOTETAKER_ENV.get(provider)
    if not env_key or not key:
        return {"ok": False, "error": "no key"}
    before = os.environ.get(env_key)
    os.environ[env_key] = key
    try:
        mod = _notetaker_module(provider)
        mod.latest_id()
        return {"ok": True}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    finally:
        if before is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = before


def _key_help(provider: str) -> str:
    """Where that notetaker's key is found, in the provider module's own
    words (the pre-call checklist prints the same string)."""
    try:
        return str(__import__(f"notetaker_{provider}").KEY_HELP)
    except Exception:                                           # noqa: BLE001
        return "Paste its personal API key."


def notetaker_prompt_spec() -> dict:
    return {
        "id": "notetaker", "multi": False, "header": "Notetaker",
        "question": "Which meeting notetaker do you use?",
        "free_text": "paste the key as your next reply",
        "options": [
            {"label": "Granola",
             "description": "You paste its API key next. " + _key_help("granola")},
            {"label": "Grain",
             "description": "You paste its API key next. " + _key_help("grain")},
            {"label": NOTETAKER_NONE,
             "description": "Van Gogh reads Granola and Grain today. Briefings still work, "
                            "without meeting notes."}]}


def _notetaker_prompt(data: dict, reason: str = "") -> dict:
    return _prompt(data, "meetings", notetaker_prompt_spec(),
                   line="Meeting notes: one question", reason=reason)


def step_notetaker(data: dict, answer=None):
    ans = _answers(data)
    if "notetaker" in ans:
        return None
    if answer is None:
        return _notetaker_prompt(data)
    pid, value = answer
    provider, key = "", ""
    if isinstance(value, dict):
        provider = str(value.get("provider") or "").strip().lower()
        key = str(value.get("key") or "")
        if not provider:
            provider = _label_of(value)
    else:
        provider = _label_of(value)
    provider = {"granola key": "granola", "grain key": "grain",
                NOTETAKER_NONE.lower(): "none", "other or none": "none", "other": "none",
                "none": "none", "neither": "none"}.get(provider, provider)
    name = provider.capitalize()
    if provider == "none":
        ans["notetaker"] = "none"
        ss.set_status(data, "meetings", ss.NOT_APPLICABLE, "no notetaker")
        ss.save(data)
        return None
    if provider not in NOTETAKER_ENV:
        return _notetaker_prompt(data, REASONS["bad_choice"])
    if not key.strip():
        return _notetaker_prompt(data, REASONS["empty_key"].format(name=name))
    res = validate_notetaker_key(provider, key.strip())
    if not res.get("ok"):
        return _notetaker_prompt(data, REASONS["bad_key"].format(name=name))
    user_state.upsert_env(NOTETAKER_ENV[provider], key.strip())
    ans["notetaker"] = provider
    ss.set_status(data, "meetings", ss.PENDING, "key saved, summaries not started")
    ss.save(data)
    _start_background(data)
    return None


TOKEN_PREFIXES = (("google", "GOOGLE_REFRESH_TOKEN_", "GOOGLE_ACCOUNT_EMAIL_", "google_client.py",
                   r"gmail: (\S+@\S+)"),
                  ("microsoft", "MS_GRAPH_REFRESH_TOKEN_", "MS_GRAPH_ACCOUNT_EMAIL_",
                   "microsoft_client.py", r"graph /me: (\S+@\S+)"))
PLACEHOLDER_EMAILS = ("you@gmail.com", "you@yourcompany.com")


def label_suffix(label: str) -> str:
    """auth_bootstrap._label_suffix, the one rule for a label's env key."""
    return label.strip().upper().replace(" ", "_").replace("-", "_")


def _label_for_suffix(suffix: str) -> str:
    """A label whose suffix is `suffix` again ("PERSONAL" gives "Personal",
    "NORTHWIND_2" gives "Northwind 2")."""
    return " ".join(part.capitalize() for part in suffix.split("_") if part)


def derive_accounts_from_env(data: dict | None = None) -> list:
    """The signed-in accounts as the state .env holds them, for a run whose
    accounts box was ticked from disk (a resume, an adopted install): one per
    refresh token, Google first. The address comes from the stored
    `*_ACCOUNT_EMAIL_<SUFFIX>`, else from one cheap live call through the
    account's own client, else stays empty (the confirm screen then shows
    "unknown address")."""
    env = ss._env_items()
    out = []
    for provider, token_prefix, email_prefix, script, pattern in TOKEN_PREFIXES:
        for key in env:
            if not key.startswith(token_prefix):
                continue
            suffix = key[len(token_prefix):]
            label = _label_for_suffix(suffix)
            assert label_suffix(label) == suffix, (label, suffix)
            email = env.get(email_prefix + suffix, "")
            if not email:
                try:
                    r = _run_script(script, ["--label", label], timeout=60)
                    m = re.search(pattern, r.get("text") or "") if r["rc"] == 0 else None
                    email = m.group(1).strip() if m else ""
                except (OSError, subprocess.SubprocessError):
                    email = ""
            out.append({"provider": provider, "email": email, "label": label,
                        "env_var": key, "slot": ""})
    return out


def _confirm_fields(data: dict) -> dict:
    ans = _answers(data)
    f = ans.get("confirm_fields")
    if f:
        return f
    client = _hints(data).get("client") or {}
    if not ans.get("accounts"):
        ans["accounts"] = derive_accounts_from_env(data)
    accounts = ans.get("accounts") or []
    full = client.get("full_name") or ""
    first = client.get("first_name") or (full.split()[0] if full else "")
    emails = [a.get("email") or f"unknown address ({a.get('label', '')})" for a in accounts]
    company = client.get("company") or ""
    if not company and emails and "@" in emails[0]:
        dom = emails[0].split("@", 1)[1].split(".")[0]
        if dom not in ("gmail", "outlook", "hotmail", "icloud", "yahoo", "live"):
            company = dom.capitalize()
    f = {"full_name": full, "first_name": first, "company": company,
         "timezone": resolve_timezone(client.get("timezone") or ""),
         "vault_path": str(Path.home() / DEFAULT_VAULT_NAME),
         "accounts": emails}
    ans["confirm_fields"] = f
    ss.save(data)
    return f


def confirm_prompt_spec(f: dict) -> dict:
    question = "\n".join([
        f"Name: {f['full_name'] or 'not set'} ({f['first_name'] or 'not set'})",
        f"Company: {f['company'] or 'not set'}",
        f"Timezone: {f['timezone'] or 'not set'}",
        f"Notes folder: {f['vault_path']} (where Van Gogh keeps its pages)",
        f"Accounts: {', '.join(f['accounts']) or 'none'}",
        "Does this look right?"])
    return {
        "id": "confirm", "multi": False, "header": "Your details",
        "question": question, "fields": dict(f),
        "options": [
            {"label": "Yes, continue",
             "description": "Saves these settings and creates the notes folder."},
            {"label": "Change something",
             "description": "Say what is wrong, for example the timezone or the notes folder."}]}


_FIELD_WORDS = {"full_name": "your full name", "first_name": "your first name",
                "company": "your company", "timezone": "your timezone"}


def _join_words(words: list) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _ask_reason(f: dict) -> str:
    """The details the key file did not carry, asked for in plain words before
    the person is shown a summary to confirm. A first name comes from the full
    name, so it is asked for only when a full name is already there."""
    keys = ["full_name", "company", "timezone"]
    if f.get("full_name") and not f.get("first_name"):
        keys.insert(1, "first_name")
    missing = [k for k in keys if not f.get(k)]
    if not missing:
        return ""
    text = REASONS["ask_details"].format(what=_join_words([_FIELD_WORDS[k] for k in missing]))
    return f"{text} {REASONS['ask_city']}" if "timezone" in missing else text


def _missing_reason(missing: list) -> str:
    words = [_FIELD_WORDS[k] for k in missing]
    if len(words) == 1:
        return REASONS["missing_one"].format(what=words[0])
    return REASONS["missing_many"].format(what=", ".join(words[:-1]) + " and " + words[-1])


def _confirm_prompt(data: dict, reason: str = "") -> dict:
    f = _confirm_fields(data)
    if not reason or reason == REASONS["updated"]:
        ask = _ask_reason(f)
        if ask:
            reason = f"{reason} {ask}".strip()
    return _prompt(data, "vault", confirm_prompt_spec(f),
                   line="Your details: one answer to confirm", reason=reason)


def step_vault(data: dict, answer=None):
    ans = _answers(data)
    if ss.box(data, "vault")["status"] == ss.DONE:
        return None
    if not ans.get("confirmed"):
        if answer is None:
            return _confirm_prompt(data)
        pid, value = answer
        f = _confirm_fields(data)
        if isinstance(value, dict) and value.get("label", "").lower().startswith("yes"):
            value = "yes"
        if isinstance(value, dict):
            reason = REASONS["updated"]
            for k in ("full_name", "first_name", "company"):
                if value.get(k):
                    f[k] = str(value[k]).strip()
            if value.get("timezone"):
                # Whatever they typed (a city, "Eastern", a zone name) is
                # resolved here and SHOWN for a tap, never applied silently.
                typed = " ".join(str(value["timezone"]).split())
                zone = resolve_timezone(typed)
                if zone:
                    f["timezone"] = zone
                else:
                    reason = REASONS["timezone"].format(answer=typed[:40])
            if value.get("vault_path") or value.get("vault"):
                f["vault_path"] = str(Path(os.path.expanduser(
                    value.get("vault_path") or value.get("vault"))))
            ans["confirm_fields"] = f
            ss.save(data)
            return _confirm_prompt(data, reason)
        if _label_of(value) not in ("yes", "yes, continue", "ok", "y", "true"):
            return _confirm_prompt(data, REASONS["say_change"])
        missing = [k for k in ("full_name", "first_name", "timezone") if not f.get(k)]
        if missing:
            return _confirm_prompt(data, _missing_reason(missing))
        zone = resolve_timezone(f["timezone"])
        if not zone:
            return _confirm_prompt(data, REASONS["timezone"].format(answer=f["timezone"][:40]))
        f["timezone"] = zone
        ans["confirm_fields"] = f
        ans["confirmed"] = True
        ss.save(data)
    f = _confirm_fields(data)
    vault = Path(f["vault_path"])
    r = _run_script("migrate_to_vault.py", ["--vault", str(vault)])
    if r["rc"] != 0:
        ans["confirmed"] = False
        ss.save(data)
        return _confirm_prompt(data, REASONS["folder_create"].format(path=vault))
    _write_install_config(data, vault, f)
    r = _run_script("vault/scaffold.py", [str(vault)])
    if r["rc"] != 0:
        ans["confirmed"] = False
        ss.save(data)
        return _confirm_prompt(data, REASONS["folder_write"].format(path=vault))
    for a in ans.get("accounts") or []:
        script = "google_client.py" if a["provider"] == "google" else "microsoft_client.py"
        r = _run_script(script, ["--label", a["label"]], timeout=120)
        if r["rc"] != 0:
            if a["provider"] == "microsoft":
                ans.setdefault("smoke_warnings", []).append(a["label"])
                continue
            # The token just written does not work: the resume signs that
            # one account in again (the accounts step runs for its slot).
            if a.get("slot") in ans.get("accounts_done", []):
                ans["accounts_done"].remove(a["slot"])
            ans["accounts_redo"] = True
            ss.save(data)
            raise StepError("vault", error_line(
                "smoke", name=PROVIDER_NAMES.get(a["provider"], a["provider"])))
    _verify(data)
    if ss.box(data, "vault")["status"] != ss.DONE:
        raise StepError("vault", settings_line(_hints(data).get("support_email") or ""))
    _start_background(data)
    return None


def _write_install_config(data: dict, vault: Path, f: dict) -> None:
    """config.json from the template with the six blocks the install
    populates, plus support.team_email and the notetaker provider."""
    ans = _answers(data)
    path = vault / "van-gogh" / "config.json"
    existing = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg = json.loads((PLUGIN_ROOT / "config.template.json").read_text(encoding="utf-8"))
    if existing:
        cfg.update(existing)
    full, first = f["full_name"], f["first_name"]
    cfg["user"] = {**cfg.get("user", {}), "full_name": full, "first_name": first,
                   "name_variants": [full, first], "self_entities": [full],
                   "timezone": f["timezone"],
                   "secondary_timezone": cfg.get("user", {}).get("secondary_timezone", "")}
    if not ans.get("accounts"):
        ans["accounts"] = derive_accounts_from_env(data)
    accounts = []
    for i, a in enumerate(ans.get("accounts") or []):
        accounts.append({"provider": a["provider"], "email": a["email"],
                         "label": a["label"], "is_primary": i == 0, "sent_folder_id": ""})
    cfg["accounts"] = accounts
    # Never a placeholder, never a label whose token is not there: the first
    # scan would look for GOOGLE_REFRESH_TOKEN_GMAIL and die.
    env = ss._env_items()
    prefixes = {p: t for p, t, _e, _s, _r in TOKEN_PREFIXES}
    for a in accounts:
        token_key = prefixes.get(a["provider"], "") + label_suffix(a["label"])
        if a["email"] in PLACEHOLDER_EMAILS or token_key not in env:
            raise StepError("vault", error_line("accounts_config"))
    if not accounts:
        raise StepError("vault", error_line("accounts_config"))
    cfg["obsidian"] = {**cfg.get("obsidian", {}), "vault_path": str(vault),
                       "hotcache_action_items_heading": f"{first}'s Action Items"}
    tag = _tag(f["company"])
    if f["company"] and not any(b.get("tag") == tag for b in cfg.get("businesses") or []
                                if isinstance(b, dict)):
        cfg["businesses"] = [
            {"tag": tag, "display_name": f["company"],
             "project_page": f"wiki/projects/{f['company']}.md",
             "meeting_route": f"20-{f['company']}/Meetings",
             "keywords": [], "priorities": []},
            *[b for b in cfg.get("businesses") or [] if isinstance(b, dict)]]
    if not any(b.get("tag") == "personal" for b in cfg.get("businesses") or []):
        cfg.setdefault("businesses", []).append(
            {"tag": "personal", "display_name": "Personal",
             "project_page": "wiki/projects/Personal.md",
             "meeting_route": "10-Personal/Meetings", "keywords": [], "priorities": []})
    cfg["email_filters"] = {k: [] for k in (cfg.get("email_filters") or {})}
    support_email = _hints(data).get("support_email") or ""
    if support_email:
        cfg.setdefault("support", {})["team_email"] = support_email
    nt = ans.get("notetaker")
    if nt and nt != "none":
        cfg.setdefault("notetaker", {})["provider"] = nt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


BRIEFING_TITLES = {"morning-coffee": "Morning Coffee", "afternoon-tea": "Afternoon Tea",
                   "week": "Week", "week-retro": "Week Retro"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


SCHEDULE_KEEP = "Keep these"
SCHEDULE_CHANGE = "Change something"
SCHEDULE_NONE = "Schedule nothing"
SCHEDULE_ASK = ("Say what to change in your own words, for example: Morning Coffee at "
                "6:30, or turn on the finance brief.")


def _schedule_proposal(data: dict | None = None) -> tuple:
    """(the config a yes would write, the job keys the client changed).

    The fresh-install proposal with every change the client has asked for so
    far laid over it, in the order they asked. Nothing is written here.
    """
    base = _read_config() if data is not None else {}
    if not base:
        base = json.loads((PLUGIN_ROOT / "config.template.json").read_text(encoding="utf-8"))
    # An install that already has a schedule keeps it: the proposal is laid
    # over a config nobody has chosen from yet, never over a person's choices.
    chosen = data is not None and (bool(data.get("adopted_from"))
                                   or bool((base.get("digest") or {}).get("enabled")))
    cfg = copy.deepcopy(base) if chosen else schedule_plan.setup_defaults(base)
    kept = list(_answers(data).get("schedule_changes") or []) if data is not None else []
    cfg, _done, _refused, touched = schedule_plan.apply_changes(cfg, kept)
    return cfg, tuple(touched)


def schedule_prompt_spec(data: dict | None = None) -> dict:
    cfg, touched = _schedule_proposal(data)
    rows = schedule_plan.table(cfg)
    return {
        "id": "schedule", "multi": False, "header": "Schedule",
        "show": schedule_plan.render(rows, touched),
        "question": "Keep these, or change something first?",
        "jobs": [{"job": r["key"], "name": r["name"], "can": r["can"]}
                 for r in rows if r["can"]],
        "options": [
            {"label": SCHEDULE_KEEP,
             "description": "Scheduled on this computer as shown. You can ask for a "
                            "change at any time later."},
            {"label": SCHEDULE_CHANGE,
             "description": "A different time or day, or something turned on or off. "
                            "You say it in your own words."},
            {"label": SCHEDULE_NONE,
             "description": "No briefing is emailed and nothing runs by itself. "
                            "Every skill still works when you ask for it."}]}


def _schedule_prompt(data: dict, reason: str = "") -> dict:
    return _prompt(data, "jobs", schedule_prompt_spec(data),
                   line="Background jobs: one answer", reason=reason)


def _take_schedule(data: dict, value):
    """Read one answer to the schedule question. Returns a prompt to ask it
    again, or None once the client has kept a schedule or chosen none."""
    ans = _answers(data)
    if isinstance(value, dict) and "changes" in value:
        asked = value.get("changes")
        asked = asked if isinstance(asked, list) else []
        cfg, _ = _schedule_proposal(data)
        kept = list(ans.get("schedule_changes") or [])
        said, refused = [], []
        for change in asked:
            cfg, done, no, _touched = schedule_plan.apply_changes(cfg, [change])
            if done:
                kept.append(change)
                said += done
            refused += no
        ans["schedule_changes"] = kept
        ss.save(data)
        if not asked:
            return _schedule_prompt(data, SCHEDULE_ASK)
        return _schedule_prompt(data, " ".join(refused + said))
    label = _label_of(value).strip().lower()
    if label.startswith(("schedule nothing", "no")):
        ans["schedule"] = "none"
    elif label.startswith(("keep", "yes")):
        ans["schedule"] = "custom" if ans.get("schedule_changes") else "all"
    else:
        return _schedule_prompt(data, SCHEDULE_ASK)
    ss.save(data)
    return None


def step_jobs(data: dict, answer=None):
    ans = _answers(data)
    b = ss.box(data, "jobs")
    if b["status"] in ss.RESOLVED:
        return None
    if "schedule" not in ans:
        if answer is None:
            return _schedule_prompt(data)
        _pid, value = answer
        again = _take_schedule(data, value)
        if again is not None:
            return again
    cfg = _read_config()
    accounts = cfg.get("accounts") or []
    primary = next((a for a in accounts if a.get("is_primary")), accounts[0] if accounts else {})
    if ans["schedule"] != "none":
        cfg, _ = _schedule_proposal(data)
        if not schedule_plan.any_on(schedule_plan.table(cfg)):
            # Every row was switched off one at a time: the same outcome as
            # choosing nothing, and it is recorded as that.
            ans["schedule"] = "none"
    if ans["schedule"] == "none":
        cfg = schedule_plan.nothing(_read_config())
        _write_config(cfg)
        ans.setdefault("skipped", [])
        if "jobs" not in ans["skipped"]:
            ans["skipped"].append("jobs")
        ss.set_status(data, "jobs", ss.SKIPPED, "no background jobs, by choice")
        ss.save(data)
        return None
    digest = cfg.setdefault("digest", {})
    digest["sender_label"] = primary.get("label", "")
    digest["recipient_email"] = primary.get("email", "")
    _write_config(cfg)
    if schedule_plan.scheduler_allowed():
        _run_script("scheduler_setup.py", ["install"])
    _verify(data)
    if ss.box(data, "jobs")["status"] != ss.DONE:
        raise StepError("jobs", error_line("jobs"))
    # Said once, on whatever the next turn prints, so the client sees what
    # was installed when it was installed (_with_news).
    ans["jobs_news"] = schedule_plan.installed(schedule_plan.table(cfg))
    ss.save(data)
    return None


def _research(data: dict) -> dict:
    """The finished research proposal, or {} when it is not done."""
    if _answers(data).get("research") == "skipped":
        return {}
    try:
        r = _run_script("operator_research.py", ["status"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if (r.get("json") or {}).get("state") != "done":
        return {}
    try:
        r = _run_script("operator_research.py", ["show"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    return r.get("json") or {}


def _business_tags() -> list:
    tags = [b.get("tag") for b in _read_config().get("businesses") or []
            if isinstance(b, dict) and b.get("tag") and b.get("tag") != "personal"]
    return tags


def _priorities_prompt(data: dict) -> dict:
    ans = _answers(data)
    tags = _business_tags()
    names = {b.get("tag"): b.get("display_name") or b.get("tag")
             for b in _read_config().get("businesses") or [] if isinstance(b, dict)}
    research = _research(data)
    options = []
    research_idx = {}
    pri = ((research.get("proposal") or {}).get("proposals") or {}).get("priorities") or []
    for i, p in enumerate(pri):
        label = str(p.get("name") or "").strip()
        if label and label not in research_idx:
            research_idx[label] = i
            options.append({"label": label, "description": str(p.get("detail") or
                                                               "Suggested from public pages.")})
    if not options:
        options = [{"label": label, "description": description}
                   for label, description in GENERIC_PRIORITIES]
    ans["priorities_research_idx"] = research_idx
    ss.save(data)
    which = ", ".join(names.get(t, t) for t in tags) or "your business"
    return _prompt(data, "deals", priorities_prompt_spec(which, options),
                   line="Your priorities: one question, asked once")


def priorities_prompt_spec(which: str, options: list | None = None) -> dict:
    if options is None:
        options = [{"label": label, "description": description}
                   for label, description in GENERIC_PRIORITIES]
    return {
        "id": "priorities", "multi": True, "header": "Priorities",
        "question": (f"What matters most right now in {which}? Tick any that are right, "
                     "and type your own if they are missing. This is what ranks your "
                     "briefings."),
        "free_text": "add a priority in your own words",
        "options": options}


# propose's own words for "the scan is still writing its results".
_NOT_WRITTEN_RE = re.compile(r"not (?:yet )?written|not written its results|no scan output yet", re.I)


def propose_line(reason: str = "") -> str:
    """A propose failure with its reason in brackets when it fits in 120."""
    reason = " ".join(str(reason or "").split()).rstrip(".")
    if reason:
        line = f"Deals could not be read ({reason}). To retry, {IN_CLAUDE}"
        if len(line) <= 120:
            return line
    return error_line("propose")


def _propose(data: dict):
    """Run propose. Returns (proposal, None) or (None, the wait or the
    error to hand back). Records the staged proposal's path and headings."""
    ans = _answers(data)
    r = _run_script("warm_start.py", ["propose"], timeout=300)
    if _capped(r):
        raise Degraded("usage cap while proposing deals", _cap_reset(r))
    proposal = r.get("json") or {}
    if "deals" not in proposal:
        reason = str(proposal.get("error") or "")
        if _NOT_WRITTEN_RE.search(reason):
            # The scan's snapshot exists but the mail review has not written
            # its results yet: not a failure, a short wait.
            return None, _wait(data, "deals", "scan", SAY_SCAN)
        _log(data, "error", step="deals", **{"class": "machine",
                                             "detail": f"propose: {reason}"[:500]})
        raise StepError("deals", propose_line(reason))
    ans["proposal_path"] = str(proposal.get("proposal_path") or "")
    ans["proposal_headings"] = [str(d.get("heading", "")) for d in proposal.get("deals") or []]
    ans["proposed_entities"] = len(proposal.get("entities") or [])
    # Declined threads are remembered by heading, so a re-proposal with new
    # indices still leaves them out.
    declined_headings = set(ans.get("declined_headings") or [])
    ans["declined_deals"] = sorted(i for i, h in enumerate(ans["proposal_headings"])
                                   if h in declined_headings)
    ss.save(data)
    return proposal, None


def _ask_deals(data: dict, proposal: dict):
    """The deals question over the proposal minus the declined ones, or
    nothing to ask (no candidates left)."""
    ans = _answers(data)
    declined = set(ans.get("declined_deals") or [])
    deals = [(i, d) for i, d in enumerate(proposal.get("deals") or []) if i not in declined]
    if not deals:
        ans["deals_picked"] = []
        ss.save(data)
        return None
    ans["proposed_deals"] = [i for i, _ in deals]
    ans["deal_labels"] = dict(deal_labels(deals))
    ss.save(data)
    return _prompt(data, "deals", deals_prompt_spec(deals, proposal.get("summary_md", "")),
                   line="Deals found in your mail: tick the real ones")


def _proposal_arg(ans: dict) -> list:
    path = ans.get("proposal_path") or ""
    return ["--proposal", path] if path else []


def _need_propose(ans: dict, reply: dict) -> bool:
    if reply.get("need_propose"):
        return True
    path = ans.get("proposal_path") or ""
    return bool(path) and not Path(path).exists() and "No staged proposal" in str(reply.get("error") or "")


def _repropose(data: dict):
    """Propose again and carry the picks across by heading. Returns None to
    carry on with the new indices, else the deals question again (a picked
    heading is gone from the new list) or a wait."""
    ans = _answers(data)
    wanted = list(ans.get("picked_headings") or [])
    proposal, pending = _propose(data)
    if pending is not None:
        return pending
    headings = ans.get("proposal_headings") or []
    found = [headings.index(h) for h in wanted if h in headings]
    _log(data, "step_begin", step="deals", **{"class": "machine",
                                              "detail": f"re-proposed: {len(found)} of {len(wanted)} picks found"})
    if wanted and len(found) == len(wanted):
        ans["deals_picked"] = found
        ss.save(data)
        return None
    ans.pop("deals_picked", None)
    ans.pop("picked_headings", None)
    ss.save(data)
    return _ask_deals(data, proposal)


def apply_line(reason: str = "") -> str:
    """An apply failure with its reason in brackets when it fits in 120."""
    reason = " ".join(str(reason or "").split()).rstrip(".")
    if reason:
        line = f"The ticked deals could not be saved ({reason}). To retry, {IN_CLAUDE}"
        if len(line) <= 120:
            return line
    return error_line("apply")


def outstanding_line(n: int) -> str:
    return error_line("outstanding", n=n, what="deal was" if n == 1 else "deals were")


def scan_line(reason: str = "") -> str:
    """The scan failure with its reason in brackets when it fits in 120."""
    reason = " ".join(str(reason or "").split()).rstrip(".")
    if reason:
        line = f"The mail scan stopped ({reason}). To run it again, {IN_CLAUDE}"
        if len(line) <= 120:
            return line
    return error_line("scan")


def _parse_wait(j: dict) -> str:
    """The scan state from Lane B's wait-scan reply, loosely: done, running,
    or failed."""
    state = str(j.get("state") or j.get("status") or "").lower()
    if j.get("done") is True or j.get("finished") is True or j.get("ready") is True \
            or state in ("done", "finished", "ready", "complete", "completed"):
        return "done"
    if j.get("failed") is True or state in ("failed", "error") or j.get("error"):
        return "failed"
    return "running"


SAY_SCAN = "reading your mail..."
SAY_MEETINGS = "filing your meetings..."
SAY_BRIEFINGS = "two briefings being written"
# Setup files every meeting from the last 60 days, at most 200. What the
# prefetch budget cannot read in full is filed from the notetaker's summary
# and read in full later by the hourly filing (the deep-read queue).
SETUP_MEETING_DAYS = 60
HISTORY_LINE = ("Your meetings from the last 60 days are filed. Next, Van Gogh gets the "
                "rest of your meeting history from your notetaker, a few meetings an "
                "hour, newest first, while this computer is on. Nothing for you to do.")
SETUP_MEETING_CAP = 200
INGEST_START = ["ingest-start", "--days", str(SETUP_MEETING_DAYS),
                "--limit", str(SETUP_MEETING_CAP)]
ALREADY_INSTALLED = "Van Gogh is already installed on this computer. Nothing to do."

DEALS_QUESTION = ("Which of these are real deals? Nothing is ticked to start. Tick each one "
                  "that is a real, live deal; only ticked ones are tracked.")
DEALS_NONE = "Pick this alone when none are real deals."


def _month_day(iso: str) -> str:
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso)
    return f"{d.strftime('%B')} {d.day}"


def deal_labels(deals: list) -> list:
    """[(label, index)] for `deals` = [(index, row), ...]. The label is the
    deal heading alone; two candidates with one heading get the person."""
    headings = [str(d.get("heading", "")) for _, d in deals]
    out, seen = [], set()
    for (i, d), heading in zip(deals, headings):
        label = heading
        if headings.count(heading) > 1:
            label = f"{heading} ({d.get('name') or d.get('email', '')})"
        n = 2
        while label in seen:
            label = f"{heading} ({d.get('name') or d.get('email', '')}, {n})"
            n += 1
        seen.add(label)
        out.append((label, i))
    return out


def deals_prompt_spec(deals: list, summary_md: str = "") -> dict:
    """`deals` is [(index, proposal row), ...]."""
    options = []
    for (label, _i), (_j, d) in zip(deal_labels(deals), deals):
        options.append({"label": label, "description": (
            f"{d.get('name') or d.get('email', '')}, {d.get('stage', '')}, "
            f"last contact {_month_day(d.get('last_contact', ''))}")})
    options.append({"label": "None of these", "description": DEALS_NONE})
    return {"id": "deals", "multi": True, "header": "Deals", "question": DEALS_QUESTION,
            "summary_md": summary_md, "options": options}


def step_deals(data: dict, answer=None):
    ans = _answers(data)
    b = ss.box(data, "deals")
    if b["status"] in ss.RESOLVED:
        return None
    _start_background(data)
    if "priorities" not in ans:
        if answer is None or answer[0] != "priorities":
            return _priorities_prompt(data)
        _pid, value = answer
        picks = value if isinstance(value, list) else ([value] if value else [])
        picks = [str(p.get("label") if isinstance(p, dict) else p).strip()
                 for p in picks if str(p.get("label") if isinstance(p, dict) else p).strip()]
        picks = [p for p in picks if p.lower() not in ("none", "none of these")]
        ans["priorities"] = picks
        ss.save(data)
        if picks:
            tags = _business_tags()
            payload = {t: picks for t in tags}
            _run_script("warm_start.py", ["priorities", "--set", json.dumps(payload)])
            idx = [i for label, i in (ans.get("priorities_research_idx") or {}).items()
                   if label in picks]
            if ans.get("priorities_research_idx"):
                _run_script("operator_research.py",
                            ["apply", "--priorities-json", json.dumps(idx)], timeout=120)
        answer = None
    if not ans.get("scan_done"):
        try:
            r = _run_script("warm_start.py", ["wait-scan", "--timeout", str(WAIT_SCAN_SLICE_S)],
                            timeout=WAIT_SCAN_SLICE_S + 60)
        except subprocess.TimeoutExpired:
            r = {"rc": 1, "json": {}, "text": ""}
        if _capped(r):
            raise Degraded("usage cap during the mail scan", _cap_reset(r))
        state = _parse_wait(r.get("json") or {})
        if state == "running":
            return _wait(data, "deals", "scan", SAY_SCAN)
        _end_wait(data, "deals")
        if state == "failed":
            reason = str((r.get("json") or {}).get("error") or "")
            _log(data, "error", step="deals", **{"class": "machine",
                                                 "detail": f"scan: {reason}"[:500]})
            raise StepError("deals", scan_line(reason))
        ans["scan_done"] = True
        ss.save(data)
        _log(data, "job_end", step="deals", **{"class": "machine", "detail": "scan"})
    if "deals_picked" not in ans:
        if answer is None or answer[0] != "deals":
            proposal, pending = _propose(data)
            if pending is not None:
                return pending
            return _ask_deals(data, proposal)
        _pid, value = answer
        picks = value if isinstance(value, list) else ([value] if value else [])
        chosen = []
        by_label = ans.get("deal_labels") or {}
        for p in picks:
            label = str(p.get("label") if isinstance(p, dict) else p).strip()
            if label in by_label:
                chosen.append(int(by_label[label]))
            elif re.fullmatch(r"\[?\d+\]?", label):
                chosen.append(int(label.strip("[]")))
        proposed = ans.get("proposed_deals") or []
        chosen = [i for i in chosen if i in proposed]
        headings = ans.get("proposal_headings") or []
        ans["deals_picked"] = chosen
        ans["picked_headings"] = [headings[i] for i in chosen if i < len(headings)]
        declined_now = set(proposed) - set(chosen)
        ans["declined_deals"] = sorted(set(ans.get("declined_deals") or []) | declined_now)
        ans["declined_headings"] = sorted(set(ans.get("declined_headings") or [])
                                          | {headings[i] for i in declined_now if i < len(headings)})
        ss.save(data)
    picked = ans.get("deals_picked") or []
    skipped = []
    j = {}
    for _attempt in range(2):
        # A proposal staged yesterday is gone today (its folder is dated), so
        # apply and verify may answer need_propose: propose again, map the
        # picks onto the new list by heading, and go once more.
        if picked:
            ents = ",".join(str(i) for i in range(ans.get("proposed_entities") or 0))
            r = _run_script("warm_start.py", ["apply", "--deals", ",".join(map(str, picked)),
                                              "--entities", ents] + _proposal_arg(ans), timeout=300)
            j = r.get("json") or {}
            if _need_propose(ans, j):
                out = _repropose(data)
                if out is not None:
                    return out
                picked = ans.get("deals_picked") or []
                continue
            if r["rc"] != 0:
                reason = str(j.get("error") or "")
                _log(data, "error", step="deals", **{"class": "machine",
                                                     "detail": f"apply: {reason}"[:500]})
                raise StepError("deals", apply_line(reason))
            # A pick apply left out because the vault already tracks it under
            # another heading. The scaffold's sample "Thread Name" row, which
            # apply removes, is bookkeeping and never counted or shown.
            skipped = [x for x in (j.get("skipped") or [])
                       if isinstance(x, dict) and x.get("matched")
                       and str(x.get("heading", "")).strip() != "Thread Name"]
        # The witness is the picks themselves: each one landed, or not. Never
        # `new_deals`: a fresh top 15 refills with other candidates after the
        # writes, so that count cannot reach zero when a subset was ticked.
        argv = ["verify"] + (["--picked", ",".join(map(str, picked))] + _proposal_arg(ans)
                             if picked else [])
        r = _run_script("warm_start.py", argv, timeout=300)
        j = r.get("json") or {}
        if picked and _need_propose(ans, j):
            out = _repropose(data)
            if out is not None:
                return out
            picked = ans.get("deals_picked") or []
            continue
        break
    ans["verify_new_deals"] = int(j.get("new_deals") or 0)      # recorded, never judged
    missing = list(j.get("missing") or [])
    landed = list(j.get("landed") or [])
    if not picked and ans.get("proposed_deals") and not _seeded_headings():
        ans.setdefault("skipped", [])
        if "deals" not in ans["skipped"]:
            ans["skipped"].append("deals")
        ss.set_status(data, "deals", ss.SKIPPED, "none ticked")
        ss.save(data)
        return None
    if not picked:
        tracked = len(_seeded_headings())
        ss.set_status(data, "deals", ss.DONE, f"{tracked} deals tracked, 0 new")
        ss.save(data)
        return None
    if missing:
        _log(data, "error", step="deals", **{"class": "machine",
                                             "detail": f"not saved: {missing}"[:500]})
        raise StepError("deals", outstanding_line(len(missing)))
    tracked = len(landed) if "landed" in j else len(picked)
    detail = f"{tracked} deals tracked"
    if skipped:
        names = ", ".join(str(s.get("matched")) for s in skipped)
        detail += f" ({len(skipped)} already tracked as {names})"
    ans["deals_skipped_as_tracked"] = [{"heading": s.get("heading"), "matched": s.get("matched")}
                                      for s in skipped]
    ss.set_status(data, "deals", ss.DONE, detail)
    ss.save(data)
    return None


def step_meetings_join(data: dict, answer=None):
    ans = _answers(data)
    b = ss.box(data, "meetings")
    if b["status"] in ss.RESOLVED:
        return None
    if ans.get("notetaker") in (None, "", "none"):
        ss.set_status(data, "meetings", ss.NOT_APPLICABLE, "no notetaker")
        ss.save(data)
        return None
    if (data.get("degraded") or {}).get("box") == "meetings" \
            and "summaries" in ans.get("jobs_started", []):
        # A cap left some unread last time: read the rest again from the start.
        ans["jobs_started"].remove("summaries")
    if "summaries" not in ans.get("jobs_started", []):
        _start_background(data)
    r = _run_script("warm_start.py", ["ingest-finish"], timeout=900)
    j = r.get("json") or {}
    filed, failed, deferred = _count(j.get("filed")), _count(j.get("failed")), _count(j.get("deferred"))
    # Cumulative across resumes: a second pass files only what the first left.
    filed += int(ans.get("meetings_filed") or 0) if (data.get("degraded") or {}).get("box") == "meetings" else 0
    if _capped(r):
        ans["meetings_filed"] = filed
        _degrade(data, "meetings", f"{filed} filed, {deferred} waiting",
                 filed=filed, waiting=deferred, reset=_cap_reset(r))
        return None
    if j.get("pending"):
        return _wait(data, "meetings", "summaries", SAY_MEETINGS)
    if _nothing_staged(j):
        # The prefetch never started (a resume on a fresh session): start it
        # now and come back for it.
        started = ans.setdefault("jobs_started", [])
        if "summaries" in started:
            started.remove("summaries")
        _start_background(data)
        return _wait(data, "meetings", "summaries", SAY_MEETINGS)
    _end_wait(data, "meetings")
    if r["rc"] != 0 and "filed" not in j:
        raise StepError("meetings", error_line("meetings"))
    ans["meetings_filed"] = filed
    # A meeting too long to read inside the install is filed from the
    # notetaker's own summary and queued for a full read (absent key = none).
    ans["meetings_summary_only"] = _count_over_budget(j.get("summary_only"))
    if deferred:
        _degrade(data, "meetings", f"{filed} filed, {deferred} waiting",
                 filed=filed, waiting=deferred, reset=_cap_reset(r))
        return None
    detail = meetings_detail(filed, failed, ans["meetings_summary_only"])
    ss.set_status(data, "meetings", ss.DONE, detail)
    ss.save(data)
    _log(data, "job_end", step="meetings", **{"class": "model", "detail": "summaries"})
    return None


def meetings_detail(filed: int, failed: int = 0, summary_only: int = 0) -> str:
    detail = f"{filed} filed"
    if summary_only:
        detail += f", {summary_only} from the notetaker's summary"
    if failed:
        detail += f", {failed} failed"
    return detail


def summary_only_line(count: int, jobs_on: bool) -> str:
    """The closing line for meetings filed from the notetaker's own summary:
    who reads the full recording, and when. "" for none."""
    if count <= 0:
        return ""
    if count == 1:
        head = "1 long meeting was filed from your notetaker's own summary for now"
        rest = "the full recording"
    else:
        head = f"{count} long meetings were filed from your notetaker's own summary for now"
        rest = "the full recordings"
    if jobs_on:
        return f"{head}; Van Gogh reads {rest} overnight while this computer is on."
    return f"{head}. To have {rest} read, in {host.host_word()}, type {LATER['meetings']}"


def _count_over_budget(rows) -> int:
    """How many meetings the install's time budget cut short.

    `summary_only` lists every meeting filed with no transcript read, which
    includes a meeting the notetaker has no transcript for. Only a budget cut
    is queued for a full read later, so only those may be promised one.
    """
    if not isinstance(rows, list):
        return 0
    return sum(1 for r in rows if isinstance(r, dict) and r.get("over_budget"))


def _count(v) -> int:
    return len(v) if isinstance(v, list) else int(v or 0)


def _nothing_staged(j: dict) -> bool:
    return any("no staged summaries" in str(e) for e in (j.get("errors") or []))


def finish_machine_boxes(data: dict) -> list:
    """Finish the boxes that need no human (called by the nudge job and by
    `adopt`): re-verify from disk, then file staged meetings when a
    notetaker key exists. Returns the keys that became resolved."""
    before = {b["key"]: b["status"] for b in data["boxes"]}
    ss.verify(data, checks=checks())
    b = ss.box(data, "meetings")
    if b["status"] not in ss.RESOLVED:
        if not _notetaker_keyed():
            ss.set_status(data, "meetings", ss.NOT_APPLICABLE, "no notetaker")
        else:
            try:
                r = _run_script("warm_start.py", ["ingest-finish"], timeout=900)
            except (OSError, subprocess.SubprocessError) as exc:
                r = {"rc": 1, "json": {}, "text": type(exc).__name__}
            j = r.get("json") or {}
            n, deferred = _count(j.get("filed")), _count(j.get("deferred"))
            errors = [str(e) for e in (j.get("errors") or [])]
            errors += [str(f.get("error") or "") for f in (j.get("failed") or [])
                       if isinstance(f, dict)]
            auth = next((e for e in errors if re.search(
                r"401|403|unauthori[sz]ed|forbidden|api key|NotetakerError", e, re.I)), "")
            if j.get("pending"):
                ss.set_status(data, "meetings", ss.PENDING, "summaries still being written")
            elif _capped(r):
                _degrade(data, "meetings", f"{n} filed, {deferred} waiting",
                         filed=n, waiting=deferred, reset=_cap_reset(r))
            elif auth:
                ss.set_status(data, "meetings", ss.OPEN,
                              f"notetaker sign-in failed ({auth[:60]})")
            elif _nothing_staged(j):
                # Only the prefetch: the scan and the research belong to the
                # install flow, not to a nudge run on an adopted install.
                try:
                    r2 = _run_script("warm_start.py", INGEST_START)
                    b.setdefault("jobs", []).append(
                        {"name": "prefetch", "pid": (r2.get("json") or {}).get("pid"),
                         "started": _iso()})
                    ss.set_status(data, "meetings", ss.PENDING, "summaries being written")
                except (OSError, subprocess.SubprocessError):
                    pass
            elif j.get("ingested"):
                short = _count_over_budget(j.get("summary_only"))
                _answers(data)["meetings_summary_only"] = short
                ss.set_status(data, "meetings", ss.DONE, meetings_detail(n, 0, short))
                _log(data, "job_end", step="meetings", **{"class": "model",
                                                          "detail": f"{n} filed"})
    ss.save(data)
    return [k for k, v in before.items()
            if v not in ss.RESOLVED and ss.box(data, k)["status"] in ss.RESOLVED]


def _which_second(now_local: datetime) -> str:
    return "morning-coffee" if now_local.hour < 12 else "afternoon-tea"


def _seeded_headings() -> list:
    cfg = _read_config()
    vault = _vault_path()
    if not vault or not cfg:
        return []
    rel = (cfg.get("obsidian") or {}).get("hotcache_relpath", "wiki/hotcache.md")
    heading = (cfg.get("obsidian") or {}).get("hotcache_active_threads_heading", "Active Threads")
    try:
        lines = (vault / rel).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = line[3:].strip() == heading
            continue
        if inside and line.startswith("### "):
            out.append(line[4:].strip())
    return out


def _names_seeded_deal(text: str, seeded: list) -> bool:
    """Whether a briefing names at least one seeded deal.

    The briefing skills render a deal by its counterparty and subject, never
    by the hot cache heading verbatim ("Bearpeakpower: Waterloo" appears as
    "Waterloo" and "Michael Marcotte" in a live briefing), so the witness
    accepts the heading, or any distinctive token of it: five or more
    letters and not in the shared generic list (`hotcache_sync._generic`,
    the one copy, never a private stoplist), or the deal's own counterparty
    name. A single generic word is never a match (the FUZZY-MATCH rule).
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    try:
        import hotcache_sync
        generic = set(hotcache_sync._generic())
    except Exception:                                           # noqa: BLE001
        generic = set()
    for heading in seeded or []:
        h = (heading or "").strip()
        if not h:
            continue
        if h.lower() in low:
            return True
        tokens = [w for w in re.findall(r"[a-z]{5,}", h.lower()) if w not in generic]
        if any(w in low for w in tokens):
            return True
        for name in _seeded_counterparties(h):
            if name and name.lower() in low:
                return True
    return False


def _seeded_counterparties(heading: str) -> list:
    """The [[Name]] links inside one seeded deal block of the hot cache."""
    cfg = _read_config()
    vault = _vault_path()
    if not vault or not cfg:
        return []
    rel = (cfg.get("obsidian") or {}).get("hotcache_relpath", "wiki/hotcache.md")
    try:
        lines = (vault / rel).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names, inside = [], False
    for line in lines:
        if line.startswith("### "):
            inside = line[4:].strip() == heading
            continue
        if inside:
            names.extend(re.findall(r"\[\[([^\]|]+)", line))
    return names


def step_briefings(data: dict, answer=None):
    ans = _answers(data)
    b = ss.box(data, "briefings")
    if b["status"] == ss.DONE and not ans.get("dispatch_open"):
        return None
    second = ans.get("second_briefing") or _which_second(_now().astimezone(_client_tz()))
    ans["second_briefing"] = second
    if not ans.get("briefing_data"):
        for which in ("week", "auto"):
            r = _run_script("warm_start.py", ["first-briefing", "--which", which], timeout=600)
            if _capped(r):
                raise Degraded("usage cap while preparing the briefings", _cap_reset(r))
        ans["briefing_data"] = True
        ss.save(data)
    dispatches = ans.get("dispatches") or 0
    if dispatches and ans.get("dispatch_open"):
        _log(data, "dispatch", step="briefings", phase="end", **{"class": "model"})
        ans["dispatch_open"] = False
        ss.save(data)
        vault = _vault_path()
        root = vault / "van-gogh" if vault else None
        seeded = _seeded_headings()
        missing = []
        since = ans.get("dispatch_at", "")
        for name in ("week", second):
            p = root / f"{name}.md" if root else None
            try:
                text = p.read_text(encoding="utf-8") if p else ""
            except OSError:
                text = ""
            fresh = bool(p and p.exists() and p.stat().st_mtime >= ans.get("dispatch_epoch", 0))
            if not text.strip() or not fresh:
                missing.append(f"{name} not written")
            elif seeded and not _names_seeded_deal(text, seeded):
                missing.append(f"{name} names no tracked deal")
        if not missing:
            ss.set_status(data, "briefings", ss.DONE, f"week, {second}")
            ss.save(data)
            return None
        if dispatches >= MAX_DISPATCHES:
            raise StepError("briefings", error_line("briefings"))
    ans["dispatches"] = dispatches + 1
    ans["dispatch_open"] = True
    ans["dispatch_at"] = _iso()
    ans["dispatch_epoch"] = time.time() - 1
    ss.set_status(data, "briefings", ss.PENDING, SAY_BRIEFINGS)
    ss.save(data)
    _log(data, "dispatch", step="briefings", phase="begin", **{"class": "model"})
    agents = [{"name": "week", "prompt": host.cmd("week")},
              {"name": second, "prompt": host.cmd(second)}]
    # Codex has no Agent tool: the entries run one after another in the
    # session that is doing setup (the install skill reads `inline`).
    return {"kind": "dispatch", "agents": agents, "inline": host.is_codex(),
            "say": SAY_BRIEFINGS, "elapsed": _elapsed(data)}


# (box the step owns, function). The order is the install's, not the box list's.
PHASES = (
    ("plugin", lambda d, a=None: step_machine_box(d, "plugin")),
    ("python", lambda d, a=None: step_machine_box(d, "python")),
    ("credentials", lambda d, a=None: step_machine_box(d, "credentials")),
    ("accounts", step_accounts),
    ("meetings", step_notetaker),
    ("vault", step_vault),
    ("jobs", step_jobs),
    ("deals", step_deals),
    ("meetings", step_meetings_join),
    ("briefings", step_briefings),
)
STEPS = {f"{i}:{box}": fn for i, (box, fn) in enumerate(PHASES)}


def answer_file_path() -> Path:
    """Where the session writes an answer for `next --answer-file`."""
    return ss.setup_dir() / "answer.json"


def _read_answer(path: str = "") -> dict | None:
    if path:
        # Read once and delete, whatever it held: an answer can carry a
        # notetaker key, and a file that outlives the read is a key on disk.
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        try:
            target.unlink()
        except OSError:
            pass
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        obj = _first_json(raw)
    return obj if isinstance(obj, dict) and obj.get("id") else None


def run_next(answer: dict | None = None) -> dict:
    """One turn of the loop. Returns the object to print."""
    if ss.load() is None and ss.read_stamp() is not None:
        res = verify_stamp(live=False)
        if res.get("already_installed"):
            return {"kind": "done", "already_installed": True, "report": res.get("report", {}),
                    "closing_text": ALREADY_INSTALLED, "elapsed": "00:00"}
    data = _load_or_new()
    _keep_awake("start")
    # The answer is matched to its prompt BEFORE the disk check: set_status
    # clears a box's pending prompt, and verify may tick the very box the
    # answer belongs to (accounts, from the token the sign-in just wrote).
    box_of_answer, pid, value = _take_answer(data, answer)
    pending_answer = (pid, value) if pid else None
    # Then disk: a box whose artifact vanished since the last turn reopens
    # here, before any step trusts the checklist's memory of it.
    if _verify(data):
        ss.save(data)
    current = None
    _CURRENT["data"] = data
    try:
        for i, (box, fn) in enumerate(PHASES):
            key = f"{i}:{box}"
            fn = STEPS.get(key, fn)
            current = box
            _CURRENT["step"] = box
            a = pending_answer if (pending_answer and box_of_answer == box) else None
            if a is not None:
                pending_answer = None
            if ss.box(data, box)["status"] in ss.RESOLVED and a is None and not (
                    box == "briefings" and _answers(data).get("dispatch_open")) and not (
                    box == "accounts" and _answers(data).get("accounts_redo")):
                # A resolved box is skipped unless it holds the answer just
                # given (verify may tick accounts from .env before the
                # mismatch answer lands) or a dispatched briefing waits to be
                # checked by its own step (content, a tracked deal named).
                continue
            _log(data, "step_begin", step=box, **{"class": "machine"})
            try:
                out = fn(data, a)
            except Degraded as exc:
                _degrade(data, box, str(exc), reset=exc.reset)
                _log(data, "step_end", step=box, **{"class": "machine", "detail": "degraded"})
                continue
            _log(data, "step_end", step=box, **{"class": "machine",
                                                "detail": ss.box(data, box)["status"]})
            if data.get("degraded") and (data["degraded"].get("box") == box
                                         and ss.box(data, box)["status"] in ss.RESOLVED):
                data["degraded"] = None
                ss.save(data)
            if out is not None:
                return _with_news(data, out)
    except StepError as exc:
        ss.save(data)
        _keep_awake("stop")
        return {"kind": "error", "box": exc.box, "line": exc.line, "elapsed": _elapsed(data)}
    except subprocess.TimeoutExpired:
        return {"kind": "error", "box": current, "line": error_line("timeout"),
                "elapsed": _elapsed(data)}
    finally:
        _CURRENT["data"] = None
        _CURRENT["step"] = ""
    return _with_news(data, finish(data))


def _with_news(data: dict, out: dict) -> dict:
    """Lead the next thing printed with anything the client must be told
    once (the jobs just scheduled), then shape it for the host. The news
    rides in `show`, which the install skill prints first, before a prompt
    or a wait alike."""
    ans = _answers(data)
    news = ans.pop("jobs_news", "")
    if news and not (out.get("kind") == "done" and out.get("complete")):
        # A finished run has removed its checklist; saving would recreate it.
        ss.save(data)
        if out.get("kind") == "prompt":
            p = out["prompt"]
            out = {**out, "prompt": {**p, "show": f"{news}\n\n{p['show']}" if p.get("show")
                                     else news}}
        else:
            out = {**out, "show": f"{news}\n\n{out['show']}" if out.get("show") else news}
    if host.is_codex() and out.get("kind") == "prompt" and out["prompt"].get("show"):
        # Codex asks in plain text, and a model reading 'print show first'
        # drops the table often enough that the client is left guessing what
        # runs. The table becomes part of the question it must ask as given.
        p = dict(out["prompt"])
        p["question"] = f"{p.pop('show')}\n\n{p['question']}"
        out = {**out, "prompt": p}
    return out


def cmd_next(args) -> int:
    answer = _read_answer(args.answer_file) if args.answer_file else (
        _read_answer() if args.answer_stdin else None)
    return _emit(run_next(answer))


# ── finish, report, closing text ─────────────────────────────────────────────

def _first_email_line(cfg: dict, now_local: datetime, support: str = "") -> str:
    digest = cfg.get("digest") or {}
    if not digest.get("enabled"):
        return (f"No briefings are scheduled yet. To turn them on, in {host.host_word()}, type "
                f"{host.cmd('update-digest-preferences')}")
    mc = (digest.get("briefings") or {}).get("morning-coffee") or {}
    hhmm = str(mc.get("time") or "07:00")
    days = [d.lower() for d in (mc.get("days") or _WEEKDAYS)]
    try:
        hour, minute = (int(x) for x in hhmm.split(":"))
    except ValueError:
        hour, minute = 7, 0
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for ahead in range(1, 8):
        day = now_local + timedelta(days=ahead)
        if names[day.weekday()] in days:
            at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            later = at + timedelta(hours=2)
            help_line = f", or write to {support}" if support else ""
            # The weekday by name: "tomorrow" reads false on a Friday.
            return (f"Your first briefing email arrives {names[day.weekday()].capitalize()} at "
                    f"{_clock(at)}. If it is not there by {_clock(later)}, open {host.host_word()} and "
                    f"type {host.cmd('morning-coffee')}{help_line}.")
    return "Your first briefing email arrives on the next scheduled morning."


def _spoken(total: str) -> str:
    """"14:45" as "14 minutes 45 seconds" (a bare 14:45 reads as a clock time)."""
    try:
        minutes, seconds = (int(x) for x in total.split(":"))
    except ValueError:
        return total
    m = f"{minutes} minute{'' if minutes == 1 else 's'}"
    s = f"{seconds} second{'' if seconds == 1 else 's'}"
    return f"{m} {s}" if minutes else s


def _clock(t: datetime) -> str:
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def unfinished_text(data: dict) -> str:
    """An unfinished run in one statement: what is done, what waits and why,
    what happens by itself, and the ONE command. Whatever it promises is true
    in code: "overnight" only when the early-morning jobs are installed, and
    only while the computer is on, which is when a job can run at all."""
    d = data.get("degraded") or {}
    limit = f"your {host.host_word()} plan's usage limit was reached"
    when = (f"once the limit resets at {d['reset']}" if d.get("reset") else
            f"once the limit resets ({host.host_word()} shows the time when you type there)")
    jobs_on = ss.box(data, "jobs")["status"] == ss.DONE
    if d.get("box") == "meetings":
        filed, waiting = int(d.get("filed") or 0), int(d.get("waiting") or 0)
        rest = "The other one is" if waiting == 1 else f"The other {waiting} are"
        head = (f"Setup is almost done: {filed} of your {filed + waiting} meetings are filed. "
                f"{rest} waiting because {limit}")
        if jobs_on:
            own = "it files by itself" if waiting == 1 else "they file by themselves"
            return (f"{head}, and {own} overnight while this computer is on. "
                    f"To check on them, {IN_CLAUDE}")
        return f"{head}. To file them {when}, {IN_CLAUDE}"
    if d.get("box") == "deals":
        return (f"Setup is almost done. Reading your mail stopped because {limit}. "
                f"To pick up where it stopped {when}, {IN_CLAUDE}")
    if d.get("box") == "briefings":
        return (f"Setup is almost done. Your first briefings are waiting because {limit}. "
                f"To have them written {when}, {IN_CLAUDE}")
    actions = {row[0]: row[3] for row in ss.BOXES}
    todo = ", ".join(actions[b["key"]] for b in ss.open_boxes(data))
    return f"Setup is not finished. Still to do: {todo}. To resume, {IN_CLAUDE}"


def undone_line(b: dict) -> str:
    """One line for a box the client left undone: what is not there, why,
    and the command that brings it later. A box still in progress has no
    line here; `unfinished_text` says it once."""
    key, status = b["key"], b["status"]
    if key == "jobs" and status == ss.SKIPPED:
        return ("Nothing is scheduled, as you chose: no briefing is emailed and nothing "
                "runs by itself. To turn something on, ask Vincent to schedule it.")
    if key == "deals" and status == ss.SKIPPED:
        return ("No deals are tracked yet, because none were ticked. "
                f"In {host.host_word()}, {LATER['deals']} proposes them again.")
    if key == "meetings" and status == ss.NOT_APPLICABLE:
        return ("No meeting notes are filed, because no notetaker was connected. When you "
                f"have one, in {host.host_word()}, type {SETTINGS_COMMAND}; it asks for the key.")
    if key == "meetings" and status == ss.SKIPPED:
        return ("No meeting notes are filed, because you skipped that step. "
                f"To file them, in {host.host_word()}, type {LATER['meetings']}")
    return ""


def closing_text(data: dict, cfg: dict | None = None, total: str = "") -> str:
    cfg = cfg if cfg is not None else _read_config()
    now_local = _now().astimezone(_client_tz())
    lines = []
    if data.get("degraded") or not ss.resolved(data):
        lines.append(unfinished_text(data))
    elif total:
        lines.append(f"Installed in {_spoken(total)}.")
    support = (cfg.get("support") or {}).get("team_email") or \
        (_hints(data).get("support_email") or "")
    # One fact, one line: when the client declined the jobs, the skipped-box
    # line below is the whole statement and the first-email line is left out.
    first = "" if ss.box(data, "jobs")["status"] == ss.SKIPPED else \
        _first_email_line(cfg, now_local, support)
    if first:
        lines.append(first)
    # Before the lines about what was left undone: two of them say to ask
    # Vincent, and a person should have been told who that is first.
    if (ss.resolved(data) and not data.get("degraded")) or \
            ss.box(data, "jobs")["status"] == ss.SKIPPED:
        lines.append(VINCENT_LINE)
    for b in data["boxes"]:
        line = undone_line(b)
        if line:
            lines.append(line)
    short = summary_only_line(int(_answers(data).get("meetings_summary_only") or 0),
                              ss.box(data, "jobs")["status"] == ss.DONE)
    if short and ss.box(data, "meetings")["status"] == ss.DONE:
        lines.append(short)
    if (ss.box(data, "meetings")["status"] == ss.DONE
            and ss.box(data, "jobs")["status"] == ss.DONE):
        lines.append(HISTORY_LINE)
    if support and support not in first:
        # Said once: the first-email line already carries the address.
        lines.append(f"For help, email {support}.")
    lines.append("You can close this window.")
    return "\n".join(lines)


def _measure(rows: list, run_id: str = "") -> dict:
    """Machine, human and model seconds, each from its own paired events."""
    if run_id:
        rows = [r for r in rows if r.get("run_id") == run_id] or rows

    def t(r):
        try:
            v = datetime.fromisoformat(r["ts"])
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            return None

    human = model = machine = 0.0
    open_prompts, open_jobs, open_steps, open_dispatch = {}, {}, {}, None
    steps_seen, failures = [], []
    start = end = None
    for r in rows:
        ts = t(r)
        if ts is None:
            continue
        ev = r.get("event")
        if ev == "start" and start is None:
            start = ts
        elif ev == "done":
            end = ts
        elif ev == "prompt_shown":
            open_prompts[r.get("prompt")] = ts
        elif ev == "answer_received":
            shown = open_prompts.pop(r.get("prompt"), None)
            if shown:
                human += (ts - shown).total_seconds()
        elif ev == "signin_failed":
            # auth_bootstrap's own row; the time it took is already inside
            # the prompt_shown/answer_received pair around the sign-in.
            failures.append(r.get("detail", ""))
        elif ev == "job_begin":
            open_jobs[(r.get("step"), r.get("detail"))] = (ts, r.get("class"))
        elif ev == "job_end":
            began = open_jobs.pop((r.get("step"), r.get("detail")), None)
            if began:
                if began[1] == "model":
                    model += (ts - began[0]).total_seconds()
                else:
                    machine += (ts - began[0]).total_seconds()
        elif ev == "dispatch":
            if r.get("phase") == "begin":
                open_dispatch = ts
            elif r.get("phase") == "end" and open_dispatch:
                model += (ts - open_dispatch).total_seconds()
                open_dispatch = None
        elif ev == "step_begin":
            open_steps[r.get("step")] = ts
            if r.get("step") not in steps_seen:
                steps_seen.append(r.get("step"))
        elif ev == "step_end":
            # The step set, not the time: a step span contains the sign-in
            # window and the waits, which belong to the other classes.
            open_steps.pop(r.get("step"), None)
    total = (end - start).total_seconds() if start and end else None
    return {"machine_s": round(machine, 1), "human_s": round(human, 1),
            "model_s": round(model, 1), "total_s": None if total is None else round(total, 1),
            "steps": steps_seen, "signin_failures": failures,
            "steps_run": [s for s in steps_seen if s != "prepare"]}


def build_report(data: dict | None, rows: list | None = None, run_id: str = "") -> dict:
    rows = ss.read_ledger() if rows is None else rows
    run_id = run_id or (data or {}).get("run_id", "")
    m = _measure(rows, run_id)
    facts = {}
    for key, fn in checks().items():
        try:
            status, detail = fn()
        except Exception:                                       # noqa: BLE001
            status, detail = None, ""
        facts[key] = {"status": status, "detail": detail}
    boxes = {b["key"]: b["status"] for b in (data or {}).get("boxes", [])} if data else {}
    complete = bool(data) and ss.resolved(data) and not (data or {}).get("degraded")
    total = m["total_s"]
    under = total is not None and total <= TARGET_SECONDS
    return {"run_id": run_id, "complete": complete,
            "degraded": (data or {}).get("degraded"),
            "total": _mmss(total or 0) if total is not None else "",
            "target": _mmss(TARGET_SECONDS),
            "machine": _mmss(m["machine_s"]), "human": _mmss(m["human_s"]),
            "model": _mmss(m["model_s"]), "steps": m["steps_run"],
            "boxes": boxes, "facts": facts,
            "line": (f"TOTAL {_mmss(total or 0)} of {_mmss(TARGET_SECONDS)}"
                     + (" DEGRADED" if (data or {}).get("degraded") else "")
                     + ("" if complete else " INCOMPLETE")),
            "ok": complete and under}


def finish(data: dict | None = None) -> dict:
    data = data or _load_or_new()
    _verify(data)
    if data.get("degraded") or not ss.resolved(data):
        ss.save(data)
        _log(data, "done", **{"class": "machine", "detail": "incomplete"})
        report = build_report(data)
        text = closing_text(data, total="")
        return {"kind": "done", "complete": False, "degraded": data.get("degraded"),
                "report": report, "closing_text": text, "elapsed": _elapsed(data)}
    _log(data, "done", **{"class": "machine", "detail": "complete"})
    report = build_report(data)
    text = closing_text(data, total=report["total"])
    ss.write_stamp({"run_id": data["run_id"], "plugin_version": data.get("plugin_version", ""),
                    "total": report["total"], "report": report,
                    "answers": {k: v for k, v in (data.get("answers") or {}).items()
                                if k in ("notetaker", "schedule", "skipped", "declined_deals",
                                         "declined_headings")},
                    "boxes": {b["key"]: b["status"] for b in data["boxes"]}})
    ss.remove()
    _nudge_job("remove")
    _keep_awake("stop")
    return {"kind": "done", "complete": True, "report": report, "closing_text": text,
            "elapsed": _elapsed(data)}


def cmd_finish(args) -> int:
    out = finish()
    _emit(out)
    return 0 if out.get("complete") else 1


def cmd_report(args) -> int:
    rows = ss.read_ledger(Path(args.ledger)) if args.ledger else None
    data = ss.load()
    run_id = ""
    if data is None:
        stamp = ss.read_stamp() or {}
        run_id = stamp.get("run_id", "")
        if stamp:
            data = {"run_id": run_id, "boxes": [{"key": k, "status": v} for k, v in
                                                 (stamp.get("boxes") or {}).items()],
                    "degraded": None, "answers": stamp.get("answers", {})}
            if not data["boxes"]:
                data["boxes"] = [{"key": k, "status": ss.DONE} for k in ss.KEYS]
    if args.ledger and rows:
        run_id = run_id or next((r.get("run_id") for r in rows if r.get("run_id")), "")
    report = build_report(data, rows, run_id)
    _emit(report)
    return 0 if report["ok"] else 1


def skip_box(key: str) -> dict:
    """The in-command "skip this step" answer: a box the client may leave
    undone is marked skipped and the install completes when that was the
    last open one. `accounts` and `vault` refuse (ChecklistError)."""
    data = ss.load()
    if data is None:
        return {"ok": False, "error": "no install in progress"}
    if key not in ss.KEYS:
        return {"ok": False, "error": f"unknown box {key}"}
    try:
        ss.set_status(data, key, ss.SKIPPED, "skipped by you")
    except ss.ChecklistError as exc:
        return {"ok": False, "error": str(exc), "box": key}
    ans = _answers(data)
    ans.setdefault("skipped", [])
    if key not in ans["skipped"]:
        ans["skipped"].append(key)
    ss.save(data)
    _log(data, "answer_received", step=key, prompt=f"skip:{key}", **{"class": "human"})
    out = {"ok": True, "box": key, "open": [b["key"] for b in ss.open_boxes(data)],
           "line": ss.one_line(data)}
    if ss.resolved(data) and not data.get("degraded"):
        out["finish"] = finish(data)
    return out


def cmd_skip(args) -> int:
    out = skip_box(args.box)
    _emit(out)
    return 0 if out.get("ok") else 1


def cmd_status(args) -> int:
    data = ss.load()
    if data is None:
        stamp = ss.read_stamp()
        return _emit({"exists": False, "stamp": stamp is not None,
                      "line": "already installed" if stamp else "not installed"})
    return _emit({"exists": True, "run_id": data["run_id"], "lines": ss.tick_list(data),
                  "open": [b["key"] for b in ss.open_boxes(data)], "total": len(ss.KEYS),
                  "line": ss.one_line(data), "degraded": data.get("degraded"),
                  "elapsed": _elapsed(data)})


def cmd_reset(args) -> int:
    had = {"checklist": ss.remove(), "stamp": ss.remove_stamp()}
    for name in ("adopted.json", "live-check.json"):
        try:
            (ss.setup_dir() / name).unlink()
        except OSError:
            pass
    nudge = _nudge_job("remove")
    _keep_awake("stop")
    import host
    # The client's own leftovers: the managed block in Codex's config, the
    # host record, and the command that removes the plugin itself (which a
    # script must not run for the person; the skill shows it).
    codex_block = host.remove_codex_block() if host.is_codex() else False
    try:
        host.host_file().unlink()
        host_file = True
    except OSError:
        host_file = False
    return _emit({"ok": True, "removed": had, "nudge_job": nudge.get("ok", False),
                  "codex_block": codex_block, "host_file": host_file,
                  "plugin_remove_command": host.plugin_remove_command()})


# ── verify-stamp ─────────────────────────────────────────────────────────────

def cmd_remove_plugin(args) -> int:
    """Run the client's own remove command (`codex plugin remove` or `claude
    plugin uninstall`). The last thing the uninstall skill runs under Codex;
    fails open with the command named, so the person can run it by hand."""
    import host
    command = host.plugin_remove_command()
    try:
        argv = host.plugin_remove_argv(host.bin())
    except FileNotFoundError as exc:
        return _emit({"ok": False, "command": command, "error": f"{type(exc).__name__}: {exc}"[:300]})
    try:
        res = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120, stdin=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return _emit({"ok": False, "command": command, "error": f"{type(exc).__name__}: {exc}"[:300]})
    out = ((res.stdout or "") + (res.stderr or "")).strip()[-600:]
    return _emit({"ok": res.returncode == 0, "command": command, "argv": argv,
                  "returncode": res.returncode, "output": out})


def _live_check_path() -> Path:
    return ss.setup_dir() / "live-check.json"


WORKS, REJECTED, UNKNOWN = "works", "rejected", "unknown"

# The provider ANSWERED and refused the stored sign-in. Everything else that
# goes wrong (no network, a timeout, a proxy, a 5xx, a crash) is no answer.
_REJECTED_RE = re.compile(
    r"invalid_grant|invalid_client|unauthorized_client|deleted_client"
    r"|AADSTS(?:50173|700082|70008)\b|Token has been expired or revoked"
    r"|Missing refresh token", re.I)


def refresh_state(provider: str, label: str) -> str:
    """One real token refresh through the account's own client, read three
    ways: WORKS, REJECTED (the provider said no), UNKNOWN (nobody answered).
    A laptop on a plane is UNKNOWN, never "sign in again"."""
    script = "google_client.py" if provider == "google" else "microsoft_client.py"
    try:
        r = _run_script(script, ["--label", label], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if r["rc"] == 0:
        return WORKS
    text = r.get("text") or ""
    try:
        import failure_class
        if failure_class.classify(text) == "network":
            return UNKNOWN
    except Exception:                                           # noqa: BLE001
        pass
    return REJECTED if _REJECTED_RE.search(text) else UNKNOWN


def _first_account() -> tuple:
    for a in _read_config().get("accounts") or []:
        if a.get("label"):
            return a.get("provider", "google"), a["label"]
    for k in ss._env_keys():
        if k.startswith("GOOGLE_REFRESH_TOKEN_"):
            return "google", k[len("GOOGLE_REFRESH_TOKEN_"):].lower()
        if k.startswith("MS_GRAPH_REFRESH_TOKEN_"):
            return "microsoft", k[len("MS_GRAPH_REFRESH_TOKEN_"):].lower()
    return "", ""


_LISTING = {}


def _scheduler_listing(platform: str | None = None):
    """The names the OS scheduler holds, from ONE call, memoised for the
    life of the process. None when the scheduler could not be read."""
    platform = platform or sys.platform
    if platform in _LISTING:
        return _LISTING[platform]
    names = None
    try:
        if platform == "darwin":
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=10,
                               stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            if r.returncode == 0:
                names = {line.split("\t")[-1].strip() for line in r.stdout.splitlines()}
        elif platform == "win32":
            r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=10,
                               stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            if r.returncode == 0:
                names = {line.split(",")[0].strip().strip('"').lstrip("\\")
                         for line in r.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        names = None
    _LISTING[platform] = names
    return names


def check_jobs_one_listing() -> tuple:
    """Every enabled registry row registered, by exact name, from one
    scheduler call (the every-command check must stay cheap)."""
    if not ss.scheduler_allowed():
        return None, "the scheduler belongs to another install"
    try:
        import job_registry
        enabled = [j for j in job_registry.jobs() if j.is_enabled()]
    except Exception as exc:                                    # noqa: BLE001
        return None, f"could not read the job table ({type(exc).__name__})"
    if not enabled:
        return None, "no jobs enabled"
    names = _scheduler_listing()
    if names is None:
        return None, "could not read the scheduler"
    missing = [j.key for j in enabled
               if (j.mac_label if sys.platform == "darwin" else j.win_task) not in names]
    if missing:
        return ss.OPEN, f"{len(missing)} of {len(enabled)} jobs not loaded"
    return ss.DONE, f"{len(enabled)} jobs loaded"


def quiet_checks() -> dict:
    """The every-command subset: file reads plus one scheduler listing. No
    interpreter spawn, no CLI, no network (plugin and Python are witnessed by
    the fact that this code is running from them)."""
    return {"credentials": ss._check_credentials, "accounts": ss._check_accounts,
            "vault": ss._check_vault, "jobs": check_jobs_one_listing}


def verify_stamp(live: bool = False, now: datetime | None = None,
                 local_checks: dict | None = None) -> dict:
    """Disk is truth; the stamp is a hint. Local checks only unless `live`,
    which refreshes one token at most once a day."""
    stamp = ss.read_stamp()
    if stamp is None:
        return {"ok": False, "already_installed": False, "line": "not installed"}
    if ss.load() is not None:
        data = ss.load()
        return {"ok": False, "already_installed": False, "line": ss.one_line(data)}
    data = ss.new(stamp.get("run_id", ""), sys.platform, stamp.get("plugin_version", ""))
    data["adopted_from"] = "stamp"
    for key, status in (stamp.get("boxes") or {}).items():
        try:
            ss.set_status(data, key, status if status in ss.STATUSES else ss.DONE)
        except (KeyError, ss.ChecklistError):
            pass
    for b in data["boxes"]:
        if b["status"] == ss.OPEN:
            ss.set_status(data, b["key"], ss.DONE, "per the stamp")
    chk = local_checks if local_checks is not None else \
        {k: v for k, v in checks().items() if k != "briefings"}
    ss.verify(data, checks=chk)
    live_done = False
    now = now or _now()
    if live:
        last = {}
        try:
            last = json.loads(_live_check_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        due = True
        try:
            when = datetime.fromisoformat(last.get("when", ""))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            due = (now - when) >= timedelta(hours=24)
        except ValueError:
            due = True
        if due:
            provider, label = _first_account()
            state = refresh_state(provider, label) if provider else UNKNOWN
            if state != UNKNOWN:
                # Only an answer counts as the day's check. With no answer
                # nothing is stamped, so the next command tries again, and
                # nothing is reopened: the local check above still decides.
                live_done = True
                _live_check_path().parent.mkdir(parents=True, exist_ok=True)
                _live_check_path().write_text(
                    json.dumps({"when": _iso(now), "ok": state == WORKS}), encoding="utf-8")
            if state == REJECTED:
                ss.set_status(data, "accounts", ss.OPEN, "sign-in no longer works")
    broken = [b["key"] for b in ss.open_boxes(data)]
    if broken:
        data["answers"] = dict(stamp.get("answers") or {})
        data["nudges"]["stall_started"] = _iso(now)
        # touch=False: `updated` must stay before `stall_started`, or the
        # first nudge run reads the reopen itself as the client resuming.
        ss.save(data, touch=False)
        _nudge_job("install")
        line = ss.one_line(data)
        return {"ok": False, "already_installed": False, "reopened": broken,
                "line": line, "live": live_done}
    return {"ok": True, "already_installed": True, "line": "already installed",
            "run_id": stamp.get("run_id", ""), "report": stamp.get("report", {}),
            "live": live_done}


def cmd_verify_stamp(args) -> int:
    out = verify_stamp(live=args.live)
    _emit(out)
    return 0 if out.get("ok") else 1


# ── adopt ────────────────────────────────────────────────────────────────────

def _adopted_path() -> Path:
    return ss.setup_dir() / "adopted.json"


def _count_sources(cfg: dict, vault: Path) -> int:
    rel = (cfg.get("obsidian") or {}).get("sources_relpath", "wiki/sources")
    try:
        return sum(1 for p in (vault / rel).iterdir() if p.suffix == ".md")
    except OSError:
        return 0


def _notetaker_keyed() -> bool:
    return any(k in NOTETAKER_ENV.values() for k in ss._env_keys())


def adopt(quiet: bool = False, force: bool = False) -> dict:
    version = _plugin_version()
    try:
        prior = json.loads(_adopted_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    if prior.get("version") == version and not force:
        return {"ok": True, "adopted": False, "reason": "already adopted this version"}
    if ss.load() is not None:
        return {"ok": True, "adopted": False, "reason": "an install is in progress"}
    if ss.read_stamp() is not None and not force:
        _adopted_path().parent.mkdir(parents=True, exist_ok=True)
        _adopted_path().write_text(json.dumps({"version": version, "when": _iso()}),
                                   encoding="utf-8")
        return {"ok": True, "adopted": False, "reason": "already stamped complete"}
    cfg = _read_config()
    vault = _vault_path()
    if not cfg or not vault:
        return {"ok": True, "adopted": False, "reason": "nothing installed to adopt"}
    warnings = []
    ck = _try_import("companion_key")
    if ck is not None and hasattr(ck, "discover"):
        # A key file in Downloads (or already in the private folder) is
        # installed by companion_key, which owns the credential rules: a
        # missing credential is written, a differing one with a working
        # sign-in is kept with a warning, a differing one that no longer
        # refreshes is replaced.
        try:
            found = ck.discover()
            path = (found[0] if isinstance(found, (list, tuple)) else found) if found else None
            if path:
                res = ck.install_all(Path(path))
                warnings += list(res.get("warnings") or [])
                if not res.get("ok"):
                    warnings.append(f"key file not installed: {res.get('error', '')}")
        except Exception as exc:                                # noqa: BLE001
            warnings.append(f"key file not read ({type(exc).__name__})")
    data = ss.new(f"adopt-{_now().strftime('%Y%m%d-%H%M%S')}", sys.platform, version)
    data["adopted_from"] = prior.get("version") or "pre-checklist install"
    ss.verify(data, checks=checks())
    pages = _count_sources(cfg, vault)
    if pages:
        ss.set_status(data, "meetings", ss.DONE, f"{pages} pages filed")
    elif _notetaker_keyed():
        ss.set_status(data, "meetings", ss.OPEN, "no meeting filed yet")
        finish_machine_boxes(data)
    else:
        ss.set_status(data, "meetings", ss.NOT_APPLICABLE, "no notetaker")
    headings = _seeded_headings()
    if headings:
        ss.set_status(data, "deals", ss.DONE, f"{len(headings)} deals tracked")
    else:
        ss.set_status(data, "deals", ss.OPEN, "no deals tracked yet")
    if ss.box(data, "jobs")["status"] == ss.OPEN:
        try:
            status, detail = checks()["jobs"]()
        except Exception:                                       # noqa: BLE001
            status, detail = None, ""
        if status is None and detail == "no jobs enabled":
            ss.set_status(data, "jobs", ss.SKIPPED, detail)
    if ss.box(data, "briefings")["status"] == ss.OPEN:
        ss.set_status(data, "briefings", ss.NOT_APPLICABLE,
                      "arrive with the next scheduled briefing")
    open_keys = [b["key"] for b in ss.open_boxes(data)]
    _adopted_path().parent.mkdir(parents=True, exist_ok=True)
    _adopted_path().write_text(json.dumps({"version": version, "when": _iso(),
                                           "open": open_keys}), encoding="utf-8")
    if not open_keys:
        ss.write_stamp({"run_id": data["run_id"], "plugin_version": version,
                        "total": "", "adopted": True,
                        "boxes": {b["key"]: b["status"] for b in data["boxes"]}})
        return {"ok": True, "adopted": True, "open": [], "stamp": True, "warnings": warnings}
    data["nudges"]["stall_started"] = _iso()
    ss.save(data)
    nudge = _nudge_job("install")
    return {"ok": True, "adopted": True, "open": open_keys, "stamp": False,
            "line": ss.one_line(data), "nudge_job": nudge.get("ok", False),
            "warnings": warnings}


def stamp_check_quietly(now: datetime | None = None) -> dict | None:
    """The stamp re-check behind every command, from the config_loader choke
    point. On a stamped-complete install it ALWAYS runs the local check
    (credentials, token present, vault resolves, jobs registered: file reads
    plus one scheduler listing, no network), and on top of it one real token
    refresh at most once per 24 h (`verify_stamp` stamps live-check.json). A
    broken box reopens the checklist and installs the nudge job. Skips at
    once with no stamp or an open checklist. Runs under VAN_GOGH_UNATTENDED
    too (a scheduled briefing is exactly when a dead sign-in or an unloaded
    job shows). Never raises, never prints."""
    try:
        if not user_state._running_in_managed_venv():
            return None
        if ss.read_stamp() is None or ss.load() is not None:
            return None
        return verify_stamp(live=True, now=now or _now(), local_checks=quiet_checks())
    except Exception:                                           # noqa: BLE001
        return None


def adopt_quietly() -> None:
    """The import-time hook: a file check, then adopt at most once per
    plugin version. Never raises, never prints."""
    try:
        if not user_state._running_in_managed_venv():
            return
        try:
            prior = json.loads(_adopted_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
        if prior.get("version") == _plugin_version():
            return
        adopt(quiet=True)
    except Exception:                                           # noqa: BLE001
        return


def cmd_adopt(args) -> int:
    return _emit(adopt(force=args.force))


# ── nudges (another lane's module) and the hidden job wrapper ────────────────

def _cmd_nudge_passthrough(name: str, rest: list) -> int:
    """`nudge [...]` is setup_nudge's `run`; `nudge-job X` is its `job X`."""
    mod = _try_import("setup_nudge")
    if mod is None:
        return _emit({"ok": False, "error": "nudges not installed"})
    argv = ["run", *rest] if name == "nudge" else ["job", *rest]
    rc = mod.main(argv)
    return rc or 0


def _cmd_job(args) -> int:
    jobs = ss.setup_dir() / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / f"{args.name}.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    started = _iso()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        result = {"name": args.name, "rc": r.returncode, "started": started,
                  "finished": _iso(), "stdout_tail": (r.stdout or "")[-2000:],
                  "stderr_tail": (r.stderr or "")[-2000:]}
    except (OSError, subprocess.SubprocessError) as exc:
        result = {"name": args.name, "rc": 1, "started": started, "finished": _iso(),
                  "error": type(exc).__name__}
    (jobs / f"{args.name}.result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["rc"] == 0 else 1


def spawn_job(name: str, cmd: list) -> int:
    """Run `cmd` detached through the `_job` wrapper. Returns the pid."""
    return _spawn_detached([_python(), str(APP_DIR / "setup_flow.py"), "_job", name, "--", *cmd],
                           f"setup-job-{name}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("nudge", "nudge-job"):
        return _cmd_nudge_passthrough(argv[0], argv[1:])
    parser = argparse.ArgumentParser(description="Van Gogh install orchestrator.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--run-id", default="")
    p.add_argument("--platform", default="")
    p = sub.add_parser("next")
    p.add_argument("--answer-stdin", action="store_true")
    p.add_argument("--answer-file", default="")
    sub.add_parser("status")
    p = sub.add_parser("report")
    p.add_argument("--ledger", default="")
    sub.add_parser("finish")
    sub.add_parser("reset")
    sub.add_parser("remove-plugin")
    p = sub.add_parser("verify-stamp")
    p.add_argument("--live", action="store_true")
    p = sub.add_parser("adopt")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("skip")
    p.add_argument("--box", required=True)
    p = sub.add_parser("_job", help=argparse.SUPPRESS)
    p.add_argument("name")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p = sub.add_parser("_bell", help=argparse.SUPPRESS)
    p.add_argument("--prompt", required=True)
    p.add_argument("--after", type=int, default=BELL_AFTER_S)
    sub.add_parser("_keepawake", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return {"prepare": cmd_prepare, "next": cmd_next, "status": cmd_status,
            "report": cmd_report, "finish": cmd_finish, "reset": cmd_reset,
            "remove-plugin": cmd_remove_plugin,
            "verify-stamp": cmd_verify_stamp, "adopt": cmd_adopt, "skip": cmd_skip,
            "_job": _cmd_job, "_bell": _cmd_bell,
            "_keepawake": _cmd_keepawake}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
