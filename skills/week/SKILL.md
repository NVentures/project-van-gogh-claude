---
name: week
description: Weekly mission control briefing — pulls this week's calendar from all
  configured accounts, runs sent-mail deal review across each, extracts open
  Obsidian roadmap tasks, and synthesizes top priorities. Use when the user
  types /van-gogh:week or asks for a weekly overview, priorities, or what they're falling behind on.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Weekly Briefing

All data collection — including done-log suppression and prior-week carry-forward — is handled by the Python script. Your jobs after the script runs: synthesize the priorities brief, render the full detail sections, and offer to draft replies.

**How this reads.** The person reading it runs a business, not a
terminal. Short sentences, lead with the point, no vocabulary from the
data model, no filler openers. The full rules are the "Written Voice"
section of `DESIGN.md`; follow them for every line you write here.

## Resolved paths come from the script

Every `week_review.py` JSON output includes a top-level `meta` block with
resolved values. Use these keys verbatim wherever a path or name is needed —
never read `config.json` directly:

- `meta.vault_path`, `meta.hotcache_path`, `meta.weekly_dir`, `meta.sources_dir`
- `meta.done_log_path` — current-year done log (e.g. `…/wiki/weekly/done-2026.md`)
- `meta.workspace_week_md` — `week.md` in the vault `van-gogh/` (drives `--since auto`)
- `meta.logs_dir` — `logs/` in the vault `van-gogh/` (script writes sidecar here)
- `meta.user_first_name`
- `meta.action_items_heading`
- `meta.accounts` — list of configured accounts, each `{label, provider, email, is_primary}`. The `source` field in JSON output uses each account's `label` verbatim (a non-primary Google account shows as `Google (<label>)`). Iterate this list; never assume a fixed primary/secondary/Outlook set.
- `meta.businesses[].{tag, display_name}` — uppercase `tag` for the `[TAG]` brackets below

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

**If `True` (Tier 1):** Skip to Workflow: the script fetches data directly via OAuth.

**If `False` (Tier 2: connector mode):** Fetch data via your available Gmail and Microsoft 365 connector tools, write a tempfile, and run the script with `--input`.

### Tier 2: what to fetch

1. **Calendar** — events this Monday through Sunday across all accounts. Per event: title, start datetime (ISO UTC), source account label.
2. **Sent mail** — threads sent in the past 14 days (default; adjust if week.md `generated:` date suggests otherwise). Per thread: subject, counterparty email + name, date of your last send, whether and when they replied, body preview (~150 chars).
3. **Inbox** — inbound threads you haven't replied to from the past week. Per thread: subject, sender email + name, date received, body preview.

Classify each email thread:
- `waiting_on_user` — they replied after your last send; fill `reply_age_days`
- `cold_urgent` — you sent last, no reply, >14 days; fill `age_days`
- `cold_monitor` — you sent last, no reply, 7–14 days; fill `age_days`
- `inbox_pending` — inbound you haven't replied to; fill `reply_age_days`

For every thread fill: `summary` (1 sentence), `intent` (action_required|deal_signal|decision_point|awaiting_their_move|intro|fyi|closing), `urgency` (high|medium|low), `suggested_action` (1 sentence). Set `is_internal: true` for known team members.

### Tier 2: write the tempfile

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp /tmp/van-gogh-week-XXXXXX.json)
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

Write a JSON object to `$TMPFILE`:

```json
{
  "calendar": [
    {"source": "Gmail", "title": "Team Sync", "day": "Wed May 28",
     "time": "9:00 AM PT / 12:00 PM ET", "_sort": "2026-05-28T16:00:00Z"}
  ],
  "waiting_on_user": [
    {"source": "Gmail", "subject": "Re: Solar Project", "counterparty_name": "John Smith",
     "counterparty_email": "john@example.com", "age_days": 5, "reply_age_days": 2,
     "body_preview": "...", "summary": "...", "intent": "deal_signal", "urgency": "high",
     "suggested_action": "...", "is_internal": false, "allowlisted": false}
  ],
  "inbox_pending": [...],
  "cold_urgent": [...],
  "cold_monitor": []
}
```

Format `time` as `"H:MM AM/PM PT / H:MM AM/PM ET"`. Format `day` as `"Mon May 25"`. `_sort` is ISO UTC for ordering.

