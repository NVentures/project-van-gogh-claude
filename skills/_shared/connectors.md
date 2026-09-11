# Reading a Claude connector from a scheduled job

Van Gogh reaches mail and calendar through the user's own OAuth. Some sources
have no OAuth client to reach for: the books live in QuickBooks, the team talks
in Slack, the pipeline sits in HubSpot, and the only way in is a connector the
user authorized on claude.ai, whose token Anthropic holds.

`app/connector_fetch.py` is how a script reads one of those without a person
present. This page is for whoever adds the next one.

## What it does

It spawns a headless `claude -p` session that may call the tools you name and
do nothing else, asks it to make those exact calls, and reads the answer out of
the tool results rather than out of the model's reply.

```python
import connector_fetch

out = connector_fetch.fetch(
    "claude_ai_Intuit_QuickBooks",
    [{"tool": "company_info", "args": {}}],
)
if out["ok"]:
    raw = out["data"]["company_info"]     # exactly what the tool returned
else:
    print(out["reason"])                  # written for a person to read
```

`status` is one of `ok`, `needs-auth`, `absent`, `error`. Only `ok` carries
data. Nothing here raises for an unavailable connector: that is a verdict the
caller has to record and pass on, not an exception.

## Checking a connector works, before writing anything

Three commands, in the order you need them. None of them requires this
codebase to know the connector exists, which is the point: they are for the
connector a client just plugged in.

**What is attached, and does it work.**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --list
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --list
```

Every connector this machine can see, each marked OK, NEEDS-AUTH or ABSENT,
with the tool prefix its tools are named under. A connector still handshaking
is asked directly rather than reported as a maybe, because "attached" and
"working" are different things and a status line has been wrong about it.

**What it offers.**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --tools claude_ai_Intuit_QuickBooks
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --tools claude_ai_Intuit_QuickBooks
```

Asks the connector for its own tool list and splits it into what reads and
what changes things. The split is a reading aid: it is biased toward calling a
read a write, and it authorizes nothing. The allowlist is still written by
hand and guarded by its own test.

**What a response actually looks like.**

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --probe claude_ai_Intuit_QuickBooks qbo_accounting_get_balance_sheet --args '{"as_of_date":"2026-09-10"}' --save "$HOME/Downloads/capture.json"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\connector_fetch.py" --probe claude_ai_Intuit_QuickBooks qbo_accounting_get_balance_sheet --args '{\"as_of_date\":\"2026-09-10\"}' --save "$HOME\Downloads\capture.json"
```

Makes one real call and prints the structure: the keys, the shape of the rows,
the names inside them, and nothing else. Figures, customer names and account
ids never reach the output, because this gets read on a terminal. `--save`
writes the full response so the fixtures can be built from it.

This step is not optional advice. The finance brief was built against the
shape QuickBooks documents rather than the one this connector sends, a parser
and sixty tests all agreed with each other, and every section of the first
live brief would have read "not measured" against a perfectly good response.
Capture first, then parse.

## Adding a connector

1. **Find the server name.** Run `claude mcp list`. The display name is
   punctuated (`claude.ai Intuit QuickBooks`) and the tool prefix is not
   (`mcp__claude_ai_Intuit_QuickBooks__`). Either spelling works; the prefix
   form is what the code uses.

2. **List the tools you need, in full, and keep them read-only.** The allowlist
   is a whitelist: a tool absent from it does not exist for the call. Wildcards
   are refused by the CLI, which is a mercy, because most connectors expose far
   more than you want. QuickBooks alone offers invoice deletes, payroll edits
   and a loan application beside the five reports the finance brief reads.
   Freeze your list as a module constant and write a test that every name in it
   is a read. `tests/test_finance_brief.py` has one to copy.

3. **Decide the arguments in code.** Pass them in the call and let the fetch
   check them: a model asked for one month that answers with the fiscal year to
   date produces a correct-looking label over a number four times too large,
   and nothing downstream can tell. This is the single most valuable thing the
   module does.

4. **Handle the three failures, all of them a person's to fix.**
   `needs-auth` means the token expired, which happens, and only a browser can
   renew it. `absent` means the connector was never added. `error` means it ran
   and something else went wrong. A scheduled job should record the run, hold
   rather than retry, and, when a reader would otherwise just see silence, tell
   them: `app/finance_send.py` mails one short note per week saying which
   service needs reconnecting.

5. **Register the failure so the watcher speaks.** `failure_class.classify`
   already names an unavailable connector `connector`, which is in `NO_RETRY`,
   and `job_watch` turns that into a sentence naming the service. Add your
   service to `job_watch._CONNECTOR_NAMES` so it is named in the reader's
   words rather than by its machine spelling.

6. **Add it to the install check.** `connector_fetch.SERVERS` and `PROBES` map
   a short name to a server and its cheapest real call, which is what
   `connector_fetch.py --check <name>` uses. Make the probe a read.

## Two things that are not obvious

**A health check is not an authorization check.** `claude mcp list` reported
QuickBooks as Connected for hours while every call returned an expired token.
The only way to know a connector works is to call it, which is exactly what
`--check` does.

**The session answers before the server connects.** A remote MCP server
finishes connecting during the session, and without the empty `--mcp-config`
the first turn does not wait for it: measured runs answered in four seconds
with the server still pending and the model reporting that no tools existed.
`fetch` passes that flag, waits, and retries once. If you ever write your own
spawn, carry the flag.

## What this is not for

Mail and calendar. Those have OAuth clients, they run unattended without a
model in the path, and they are not slowed down by a connector handshake or
stopped by a token only a browser can renew. `app/data_sources.py` is the
routing layer for those, and it stays the primary path.
