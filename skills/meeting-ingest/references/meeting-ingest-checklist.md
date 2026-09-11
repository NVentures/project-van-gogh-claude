# Meeting Ingest Checklist — Edge Cases

The `meeting_ingest.py` script handles ~95% of meeting ingestion mechanically. This file covers the human-judgment cases that the script cannot decide for itself. Read it when something feels ambiguous or when the dry-run output flags an unexpected pattern.

## Sales-call vs meeting type

The script defaults to `--type meeting`. Pass `--type sales-call` when:
- The meeting includes attendees from a known prospect/customer organization
- The notetaker folder name contains "Sales", "Demos", "Prospects", or similar
- The discussion is structured around product demo, pricing, or commercial terms

When unsure, ask the user. Default to `meeting` if no clear signal.

## Business tag inference

The script infers the business tag from detected entities' frontmatter `business:` field, taking the most common. This works when there are existing entities in the wiki that match what's discussed in the summary. When the inference fails:

- **No entities detected**: script returns `(none)`. You must pick from the configured business tags (`config.json` `businesses[].tag`). Match signals against each business's `keywords[]`:
  - Attendees whose email domain matches a business's internal domain, that business
  - Topics that hit one of a business's configured `keywords[]`, that business
- **Multiple plausible tags**: pick the one for the org actually paying for the meeting time. Add additional tags to the source page's `tags:` array if the topic crosses boundaries (e.g. a secondary business discussed inside another business's meeting, primary `business:` is the host and `tags:` lists both).

## Project Dev Log rollup

The script does NOT touch project Dev Logs. After ingest, decide whether to append a one-line dated entry to a project page's `## Dev Log`:

**Roll up when ALL of these are true:**
1. The meeting is unambiguously tied to one specific `wiki/projects/*.md` page
2. Something substantive happened (decision, learning, status change, customer signal)
3. The user confirms when asked

**Don't roll up when:**
- Routine status updates with no decisions
- Meeting tagged to a parent business rather than a sub-brand, those stay in the source page only
- Meeting touches multiple projects equally — file the source page, mention it in your report, but don't pick one

**Format (always confirm with user before writing):**
```
- **YYYY-MM-DD** — <1-2 sentence narrative of decisions/learnings>. See [[Source Page Title]].
```

Action items stay only in the source page. Dev Log is narrative, not a task list.

## Attendee handling

The script wikilinks attendees that match existing entity pages (or aliases). New attendees are listed as plain text in the source page. Do NOT auto-promote them to entity pages unless:

- They're internal team members who'll appear repeatedly (your own employees or teammates)
- They're partners/vendors you'll reference in multiple sources

For one-off external attendees (lawyers, landowners, single-meeting prospects), leave them as plain text. They earn an entity page on their second appearance, not their first. The `--stub-attendees` flag exists if you do want to bulk-promote, but it's intentionally off by default.

## Jargon table misses

The script applies the Jargon & Alias Table from `wiki/CLAUDE.md` mechanically. If you spot a transcription error in the written page that's NOT in the table (e.g. a name the notetaker consistently mishears), do two things:

1. Fix the source page by hand
2. Add the correction to the Jargon & Alias Table in `wiki/CLAUDE.md` so the next ingest catches it automatically

This is the self-annealing loop. The table grows whenever a new error appears.

## When the script reports 0 action items

Could mean:
- The meeting genuinely has no action items (some discovery/strategy meetings don't)
- The AI summary uses an unexpected heading the regex doesn't match yet

If the meeting clearly had follow-ups but the script returned 0, check the raw summary:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_fetch.py" --meeting <id> --fields summary
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_fetch.py" --meeting <id> --fields summary
```
Look for the actual heading the notetaker used. If it's a new variant (e.g. `### Action Plan`), update the `ACTION_HEADINGS_RE` regex in `meeting_ingest.py` to include it.

## Re-ingesting an existing meeting

The script refuses to overwrite by default. If you need to re-ingest:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_ingest.py" \
    --meeting <id> --apply --business <tag> --force
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_ingest.py" --meeting <id> --apply --business <tag> --force
```
This rewrites only the source page. The index.md and log.md updates are idempotent — they won't add duplicate entries.

## Pre-write sanity checks (when something feels off)

- Title has no strings from the vault's own Jargon and Alias table
- Date is the actual meeting date (from `calendar_event.scheduled_start_time`), not the notetaker's processing date
- Business tag matches the rest of the meeting's signals
- Detected entities make sense for the topic (none missing, none false-positive)
- No raw email addresses or phone numbers anywhere in the page
