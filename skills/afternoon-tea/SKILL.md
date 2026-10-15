---
name: afternoon-tea
description: >-
  End-of-day retrospective that pulls today's sent mail across all configured accounts, notetaker
  meetings, tomorrow's calendar, and morning-coffee.md action items. Reports what the automatic
  check-off closed today, each with the id that reopens it, names any inbound lure it held
  back, and renders a focused daily retro: done today, wins, closed today, still-open items,
  meeting commitments, and tomorrow's preview. Never asks the reader to confirm a close. Use when
  the user types /van-gogh:afternoon-tea or asks for their end-of-day wrap-up, afternoon tea,
  daily retro, or what got done today.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Afternoon Tea

All data work is handled by a Python script. It pulls sent mail, notetaker meetings, tomorrow's calendar, and morning-coffee.md action items, then auto-detects which open tasks were completed based on fuzzy match against today's activity. Your job: check off completed items in morning-coffee.md, render the retro, and write it to afternoon-tea.md.

**How this reads.** The person reading it runs a business, not a
terminal. Short sentences, lead with the point, no vocabulary from the
data model, no filler openers. The full rules are the "Written Voice"
section of `DESIGN.md`; follow them for every line you write here.

## Resolved paths come from the script

`afternoon_tea.py`'s JSON output includes a top-level `meta` block (same shape
as `app/skill_context.py` — run that if you need values without the full
data pull). Reference these keys verbatim instead of substituting placeholders:

- `meta.accounts` — list of configured accounts, each `{label, provider, email, is_primary}`. The `account` / `source` fields in JSON output use each account's `label` verbatim (a non-primary Google account shows as `Google (<label>)`). Iterate the list; don't assume a fixed primary/secondary/Outlook set.
- `meta.user_first_name`
- `meta.morning_coffee_md` — the morning-coffee.md to read and check off (in the vault `van-gogh/`)
- `meta.afternoon_tea_md` — where to write the retro (in the vault `van-gogh/`)

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Detect tier and fetch data

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

**If `True` (Tier 1):** Skip to Workflow.

**If `False` (Tier 2: connector mode):** Fetch today's sent mail, the last 2 days of inbound mail, and tomorrow's calendar via connector tools.

### Tier 2: what to fetch

1. **Sent mail today** — emails sent from all accounts today. Per item: subject, recipient name + email, sent date (`YYYY-MM-DD`), source account label.
2. **Inbound mail (2-day window)** — the latest external message per thread received over today + yesterday, across all accounts. This feeds the counterparty kill/pass scan and the deal-activity view; a sent-only retro is blind to a pass, which arrives inbound. Per item: subject, sender name + email, received date (`YYYY-MM-DD`), body preview (~300 chars), source account label, and whether the sender is internal (a forwarded kill keeps `internal: true` but is still scanned).
3. **Tomorrow's calendar** — events from all accounts for tomorrow. Per event: title, start datetime (ISO UTC), source label.

Meeting notes are fetched by the script directly via the notetaker's API key — no connector needed for them.

### Tier 2: write the tempfile

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp /tmp/van-gogh-tea-XXXXXX.json)
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

Field names below are the keys the script reads directly (`load_input` does no
renaming) — match them exactly. `date` is a plain `YYYY-MM-DD` (user timezone).

```json
{
  "sent_today": [
    {"account": "Gmail", "subject": "Re: Solar Project", "to": "John Smith",
     "to_email": "john@example.com", "date": "2026-05-28"}
  ],
  "inbound_today": [
    {"account": "Gmail", "subject": "Re: Acme acquisition", "from": "Jane Roe",
     "from_email": "jane@acme.com", "snippet": "after discussion we've decided not to move forward...",
     "date": "2026-05-28", "internal": false}
  ],
  "calendar_tomorrow": [
    {"source": "Gmail", "title": "Investor Call", "day": "Thu May 29",
     "time": "9:00 AM PT / 12:00 PM ET", "_sort": "2026-05-29T16:00:00Z"}
  ]
}
```

### Tier 2: run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/afternoon_tea.py" --input "$TMPFILE"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\afternoon_tea.py" --input "$TMPFILE"
```

---

## Workflow

**Tier 1 only: skip if you already ran with `--input` above:**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/afternoon_tea.py"
# Items that look done but were not closed (read-only, today only)
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/completion_scan.py" --since 1
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\afternoon_tea.py"
# Items that look done but were not closed (read-only, today only)
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\completion_scan.py" --since 1
```