### Tier 2: run the script

macOS / Linux (bash/zsh):
```bash
LOGS_DIR=$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())") && mkdir -p "$LOGS_DIR" && "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/week_review.py" --input "$TMPFILE" > "$LOGS_DIR/week_review_latest.json"
```

Windows (PowerShell):
```powershell
$LOGS_DIR = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())").Trim()
New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\week_review.py" --input "$TMPFILE" | Out-File -Encoding utf8 "$LOGS_DIR\week_review_latest.json"
```

Then continue with the JSON slices and rendering steps in Workflow below.

---

## Workflow

Logs live in the vault `van-gogh/logs/`.

**Tier 1 only: skip if you already ran with `--input` above:**

macOS / Linux (bash/zsh):
```bash
LOGS_DIR=$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())") && mkdir -p "$LOGS_DIR" && "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/week_review.py" > "$LOGS_DIR/week_review_latest.json"
```

Windows (PowerShell):
```powershell
$LOGS_DIR = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import logs_dir; print(logs_dir())").Trim()
New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\week_review.py" | Out-File -Encoding utf8 "$LOGS_DIR\week_review_latest.json"
```

The script takes 30-90 seconds (parallel API fetches). It also writes
`$LOGS_DIR/week_review_latest.full.json` as a sidecar — the full output with
`filtered_pending` bodies for audit, in case anything looks misclassified.

Flags (all optional — defaults are production-ready):
- `--since auto` (default) — days back computed from the prior `week.md` `generated:` date (14-day floor, 30-day cap). Floor is 14 (not 7) because cold_monitor threads live in the 7–14d bucket and need that window to surface. Pass an int (e.g., `--since 7`) to override.
- `--week-ahead N` — days forward for calendar (default 6 = Mon–Sun, current week only)
- `--sources-days N` — days back for source page action items (default 7)
- `--no-sidecar` — skip the full JSON sidecar (rarely needed)

**Read the script output in slices, not a full Read.** One slice per section
keeps the agent context tight. The slicer is the venv python — no `jq` needed:

macOS / Linux (bash/zsh):
```bash
VG_PY="$HOME/.config/van-gogh/venv/bin/python"
"$VG_PY" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps({k: d.get(k) for k in ['meta','week_start','week_end','since_days','errors','trend','filter_stats']}, indent=2))" "$LOGS_DIR/week_review_latest.json"
"$VG_PY" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR/week_review_latest.json" calendar
"$VG_PY" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR/week_review_latest.json" waiting_on_user
"$VG_PY" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR/week_review_latest.json" inbox_pending
"$VG_PY" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps((d.get('cold_urgent') or []) + (d.get('cold_monitor') or []), indent=2))" "$LOGS_DIR/week_review_latest.json"
"$VG_PY" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR/week_review_latest.json" obsidian_tasks
```

Windows (PowerShell — same python slices):
```powershell
$VG_PY = "$HOME\.config\van-gogh\venv\Scripts\python.exe"
& $VG_PY -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps({k: d.get(k) for k in ['meta','week_start','week_end','since_days','errors','trend','filter_stats']}, indent=2))" "$LOGS_DIR\week_review_latest.json"
& $VG_PY -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR\week_review_latest.json" calendar
& $VG_PY -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR\week_review_latest.json" waiting_on_user
& $VG_PY -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR\week_review_latest.json" inbox_pending
& $VG_PY -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps((d.get('cold_urgent') or []) + (d.get('cold_monitor') or []), indent=2))" "$LOGS_DIR\week_review_latest.json"
& $VG_PY -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2]), indent=2))" "$LOGS_DIR\week_review_latest.json" obsidian_tasks
```

Only fall back to reading `filtered_pending` or `suppressed` if you need to audit a missing thread. The full sidecar (`.full.json`) has `body_preview` on filtered items if you need to verify a classification.

---

## What the script produces

JSON blob with these keys:

- `meta` — resolved paths and labels (see top of this doc)
- `week_start` / `week_end` — date range
- `since_days` — the actual window used for sent-mail review (after `--since auto` resolution)
- `calendar` — merged Google + Outlook events, deduplicated, sorted by start time
- `obsidian_tasks` — open action items from hotcache + recent source pages
- `waiting_on_user` — threads where the user sent first, the counterparty replied, and the user hasn't responded since. Sorted by `reply_age_days` desc. Includes `summary`, `intent`, `urgency`, `suggested_action` from Haiku classification. `allowlisted: true` marks deal-critical senders (counsel, partners) that bypassed the classifier — never silently drop these.
- `inbox_pending` — **inbound-only threads** the user hasn't replied to. Anchored to last Monday. Catches threads invisible to `waiting_on_user` because the user never sent first (e.g., a counsel firm sending a DD list, a partner sending a JV term sheet). Each entry carries `inbound_only: true` and `is_internal: true|false`. Render this section RIGHT AFTER `waiting_on_user` — these are the freshest, highest-signal items.
- `filtered_pending` — inbox threads Haiku classified as `suppress=true` (newsletters, spam, FYI marketing). Kept for audit, NOT for rendering. In the slim stdout JSON, `body_preview` is dropped from these to save tokens; the full sidecar retains bodies.
- `cold_urgent` — user sent last, no reply, >14 days. Includes carried-forward unresolved items from the prior week's Obsidian file (marked `carried: true`).
- `cold_monitor` — user sent last, no reply, 7-14 days
- `suppressed` — entries the script removed because they appear in `done-{YYYY}.md` (matched on subject+email). Audit only; never render.
- `status_change_alerts`: counterparty kill/pass language found in `inbox_pending`, tied to an open hotcache deal when possible (`{thread, subject, from, from_email, account, signal, snippet, forwarded}`). Scanned over the RAW inbound before classification, so a pass email cannot be suppressed away first. Surfaced for review, **never auto-killed**. Render these FIRST in the deals section. A `thread: null` alert is an unattributed pass still worth a glance; `forwarded: true` is an internal forward of an external pass.
- `errors` — any section that failed (still render the rest)
- `trend` — deltas vs. last week
- `filter_stats`: `{inbox_filtered_low_urgency, suppressed_done, allowlisted_tier1, carried_forward_prior_week, resolved_by_meeting}`. Renders as a footer in `week.md` so anomalies are visible at a glance. `resolved_by_meeting` counts threads a recent meeting likely handled.

Each thread entry: `source` (configured label), `subject`, `counterparty_name`, `counterparty_email`, `age_days`, and `reply_age_days` (for waiting + inbox). `body_preview` (150 chars, unicode padding stripped) present on waiting + inbox entries. `is_internal: true` marks team members. `carried: true` marks an entry pulled forward from last week's file. `allowlisted: true` marks deal-critical-domain senders. `met_since: YYYY-MM-DD` (when present) marks a thread a recent meeting likely handled: render it de-prioritized as "likely handled, confirm" but keep it in the section count.

**Hotcache freshness:** The script reads `hotcache.md` for the Action Items section, but the rest of hotcache (Active Threads, deal stages, last_contact dates) can be days stale. Before treating hotcache deal context as ground truth, **check the `updated:` frontmatter date.** If it's >3 days old, cross-reference against recent sent mail and inbox_pending before asserting current deal state in TOP PRIORITIES.

---

## What you still need to do

### 0. Harvest checked items into done-{YYYY}.md

**Do this before rendering anything.** This is the only carry-forward step left for the agent — the script handles suppression and prior-week cold/waiting carry-forward itself.

**A. Read the existing `week.md`** at `meta.workspace_week_md` (if it exists) with a targeted grep:

macOS / Linux (bash/zsh):
```bash
grep -E "^- \[x\]" "{meta.workspace_week_md}"
```

Windows (PowerShell):
```powershell
Select-String -Pattern '^- \[x\]' -Path "{meta.workspace_week_md}"
```

Bucket the `[x]` lines into:
- **Deal threads** — lines containing a quoted subject and an email in parens
- **Source page tasks** — lines tagged with a source page name

**B. Hotcache `[Action Items]` syncs are owned by `/van-gogh:morning-coffee`** — skip them here.

For source page tasks: do not edit the source pages — they are generated meeting notes. Append to the done log instead.

**C. Append harvested items to `{meta.done_log_path}`:**

