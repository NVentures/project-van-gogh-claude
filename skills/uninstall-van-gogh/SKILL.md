---
name: uninstall-van-gogh
description: Remove the Project Van Gogh background scheduler — the launchd
  agents on macOS or Scheduled Tasks on Windows that auto-run /van-gogh:meeting-ingest
  and /van-gogh:ingest-workspace each morning. Use when the user says "uninstall van
  gogh", "remove the scheduler", "stop the background jobs", "disable
  meeting-ingest auto-run", "uninstall launchd agents", or "I don't want this
  running automatically anymore". Leaves the repo, config, and vault untouched
  — skills still work when invoked manually.
disable-model-invocation: true
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# /van-gogh:uninstall-van-gogh

Tear down the scheduled background jobs that Step 8 of `/van-gogh:install-van-gogh` set
up. This is the inverse of that step. Do not touch the repo, `config.json`,
`.env`, or the user's vault — only the scheduler.

Walk the user through every step. Ask before destructive actions. Verify after.

The scheduler logic lives in `app/scheduler_setup.py` — it detects the OS
itself (launchd on macOS, Task Scheduler on Windows, nothing on Linux) and uses
the same label/task-name constants the installer used.

---

## Step 1 — Show what's installed

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" status
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scheduler_setup.py" status
```

Show the user the output. If it prints `UNSUPPORTED` (Linux), tell the user
Project Van Gogh's installer doesn't set up a Linux scheduler — there's nothing
for this skill to remove; stop. If every job reports `not installed`, tell the
user the scheduler isn't installed and skip to Step 3. Note the `logs_dir:`
line — Step 3 uses it.

---

## Step 2 — Remove the scheduler

Ask y/n before proceeding. On yes:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" uninstall
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scheduler_setup.py" uninstall
```

It prints `OK: removed ...` per job (or `NOT_INSTALLED` for anything already
absent). Verify by re-running `status` — every job should now report
`not installed`. If one still shows as installed, something didn't unload
cleanly — show the user and investigate before declaring success.

---

## Step 3 — Logs (optional)

The installer wrote scheduler output to the `logs/` folder inside the vault's
`van-gogh/` directory — the `logs_dir:` path printed by `status` in Step 1
(don't assume the path). Ask the user y/n whether to delete the scheduler log
files. Default keep (they're useful if the user reinstalls and wants to
compare). On yes, set `LOGS_DIR` to the path `status` printed, then delete:

macOS / Linux (bash/zsh):
```bash
LOGS_DIR="<logs_dir from status output>"
rm -f "$LOGS_DIR/meeting-ingest.log" "$LOGS_DIR/meeting-ingest.err" \
      "$LOGS_DIR/granola-ingest.log" "$LOGS_DIR/granola-ingest.err"  # legacy name
rm -f "$LOGS_DIR/ingest-workspace.log" "$LOGS_DIR/ingest-workspace.err"
```

Windows (PowerShell):
```powershell
$LOGS_DIR = "<logs_dir from status output>"
Remove-Item -Force -ErrorAction SilentlyContinue "$LOGS_DIR\meeting-ingest.log", "$LOGS_DIR\meeting-ingest.err", "$LOGS_DIR\granola-ingest.log", "$LOGS_DIR\granola-ingest.err"
Remove-Item -Force -ErrorAction SilentlyContinue "$LOGS_DIR\ingest-workspace.log", "$LOGS_DIR\ingest-workspace.err"
```

**Do not** delete the rest of `van-gogh/` — config.json, the briefings, and the
projects/ memory are the user's data. This skill only removes the scheduler and,
optionally, its own log files.

---

## Step 4 — Confirm

Tell the user:

```
Scheduler removed. /van-gogh:meeting-ingest and /van-gogh:ingest-workspace still work when you
invoke them manually, they just won't run automatically each morning.

To re-enable scheduling later, re-run Step 8 of /van-gogh:install-van-gogh.
```
