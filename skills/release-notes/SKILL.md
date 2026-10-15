---
name: release-notes
description: Re-show the release notes for the last Van Gogh update (what changed between the version you had and the version you have now), or the full version history on request. Use when the user types /van-gogh:release-notes or asks "what's new", "what changed in the last update", "show me the release notes", "what did the update do", or "release history".
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Release notes

Re-shows what the last plugin update changed. The briefing footer shows these
notes exactly once, in the first chat session after an update (never in a
digest email or on a published page), then goes quiet; this skill is how the
user gets them back, scoped exactly as they were shown: the versions between
what the machine had and what it has now.

Everything here is a local read. No network, no `git`, no `claude` CLI, so it
behaves identically in the Claude Desktop app, a terminal, and anywhere else.
Checking whether a *newer* version exists is a different job: that is
`/van-gogh:check-updates`.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/plugin_update.py" --release-notes
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\plugin_update.py" --release-notes
```

The script prints one JSON object:

- `installed` — the running version.
- `last_update` — the newest entry from the vault's update log
  (`{vault}/van-gogh/logs/update-log.jsonl`), or `null` if no update has been
  recorded on this machine. Fields: `ts`, `from`, `to`, `notes_md`.
- `update_count` — how many updates the vault log has recorded.
- `current_entry_md` — the running version's own changelog entry, the fallback
  when there is no logged update.
- `changelog_versions` — every version the shipped changelog documents,
  newest first.

---

## Render

The notes are already written for a non-technical reader; show them verbatim,
never paraphrased into something more technical.

**When `last_update` is present** (the normal case):

```
Updated {from} to {to} on {ts, date only}.

{notes_md, verbatim}
```

When `notes_md` is empty (the update predated its own release note), say so in
one line and show `current_entry_md` instead.

**When `last_update` is null** (fresh install, or the machine has never
updated since this log existed): one line, "No update recorded on this machine
yet. You're on {installed}, here is what it includes:" then `current_entry_md`
verbatim.

**When the user asks for more** ("show everything", "older versions", a
specific version): read `${CLAUDE_PLUGIN_ROOT}/CHANGELOG.md` directly. Each
`## <version>` section is self-contained; show the sections they asked for,
newest first, and skip the maintainer intro above the first heading. If they
ask about earlier *updates on this machine* (not versions), the full history is
the vault log named above: one JSON line per update, oldest first.

Do not offer to update from here, and do not run the update check. If the user
asks whether something newer exists, point them at `/van-gogh:check-updates`.
