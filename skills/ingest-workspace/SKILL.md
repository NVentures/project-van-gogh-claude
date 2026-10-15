---
name: ingest-workspace
description: >-
  Maintainer tool: sync Project Van Gogh's own development state to the Obsidian Second Brain.
  Detects new skills, scripts, and settled decisions since the last sync, then appends a Dev
  Log entry to wiki/projects/Project Van Gogh.md. Use when asked to "ingest workspace", "sync
  to vault", or "what's new in Project Van Gogh".
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Ingest Workspace (Project Van Gogh)

Detects what has changed in the Project Van Gogh repo since the last sync and writes a Dev Log entry to the vault.

## Resolved paths

This skill spans two roots, and it matters which is which:

- **Plugin root** (`${CLAUDE_PLUGIN_ROOT}`) — the *source* being scanned
  for changes: `skills/`, `app/`, `CLAUDE.md`.
- **`van-gogh/` in the vault** — where Van Gogh *state* lives. The state file,
  the cutoff marker, and the `projects/Project Van Gogh/memory.md` it reads all
  live here, not in the repo.

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

Resolve the state paths once, up front, instead of hardcoding them:

macOS / Linux (bash/zsh):

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "
import sys, os, json; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app'))
from config_loader import resolved_meta
m = resolved_meta()
print(json.dumps({k: m[k] for k in ('vault_path','van_gogh_root','projects_dir','vangogh_memory_path','ingest_state_path','logs_dir')}, indent=2))
"
```

Windows (PowerShell):

```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os, json; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import resolved_meta; m = resolved_meta(); print(json.dumps({k: m[k] for k in ('vault_path','van_gogh_root','projects_dir','vangogh_memory_path','ingest_state_path','logs_dir')}, indent=2))"
```

Use `ingest_state_path`, `van_gogh_root`, `logs_dir`, and `vangogh_memory_path`
verbatim below.

## State file

The state file is `{ingest_state_path}` ( = `{van_gogh_root}/.ingest_state.json`).

Format:
```json
{
  "last_ingest": "YYYY-MM-DD"
}
```

If absent, treat as never-synced (cutoff = `1970-01-01`).

---

## Unattended Mode (--auto)

When invoked as `/van-gogh:ingest-workspace --auto` (e.g. from a LaunchAgent), skip all confirmation:

1. Load state and detect changes (Steps 1-2).
2. If nothing changed: print "No changes since {cutoff}" and exit.
3. Skip dry-run — analyze changes and compose the Dev Log entry directly.
4. Apply: write the Dev Log entry to the vault page and append to `wiki/log.md`.
5. Update state file.
6. Print what was written to stdout. Do not ask anything.

---

## Step 1 — Load state

Read the state file at `{ingest_state_path}` (resolved above):

macOS / Linux (bash/zsh):

```bash
cat "{ingest_state_path}" 2>/dev/null || echo "no state file"
```

Windows (PowerShell):

```powershell
if (Test-Path "{ingest_state_path}") { Get-Content "{ingest_state_path}" } else { "no state file" }
```

Record `last_ingest`. If missing, set cutoff to `1970-01-01`.

---

## Step 2 — Detect changes

Find files modified since the cutoff in the four areas that matter:

The cutoff marker lives in the vault `logs/` (`{logs_dir}/vangogh_cutoff`). The
*source* dirs being scanned (`skills`, `app`, repo `CLAUDE.md`) are in the
repo; the `memory.md` being scanned is the migrated copy under `{van_gogh_root}`.

macOS / Linux (bash/zsh):

```bash
CUTOFF="{logs_dir}/vangogh_cutoff"
touch -t {YYYYMMDD}0000 "$CUTOFF"

# New or changed skills (repo source)
find "${CLAUDE_PLUGIN_ROOT}/skills" \
  -name "SKILL.md" -newer "$CUTOFF" | sort

# New or changed scripts (repo source)
find "${CLAUDE_PLUGIN_ROOT}/app" \
  -name "*.py" -newer "$CUTOFF" | sort

# memory.md changes (vault state)
find "{van_gogh_root}/projects/Project Van Gogh" \
  -name "memory.md" -newer "$CUTOFF"

# CLAUDE.md changes (repo source)
find "${CLAUDE_PLUGIN_ROOT}" \
  -maxdepth 1 -name "CLAUDE.md" -newer "$CUTOFF"
