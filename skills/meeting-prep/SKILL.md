---
name: meeting-prep
description: >-
  Pre-call one-pager. Finds the next meeting with external attendees, or every
  meeting today, and pulls entity pages, meeting history, hotcache deal
  context, week.md threads, the live mail with each attendee, and whatever the
  user's connectors (a CRM, for one) hold on them. Renders a 60-second brief
  with attendee summary, context, last touchpoints, the mail, open
  commitments, win condition, and talking points. Use when the user types
  /van-gogh:meeting-prep or asks to prep for a call or for the day's calls.
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

Default (`--meeting next`) finds the next event with external attendees. Takes 10-15 seconds, and longer when a connector is set up: each person asked about costs it up to a minute the first time that day, and nothing after that.

Wait for full JSON before proceeding.

### The whole day

When the argument is `--today`, or the user asks to prep for today's calls, pass `--today` instead of `--meeting`:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_prep.py" --today
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_prep.py" --today
```

It returns `{"meta", "date", "connectors", "meetings": [...]}`, where each entry of `meetings` has the single-meeting shape below. Render it as described under **The whole day, rendered**.

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
      "meeting_sources": ["/path/to/wiki/sources/<Counterparty> - 2026-05-01.md"],
      "first_meeting": false,
      "mail": {
        "checked": true,
        "window_days": 60,
        "accounts_read": ["<account label>"],
        "accounts_failed": [],
        "last_word": "them",
        "why": "",
        "threads": [
          {"subject": "<subject>", "last_date": "2026-05-20", "last_from": "them",
           "snippet": "<opening of the newest message>", "account": "<account label>",
           "messages_seen": 3}
        ]
      },
      "connectors": [
        {"name": "crm", "status": "ok", "reason": "",
         "findings": [{"tool": "<tool>", "args": {}, "text": "<what the system returned>"}]}
      ]
    }
  ],
  "connectors": [{"name": "crm", "about": "<what it holds>"}],
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

0. **Read `mail` and `connectors` as they are.** They arrive complete in the JSON; there is nothing further to fetch. How to report them is under **The mail and the connectors** below.
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
  First meeting. [anything known, with where it came from]

### Context
[hotcache_snippets content, stripped to the key facts, 3-5 lines per thread]
  None: no active threads mention these attendees.

### Last touchpoints
- [Date from meeting source filename or frontmatter]: [one-line: topic, what was decided/committed]
- [Prior meeting if available]
  No prior meetings in vault.

### In the mail
- [Name]: [last_date], [who wrote last], "[subject]". [What the snippet says, one clause]
  No mail with [Name] in the last 60 days.

### From [connector name]
- [Name]: [what that system holds: role, company, deal or stage, last activity]

### Open commitments
- [Item from unresolved_action_items, owned by {meta.user_first_name}]
  Nothing tracked as open.

### Week priorities
[week_context lines that are task bullets, verbatim, max 3]
  Not in current week priorities.

---

### Win condition
[Synthesize from context + open items: the single most valuable outcome {meta.user_first_name} could leave this call with]

### Talking points
1. [Grounded in context or last touchpoint]
2. [Address any open commitment]
3. [Forward-looking ask or next step]
```

---

## A first meeting, and sections with nothing in them

The format above is the full shape. Two rules keep it from filling up with lines that say nothing.

**A first meeting is stated once.** When an attendee's `first_meeting` is `true`, no meeting with them has ever been filed. Their line under Who's on the call is:

```
**[Name]**: First meeting. [Anything known: role and company from their entity page, the mail, or a connector, with where it came from]
```

When nothing at all is known, the line is just `**[Name]**: First meeting.` Do not also write "no prior meetings in the vault" or "not yet filed" for that person, anywhere in the brief. The one sentence covers all of it.

Never write "no entity page" about anyone, first meeting or not. Whether the vault has a page for someone is the vault's bookkeeping. The reader needs what is known about the person, and nothing about where it is or is not filed.

The section about the deals and threads these people are part of is headed `Context`, never "Deal context".

**A section with nothing to report is left out.** Context, Last touchpoints, In the mail, a connector section, Open commitments and Week priorities each appear only when they have something to say. Do not print a heading over a line such as "None", "Nothing tracked as open" or "Not in current week priorities". The empty-state lines in the format are for a single person inside a section that others fill, never for a whole section.

Three sentences are never dropped, because each is a fact the reader would otherwise assume the opposite of:

