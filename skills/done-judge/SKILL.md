---
name: done-judge
description: >-
  The check-off of finished to-dos. Shows what was closed today and the
  evidence behind each close, explains why one item was or was not closed,
  reopens an item and keeps it open for good, or judges one item now. Use when
  the user types /van-gogh:done-judge or asks "what did you check off today",
  "what closed today", "why did that close", "why is that still open",
  "reopen that one", "undo that check-off", "that is not done, put it back",
  or "check whether this one is done".
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Check-off of finished to-dos

Every morning at 5:10 AM Van Gogh reads the open to-dos in the hotcache,
looks for evidence that each one is finished (mail you sent, what was said on
later calls, decisions written down after meetings), and closes the ones the
evidence proves. It never asks. The gate is the undo: any close can be put
back with one command, and an item put back stays open for good.

Each to-do carries an id that looks like `cb-1a2b3c4d`. Every line this skill
prints names the id, and every action takes one.

This skill is four small reads and writes over that record. It does not
decide anything itself: what closed, and why, was decided by code.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## What closed today

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/done_judge.py" --closed-today
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\done_judge.py" --closed-today
```

Show what it prints as it is. The block is written by code because it carries
the ids a person acts on: do not reword a line, renumber it, or drop an id.
"Closed today" items were closed on evidence, and each line quotes that
evidence. "Expired" items had no due date, belonged to no deal, and nothing
about them turned up in a complete search of the last 30 days.

When the output ends with a sentence starting "The check-off did not run", show
that sentence too. It means the run failed, which is different from a day on
which nothing was finished.

## Why one item closed, or did not

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/done_judge.py" --why cb-xxxxxxxx
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\done_judge.py" --why cb-xxxxxxxx
```

It prints the recorded verdict, how confident it was, the kind of evidence,
the sentence it quoted, and `downgrades`: the checks that kept the item open.
Say those in plain words. The common ones:

| It says | It means |
|---|---|
| `citation_is_a_promise` | The quoted sentence says the work will be done, not that it was |
| `citation_says_not_done` | The quoted sentence itself says it is not finished |
| `citation_not_in_one_section` | The quotation could not be found, word for word, in one source |
| `source_predates_item` | The evidence is older than the to-do |
| `citation_is_mail_metadata` | The words were in a subject line, not in the message |
| `mail_has_no_real_recipient` | The mail went only to you |
| `citation_is_the_task` | The quotation was the to-do's own words |
| `weak_class:...` | That kind of evidence cannot close this kind of item |
| `confidence_..._below_...` | Not sure enough |
| `pinned_open` | You reopened it, so it stays open |
| `no_evidence` | Nothing about it was found |

## Reopen one

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/done_judge.py" --undo cb-xxxxxxxx
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\done_judge.py" --undo cb-xxxxxxxx
```

This puts the item's lines back exactly as they were, in the hotcache, on the
meeting page and in the week file, and marks the item so it is never closed
automatically again. It works for an expired item the same way. Run it at
once when the user asks: it is reversible, and it only ever reopens.

When the user names an item in words rather than by id ("reopen the site plan
one"), run `--closed-today` first, find the line, and use its id. If more than
one line could be meant, ask which.

## Judge one item now

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/done_judge.py" --live --only cb-xxxxxxxx
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\done_judge.py" --live --only cb-xxxxxxxx
```

The same thing the morning run does, for one item. It closes the item only if
the evidence proves it, and prints which it did. Leave off `--live` to see
what it would do without changing anything.

---

## Rules

- Never tick a to-do by editing the hotcache by hand from this skill. A close
  goes through the script, which is what records the evidence and keeps the
  undo.
- Never tell the user an item is done because the script closed it. Say what
  the evidence was, and that it can be reopened.
- Turning the whole thing off is a setting: `auto_close.enabled` in
  `/van-gogh:update-settings`.
