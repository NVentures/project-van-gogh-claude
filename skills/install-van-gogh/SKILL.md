---
name: install-van-gogh
description: >-
  First-time setup for Project Van Gogh, run by the orchestrator in
  app/setup_flow.py: accounts signed in, settings confirmed, background jobs
  scheduled, deals and people seeded, meetings filed, first briefings shown,
  with five questions and nothing to install by hand. Use when the user says
  "install van gogh", "set up van gogh", "finish setup", or runs first-time
  setup; the same command resumes an install that stopped early.
disable-model-invocation: true
allowed-tools:
  - Write
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Agent
---

# /van-gogh:install-van-gogh

The install is a loop. One script decides everything and keeps the checklist
at `~/.config/van-gogh/setup/checklist.json`; you call it, read one JSON
object, and do what `kind` says. Never run a command that this page, or a page it sends you to, does not show; never guess a
step, never paraphrase a line the script hands you. Running this again after
a crash, a closed laptop or a new session resumes at the first open box and
never asks again for an answer it already has (a question comes back only when the answer did not work or the person changed something).

## The loop

<!-- codex-first-chat -->
Under Codex, before anything else: when `$CLAUDE_PLUGIN_ROOT` is empty in a
command you run (`echo "$CLAUDE_PLUGIN_ROOT"`, or `echo $env:CLAUDE_PLUGIN_ROOT`
on Windows), this chat started before Van Gogh's settings were in place (the
plugin was installed from the Codex app's plugin screen, and Codex reads the
settings only when a chat starts). Run this one command, and nothing else, in
this chat. It decides everything itself: it prepares the computer when nothing
has (about two minutes; it writes Codex's settings and Van Gogh's own folder,
so approve it when Codex asks), and refuses on a computer that runs Van Gogh
under Claude. Then print its one line exactly as given and stop.

macOS / Linux (bash/zsh):
```bash
bash "$(ls -d "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/van-gogh/van-gogh/*/ | sort -V | tail -n 1)hooks/setup_notice.sh" --codex --first-chat
```

Windows (PowerShell):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ((Get-ChildItem "$(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" })\plugins\cache\van-gogh\van-gogh" -Directory | Sort-Object { [version]$_.Name } | Select-Object -Last 1).FullName + "\hooks\setup_notice.ps1") -Codex -FirstChat
```
<!-- /codex-first-chat -->

Call `next`:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/setup_flow.py" next
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\setup_flow.py" next
```

Read the JSON and branch on `kind`. Whatever the kind, when the object (or
its `prompt`) has a `show` field, print it first, exactly as given and inside
a code block so its columns line up: it is how the client learns, at the
moment it happens, what was just scheduled on their computer.

- `prompt`: print `show` first (above). Under Codex the table is already
  inside `prompt.question`, so ask the question exactly as given, table and
  all. Then ask with the
  AskUserQuestion tool, using `prompt.header` as the
  header, `prompt.question` as the question, every entry of `prompt.options`
  as an option (label and description), and `prompt.multi` as `multiSelect`.
  Then send the answer back in a file, never on the command line (a
  notetaker key would otherwise be visible to every process on the machine).
  Write one JSON object, `{"id": "<prompt.id>", "value": <the answer>}`, to
  the path in `prompt.answer_file` with the Write tool, then call `next` with
  that path. The script reads the file and deletes it.

  macOS / Linux (bash/zsh):
  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/setup_flow.py" next --answer-file "<prompt.answer_file>"
  ```

  Windows (PowerShell):
  ```powershell
  & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\setup_flow.py" next --answer-file "<prompt.answer_file>"
  ```

  The value per prompt id: `notetaker` is `{"provider": "granola", "key": "<the pasted key>"}`
  (or `"grain"`); when they tap Granola or Grain, ask for the key as a plain
  reply first, and never echo it. The question's third option is
  "None, or another notetaker"; when they tap it, the value is that label.
  `confirm` is `"Yes, continue"`, or an object of the fields they want changed
  (`full_name`, `first_name`, `company`, `timezone` exactly as they said it,
  a city is fine, and `vault_path`); the script shows the screen again with
  the change.
  `schedule` is the tapped label for `Keep these` and `Schedule nothing`.
  When they tap `Change something`, ask "What should change?" as a plain
  message, wait for their reply, and send what they said as
  `{"changes": [...]}`, one entry for each job they named:
  `{"job": "<the job's name as the table prints it>", "on": true or false, "time": "HH:MM on a 24 hour clock", "days": ["monday", ...], "mode": "each" or "daily"}`,
  leaving out every field they did not mention. `mode` is for meeting prep
  only: `daily` is one email each morning and `each` is one before every
  call. Never decide for them whether a change is allowed and never correct
  it: send it as they said it. The script checks every change, shows the
  table again with the changes marked, and says in the question what it
  could not place. They can change more or keep it. `priorities` is the list of ticked labels
  plus anything they typed, in their words. `deals` is the list of ticked
  labels, or `["None of these"]`. `account_mismatch` (asked only when the
  signed-in address differs from the key file) is the tapped label.
  `signin_link` (asked only when no browser could open on this computer):
  show the question exactly as given, it carries the link or the code. For
  Google the value is `{"label": "I signed in", "address": "<the pasted address>"}`,
  the address being their next plain reply; for Microsoft it is the tapped
  label.
  Call `next` again after every answer; the script may ask the same question
  again, and then the reason leads `prompt.question` (a key that did not work,
  a folder that could not be created), so ask it exactly as given.
- `wait`: say `say` once, in one short line, then sleep `poll_seconds`
  (put that number in place of the 15 below) and call `next` again. Say
  nothing more while the same wait repeats.

  macOS / Linux (bash/zsh):
  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" -c "import time; time.sleep(15)"
  ```

  Windows (PowerShell):
  ```powershell
  & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import time; time.sleep(15)"
  ```
