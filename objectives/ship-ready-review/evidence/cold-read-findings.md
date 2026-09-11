# Cold read: two strangers, three briefings, no knowledge of the build

Two fresh agents were each handed only `evidence/three-briefings.txt` and told
they were the business person reading it: a dealmaker, an advisor, and someone
who installed the tool yesterday. Ninety seconds, before a first meeting. No
repo access, no code, no context about how any of it was made. That was the
point; the value came from not knowing.

Both were asked the same five questions, and both were asked to quote the three
worst lines verbatim and say whether they would act on the briefing or go back
to their inbox.

Both said they would go back to their inbox. The first found a defect no gate
in this contract was written to catch.

## Round one

**Verdict: skim it and go back to the inbox.**

> "It told me 'nothing moving on this' about a priority while the movement was
> sitting on the page. Once I catch that, the document's central claim, that it
> has sorted my morning for me, is gone, and I have to re-check the source
> anyway. A briefing I have to verify is slower than the inbox."

| Finding | Their words | What it was | Fixed |
|---|---|---|---|
| A priority reported starving while its own item sat nine lines below, under a heading saying it matched nothing | "the words are identical. If I trust the 'nothing moving' line I ignore a warm intro" | A real bug: bucket assignment was keyword-only, so an item never reached the judgment layer unless a keyword placed it first | `assign_bucket` now falls back to the words the user wrote their priorities in. Two tests pin it |
| "Everything else" | "a bucket name from the code, not English. It appears under four headings meaning four different things" | Developer vocabulary | Appears only where there are priorities to be other than; gone elsewhere |
| "NOT SORTED, these did not match any part of your work" | "the tool is telling me my invoice isn't my work" | A judgment about the items, when it was the tool that failed to place them | "Couldn't place these, tell me where they belong and I will file them next time" |
| The setup nag stapled to two section headings | "passive-aggressive setup nagging, printed on the same line as a real section heading" | Nagging in the wrong place | One line at the foot, naming only buckets the reader can see |
| Day one showed two empty scaffolds above the only real item | "A new user reads 'PERSONAL, nothing moving' at the top and may not scroll" | The contract deadline was buried under an empty heading | A bucket with no items and no priorities is not rendered |
| No sender, no age on any item | "'QX Corp diligence list', since when? Two hours or two weeks changes everything" | Not actionable in ninety seconds | Items carry "from Dana Ruiz" and "6 days ago" |

## Round two, on the rewritten version

A different agent, same conditions, sharper.

**Verdict: act on the top two lines, then back to the inbox.**

| Finding | Their words | Fixed |
|---|---|---|
| "Worth a look" had become a junk drawer | "a 40-day-old newsletter. The label is a lie and I will stop trusting the other two" | Renamed "Older, still open", which is what it is |
| The footer named a section the reader could not see | "The tool is naming a category it never showed them. That is a hardcoded string leaking through, and it's the first thing a day-one user sees" | The footer names only rendered buckets |
| Day one headed "COULDN'T PLACE THESE" | "the first impression is 'this tool failed'. Inside it, though, is the contract signature. That one line earns the install" | With nothing configured there is nothing to place against, so the section is "TODAY" and the footer explains the setup step in their situation |
| "Not tied to a priority" read as a priority name | "a negative-space label sitting where a priority name should be" | Only appears where there are priorities; absent otherwise |

## Declined, on purpose

**Title-casing the priority names**, so `grow the pipeline` renders as
`Grow the pipeline`. Those are the words the user typed. Echoing them back
verbatim is the feature; normalizing them is the product editing its owner.

**Ranking two resignations above a countersignature.** The reader is right that
resignations are more urgent, but nothing in the data says so. Inventing an
urgency signal is worse than the honest order: dated first, then waiting, then
older.

## Still open, recorded not built

- **Verbs on every line.** "Send QX Corp the diligence list", not "QX Corp
  diligence list". A noun with no verb is not an instruction. This needs the
  drafting layer to summarize each item, which is the deferred PR.
- **A count at the top.** "12 emails overnight, 4 need you, 2 late", so a reader
  can tell a quiet night from a broken filter.
- **Newsletters and fire drills reaching the briefing at all.** That is the
  upstream spam filter, not the bucket layer.

## Why this pass is worth its four minutes

Twenty-nine contract items, twelve verification scripts and 734 tests were green
when the first reader was handed the page. Every defect above was invisible to
all of them, because a gate tests a property somebody thought of, and nobody had
thought to ask whether the page contradicts itself.

The highest-yield question of the five was "does anything contradict anything
else". It is the one that found the bug.