`afternoon_tea.py` pulls all configured email accounts in parallel plus the notetaker and all calendars. `completion_scan.py --since 1` returns `{"candidates": [...]}`, open items (action items, project tasks, deals) that today's email or meetings suggest are done, each with `evidence`. It is read-only here: the briefing lists them on one quiet line and never asks about them. In Tier 2 pass `--input "$TMPFILE"`. Wait for full JSON from both before proceeding.

---

## What the script produces

```json
{
  "date": "YYYY-MM-DD",
  "sent_today": [
    {
      "subject": "Re: <Subject>",
      "to": "<Counterparty Name>",
      "to_email": "<email>",
      "account": "<source account label>",
      "tag": "deal-move|relationship|admin"
    }
  ],
  "status_change_alerts": [
    { "thread": "<DealName or null>", "subject": "...", "from": "...", "from_email": "...", "account": "<label>", "signal": "<matched kill phrase>", "snippet": "...", "forwarded": false }
  ],
  "deal_inbound": [
    { "thread": "<DealName>", "subject": "...", "from": "...", "from_email": "...", "account": "<label>", "date": "YYYY-MM-DD" }
  ],
  "inbound_count": 0,
  "inbound_today": ["<external inbound items>"],
  "avoided": [
    { "key": "...", "from": "...", "from_email": "...", "subject": "...", "verdict": "suspect|junk", "reason": "...", "signals": ["unknown_sender", "lure_link"], "accounts": ["<label>"], "date": "YYYY-MM-DD" }
  ],
  "avoided_md": "...",
  "closed_today": ["<one row per to-do the automatic check-off closed today>"],
  "closed_today_md": "...",
  "judge_note": "",
  "hotcache_sync": {
    "revivals": [{ "deal": "...", "newest_mail": "YYYY-MM-DD" }],
    "last_contact_updates": [{ "deal": "...", "to": "YYYY-MM-DD" }],
    "stale_days": 0, "stale": false
  },
  "meetings_today": [
    {
      "title": "<Meeting Title>",
      "attendees": ["<Attendee 1>", "<Attendee 2>"],
      "decisions": ["..."],
      "commitments": ["..."]
    }
  ],
  "tomorrow_calendar": [
    {
      "title": "<Event Title>",
      "time": "9:00 AM PT / 12:00 PM ET",
      "source": "<source account label>"
    }
  ],
  "hotcache_threads": ["<DealName A>", "<DealName B>", "..."],
  "morning_priorities": ["item already done this morning"],
  "action_items": {
    "open": ["items that didn't match today's activity"],
    "completed": ["items already [x] in morning-coffee.md"],
    "auto_detected_done": ["items detected completed via sent mail/meetings"]
  },
  "errors": []
}
```

- `sent_today` — all emails sent today, tagged: `deal-move` (recipient matches a hotcache thread), `relationship` (external, non-deal), `admin` (internal)
- `status_change_alerts`: **highest priority.** Inbound mail (2-day window, incl. forwarded) with counterparty kill/pass language ("not moving forward", "releasing from exclusivity", "decided to pass", "withdraw", "terminate"), tied to an open hotcache thread when `thread` is set. `forwarded: true` means it arrived as an internal forward. These are candidates for review, not facts: read the `snippet` and confirm before declaring a deal dead.
- `deal_inbound`: inbound that landed on an active deal today (matched to a hotcache thread via the distinctive-token rule, so it is newsletter-immune). This is the inbound the retro renders; the rest is just `inbound_count`.
- `inbound_count` / `inbound_today`: total external inbound in the window, and the external-only list. Report the count; do not dump the full list (morning-coffee does the full classified inbox sweep).
- `avoided` / `avoided_md`: inbound mail held back as a lure (a signature and a link to a form, a shared file or a signing page, from an address the user has never written to, with no real message). It is already gone from `inbound_today`, `inbound_count` and `deal_inbound`, so it can never become an item. `avoided_md` is the finished "One to avoid" block; each held message appears in exactly one briefing, so both are empty on most days and on every rerun. Nothing was deleted or moved in the mailbox.
- `closed_today` / `closed_today_md`: the to-dos the automatic check-off closed today, and the finished block that lists them with the id and the command that reopens one. Built by code.
- `judge_note`: one sentence when the automatic check-off did not run today (for example "The check-off did not run today: quota"), else "". When it is set, zero closes means nobody looked, not that nothing was finished.
- `hotcache_sync`: the deterministic deal-metadata write-back that already ran. `revivals` are deals tagged dead that got fresh mail and were auto-flipped to active (surface these). `last_contact_updates` and `stale_days`/`stale` report the refresh and the staleness age. Safe-direction only: it never auto-marks a live deal dead, and never revives a deal flagged `no_revive=1` (a dead parent with surviving workstreams).
- `meetings_today` — notetaker meetings from today only
- `auto_detected_done` — open action items from morning-coffee.md that match today's sent mail subjects/recipients or meeting attendees (50% token overlap threshold)
- `errors` — partial failures; render the rest and note errors at bottom
- `notes_today`: the Notes the watcher mailed between the briefings today (`/van-gogh:note`, opt-in), each `{status, kind, at, line}`. `status` is `sent` or `folded` (past the daily cap, so the reader has not seen it yet). Render `line` verbatim: code wrote it from the event and it claims nothing that did not happen.

