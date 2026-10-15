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

## The Four C's (the frame for all work here)

Everything in this product is one of four things: **Context** (the vault, the
only memory), **Connectors** (the systems it reads), **Capabilities** (the
skills, the agents and the advisor above them) and **Cadence** (when each
capability fires, one row per schedule in `app/job_registry.py`).
`FOUR-CS.md` maps every skill and every scheduled job onto them, and
`tests/test_four_cs.py` fails when a skill or a job exists that the page does
not name.

Hold new work to the rule on that page: a capability is finished when it
reads the context, reaches what it needs through a connector, writes what it
produced back to the context, and has a cadence. Building one without a
schedule is a decision to say out loud, not a default.

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
- **Connector lookup**, the general form of the same thing, for a question a
  person reads the answer to: `app/connector_lookup.py` asks any connector the
  user has saved (`config.json connectors[]`, written by
  `/van-gogh:add-connector`) what it holds about a person, with no code per
  service. The model chooses the arguments, since every CRM spells a contact
  search its own way, and code keeps the rest: the session can call only the
  saved tools, a tool is usable only when its name has a reading word and no
  changing one (`reads_only`, an allowlist, because the denylist it replaced
  passed `rebuild_corpus` on the first real connector), at most four calls
  count, and a result that does not name the person is dropped after the
  tool's restatement of its own query is removed (a search for an invented
  person came back "found" on that echo alone). A finding is therefore
  context shown as a quotation, never a fact to write, address or act on. If
  code needs to act on a connector's answer, it needs an exact read instead.
  `meeting_prep.live_context` is the first caller, beside
  `app/attendee_mail.py`, which reads the recent threads with each attendee
  from the OAuth mailboxes and reports who wrote last. Both are gathered once
  per run, smallest meeting first, cached or capped, and both fail open.

When adding a data-fetching code path, gate it on `is_tier1()` and provide the Tier 2 `load_input` fallback so both modes keep working. The exception is any feature that must run **unattended** (the digest scheduler, and the Workbench once it lands): those require Tier 1 and must fail loudly with a pointer to `/van-gogh:install-van-gogh`, never degrade into a half-working state. **Never make SKILL.md prose decide the tier**: refresh tokens live in the state `.env`, which only Python processes can read (via `user_state.load_env()`), so Claude cannot check for them; a skill must always attempt the script (e.g. `app/outlook_draft.py`) and branch on its machine-readable success/error output instead. The OAuth clients read app credentials from `~/.config/van-gogh/` (supplied by the user at install via `app/install_oauth_credentials.py`, falling back to a local, gitignored `config/oauth/`) and per-account refresh tokens from the state `.env` at `~/.config/van-gogh/.env` (`*_REFRESH_TOKEN_<LABEL>`), loaded via `user_state.load_env()`. Microsoft rotates refresh tokens; `microsoft_client.py` writes rotations back to the state `.env` so the stored token never goes stale.

**Scopes are named at consent and nowhere else** (`app/oauth_scopes.py`, the one place they are written down). A refresh token carries the permissions its owner approved on the day they signed in, and both providers reject a refresh naming a permission the token lacks, rejecting the whole refresh rather than the new part. So a Google refresh passes `scopes=None`, and a Microsoft refresh passes the scopes that account was granted (stored beside its token at sign-in, else `MS_BASELINE_SCOPES`), never the live consent list. This is not a style preference: a release once added two scopes for one optional feature and disconnected Gmail and Calendar for every account connected before it. Adding a scope to a consent list is therefore safe, and the feature wanting it owns the other half, turning the resulting 403 into a plain "sign in again" line for that one account (`contact_capture.SCOPE_HELP`, `send_email._is_missing_send_scope`). `tests/test_oauth_scope_rule.py` and `tests/test_google_refresh_scopes.py` enforce all of it, the latter by reading the AST so a new construction site cannot opt out.

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
  voice rules in DESIGN.md govern everything they say. `vincent` is the
  chief of staff and the one agent a user talks to: he knows every skill,
  agent and scheduled job, and hands work to them (see "Vincent" below).
  `advisor` is the layer above the briefings (what is worth doing next,
  across the whole vault); its judgment rules live in its agent file, its
  unattended run is `app/advisor.py`, described below, and Vincent hands
  that question to it. Also shipped:
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
  `priority_judge.build_prompt`). It shades both things the judge decides:
  whether an item serves a stated priority, and its slip cost (what it costs
  to leave alone). Slip cost is asked of every open item on every Week and
  Morning Coffee run, priorities or not, and it reorders items INSIDE a
  front-page tier only (`week_review.front_page`); the tier order itself,
  what is on the page and what is hidden stay in code. The same read
  returns a deadline when the message states one, kept only with the words
  that state it (`priority_judge._read_due`); that is the one verdict that
  moves an item between tiers, into dated or late, and it is refused on an
  item judged to cost nothing or to be dead. Afternoon Tea and Week Retro
  build their pages from items the judge does not read, so their order is
  unchanged.

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

