---
name: update-settings
description: Walk a non-technical user through updating config.json — adding,
  changing, or removing identity, accounts, vault paths, businesses, or email
  filters. Use when the user says "update my config", "add a business", "change
  my email", "fix my vault path", "remove a project", "add a keyword", or
  anything else that means editing config.json. Never edit config.json directly
  without invoking this skill — it handles backup, validation, and confirmation.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Update Config

Your job: help the user edit `config.json` conversationally. They may not know
JSON. You translate plain English into a precise edit, show them the change,
and only write after they say yes.

## The sections of config.json

Use these plain-English names with the user (not raw keys):

| User-facing name        | JSON section      | What lives here                                                  |
|-------------------------|-------------------|------------------------------------------------------------------|
| **About you**           | `user`            | Name, first name, nicknames, role description                    |
| **Email & calendar accounts** | `accounts`  | Gmail + Outlook addresses and the labels shown in briefings      |
| **Obsidian vault**      | `obsidian`        | Vault path and the files/folders inside it the scripts read/write |
| **Businesses / buckets** | `businesses`    | One entry per venture, client, or area of life: tag, name, project page, meeting folder, keywords, and the priorities inside it |
| **Meeting notetaker**   | `notetaker`       | Which notetaker meeting notes are read from (one at a time)      |
| **Briefing front page** | `briefing`        | Front-page size, the kind-of-work vocabulary, your own filing keywords, and the private web page toggle |
| **Email filters**       | `email_filters`   | Domains/emails to treat as internal, allow-listed, or extra spam |
| **Failure alerts**      | `support`         | Where to email when an unattended job keeps failing (off by default) |
| **Chat memory mirror**  | `memory_sync`     | Whether chat memory is copied into the vault, and from which projects (off by default) |

The full schema is in `config.template.json` at the repo root. Read it before
proposing changes you're unsure about, so the field names and shapes match.

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

## Checkpoint every answer

Changing settings is a short interview, and the answers are worth keeping:
a session that ends before the write leaves the user re-deciding the same
things next time. Open a capture before the first question:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" start --skill update-settings --goal "change settings"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" start --skill update-settings --goal "change settings"
```

`resumable: true` means an earlier run is still open. Read it with `show`,
and do not re-ask what it already holds.

Append after every answer, before asking the next thing:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" append --skill update-settings --question "<what you asked>" --answer "<what they said>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" append --skill update-settings --question "<what you asked>" --answer "<what they said>"
```

Close it once config.json has been written:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" close --skill update-settings --status complete
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" close --skill update-settings --status complete
```

---

## How to run the skill

1. **Figure out the intent.** If the user's request is vague ("update my
   config"), ask which section. If they named something specific ("add a new
   business called Acme"), skip straight to step 3.

2. **Read the current config.** `config.json` lives in the user's vault at
   `{vault}/van-gogh/config.json` — never the repo. Resolve its path:

   macOS / Linux (bash/zsh):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
   ```

   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
   ```
   Use the Read tool on that path. Show the user only the relevant slice in a
   readable form (a short bullet list, not raw JSON), so they can confirm
   what's there today.

3. **Gather missing fields conversationally.** For adds/changes, ask one
   focused question at a time for any required field they haven't given you.
   For a new business, that's: tag (short lowercase, e.g. `acme`), display
   name, project page path inside the vault (e.g.
   `wiki/projects/Acme.md`), meeting route folder (e.g. `20-Acme/Meetings`),
   and any keywords for classification. Offer sensible defaults derived from
   the display name when possible.

4. **Preview the edit.** Show the user the before/after for the field(s)
   you're about to change, in plain English. Example:
   > I'm going to add a new business: **Acme** (tag `acme`), project page
   > `wiki/projects/Acme.md`, meetings filed under `20-Acme/Meetings`,
   > keywords: acme, acme corp.
   >
   > Sound good?

5. **Wait for explicit yes.** Don't edit until the user confirms. If they
   want tweaks, loop back to step 3.

6. **Back up, then write.** Before editing, copy the vault `config.json` to
   `config.json.bak` beside it (in `van-gogh/`, overwriting any prior backup).
   Then use the Edit tool on the vault `config.json` with a minimal, targeted
   change — never rewrite the whole file. Preserve existing key order and
   indentation (2 spaces).

7. **Verify the JSON parses.** Run (substitute the resolved vault path):
   macOS/Linux `"$HOME/.config/van-gogh/venv/bin/python" -c "import json; json.load(open(r'{vault}/van-gogh/config.json'))"`,
   Windows (PowerShell) `& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import json; json.load(open(r'{vault}/van-gogh/config.json'))"`.
   If it fails, restore from the backup and tell the user what broke.

8. **Confirm in one line.** Tell the user what changed and that the backup is
   at `van-gogh/config.json.bak`. Don't dump the whole file.

