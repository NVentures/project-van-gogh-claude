---
name: operator-research
description: >-
  Research the user and their company on the public web, then file what it
  finds as entity pages, a profile in workspace memory, and suggested
  settings. Use when the user types /van-gogh:operator-research, asks Van Gogh
  to look them up, to research their company, to build or refresh their
  operator profile, or says the product does not know enough about them or
  their business yet.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Operator research

Reads the public web for who the user is and what their company does, then
files it into the vault so every briefing afterwards knows something about
them. Setup runs this in the background; this skill is how it gets run again,
on an install that predates the feature or when the facts have gone stale.

Nothing reaches the vault without the user reading a summary and saying yes.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Step 1 : who to research

Read what setup already knows, so the user is not asked to retype it:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/skill_context.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\skill_context.py"
```

Take `user.full_name`, `user.role_description`, the account email domains, and
the businesses. If there is exactly one business that is not `personal`, that
is the company. If there are several, ask with the AskUserQuestion tool
(header `Company`) which one this profile is about, listing their display
names. If the user named a company in their request, use that and skip the
question.

If `--status` was passed, run `status` from Step 3 and report it. Do nothing
else.

---

## Step 2 : run it

Start the research and wait for it. The user asked for this right now, so a
background run they have to come back for is worse than a wait they can watch.
Tell them it takes a few minutes before you start.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" start --name "<full name>" --company "<company>" --domains "<comma separated domains>" --role "<role line, if any>" --wait 900
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" start --name "<full name>" --company "<company>" --domains "<comma separated domains>" --role "<role line, if any>" --wait 900
```

Branch on the JSON:

- **`ok: false`** : report `why` in one line and stop. The usual cause is no
  `claude` binary on PATH, which is Step 1 of the install skill.
- **`ok: true`** with `final.state` `done` : go to Step 3.
- **`final.state` `failed`** : say what `final.reason` says, in the user's
  words, and offer to run it again. Nothing was written.
- **`final.timed_out`** : say it is still going and they can come back with
  `/van-gogh:operator-research --status`. Nothing was written.

---

## Step 3 : show it, and ask

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" show
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" show
```

Render `summary_md` verbatim. It is already written for the reader and already
carries the confidence, the evidence and the source count.

Then two questions, in order.

**First, is this them?** AskUserQuestion, header `Identity`:

- `Yes, that is me`
- `Right person, some details are wrong`
- `That is not me`

On `That is not me`, write nothing. Say the research is discarded, and ask
whether the company name should be spelled differently, then offer to run it
again with the correction. On `Right person, some details are wrong`, carry on
to the next question: the pages record their sources, so a wrong fact is
visible and correctable in the vault, and the profile is still worth having.

**Second, which priorities are real?** Only if `proposal.proposals.priorities`
is non-empty. AskUserQuestion, header `Priorities`, `multiSelect: true`, one
option per suggested priority using its `name`, plus a final option
`None of these`. These are guesses from public sources, so say that in the
question: "These are guesses from what it read. Which are actually on your
plate?"

---

## Step 4 : file it

Pass the indexes the user picked, as JSON, in the order they were shown in
`summary_md` (they are numbered from 0 there). `None of these`, or no
priorities offered, means pass `[]`.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/operator_research.py" apply --priorities-json "[0,2]"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\operator_research.py" apply --priorities-json "[0,2]"
```

Branch on the JSON:

- **`ok: true`** : report what changed in plain words, three lines at most.
  Name the entity pages by their page names, say the profile is now in the
  workspace memory that loads every session, and if `config_changes` carries
  `keywords_added` or `priorities_added`, say what was added to which
  business. Do not list file paths.
- **`ok: false`** : report `why`. The one deliberate refusal is an identity
  the research could not confirm, which files nothing. Tell them the company
  name is the usual fix.

If the output carries an `index_note`, a `memory_note` or a `config_note`, say
that one part did not land and what it was. A run that filed the pages but
could not reach settings is a partial success and reads as one.

---

## Notes

Re-running is safe and is the point: the memory profile is replaced in place
rather than stacked, an entity page the user has written on is appended to
rather than overwritten, and settings only ever gain keywords and the
priorities the user picked. The one thing it will not do is overwrite a role
description they already have.
