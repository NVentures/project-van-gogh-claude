---
name: morning-coffee
description: Daily briefing. Runs email scan + meeting prep for every event today, then renders a combined briefing organized as meeting-by-meeting prep briefs followed by outstanding items by business line. Use when the user types /van-gogh:morning-coffee or asks for their morning briefing, daily briefing, morning coffee, or what's on today.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Morning Coffee

Two scripts run in sequence. Your job: render the combined briefing in the prescribed format and offer to draft replies or agendas.

**How this reads.** The person reading it runs a business, not a
terminal. Short sentences, lead with the point, no vocabulary from the
data model, no filler openers. The full rules are the "Written Voice"
section of `DESIGN.md`; follow them for every line you write here.

## Resolved paths come from the script

Every `morning_coffee.py` JSON output includes a top-level `meta` block. Use these keys verbatim:

- `meta.vault_path`
- `meta.hotcache_path`
- `meta.weekly_dir`
- `meta.workspace_week_md`
- `meta.morning_coffee_md` — where to write the briefing
- `meta.user_first_name`
- `meta.action_items_heading`
- `meta.accounts` — list of `{label, provider, email, is_primary}`
- `meta.businesses[].{tag, display_name}`

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Detect tier

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

**If `True` (Tier 1):** Skip to Workflow: scripts fetch via OAuth.

