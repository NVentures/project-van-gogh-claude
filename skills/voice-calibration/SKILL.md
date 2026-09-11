---
name: voice-calibration
description: Weekly voice learning loop. Pulls the last 7 days of sent email, extracts the user's voice patterns, and appends a dated summary to the Voice Snapshot file. If AI drafts were saved this week by other skills, it also diffs them against the matching sent emails to find where AI output diverged from the user's edits. Use when the user types /van-gogh:voice-calibration or asks to refresh their voice patterns from recent email.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Voice Calibration

A Python script does the email pull, pattern extraction, and draft diffing. Your job: run it, then surface the new voice-pattern learnings.

This is meant to run on a weekly cadence (Sunday evening is the intended slot), so the snapshot stays current regardless of whether any drafting tool was used that week.

## Resolved paths come from the script context

This script is a file-writer and prints a plain-text summary, not JSON. To get resolved paths, run:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/skill_context.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\skill_context.py"
```

Use these keys verbatim, never hardcode them:

- `meta.voice_snapshot_path`: the file the script appends a dated learning block to
- `meta.voice_drafts_path`: the draft log it diffs against, if present

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Workflow

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/cos_voice_calibration.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\cos_voice_calibration.py"
```

Wait for the final line, which is plain text:

```
SUMMARY sent=N drafts=N matched=N
```

- `sent`: sent emails analyzed in the 7-day window
- `drafts`: AI drafts found in the draft log
- `matched`: drafts matched against a sent email for a diff

Then report one line back to the user:

```
Voice Snapshot updated. N sent emails analyzed, N drafts matched.
```

---

## What it produces

The script appends a `### Week of YYYY-MM-DD` section to the file at `meta.voice_snapshot_path` with:

- **Voice patterns from sent mail**: opening style, length, sign-off, tone by recipient type, vocabulary habits, structural moves
- **AI draft diffs** (if any): per-contact edit bullets plus recurring divergence patterns

After running, read the new section from `meta.voice_snapshot_path` and surface the new learnings inline: 3 to 6 bullets covering the most useful pattern shifts this week. If `matched=0`, say so and note the snapshot still captured fresh sent-mail patterns.

## When to ask the user

- Script fails entirely: surface the error, and suggest confirming the email accounts are authenticated and the drafting CLI is on PATH.
- `sent=0`: the lookback window had no sent mail. Note it, and the snapshot will be thin this week.
- Draft diffs are optional. If `drafts=0`, that is normal: the script still runs fully on sent mail alone.