## Field-by-field guidance

### About you (`user`)
- `full_name`: full display name (e.g. "Jane Doe").
- `first_name`: just the first name — used in briefing greetings.
- `name_variants`: every way the user's name might appear in emails/tasks
  (full name, first name, nicknames). Used to detect "your action items".
- `self_entities`: names the user goes by as an entity in the vault. Usually
  the same as full name plus any aliases.
- `role_description`: optional one-liner used in Haiku classifier prompts.
- `timezone`: the user's IANA timezone name (e.g. `America/Los_Angeles`,
  `Europe/London`, `Asia/Singapore`). Every meeting time and the "today /
  tomorrow" windows render against this zone. Changing it re-homes all briefings
  to the new zone immediately. Unset falls back to `America/Los_Angeles`. If the
  user gives a city and state/country instead of an IANA name, derive the IANA
  name from it yourself (e.g. "Austin, Texas" maps to `America/Chicago`) and
  confirm before writing. To validate a name, run
  macOS/Linux `"$HOME/.config/van-gogh/venv/bin/python" -c "from zoneinfo import ZoneInfo; ZoneInfo('America/Denver'); print('valid')"`,
  Windows (PowerShell) `& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "from zoneinfo import ZoneInfo; ZoneInfo('America/Denver'); print('valid')"`.
- `secondary_timezone`: optional IANA name for a side-by-side dual display
  (e.g. set `America/New_York` alongside a `America/Los_Angeles` primary to render
  "9:00 AM PDT / 12:00 PM EDT"). Leave `""` (the default) for a single-zone
  display.

### Accounts (`accounts`)
`accounts` is a **list** of account objects — add as many Google and Microsoft
accounts as the user has (use `/van-gogh:add-account` and `/van-gogh:remove-account` for the
guided flows). Each entry:
- `provider`: `"google"` or `"microsoft"`.
- `email`: the account's address.
- `label`: display label in briefings. It also identifies the account's OAuth
  refresh token (`GOOGLE_REFRESH_TOKEN_<LABEL>` / `MS_GRAPH_REFRESH_TOKEN_<LABEL>`),
  provisioned by `app/auth_bootstrap.py`. Labels must be unique.
- `is_primary`: exactly one account across the whole list is `true` (the
  designated primary). To change which account is primary, flip this flag —
  set the new primary `true` and the old one `false`.
- `sent_folder_id`: Microsoft Graph folder id for Sent Items (microsoft only;
  leave `""` for google or if not yet discovered).

A legacy flat-key config (`google_primary_email`, `microsoft_email`, …) is
auto-migrated to this list shape on read, but new edits should use the list.

### Obsidian vault (`obsidian`)
- `vault_path`: absolute path to the vault (use `~` for home).
- `hotcache_relpath`, `sources_relpath`, `weekly_relpath`: paths *inside* the
  vault — relative, no leading slash.
- `hotcache_action_items_heading`: exact text of the heading in
  `hotcache.md` where the user's tasks are appended (e.g. "My Action
  Items").

### Businesses (`businesses`)
Array. Each entry:
- `tag`: short lowercase identifier (used in `[TAG]` brackets in briefings).
- `display_name`: human-readable name.
- `project_page`: vault-relative path to the project's markdown page.
- `meeting_route`: vault-relative folder where meetings for this
  business get filed.
- `keywords`: list of substrings used to classify meetings/emails to this
  business. Case-insensitive.
- `priorities`: optional list of what the user is actually pushing on inside
  this bucket. Each entry is `{"name": "...", "detail": "", "added": "YYYY-MM-DD"}`.
  `name` is the user's own words, kept short enough to read as a heading in a
  briefing. `added` is today's date, so a priority set months ago is visibly
  stale. Order matters: it is the order they render in.

A business is a **bucket**: a venture, an advisory client, a company, a board
seat, or an area of life like "personal". There is no fixed set and no limit.
When a user says "I have six buckets" or "add my new client", add one array
entry per bucket, with the tag and keywords they give you.

To remove a business, confirm the tag and remove that single array entry.

