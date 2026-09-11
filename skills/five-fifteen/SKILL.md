---
name: five-fifteen
description: Weekly client 5:15 reports — a 5-minute-to-read, 15-minute-to-write client update, one per advisory client. For each client it pulls the week's signals from that client's own sources (email, Slack, Microsoft Teams, HubSpot) plus matched notetaker meetings, reads last week's report to carry forward open items, then synthesizes a look-back (worked on / accomplished / identified / still outstanding) and look-forward (next week's focus) in the user's voice. Writes a dated markdown copy into the vault and prepares a ready-to-review email draft to each client contact. Use when the user types /van-gogh:five-fifteen or asks for their weekly 5:15 / client update reports. Runs every Friday.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Five-Fifteen (Weekly Client Reports)

A **5:15 report** = 5 minutes to read, 15 minutes to write. One per advisory
client. Each is a look **back** at the week (what was worked on, accomplished,
identified, and what's still outstanding) and a look **forward** (next week's
focus), written to keep the user and the client aligned. Nothing is auto-sent —
you produce a vault copy and a review-ready draft; the user sends.

The Python script `app/five_fifteen.py` does the deterministic work (week
window, meeting match, prior-report lookup, path/recipient/account resolution).
You gather each client's connector signals, synthesize the narrative in the
user's voice, write the vault file, and prepare the email.

**How this reads.** The person reading it runs a business, not a
terminal. Short sentences, lead with the point, no vocabulary from the
data model, no filler openers. The full rules are the "Written Voice"
section of `DESIGN.md`; follow them for every line you write here.

## True north — the contract is the spine of every report

The whole point of a 5:15 is to show the client that the engagement is
delivering against what it was hired to do. So the contract isn't background —
it's the organizing principle. `client.contract` carries the engagement terms:

- `counterparty`, `term`, `retainer`, `engagement` (e.g. "50% time allocation")
- `priorities[]` — the contracted lines of work, **in priority order**, each
  `{name, detail}`. These are the section headers your look-back and look-forward
  hang on. Lead with Priority 1; a quiet week on P1 is itself worth saying.
- `value_levers[]` — how the user earns beyond retainer (performance comp,
  commission tiers, bonuses). When the week moved something tied to a lever
  (capacity contracted, ARR booked), name it in those terms — that's the dollars
  the reader cares about.
- `out_of_scope[]` — work the engagement excludes. Don't claim credit for it,
  and if a request is drifting out of scope, that's a legitimate alignment note.

Organize each report around `priorities[]` so the client sees, week over week,
movement on exactly what they're paying for. If `contract` is empty, fall back
to organizing by the client's actual workstreams and note that no contract
anchor is configured.

## Resolved values come from the script

`five_fifteen.py`'s JSON output includes a top-level `meta` block and a
per-client `client` block. Reference these verbatim — never substitute
placeholders:

- `meta.user_first_name`, `meta.user_full_name`
- `meta.clients[]` — every configured client (run `app/skill_context.py` for
  this list without a full data pull). Each has `tag`, `display_name`,
  `from_account`, `from_email`, `from_platform` (`gmail`|`outlook`), `sources`,
  `recipient_name`, `recipient_email`, `signoff`, `report_dir`, and `contract`
  (the engagement terms — see **True north** above; `{}` if not configured).
