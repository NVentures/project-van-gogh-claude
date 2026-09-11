# The front page, the folds, and the page

Shared by `/van-gogh:morning-coffee`, `/van-gogh:afternoon-tea`,
`/van-gogh:week` and `/van-gogh:week-retro`. Every one of those scripts emits
the same five keys, so every one of them renders the same way.

## The five keys

| Key | What it is |
|---|---|
| `front_page` | the items code chose, in order, each with `bucket_tag`, `bucket_name`, `function`, `label`, `why`, `action`, `account`, `counterparty_email`, `counterparty_name`, `subject`, `draft_note` |
| `front_page_md` | those items rendered for the TERMINAL, opening sentence included. Same items, order, fields and wording as `front_page_file_md`; only the section chrome differs (band rules and a padded label column instead of a heading and bold text) |
| `fold_rows_md` | one summary line per business, rendered for the terminal |
| `front_page_file_md` | the same page in markdown, for the written file, the web page and the digest |
| `fold_rows_file_md` | the same rows in markdown; kept for callers that want a ledger view without folds, NOT written into the briefing file |
| `folded_md` | the full ledger as one closed callout per business (markdown already) |
| `counts` | `{open, late, front, folded, stale}` |

Code chose them. Do not re-rank, re-word, add an item, or drop one. If the
selection looks wrong the fix is the user's `keywords[]`, `priorities[]` or
`briefing.front_page_cap` in config, not a quiet correction here.

An empty `front_page_md` means the front page could not be built. Render the
full ledger (`buckets_md`) instead and say one line about it. Never invent a
front page of your own.

## Drafting, before you render

Only Morning Coffee and Afternoon Tea draft. Week and Week Retro never do.

For each `front_page` item whose `action` is `email reply` and whose
`draft_note` is empty:

1. Pull what the vault already knows about them, so the reply argues from the
   record instead of from the last message alone.

   macOS / Linux:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/context_pack.py" \
     --name "Their Name" --email "them@example.com"
   ```
   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\context_pack.py" `
     --name "Their Name" --email "them@example.com"
   ```

   It prints `entity_page`, `meetings`, `open_commitments`, `deal_context` and
   `graph.neighbors`, each entry carrying the file it came from.

   - `"is_empty": true` means the vault knows nothing about this person. Write
     from the thread alone and say so in the item's line. Never let the pack's
     silence be filled in by invention.
   - Otherwise use it, and only it. Every claim about history, a commitment or
     a prior conversation must trace to a pack entry. A fact you cannot point
     at a file for does not go in the email.
   - An open commitment is the strongest material there is: it is something the
     user promised and has not closed. Lead with it when one exists.

2. Write the reply in the user's voice (`meta.voice_guide` and the tone
   profile). Honour the age of the thread: past two weeks, acknowledge the
   gap in one clause, never restate a date or a next step from the original,
   and ask whether it is still live or make a present-day offer. A fluent
   reply that confirms a meeting from four months ago is worse than no reply.
3. Create the draft. Never send: that script has no send path.

   macOS / Linux:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/draft_email.py" \
     --provider google --label "Gmail" --to "them@example.com" \
     --subject "Re: the subject" --body-file "$BODYFILE" --counterparty "Their Name"
   ```
   Windows (PowerShell):
   ```powershell
   & "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\draft_email.py" `
     --provider google --label "Gmail" --to "them@example.com" `
     --subject "Re: the subject" --body-file "$BODYFILE" --counterparty "Their Name"
   ```
   `--provider` and `--label` come from the item's `account` matched against
   `meta.accounts`.

4. Read the JSON it prints and say the true thing:
   - `{"ok": true, ...}` the reply is in that account's Drafts folder.
   - `{"ok": true, "existing": true}` it was already drafted; leave it alone.
   - `{"no_token": true}` that account has no credential here. Put the text
     inline in the briefing and say it is not in a Drafts folder.
   - `{"skipped": "terminal_deal"}` the deal behind it is closed. No draft,
     and say why in the item's line.

Keep every draft's text in the written file under `## Drafts`, whether or not
it reached a Drafts folder. The heading line and the one-line-per-reply
summary under it are `drafts_file_md`, rendered by code: paste it, then add
the callouts holding each reply's full text beneath.

