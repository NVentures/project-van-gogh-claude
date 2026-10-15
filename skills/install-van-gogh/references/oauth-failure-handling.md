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

## 5. Not a test user / unverified app (Google)

A Google app whose consent screen is still in Testing mode admits only the
addresses listed as test users, and an app that has not passed Google's
verification shows a warning wall to everyone else. The person sees a page
headed "Access blocked" or "This app isn't verified" with "Error 403:
access_denied" and text like "has not completed the Google verification
process" or "is currently being tested, and can only be accessed by
developer-approved testers". Two shapes reach the script:

- the browser comes back with `error=access_denied` and the page text named
  verification or testers: `error_class` is `unverified_app`;
- the person never gets past the wall, so the loopback server times out after
  5 minutes: `error_class` is `timeout`, and the person tells you what the page
  said.

This is not the person's fault and no retry fixes it. It is the dev side's:
the shared consent screen must be In production (checked by the dev's
preflight before every install). Tell the person their address has to be
added by the Van Gogh team, skip the account, finish the install, and reach
the team with the client's support address. Once the app is in production or
their address is a test user, `/van-gogh:add-account` connects it.

## The failure line

With `--json`, `auth_bootstrap.py` prints one object on every failure:
`{"ok": false, "provider", "email", "label", "env_var", "error_class", "line",
"detail"}`. `line` is at most 120 characters and always ends by saying where
the resume command goes ("in Claude, type /van-gogh:install-van-gogh"), since
the setup line itself is pasted into Terminal and never into Claude. It can be
shown unchanged. `detail` carries the
provider's own words (a Microsoft `error_description` is printed, never
discarded). The classes:

| error_class | What happened | Browser windows | line (shape) |
|---|---|---|---|
| `admin_consent` | AADSTS65001, AADSTS90094, AADSTS650052 or `interaction_required` naming consent; Google access_denied with admin or policy wording | exactly 1, no retry | Your IT admin must approve Van Gogh for Microsoft. Once they have, in Claude, type /van-gogh:install-van-gogh |
| `access_denied` | the person cancelled on the consent screen | 1 | The Google sign-in was cancelled. To try again, in Claude, type /van-gogh:install-van-gogh |
| `unverified_app` | case 5 above | 1 | Google has not approved this account yet. Ask the Van Gogh team to add it. In Claude, type /van-gogh:install-van-gogh |
| `missing_scope` | the token came back without a permission the flow asked for (a box unticked); nothing is saved | 1 | The sign-in left out your Google Calendar. Tick every box next time: in Claude, type /van-gogh:install-van-gogh |
| `timeout` | no answer from the browser in 5 minutes (Google) or MSAL's wait (Microsoft) | 1 for Google, 2 for Microsoft (one retry) | The Google sign-in got no answer in 5 minutes. To try again, in Claude, type /van-gogh:install-van-gogh |
| `no_credential` | the app credential file is missing from the state dir | 0 | The Google app credential is missing. Add the key file, then in Claude, type /van-gogh:install-van-gogh |
| `no_refresh_token` | the provider answered without a refresh token; the old token is untouched | 1 | Google returned no refresh token. To sign in again, in Claude, type /van-gogh:install-van-gogh |
| `no_browser` | no browser could be opened | 0 | No browser could open for Google here. On your own computer, in Claude, type /van-gogh:install-van-gogh |
| `provider_error` | any other AADSTS code or Google error, quoted in `detail` | 1 | Microsoft refused the sign-in (AADSTS50020). To try again, in Claude, type /van-gogh:install-van-gogh |

A hint mismatch is not a failure: with `--expect-email`, the success object
carries `email_matches_hint` true or false, and the installer asks whether to
use the account anyway. A second account on the same domain never takes the
first one's label: the script picks a distinct one and reports it as `label`
with `label_adjusted: true`.
