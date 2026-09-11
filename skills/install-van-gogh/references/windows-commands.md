# Windows (PowerShell) command equivalents

Every shell command in SKILL.md is shown there in its macOS/Linux (bash) form.
This file holds the PowerShell equivalent for each, keyed by the same step
numbers. On Windows, read this file once at the start of the install and use
these forms.

## Step 1 — Prerequisites

Detect the OS: `python3 -c "import sys; print(sys.platform)"` works as-is (or
`python` if `python3` is not aliased).

Check what's present:

```powershell
foreach ($c in 'winget','claude') { "$c => " + ((Get-Command $c -ErrorAction SilentlyContinue).Source) }
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { python -c "import sys; print('python', '.'.join(map(str, sys.version_info[:3])), 'OK' if sys.version_info >= (3,10) else 'TOO_OLD')" } else { 'python MISSING' }
```

Installer: Python `winget install --id Python.Python.3.12 -e`. (brew is
macOS-only; skip it.)

## Step 2 — Python dependencies

Create the venv and install deps:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.config\van-gogh" | Out-Null; python -m venv "$HOME\.config\van-gogh\venv"; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -m pip install -q -r "$env:CLAUDE_PLUGIN_ROOT\requirements.txt"
```

Verify:

```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import requests, dotenv, google.oauth2, googleapiclient, msal; print('ok')"
```

## Step 3 — Connect your accounts

Ensure `.env` exists:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.config\van-gogh" | Out-Null; if (-not (Test-Path "$HOME\.config\van-gogh\.env")) { Copy-Item "$env:CLAUDE_PLUGIN_ROOT\.env.template" "$HOME\.config\van-gogh\.env" }
```

Google sign-in (per account):

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider google
```

Headless follow-up (pasted redirect URL):

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider google --redirect-response "<pasted_url>"
```

Relabel an account:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider google --relabel <auto_label> <chosen_label>
```

Microsoft sign-in (foreground, never background):

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider microsoft
```

Device-code phase 2:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider microsoft --complete-device-code
```

Fetch the Outlook Sent folder ID:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from microsoft_client import microsoft_client; print(microsoft_client('<MS_LABEL>').get('/me/mailFolders/sentitems')['id'])"
```

## Step 4 — Your details

Check the conventional vault location first:

```powershell
$v = Join-Path $HOME 'Documents\van-gogh-vault'; $legacy = Join-Path $HOME 'Documents\vault'; if (Test-Path $v) { "FOUND: $v" } elseif (Test-Path $legacy) { "FOUND: $legacy" } else { 'NOT_FOUND' }
```

Folder picker fallback:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\pick_vault.py"
```

Migration helper:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\migrate_to_vault.py" --vault "<VAULT_PATH>"
```

Copy the config template into the vault:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; $VG_CONFIG = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')").Trim(); if (-not (Test-Path $VG_CONFIG)) { Copy-Item config.template.json $VG_CONFIG }
```

## Step 5 — Meeting notetaker (optional)

Write the API key (BOM-safe writer):

```powershell
$env:VG_ENV_VALUE = "<pasted_key>"; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\user_state.py" set-env <ENV_KEY>   # GRANOLA_API_KEY or GRAIN_API_KEY
```

Verify:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\meeting_fetch.py" --check
```

## Step 6 — Scaffold vault + smoke test

Scaffold:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; $VAULT = (& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import vault; print(vault())").Trim(); & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\vault\scaffold.py" "$VAULT"
```

Config verification:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import cfg, vault, van_gogh_root, primary_account; print('vault:', vault()); print('van-gogh root:', van_gogh_root()); print('config found via pointer:', (primary_account() or {}).get('email')); print('config OK')"
```

Client smoke tests:

```powershell
cd $env:CLAUDE_PLUGIN_ROOT
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\google_client.py" --label <PRIMARY_LABEL>
# if secondary Google:
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\google_client.py" --label <SECONDARY_LABEL>
# if Outlook:
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\microsoft_client.py" --label <MS_LABEL>
```

## Step 8 — Schedule background tasks

```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scheduler_setup.py" install
```
