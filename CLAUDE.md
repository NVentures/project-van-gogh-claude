# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Repo access restriction.** Only the GitHub users **`nobeljchang`** and
> **`wesleywchang`** may modify the `project-van-gogh` repository. Anyone else
> must not push, merge, or otherwise change repository code. (Documentation note
> only — this does not alter any GitHub settings.)

# Project Van Gogh

Your personal chief of staff stack. Skills + scripts + Obsidian integrations,
packaged as the **`van-gogh` Claude Code plugin** (this repo is both the plugin
and its own marketplace — see `.claude-plugin/`). Skills are invoked as
`/van-gogh:<name>` once the plugin is installed.

## How it fits together (data flow)

Every slash-command is a thin skill that shells out to a Python script and renders the result. The skill never touches APIs directly:

```
skills/<name>/SKILL.md           ← workflow + run command + render instructions
        │  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/<script>.py"
        ▼
app/<script>.py                  ← fetches data, emits JSON to stdout (+ a sidecar in logs/)
        │  JSON
        ▼
Claude renders the briefing → writes {vault}/van-gogh/<name>.md
```

So a script's contract is its **JSON output shape**, consumed by the SKILL.md prose. All four briefings emit the same five front-page keys (`front_page`, `front_page_md`, `fold_rows_md`, `folded_md`, `counts`) on both their success and failure branches: the front page is chosen by code in `week_review.front_page`, never by the model, and the replies on it are drafted through `app/draft_email.py` and keyed in a ledger so a rerun reuses yesterday's draft instead of writing a second one. The shared render contract lives in `skills/_shared/front-page.md`. When you change a script's output, update the matching SKILL.md render section, and vice versa. `morning_coffee.py` and `week_review.py` don't call APIs themselves, `morning_coffee.py` shells out to `week_review.py` (see `morning_coffee.py:72`); the scripts that actually fetch are `week_review.py`, `week_retro.py`, `afternoon_tea.py`, `meeting_prep.py`, `relationship_radar.py`, `voice_generator.py`, `calendar_stub_check.py`, `five_fifteen.py` (meeting notes via the shared `notetaker.py`; email/Slack/Teams/HubSpot via Tier 2 connectors), and `finance_brief.py` (QuickBooks via the unattended connector fetch below).

## Tier 1 vs Tier 2 (where the data comes from)

`app/data_sources.py` is the routing layer that decides how a script gets its email/calendar data. **Tier 1 (OAuth) is the primary path.** It is what `/van-gogh:install-van-gogh` sets up, and it is the only mode that can run unattended. Tier 2 exists solely as a fallback for machines where OAuth consent is blocked (locked-down Microsoft tenants, admin-approval walls); never present it to a user as an easier alternative worth choosing on purpose.

- **Tier 1 (direct OAuth), the default**: OAuth refresh tokens are present in `.env` (`GOOGLE_REFRESH_TOKEN_*`, `MS_GRAPH_REFRESH_TOKEN_*`). Scripts call `google_client.py` (Gmail/Calendar/Drive via `google-api-python-client`) and `microsoft_client.py` (Graph Mail/Calendar via MSAL) directly. `is_tier1()` returns true.
- **Tier 2 (connector), the fallback**: no OAuth tokens. The SKILL.md uses Claude's MCP connectors to fetch mail/calendar, writes the result to a temp JSON file, and passes the path to the script, which reads it via `load_input(path)`. If the input is missing, scripts call `missing_input_error(name)` and exit 1. A human has to be in a chat session for this, so it is not a path anything scheduled can use.
- **Unattended connector fetch**, a third mode, and only for sources that have no OAuth client at all: `app/connector_fetch.py` spawns a confined headless `claude -p` that inherits the user's claude.ai connectors. It is what lets the weekly finance brief read QuickBooks with nobody present. It is NOT a way to schedule Tier 2: mail and calendar keep their OAuth clients, which are faster, need no model in the path, and cannot be stopped by a token only a browser can renew. Every limit is a CLI flag rather than a prompt instruction (`--tools ""`, a full-name `--allowedTools` whitelist, `--permission-prompts none`, `--disable-slash-commands`, and an empty non-strict `--mcp-config` whose only job is to make the CLI wait for the remote server before the first turn). The caller owns the tool arguments and the verdict is read from the tool_result events, never from the model's prose. Its failure mode is its own class: a connector token expires, no retry can renew it, `failure_class` names that `connector`, and `job_watch` holds the job with a sentence naming the service. `skills/_shared/connectors.md` is the procedure for adding another one.

When adding a data-fetching code path, gate it on `is_tier1()` and provide the Tier 2 `load_input` fallback so both modes keep working. The exception is any feature that must run **unattended** (the digest scheduler, and the Workbench once it lands): those require Tier 1 and must fail loudly with a pointer to `/van-gogh:install-van-gogh`, never degrade into a half-working state. **Never make SKILL.md prose decide the tier**: refresh tokens live in the state `.env`, which only Python processes can read (via `user_state.load_env()`), so Claude cannot check for them; a skill must always attempt the script (e.g. `app/outlook_draft.py`) and branch on its machine-readable success/error output instead. The OAuth clients read app credentials from `~/.config/van-gogh/` (supplied by the user at install via `app/install_oauth_credentials.py`, falling back to a local, gitignored `config/oauth/`) and per-account refresh tokens from the state `.env` at `~/.config/van-gogh/.env` (`*_REFRESH_TOKEN_<LABEL>`), loaded via `user_state.load_env()`. Microsoft rotates refresh tokens; `microsoft_client.py` writes rotations back to the state `.env` so the stored token never goes stale.

## Notetakers (where meeting notes come from)

