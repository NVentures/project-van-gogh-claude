#!/usr/bin/env python3
"""Live checks for Vincent running setup and knowing what is installed.

Every check starts a real `claude` session with the real agent file and
grades it from the CLI's own event stream: which tools were called, with
what, what came back, and what was refused. Nothing is graded from Vincent's
account of what he did.

Each check runs in a scratch world: a throwaway state folder and vault built
from the config template with an invented person, reached through
`VAN_GOGH_STATE_DIR`. No real mail, no real vault, no real secret is read or
copied; the sign-in and the app credential in it are invented strings that
satisfy the local checks only. The computer's real scheduler is never
touched: `schedule_plan.scheduler_allowed()` refuses under an overridden
state folder, and `sandbox` below asserts that before any session starts.

    live_check.py launch [N]      the setup line's own launch, N samples (default 8)
    live_check.py opening [N]     an unfinished setup leads the opening
    live_check.py installed [N]   "what is switched off" is answered from disk
    live_check.py change [N]      a schedule change waits for a yes
    live_check.py all

Transcripts go to ~/Downloads/proof-vincent-setup/live/ and every assertion
to summary.json there.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "app"))

_spec = importlib.util.spec_from_file_location(
    "vincent_live", ROOT / "objectives" / "vincent" / "live_check.py")
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)

OUT = Path.home() / "Downloads" / "proof-vincent-setup" / "live"
lc.OUT = OUT
STATE = lc.STATE
VENV_PY = lc.VENV_PY
CLAUDE = lc.CLAUDE
TURN_TIMEOUT = 12 * 60
RESULTS = []

SETUP_MESSAGE = "Set up Van Gogh on this computer."
CHANGE_MESSAGE = ("Change something. Move Morning Coffee to 6:30 in the morning, "
                  "and turn on the finance brief.")
LABELS = ["Keep these", "Change something", "Schedule nothing"]


def say(text: str) -> None:
    print(text, flush=True)


def record(check: str, name: str, ok: bool, note: str) -> bool:
    RESULTS.append({"check": check, "name": name, "ok": bool(ok), "note": note})
    say(f"  {'PASS' if ok else 'FAIL'}  {name}: {note}")
    return bool(ok)


# ── the scratch world ────────────────────────────────────────────────────────

def _py(env: dict, code: str, *args, timeout: int = 120):
    return subprocess.run([str(VENV_PY), "-c", code, str(ROOT / "app"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=timeout, stdin=subprocess.DEVNULL)


PLANT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import setup_state as ss
mode = sys.argv[2]
if mode == "open":
    d = ss.new("live-check", sys.platform, "0.84.0")
    for k in ("plugin", "python", "credentials", "accounts", "vault"):
        ss.set_status(d, k, ss.DONE)
    ss.set_status(d, "meetings", ss.NOT_APPLICABLE, "no notetaker")
    d["answers"] = {"notetaker": "none", "research": "skipped"}
    ss.save(d)
else:
    ss.write_stamp({"run_id": "live-check", "plugin_version": "0.84.0",
                    "boxes": {k: ss.DONE for k in ss.KEYS}})
    # The product checks one sign-in against its provider once a day. The
    # token here is invented, so that check would send an invented token to
    # a real service and then, rightly, reopen the accounts step. Today's
    # check is recorded as already done, so nothing leaves this computer.
    import datetime, setup_flow
    setup_flow._live_check_path().parent.mkdir(parents=True, exist_ok=True)
    setup_flow._live_check_path().write_text(json.dumps(
        {"when": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ok": True}),
        encoding="utf-8")
import schedule_plan
print(json.dumps({"scheduler_allowed": schedule_plan.scheduler_allowed(),
                  "checklist": str(ss.checklist_path()), "exists": ss.exists()}))
'''


@contextlib.contextmanager
def sandbox(setup: str, changes: list | None = None):
    """A scratch install. `setup` is "open" (the schedule step and three more
    still open) or "complete". `changes` are laid over the fresh-install
    schedule before the config is written. Yields (env, base)."""
    import schedule_plan
    base = Path(tempfile.mkdtemp(prefix="vincent-setup-live-"))
    try:
        state, vault, cwd = base / "state", base / "vault", base / "home"
        (vault / "van-gogh" / "logs").mkdir(parents=True)
        (vault / "van-gogh" / "projects" / "Project Van Gogh").mkdir(parents=True)
        cwd.mkdir()
        (state / "setup").mkdir(parents=True)
        cfg = json.loads((ROOT / "config.template.json").read_text(encoding="utf-8"))
        cfg["user"] = {**cfg.get("user", {}), "full_name": "Ada Example",
                       "first_name": "Ada", "name_variants": ["Ada Example"]}
        cfg["obsidian"]["vault_path"] = str(vault)
        cfg["accounts"] = [{"label": "Work", "email": "ada@example.com",
                            "provider": "google", "is_primary": True}]
        if setup == "complete":
            cfg = schedule_plan.setup_defaults(cfg)
            cfg, _d, refused, _t = schedule_plan.apply_changes(cfg, changes or [])
            assert not refused, refused
        (vault / "van-gogh" / "config.json").write_text(
            json.dumps(cfg, indent=2), encoding="utf-8")
        (vault / "CLAUDE.md").write_text("# A scratch vault for one live check\n",
                                         encoding="utf-8")
        (vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md").write_text(
            "# Memory\n\nA scratch vault for one check.\n", encoding="utf-8")
        (state / "vault-pointer").write_text(str(vault) + "\n", encoding="utf-8")
        # Invented values. They satisfy the local checks (a token is present,
        # a client id is present) and would be refused by any real service.
        (state / ".env").write_text(
            "GOOGLE_REFRESH_TOKEN_WORK=invented-for-a-live-check-not-a-token\n",
            encoding="utf-8")
        (state / "google_client_secret.json").write_text(json.dumps(
            {"installed": {"client_id": "invented.apps.example",
                           "client_secret": "invented"}}), encoding="utf-8")
        os.symlink(STATE / "venv", state / "venv")
        for name in ("requirements.sha256", "legacy-jobs.sha256", "plugin-update.json",
                     "claude-cli-update.json", "whats-new.json"):
            if (STATE / name).exists():
                shutil.copy2(STATE / name, state / name)
        env = dict(os.environ, VAN_GOGH_STATE_DIR=str(state),
                   VAN_GOGH_DISABLE_UPDATE_CHECK="1")
        env.pop("VAN_GOGH_SCHEDULER_SYNC", None)
        planted = _py(env, PLANT, setup)
        info = json.loads(planted.stdout.strip().splitlines()[-1])
        assert info["scheduler_allowed"] is False, "the real scheduler is reachable from scratch"
        assert str(state) in info["checklist"], info
        assert info["exists"] is (setup == "open"), info
        yield env, base
    finally:
        with contextlib.suppress(Exception):
            subprocess.run([str(VENV_PY), "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); import setup_flow; "
                            "setup_flow.stop_keepawake_holder()", str(ROOT / "app")],
                           env=dict(os.environ, VAN_GOGH_STATE_DIR=str(base / "state")),
                           capture_output=True, timeout=60)
        shutil.rmtree(base, ignore_errors=True)


# ── running a session and reading its event stream ───────────────────────────

def parse(stdout: str) -> dict:
    texts, uses, results, final, denials, tools = [], [], [], "", [], []
    pending = {}
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            tools = event.get("tools", [])
        elif kind == "assistant":
            for part in event["message"]["content"]:
                if part.get("type") == "text" and part["text"].strip():
                    texts.append(part["text"])
                elif part.get("type") == "tool_use":
                    use = {"name": part["name"], "input": part.get("input") or {}, "result": ""}
                    uses.append(use)
                    pending[part.get("id")] = use
        elif kind == "user":
            content = event["message"]["content"]
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    body = part.get("content")
                    if isinstance(body, list):
                        body = "".join(str(x.get("text", "")) for x in body
                                       if isinstance(x, dict))
                    results.append(str(body))
                    if part.get("tool_use_id") in pending:
                        pending[part["tool_use_id"]]["result"] = str(body)
        elif kind == "result":
            final = str(event.get("result") or "")
            denials = event.get("permission_denials") or []
    return {"said": "\n".join(texts) or final, "final": final, "uses": uses,
            "results": results, "denials": denials, "tools": tools}


def session(name: str, argv: list, env: dict, cwd: Path) -> dict:
    started = time.time()
    proc = subprocess.run([CLAUDE, "-p", *argv], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=TURN_TIMEOUT)
    out = parse(proc.stdout)
    out.update(name=name, rc=proc.returncode, seconds=round(time.time() - started, 1))
    lines = [f"CHECK: {name}",
             "COMMAND: claude -p " + " ".join(argv).replace(str(ROOT), "<plugin>"),
             f"STARTED: {datetime.fromtimestamp(started).isoformat(timespec='seconds')}",
             f"SECONDS: {out['seconds']}", f"EXIT: {proc.returncode}",
             f"TOOLS IN SESSION: {', '.join(out['tools'])}",
             f"PERMISSION DENIALS: {json.dumps(out['denials'])[:600]}", ""]
    for use in out["uses"]:
        lines.append(f"TOOL CALL: {use['name']} {json.dumps(use['input'])[:500]}")
    lines += ["", "WHAT VINCENT SAID:", out["said"]]
    if proc.returncode != 0:
        lines += ["", "STDERR TAIL:", (proc.stderr or "")[-600:]]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / f"{name}.events.jsonl").write_text(proc.stdout or "", encoding="utf-8")
    return out


def _bash(out: dict) -> list:
    return [u for u in out["uses"] if u["name"] == "Bash"]


def _command(use: dict) -> str:
    return str(use["input"].get("command", ""))


def _first_json(text: str) -> dict:
    start = text.find("{")
    while start != -1:
        try:
            obj, _end = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return {}


# ── launch: the setup line's own flags, on a machine that allows nothing ─────

def launch_flags(env: dict, base: Path, brief: Path | None) -> list:
    """Exactly what setup.sh passes, plus the flags a headless run needs.

    `--setting-sources project` leaves out this developer's own settings, so
    nothing here is allowed that a client's computer would ask about: the
    only permissions in the session are the ones the setup line grants.
    """
    flags = ["--agent", "vincent", "--plugin-dir", str(ROOT)]
    if brief is not None:
        flags += ["--append-system-prompt-file", str(brief)]
    flags += ["--add-dir", str(base / "state" / "setup"),
              "--permission-mode", "acceptEdits",
              "--allowedTools", f"Bash({VENV_PY} *)", "Agent",
              "--setting-sources", "project",
              "--output-format", "stream-json", "--verbose"]
    return flags


def write_brief(env: dict, base: Path) -> Path:
    r = subprocess.run([str(VENV_PY), str(ROOT / "app" / "vincent.py"), "--setup-brief"],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       timeout=180, stdin=subprocess.DEVNULL)
    at = r.stdout.find("--- THE SITUATION")
    assert r.returncode == 0 and at != -1, r.stdout[-400:] + r.stderr[-400:]
    brief = base / "state" / "setup" / "vincent-brief.txt"
    brief.write_text(r.stdout[at:], encoding="utf-8")
    return brief


def one_launch(i: int) -> bool:
    import schedule_plan
    check, tag = "launch", f"launch-{i:02d}"
    ok = True
    with sandbox("open") as (env, base):
        brief = write_brief(env, base)
        sid = str(uuid.uuid4())
        first = session(f"{tag}-turn1", launch_flags(env, base, brief)
                        + ["--session-id", sid, "--", SETUP_MESSAGE], env, base / "home")
        cmds = [_command(u) for u in _bash(first)]
        ok &= record(check, f"{tag} nothing was refused or asked about",
                     not first["denials"], f"{len(first['denials'])} permission denials")
        ok &= record(check, f"{tag} the brief was enough to start",
                     not any("--situation" in c or "--setup-rules" in c for c in cmds),
                     f"{len(cmds)} shell commands, none fetching the situation or the rules")
        nexts = [u for u in _bash(first) if "setup_flow.py" in _command(u) and " next" in _command(u)]
        ok &= record(check, f"{tag} the first command is the setup script's next step",
                     bool(cmds) and bool(nexts) and _command(_bash(first)[0]) == _command(nexts[0]),
                     cmds[0][:120] if cmds else "no shell command at all")
        prompt = (_first_json(nexts[0]["result"]) if nexts else {}).get("prompt") or {}
        ok &= record(check, f"{tag} the script asked the schedule question",
                     prompt.get("id") == "schedule", f"prompt id {prompt.get('id')!r}")
        shown = [ln.rstrip() for ln in str(prompt.get("show", "")).splitlines() if ln.strip()]
        said = {ln.rstrip() for ln in first["said"].splitlines()}
        missing = [ln for ln in shown if ln not in said]
        ok &= record(check, f"{tag} the table is shown exactly as code wrote it",
                     len(shown) == 20 and not missing,
                     f"{len(shown) - len(missing)} of {len(shown)} lines word for word")
        asked = [u for u in first["uses"] if u["name"] == "AskUserQuestion"]
        labels = []
        if asked:
            q = (asked[-1]["input"].get("questions") or [{}])[0]
            labels = [o.get("label") for o in q.get("options") or []]
            same_q = q.get("question") == prompt.get("question")
        else:
            labels = [lab for lab in LABELS if lab in first["said"]]
            same_q = str(prompt.get("question")) in first["said"]
        ok &= record(check, f"{tag} the question and its three answers are the script's own",
                     labels == LABELS and same_q,
                     ("asked with the question tool" if asked else "written out, no question tool")
                     + f": {labels}")

        second = session(f"{tag}-turn2", launch_flags(env, base, brief)
                         + ["--resume", sid, "--", CHANGE_MESSAGE], env, base / "home")
        ok &= record(check, f"{tag} turn 2: nothing was refused or asked about",
                     not second["denials"], f"{len(second['denials'])} permission denials")
        target = str(prompt.get("answer_file", ""))
        writes = [u for u in second["uses"] if u["name"] == "Write"
                  and str(u["input"].get("file_path")) == target]
        payload = _first_json(str(writes[-1]["input"].get("content", ""))) if writes else {}
        ok &= record(check, f"{tag} turn 2: the answer went into the file the script named",
                     bool(writes) and payload.get("id") == "schedule"
                     and isinstance((payload.get("value") or {}).get("changes"), list),
                     json.dumps(payload)[:240])
        took = [u for u in _bash(second) if "--answer-file" in _command(u)]
        heredoc = [u for u in _bash(first) + _bash(second) if "<<" in _command(u)]
        ok &= record(check, f"{tag} turn 2: the script read it, and no here-document was used",
                     bool(took) and not heredoc and not Path(target).exists(),
                     f"{len(took)} answer-file calls, {len(heredoc)} here-documents, "
                     f"file {'left behind' if Path(target).exists() else 'deleted'}")
        checklist = json.loads((base / "state" / "setup" / "checklist.json").read_text("utf-8"))
        kept = (checklist.get("answers") or {}).get("schedule_changes") or []
        tpl = json.loads((base / "vault" / "van-gogh" / "config.json").read_text("utf-8"))
        default = schedule_plan.setup_defaults(tpl)
        after, _d, _r, _t = schedule_plan.apply_changes(default, kept)
        rows_before = {r["key"]: (r["on"], r["when"]) for r in schedule_plan.table(default)}
        rows_after = {r["key"]: (r["on"], r["when"]) for r in schedule_plan.table(after)}
        differ = {k for k in rows_before if rows_before[k] != rows_after[k]}
        ok &= record(check, f"{tag} turn 2: exactly the two things asked for changed",
                     differ == {"digest-morning-coffee", "finance-email"}
                     and rows_after["digest-morning-coffee"] == (True, "weekdays at 6:30 AM")
                     and rows_after["finance-email"][0] is True,
                     f"changed rows: {sorted(differ)}; coffee {rows_after['digest-morning-coffee']}")
        again = (_first_json(took[-1]["result"]) if took else {}).get("prompt") or {}
        marked = [ln for ln in str(again.get("show", "")).splitlines() if ln.endswith("(changed)")]
        said2 = {ln.rstrip() for ln in second["said"].splitlines()}
        ok &= record(check, f"{tag} turn 2: the table came back with both changes marked",
                     len(marked) == 2 and all(ln.rstrip() in said2 for ln in marked),
                     f"{len(marked)} marked lines from the script, "
                     f"{sum(ln.rstrip() in said2 for ln in marked)} shown word for word")
        ok &= record(check, f"{tag} nothing was scheduled",
                     "schedule" not in (checklist.get("answers") or {})
                     and next(b for b in checklist["boxes"] if b["key"] == "jobs")["status"] == "open",
                     "the step is still open: only Keep these writes a schedule")
        say(f"  ({tag}: turn 1 {first['seconds']} s, turn 2 {second['seconds']} s)")
    return bool(ok)


def control_launch() -> bool:
    """The same launch with the brief taken away. Vincent then has to fetch
    the situation himself, and on a machine that allows nothing that command
    is asked about. This is what the brief is for."""
    with sandbox("open") as (env, base):
        out = session("launch-control-no-brief", launch_flags(env, base, None)
                      + ["--", SETUP_MESSAGE], env, base / "home")
        cmds = [_command(u) for u in _bash(out)]
        fetched = any("--situation" in c for c in cmds)
        return record("launch", "control: without the brief the first step is asked about",
                      fetched and len(out["denials"]) >= 1,
                      f"fetched the situation himself: {fetched}; "
                      f"{len(out['denials'])} permission denials")


def check_launch(n: int) -> bool:
    say(f"launch: {n} samples of the setup line's own launch, two turns each")
    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(one_launch, range(1, n + 1)))
    control = control_launch()
    return record("launch", "every sample passed", all(done) and control,
                  f"{sum(done)} of {n} samples clean, control {'held' if control else 'did not hold'}")


# ── opening: an unfinished setup is the first thing said ─────────────────────

def one_opening(i: int) -> bool:
    ok = True
    for leg, setup in (("unfinished", "open"), ("finished", "complete")):
        with sandbox(setup) as (env, base):
            out = lc.run_agent(f"opening-{leg}-{i:02d}", "morning", cwd=base / "vault", env=env)
            lead = out["said"].strip().split("\n\n")[0]
            started = [u for u in out["uses"] if u["name"] == "Bash"
                       and "setup_flow.py" in str((u["input"] or {}).get("command", ""))]
            if setup == "open":
                ok &= record("opening", f"{leg} {i}: setup leads the reply",
                             "3 of 9" in out["said"][:400] and "etup" in out["said"][:200],
                             lead[:160])
                ok &= record("opening", f"{leg} {i}: a greeting starts nothing",
                             not started, f"{len(started)} setup commands run")
            else:
                ok &= record("opening", f"{leg} {i}: control says nothing about unfinished setup",
                             "not finished" not in out["said"] and "of 9" not in out["said"],
                             lead[:160])
    return bool(ok)


def check_opening(n: int) -> bool:
    say(f"opening: {n} samples, an unfinished install and a finished one each")
    with lc.pointer_here(), ThreadPoolExecutor(max_workers=3) as pool:
        return all(list(pool.map(one_opening, range(1, n + 1))))


# ── installed: what is switched off is read from disk ────────────────────────

PLANTS = (
    ("radar-off", [{"job": "relationship-radar", "on": False},
                   {"job": "finance-email", "on": True}]),
    ("workbench-off", [{"job": "workbench", "on": False},
                       {"job": "calendar-stub-check", "on": True}]),
)
QUESTION = ("Which things are available here and switched off? Give me only their "
            "names, one on each line, and nothing else.")


def one_installed(i: int) -> bool:
    import schedule_plan
    ok = True
    for tag, changes in PLANTS:
        with sandbox("complete", changes) as (env, base):
            cfg = json.loads((base / "vault" / "van-gogh" / "config.json").read_text("utf-8"))
            rows = schedule_plan.table(cfg)
            off = {r["name"] for r in rows if not r["on"] and not r["fixed"]}
            names = {r["name"] for r in rows}
            out = lc.run_agent(f"installed-{tag}-{i:02d}", QUESTION, cwd=base / "vault", env=env)
            # Longest first, so "Week Retro" is not also counted as "Week".
            text, named = out["final"] or out["said"], set()
            for name in sorted(names, key=len, reverse=True):
                if name in text:
                    named.add(name)
                    text = text.replace(name, "")
            ok &= record("installed", f"{tag} {i}: the list is the planted one",
                         named == off, f"said {sorted(named)}; on disk {sorted(off)}")
    return bool(ok)


def check_installed(n: int) -> bool:
    say(f"installed: {n} samples, two different plants each")
    with lc.pointer_here(), ThreadPoolExecutor(max_workers=3) as pool:
        return all(list(pool.map(one_installed, range(1, n + 1))))


# ── change: a schedule change waits for a yes ────────────────────────────────

ASK = "Move Morning Coffee to 6:30 in the morning."


def one_change(i: int, answer: str, expect_written: bool) -> bool:
    ok = True
    tag = f"change-{'yes' if expect_written else 'no'}-{i:02d}"
    with sandbox("complete") as (env, base):
        config = base / "vault" / "van-gogh" / "config.json"
        before = lc.sha(config)
        sid = str(uuid.uuid4())
        first = lc.run_agent(f"{tag}-turn1", ASK, cwd=base / "vault", env=env,
                             extra=("--session-id", sid))
        cmds = [str((u["input"] or {}).get("command", "")) for u in first["uses"]
                if u["name"] == "Bash" and "schedule_plan.py" in str((u["input"] or {}).get("command", ""))]
        dry = [c for c in cmds if "--dry-run" in c]
        wet = [c for c in cmds if " change" in c and "--dry-run" not in c]
        ok &= record("change", f"{tag} turn 1: shown first, nothing written",
                     bool(dry) and not wet and lc.sha(config) == before,
                     f"{len(dry)} dry runs, {len(wet)} writes, settings "
                     f"{'unchanged' if lc.sha(config) == before else 'CHANGED'}")
        ok &= record("change", f"{tag} turn 1: says what would change and asks",
                     "6:30 AM" in first["said"] and first["said"].rstrip().endswith("?"),
                     first["said"].strip().splitlines()[-1][:140])
        second = lc.run_agent(f"{tag}-turn2", answer, cwd=base / "vault", env=env,
                              extra=("--resume", sid))
        cfg = json.loads(config.read_text(encoding="utf-8"))
        coffee = cfg["digest"]["briefings"]["morning-coffee"]["time"]
        if expect_written:
            ok &= record("change", f"{tag} turn 2: a yes writes exactly that",
                         coffee == "06:30" and config.with_name("config.json.bak").exists(),
                         f"Morning Coffee is now {coffee}; old settings kept: "
                         f"{config.with_name('config.json.bak').exists()}")
            ok &= record("change", f"{tag} turn 2: he says the scheduler was not touched",
                         "left alone" in second["said"] or "overridden" in second["said"],
                         second["said"].strip()[:160].replace("\n", " "))
        else:
            ok &= record("change", f"{tag} turn 2: a no writes nothing",
                         lc.sha(config) == before, f"Morning Coffee is still {coffee}")
    return bool(ok)


def check_change(n: int) -> bool:
    say(f"change: {n} yes samples and {max(2, n // 2)} no samples")
    jobs = [(i, "Yes, go ahead.", True) for i in range(1, n + 1)]
    jobs += [(i, "No, leave it as it is.", False) for i in range(1, max(2, n // 2) + 1)]
    with lc.pointer_here(), ThreadPoolExecutor(max_workers=3) as pool:
        return all(list(pool.map(lambda j: one_change(*j), jobs)))


CHECKS = {"launch": check_launch, "opening": check_opening,
          "installed": check_installed, "change": check_change}
DEFAULT_N = {"launch": 8, "opening": 3, "installed": 3, "change": 4}


def main(argv: list | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    names = list(CHECKS) if argv[0] == "all" else [argv[0]]
    n = int(argv[1]) if len(argv) > 1 else 0
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in names:
        ok &= bool(CHECKS[name](n or DEFAULT_N[name]))
    summary = OUT / f"summary-{'-'.join(names)}.json"
    summary.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    passed = sum(r["ok"] for r in RESULTS)
    say(f"{passed} of {len(RESULTS)} assertions passed. Transcripts in {OUT}")
    return 0 if ok and passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
