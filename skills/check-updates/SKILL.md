---
name: check-updates
description: Check whether a newer version of the Van Gogh plugin has been published, and offer to install it. Use when the user types /van-gogh:check-updates or asks "am I up to date", "is there a new version", "check for updates", "update Van Gogh", or "what version am I running".
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Check for updates

Compares the installed plugin version against the one published on the
marketplace's `main` branch, then offers to apply the update.

The check is a single unauthenticated HTTPS read of the published
`plugin.json`. It deliberately does **not** use `git`, `gh`, or the `claude`
CLI, so it behaves identically in the Claude Desktop app, a terminal session,
and an unattended scheduled run. Applying the update does need the `claude`
CLI; the script reports that cleanly instead of failing when it is absent.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## Run the check

Always pass `--force`: the user asked right now, so the once-a-day throttle
that keeps this cheap on ordinary skill runs does not apply here.

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/plugin_update.py" --force
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\plugin_update.py" --force
```

The script prints one JSON object. Branch on `status` and `update_available`:

- **`status: "ok"`, `update_available: true`** — an update exists. Report it in
  one line naming both versions. When the output carries `pending_notes_md`
  (the release notes for the versions the user would gain, fetched from the
  published CHANGELOG.md), render it verbatim under a "What you'd get:" line —
  it is already written for a non-technical reader. When it is absent or empty
  (offline, or the changelog could not be read), skip it silently. Then go to
  **Offer to apply** below.
- **`status: "ok"`, `update_available: false`** — say so in one line, quoting
  `installed`: "You're on 0.44.0, the current version." If the user asked what
  changed recently (not just whether an update exists), show the current
  version's entry from `${CLAUDE_PLUGIN_ROOT}/CHANGELOG.md` (read the file;
  each `## <version>` section is self-contained and written for a
  non-technical reader). Otherwise stop. Do not offer to update, and do not
  run anything else.
- **`status: "unknown"`** — the published version could not be read (offline,
  proxy, captive network). Say: "Couldn't reach GitHub to check — you're on
  `<installed>`." Stop. This is not an error worth troubleshooting; the check
  fails open by design.
- **`status: "disabled"`** — `VAN_GOGH_DISABLE_UPDATE_CHECK` is set on this
  machine, usually a centrally managed install. Say so and stop.

Never present a `status` other than `ok` as a failure the user must fix.

---

## Offer to apply

Only when `update_available` is true. Ask with the AskUserQuestion tool
(header `Update`): "Van Gogh `<published>` is available — you're on
`<installed>`. Install it now?"

- **Yes, update now** (recommended) — run the apply command below.
- **Not now**: acknowledge in one line and stop. Do not nag. (A no given
  here is not recorded, so the briefings still ask once about this version.)

Apply command:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/plugin_update.py" --apply
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\plugin_update.py" --apply
```

This runs `claude plugin marketplace update van-gogh` and then
`claude plugin update van-gogh@van-gogh`: the first only refreshes the catalog,
the second is what moves the installed plugin. It never pulls inside the plugin
cache, because the next marketplace re-clone would silently discard anything
written there.

Read the result JSON:

- **`status: "ok"`** — updated. Tell the user the new version from
  `version`, and add: "Restart this session to load it." The running
  session still holds the old plugin cache in memory; that line is not
  optional.
- **`status: "no_cli"`** — the `claude` binary is not on PATH. Whether it is
  depends on how Claude Code was installed, so treat this as expected, not
  broken, and do **not** treat it as a dead end. Tell the user:
  "I can't run the updater from here, but you can update in two clicks: open
  `/plugin` → Marketplaces → van-gogh → Update." Then mention that turning on
  auto-update for the `van-gogh` marketplace in that same menu keeps this
  current on its own.
- **`status: "unchanged"`**: the command ran clean and the installed version
  did not move. Never report this as updated. Say so in one line, quoting
  `version`, and point at the same `/plugin` menu as the manual path.
- **`status: "error"`** — show the last line of `output` and point at the same
  `/plugin` menu as the manual path.

---

## After an update: the scheduling review

Features keep being added, and **an existing install never re-runs the install
skill.** So someone who set Van Gogh up before a feature existed is never
asked about it by any other path. That is not hypothetical: the four emailed
briefings shipped switched off, and the only place that ever offered them was
an install step that had already run. Every machine installed before then has
been quietly getting none.

So after an update lands, check once whether anything schedulable has never
been offered, and ask about just those.

**Only do this when an update was actually applied in this session**, or when
the user asks for it directly. It is not part of a plain version check.

### 1. What is installed, and what has been offered

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" status
```

Read the vault `config.json` `scheduler` block: `reviewed` is the version whose
questions have been answered (empty means never), and `declined` lists the job
keys the user has already said no to.

A job is worth asking about when **all three** hold:

- its feature is off in config (so it is not already running),
- its key is not in `scheduler.declined` (a no is an answer, and asking again
  after someone declined is nagging),
- and `scheduler.reviewed` is older than the current plugin version.

If nothing qualifies, say nothing about scheduling at all. Silence is the
correct output of a review with nothing to review.

### 2. Ask

Use the **same questions as install Step 8**, in the same words, for whichever
features qualify. Do not invent new phrasings: the install text is what the
guard test checks against, and two descriptions of one feature is how a user
ends up unsure whether they are the same thing.

Ask only about what qualifies. If only the briefings were never offered, ask
only 8b. Lead with one sentence saying why they are being asked now, for
example: "This update added the briefing emails, which your install predates."

### 3. Write, install, and stamp

Write each answer into its config block exactly as Step 8 does. Record every
`No thanks` as the job's key in `scheduler.declined`, so the next update does
not ask again. Then:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" install
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/scheduler_setup.py" sync-digests
```

Finally set `scheduler.reviewed` to the version that is now installed. **This
stamp is what makes the review idempotent**: without it, every update re-asks
every question, which is worse than never asking.

Read the output the same way Step 8 does: `OK: ...` per job is done, and any
`ERROR: ...` goes to the user before you move on.

---

## Notes

- **This skill is the manual trigger, not the mechanism.** Every skill run
  already refreshes the check at most once a day
  (`plugin_update.check_quietly()`, called at `config_loader` import) and
  exposes the pending notice as `meta.update_notice` at no cost. The briefings
  turn it into one question after everything else has rendered, and install on
  a yes (`skills/_shared/front-page.md` step 13). They never print a command:
  "Run `claude plugin ...`" is a present-tense imperative, which `DESIGN.md`
  reserves for `SEND` and `KICK OFF`. A no there is remembered, so the question
  returns only when a newer version is published.
- **Auto-update is still the best answer.** If the user has it off, this is
  worth one sentence: `/plugin` → Marketplaces → van-gogh → enable auto-update
  refreshes the catalog *and* the installed plugin once per session, and the
  drift this skill detects stops happening.
- **Turning the check off:** set `VAN_GOGH_DISABLE_UPDATE_CHECK=1`.
