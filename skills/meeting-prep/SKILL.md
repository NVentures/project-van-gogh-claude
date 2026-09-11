---
name: meeting-prep
description: Pre-call one-pager. Finds the next meeting with external attendees, pulls entity pages + meeting history + hotcache deal context + week.md threads. Renders a 60-second brief with attendee summary, deal context, last touchpoints, open commitments, win condition, and talking points. Use when the user types /van-gogh:meeting-prep or asks to prep for a call.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Meeting Prep

All data aggregation is handled by a Python script. Your job: read the returned entity pages and meeting sources, then synthesize the one-pager.

## Resolved paths come from the script

`meeting_prep.py`'s JSON output includes a top-level `meta` block (same shape
as `app/skill_context.py` — run that if you need values without the full data
pull). Use these keys verbatim:
- `meta.user_first_name`
- `meta.accounts` — list of configured accounts, each `{label, provider, email, is_primary}`; the `source` field in JSON uses each account's `label` verbatim (iterate the list, don't assume a fixed set)

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

**If `False` (Tier 2: connector mode):** Fetch upcoming calendar events via your Microsoft 365 and Gmail connector tools, write a tempfile, and run the script with `--input`.

### Tier 2: what to fetch

Upcoming events for the **next 7 days** across all accounts. Per event: title, start datetime (ISO UTC), source account label, and the full attendee list (name + email). Include internal attendees too — the script filters known team members itself.

### Tier 2: write the tempfile

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp /tmp/van-gogh-prep-XXXXXX.json)
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

Write a JSON object to `$TMPFILE`:

```json
{
  "calendar": [
    {"source": "Outlook", "title": "Acme <> You", "start": "2026-05-29T20:00:00Z",
     "attendees": [{"name": "John Smith", "email": "john@acme.com"}]}
  ]
}
```

`start` is ISO UTC (a `Z` suffix or `+00:00` offset both work).

### Tier 2: run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_prep.py" --input "$TMPFILE" [--meeting "next|<title fragment>|HH:MM"]
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_prep.py" --input "$TMPFILE" [--meeting "next|<title fragment>|HH:MM"]
```

Then continue with **After running the script** below.

---

## Workflow

**Tier 1 only: skip if you already ran with `--input` above:**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_prep.py" [--meeting "next|<title fragment>|HH:MM"]
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_prep.py" [--meeting "next|<title fragment>|HH:MM"]
```

Default (`--meeting next`) finds the next event with external attendees. Takes 10-15 seconds.

Wait for full JSON before proceeding.

---

## What the script returns

```json
{
  "meeting": {
    "title": "<Counterparty> <> {meta.user_first_name}",
    "time": "1:00 PM PT / 4:00 PM ET",
    "day": "Fri May 22",
    "source": "<source account label>"
  },
  "attendees": [
    {
      "name": "<Counterparty Name>",
      "email": "<email>",
      "entity_page": "/path/to/wiki/entities/<Counterparty Name>.md",
      "meeting_sources": ["/path/to/wiki/sources/<Counterparty> - 2026-05-01.md"]
    }
  ],
  "hotcache_snippets": ["### <Topic>\n..."],
  "week_context": {
    "file": "/path/to/week-2026-05-18.md",
    "lines": ["- [ ] [DEAL] <Counterparty Name>: ..."]
  },
  "unresolved_action_items": [
    {"source": "<Counterparty> - 2026-05-01.md", "item": "{meta.user_first_name} to send updated <document>"}
  ]
}
```

---

## After running the script

1. **Read each `entity_page`** path returned (skip if null — no page exists yet).
2. **Read the most recent `meeting_source`** per attendee (first in list = most recent). Skip if list is empty.
3. Do NOT read all meeting sources — first one per attendee is enough.
4. **Render the brief** using the format below.

---

## Output format

```
## [Meeting Title]
[Day]  |  [Time]

---

### Who's on the call
**[Name]**: [one-line summary from entity page: role, company, current deal status]
  No entity page: first meeting or not yet filed.

### Deal context
[hotcache_snippets content, stripped to the key facts, 3-5 lines per thread]
  None: no active threads mention these attendees.

### Last touchpoints
- [Date from meeting source filename or frontmatter]: [one-line: topic, what was decided/committed]
- [Prior meeting if available]
  No prior meetings in vault.

### Open commitments
- [Item from unresolved_action_items, owned by {meta.user_first_name}]
  Nothing tracked as open.

### Week priorities
[week_context lines that are task bullets, verbatim, max 3]
  Not in current week priorities.

---

### Win condition
[Synthesize from deal context + open items: the single most valuable outcome {meta.user_first_name} could leave this call with]

### Talking points
1. [Grounded in deal context or last touchpoint]
2. [Address any open commitment]
3. [Forward-looking ask or next step]
```

---

## Edge cases

**No attendees found:** Calendar event exists but has no external attendees (internal call, blocked time). Run with a specific title fragment or time: `/van-gogh:meeting-prep "<name>"` or `/van-gogh:meeting-prep 14:00`.

**No entity page:** Note it inline. Don't stub one unless the user asks — one-off attendees aren't worth filing until they appear in a second meeting.

**Meeting not found:** Tell the user which meetings ARE upcoming (print the titles from all_events before the script exits with error).

**Calendar auth error:** Either Google account's calendar will silently fail if tokens are expired (script logs `[warn]`). Outlook is the most reliable source. If only Outlook returned events, note it in the brief header.

---
