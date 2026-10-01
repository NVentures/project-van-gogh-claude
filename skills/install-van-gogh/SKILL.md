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
> get new versions automatically; with it off, a briefing asks once when a new
> version is published and installs it on a yes. To update on demand, run
> `/van-gogh:check-updates`. Python dependencies re-sync themselves the next time any skill runs,
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

Everything Van Gogh can run on its own gets offered here, once, with the
product's defaults as the default answer. Up to eight questions, most of them
one tap, and fewer than eight for most people: several are skipped when an
earlier answer makes them moot, and the last one is usually "yes, those times
are fine". Picking `Let me pick` at 8b or 8h opens a short follow-up; taking
the defaults does not.
**A feature nobody is asked about is a feature nobody gets**: the briefings
used to be mentioned only in the closing text, so a user who finished this
install and stopped had the two early-morning filing jobs and no briefings at
all.

Ask them in order. Each writes into the vault `config.json`; nothing is
installed until the end, so a user can change their mind mid-way without
leaving half a scheduler behind.

Say once, before the first question, that **none of this is permanent**:
`/van-gogh:update-settings` changes any of it later, and
`/van-gogh:update-digest-preferences` changes the briefings specifically. A
reader who knows an answer is reversible answers faster.

Two more things worth saying before they start. Everything here goes to
whichever address their briefings go to, which they set earlier in this
install. And on **Linux** there is no scheduler to install at all: say so now rather
than revealing it after eight questions, and tell them every skill still works
when they run it by hand.

On Linux, ask only 8g, then go to Step 9. There is no 8i to run: it installs
scheduled jobs and Linux has no scheduler for them. 8g is still worth asking
because the answer is recorded, so it already holds if a Linux scheduler is
added later. Tell them the Workbench will not start by itself on Linux and
that `/van-gogh:workbench` opens it whenever they want it.

### 8a. The background jobs at all

**Ask with the AskUserQuestion tool** (header `Scheduler`):

> "Should Van Gogh run on its own? That means filing your meeting notes and
> syncing your project notes each morning before you are up, emailing you the
> briefings you pick next, and a check that restarts any of it that fails."

Options: `Yes, run it for me`, `No, I'll run skills by hand`.

If they say no, skip 8b through 8f and the times question: there is nothing to
schedule and no watcher to install, because there would be nothing to watch.

**Still ask 8g.** The Workbench is a dashboard they open, not a job that runs
on a schedule, so someone who wants nothing running in the background may
still want it waiting when they log in.

### 8b. The briefings

**Ask** (header `Briefings`):

> "Email your briefings automatically? Morning Coffee each weekday morning,
> Afternoon Tea each weekday afternoon, Week on Monday, Week Retro on Friday.
> Times come next."

Options: `Yes, all four`, `Let me pick which`, `No thanks`.

Name the days but not the clock times. The days are part of what each
briefing is; the times are settled once, at 8h.

On `Yes, all four`, write `digest.enabled: true` and leave each briefing's
`enabled` at its default of true.

On `Let me pick which`, ask a follow-up with `multiSelect: true` over the four
names and write `digest.enabled: true` plus `enabled` per briefing to match
what they picked. Picking none is the same as `No thanks`.

On `No thanks`, leave `digest.enabled: false`. Say that
`/van-gogh:update-digest-preferences` turns them on later, and move on
without arguing.

### 8c. Meeting prep emails

**Ask** (header `Meeting prep`):

> "Want a meeting prep emailed to you before each call? It is the same
> one-pager `/van-gogh:meeting-prep` writes: who is on the call, the deal
> context, what you owe them, and what a good outcome looks like."

Options: `One email per meeting, about an hour ahead`, `One email each morning
covering the whole day`, `No thanks`.

Write the answer into the `meeting_prep` block: `enabled` true for either of
the first two, `mode` `each` or `daily` to match. Leave `lead_minutes` at 60.

If they picked the daily option, leave `daily_time` at 06:15. It is
deliberately not 07:00: that is when Morning Coffee lands, and two emails in
the same minute read as one duplicate. The 8h table carries a
`6:15 AM Mon-Fri` row for it, and `Let me pick` offers it like any other time.

Per-meeting prep has no clock time at all: it arrives relative to each call,
so it gets no row in that table and there is nothing to set.

Tell them it sends wherever their briefings go, and that
`/van-gogh:update-settings` turns it off if it becomes too much mail. If they
picked per-meeting, say the prep arrives about an hour before the call, which
is the `lead_minutes` value above. Either way, only meetings with someone
outside the team are prepped, so internal one-to-ones will not appear.

