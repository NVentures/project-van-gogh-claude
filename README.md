<div align="center">

<img src="assets/banner.png" alt="Project Van Gogh" width="100%"/>

**A personal chief of staff, running entirely on your machine.**

Daily briefings. Weekly priorities. Meeting notes filed into your second brain. All wired together from your Gmail, Outlook, calendar, and Obsidian vault, no SaaS, no subscriptions, no data leaving your laptop.

</div>

---

## The Four C's

Van Gogh is four things, and everything in it belongs to one of them.

| | What it is |
|---|---|
| Context | Your second brain. Everything it learns or produces is written to your vault, so each run starts from more than the last. |
| Connectors | The systems it reads: mail, calendar, your notetaker, your books, and any other app you have connected in Claude. |
| Capabilities | What it can do: every skill below, and Vincent, the chief of staff who knows which exist and hands your request to the right one. |
| Cadence | When each capability fires without being asked, and the watcher that checks it did. |

Meeting prep is the plainest example. The capability is the prep itself. It reads the context (who these people are, what was promised), reaches through connectors (the live mail with each attendee, and your CRM if you have connected one), and its cadence is one email each morning covering the day's calls. [FOUR-CS.md](FOUR-CS.md) maps every skill and every scheduled job onto the four.

## What it does

Project Van Gogh is a [Claude Code](https://claude.ai/code) **plugin** — a set of skills backed by Python scripts, installed and auto-updated through the Claude Code plugin marketplace built into this repo. Each morning it reads your open deals, flags what's slipping, pulls your calendar, and gives you a focused briefing. Every week it synthesizes your email threads into a priority list. After every meeting it files your Granola or Grain transcript into structured vault pages.

**The daily loop** — your recurring briefings.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:vincent` | Any time, for anything | Vincent, the chief of staff. He knows every skill and agent on your computer, whether each scheduled job ran, and what you asked him to remember. He opens with anything that did not run and why, then hands whatever you ask for to the skill or agent that does it. Changes to your accounts or settings, repairs and re-runs wait for your yes. He never sends anything |
| `/van-gogh:morning-coffee` | Every morning | Open deals, calendar, slipping threads; deal kill/pass alerts; what was closed for you overnight; hotcache sync |
| `/van-gogh:afternoon-tea` | End of day | Sent + inbound mail + meetings + tomorrow's calendar; deal kill/pass alerts; what was closed for you today |
| `/van-gogh:week` | Monday morning | Weekly priorities from email + calendar + vault; inbound kill/pass scan; meeting cross-ref |
| `/van-gogh:week-retro` | Friday | Retro summary + full deal coverage (deaths, at-risk, likely-handled), saved as a vault source page |
| `/van-gogh:note` | Every 15 minutes (auto, opt-in) | The watcher between briefings: a reply on a front-page item, a deal pass in words, or a moved meeting becomes a short Note with the reply pre-drafted; at most two a day, never mid-meeting |
| `/van-gogh:suggest` | Any time, or daily (auto, opt-in) | The advisor reads the whole vault, suggests at most a few things it could do, and does them on a yes: a draft, a file, or a small vault correction. Answer by email reply, in the Workbench, or in chat. Nothing is sent on a yes |
| `/van-gogh:five-fifteen` | Friday, on request or scheduled (opt-in) | Weekly 5:15 client reports: per-client look-back and look-forward from email, Slack, Teams, HubSpot and meetings, a vault copy and a review-ready email draft. Never sent for you |

**Meetings and notes** — capture every call into the vault.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:meeting-prep` | Before any call, or every morning by email (auto, opt-in) | A one-page brief per call: who is on it, the context, what was last said, the live mail with each person, what your CRM holds on them, what is still open, and what you want out of the call. Says First meeting for someone new and shows only the sections that have something in them. Mailed as Daily Meeting Prep with the date, one card per call |
| `/van-gogh:meeting-ingest` | After any meeting | Meeting transcript → entity pages + dev log + action items + deal-stage advance |
| `/van-gogh:calendar-stub-check` | On request, or every morning (auto, opt-in) | Stubs unrecorded meetings into the vault so nothing slips through |
| `/van-gogh:ingest-workspace` | On demand | Sync workspace project changes to vault dev logs |

**Voice and relationships** — sound like yourself, keep contacts warm.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:relationship-radar` | On request, or Monday morning (auto, opt-in) | Flags contacts going cold (30d yellow, 60d red), drafts check-ins in your voice |
| `/van-gogh:follow-up-radar` | Daily / on demand | Date-triggered tickler: surfaces due/overdue follow-ups from the Pending follow-ups ledger, drafts value-add nudges |
| `/van-gogh:voice-generator` | On request, or each Sunday (auto, opt-in) | Tone-of-voice profile from 90 days of sent mail, segmented by audience. The profile it replaces is kept beside the new one |
| `/van-gogh:voice-bootstrap` | New-user setup | Build a voice style guide from scratch from your writing samples |
| `/van-gogh:voice-calibration` | On request, or Sunday evening (auto, opt-in) | Learns voice patterns from the week's sent mail, appends to the Voice Snapshot |

**Setup and maintenance** — install, configure, update, tear down.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:install-van-gogh` | Once | Interactive first-time setup wizard |
| `/van-gogh:migrate-from-legacy-van-gogh` | Once, if upgrading | Move a legacy standalone (git-clone) install into the plugin — tokens, vault pointer, config blocks, podcast feeds, and the follow-up ledger |
| `/van-gogh:update-settings` | When config changes | Conversational editor for `config.json` (accounts, vault, timezone, businesses, filters) |
| `/van-gogh:update-digest-preferences` | To get briefings by email | Opt in/out of the emailed briefing digest, set its cadence, sender, and recipient; schedules the background send jobs |
| `/van-gogh:operator-research` | Once at setup, then when it goes stale | Reads the public web for who you are and what your company does, then files entity pages, a session profile, and suggested keywords and priorities for your approval |
| `/van-gogh:add-account` | Anytime | Add another Gmail or Outlook account (runs OAuth, appends it to your accounts list) |
| `/van-gogh:add-connector` | Anytime | Let Van Gogh ask another system about a person: a CRM, a help desk, anything already connected in Claude. You pick the tools, it proves the connection with one real lookup, and meeting prep starts including what that system holds on each attendee |
| `/van-gogh:remove-account` | Anytime | Remove an account, optionally clean its stored token, reassign primary if needed |
| `/van-gogh:kpi-scorecard` | Anytime | Six measures on whether Van Gogh is earning its place, counted from what it already did. The same numbers arrive weekly by email when you switch the scorecard on |
| `/van-gogh:finance-brief` | Weekly / anytime | Where the money stands, read from QuickBooks: cash by account, who is past due to you, what you owe, and the month so far against last month. Arrives by email every Monday when you switch it on |
| `/van-gogh:check-updates` | Anytime | Check whether a newer plugin version is published, show what it would bring, and offer to install it |
| `/van-gogh:release-notes` | After an update | Re-show what the last update changed (the versions between what you had and what you have), plus full version history on request |
| `/van-gogh:uninstall-van-gogh` | If you want to stop | Tear down the background scheduler (launchd/Scheduled Tasks) |

(There is no update skill — updates come through the plugin system alone; see
[Updates](#updates).)

**Specialists**: deep domain experts Claude hands the work to on its own. You
do not call these with a slash command. You ask your question in your own
words and the right specialist answers.

| Specialist | Ask it about |
|---|---|
| Bookkeeper and controller | Closing the month, a balance that will not reconcile, AP or AR aging, journal entries, who may approve what, getting ready for an audit, revenue recognition, leases, stock compensation, purchase accounting |

A specialist works from the records already in your vault and names the file
behind every number it gives you. When the support is not there it says so
rather than estimating, and anything it writes to send arrives as a draft.

---

## How it works

```
Your email + calendar + meeting notes
        ↓
  Claude Code skill (skills/) shells out to…
        ↓
  Python script (app/) → fetches data, emits JSON
        ↓
  Skill renders the briefing
        ↓
  Obsidian Second Brain (van-gogh/ + wiki/ vault)
```

Auth lives in a gitignored `config.json` inside your vault (`van-gogh/config.json`), created at install time. No credentials are ever committed. Updates ship through the Claude Code plugin system: `/plugin marketplace update van-gogh` pulls the latest version (or turn on auto-update for the marketplace in `/plugin`).

### One shared source layer

Every briefing draws on the same sources through one collector surface (`app/collectors.py`), so a source can't silently fall out of one briefing while staying in another. A coverage test (`tests/test_briefing_coverage.py`) enforces that each briefing consults every source its role requires, and the genuinely shared logic (the kill/pass scan, the deal matcher, the hotcache parser) lives in small leaf modules that the collectors delegate to, never copied per briefing.

```
       Gmail     Outlook     Calendar     Notetaker     Obsidian vault
         └──────────┴───────────┼────────────┴───────────────┘
                                │
      ┌─────────────────────────────────────────────────────────────┐
      │   app/collectors.py  ·  one shared source surface           │
      │                                                             │
      │   sent_mail · inbound_mail · meetings · calendar            │
      │   hotcache_read · obsidian_tasks · kill_scan · deal_match   │
      └───────────────────────────┬─────────────────────────────────┘
                                  │  delegates to leaf modules (no cycle)
            ┌─────────────────────┼──────────────────────┐
    ┌───────▼────────┐   ┌────────▼─────────┐   ┌────────▼─────────┐
    │ deal_status.py │   │ hotcache_sync.py │   │  notetaker.py    │
    │ KILL_RE +      │   │ match_deal +     │   │  provider layer  │
    │ detect_kills   │   │ sync_hotcache    │   │  granola / grain │
    └────────────────┘   └──────────────────┘   └──────────────────┘

      Each briefing composes that same surface:

        /van-gogh:week            the hub: every source, the kill/pass scan, the
                         meeting cross-ref, classification; emits JSON
        /van-gogh:morning-coffee  renders the hub's JSON, what the check-off
                         closed, hotcache write-back
        /van-gogh:afternoon-tea   sent + inbound mail, meetings, tomorrow's calendar,
                         kill/pass scan, what the check-off closed
        /van-gogh:week-retro      full deal coverage via the hub (skips metered Haiku)
```

### How it connects to your mail

`app/data_sources.py` routes how each script gets its mail and calendar.

- **Tier 1 (direct OAuth), the way Van Gogh is meant to run**: OAuth refresh tokens live in `~/.config/van-gogh/.env` and scripts call Gmail, Calendar, and Microsoft Graph directly. Fully unattended, so the background scheduler, the emailed digests, and anything else that fires on a timer all work. `/van-gogh:install-van-gogh` sets this up.
- **Tier 2 (connector), the fallback**: with no tokens, the skill uses Claude's built-in MCP connectors to fetch your mail and calendar, hands the result to the script as a temp JSON file, and the script renders it.

Both modes produce the same briefings, but they are not equivalent. Tier 2 needs you in a chat session each time, so nothing runs on a timer and no digests go out, and connector coverage is patchier than the direct APIs. Use it only if your IT policy blocks OAuth consent. If OAuth fails during install, retry it before settling for the fallback.

### Connectors

Some things have no sign-in Van Gogh can do on your behalf. Your books are one:
QuickBooks is reached through a **connector** you authorize once on claude.ai,
and Claude holds that connection for you.

That is what the weekly finance brief reads. Connect it in claude.ai under
Settings, then Connectors, then Intuit QuickBooks. To check it actually works,
rather than merely showing as connected:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/connector_fetch.py" --check quickbooks
```

It prints `OK` and your company name, or tells you it needs reconnecting.

Two things worth knowing. The session that reads your books is allowed those
report tools and nothing else, so nothing in QuickBooks can be changed by it,
and it never sees anything outside those reports. And the connection expires
from time to time, which only you can renew: when that happens the brief does
not simply stop arriving, you get a short email saying which service needs
reconnecting and where.

### Your CRM, or any other system

The same kind of connection lets a meeting prep say what another system
holds on the people you are about to meet. Connect the system in claude.ai
under Settings, then Connectors (HubSpot, Salesforce, a help desk, anything
listed there), then run `/van-gogh:add-connector`. It shows what is connected,
you pick which of its tools may be used, and it proves the connection by
looking up one real person before saving anything.

Nothing is written for each system, so it works with whatever you connect.
What keeps that safe is in code rather than in instructions: only tools whose
names say they read can be chosen, at most four lookups are made per person,
and a result is kept only if it actually names that person. What comes back
is shown in the prep as coming from that system, and Van Gogh never changes
anything in it or acts on what it says. When the connection expires, the prep
says so in one line and carries on.

### What runs by itself

Everything recurring can run on a schedule, and a watcher checks every 30
minutes that what was due actually ran and delivered, restarting what failed.
All of it is off until you ask, except the two jobs that file your notes,
the check-off of finished to-dos, and the watcher itself. Times are your computer's own, and each one is a setting.

| When | What |
|---|---|
| Every hour | File your meeting notes, when there is a new one |
| 4:50 AM daily | Sync your project notes |
| 5:05 AM daily | Check for meetings nobody recorded |
| 5:10 AM daily | Close the to-dos your mail and meetings prove are finished |
| 5:20 AM Monday | Who is going cold |
| 6:00 AM daily | Suggestions: what is worth doing next |
| 6:15 AM weekdays | Daily Meeting Prep |
| 6:30 AM Monday | Week |
| 6:45 AM Monday | Finance brief |
| 7:00 AM weekdays | Morning Coffee |
| 1:00 PM Friday | Client reports, left as drafts |
| 2:30 PM weekdays | Afternoon Tea |
| 3:30 PM Friday | Week Retro, with the scorecard |
| 5:00 AM Sunday | Save new contacts |
| 5:40 AM Sunday | Refresh the tone profile |
| 6:00 PM Sunday | Learn from the week's sent mail |
| Every 15 minutes | Notes between briefings |

None of these sends anything to anyone but you. The most any of them leaves
for someone else is a draft. [FOUR-CS.md](FOUR-CS.md) lists every capability
and its schedule.

### Check-off from evidence

Every morning at 5:10 AM Van Gogh goes through your open to-dos and closes the ones that are provably finished. It does not ask first. What it closed appears in your next Morning Coffee and Afternoon Tea under **Closed today**, each line with the sentence that proved it, and any of them can be put back.

**What closes a to-do.** Something you owed closes on your own evidence: mail you sent to a real recipient, your own words on a later call, or a decision written down after a meeting. Something owed to you closes only when the other person delivers, in mail from them or in their own words on a call. For each item Van Gogh gathers that evidence, asks a model whether the work is finished, and requires the exact sentence that shows it.

**What never closes one.** The model only proposes. Code then checks the sentence it quoted, and refuses the close when the sentence is not word for word in one dated source, when the source is older than the to-do, when the sentence is a promise ("I will send it Friday"), when it says the work is not done, when it is the to-do's own words, when it sits in a subject line and not in the message, when the mail went only to yourself, when it is someone else speaking about something you owed, or when the model was not sure enough. A draft you never sent is not evidence at all. A meeting where the work was discussed does not finish it, and neither does you chasing someone for something they owe you.

**Expiry.** A to-do with no due date, tied to no deal, about which nothing at all has turned up in 30 days is closed as expired and listed separately under **Expired**. This only happens on a morning when your mail and meetings could be read in full. A to-do with a due date or a deal never expires.

**Putting one back.** Every line carries an id such as `cb-1a2b3c4d`. `/van-gogh:done-judge --undo cb-1a2b3c4d` puts the item's lines back exactly as they were, in the hot cache, on the meeting page and in the week file, and keeps that item open for good: nothing will close it automatically again. `/van-gogh:done-judge` on its own shows what closed today, and `--why` with an id says why an item was or was not closed.

**If it did not run.** A morning when the check-off failed (a usage limit, an expired sign-in) says so plainly at the top of that section in the briefing. It never reads as a day on which nothing was finished.

**Settings.** `auto_close` in your settings: `enabled` (on by default), `confidence` (how sure it must be, 0.85 by default), and `expire_minor_days` (30 by default, 0 turns expiry off). Change them with `/van-gogh:update-settings`.

Things the briefings notice but cannot prove are still shown, as a quiet "looks done" line, and are never ticked for you.

**Hotcache sync** (`/van-gogh:morning-coffee` and `/van-gogh:afternoon-tea`): the briefings keep the active-deals section of `hotcache.md` honest from the mail they already pulled, in safe directions only. `morning_coffee.py` propagates any `[x]` ticks you made by hand in the week file into the matching `hotcache.md` lines. `app/hotcache_sync.py` then refreshes each deal's `last_contact` to its most recent matched mail, revives a deal wrongly tagged `dead` that has newer mail (raising an alert), and bumps the file's staleness date. It never auto-marks a live deal dead: a counterparty pass is always a human-confirmed alert, never an automatic write.

### Buckets and priorities

Every venture, client, company, board seat, or area of life you configure is a **bucket** (`businesses[]` in config). Inside each one you can state the priorities you are actually pushing on, and the briefings lead with those.

This is what stops a briefing from surfacing whatever email arrived last. `/van-gogh:week` assigns every item to exactly one bucket by your own keywords, grades it against that bucket's stated priorities, and renders a fixed section per bucket: priorities first, in your order, then anything else. A priority with nothing open under it says so out loud, because a thing you called a top priority that nothing is moving on is the most useful line on the page.

Code decides which bucket an item lands in and where it sorts; the model only writes the sentence inside a row. It never moves an item between buckets. An item that matches no bucket, or matches no priority in its bucket, is demoted out of the urgent section rather than competing with the work you said mattered. Three exemptions keep that from misfiring: an allowlisted deal-critical sender, a counterparty with a live deal in your hotcache, and an install with no buckets configured yet.

Set priorities by saying them in plain English (`/van-gogh:update-settings`); they are written into config in the same turn rather than acknowledged and forgotten.

### Overdue nudges and drafting

Deliverables are filed in the "## Pending follow-ups" ledger in your workspace memory, one bullet each:

```
- [ ] (ACME) Send the widget redline | due: 2026-09-12 | format: email
```

Both daily briefings surface the overdue ones and offer to draft them. The order is fixed: the draft appears in chat first as plain text or a plain-ASCII sketch, you edit it there, a file is only written when you say so, and sending is a separate explicit instruction after that. Older prose bullets in the same ledger keep working unchanged.

### Deal-status alerts

`/van-gogh:week`, `/van-gogh:morning-coffee`, and `/van-gogh:afternoon-tea` scan inbound mail for counterparty kill/pass language (`app/deal_status.py`) before anything is classified, so a "we've decided not to move forward" email can't be quietly dropped as a closing acknowledgement. Each hit is tied to the open deal it belongs to (via the same distinctive-token matcher the hotcache uses, so a newsletter never trips it) and surfaced for your review. Nothing is ever auto-killed.

The hub also cross-references your recent meetings: a thread whose counterparty you met since their last email is tagged "likely handled, confirm" rather than dropped, so a deal you closed on a call stops nagging you without losing the audit trail.

---

## Prerequisites

Runs on macOS and Windows 10/11 (the macOS lines below also work on Linux). Every skill and script is cross-platform: paths, encodings, time formatting, and shell steps all work the same on each OS (commands that differ are shown in both bash/zsh and PowerShell forms).

| Tool | Purpose | Install |
|---|---|---|
| [Claude Code](https://claude.ai/code) or [Codex](https://chatgpt.com/codex) | Runs the skills (one of the two per computer) | claude.ai/code, or the Codex app (on a Mac it is part of the ChatGPT app) plus the `codex` command, which OpenAI's installer line below puts on your PATH |
| Python 3.10+ | Runs the briefing scripts | The install line uses the Python already on the machine, or fetches a private copy into `~/.config/van-gogh/` with no administrator password |
| Git | Plugin install & updates (the marketplace clones this repo) | [git-scm.com](https://git-scm.com) — usually already present |
| [Granola](https://granola.ai) *or* [Grain](https://grain.com) | Meeting transcription (optional) — one active at a time | granola.ai / grain.com |
| [Obsidian](https://obsidian.md) | Optional: a nicer way to view the vault, which is a plain folder of markdown files | obsidian.md |

Gmail, Calendar, and Outlook access runs through your own sign-in (Tier 1, the path the install sets up on either client), or under Claude only through Claude's built-in connectors (Tier 2); no mail or calendar tool has to be installed either way. `/van-gogh:install-van-gogh` walks you through connecting each account, and lets you add more later with `/van-gogh:add-account`.

---

## Installation

Project Van Gogh installs as a **Claude Code plugin** or as a **Codex
plugin**, from the same repository: it is its own marketplace for both and
is public, so there is no GitHub sign-in step. Pick the client you use; one
computer runs one of the two.

Before anything else, run the readiness check. It needs nothing installed
and prints READY, or names what is missing in plain words:

macOS (Terminal), Claude Code:
<!-- paste: check-claude-mac -->
```
curl -fsSL https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.sh | sh
```

Windows (PowerShell), Claude Code:
<!-- paste: check-claude-windows -->
```
irm https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.ps1 | iex
```

macOS (Terminal), Codex:
<!-- paste: check-codex-mac -->
```
curl -fsSL https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.sh | sh -s -- --host codex
```

Windows (PowerShell), Codex (`irm ... | iex` passes no options to the
script, so the client is named in the environment):
<!-- paste: check-codex-windows -->
```
$env:VAN_GOGH_HOST = "codex"; irm https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.ps1 | iex
```

Under Codex there is a way with no Terminal at all, in the Codex app (on a
Mac it is part of the ChatGPT app):

1. Open the plugin screen, choose Add marketplace, and enter
   `NVentures/project-van-gogh-claude`.
2. Install Van Gogh from that marketplace.
3. Save the key file the Van Gogh team sent you in Downloads.
4. Open a new chat and type `$van-gogh:install-van-gogh`. The first time,
   it gets the computer ready (Python and its settings, about two minutes):
   approve it when Codex asks, and say yes if Codex asks whether to trust
   Van Gogh's hooks.
5. Open one more new chat and type `$van-gogh:install-van-gogh` again.
   Codex reads Van Gogh's settings only when a chat starts, which is why this
   takes a second chat. Vincent then asks for your name, company and city if
   the key file did not carry them, and runs the rest of setup.

Or paste the install line for your client into Terminal (not into Claude
or Codex); under Codex, install the `codex` command first, as its lines say.
The install line puts the plugin in place, prepares Python without an
administrator
password, reads the key file the Van Gogh team sent you (saved in
Downloads), and opens your client with setup already running:

macOS (Terminal), Claude Code:
<!-- paste: install-claude-mac -->
```
claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; claude plugin marketplace update van-gogh; sh ~/.claude/plugins/marketplaces/van-gogh/setup.sh
```

Windows (PowerShell), Claude Code:
<!-- paste: install-claude-windows -->
```
claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; claude plugin marketplace update van-gogh; powershell -ExecutionPolicy Bypass -File "$HOME\.claude\plugins\marketplaces\van-gogh\setup.ps1"
```

macOS (Terminal), Codex (install the command first with
`curl -fsSL https://chatgpt.com/codex/install.sh | sh`, then open a new
window; the ChatGPT app carries a copy of Codex, but not as a command you
can type):
<!-- paste: install-codex-mac -->
```
codex plugin marketplace add NVentures/project-van-gogh-claude && codex plugin add van-gogh@van-gogh && cd ~/.codex/plugins/cache/van-gogh/van-gogh && sh "$(ls -d */ | sort -V | tail -n 1)setup.sh" --host codex
```

Windows (PowerShell), Codex (install the command first with
`irm https://chatgpt.com/codex/install.ps1 | iex`, then open a new window):
<!-- paste: install-codex-windows -->
```
codex plugin marketplace add NVentures/project-van-gogh-claude; codex plugin add van-gogh@van-gogh; powershell -ExecutionPolicy Bypass -File ((Get-ChildItem "$HOME\.codex\plugins\cache\van-gogh\van-gogh" -Directory | Sort-Object { [version]$_.Name } | Select-Object -Last 1).FullName + "\setup.ps1") -Host codex
```

Under Codex the first session asks once whether to trust the plugin's hooks;
say yes (they do one thing: print the line that reminds you of an unfinished
install). Skills
are named `$van-gogh:<name>` there instead of `/van-gogh:<name>`, and
everything below reads the same way with that spelling. Four things need
Claude and say so in one line when run under Codex:

- the QuickBooks read behind `$van-gogh:finance-brief`;
- the chat connector fallback for mail, which `$van-gogh:meeting-prep` and
  the briefings reach only when no mail account is signed in;
- the CRM lookups that `$van-gogh:add-connector` sets up;
- each briefing's private web page on claude.ai, which is not published
  from Codex.

Everything else runs under either client; a skill that looks something up on
the web uses Codex's own web search when that is switched on, and otherwise
says the lookup was skipped.

The session it opens (Claude or Codex) is Vincent, your chief of staff, and he runs setup. He
asks five questions when nothing goes wrong (your notetaker and its key, one confirmation of your details,
the schedule, your priorities, and which of the deals it found are real),
signs you in to each account in your browser, and keeps a checklist of the
install's steps on disk. The schedule question shows every job that can run by
itself, with its time on your computer and whether it starts on or off, and
you keep it, change it in your own words, or schedule nothing. If the window
closes or the session is interrupted, ask Vincent to finish setup
(`/van-gogh:vincent`; he follows the same steps himself, from the same rules
page), or run `/van-gogh:install-van-gogh`, and it resumes at
the first open step; while any step is open, Van Gogh reminds you once when you
open Claude or Codex, once at the top of any other skill's reply, once in
any briefing, and by email on day one, three and seven.
It ends with your first briefings on screen. The venv it creates lives at
`~/.config/van-gogh/venv`, outside the plugin cache, so a plugin update never
deletes it; its packages come from `requirements.txt` and refresh themselves
when an update changes that file. No key file from
the team? `/van-gogh:install-van-gogh` still works; it points you at the
instructions for creating your own Google and Microsoft app credentials.

All Python invocations use that venv's interpreter (`~/.config/van-gogh/venv/bin/python`, or `venv\Scripts\python.exe` on Windows) so the managed environment is always active.

---

## Vault structure

`/van-gogh:install-van-gogh` scaffolds this directory structure inside your Obsidian vault:

```
van-gogh/            ← Project Van Gogh state (config + briefings + logs)
  config.json        ← your accounts, vault path, businesses, filters
  week.md            ← rendered weekly briefing
  morning-coffee.md  ← rendered daily briefing
  afternoon-tea.md   ← rendered end-of-day retro
  logs/              ← script sidecars + scheduler logs
  projects/          ← workspace memory (Project Van Gogh/memory.md)
wiki/
  hotcache.md        ← working memory: active deals, action items, key numbers
  memory/            ← mirrored chat memory (opt-in, off by default)
  index.md           ← content catalog
  log.md             ← operation log
  entities/          ← one page per person or org
  projects/          ← one page per venture or initiative
  sources/           ← meeting notes, retros, filed documents
  weekly/            ← weekly briefing files
  analyses/          ← research and synthesis outputs
raw/
  assets/            ← source files, attachments
CLAUDE.md            ← vault schema and business context (fill this in after install; under Codex the file is AGENTS.md)
```

After install, open `CLAUDE.md` in your vault (`AGENTS.md` under Codex; setup creates the one your client reads) and fill in two tables: your ventures (Business Tags) and any known transcription corrections (Jargon). That's the only manual setup required when the Van Gogh team sent you a key file.

### Where to open sessions

For chief-of-staff work, open Claude Code **in your vault folder** — the vault
`CLAUDE.md` then loads automatically, giving every prompt the Van Gogh context:
what the stack is, where state lives, which skill handles which intent, and your
workspace memory (including the pending-follow-ups ledger).

```bash
cd "/path/to/your vault" && claude        # macOS / Linux
```
```powershell
cd "C:\path\to\your vault"; claude        # Windows
```

To talk to Vincent for a whole session, start Claude as him from the same
folder. He reads what ran, what did not and what you asked him to remember
before his first reply, and you can ask for any work in your own words:

```bash
cd "/path/to/your vault" && claude --agent vincent        # macOS / Linux
```
```powershell
cd "C:\path\to\your vault"; claude --agent vincent        # Windows
```

Under Codex, open the Codex app (inside the ChatGPT app on a Mac) or `codex` in the vault folder (it reads
the vault `AGENTS.md` the same way Claude reads `CLAUDE.md`) and mention
`$van-gogh:vincent`.

The chat on each briefing page in the Workbench is Vincent as well. There he
can read and answer, and he runs a briefing, a meeting prep or a radar when
you ask for one. Anything else he tells you to run from a terminal.

The `/van-gogh:*` skills also work from any other directory — you just won't
have the ambient vault context there. The top of the vault `CLAUDE.md` (or
`AGENTS.md` under Codex) is a
managed block (between `van-gogh:context` markers) that self-refreshes on
plugin updates; everything you write below the end marker is yours and is never
touched.

---

## Updates

When a new version is published, the next chat you open asks once whether to
install it (the next briefing you run asks too, if you have not answered), and
installs it on a yes; `/van-gogh:check-updates`
does the same on demand. Nothing is installed without your say. To skip the
question entirely, enable auto-update for the `van-gogh` marketplace in
`/plugin` → Marketplaces (the install wizard walks you through this). Under
Codex the same question installs the update through `codex plugin add`, and
the config block Codex reads is rewritten to the new version's folder.
Your state (`.env` tokens, vault pointer, venv) lives in `~/.config/van-gogh/`,
outside the plugin cache, so updates can never lose it. Python dependencies
re-sync themselves the next time any skill runs — there is no separate update
command to remember.

---

## Configuration

All personal values live in `config.json` inside your vault at
`{vault}/van-gogh/config.json` (gitignored). `/van-gogh:install-van-gogh` creates it for
you from the template and writes a `~/.config/van-gogh/vault-pointer` so the
scripts can find your vault. To set it up manually:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/migrate_to_vault.py" --vault "/path/to/your/Obsidian Vault"
cp "${CLAUDE_PLUGIN_ROOT}/config.template.json" "<vault>/van-gogh/config.json"
```

After install, edit it conversationally with `/van-gogh:update-settings` rather than by
hand.

The schema is six nested blocks:

```json
{
  "user": {
    "full_name": "Your Name",
    "first_name": "Your",
    "name_variants": ["Your Name", "Your"],
    "self_entities": ["Your Name"],
    "role_description": "",
    "timezone": "America/Los_Angeles",
    "secondary_timezone": ""
  },
  "accounts": [
    { "provider": "google", "email": "you@gmail.com", "label": "Gmail", "is_primary": true, "sent_folder_id": "" },
    { "provider": "microsoft", "email": "you@yourcompany.com", "label": "Outlook", "is_primary": false, "sent_folder_id": "" }
  ],
  "obsidian": {
    "vault_path": "~/Documents/van-gogh-vault",
    "hotcache_relpath": "wiki/hotcache.md",
    "sources_relpath": "wiki/sources",
    "weekly_relpath": "wiki/weekly",
    "hotcache_action_items_heading": "Your Action Items"
  },
  "businesses": [
    {
      "tag": "personal",
      "display_name": "Personal",
      "project_page": "wiki/projects/Personal.md",
      "meeting_route": "10-Personal/Meetings",
      "keywords": [],
      "priorities": []
    }
  ],
  "email_filters": {
    "internal_domains": [],
    "internal_team_emails": [],
    "allow_domains": [],
    "extra_spam_fragments": []
  },
  "relationship_radar": {
    "yellow_days": 30,
    "red_days": 60,
    "skip_tags": [],
    "skip_entity_names": []
  }
}
```

- `user`: identity used by briefings, action-item matching, and classification prompts. `timezone` (any IANA name) is what every meeting time and "today / tomorrow" window renders against; set `secondary_timezone` to show a second zone side by side (e.g. PT / ET).
- `accounts`: a list of any number of Google and Microsoft accounts. Each entry has a `provider`, `email`, display `label`, an `is_primary` flag (exactly one account is primary), and a `sent_folder_id` (Microsoft only). Add or remove accounts with `/van-gogh:add-account` and `/van-gogh:remove-account`.
- `obsidian`: vault path and the relative paths Project Van Gogh writes into.
- `businesses[]`: one entry per venture, client, or area of life. These are your **buckets**; add as many as you have. `keywords[]` route meetings and emails to the right bucket, and `priorities[]` holds what you are actually pushing on inside it, so briefings lead with those instead of whatever email arrived last. Set them by telling `/van-gogh:update-settings` in plain English.
- `email_filters`: your domains and any extra spam fragments. Generic spam patterns stay in code.
- `support`: where to email if an unattended job keeps failing after its retries. Off by default; the alert carries a failure signature and never briefing content.
- `memory_sync`: whether Claude Code's chat memory is mirrored into `wiki/memory/`. **Off by default**, and deliberately so: one computer can hold Claude Code projects for several unrelated clients, so the mirror only ever reads directories you name explicitly, or the two that provably belong to this install (your vault and the plugin repo). It never scans `~/.claude/projects/*`, it only writes into the vault, and it proposes merges into existing pages rather than making them.
- `relationship_radar`: cold-contact thresholds (yellow/red days) and entities to skip.
- `meeting_prep`: the prep email. Off by default; `mode` is `daily` (one email each morning at `daily_time`) or `each` (one an hour before every call).
- `connectors[]`: other systems a meeting prep may ask about a person, such as your CRM. Add one with `/van-gogh:add-connector` rather than by hand.
- `cadence`: schedules for the capabilities that otherwise run only on request (unrecorded meetings, the relationship radar, client reports, and the two voice jobs). All off by default.
- `advisor`: the daily suggestions. Off by default.

The install wizard populates this for you. To edit later, run `/van-gogh:update-settings`, it walks through changes conversationally and validates before saving. Scripts read config via `app/config_loader.py`.

---

<div align="center">
<sub>Built on <a href="https://claude.ai/code">Claude Code</a> · Vault schema inspired by <a href="https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f">Karpathy's LLM Wiki</a></sub>
</div>