- "Mail with [Name] was not read: [why]."
- "([account] could not be read)."
- A connector's own failure line (`needs-auth`, `absent` or `error`).

A connector section appears when that system holds something on at least one person on the call, or when it failed. When every attendee came back `nothing` or `skipped`, leave the whole section out: a line saying the CRM holds nothing on anyone is a heading over nothing. Inside a section that does appear, people it holds nothing on are not listed.

Who's on the call, Win condition and Talking points always appear. On a first meeting with nothing known, the win condition and talking points are about finding out, and say so.

Pull what is relevant and stop. A fact that does not change how the user walks into the call is not in the brief.

## The mail and the connectors

These two sections report what other systems say. They are the part of a prep most likely to be wrong in a way that costs something, so the rules are narrow.

**In the mail.** One line per thread in `mail.threads`, newest first, grouped by person. `last_from` is `you`, `them` or `someone else`: write "you wrote last", "[Name] wrote last" or "someone else on the thread wrote last". Say who wrote last and when. Do not say a reply is owed, in either direction, unless the snippet itself asks for one. A person on several of today's calls is listed once.

- `checked` true and no threads: "No mail with [Name] in the last 60 days."
- `checked` false: "Mail with [Name] was not read: [why]." This is a different sentence from the one above and must never be replaced by it. Not looked at is not the same as nothing there.
- `accounts_failed` not empty while `checked` is true: add "([account] could not be read)" to that person's first line.

**From a connector.** When the top-level `connectors` list is empty, nothing is set up: leave the section out and say nothing about connectors anywhere in the brief. Mentioning it every morning is noise. Otherwise one section per connector, headed with its `name`.

- `status` `ok`: report only what `findings[].text` states. It is a quotation from that system. Attribute it ("per crm") and never fold it into Context or the Win condition as though the vault had said it. When it disagrees with the vault, give both and say they disagree.
- `status` `nothing`: say nothing about that person. If nobody on the call has a finding and the connector did not fail, there is no section.
- any other status (`needs-auth`, `absent`, `error`, `skipped`): one line for the whole connector, using its `reason`, not one per person.
- Never invent a field the text does not carry. An empty stage is "no stage recorded", not a guess.

Talking points may draw on both sections. Each one that does says where it came from.

## The whole day, rendered

For `--today`, the brief is one document:

```
## Today's calls: [date]
[One line: how many calls have outside attendees, and the one that matters most with the reason]

## [Time]  [Meeting Title]
[the single-meeting sections, in the same order]
```

- Meetings in time order. A meeting with no attendees in the JSON has nobody else on the invite, which says nothing about what it is: a hold, time off, or a call whose invite simply lists no one. List those together on one closing line by their titles ("Also on the calendar: ...") and write no sections for them. Do not call them internal.
- A meeting with more than six attendees gets the short form: Who's on the call (names and one clause each), In the mail only for people with a thread, Win condition. Nobody preps eight people.
- An empty `meetings` list: one line saying there are no calls left today. That is a complete answer.

## When nobody is there

The JSON carries `meta.unattended`. When it is `true`, a scheduled run is mailing exactly what this skill prints, to someone about to walk into these calls.

- Start with the brief's first heading. No preamble.
- Write nothing about how the run went: no notes on what you looked up or could not, no defects, no observations, no status line, no question, no offer. A line in a section saying a mailbox or a connector was not read is part of the brief and stays.
- After the last line of the brief, print this line by itself, and nothing after it:

```
END OF BRIEF
```

Whatever follows that line is cut before the email is sent. That is the guarantee; the rules above are so that nothing needs cutting.

When `meta.unattended` is `false`, do not print the marker.

---

## Edge cases

**No attendees found:** Calendar event exists but has no external attendees (internal call, blocked time). Run with a specific title fragment or time: `/van-gogh:meeting-prep "<name>"` or `/van-gogh:meeting-prep 14:00`.

**Someone the vault has no page for:** Say what is known about them and nothing about the missing page. Don't stub one unless the user asks: one-off attendees aren't worth filing until they appear in a second meeting.

**Meeting not found:** Tell the user which meetings ARE upcoming (print the titles from all_events before the script exits with error).

**Calendar auth error:** Either Google account's calendar will silently fail if tokens are expired (script logs `[warn]`). Outlook is the most reliable source. If only Outlook returned events, note it in the brief header.

---