## Render order

Exactly this, top to bottom:

1. **THE READ**, three or four sentences you write. See below.
2. **SUGGESTED FOCUS TASK**, one item you choose. See below.
3. `travel_md`, verbatim, when the briefing emits it and it is non-empty.
   Built by code from the calendar and the airline confirmation; never write
   or paraphrase it, and never restate a time it carries. Omit the block
   entirely when empty, which is most days.
4. `front_page_md`, verbatim. It already carries the opening sentence and the
   DO TODAY band. Do not write your own opening above it.
5. `drafts_md`, verbatim, when the briefing emits it and it is non-empty.
   Built by code from the draft ledger; never write or paraphrase it.
6. The day: today's meetings, where the briefing has them.
7. What changed since the last one: completions and what the calls left behind.
8. `extra_sections`, when non-empty: the user's own extension sections
   (modules they placed in `~/.config/van-gogh/extensions/`; see
   `app/extensions.py`). Render each entry as `### {title}` followed by its
   `md` verbatim — the content is the user's code talking to the user, so do
   not summarize, re-order or drop one. A failed extension is already a line
   in `errors`; report it there like any other error, never as a section.
9. `fold_rows_md`, verbatim. Nothing after it except step 10: do not print a
   menu of the fold commands. They work whenever the reader asks for them, and
   a briefing that ends by teaching its own syntax reads as a chatbot.
10. `meta.whats_new`, only when non-empty: the plugin updated recently and these
   are the release notes the reader has not seen. Render a small closing block,
   past tense, no commands quoted:

   ```
   Van Gogh updated itself.
   {meta.whats_new, verbatim}
   ```

   It is "" almost always (the notes show exactly once, in the first chat
   session after an update, then go quiet — the script stamps them seen when
   it emits them), so most briefings still end at the fold. This is the one
   exception to "nothing after the fold" because it is the only
   end-of-briefing text in completed aspect about the product itself;
   imperatives like "Run claude plugin ..." stay out (that is
   `update_notice`, which briefings never print, see /van-gogh:check-updates).

   **This block is terminal-chat only.** It never goes into the written file,
   and therefore never reaches the digest email, the Workbench page, or the
   published artifact — those are briefing surfaces, and release notes are a
   conversation moment. (Unattended runs already receive `""` here; the rule
   matters when you render both the chat and the file in one session.) A
   reader who missed it has /van-gogh:release-notes.

11. `meta.job_watch`, only when non-empty: something scheduled did not run when
   it should have. Render it as one line under a rule, verbatim, with no
   heading and nothing added:

   ```
   ---
   {meta.job_watch, verbatim}
   ```

   It is "" on a normal day, which is most days. Unlike step 10 this belongs on
   **every** surface, the written file and the digest email included: if the
   morning briefing had to be restarted, or is waiting on a usage cap, the
   person reading it by email is the one who most needs to know why it is
   late. Never dress it up, never add a fix of your own, and never turn it
   into a section. The sentence already says what happened and whether
   anything is needed of the reader.

12. `meta.task_stall`, only when non-empty: work that stopped moving. Same
   treatment as step 11, one line under the same rule, verbatim:

   ```
   {meta.task_stall, verbatim}
   ```

   The two are different in kind and must never be merged into one sentence.
   Step 11 is machinery: something did not run, and the system either fixed it
   or is waiting. This is the work itself: a commitment weeks past its date, a
   draft nobody read. Nothing re-runs those, which is exactly why they are
   worth a line, and why that line says whose move it is rather than counting
   them. Also "" on most days.

## The suggested focus task

Everything else on this page is reactive. The front page ranks by late, then
due, then oldest, which is a good answer to "what is on fire" and no answer at
all to "what would actually move a business forward". Work that compounds
almost never has a deadline and nobody chases the reader for it, which is
exactly why it never reaches a page built on deadlines and chasers. Left alone,
a reader can clear eight items a day for a month and end the month where they
started.

So one item, chosen by you, above the checklist. Not the most urgent thing.
The thing that buys the most.

**How to choose.** Read the whole ledger, not the front page: `buckets` carries
every open item, and the front page is the eight that shouted loudest.
Weigh, in this order:

