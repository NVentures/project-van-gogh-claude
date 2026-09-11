---
name: install-van-gogh
description: First-time setup for Project Van Gogh — Python venv, OAuth
  account connection, vault pointer + config, optional notetaker key, vault
  scaffold, and background job scheduling. Use when the user says "install
  van gogh", "set up van gogh", or runs first-time setup.
disable-model-invocation: true
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# /van-gogh:install-van-gogh

First-time setup for Project Van Gogh. Work through the steps in order. Each
step either runs automatically or asks for one thing — not both.

**The Claude desktop app is the smoothest place to run this** — the account
login steps open a browser on your computer automatically. It also works from a
web / cloud session: when no browser is available, the login switches to a
copy-paste flow (open a URL, paste the redirect URL back) — see Step 3.

**Windows:** commands below are in macOS/Linux (bash) form. On Windows, read
[references/windows-commands.md](references/windows-commands.md) once at the
start — it holds the PowerShell equivalent for every command, keyed by the same
step numbers — and use those forms throughout.

---

## Step 1 — Prerequisites

Detect the OS with `python3 -c "import sys; print(sys.platform)"`, then check
what's present:

```bash
which brew; which claude
python3 -c "import sys; print('python', '.'.join(map(str, sys.version_info[:3])), 'OK' if sys.version_info >= (3,10) else 'TOO_OLD')" 2>/dev/null || echo "python MISSING"
```

Install anything missing — **do not ask y/n, just do it** and report what you
installed:

- **brew** (macOS only): `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` then `eval "$(/opt/homebrew/bin/brew shellenv)"`
- **Python** (>= 3.10, required to create the venv in Step 2): install only if the check
  above printed `MISSING` or `TOO_OLD`; a newer pre-installed Python passes — don't
  downgrade it. Install via the **system package manager**:
  - **macOS:** `brew install python@3.12` then `brew link python@3.12` (and ensure
    it's on PATH via `eval "$(/opt/homebrew/bin/brew shellenv)"`).
  - **Windows:** `winget install --id Python.Python.3.12 -e`
  - **Linux:** use the distro package manager (e.g. `apt install python3`); if none
    is detectable, flag it to the user rather than guessing.
- **claude**: don't install it (you're already running inside it), but `which
  claude` must resolve — several scripts shell out to `claude -p` for Haiku
  classification, and the Step 8 scheduler resolves it from PATH. If it's
  empty, the binary isn't on PATH: tell the user to add it (or symlink the desktop
  app's CLI) before the scheduled jobs will work.

  Once it resolves, bring it up to date. **Do not ask, just run it**:

  ```bash
  claude update && claude --version
  ```
  ```powershell
  claude update; claude --version
  ```

  A CLI more than a few versions old is rejected by the API for current models
  ("Claude Code X does not support this model; version Y or newer is
  required"), which fails every briefing on the machine. From here on Van Gogh
  keeps it current on its own (`app/claude_update.py`, once a day before any
  scheduled run), so this is the only time you do it by hand.

git and the GitHub CLI are deliberately **not** checked here. The marketplace
repo is public, so nothing needs a GitHub sign-in, and reaching this skill at
all means `/plugin marketplace add` already cloned it over git — a check could
only ever pass. No external Gmail/Outlook CLI tools are required either —
Project Van Gogh uses direct OAuth clients instead.

---

## Step 2 — Python dependencies

The venv lives in the per-user state dir `~/.config/van-gogh/` (outside the
plugin cache, so plugin updates never delete it — see
[`_shared/python-runtime.md`](../_shared/python-runtime.md)). The dependencies
still come from the plugin's `requirements.txt`.

```bash
mkdir -p "$HOME/.config/van-gogh" && python3 -m venv "$HOME/.config/van-gogh/venv" && "$HOME/.config/van-gogh/venv/bin/python" -m pip install -q -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
```

Verify:

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import requests, dotenv, google.oauth2, googleapiclient, msal; print('ok')"
```

---

## Step 3 — Supply the OAuth app credentials

Van Gogh signs in with **your own** Google and Microsoft app registrations. Two
files, supplied once here and stored per-user in `~/.config/van-gogh/`:

| File | What it is | Where it comes from |
|---|---|---|
| `google_client_secret.json` | Google **Desktop app** OAuth client | Google Cloud Console → APIs & Services → Credentials → Create credentials → OAuth client ID → **Desktop app** → Download JSON |
| `ms_client_secret.json` | Microsoft **public client** app id | Entra admin centre → App registrations → New registration → (public client / mobile-desktop, redirect `http://localhost`) → copy the Application (client) ID |

The Microsoft file is one the user writes by hand, and carries no secret — a
public client has none:

```json
{ "client_id": "<Application (client) ID>", "tenant_id": "common" }
```

**Check what is already there before asking for anything:**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --status
```
Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\install_oauth_credentials.py" --status
```

Read the JSON and branch on `needs_google` / `needs_microsoft`. `false` means
the user has already supplied a valid file — say so in one line and skip that
provider. `true` means ask for it. `source` tells you why: `"user"` is supplied,
`"bundled"` is a leftover shipped copy that must be replaced, `"missing"` is
nothing at all.

**For each provider still needed,** ask the user to either give you the path to
the downloaded file or paste its contents. Then hand it to the installer, which
auto-detects which provider it is — never ask them to tell you:

```bash
# they gave a path
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --file "<path they gave>"
# they pasted the contents
# Heredoc, not `printf '<json>' |`: a command-line argument is visible to every
# local user in `ps` output, and this one carries the client secret.
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --stdin <<'VANGOGH_JSON'
<pasted JSON>
VANGOGH_JSON
```

Windows (PowerShell): same two commands with
`& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\install_oauth_credentials.py"`.
For the paste form use a here-string, which keeps the secret out of the command
line exactly as the heredoc does:

```powershell
@'
<pasted JSON>
'@ | & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\install_oauth_credentials.py" --stdin
```

**Prefer the `--file` form when the user has the download on disk.** It never
puts the credential through a shell at all.

**Branch on the machine-readable result, never on prose:**

- `"ok": true` — installed. It reports `provider`, `path` and `replaced`. Confirm
  in one line: "Google credentials installed." Do not echo `client_id`, and never
  echo the file contents.
- `"ok": false` — nothing was written. Show the `error` verbatim, ask for a
  corrected file, and try again. The usual causes are pasting a fragment instead
  of the whole file (the `hint` says so) or handing over a Google **web** client
  where a Desktop one is needed.

If the user has no Microsoft account at all, skip `ms_client_secret.json`
entirely — it is only read when an Outlook account is connected.

Re-run `--status` once both are in. Do not start sign-in until `needs_google` is
`false` (and `needs_microsoft` too, if they have Outlook): signing in against a
missing or stale app credential fails deep inside the OAuth exchange with an
error that reads like a network fault.

---

## Step 3b — Connect your accounts

Now sign in. This grants Van Gogh access to the user's mailboxes using the app
registration from Step 3.

**Ask with the AskUserQuestion tool** so the choices appear as clickable options
(not a typed reply). Ask both in a single call:

1. Header `Gmail` — "How many Gmail accounts do you have?" Options: `1`, `2`, `3`.
2. Header `Microsoft` — "How many Outlook / Microsoft 365 accounts do you have?" Options: `None`, `1`, `2`.

The tool always adds an "Other" choice for anyone outside these ranges. Wait for
the answer, then immediately start logging in. **Do not ask anything else before
running the login command.**

Make sure `.env` exists. Tokens live in the per-user state dir at
`~/.config/van-gogh/.env` (the template still ships in the plugin cache).

```bash
mkdir -p "$HOME/.config/van-gogh" && { [ -f "$HOME/.config/van-gogh/.env" ] || cp "${CLAUDE_PLUGIN_ROOT}/.env.template" "$HOME/.config/van-gogh/.env"; }
```

**For each Google account**, run:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider google
```

The script auto-detects the environment. **Read its output and branch on which
marker block appears:**

- **`===VANGOGH_AUTH===`** — a browser opened, the user signed in, and login is
  done. Read `email` / `label` from the block and continue below.
- **`===VANGOGH_HEADLESS_URL===`** — no browser (web/cloud session). The block
  contains a sign-in URL. Present that URL to the user and tell them: "Open this,
  sign in, and you'll land on a 'site can't be reached' page — that's expected.
  Copy the full URL from your address bar and paste it back here." Collect the
  pasted URL (a plain reply is fine), then finish the exchange:

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider google --redirect-response "<pasted_url>"
  ```

  That second run prints the `===VANGOGH_AUTH===` block. Continue below with its
  `email` / `label`.

The success block looks like:

```
===VANGOGH_AUTH===
provider=google
email=you@example.com
label=Example
env_var=GOOGLE_REFRESH_TOKEN_EXAMPLE
===END===
```

Read the `email` from the block and **propose a short label** based on it:
- `@gmail.com` → propose **Gmail** (or Personal if there's a second Google account)
- company domain → derive a clean name (e.g. `@northwindpartners.com` → **Northwind**, `@qxcorp.com` → **QX**)

Confirm the label with the AskUserQuestion tool (header `Label`): question
"Signed in as `<email>`. What should I label this account?" Offer 2-3 candidate
labels derived from the email, best pick first with "(Recommended)" on it (e.g.
for `you@northwindpartners.com`: `Northwind` (Recommended), `Northwindpartners`);
"Other" lets them type a custom one. If they choose a label different from the
auto-derived one:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider google --relabel <auto_label> <chosen_label>
```

Record each `(email, label)` pair. First Google account = primary, second = secondary.

### If OAuth fails (applies to every account, Google and Microsoft)

If a login run doesn't end in `===VANGOGH_AUTH===`, read
[references/oauth-failure-handling.md](references/oauth-failure-handling.md)
before responding. Short version: an IT-policy / admin-consent denial (AADSTS
consent codes, "blocked by your organization", "admin approval") means **skip
that account, tell the user to ask IT, and keep installing** — never abort; a
transient network/timeout error means **retry the same command once** before
asking the user anything. The reference file has the full marker lists and the
exact wording to use.

**For Outlook** (if they said yes). Run in the **foreground** (never background):

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider microsoft
```

**Read its output and branch on the marker block:**

- **`===VANGOGH_AUTH===`** — a browser opened and login is done. Continue below.
- **`===VANGOGH_DEVICE_CODE===`** — no browser (web/cloud session). The block
  contains `verification_uri` and `user_code`. **Immediately and prominently show
  the user both** — do not wait silently, this is the step they need to act on:

  > "Open **`<verification_uri>`** in your browser and enter the code
  > **`<user_code>`**, then sign in with your Microsoft/Outlook account."

  Then run phase 2, which waits for them to finish (it polls and returns once
  they approve — leave it running in the foreground):

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/auth_bootstrap.py" --provider microsoft --complete-device-code
  ```

  It prints the `===VANGOGH_AUTH===` block when done.

Same flow after either path: read email, propose label, relabel if needed.

Then auto-fetch the Outlook Sent folder ID (substitute the actual MS label):

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "
import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app'))
from microsoft_client import microsoft_client
print(microsoft_client('<MS_LABEL>').get('/me/mailFolders/sentitems')['id'])
"
```

Save that string for config.json. **If this call fails** — with either a
network / proxy error (`403` / `Host not in allowlist`) or an IT-policy error —
read the "Graph Sent-folder lookup failures" section of
[references/oauth-failure-handling.md](references/oauth-failure-handling.md):
the network case keeps the token (leave `outlook_sent_folder_id` blank and
continue); the policy case means the account can't be used until IT consents.
If the user skipped Outlook, leave Microsoft fields blank.

---

## Step 3c — Replacing a credential that has gone stale

App credentials do not last forever. A Google client secret can be rotated or
deleted in Cloud Console, an Entra registration can be removed or its tenant
policy changed, and either way **every** account signed in through that
registration stops working at once — not just one mailbox. This step is
re-runnable on its own: a user who hits it months later does not need the rest
of the install.

**The symptom.** Sign-in or a refresh fails with `invalid_client`,
`unauthorized_client`, `deleted_client`, or a Microsoft
`AADSTS700016` / `AADSTS7000215`. Distinguish it from the failure modes in
[`references/oauth-failure-handling.md`](references/oauth-failure-handling.md)
by scope: one account failing while others work is an account problem; **all**
accounts for one provider failing at once is the app credential.

**Confirm what is installed** (the same status call as Step 3):

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --status
```
Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\install_oauth_credentials.py" --status
```

Show the user the `client_id` on the failing provider and ask them to confirm it
still exists in the Console. A `client_id` that no longer appears there is the
whole diagnosis.

**Take the replacement.** Ask for the freshly downloaded file (Google) or the
new Application ID (Microsoft), in either form — a path or a paste — and run the
same installer as Step 3:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --file "<path they gave>"
# Heredoc, not `printf '<json>' |`: a command-line argument is visible to every
# local user in `ps` output, and this one carries the client secret.
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --stdin <<'VANGOGH_JSON'
<pasted JSON>
VANGOGH_JSON
```

`"replaced": true` in the result confirms it overwrote the stale file rather
than writing a second one somewhere. If `"ok": false`, show the `error` and ask
again — nothing was written, so the old credential is still in place and the
user is no worse off than before they pasted.

**Then re-authorize.** Replacing the app credential does **not** revive the
existing refresh tokens: they were issued by the old registration and are dead.
Re-run sign-in from Step 3b for every account on that provider. Tell the user
this up front so a second round of browser windows is expected, not alarming.

Leave the other provider alone. A stale Google client says nothing about the
Microsoft registration, and re-authorizing accounts that are working only risks
breaking them.

---

## Step 4 — Your details

Ask the user for everything at once in **one message**:

> "A few quick details to personalise your briefings:
> 1. Full name (used in email classification and meeting notes)
> 2. First name (how briefings address you)
> 3. Your role in one line, optional, e.g. 'energy CEO, solar, M&A'
> 4. Your main venture or project name (I'll add more later but need at least one)
> 5. The city and state/country you're based in (so briefings use your timezone)"

**Then set their timezone from the city/state they gave.** Briefings render every
meeting time and compute "today / tomorrow" windows against this zone, so it must
be the user's, not a default. Do NOT auto-detect the machine zone (unreliable on
Windows, and the laptop may be travelling). Instead, derive the IANA timezone from
the city and state/country the user gave in item 5, using your own knowledge
(e.g. "Austin, Texas" maps to `America/Chicago`; "Miami, Florida" to
`America/New_York`; "London, UK" to `Europe/London`; "Singapore" to
`Asia/Singapore`).

Then **confirm with the AskUserQuestion tool** (header `Timezone`): "Based on
`<city, state>`, your timezone is `<derived IANA name>`. Is that right?" Offer the
derived zone as the first option plus a couple of nearby/common IANA zones and an
"Other" option for the user to type any IANA name. If the location is ambiguous
(a state that spans two zones, e.g. parts of Florida, Indiana, or Tennessee), ask
which of the two zones applies rather than guessing. Save the confirmed IANA name
for `config.json` `user.timezone`.

Dual-timezone display (showing a second zone side by side, e.g. PT / ET) is
**off by default**. Only set `user.secondary_timezone` if the user explicitly
asks for a second zone; otherwise leave it `""` and briefings render the single
`user.timezone`.

**Then get the vault path.** Most users pick their vault with the folder
picker below, but check the conventional location first — a user who cloned a
vault repo there (or ran an older install) should not have to hunt for it:

```bash
if [ -d "$HOME/Documents/van-gogh-vault" ]; then echo "FOUND: $HOME/Documents/van-gogh-vault"
elif [ -d "$HOME/Documents/vault" ]; then echo "FOUND: $HOME/Documents/vault"
else echo "NOT_FOUND"; fi
```

(`~/Documents/vault` is the pre-0.18.4 default — checking it second keeps
older machines working without a picker detour.)

- **`FOUND: ...`** — a vault already sits at the conventional path. Use it as
  `<VAULT_PATH>` directly and confirm with the user: "Found a vault at
  `<the found path>` — using that one." Skip the picker.
- **`NOT_FOUND`** — the normal case on a fresh machine. Fall back to the native
  folder picker:

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/pick_vault.py"
  ```

  This opens an OS-native "choose folder" dialog (Finder on macOS, Explorer on
  Windows, zenity/kdialog on Linux). **Read its output and branch on the marker
  block:**

  - **`===VANGOGH_VAULT_PICK===`** — the user selected a folder. Read the `path=`
    line; that value is `<VAULT_PATH>`. Tell them which folder you'll use.
  - **`===VANGOGH_VAULT_CANCELLED===`** — they closed the dialog without choosing.
    Re-run the picker once. If they cancel again, ask them to type the path
    (default: `~/Documents/van-gogh-vault`) and use that as `<VAULT_PATH>`.
  - **`===VANGOGH_VAULT_NO_PICKER===`** — no GUI available (web/cloud session, or a
    Linux box with no picker binary). Fall back to asking them to type it: "Path to
    your vault folder (default: `~/Documents/van-gogh-vault`)". Use their
    reply as `<VAULT_PATH>`.

The vault is a **plain folder of markdown files** — the scripts read and write
it directly, and nothing in Project Van Gogh requires the Obsidian app.
Obsidian is entirely optional: its only purpose is a nicer way to *view* the
vault (backlinks, graph). Never tell the user to install or open Obsidian as
part of setup; if they ask, say they can point Obsidian (or any editor) at the
vault folder later.

All Project Van Gogh state — including `config.json` itself — lives in a
`van-gogh/` folder at the **root of the user's vault**, not in the repo.
First run the migration helper with the vault path from the picker. It creates
`{vault}/van-gogh/`, writes the vault pointer at `~/.config/van-gogh/vault-pointer`
that the scripts use to find the vault, moves any pre-existing repo-root state
into the vault, and seeds
`van-gogh/projects/Project Van Gogh/memory.md` from `memory.template.md` (only if
absent). It is **idempotent** — safe to re-run on an already-migrated machine (it
reports "already in vault" / "already present" and skips):

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/migrate_to_vault.py" --vault "<VAULT_PATH>"
```

Then copy the template into the **vault** location and write `config.json` there
(resolve the path via the loader so it's correct on every OS):

```bash
VG_CONFIG="$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')")" && { [ -f "$VG_CONFIG" ] || cp "${CLAUDE_PLUGIN_ROOT}/config.template.json" "$VG_CONFIG"; }
```

Populate all six blocks in one write to that vault `config.json`:

- `user`: fill from answers above. Set `name_variants` to `[full_name, first_name]`,
  `self_entities` to `[full_name]`, and `timezone` to the IANA zone confirmed
  above. Leave `secondary_timezone` as `""` unless the user asked for a dual
  display.
- `accounts`: fill from the OAuth pairs in Step 3 plus the discovered
  `outlook_sent_folder_id`.
- `obsidian`: fill vault path; all `*_relpath` fields use template defaults.
  Set `hotcache_action_items_heading` to `<first_name>'s Action Items`.
- `businesses`: one entry from the venture name they gave; always include a `personal` entry too.
- `email_filters`: all arrays empty — the user can add team emails and domains later.
- `relationship_radar`: template defaults.

After writing, show the accounts block only and confirm with the AskUserQuestion
tool (header `Confirm`): "Does this look right?" Options: `Yes, continue`,
`No, fix it`. If they pick fix-it, ask what's wrong and correct the vault
`config.json`.

---

## Step 4b : Start reading up on them

Now that you know their name and their company, start the background research
that runs while the rest of setup happens. It reads the public web for who
they are and what the business does, so their first briefing knows something
about them instead of starting from an empty page.

**Run it and move straight on.** Do not wait, do not ask, and do not describe
what it does at length. It is finished in Step 9.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" start --name "<full name>" --company "<their venture name>" --domains "<comma separated domains from their account emails>" --role "<their one line role, if they gave one>" --city "<the city they gave>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" start --name "<full name>" --company "<their venture name>" --domains "<comma separated domains from their account emails>" --role "<their one line role, if they gave one>" --city "<the city they gave>"
```

Branch on the JSON, in one line either way:

- **`ok: true`** : "Reading up on you and <company> in the background while we
  finish." Continue to Step 5 immediately.
- **`ok: false`** : say setup will carry on without it and that
  `/van-gogh:operator-research` runs it later. Never stop the install for
  this. The usual cause is no `claude` binary on PATH, which Step 1 covers.

## Step 5 — Meeting notetaker (optional)

Van Gogh reads meeting notes from **one** AI notetaker at a time. Ask with the
AskUserQuestion tool (header `Notetaker`): "Which meeting notetaker do you use?"
Options: `Granola`, `Grain`, `Neither`.

If `Neither`, skip this step entirely — every briefing still works, just without
meeting signal.

Otherwise, per provider:

| Provider | Where the key comes from | Env key |
|---|---|---|
| Granola | Granola desktop app → Settings → Personal API Keys | `GRANOLA_API_KEY` |
| Grain | grain.com → Account settings → Integrations → Personal API (**needs a Starter plan or above** — the Free plan has no API access) | `GRAIN_API_KEY` |

1. Tell the user where to get the key (table above), then ask: "Paste your
   <provider> API key:"
2. Write it into `~/.config/van-gogh/.env` with the **same cross-platform Python
   writer on both OSes** (`app/user_state.py set-env`, which upserts the key
   reading `utf-8-sig` / writing plain `utf-8` — Python never emits a BOM, so
   this avoids the Windows PowerShell 5.1 `Set-Content -Encoding utf8` BOM that
   poisons the first `.env` key; see lab note 2026-05-29). The pasted key is
   passed via the `VG_ENV_VALUE` environment variable, never interpolated into
   the shell, so special characters are safe.

   ```bash
   VG_ENV_VALUE="<pasted_key>" "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/user_state.py" set-env <ENV_KEY>
   ```

   It prints `<ENV_KEY> written (utf-8, no BOM)` on success. If the user leaves
   the key blank, skip this write entirely.

3. Verify — this reports the active provider, whether its key round-trips, and
   the most recent meeting it can see:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/meeting_fetch.py" --check
```

`"ok": true` means connected. `"ok": false` carries a `reason` — a missing key,
a bad key, a plan without API access, or a working key on an empty account
(which is fine). Only one key needs to exist; with exactly one present the
provider is inferred automatically and `config.json notetaker.provider` can stay
empty. If the user later keeps **both** keys, set `notetaker.provider` explicitly
via `/van-gogh:update-settings` — otherwise the ambiguity falls back to Granola.

---

## Step 5b: Connect QuickBooks (optional)

Only if they keep their books in QuickBooks Online and want the weekly finance
brief. Ask with the AskUserQuestion tool (header `QuickBooks`): "Do you use
QuickBooks Online? Van Gogh can read it and send you a weekly brief: cash by
account, who is past due to you, what you owe, and how the month is going."
Options: `Yes, connect it`, `Not now`.

If `Not now`, skip this step. Everything else works without it.

Otherwise, tell them this is a browser step Van Gogh cannot do for them:

1. Open claude.ai, go to **Settings**, then **Connectors**.
2. Find **Intuit QuickBooks** and connect it. Sign in to QuickBooks and
   approve the company they want read.
3. Come back here and say when it is done.

Then verify, which means actually calling it:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --check quickbooks --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --check quickbooks --json
```

This makes a real call and reads the company name back. **Do not substitute
`claude mcp list`**: that reports whether the server can be reached, not
whether the connection works, and it has shown "Connected" while every call
was rejected.

Read the result:

- `"ok": true`: connected. The `summary` carries the company name and id.
  Write both into the vault `config.json` `finance` block as `company_name` and
  `company_id`. That is what stops a brief ever reporting a different company's
  numbers without saying so. Tell the user which company it found, in case it
  is not the one they expected.
- `"status": "needs-auth"`: the connection did not complete, or expired
  already. Send them back to step 2.
- `"status": "absent"`: QuickBooks is not in their connector list at all, so
  step 1 did not happen on this machine or this account.

Say plainly what it can and cannot do: it may read those reports and nothing
else, so nothing in their books can be changed, and the connection expires
every so often, at which point they get an email telling them to reconnect.

---

## Step 6 — Scaffold vault + smoke test

Run both automatically, no confirmation needed.

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/vault/scaffold.py" "$("$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import vault; print(vault())")"
```

Scaffolding writes `{vault}/CLAUDE.md`, whose top section is a managed
"Project Van Gogh" context block (between `van-gogh:context` markers). That
block makes any Claude Code session opened in the vault a chief-of-staff
session, and it self-refreshes on plugin updates — re-running scaffold, or any
skill run after an update, syncs it without touching the user's own edits
below the markers.

Verify the config and each connected account:

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "
import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app'))
from config_loader import cfg, vault, van_gogh_root, primary_account
print('vault:', vault())
print('van-gogh root:', van_gogh_root())
print('config found via pointer:', (primary_account() or {}).get('email'))
print('config OK')
"
```

This exercises the full bootstrap: the `~/.config/van-gogh/vault-pointer` file →
vault → `{vault}/van-gogh/config.json`. If it prints the gmail address, discovery works.

Run a client smoke test for each connected account:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/google_client.py" --label <PRIMARY_LABEL>
# if secondary Google:
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/google_client.py" --label <SECONDARY_LABEL>
# if Outlook:
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/microsoft_client.py" --label <MS_LABEL>
```

Each prints `OK [label] ...` on success. Fix any errors before continuing —
**except** a Microsoft smoke test that fails with a network / proxy error
(`403` / `Host not in allowlist`): that's the sandbox blocking
`graph.microsoft.com`, not a bad token. Note it and continue; the token is valid
and will work wherever `graph.microsoft.com` is reachable (e.g. the desktop app).

---

## Step 7 — Updates

Nothing to install — updates come through the Claude Code plugin system, and
there is no separate update skill or step. Walk the user through enabling
auto-update once: open `/plugin` → **Marketplaces** → `van-gogh` → enable
**auto-update**. (Auto-update is off by default for third-party marketplaces;
with it on, updates apply automatically at session start.) Then tell the user:

> Project Van Gogh updates via the plugin marketplace. With auto-update on you
> get new versions automatically at session start; to update on demand, run
> `/plugin marketplace update van-gogh` — that single command is the whole
> update. Python dependencies re-sync themselves the next time any skill runs,
> and the `claude` CLI itself is checked once a day before your scheduled
> briefings, so it can't fall behind the models they need.

Do **not** add any git-pull hooks to `.claude/settings.json` — the plugin cache
is not a working checkout the user should pull into.

---

## Step 7b: fewer permission prompts (optional, ask, default no)

Van Gogh reads mail and writes into the vault constantly, so a briefing can
raise a permission prompt every few seconds. Users consistently report this as
the single most annoying thing about running it. There is a setting that turns
it off, and it is worth offering once, out loud, with its real scope stated.

**Ask with the AskUserQuestion tool** (header `Permissions`), and make the
default the safe answer:

- **Keep asking (recommended)**: Claude asks before each file write or command.
- **Stop asking**: Claude runs tools without prompting, in **every project on
  this computer**, not only Van Gogh.

Say that scope in plain words before they choose. It is a machine-wide change
to their own Claude Code settings, not a Van Gogh setting.

If they decline, say nothing further and move on. Do not raise it again.

If they accept:

1. Show them the current `permissions` block first, so they can see what is
   about to change:

   macOS / Linux (bash/zsh):
   ```bash
   cat "$HOME/.claude/settings.json"
   ```

   Windows (PowerShell):
   ```powershell
   Get-Content "$HOME\.claude\settings.json"
   ```

2. Make a **targeted edit** adding this one key. Never rewrite the file, and
   never touch any other key in it:

   ```json
   { "permissions": { "defaultMode": "bypassPermissions" } }
   ```

   If `permissions` already exists, add or change only `defaultMode` inside it.
   If the file does not exist, create it with just that block.

3. Tell them how to undo it: open `~/.claude/settings.json` and delete the
   `defaultMode` line, or run `/config` and change it back. It takes effect on
   the next session.

---

## Step 7c — Personal companion plugin (only if they have a key file)

Some users receive a **Van Gogh key file** from the team — a small JSON file
granting access to a private companion plugin with skills made just for them.
Ask once: "Did the Van Gogh team send you a personal key file?" If no (the
normal case), skip this step entirely. If yes, ask for the file's PATH (never
its contents — the file holds a secret that must not enter the chat), then:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/companion_key.py" ingest --file "/path/to/key.json"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\companion_key.py" ingest --file "C:\path\to\key.json"
```

Render its JSON: on `{"ok": true}` their personal skills are live as
`/van-gogh-custom:<name>` (fresh session may be needed) and they should
delete the key file; on `{"ok": false}` report `error` + `hint` verbatim and
move on — the rest of the install does not depend on this step. A key that
arrives later is ingested anytime with /van-gogh:update-van-gogh.

## Step 8 — Schedule background tasks

**Ask with the AskUserQuestion tool upfront** (header `Scheduler`) before running
any scheduler commands:

> "Should I set up daily background jobs (4:30 AM) that auto-run `/van-gogh:meeting-ingest`
> and `/van-gogh:ingest-workspace` each morning, plus a check that restarts them
> if they ever fail to run?"

Options: `Yes, set them up`, `No, I'll run skills manually`.

