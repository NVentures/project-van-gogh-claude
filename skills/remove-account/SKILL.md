---
name: remove-account
description: Remove a configured email/calendar account from Project Van Gogh.
  Use when the user says "remove an account", "delete my work email", "take off
  the Outlook account", "drop a Gmail", or anything that means deleting an
  account from config.json. Presents the configured accounts, removes the chosen
  one from the accounts list, optionally cleans its OAuth refresh token from
  .env, and (if the removed account was primary) prompts for a new primary.
  Handles backup and JSON validation.
disable-model-invocation: true
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Remove Account

Your job: walk the user through removing one email/calendar account. You show
them the configured accounts, they pick one, you remove it from the vault
`config.json` accounts list, optionally clean its refresh token from
`~/.config/van-gogh/.env`, and if the removed account was the primary you have them pick a
new primary. Back up and validate before writing, then sync the vault.

The accounts schema is the N-account list in `config.template.json`: each entry
is `{ "provider": "google|microsoft", "email": "...", "label": "...",
"is_primary": true|false, "sent_folder_id": "" }`. Exactly one account has
`is_primary: true`. See `config_loader.py` (`accounts()`, `primary_account()`)
for how scripts read it.

## Python runtime

## How to run the skill

### 1. Confirm intent and read the current accounts

Ask the user to confirm they want to remove an account (skip the confirmation if
they already named one, e.g. "remove my work Gmail").

`config.json` lives in the user's vault at `{vault}/van-gogh/config.json`, never
the repo. Resolve its path and the configured accounts in one step.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os,json; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root, accounts; print('CONFIG:', van_gogh_root() / 'config.json'); print(json.dumps(accounts(), indent=2))"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os,json; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root, accounts; print('CONFIG:', van_gogh_root() / 'config.json'); print(json.dumps(accounts(), indent=2))"
```

### 2. Present the accounts and let the user pick one

List every configured account as a numbered choice showing its **label**,
**provider**, and **email**, marking the primary (e.g. `(primary)`):

> Which account do you want to remove?
> 1. **Gmail**, google, you@gmail.com (primary)
> 2. **Work**, google, you@work.com
> 3. **Outlook**, microsoft, you@company.com

Use `AskUserQuestion` (or a numbered prompt) to get the selection. Note whether
the chosen account `is_primary` and its `label`, you need both below.

**Guard the last account.** If there is only one account configured, do **not**
remove it: tell the user Project Van Gogh needs at least one account, and stop
here (no edit). Suggest
`/van-gogh:add-account` first if they meant to swap.

### 3. Remove the account from config.json

1. **Back up first.** Copy the vault `config.json` to `config.json.bak` beside it
   (in `van-gogh/`, overwriting any prior backup).

   macOS / Linux (bash/zsh):
   ```bash
   VG=$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root())")
   cp "$VG/config.json" "$VG/config.json.bak"
   ```

   Windows (PowerShell):
   ```powershell
   $VG = (cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root())")
   Copy-Item "$VG/config.json" "$VG/config.json.bak" -Force
   ```

2. **Edit the accounts list.** Use the Read tool on the vault `config.json`, then
   the Edit tool to delete the one account object the user chose (matched by its
   `email`/`label`). Make a minimal, targeted edit, remove only that one entry
   from the `accounts` array; never rewrite the whole file. Preserve key order
   and 2-space indentation. Leave the trailing comma situation valid (no dangling
   comma).

3. **Validate the JSON parses** (substitute the resolved vault path):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import json; json.load(open(r'{vault}/van-gogh/config.json', encoding='utf-8'))"
   ```
   PowerShell uses the venv python the same way (`& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c ...`). If it fails,
   restore from the backup and tell the user what broke:

   macOS / Linux: `cp "$VG/config.json.bak" "$VG/config.json"`
   Windows: `Copy-Item "$VG/config.json.bak" "$VG/config.json" -Force`

