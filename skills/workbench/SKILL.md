---
name: workbench
description: Open the Van Gogh Workbench, a local dashboard on 127.0.0.1 showing the briefings, the prepped-work ticket ledger, the vault graph, and the inbox roll-up. Use when the user types /van-gogh:workbench or asks to open the workbench, the dashboard, the local UI, or to see their briefings in a browser.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
---

# Workbench

Starts the local server and opens it. The server keeps running after this skill
finishes, which is the point: it is a page the user leaves open, not a report.

It opens on the **Morning Coffee page** (`/briefing/morning-coffee?t={token}`).
That page is the product's front door for the user and for every client. The
dashboard shell (ticket ledger, vault graph, inbox roll-up) is still served at
`/?t={token}`, but the launcher never opens it and the skill never leads with it.

## Requires OAuth

The Workbench is Tier 1 only. Without refresh tokens it can display whatever a
past briefing left on disk but cannot refresh anything and cannot be scheduled,
so it opens to a setup notice instead of a dashboard. Do not try to detect this
yourself: run the script and read `oauth` in its JSON.

## 1. Check whether a server is already running

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import json,os,sys; from pathlib import Path; p=Path(os.environ.get('VAN_GOGH_STATE_DIR', str(Path.home()/'.config/van-gogh')))/'workbench_session.json'; print(json.dumps(json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}))"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import json,os,sys; from pathlib import Path; p=Path(os.environ.get('VAN_GOGH_STATE_DIR', str(Path.home()/'.config/van-gogh')))/'workbench_session.json'; print(json.dumps(json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}))"
```

If that prints a `port` and a `version` matching the installed plugin, the
server is already up. Give the user its URL
(`http://127.0.0.1:{port}/briefing/morning-coffee?t={token}`) and stop here.

If the `version` differs, the plugin was updated under a running server. Stop
the old one first (step 3), then start a new one.

## 2. Start it

Run in the **background** so the server outlives this skill turn.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/workbench_serve.py" &
```

Windows (PowerShell):
```powershell
Start-Process -WindowStyle Hidden -FilePath "$HOME\.config\van-gogh\venv\Scripts\pythonw.exe" -ArgumentList "$env:CLAUDE_PLUGIN_ROOT\app\workbench_serve.py"
```

It prints one JSON object and then serves:

```json
{"ok": true, "url": "http://127.0.0.1:8765/briefing/morning-coffee?t=...", "port": 8765, "oauth": true}
```

A browser opens on its own. If `ok` is false, the port was taken: re-run with
`--port 0` to let the OS pick a free one.

## 3. Stopping it

```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import json,os,urllib.request; from pathlib import Path; s=json.loads((Path(os.environ.get('VAN_GOGH_STATE_DIR', str(Path.home()/'.config/van-gogh')))/'workbench_session.json').read_text(encoding='utf-8')); r=urllib.request.Request(f\"http://127.0.0.1:{s['port']}/api/shutdown\", data=b'{}', method='POST', headers={'X-Workbench-Token': s['token'], 'Content-Type':'application/json'}); print(urllib.request.urlopen(r, timeout=5).read().decode())"
```

Windows (PowerShell): same command against
`& "$HOME\.config\van-gogh\venv\Scripts\python.exe"`.

## What to tell the user

Report the URL, and say what is live versus what is not, so nothing on the page
is mistaken for something it is not yet:

- **Live now:** the three briefings rendered from the vault, the ticket ledger
  auto-ranked from the latest briefing, comments and dismissals, the vault
  graph with search, the inbox roll-up with deal-stage marks, and the per
  briefing refresh buttons (each one re-renders that briefing through an
  agentic `claude -p`, the same path the emailed digests use).
- **The briefing pages** at `/briefing/<name>` (Morning Coffee, Afternoon Tea,
  The Week, Week Retro). Each renders the briefing the scheduled run wrote, with
  its controls live: a tick closes the item in the vault, a stamp sends the one
  reply that briefing drafted, and a chat panel answers questions about the page
  on the user's own subscription. A scheduled run opens its page by itself
  unless `workbench.open_on_run` is false.
- **Not wired yet:** approving a TICKET records the approval but does not draft,
  build, or send anything. Staging an email, building a deck or a workbook, and
  the confirm-to-send gate all depend on the job runner, which is the next
  piece of work.

The stamp on a briefing page is the only thing in the product that sends mail,
one press per message, and it sends the draft by its provider id so the user's
own edits are what go out. Nothing else on this page can send. Never imply a
ticket was sent.

## Where things live

- Ledger and ratings: `{vault}/van-gogh/workbench/`
- Session file (port, token, pid): `~/.config/van-gogh/workbench_session.json`
- Design rules for anything visual: `DESIGN.md` at the plugin root
