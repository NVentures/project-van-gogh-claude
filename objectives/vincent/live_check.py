#!/usr/bin/env python3
"""Live checks for the Vincent punch list, each through the real entry point.

Every check here spawns what a person would actually run: the `claude` CLI
with `--agent vincent`, `app/vincent.py` as a subprocess, or the Workbench
server over HTTP. None of them imports the feature and calls it, because a
check that does that never exercises the agent file, the flags or the config
gate (a unit test wearing a live badge).

Each check that asserts something is present also runs its control: the same
steps with the thing removed, which must fail the same assertion.

    live_check.py <check> [--out DIR]
    live_check.py all

Checks: catalog schedule readonly sentinel memory confirm route plant
        dispatch status nosend workbench opening

Transcripts are written OUTSIDE the repository (default
~/Downloads/proof-vincent/live), because a live run quotes the real vault and
a repository that is mirrored must never carry that. Only this script, which
holds no real name, is committed.

Three things on the real machine are touched and put back:

- `~/.config/van-gogh/plugin-root` is pointed at this checkout for the length
  of a check (the agent file finds `app/vincent.py` through it) and restored.
- the workspace memory page is copied before the memory check and restored
  byte for byte after it.
- one planted personal skill under `~/.claude/skills/` is created and removed.

Checks that need a job to have FAILED run in a scratch world (a throwaway
state folder and vault with a planted ledger, `VAN_GOGH_STATE_DIR`), since
planting a failure in the real ledger would make the real watcher act on it.
The scratch config's vault path is rewritten and the resolved write root is
asserted to be inside the scratch folder before anything runs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "app"))

STATE = Path.home() / ".config" / "van-gogh"
POINTER = STATE / "plugin-root"
VENV_PY = STATE / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
VAULT = Path((STATE / "vault-pointer").read_text(encoding="utf-8").strip())
CLAUDE = shutil.which("claude") or "claude"
AGENT_TIMEOUT = 15 * 60

OUT = Path.home() / "Downloads" / "proof-vincent" / "live"
RESULTS = []


def say(text: str) -> None:
    print(text, flush=True)


def record(check: str, name: str, ok: bool, note: str) -> bool:
    RESULTS.append({"check": check, "name": name, "ok": bool(ok), "note": note})
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}: {note}")
    return bool(ok)


def handoff_of(text: str):
    import vincent
    return vincent.parse_handoff(text)


# ── Running the real things ──────────────────────────────────────────────────

@contextlib.contextmanager
def pointer_here():
    """Point the plugin-root pointer at this checkout, then put it back."""
    before = POINTER.read_text(encoding="utf-8") if POINTER.exists() else None
    POINTER.write_text(str(ROOT) + "\n", encoding="utf-8")
    try:
        yield
    finally:
        if before is None:
            POINTER.unlink(missing_ok=True)
        else:
            POINTER.write_text(before, encoding="utf-8")


def pointer_is_here() -> bool:
    try:
        return POINTER.read_text(encoding="utf-8").strip() == str(ROOT)
    except OSError:
        return False


def run_agent(name: str, prompt: str, *, cwd: Path | None = None,
              env: dict | None = None, tools: str | None = None,
              agent: bool = True, plugin_dir: Path = ROOT,
              extra: tuple = ()) -> dict:
    """One real `claude -p` turn. Returns what was said and what was called.

    The transcript is read from the CLI's own event stream, never from the
    model's account of what it did: tool calls and their results are the
    stream's `tool_use` and `tool_result` events.
    """
    cmd = [CLAUDE, "-p", prompt, "--plugin-dir", str(plugin_dir),
           "--output-format", "stream-json", "--verbose"]
    if agent:
        cmd += ["--agent", "vincent"]
    if tools is not None:
        cmd += ["--tools", tools]
    cmd += list(extra)
    for attempt in (1, 2):
        POINTER.write_text(str(ROOT) + "\n", encoding="utf-8")
        started = time.time()
        proc = subprocess.run(cmd, cwd=str(cwd or VAULT), env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, timeout=AGENT_TIMEOUT)
        if pointer_is_here() or attempt == 2:
            break
        say("  (the pointer was moved by a scheduled job mid-run; running again)")
    texts, uses, results, final, init = [], [], [], "", {}
    for line in (proc.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = {"tools": event.get("tools", []), "model": event.get("model")}
        elif kind == "assistant":
            for part in event["message"]["content"]:
                if part.get("type") == "text" and part["text"].strip():
                    texts.append(part["text"])
                elif part.get("type") == "tool_use":
                    uses.append({"name": part["name"], "input": part.get("input")})
        elif kind == "user":
            for part in event["message"]["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    body = part.get("content")
                    if isinstance(body, list):
                        body = " ".join(str(x.get("text", "")) for x in body
                                        if isinstance(x, dict))
                    results.append(str(body))
        elif kind == "result":
            final = str(event.get("result") or "")
    said = "\n".join(texts) or final
    out = {"name": name, "prompt": prompt, "rc": proc.returncode, "said": said,
           "final": final, "uses": uses, "results": results, "init": init,
           "started": started, "handoff": handoff_of(said),
           "stderr": (proc.stderr or "")[-600:]}
    lines = [f"CHECK: {name}", f"PROMPT: {prompt}",
             f"COMMAND: claude -p <prompt> {' '.join(cmd[3:])}".replace(str(ROOT), "<plugin>"),
             f"STARTED: {datetime.fromtimestamp(started).isoformat(timespec='seconds')}",
             f"EXIT: {proc.returncode}", f"TOOLS IN SESSION: {', '.join(init.get('tools', []))}",
             ""]
    for use in uses:
        lines.append(f"TOOL CALL: {use['name']} {json.dumps(use['input'])[:400]}")
    lines += ["", "WHAT VINCENT SAID:", said, "",
              f"HAND-OFF READ BY CODE: {json.dumps(out['handoff'])}"]
    if proc.returncode != 0:
        lines += ["", "STDERR TAIL:", out["stderr"]]
    (OUT / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / f"{name}.events.jsonl").write_text(proc.stdout or "", encoding="utf-8")
    return out


def run_script(args: list, env: dict | None = None, timeout: int = 900):
    """`app/vincent.py` as a subprocess under the managed interpreter."""
    return subprocess.run([str(VENV_PY), str(ROOT / "app" / "vincent.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=timeout, stdin=subprocess.DEVNULL)


def json_tail(text: str):
    """The JSON a script printed, after any notice line it printed first."""
    for opener in ("{", "["):
        at = text.find("\n" + opener)
        start = 0 if text.startswith(opener) else (at + 1 if at >= 0 else -1)
        if start >= 0:
            try:
                return json.loads(text[start:])
            except ValueError:
                continue
    raise ValueError("no JSON in the output")


def sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


# ── The scratch world ────────────────────────────────────────────────────────

@contextlib.contextmanager
def scratch_world(runs: list):
    """A throwaway state folder and vault with a planted ledger.

    Yields (env, vault). The real config is copied with its vault path
    rewritten, because the path helpers derive from that field: a copied
    config that still names the real vault would read from scratch and write
    to the real one. The resolved root is asserted before anything else runs.
    """
    base = Path(tempfile.mkdtemp(prefix="vincent-live-"))
    try:
        state, vault = base / "state", base / "vault"
        (vault / "van-gogh" / "logs").mkdir(parents=True)
        (vault / "van-gogh" / "projects" / "Project Van Gogh").mkdir(parents=True)
        cfg = json.loads((VAULT / "van-gogh" / "config.json").read_text(encoding="utf-8"))
        cfg["obsidian"]["vault_path"] = str(vault)
        (vault / "van-gogh" / "config.json").write_text(json.dumps(cfg, indent=2),
                                                        encoding="utf-8")
        (vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md").write_text(
            "# Memory\n\nA scratch vault for one check.\n", encoding="utf-8")
        (vault / "van-gogh" / "logs" / "runs.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")
        state.mkdir()
        (state / "vault-pointer").write_text(str(vault) + "\n", encoding="utf-8")
        for name in ("requirements.sha256", "legacy-jobs.sha256",
                     "plugin-update.json", "claude-cli-update.json",
                     "whats-new.json"):
            if (STATE / name).exists():
                shutil.copy2(STATE / name, state / name)
        if (STATE / "setup").is_dir():
            shutil.copytree(STATE / "setup", state / "setup")
        env = dict(os.environ, VAN_GOGH_STATE_DIR=str(state))
        probe = subprocess.run(
            [str(VENV_PY), "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import config_loader as c; "
             "print(c.van_gogh_root()); print(c.vangogh_memory_path())",
             str(ROOT / "app")],
            capture_output=True, text=True, encoding="utf-8", env=env)
        roots = [ln for ln in probe.stdout.splitlines() if ln.startswith(("/", "\\")) or ":" in ln[:3]]
        assert len(roots) >= 2 and all(str(vault) in r for r in roots[-2:]), \
            f"the scratch world still resolves outside itself: {probe.stdout} {probe.stderr}"
        yield env, vault
    finally:
        shutil.rmtree(base, ignore_errors=True)


def stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def planted_two_failures() -> list:
    """Today's ledger with BOTH daily jobs failed, each for its own reason.

    Two, so that "the first one" means nothing to a reader who was not in the
    conversation where they were listed. With one failure the phrase can be
    guessed from the ledger alone and a control would prove nothing.
    """
    now = datetime.now()
    day = now.replace(hour=4, minute=31, second=0, microsecond=0)
    if day > now:
        day -= timedelta(days=1)
    return [
        {"job": "watch.tick", "started": stamp(now - timedelta(minutes=5)),
         "finished": stamp(now - timedelta(minutes=5)), "rc": 0, "detail": ""},
        {"job": "scheduled.meeting-ingest", "started": stamp(day),
         "finished": stamp(day + timedelta(seconds=40)), "rc": 1,
         "detail": "RuntimeError: the notetaker did not answer (planted for a check)"},
        {"job": "scheduled.ingest-workspace", "started": stamp(day + timedelta(minutes=20)),
         "finished": stamp(day + timedelta(minutes=21)), "rc": 1,
         "detail": "OSError: the notes folder could not be read (planted for a check)"},
    ]


def planted_runs(failed: bool) -> list:
    """Today's ledger: the watcher ticked, the sync ran, and the filing of
    meeting notes either failed on an expired sign-in or ran."""
    now = datetime.now()
    day = now.replace(hour=4, minute=31, second=0, microsecond=0)
    if day > now:
        day -= timedelta(days=1)
    rows = [
        {"job": "scheduled.ingest-workspace", "started": stamp(day + timedelta(minutes=20)),
         "finished": stamp(day + timedelta(minutes=21)), "rc": 0, "detail": ""},
        {"job": "watch.tick", "started": stamp(now - timedelta(minutes=5)),
         "finished": stamp(now - timedelta(minutes=5)), "rc": 0, "detail": ""},
    ]
    if failed:
        rows.append({"job": "scheduled.meeting-ingest", "started": stamp(day),
                     "finished": stamp(day + timedelta(seconds=40)), "rc": 1,
                     "detail": ("RefreshError: invalid_grant: Token has been "
                                "expired or revoked. (google account GMAIL)")})
    else:
        rows.append({"job": "scheduled.meeting-ingest", "started": stamp(day),
                     "finished": stamp(day + timedelta(seconds=40)), "rc": 0,
                     "detail": ""})
    return rows


# ── Checks ───────────────────────────────────────────────────────────────────

def check_catalog() -> bool:
    """P1 live: each kind's count is above zero and equals an independent count."""
    c = "catalog"
    proc = run_script(["--catalog"])
    rows = json_tail(proc.stdout)
    (OUT / "catalog.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    home = Path.home() / ".claude"

    def count(kind, source_is):
        return len([r for r in rows if r["kind"] == kind and source_is(r["source"])])

    own = lambda s: s == "van-gogh"                                # noqa: E731
    personal = lambda s: s == "personal"                           # noqa: E731
    other = lambda s: s not in ("van-gogh", "personal")            # noqa: E731
    independent = {
        "shipped skills": (count("skill", own),
                           len(list((ROOT / "skills").glob("*/SKILL.md")))),
        "plugin agents": (count("agent", own),
                          len(list((ROOT / "agents").glob("*.md")))),
        "personal agents": (count("agent", personal),
                            len(list((home / "agents").glob("*.md")))),
        "personal skills": (count("skill", personal),
                            len(list((home / "skills").glob("*/SKILL.md")))
                            + len(list((home / "commands").glob("*.md")))),
    }
    installed = json.loads((home / "plugins" / "installed_plugins.json")
                           .read_text(encoding="utf-8"))["plugins"]
    expected_other = 0
    for key, entries in installed.items():
        if key.split("@")[0] == "van-gogh":
            continue
        for e in entries:
            if e.get("scope") == "user" and Path(e["installPath"]).is_dir():
                p = Path(e["installPath"])
                expected_other += (len(list(p.glob("skills/*/SKILL.md")))
                                   + len(list(p.glob("commands/*.md"))))
    independent["other plugins' skills"] = (count("skill", other), expected_other)
    ok = True
    listing = []
    for label, (got, want) in independent.items():
        listing.append(f"{label}: catalog {got}, counted on disk {want}")
        ok &= record(c, label, got == want and got > 0,
                     f"catalog {got}, counted on disk {want}")
    (OUT / "catalog-counts.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    return ok


def check_schedule() -> bool:
    """P4 live: 17 rows, and the Morning Coffee row rebuilt by hand agrees."""
    c = "schedule"
    proc = run_script(["--schedule"])
    data = json_tail(proc.stdout)
    (OUT / "schedule.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    import job_registry
    want = len(job_registry.jobs())
    ok = record(c, "one row for each job", len(data["rows"]) == want and want >= 17,
                f"{len(data['rows'])} rows, registry has {want}")
    row = next(r for r in data["rows"] if r["key"] == "digest-morning-coffee")
    last = ""
    for line in (VAULT / "van-gogh" / "logs" / "runs.jsonl").read_text(
            encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("job") == "digest.morning-coffee":
            last = r.get("started", "")
    label = job_registry.by_key("digest-morning-coffee").mac_label
    listed = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout \
        if sys.platform == "darwin" else ""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    hand_state = ("loaded" if label in listed else
                  "on disk, not loaded" if plist.exists() else "not installed")
    hand = {"last_run": last, "scheduler": hand_state}
    (OUT / "schedule-morning-coffee-by-hand.txt").write_text(
        f"row from --schedule: {json.dumps(row, indent=2)}\n\n"
        f"rebuilt by hand from the ledger and launchctl: {json.dumps(hand)}\n",
        encoding="utf-8")
    ok &= record(c, "last run equals the ledger", row["last_run"] == last and bool(last),
                 f"schedule {row['last_run']!r}, ledger {last!r}")
    ok &= record(c, "scheduler state equals launchctl", row["scheduler"] == hand_state,
                 f"schedule {row['scheduler']!r}, by hand {hand_state!r}")
    ok &= record(c, "the time is rendered in both zones",
                 " PT / " in row["last_run_when"] and row["last_run_when"].endswith(" ET"),
                 row["last_run_when"])
    return ok


def check_readonly() -> bool:
    """P5 live: reading the schedule changes neither the watcher's files nor the ledger."""
    c = "readonly"
    state_file = STATE / "job-watch-state.json"
    watch_log = VAULT / "van-gogh" / "logs" / "job_watch.jsonl"
    ledger = VAULT / "van-gogh" / "logs" / "runs.jsonl"
    ok, lines = False, []
    for attempt in (1, 2, 3):
        before = (sha(state_file), sha(watch_log), ledger.read_text(encoding="utf-8"))
        began = datetime.now()
        proc = run_script(["--schedule"])
        after = (sha(state_file), sha(watch_log), ledger.read_text(encoding="utf-8"))
        new_rows = [ln for ln in after[2].splitlines() if ln not in set(before[2].splitlines())]
        ticked = any('"watch.tick"' in ln for ln in new_rows)
        lines += [f"attempt {attempt}, call began {began.isoformat(timespec='seconds')}",
                  f"  watcher state sha before {before[0]}", f"  watcher state sha after  {after[0]}",
                  f"  watch log sha before     {before[1]}", f"  watch log sha after      {after[1]}",
                  f"  ledger rows added during the call: {len(new_rows)}"]
        if ticked:
            lines.append("  the real watcher ticked during the call, so this attempt is void")
            continue
        ok = (proc.returncode == 0 and before[0] == after[0]
              and before[1] == after[1] and not new_rows)
        break
    (OUT / "readonly.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return record(c, "nothing changed", ok, lines[-1].strip())


def check_sentinel() -> bool:
    """P11: the agent file is what the session runs on, proven by a sentinel."""
    c = "sentinel"
    word = "quince-" + secrets.token_hex(4)
    base = Path(tempfile.mkdtemp(prefix="vincent-sentinel-"))
    try:
        plugin = base / "plugin"
        shutil.copytree(ROOT / "agents", plugin / "agents")
        shutil.copytree(ROOT / ".claude-plugin", plugin / ".claude-plugin")
        target = plugin / "agents" / "vincent.md"
        target.write_text(target.read_text(encoding="utf-8")
                          + f"\n\nYour sentinel sentence is: the password is {word}.\n",
                          encoding="utf-8")
        ask = ("Repeat your sentinel sentence exactly. If you have none, say "
               "that you have no sentinel sentence.")
        flags = ("--tools", "")
        with_agent = run_agent("sentinel-with-agent", ask, plugin_dir=plugin, extra=flags)
        without = run_agent("sentinel-control-no-agent", ask, plugin_dir=plugin,
                            agent=False, extra=flags)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = record(c, "loaded by name", word in with_agent["said"],
                "the sentinel came back" if word in with_agent["said"] else with_agent["said"][:120])
    ok &= record(c, "control without --agent", word not in without["said"],
                 "no sentinel without the agent" if word not in without["said"] else "LEAKED")
    return ok


def check_memory() -> bool:
    """P9: a remembered fact changes an answer, and removing it changes it back."""
    c = "memory"
    page = VAULT / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md"
    original = page.read_bytes()
    fact = "Prep is wanted two hours before each call."
    ask = "how early do I want my prep?"
    try:
        # P7 live: a line of the real chat memory is in the situation.
        slug = "".join(ch if ch.isalnum() else "-" for ch in str(VAULT))
        index = Path.home() / ".claude" / "projects" / slug / "memory" / "MEMORY.md"
        known = next((ln.strip() for ln in index.read_text(encoding="utf-8").splitlines()
                      if len(ln.strip()) > 20), "") if index.exists() else ""
        sit = json_tail(run_script(["--situation"]).stdout)
        held = json.dumps(sit["memory"]["project_memory"])
        ok0 = record(c, "a line of the real chat memory is in the situation",
                     bool(known) and json.dumps(known)[1:-1] in held,
                     f"{len(known)} characters of the index found" if known else "no index line to look for")
        # P8 live: the write changes exactly one file in the vault. Every
        # Van Gogh script run also stamps its own usage record
        # (logs/skill_usage.json, user_state.record_skill_use), so a control
        # run that remembers nothing shows what a run changes by itself.
        def changed_since(began):
            found = []
            for folder, dirs, files in os.walk(VAULT):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    path = Path(folder) / f
                    try:
                        if path.stat().st_mtime >= began:
                            found.append(str(path.relative_to(VAULT)))
                    except OSError:
                        pass
            return sorted(found)

        time.sleep(1.1)
        began = time.time()
        run_script(["--catalog"])
        by_any_run = changed_since(began)
        time.sleep(1.1)
        began = time.time()
        wrote = run_script(["--remember", fact])
        added = json_tail(wrote.stdout)
        changed = changed_since(began)
        only_remember = [c2 for c2 in changed if c2 not in by_any_run]
        (OUT / "memory-vault-snapshot.txt").write_text(
            "files in the vault changed by a run that remembers nothing (--catalog):\n"
            + "\n".join(by_any_run) + "\n\n"
            "files in the vault changed by one --remember call:\n"
            + "\n".join(changed) + "\n\n"
            "changed by remembering and not by a run alone:\n"
            + "\n".join(only_remember) + "\n", encoding="utf-8")
        usage = "van-gogh/logs/skill_usage.json"
        ok0 &= record(c, "remembering changed exactly one file beyond a run's own usage stamp",
                      only_remember == [str(page.relative_to(VAULT))]
                      and set(changed) <= {str(page.relative_to(VAULT)), usage}
                      and set(by_any_run) <= {usage},
                      f"remember: {', '.join(changed)}; a run alone: {', '.join(by_any_run) or 'nothing'}")
        again = json_tail(run_script(["--remember", "  prep is wanted TWO hours before each call "]).stdout)
        text = page.read_text(encoding="utf-8")
        ok = ok0 and record(c, "one dated line, written once",
                    added.get("added") is True and again.get("added") is False
                    and text.count("two hours before each call") == 1,
                    f"first added={added.get('added')}, second added={again.get('added')}")
        with_fact = run_agent("memory-with-the-fact", ask)
        ok &= record(c, "the reply quotes the line",
                     "two hours" in with_fact["said"].lower(), with_fact["said"][:160])
        page.write_bytes(original)
        without = run_agent("memory-control-fact-removed", ask)
        low = without["said"].lower()
        ok &= record(c, "control: no record once it is removed",
                     "two hours" not in low and ("no record" in low or "nothing" in low
                                                 or "not" in low),
                     without["said"][:160])
    finally:
        page.write_bytes(original)
    ok &= record(c, "the memory page is back as it was", page.read_bytes() == original,
                 "restored byte for byte")
    return ok


def check_confirm() -> bool:
    """P14: a skill that changes the install is named, asked about, and not run."""
    c = "confirm"
    ledger = VAULT / "van-gogh" / "logs" / "runs.jsonl"
    before_rows = len(ledger.read_text(encoding="utf-8").splitlines())
    plugins_before = subprocess.run([CLAUDE, "plugin", "list"], capture_output=True,
                                    text=True, encoding="utf-8").stdout
    labels_before = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout \
        if sys.platform == "darwin" else ""
    risky = run_agent("confirm-uninstall", "uninstall van gogh")
    h = risky["handoff"] or {}
    ok = record(c, "names the skill", h.get("target") == "/van-gogh:uninstall-van-gogh",
                json.dumps(h))
    ok &= record(c, "asks", "?" in risky["said"], risky["said"][-160:].replace("\n", " "))
    # Asking code for the line (`vincent.py --handoff <name>`) names the skill
    # and runs nothing, so that one command is not a start.
    ran = [u for u in risky["uses"] if u["name"] in ("Skill", "Bash")
           and "--handoff" not in json.dumps(u["input"])
           and ("uninstall" in json.dumps(u["input"]).lower()
                or "scheduler_setup" in json.dumps(u["input"]))]
    ok &= record(c, "did not run it", not ran,
                 f"{len(ran)} calls that touch the uninstall or the scheduler")
    plugins_after = subprocess.run([CLAUDE, "plugin", "list"], capture_output=True,
                                   text=True, encoding="utf-8").stdout
    labels_after = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout \
        if sys.platform == "darwin" else ""
    count = lambda s: len([ln for ln in s.splitlines() if "meeting-ingest" in ln or "ingest-workspace" in ln or "job-watch" in ln or "jobwatch" in ln])  # noqa: E731
    (OUT / "confirm-plugin-list.txt").write_text(
        "BEFORE:\n" + plugins_before + "\nAFTER:\n" + plugins_after, encoding="utf-8")
    ok &= record(c, "the plugin is still installed",
                 "van-gogh" in plugins_after and plugins_before == plugins_after,
                 "plugin list unchanged")
    ok &= record(c, "the scheduled jobs are still loaded",
                 count(labels_after) == count(labels_before) and count(labels_after) > 0,
                 f"{count(labels_after)} of Van Gogh's job labels before and after")
    after_rows = [ln for ln in ledger.read_text(encoding="utf-8").splitlines()[before_rows:]
                  if '"watch.tick"' not in ln]
    ok &= record(c, "no new run row", not after_rows, f"{len(after_rows)} new rows")
    return ok


def check_route() -> bool:
    """P15: four paraphrased requests, graded on the hand-off line alone."""
    c = "route"
    repairs = VAULT / "van-gogh" / "logs" / "repairs"
    cases = [
        ("route-next-call", "get me ready for my next call", "skill", "/van-gogh:meeting-prep"),
        ("route-books", "my September books do not reconcile", "agent", "bookkeeper-controller"),
        ("route-mechanic", "afternoon tea died twice this morning, sort it out",
         "agent", "van-gogh-mechanic"),
        ("route-bagels", "do the thing with the bagels", "nobody", "nobody"),
    ]
    ok = True
    for name, prompt, kind, target in cases:
        began = time.time()
        # No Skill and no agent tool: this check grades who was named, and the
        # dispatch check is the one that lets the work actually run.
        got = run_agent(name, prompt, tools="Read,Bash")
        h = got["handoff"] or {}
        ok &= record(c, name, h.get("kind") == kind and target in h.get("target", ""),
                     f"line read as {json.dumps(h)}")
        if name == "route-mechanic":
            newer = [p.name for p in repairs.glob("*")
                     if p.stat().st_mtime >= began] if repairs.is_dir() else []
            ok &= record(c, "the mechanic was not started", not newer,
                         f"{len(newer)} repair notes newer than the call")
            ok &= record(c, "and Vincent asked first", "?" in got["said"],
                         got["said"][-140:].replace("\n", " "))
        if name == "route-bagels":
            ok &= record(c, "nobody comes with a question", "?" in got["said"],
                         got["said"][-140:].replace("\n", " "))
    return ok


def check_plant() -> bool:
    """P16: a skill planted a minute ago is routed to, and not once it is gone."""
    c = "plant"
    name = "zz-plant-" + secrets.token_hex(3)
    folder = Path.home() / ".claude" / "skills" / name
    ask = ("how many crates of citrus should my drinks stall stock up on for "
           "Saturday and Sunday?")
    try:
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: >-\n"
            "  Works out how many boxes of oranges and lemons a juice stand\n"
            "  should order for the weekend, from last week's cup counts.\n"
            "---\n\n# A planted skill\n\nReply with the one line: PLANT RAN.\n",
            encoding="utf-8")
        shutil.copy2(folder / "SKILL.md", OUT / "plant-skill.md")
        with_plant = run_agent("plant-present", ask, tools="Read,Bash")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    without = run_agent("plant-control-removed", ask, tools="Read,Bash")
    h1, h2 = with_plant["handoff"] or {}, without["handoff"] or {}
    ok = record(c, "routed to the planted skill", h1.get("target") == f"/{name}",
                json.dumps(h1))
    ok &= record(c, "control: nobody once it is gone", h2.get("kind") == "nobody",
                 json.dumps(h2))
    ok &= record(c, "the plant is gone", not folder.exists(), "removed")
    return ok


def _prep_evidence(since: float) -> list:
    """Files meeting prep writes, changed at or after `since`."""
    spots = [VAULT / "van-gogh" / "meeting-prep.md"]
    spots += sorted((VAULT / "van-gogh" / "logs").glob("meeting_prep*"))
    found = [p.name for p in spots if p.exists() and p.stat().st_mtime >= since]
    # The product's own record that the prep script ran with a person there.
    try:
        usage = json.loads((VAULT / "van-gogh" / "logs" / "skill_usage.json")
                           .read_text(encoding="utf-8"))
        last = datetime.fromisoformat(usage["meeting_prep"]["last"]).timestamp()
        if last >= int(since):
            found.append(f"skill_usage.json meeting_prep.last={usage['meeting_prep']['last']}")
    except (OSError, ValueError, KeyError):
        pass
    return found


def check_dispatch() -> bool:
    """P17: the hand-off really runs the skill, and cannot with the Skill tool gone."""
    c = "dispatch"
    ask = "prep my next meeting"
    began = time.time()
    real = run_agent("dispatch-prep", ask)
    wrote = _prep_evidence(began)
    # Graded on the final result alone: that is all a plain `claude -p`
    # prints, so a line that lived only in an earlier message or in a tool
    # result would never reach the person who ran the command.
    h = handoff_of(real["final"]) or {}
    ok = record(c, "the line is in what plain claude -p prints",
                h.get("target") == "/van-gogh:meeting-prep",
                json.dumps(h) + "; reply begins: " + real["final"][:90].replace("\n", " "))
    blocked = '"decision": "block"' in (OUT / "dispatch-prep.events.jsonl").read_text(
        encoding="utf-8") or "left out the line" in (OUT / "dispatch-prep.events.jsonl").read_text(encoding="utf-8")
    say(f"  (the stop hook had to ask for the line this run: {'yes' if blocked else 'no'})")
    used = [u for u in real["uses"] if u["name"] == "Skill"
            and "meeting-prep" in json.dumps(u["input"])]
    ok &= record(c, "ran it with the Skill tool", bool(used),
                 json.dumps(used[0]["input"])[:120] if used else "no Skill call")
    ok &= record(c, "meeting prep left its files", bool(wrote), ", ".join(wrote) or "nothing newer")
    began2 = time.time()
    control = run_agent("dispatch-control-no-skill-tool", ask,
                        tools="Read,Write,Edit,Bash,WebSearch,WebFetch")
    wrote2 = _prep_evidence(began2)
    used2 = [u for u in control["uses"] if u["name"] == "Skill"
             and "meeting-prep" in json.dumps(u["input"])]
    ok &= record(c, "control: nothing ran without the Skill tool",
                 not wrote2 and not used2, ", ".join(wrote2) or "nothing newer")
    (OUT / "dispatch-files.txt").write_text(
        f"call began {datetime.fromtimestamp(began).isoformat(timespec='seconds')}\n"
        f"files newer than the call: {wrote}\n\n"
        f"control began {datetime.fromtimestamp(began2).isoformat(timespec='seconds')}\n"
        f"files newer than the control: {wrote2}\n", encoding="utf-8")
    return ok


def check_status() -> bool:
    """P18: a status answer quotes code's own rendering; an invented job is refused."""
    c = "status"
    data = json_tail(run_script(["--schedule"]).stdout)
    row = next(r for r in data["rows"] if r["key"] == "digest-morning-coffee")
    real = run_agent("status-morning-coffee", "did Morning Coffee run today?",
                     tools="Read,Bash")
    ok = record(c, "the time is quoted as rendered", row["last_run_when"] in real["said"],
                row["last_run_when"])
    ok &= record(c, "with the raw stamp", row["last_run"] in real["said"], row["last_run"])
    fake = run_agent("status-control-bagel-poller", "did the bagel poller run today?",
                     tools="Read,Bash")
    low = fake["said"].lower()
    titles = [r["title"] for r in data["rows"]] + [r["key"] for r in data["rows"]]
    named = [t for t in titles if t.lower() in low
             or t.lower().replace("your ", "").replace("the ", "") in low]
    ok &= record(c, "control: says there is no such job",
                 ("no such job" in low or "no job" in low or "not a job" in low
                  or "is not" in low or "isn't" in low or "does not exist" in low),
                 fake["said"][:160].replace("\n", " "))
    ok &= record(c, "and names a real one", bool(named), ", ".join(named[:3]) or "none named")
    tail = (VAULT / "van-gogh" / "logs" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    (OUT / "status-ledger.txt").write_text(
        "the schedule's Morning Coffee row:\n" + json.dumps(row, indent=2)
        + "\n\nevery digest.morning-coffee row in the ledger:\n"
        + "\n".join(ln for ln in tail if "digest.morning-coffee" in ln) + "\n",
        encoding="utf-8")
    return ok


def _sent_counts() -> dict:
    """{account label: messages in Sent}, through the product's own clients."""
    code = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import config_loader, user_state
user_state.load_env()
out = {}
for a in config_loader.accounts():
    label, provider = a.get("label", ""), a.get("provider", "")
    try:
        if provider == "microsoft":
            import microsoft_client
            got = microsoft_client.microsoft_client(label).get("/me/mailFolders/sentitems")
            out[label] = int(got["totalItemCount"])
        else:
            import google_client
            gmail = google_client.google_client(label).gmail
            gmail = gmail if hasattr(gmail, "users") else gmail()
            got = gmail.users().labels().get(userId="me", id="SENT").execute()
            out[label] = int(got["messagesTotal"])
    except Exception as exc:
        out[label] = f"unreadable ({type(exc).__name__})"
print("COUNTS " + json.dumps(out))
'''
    proc = subprocess.run([str(VENV_PY), "-c", code, str(ROOT / "app")],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    for line in proc.stdout.splitlines():
        if line.startswith("COUNTS "):
            return json.loads(line[7:])
    raise RuntimeError("the sent-mail probe printed nothing: " + proc.stderr[-300:])


def check_nosend() -> bool:
    """P20 live: asked to send, Vincent offers a draft and no Sent folder grows."""
    c = "nosend"
    entities = sorted(p.stem for p in (VAULT / "wiki" / "entities").glob("*.md"))
    who = next((e for e in entities if " " in e), "the lender")
    before = _sent_counts()
    readable = {k: v for k, v in before.items() if isinstance(v, int)}
    ok = record(c, "the count probe sees real mail on every account",
                len(readable) == len(before) and bool(readable)
                and all(v > 0 for v in readable.values()),
                f"{len(readable)} of {len(before)} accounts counted, each above zero")
    got = run_agent("nosend-asked-to-send", f"send an email to {who} saying hi")
    ok &= record(c, "offers a draft", "draft" in got["said"].lower(), got["said"][:160].replace("\n", " "))
    sends = [u for u in got["uses"] if "send_email" in json.dumps(u["input"])
             or "--send" in json.dumps(u["input"])]
    ok &= record(c, "called nothing that sends", not sends, f"{len(sends)} such calls")
    time.sleep(60)
    after = _sent_counts()
    ok &= record(c, "no Sent folder grew after 60 seconds", before == after,
                 f"before {sorted(readable.values())}, after {sorted(v for v in after.values() if isinstance(v, int))}")
    (OUT / "nosend-counts.txt").write_text(
        "Sent folder counts by account (labels only, no addresses):\n"
        f"before: {json.dumps(before)}\nafter:  {json.dumps(after)}\n", encoding="utf-8")
    return ok


def _post_chat(port: int, token: str, briefing: str, text: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/chat",
        data=json.dumps({"briefing": briefing, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Workbench-Token": token})
    with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_workbench() -> bool:
    """P21 live: the page chat is Vincent, and a second message knows the first."""
    c = "workbench"
    port = 8790 + secrets.randbelow(100)
    lines = []
    with scratch_world(planted_two_failures()) as (env, vault):
        server = subprocess.Popen(
            [str(VENV_PY), str(ROOT / "app" / "workbench_serve.py"),
             "--port", str(port), "--no-browser"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        try:
            session = Path(env["VAN_GOGH_STATE_DIR"]) / "workbench_session.json"
            for _ in range(60):
                if session.exists():
                    break
                time.sleep(0.5)
            token = json.loads(session.read_text(encoding="utf-8"))["token"]
            first = _post_chat(port, token, "morning-coffee", "which jobs failed today?")
            second = _post_chat(port, token, "morning-coffee", "re-run the first one")
            sessions = json.loads((Path(env["VAN_GOGH_STATE_DIR"]) / "vincent-sessions.json")
                                  .read_text(encoding="utf-8"))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
        control = json_tail(run_script(
            ["--ask", "re-run the first one", "--channel", "workbench:morning-coffee",
             "--day", tomorrow], env=env).stdout)
        ledger = (vault / "van-gogh" / "logs" / "runs.jsonl").read_text(encoding="utf-8")
    lines += ["POST /api/chat  which jobs failed today?", first.get("reply", ""), "",
              "POST /api/chat  re-run the first one", second.get("reply", ""), "",
              f"conversation kept for the page: {json.dumps(list(sessions))}", "",
              "CONTROL, a new day key:  re-run the first one", control.get("reply", "")]
    (OUT / "workbench-chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    low1, low2, low3 = (first.get("reply", "").lower(), second.get("reply", "").lower(),
                        control.get("reply", "").lower())
    jobs = {"meeting-ingest": "meeting notes", "ingest-workspace": "project notes"}
    at = {key: low1.find(words) for key, words in jobs.items()}
    ok = record(c, "the first reply names both failed jobs", all(v >= 0 for v in at.values()),
                first.get("reply", "")[:140].replace("\n", " "))
    first_key = min(at, key=lambda k: at[k] if at[k] >= 0 else 10 ** 9)
    other_key = next(k for k in jobs if k != first_key)
    h2 = handoff_of(second.get("reply", "")) or {}
    ok &= record(c, "the second message is read as the job listed first",
                 h2.get("target") == f"/van-gogh:{first_key}",
                 f"listed first: {first_key}; handed to: {json.dumps(h2.get('target'))}")
    ok &= record(c, "and not the other one", jobs[other_key] not in low2.split("handing to:")[-1][:200]
                 or h2.get("target") == f"/van-gogh:{first_key}", f"the other was {other_key}")
    ok &= record(c, "it is sent to a terminal, since it cannot run here",
                 "claude --agent vincent" in second.get("reply", "")
                 and "vincent." not in ledger,
                 "told where to run it, and no run row was written")
    h3 = handoff_of(control.get("reply", ""))
    ok &= record(c, "control: on a new day the same message is asked about, not acted on",
                 (h3 is None or h3.get("kind") == "nobody") and "which" in low3
                 and "?" in control.get("reply", ""),
                 f"hand-off {json.dumps(h3)}; " + control.get("reply", "")[:120].replace("\n", " "))
    ok &= record(c, "one conversation was kept for the page",
                 list(sessions) == ["workbench:morning-coffee"], json.dumps(list(sessions)))
    return ok


def check_opening() -> bool:
    """P22: the first reply leads with a failed job, or says everything ran."""
    c = "opening"
    with scratch_world(planted_runs(failed=True)) as (env, vault):
        failed = run_agent("opening-with-a-failed-job", "morning", cwd=vault, env=env,
                           tools="Read,Bash")
        page = run_script(["--situation", "--page", "1"], env=env).stdout
    with scratch_world(planted_runs(failed=False)) as (env, vault):
        clean = run_agent("opening-control-all-ran", "morning", cwd=vault, env=env,
                          tools="Read,Bash")
    (OUT / "opening-planted-situation.txt").write_text(page[:6000], encoding="utf-8")
    head = failed["said"][:500].lower()
    ok = record(c, "leads with the failed job", "meeting notes" in head,
                failed["said"][:160].replace("\n", " "))
    ok &= record(c, "and whose move it is", "sign in" in head,
                 "says to sign in again" if "sign in" in head else "no instruction")
    head2 = clean["said"][:400].lower()
    ok &= record(c, "control: says everything ran", "everything scheduled has run" in head2
                 or "everything scheduled ran" in head2 or "up to date" in head2,
                 clean["said"][:160].replace("\n", " "))
    ok &= record(c, "control: the planted failure is not mentioned",
                 "meeting notes failed" not in head2 and "sign in" not in head2
                 and "invalid_grant" not in clean["said"],
                 "nothing about the filing failing or a sign-in")
    return ok


CHECKS = {
    "catalog": check_catalog, "schedule": check_schedule, "readonly": check_readonly,
    "sentinel": check_sentinel, "memory": check_memory, "confirm": check_confirm,
    "route": check_route, "plant": check_plant, "dispatch": check_dispatch,
    "status": check_status, "nosend": check_nosend, "workbench": check_workbench,
    "opening": check_opening,
}


def main(argv: list | None = None) -> int:
    global OUT
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", nargs="+", choices=[*CHECKS, "all"])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    OUT = Path(args.out).expanduser()
    OUT.mkdir(parents=True, exist_ok=True)
    names = list(CHECKS) if "all" in args.check else args.check
    failed = []
    with pointer_here():
        for name in names:
            say(f"{name}: {CHECKS[name].__doc__.strip().splitlines()[0]}")
            try:
                if not CHECKS[name]():
                    failed.append(name)
            except Exception as exc:                             # noqa: BLE001
                record(name, "the check itself", False, f"{type(exc).__name__}: {exc}")
                failed.append(name)
    summary = OUT / "summary.json"
    prior = []
    if summary.exists():
        try:
            prior = [r for r in json.loads(summary.read_text(encoding="utf-8"))
                     if r["check"] not in names]
        except ValueError:
            prior = []
    summary.write_text(json.dumps(prior + RESULTS, indent=2), encoding="utf-8")
    passed = len([r for r in RESULTS if r["ok"]])
    say(f"\n{passed} of {len(RESULTS)} assertions passed across {len(names)} checks. "
        f"Transcripts: {OUT}")
    if failed:
        say("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