- `dispatch`: launch both entries of `agents` in ONE message with the Agent
  tool, each agent's prompt being exactly its `prompt` field (for example
  `/van-gogh:week`), say `say` once, wait for all of them to finish, then
  call `next`. Under Claude, launch both at once rather than one after the
  other, so the first briefings arrive together. Under Codex `inline` is
  true (no Agent tool): say `say`, then run the entries yourself, one after
  the other, each by following its skill (they arrive a few minutes apart
  there), and call `next` when both are done.
- `error`: print `line` verbatim and stop, with the one exception below. It already names the resume
  command. When `box` is `credentials` and the line says the key file is not
  in Downloads, the client has no key file from the Van Gogh team: read
  [references/own-credentials.md](references/own-credentials.md) and follow it,
  then call `next` again. For any other sign-in failure the one line is the
  whole answer; [references/oauth-failure-handling.md](references/oauth-failure-handling.md)
  explains the classes if the client asks why.
- `done`: print `closing_text` verbatim. Vincent then adds the one line his
  own rules give him; anyone else stops there. When `already_installed` is
  true nothing ran, and the same text says so.

Show the client the `elapsed` field only if they ask how long it has been.

## What the schedule question turns on

The `schedule` prompt is the one place the background jobs are offered, so it
shows every one of them before it asks. `prompt.show` is that table: each job
that can run by itself, when it would run on this computer's clock, and
whether it starts on or off.

Starting on:

- Morning Coffee each weekday morning, Afternoon Tea each weekday afternoon,
  Week on Monday and Week Retro on Friday
- one morning email with the day's meeting preps (the meeting prep row, `mode` `daily`)
- a weekly look at who is going cold
- learning from the mail you sent this week
- refreshing your tone profile
- the Workbench at login
- three early-morning jobs: filing your meeting notes,
  syncing your project notes, and closing finished to-dos for you. The
  third closes a to-do only when your own mail or meetings prove it is
  finished, shows what it closed in the next briefing, and any close can
  be put back with `/van-gogh:done-judge`
- the watcher that restarts any of them that fail

Starting off, and offered in the same table:

- a meeting prep emailed to you before each call, in place of the morning one (the same meeting prep row with `mode` `each`; the table shows one row)
- the weekly finance brief
- saving new contacts to your address book
- checking for meetings nobody recorded
- drafting your weekly client reports
- the watcher between briefings, which sends a short Note when something moves (a different job from the one that restarts failed jobs)

`Keep these` writes the table as shown, `digest.enabled` included, and
schedules it. `Schedule nothing` schedules nothing; every skill still works
when run by hand. Either way, Vincent (`/van-gogh:vincent`) changes any of it
later when asked.

## Rules

- Every question that comes with options goes through AskUserQuestion with
  the options the script gave, so the client taps rather than types. Five
  questions on the happy path: notetaker, confirm, schedule, priorities,
  deals. The answers that are typed on purpose (a notetaker key, what
  should change in the schedule, an address, the field changes at confirm,
  and any free text under priorities) arrive as plain messages. The
  schedule question is asked again each time they
  change something, until they keep a table or choose nothing.
- Nothing the script prints is edited: `line`, `say` and `closing_text` are
  shown as they are. Do not add summaries, do not describe what a step does.
- Never print a token, a key, or the contents of the key file. The script
  never does either; keep it that way in your own messages.
- If a command fails to run at all (the interpreter is missing), tell the
  client to run the README's install line in Terminal again, then this
  command.
