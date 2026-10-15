#!/usr/bin/env python3
"""setup_nudge.py: the reminder for an install that stopped early.

Nothing else in the product can reach a client whose install stalled: every
other notice surface needs a vault and config, which is box 5 of 9. This is
the one transient job, installed at the credentials step, needing only the
state dir, and it removes itself after the third reminder or when the install
finishes. It is deliberately NOT a `job_registry` row: the registry is for
jobs that exist once a vault does.

    setup_nudge.py run [--maybe] [--now ISO] [--dry-run]
        One fire. Silent (exit 0, empty stdout and stderr) when there is no
        checklist, or when `--maybe` is given from a hook inside an unattended
        run. Otherwise: finish the boxes that need no human (through
        `setup_flow.finish_machine_boxes` when that module exists), re-verify
        from disk, and if a box a human must tick remains, apply the cadence
        and send.
    setup_nudge.py job install|remove|status
        The standalone daily job (launchd on macOS, Task Scheduler on
        Windows), 09:00 local, logs under `setup_state.logs_dir()`.
    setup_nudge.py stop
        Stop the email for good; the hook line stays. Keeps the checklist.
    setup_nudge.py line
        The one line every surface prints.

The cadence, exactly: `stall_started` is set the first time a run sees an
open human box after at least 30 minutes with no checklist update, so an
install in progress is never nudged. Sends happen when the days since
`stall_started` reach 1, 3 and 7 (first, second, third), never more than one
in 24 hours however many times the job fires, and never after the third: that
run writes a "went quiet" row and removes the job. A checklist updated after
`stall_started` means the client resumed: the stall clears and a later stall
starts a fresh cadence. `stop_reminders` ends the email for good.

"Can I send?" means one account in the state `.env` whose refresh works. Then
the email goes from that account to its own address through
`send_email.send_email`. With no working sign-in (the broken sign-in is
usually the open box) a desktop notification goes instead. The body carries
one action, where to type it, who must act when it is not the client, and the
help address. It carries no mail content, no secrets, and no list of
everything unfinished.

The email is the one message this product sends without an explicit
instruction. It goes to the client only, from their own account, at most three
times per stall. The carve-out is documented in CLAUDE.md.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import host
import setup_state  # noqa: E402
import user_state  # noqa: E402
from platform_compat import NO_WINDOW  # noqa: E402

MAC_LABEL = "com.monet.setup-nudge"
WIN_TASK = "VanGogh-setup-nudge"
SUBJECT = "Two minutes to finish setting up Van Gogh"
HOUR, MINUTE = 9, 0
LOG_NAME = "setup-nudge.log"
ERR_NAME = "setup-nudge.err"

STALL_AFTER = timedelta(minutes=30)
SEND_AFTER_DAYS = (1, 3, 7)
MAX_SENDS = len(SEND_AFTER_DAYS)
MIN_GAP = timedelta(hours=24)
RUNS_KEPT = 200
UNATTENDED_VARS = ("VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED")

RESUME_COMMAND = setup_state.RESUME_COMMAND
ACTIONS = {b[0]: b[3] for b in setup_state.BOXES}

# A machine box left open with one of these in its detail is a box the machine
# cannot finish on its own: a key or a sign-in a person has to renew.
_HUMAN_FIX_RE = re.compile(
    r"sign[- ]?in|credential|api key|\bkey\b|expired|unauthori[sz]ed|forbidden"
    r"|\b401\b|\b403\b|invalid", re.I)
_ADMIN_RE = re.compile(
    r"admin[ _-]?consent|admin approval|needs admin|AADSTS65001|AADSTS90094", re.I)
_UNVERIFIED_RE = re.compile(
    r"unverified|access_denied|not a test user|testing mode"
    r"|has not completed the Google verification", re.I)

_NUDGE_DEFAULTS = {"stall_started": "", "sent": [], "runs": [],
                   "stop_reminders": False, "quiet": False,
                   "stall_sends": 0, "seen_updated": ""}


# ── time ─────────────────────────────────────────────────────────────────────

def _local_tz():
    return datetime.now(timezone.utc).astimezone().tzinfo


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt


def _now(given=None) -> datetime:
    if given is None:
        return datetime.now(timezone.utc).astimezone()
    dt = _parse(given)
    if dt is None:
        raise ValueError(f"not an ISO time: {given!r}")
    return dt


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def unattended() -> bool:
    return any(os.environ.get(v) for v in UNATTENDED_VARS)


# ── what to nudge about ──────────────────────────────────────────────────────

def nudge_targets(data: dict) -> list:
    """The boxes a person has to act on, in checklist order.

    Every unresolved human box, plus a machine box the machine gave up on for
    a reason only a person can fix (a notetaker key that no longer signs in).
    A machine box that is merely pending is a job still working, not a nudge.
    """
    out = []
    for b in data["boxes"]:
        if b["status"] in setup_state.RESOLVED:
            continue
        if b["needs"] == "human":
            out.append(b)
        elif b["status"] == setup_state.OPEN and _HUMAN_FIX_RE.search(b.get("detail") or ""):
            out.append(b)
    return out


def who_must_act(box: dict) -> str:
    """One plain sentence when the person who must act is not the client."""
    detail = box.get("detail") or ""
    if box["key"] == "accounts":
        if _ADMIN_RE.search(detail):
            return ("Your IT admin must approve Van Gogh for your work account "
                    "before you can sign in. Forwarding this email to them is enough.")
        if _UNVERIFIED_RE.search(detail):
            return ("The Van Gogh team must add your account to the app before "
                    "you can sign in. Nothing is needed from you until they confirm.")
    if box["key"] == "meetings" and _HUMAN_FIX_RE.search(detail):
        return ("Your notetaker sign-in is not working, so your meetings could "
                "not be filed. A fresh key from your notetaker fixes it.")
    return ""


def support_email() -> str:
    """The help address: the key file's `support_email`, else the config's
    `support.team_email` when a vault exists, else "" (the line is omitted)."""
    for path in sorted(setup_state.keyfile_dir().glob("van-gogh-key*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            value = str(doc.get("support_email") or "").strip()
            if value:
                return value
    try:
        if not user_state.pointer_file().exists():
            return ""
        import config_loader
        return config_loader.support_team_email()
    except Exception:                                           # noqa: BLE001
        return ""


def body_for(data: dict, help_address: str = "") -> str:
    """The email body. One action, where to type it, who must act, the help
    address. Never a list of everything unfinished, never a secret."""
    targets = nudge_targets(data)
    first = targets[0] if targets else (setup_state.first_open(data) or data["boxes"][0])
    lines = [
        "Hello,",
        "",
        "Van Gogh is not finished setting up on this computer.",
        "",
        f"The next step: {ACTIONS[first['key']]}.",
        "",
        f"Open {host.host_word()} and type {RESUME_COMMAND}",
        "",
    ]
    seen = set()
    for b in targets:
        who = who_must_act(b)
        if who and who not in seen:
            seen.add(who)
            lines += [who, ""]
    if help_address:
        lines += [f"Need a hand? Write to {help_address}.", ""]
    lines.append("Van Gogh")
    body = "\n".join(lines) + "\n"
    assert chr(0x2014) not in body and chr(0x2013) not in body
    return body


# ── can I send? ──────────────────────────────────────────────────────────────

def _account_labels() -> list:
    out = []
    for key in setup_state._env_keys():
        if key.startswith("GOOGLE_REFRESH_TOKEN_"):
            out.append(("google", key[len("GOOGLE_REFRESH_TOKEN_"):]))
        elif key.startswith("MS_GRAPH_REFRESH_TOKEN_"):
            out.append(("microsoft", key[len("MS_GRAPH_REFRESH_TOKEN_"):]))
    return out


def _probe(provider: str, label: str) -> str:
    """The cheapest real call: the signed-in address. Raises when the sign-in
    does not work, which is the answer "no"."""
    user_state.load_env()
    if provider == "google":
        from google_client import google_client
        profile = google_client(label).gmail.users().getProfile(userId="me").execute()
        return str(profile.get("emailAddress") or "").strip()
    from microsoft_client import microsoft_client
    me = microsoft_client(label).get("/me")
    return str(me.get("mail") or me.get("userPrincipalName") or "").strip()


def _working_account(probe=None) -> dict | None:
    """One account whose refresh works, as `send_email` wants it, or None.

    Exceptions are swallowed on purpose and their text is never kept: a
    refresh error quotes the request it failed on.
    """
    probe = probe or _probe
    for provider, label in _account_labels():
        try:
            email = probe(provider, label)
        except Exception:                                       # noqa: BLE001
            continue
        if email:
            return {"provider": provider, "label": label, "email": email}
    return None


def _send(account: dict, body: str) -> str:
    """Send to the client's own address. Returns the provider's message id,
    or "" when the provider returns none (Graph answers 202 with no body)."""
    import send_email
    resp = send_email.send_email(account, account["email"], SUBJECT, body)
    if isinstance(resp, dict):
        return str(resp.get("id") or "")
    return ""


def _ps_text(text: str) -> str:
    return text.replace("'", "''")


def _notify_desktop(line: str, platform: str | None = None) -> bool:
    """One desktop notification. Fails open: False, never an exception."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            safe = line.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{safe}" with title "Van Gogh"'
            return _run(["osascript", "-e", script]).returncode == 0
        if platform == "win32":
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
                "$t = [Windows.UI.Notifications.ToastNotificationManager]::"
                "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$n = $t.GetElementsByTagName('text'); "
                "$n.Item(0).AppendChild($t.CreateTextNode('Van Gogh')) | Out-Null; "
                f"$n.Item(1).AppendChild($t.CreateTextNode('{_ps_text(line)}')) | Out-Null; "
                "[Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('Van Gogh').Show("
                "[Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            return _run(["powershell", "-NoProfile", "-Command", ps]).returncode == 0
        if shutil.which("notify-send"):
            return _run(["notify-send", "Van Gogh", line]).returncode == 0
    except OSError:
        return False
    return False