`app/notetaker.py` is to meeting notes what `data_sources.py` is to mail: the one
routing layer, with **exactly one provider active at a time**. Providers live
beside it as `notetaker_<name>.py` — today `notetaker_granola.py` and
`notetaker_grain.py`. Selection is `config.json notetaker.provider`; when that is
empty the layer infers from whichever API key is present in the state `.env`
(`GRANOLA_API_KEY`, `GRAIN_API_KEY`), which is what lets an existing Granola
install keep working with no config change and a fresh Grain install work the
moment its key is written. Ambiguous (both keys, or neither) falls back to
Granola. An *unregistered* name (a typo) also falls back to Granola rather
than to silence, and `notetaker.config_problem()` surfaces it into the
briefing's errors list — a briefing reporting zero meetings must never be
indistinguishable from a quiet week.

Unlike mail, this is independent of Tier 1 / Tier 2: it is always a direct
public-API call keyed on the provider's API key, so it runs the same in both
modes. With no key the read helpers return empty — a missing notetaker weakens a
briefing, it never fails one.

Two rules make adding a provider cheap. A provider module implements only five
things (`NAME`/`DISPLAY_NAME`/`ENV_KEY`/`KEY_HELP`, `api_key`, `iter_stubs`,
`fetch_note`, `latest_id`) and **fetches and maps, nothing else** — windowing,
commitment extraction, PII stripping, date resolution and output shaping all live
in `notetaker.py` and are shared. And every provider maps into the one
**canonical note** shape (documented at the top of `notetaker.py`), which is
Granola's shape kept deliberately, so nothing downstream had to change when the
layer landed. Where a service's shape genuinely differs, the difference is
absorbed in its provider: Grain returns action items as a separate
`ai_action_items` field, and `notetaker_grain._summary_markdown` folds them back
under an `## Action Items` heading because `meeting_ingest.ACTION_HEADINGS_RE` is
the contract the whole action-item pipeline reads.

Consumers never name a vendor: `morning_coffee.py`, `afternoon_tea.py` and
`five_fifteen.py` call `notetaker.fetch_meetings()`, `collectors.py` exposes
`meetings()` / `meeting_texts()`, and the two CLIs are `meeting_fetch.py` (raw
queries, `--check` diagnostics, `--source` override) and `meeting_ingest.py`
(end-to-end vault ingest, the `/van-gogh:meeting-ingest` skill). Source pages
record `meeting_id:` + `meeting_source:` in frontmatter; `MEETING_ID_RE` also
reads the legacy `granola_id:` forever, because a vault full of already-filed
meetings must never look uningested and get filed twice.


## Extending Van Gogh (user extensions)

Users modify Van Gogh **without touching this repo or the plugin cache** (a
marketplace update re-clones the cache, so nothing user-authored may live
there). Four surfaces, split by a hard rule: **executable code is
machine-local, data overrides are vault-synced.** Code must never arrive
through vault sync — a half-synced module or a "conflicted copy" would be
imported into an unattended digest run.

- **Personal skills** — `~/.claude/skills/<name>/`, scaffolded by
  `/van-gogh:create-skill` via `app/scaffold_skill.py` (which also **forks** a
  shipped skill into a personal variant, rewriting `${CLAUDE_PLUGIN_ROOT}`
  references to the `~/.config/van-gogh/plugin-root` pointer form — the
  supported way to change how a built-in skill behaves). What user code may
  import or shell out to is the supported surface in
  `skills/_shared/personal-skill-api.md`, guarded by
  `tests/test_personal_skill_surface.py` — extend that doc and test together
  when blessing a new name, and treat renaming anything on it as a breaking
  change.
- **Agents**: `agents/<name>.md`, shipped with the plugin and discovered by
  Claude Code automatically: the directory is the registration, there is no
  manifest list. Each is a domain persona the orchestrator delegates to on a
  `description` match, so that field is a routing rule rather than a job
  description; it names the situations that should reach the agent. Agents
  read the vault through the same supported surface as personal skills
  (`skills/_shared/personal-skill-api.md`) via the
  `~/.config/van-gogh/plugin-root` pointer, never a relative path, and the
  voice rules in DESIGN.md govern everything they say. Shipped:
  `bookkeeper-controller` (month-end close, reconciliations, controls, audit)
  and `van-gogh-mechanic` (the product's own machinery: a job that stopped
  running, a poller gone quiet, a config or state file that drifted). The
  mechanic is the one agent also reached without a person present, by
  `app/repair.py` when the watcher gives up on a job; its rules about what it
  may and may not change are enforced by the flags that spawn it, not only by
  its own text. `operator-research` is the third: it reads the public
  web for who the user is and what their company does, and it is spawned by
  `app/operator_research.py` during install rather than by a person. Its
  containment is the same shape and for the same reason: its working
  directory is a staging folder under `logs/`, no `--add-dir` is passed at
  all, and it has no Bash tool, so the vault it is eventually feeding is not
  reachable from the session that researches it.
