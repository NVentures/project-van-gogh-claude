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
- **Not now** — acknowledge in one line and stop. Do not nag; the notice
  reappears on the next run.

Apply command:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/plugin_update.py" --apply
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\plugin_update.py" --apply
```

This runs `claude plugin marketplace update van-gogh` — the one sanctioned
update path. It never pulls inside the plugin cache, because the next
marketplace re-clone would silently discard anything written there.

Read the result JSON:

- **`status: "ok"`** — updated. Tell the user the new version from
  `check.installed`, and add: "Restart this session to load it." The running
  session still holds the old plugin cache in memory; that line is not
  optional.
- **`status: "no_cli"`** — the `claude` binary is not on PATH. Whether it is
  depends on how Claude Code was installed, so treat this as expected, not
  broken, and do **not** treat it as a dead end. Tell the user:
  "I can't run the updater from here, but you can update in two clicks: open
  `/plugin` → Marketplaces → van-gogh → Update." Then mention that turning on
  auto-update for the `van-gogh` marketplace in that same menu keeps this
  current on its own.
- **`status: "error"`** — show the last line of `output` and point at the same
  `/plugin` menu as the manual path.

---

## Notes

- **This skill is the manual trigger, not the mechanism.** Every skill run
  already refreshes the check at most once a day
  (`plugin_update.check_quietly()`, called at `config_loader` import) and
  exposes the pending notice as `meta.update_notice` at no cost. The briefings
  deliberately do **not** print it: "Run `claude plugin ...`" is a present-tense
  imperative, which `DESIGN.md` reserves for `SEND` and `KICK OFF`. Housekeeping
  belongs here, not in the middle of someone's morning. (`meta.whats_new` is the
  one product line briefings do print, and only after an update: it is past
  tense and quotes no commands, see `skills/_shared/front-page.md` step 7.)
- **Auto-update is still the best answer.** If the user has it off, this is
  worth one sentence: `/plugin` → Marketplaces → van-gogh → enable auto-update
  refreshes the catalog *and* the installed plugin at session start, and the
  drift this skill detects stops happening.
- **Turning the check off:** set `VAN_GOGH_DISABLE_UPDATE_CHECK=1`.
