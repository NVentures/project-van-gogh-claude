---
name: update-digest-preferences
description: Opt in or out of emailed briefings and manage their schedule — the
  email digest that sends Morning Coffee, Afternoon Tea, Week, and Week Retro
  to your inbox automatically. Use when the user says "email me my briefings",
  "set up the digest", "update digest preferences", "change when my digest
  sends", "stop emailing me", "change the digest sender/recipient", or anything
  else about receiving briefings by email.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Update Digest Preferences

Your job: manage the `digest` block of `config.json` conversationally, then
apply the schedule by running the scheduler sync. The digest emails each
rendered briefing to the user on a schedule (computer-local time):

| Briefing        | Default cadence      | Subject                                   |
|-----------------|----------------------|-------------------------------------------|
| Morning Coffee  | Mon-Fri 7:00 AM      | `[Project Van Gogh] Morning Coffee MM-DD-YYYY` |
| Afternoon Tea   | Mon-Fri 1:00 PM      | `[Project Van Gogh] Afternoon Tea MM-DD-YYYY`  |
| Week            | Monday 7:00 AM       | `[Project Van Gogh] Week MM-DD-YYYY`           |
| Week Retro      | Friday 7:00 AM       | `[Project Van Gogh] Week Retro MM-DD-YYYY`     |

The whole digest is **opt-in** (`digest.enabled`, default off). By default it
sends **from and to the primary configured account**; both are overridable.

## What's configurable (the `digest` block)

- `enabled` — master opt-in (bool).
- `sender_label` — label of the configured account to send from (`""` = the
  primary account). Must be one of the labels in `accounts` — sending uses that
  account's OAuth token, so an arbitrary address cannot be a sender.
- `recipient_email` — where digests are delivered (`""` = the primary
  account's address). Any address is fine here.
- `briefings.<name>` — per-briefing `enabled` (bool), `days` (list of
  lowercase weekday names), `time` (24h `"HH:MM"`, computer-local time), for
  `morning-coffee`, `afternoon-tea`, `week`, `week-retro`.

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

## Preconditions — check before editing anything

1. **Tier 1 required.** Digests send headlessly via OAuth; connector (Tier 2)
   mode cannot send. Check:

   macOS / Linux (bash/zsh):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
   ```

   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from data_sources import is_tier1; print(is_tier1())"
   ```

   If `False`, stop: tell the user the digest needs a connected account —
   run `/van-gogh:add-account` (or re-run `/van-gogh:install-van-gogh` OAuth) first.

2. **macOS or Windows only.** Linux has no scheduler integration; on Linux,
   explain that `app/digest_send.py <briefing>` can be run from cron manually,
   and still offer to update the config.

## How to run the skill

1. **Read the current settings.** Resolve the config path:

   macOS / Linux (bash/zsh):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
   ```

   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); from config_loader import van_gogh_root; print(van_gogh_root() / 'config.json')"
   ```

   Read it and show the digest settings as a short bullet list (opted in or
   not, sender, recipient, each briefing's cadence in plain English). If the
   config has no `digest` block yet, show the defaults from the table above
   and say the digest is currently **off**.

2. **Gather the changes conversationally.** If the user just said "set up the
   digest", confirm: opt in with the default cadences, sending from and to the
   primary account? One question at a time for anything they want to change.
   Validate: `sender_label` must match a configured account label; `days` are
   lowercase weekday names; `time` is 24h `HH:MM`.

3. **Preview, then wait for an explicit yes.** Show before/after in plain
   English (e.g. "I'll turn the digest on, sending from Gmail to
   you@gmail.com, with Afternoon Tea moved to 3:00 PM").

4. **Back up, then write.** Copy the vault `config.json` to `config.json.bak`
   beside it, then apply a minimal targeted Edit — add or modify only the
   `digest` block (full shape in `config.template.json`). Preserve key order
   and 2-space indentation.

5. **Verify the JSON parses.** macOS/Linux:
   `"$HOME/.config/van-gogh/venv/bin/python" -c "import json; json.load(open(r'{config path}', encoding='utf-8'))"`
   (PowerShell: same one-liner with `& "$HOME\.config\van-gogh\venv\Scripts\python.exe"`).
   If it fails, restore the backup and tell the user what broke.

6. **Apply the schedule.** Run the scheduler sync — this installs one
   background job per opted-in briefing and removes the jobs for everything
   disabled (idempotent, safe to re-run):

   macOS / Linux (bash/zsh):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" sync-digests
   ```

   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\scheduler_setup.py" sync-digests
   ```

   Read the output: one `OK: ... scheduled (...)` line per enabled briefing
   means done. `ERROR: ... 'claude' CLI was not found on PATH` means the
   `claude` binary isn't on PATH — the scheduled run needs it; have the user
   fix PATH and re-run this one command. Show any other `ERROR:` line to the
   user.

7. **Offer a test send.** If the user opted in, offer to email one right now
   without waiting for the schedule (uses the already-rendered file, no
   re-render — pick a briefing whose file exists, `week` is usually safest):

   macOS / Linux (bash/zsh):
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/digest_send.py" week --no-render
   ```

   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\digest_send.py" week --no-render
   ```

   A JSON line with `"status": "sent"` confirms delivery — tell the user to
   check their inbox.

8. **Confirm in one line.** What changed, where the backup is, and when the
   next digest will arrive.

## Rules

- **Never** edit config.json without an explicit yes on a concrete preview,
  and **always** back up first (`config.json.bak`).
- **Always** run `sync-digests` after any digest config change — the config
  and the scheduled jobs must never drift apart. (Turning the digest off also
  goes through `sync-digests`; it removes the jobs.)
- Scheduled runs are headless and non-interactive: the briefing renders with
  no one to answer its optional prompts (e.g. Afternoon Tea's one-tap
  confirmations are skipped). Mention this once when the user first opts in.
- Times are the **computer's local time zone** (the scheduler fires on the
  machine's clock), not the config `timezone` field.
- Missed runs differ by OS: Windows tasks use `-StartWhenAvailable` (a missed
  run fires when the machine becomes available); macOS launchd catches up
  after sleep but a run missed while the machine was powered off is skipped
  entirely. Either way `digest_send.py` re-checks the configured days at fire
  time, so a catch-up can never send a digest on the wrong day.