## Hosts (Claude Code and Codex)

Van Gogh runs under two clients, Claude Code and OpenAI Codex, and
**`app/host.py` is the only file that spells either**. Everything that
differs between them lives there: which binary, every headless command line
(`skill_cmd`, `completion_cmd`, `readonly_cmd`, `agent_cmd`), how a skill is
named in a prompt (`host.cmd("week")` is `/van-gogh:week` or
`$van-gogh:week`), where personal skills live, which instructions file a
vault carries (`CLAUDE.md` or `AGENTS.md`), how a reply is read back
(`host.finish`), and the managed block Codex needs in its own `config.toml`
(`host.sync_codex_config`, which gives every command the model runs
`CLAUDE_PLUGIN_ROOT`). An install records its client in
`~/.config/van-gogh/host`; `VAN_GOGH_HOST` overrides it for a session and
for every scheduled job; with neither, a Codex session is recognised by the
`CODEX_THREAD_ID` it puts in a command's environment, and everything else
is Claude. Codex has no `--agent`, no AskUserQuestion, no Skill and no Agent
tool: an agent's file travels at the top of the prompt (`host.agent_prompt`),
and the table at the end of `skills/_shared/python-runtime.md` says how
every Claude tool a skill names reads under Codex.

The definition of done for any change touching this: a spawn goes through
`host.py` (never a `["claude", ...]` or `["codex", ...]` list, never a bare
`-p`), an emitted command name goes through `host.cmd()`, a host-dependent
function has a row in `host._DISPATCH` with both branches and a test that
uses the `host` fixture from `tests/conftest.py` (which runs it once per
client), and a new tool name in a skill has a line in that table. Five
guards hold it: `tests/test_host_branches.py` (every row has every host),
`tests/test_no_host_literals.py` (no client spelled outside the seam; three
Claude-only modules must branch on `host.is_claude()`),
`tests/test_tool_name_map.py`, `tests/test_codex_surface.py` (every flag
`host.py` uses exists in the installed Codex binary; `VAN_GOGH_REQUIRE_CODEX=1`
turns its skip into a failure in the CI lane that installs Codex), and
`tests/test_scheduler_golden_codex.py` (the Codex jobs, beside the Claude
corpus, whose hash it pins). The Claude-only features by decision are the
claude.ai connector reads (`connector_fetch`, `connector_lookup`) and the
Claude Code memory mirror (`memory_sync`); under Codex each answers with one
sentence, never a spawn.

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
                    (job_registry.py is the one table of scheduled jobs)
config.template.json — config schema (tracked; the live config lives in the vault)
memory.template.md — seed for van-gogh/projects/Project Van Gogh/memory.md on a fresh install
config/oauth/     — local-only fallback OAuth app credentials (gitignored,
                    never committed). The user supplies their own during install
                    (from the key file at the credentials step, app/install_oauth_credentials.py), which lands in
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