Append all checked items (deal threads + source page tasks) under a new `## Week of {week_start}` heading. Skip anything already present. Format:
```
## Week of 2026-05-04

### Deals handled
- [<account label>] "<Subject>": <Counterparty> (<email>)   <!-- label = the source account's meta.accounts[].label -->
- [<account label>] "<Subject>": <Counterparty> (<email>)

### Source page tasks completed
- [<Source page>] <Task description>
```

The format is load-bearing: the Python script parses these lines on next run to suppress matching threads. Keep the `"subject": name (email)` pattern.

That's it for harvesting. The script reads the done log and applies suppression itself; you don't need to build a suppression set or cross-check it manually.

---

### 1. Read hotcache for strategic context

Before synthesizing priorities, read the hotcache at `{meta.hotcache_path}`.

This is the source of truth for active strategic threads. Use it to:
- Understand the *weight* behind action items in the JSON
- Surface active threads that have no email trail but are high-priority
- Inform the TOP PRIORITIES ranking — strategic decisions trump aging email threads

### THE WEEK'S ONE BIG ROCK

One piece of work you choose, above the priorities. Same reasoning as the
dailies (full rules in
[`_shared/front-page.md`](../_shared/front-page.md), "The suggested focus
task"), at a week's scale: not a task that fits in a morning, but the thing
that, done by Friday, makes the most other things possible. TOP PRIORITIES
below it is code-ranked by late and due, which is a good answer to what is on
fire and no answer to what would move a business forward.

The rock, why this one, the first move, and what it unblocks. Name what it
beat. If nothing in the ledger genuinely compounds this week, say so in one
line and render no block.

---

### TRAVEL

`travel_md` is the finished block: paste it VERBATIM, directly under the one
big rock and above TOP PRIORITIES. Omit it entirely when it is empty, which is
what it is most weeks.

Do not write this section yourself and do not restate a notice in your own
words. Code builds it from the calendar and the airline confirmation, and each
line was written against the flight's own timezone, which is often not the
reader's. Never recompute or restate a time, never add a terminal or a drive
time the notice does not carry, and when a notice says a drive time could not
be established, keep that clause.

The week's window is the widest of the three briefings: an unbooked flight is
flagged as soon as it is on the calendar, because a week is the horizon on
which a flight can still be booked cheaply.

---

### 2. TOP PRIORITIES (render this first)

The script chose them. `front_page_md` is the finished block: paste it VERBATIM
and change nothing. It already carries the opening sentence, so do not write
one above it. The ranking is code: late first, then dated, then what your
stated priorities are waiting on. Never re-rank it, never re-word an item,
never add or drop one.

You write one thing here and only one: where a priority genuinely needs a
sentence of context the item line cannot carry, add it under that item. Nothing
else.

**Filtering `obsidian_tasks` before writing:** Source page items often include third-party action items captured from meeting notes. Only write items that are clearly the user's own actions — skip items where the action is assigned to another person or company. When in doubt, include it.

If `trend` key is present, append a one-line trend note after the priorities list:
`Trend vs last week — Cold urgent: {cold_urgent_prev} ({cold_urgent_delta:+d}), Waiting: {waiting_prev} ({waiting_delta:+d})`

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
### 3. Render full detail sections in this order

All suppression and carry-forward already applied by the script. Just render what's in the slim JSON sections.

`buckets_md` is a finished section: paste it verbatim where it appears below.
Code decided which bucket every item is in, which priority it sits under, and
the order. Never move an item between buckets, never re-sort, never invent or
drop a bucket. A bucket that reads `· no priorities set` is an invitation to
offer `/van-gogh:update-settings`, not a defect. When `buckets_md` is empty the
install has no businesses configured yet: skip the section and render the flat
detail sections below as before.

If `judgment.degraded` is true, `buckets_md` already opens with a line saying
nothing was filtered. Keep that line. It is the difference between a reader
seeing a full list and knowing why, and a reader assuming it was a busy day.

**TRAVEL (omit entirely when `travel` is empty, which is most weeks.)**

Render `travel` directly under the calendar, one line per notice, `text`
verbatim:

```
Travel this week
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

On a Monday the useful notice is the unbooked flight, while there is still a
week to book it. The morning-of leave-by time is Morning Coffee's job on the
day, and naming a trip here does not suppress it.

```
=== WEEK OF {week_start} to {week_end} ===

TOP PRIORITIES
1. [TAG]  ...
...

{buckets_md}          <- paste VERBATIM, change nothing

──────────────────────────────────────────────
CALENDAR: N events this week
──────────────────────────────────────────────
{day}  {time}  {title}  [{source}]
...

──────────────────────────────────────────────
DEAL STATUS CHANGES ({N}): review, never auto-killed
──────────────────────────────────────────────
[{account}] "{subject}" ({from}): {signal}   (thread: {thread or "unmatched"})
  {snippet}
(render this block FIRST; a thread:null alert is an unattributed pass; never mark a deal dead here, it is a human-confirmed change)

──────────────────────────────────────────────
DEALS, WAITING ON {meta.user_first_name} ({N})
──────────────────────────────────────────────
[{intent} • {urgency}] [{source}] "{subject}": {counterparty_name}, {reply_age_days}d ago
  {summary}
  → {suggested_action}

DEALS, INBOX PENDING ({N}), inbound, {meta.user_first_name} hasn't replied
──────────────────────────────────────────────
External (sort by reply_age_days desc):
[{source}] "{subject}": {counterparty_name} ({counterparty_email}), {reply_age_days}d
  {summary}
  → {suggested_action}

Internal team (is_internal: true, collapse into one bullet per sender, optional):
[{source}] {counterparty_name}: {N} threads, {subjects summarized}

DEALS, COLD URGENT ({N}), sent last, no reply >14 days
[{source}] "{subject}": {counterparty_name}, {age_days} days cold
(entries with carried: true were pulled forward from last week's file)

DEALS, COLD MONITOR ({N}), sent last, no reply 7-14 days
...

──────────────────────────────────────────────
OPEN TASKS BY PROJECT
──────────────────────────────────────────────
{project}: {task}
           {task}
...

──────────────────────────────────────────────
FILTER STATS
──────────────────────────────────────────────
Window: {since_days}d  |  Filtered low-urgency: {inbox_filtered_low_urgency}  |  Suppressed (done log): {suppressed_done}  |  Allowlisted tier-1: {allowlisted_tier1}  |  Carried from prior week: {carried_forward_prior_week}
```

All times already formatted as PT / ET by the script — render them as-is.

### 4. Write the week files

After rendering, write to **both** locations. Write only unchecked (`- [ ]`) items. For any deal whose entry has `met_since` set, append `<!-- met:{met_since} -->` to that line and group it under a de-prioritized `### Likely handled (met since), confirm` sub-block within its deals section. The token lets /van-gogh:morning-coffee surface it as a one-tap confirm candidate; never pre-check the box.

**A. Vault `van-gogh/` (always overwrite — latest data pull):**
```
{meta.workspace_week_md}
```

**B. Obsidian archive (no-overwrite — preserves checked items for manual tracking):**
```
{meta.weekly_dir}/week-{week_start}.md
```

For the Obsidian file: check if it already exists. If it does, skip writing it and note that to the user ("Obsidian archive already exists — skipped to preserve your check marks").

**Shape of the file.** The front page comes first: `front_page_file_md`, never
`front_page_md`. The terminal key aligns its columns with whitespace and every
markdown renderer collapses that into one run-on paragraph, which is how it
reached a published page once already. The Workbench serves `/briefing/week`
by rendering this file, so a terminal key here ships a broken page. Then the
sections below, then `folded_md` verbatim as the closing ledger.

Do NOT write `fold_rows_md` into the file: `folded_md` opens with the same
business names, so the file would list every business twice. The rows are for
the terminal, which has no folds.

Use this format for both:

```markdown
---
week: {week_start}
week_end: {week_end}
generated: {today}
---

# Week of {week_start} to {week_end}

{one big rock block}          <- the block you chose above

{travel_file_md}              <- paste VERBATIM; omit when empty, which is
                                 most weeks. Never `travel_md`: the file key
                                 carries the booking hyperlink.

{front_page_file_md}          <- paste VERBATIM, change nothing

## Top Priorities
- [ ] [TAG] {priority 1 description}
...

## Calendar
| Day | Time | Event | Source |
|-----|------|-------|--------|
| Mon May 4 | 9:00 AM PT / 12:00 PM ET | <Event Title> | <source account label> |
...

## Deals, waiting on {meta.user_first_name}
- [ ] [{source}] "{subject}": {counterparty_name} ({counterparty_email}), {reply_age_days}d ago
  {summary}
  → {suggested_action}

## Inbox, pending (inbound, {meta.user_first_name} hasn't replied)

### External
- [ ] [{source}] "{subject}": {counterparty_name} ({counterparty_email}), {reply_age_days}d
  {summary}
  → {suggested_action}

### Internal team (lower priority)
- [ ] [{source}] {counterparty_name}: {N} threads, {subjects}

## Deals, cold urgent (>14 days)
- [ ] [{source}] "{subject}": {counterparty_name} ({counterparty_email}), {age_days}d cold

## Deals, cold monitor (7-14 days)
- [ ] [{source}] "{subject}": {counterparty_name} ({counterparty_email}), {age_days}d cold

## Open Tasks (Obsidian)
- [ ] [{project}] {task}
...

{folded_md}          <- paste VERBATIM: one closed callout per business

---

**Filter stats:** Window {since_days}d · Filtered low-urgency: {N} · Suppressed (done log): {N} · Allowlisted tier-1: {N} · Carried forward: {N}
```

Rules:
- Use `- [ ]` for anything actionable
- Calendar rows are informational — no checkboxes
- The vault `van-gogh/week.md` is always overwritten (its `generated:` date drives next run's `--since auto`)
- Obsidian `week-{date}.md` is never overwritten (completion tracking file)
- Never carry `- [x]` items into either file — they go to the done log only
- To snooze a deal, append `[snooze:YYYY-MM-DD]` to the detail field. When rendering, skip any line where a `[snooze:...]` tag has a future date.

### 5. Offer next steps

**Overdue deliverables.** When `nudges` is non-empty, list each one after the
briefing (title, how many days late, the bucket it belongs to) and offer to
draft it. One line per nudge, then a single offer covering all of them.

Draft in the chat first: plain text for an email, a plain-ASCII sketch for a
deck or a sheet. Do not create a file until the user says to, and do not send
anything, ever, without a separate explicit instruction.

When `nudges` is empty, omit this entirely. Do not say "no nudges".


After confirming the file was written, ask:
- "Want me to draft a reply for any of the waiting-on-{meta.user_first_name} threads?"
- "Any of these priorities you want to dig into?"

## When to ask the user

- If `errors` is non-empty: show errors at the bottom and note which sections are missing
- If `filter_stats.inbox_filtered_low_urgency` jumps unexpectedly (>2x last run): flag it — could indicate classifier drift or a new spam pattern
- If `waiting_on_user` has high-priority deals, offer to draft the reply inline
- If a thread you expected to see is missing: check `filtered_pending` and `suppressed` in the slim JSON (or the full sidecar for bodies)

---

## Known behavior

- **`--since auto`**: reads `generated:` from the existing vault `van-gogh/week.md` and computes days since (14-day floor, 30-day cap). On weekly cadence this stays at `--since 14`; longer gaps expand up to 30.
- **Suppression and carry-forward are Python's job.** The script reads `done-{YYYY}.md` and the prior `week-{date}.md` file directly. Don't rebuild the suppression set in the agent.
- Gmail and Outlook reply checks run in parallel via ThreadPoolExecutor, but the full run (deal classification across accounts via Haiku) typically takes 5-10 minutes depending on email volume. Wait for full JSON output — do not assume it has hung.
- Outlook calendar returns all shared calendars when `--user-id me` is used (correct behavior).
- Spam/automated senders are filtered by pattern match + Haiku classification — items Haiku tags `suppress=true` go to `filtered_pending`.
- Google Calendar returns primary calendar only. Shared Google calendars are not included.
- **Hotcache tasks are not suppressed by the done log** — they come from a live source and will reappear until removed from hotcache.md directly.
- `body_preview` is 150 chars with unicode padding stripped. Full bodies are in the `.full.json` sidecar if needed.
- **Deal-critical senders** (configured in `email_filters.deal_critical_domains`) bypass the classifier — they're always tier-1 regardless of Haiku's call.