If they say no, skip to Step 9.

If they say yes, run the scheduler installer. `app/scheduler_setup.py` detects
the OS itself and does the whole job: on **macOS** it writes and loads the two
launchd LaunchAgents (`com.monet.meeting-ingest` / `com.monet.ingest-workspace`);
on **Windows** it registers the two Scheduled Tasks (`VanGogh-meeting-ingest` /
`VanGogh-ingest-workspace`, with `-StartWhenAvailable` so a missed run fires on
wake); on **Linux** it prints `UNSUPPORTED` and installs nothing (skills still
work invoked manually). Each job runs `app/skill_run.py <skill>` daily at
4:30 AM with output logged to the vault's `van-gogh/logs/`. That wrapper is
what gives an unattended run its retries, its failure ledger, and its daily
`claude` CLI freshness check. The names it uses
are the same constants `/van-gogh:uninstall-van-gogh` removes.

It also installs the watcher (`com.monet.job-watch` / `VanGogh-job-watch`,
`app/job_watch.py`), which runs every 30 minutes and at login. It is what
notices that a job did not run and starts it again, and it is why the answer
to "did my briefing arrive?" stops depending on the user remembering to look.
It holds rather than retries when a re-run cannot help (a usage cap, a sign-in,
a CLI mid-update), so it costs nothing on a bad morning. If the user asked for
no scheduled jobs, they get no watcher either: there would be nothing to
watch.

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" install
```

**Then ask about meeting prep emails** (header `Meeting prep`), a second
question, only if they said yes to the scheduler:

> "Want a meeting prep emailed to you before each call? It is the same
> one-pager `/van-gogh:meeting-prep` writes: who is on the call, the deal
> context, what you owe them, and what a good outcome looks like."

Options: `One email per meeting, about an hour ahead`, `One email each morning
covering the whole day`, `No thanks`.

Write the answer into the vault `config.json` `meeting_prep` block: `enabled`
true for either of the first two, `mode` `each` or `daily` to match. Leave
`lead_minutes` at 60 and `daily_time` at 07:00 unless they ask for something
else. Then re-run the same `scheduler_setup.py install` command above so the
poller is registered, since it only installs when the feature is on.

Tell them it sends to wherever their digest already goes, and that
`/van-gogh:update-settings` turns it off if it becomes too much mail. If they
picked per-meeting, say the prep arrives 45 to 60 minutes before the call, and
that only meetings with someone outside the team get one.

**Then ask about the weekly scorecard** (header `Scorecard`), a third
question, only if they said yes to the scheduler:

> "Want a short scorecard every week? Six numbers on whether Van Gogh is
> earning its place: how many threads are waiting on you, how fast things
> close, how much got filed. It is counted on your machine and never quotes a
> message."

Options: `Yes, email it weekly`, `No thanks`.

On yes, write `enabled` true into the vault `config.json` `kpi` block and
re-run the same `scheduler_setup.py install` command above, since the job only
installs when the feature is on. Leave `day` at friday and `time` at 16:00
unless they ask otherwise.

Tell them two things. It goes to wherever their briefings go unless they set
`recipient_email` in that block, which exists because a scorecard is about them
rather than about their week. And the first one arrives about a fortnight from
today, not this week, because a scorecard with nothing behind it is six empty
cards. Name the actual date.

**Then ask about the weekly finance brief** (header `Finance`), a fourth
question, only if they said yes to the scheduler AND QuickBooks connected in
Step 5b:

> "Want the finance brief every Monday morning? Cash by account, who is past
> due to you, what you owe, and the month so far against last month. It reads
> QuickBooks and changes nothing in it."

Options: `Yes, email it Monday mornings`, `No thanks`.

On yes, write `enabled` true into the vault `config.json` `finance` block and
re-run the same `scheduler_setup.py install` command above, since the job only
installs when the feature is on. Leave `day` at monday and `time` at 07:00
unless they ask otherwise.

Tell them it goes wherever their briefings go unless they set
`recipient_email` in that block, which exists because the cash position is not
the same kind of thing as a calendar. And tell them what happens when the
QuickBooks connection expires: they get a short email saying to reconnect it,
rather than the brief just stopping.

Read the output: `OK: ...` per job means it's installed and verified — done. An
`ERROR: ... 'claude' CLI was not found on PATH` means the `claude` binary isn't
on PATH (see Step 1) — tell the user to fix that, then re-run this one command.
Any other `ERROR: ...` line — show it to the user and investigate before
continuing.

---

## Step 9 — Finish

### Your profile

Collect the research that started in Step 4b. Skip this whole section if that
step reported `ok: false`.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" status
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" status
```

