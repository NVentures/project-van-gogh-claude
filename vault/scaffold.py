#!/usr/bin/env python3
"""
scaffold.py — Set up a fresh Obsidian Second Brain vault for Project Van Gogh.

Usage:
    python3 vault/scaffold.py ~/Documents/van-gogh-vault

Creates the directory structure and copies CLAUDE.md into the vault root.
Safe to re-run — skips directories and files that already exist, except the
managed context block in CLAUDE.md, which is refreshed in place (user content
outside the markers is never touched).
"""

import os
import shutil
import sys
from pathlib import Path

DIRS = [
    "raw/assets",
    "wiki/sources",
    "wiki/entities",
    "wiki/projects",
    "wiki/assets",
    "wiki/concepts",
    "wiki/analyses",
    "wiki/weekly",
]

INDEX_STUB = """\
---
type: index
title: Wiki Index
updated: {date}
---

# Wiki Index

Content catalog. Add an entry here whenever a new page is created.

## Sources
## Entities
## Projects
## Assets
## Concepts
## Analyses
"""

LOG_STUB = """\
---
type: log
title: Operation Log
---

# Operation Log

Chronological record of all wiki operations.

"""

HOTCACHE_STUB = """\
---
title: Hot Cache
type: index
updated: {date}
---

# Hot Cache

Working memory scratch pad. Updated at the start of each wiki session.

## Active Threads

<!-- Add threads here. Each entry must include a metadata comment:

### Thread Name
<!-- deal: stage=active last_contact=YYYY-MM-DD next_action_due=YYYY-MM-DD deadline=YYYY-MM-DD -->
- Notes about this thread

Stages: closing | negotiation | active | due-diligence | monitoring | discovery | outreach | internal | permitting
-->

## Key Numbers

| Item | Value |
|---|---|

## [Your Name]'s Action Items

- [ ] First action item

## Last Session

- **Date:** {date}
- **Ingested:** (none yet)
"""


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 vault/scaffold.py <path-to-vault>")
        sys.exit(1)

    vault = Path(sys.argv[1]).expanduser().resolve()
    print(f"Scaffolding vault at: {vault}")

    for d in DIRS:
        target = vault / d
        if not target.exists():
            target.mkdir(parents=True)
            print(f"  created  {d}/")
        else:
            print(f"  exists   {d}/")

    from datetime import date
    index_path = vault / "wiki/index.md"
    if not index_path.exists():
        index_path.write_text(INDEX_STUB.format(date=date.today()))
        print("  created  wiki/index.md")
    else:
        print("  exists   wiki/index.md")

    log_path = vault / "wiki/log.md"
    if not log_path.exists():
        log_path.write_text(LOG_STUB)
        print("  created  wiki/log.md")
    else:
        print("  exists   wiki/log.md")

    hotcache_path = vault / "wiki/hotcache.md"
    if not hotcache_path.exists():
        hotcache_path.write_text(HOTCACHE_STUB.format(date=date.today()))
        print("  created  wiki/hotcache.md")
    else:
        print("  exists   wiki/hotcache.md")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
    import user_state

    claude_src = Path(__file__).parent / "CLAUDE.md"
    claude_dst = vault / "CLAUDE.md"
    if not claude_dst.exists():
        shutil.copy(claude_src, claude_dst)
        print("  created  CLAUDE.md")
    else:
        # Refresh only the managed context block; user content stays untouched.
        if user_state.sync_claude_block_into(vault):
            print("  updated  CLAUDE.md (managed context block refreshed)")
        else:
            print("  exists   CLAUDE.md (your edits kept)")

    # Fill still-pristine template placeholders (name, Business Tags, hotcache
    # action-items heading) from config.json, so the scaffolded instructions
    # never contradict the configured tags. No-op if config.json doesn't exist
    # yet or the user already edited those sections.
    if user_state.personalize_claude_scaffold(vault):
        print("  updated  scaffold placeholders filled from config.json")

    print("\nDone. Next steps:")
    print("  1. Open vault/CLAUDE.md and fill in the Project Repos and Jargon tables")
    print("  2. Point Obsidian at this directory")
    print("  3. Run /morning-coffee or /week to start using Project Van Gogh")


if __name__ == "__main__":
    main()
