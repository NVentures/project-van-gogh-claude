---
name: calendar-stub-check
description: Stub unrecorded meetings into the vault sources directory. Scans a day's calendar across all configured accounts, finds meetings that were never filed as a source page, and writes a placeholder stub for each so nothing slips through unrecorded. Use when the user types /van-gogh:calendar-stub-check or asks to backfill stubs for meetings that have not been written up yet.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Calendar Stub Check

A Python script does all the work. It scans the calendar, compares against existing source pages, and writes a stub file for any meeting that was not recorded. Your job: run it and report how many stubs were created.

This normally runs on a daily scheduler. This skill is the manual trigger.

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

- `meta.sources_dir`: where the meeting stubs are written

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Detect tier and fetch data

Before running the script, check whether Tier 1 OAuth credentials are present:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

**If `True` (Tier 1):** Skip to Workflow: the script fetches the calendar directly via OAuth.

**If `False` (Tier 2: connector mode):** Fetch **yesterday's** calendar events (Pacific time) via your Microsoft 365 and Gmail connector tools, write a tempfile, and run the script with `--input`.

### Tier 2: what to fetch

Yesterday's real meetings across all accounts. **Drop** all-day events, cancelled events, and events you declined. Per event: title, source account label, attendee display names (**excluding yourself**), and the invite agenda/body text. A meeting with no other attendees isn't worth stubbing — skip it.

### Tier 2: write the tempfile

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp /tmp/van-gogh-stub-XXXXXX.json)
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

Write a JSON object to `$TMPFILE`:

```json
{
  "calendar": [
    {"source": "Outlook", "title": "Acme pricing call",
     "attendees": ["John Smith", "Jane Roe"], "agenda": "Discuss Q3 pricing..."}
  ]
}
```

### Tier 2: run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/calendar_stub_check.py" --input "$TMPFILE" [--day YYYY-MM-DD] [--dry-run]
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\calendar_stub_check.py" --input "$TMPFILE" [--day YYYY-MM-DD] [--dry-run]
```

When using `--input`, the connector fetch determines which day's events are in the file; `--day` only sets the date stamped onto the stubs (default: yesterday PT). Then continue with **After running** below.

---

## Workflow

**Tier 1 only: skip if you already ran with `--input` above:**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/calendar_stub_check.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\calendar_stub_check.py"
```

By default it targets yesterday (Pacific). Flags:

- `--day YYYY-MM-DD`: target a specific day instead of yesterday
- `--dry-run`: report what would be stubbed without writing anything

The script prints a plain-text summary line:

```
calendar_stub_check YYYY-MM-DD: N event(s) scanned, N already recorded, created N stub(s).
```

Each created stub is listed below the summary as `  + <filename>`. Any data-collection errors print to stderr as `  ! <error>`.

---

## After running

Report back concisely:

- How many stubs were created, and for which meetings (the `  + <filename>` lines).
- How many events were already recorded and skipped.
- If 0 stubs were created, say so: every meeting that day is already filed.

The stubs land in `meta.sources_dir`. They are placeholders, so offer to fill any of them in via /van-gogh:meeting-ingest if the user wants the full write-up.

## When to ask the user

- Errors on stderr (calendar auth failure on an account): note which account failed, and report the stubs that were still created from the accounts that succeeded.
- If the user wants a different day, rerun with `--day YYYY-MM-DD`.
- If they want to preview without writing, rerun with `--dry-run`.