`/van-gogh:install-van-gogh` handles setup end to end, and it is a loop over
one deterministic script: `app/setup_flow.py next` runs machine steps until a
human or a join is needed and prints one JSON object (`prompt`, `wait`,
`dispatch`, `error` or `done`), the skill does what `kind` says and calls it
again. The checklist at `~/.config/van-gogh/setup/checklist.json`
(`app/setup_state.py`) is the only state; every box is ticked from disk, so a
crash or a new session resumes at the first open box and nothing is asked
twice. `setup.sh` / `setup.ps1` at the plugin root run `setup_flow.py prepare`
(plugin, Python, the key file's credentials) before Claude opens. The Claude
they open is Vincent (`--agent van-gogh:vincent`), who drives the same loop:
the scripts write `vincent.py --setup-brief` (the situation and the setup
rules, every path spelled out) to a file and hand it over with
`--append-system-prompt-file`, so his first turn fetches nothing; if that file
cannot be written, the install skill opens as before. Three things about that
launch were found by probing the real CLI with the developer's own settings
left out (`--setting-sources project`), and each one passed every test here
while being wrong on a client's computer. A message placed straight after
`--allowedTools` is read as one more tool name, so it goes after `--`. A
command with a here-document, or with `$(...)` in it, is asked about even
when its program is allowed, so an answer travels in a file
(`prompt.answer_file`, written with the Write tool into the setup folder the
launch opens with `--add-dir`, read once by `next --answer-file` and deleted)
and commands name the plugin folder outright. And a developer whose settings
allow every shell command sees none of it: only a run without those settings
is evidence about a client. Five
questions on the happy path (notetaker, confirm, schedule, priorities,
deals). The schedule question is `app/schedule_plan.py`: `prompt.show` is one
row per `job_registry` row, read from the config a yes WOULD write
(`setup_defaults`, the twelve that start on), and "Change something" sends
`{"changes": [...]}` that a model wrote from the person's words and code
checks one at a time (`apply_changes`: the job has to exist, a time has to be
a time, a fixed row has no setting), re-asking with the table marked until
they keep one or choose nothing. An install that already has a schedule is
shown its own, never the defaults over it. The finish writes `setup/complete.json`, deletes the checklist and
removes the setup reminder job. The stamp is a hint: `verify-stamp` re-checks
disk before any command says "already installed", `adopt` builds a checklist
for an install that predates the flow (once per plugin version, at the
`config_loader` choke point and after a plugin update), and
`uninstall-van-gogh` runs `reset`. The vault step writes the
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
company (the orchestrator starts it as soon as the vault settings exist), so the public-web research runs while the user is
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

The briefing page it serves at `/briefing/<name>` carries a chat, and that chat is Vincent (`vincent.answer`, described below) with the page as its channel. It is the one place in the product where a model gets real tools with nobody at a terminal. It runs through `claude_cli.run_claude_readonly`, which is deliberately separate from `run_claude` (that one stays non-agentic; other callers depend on it). Every limit is a CLI flag, never a prompt instruction: `--restricted` removes the command-running tools, ignores the user's own settings files so a personal permission rule cannot widen this call, confines the file tools to the working directory, and refuses `bypassPermissions`; `--tools "Read,Grep,Glob"` is a whitelist, so Write and Edit do not exist for the call and it cannot alter the vault it reads; `--permission-prompts none` denies anything that would prompt, because nobody is at a terminal. The vault is the working directory and no `--add-dir` is passed, which is what stops it reaching the rest of the disk. Sending is impossible structurally rather than by instruction: nothing in that tool set can execute anything. Asking the chat for a deck stages a plan and stops, exactly as the dashboard does; the build runs in `_build_worker` on an explicit press, and `GET /download/<ticket>` serves the result token-gated, from the path the ledger recorded and only if it still resolves inside the deliverables folder.

A ticket carrying a file type (`pptx`, and later `xlsx`/`docx`) is produced by `app/deliverable.py`, which is the producer end of the ledger's lifecycle. It has two halves and they must stay separate: a **stager** (`stage_plan`) asks the model for a content plan and stops, and a **runner** (`build`) turns an approved plan into a native file, then `verify` reopens that file and checks it against the plan. The nod is what starts the build: `staging` writes no file, and only `approved` reaches a builder. Plans are validated before anything is built, so an empty slide, a placeholder, an oversized deck or a dash is refused with a reason rather than silently shipped. Decks are built natively with `python-pptx` (every string selectable and editable); rendering slides to images is permanently out. The workers that drive the lifecycle live in `workbench_serve.py` (`_stage_worker`, `_build_worker`) and any failure lands the ticket on `failed` carrying the reason, never wedged in `running`. Staging pulls `app/context_pack.py` for the counterparty first, so a deliverable argues from the vault's record; that lookup fails open, since losing the evidence should cost the deck its grounding, not the whole run.

`app/context_pack.py` is the evidence layer shared by drafts and deliverables: given a counterparty it returns their entity page, recent meetings, open commitments, hotcache deal context and a one-hop `brain_graph` neighbourhood, every entry carrying its source path. Two rules are load-bearing. An empty pack must never reach a model (`is_empty()` is the contract; the caller falls back to the thread alone and says so), because drafting on no evidence is how a confident wrong email gets written. And `open_commitments` holds only the user's own promises: the vault marks ownership with `dir=by_me|to_me|third_party`, and raising a counterparty's commitment as something the user owes is the expensive kind of wrong. Vault text carries machine state in HTML comments (`ai:`, `synth:`, `ae:`, `deal:`, `tasks:`); all of it is stripped generically at the single point text enters a pack, because stripping by name let `deal:` through to a live pack after `ai:` was handled.

Each briefing also owns exactly one **private web page** on claude.ai, rebuilt and republished to the same URL every run (`app/briefing_html.py`, the URL stored in `{vault}/van-gogh/logs/briefing_pages.json`). Publishing fails open: a run with no publishing surface renders the briefing in full, exits 0, and carries one line saying why the page did not update. `briefing.publish_page: false` turns it off.

**A fresh vault is seeded, not left empty.** Nothing in the codebase ever
CREATED a hotcache deal thread or an entity page from mail: `hotcache_sync`
and `meeting_ingest.update_hotcache_last_contact` are both update-only, so a
new install had neither, and everything downstream of them (overdue alerts,
kill and re-engage detection, the context pack behind every draft, the
relationship radar) was dead on day one. `app/warm_start.py` closes that at
install (the orchestrator's deals and meetings steps) and on demand via `/van-gogh:warm-start`: it reads the
60-day scan, proposes deal threads and entity pages, and writes only what the
user ticked. Three rules are load-bearing. **The confirm gate is the precision
guarantee**, not a formality: a dead thread recorded as live is worse than a
missing one, `cold_monitor` is excluded as the weakest signal, a cold thread
never gets an invented `next_action_due`, and nothing is ever written
`stage=dead`. **A seeded deal carries both the address and a `[[Name]]`
wikilink**, because `week_review.load_active_thread_emails` scans that section
for addresses and `relationship_radar.get_hotcache_active_names` scans it for
names; a block with one of them fixes half the product. And **a seeded
entity's `updated:` is the real last-contact date**, since the radar falls
back to that field when `sources` is empty, and stamping today would silence
it about exactly the people it exists to surface. Order matters: entities
before hotcache (the deal body links them), both before the meeting ingest
(whose per-meeting tag inference reads those pages), backfill last. `verify`
re-proposes against the vault and must report zero.

**The first run is its own case.** `config_loader.first_run()` is true when
nothing has ever rendered for this vault (no `week-*.md` at any age, no
`morning-coffee.md`); it reaches every briefing as `meta.first_run` and fails
toward False, because treating an established vault as fresh would suppress
real sections. Three rules follow from it. A first Morning Coffee **renders
without a week file** rather than erroring, since everything the week file
feeds is a comparison against a previous page and there is none (auto-running
Week would not help: the week FILE is written by the model in the Week skill,
not by `week_review.py`). Sections that describe a CHANGE stay quiet rather
than report the absence of history as news: `newly_cold` is skipped, the
missing-ledger error is dropped, and the SKILL omits COMPLETED SINCE LAST RUN.
And `morning_coffee.build_first_run_line` states, in code because it carries
counts, what was read and why nothing is marked as moved.

Related, and the same class of bug: **absence is only evidence over a
populated set.** `is_stale_item` and `gate_cold_promotion` demote a thread
whose counterparty has no live hotcache deal, which on a vault where nobody
has written any deals down demoted everything, so a new install saw FEWER
items than its mail supported. Both now require `active_emails` to be
non-empty before absence counts. Any new gate keyed on "not in the vault's
set of X" needs the same guard.

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
- `digest`: the emailed-briefing opt-in: `enabled` (default false), `sender_label` (a configured account label; empty = primary), `recipient_email` (empty = primary's address), `channels` (how a briefing is delivered; `email` is the only implementation, and a config with no `channels` block reads as email-on), and `briefings.{morning-coffee,afternoon-tea,week,week-retro}` each with `enabled` / `days[]` / `time` (24h, computer-local). Managed by `/van-gogh:update-digest-preferences`; `scheduler_setup.py install` and its narrowed twin `sync-digests` turn it into per-briefing launchd/Task Scheduler jobs that run `app/digest_send.py` (render via `claude -p`, then deliver via `_deliver`).
- `scheduler`: `reviewed` (the plugin version whose scheduling questions have been answered) and `declined[]` (job keys the user said no to). `/van-gogh:check-updates` asks once after an update about anything never offered and stamps the version, so the review is idempotent and a decline is never re-asked. Resolved via `scheduler_reviewed_version()` / `scheduler_declined()`.
- `workbench`: the local dashboard: `autostart` (start it at login, default false), `port` (default 8765), `email_mode`, `logo_fetch`, `open_on_run`, `chat_model`.
- `cadence`: schedules for the capabilities that otherwise run only on request, one entry per skill (`calendar-stub-check`, `relationship-radar`, `five-fifteen`, `voice-calibration`, `voice-generator`), each `{enabled, days[], time}`. All ship off, and only a literal true switches one on. Resolved via `skill_schedule(skill)`, which falls back field by field so a bad time costs the chosen time and never the job; the rows are `job_registry.SCHEDULED_SKILLS`, each a headless `skill_run.py <skill>`. A skill scheduled this way must carry a "When nobody is there" section keyed on `meta.unattended` (true under `VAN_GOGH_UNATTENDED=1`), because it cannot ask anything and may be mailed as printed; `tests/test_skill_schedule.py` enforces it.
- `connectors[]`: other systems a capability may ask about a person. Each has `name` (the user's word for it), `server` (the connector's tool prefix without `mcp__`), `tools[]` (only tools whose names read), `about`, and `use_for[]` (default `people`). Written by `/van-gogh:add-connector`, which checks every tool against the connector before saving. Resolved via `connectors()`, and filtered to usable tools by `connector_lookup.configured()`.
- `advisor`: the daily suggestions (`app/advisor.py`, `/van-gogh:suggest`). `enabled` (default false), `time` (local `HH:MM` after which the day's look may happen, default 06:00, early so the list is ready for Morning Coffee), `daily_cap` (clamped 1 to 5, default 3), `allow_send` (whether an email reply of `SEND n` may send a draft the advisor wrote; only a literal true counts, default false), `model` (override, empty for the default). Sender and recipient come from the `digest` block. Resolved via `advisor_enabled()`, `advisor_time()`, `advisor_daily_cap()`, `advisor_allow_send()`, `advisor_model()`.
- `auto_close`: the check-off of finished to-dos (`app/done_judge.py`, `/van-gogh:done-judge`). `enabled` (default TRUE, the one block that ships on; only a literal false switches it off), `model` (override, empty for `done_judge.JUDGE_MODEL`), `confidence` (the floor a verdict must clear, clamped 0.5 to 1.0, default 0.85), `expire_minor_days` (how old an undated, deal-less, untouched to-do must be to expire, clamped 0 to 365, default 30; 0 switches expiry off). Resolved via `auto_close_enabled()`, `auto_close_model()`, `auto_close_confidence()`, `auto_close_expire_days()`.
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
hooks, so the install skill is that step). An update is two CLI commands in
order, and `plugin_update.apply_update()` is the only code that runs them:
`claude plugin marketplace update van-gogh` re-clones the catalog and leaves
the installed plugin exactly where it was, and `claude plugin update
van-gogh@van-gogh` is what moves it. The first alone was once shipped as "the
whole update" and reported success while changing nothing, so the verdict
is read from the CLI's own `--json` reply (`updateOutcome`), never from exit 0.
Marketplace auto-update does both by itself, but it is off by default for a
third-party marketplace and most installs never switch it on. So updates are
**ask first**: nothing here pulls inside the plugin cache, and nothing is
installed without a yes. `app/plugin_update.py` compares the installed
`plugin.json` version against the one published on `main`; when they differ,
the four briefings end with one question (`skills/_shared/front-page.md` step
13) and install on a yes. A no is stamped against that version and the
question returns only when a newer one is published; unattended runs are never
asked, because nobody is there to answer.
`check_quietly()` runs at `config_loader` import (same managed-venv gate as
the hooks above), refreshes at most every fifteen minutes, and fails open;
every other run is a file read. The chat-start hook (`--session-notice`)
checks live with a two-second timeout unless a check ran in the last five
minutes, so a release published this hour is asked about in the next chat.
A failed check keeps a newer version an earlier check found. The result reaches skills as `meta.update_notice`, which
is "" unless an update is pending and has not been declined or already
installed by a session still running the old cache. `/van-gogh:check-updates`
is the manual trigger, which offers the same install. The check is one
unauthenticated HTTPS GET of the public repo's
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
expired login until a real check shows it works again. Re-runs go back through the OS
scheduler so a job runs exactly as it does at its normal hour, capped at
`support.job_watch_kick_cap` per slot. That restart carries a kill, so the
watcher must know a job is still working: a run row is written only when a
run ends, and silence once read as "never fired". Every clock job therefore
calls `run_ledger.mark_started` when its real work begins (a marker with its
process id, in the state directory because a pid means nothing on another
machine), `record_run` clears it, and a job with a live marker grades
`running` and is never restarted. `tests/test_job_registry_guard.py` fails a
clock job that leaves no marker. Two holds are no longer unconditional. An
expired login is released by `_auth_recovered`, which makes the one real call
that proves a sign-in works (a refresh, or `claude auth status`), and for a
missing permission, which still refreshes fine, waits for the stored sign-in
itself to change. And a job that is switched on but that the scheduler
reports as absent is told to the reader in the footer, unless they declined
it at install; a scheduler that could not be read at all says nothing,
because no answer is not the answer "absent". Every path fails open, and it never
announces itself: its own liveness reaches the user as `meta.job_watch`, one
footer line the briefings render (`skills/_shared/front-page.md` step 9),
which is also what says so when the watcher itself has gone quiet. That is
why there is no second process watching this one. **Any new unattended entry
point must record a run through `run_ledger` and appear in `job_watch.roster()`**,
which derives itself from `app/job_registry.py` rather than a hand-kept list,
so nothing can be scheduled and ungraded.

One message is sent without an explicit instruction, and it is the only one:
the setup nudge (`app/setup_nudge.py`). While `<state_dir>/setup/checklist.json`
exists, an install stopped before the finish line, and nothing else in the
product can reach that client: every other notice surface needs a vault and
config, which is box 5 of 9, and the sign-in that broke is usually the step
they stopped at. So the nudge goes to the client only, from their own account
to their own address, at most three times per stall (day 1, 3 and 7, never two
inside 24 hours), with one plain action, where to type it, who must act when
it is not them, and the help address; never mail content, never a secret,
never a list of everything unfinished. When no sign-in works it is a desktop
notification instead. `stop` ends it for good, finishing the install removes
it, and it is deliberately not a `job_registry` row (see FOUR-CS.md). It is
the same class of carve-out as the opt-in failure alert, and the pre-call
checklist tells the client it will happen.

**Every scheduled job is one row in `app/job_registry.py`**, and everything
else derives from it: `scheduler_setup` installs, removes and reports from it,
`job_watch.roster()` grades from it, and the install skill asks about it. It
used to be defined in three places with each hand-written job repeated across
four functions, and the copies drifted silently: the prep poller went
unreported on macOS and unreported-plus-ungraded on Windows, the Note tick was
installed and graded by nothing, and four briefings could be scheduled without
the install ever asking about them.

Adding a job is adding a row. `tests/test_job_registry_guard.py` walks the
table and asserts each row is installable, uninstallable, reported on both
platforms, gradeable, recording a ledger name its own script actually writes,
and **offered to the user**: its `skill_phrase` must appear in a SKILL.md
that asks about it, which is what makes "a feature nobody is ever asked about"
impossible to reintroduce. A row may skip grading only by declaring
`graded=False`, and exactly two may: the watcher (which cannot watch itself)
and the Workbench (a server, graded by liveness).

A briefing also declares `depends_on`. launchd coalesces every missed run and
fires them on wake in no order, so a laptop opened at 08:30 on a Monday can
run Morning Coffee against a vault nothing has filed yet, and the watcher
cannot catch that, because a briefing that ran early exits 0 and writes a
file. `run_ledger.wait_for_dependencies` holds a scheduled briefing until its
ingests have run today, bounded, then runs anyway and says so. It only applies
to a run the OS started (`VAN_GOGH_SCHEDULED=1`, set in the job's own command
line): a hand-run briefing wants to run now, and a machine that has never run
anything is a fresh install rather than a late morning.

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

`app/advisor.py` is the layer above the watcher, and the watcher calls it
last in every tick (`job_watch._advise`, after the tick is graded and
recorded, so a slow look can never cost a check). The watcher keeps the jobs
running; the advisor asks what is worth doing and does it on a yes. Off until
`advisor.enabled`. Once a day it gathers a situation in code, lets a
read-only session read the vault (`claude_cli.run_claude_readonly`, so the
session that looks has Read, Grep and Glob and cannot write, draft or send),
and files each surviving suggestion as a Workbench ticket already `staged`,
which is the state the page draws as waiting on a nod and the one
`store.propose` never prunes. Five rules are enforced in code, never left to
the prompt. **Evidence is checked against the page**: every suggestion quotes
a vault file and the quote must be found there after whitespace is collapsed,
or the whole suggestion is dropped; a draft's address must already appear in
the vault or on the front page; a vault edit's `find` must occur exactly once
in a markdown note outside `van-gogh/`. **Only the user's own words are a
yes**: an email answer is read from the SENT folder of the account the list
was mailed to, never an inbox, and only from the leading lines of the reply
through one strict pattern, so the quoted original and any prose are ignored.
**A yes never sends**: it leaves a draft, a file, or an edit with the old
page copied to `logs/advisor/backups/`. Sending is a separate `SEND n`, and
by email it also needs `advisor.allow_send`, which ships off. **Every action
is claimed before it is taken**: a reply's message id before its answer is
applied, a ticket's move to `running` before the work, the `sent_at` stamp
before the send, so a crash or a lost response costs a miss and never a
duplicate. **A no is remembered**: a dismissed ticket keeps its id, so the
same suggestion cannot return. The ledger is now written by two processes
(the server and the watcher), which is why `workbench_store` holds a lock
file around every mutation. The same answers work from the Workbench
(KICK OFF and Dismiss, via `_advisor_worker`) and from a chat
(`/van-gogh:suggest`). Morning Coffee and Afternoon Tea carry the same list
as `meta.suggestions_md`, built by `advisor.briefing` and pasted verbatim
(`skills/_shared/front-page.md`, under step 5): the numbers it prints are
acted on, so it is code's to write and never the model's. A list's reference
(`batch_code`) is derived from its ticket ids in numbered order rather than
minted at send time, because a briefing is rendered in one process and
mailed by another; the same list therefore always means the same numbering,
whichever message is replied to, and replies are found by that reference
anywhere in the message. A briefing only offers a reply when the daily look
is on, since the same tick reads replies.

**Vincent** (`agents/vincent.md`, `app/vincent.py`, `/van-gogh:vincent`) is
the chief of staff: one agent that knows every skill and agent on the
machine, whether each scheduled job ran, and what the user asked him to
remember, and that hands each request to whichever of them does it. He is
reached three ways (the skill, `claude --agent vincent` from the vault, and
the Workbench briefing chat), and all three start from the same thing: a
situation gathered by code with no model in the path. `catalog()` reads
every SKILL.md and agent file off disk on each call (the plugin's own, the
user's `~/.claude/skills` and `~/.claude/agents`, and every other plugin in
`installed_plugins.json`), so nothing is hand-kept and a skill added a
minute ago is in the next answer. `schedule()` is one row per `job_registry`
row with today's grade from `job_watch.evaluate` and the watcher's own
sentence from `job_watch.decide`, run against a copy of the watcher's state:
it never re-runs anything and never writes that state. `memory()` reads the
workspace memory page, the follow-ups ledger and Claude Code's memory for
this vault and plugin (`memory_sync.candidate_memory_dirs`, which does not
wait for the mirror switch because nothing is copied), with credential
shapes replaced. `--situation --page N` prints the same thing as text in
pages, because a session's shell tool cuts long output and a catalog that
arrives truncated has skills silently missing.

Four things are code's and never the prompt's. **Who got the work**: the
agent file requires one line, `Handing to: /van-gogh:<skill>`, `Handing to:
<agent>` or `Handing to: nobody`, before any hand-off, and
`vincent.parse_handoff` reads it; two different lines in one reply are no
hand-off at all. In a session with real tools the line comes from
`vincent.py --handoff <name>`, which checks the name against the catalog and
says whether it waits for a yes: on the first live run the written rule
alone was skipped exactly when the work really started, because a model
about to call a tool goes straight to the tool, and a command is something
it does call. That got the line produced and still not said: the independent
evaluation ran one real hand-off three times, the skill ran every time, and
the line reached the reply once, because a model that has just run a skill
narrates the skill's result. So the guard is a Stop hook
(`hooks/handoff_line.sh`, `app/handoff_hook.py`): it reads the session's own
transcript, and when the turn that is ending ran the hand-off command and the
line it printed is not in the last thing the assistant said, the stop is
refused once with the exact line to put at the top. It costs no Python in a
session that never ran the command, never loops (`stop_hook_active`), and
answers every doubt with "let it stop". A skill's own instructions arrive in
the transcript as a user-shaped entry marked `isMeta`; reading that as the
person speaking is the bug the first version had, found only by running it
on the real captured session. Two more came the same way and both are
tested against the sessions that showed them: a turn can ask for the line
twice for different names, so any one of them in the reply passes (demanding
the last one put two different lines in one reply); and the reply that is
graded is the one the hook is handed (`last_assistant_message`), never the
transcript's last text, because the file is written a moment behind and the
hook twice read it before the final reply had landed. **What runs with nobody at a terminal**: `vincent.answer`
is `claude_cli.run_claude_readonly` with the agent file as its rules, so the
turn has Read, Grep and Glob and cannot run, write or send; when its line
names a skill, code runs it through `skill_run.run_skill`, and only if it is
in `vincent.HEADLESS_RUNNABLE`, and only if the reply does not end in a
question (`vincent.is_asking`): Vincent writes the same line when he is
proposing work that waits for a yes, so the closing question is what tells
code "not yet". Anything else is answered with where to run it. **What needs a yes**: `vincent.NEEDS_CONFIRM` marks the skills that
change an account, the settings or the install; every catalog row carries
the flag and none of them is headless-runnable. **How a failure reads**: a
skill that exits nonzero is reported as not finished with the line it left,
the reply is cut to `REPLY_CAP` and says so, and a turn is stopped at
`VINCENT_TIMEOUT_S`. A conversation is kept per channel per day
(`~/.config/van-gogh/vincent-sessions.json`), so a second message can refer
to the first and yesterday's is not carried into today. The one write is
`--remember`, a dated line inside a marked block of the workspace memory
page, placed above the follow-ups ledger so the radar never reads it as an
entry. Vincent runs on the same `claude` CLI as every job, so a usage cap or
a stale CLI stops him too; `vincent.py --schedule` needs no model and still
answers. A new job must map to a skill in `vincent.skill_of_job` or be named
in `JOBS_WITHOUT_A_SKILL`, and `tests/test_vincent.py` fails until it does.

Vincent also knows what is set up and what is not (`vincent.install`, the
SETUP and WHAT IS SET UP sections of page 1). The setup steps come from the
checklist when one exists, which is the truth, because the product reopens a
step there by itself when a sign-in stops working or a job disappears, and
from the completion stamp otherwise; the read re-checks disk on a copy and
never saves. While a step is open the opening leads with it, and asking him
to set up is the go-ahead: setup is the one piece of work he does himself,
by following `vincent.py --setup-rules` (the install skill's own page, so
there is one copy of the rules), with no hand-off line. A job is not reported
as not having run before setup has reached its schedule step. He changes the
schedule the same way a person at setup does: `schedule_plan.py change
--dry-run` shows what would change, a yes runs it without the flag, and it
keeps `config.json.bak`, writes, and resyncs the scheduler. A new job needs a
row in `schedule_plan.NAMES` too, and `tests/test_schedule_plan.py` holds the
table to the registry row by row: whether each is on and when it runs must
agree with what `scheduler_setup` would install from the same config.

**A scratch state folder never reaches the real scheduler.** The jobs live in
the per-user scheduler whatever `VAN_GOGH_STATE_DIR` says, so a harness run
that resynced them would point every job on the machine at a scratch vault,
and one that read them would tick its own schedule step from another
install's jobs. `setup_state.scheduler_allowed()` refuses both unless
`VAN_GOGH_SCHEDULER_SYNC=1`, the same shape as the reminder job's rule; a test
that stubs the scheduler sets it.

`app/task_stall.py` answers the other half of "is anything wrong": the
machinery ran fine and a commitment has been sitting in the vault for three
weeks anyway. It reads the follow-up ledger through `follow_up_radar`'s own
parser (two definitions of overdue that can disagree is how a system starts
nudging about finished work) and the Workbench ledger for tickets waiting on
the user. Nothing re-runs any of it, so it is never actioned, only reported as
`meta.task_stall`, one line that names the items and says whose move it is.

Whether any of it is worth having is `app/kpi_report.py`'s question. Six
measures over a fortnight, rendered by `app/kpi_html.py` and carried inside
Week Retro (`week_retro._scorecard`) when `kpi.enabled` is on. It used to be
its own weekly email on its own scheduled job; folding it into the Friday
retro removed a job to schedule, grade and ask about, and an email. `kpi.day`
and `kpi.time` are dead keys, still read so an existing config parses. They are computed from
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

Whether a to-do is finished is `app/done_judge.py`'s question, and it is the
one place in the product that closes something nobody ticked. It runs at 5:10
AM, after the two ingests and before every briefing, and it ships ON
(`auto_close.enabled`), which nothing else here does. Five rules make that
safe, and each is code rather than a sentence in a prompt.
**Every to-do carries an id on its own line**:
`<!-- cb=cb-XXXXXXXX opened=YYYY-MM-DD -->`, written by `app/item_ids.py`.
`meeting_ingest` stamps it on every checkbox under What You Owe, What Others
Owe You and Action Items and on the hotcache line it promotes, the two
sharing one id because both derive it from the page title and the item's
words; `opened` is the meeting date and never today, since the judge refuses
evidence older than the item. Lines written before ids existed are backfilled
on the judge's first live run, and a bare `cb=` keeps its id. Stamping never
rewords, a second pass changes zero bytes, and files keep their own line
endings (read and written with `newline=""`).
**The model proposes and `validate` disposes.** A verdict closes something
only after the quoted sentence is found word for word in ONE dated evidence
section, dated on or after `opened`, and survives every check named in
`done_judge.GUARDS` (a promise, a denial, the task's own words, a mail
header, the older message quoted beneath a reply, mail to oneself, someone
else's line, a class too weak for who owes the item, the confidence floor, a
pin). A mail body is split before it is shown (`split_quoted`): what the
sender wrote is the evidence, and the thread quoted under it is shown apart,
labelled, so the judge can see what was being answered and cannot cite it. A
reply carries the other side's sentences inside a message the user sent, and
before the split "I mailed the sheets yesterday" quoted under a "thanks" was
citable as the user's own sent mail. `tests/test_done_judge_controls.py`
switches each guard off in turn and requires a planted control to close as a
result, so a guard nothing depends on fails the suite, and a guard added to
`validate` without a control fails it too. The same controls go through the
real model in `done_judge.py --selftest`, which also holds known-done
controls so a judge that answers open to everything fails.
**There is no confirm step, so there is an undo.** `--undo <cb>` restores the
hotcache line, the page line and the week file line byte for byte when they
are as the close left them, clears only the box when someone has edited the
line since, and pins the item open in the state file
(`~/.config/van-gogh/done_judge_state.json`, never the vault, because a pin
is a decision made on one machine).
**`completion_scan.mark_done` is the one writer.** The judge hands it
candidates carrying `cb`, and its id branch flips the line with that id in
each file. That is what keeps the scorecard's rule true with no code here:
a close event is recorded by the writer, and an expiry (an old item with no
due date, no deal and no trace of activity) passes `kpi: False` because
tidying a list is not finishing work.
**Absence only counts over a complete search.** Expiry runs only when the
mailboxes were read and no transcript the item needed was skipped; a
degraded run expires nothing. A model that stops answering (`JudgeUnavailable`,
exit 75) costs the whole run and never half of one, since every batch is
judged before anything is applied, and the ledger row carries the class so
the watcher holds the job. The briefings read `closed_today`,
`render_closed_md` (written by code, every line with its id and its undo)
and `judge_note`, the sentence that makes a failed run read differently from
a quiet day. Model calls go through `claude_cli.run_claude` with
`claude_update.ensure_current` before the first one.
**The clock is the one ceiling, and the freshest proof is read first.** A
run used to stop at 25 calls with the queue in the order the hot cache lists
it, so a ledger past 200 items left the same tail unread every morning and
the to-do answered last night was as likely to be in that tail as anywhere.
There is no call ceiling now (`--max-calls` still stops a run for whoever
passes one). `queue_key` puts the item with the newest mail or decision
first, a meeting that merely shares words with a task does not count as
news, and `MAX_MINUTES` ends a run that has gone on an hour, reporting what
it left as not reached.

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
opts a centrally managed install out. There are four sanctioned raw
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