### 4. Optionally clean the OAuth refresh token from .env

The account's refresh token lives in the state `.env` at
`~/.config/van-gogh/.env` as
`GOOGLE_REFRESH_TOKEN_<LABEL>` (google) or `MS_GRAPH_REFRESH_TOKEN_<LABEL>`
(microsoft), where `<LABEL>` is the account's label uppercased with spaces and
hyphens turned into underscores, exactly how `google_client.py` /
`microsoft_client.py` compute `_label_suffix(label)`:
`label.strip().upper().replace(" ", "_").replace("-", "_")`.

Ask the user whether to also remove the stored token (default yes, a leftover
token is harmless but stale). If yes, do the `.env` edit with the cross-platform
Python helper below (it rewrites `.env` with `encoding="utf-8"`, dropping only
the matching `KEY=` line, no BOM, no shell quoting traps, identical on every
OS). Substitute the chosen account's `provider` and `label`:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "
import sys
from pathlib import Path
provider, label = sys.argv[1], sys.argv[2]
suffix = label.strip().upper().replace(' ', '_').replace('-', '_')
prefix = 'GOOGLE_REFRESH_TOKEN_' if provider == 'google' else 'MS_GRAPH_REFRESH_TOKEN_'
key = prefix + suffix
env = Path.home() / '.config' / 'van-gogh' / '.env'
if env.exists():
    lines = env.read_text(encoding='utf-8').splitlines()
    kept = [ln for ln in lines if not ln.strip().startswith(key + '=')]
    env.write_text('\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8')
    print('removed' if len(kept) != len(lines) else 'no matching line', key)
else:
    print('no .env file')
" "<provider>" "<label>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "
import sys
from pathlib import Path
provider, label = sys.argv[1], sys.argv[2]
suffix = label.strip().upper().replace(' ', '_').replace('-', '_')
prefix = 'GOOGLE_REFRESH_TOKEN_' if provider == 'google' else 'MS_GRAPH_REFRESH_TOKEN_'
key = prefix + suffix
env = Path.home() / '.config' / 'van-gogh' / '.env'
if env.exists():
    lines = env.read_text(encoding='utf-8').splitlines()
    kept = [ln for ln in lines if not ln.strip().startswith(key + '=')]
    env.write_text('\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8')
    print('removed' if len(kept) != len(lines) else 'no matching line', key)
else:
    print('no .env file')
" "<provider>" "<label>"
```

Use the chosen account's actual `provider` and `label` as the two arguments. The
helper is identical on both shells; only the interpreter path syntax differs
(`"$HOME/.config/van-gogh/venv/bin/python" -c` vs
`& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c`).

### 5. If the removed account was primary, pick a new primary

If the account you removed had `is_primary: true`, the list now has **no**
primary. Read back the remaining accounts (step 1's command) and ask the user to
pick which one should become primary. Then Edit the vault `config.json` to set
that account's `is_primary` to `true` (the others stay `false`), exactly one
`true` across the whole list. Validate the JSON parses again (step 3.3).

If the removed account was **not** primary, skip this step, the existing primary
is unchanged.

### 6. Confirm

Tell the user in one line what was removed, whether the token was cleaned, the
new primary (if it changed), and that the backup is at
`van-gogh/config.json.bak`. Don't dump the whole file.

## Rules

- **Never** remove the last remaining account, Project Van Gogh needs at least
  one. Stop and explain (step 2).
- **Always** back up to `config.json.bak` before editing, and validate the JSON
  parses after each write; restore from the backup if it fails.
- **Never** rewrite the whole `config.json`, delete only the one chosen account
  entry with a targeted Edit.
- **Always** keep exactly one account `is_primary: true`. If you removed the
  primary, you must set a new one before finishing.
- Editing `.env` is optional and only ever **removes** the one matching
  `*_REFRESH_TOKEN_<LABEL>` line, never touch other keys.
- Don't touch `config.template.json`.
