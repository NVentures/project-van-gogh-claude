---
name: add-connector
description: >-
  Connect another system Van Gogh can ask about a person: a CRM, a help desk,
  a deal tracker, anything already connected in Claude. Shows what is
  connected, lets the user pick the tools that may be used, proves it with one
  real lookup, and saves it, so meeting prep includes what that system holds
  on each attendee. Use when the user types /van-gogh:add-connector or asks to
  connect their CRM, add HubSpot or Salesforce or another app, see which
  connectors Van Gogh uses, test one, or remove one.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Add a connector

Van Gogh reads mail and calendar itself. Everything else it reaches through
connectors the user has already switched on in Claude. This skill tells Van
Gogh which of those it may ask about a person, and with which tools.

The connector has to exist in Claude first. If the user's system is not in the
list from Step 1, they add it in Claude's own settings (Settings, then
Connectors), sign in there, and come back. Nothing here can do that for them.

Scripts decide what may be used, not you. A tool is accepted only when its
name says it reads. If the script refuses a tool, report the refusal and do
not look for a way around it.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Step 1: see what is connected

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --list --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --list --json
```

Takes up to a minute. It prints `servers`, each with a `name`, a `prefix` and
a `status`. Show the user the names whose status is `connected` and ask which
one they want Van Gogh to use. A status of `needs-auth` means they must sign
in to it again in Claude's settings first.

To see what is already saved:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_lookup.py" --list
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_lookup.py" --list
```

## Step 2: see what it offers

Use the `prefix` from Step 1 with its leading `mcp__` removed as SERVER.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --tools SERVER --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --tools SERVER --json
```

It prints `tools`. From them, propose the few that find and read a person, a
company, a deal and recent activity: usually a search tool and one or two that
get a record by its id. Three or four is plenty. Show the user the names you
propose and what each is for, and let them change the list.

Only propose tools whose names read (search, get, list, find, query and the
like). Never propose one that creates, updates, deletes, sends or runs
anything, whatever the user's reason: Step 3 refuses those.

## Step 3: save it

Ask the user for a short name for this system in their own words (`crm`,
`helpdesk`), and write one line on what it holds.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_lookup.py" --add NAME --server SERVER --tools TOOL1,TOOL2 --about "what it holds"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_lookup.py" --add NAME --server SERVER --tools TOOL1,TOOL2 --about "what it holds"
```

It checks every tool against the connector, refuses any it does not offer and
any whose name does not read, and only then saves, keeping a copy of the
settings as they were. When `ok` is false, tell the user `why` in plain words
and go back to Step 2 with the refused tools left out.

## Step 4: prove it

Ask the user for someone who is certainly in that system, by name and email.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_lookup.py" --test NAME --name "Full Name" --email person@example.com
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_lookup.py" --test NAME --name "Full Name" --email person@example.com
```

Takes up to a minute. Read `status`:

- `ok`: say what it found, in two or three lines from `findings`. This is
  what will appear in their meeting preps.
- `nothing`: the system answered and holds nothing under that name or
  address. Ask whether the person is filed under a different address, and
  try once more. If it is still nothing, the tools chosen may not search the
  right records: go back to Step 2.
- anything else: report `reason` as it is written.

A connector that has not returned `ok` for a person the user knows is in
there is not finished. Say so rather than calling it set up.

## Removing one

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_lookup.py" --remove NAME
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_lookup.py" --remove NAME
```

## What the user should know

Say these once, after a connector is saved:

- It is used when a meeting prep is written, to look up the people on the
  call. It is not read at any other time.
- What it returns is shown in the prep as coming from that system. Van Gogh
  never changes anything in it and never acts on what it says.
- If its sign-in expires, the prep says so in one line and carries on without
  it.
