---
name: week-retro
description: >-
  Weekly business retrospective that pulls Outlook calendar, meeting source pages, Obsidian log
  entries, and project dev logs, then synthesizes a 9-section retro: Wins, Intentions vs.
  Outcomes (with real meeting hours), Time Audit, Blind Spots, Stuck Items, Stop/Delegate,
  Next Week 3 Commitments, Recommended Automations, and Meetings Not Filed. Use when the user
  types /van-gogh:week-retro or asks for a weekly retro, week in review, or how their week
  actually went.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Week Retro

All data collection is handled by a Python script. Your job: synthesize the JSON into the
9-section retro, save it to the vault automatically, then offer to set next week's intentions.

**How this reads.** The person reading it runs a business, not a
terminal. Short sentences, lead with the point, no vocabulary from the
data model, no filler openers. The full rules are the "Written Voice"
section of `DESIGN.md`; follow them for every line you write here.

## Resolved paths come from the script

Every `week_retro.py` JSON output includes a top-level `meta` block with
resolved values. Use these keys verbatim wherever a path or name is needed:

- `meta.vault_path`, `meta.hotcache_path`, `meta.weekly_dir`
- `meta.user_first_name`
- `meta.businesses[].display_name` — use these as the rows in the Intentions vs. Outcomes table

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

**If `False` (Tier 2: connector mode):** Fetch this week's calendar events via connector tools.

### Tier 2: what to fetch

**Calendar** — events Monday through Friday this week across all accounts. Per event: title, date (YYYY-MM-DD), start + end datetime (to compute duration in minutes), organizer name.

Meeting sources and Obsidian logs are fetched by the script directly — no connector needed.

### Tier 2: write the tempfile

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp /tmp/van-gogh-retro-XXXXXX.json)
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

```json
{
  "calendar_week": [
    {"subject": "Team Sync", "date": "2026-05-28", "duration_min": 60, "organizer": "John Smith"}
  ]
}
```

### Tier 2: run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/week_retro.py" --input "$TMPFILE"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\week_retro.py" --input "$TMPFILE"
```

---

## Workflow

**Tier 1 only: skip if you already ran with `--input` above:**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/week_retro.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\week_retro.py"
```

