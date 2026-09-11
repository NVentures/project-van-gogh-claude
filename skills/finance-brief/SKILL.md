---
name: finance-brief
description: >-
  Where the money stands, read straight from QuickBooks: cash in the bank, who
  is past due to you, what you are past due on, and the month so far against
  last month. Every figure is read by code and carries its date and direction.
  Use when the user types /van-gogh:finance-brief or asks how the books look,
  what the cash position is, who owes them money, what is overdue, or how the
  month is going.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# The finance brief

Five figures a person who runs a business wants before the week starts. The
script reads them from QuickBooks through the connector the user authorized,
and renders the whole page itself.

**How this reads.** The person reading it runs a business and did not ask for
software. Lead with the number, short sentences, no vocabulary from the data
model. The full rules are the "Written Voice" section of `DESIGN.md`.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Run it

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/finance_brief.py" --json
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\finance_brief.py" --json
```

It takes about half a minute: it opens a confined session that may read those
QuickBooks reports and nothing else.

---

## If it cannot read the books

The script exits 1 and its JSON carries a `status` of `needs-auth` or `absent`,
with a `reason` written for the reader. Say what it says and stop. Do not
retry, and do not offer figures from a previous run as though they were today's.

- `needs-auth`: the QuickBooks connection has expired. Tell them to open
  claude.ai, go to Settings, then Connectors, and reconnect Intuit QuickBooks.
  It is a browser step and nothing here can do it for them.
- `absent`: QuickBooks is not connected on this machine at all, which is a
  setup step rather than a lapsed one. Same place, same two clicks.

---

## What comes back

- `finance_md`: the finished brief. **Paste it verbatim.** Every figure, every
  date and every direction word in it was decided by code.
- `company`: whose books these are, by name and id.
- `as_of`, `basis`, `currency`: when the figures are from and how they are kept.
- `cash`, `ar`, `ap`, `pl_mtd`, `pl_prior`: the sections, each carrying
  `measured` and, when it is false, a `reason` in plain words.
- `deltas`: the movement against the last weekly brief, per measure, with the
  direction already worked out.
- `sections`: which parts could be read this time.
- `meta.output_path`: where the finished page belongs in the vault.

---

## Render it

Paste `finance_md` verbatim. Do not re-order the sections, re-word a figure,
round anything, or turn a "not measured" into a zero. A section that says it
could not be read is giving the honest answer, and a zero there would be a
claim nobody checked.

Then add **the controller's read**, three to five sentences under a
`## What a controller would say` heading, in the voice of the
`bookkeeper-controller` specialist: what in these numbers would make an
experienced controller pick up the phone. Lead with the thing that matters
most. Name the account or the customer the brief already named.

**Use no number that is not already in `finance_md`.** Not a total you worked
out, not a percentage, not a days-sales figure, not a difference between two
of the figures shown. If you want to say something is up by a quarter, say it
is up, because the page already says by how much. This is the one rule of the
commentary and it is checked.

If the user asked a specific question ("who owes us the most?"), answer it from
the brief in one line before the commentary.

---

## Write it down

Write the whole thing, the pasted brief and the commentary, to
`meta.output_path`. Overwrite whatever is there: this is the current picture,
and last week's is in the logs.

Frontmatter:

```yaml
---
as_of: <report.as_of>
company: <report.company.name>
generated: <today's date>
---
```

---

## When nobody is there

When `VAN_GOGH_UNATTENDED` is set, ask nothing and offer nothing. Write the
file and stop. The weekly email is sent by `app/finance_send.py`, which does
its own fetch, so this skill is not what mails it.

---

## The weekly email

The same brief arrives by email when it is switched on.
`/van-gogh:update-settings` turns it on, sets the day and time, and can point
it at a different address from the briefings, which exists because the cash
position is not the same kind of thing as a calendar.

Nothing here ever changes anything in QuickBooks. The session that reads it is
allowed those report tools and nothing else.