# ── one fire ─────────────────────────────────────────────────────────────────

def _finish_machine(data: dict) -> str:
    """Finish the boxes that need no human, through the orchestrator when it
    is importable. A note for the run row either way."""
    try:
        import setup_flow
    except Exception:                                           # noqa: BLE001
        return "setup_flow unavailable, machine boxes untouched"
    fn = getattr(setup_flow, "finish_machine_boxes", None)
    if fn is None:
        return "setup_flow has no finish_machine_boxes, machine boxes untouched"
    try:
        done = fn(data)
    except Exception as exc:                                    # noqa: BLE001
        return f"finish_machine_boxes failed ({type(exc).__name__})"
    return f"{len(done or [])} machine boxes finished"


def _last_send(nudges: dict) -> datetime | None:
    rows = nudges.get("sent") or []
    return _parse(rows[-1].get("when")) if rows else None


def run_once(now=None, dry_run: bool = False, platform: str | None = None,
             probe=None, notify=None, checks: dict | None = None) -> dict | None:
    """One fire of the nudge. None means there was nothing to say (no
    checklist); the job, if still installed, is removed on the way out.

    `probe`, `notify` and `checks` exist so a test can run thirty simulated
    days with no network, no scheduler and no notification centre.
    """
    now = _now(now)
    platform = platform or sys.platform
    data = setup_state.load()
    if data is None:
        if setup_state.setup_dir().exists():
            setup_state.log_event("nudge", sent=0, reason="ran, 0 to send")
            if not dry_run:
                job_remove(platform, quiet=True)
        return None

    nudges = data.setdefault("nudges", {})
    for key, default in _NUDGE_DEFAULTS.items():
        nudges.setdefault(key, json.loads(json.dumps(default)))

    # The client resumed: the checklist moved after the stall began, and not
    # by this job's own hand. Clear the stall; a later one starts fresh.
    updated = _parse(data.get("updated"))
    stall = _parse(nudges.get("stall_started"))
    resumed = False
    if (stall and updated and updated > stall
            and data.get("updated") != nudges.get("seen_updated")):
        nudges["stall_started"] = ""
        nudges["stall_sends"] = 0
        stall = None
        resumed = True

    machine_note = _finish_machine(data)
    setup_state.verify(data, checks=checks)
    targets = nudge_targets(data)

    sent = 0
    channel = ""
    message_id = ""
    box_key = targets[0]["key"] if targets else ""
    body = ""
    went_quiet = False

    if setup_state.resolved(data):
        reason = "ran, 0 to send (install complete)"
    elif not targets:
        reason = "ran, 0 to send (machine steps still running)"
    elif nudges.get("stop_reminders"):
        reason = "ran, 0 to send (reminders stopped)"
    elif nudges.get("quiet"):
        reason = "ran, 0 to send (went quiet)"
    else:
        if stall is None:
            if updated is None or now - updated >= STALL_AFTER:
                nudges["stall_started"] = _iso(now)
                stall = now
            else:
                reason = "ran, 0 to send (install in progress)"
        if stall is not None:
            n = int(nudges.get("stall_sends") or 0)
            days = (now - stall).total_seconds() / 86400
            last = _last_send(nudges)
            if n >= MAX_SENDS:
                reason = "ran, 0 to send (three already sent)"
            elif days < SEND_AFTER_DAYS[n]:
                reason = (f"ran, 0 to send (day {int(days)} of the stall, "
                          f"next on day {SEND_AFTER_DAYS[n]})")
            elif last is not None and now - last < MIN_GAP:
                reason = "ran, 0 to send (sent within 24 h)"
            else:
                account = _working_account(probe)
                help_address = support_email()
                body = body_for(data, help_address)
                if dry_run:
                    reason = (f"would send by {'email' if account else 'desktop'} "
                              f"(nudge {n + 1} of {MAX_SENDS})")
                elif account:
                    try:
                        message_id = _send(account, body)
                        channel = "email"
                    except Exception as exc:                    # noqa: BLE001
                        reason = f"ran, 0 to send (send failed: {type(exc).__name__})"
                else:
                    line = setup_state.one_line(data)
                    if (notify or _notify_desktop)(line, platform):
                        channel = "desktop"
                    else:
                        reason = ("ran, 0 to send (no working sign-in and no "
                                  "desktop notification)")
                if channel:
                    sent = 1
                    nudges["sent"].append({"when": _iso(now), "channel": channel,
                                           "message_id": message_id, "box": box_key})
                    nudges["stall_sends"] = n + 1
                    reason = f"sent by {channel} (nudge {n + 1} of {MAX_SENDS})"
                    went_quiet = n + 1 >= MAX_SENDS

    row = {"when": _iso(now), "sent": sent, "reason": reason, "machine": machine_note}
    if resumed:
        row["resumed"] = True
    result = {"sent": sent, "reason": reason, "channel": channel, "box": box_key,
              "message_id": message_id, "resumed": resumed, "machine": machine_note,
              "stall_started": nudges.get("stall_started", ""),
              "quiet": bool(nudges.get("quiet")) or went_quiet}
    if dry_run:
        result["body"] = body
        return result

    nudges["runs"].append(row)
    if went_quiet:
        nudges["quiet"] = True
        nudges["runs"].append({"when": _iso(now), "sent": 0, "reason": "went quiet"})
    nudges["runs"] = nudges["runs"][-RUNS_KEPT:]
    nudges["seen_updated"] = data.get("updated", "")
    setup_state.save(data, touch=False)
    setup_state.log_event("nudge", run_id=data.get("run_id", ""), sent=sent, reason=reason)
    if went_quiet:
        setup_state.log_event("nudge", run_id=data.get("run_id", ""), sent=0,
                              reason="went quiet")
        job_remove(platform, quiet=True)
    return result


