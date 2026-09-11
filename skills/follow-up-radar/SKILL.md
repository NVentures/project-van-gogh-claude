---
name: follow-up-radar
description: Date-triggered follow-up tickler. Reads the Pending follow-ups ledger, surfaces which relationship threads are due, overdue, or due soon by their trigger dates, checks the named inbox for a reply, and drafts a value-add nudge (fresh source first, never a bare check-in) in the user's voice for each one that still needs a push. Use when the user types /van-gogh:follow-up-radar or asks what follow-ups are due, who they owe a reply, which relationships need a nudge, or to run their tickler.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Follow-up Radar (the tickler)

The user carries a running list of open relationship threads — each with a
trigger date ("if unheard by Thu 2026-07-23, follow up"), a mailbox to watch,
and often a note that the nudge must be **value-add, never a bare check-in**.
This skill fires those triggers on time and turns each due one into a
ready-to-review draft. Nothing is auto-sent — the user reviews and sends.

A Python script does the deterministic part (find the ledger, parse dates,
classify each item's status against today). You do the judgment part: confirm
whether a reply already landed, pull a fresh hook, and draft in the user's voice.

The ledger is a "## Pending follow-ups" section in the vault workspace memory
(`{vault}/van-gogh/projects/Project Van Gogh/memory.md` by default; override
with `follow_ups.memory_path` in config).

## The rule that makes this worth doing

**A nudge is never "just checking in."** Every follow-up the user sends leads
with something new and useful — a current podcast, article, case study, or a
specific insight tied to *that person's* thesis — and the ask rides along behind
it. That is the whole business-development motion this ledger exists to drive.
If you can't find a genuine value-add hook for a due item, say so and draft a
lighter touch or hold, rather than shipping a hollow "any update?".

## Resolved values come from the script

`follow_up_radar.py` emits JSON with a top-level `meta` block (same shape every
skill gets). Reference these verbatim — never hardcode:

- `meta.accounts[]` — `{label, provider, email, is_primary}`. Map each item's
  `watch_inbox_hints` (e.g. `"Work Outlook"`, `"Acme inbox"`) to an account
  by matching the hint's leading word against `label`.
- `meta.user_first_name`, `meta.user_full_name`
- `meta.voice_guide_path`, `meta.tone_profile_path` — prime every draft's voice.

---

## Python runtime

**Python runtime:** before the first script invocation, run the ensure-venv
guard in [`_shared/python-runtime.md`](../_shared/python-runtime.md).

---

## 1 — Run the script

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/follow_up_radar.py"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\follow_up_radar.py"
```

Options (rarely needed): `--today YYYY-MM-DD` to preview a future day,
`--horizon N` to widen the due-soon window (default 4 days), `--memory PATH` to
point at a specific ledger. If it prints `MEMORY_NOT_FOUND`, the vault memory
file has no ledger yet — offer to add a "## Pending follow-ups" section to it,
or tell the user to set `follow_ups.memory_path` in config (via
`/van-gogh:update-settings`) if their ledger lives elsewhere.

Read the full JSON. Each item has: `title`, `status`
(`overdue`|`due`|`due_soon`|`watch`|`scheduled`|`dormant`), `earliest_trigger`,
`trigger_dates`, `context_dates`, `hard_trigger`, `value_add`, `suppressed`,
`watch_inbox_hints`, `topic_files`, and `raw` (the full ledger bullet — your
source for who the counterparty is, the thread subject, and what's owed). The
`actionable` array is the union of `overdue` + `due` + `due_soon` — start there.

## 2 — Triage each actionable item

Work `actionable` in order (overdue first, then due, then due_soon). Give
`hard_trigger` items priority — those are the user's own "do not let this slip"
markers. For each item:

**a. Check whether a reply already landed.** Resolve the item's
`watch_inbox_hints` to an account in `meta.accounts`, then search that mailbox
for a recent message from the counterparty (get the name/thread from `raw`),
dated after the item's most recent `context_dates` (the last action). Use the
connector for that account:
- Gmail account → Gmail connector.
- Outlook account → Microsoft/Outlook connector.

If a reply **has** landed: the trigger is likely satisfied. Don't draft a nudge
— summarize the reply, say the item can probably be cleared or now needs a
substantive response instead, and let the user decide. Note it so they can
remove the bullet from the ledger.

**b. If no reply, build the value-add hook.** Read the `topic_files` context and
`raw` for the counterparty's thesis (what they care about — their GTM/ROI angle,
their fund's focus, the deal in play). Then find something genuinely fresh and
relevant: a recent podcast episode, article, or case study (use web search for
something current), or a specific insight from the user's recent work. This hook
is the reason the message exists; the ask follows it.

**c. Draft the nudge in the user's voice.** Prime with `meta.voice_guide_path`
and `meta.tone_profile_path`. Lead with the value-add hook, then the light ask.
Keep it short, direct, warm, specific to shared context — no "just circling
back," no hedging, no AI/consultant filler. End with a concrete next step
(a call offer, a question). Show the draft inline.

## 3 — Prepare the draft, never send

Match the reachable-mailbox reality:
- If the watch account is a **Gmail** the connector can reach: offer to create a
  Gmail **draft** (never send). Confirm before creating it.
- If the watch account is **Outlook**: confirm first, then **always attempt**
  `app/outlook_draft.py` — it creates a real **draft** via the Graph client
  (`POST /me/messages` drafts, it does not send). Never decide the tier
  yourself by inspecting environment variables or connector capabilities — the
  token lives in `~/.config/van-gogh/.env`, which only the script can read, so
  **the script is the probe**. Write the nudge body to a temp file, then:

  macOS / Linux (bash/zsh):
  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/outlook_draft.py" --label "<ACCOUNT_LABEL>" --to "<RECIPIENT_EMAIL>" --subject "<SUBJECT>" --body-file "<BODY_FILE>"
  ```

  Windows (PowerShell):
  ```powershell
  & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\outlook_draft.py" --label "<ACCOUNT_LABEL>" --to "<RECIPIENT_EMAIL>" --subject "<SUBJECT>" --body-file "<BODY_FILE>"
  ```

  On `{"ok": true, ...}` share the `web_link`. On
  `{"ok": false, "error": "no_token", ...}` the machine is Tier 2 for that
  account — fall through to ready-to-paste below (and mention
  `/van-gogh:add-account` can connect the account for real drafts).
- Otherwise (Gmail draft failed / Outlook `no_token` or `draft_failed`): put
  the full **ready-to-paste** message (To / Subject / Body) in your reply for
  the user to send from that mailbox.

**Sending is the user's call.** Drafting and web search are fine without asking;
creating a draft, and above all sending, require an explicit yes each time. Do
not send on the user's behalf.

## 4 — Round out the picture

After the actionable items, briefly:
- List **watch** items (open, no trigger date) as one-liners — these are threads
  with no firm date that the user may still want to move (e.g. a value-add
  follow-up owed with no deadline). Offer to draft any on request.
- Give a one-line count of **scheduled** (trigger in the future — nothing to do
  yet, name the date) and **dormant** (deferred / decided-not-to-send / parked
  until a future month) so the user sees they're tracked, not lost.

---

## Edge cases

- **Nothing actionable** (`actionable` empty): good news — say what's on the
  horizon instead (the nearest `scheduled` date) so the user knows the next
  trigger. Don't manufacture work.
- **Reply already landed on several:** lead with those — clearing stale bullets
  is as valuable as sending nudges. Offer to note which ledger lines to remove.
- **A `suppressed`/dormant item looks like it should fire:** the ledger marked it
  deferred, decided-not-to-send, or parked (e.g. a November check-in). Respect
  that; mention it only if the user asks why it's not surfacing.
- **No value-add hook exists for a due item:** don't ship a hollow nudge. Say so,
  and either draft a genuinely light touch or recommend holding until there's
  something worth sending.
- **This skill reads `app/`-parsed data but does not edit the ledger or `app/`.**
  If parsing looks wrong (a date misread, an item mis-bucketed), tell the user;
  only change `app/follow_up_radar.py` if they ask.

## When to ask the user

- Before creating any draft, and always before sending.
- When a reply's content means the follow-up needs a real answer, not a nudge —
  surface it and ask how they want to respond.
- When an item's counterparty or mailbox can't be resolved from `raw` +
  `meta.accounts` — ask rather than guessing the recipient.
