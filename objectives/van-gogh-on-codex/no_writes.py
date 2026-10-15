#!/usr/bin/env python3
"""P17: nothing of this person's real install changes across the live runs.

  --snapshot FILE   hash every file under the real paths into FILE
  --compare FILE    hash again and report: unchanged, changed, added, removed,
                    and UNKNOWN for a path that did not exist at snapshot time

A path that does not exist is reported UNKNOWN, never counted as unchanged.
Changes are also classified: a file this machine's own scheduled jobs write
(the watcher's state, inflight markers, logs, the CLI update stamp) is named
as such, so a reader can tell the install's own heartbeat from a write the
live runs made. The verdict line counts only the rest.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HOME = Path.home()
PATHS = [HOME / ".codex" / "config.toml", HOME / ".codex" / "auth.json",
         HOME / ".config" / "van-gogh", HOME / ".agents" / "skills",
         HOME / "Library" / "LaunchAgents",
         HOME / "Documents" / "Obsidian - Second Brain" / "van-gogh"]
OWN_JOBS = ("job-watch-state.json", "inflight/", "claude-cli-update.json", "plugin-update.json",
            "/logs/", ".log", ".err", "runs.jsonl", "vincent-sessions.json", "advisor-state.json",
            "repair-state.json", "done_judge", "kpi_events.jsonl", "briefing_pages.json",
            "requirements.sha256", "venv/")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot() -> dict:
    out = {"unknown": [], "files": {}}
    for root in PATHS:
        if not root.exists():
            out["unknown"].append(str(root)); continue
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in files:
            if root.name == "LaunchAgents" and not (f.name.startswith("com.monet.") and "scratch" not in f.name):
                continue
            if "/venv/" in str(f) or "/tools/" in str(f):
                continue
            try:
                out["files"][str(f)] = digest(f)
            except OSError as exc:
                out["files"][str(f)] = f"UNREADABLE:{type(exc).__name__}"
    return out


if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__ or "no usage text"); sys.exit(0)


def main() -> int:
    mode, file = sys.argv[1], Path(sys.argv[2])
    if mode == "--snapshot":
        snap = snapshot(); file.write_text(json.dumps(snap, indent=1))
        print(f"snapshot: {len(snap['files'])} files, UNKNOWN paths: {snap['unknown']}")
        return 0
    before = json.loads(file.read_text()); now = snapshot()
    changed = sorted(k for k in before["files"] if k in now["files"] and before["files"][k] != now["files"][k])
    added = sorted(set(now["files"]) - set(before["files"]))
    removed = sorted(set(before["files"]) - set(now["files"]))
    def own(p): return any(tag in p for tag in OWN_JOBS)
    foreign = [p for p in changed + added + removed if not own(p)]
    print(f"compared {len(before['files'])} files; changed {len(changed)}, added {len(added)}, removed {len(removed)}")
    for p in changed: print(" changed", "(own jobs)" if own(p) else "(FOREIGN)", p)
    for p in added: print(" added", "(own jobs)" if own(p) else "(FOREIGN)", p)
    for p in removed: print(" removed", "(own jobs)" if own(p) else "(FOREIGN)", p)
    print("UNKNOWN at snapshot:", before["unknown"])
    print("VERDICT:", "PASS (no write outside this machine's own jobs)" if not foreign else f"FAIL ({len(foreign)} foreign)")
    return 1 if foreign else 0


if __name__ == "__main__":
    raise SystemExit(main())
