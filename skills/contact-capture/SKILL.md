---
name: contact-capture
description: Save the people you actually correspond with into Google Contacts, automatically. Finds everyone the account owner has had a real two-way email exchange with, promotes them out of Gmail's hidden "Other contacts" bucket into the real address book, and fills in title, phone and company from their own signature. Use when the user types /van-gogh:contact-capture, asks to save or capture contacts, mentions that their address book is empty or out of date, or complains that saving a contact in Gmail is too manual.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Contact Capture

Gmail already collects every address the user sends to, in a hidden bucket
called "Other contacts". That is where the thousands of people they have
emailed actually live. The handful in their real address book are just the ones
they clicked to save, one menu at a time.

This skill closes that gap. A Python script does all the work; your job is to
run it, read the report, and get a decision on the first write.

**The default writes nothing.** Every run is a dry run unless `--apply` is
passed. The address book is easy to add to and tedious to clean up, so the
first real write always follows a report the user has actually looked at.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Step 1: the dry run

Always start here, including on a machine that has run this before. It reports
who would be captured and touches nothing.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/contact_capture.py" --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\contact_capture.py" --json
```

Useful flags:

- `--days N` how far back to look for exchanges (default 365). The first run on
  a long-lived mailbox is the slow one; later weekly runs only need `--days 14`.
- `--account "Gmail"` restrict to one configured account label. Default is
  every Google account in the config.
- `--limit N` cap how many are written in a single run.

### The output shape

```json
{
  "dry_run": true,
  "candidates": 214,
  "promoted": 0,
  "accounts": [{
    "label": "Gmail", "email": "owner@company.com",
    "other_contacts": 9713, "already_saved": 112,
    "candidates": 214,
    "sample": [{"email": "...", "name": "...", "last_contact": "2026-09-02",
                "sent_count": 4, "reply_count": 3,
                "title": "Chief Financial Officer", "org": "Northwind"}]
  }]
}
```

### What to say

Report it as a short before-and-after, not a table dump:

- how many addresses are sitting in Other contacts (`other_contacts`)
- how many are saved today (`already_saved`)
- how many this would add (`candidates`)
- five or six names from `sample`, with their title and company, so the user
  can sanity-check the filter caught real people

Then ask whether to save them. Use AskUserQuestion, and offer capturing them
all against starting with a capped batch.

## Step 2: apply, once they say yes

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/contact_capture.py" --apply --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\contact_capture.py" --apply --json
```

Report `promoted`, and read out anything in `failed`. A contact already saved
is skipped rather than duplicated, so re-running is safe.

## Step 3: offer the weekly job

Once the first batch is in, the point is that it never needs doing again. The
weekly job is part of the standard scheduler install:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" install
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scheduler_setup.py" install
```

Turn it on with `contact_capture.enabled` in config (via
`/van-gogh:update-settings`); `day` and `time` set the slot, `window_days` how
far back each weekly run looks.

---

## If the script reports a permissions error

Contact capture needs two Google permissions that installs predating it never
granted: read Other contacts, and manage contacts. An older sign-in produces a
message about insufficient scopes.

This cannot be fixed by re-running. The user signs in again:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py"
```

Say plainly that the extra permission is what lets it read the hidden bucket
and write the contact, and that nothing is written until they approve a report.

---

## Rules

- **Never pass `--apply` without an explicit yes** in the current conversation.
  A dry-run report the user has not responded to is not approval.
- **Never present the count as contacts "found"**: they were always there. The
  number that matters is how many are being saved properly for the first time.
- Two-way exchange is the bar: they were written to, and they wrote back. If
  the user wants everyone they ever emailed, that is a different filter and a
  much larger, noisier address book; say so before changing it.
- The script writes to a real address book. Nothing in this skill deletes or
  merges a contact, and neither should you.