---

## What you still need to do

### 1. Closed today (paste, never ask)

Finished to-dos close themselves. A scheduled check reads the sent mail, the
meeting notes and the decisions since each item was opened, closes an item
only when it can quote the evidence, and records every close under a short id
so it can be reopened. This step reports what it did. It never asks the reader
to confirm a close.

1. `closed_today_md` is the finished block: paste it VERBATIM. Code wrote every
   line. It opens with its own `Closed today (N)` line, lists each item with
   its id and the evidence it was closed on, adds an `Expired (N)` group
   (no tick: those were given up on, not finished) when old items with no
   due date aged out, and ends with the one line that says how to put an item back. Never reword a
   line, drop an id, reorder, add a heading or a count of your own above it
   in the terminal, or merge the two groups.
2. When `judge_note` is non-empty, print it exactly as written as the first
   line of the section and do NOT print a zero count. It means the check did
   not run today, and that is a different thing from a quiet day: "Closed
   today (0)" would say nothing was finished when the truth is that nobody
   looked. If `closed_today_md` is also non-empty (an earlier run today did
   close something), print the note first and the block under it.
3. When both are empty, the whole section is the one line
   `Nothing closed today.`
4. The `candidates` array from `completion_scan.py --since 1` is not a
   question any more. Those are open items that today's mail or meetings
   suggest are done but that the check has not closed, usually because
   nothing it found could be quoted as proof. Render them as one quiet line
   under the block, never as a list and never with a question:

   `Looks done, not yet closed ({N}): {item}; {item}; {item}`

   At most five items, each the candidate's own text, then `and {N} more`
   when there are more. Omit the line when `candidates` is empty, and leave
   out any candidate whose text already appears in `closed_today`. Do not
   offer to check them off.

Only when the reader names an item and says to check it off (in their own
words, after the briefing) is anything written. Pipe exactly the candidates
they named, as a verbatim JSON array, to the writer:

macOS / Linux (bash/zsh):
```bash
echo '<named candidates JSON>' | ("$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/completion_scan.py" --mark-done)
```

Windows (PowerShell):
```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new()
'<named candidates JSON>' | & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\completion_scan.py" --mark-done
```

When nobody is there (`meta.unattended`), nothing in this step changes: every
part of it is a paste or a line, and none of it waits for an answer.

### 2. Render the retro briefing

Start with a stoic quote, then THE READ: three or four sentences on the day
behind, written by you from what the script found.

Morning Coffee's read looks forward; this one looks back. Say what actually
happened (what closed, what was sent, which calls landed and what they left
behind), what did not move and has now been sitting long enough to name, and
what tomorrow inherits. The full rules are in
[`_shared/front-page.md`](../_shared/front-page.md), "The read"; the one that
matters is that every claim traces to a field in the JSON. Afternoon Tea's own
sources are `completed`, `front_page`, `slipping`, `newly_cold`,
`status_change_alerts` and today's meetings.

A quiet afternoon is allowed to be two sentences. Do not pad it, and do not
grade the day: "a light admin day" is a verdict the data does not carry, while
"two calls, one reply out, nothing closed" is what happened.

Print `quote_md` verbatim as the first line. It is already formatted as a
blockquote with its author, and it is already checked for dashes.

