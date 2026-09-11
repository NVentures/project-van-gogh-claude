---
name: create-skill
description: Scaffold a user-authored personal skill that plugs into the Van Gogh framework (shared venv, config, vault paths) but lives in ~/.claude/skills/ — outside the plugin cache and outside any git repo, so it is never committed and survives plugin updates. Can also fork a shipped skill into a personal variant the user is free to change. Use when the user types /van-gogh:create-skill or asks to create their own skill, add a custom skill, customize a built-in skill, or extend Van Gogh with a personal command.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
---

# Create Skill

Scaffold a new **personal skill** for this user. Personal skills live in
`~/.claude/skills/<name>/` and are invoked as `/<name>` (no `van-gogh:` prefix
— that namespace is reserved for plugin-shipped skills). Because they live
outside the plugin cache and outside any git repository, they are never
tracked, never committed, and a marketplace update can never delete them.

The scaffold wires the skill into the Van Gogh framework: the shared venv at
`~/.config/van-gogh/venv`, and the plugin's `app/` modules (config loader,
vault paths, clients) via the `plugin-root` pointer. The names user code may
safely lean on are listed in
[`_shared/personal-skill-api.md`](../_shared/personal-skill-api.md); read it
before writing any script logic, and point the user at it too.

## Python runtime

Before the first script invocation, run the ensure-venv guard in
[`_shared/python-runtime.md`](../_shared/python-runtime.md).

## Checkpoint every answer

Open a capture before the first interview question, so a session that ends
early does not cost the user the whole design conversation:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" start --skill create-skill --goal "scaffold a personal skill"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" start --skill create-skill --goal "scaffold a personal skill"
```

`resumable: true` means an earlier run is still open: read it with `show`
and continue from where it stopped rather than starting over.

Append after every answer:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" append --skill create-skill --question "<what you asked>" --answer "<what they said>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" append --skill create-skill --question "<what you asked>" --answer "<what they said>"
```

Close it once the skill has been written to disk:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py" close --skill create-skill --status complete
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\interview_capture.py" close --skill create-skill --status complete
```

---

## Step 1 — Interview

Collect (from the user's request first; only ask for what's missing):

1. **Name** — kebab-case; it becomes the directory name and the `/<name>`
   command. Suggest one from their description if they didn't give one.
2. **What it does** — one or two sentences, plus the phrases that should
   trigger it (these go into the `description` frontmatter).
3. **Shape** — one of three:
   - **prose-only**: instructions you follow directly (a checklist, a custom
     render). The simpler default when unclear.
   - **script-backed**: needs a Python script (fetches/crunches data, writes
     files).
   - **fork of a shipped skill**: the user wants `/van-gogh:<something>` to
     *behave differently* (a different render, extra steps, fewer steps).
     Forking gives them their own copy to edit; the shipped one stays as is.

## Step 2 — Refresh the plugin-root pointer

Personal skills don't get `${CLAUDE_PLUGIN_ROOT}`, so their scripts find the
plugin's `app/` modules through `~/.config/van-gogh/plugin-root` (written by
`user_state.record_plugin_root()` at every plugin script run). Make sure it's
current right now:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import config_loader; import user_state; print(user_state.state_dir() / 'plugin-root')"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import config_loader; import user_state; print(user_state.state_dir() / 'plugin-root')"
```

The import of `config_loader` records the pointer as a side effect; the
printed path must exist afterwards. If it doesn't, stop and report — don't
scaffold a script that can't find the plugin.

## Step 3 — Run the scaffolder

`app/scaffold_skill.py` writes the skeleton deterministically (collision
check, cross-platform command blocks, the plugin-root bootstrap). Pick the
matching form:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scaffold_skill.py" \
  --name <name> --kind <prose|script> --description "<one-line summary + trigger phrases>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scaffold_skill.py" `
  --name <name> --kind <prose|script> --description "<one-line summary + trigger phrases>"
```

For a fork, pass `--fork <shipped-skill-name>` instead of `--kind` and
`--description` (e.g. `--fork morning-coffee`). The scaffolder copies the
shipped SKILL.md and rewrites every `${CLAUDE_PLUGIN_ROOT}` reference to the
pointer form, so the fork runs the same scripts from day one.

Read the JSON it prints:

- `{"ok": true, ...}` — done; `files` lists what was written, `warnings`
  carries anything worth relaying (e.g. a basename that shadows a shipped
  skill).
- `{"ok": false, "error": "exists", ...}` — a skill of that name already
  exists. Show the user its current SKILL.md description and ask: overwrite
  (re-run with `--force`) or pick a new name.
- Any other `{"ok": false}` — report the error verbatim and stop.

## Step 4 — Fill in the behavior

The scaffold is a skeleton with `TODO` markers; a fork is a verbatim copy.
Now edit the generated files to match the interview:

- Prose skill: replace the TODO section with the actual instructions.
- Script skill: implement `main()` in `<name>.py` and describe its JSON keys
  in the SKILL.md render section. Everything the script may import or shell
  out to is in `_shared/personal-skill-api.md`; stay on that surface, and
  keep the generated code cross-platform (all file I/O `encoding="utf-8"`,
  `pathlib.Path` joins, config via `config_loader` helpers only, no
  macOS-only shell commands, both shell forms for every command you show).
- Fork: apply the user's requested changes to the copied SKILL.md, and
  update its frontmatter `description` so the trigger phrases don't collide
  with the shipped skill's.

## Step 5 — Report

Tell the user:

- The skill is live as `/<name>` (a fresh Claude Code session may be needed
  before it appears in the command list).
- Where it lives: `~/.claude/skills/<name>/` — theirs to edit freely; it is
  not in any repo, will never be committed, and plugin updates can't touch it.
- To remove it, delete that directory.
- One caveat: beyond the supported surface in `_shared/personal-skill-api.md`,
  the plugin's internals can change across updates; if their skill breaks
  after an update, re-run `/van-gogh:create-skill` mentioning the old skill
  and you'll help fix it up. (Forks carry the same caveat: the shipped skill
  they copied keeps evolving, the fork doesn't follow.)
