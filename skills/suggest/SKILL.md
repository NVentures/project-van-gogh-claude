---
name: suggest
description: >-
  Ask the advisor what is worth doing next, see what it would do, and answer
  each suggestion with a yes or a no. A yes does the work and leaves it for
  the user to check: a draft in their Drafts folder, a file, or a small
  correction to a vault page. Nothing is sent. Use when the user types
  /van-gogh:suggest or asks what they should be working on, what they are
  missing, what you would do next, to see their suggestions, to approve or
  dismiss a suggestion, or to switch the daily suggestions on or off.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Suggestions

The advisor reads the whole vault, picks at most a few things it could do for
the user, and asks. This skill is the terminal's way to see those and answer
them. The same suggestions also sit in the Workbench, appear inside Morning
Coffee and Afternoon Tea, and arrive by email when the daily look is switched
on (`advisor.enabled`); an answer given in any one place counts everywhere.

Code decides what may be suggested, not you. Every suggestion printed by the
script has already had its evidence found in the vault word for word, its
address checked against what the vault holds, and its edit checked against
the page. Show them as they are. Never add a suggestion of your own to the
list, and never reword one into something the script did not check.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Step 1: see what is waiting

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/advisor.py" --list
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\advisor.py" --list
```

It prints `{"waiting": [...]}`. Each row has `id`, `title`, `kind` (`draft`,
`file` or `vault`), `why`, `plan`, `evidence` (the vault page and the passage
it rests on), and for a vault correction `edits` (the exact text that would
change and what it would become).

If `waiting` is empty and the user asked for suggestions, go to Step 2. If
they only asked to see what is waiting, say nothing is, in one line, and stop.

## Step 2: look now, when asked

Only when the user asks for fresh suggestions, or nothing is waiting and they
asked what to work on. A look reads the vault with a model and takes a few
minutes, so say that before starting.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/advisor.py" --propose --force --no-mail
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\advisor.py" --propose --force --no-mail
```

`--no-mail` because the user is here: the list is shown in the chat, and an
email of the same list would be noise. The reply has `asked` (how many were
filed), `why` (the reason when it is zero), and `dropped` (one plain reason
per suggestion the checks refused). Then run Step 1 again and show the list.

When `asked` is 0, say so in one line using `why`. A quiet day is a normal
answer, not an error. When `dropped` is not empty, add one line saying how
many suggestions were set aside because their evidence could not be
confirmed. Do not list the reasons unless asked.

## Step 3: show them

One block per suggestion, numbered, in this shape and in the user's words:

```
1. {title}
   Why: {why}
   What I would do: {plan}
   From {evidence[0].path}: "{evidence[0].quote}"
```

For a `vault` suggestion, also show each edit, because the yes approves that
exact change:

```
   In {path}, this:   {find}
   would become:      {replace}
```

Then ask, for all of them at once, which are a yes and which are a no. A
suggestion the user does not mention stays waiting.

## Step 4: act on the answers

One command per answer, with the suggestion's `id`.

A yes, which does the work right away:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/advisor.py" --approve ID
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\advisor.py" --approve ID
```

A no, which is remembered so the same thing is never suggested again:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/advisor.py" --dismiss ID
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\advisor.py" --dismiss ID
```

Each prints `{"id", "title", "ok", "did"}`. Report `did` for each one as it
is written: it already says where the draft is, where the file is, or which
page was corrected and that the earlier version was kept. When `ok` is false,
`did` says why in plain words. Report it and do not retry.

## Sending

A yes to a draft leaves a draft. It never sends.

Send one only when the user says, about that specific message, to send it.
Tell them first that it goes exactly as it stands in their Drafts folder,
including anything they changed there.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/advisor.py" --send ID
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\advisor.py" --send ID
```

If the reply says it may not have been sent, tell the user to look in their
Sent folder before anything else. Never run the command a second time for the
same message.

## Switching the daily look on or off

The daily look is off until asked for. It is controlled by the `advisor`
block in the vault's config, changed through `/van-gogh:update-settings`:

- `enabled`: whether the watcher asks for suggestions once a day.
- `time`: the local time after which that day's look may happen.
- `daily_cap`: how many suggestions at most, one to five.
- `allow_send`: whether an email reply of `SEND` and a number may send a
  draft. Off unless the user asks for it by name, and say what it does
  before turning it on.

When it is on, the user answers by replying to the email with `Y` or `N` and
the number on the first line, or with KICK OFF and Dismiss in the Workbench.
