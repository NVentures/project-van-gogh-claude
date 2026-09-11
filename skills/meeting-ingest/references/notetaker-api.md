# Notetaker APIs — Reference

Brief reference for the meeting-notetaker APIs as wrapped by the scripts in
`${CLAUDE_PLUGIN_ROOT}/app/`:

- **`notetaker.py`** — the provider layer. Picks the active service, defines the
  canonical note shape, and owns everything shared (windowing, PII stripping,
  date resolution, commitment extraction). Providers live beside it as
  `notetaker_<name>.py`.
- **`meeting_ingest.py`** — high-level: end-to-end ingest into the Second Brain
  wiki (summary only, no transcript). Entry point for the `meeting-ingest` skill.
- **`meeting_fetch.py`** — low-level: raw queries with field whitelisting. Used
  for ad-hoc transcript fetches, listing meetings, `--check` diagnostics, or
  anything `meeting_ingest.py` doesn't cover.

Read this reference when either script returns an unexpected shape, when you need
a field they don't expose, or when you need to call an API directly for a one-off.

## Which provider is active

One notetaker is active at a time. `config.json notetaker.provider` decides
(`granola`, `grain`); when it is empty, the layer infers from whichever API key
is present in `~/.config/van-gogh/.env`. To see where you stand:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_fetch.py" --check
```
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_fetch.py" --check
```

That prints the active provider, whether its key round-trips, and every
registered provider with its key status.

---

# Granola

**Official docs:** https://docs.granola.ai/

## Auth

- Header: `Authorization: Bearer grn_<key>`
- Key lives in `~/.config/van-gogh/.env` as `GRANOLA_API_KEY`
- Created in Granola → Settings → Personal API Keys
- Rate limits are per-workspace; per-user limits also apply to personal keys; 429 = back off

## Base URL

```
https://public-api.granola.ai/v1
```

## Endpoints

### `GET /notes` — list notes

| Param | Type | Notes |
|---|---|---|
| `created_after` | ISO date/datetime | inclusive lower bound |
| `created_before` | ISO date/datetime | inclusive upper bound |
| `updated_after` | ISO date/datetime | for incremental sync |
| `cursor` | string | pagination |
| `page_size` | int | 1–30, default 10 |

Response:
```json
{
  "notes": [
    { "id": "not_…", "object": "note", "title": "string|null",
      "owner": {"name": "string", "email": "string"},
      "created_at": "ISO8601", "updated_at": "ISO8601" }
  ],
  "hasMore": true,
  "cursor": "string|null"
}
```

The list view is a **NoteSummary** — no transcript, no attendees, no calendar event. To get those, fetch the individual note.

### `GET /notes/{id}` — get a single note

Path: `/notes/not_<14alnum>`. Use `?include=transcript` to include transcript items.

Response:
```json
{
  "id": "not_…",
  "object": "note",
  "title": "string|null",
  "owner": {"name": "...", "email": "..."},
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "calendar_event": {
    "event_title": "string|null",
    "invitees": [{"email": "..."}],
    "organiser": "email|null",
    "calendar_event_id": "string|null",
    "scheduled_start_time": "ISO8601|null",
    "scheduled_end_time": "ISO8601|null"
  } ,
  "attendees": [{"name": "...", "email": "..."}],
  "folder_membership": [{"name": "...", "id": "..."}],
  "summary_text": "plain string",
  "summary_markdown": "markdown string|null",
  "transcript": [
    {"speaker": {"source": "microphone|speaker"},
     "text": "string", "start_time": "ISO8601", "end_time": "ISO8601"}
  ]
}
```

**Important quirks:**
- The API only returns notes that have a generated AI summary AND transcript. Unprocessed notes return 404.
- There is **no `action_items` field**. Action items must be parsed out of `summary_markdown`. The AI summary uses different headings depending on meeting style: `## Action Items`, `### Action Items`, `### Next Steps`, `### Follow-ups`, etc. `meeting_ingest.py` matches all of these.
- There is **no top-level `share_url` field**. But the AI summary footer consistently includes `Chat with meeting transcript: [https://notes.granola.ai/t/<uuid>](...)` — `meeting_ingest.py` extracts the URL via regex on this footer.
- `calendar_event.scheduled_start_time` is the meeting time; `created_at` is when Granola first processed the note. Prefer the calendar time when present.
- `summary_text` and `summary_markdown` are different surfaces of the same content. Always prefer `summary_markdown` (the scripts do).

---

# Grain

**Official docs:** https://developers.grain.com/

> **Verification status:** this integration was built against Grain's published
> API reference (version `2025-10-31`) and is covered by
> `tests/test_notetaker_grain.py` with recorded fixtures, but it has not yet been
> exercised against a live Grain workspace. If a field comes back differently
> than documented here, fix the mapping in `app/notetaker_grain.py` — that file
> is the only place Grain's shape is known, and it already reads field names
> defensively (snake_case and camelCase, string and object forms).

## Auth

- Header: `Authorization: Bearer <token>`
- **Also required on every request:** `Public-Api-Version: 2025-10-31`
- Key lives in `~/.config/van-gogh/.env` as `GRAIN_API_KEY`
- Personal Access Token created in Grain → Account settings → Integrations →
  Personal API. **Requires a Starter plan or above** — the Free plan has no API
  access at all, and a token from a Free workspace returns 403.
- Rate limit: 300 requests/minute per token; 429 = back off.

## Base URL

```
https://api.grain.com/_/public-api/v2
```

## Endpoints