1. **What compounds.** The decision five other items are waiting on. The
   document that unblocks three people. The one conversation that closes a
   question three deals keep re-asking. Leverage first: a task that finishes
   itself and nothing else loses to a task that finishes six things.
2. **The reader's stated priorities.** `businesses[].priorities[]` is what they
   said matters, in their own words, and `priority_matched` on an item is a
   judged link to one. A compounding task inside a stated priority beats a
   compounding task outside one.
3. **What is quietly stuck.** A priority with nothing moving on it, a thread
   cold for weeks that both sides still need, a decision nobody has made
   because nobody is chasing it.

Reject the obvious answer if it is merely urgent. If the honest choice is an
item already on the front page, choose it anyway and say what makes it the
focus rather than just the top of a list; do not invent a second task to avoid
repeating one.

**What to write.** Four parts, in this order, each one or two sentences:

| Part | What it says |
|---|---|
| The task | What to do, named concretely enough to start. Not "make progress on Harbor Solar" |
| Why this one | What makes it beat everything else on the page today. Name the alternative it beat |
| The first move | The concrete first step, so there is no starting cost. A call to book, a file to open, a person to ask |
| What it unblocks | What is freed when it lands, with the count if you have one: "the three items under Legal that are all waiting on this answer" |

**Rules.** Every claim traces to a field, exactly as the read does. Never
invent a task the ledger does not support: a focus task the reader has no
context for is worse than none, because it makes them doubt the eight items
below it. One task, never two. If nothing in the ledger compounds (a genuinely
quiet week, or a ledger of nothing but same-day replies), say so in one line
and render no card: a manufactured focus task is the failure this section
exists to prevent.

Never phrase it as a command. The reader decides what they work on; this is
the recommendation of someone who read everything and has an opinion.

## The read

The counts sentence inside `front_page_md` says how many things there are. It
never says what is going on, because it is arithmetic: the code caps it at two
sentences and three named numbers, every one of them counted. That is the right
constraint for a number and the wrong one for a day. So the briefing opens with
three or four sentences that a person would say out loud.

Write it from what the script already found, and from nothing else:

| Say this | From |
|---|---|
| what today is actually about | `front_page`, `today_calendar` |
| what moved or slipped since the last run | `completed`, `slipping`, `status_change_alerts`, `hotcache_sync.revivals` |
| the one thing that matters most, and why it is that one | the top of `front_page`, with its `days_late` or `due` |
| what is quietly going wrong | `newly_cold`, `fold_rows[].flags` |

Four rules, and the first is the one that matters:

1. **Every claim traces to a field.** If you cannot point at the key it came
   from, it does not go in. No "momentum", no "a busy day ahead", no reading of
   a mood the data does not carry. An invented sentence at the top of a
   briefing is worse than no sentence, because everything under it is true and
   the reader has no way to tell which line was the guess.
2. **Name things.** "Lumen is three days late and it is the last open point on
   the redline" beats "several items need attention". A read made of categories
   is the counts sentence again, with adjectives.
3. **Say the shape of the day, not its size.** The size is already the sentence
   underneath you. If today is three calls and one decision, say that. If it is
   one thing that has been sitting for nine days, say that, and say the other
   six can wait.
4. **A quiet day is allowed to be quiet.** Two sentences is a complete read
   when nothing is happening. Do not pad it to four. If the scan found nothing,
   say the scan found nothing, and do not write a read at all.

Never open with the date or the weekday: the header carries both. Never end by
telling the reader what to do next, and never end with a question. The read is
what a chief of staff says while handing over the folder, then stops talking.

There are no standalone alert sections any more. A deal status change, an
overdue deal, a revival, a contact gone quiet: each is already either an item
on the front page or a flag on a business row, and each is still in the fold.
Do not add a section for them.

## Opening a fold

- `open <business>` and `open <business> <function>`: run `fold_slice` and
  paste what it returns verbatim. It is a slice of the same ledger, never a
  fresh summary.

  ```bash
  "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os, json; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import week_review as wr; d=json.load(open(sys.argv[1])); keys={tuple(i['key']) for i in d['front_page']}; print(wr.fold_slice(d['buckets'], keys, sys.argv[2], sys.argv[3] if len(sys.argv)>3 else ''))" "$SIDECAR" "<tag>" "<function>"
  ```