- **Extension modules** — `~/.config/van-gogh/extensions/`
  (`user_state.extensions_dir()`), discovered by filename scan in
  `app/extensions.py`; dropping a file in a Van-Gogh-owned dir is the
  registration, there is no config list. Two kinds: `briefing_section_<name>.py`
  (adds a section to the briefings' `extra_sections` output key; contract at
  the top of `extensions.py`) and `notetaker_<name>.py` (an external provider
  merged into `notetaker.py`'s registry; built-ins win name collisions).
- **Data overrides** — `briefing.function_keywords` in config.json, and the
  judgment preamble at `{vault}/van-gogh/prompts/priority-judge.md`
  (`config_loader.judgment_preamble()`, capped, appended to
  `priority_judge.build_prompt`).

Every extension path **fails open**: a broken user module costs its own
section or provider and lands one line in the briefing's errors list
(mirroring `notetaker.config_problem()`), never a crashed run — the digest
scheduler executes these same paths unattended. Keep that property when
touching any of them.

## Design System

Always read `DESIGN.md` before making any visual or UI decision. All font choices,
colors, spacing, layout, motion, and interface voice are defined there. Do not deviate
without explicit user approval. In QA or review mode, flag any code that does not match
`DESIGN.md`.

Three rules are load-bearing and get broken by accident most often: chrome yellow
(`--lamp`) means *staged and awaiting your nod* and appears nowhere else; there is no
sans-serif anywhere in the product; and the interface writes in completed past tense,
with `SEND` and `KICK OFF` as the only present-tense imperatives.

## Cross-platform

This project ships to **macOS and Windows 10/11** (and Linux). All code, skills, and slash-command definitions must run on every target OS. When portability is genuinely impossible, provide a separate, working path for each OS rather than picking one. When writing or changing anything:

- **Paths:** never hardcode `/`-style paths or `~`. Use `pathlib.Path`, `Path.home()`, and `/`-operator joins; let Python normalize separators. No string concatenation of path fragments.
- **No platform-only shell commands** in scripts. Avoid `open`, `say`, `pbcopy`, bare `find`/`grep`/`sed` assumptions, and macOS/Linux-only flags. If a shell-out is unavoidable, branch on `sys.platform` and provide an equivalent for each OS, or use a Python stdlib API instead.
- **Locations:** resolve user dirs via `Path.home()` and config, not `/Users/...`, `/home/...`, or `C:\Users\...`. Binary lookups (`~/bin`, `claude`) must use `shutil.which` / `PATH`, not absolute paths.
- **Subprocess:** pass argument lists (not shell strings) and avoid shell built-ins; prefer `sys.executable` over a hardcoded `python`/`python3`. Every spawn must pass `creationflags=platform_compat.NO_WINDOW` — without it a console window flashes in the foreground on Windows (enforced by `tests/test_no_console_window.py`). Windows scheduled tasks must launch through the wscript `.vbs` wrapper (`scheduler_setup._vbs_content`), never a bare `cmd.exe` action, for the same reason.
- **Line endings & encoding:** always read/write text with `encoding="utf-8"`; don't assume `\n`-only or a system default codec.
- **Skills & commands:** every SKILL.md must work on both macOS and Windows 10/11. Any shell command shown to the user (install steps, run commands, git/auth setup) needs both a macOS/Linux (bash/zsh) form and a Windows (PowerShell) form when the syntax differs (show them side by side, never bash-only).
- When a cross-platform approach isn't obvious, flag the tradeoff rather than silently picking a macOS-only path.

## Structure

The repo holds **source only**. Nothing mutable lives in the plugin cache — a
marketplace update re-clones that directory, so a plain `/plugin` update must
always be safe. Per-user machine state lives in `~/.config/van-gogh/`; all
vault-shaped state lives in the vault.

```
.claude-plugin/   — plugin.json (plugin manifest) + marketplace.json (this repo is its own marketplace)
skills/           — skill definitions (one directory per slash-command)
agents/             subagent definitions, auto-discovered by Claude Code and
                    routed on their `description`; one .md per agent
app/              — Python scripts called by skills
config.template.json — config schema (tracked; the live config lives in the vault)
memory.template.md — seed for van-gogh/projects/Project Van Gogh/memory.md on a fresh install
config/oauth/     — local-only fallback OAuth app credentials (gitignored,
                    never committed). The user supplies their own during install
                    (Step 3, app/install_oauth_credentials.py), which lands in
                    ~/.config/van-gogh/ and takes precedence; this copy is only
                    read when nothing has been supplied.
tests/            — pytest suite (CI-safe; primes config_loader with a fixture)
```

Per-user machine state lives in `~/.config/van-gogh/` (`app/user_state.py`;
tests point `VAN_GOGH_STATE_DIR` at a temp dir):

```
~/.config/van-gogh/
  .env            — OAuth refresh tokens + API keys (written by auth_bootstrap.py)
  vault-pointer   — absolute path to the user's Obsidian vault
  plugin-root     — absolute path to the installed plugin cache, self-healed at
                    every script run (user_state.record_plugin_root); lets
                    user-authored personal skills (scaffolded into
                    ~/.claude/skills/ by /van-gogh:create-skill, outside any
                    repo) import the plugin's app/ modules
  venv/           — the stable venv every skill invokes (skills/_shared/python-runtime.md)
  scheduler/      — wscript .vbs launchers for the Windows scheduled tasks
                    (written by scheduler_setup.py; removed on uninstall)
  *_client_secret.json — optional per-user OAuth app credential overrides
```

Legacy installs kept `.env` / `.van-gogh-vault` at the plugin root; both
self-migrate (copy) into the state dir on first read — see `user_state.py`.

User state lives in a `van-gogh/` folder at the **root of the Obsidian vault**:

```
{vault}/van-gogh/
  config.json          — user-specific auth and account config
  week.md              — rendered weekly briefing
  morning-coffee.md    — rendered daily briefing
  afternoon-tea.md     — rendered end-of-day retro
  logs/                : script sidecars + scheduler logs, plus
                         runs.jsonl (one row per unattended run),
                         vault_audit_ledger.json + vault_audit_latest.json
                         (the audit's findings and its machine copy),
                         and captures/ (interviews checkpointed to disk)
  projects/            — workspace memory (Project Van Gogh/memory.md)
  .ingest_state.json   — /van-gogh:ingest-workspace sync state
```

The living Obsidian notes under `wiki/` (hotcache, sources, weekly, entities,
voice artifacts) are **not** part of `van-gogh/` — they are notes the user reads
and links to directly.

## Setup

`/van-gogh:install-van-gogh` handles setup end to end: it writes the
`~/.config/van-gogh/vault-pointer`, creates `{vault}/van-gogh/`, and writes
`config.json` there from `config.template.json`. The migration helper does the
file moves:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/migrate_to_vault.py" --vault "/path/to/your/Obsidian Vault"
```

It is idempotent — re-running on an already-migrated machine is a no-op. After
setup, edit the **vault** `config.json` (via `/van-gogh:update-settings`), never a
plugin-root copy.

Setup also starts `app/operator_research.py` as soon as it knows a name and a
company (install Step 4b), so the public-web research runs while the user is
still answering questions about notetakers and schedulers. Two rules make that
safe to do unattended. The research session writes only into its own staging
folder, so a run that goes wrong costs a folder nobody opens. And `apply` is a
separate command reached only from a skill, after the user has read a summary
and said yes: it writes the two entity pages, the source page, the managed
profile block in workspace memory, and the config enrichments (keywords, the
priorities they picked, a `role_description` only when it was empty), backing
config.json up first. A fact whose `source_url` is not in the proposal's own
sources list is demoted to inference in code, because a prompt asking for
citations is necessary and never sufficient. `/van-gogh:operator-research`
runs the same thing on demand, which is the path for installs that predate
the feature.

## Scripts

All `.py` scripts live in `app/`. Never create scripts elsewhere.

Skills run from the installed plugin cache, so every invocation anchors script
paths on `${CLAUDE_PLUGIN_ROOT}` (never on the user's cwd or `git rev-parse`)
and the interpreter on the stable per-user venv (`skills/_shared/python-runtime.md`):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/morning_coffee.py"
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/week_review.py"
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_ingest.py" --meeting <meeting_id>
```
(Windows/PowerShell form: `& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\<script>.py"`.)
When developing in a checkout of this repo, run from the repo root with `.venv/bin/python app/<script>.py` (the repo-root `.venv` is a dev convenience only; installed skills never use it).

All Python invocations use a managed venv interpreter directly (created with `python -m venv` and populated from `requirements.txt` via pip). Don't call a bare `python3` for scripts — it bypasses the managed environment; bare `python3` is fine only for stdlib one-liners like OS detection and for bootstrapping the venv itself.

`app/workbench_serve.py` is the one long-running component: the Workbench, a localhost server (default port 8765) that renders the briefings, the ticket ledger, the vault graph and the Inbox roll-up as a local web page. It is source-only like everything else; its read side is `workbench_data.py`, its ledger is `workbench_store.py`, its graph is `brain_graph.py`, and its UI is the static files in `app/workbench_static/`. Launch it with `/van-gogh:workbench`. It requires Tier 1 (OAuth) and serves a setup notice instead of a dashboard without it.

The briefing page it serves at `/briefing/<name>` carries a chat, and that chat is the one place in the product where a model gets real tools. It runs through `claude_cli.run_claude_readonly`, which is deliberately separate from `run_claude` (that one stays non-agentic; other callers depend on it). Every limit is a CLI flag, never a prompt instruction: `--restricted` removes the command-running tools, ignores the user's own settings files so a personal permission rule cannot widen this call, confines the file tools to the working directory, and refuses `bypassPermissions`; `--tools "Read,Grep,Glob"` is a whitelist, so Write and Edit do not exist for the call and it cannot alter the vault it reads; `--permission-prompts none` denies anything that would prompt, because nobody is at a terminal. The vault is the working directory and no `--add-dir` is passed, which is what stops it reaching the rest of the disk. Sending is impossible structurally rather than by instruction: nothing in that tool set can execute anything. Asking the chat for a deck stages a plan and stops, exactly as the dashboard does; the build runs in `_build_worker` on an explicit press, and `GET /download/<ticket>` serves the result token-gated, from the path the ledger recorded and only if it still resolves inside the deliverables folder.

A ticket carrying a file type (`pptx`, and later `xlsx`/`docx`) is produced by `app/deliverable.py`, which is the producer end of the ledger's lifecycle. It has two halves and they must stay separate: a **stager** (`stage_plan`) asks the model for a content plan and stops, and a **runner** (`build`) turns an approved plan into a native file, then `verify` reopens that file and checks it against the plan. The nod is what starts the build: `staging` writes no file, and only `approved` reaches a builder. Plans are validated before anything is built, so an empty slide, a placeholder, an oversized deck or a dash is refused with a reason rather than silently shipped. Decks are built natively with `python-pptx` (every string selectable and editable); rendering slides to images is permanently out. The workers that drive the lifecycle live in `workbench_serve.py` (`_stage_worker`, `_build_worker`) and any failure lands the ticket on `failed` carrying the reason, never wedged in `running`. Staging pulls `app/context_pack.py` for the counterparty first, so a deliverable argues from the vault's record; that lookup fails open, since losing the evidence should cost the deck its grounding, not the whole run.

`app/context_pack.py` is the evidence layer shared by drafts and deliverables: given a counterparty it returns their entity page, recent meetings, open commitments, hotcache deal context and a one-hop `brain_graph` neighbourhood, every entry carrying its source path. Two rules are load-bearing. An empty pack must never reach a model (`is_empty()` is the contract; the caller falls back to the thread alone and says so), because drafting on no evidence is how a confident wrong email gets written. And `open_commitments` holds only the user's own promises: the vault marks ownership with `dir=by_me|to_me|third_party`, and raising a counterparty's commitment as something the user owes is the expensive kind of wrong. Vault text carries machine state in HTML comments (`ai:`, `synth:`, `ae:`, `deal:`, `tasks:`); all of it is stripped generically at the single point text enters a pack, because stripping by name let `deal:` through to a live pack after `ai:` was handled.

Each briefing also owns exactly one **private web page** on claude.ai, rebuilt and republished to the same URL every run (`app/briefing_html.py`, the URL stored in `{vault}/van-gogh/logs/briefing_pages.json`). Publishing fails open: a run with no publishing surface renders the briefing in full, exits 0, and carries one line saying why the page did not update. `briefing.publish_page: false` turns it off.

**Outbound mail is drafts-first.** Skills and sessions prepare drafts for the user's review (`app/outlook_draft.py`) and never send on their own initiative. Actually sending happens only on an explicit, per-message user instruction, and only through `app/send_email.py` (any configured account, Google or Microsoft) — never by importing `digest_send`, hand-rolling API calls, or scratchpad scripts around the OAuth clients. `digest_send.py` imports its mail primitives from `send_email.py`; don't add a second send implementation.

## Config

All user-specific data lives in `{vault}/van-gogh/config.json` (gitignored, in
the vault). See `config.template.json` for the full schema. The shape is ten
nested blocks:

- `user` — `full_name`, `first_name`, `name_variants[]`, `self_entities[]`, optional `role_description` (also used to build the voice-prompt persona). Used by briefings, action-item matching, and Haiku classification prompts.
- `accounts` — Gmail (primary + optional secondary), Outlook (Microsoft). Each account has an email, a display `label` shown in briefings, and where applicable a `config_dir` / `sent_folder_id`.
- `obsidian` — `vault_path`, `*_relpath` paths inside the vault (`hotcache`, `sources`, `weekly`, `entities`, and the voice artifacts `voice_guide`, `tone_profile`, `voice_snapshot`, `voice_drafts`, `relationship_radar`), plus `hotcache_action_items_heading` and `hotcache_active_threads_heading` (the headings in `hotcache.md` that scripts read).
- `businesses[]`: one entry per venture/project/client/area of life (a **bucket**; there is no fixed set, users add their own). Each has `tag`, `display_name`, `project_page` (vault-relative), `meeting_route` (vault-relative folder for meeting ingestion), `keywords[]` (used to classify meetings/emails to a business), and `priorities[]` of `{name, detail, added}`, the user's stated priorities inside that bucket, which briefings render first and grade incoming signal against. Resolved via `business_priorities(tag)` and `business_tag_for_text(text)`; both share `_business_match` with `business_for_text` so name and tag can never disagree.
- `clients[]` — one entry per advisory client the user writes a weekly **5:15 report** for (`/van-gogh:five-fifteen`, `app/five_fifteen.py`). Each has `tag`, `display_name`, `recipient_name`, `recipient_email`, `from_account` (an account `label` from the `accounts` block — the mailbox the report is sent from), `sources[]` (which signal sources the client uses: any of `email`, `slack`, `teams`, `hubspot`, `meetings` — legacy configs may say `granola`, which means the same thing), `report_dir` (vault-relative archive of dated reports), `signoff`, `keywords[]` (classify meetings/Teams/email to the client), and an optional `contract` block (the engagement "true north" the 5:15 measures against: `counterparty`, `term`, `retainer`, `engagement`, priority-ordered `priorities[]` of `{name, detail}`, `value_levers[]`, `out_of_scope[]`). Resolved via `clients()`, `client_by_tag()`, `client_report_dir()`, `account_by_label()`; surfaced to skills in `resolved_meta()["clients"]` (with `contract` defaulting to `{}` when absent).
- `email_filters` — `internal_domains[]`, `internal_team_emails[]`, `internal_team_names[]` (name-based, for relationship radar), `allow_domains[]`, `extra_spam_fragments[]`, `deal_critical_domains[]`. Generic spam patterns stay in code; only user-specific additions go here.
- `digest` — the emailed-briefing opt-in: `enabled` (default false), `sender_label` (a configured account label; empty = primary), `recipient_email` (empty = primary's address), and `briefings.{morning-coffee,afternoon-tea,week,week-retro}` each with `enabled` / `days[]` / `time` (24h, computer-local). Managed by `/van-gogh:update-digest-preferences`; `scheduler_setup.py sync-digests` turns it into per-briefing launchd/Task Scheduler jobs that run `app/digest_send.py` (render via `claude -p`, then send via the sender account's OAuth client).
- `notes`: the watcher between briefings (`app/note_send.py`, `app/note_watch.py`, `/van-gogh:note`). `enabled` (default false), `daily_cap` (Notes per day, clamped 1 to 5, default 2; the rest fold into Afternoon Tea), `from` / `until` (local `HH:MM` active window, default 07:30 to 18:00). Sender and recipient come from the `digest` block. Resolved via `notes_enabled()`, `notes_daily_cap()`, `notes_window()`.
- `relationship_radar` — `yellow_days` / `red_days` thresholds, `yellow_draft_cap`, `skip_tags[]` (entity frontmatter tags to ignore), and `skip_entity_names[]` (org pages to ignore).
- `follow_ups` — the date-triggered tickler (`/van-gogh:follow-up-radar`, `app/follow_up_radar.py`). `horizon_days` (how far ahead a trigger date still counts as `due_soon`, default 4) and optional `memory_path` (override for the "## Pending follow-ups" ledger; empty = the vault workspace memory at `{vault}/van-gogh/projects/Project Van Gogh/memory.md`). Resolved via `follow_ups_horizon_days()` / `follow_ups_memory_path()`.
- `memory_sync`: the chat-memory mirror (`app/memory_sync.py`). `enabled` (ships false), `claude_project_dirs[]` (explicit Claude Code project dirs to mirror from; empty means the vault and the repo only, NEVER a blanket scan of `~/.claude/projects/*`, where other clients' projects live), and `target_relpath` (default `wiki/memory`). Resolved via `memory_sync_enabled()`, `memory_source_dirs()`, `memory_target_dir()`.
- `briefing`: the front page: `front_page_cap` (how many non-late items reach it, clamped 3 to 9, default 7), `functions[]` (the kind-of-work vocabulary items are filed under inside a business; default Sales, Finance, Accounting, Legal, People, Operations, Product, Admin, Other), `function_keywords` (user keyword lists per function name, `{"Name": ["kw", ...]}` — joins the built-in keyword table's longest-match contest, user words winning ties, which is what makes a custom `functions[]` name routable by keyword) and `publish_page` (whether each briefing keeps one permanent private web page on claude.ai, default true; set false to turn it off). Resolved via `briefing_front_page_cap()`, `briefing_functions()`, `briefing_function_keywords()`, `briefing_publish_page()`.
- `notetaker` — `provider`: which AI meeting notetaker is active (`granola`, `grain`; empty = infer from whichever API key is set). One at a time. Resolved via `notetaker_provider()`; see the Notetakers section above for the provider contract.
- `podcasts` — `feeds` ({show-key: RSS URL}) for `app/podcast_transcribe.py`, which downloads and locally Whisper-transcribes episodes into `{vault}/van-gogh/podcast-inbox/`. `faster-whisper` is an optional dependency, installed into the venv on demand (deliberately not in requirements.txt). Resolved via `podcast_feeds()`.

Scripts read config via `app/config_loader.py` (`cfg()`, `vault()`, `van_gogh_root()`, `logs_dir()`, `workspace_week_md_path()`, `morning_coffee_md_path()`, `afternoon_tea_md_path()`, `projects_dir()`, `ingest_state_path()`, `entities_dir()`, `user_first_name()`, `businesses()`, `project_pages()`, `voice_guide_path()`, `radar_yellow_days()`, etc.) — never by reading config.json directly. Generic tuning constants (lookback windows, the Haiku model id) stay as code constants; only personal data lives in config.

**Config discovery (bootstrap):** config.json stores `vault_path` but lives *inside* the vault, so the loader can't read the vault path from a config it hasn't found. The `~/.config/van-gogh/vault-pointer` file (`user_state.pointer_file()`, which self-migrates a legacy plugin-root `.van-gogh-vault`) records the absolute vault path. The loader reads the pointer → `{vault}/van-gogh` → `{vault}/van-gogh/config.json`. The pointer is read only inside the lazy `cfg()` loader; all other path helpers derive from `vault()` (i.e. from `cfg()`), which keeps the whole path surface testable via a primed fixture with no pointer present.

Anthropic: always unset `ANTHROPIC_API_KEY` in scripts to force subscription routing via the `claude` CLI. Never import the `anthropic` SDK.

## Tests

```bash
.venv/bin/python -m pytest                          # full suite
.venv/bin/python -m pytest tests/test_week_review.py            # one file
.venv/bin/python -m pytest tests/test_week_review.py::test_name  # one test
```

The suite (`tests/`) primes `config_loader` with a fixture and points `VAN_GOGH_STATE_DIR` at a temp dir, so it runs green with NO `config.json` and NO vault pointer present, and never touches the real `~/.config/van-gogh/` (CI-safe). `test_no_cli_references.py` guards against re-introducing the retired Google/Microsoft CLI tools by name (everything now goes through the OAuth clients), and `test_script_imports.py` smoke-tests that every script imports cleanly under the fixture. It covers the config loader, the `van-gogh/` path resolution and the vault migration helper, business routing, name regex, the `meta` block shape, cross-account dedup, deal-stage advance, the week.md HTML renderer, and entity parsing. Run it before committing any change to `app/` or the config schema.

**The developer machine is the least trustworthy place to prove a test is
hermetic**, because it is the one machine where every dependency is already
installed. Two environment assumptions have each cost a red CI run on a green
local suite, and both are invisible here:

- **A stub intercepts a call, not the expression that builds its arguments.**
  `test_repair.py` replaced `subprocess.run` and said so in its docstring, but
  the argv was assembled by calling `claude_bin()`, which resolves the CLI from
  PATH *before* the stubbed call is reached. On a runner with no Claude Code
  installed, ten assertions failed in ways that read as product defects. Stub
  the resolver too, and check the CI condition with
  `PATH=/usr/bin:/bin .venv/bin/python -m pytest`.
- **Windows is a real target, and the suite runs there.** POSIX mode bits are
  not expressible (`os.chmod` only toggles read-only; access is by ACL), so a
  `0o600` assertion must be skipped on `win32` with the reason stated. And
  `str(Path)` renders the OS separator, so a path compared against a
  forward-slash literal needs `.as_posix()`.

When CI fails right after your change, list the previous runs before reading
any code: identical failures on an earlier commit mean the breakage was
inherited, not caused, and that changes what you are looking for.

## Install & updates (plugin)

Distribution is the plugin marketplace built into this (public) repo. Users run
`/plugin marketplace add NVentures/project-van-gogh-claude`,
`/plugin install van-gogh@van-gogh`, then `/van-gogh:install-van-gogh` for
first-time setup (OAuth, vault pointer, config — plugins have no post-install
hooks, so the install skill is that step). Updates have exactly one entry
point: `claude plugin marketplace update van-gogh` — with auto-update enabled
for the `van-gogh` marketplace (the install skill has the user turn it on in
`/plugin` → Marketplaces) it refreshes the catalog *and* updates the installed
plugin, and the same update also runs automatically at session start. There is
no update *applier* skill — updating is the marketplace's job, and nothing
here ever pulls inside the plugin cache. What Van Gogh does own is the
**signal**: `app/plugin_update.py` compares the installed `plugin.json`
version against the one published on `main` and says when they differ.
`check_quietly()` runs at `config_loader` import (same managed-venv gate as
the hooks above), refreshes at most once a day, and fails open; every other
run is a file read. The result reaches skills as `meta.update_notice` — ""
unless an update is pending — and `/van-gogh:check-updates` is the manual
trigger, which also offers to run `claude plugin marketplace update van-gogh`
for the user. The check is one unauthenticated HTTPS GET of the public repo's
manifest, deliberately needing no `git`, no `gh` and no `claude` binary, which
is what makes it behave the same in the Claude Desktop app as in a terminal.
`VAN_GOGH_DISABLE_UPDATE_CHECK=1` opts out. After the plugin cache refreshes,
the first script run self-syncs the venv — `user_state.sync_runtime_deps()` (called at
`config_loader` import) hash-compares the plugin's `requirements.txt` against
`~/.config/van-gogh/requirements.sha256` and pip-installs on change. The same
import-time choke point also refreshes the managed context block in
`{vault}/CLAUDE.md` (`user_state.sync_vault_claude_block()` — the vault file's
marker-delimited top section is owned by the `vault/CLAUDE.md` template; user
content outside the markers is never touched). The same choke point also clears scheduled jobs orphaned by a skill rename
(`user_state.sync_legacy_scheduler_jobs()`, stamped by
`scheduler_setup.LEGACY_SKILLS`): a rename reaches existing installs through a
marketplace auto-update, which never re-runs the install skill, so the
cleanup has to live somewhere that actually runs after an update. All
per-user state lives in `~/.config/van-gogh/`, so the refresh can never lose
it. Never add git-pull hooks or pull inside the plugin cache.

Whether those scheduled jobs actually ran is `app/job_watch.py`'s question.
It is installed by `scheduler_setup` like everything else, runs every 30
minutes plus at login, and compares what was due against `runs.jsonl`, the
failure ledger and the rendered file's own date. **Deliverables beat exit
codes in both directions**: an `rc 0` that left yesterday's file did not
deliver, and a fresh file with no run row did. What it does about a failure is
decided by its class (`app/failure_class.py`, the one place any of those
patterns live, shared with `priority_judge`), never by a counter: a usage cap
is held until the reset time named in the message, a stale CLI until the
installed version clears the bar, a classifier outage for one tick, and an
expired login is never retried at all. Re-runs go back through the OS
scheduler so a job runs exactly as it does at its normal hour, capped at
`support.job_watch_kick_cap` per slot. Every path fails open, and it never
announces itself: its own liveness reaches the user as `meta.job_watch`, one
footer line the briefings render (`skills/_shared/front-page.md` step 9),
which is also what says so when the watcher itself has gone quiet. That is
why there is no second process watching this one. **Any new unattended entry
point must record a run through `run_ledger` and appear in `job_watch.roster()`**,
which derives itself from `scheduler_setup.SKILLS` and the enabled digest
briefings rather than a hand-kept list, so nothing can be scheduled and
ungraded.

The roster grades two shapes of job and the difference is load-bearing. A
**clock job** is graded by its slot: it was due at a time, did it run, did it
deliver. A **poller** carries `interval_min` instead of an hour and is graded
by `_poller_row` on liveness alone, because on a day with no calls the prep
poller correctly does nothing and an absent artifact is the healthy case. Its
slot key is the calendar day rather than a moment, since every key derived
from the ledger moves when a row lands, which orphans the kick count and
resets the cap to zero forever.

When a re-run has been proven not to help, `app/repair.py` escalates to the
`van-gogh-mechanic` agent: a headless `claude -p` session that reads the
ledgers, diagnoses, repairs what it is allowed to repair, and files a note in
`{vault}/van-gogh/logs/repairs/`. Three boundaries are structural rather than
prompted, because a prompt instruction is necessary and never sufficient.
`--agent` makes the agent file the session's own definition, so its rules
arrive as the system prompt. The working directory is the **vault** and the
only `--add-dir` is the state directory, so **the plugin's own source is not
writable** (which matters twice: a fix there would be erased by the next
update anyway). And the attempt is stamped *before* the spawn, so a session
that hangs or dies still spends its budget rather than being retried at every
tick. It is capped at one look per job and `repair.MAX_PER_DAY` looks per day,
skips every class that releases itself (`auth`, `quota`, `version`), and its
switch fails toward **on**: reading the switch needs the config, which needs
the vault pointer, and a machine missing that pointer is exactly the fault
worth being called about.

`app/task_stall.py` answers the other half of "is anything wrong": the
machinery ran fine and a commitment has been sitting in the vault for three
weeks anyway. It reads the follow-up ledger through `follow_up_radar`'s own
parser (two definitions of overdue that can disagree is how a system starts
nudging about finished work) and the Workbench ledger for tickets waiting on
the user. Nothing re-runs any of it, so it is never actioned, only reported as
`meta.task_stall`, one line that names the items and says whose move it is.

Whether any of it is worth having is `app/kpi_report.py`'s question. Six
measures over a fortnight, rendered by `app/kpi_html.py` and mailed weekly by
`app/kpi_send.py` when `kpi.enabled` is on. They are computed from
`{vault}/van-gogh/logs/kpi_events.jsonl`, which the four briefings, the
check-off path and `meeting_prep` append to through `app/kpi_events.py`. Four
rules hold it together. **Content never reaches that file**: identity is a
hash from the one `item_key`, and `snapshot_from_output` is the only bridge
from briefing data to a log line, which is what makes the promise in the
email's footer a property of the code. **Direction is data, not a sign**:
`POLARITY` says which way is good per measure, because down is good for three
of the six and up for the other three. **Disappearance is not closure**: only
an explicit close event counts, so a tidy-up can never read as a productive
fortnight. And **below its evidence bar a measure says so**, never a zero.
No model is called anywhere in that path, so the weekly job cannot inherit the
CLI's version, quota or classifier failures; a test enforces it. Any new
briefing or check-off path records its own line, or the measure it feeds
quietly stops counting.

The **`claude` CLI itself** is kept current by `app/claude_update.py`, because a
CLI a few versions old is rejected by the API for current models ("Claude Code
X does not support this model; version Y or newer is required") and that breaks
every briefing on the machine at once. Two layers, both fail open:
`ensure_current()` runs `claude update` at most once a day (stamped in
`~/.config/van-gogh/claude-cli-update.json`) at the unattended entry points
(`digest_send.py`, `skill_run.py`), and `heal_if_stale()` recognizes that error
in a child's output and forces the update so the caller's retry lands on a
current CLI (`claude_cli.run_claude` retries itself). Any new code path that
spawns the CLI unattended must call one of the two. `VAN_GOGH_DISABLE_CLI_UPDATE=1`
opts a centrally managed install out. There are now four sanctioned raw
`claude -p` callers: `claude_cli` (non-agentic classification), `digest_send`
and `scheduler_setup` (agentic slash-command renders), and `connector_fetch`
(the confined connector read, which must NOT carry `claude_cli`'s flags because
`--strict-mcp-config` would disable the very connector it exists to read).
`tests/test_claude_cli_invocation.py` holds that exempt list and asserts the
stricter flags the fetch carries instead.

## Versioning (ZeroVer)

This project follows **ZeroVer** (https://0ver.org/) — zero-based versioning. The
major version stays `0` permanently; the project never reaches `1.0.0`.

- **Single source of truth:** the `version` field in `pyproject.toml`. The whole
  repo — scripts and skills together — shares this one version; there are no
  per-skill or per-file version strings. `.claude-plugin/plugin.json` mirrors it
  (kept in lockstep, guarded by `tests/test_plugin_manifest.py`) so plugin
  installs report the same version.
- **Format:** `0.MINOR.PATCH`. The leading `0` never changes.
  - Bump **MINOR** for shippable features, new skills, or schema changes
    (e.g. `0.1.0` → `0.2.0`).
  - Bump **PATCH** for shippable bug fixes or doc/compat corrections
    (e.g. `0.2.0` → `0.2.1`).
- **When:** bump on every shippable change, in the same commit as the change.
  Reset `PATCH` to `0` on a MINOR bump.
- **Release notes:** every version bump adds an entry to `CHANGELOG.md` in the
  same commit, written for a **non-technical reader**: what got better, in
  plain words, no file or function names, bullets starting `New:` /
  `Improved:` / `Fixed:`. The session making the change writes the entry (it
  has the context; commit messages are too technical to reuse). The entry IS
  the release notes: `plugin_update.whats_new()` reads the plugin's local
  `CHANGELOG.md` after an update and surfaces the unseen entries as
  `meta.whats_new` (rendered per `skills/_shared/front-page.md` step 8 —
  terminal chat only, exactly once: the briefing marks them shown via
  `plugin_update.mark_notes_shown()`, and unattended runs get "" via
  `VAN_GOGH_UNATTENDED`, so digests and published pages never carry them), and
  `/van-gogh:check-updates --force` fetches the published changelog to show
  what a pending update would bring. Each detected update also appends one line
  to the vault's durable log ({vault}/van-gogh/logs/update-log.jsonl: ts, from,
  to, notes_md), which is what `/van-gogh:release-notes` re-shows after the
  briefing footer goes quiet. `tests/test_plugin_manifest.py` guards that the
  current version has an entry.
- Keep it ZeroVer: do not introduce a `1.x` version or any other scheme.

## Think Before Acting

- State assumptions explicitly; if uncertain or multiple interpretations exist, ask rather than pick silently. If a simpler approach exists, say so.
- **Bias toward action:** for clear or small requests, make the reasonable assumption, do it, and confirm after — don't list options when asked for an action. When fixing a reported bug, implement the fix immediately; only stop if the root cause is ambiguous or the fix is irreversible.
- Before declaring a bug fixed, grep for the same pattern across all call sites — not just the reported location. Before a feature, check whether it already exists. Before adding from a list, add ALL items, not a subset.

## Simplicity First

- Minimum work that solves the problem: no speculative features, single-use abstractions, unrequested flexibility, or error handling for impossible cases. If 200 lines could be 50, rewrite it.
- Simplicity governs *scope* (what you build), never shallowness (how well you finish). Pick the narrowest scope, then finish it completely — see **Completeness**.

## Completeness

- Finish the job to the bone: no dangling thread when tying it off takes five more minutes, no "table it for later" when the permanent solve is in reach.
- The real fix, never the workaround — offer a workaround only when the root cause is genuinely out of reach, and say why. Tests and docs are part of "done"; a change with no way to verify it, or that leaves docs/task files stale, isn't finished.
- The bar is not "good enough" — it's "how is this already done, and done this well?" This is depth, not scope: it never licenses features nobody asked for.

## Surgical Changes

- Touch only what the request requires; every changed line should trace to it. Match existing style even if you'd do it differently.
- Don't "improve" adjacent code or formatting. Remove only imports/vars YOUR changes made unused; flag pre-existing dead code, don't delete it.

## Self-Healing

When something breaks, you get a fact wrong, or the user corrects you, fix the *system* so it can't recur — not just the symptom in chat.

1. **Root cause, not symptom.** Repair the actual script, config, or skill; never paper over a failure or silence a check.
2. **Verify.** For bugs, write a repro first, then make it pass.
3. **Record the lesson where it'll be seen again:** a durable fact, correction, or reusable footgun (with **Why** / **Do instead**) → project memory at `{vault}/van-gogh/projects/Project Van Gogh/memory.md`; a bug in a script/skill → fix that file; a rule that should always apply → add it here.

Bias toward action: when the right home is obvious and the change is additive, make it the same turn rather than asking.