Branch on `state`:

- **`done`** : go to "Show it" below.
- **`running`** : it is still going. Ask with the AskUserQuestion tool (header
  `Profile`): "Van Gogh is still reading up on you. Wait for it, or finish
  now and pick it up later?" Options: `Wait, up to 5 minutes`, `Finish now`.
  On wait, run the same command with `wait --timeout 300` instead of `status`,
  then branch on the state it returns. On finish now, say
  `/van-gogh:operator-research` collects it whenever they want, and go to the
  finish text.
- **`failed`** : one line saying it did not finish, using `reason` in their
  words, and that `/van-gogh:operator-research` runs it again. Nothing was
  written. Go to the finish text.
- **`not_started`** : say nothing at all and go to the finish text.

**Show it.** Run `show` (same two command forms, with `show` in place of
`status`) and render its `summary_md` verbatim.

Then ask twice, in order.

**Is this them?** AskUserQuestion, header `Identity`: `Yes, that is me`,
`Right person, some details are wrong`, `That is not me`. On `That is not me`,
write nothing, say so, and mention they can rerun it later with the right
company name. On either of the other two, carry on.

**Which priorities are real?** Only when the summary listed any. AskUserQuestion,
header `Priorities`, `multiSelect: true`, one option per suggested priority
plus `None of these`. Say in the question that these are guesses from public
pages, not things it knows.

