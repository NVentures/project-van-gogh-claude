#!/usr/bin/env python3
"""P13: the path every new client walks, on a machine with nothing set up.

No ~/.config/van-gogh, no vault pointer, no config. Runs the real entry points
against a throwaway vault in a temp dir (OAuth is out of scope; it needs a
browser). Ends by rendering a real briefing section from fixture data, because
"the install completed" and "the thing works" are different claims.

Nothing outside the temp dirs is touched, and both are removed at the end.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/bin/python")


def step(msg):
    print(f"  {msg}")


def main():
    state = Path(tempfile.mkdtemp(prefix="vg-firstrun-state-"))
    vault = Path(tempfile.mkdtemp(prefix="vg-firstrun-vault-")) / "My Vault"
    vault.mkdir(parents=True)
    env = dict(os.environ, VAN_GOGH_STATE_DIR=str(state))
    problems = []

    try:
        print("A clean machine: no state dir contents, no vault pointer, no config.")
        step(f"state dir: {state}  (empty: {not any(state.iterdir())})")
        step(f"vault:     {vault}")

        print("\n1. Scaffold the vault")
        r = subprocess.run([PY, str(ROOT / "vault/scaffold.py"), str(vault)],
                           capture_output=True, text=True, env=env, cwd=str(ROOT))
        if r.returncode != 0:
            problems.append(f"scaffold failed: {r.stderr[-400:]}")
        for line in r.stdout.strip().splitlines()[:6]:
            step(line.strip())
        step(f"... {len(r.stdout.strip().splitlines())} lines total")

        print("\n2. Write the vault pointer and the config, as the install skill does")
        code = f'''
import sys, json, shutil
sys.path.insert(0, {str(ROOT / "app")!r})
import user_state
p = user_state.pointer_file()
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text({str(vault)!r}, encoding="utf-8")
vg = {str(vault)!r} + "/van-gogh"
import os; os.makedirs(vg, exist_ok=True)
cfg = json.loads(open({str(ROOT / "config.template.json")!r}, encoding="utf-8").read())
cfg["obsidian"]["vault_path"] = {str(vault)!r}
cfg["user"]["full_name"] = "New Client"
cfg["user"]["first_name"] = "New"
json.dump(cfg, open(vg + "/config.json", "w", encoding="utf-8"), indent=2)
print("pointer:", p)
print("config: ", vg + "/config.json")
'''
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           env=env, cwd=str(ROOT))
        if r.returncode != 0:
            problems.append(f"config write failed: {r.stderr[-600:]}")
        for line in r.stdout.strip().splitlines():
            step(line)

        print("\n3. Load config through the real loader, with nothing primed")
        code = '''
import sys
sys.path.insert(0, %r)
import config_loader as cl
print("vault:      ", cl.vault())
print("businesses: ", [b["tag"] for b in cl.businesses()])
print("priorities: ", [p["name"] for p in cl.business_priorities("personal")])
print("judgment:   ", cl.judgment_enabled(), cl.judgment_model() or "(default)")
print("memory sync:", cl.memory_sync_enabled())
print("meta keys:  ", len(cl.resolved_meta()))
''' % str(ROOT / "app")
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           env=env, cwd=str(ROOT))
        if r.returncode != 0:
            problems.append(f"config load failed: {r.stderr[-800:]}")
        for line in r.stdout.strip().splitlines():
            step(line)

        print("\n4. Render a briefing section from fixture mail")
        code = '''
import sys
sys.path.insert(0, %r)
import week_review as wr
out = {"waiting_on_user": [{"subject": "Contract needs your signature by Friday",
                            "counterparty_email": "a@b.com", "days_late": 2}],
       "inbox_pending": [], "cold_urgent": [], "cold_monitor": []}
print(wr.render_buckets_md(wr.group_by_bucket(out), "morning-coffee"))
''' % str(ROOT / "app")
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           env=env, cwd=str(ROOT))
        if r.returncode != 0:
            problems.append(f"render failed: {r.stderr[-800:]}")
        else:
            for line in r.stdout.rstrip().splitlines():
                step(line)
            if "SIGNATURE" not in r.stdout.upper() and "signature" not in r.stdout:
                problems.append("the rendered briefing lost the fixture item")

        print("\n5. Nothing landed outside the temp dirs")
        real = Path.home() / ".config" / "van-gogh"
        leaked = [str(p) for p in [real / "vault-pointer"] if p.exists()]
        step(f"~/.config/van-gogh/vault-pointer exists: {bool(leaked)}")
        if leaked:
            problems.append(f"wrote outside the temp state dir: {leaked}")
    finally:
        shutil.rmtree(state, ignore_errors=True)
        shutil.rmtree(vault.parent, ignore_errors=True)
        print(f"\ncleaned up {state} and {vault.parent}")

    print("\nRESULT:", "FAIL" if problems else "PASS")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