def stop() -> dict:
    """No email ever again. The checklist and the hook line stay."""
    data = setup_state.load()
    if data is None:
        return {"ok": True, "stopped": False, "reason": "no checklist"}
    nudges = data.setdefault("nudges", {})
    for key, default in _NUDGE_DEFAULTS.items():
        nudges.setdefault(key, json.loads(json.dumps(default)))
    nudges["stop_reminders"] = True
    nudges["runs"].append({"when": _iso(_now()), "sent": 0, "reason": "reminders stopped"})
    setup_state.save(data, touch=False)
    job = job_remove(quiet=True)
    return {"ok": True, "stopped": True, "job": job.get("state", "")}


# ── the standalone job ───────────────────────────────────────────────────────

def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          creationflags=NO_WINDOW)


def plugin_root() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env)
    try:
        pointed = Path((user_state.state_dir() / "plugin-root")
                       .read_text(encoding="utf-8").strip())
        if pointed.exists():
            return pointed
    except OSError:
        pass
    return Path(__file__).resolve().parent.parent


def _python() -> str:
    venv = setup_state._venv_python()
    return str(venv) if venv.exists() else sys.executable


def _script() -> str:
    return str(plugin_root() / "app" / "setup_nudge.py")


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / f"{MAC_LABEL}.plist"


