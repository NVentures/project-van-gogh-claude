#!/usr/bin/env python3
"""P14: what happens to a client who already had this installed.

They have an old config with none of the new keys, an old vault CLAUDE.md with
their own writing in it, and an old requirements hash. The upgrade must add the
new managed context without touching their words, keep every config value they
already set, and re-sync dependencies once.

The failure this hunts is quiet: a managed block that overwrites the user's own
notes, or a config that loses a field on the way through.
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

import user_state                                               # noqa: E402

OLD_CLAUDE_MD = """<!-- van-gogh:context:begin (managed) -->
# Old managed content from a previous version
This paragraph is machine-owned and should be replaced.
<!-- van-gogh:context:end -->

# My own vault notes

## Business Tags
| Tag | Business |
|---|---|
| `#acme` | Acme Corp |

## My private jargon table
Nobody's tooling should ever touch this line.
"""

OLD_CONFIG = {
    "user": {"full_name": "Existing Client", "first_name": "Existing",
             "timezone": "America/Chicago"},
    "obsidian": {"vault_path": "PLACEHOLDER", "hotcache_relpath": "wiki/hotcache.md",
                 "sources_relpath": "wiki/sources", "weekly_relpath": "wiki/weekly",
                 "hotcache_action_items_heading": "My Action Items"},
    "accounts": [{"provider": "google", "email": "old@client.com", "label": "Gmail",
                  "is_primary": True}],
    "businesses": [{"tag": "acme", "display_name": "Acme Corp",
                    "project_page": "wiki/projects/Acme.md",
                    "meeting_route": "10-Acme", "keywords": ["acme"]}],
}


def main():
    vault = Path(tempfile.mkdtemp(prefix="vg-upgrade-vault-")) / "Old Vault"
    (vault / "van-gogh").mkdir(parents=True)
    problems = []
    try:
        cfg = json.loads(json.dumps(OLD_CONFIG))
        cfg["obsidian"]["vault_path"] = str(vault)
        cfg_path = vault / "van-gogh" / "config.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        (vault / "CLAUDE.md").write_text(OLD_CLAUDE_MD, encoding="utf-8")
        before_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

        print("An existing install, upgrading:")
        print(f"  vault:  {vault}")
        print(f"  config: {len(before_cfg)} top-level keys, no priorities, "
              "no judgment, no memory_sync")

        print("\n1. The managed CLAUDE.md block refreshes")
        changed = user_state.sync_claude_block_into(vault)
        after = (vault / "CLAUDE.md").read_text(encoding="utf-8")
        print(f"  block rewritten: {changed}")
        for phrase, label in [
            ("My private jargon table", "user's own section"),
            ("Nobody's tooling should ever touch this line.", "user's own line"),
            ("`#acme` | Acme Corp", "user's own table row"),
        ]:
            kept = phrase in after
            print(f"  kept {label}: {kept}")
            if not kept:
                problems.append(f"upgrade destroyed the {label}")
        for phrase, label in [
            ("Priorities: write them down the moment they are said", "priorities rule"),
            ("Propensity for action", "drafting rule"),
            ("three memory surfaces", "memory surfaces rule"),
        ]:
            got = phrase in after
            print(f"  added {label}: {got}")
            if not got:
                problems.append(f"upgrade did not deliver the {label}")
        if "Old managed content from a previous version" in after:
            problems.append("the stale managed block survived the refresh")
        print("  stale managed content gone:",
              "Old managed content" not in after)

        print("\n2. Running it twice changes nothing more (idempotent)")
        h1 = hashlib.sha256((vault / "CLAUDE.md").read_bytes()).hexdigest()
        user_state.sync_claude_block_into(vault)
        h2 = hashlib.sha256((vault / "CLAUDE.md").read_bytes()).hexdigest()
        print(f"  identical after second run: {h1 == h2}")
        if h1 != h2:
            problems.append("the managed block is not idempotent")

        print("\n3. No config value was lost")
        after_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        print(f"  config untouched by the sync: {after_cfg == before_cfg}")
        if after_cfg != before_cfg:
            problems.append("the upgrade modified config.json")

        print("\n4. The old config still resolves under the new code")
        import config_loader as cl
        saved = cl._config
        cl._config = after_cfg
        try:
            checks = {
                "business_priorities(acme)": cl.business_priorities("acme"),
                "judgment_enabled": cl.judgment_enabled(),
                "memory_sync_enabled": cl.memory_sync_enabled(),
                "resolved_meta keys": len(cl.resolved_meta()),
            }
            for k, v in checks.items():
                print(f"  {k}: {v}")
            if checks["business_priorities(acme)"] != []:
                problems.append("an old bucket did not default to no priorities")
            # ON is correct here. The call only fires for a bucket that has
            # priorities, and an existing client has none, so they pay nothing
            # until they deliberately set one. What must NOT happen is an
            # existing client silently keeping the weaker matcher forever.
            if checks["judgment_enabled"] is not True:
                problems.append(
                    "an upgrading client would keep the weaker word matcher")
            if checks["business_priorities(acme)"]:
                problems.append("an upgrading client would incur model calls "
                                "for priorities they never set")
        except Exception as e:                                  # noqa: BLE001
            problems.append(f"old config failed under new code: {e}")
        finally:
            cl._config = saved

        print("\n5. Dependency re-sync is hash-driven")
        req = (ROOT / "requirements.txt").read_bytes()
        print(f"  requirements.txt sha256: {hashlib.sha256(req).hexdigest()[:16]}")
        print("  sync_runtime_deps no-ops outside the managed venv: "
              f"{not user_state._running_in_managed_venv()}")
    finally:
        shutil.rmtree(vault.parent, ignore_errors=True)

    print("\nRESULT:", "FAIL" if problems else "PASS")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