Do not choose a quote yourself and do not substitute one. The script owns the
selection because it owns the only thing that can prevent a repeat: a ledger
of the last 30 quotes shown, which no prose instruction can consult. If
`quote_md` is empty, open with the read and no quote.

After the summary, render in this order and no other: the suggested focus
task, travel, the front page, the drafts block, the day's detail, then the
business rows. This is the order in `_shared/front-page.md`, "Render order",
and it is the order the written file and the Workbench page must match. The
order is the product.

---

### SUGGESTED FOCUS TASK

One item you choose, above the checklist. Afternoon Tea's version looks at
tomorrow: the work that would compound if it got a clear run in the morning,
rather than what is loudest right now. Full rules in
[`_shared/front-page.md`](../_shared/front-page.md), "The suggested focus
task". Read `buckets`, weigh leverage first, then the reader's stated
`priorities[]`, then what is quietly stuck.

The task, why this one, the first move, and what it unblocks. If nothing
genuinely compounds, say so in one line and render no block.

---

### TRAVEL

`travel_md` is the finished block: paste it VERBATIM, directly under the focus
task and above the front page. Omit it entirely when it is empty, which is what
it is on most evenings.

Do not write this section yourself and do not restate a notice in your own
words. Code builds it from the calendar and the airline confirmation, and each
line was written against the flight's own timezone, which is often not the
reader's. Three rules follow from that, and they are why this block is not
yours to phrase:

- Never recompute or restate a time. The leave-by is the one number in this
  briefing where being wrong means a missed flight.
- Never add a terminal, a gate, or a drive time the notice does not carry.
  A confidently wrong terminal is worse than no terminal.
- When a notice says a drive time could not be established, keep that clause.
  It is the difference between a reader allowing extra time and a reader
  trusting a number that was never computed.

Afternoon Tea's departure window is one day wider than Morning Coffee's: told
at 5:55 AM that a flight leaves at 7 it is already too late to pack, and told
the evening before it is not. It sits second on the page because it is the one
block whose deadline cannot move.

---

### THE FRONT PAGE

`front_page_md` is the finished block: paste it VERBATIM and change nothing. It
already carries the opening sentence, so do not write one above it. Code chose
the items, their order and their wording. Never re-rank, re-word, add or drop
one.

Before you paste it, draft the replies: see
[`_shared/front-page.md`](../_shared/front-page.md), "Drafting, before you
render". Every item whose `action` is `email reply` gets a draft in that
account's Drafts folder, keyed in the ledger so a rerun reuses it. Nothing is
ever sent.

### THE REST

`fold_rows_md` is the finished block: paste it VERBATIM. One line per business,
the folded count broken out by function, at most two flags.

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

There are no standalone alert sections in the terminal render any more. A deal
status change, an overdue deal, a revival, a contact gone quiet: each is
already an item on the front page or a flag on a business row, and each is
still inside its fold. Do not add a section for any of them.

When `front_page_md` is empty, fall back to `buckets_md` and say one line about
it. Never invent a front page of your own.

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

The full ledger still exists: it is `folded_md`, one closed callout per
business, written into afternoon-tea.md. Its three labels are `Done today`,
`Open loop` and `Still open`, and they are the only three that may appear.