def _uid() -> int:
    return int(getattr(os, "getuid", lambda: 0)())


def plist_content() -> bytes:
    logs = setup_state.logs_dir()
    doc = {
        "Label": MAC_LABEL,
        "ProgramArguments": [_python(), _script(), "run"],
        "StartCalendarInterval": {"Hour": HOUR, "Minute": MINUTE},
        "RunAtLoad": False,
        "StandardOutPath": str(logs / LOG_NAME),
        "StandardErrorPath": str(logs / ERR_NAME),
    }
    env = {k: os.environ[k] for k in ("VAN_GOGH_STATE_DIR", "VAN_GOGH_SETUP_LOGS")
           if os.environ.get(k)}
    if env:
        doc["EnvironmentVariables"] = env
    return plistlib.dumps(doc)


def _win_inner() -> str:
    logs = setup_state.logs_dir()
    parts = []
    for key in ("VAN_GOGH_STATE_DIR", "VAN_GOGH_SETUP_LOGS"):
        if os.environ.get(key):
            parts.append(f"set {key}={os.environ[key]}&&")
    parts += [f'"{_python()}"', f'"{_script()}"', "run",
              f'1>> "{logs / LOG_NAME}"', f'2>> "{logs / ERR_NAME}"']
    return " ".join(parts)


