#!/usr/bin/env python3
"""P29: does a Codex job started by launchd read a vault under ~/Documents and finish?

--run     copies the scratch vault under ~/Documents, writes a throwaway
          launchd job (label com.monet.scratch.codex-probe) that runs the
          Codex job command line against it, waits for the JSONL to carry
          turn.completed, prints the command items, then removes the job.
--control the same job with the Codex binary path pointed at a file that does
          not exist; the job must leave a nonzero exit and name the path.
--cleanup removes the Documents copy, the plist and the logs.

Nothing here touches the real state dir, the real vault or the real Codex
home: HOME, VAN_GOGH_STATE_DIR and CODEX_HOME all point under the scratchpad.
"""
from __future__ import annotations

import sys
import json, os, plistlib, shutil, subprocess, sys, time
from pathlib import Path

if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)

S = Path(os.environ["VG_SCRATCH"])
WT = Path(__file__).resolve().parents[2]
LABEL = "com.monet.scratch.codex-probe"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOGS = Path.home() / "Library" / "Logs" / "VanGoghScratch"
DOC_VAULT = Path.home() / "Documents" / "vg-scratch-vault"
HOME = S / "home-launchd"
STATE = HOME / ".config" / "van-gogh"
CODEX_HOME = S / "codex-home"
CACHE = CODEX_HOME / "plugins" / "cache" / "van-gogh" / "van-gogh" / "0.86.1"
NATIVE = S / "codex-npm/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"


def prepare():
    if DOC_VAULT.exists():
        shutil.rmtree(DOC_VAULT)
    shutil.copytree(S / "vault", DOC_VAULT, symlinks=True)
    cfg_path = DOC_VAULT / "van-gogh" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["obsidian"]["vault_path"] = str(DOC_VAULT)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "vault-pointer").write_text(str(DOC_VAULT), encoding="utf-8")
    link = STATE / "venv"
    if not link.is_symlink():
        link.symlink_to(WT / ".venv")
    LOGS.mkdir(parents=True, exist_ok=True)


def job(binary: str, tag: str):
    out = LOGS / f"probe-{tag}.jsonl"
    err = LOGS / f"probe-{tag}.err"
    for p in (out, err):
        if p.exists():
            p.unlink()
    # The real job shape (tests/test_scheduler_golden_codex.py pins it): the
    # venv python runs skill_run.py, which spawns the client. The job's own
    # environment carries VAN_GOGH_HOST=codex; launchd gives it a bare PATH.
    plugin_root = Path((STATE / "plugin-root").read_text(encoding="utf-8").strip()) if (STATE / "plugin-root").exists() else WT
    argv = [str(STATE / "venv" / "bin" / "python"), str(plugin_root / "app" / "skill_run.py"),
            "release-notes", "--claude", binary, "--print"]
    plist = {
        "Label": LABEL, "ProgramArguments": argv, "RunAtLoad": True,
        "EnvironmentVariables": {"HOME": str(HOME), "PATH": "/usr/bin:/bin",
                                 "VAN_GOGH_STATE_DIR": str(STATE), "CODEX_HOME": str(CODEX_HOME),
                                 "VAN_GOGH_SCHEDULED": "1", "VAN_GOGH_HOST": "codex",
                                 "VAN_GOGH_DISABLE_CLI_UPDATE": "1",
                                 "VAN_GOGH_DISABLE_UPDATE_CHECK": "1"},
        "WorkingDirectory": str(WT),
        "StandardOutPath": str(out), "StandardErrorPath": str(err),
    }
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["plutil", "-lint", str(PLIST)], check=True, capture_output=True)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], check=True)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(5)
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        state = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True).stdout
        exited = "state = not running" in state or "last exit code" in state and "pid =" not in state
        if exited and (text or (err.exists() and err.stat().st_size)):
            break
    state = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"], capture_output=True, text=True).stdout
    exit_line = next((l.strip() for l in state.splitlines() if "last exit" in l), "no exit line")
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    return out, err, exit_line


def report(out: Path, err: Path, exit_line: str):
    print("launchd:", exit_line)
    print("stderr:", (err.read_text(errors="replace")[-600:] if err.exists() else "").strip())
    ledger = DOC_VAULT / "van-gogh" / "logs" / "runs.jsonl"
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()] if ledger.exists() else []
    mine = [r for r in rows if r.get("job") == "scheduled.release-notes"]
    print("run row:", mine[-1] if mine else "none")
    text = out.read_text(errors="replace") if out.exists() else ""
    print("stdout head:", text[:300].replace("\n", " | "))
    if mine and mine[-1].get("rc") == 0 and text.strip():
        print("VERDICT: PASS (job ran under launchd, run row rc 0, deliverable printed)")
    elif mine:
        print("VERDICT: run row rc", mine[-1].get("rc"), "detail:", str(mine[-1].get("detail"))[:200])
    else:
        print("VERDICT: no run row")
    return
    done = False
    for line in out.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            print("nonjson:", line[:160]); continue
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "command_execution":
            print("CMD rc=%s | %s" % (it.get("exit_code"), str(it.get("command"))[:200]))
            o = (it.get("aggregated_output") or "")[:300].replace("\n", " | ")
            if o: print("   OUT:", o)
        if e.get("type") == "item.completed" and it.get("type") == "agent_message":
            print("MSG:", it.get("text", "")[:300].replace("\n", " "))
        if e.get("type") == "turn.completed":
            done = True; print("USAGE", e.get("usage"))
        if e.get("type") in ("turn.failed", "error"):
            print("ERR", json.dumps(e)[:300])
    print("turn.completed:", done)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--run"
    if mode == "--cleanup":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        PLIST.unlink(missing_ok=True)
        shutil.rmtree(DOC_VAULT, ignore_errors=True); shutil.rmtree(LOGS, ignore_errors=True)
        print("cleaned"); sys.exit(0)
    prepare()
    if mode == "--control":
        report(*job(str(S / "no-such-codex-binary"), "control"))
    else:
        report(*job(str(NATIVE), "run"))
