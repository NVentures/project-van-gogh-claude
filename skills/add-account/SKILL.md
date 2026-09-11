---
name: add-account
description: Connect an additional email / calendar account (Google or Microsoft)
  to Project Van Gogh and append it to config.json. Use when the user says "add an
  account", "connect another email", "add my work Gmail", "add a second Outlook",
  "hook up another inbox", or "I have a new email I want briefings to cover". Runs
  the OAuth sign-in, handles the IT-policy denial case, appends the account to the
  config accounts list, and optionally re-designates the primary account.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# /van-gogh:add-account

Connect one more Google or Microsoft account and register it in `config.json`.
This reuses the exact OAuth flow from `/van-gogh:install-van-gogh` Step 3 and appends the
new account (as `is_primary: false`) to the `accounts` list in the **vault**
`config.json`. At the end it offers to designate a new primary.

Do this one account per invocation. To add several, run the skill again.

## Python runtime

---

## Step 1: Which provider?

**Ask with the AskUserQuestion tool** so the choices are clickable (not a typed
reply). Single question, header `Provider`:

> "What kind of account do you want to add?" Options: **Google** (Gmail),
> **Microsoft** (Outlook / Microsoft 365).

Wait for the answer, then go straight to Step 2. Don't ask anything else first.

Make sure `.env` exists (the OAuth token is written there, it lives in the
per-user state dir at `~/.config/van-gogh/.env`; the template still ships in the
plugin cache).

**macOS / Linux (bash):**

```bash
mkdir -p "$HOME/.config/van-gogh" && { [ -f "$HOME/.config/van-gogh/.env" ] || cp "${CLAUDE_PLUGIN_ROOT}/.env.template" "$HOME/.config/van-gogh/.env"; }
```

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.config\van-gogh" | Out-Null; if (-not (Test-Path "$HOME\.config\van-gogh\.env")) { Copy-Item "$env:CLAUDE_PLUGIN_ROOT\.env.template" "$HOME\.config\van-gogh\.env" }
```

---

## Step 2: Sign in (OAuth)

This is the same flow as `/van-gogh:install-van-gogh` Step 3. Substitute `google` or
`microsoft` for `<provider>` based on Step 1.

### Google

**macOS / Linux (bash):**

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider google
```

**Windows (PowerShell):**

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider google
```

Read the script output and **branch on which marker block appears**:

- **`===VANGOGH_AUTH===`**, a browser opened, the user signed in, login is done.
  Read `email` / `label` from the block and continue to Step 3.
- **`===VANGOGH_HEADLESS_URL===`**, no browser (web/cloud session). The block
  contains a sign-in URL. Present it and tell the user: "Open this, sign in, and
  you'll land on a 'site can't be reached' page, that's expected. Copy the full
  URL from your address bar and paste it back here." Collect the pasted URL, then:

  **macOS / Linux (bash):**

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider google --redirect-response "<pasted_url>"
  ```

  **Windows (PowerShell):**

  ```powershell
  cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider google --redirect-response "<pasted_url>"
  ```

  That second run prints the `===VANGOGH_AUTH===` block. Continue to Step 3 with
  its `email` / `label`.

The success block looks like:

```
===VANGOGH_AUTH===
provider=google
email=you@example.com
label=Example
env_var=GOOGLE_REFRESH_TOKEN_EXAMPLE
===END===
```

### Microsoft

Run in the **foreground** (never background).

**macOS / Linux (bash):**

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider microsoft
```

**Windows (PowerShell):**

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider microsoft
```

Read its output and **branch on the marker block**:

- **`===VANGOGH_AUTH===`**, a browser opened and login is done. Continue to Step 3.
- **`===VANGOGH_DEVICE_CODE===`**, no browser (web/cloud session). The block
  contains `verification_uri` and `user_code`. **Immediately and prominently show
  the user both**, this is the step they need to act on:

  > "Open **`<verification_uri>`** in your browser and enter the code
  > **`<user_code>`**, then sign in with your Microsoft/Outlook account."

  Then run phase 2 (it polls and returns once they approve, leave it running in
  the foreground):

  **macOS / Linux (bash):**

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider microsoft --complete-device-code
  ```

  **Windows (PowerShell):**

  ```powershell
  cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider microsoft --complete-device-code
  ```

  It prints the `===VANGOGH_AUTH===` block when done.

### IT-policy denial: STOP, don't write a half account

Some org accounts block third-party apps at the tenant level. If sign-in fails
with an **IT-policy / admin-consent** error, the token was **not** saved. Do
**not** append anything to `config.json`, abort cleanly so no half account is
written.

Recognise it by the error text in the script output (distinct from a transient
network error like a timeout, DNS failure, or `Host not in allowlist`):

- **Microsoft:** AADSTS codes such as `AADSTS65001` (no consent / admin consent
  required), `AADSTS90094` (admin approval required), `AADSTS650056`
  (misconfigured app / admin must consent), `AADSTS7000112` (app disabled by
  tenant), or any message containing "admin consent", "need admin approval",
  "blocked by your organization", "Conditional Access", or "tenant policy".
- **Google:** `access_denied` with "admin", "organization policy", "blocked this
  app", or "hasn't completed the Google verification process" framed as an org
  restriction.

When you see one of these, tell the user plainly and stop:

> "Your organization's IT policy is blocking Project Van Gogh from connecting
> **`<email>`**. The sign-in itself worked, but your admin has to approve
> third-party app access before I can finish. Please ask your IT department to
> approve the permission, then run `/van-gogh:add-account` again. I haven't changed
> anything, your config is untouched."

Then end the skill. Do not run Steps 3 or 4.

A genuine **network / proxy** failure (timeout, offline, `403 Host not in
allowlist`) is different: that is not an IT-policy block. For Microsoft
specifically, if sign-in succeeded but only the later sent-folder lookup fails
with a network error, keep going (Step 3 tolerates a blank `sent_folder_id`).

---

## Step 3: Label and (Microsoft) sent folder

**Propose a short label** from the email and confirm it with the AskUserQuestion
tool (header `Label`):

- `@gmail.com` → propose **Gmail** (or **Personal** if Gmail is taken)
- company domain → derive a clean name (e.g. `@northwindpartners.com` →
  **Northwind**, `@qxcorp.com` → **QX**)

Question: "Signed in as `<email>`. What should I label this account?" Offer 2-3
candidates (best first, marked "(Recommended)"); "Other" lets them type one. The
label must be **unique** across the existing accounts (it is the OAuth token
suffix). If they pick a label different from the one `auth_bootstrap` chose:

**macOS / Linux (bash):**

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider <provider> --relabel <auto_label> <chosen_label>
```