**Priorities are the highest-value edit this skill makes.** When the user
states priorities in conversation ("in power market my top four are closing
X, closing Y, the Z pipeline, and lead gen"), do not merely acknowledge them.
Write them into `businesses[].priorities` for that bucket in the SAME turn,
confirm-gated like any other edit. Briefings grade every item against these:
an unstated priority means the system keeps surfacing whatever email happens
to be loudest. If the bucket does not exist yet, offer to create it first.

### Failure alerts (`support`)
### `finance` (the weekly finance brief)

Reads QuickBooks and mails where the money stands, Monday mornings.
`enabled` (ships false), `day`, `time`, and `recipient_email`, which falls back
to the digest recipient when empty and exists because the cash position is not
the same kind of thing as a calendar. `commentary` controls whether the brief
carries the controller's read under the figures.

`company_id` and `company_name` pin whose books these are. They are written at
install from the connector's own answer, and a run whose connector reports a
different company shows no numbers and says which company it found instead.
Only change them if the user deliberately moves the brief to another QuickBooks
company, and then re-run the Step 5b check to get the right id.

Turning `enabled` on or off means re-running `scheduler_setup.py install`, the
same as the scorecard.

### `kpi` (the weekly scorecard)

- `enabled`: ships `false`. Turning it on emails six measures once a week and
  installs a scheduled job; say plainly that it is a real email, not a page.
- `day` / `time`: when it sends, computer-local. Default friday at 16:00.
- `recipient_email`: empty means wherever the digest already goes. Its own
  setting exists because a scorecard is personal performance data and some
  people point their briefings at an assistant.
- `share_with_support`: sends the identical email to `support.team_email` too.
  Off by default. The email carries no names or subjects, only counts.
- `window_days`: how far back it looks, default 14, always rounded to an even
  number so the two halves match.

After ANY change here, re-run `scheduler_setup.py install` so the scheduled job
matches the config. Turning it off removes the job.

- `team_email`: a second address for failure alerts (whoever set the install up).
- `alerts_enabled`: master opt-in. Ships `false`.
- `alert_cooldown_days`: days before the same failure may alert again.

### Chat memory mirror (`memory_sync`)
- `enabled`: ships `false`. Say plainly what turning it on does: it copies
  memory files from named Claude Code projects into the vault.
- `claude_project_dirs`: the explicit project directories to mirror from.
  Empty means the two obvious ones (the vault and the plugin repo). It is
  never "all projects": a machine can hold other clients' projects, and those
  must never land in this vault.
- `target_relpath`: where mirrored memory lands. Default `wiki/memory`.

### Front page (`briefing`)
- `front_page_cap`: how many non-late items reach the front page (clamped 3-9,
  default 7).
- `functions[]`: the kind-of-work vocabulary items are filed under inside a
  business (Sales, Legal, ... — the user can rename or add their own).
- `function_keywords`: the user's own filing words, per function name —
  `{"Ranching": ["cattle brand", "herd"]}`. This is what "file X under Y"
  requests become: add the word here and the next briefing files it there.
  A custom name in `functions[]` needs keywords here (or the classifier) to
  ever receive items.
- `publish_page`: whether each briefing keeps one permanent private web page
  on claude.ai (default true).

### Meeting notetaker (`notetaker`)
- `provider`: which AI notetaker meeting notes come from — `granola`, `grain`,
  or the name of a notetaker extension the user installed in
  `~/.config/van-gogh/extensions/` (a file named `notetaker_<name>.py`).
  **One at a time.** Empty means "infer from whichever API key is set", which is
  the right setting for almost everyone; only pin it explicitly when both keys
  exist in `~/.config/van-gogh/.env`, since an ambiguous inference falls back to
  Granola.

  Switching providers is two moves, and the key is the one that matters — this
  skill edits config, it cannot write the `.env`:

  1. Write the new provider's key. Ask for it (Granola: Settings → Personal API
     Keys; Grain: Account settings → Integrations → Personal API, **Starter plan
     or above**), then:

     ```bash
     VG_ENV_VALUE="<pasted_key>" "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/user_state.py" set-env GRAIN_API_KEY
     ```
     ```powershell
     $env:VG_ENV_VALUE = "<pasted_key>"; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\user_state.py" set-env GRAIN_API_KEY
     ```

  2. Set `provider` here, then verify with `app/meeting_fetch.py --check` — it
     reports the active provider, whether its key round-trips, and every
     registered provider with its key status. Never claim the switch worked
     without running it.

  Already-filed source pages are unaffected: they record `meeting_id:` and
  `meeting_source:`, and the ingest scan reads both the current and the legacy
  frontmatter key, so nothing gets re-filed after a switch.

### Email filters (`email_filters`)
All four are arrays of strings:
- `internal_domains`: domains treated as "your company" (e.g. `acme.com`).
- `internal_team_emails`: specific addresses treated as internal.
- `allow_domains`: domains that bypass spam filtering.
- `extra_spam_fragments`: user-specific spam patterns to filter out (generic
  ones stay in code).

## Rules

- **Never** edit `config.json` without an explicit "yes" from the user on a
  concrete preview.
- **Always** back up to `config.json.bak` first.
- **Never** rewrite the whole file when one field changed — use a targeted
  Edit.
- **Always** validate JSON parses after writing.
- If the user asks to do something the schema doesn't support (e.g. "add a
  Slack account"), say so — don't invent new fields.
- Don't touch `config.template.json` unless the user explicitly asks to
  update the template too.
