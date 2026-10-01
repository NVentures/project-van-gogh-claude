---
name: note
description: >-
  The watcher between briefings. Runs headlessly every fifteen minutes from
  the Note tick (app/note_send.py, opt-in via notes.enabled) when a
  counterparty replied on a front-page item, said in words that a deal is
  off, or moved a meeting on today's page. Drafts the reply (never sends),
  writes one short Note in the house voice, and republishes the briefing
  page. Invoked as /van-gogh:note <path to the event file>; not meant to be
  typed by hand, though it can be for a rerun.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# The Note

Code already decided that this event is worth a Note: it matched a reply to
a front-page item, tied a pass phrase to a named deal, or saw a meeting move.
Your job is the half only a writer can do: read the event, draft what can be
drafted, and write the Note. Everything you print to stdout is mailed as the
Note, verbatim, so print nothing else.

## Python runtime

Before the first script invocation, run the ensure-venv guard in
[`_shared/python-runtime.md`](../_shared/python-runtime.md).

## Step 1: read the event

The argument is the path to a JSON file, possibly quoted. Read it. It holds:

| Key | What it is |
|---|---|
| `kind` | `reply`, `deal` or `calendar` |
| `time` | when it happened, both zones, e.g. `10:42 AM PT / 1:42 PM ET`. Use it verbatim |
| `today` | the local date |
| `briefing` | which briefing is on screen (`morning-coffee` or `afternoon-tea`) |
| `briefing_md` | the path of that briefing's written file |
| `others_moved_today` | how many other Notes were sent or folded today |
| `item` | reply only: the front-page item (`subject`, `counterparty_name`, `counterparty_email`, `account`, `label`, `why`, `age_days`, `due`, `function`, `bucket_name`) |
| `mail` | reply and deal: the inbound message (`subject`, `from`, `from_email`, `account`, `snippet`, `received`) |
| `account` | reply and deal: the account it arrived on (`label`, `provider`, `email`) |
| `thread`, `signal` | deal only: the hotcache deal name and the phrase that matched |
| `title`, `change`, `old_time`, `new_time` | calendar only: the meeting, `moved` or `gone`, and its times |

## Step 2: do the part that needs a writer

**`reply`.** Draft the answer. Follow "Drafting, before you render" in
[`_shared/front-page.md`](../_shared/front-page.md) exactly: pull the
context pack for `item.counterparty_name` and `item.counterparty_email`,
argue from the record and only the record, honour the age of the thread, and
create the draft through `draft_email.py` with `--provider account.provider`,
`--label account.label`, `--to mail.from_email`, `--subject "Re: mail.subject"`
and `--counterparty item.counterparty_name`. The script never sends. If it
returns `no_token` or `skipped`, say so in the Note in one clause and carry
on; a Note without a draft is still a Note.

**`deal`.** Draft nothing. The Note asks one question: whether to mark
`thread` as dead in the notes. Quote `signal` and enough of `mail.snippet` for
the reader to judge it in one glance. Never write to the hotcache and never
say the deal is dead; the reader decides that.

**`calendar`.** Read `briefing_md` and re-rank the rest of today in two or
three sentences: what the move frees, what it collides with, what should
happen in the freed slot if there is one. Draft nothing.

## Step 3: write the Note

Four to seven sentences, in the house voice (DESIGN.md, "Written Voice").
Completed past tense throughout. The shape:

1. **What happened and when**, first sentence, bold lead of five words at
   most, then the time verbatim. `**Duke study came back.** Foster Lin sent
   the results at 10:42 AM PT / 1:42 PM ET`.
2. **What Van Gogh remembers**, in one clause: how long since the ask, how
   many chases, what the item was waiting for. From `item.why`,
   `item.age_days`, `item.due` and the context pack. Paraphrase; never quote
   the reader's own earlier words back at them.
3. **What the draft says**, one clause, for a reply. For a deal, the
   question. For a calendar change, the re-ranked afternoon.
4. **The nod line.** `A reply is written and waiting for your nod.` or
   `Nothing in your notes changed; say the word and it is marked.` Present
   tense is allowed here and nowhere else.
5. **The last sentence** says whether the rest of the page still holds:
   `Nothing else on today's page moved.` only when `others_moved_today` is
   0; otherwise `One other thing moved today; it is on the page.` (or the
   number). Never omit it.

Rules that are not optional: no em or en dashes anywhere (use a comma, a
colon or two sentences); both time zones every time a time appears; no
count without its direction; nothing invented, every fact traces to the
event file, the context pack or the briefing file; no headings, no bullet
lists, no sign-off, no greeting. Under 120 words.

## Step 4: republish the page, then print

The tick has already written this event's line into the Notes block of
`briefing_md`. Republish that briefing's page so the phone shows it too:
follow "The artifact page" in
[`_shared/front-page.md`](../_shared/front-page.md) for `briefing`, building
from `briefing_md`. Fail open: no Artifact tool, no network or
`briefing.publish_page` false means the Note is finished anyway. Never
retry in a loop.

Then print the Note. Its final line, after a blank line, is the page line:

macOS / Linux (bash/zsh):
```bash
"$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; print(briefing_html.page_line(sys.argv[1]))" "<briefing>"
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; print(briefing_html.page_line(sys.argv[1]))" "<briefing>"
```

## What this skill never does

- Send mail. There is no send path here and none may be added.
- Mark a deal dead, tick an item, or edit the briefing file. The tick owns the
  Notes block; the reader owns everything else.
- Print anything but the Note. Progress, reasoning and the draft's full text
  belong in the Drafts folder and the page, not in the email.