### 8d. The weekly scorecard

Skip this unless Week Retro is on: the scorecard is a section of that
briefing, so offering it otherwise would be offering part of something they
have already declined. That means skipping it if they said `No thanks` at 8b,
and if they picked and left Week Retro out. **Ask** (header `Scorecard`):

> "Want a scorecard in your Friday retro? Six numbers on whether Van Gogh is
> earning its place: how many threads are waiting on you, how fast things
> close, how much got filed. It is counted on your machine and never quotes a
> message."

Options: `Yes, include it`, `No thanks`.

On yes, write `enabled` true into the `kpi` block. Nothing else is scheduled:
it rides along inside Week Retro, so it costs no extra job and no extra email.
(`kpi.day` and `kpi.time` in that block are ignored. Do not write them, and do
not mention them to the user.)

Tell them it needs a fortnight of history before the numbers mean anything.
Work out the date of the Week Retro fourteen days from today, name it, and say
that is the first one worth reading.

### 8e. The weekly finance brief

Only if QuickBooks connected in Step 5b. **Ask** (header `Finance`):

> "Want the weekly finance brief every Monday morning? Cash by account, who is
> past due to you, what you owe, and the month so far against last month. It
> reads QuickBooks and changes nothing in it."

Options: `Yes, email it Monday mornings`, `No thanks`.

On yes, write `enabled` true into the `finance` block. Leave `day` at monday
and `time` at 06:45. If they also took the briefings, say what Monday looks
like: Week at 6:30, the finance brief at 6:45, Morning Coffee at 7:00, one run
of reading rather than three interruptions. If they declined the briefings,
this is simply a Monday email at 6:45, and do not describe briefings they are
not getting.

Tell them it goes wherever their briefings go, and that they can send it
somewhere else instead if they would rather their numbers did not land in the
same inbox as their calendar (`finance.recipient_email`). And tell them what happens when the
QuickBooks connection expires: they get a short email saying to reconnect it,
rather than the brief just stopping.

### 8f. Saving new contacts to your address book

**Ask** (header `Contacts`):

> "Want Van Gogh to save the people you correspond with into your Google
> Contacts once a week? It adds people you have actually exchanged mail with
> and never deletes or edits anyone."

Options: `Yes, weekly`, `No thanks`.

On yes, write `enabled` true into the `contact_capture` block. Leave `day` at
sunday and `time` at 05:00.

Say it runs early on Sunday, before they are up, and that
`/van-gogh:contact-capture` shows what it would add without adding it.

### 8g. The Workbench at login

**Ask** (header `Workbench`):

> "Start the Workbench automatically when you log in? It is the local
> dashboard at 127.0.0.1 that shows your briefings, your tickets and your
> vault graph, and having it already running is what lets a briefing open
> itself in your browser when it lands."

Options: `Yes, start it at login`, `No, I'll open it when I want it`.

On yes, write `autostart` true into the `workbench` block. It binds only
127.0.0.1 and every page is behind a per-boot token, so nothing on the network
can reach it.

If they say no, say `/van-gogh:workbench` still opens it on demand. If they
are also getting briefings, add that a briefing will note the page is not open
rather than starting a server uninvited.

### 8g-2. The things you would otherwise have to remember

Five things Van Gogh can do only run when someone asks. Each can run on its
own schedule instead. **Ask** (header `On a schedule`), as one question that
takes more than one answer:

> "These five only run when you ask for them. Which should run by
> themselves?"

Options, each with its line:

- `Meetings nobody recorded`: checking for meetings nobody recorded, every
  morning, and leaving a stub in your vault for each so no call goes
  unfiled.
- `Who is going cold`: a weekly look at who is going cold, Monday morning,
  with a check-in drafted on the radar page for the ones overdue.
- `Client reports`: drafting your weekly client reports on Friday afternoon.
  Each is left as a draft for you to read and send. Offer this only when
  `clients` in their config is not empty.
- `How you write`: learning from the mail you sent this week, Sunday evening,
  so drafts keep sounding like you.
- `Tone profile`: refreshing your tone profile each Sunday from the last 90
  days of sent mail. The profile it replaces is kept beside the new one.

