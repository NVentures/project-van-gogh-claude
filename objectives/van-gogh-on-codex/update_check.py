#!/usr/bin/env python3
"""P13 live: the update path under Codex, in a scratch world.

A copy of this checkout is the marketplace at 0.87.0; it is installed, a
second (older) version directory is planted beside it so two versions are
present, the pointer names the installed one, then the copy is bumped to
0.88.0 and `plugin_update.apply_update` runs for real against the native
codex binary. Graded: status ok, the version rose, and three paths agree:
the config.toml env path, the path derived from `codex plugin list --json`,
and the pointer file. Control: a second apply_update with nothing new must
say "unchanged" and move nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)

S = Path(os.environ["VG_SCRATCH"])
NAT = S / "codex-npm/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
E = S / "p13"


def codex(env, *args):
    return subprocess.run([str(NAT), *args], env=env, capture_output=True, text=True, timeout=300)


def bump(mkt: Path, old: str, new: str):
    for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "pyproject.toml"):
        p = mkt / rel; t = p.read_text(); assert t.count(f'"{old}"') == 1, rel
        p.write_text(t.replace(f'"{old}"', f'"{new}"'))


def main() -> int:
    if E.exists():
        shutil.rmtree(E)
    mkt, home, codex_home = E / "mkt", E / "home", E / "codex"
    state = home / ".config" / "van-gogh"
    for d in (home, codex_home, state):
        d.mkdir(parents=True)
    shutil.copytree(ROOT, mkt, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "objectives", "*.pyc"))
    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(codex_home), "VAN_GOGH_STATE_DIR": str(state),
           "VAN_GOGH_HOST": "codex", "VAN_GOGH_CODEX_BIN": str(NAT), "VAN_GOGH_DISABLE_UPDATE_CHECK": "1"}
    assert codex(env, "plugin", "marketplace", "add", str(mkt)).returncode == 0
    r = codex(env, "plugin", "add", "van-gogh@van-gogh", "--json"); assert r.returncode == 0, r.stderr
    cache = codex_home / "plugins" / "cache" / "van-gogh" / "van-gogh"
    installed = cache / "0.87.0"; assert installed.is_dir()
    shutil.copytree(installed, cache / "0.86.0")                 # two versions present
    (state / "plugin-root").write_text(str(installed) + "\n")
    (state / "vault-pointer").write_text(str(S / "vault") + "\n")
    print("cache before:", sorted(p.name for p in cache.iterdir()))
    bump(mkt, "0.87.0", "0.88.0")

    code = ("import sys, os, json; sys.path.insert(0, 'app'); import plugin_update; "
            "r = plugin_update.apply_update(os.environ['VAN_GOGH_CODEX_BIN']); "
            "print(json.dumps({k: r.get(k) for k in ('status','version_before','version','skipped','returncode')}))")
    run = subprocess.run([sys.executable, "-c", code], cwd=str(installed), env=env,
                         capture_output=True, text=True, timeout=600)
    print("apply_update:", run.stdout.strip(), run.stderr.strip()[-400:])
    rec = json.loads(run.stdout.strip().splitlines()[-1])
    ok = rec["status"] == "ok" and rec["version"] == "0.88.0" and rec["version_before"] == "0.87.0"

    cfg = tomllib.loads((codex_home / "config.toml").read_text())
    env_path = cfg["shell_environment_policy"]["set"]["CLAUDE_PLUGIN_ROOT"]
    listed = codex(env, "plugin", "list", "--json").stdout
    rows = json.loads(listed[listed.index("{"):])["installed"]
    row = next(r for r in rows if r["pluginId"] == "van-gogh@van-gogh")
    list_path = row.get("installedPath") or str(cache / row["version"])
    pointer = (state / "plugin-root").read_text().strip()
    print("cache after:", sorted(p.name for p in cache.iterdir()))
    print("env path   :", env_path); print("list path  :", list_path); print("pointer    :", pointer)
    three = len({os.path.realpath(env_path), os.path.realpath(list_path), os.path.realpath(pointer)}) == 1
    print("three paths equal:", three, "| version rose:", ok)

    run2 = subprocess.run([sys.executable, "-c", code], cwd=str(cache / "0.88.0"), env=env,
                          capture_output=True, text=True, timeout=600)
    rec2 = json.loads(run2.stdout.strip().splitlines()[-1])
    cfg2 = tomllib.loads((codex_home / "config.toml").read_text())
    control = rec2["status"] == "unchanged" and cfg2["shell_environment_policy"]["set"]["CLAUDE_PLUGIN_ROOT"] == env_path
    print("control (nothing new):", rec2, "| unchanged and paths held:", control)
    verdict = ok and three and control
    print("VERDICT:", "PASS" if verdict else "FAIL")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
