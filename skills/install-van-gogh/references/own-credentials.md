# Create your own Google and Microsoft app credentials

Read this only when `companion_key.py discover` found no key file. With a
key file the credentials arrive inside it and none of this applies. Without
one, Van Gogh signs in with app registrations you create yourself, as
follows.

Van Gogh signs in with **your own** Google and Microsoft app registrations. Two
files, supplied once here and stored per-user in `~/.config/van-gogh/`:

| File | What it is | Where it comes from |
|---|---|---|
| `google_client_secret.json` | Google **Desktop app** OAuth client | Google Cloud Console → APIs & Services → Credentials → Create credentials → OAuth client ID → **Desktop app** → Download JSON |
| `ms_client_secret.json` | Microsoft **public client** app id | Entra admin centre → App registrations → New registration → (public client / mobile-desktop, redirect `http://localhost`) → copy the Application (client) ID |

The Microsoft file is one the user writes by hand, and carries no secret, since a
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
the user has already supplied a valid file: say so in one line and skip that
provider. `true` means ask for it. `source` tells you why: `"user"` is supplied,
`"bundled"` is a leftover shipped copy that must be replaced, `"missing"` is
nothing at all.

**For each provider still needed,** ask the user to either give you the path to
the downloaded file or paste its contents. Then hand it to the installer, which
auto-detects which provider it is, so never ask them to tell you:

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

- `"ok": true`: installed. It reports `provider`, `path` and `replaced`. Confirm
  in one line: "Google credentials installed." Do not echo `client_id`, and never
  echo the file contents.
- `"ok": false`: nothing was written. Show the `error` verbatim, ask for a
  corrected file, and try again. The usual causes are pasting a fragment instead
  of the whole file (the `hint` says so) or handing over a Google **web** client
  where a Desktop one is needed.

If the user has no Microsoft account at all, skip `ms_client_secret.json`
entirely; it is only read when an Outlook account is connected.

Re-run `--status` once both are in. Do not start sign-in until `needs_google` is
`false` (and `needs_microsoft` too, if they have Outlook): signing in against a
missing or stale app credential fails deep inside the OAuth exchange with an
error that reads like a network fault.

---