```
=== AFTERNOON TEA: {weekday} {date} ===

"{quote}", {author}

{1-2 sentence day summary}

──────────────────────────────────────────────
⚠️  DEAL STATUS CHANGES ({N})
──────────────────────────────────────────────
ONLY if status_change_alerts is non-empty. The most important thing in the retro: a counterparty may have killed or paused a deal. For each alert, read the snippet:
- ⚠️ **{thread or "Unmatched: " + subject}**: {from} ({account}) signals "**{signal}**"{" [forwarded]" if forwarded}
  - "{snippet}"
  - Recommend: verify the email, then update hotcache if the deal is dead/paused. If the parent is dead but a financing or PPA workstream keeps moving, set last_contact to the death date and add no_revive=1 to its <!-- deal: --> metadata.
These are CANDIDATES, not facts. Never silently mark a deal dead. (Omit this section if status_change_alerts is empty.)

──────────────────────────────────────────────
HOTCACHE AUTO-SYNC (revivals + staleness)
──────────────────────────────────────────────
From hotcache_sync (already written to hotcache; surface only):
- For each revivals entry: "♻️ **{deal}** was tagged dead but has fresh activity ({newest_mail}); auto-flipped to active. Confirm the precise stage."
- If stale is true: "⏳ Hotcache was {stale_days} days stale before this run (now bumped). Re-verify load-bearing deal claims older than that."
(Omit this block if revivals is empty and stale is false.)

──────────────────────────────────────────────
ONE TO AVOID
──────────────────────────────────────────────
ONLY if avoided_md is non-empty. Paste `avoided_md` verbatim and add nothing: no advice, no second
warning, no offer to delete or report the mail. Each line names one inbound message that was held
back as a lure and says why. It is shown this once and never again, so do not carry it forward,
count it as inbound, or turn it into an item.

──────────────────────────────────────────────
DONE TODAY: {N} sent, {N} meetings
──────────────────────────────────────────────
**Deals**
- [DEAL] "{subject}" → {to} [{account}]

**Relationships**
- "{subject}" → {to} [{account}]

**Meetings**
- {title} (with {attendees joined by ", "})
  - Decided: {decisions[0]}
  - Committed: {commitments[0]}

(Omit a category entirely if it has no items.)

──────────────────────────────────────────────
NOTES TODAY ({N})
──────────────────────────────────────────────
The Notes that went out between the briefings, oldest first, one line each: `line` verbatim.
A `folded` one carries the suffix `(folded past the daily cap; you are reading it here first)`,
because that Note never arrived and this is the reader's first sight of it.
- {line}

(Omit the section entirely when `notes_today` is empty. Never reword a line.)

──────────────────────────────────────────────
WINS
──────────────────────────────────────────────
Write 2-4 sentences of narrative prose, not bullets. Synthesize deal-move emails, meeting decisions, and checked-off tasks into a short story of the day's momentum. Make it feel earned. Be specific about names, deals, and outcomes. Lead with the most meaningful move.

If it was a quiet day, acknowledge it honestly: "Lighter execution day: one relationship touch and admin cleanup. The groundwork laid this week sets up a stronger Thursday."

Closed today comes next: Step 1, exactly as written there. Print no banner for it, because the block opens with its own "Closed today (N)" line. In order:
{judge_note, verbatim, only when non-empty; never a zero count beside it}
{closed_today_md, verbatim}
Looks done, not yet closed ({N}): {item}; {item}; {item}

(When judge_note and closed_today_md are both empty, write the one line "Nothing closed today." Omit the "Looks done" line when there are no candidates. No question anywhere in this section.)

──────────────────────────────────────────────
STILL OPEN ({N})
──────────────────────────────────────────────
Items from morning-coffee.md that remain open (carried to tomorrow):
- [ ] {item}

(Omit section if action_items.open is empty.)

──────────────────────────────────────────────
OPEN LOOPS ({N})
──────────────────────────────────────────────
Commitments made in today's meetings that haven't been sent on yet.
Pull from meetings_today[*].commitments. Skip any item that already appears in sent_today subjects.
- "{commitment}" (from: {meeting title})

(Omit section if no commitments.)

──────────────────────────────────────────────
TOMORROW: {N} events
──────────────────────────────────────────────
{time}  {title}  [{source}]

**Prep flags:** For each tomorrow event whose title or attendees appear in hotcache_threads, add one line:
- ⚑ {event title}: active deal thread ({hotcache thread name}), prep may be needed

(If tomorrow_calendar is empty: "No events found for tomorrow.")

**TRAVEL (omit entirely when `travel` is empty, which is most days.)**

Render each notice in `travel` FIRST, above tomorrow's events, because a
leave-by time governs the whole morning and this is the last briefing before
it:

Travel
• {text}
  {link, on its own line, flights notices only}
```

Print each notice's `text` verbatim. It is already written for a reader and
already carries its own caveat when something could not be established. Three
rules, the same ones Morning Coffee follows:

- Never recompute or restate a time. A leave-by is the one number in the
  product where being wrong means a missed flight, and it was computed against
  the flight's own timezone, which is often not the reader's.
- Never add a terminal, a gate, or a drive time the notice does not carry.
- When `leave_by` is null, leave it null. Do not offer an estimate.

This briefing's travel window is one day wider than Morning Coffee's on
purpose: an early flight told at 5:55 AM is too late to pack for, and told the
evening before it is not. The notices are deduplicated per briefing, so a trip
named here is still named again on the morning itself.

All times are pre-formatted as PT / ET — render as-is.

