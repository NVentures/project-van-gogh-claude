---
name: update-van-gogh
description: Ingest or refresh a personal Van Gogh key file — the small JSON file the Van Gogh team sends to users who get a private companion plugin (custom skills made just for them). Use when the user types /van-gogh:update-van-gogh, says they received a Van Gogh key file, a new key, a replacement key, or that their custom skills stopped updating.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
---

# Update Van Gogh (companion key)

Some users receive a **private companion plugin** — custom skills built just
for them, delivered from a private repository. Access comes as a small JSON
**key file** the Van Gogh team sends directly. This skill ingests that file:
first time (installs the companion plugin) or as a replacement key (rotates
the credential in place). The script does everything; the key's secret never
appears in the conversation.

## Step 1 — Get the key file path

Ask the user where they saved the key file (a `.json` file, usually from an
email attachment). Do not ask them to paste its contents — the path is all
you need, and pasting would put the secret in the chat.

## Step 2 — Run the ingester

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/companion_key.py" rekey --file "/path/to/key.json"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\companion_key.py" rekey --file "C:\path\to\key.json"
```

`rekey` handles both cases: on a machine that never had the companion it
falls through to a full install; on an existing install it rotates the
stored credential and verifies with a fetch.

## Step 3 — Render the result

Read the JSON it prints and say the true thing, past tense:

- `{"ok": true, "mode": "ingest", ...}` — the companion plugin was installed.
  Their personal skills are available as `/van-gogh-custom:<name>` (a fresh
  Claude Code session may be needed before they appear). Suggest deleting the
  key file from Downloads/email now that it is stored.
- `{"ok": true, "mode": "rekey", ...}` — the new key was stored and verified;
  updates to their personal skills will flow again. Suggest deleting the file.
- `{"ok": false, ...}` — report the `error` and `hint` verbatim (they are
  already redacted). Do not retry in a loop; a bad token needs a new key file
  from the Van Gogh team.

Never print, echo, or cat the key file's contents.
