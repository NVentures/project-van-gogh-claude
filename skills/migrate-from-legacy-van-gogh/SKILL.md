---
name: migrate-from-legacy-van-gogh
description: One-time migration from a legacy (pre-plugin) Project Van Gogh install into the current plugin. Copies OAuth tokens + API keys from the old repo-root .env into ~/.config/van-gogh/.env, writes the vault pointer, reconciles config.json (adds new blocks, ports podcast feeds), and moves the follow-up ledger into the vault. Use when the user says "migrate from my old van gogh", "I have an old standalone van-gogh checkout to move over", "import my legacy van gogh data", or is switching from a git-clone install to the plugin.
disable-model-invocation: true
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# /van-gogh:migrate-from-legacy-van-gogh

A one-time move from the **legacy** Project Van Gogh — the standalone `git clone`
that kept its state at the repo root (`.env`, `.van-gogh-vault`, a `uv`-managed
`.venv/`) and its follow-up ledger in Claude Code's per-project memory — into the
**plugin** layout, which reads machine state from `~/.config/van-gogh/` and the
follow-up ledger from the vault. The plugin's own auto-migration only rescues
legacy files sitting in the plugin cache, so an external clone needs this step.

A Python script does the whole deterministic migration; your job is to find the
old clone, run the script (dry-run first), read the JSON result back to the user,
and walk them through the short manual follow-up. **The migration only reads the
legacy clone — nothing is moved out of or deleted from it**, so it is safe to run
and safe to re-run.

## Prerequisite: the plugin must be installed first

This skill migrates *data*, not the plugin itself. If `~/.config/van-gogh/venv`
doesn't exist yet, the user hasn't run first-time setup — point them to
`/van-gogh:install-van-gogh` first (it creates the venv, OAuth app, and vault
scaffold), then come back here to pull their legacy data across. If the vault
already has a working `{vault}/van-gogh/config.json` from the legacy install,
they can run this skill directly after the venv exists.

---

## Python runtime

**Python runtime:** before the script invocation, run the ensure-venv guard in
[`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Step 1 — Locate the legacy clone

Ask the user for the path to their old standalone `project-van-gogh` checkout
(the folder that contains `app/`, and — hidden — a `.env` and/or a
`.van-gogh-vault` file). Common spots: `~/project-van-gogh`,
`~/Development/project-van-gogh`, `~/code/project-van-gogh`.

If they're unsure, look for it (macOS/Linux):
```bash
find "$HOME" -maxdepth 4 -name ".van-gogh-vault" 2>/dev/null
```
Windows (PowerShell):
```powershell
Get-ChildItem -Path $HOME -Recurse -Depth 4 -Force -Filter ".van-gogh-vault" -ErrorAction SilentlyContinue | Select-Object FullName
```

Do NOT guess the plugin cache directory — that is not the legacy clone.

## Step 2 — Dry run (preview, writes nothing)

Run with `--dry-run` first and show the user exactly what will change before
touching anything.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/migrate_from_legacy.py" --legacy-clone "<CLONE_PATH>" --dry-run
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\migrate_from_legacy.py" --legacy-clone "<CLONE_PATH>" --dry-run
```

Optional flags:
- `--vault "<PATH>"` — set the vault explicitly (default: read the clone's
  `.van-gogh-vault`, then any existing pointer).
- `--force` — overwrite secrets that already exist in `~/.config/van-gogh/.env`
  (default: existing keys are kept, and listed under `skipped_existing`).

If the output has an `error` key, stop and resolve it (a wrong clone path, or no
vault path could be determined — pass `--vault`). Otherwise summarize the preview
from the JSON (see **Reading the result** below) and confirm with the user before
the real run.

## Step 3 — Run the migration

Re-run the exact same command **without** `--dry-run`. Read the JSON result.

## Reading the result

The script emits one JSON object. Surface these to the user in plain language:

- `vault` — the vault the plugin will now use; `pointer` — where the pointer was written.
- `secrets.migrated` / `secrets.skipped_existing` — which OAuth tokens / API keys
  were copied into `~/.config/van-gogh/.env` (values are never printed). Skipped
  keys already existed; re-run with `--force` only if the legacy value is newer.
- `config.blocks_added` — new config blocks seeded from the current template
  (e.g. `digest`, `podcasts`); `config.podcast_feeds_added` — legacy hardcoded
  podcast feeds ported into `podcasts.feeds`.
- `follow_ups.status`:
  - `replaced_placeholder` / `appended` / `created` — the legacy "## Pending
    follow-ups" ledger was moved into the vault workspace memory. Done.
  - `no_legacy_ledger` — nothing to move (no legacy ledger, or it was empty).
  - `conflict_both_have_entries` — **needs you**: the vault memory already has a
    populated ledger. The legacy ledger is in `follow_ups.legacy_section`; open
    the vault memory (`{vault}/van-gogh/projects/Project Van Gogh/memory.md`),
    merge the legacy bullets into its "## Pending follow-ups" section by hand
    (dedupe obvious repeats), and confirm with the user.
- `manual_next_steps` — read these out; they're the human follow-ups below.

## Step 4 — Manual follow-ups (walk the user through)

1. **Verify OAuth** — run a briefing (`/van-gogh:morning-coffee`). If a token was
   rejected (tokens can expire or be revoked), reconnect that account with
   `/van-gogh:add-account`.
2. **Turn on auto-update** — in `/plugin` → Marketplaces, enable auto-update for
   the `van-gogh` marketplace. The legacy un-namespaced update command is
   gone; updates now come from the marketplace. (Today's
   `/van-gogh:update-van-gogh` is unrelated: it installs or re-keys a personal
   companion plugin, never the core plugin.)
3. **New command names** — everything is namespaced under `van-gogh:` now:
   `/van-gogh:five-fifteen`, `/van-gogh:week`, etc. — the old un-prefixed
   `five-fifteen` form no longer resolves.
4. **Retire the old clone** — once briefings run cleanly, the legacy checkout can
   be deleted. This migration only read from it, so nothing is lost. Its old
   `uv`-managed `.venv/` is not reused (the plugin has its own venv).

## When to ask the user

- Before the real (non-dry-run) run, if anything in the preview looks off
  (unexpected vault path, a `--force` that would overwrite a token they just set).
- On `follow_ups.status: conflict_both_have_entries` — the ledger merge is a
  judgment call; show both and confirm the merged result.
- If OAuth verification fails after migration — confirm which account to reconnect.