```

Windows (PowerShell):

```powershell
$CUTOFF = "{logs_dir}\vangogh_cutoff"
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import pathlib, os, datetime; p=pathlib.Path(r'$CUTOFF'); p.touch(); ts=datetime.datetime({YYYY},{MM},{DD}).timestamp(); os.utime(p,(ts,ts))"
$cut = (Get-Item $CUTOFF).LastWriteTime

# New or changed skills (repo source)
Get-ChildItem -Path "$env:CLAUDE_PLUGIN_ROOT\skills" -Recurse -Filter SKILL.md | Where-Object { $_.LastWriteTime -gt $cut } | Sort-Object FullName | ForEach-Object FullName

# New or changed scripts (repo source)
Get-ChildItem -Path "$env:CLAUDE_PLUGIN_ROOT\app" -Filter *.py | Where-Object { $_.LastWriteTime -gt $cut } | Sort-Object FullName | ForEach-Object FullName

# memory.md changes (vault state)
Get-ChildItem -Path "{van_gogh_root}\projects\Project Van Gogh" -Filter memory.md | Where-Object { $_.LastWriteTime -gt $cut } | ForEach-Object FullName

# CLAUDE.md changes (repo source)
Get-ChildItem -Path "$env:CLAUDE_PLUGIN_ROOT" -Filter CLAUDE.md | Where-Object { $_.LastWriteTime -gt $cut } | ForEach-Object FullName
```

If nothing changed: report "No changes since {cutoff}" and stop.

---

## Step 3 — Analyze

For each changed area, read the relevant file and extract what's worth preserving:

**New skills** — read each changed `SKILL.md` and extract the skill name and one-line purpose from `description:` frontmatter.

**New scripts** — note filename and infer purpose from the filename and first 20 lines (docstring or comment at top).

**memory.md** — read the full file and identify any new entries under `## Next to build` or any new items that weren't in the previous sync's content. Focus on decisions, not implementation details.

**CLAUDE.md** — note only structural changes (new sections, new behavioral rules) — not formatting tweaks.

**What counts as worth syncing:**
- New skill added (name + one-line purpose)
- New Python script representing a new capability
- New settled decision or graduation path milestone
- Notable CLAUDE.md rule additions

**What to skip:**
- Bug fixes and minor edits
- Config or credential changes
- Anything already in the vault Dev Log

---

## Step 4 — Dry-run

Print a preview before writing:

```
=== Project Van Gogh: Ingest Preview ===
Vault page: wiki/projects/Project Van Gogh.md
Changes since {last_ingest}:

  New skills: /van-gogh:afternoon-tea, /van-gogh:ingest-workspace
  New scripts: afternoon_tea.py
  memory.md: no changes

Proposed Dev Log entry:
  2026-05-22 -- Workspace sync. Added /van-gogh:afternoon-tea (end-of-day focus wrap-up) and
  /van-gogh:ingest-workspace (this skill). afternoon_tea.py cross-references meeting action
  items with morning-coffee open tasks.
```

Ask: "Apply this Dev Log entry? (yes / edit / skip)"

---

## Step 5 — Apply

On approval:

1. Read the current vault page:
   ```
   {obsidian_vault_path}/wiki/projects/Project Van Gogh.md
   ```

2. Append under `## Dev Log` (do not duplicate an entry with the same date):
   ```
   - **YYYY-MM-DD** -- Workspace sync. [one or two sentences. No code details. Use [[wikilinks]] for known entities.]
   ```

3. Append to `{obsidian_vault_path}/wiki/log.md`:
   ```
   ## [YYYY-MM-DD] workspace-ingest | Project Van Gogh

   - **Business**: `#van-gogh`
   - **Changed**: [list what changed]
   - **Dev Log entry**: [the entry text]
   ```

---

## Step 6 — Update state file

Write today's date to `{ingest_state_path}`:

```json
{
  "last_ingest": "YYYY-MM-DD"
}
```

---

## Step 7 — Summary

```
Workspace ingest complete.

Applied: wiki/projects/Project Van Gogh.md, Dev Log updated.

Next: run /van-gogh:meeting-ingest to catch any meetings since last session.
```

---

## Rules

- Never write code details or implementation notes to the vault.
- Never duplicate an entry already in the Dev Log (read existing section before writing).
- Always confirm before applying (dry-run first).
- Past tense, one or two sentences, wikilinks for known entities.
- This skill writes Dev Log entries only — do not create new entity pages.
