<div align="center">

<img src="assets/banner.png" alt="Project Van Gogh" width="100%"/>

**A personal chief of staff, running entirely on your machine.**

Daily briefings. Weekly priorities. Meeting notes filed into your second brain. All wired together from your Gmail, Outlook, calendar, and Obsidian vault, no SaaS, no subscriptions, no data leaving your laptop.

</div>

---

## What it does

Project Van Gogh is a [Claude Code](https://claude.ai/code) **plugin** — a set of skills backed by Python scripts, installed and auto-updated through the Claude Code plugin marketplace built into this repo. Each morning it reads your open deals, flags what's slipping, pulls your calendar, and gives you a focused briefing. Every week it synthesizes your email threads into a priority list. After every meeting it files your Granola or Grain transcript into structured vault pages.

**The daily loop** — your recurring briefings.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:morning-coffee` | Every morning | Open deals, calendar, slipping threads; deal kill/pass alerts; confirm-gated completion scan; hotcache sync |
| `/van-gogh:afternoon-tea` | End of day | Sent + inbound mail + meetings + tomorrow's calendar; deal kill/pass alerts; confirm-gated completion scan |
| `/van-gogh:week` | Monday morning | Weekly priorities from email + calendar + vault; inbound kill/pass scan; meeting cross-ref |
| `/van-gogh:week-retro` | Friday | Retro summary + full deal coverage (deaths, at-risk, likely-handled), saved as a vault source page |
| `/van-gogh:note` | Every 15 minutes (auto, opt-in) | The watcher between briefings: a reply on a front-page item, a deal pass in words, or a moved meeting becomes a short Note with the reply pre-drafted; at most two a day, never mid-meeting |
| `/van-gogh:five-fifteen` | Friday | Weekly 5:15 client reports — per-client look-back/look-forward from email/Slack/Teams/HubSpot/meetings, vault copy + review-ready email draft |

**Meetings and notes** — capture every call into the vault.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:meeting-prep` | Before any call | Attendee entity pages + meeting history + hotcache → one-page brief |
| `/van-gogh:meeting-ingest` | After any meeting | Meeting transcript → entity pages + dev log + action items + deal-stage advance |
| `/van-gogh:calendar-stub-check` | Daily (auto) | Stubs unrecorded meetings into the vault so nothing slips through |
| `/van-gogh:ingest-workspace` | On demand | Sync workspace project changes to vault dev logs |

**Voice and relationships** — sound like yourself, keep contacts warm.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:relationship-radar` | Weekly | Flags contacts going cold (30d yellow, 60d red), drafts check-ins in your voice |
| `/van-gogh:follow-up-radar` | Daily / on demand | Date-triggered tickler: surfaces due/overdue follow-ups from the Pending follow-ups ledger, drafts value-add nudges |
| `/van-gogh:voice-generator` | On demand | Tone-of-voice profile from 90 days of sent mail, segmented by audience |
| `/van-gogh:voice-bootstrap` | New-user setup | Build a voice style guide from scratch from your writing samples |
| `/van-gogh:voice-calibration` | Weekly (Sundays) | Learns voice patterns from the week's sent mail, appends to the Voice Snapshot |

**Setup and maintenance** — install, configure, update, tear down.

| Skill | When to run | What it does |
|---|---|---|
| `/van-gogh:install-van-gogh` | Once | Interactive first-time setup wizard |
| `/van-gogh:migrate-from-legacy-van-gogh` | Once, if upgrading | Move a legacy standalone (git-clone) install into the plugin — tokens, vault pointer, config blocks, podcast feeds, and the follow-up ledger |
| `/van-gogh:update-settings` | When config changes | Conversational editor for `config.json` (accounts, vault, timezone, businesses, filters) |
| `/van-gogh:update-digest-preferences` | To get briefings by email | Opt in/out of the emailed briefing digest, set its cadence, sender, and recipient; schedules the background send jobs |
| `/van-gogh:operator-research` | Once at setup, then when it goes stale | Reads the public web for who you are and what your company does, then files entity pages, a session profile, and suggested keywords and priorities for your approval |
| `/van-gogh:add-account` | Anytime | Add another Gmail or Outlook account (runs OAuth, appends it to your accounts list) |
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
        /van-gogh:morning-coffee  renders the hub's JSON, confirm-gated completion
                         scan, hotcache write-back
        /van-gogh:afternoon-tea   sent + inbound mail, meetings, tomorrow's calendar,
                         kill/pass scan, confirm-gated completion scan
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

### Confirm-gated checkoff

Both `/van-gogh:morning-coffee` and `/van-gogh:afternoon-tea` include a completion scan step powered by `app/completion_scan.py`. After pulling your briefing data, the script scans recent sent mail and meeting notes for open items (action items, project tasks, deals) that look done, each surfaced with the evidence that triggered it. Nothing is written until you explicitly confirm.

Detection is recall-first but corroborated: an item surfaces when a single email or meeting clears a real bar, a distinctive org handle, a full name, a named person plus a shared topic word, or two or more meaningful words in common. A lone common first name is deliberately not enough (a bare "Mark" overlap once checked off an unrelated task), so that thin match is now rejected. The confirm step is still the gate: a fuzzy signal never silently buries a live item.

**`--since`** windows the scan to activity since your last briefing, so each run only re-examines what's new.

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

Runs on macOS, Windows 10/11, and Linux. Every skill and script is cross-platform: paths, encodings, time formatting, and shell steps all work the same on each OS (commands that differ are shown in both bash/zsh and PowerShell forms).

| Tool | Purpose | Install |
|---|---|---|
| [Claude Code](https://claude.ai/code) | Runs the skills | claude.ai/code |
| Python 3.10+ | Runs the briefing scripts | `/van-gogh:install-van-gogh` installs it if missing (Homebrew on macOS, winget on Windows) |
| Git | Plugin install & updates (the marketplace clones this repo) | [git-scm.com](https://git-scm.com) — usually already present |
| [Granola](https://granola.ai) *or* [Grain](https://grain.com) | Meeting transcription (optional) — one active at a time | granola.ai / grain.com |
| [Obsidian](https://obsidian.md) | Second Brain vault | obsidian.md |

Gmail, Calendar, and Outlook access runs either through direct OAuth (Tier 1) or Claude's built-in connectors (Tier 2), no external CLI tools required either way. `/van-gogh:install-van-gogh` walks you through connecting each account, and lets you add more later with `/van-gogh:add-account`.

---

## Installation

Project Van Gogh installs as a **Claude Code plugin**. The repo is its own
marketplace and is public, so there is no GitHub sign-in step — git alone is
enough to clone it.

1. In any Claude Code session:

```
/plugin marketplace add NVentures/project-van-gogh-claude
/plugin install van-gogh@van-gogh
```

2. Then run the setup wizard:

```
/van-gogh:install-van-gogh
```

The install skill walks you through every step interactively: installs Python if needed, creates a stable per-user venv at `~/.config/van-gogh/venv` (outside the plugin cache, so plugin updates never touch it) and installs dependencies from `requirements.txt`, connects one or more email accounts, sets your timezone from the city and state you give it, sets up your notetaker API key (Granola or Grain), and scaffolds your vault. It takes about 10 minutes.

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
CLAUDE.md            ← vault schema and business context (fill this in after install)
```

After install, open `CLAUDE.md` in your vault and fill in two tables: your ventures (Business Tags) and any known transcription corrections (Jargon). That's the only manual setup required.

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

The `/van-gogh:*` skills also work from any other directory — you just won't
have the ambient vault context there. The top of the vault `CLAUDE.md` is a
managed block (between `van-gogh:context` markers) that self-refreshes on
plugin updates; everything you write below the end marker is yours and is never
touched.

---

## Updates

Updates have a single entry point: `claude plugin marketplace update van-gogh`
(the same command is available from `/plugin`). Enable auto-update for the
`van-gogh` marketplace in `/plugin` → Marketplaces — the install wizard walks
you through this — and the update also runs automatically at session start.
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

The install wizard populates this for you. To edit later, run `/van-gogh:update-settings`, it walks through changes conversationally and validates before saving. Scripts read config via `app/config_loader.py`.

---

<div align="center">
<sub>Built on <a href="https://claude.ai/code">Claude Code</a> · Vault schema inspired by <a href="https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f">Karpathy's LLM Wiki</a></sub>
</div>