**Windows (PowerShell):**

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\auth_bootstrap.py" --provider <provider> --relabel <auto_label> <chosen_label>
```

**For Microsoft only**, fetch the Sent folder id (substitute the actual label):

**macOS / Linux (bash):**

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "
import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app'))
from microsoft_client import microsoft_client
print(microsoft_client('<MS_LABEL>').get('/me/mailFolders/sentitems')['id'])
"
```

**Windows (PowerShell):**

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from microsoft_client import microsoft_client; print(microsoft_client('<MS_LABEL>').get('/me/mailFolders/sentitems')['id'])"
```

Save that string for config.json. **If this call fails with a network / proxy
error** (e.g. `403` / `Host not in allowlist`, some cloud sandboxes block
`graph.microsoft.com` even though sign-in succeeded), the **token is still saved
and valid**: leave `sent_folder_id` blank (scripts resolve it at runtime) and
tell the user to re-run this one command later from the desktop app. Do **not**
treat it as a failure. (For Google, `sent_folder_id` stays `""`.)

---

## Step 4: Append to config.json

`config.json` lives in the user's **vault** at `{vault}/van-gogh/config.json` -
never a repo-root copy. Resolve its path:

**macOS / Linux (bash):**

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
```

**Windows (PowerShell):**

```powershell
cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
```

**Read** that file with the Read tool. The `accounts` value is a **list** of
account objects (Item 1 schema):

```json
"accounts": [
  { "provider": "google", "email": "you@gmail.com", "label": "Gmail", "is_primary": true, "sent_folder_id": "" }
]
```

1. **Back up first.** Copy the vault `config.json` to `config.json.bak` beside it
   (in `van-gogh/`, overwriting any prior backup).

   **macOS / Linux (bash):**

   ```bash
   cp "<CONFIG_PATH>" "<CONFIG_PATH>.bak"
   ```

   **Windows (PowerShell):**

   ```powershell
   Copy-Item "<CONFIG_PATH>" "<CONFIG_PATH>.bak" -Force
   ```

2. **Append the new account** to the `accounts` list using the Edit tool (a
   minimal, targeted edit, never rewrite the whole file; preserve key order and
   2-space indentation). The new entry is always `is_primary: false`:

   ```json
   { "provider": "<provider>", "email": "<email>", "label": "<label>", "is_primary": false, "sent_folder_id": "<folder_id_or_blank>" }
   ```

   Add a comma to the previously-last entry so the JSON stays valid. For Google,
   `sent_folder_id` is `""`.

3. **Validate the JSON parses** (substitute the resolved path):

   **macOS / Linux (bash):**

   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import json; json.load(open(r'<CONFIG_PATH>', encoding='utf-8'))" && echo OK
   ```

   **Windows (PowerShell):**

   ```powershell
   cd $env:CLAUDE_PLUGIN_ROOT; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import json; json.load(open(r'<CONFIG_PATH>', encoding='utf-8'))"; echo OK
   ```

   If it fails, **restore from the backup** and tell the user what broke:

   **macOS / Linux (bash):**

   ```bash
   cp "<CONFIG_PATH>.bak" "<CONFIG_PATH>"
   ```

   **Windows (PowerShell):**

   ```powershell
   Copy-Item "<CONFIG_PATH>.bak" "<CONFIG_PATH>" -Force
   ```

---

## Step 5: Designate a new primary? (optional)

Exactly one account in the list has `is_primary: true`. The account you just
added is `false`. Ask the user with the AskUserQuestion tool (header `Primary`):

> "Want to make **`<new_email>`** your primary account instead of the current
> primary (**`<current_primary_email>`**)?" Options: **Keep current**, **Make
> the new account primary**, or pick from the full list if there are more than
> two accounts.

- **Keep current** → no change.
- **Make a different account primary** → with the Edit tool, set the chosen
  account's `is_primary` to `true` and the previously-primary one to `false`.
  Keep the invariant: **exactly one `true`**. Re-run the JSON validation from
  Step 4.3, restoring from backup on failure.

Confirm in one line what changed (account added, label, and whether the primary
moved), and note the backup at `van-gogh/config.json.bak`. Don't dump the file.

---

## Rules

- **One account per run.** To add more, invoke `/van-gogh:add-account` again.
- **Never** write `config.json` without backing up to `config.json.bak` first,
  and always validate the JSON parses after, restore from the backup on failure.
- **Never** rewrite the whole file when appending, use a targeted Edit.
- **The new account is always `is_primary: false`.** Only Step 5 changes the
  primary, and the list must always have exactly one `true`.
- **Labels are unique** across all accounts (the OAuth token suffix).
- On an **IT-policy / admin-consent** denial, abort before Step 3, write nothing
  and tell the user to contact IT. Don't confuse it with a network failure.
- Edit only the **vault** `config.json`, never a repo-root copy or
  `config.template.json`.