Takes 60-120 seconds: the Microsoft calendar query plus a week_review shell-out for full deal coverage (it reuses the hub's mail scan with `--no-classify`, so no metered Haiku call). Wait for full JSON output before rendering.

Then read strategic context:
```
{meta.hotcache_path}
```

Then render all 9 sections.

---

## JSON Keys

- `week_start`, `week_end` — ISO dates for the Mon-Fri window
- `calendar_events` — list of `{subject, date, duration_min, organizer}` from Outlook
- `meeting_sources_this_week` — source pages filed this week, each with `title`, `date`, `business`, `action_items_open`, `action_items_done`, `user_owned_open`
- `calendar_meeting_gaps` — calendar events with no matching source page (same shape as `calendar_events`)
- `obsidian_log_entries` — raw log lines from this week
- `devlog_last_entries` — keyed by each `businesses[].display_name`, value is the most recent dev log bullet for that project (or null)
- `all_open_action_items` — list of `{text, source, days_open, overdue, user_owned}` across all source pages this week
- `prior_retro_commitments` — raw text of the "3 Commitments" section from the most recent `retro-*.md`
- `deals_this_week`: full deal coverage from the week_review hub. `died_or_at_risk` (counterparty kill/pass alerts), `likely_handled_met` (`{name, subject, met}` for deals a meeting since the last email likely handled), plus `waiting_on_user_count` / `inbox_pending_count` / `cold_count`. Empty in Tier 2 connector mode (no deal half via this script).
- `hotcache_deal_state`: every active-threads deal's `{deal, stage, last_contact}` from hotcache.md
- `status_change_alerts`: counterparty kill/pass language tied to open deals (review only, never auto-killed); same data as `deals_this_week.died_or_at_risk`
- `meetings_api`: API-sourced meeting `{title, date}` for the Mon-Fri window, kept separate from `calendar_meeting_gaps` (which uses the local `sources/*.md`)
- `errors` — list of non-fatal data collection errors; render what you have, note errors at bottom

---

## Retro format

No em-dashes anywhere in the output. If times are mentioned, show PT and ET side by side.

The front page comes first: `front_page_md` verbatim, then the nine sections
below. Sections 3 through 9 are folds in the written file, one closed callout
each, so the retro opens on what is still open and what next week owes.

---

### THE FRONT PAGE

`front_page_md` is the finished block: paste it VERBATIM and change nothing. It
already carries the opening sentence, so do not write one above it. Code chose
the items, their order and their wording. Never re-rank, re-word, add or drop
one.

This briefing does not draft replies. Skip the drafting step in
[`_shared/front-page.md`](../_shared/front-page.md).

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

When `front_page_md` is empty, open on section 1 instead and say one line about
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

### HOUSE STYLE FOR EVERY SECTION BELOW

The reader runs a business and did not ask for software. These rules are not
suggestions, and they apply to any section you write, including one you add.

**Never print a word from the data model.** The script hands you JSON, and its
key names and enum values are for you, not for the reader. Banned outright:
`stalled`, `advanced`, `at risk` as a bare label, `loi`, `beta tags`,
`user_owned`, `overdue` as a column, `cold_urgent`, `died_or_at_risk`,
`likely_handled_met`, `null`, and any `z-*.md` or `*.json` filename. Say what
happened in the words the reader would use out loud. "Realogix has not been
touched in 88 days" is the same fact as "Realogix: stalled (88d since contact)"
and it is a sentence.

**Expand an acronym the first time it appears**, every run. The reader has not
read the docs and the page is read cold at the end of a hard week. `LOI` is a
letter of intent. `KR` is a key result. `SPP` is the Southwest Power Pool. If
you cannot expand it, you do not understand it well enough to print it.

**One thing per bullet.** A bullet holding ten deals separated by semicolons is
a paragraph wearing a bullet, and on a phone it is a twelve-line wall that
buries every deal in it. Ten stalled deals is ten bullets, or a count plus the
three that matter. Never chain items with `;`.

**Lead with the subject, not the status.** "Realogix, no contact in 88 days" not
"stalled: Realogix (88d since contact)". The reader is scanning for the name.

**Say the direction with the number.** A count on its own makes the reader guess
which way is bad. Not "3 waiting" but "3 waiting on you". Not "2 red" but "2 need
an answer this week".

**Write the lateness in a shape the page can colour.** The web page marks decay
in red, and it recognises the phrasings this product already uses: `3 days late`,
`2d overdue`, `overdue since 9/1`, `28 days stale`, `88d since contact`,
`due today`. Use one of those. Inventing a new phrasing for the same fact costs
the reader the only colour on the page that means "this is slipping".

**No em-dashes or en-dashes**, in any heading or line you write, including
headings you copy from this file.

---

### 1. Wins

What actually closed, shipped, or moved to done this week. Be specific and positive.
Pull from: `meeting_sources_this_week` (action_items_done), `obsidian_log_entries`, `devlog_last_entries`.
Do not include items that are still in progress.

---

### Deal Movement (this week)

Pull from `deals_this_week`, `status_change_alerts`, and `hotcache_deal_state`.

- **At risk / died:** one line per `status_change_alerts` entry: "[thread]: [signal] (from [from])". Counterparty kill/pass signals tied to an open deal. Surface for review; never assert a deal is dead without confirmation.
- **Likely handled in a meeting:** one line per `deals_this_week.likely_handled_met`: "[name], [subject] (met [met])". A meeting since the last email is a heuristic the deal moved; flag for one-tap confirm.
- **Still open:** headline the `waiting_on_user_count` / `inbox_pending_count` / `cold_count`.
- **Stage snapshot:** scan `hotcache_deal_state` for any deal whose `last_contact` looks stale relative to its `stage`.

If both `died_or_at_risk` and `likely_handled_met` are empty, say "No deal status changes detected this week." (Tier 2 connector mode has no deal half via this script; note that instead.)

---

### 2. Intentions vs. Outcomes

Table format — one row per `businesses[].display_name`, plus an Admin/Personal row:

| Business | Meetings | Hours | Research/Writing | What Moved |
|---|---|---|---|---|
| <Business display_name> | N | Nh | ... | ... |
| ... | ... | ... | ... | ... |
| Admin/Personal | N | Nh | -- | ... |

Hours: sum `duration_min` from `calendar_events`, grouped by business. You do the
categorization using subject + organizer context — the script does not categorize.
Use `meta.businesses[].keywords` plus hotcache.md context to map ambiguous
subject names to a business.

Below the table, add 2 sentences: "The pattern: ..."

---

### 3. Time Audit

2-3 sentences. Was time aligned with priorities? Name the highest-leverage underserved opportunity.
Do not repeat the table. Add a judgment.

---

### 4. Blind Spots

Adversarial pass. Actively look for what is missing, not just what is present.

Sources to check:
- `calendar_meeting_gaps` — meetings that happened but were never filed as source pages
- `all_open_action_items` where `user_owned: true` and `overdue: true` — the user is the bottleneck
- `prior_retro_commitments` — were last week's 3 commitments met? (check against wins and source pages)
- Strategic gaps: businesses with zero meetings, open deals with no movement, team members not mentioned

**Critical: verify before flagging.** Before asserting any deal or project is stalled:
- Note it as a possible blind spot and ask the user to confirm
- Do NOT assert a deal is dead or a project is dark based on log gaps alone
- Email (sent mail) is the ground truth — say "worth confirming via email: [item]" rather than declaring it a problem

---

### 5. Stuck Items

Table format:

| Item | Open Since | Why It's Stuck |
|---|---|---|
| ... | YYYY-MM-DD | ... |

Pull from `all_open_action_items` where `overdue: true` (days_open > 7).
Add any items from `prior_retro_commitments` that appear unmet.
If stuck items list is empty, say so explicitly.

---

### 6. What to Stop or Delegate

2-3 concrete observations. Ask: was the user doing work that belongs to a team member?
Pull from: source pages where the user is the owner of something operational,
meetings where the user was the only person from their org, recurring admin patterns.
Be specific — name the task and the person it should go to.

---

### 7. Next Week: 3 Commitments

Exactly 3. Specific and verifiable (can be checked at the end of next week).
Format:
1. [Commitment]
2. [Commitment]
3. [Commitment]

---

### 8. Recommended Automations

The weekly pick is owned by `/van-gogh:vault-audit`, which writes it into
`{vault}/van-gogh/vault-audit.md` under either "One thing to change" or "One
Thing To Stop Doing", depending on whether its answer was to keep the work or
to end it. Read that file's `Picked:` line: if the date on it is within the
last 7 days, reference that pick here rather than inventing a second one, and
say which of the two sections it came from.

Otherwise fall back to 1 to 3 automation ideas from friction seen in this
week's retro. For each: name the pain, propose the solution, estimate effort
(S, M or L), ordered by impact against ease. Ask whether the work should
happen at all before proposing a faster version of it.

---

### 9. Meetings Not Filed

Table from `calendar_meeting_gaps`:

| Meeting | Date | Duration | Organizer |
|---|---|---|---|
| ... | ... | Xm | ... |

After the table, ask: "Which of these do you want me to ingest via /van-gogh:meeting-ingest?"

If `calendar_meeting_gaps` is empty, omit this section.

---

## After Rendering

**Overdue deliverables.** When `nudges` is non-empty, list each one after the
briefing (title, how many days late, the bucket it belongs to) and offer to
draft it. One line per nudge, then a single offer covering all of them.

Draft in the chat first: plain text for an email, a plain-ASCII sketch for a
deck or a sheet. Do not create a file until the user says to, and do not send
anything, ever, without a separate explicit instruction.

When `nudges` is empty, omit this entirely. Do not say "no nudges".


1. **Save the full retro automatically** (no prompt) to:
   `{meta.weekly_dir}/retro-{YYYY-MM-DD}.md`
   Date = Friday of the week being reviewed.

   **Shape of the file**, in this order:

   1. The front page: `front_page_file_md`, never `front_page_md`. The terminal
      key aligns its columns with whitespace and every markdown renderer
      collapses that into one run-on paragraph, which is how it reached a
      published page once already.
   2. Sections 1 and 2 (wins, intentions vs outcomes) open, as written.
   3. Sections 3 through 9 as closed callouts, one `> [!note]- {title}` each.
   4. `folded_md` verbatim: one closed callout per business, the whole ledger.

   Do NOT write `fold_rows_md` into the file. It is one line per business, and
   `folded_md` right under it opens with the same business names, so the file
   would list every business twice. The rows are for the terminal, which has no
   folds. Strip the `═══` banner lines: terminal display only.

   After saving, append a one-line entry to `{meta.vault_path}/wiki/log.md`:
   `- YYYY-MM-DD: week retro saved for week of {week_start}`
   Then tell the user where it was saved (one line, with the path).

2. Ask: "Want to set 3 intentions for next week to anchor the next retro?"
   If yes, save to:
   `{meta.weekly_dir}/intentions-{next_monday}.md`
   Format: one bullet per intention (plain, no frontmatter).

---

## Error Handling

- If `errors` contains a calendar timeout or exception: note it, render the rest using meeting sources only for hours estimation
- If `errors` contains a missing project page: skip that row in Intentions table, note it
- If `prior_retro_commitments` is empty string: skip the commitments check in Blind Spots, note "no prior retro found"
- If `meeting_sources_this_week` is empty: flag it — either no meetings were filed this week (unusual) or the script couldn't find the sources directory