### 3. Write afternoon-tea.md

The file carries the same sections as the terminal render, travel included.
Travel is `travel_file_md`, pasted verbatim directly under the focus task,
exactly as in the terminal render: do not hand-write it, do not restate a time
it carries, and do not move it down beside the tomorrow section. This file is
what the Workbench page and the emailed digest both render, so a notice missing
here is missing everywhere the reader might actually see it.

After rendering, write to `meta.afternoon_tea_md`:
```
{meta.afternoon_tea_md}
```

Overwrite any previous file. Use this frontmatter:

```markdown
---
date: {YYYY-MM-DD}
generated: {HH:MM PT}
sent_count: {len(sent_today)}
meetings_count: {len(meetings_today)}
checked_off: {len(auto_detected_done)}
---
```

Then write the briefing as the body. Use markdown: `##` for section headers,
`- [ ]` / `- [x]` for open/checked items. Strip the `═══` banner lines: those
are terminal display only.

**Write every `##` heading in sentence case**, however the section is spelled
in the terminal template above: `## Done today`, not `## DONE TODAY`. The
box-rule blocks are shouted because a terminal has no type hierarchy to carry
emphasis; the file has one, and the Workbench card bar uppercases the heading
in CSS, so caps written here are applied twice. This is also what keeps the
four briefings reading as one product: Morning Coffee writes
`## Suggested focus task` and The Week writes `## Top priorities`, and a set of
pages where only one shouts reads as a page from somewhere else. Proper nouns
and acronyms keep their own case.

**Shape of the file**, in this order:

This is the terminal order minus the read, and it is the order the Workbench
page and the digest both inherit, because both render this file. A section
that moves here moves on every surface the reader actually sees.

1. The suggested focus task, the block you chose above. Same four parts, no
   heading inside them.
2. `travel_file_md` verbatim, directly under the focus task, when it is
   non-empty. Use the `_file_md` key, never `travel_md`: a trip with nothing
   booked carries a hyperlink there, and a file is read where no button
   exists. Omit it entirely when empty, which is most evenings.
3. The front page: `front_page_file_md`, never `front_page_md`. The terminal
   key aligns its columns with whitespace and every markdown renderer collapses
   that into one run-on paragraph, which is how it reached a published page
   once already.
4. `## Drafts`: one `> [!note]- {counterparty}: {subject}` callout per reply you
   wrote, holding the full text, whether or not it reached a Drafts folder.
5. The retro sections you rendered above (one to avoid, done today, wins,
   closed today, still open, open loops, tomorrow), as prose and lists. Under
   `## Closed today` and `## One to avoid` the body is the same pasted block
   as in the terminal, unchanged. The file is the one place a heading goes
   above the closed block, because the page is cut into cards on `##`.
6. `folded_md` verbatim: one closed callout per business, the whole ledger.

Do NOT write `fold_rows_md` into the file. It is one line per business, and
`folded_md` right under it opens with the same business names, so the file
would list every business twice. The rows are for the terminal, which has no
folds.

This file IS the local page: the Workbench serves `/briefing/afternoon-tea`
by rendering exactly this markdown, so a terminal key written here ships a
broken page.

### 4. Offer next steps

**Overdue deliverables.** When `nudges` is non-empty, list each one after the
briefing (title, how many days late, the bucket it belongs to) and offer to
draft it. One line per nudge, then a single offer covering all of them.

Draft in the chat first: plain text for an email, a plain-ASCII sketch for a
deck or a sheet. Do not create a file until the user says to, and do not send
anything, ever, without a separate explicit instruction.

When `nudges` is empty, omit this entirely. Do not say "no nudges".


After the briefing:
- "Want me to draft a follow-up for any of the open loops?"
- "Any items you want to manually check off or carry forward?"
- If today is Friday: "It's Friday — good time to run `/van-gogh:week-retro` before you close out."

## When to ask the user

- If `errors` contains all configured email accounts: tell them to check Google and Microsoft OAuth tokens (re-run `app/auth_bootstrap.py` if needed), then re-run
- If `errors` is non-empty but partial: show which accounts are missing, render with what's available
- If `meetings_today` is empty and it's past 3pm: note that no meetings were found — ask if any should be manually added
- If `action_items.open` and `action_items.auto_detected_done` are both empty but morning-coffee.md has items: note that auto-detection found no matches and the user may want to confirm manually