**Reads are POSTs.** Grain's v2 read endpoints take a JSON body carrying `filter`
and `include` rather than a query string. The transcript endpoint is the one GET.

### `POST /recordings` — list recordings

Body:
```json
{
  "cursor": "string|null",
  "limit": 50,
  "filter": {
    "after_datetime": "ISO8601",
    "before_datetime": "ISO8601",
    "title_search": "string",
    "participant_scope": "…",
    "team": "…",
    "meeting_type": "…"
  },
  "include": { "participants": true }
}
```

Response: `{ "recordings": [ … ], "cursor": "string|null" }`

### `POST /recordings/{id}` — get one recording

Body:
```json
{ "include": { "participants": true, "ai_summary": true,
               "ai_action_items": true, "calendar_event": true } }
```

Response (the fields the mapping reads):
```json
{
  "id": "uuid",
  "title": "string",
  "start_datetime": "ISO8601",
  "end_datetime": "ISO8601",
  "duration_ms": 0,
  "url": "https://grain.com/share/recording/…",
  "participants": [{"id": "…", "name": "…", "email": "…",
                    "scope": "…", "confirmed_attendee": true}],
  "ai_summary": "markdown string",
  "ai_action_items": ["string", {"text": "…", "assignee": {"name": "…"}}],
  "teams": [{"id": "…", "name": "…"}]
}
```

### `GET /recordings/{id}/transcript` — transcript

Returns JSON turns: `{ "start": ms, "end": ms, "text": "…", "speaker": "Name",
"participant_id": "uuid" }`. `.txt`, `.vtt` and `.srt` variants exist at the
respective paths; the scripts only use JSON.

**Important quirks (all absorbed in `notetaker_grain.py`):**
- **Action items are a separate field.** Granola ships one markdown blob with an
  `## Action Items` heading in it, and the whole ingest pipeline reads that
  heading. `_summary_markdown()` re-joins `ai_summary` and `ai_action_items` into
  that one blob so nothing downstream needs a second shape.
- **There is no `created_at`.** `start_datetime` is both the meeting time and the
  note timestamp; the mapping puts it in `calendar_event.scheduled_start_time`
  so `note_date()` resolves the same way it does for Granola.
- **`url` is the share link**, a real field — unlike Granola, no footer regex
  needed. `meeting_ingest.py` prefers the field and falls back to the footer.
- A 403 means "valid token, no API access on this plan", not "bad token".

---

# Script output contract (`meeting_fetch.py`)

The script reshapes whichever provider answered into one stable, lean contract:

```json
{
  "id": "not_… | uuid",
  "title": "Meeting Title",
  "date": "YYYY-MM-DD",
  "attendees": ["Name1", "Name2"],
  "summary": "markdown string (PII stripped)",
  "transcript": "[speaker] line\n[speaker] line\n… (PII stripped)",
  "calendar_event": { … raw object … },
  "source": "granola | grain"
}
```

Optional fields available via `--fields`: `created_at`, `updated_at`, `owner`,
`folder_membership`, `share_url`, `source`.

The `date` field is derived: `calendar_event.scheduled_start_time` → date,
falling back to `created_at`. The `transcript` field flattens the array into one
line per turn, prefixed with `[speaker]`. PII (emails, phones) is stripped from
`summary` and `transcript` before output.

## Error codes from the scripts

Shared by `meeting_fetch.py` and `meeting_ingest.py`:

| Exit | Meaning |
|---|---|
| 2 | The active provider's API key is not set in `.env` |
| 3 | Network/transport error |
| 4 | 401/403 — bad, expired, or plan-restricted key |
| 5 | 404 Not Found — wrong ID or unprocessed note |
| 6 | 429 Rate Limited — back off |
| 7 | Other HTTP failure (status + body printed to stderr) |
| 8 | `--meeting latest` returned no meetings |
| 9 | Unknown field name in `--fields` (`meeting_fetch.py`) |
| 14 | Unknown `--source` (no such notetaker provider) |

`meeting_ingest.py` adds 10–13 (business/type/page-exists); see its docstring.

## Calling an API directly (without the script)

For one-offs only, prefer the script.

Granola — macOS / Linux (bash/zsh):
```bash
curl -s -H "Authorization: Bearer $GRANOLA_API_KEY" \
  "https://public-api.granola.ai/v1/notes?page_size=5" | python3 -m json.tool
```

Granola — Windows (PowerShell):
```powershell
$h = @{ Authorization = "Bearer $env:GRANOLA_API_KEY" }
Invoke-RestMethod -Headers $h -Uri "https://public-api.granola.ai/v1/notes?page_size=5" |
  ConvertTo-Json -Depth 10
```

Grain — macOS / Linux (bash/zsh):
```bash
curl -s -X POST \
  -H "Authorization: Bearer $GRAIN_API_KEY" \
  -H "Public-Api-Version: 2025-10-31" \
  -H "Content-Type: application/json" \
  --data '{"limit": 5, "include": {"participants": true}}' \
  "https://api.grain.com/_/public-api/v2/recordings" | python3 -m json.tool
```

Grain — Windows (PowerShell):
```powershell
$h = @{ Authorization = "Bearer $env:GRAIN_API_KEY"; "Public-Api-Version" = "2025-10-31" }
Invoke-RestMethod -Method Post -Headers $h -ContentType "application/json" `
  -Body '{"limit": 5, "include": {"participants": true}}' `
  -Uri "https://api.grain.com/_/public-api/v2/recordings" | ConvertTo-Json -Depth 10
```
