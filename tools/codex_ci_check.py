#!/usr/bin/env python3
"""The Codex CI lane, cross-platform, with no model calls.

Run on a runner that has `codex` on PATH (npm or the official installer):

  1. the README's own Codex paste line (tools/readme_lines.py), run with the
     marketplace source swapped for this checkout and `--prepare-only`
     appended, under a scratch CODEX_HOME and state dir, so the plugin as
     checked out installs and the setup lane prepares; needs no sign-in;
  2. the checkout itself installed through Codex (VAN_GOGH_MARKETPLACE names
     it), then the five-way version equality (pyproject, both manifests,
     `codex plugin list --json`, the cache directory) and the skill set
     `codex debug prompt-input` lists against skills/ on disk;
  3. the SessionStart hook in Codex mode with a planted checklist, which
     must print the one line, and nothing without the checklist.

Every check prints what it saw; a failure names the value. Exit 1 on the
first failure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = sys.platform == "win32"
PUBLIC_SOURCE = "NVentures/project-van-gogh-claude"


def sh(cmd, env, **kw):
    """Run one paste line the way a client would: sh on POSIX, PowerShell on Windows."""
    if WIN:
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
    else:
        argv = ["sh", "-c", cmd]
    return subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, **kw)


def codex(env, *args):
    return subprocess.run(["codex", *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300, shell=WIN)


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vg-codex-ci-"))
    # A developer may run this from inside a session; the setup lane refuses
    # to run inside one, so the session markers are scrubbed (CI has none).
    base = {k: v for k, v in os.environ.items()
            if k not in ("VAN_GOGH_HOST", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID")}
    print("codex:", codex(base, "--version").stdout.strip())

    # 1. The README line, verbatim plus --prepare-only, in a scratch world.
    line = subprocess.run([sys.executable, str(ROOT / "tools" / "readme_lines.py"),
                           "install-codex-windows" if WIN else "install-codex-mac", "--prepare-only"],
                          capture_output=True, text=True, check=True).stdout.strip()
    # The marketplace source is the one substitution: the README names the
    # public repository, CI installs the commit under test.
    source = ROOT.as_posix() if not WIN else str(ROOT)
    if PUBLIC_SOURCE not in line:
        fail(f"the README line no longer names {PUBLIC_SOURCE}")
    line = line.replace(PUBLIC_SOURCE, f'"{source}"')
    # The README line looks under ~/.codex, so the scratch Codex home IS
    # that folder of the scratch HOME; Codex also refuses a home that does
    # not exist yet.
    home1 = tmp / "home1"; (home1 / ".config" / "van-gogh").mkdir(parents=True)
    (home1 / ".codex").mkdir()
    env1 = {**base, "CODEX_HOME": str(home1 / ".codex"), "HOME": str(home1),
            "USERPROFILE": str(home1), "VAN_GOGH_STATE_DIR": str(home1 / ".config" / "van-gogh"),
            "VAN_GOGH_SETUP_LOGS": str(tmp / "logs1")}
    print("README line:", line)
    res = sh(line, env1, cwd=str(tmp))
    print(res.stdout[-1500:], res.stderr[-800:])
    if res.returncode != 0:
        fail(f"the README install line exited {res.returncode}")
    elif (home1 / ".config" / "van-gogh" / "host").read_text().strip() != "codex" \
            if (home1 / ".config" / "van-gogh" / "host").exists() else True:
        fail("the README line did not record host codex")

    # 2. The checkout installed through Codex, then the five-way equality.
    home2 = tmp / "home2"; state2 = home2 / ".config" / "van-gogh"; state2.mkdir(parents=True)
    (home2 / ".codex").mkdir()
    env2 = {**base, "CODEX_HOME": str(home2 / ".codex"), "HOME": str(home2), "USERPROFILE": str(home2),
            "VAN_GOGH_STATE_DIR": str(state2), "VAN_GOGH_SETUP_LOGS": str(tmp / "logs2"),
            "VAN_GOGH_MARKETPLACE": str(ROOT)}
    if WIN:
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(ROOT / "setup.ps1"), "-Host", "codex", "-PrepareOnly"],
                             env=env2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    else:
        res = subprocess.run(["sh", str(ROOT / "setup.sh"), "--host", "codex", "--prepare-only"],
                             env=env2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    print(res.stdout[-1500:], res.stderr[-800:])
    if res.returncode != 0:
        fail(f"setup --host codex --prepare-only exited {res.returncode}")
    listed = codex(env2, "plugin", "list", "--json").stdout
    rows = json.loads(listed[listed.index("{"):]).get("installed", [])
    row = next((r for r in rows if r.get("pluginId") == "van-gogh@van-gogh"), None)
    if not row:
        fail("codex plugin list does not show van-gogh@van-gogh")
    versions = {
        "pyproject": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "claude manifest": json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"],
        "codex manifest": json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"],
        "plugin list": row.get("version"),
    }
    cache = Path(env2["CODEX_HOME"]) / "plugins" / "cache" / "van-gogh" / "van-gogh"
    dirs = sorted(p.name for p in cache.iterdir() if p.is_dir()) if cache.exists() else []
    versions["cache dir"] = dirs[-1] if dirs else None
    print("versions:", versions)
    if len(set(versions.values())) != 1:
        fail("the five versions differ")

    # The skill set the model is shown, against skills/ on disk.
    pi = codex(env2, "debug", "prompt-input", "hello").stdout
    shown = set(re.findall(r"\bvan-gogh:([a-z0-9-]+)\b", pi))
    on_disk = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir() and p.name != "_shared"}
    print("skills shown:", len(shown), "on disk:", len(on_disk))
    if shown != on_disk:
        fail(f"skill sets differ: only shown {sorted(shown - on_disk)}, only on disk {sorted(on_disk - shown)}")

    # 3. The SessionStart hook in Codex mode.
    sys.path.insert(0, str(ROOT / "app"))
    os.environ["VAN_GOGH_STATE_DIR"] = str(state2)
    import setup_state as ss
    data = ss.new("ci")
    for k in ss.KEYS[:3]:
        ss.set_status(data, k, ss.DONE)
    ss.save(data)
    hook_env = {**env2, "CLAUDE_PLUGIN_ROOT": str(ROOT)}
    hook_env.pop("VAN_GOGH_UNATTENDED", None); hook_env.pop("VAN_GOGH_SCHEDULED", None)
    if WIN:
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "hooks" / "setup_notice.ps1"), "-Codex"]
    else:
        argv = ["bash", str(ROOT / "hooks" / "setup_notice.sh"), "--codex"]
    res = subprocess.run(argv, env=hook_env, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    print("hook line:", res.stdout.strip())
    if not res.stdout.startswith("Van Gogh setup:") or "$van-gogh:install-van-gogh" not in res.stdout:
        fail("the hook did not print the Codex line")
    (state2 / "setup" / "checklist.json").unlink()
    res = subprocess.run(argv, env=hook_env, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    if res.stdout.strip():
        fail("the hook printed without a checklist")
    print("OK: codex lane")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
