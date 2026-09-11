---
name: relationship-radar
description: Scan entity pages for contacts going cold. Flags 30+ days as yellow (check in soon) and 60+ days as red (reach out now). Cross-references the hotcache to skip actively-managed deal contacts, and drafts check-in messages in the user's voice. Use when the user types /van-gogh:relationship-radar or asks about neglected contacts, who they have lost touch with, or who is going cold.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Relationship Radar

A Python script does all scanning and draft generation. Your job: run it, read the written radar page, and render a clean summary the user can act on.

## Resolved paths come from the script context

This script is a file-writer. It does NOT emit JSON, so there is no `meta` block in its output. To get resolved paths, run:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/skill_context.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\skill_context.py"
```

Use these keys verbatim, never hardcode them:

- `meta.relationship_radar_path`: the radar page the script writes
- `meta.voice_drafts_path`: where check-in drafts are appended for later voice calibration
- `meta.user_first_name`

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Workflow

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/relationship_radar.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\relationship_radar.py"
```

Takes 20 to 40 seconds (it drafts a check-in per flagged contact). Wait for the final `SUMMARY` line before proceeding. The script prints plain text, not JSON. The last line is:

```
SUMMARY red=N yellow=N in_deal=N
```

The script also writes the full radar page to `meta.relationship_radar_path` and appends each draft to `meta.voice_drafts_path`.

---

## After running

1. Read the radar page at `meta.relationship_radar_path`.
2. Render the output inline: red contacts first, then yellow.
3. For each red contact that has a draft, show the draft and ask whether the user wants to send it, edit it, or skip it.
4. For yellow contacts, show name, days cold, and a short context preview. No draft action is needed unless the user asks.
5. In the In Active Deal section, list names only, one per line.

## Output interpretation

- **Red (60+ days):** Needs outreach this week. A draft is generated if the entity page has enough context.
- **Yellow (30 to 59 days):** On the radar. Drafts are generated for the first few that have context.
- **In Active Deal:** Skipped, because these contacts appear in the hotcache active threads. The morning briefing already tracks them.
- **No draft shown:** The entity page is a stub with insufficient context. Note it and ask whether the user wants to add context before drafting.

---

## Edge cases

**All contacts in active deal or all recent:** The radar returns empty (`SUMMARY red=0 yellow=0 in_deal=0`). That is good news: nothing is going cold.

**Draft feels generic:** The voice is grounded in the configured voice guide. Offer to regenerate with more specific relationship context that the user provides.

**A contact should be treated as internal but appears in the radar, or an org slipped through as a person:** These are filtered by name lists inside `app/relationship_radar.py`. Tell the user, and only adjust the script if they ask (this skill does not edit `app/`).