**If `False` (Tier 2):** Fetch email + calendar via connector tools, write a tempfile (mint with Python's `tempfile`: never hardcode `/tmp`), pass `--input "$TMPFILE"` to both scripts.

macOS / Linux:
```bash
TMPFILE=$("$HOME/.config/van-gogh/venv/bin/python" -c "import tempfile,os; fd,p=tempfile.mkstemp(prefix='van-gogh-coffee-',suffix='.json'); os.close(fd); print(p)")
```

Windows (PowerShell):
```powershell
$TMPFILE = & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import tempfile,os; fd,p=tempfile.mkstemp(prefix='van-gogh-coffee-',suffix='.json'); os.close(fd); print(p)"
```

---

## Workflow

Run all three scripts. Wait for each to finish before rendering — do not assume any has hung. The meeting prep script logs each event as it resolves (1–3 min depending on calendar size).

macOS / Linux:
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/morning_coffee.py"
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_prep.py" --today
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/completion_scan.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\morning_coffee.py"
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_prep.py" --today
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\completion_scan.py"
```

Tier 2: append `--input "$TMPFILE"` to `morning_coffee.py` and `meeting_prep.py`. Pass `--input "$TMPFILE"` to `completion_scan.py` too.

**Side effects (intentional):**
- `morning_coffee.py` updates `wiki/weekly/week-{date}.md` in-place, marking `- [x]` for tasks already checked off in their project page.
- `morning_coffee.py` syncs `{meta.action_items_heading}` in hotcache from checked week-file action items.
- `completion_scan.py` is read-only until `--mark-done`.

---

## What the scripts produce

### morning_coffee.py output

```json
{
  "today": "YYYY-MM-DD",
  "week_file": "/path/to/week-{date}.md",
  "completed": [{"type": "deal|task", "description": "...", "detail": "..."}],
  "completed_keys": [...],
  "still_open_deals": [
    {"source", "subject", "name", "email", "section": "waiting|cold",
     "reply_age_days|age_days", "met_date": "YYYY-MM-DD or null"}
  ],
  "still_open_tasks": [{"project", "task"}],
  "today_calendar": [{"source", "title", "day", "time", "location"}],
  "travel": [
    {"kind": "flights|weather|departure", "trip": "...", "text": "...",
     "link": "(flights only)", "leave_by": "(departure only, may be null)",
     "drive_minutes": "(departure only, may be null)"}
  ],
  "slipping": [{"source", "subject", "name", "age_days_now", "age_days_monday"}],
  "newly_cold": [{"source", "subject", "name", "email", "age_days"}],
  "hotcache_alerts": [
    {"thread", "stage", "last_contact", "flags": [{"type", "days|days_left", "date"}]}
  ],
  "hotcache_sync": {
    "revivals": [{"deal", "newest_mail"}], "last_contact_updates": [{"deal", "to"}],
    "stale_days": 0, "stale": false
  },
  "recent_meetings_since": "YYYY-MM-DD",
  "recent_meetings": [
    {"title", "attendees": [...], "decisions": [...], "commitments": [...]}
  ],
  "met_candidates": [
    {"source", "subject", "name", "email", "met_date": "YYYY-MM-DD", "reply_age_days"}
  ],
  "status_change_alerts": [
    {"thread", "subject", "from", "from_email", "account", "signal", "snippet", "forwarded"}
  ],
  "buckets": [...], "buckets_md": "...",
  "front_page": [
    {"bucket_tag", "bucket_name", "function", "label", "why", "action",
     "account", "counterparty_email", "counterparty_name", "subject",
     "days_late", "due", "age_days", "key", "draft_note"}
  ],
  "front_page_md": "...", "fold_rows_md": "...", "folded_md": "...",
  "fold_rows": [{"tag", "display_name", "folded", "functions": [...], "flags": [...]}],
  "counts": {"open": 0, "late": 0, "front": 0, "folded": 0, "stale": 0},
  "drafts_done": [{"key", "web_link", "id", "provider", "label", "created"}],
  "page_note": "",
  "errors": []
}
```

The front-page keys and how to render them are in
[`_shared/front-page.md`](../_shared/front-page.md). `drafts_done` is what the
draft ledger already holds, so a rerun does not draft the same reply twice.
`page_note` is non-empty only when the last run could not update the web page;
print it as its own line at the end of the briefing.

`recent_meetings` are notetaker calls between the last briefing and now (`recent_meetings_since` is the lower bound — the prior briefing's date, so each run picks up new calls without overlapping afternoon-tea's same-day coverage). Empty when no notetaker key is configured.

`met_candidates` are still-open waiting deals carrying a `<!-- met: -->` token in the week file: a meeting happened since the last email, so the deal MAY be handled. A heuristic, never an auto-[x]; fold them into the Step 2 confirm question. `status_change_alerts` are counterparty kill/pass signals passed through from the week_review hub (review only, never auto-killed); surface them at the top of the briefing under DEAL STATUS CHANGES.

`travel_md` and `travel_file_md` are the finished Travel block, rendered by
code: paste one verbatim (the `_file_md` one carries the links) and never
write this section yourself. `travel` is the same notices as data, at most one
per trip per day, already deduplicated against a ledger so a rerun never
repeats one. Each notice carries finished `text`:
print it as written and do not recompute a time from it. A `departure` notice
whose `leave_by` is null means the drive time could not be established; its
text already says so, so do not fill the gap with an estimate of your own.
The list is empty when no trip is detected, which is the normal case on most
days. Trips are read from calendar events whose location names an airport
code, so an event with no location never becomes a trip.

`hotcache_sync` is the deterministic deal-metadata write-back that already ran. `revivals` are deals tagged dead that got fresh matched mail and were auto-flipped to active (surface them, see HOTCACHE REVIVALS + STALENESS). `stale_days`/`stale` report how old the hotcache `updated:` date was before this run bumped it. Safe-direction only: it never auto-marks a live deal dead and never revives a deal flagged `no_revive=1`.

### meeting_prep.py --today output

```json
{
  "meta": {...},
  "date": "YYYY-MM-DD",
  "meetings": [
    {
      "meeting": {"title": "...", "time": "...", "day": "...", "source": "..."},
      "attendees": [
        {"name": "...", "email": "...", "entity_page": "/path or null",
         "meeting_sources": ["/path/to/source.md", ...]}
      ],
      "hotcache_snippets": ["### Thread\n..."],
      "week_context": {"file": "/path", "lines": ["- [ ] ..."]},
      "unresolved_action_items": [{"source": "filename.md", "item": "..."}]
    }
  ]
}
```

---

## Step 0a: check Slack for anything from overnight (skip if not connected)

If a Slack connector is available, search the last 24 hours for context on
today's meetings and open items. Everything you search for comes from the
user's own config, never from a list kept here:

1. Each external attendee on today's calendar, by name and by their company.
2. Each business tag in `meta.businesses[]` and the priorities inside it
   (`meta.businesses[].priorities[].name`).
3. Any deal or thread named in the hotcache alerts the script already returned.

Scope the search with `newer_than:1d`. Fold anything useful into the relevant
meeting's prep block as an extra bullet. If nothing surfaces, say nothing:
never write a line announcing that a search found nothing.

If no Slack connector is configured, skip this step entirely and silently.

## Step 0b: check Teams for the same (skip if not connected)

Same idea, same sources, for Microsoft Teams. Search recent chat messages for
the external attendees on today's calendar and for the businesses in
`meta.businesses[]`. Never hardcode a person or a company here: the whole
point is that this works for whoever installed it.

If no Teams connector is configured, skip this step entirely and silently.

## Step 0c — Read entity pages and meeting sources

For each meeting in `meeting_prep` output:
- Read each attendee's `entity_page` (skip if null).
- Read the **first** `meeting_sources` entry per attendee (most recent; skip if empty).
- Do NOT read all sources — first one per attendee is enough.

---

## Step 1 — Render the briefing

Render in this order and no other: the read, the suggested focus task, the
front page, the day (travel notices first inside it, when there are any),
what happened since the last briefing, then the business rows. The order is the
product: the reader opens on what today is about, then on what to do first,
with the replies already written.

```
=== MORNING COFFEE: {weekday} {date} ===
```

Then THE READ, then `front_page_md`. The latter carries the opening sentence;
do not write one of your own above it.

---

### THE READ

Three or four sentences on what this morning is about, written by you from what
the script found. The full rules are in
[`_shared/front-page.md`](../_shared/front-page.md), "The read"; the one that
matters is that every claim traces to a field in the JSON. Morning Coffee's own
sources are `front_page`, `today_calendar`, `completed`, `slipping`,
`newly_cold`, `status_change_alerts` and `recent_meetings`.

Say what the day's shape is (three calls and one decision; or one thing that
has sat for nine days while six others can wait), what moved overnight, and
which single item matters most and why that one. No heading of its own and no
band: it is the first thing under the title rule, in plain prose.

---

### SUGGESTED FOCUS TASK

One item you choose, above the checklist: the work that compounds, not the work
that is loudest. Full rules in
[`_shared/front-page.md`](../_shared/front-page.md), "The suggested focus
task". Read `buckets` (the whole ledger), not just `front_page`, and weigh
leverage first, then the reader's stated `priorities[]`, then what is quietly
stuck.

Render it as its own block: the task, why this one beats everything else today,
the first move, and what it unblocks. Four short parts, no heading inside them.
If nothing in the ledger genuinely compounds, say so in one line and render no
block rather than manufacturing one.

---

### TRAVEL

`travel_md` is the finished block: paste it VERBATIM, directly under the focus
task and above the front page. Omit it entirely when it is empty, which is
what it is on most mornings.

Do not write this section yourself and do not restate a notice in your own
words. Code builds it from the calendar and the airline confirmation, and each
line was written against the flight's own timezone, which is often not the
reader's. Three rules follow from that, and they are why this block is not
yours to phrase:

- Never recompute or restate a time. The leave-by is the one number in this
  briefing where being wrong means a missed flight.
- Never add a terminal, a gate, or a drive time the notice does not carry.
  A confidently wrong terminal at 5 AM is worse than no terminal.
- When a notice says a drive time could not be established, keep that clause.
  It is the difference between a reader allowing extra time and a reader
  trusting a number that was never computed.

It sits second on the page because it is the only block whose deadline cannot
move: a flight leaves whether or not the briefing was read carefully. On the
Workbench it is outlined in amber, and a trip with nothing booked carries a
Find flights button.

---

### DRAFTS

`drafts_md` is the finished block: paste it VERBATIM, after the front page.
Omit it entirely when it is empty, which is what it is on a morning with
nothing drafted.

Do not write this section yourself and do not describe what a reply says.
Code builds it from the draft ledger, which already holds who each reply is
to, what it is about, which account it is in, and whether it has been sent.
Everything in it is a fact about a record, so a sentence of your own here can
only restate that record less accurately.

---

### THE FRONT PAGE

The script chose these. `front_page_md` is the finished block: paste it
VERBATIM, inside a fenced block, and change nothing. It already carries the
opening sentence, so do not write one above it.

Before you paste it, draft the replies: see
[`_shared/front-page.md`](../_shared/front-page.md), "Drafting, before you
render". Every front-page item whose `action` is `email reply` gets a draft in that account's Drafts folder before the reader arrives.

---

### YOUR DAY: {N} meetings

Travel is not rendered here. It is its own block under the focus task, where
a leave-by time is read before the day's meetings rather than after them.

For each meeting in `meeting_prep.meetings`, sorted by time, render a prep block.

**Meetings with external attendees:**
```
{time}  {title}  [{source}]
{1-line description: who this is and what the meeting is about, synthesized from entity page, meeting history, email context}

Prep:
• {most important context point: deal status, last touchpoint, open commitment}
• {second context point from meeting notes/email}
• {talking point or specific agenda item}
• Win condition: {the single most valuable outcome for this meeting}
```

**Meetings with no external attendees** (internal, personal — school run, yoga, etc.):
```
{time}  {title}  [{source}]
```
No prep section. These are schedule context only.

**Rules:**
- If no meeting history and no entity page: note "No prior history in vault." Suggest 1–2 talking points based on what you know about the company/person from memory.
- If multiple attendees: synthesize across all context — don't repeat per-person.
- `hotcache_snippets` and `unresolved_action_items` → open commitments.
- Keep each prep block to 4–6 bullets. Dense, not verbose.

---

### RECENT CALLS: SINCE LAST BRIEFING (omit section if `recent_meetings` empty)

From `recent_meetings` (notetaker calls since `recent_meetings_since`). Surface the commitments and decisions so nothing said on a call falls through into today. Roll any commitment that is still open into the OUTSTANDING list below.

```
──────────────────────────────────────────────
SINCE YOUR LAST BRIEFING: {N} call(s)
──────────────────────────────────────────────
{title}  ({attendees, comma-separated})
  Decisions:   {each decision, or omit line if none}
  You owe:     {each commitment, or omit line if none}
```

Skip a meeting entirely if it has no decisions and no commitments. Do not echo raw summary text — only the extracted decision/commitment lines.

---

### COMPLETED SINCE LAST RUN

```
──────────────────────────────────────────────
COMPLETED SINCE LAST RUN ({N})
──────────────────────────────────────────────
✓ [TYPE]  {description}: {detail}
(If empty: "Nothing auto-resolved.")
```

---

---

### THE REST

`fold_rows_md` is the finished block: paste it VERBATIM. One line per
business, the folded count broken out by function, at most two flags.

For the written file and the web page use `front_page_file_md`, and do NOT
write the rows there at all: `folded_md` opens with the same business names, so
writing both lists every business twice. The rows are for the terminal, which
has no folds. (The `_file_md` keys exist because the terminal render aligns its
columns with whitespace, which every markdown renderer collapses into one
run-on paragraph.)

Nothing after it: do not print a menu of the fold commands. The reader can say
"open Harbor Solar", "open Harbor Solar sales", "open everything" or
"file X under Cedar" whenever they want, and how to handle each is in
[`_shared/front-page.md`](../_shared/front-page.md), "Opening a fold". A
briefing that ends by teaching its own syntax reads as a chatbot, not a
briefing.

There are no standalone alert sections. A deal status change, an overdue deal,
a revival, a contact gone quiet: each is already an item on the front page or a
flag on a business row, and each is still inside its fold. Do not add a section
for any of them.

When `front_page_md` is empty (an install with no businesses configured yet, or
a front page that failed to build), fall back to `buckets_md` and say one line
about it. Never invent a front page of your own.

**Noise suppression.** Do not surface an item that is purely social, a
newsletter, a receipt, a calendar invite echo, or an automated notification.
The user's own exclusions live in their workspace memory; read them from there
rather than assuming any name is or is not important. Never hardcode a person
into this skill.

---

---

### THE WEB PAGE

This briefing owns one permanent private page and republishes to the same URL
every run. The full procedure is
[`_shared/front-page.md`](../_shared/front-page.md), "The web page". In short:
read the stored URL, read that artifact with `action: "read"`, build the HTML
through `briefing_html.render_page` over `briefing_html.redact`, and only then
publish the page to that same URL and record it.

End the briefing with the page line, whichever one applies: `page_note` in the
script's output carries the link when the page is current, and why it did not
update plus the day it still shows when it does not. Print it either way. A
page nobody was handed a link to is as useless as one that silently went stale.

Fail open. If there is no publishing surface in this session, no network, or
`briefing.publish_page` is false, record the failure and print that same line.
Never retry in a loop. A briefing that could not update its page is still a
finished briefing.

---


---

## Step 2 — Confirm completions

Use `completion_scan.py` candidates:
1. If empty, skip silently.
2. Otherwise ask with AskUserQuestion (multi-select), one option per candidate with evidence. Phrase: "These look done based on recent email/meetings — which should I check off?"
3. Pipe confirmed JSON to `completion_scan.py --mark-done`.
4. Exclude confirmed items from the outstanding section in the written file.

Also fold any `met_candidates` into the same multi-select as lower-confidence options, labeled "met {met_date}, confirm handled?". A met candidate is a heuristic (a meeting happened since the last email), never auto-checked; only mark it done if the user confirms.

---

## Step 3 — Write morning-coffee.md

Write to `meta.morning_coffee_md`. Frontmatter:

```markdown
---
date: {YYYY-MM-DD}
generated: {YYYY-MM-DD}
completed_keys:
  - {key from output["completed_keys"]}
---
```

Body: full rendered briefing. Use `##` section headers, `- [ ]` checkboxes for outstanding items. Strip `═══` ASCII banner lines: terminal display only. EXCEPTION: the `─` bands inside `buckets_md` are part of that section and survive into the written file. Write the bucket section from `render_buckets_md`'s md mode when the script provides it.

---

**Shape of the file**, in this order:

This is the terminal order minus the read, and it is the order the
Workbench page and the digest both inherit, because both render this file.
Sections 1 to 4 must match the terminal render's order exactly; a section
that moves here moves on every surface the reader actually sees.

1. The suggested focus task, the block you chose above. Same four parts, no
   heading inside them.
2. `travel_file_md` verbatim, directly under the focus task, when it is
   non-empty. Use the `_file_md` key, never `travel_md`: a trip with nothing
   booked carries a hyperlink there, and a file is read where no button
   exists.
3. The front page: `front_page_file_md`, never `front_page_md`. The
   terminal key aligns its columns with whitespace and every markdown
   renderer collapses that into one run-on paragraph, which is how it
   reached a published page once already.
4. `drafts_file_md` verbatim, after the front page. Same block as the terminal
   render, except each waiting reply's subject is a hyperlink to it in its own
   Drafts folder. Under it, keep one `> [!note]- {counterparty}: {subject}`
   callout per reply holding the full text, whether or not it reached a
   Drafts folder.
5. The meeting prep briefs, one closed callout each.
6. `folded_md` verbatim: one closed callout per business, the whole ledger.

Closed callouts are the point. Everything is on the page; almost none of it is
on the screen until the reader opens it.

## Step 4 — Offer next steps

**Overdue deliverables.** When `nudges` is non-empty, list each one after the
briefing (title, how many days late, the bucket it belongs to) and offer to
draft it. One line per nudge, then a single offer covering all of them.

Draft in the chat first: plain text for an email, a plain-ASCII sketch for a
deck or a sheet. Do not create a file until the user says to, and do not send
anything, ever, without a separate explicit instruction.

When `nudges` is empty, omit this entirely. Do not say "no nudges".


- "Want me to draft an agenda or talking points for any of these meetings?"
- "Want me to draft a reply for any open deals?"
- "Any of these you want to dig into?"

---

## Error handling

- `errors` contains "No week file found": tell the user to run `/van-gogh:week` first.
- `meeting_prep.py --today` returns empty `meetings` array: render briefing without meeting preps, note calendar fetch failed.
- `errors` non-empty for other reasons: render what's available, note what's missing.