- Per run the output also has: `client` (the resolved client), `week`
  (`ending`, `start`, `label`, `ending_display`, `range_display`), `meetings`
  (meetings matched to this client), `meetings_all_titles` (every meeting in the
  window — use to catch a meeting keyword-match missed), `prior_report`
  (`path`, `text`, `found`), `output_path` (where to write this week's file),
  `signals` (the connector data you passed in), `errors`.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Which clients to run

Default: **all** clients in `meta.clients[]`. If the user named one
("just the Client One 5:15"), run only that one. Run each client end-to-end
(fetch → script → synthesize → write → draft) before moving to the next.

To list clients without a data pull:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/skill_context.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\skill_context.py"
```

If `meta.clients[]` is empty, stop: point the user to
`/van-gogh:update-settings` to add a client (or ask for the details to add).

## Detect tier

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
```

Meeting notes are always fetched by the script directly. Email/Slack/Teams/HubSpot
come from **connectors** in Tier 2: fetch them yourself and pass them to the
script via `--input`. In Tier 1 the same `--input` shape still applies for the
connector-only sources (Slack/Teams/HubSpot have no OAuth client); gather what
you can and pass it in.

---

## Per client: 1 — gather the week's signals (connectors)

Compute the week window first (the script uses the most recent Friday by
default; you can preview it from `week` after step 2, or just fetch the last
7 days). For each source listed in the client's `sources`, fetch items dated in
the window `[week.start, week.ending]` and collect them into an `--input` JSON.
Only pull from the sources that client actually uses, and only via connectors
that are actually available in this session — a configured source whose
connector isn't connected is skipped and noted, never a hard failure.

**email** — both directions, scoped to the client's `from_account` mailbox:
- If `from_platform` is `gmail`: use the Gmail connector. Sent: `in:sent after:{start} before:{ending+1}`. Received from the client domain/contacts as relevant.
- If `from_platform` is `outlook`: use the Outlook connector. Search `Sent Items` and Inbox by date range; filter to threads relevant to this client (recipient/sender/keywords).
- Per item capture: `direction` (`sent`|`received`), `subject`, `counterparty`, `date` (ISO), `preview` (~200 chars).

**slack** — the client's Slack workspace. Search messages you (the user) sent
or that mention the client's deals in the window. Capture `channel`, `date`,
`text` (trimmed), `author`.

**teams** — Microsoft Teams chat via the Microsoft connector's chat message
search. Capture `chat`/`topic`, `date`, `text`, `author`.

**hubspot** — the client's HubSpot portal. Pull deals updated in the window
(name, stage, amount, close date, last activity) and any notable notes/tasks.
Capture under `deals[]` and `notes[]`.

**claude code sessions** — *optional but high-signal when available.* If a
session-memory search tool is connected (e.g. the claude-mem plugin's
`search` / `observation_search` / `timeline` tools), query it for
sessions/observations in the week window matching the client's `keywords` —
much of the user's real analysis, drafting, and deal strategy happens inside
Claude Code and never shows up in email or Slack. Capture the substance, not
the mechanics: what was figured out, drafted, decided, or shipped. Each item:
`date`, `summary` (one line, outcome-focused), and `artifact` if a concrete
deliverable resulted (doc name / vault path). If no such tool is connected,
skip this source silently — do not fail the run.

Write everything to a temp JSON file (cross-platform — use the OS temp dir, not
`/tmp`):

macOS / Linux (bash/zsh):
```bash
TMPFILE=$(mktemp "${TMPDIR:-/tmp}/van-gogh-515-XXXXXX.json")
```

Windows (PowerShell):
```powershell
$TMPFILE = [System.IO.Path]::GetTempFileName()
```

```json
{
  "email":   [{"direction": "sent", "subject": "...", "counterparty": "...", "date": "2026-05-27T18:00:00Z", "preview": "..."}],
  "slack":   [{"channel": "#deals", "date": "2026-05-27", "author": "User", "text": "..."}],
  "teams":   [{"chat": "Client One GTM", "date": "2026-05-27", "author": "User", "text": "..."}],
  "hubspot": {"deals": [{"name": "Deal Name", "stage": "Negotiation", "amount": 50000, "close_date": "2026-06-30", "last_activity": "2026-05-28"}], "notes": ["..."]},
  "claude_sessions": [{"date": "2026-05-28", "summary": "Drafted the GTM-agent spec doc for the client", "artifact": "wiki/analyses/Client One AI Agents.md"}]
}
```

Omit keys for sources the client doesn't use or that returned nothing — note
what was skipped and synthesize from what you have.

## Per client: 2 — run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/five_fifteen.py" --client <tag> --input "$TMPFILE"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\five_fifteen.py" --client <tag> --input "$TMPFILE"
```

Pass `--week-ending YYYY-MM-DD` only to override the default (most recent
Friday). Read the full JSON before synthesizing. Inspect `meetings_all_titles`:
if a meeting clearly belongs to this client but isn't in `meetings`, fold it in
manually.

## Per client: 3 — synthesize the 5:15

Write in the user's voice — prime with `meta.voice_guide_path` and
`meta.tone_profile_path` when they exist. Match the structure of
`prior_report.text` when one exists — clients expect a consistent shape week to
week. Otherwise use this template. **Section headers are the contract's
`priorities[]`, in order** — that's how the client sees movement on what they're
paying for:

```
{Greeting to recipient_name},

{One warm line, e.g. "Hope you're well and having a nice weekend.", then a
one-line frame: "Please see the below weekly recap and look-forward for the
coming weeks." Add a travel line here only when the user is out (leaving when,
back when).}

The key initiatives from last week were:

1. {The single most important thing being driven, concrete, with the date/target it's aimed at.}
2. {Second.}
3. {Third. Usually 2-4 items.}

## {Priority 1}
- {What moved, concrete: names, numbers, dates. Tie to the objective. Keep
  next-step actions in the Next Week section, not here, so nothing repeats.}

## {Priority 2}
- ...

## {Priority 3}
- ...

## Watch / Needs a Decision
- {What's blocked, at risk, or needs the client's principals to weigh in.
  Include anything drifting out of scope.}

## Next Week
- {The 2-4 priorities for the coming week, mapped to the same workstreams.}

## On the Horizon (next few weeks)
- {What's coming that isn't next-week's work yet: milestones, decisions, or
  closes 2-4 weeks out. Keeps the client seeing around the corner.}

{signoff}
```

Rules — write like a CEO briefing a CEO:
- **Open warm, then the work.** Start with a brief, warm greeting + a one-line
  frame ("Hope you're well — please see the below weekly recap and look-forward"),
  plus a travel line when the user is out. Then "The key initiatives from last
  week were:" and a short numbered list (2–4) of what's being driven, each with
  its date/target. Never editorialize with a headline like "X is the week" —
  the user wouldn't write that. The numbered items are the executive summary;
  the priority sections below are where the specifics go.
- **Brief, bulleted, direct — facts only.** No preamble, no throat-clearing, no
  AI or consultant speak ("leveraged," "synergies," "circled back," "I've gone
  ahead and"). Plain speak. Cut hedging and adjectives ("strong potential," "I'm
  comfortable with," "really"); state the fact. Short declarative bullets — when
  a line can be shorter, make it shorter. A busy CEO reads this in 5 minutes.
- **Organize by the contract's priorities** so value maps to what was promised.
  Lead with Priority 1. A quiet week on a priority is worth one honest line, not
  silence.
- **Speak in the value levers.** When something moved that touches comp
  (capacity contracted, ARR booked, a close date set), name the number —
  that's the dollars the reader tracks.
- **Carry forward.** Reconcile this week against last week's "Next Week" items
  from `prior_report.text`. Anything not advanced is still outstanding — say so;
  don't silently drop it.
- **Evidence-based.** Every claim traces to a signal — email/Slack/Teams/
  HubSpot/meeting notes and any Claude Code session memory. Don't invent progress. If
  the week was quiet, say so plainly; a CEO trusts an honest light week more
  than manufactured activity.
- **Numbers and dates** (units contracted, ARR, close dates) are what make a
  5:15 worth reading — include them.

## Per client: 4 — write the vault copy

Write the rendered report (markdown body, no email greeting/signoff needed in
the file header) to `output_path`. Create the parent folder if missing.
Frontmatter:

```markdown
---
client: {client.display_name}
week_ending: {week.ending}
week_label: {week.label}
generated: {YYYY-MM-DD HH:MM}
recipient: {client.recipient_name}
---
```

Then the full report body. Overwrite if the file already exists (idempotent
re-run for the same week).

## Per client: 5 — prepare the email draft

Subject: `5:15 Report — Week Ending {week.ending_display}` (keep the client's
own convention if `prior_report` shows a different one). Body = the synthesized
report including the greeting and `client.signoff`. To: `client.recipient_email`.

- If `client.recipient_email` is empty: resolve it by searching the
  `from_account` mailbox for `recipient_name`; if still unknown, **ask the user
  for the address** before drafting (and offer to save it to config).
- If `from_platform` is `gmail` **and** the Gmail connector can reach that
  mailbox: create a Gmail **draft** (never send). Confirm the draft was created.
- If `from_platform` is `outlook`: **always run** `app/outlook_draft.py` — it
  creates a real **draft** in the Drafts folder (`POST /me/messages` via the
  Graph client; `Mail.ReadWrite` covers it, and it does **not** send). Never
  decide the tier yourself by inspecting environment variables or connector
  capabilities — the token lives in `~/.config/van-gogh/.env`, which only the
  script can read, so **the script is the probe**.

  macOS / Linux (bash/zsh):
  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/outlook_draft.py" --label "<FROM_ACCOUNT_LABEL>" --to "<RECIPIENT_EMAIL>" --subject "<SUBJECT>" --body-file "<OUTPUT_PATH>" --strip-frontmatter
  ```

  Windows (PowerShell):
  ```powershell
  & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\outlook_draft.py" --label "<FROM_ACCOUNT_LABEL>" --to "<RECIPIENT_EMAIL>" --subject "<SUBJECT>" --body-file "<OUTPUT_PATH>" --strip-frontmatter
  ```

  Read the JSON it prints:
  - `{"ok": true, ...}` — confirm the draft was created and share its
    `web_link`.
  - `{"ok": false, "error": "no_token", ...}`, this machine is Tier 2 for
    that account: put the full ready-to-paste email (To / Subject / Body) in
    your reply, tell the user to paste it into Outlook, and mention that
    `/van-gogh:add-account` can connect the account so future reports land as
    real drafts.
  - `{"ok": false, "error": "draft_failed", ...}` — relay the message and use
    the same paste fallback.
- Do the same paste fallback for Gmail if the draft call fails.

Never send. The user reviews and sends.

---

## After all clients

Briefly report, per client: vault path written, draft status (Gmail/Outlook
draft created with its `webLink`, or ready-to-paste in Tier 2), and any
`errors`. Then offer:
- "Want me to adjust the tone or trim any section before you send?"
- "Any open item you want carried into next week's focus that I missed?"

## When to ask the user

- A client's `recipient_email` is empty and can't be resolved from sent mail.
- All of a client's configured sources returned nothing (connector down or not
  connected) — confirm whether to skip that client or proceed with meeting notes only.
- The client config is missing (`meta.clients[]` empty) — point them to
  `/van-gogh:update-settings` to add a client, or ask for the details to add.