For each one chosen, write `enabled` true into that skill's entry in the
`cadence` block (`calendar-stub-check`, `relationship-radar`, `five-fifteen`,
`voice-calibration`, `voice-generator`), creating the block from
`config.template.json` when it is missing. Leave `days` and `time` alone.

Say once that none of these sends anything to anyone: the most any of them
leaves is a draft or a page in the vault.

### 8h. The times

Last, after the features are settled. **Ask** (header `Times`), showing the
table in full:

> "These are the times. Take them, or pick your own?"
>
> | Time | What |
> |---|---|
> | 4:30 AM | file your meeting notes |
> | 4:50 AM | sync your project notes |
> | 6:15 AM Mon-Fri | your day's meeting prep |
> | 6:30 AM Mon | Week |
> | 6:45 AM Mon | finance brief |
> | 7:00 AM Mon-Fri | Morning Coffee |
> | 2:30 PM Mon-Fri | Afternoon Tea |
> | 3:30 PM Fri | Week Retro |
> | 5:00 AM Sun | save new contacts |
> | 5:05 AM daily | check for meetings nobody recorded |
> | 5:20 AM Mon | who is going cold |
> | 1:00 PM Fri | draft the client reports |
> | 5:40 AM Sun | refresh the tone profile |
> | 6:00 PM Sun | learn from the week's sent mail |

Options: `These times`, `Let me pick`.

Show the two filing rows (they come with 8a) plus a row for each feature they
turned on, and nothing else. Two rows need care: include the meeting-prep row
only if they chose the daily option, because per-meeting prep has no fixed
time (it follows each call), and include a briefing row only for the briefings
they actually picked. Most people take the defaults in one tap.

On `Let me pick`, walk the times conversationally and write each into the
matching config block (`digest.briefings.<name>.time` and `.days`,
`meeting_prep.daily_time`, `finance.time`, `contact_capture.time`, and
`cadence.<skill>.time` and `.days` for anything chosen in 8g-2).

Say two things either way, because both surprise people. The times are
**computer-local**, not a server's. And a machine that is **asleep at 7:00 AM
runs the briefing when it wakes**, rather than skipping it; a briefing waits
for the morning filing jobs to finish first, so a late start still reads a
filed vault rather than yesterday's.

### 8i. Install it

Only now, once. `app/scheduler_setup.py` detects the OS itself and installs
every job the answers above turned on, removing any the user said no to:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" install
```

On **macOS** it writes and loads a launchd LaunchAgent per job; on **Windows**
it registers a Scheduled Task per job (with `-StartWhenAvailable`, so a missed
run fires on wake); on **Linux** it prints `UNSUPPORTED` and installs nothing,
and the skills still work invoked by hand.

Jobs that run a skill go through `app/skill_run.py`, so a run with nobody
watching gets retried when it fails, writes down what happened either way, and
keeps its own tooling up to date.

The watcher (`com.monet.job-watch` / `VanGogh-job-watch`) goes in alongside
them. It looks every 30 minutes and at login, notices a job that did not run,
and starts it again. When a re-run cannot help (a usage cap, an expired
sign-in) it waits instead of hammering. It is why "did my briefing arrive?"
stops depending on the user remembering to look.

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

## Step 9b : Warm start, so the first briefing is worth reading

A vault this new holds nothing: no deal threads, no pages for the people they
work with, no priorities, no meeting history. A briefing rendered against it
can only list mail, which is exactly the "it does not know anything about me"
first impression this step exists to prevent.

Run the warm start now, while the user is still here to confirm what it finds.
Follow `skills/warm-start/SKILL.md` end to end: it starts a 60 day scan, asks
their priorities per business while that runs, proposes deal threads and
people for them to tick, writes only what they ticked, files a meeting
backlog, and verifies nothing was left outstanding.

Budget 25 to 40 minutes and say so before starting. If the user would rather
not spend that now, tell them `/van-gogh:warm-start` does the same thing
whenever they want it, and skip to the closing message.

Do not hand any of this to the user as homework. Seeding a hot cache by hand
from a briefing is the thing nobody ever does, which is why it stopped being
an instruction and became a step.

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
• Run /van-gogh:morning-coffee each morning for your daily check-in (~5 min).
• Run /van-gogh:meeting-ingest after any meeting to file it into your vault.
• Run /van-gogh:update-digest-preferences any time to change which briefings
  are emailed, or when they arrive.

To update later: nothing to do. A briefing asks once when a new version is
published and installs it on a yes, or run /van-gogh:check-updates on demand.
Dependencies re-sync on the next skill run.
```
