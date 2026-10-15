#!/usr/bin/env python3
"""Live `codex exec` runs of Van Gogh skills in the scratch world (P4, P7, P9).

Every verdict is read from the JSONL the run prints and from files code
wrote, never from the model's prose. The scratch world is the one the
access audit built: HOME, VAN_GOGH_STATE_DIR and CODEX_HOME all under the
session scratchpad, the plugin installed there from this checkout, the real
auth.json copied in for the run.

  --skill release-notes --runs 8            P4, the skill through the plugin
  --skill vault-audit --args --auto --runs 8
  --control                                 P4 control: no env key, no -c
  --readonly-plant --runs 8                 P7: a read-only session told to write
  --hook bypass|none|no-checklist --runs 8  P9: the SessionStart hook under exec

Prints one line per run and a final PASS/FAIL table; exit 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

S = Path(os.environ["VG_SCRATCH"])
WT = Path(__file__).resolve().parents[2]
HOME = S / "home"
STATE = HOME / ".config" / "van-gogh"
CODEX_HOME = S / "codex-home"
VAULT = S / "vault"
RUNS = S / "runs"
NATIVE = S / "codex-npm/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
KNOWN_ITEMS = {"agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call",
               "web_search", "todo_list", "error", "collab_tool_call"}


def cache_dir() -> Path:
    base = CODEX_HOME / "plugins" / "cache" / "van-gogh" / "van-gogh"
    dirs = sorted((p for p in base.iterdir() if p.is_dir()),
                  key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.name)))
    return dirs[-1]


def base_env(extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "VAN_GOGH_HOST")}
    env.update({"HOME": str(HOME), "VAN_GOGH_STATE_DIR": str(STATE), "CODEX_HOME": str(CODEX_HOME),
                "VAN_GOGH_HOST": "codex"})
    env.update(extra or {})
    return env


def exec_argv(prompt: str, sandbox="workspace-write", with_override=True, extra=()):
    argv = [str(NATIVE), "exec", "--json", "--skip-git-repo-check", "-s", sandbox, "-C", str(VAULT),
            "-c", "sandbox_workspace_write.network_access=true",
            "-c", f'sandbox_workspace_write.writable_roots=["{HOME}", "{WT / ".venv"}"]']
    if with_override:
        argv += ["-c", f"shell_environment_policy.set.CLAUDE_PLUGIN_ROOT='{cache_dir()}'"]
    argv += list(extra) + [prompt]
    return argv


def run(tag: str, argv: list, env: dict, timeout=900) -> tuple:
    out = RUNS / f"{tag}.jsonl"; err = RUNS / f"{tag}.err"
    t0 = time.time()
    with open(out, "w", encoding="utf-8") as fo, open(err, "w", encoding="utf-8") as fe:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, env=env,
                           cwd=str(VAULT), timeout=timeout)
    events = []
    for line in out.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return p.returncode, events, round(time.time() - t0, 1)


def items(events, kind):
    for e in events:
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == kind:
            yield it


def unknown_items(events):
    return sorted({(e.get("item") or {}).get("type") for e in events
                   if e.get("type", "").startswith("item.") and (e.get("item") or {}).get("type") not in KNOWN_ITEMS})


def last_message(events):
    msgs = [it.get("text", "") for it in items(events, "agent_message")]
    return msgs[-1] if msgs else ""


def grade_skill(skill, events, rc, started):
    """P4: the venv python ran a real script under the cache, exit 0, no
    approval items, and the deliverable code wrote is there."""
    # Codex records a command as the model typed it: the plugin root appears
    # either as the real cache path or as the variable the shell expanded
    # (`$CLAUDE_PLUGIN_ROOT` or `${CLAUDE_PLUGIN_ROOT}`). Both count, and the
    # proof the variable resolved is the exit code and the script's own
    # output below; the control run shows what an unset variable looks like.
    cache = str(cache_dir())
    cmds = list(items(events, "command_execution"))
    def names_root(c):
        s = str(c.get("command"))
        return cache in s or "$CLAUDE_PLUGIN_ROOT" in s or "${CLAUDE_PLUGIN_ROOT}" in s
    ran = [c for c in cmds if "venv/bin/python" in str(c.get("command")) and names_root(c)
           and ".py" in str(c.get("command"))]
    ok_cmd = [c for c in ran if c.get("exit_code") == 0]
    reasons = []
    if rc != 0:
        reasons.append(f"exec rc {rc}")
    if not ran:
        reasons.append("no command named the venv python and the plugin root")
    elif not ok_cmd:
        reasons.append("the skill's script exited nonzero: " + str(ran[-1].get("exit_code")))
    elif not any((c.get("aggregated_output") or "").strip() for c in ok_cmd):
        reasons.append("the script printed nothing")
    odd = unknown_items(events)
    if odd:
        reasons.append(f"unexpected item types (approval?): {odd}")
    if not any(e.get("type") == "turn.completed" for e in events):
        reasons.append("turn never completed")
    msg = last_message(events)
    if skill == "release-notes":
        # Code wrote the update log the notes come from; the notes name it.
        log = VAULT / "van-gogh" / "logs" / "update-log.jsonl"
        last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        if str(last.get("to")) not in msg:
            reasons.append(f"reply does not carry the update log's version {last.get('to')}")
    if skill == "vault-audit":
        # The contract grades the deliverable's code-written count lines. The
        # counts are code's: vault_gardener.py --no-write (no model) says what
        # the vault holds, and the page's summary table must carry each
        # number; the score block markers and the machine copy are code's
        # too. A run that validated the day's page rather than rewriting it
        # still passes (seen live: the model ran the script report-only).
        latest = VAULT / "van-gogh" / "logs" / "vault_audit_latest.json"
        page = VAULT / "van-gogh" / "vault-audit.md"
        if not latest.exists() or not page.exists():
            reasons.append("vault_audit_latest.json or vault-audit.md is absent")
            return reasons
        probe = subprocess.run([str(STATE / "venv" / "bin" / "python"),
                                str(cache_dir() / "app" / "vault_gardener.py"), "--no-write"],
                               env=base_env({"VAN_GOGH_DISABLE_UPDATE_CHECK": "1"}), capture_output=True,
                               text=True, encoding="utf-8", timeout=300, stdin=subprocess.DEVNULL)
        out = probe.stdout
        doc = json.loads(out[out.index("{"):])
        text = page.read_text(encoding="utf-8")
        counts = {"stub sources": (doc.get("stub_sources") or {}).get("total"),
                  "stub entities": len(doc.get("stub_entities") or []),
                  "stale projects": len(doc.get("stale_projects") or []),
                  "orphan entities": len(doc.get("orphan_entities") or [])}
        summary = text.split("## Freshness")[0] if "## Freshness" in text else text[:3000]
        if not text.startswith("Audit date:") or "<!-- score:begin -->" not in text:
            reasons.append("the page lacks the audit date line or the score block")
        for name, n in counts.items():
            if n is None or not re.search(rf"\b{n}\b", summary):
                reasons.append(f"{name} count {n} not in the page's summary")
        machine = json.loads(latest.read_text(encoding="utf-8"))
        if not (machine.get("score") or {}).get("rows"):
            reasons.append("vault_audit_latest.json has no score rows")
    return reasons


_REFUSED = ("operation not permitted", "permission denied", "read-only", "read only",
            "rejected", "not allowed", "sandbox")


def rollout_for(events) -> Path | None:
    tid = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), "")
    hits = sorted((CODEX_HOME / "sessions").rglob(f"*{tid}*.jsonl")) if tid else []
    return hits[-1] if hits else None


def grade_readonly(events, target: Path):
    """P7: the write was refused (in the session transcript, where Codex
    records the tool call and the sandbox's answer; the --json stream shows
    the model's sentence only) and the file does not exist."""
    reasons = []
    if target.exists():
        reasons.append("the read-only session wrote the file")
    roll = rollout_for(events)
    if roll is None:
        reasons.append("no session transcript for this thread"); return reasons
    refused, attempted = False, False
    for line in roll.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        p = e.get("payload") or {}
        if p.get("type") in ("custom_tool_call", "function_call") and target.name in json.dumps(p):
            attempted = True
        if p.get("type") in ("custom_tool_call_output", "function_call_output"):
            text = json.dumps(p.get("output")).lower()
            if any(w in text for w in _REFUSED) or "exit_code=1" in text:
                refused = True
    if not attempted:
        reasons.append("the transcript shows no attempt to write the planted file")
    if not refused:
        reasons.append("the transcript shows no refusal of the write")
    return reasons


def grade_control(events):
    """The named failure: a command that used CLAUDE_PLUGIN_ROOT could not
    find the plugin (the variable unexpanded in its text, or the shell
    resolving it to `/app/...`). An unrelated "No such file" (a `cat` of a
    page the model guessed) does not count; the evaluation found the
    earlier any-text check vacuous."""
    for e in events:
        item = e.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        cmd = item.get("command") or ""
        out = (item.get("aggregated_output") or item.get("output") or "")
        if "CLAUDE_PLUGIN_ROOT" not in cmd:
            continue
        missing = "No such file" in out or "can't open file" in out
        if "${CLAUDE_PLUGIN_ROOT}" in out or ("/app/" in out and missing):
            return []
        if item.get("exit_code") not in (0, None) and missing:
            return []
    return ["no command that used CLAUDE_PLUGIN_ROOT failed to find the plugin"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill"); ap.add_argument("--args", default="")
    ap.add_argument("--skill-injected", help="P2: the named skill's body is in the live session's rollout")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--readonly-plant", action="store_true")
    ap.add_argument("--hook", choices=("bypass", "none", "no-checklist"))
    ap.add_argument("--regrade", action="store_true",
                    help="grade the kept JSONL of --skill runs again without spawning")
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    print("cache:", cache_dir())
    rows = []
    if a.regrade and a.readonly_plant:
        for i in range(1, a.runs + 1):
            path = RUNS / f"readonly-plant-{i}.jsonl"
            ev = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            reasons = grade_readonly(ev, VAULT / "van-gogh" / f"planted-write-{i}.md")
            rows.append((i, "PASS" if not reasons else "FAIL", "", "; ".join(reasons)))
            print(f"run {i}: {rows[-1][1]} {'; '.join(reasons)}")
        bad = [r for r in rows if r[1] == "FAIL"]
        print(f"{len(rows) - len(bad)} of {len(rows)} PASS")
        return 1 if bad else 0
    if a.regrade and a.skill:
        for i in range(1, a.runs + 1):
            path = RUNS / f"{a.skill}-{i}.jsonl"
            ev = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            rc = 0 if any(e.get("type") == "turn.completed" for e in ev) else 1
            reasons = grade_skill(a.skill, ev, rc, 0)
            rows.append((i, "PASS" if not reasons else "FAIL", "", "; ".join(reasons)))
            print(f"run {i}: {rows[-1][1]} {'; '.join(reasons)}")
        bad = [r for r in rows if r[1] == "FAIL"]
        print(f"{len(rows) - len(bad)} of {len(rows)} PASS")
        return 1 if bad else 0
    for i in range(1, a.runs + 1):
        started = time.time()
        if a.skill and a.control:
            # Strip the managed block from a copy of config.toml and pass no -c.
            cfg = CODEX_HOME / "config.toml"; backup = cfg.read_text(encoding="utf-8")
            sys.path.insert(0, str(WT / "app")); import host
            host.remove_codex_block(cfg)
            try:
                prompt = f"$van-gogh:{a.skill}" + (f" {a.args}" if a.args else "")
                rc, ev, secs = run(f"{a.skill}-control-{i}", exec_argv(prompt, with_override=False), base_env())
            finally:
                cfg.write_text(backup, encoding="utf-8")
            reasons = grade_control(ev)
        elif a.skill_injected:
            # A mention is expanded only in a real session: the rollout file
            # carries the SKILL.md body as the injected skill block.
            body = (WT / "skills" / a.skill_injected / "SKILL.md").read_text(encoding="utf-8")
            if body.startswith("---"):
                body = body.split("---", 2)[2]
            lines = [l.strip() for l in body.splitlines()
                     if len(l.strip()) >= 40 and '"' not in l and "\\" not in l and "`" not in l]
            needle = " ".join(lines[0].split())
            prompt = f"$van-gogh:{a.skill_injected} is mentioned; reply with the single word ok and run nothing."
            rc, ev, secs = run(f"skill-injected-{a.skill_injected}-{i}", exec_argv(prompt, sandbox="read-only"), base_env())
            tid = next((e.get("thread_id") for e in ev if e.get("type") == "thread.started"), "")
            rollouts = sorted((CODEX_HOME / "sessions").rglob(f"*{tid}*.jsonl")) if tid else []
            text = " ".join(rollouts[-1].read_text(encoding="utf-8").split()) if rollouts else ""
            reasons = [] if needle in text else [f"the rollout does not carry the skill body line: {needle[:60]}"]
            if not rollouts:
                reasons.append("no rollout file found for the thread")
        elif a.skill:
            prompt = f"$van-gogh:{a.skill}" + (f" {a.args}" if a.args else "")
            rc, ev, secs = run(f"{a.skill}-{i}", exec_argv(prompt), base_env())
            reasons = grade_skill(a.skill, ev, rc, started)
        elif a.readonly_plant:
            target = VAULT / "van-gogh" / f"planted-write-{i}.md"
            if target.exists():
                target.unlink()
            # The model is told the exact command and told not to pre-judge
            # the sandbox: a run where it declines on its own never tests
            # the refusal (run 7 of the third evaluation round).
            prompt = (f"This is a sandbox test and the expected outcome is that the command fails; "
                      f"the failure must be recorded by the shell tool, so you must run it rather than "
                      f"decline. Run exactly this with the shell tool, then report the exit code it "
                      f"returned: printf 'PLANTED\\n' > '{target}' . Do not answer until the shell "
                      "tool has returned. Then reply with the word done.")
            rc, ev, secs = run(f"readonly-plant-{i}", exec_argv(prompt, sandbox="read-only"), base_env())
            reasons = grade_readonly(ev, target)
        elif a.hook:
            sys.path.insert(0, str(WT / "app")); os.environ["VAN_GOGH_STATE_DIR"] = str(STATE)
            import setup_state as ss
            check = STATE / "setup" / "checklist.json"
            if a.hook == "no-checklist":
                if check.exists():
                    check.rename(check.with_suffix(".json.parked"))
            else:
                # Past the plugin and Python boxes, so the line is the one that
                # names the install skill (the Terminal variant names none).
                data = ss.new("live")
                for k in ("plugin", "python", "credentials"):
                    ss.set_status(data, k, ss.DONE)
                ss.save(data)
            extra = ("--dangerously-bypass-hook-trust",) if a.hook == "bypass" else ()
            rc, ev, secs = run(f"hook-{a.hook}-{i}", exec_argv("Reply with the single word ok.", sandbox="read-only", extra=extra), base_env())
            # Graded from the session transcript code wrote: a developer
            # message carrying the line, never the model's reply.
            tid = next((e.get("thread_id") for e in ev if e.get("type") == "thread.started"), "")
            rollouts = sorted((CODEX_HOME / "sessions").rglob(f"*{tid}*.jsonl")) if tid else []
            text = rollouts[-1].read_text(encoding="utf-8") if rollouts else ""
            carried = "Van Gogh setup:" in text
            expect = a.hook == "bypass"
            reasons = [] if carried == expect else [f"line carried={carried}, expected {expect}"]
            if carried and ("$van-gogh:install-van-gogh" not in text or "/van-gogh:install-van-gogh" in text):
                reasons.append("the line is not in Codex spelling")
            if a.hook == "no-checklist" and check.with_suffix(".json.parked").exists():
                check.with_suffix(".json.parked").rename(check)
        else:
            ap.error("name a mode")
        verdict = "PASS" if not reasons else "FAIL"
        rows.append((i, verdict, secs, "; ".join(reasons)))
        print(f"run {i}: {verdict} {secs}s {'; '.join(reasons)}", flush=True)
    bad = [r for r in rows if r[1] == "FAIL"]
    print(f"{len(rows) - len(bad)} of {len(rows)} PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
