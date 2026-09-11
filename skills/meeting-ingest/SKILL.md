---
name: meeting-ingest
description: Ingest a meeting summary from the configured AI notetaker (Granola, Grain, ...) into the Second Brain wiki via app/meeting_ingest.py. Use when the user says "ingest my last meeting", "ingest granola", "ingest grain", "pull the [meeting name] from my notetaker", references a notes.granola.ai or grain.com URL, or asks to file/summarize a recent call. The script handles fetch, jargon corrections, summary insertion, action item extraction, share URL detection, entity wikilinking, index/log updates. You only pick the business tag and handle ambiguities.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Meeting Ingest

End-to-end ingestion is handled by a deterministic Python script. Your only jobs are: pick a business tag, handle ambiguities, optionally roll up to a project Dev Log. Everything else (fetching, jargon corrections, action item extraction, entity wikilinking, schema-compliant page generation, index/log updates) is mechanical.

The script does NOT fetch the transcript — only the AI summary. The transcript is retrievable on demand via the `meeting_id` / `meeting_source` pair stored in the source page frontmatter (see "Asking ad-hoc questions" below).

## Unattended Mode (--auto)

When invoked as `/van-gogh:meeting-ingest --auto` (e.g. from a LaunchAgent), skip all confirmation questions and apply immediately:

1. **Python runtime:** run the ensure-venv guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md) before the first script invocation.
2. Run `--meeting uningested --dry-run` to get the list and inferred tags.
3. For each meeting, apply using the script's inferred `--business` tag. If the script cannot infer a tag, default to `personal` (or another tag from `config.json businesses[].tag` that matches the meeting context).
4. Default `--type` to `meeting` for all.
5. Skip Dev Log rollup entirely (requires human judgment).
6. Skip attendee entity page promotion entirely.
7. Log what was ingested and what was skipped to stdout. Do not ask anything.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Workflow

Always start with `--meeting uningested` to catch everything not yet in Obsidian (scans last 30). Only drop to `--meeting latest` if the user explicitly asks for just the most recent meeting.

macOS / Linux (bash/zsh):
```bash
# Step 1: preview all uningested meetings (default, always start here)
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_ingest.py" --meeting uningested --dry-run

# Step 2: dry-run each ambiguous meeting individually to get inferred tags
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_ingest.py" --meeting not_xxxxxxx --dry-run

# Step 3: apply each one (business tag required; <tag> is one of businesses[].tag)
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_ingest.py" --meeting not_xxxxxxx --apply --business <tag>
```

Windows (PowerShell):
```powershell
# Step 1: preview all uningested meetings (default, always start here)
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_ingest.py" --meeting uningested --dry-run

# Step 2: dry-run each ambiguous meeting individually to get inferred tags
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_ingest.py" --meeting not_xxxxxxx --dry-run

# Step 3: apply each one (business tag required; <tag> is one of businesses[].tag)
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_ingest.py" --meeting not_xxxxxxx --apply --business <tag>
```

If the user names a specific meeting, find its ID:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_fetch.py" --list --limit 5
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_fetch.py" --list --limit 5
```

Then pass the `not_xxxxxxx` ID to `--meeting`.

## Flags

- `--meeting <id|latest|uningested>` — required. A meeting ID from the active notetaker, `latest` for the most recent only, or `uningested` to scan the last 30 and skip already-ingested ones.
- `--business <tag>` — required for `--apply`. Must be one of the `tag` values defined in `config.json businesses[]`. Optional for `--dry-run`; script will infer from detected entities and print the suggestion.
- `--type meeting|sales-call` — default `meeting`. Use `sales-call` for prospect/customer calls.
- `--dry-run` / `--apply` — preview vs write.
- `--force` — overwrite an existing source page (idempotent by default).
- `--stub-attendees` — auto-create minimal entity stubs for attendees with no existing page. Default: off (too aggressive — see "When to use stubs" below).
- `--source <name>` — read from a specific notetaker (`granola`, `grain`) instead of the configured one. Rarely needed: the provider comes from `config.json notetaker.provider`, or is inferred from whichever API key is set.
- `--link-deals` — after applying, append `<!-- met:YYYY-MM-DD -->` to any matching open deal lines in the current Obsidian weekly file. Matching is by attendee email (exact) or meeting title name tokens (fallback). If matches are found, they're printed to stdout regardless of this flag; the flag only controls whether the weekly file is written.

## What the script does

- Fetches the meeting summary from the active notetaker (no transcript)
- Strips PII (emails, phone numbers)
- Applies the Jargon & Alias Table from `wiki/CLAUDE.md`
- Wikilinks known entities/concepts/projects (uses `aliases:` frontmatter when present)
- Extracts action items from `## Action Items`, `### Next Steps`, or similar headings
- Extracts the share URL (a field on Grain; the summary footer on Granola)
- Stores `meeting_id:` and `meeting_source:` in source page frontmatter for later transcript fetches
- Writes `wiki/sources/<Title> - <date>.md` using the streamlined meeting schema
- Appends a one-line entry to `wiki/index.md` under `## Sources`
- Appends a block to `wiki/log.md` under today's date with `ingest |` prefix
- Refuses to overwrite existing source pages unless `--force`

## What you still need to do

1. **Read the written page** to spot any issues (jargon table misses, ambiguous wikilinks, AI summary inaccuracies the user might want fixed by hand)
2. **Decide on project Dev Log rollup** — if the meeting is unambiguously tied to a project page in `wiki/projects/`, append a one-line dated entry to that page's `## Dev Log` section. Skip if multiple or no projects match. Always confirm with the user before writing.
3. **Promote attendees to entity pages** if they're internal team members or partners who'll appear repeatedly. Don't auto-stub one-off external attendees.

## When to ask the user

- Sales-call vs meeting (if not obvious from attendees/topic)
- Multiple plausible business tags (script will report which one it inferred — confirm if borderline)
- Whether to roll up to a project Dev Log
- Whether to promote new attendees to entity pages

## When to use `--stub-attendees`

Off by default. Only pass it when:
- The meeting is full of internal team members who deserve their own pages
- You want to bulk-create stubs for a known team's first appearance

Don't use it for one-off external attendees (lawyers, landowners, prospects). They're noise until they appear in a second source.

## Asking ad-hoc questions about a meeting later

The source page frontmatter stores `meeting_id:` and `meeting_source:`. To pull the transcript on demand:

macOS / Linux (bash/zsh):
```bash
# Read the source page first to get the meeting_id
# Then:
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_fetch.py" \
    --meeting <meeting_id> --fields transcript
```

Windows (PowerShell):
```powershell
# Read the source page first to get the meeting_id
# Then:
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_fetch.py" --meeting <meeting_id> --fields transcript
```

Pipe to `grep` or load directly to answer questions like "what did Ryan say about the substation?". The transcript stays in context only for the duration of that one question.

## References

- [notetaker-api.md](references/notetaker-api.md) — per-provider endpoint shapes, exit codes, raw API quirks (read if the script breaks)
- [meeting-ingest-checklist.md](references/meeting-ingest-checklist.md) — edge-case handbook for the human-judgment parts (sales-call vs meeting, Dev Log rollup, attendee promotion)
