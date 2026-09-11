# OAuth failure handling (Google and Microsoft)

Read this when an `auth_bootstrap.py` run (or the pasted-redirect /
device-code follow-up) does **not** print `===VANGOGH_AUTH===` and instead
errors, or when a Graph call fails after sign-in. Classify the failure before
deciding what to do — the two cases get opposite treatment.

## 1. IT-policy / admin-consent denial (the org blocks third-party apps)

This is **not** something the user can fix by retrying. Detect it when the
error text (in the script output, the error page the user lands on, or the
pasted redirect URL's query string) contains any of:

- Microsoft: `AADSTS65001` (consent not granted), `AADSTS90094` (admin consent
  required), `AADSTS50105`, `AADSTS700016`, or phrases like
  `admin approval`, `need admin to consent`, `Need admin approval`,
  `AADSTS650056`, `blocked by your organization`, `tenant policy`.
- Google: `access_denied` combined with `admin`/`policy` wording, e.g.
  `blocked by admin`, `org policy`, `organization's policies`,
  `This app is blocked`, `admin has restricted access`.

On this case, tell the user plainly (no retry):

> "Your organization's IT policy blocks third-party apps like this one from
> signing in to **`<email>`**. This isn't something you can approve yourself.
> You'll need to ask your IT department / Microsoft 365 (or Google Workspace)
> admin to grant consent for this app. I'll **skip this account for now** and
> finish the rest of your install. Until it's connected, that account falls back
> to connector mode: briefings still work, but you have to ask for them in chat,
> and they can't run on a schedule. Once IT approves it, re-run
> `/van-gogh:add-account` to connect it."

Then drop that account and continue the install with the accounts that did
succeed. Do **not** abort the whole install.

Do not offer the connector fallback as a *choice*. It is what happens when OAuth
is unavailable, never a lighter-weight option a user might reasonably prefer.

## 2. Transient / network failure (retry, do not treat as a policy block)

Symptoms: a timeout, `Connection refused` / `connection reset`, DNS failure,
`SSL` error, an HTTP `5xx`, `temporarily unavailable`, or the browser/device-code
flow timing out before the user finished. These are recoverable: re-run the exact
same `auth_bootstrap.py` command once (for device-code, re-issue a fresh code).
If it still fails after a retry, surface the raw error to the user and ask whether
to skip the account or try again later. Never tell the user to contact IT for a
network error.

When unsure which bucket an error falls in, prefer **retry once** first; only
escalate to the IT-policy message when the error text clearly matches the
admin-consent / org-policy markers above.

## 3. Graph Sent-folder lookup failures (after a successful Microsoft sign-in)

- **Network / proxy error** (`403` / `Host not in allowlist` — some cloud
  sandboxes block `graph.microsoft.com` even though sign-in via
  `login.microsoftonline.com` succeeded): the **token is still saved and
  valid**. Leave `outlook_sent_folder_id` blank (week_review resolves it at
  runtime) and tell the user to re-run that one command later from the desktop
  app. Do **not** treat it as a failed install.
- **IT-policy / admin-consent error** (the Graph response or error text shows
  the markers from case 1): the org is blocking Graph access for this app, not
  just the folder lookup. Apply the case-1 treatment — tell the user their IT
  department must approve the app, skip this Microsoft account, and continue
  the install. A policy block means the account can't be used until IT
  consents.

## 4. Stale or revoked app credential (every account on one provider fails)

Distinguished from 1-3 by **scope**. A policy block or a network fault hits one
account; a dead app registration takes down every account signed in through it,
because they all share one `client_id`.

Symptoms, on sign-in or on a token refresh during an ordinary briefing:

- Google: `invalid_client`, `deleted_client`, `unauthorized_client`
- Microsoft: `AADSTS700016` (app not found in tenant), `AADSTS7000215`
  (invalid client secret)

Causes: the secret was rotated or deleted in Cloud Console, the Entra
registration was removed, or the OAuth client was created in a project that has
since been deleted.

Do **not** treat this as a per-account problem and do not start deleting
accounts from `config.json`. Confirm the shape first:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/install_oauth_credentials.py" --status
```
Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\install_oauth_credentials.py" --status
```

If the reported `client_id` no longer exists in the user's Console, the
credential is the fault. Send them to **Step 3c** of
[`../SKILL.md`](../SKILL.md), which takes the replacement file and re-authorizes.

Note that replacing the credential invalidates every refresh token issued under
the old registration, so each account on that provider has to sign in again.
That is expected, not a second failure.
