# Project Van Gogh — Memory

Lightweight init context. Read this at the start of any Project Van Gogh session.

## What Project Van Gogh is

Nobel's personal chief of staff stack. Skills + scripts + Obsidian integrations running locally. Built on Project Monet (the distributable framework from GitHub). Van Gogh is opinionated and Nobel-specific; Project Monet is the clean version that ships to others.

## Active stack

- `/morning-coffee` → daily briefing from week file + email scan
- `/week` → weekly priorities, sent mail analysis, inbox triage (Gmail + Outlook + secondary account)
- `/week-retro` → Friday retro → vault source page
- `/meeting-ingest` → notetaker meeting → entity pages + dev logs + action items

## Auth

Auth is configured in `config.json` (gitignored). See `config.template.json` for all fields.

- Google primary + secondary: direct OAuth via `app/google_client.py`. Each account is a label with its own `GOOGLE_REFRESH_TOKEN_<LABEL>` env var, provisioned by `app/auth_bootstrap.py`.
- Microsoft: direct OAuth via `app/microsoft_client.py` (MSAL + Graph REST), `MS_GRAPH_REFRESH_TOKEN_<LABEL>`.
- Anthropic: `claude` CLI via subscription — always unset `ANTHROPIC_API_KEY` in scripts

## Graduation path

Features proven in Van Gogh → stripped of personal context → shipped into Project Monet (GitHub).

## Pending follow-ups (remove when resolved)

_The `/van-gogh:follow-up-radar` ledger. One `- **Title** — ...` bullet per open
thread; include a trigger date ("if unheard by Thu 2026-07-23", "due EOD Fri
7/17"), the mailbox to watch ("Work Outlook"), and cues like HARD TRIGGER /
value-add / DEFERRED. Keep terse; delete bullets when resolved._

## Next to build

1. `/meeting-prep` — pre-call one-pager using entity pages + meeting history + hotcache
2. `/follow-up-watcher` — open commitments from meeting notes, cross-referenced with sent mail