**File it.** Pass the numbers the summary showed, as JSON, or `[]` for none:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" apply --priorities-json "[0,2]"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" apply --priorities-json "[0,2]"
```

On `ok: true`, say in two lines what landed: the two pages now in their wiki,
and that the profile loads in every session from here on. On `ok: false`, say
`why` in one line and move on. Either way, finish the install.

Tell the user:

```
You're set up. Here's what to do next:

• For day-to-day chief-of-staff work, open Claude Code in your vault folder,
  that loads your Van Gogh context (skills, memory, current state)
  automatically:
    macOS/Linux:  cd "/path/to/your vault" && claude
    Windows:      cd "C:\path\to\your vault"; claude
  (/van-gogh:* skills also work from any other folder, you just won't have
  the ambient vault context there.) To check it loaded, run /context once in
  a vault session: the vault CLAUDE.md and its imported memory.md should
  appear under Memory files.
• Run your first /van-gogh:week with a wide lookback so it catches every live
  deal: tell Claude "run /van-gogh:week, look back 60 days" (it uses --since 60).
  This first run takes 5-10 minutes (it classifies every deal across your
  accounts), so let it finish. After this first run, plain /van-gogh:week auto-sizes
  the window from the last briefing and runs faster. /van-gogh:week scans your email, surfaces your active deals and open
  action items, and writes week.md. Then just ask Claude to seed
  hotcache.md from that briefing (you only refine the deal stages and
  priority order, the threads come straight from /van-gogh:week). Don't fill
  hotcache.md in from scratch.
• Run /van-gogh:morning-coffee each morning for your daily check-in (~5 min).
• Run /van-gogh:meeting-ingest after any meeting to file it into your vault.
• Want your briefings emailed to you automatically? Run
  /van-gogh:update-digest-preferences to opt in. By default it sends Morning
  Coffee (Mon-Fri 7:00 AM), Afternoon Tea (Mon-Fri 1:00 PM), Week (Monday
  7:00 AM), and Week Retro (Friday 7:00 AM) from and to your primary account.

To update later: nothing to do if auto-update is on (it applies at session
start), or run /plugin marketplace update van-gogh on demand. That one
command is the whole update; dependencies re-sync on the next skill run.
```