- `open everything`: paste `buckets_md`.
- `file <thing> under <business>`: add the word to that business's
  `keywords[]` through `/van-gogh:update-settings`. Say that the next
  briefing will file it there. Never edit config.json by hand.

## The written file

Front page first, then `## Drafts` as a callout, then the `extra_sections`
(each as `### {title}` + `md` verbatim, same as the terminal), then
`folded_md` verbatim.

**Use the `_file_md` keys here, never the terminal ones.** The terminal render
aligns its columns with whitespace, and every markdown renderer collapses
whitespace, so pasting `front_page_md` into a markdown file turns the whole
front page into one run-on paragraph. That shipped onto a real published page
and only a screenshot caught it: no gate can see it, because the text is all
still there.
The callouts are what fold: `> [!note]- Name` is closed in Obsidian, a closed
`<details>` in the Workbench, and a shaded block in the digest email.

**`fold_rows_file_md` does not go in the file.** It is one summary line per
business, and `folded_md` opens with the same business names right under it, so
writing both lists every business twice on a surface where the fold headers are
already the list. The rows exist for the terminal, which has no folds and needs
a ledger view. On a surface that folds, the fold headers are that view.

## The two pages

There are two, and they are not the same thing.

**The local page** is the one that matters. It is served by the Workbench at
`/briefing/<name>` from the file you just wrote, it is what a scheduled run
opens by itself, and it is the only surface where the reader can act: a tick
closes an item in the vault, a stamp sends the reply this briefing drafted, and
the chat panel answers questions about the page. You do not build it. Writing
the file IS building it, because the server renders that file on request. If
you changed the file, the page changed.

**The artifact** below is a read-only mirror for a phone. It renders from the
same markdown through the same function, but with no token and no controls, so
it can be shared without handing anyone the ability to act.

## The artifact page

Each briefing owns exactly one artifact URL, stored in
`logs/briefing_pages.json`. Every run republishes to that same URL. A run
never creates a second link.

1. Read the stored URL:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; print(briefing_html.page_url(sys.argv[1]))" "<briefing>"
   ```
2. If a URL came back, read that artifact first with `action: "read"` and
   build your update on what it returns. Publishing to an artifact this
   session has not read is refused.
3. Build the page from the WRITTEN FILE (the one made of `front_page_file_md`
   and `folded_md`), never from the terminal render.
   `redact` runs over the body before anything is published:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html as b; md=open(sys.argv[1], encoding='utf-8').read(); open(sys.argv[2],'w',encoding='utf-8').write(b.render_page(b.redact(md), {'title': sys.argv[3], 'briefing_date': sys.argv[4], 'newest_date': sys.argv[4]}))" "$BRIEFING_MD" "$PAGE_HTML" "<Title>" "<date>"
   ```
4. Only now publish the page with the Artifact tool, passing the stored URL when
   there is one, then record what happened:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; briefing_html.record_page(sys.argv[1], sys.argv[2], sys.argv[3])" "<briefing>" "<url>" "<date>"
   ```
5. Print the page line, whichever it is. `page_line` gives back the link when
   the page is current and the reason plus the day it still shows when it is
   not, so print it unconditionally as the last line of the briefing:
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; print(briefing_html.page_line(sys.argv[1]))" "<briefing>"
   ```
   The scripts already emit it as `page_note`, so in a normal run you have it
   without asking again.

6. Fail open, always. No Artifact tool in this session, no network, or
   `briefing.publish_page` false: the briefing is finished anyway. Record the
   failure with its reason and print the one line it gives back.
   ```bash
   "$HOME/.config/van-gogh/venv/bin/python" -c "import sys, os; sys.path.insert(0, os.path.join(os.environ['CLAUDE_PLUGIN_ROOT'], 'app')); import briefing_html; briefing_html.record_page(sys.argv[1], '', sys.argv[2], published=False, reason=sys.argv[3]); print(briefing_html.failure_line(sys.argv[1]))" "<briefing>" "<date>" "<reason>"
   ```
   Never retry in a loop, and never let this change the exit of the run.

The page is private on claude.ai. Sharing it is the user's action from the
page menu, never yours.
