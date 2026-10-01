---
name: warm-start
description: >-
  Fill a fresh vault from the user's own mail and meetings so the first
  briefing has something to work with: deal threads, pages for the people they
  deal with, their stated priorities, and a meeting backlog. Use when the user
  types /van-gogh:warm-start, says their briefings feel empty or generic, says
  Van Gogh does not know about their deals or their people yet, or has just
  finished an install that predates this step.
allowed-tools:
  - Bash("$HOME/.config/van-gogh/venv/bin/python" *)
  - Bash(& "$HOME\.config\van-gogh\venv\Scripts\python.exe" *)
  - Bash(cd $env:CLAUDE_PLUGIN_ROOT)
---

# Warm start

A fresh vault has no deal threads and no pages for the people the user works
with, and nothing in Van Gogh creates either one on its own. Everything that
makes a briefing worth reading sits downstream of them: overdue alerts, the
evidence behind every drafted reply, the relationship radar, the detection of
a deal going quiet. Until they exist the briefing can only list mail.

This fills them from the user's own sent mail, with the user confirming every
deal before it is written. Run it once at install, or any time afterwards on
an install that predates it. It is safe to re-run: a second pass proposes only
what is genuinely missing.

Budget about 25 to 40 minutes, most of it waiting on the backlog ingest.

## Step 1 : Start the scan, then talk while it runs

The scan is the source for everything below and takes 5 to 10 minutes, so
start it first and hold the priorities conversation while it works.

macOS/Linux:
```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" scan --since 60
```
Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\warm_start.py" scan --since 60
```

Tell the user it is running and move straight to Step 2. Do not wait.

## Step 2 : Ask what actually matters, per business

Read `businesses` from `meta`. For each one that is not `personal`, ask the
user, with AskUserQuestion, what their priorities are inside that bucket right
now. Ask about one business per question; offer three or four plausible
options drawn from what you know of their work plus an open answer, and let
them multi-select.

This is the highest-leverage answer in the whole product. With no priorities
set, nothing on the front page carries a reason and the ranking is pure
recency, forever. Say that in one sentence when you ask, so the question does
not read as paperwork.

Persist the answers immediately, in the same turn, never at the end:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" \
  priorities --set '{"acme": ["Close the Harbor deal", "Hire a PM"]}'
```

Windows (PowerShell):
```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\warm_start.py" `
  priorities --set '{\"acme\": [\"Close the Harbor deal\", \"Hire a PM\"]}'
```

It backs up config.json before writing and dedupes against anything already
there, so a re-run never doubles a priority.

## Step 3 : Propose the deals and the people

Once the scan has finished (the file at `meta.logs_dir` named
`week_review_latest.full.json` exists and is from today):

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" propose
```

Show the user `summary_md` as it comes back. Do not reformat it into your own
table: the indices are what Step 4 takes, so they must stay visible and
correct.

## Step 4 : Let them tick, then write

Ask with AskUserQuestion, multi-select, which of the proposed deals are real.
The confirm is the whole point: a dead thread recorded as a live deal is worse
than a missing one, and the user is right here, so ask rather than guess. Make
it easy to say none of them.

Say plainly what a tick does: it writes a thread into their hot cache that
briefings will track from then on, and they can edit or delete any of it in
Obsidian afterwards.

People are written for every ticked deal plus anyone they have several threads
with. Pass both index lists:

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" \
  apply --deals 0,2,3 --entities 0,1,2
```

An empty list writes nothing, which is a real answer.

## Step 5 : File the meeting backlog

This reads the transcripts of recent meetings and writes them into the vault,
which is what gives the first briefing its meeting history. It is the long
part, 10 to 20 minutes for ten meetings. Tell the user that before starting it
and let them decline.

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" ingest --limit 10
```

Ten is deliberate: past that the wait stops being worth it, and the nightly
job files the rest over the following nights. If the user has no notetaker
configured this is a no-op, so skip it rather than explaining it.

If it reports errors, say which part failed and carry on. A backlog that did
not file costs the vault some history, never the setup.

## Step 6 : Prove it landed, then show them

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/warm_start.py" verify
```

`new_deals` and `new_entities` must both be 0: everything proposed was either
written or deliberately skipped. If either is non-zero, say so plainly and
name what is still outstanding rather than reporting success.

Then run the first briefing, so the user sees the result rather than a
description of it:

```
/van-gogh:morning-coffee
```

## Reporting

Close with the counts, in the user's terms: how many deals are now tracked,
how many people have pages, how many meetings were filed, what priorities were
recorded. Then one line on what changes tomorrow, which is that the briefing
can compare against today.

Never claim a count you did not read out of a command's output.