def task_ps(vbs: Path) -> str:
    """Register the Windows task: daily 09:00, runs when missed, and on
    battery, since a laptop at a cafe is the stalled install's usual home."""
    import scheduler_setup
    q = scheduler_setup._ps_quote
    argument = f'//B //Nologo "{vbs}"'
    return (
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {HOUR}:{MINUTE:02d}am; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' "
        f"-Argument {q(argument)} -WorkingDirectory {q(str(plugin_root()))}; "
        f"Register-ScheduledTask -TaskName {q(WIN_TASK)} -Action $action "
        f"-Trigger $trigger -Settings $settings "
        f"-Description {q('Van Gogh: a reminder while setup is unfinished')} "
        "-Force | Out-Null"
    )


def job_status(platform: str | None = None) -> dict:
    platform = platform or sys.platform
    if platform == "darwin":
        plist = plist_path()
        loaded = MAC_LABEL in (_run(["launchctl", "list"]).stdout or "")
        state = ("loaded" if loaded else
                 "on disk, not loaded" if plist.exists() else "not installed")
        return {"name": MAC_LABEL, "state": state, "path": str(plist)}
    if platform == "win32":
        res = _run(["schtasks", "/query", "/tn", WIN_TASK])
        return {"name": WIN_TASK,
                "state": "installed" if res.returncode == 0 else "not installed",
                "path": ""}
    return {"name": "", "state": "unsupported", "path": ""}


def job_install(platform: str | None = None) -> dict:
    platform = platform or sys.platform
    logs = setup_state.logs_dir()
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if platform == "darwin":
        plist = plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plist_content())
        _run(["launchctl", "bootout", f"gui/{_uid()}/{MAC_LABEL}"])   # ignore
        res = _run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)])
        if res.returncode != 0:
            res = _run(["launchctl", "load", str(plist)])
        return {"ok": res.returncode == 0, **job_status(platform)}
    if platform == "win32":
        import scheduler_setup
        vbs = scheduler_setup._vbs_path(WIN_TASK)
        scheduler_setup._write_vbs(vbs, scheduler_setup._vbs_content(_win_inner()))
        res = _run(["powershell", "-NoProfile", "-Command", task_ps(vbs)])
        status = job_status(platform)
        return {"ok": res.returncode == 0 and status["state"] == "installed", **status}
    return {"ok": False, "name": "", "state": "unsupported", "path": ""}


def job_remove(platform: str | None = None, quiet: bool = False) -> dict:
    platform = platform or sys.platform
    if platform == "darwin":
        plist = plist_path()
        _run(["launchctl", "bootout", f"gui/{_uid()}/{MAC_LABEL}"])   # ignore
        if plist.exists():
            _run(["launchctl", "unload", str(plist)])              # ignore
            plist.unlink(missing_ok=True)
        return {"ok": True, **job_status(platform)}
    if platform == "win32":
        import scheduler_setup
        _run(["schtasks", "/delete", "/tn", WIN_TASK, "/f"])
        scheduler_setup._vbs_path(WIN_TASK).unlink(missing_ok=True)
        return {"ok": True, **job_status(platform)}
    return {"ok": True, "name": "", "state": "unsupported", "path": ""}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--maybe", action="store_true",
                     help="from a hook: stay silent inside an unattended run")
    run.add_argument("--now", default=None, help="ISO time to grade against")
    run.add_argument("--dry-run", action="store_true",
                     help="decide and print, send nothing, write nothing")
    job = sub.add_parser("job")
    job.add_argument("action", choices=("install", "remove", "status"))
    sub.add_parser("stop")
    sub.add_parser("line")
    args = parser.parse_args(argv)

    if args.command == "line":
        print(setup_state.one_line())
        return 0
    if args.command == "job":
        out = {"install": job_install, "remove": job_remove, "status": job_status}[args.action]()
        print(json.dumps(out))
        return 0
    if args.command == "stop":
        print(json.dumps(stop()))
        return 0
    if args.maybe and unattended():
        return 0
    result = run_once(now=args.now, dry_run=args.dry_run)
    if result is None:
        return 0
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
