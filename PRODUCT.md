# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a founder or operator running several businesses at once, on
their own machine, with no assistant between them and the work. They open the
product twice a day, in the morning before the day starts and once at the end of
it, and they have about fifteen minutes each time. They arrive wanting to act and
leave, not to browse. On Monday and Friday the same person reads a wider version
of the same thing: the week ahead, and the week behind.

Every user of the plugin is this person. There is no second role, no team seat,
no admin. One machine, one vault, one mailbox set.

## Product Purpose

Van Gogh is a personal chief of staff that runs while the user is not there. On a
schedule it reads their mail, calendar and meeting transcripts, decides what
matters, writes the replies that can be written, and leaves a finished briefing
behind. What the user does with it is give or withhold a nod: send the reply that
is already written, tick off what is done, and close the page.

Success is that the user leaves with nothing waiting on them that they know
about, in less time than it would have taken to read the inbox.

## Positioning

The mechanism a neighboring product cannot copy: the work is already done before
the user arrives. Category peers (Linear, Superhuman, Raycast, Attio, Obsidian)
give you a faster place to do the work yourself. This one presents finished work
and asks for approval. That is why the interface speaks in completed past tense
and why the only present-tense verbs in the product are the two things a human
must do.

Second, it is local-first by necessity rather than by ideology. The refresh
tokens, the vault and the mail live on the user's machine, so the surface that
can actually act has to be there too.

## Operating Context

- **The ritual.** Morning Coffee at 7:00 AM local, Afternoon Tea at 1:00 PM, The
  Week on Monday, Week Retro on Friday. Scheduled through launchd on macOS and
  Task Scheduler on Windows; the job renders the briefing by spawning the Claude
  CLI, which writes markdown into the vault.
- **The vault.** An Obsidian vault the user already reads and links in. Van Gogh
  writes `{vault}/van-gogh/` (briefings, config, logs, ticket ledger) and reads
  and updates the user's own notes: `wiki/hotcache.md`, the weekly files, entity
  pages, project pages.
- **The Workbench.** A localhost server the user starts, serving the briefings,
  the ticket ledger, the vault graph and an inbox roll-up on 127.0.0.1 with a
  per-boot token.
- **Two other surfaces, both read-only.** An emailed digest, and one private
  claude.ai page per briefing that republishes to the same URL each run. Both
  are for reading away from the machine.
- **Tier 1 and Tier 2.** Tier 1 is direct OAuth (Gmail, Google Calendar,
  Microsoft Graph) and is how mail and calendar are read, attended or not.
  Tier 2 is a fallback for locked-down tenants where a human pastes connector
  output into a file; it needs a person in a chat session.
- **Unattended connector fetch.** For sources with no OAuth client of their own
  (the books, in QuickBooks), a confined headless session reads a named list of
  report tools through the user's own claude.ai connector, with every limit set
  by a CLI flag rather than a prompt. It can be scheduled. Its one human
  dependency is the connector token, which expires and which only the user can
  renew, so a run that hits that mails them a note saying so.

## Capabilities and Constraints

- **Drafts-first is absolute.** Skills and scheduled runs prepare drafts and
  never send. Sending happens only on an explicit, per-message instruction from
  the user, through one module. A second send implementation is a defect.
- **No API key.** Every model call unsets `ANTHROPIC_API_KEY` and routes through
  the user's Claude subscription via the CLI. The Anthropic SDK is never
  imported.
- **Both operating systems.** macOS and Windows 10/11 are shipped targets, Linux
  works but has no scheduler. No hardcoded paths, no platform-only shell
  commands, every subprocess hides its console window on Windows.
- **Nothing mutable in the plugin cache.** A marketplace update re-clones it.
  Machine state lives in `~/.config/van-gogh/`, everything vault-shaped lives in
  the vault.
- **The user's own notes are edited in place.** A check-off flips a checkbox in
  files the user reads and edits by hand, so every write must be idempotent and
  must never guess: an unmatched item is reported, not approximated.
- **Terminology the product uses with the user:** the briefings by name (Morning
  Coffee, Afternoon Tea, The Week, Week Retro), the front page, the fold, a
  business, a function, a draft, the nod. Terms from the data model are never
  shown.

## Brand Commitments

- The name is Van Gogh, and it means the man who wrote hundreds of letters to
  his brother Theo at night in ink on cream paper, not the Starry Night swirl.
- The interface speaks in completed past tense. `SEND` and `KICK OFF` are the
  only present-tense imperatives.
- Times are shown in PT and ET side by side.
- Zero em-dashes and en-dashes in any string, label or generated artifact.
- The incumbent visual system is Dear Theo (DESIGN.md): warm paper, Charter and
  Commit Mono, no sans-serif, chrome yellow reserved for work that is finished
  and waiting for the user. It is the default, and the user has explicitly left
  it open to replacement for the briefing page.

## Evidence on Hand

- The shipping renderers produce real briefings over a test vault; that output
  is the content in every mockup (four businesses, 120 open items, seven on the
  front page).
- Four visual directions built as working HTML and published for comparison:
  https://claude.ai/code/artifact/f04c78ba-8ed2-44e9-898c-4c5ef7badd85
- `DESIGN.md` carries measured WCAG contrast figures for every token pair and a
  dated decisions log.
- No customers, testimonials, benchmarks, pricing or install numbers exist. None
  may be invented.

## Product Principles

1. **The work arrives finished.** Every surface presents completed work awaiting
   a nod. An interface that asks the user to start something has failed.
2. **The nod is the product.** Approval is one deliberate press per thing. It is
   never batched, never implied, never automatic.
3. **Say what was actually done.** A page assembled without part of its
   machinery says so in its first line. A confident page that quietly hid
   something is worse than an honest noisy one.
4. **Act where the data lives.** Anything that changes the world runs on the
   user's machine, against their own files and tokens.
5. **Fifteen minutes.** Density and ranking serve the reader who has a quarter
   of an hour. Nothing is added that does not survive that test.

## Accessibility & Inclusion

- No color-only status. Every state carries its word in text, so the product
  survives grayscale and color blindness on one system.
- Body text meets WCAG AA on both grounds; chrome yellow is a fill color only on
  light ground, with a darker ink token for letterforms.
- Both themes ship, and the viewer's system setting is honored without a toggle.
- `prefers-reduced-motion` disables all motion and keeps the end states.
