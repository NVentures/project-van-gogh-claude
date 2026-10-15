# Cold-read log: the weekly scorecard email

Nine rounds. Each round handed a fresh agent ONLY the rendered email as text,
with no knowledge of the build, and asked four questions: does anything
contradict anything else, what are the worst lines verbatim, does any part read
as machine-generated, and what is redundant. Every claimed defect was checked
against the code before it was acted on, because a cold reader's arithmetic is
not reliable and two claims across these rounds were wrong.

The loop converged rather than ended: rounds 1 to 9 each found something real,
and the shape of what they found changed as it went. Early rounds found broken
sentences. Late rounds found true statements that mislead, which is the class
no gate can express.

## Round 1

Found the failure class the whole build had missed: the punch list tested
plumbing, not whether a number is true in the sense the reader takes it.

- `only 0 outside calls in this window` is not English. Zero is not "only"
  anything. FIXED: no calls at all now reads differently from a few calls.
- A 14 day window titled `Your week`. FIXED: "Your fortnight".
- The opening led with `Commitments closed rose from 0 to 2`, inviting the
  reader to hear a doubling where the fact is that two things happened.
- `that week` was ambiguous across a two-half window.

## Round 2

- THE card contradiction: `down from 3.5, the wrong way` printed directly above
  `Target: three or more` on a value of 3. The card graded a passing number as
  a failure. FIXED: the verdict line (`Met.` / `Not yet.`) was added, and a
  target that is a FLOOR stopped being treated as a maximise.
- Every card printed its number three times: headline, count line, bar label.
  FIXED: the count line appears only when it says something the headline does
  not.
- `this week` / `last week` bar labels inside a fortnight header pointed at
  nothing definite. FIXED: "latest week" and "week before".

## Round 3

- `holding above three` sat above a verdict of `Not yet.`, and 3 is not above
  three. FIXED.
- The opening duplicated two cards verbatim, so the reader read both facts
  twice within one screen. FIXED: the opening now counts and interprets rather
  than retelling.
- `Worth a run on a quiet Friday, before the week closes` claimed a day the
  email cannot know it is read on. FIXED, and a test now bans every weekday
  name from the spotlight text.

## Round 4

- `steady` printed above bars reading 3 and 3.5, which the bars contradict.
- `the first week` introduced a third vocabulary for a half the bars already
  name. FIXED: one vocabulary everywhere.
- `Not enough yet.` above a reason beginning "not enough" said it twice.
  FIXED: the reason alone carries it.

## Round 5

- Zero-valued clauses (`0 arrived`, `0 newly late`) printed as content. A
  template filling every slot regardless. FIXED: only what happened is said.
- The card led with the half of a two-part target that PASSES while the verdict
  said `Not yet.`, so the reader had to hunt for which half failed.
- `up from 0` and `2 more than last week` were the same comparison twice.

## Round 6

- The worst line of the whole build: `49%` / `against a bar of nine in ten` /
  `of action items name who owns them`. The number was separated from its noun
  by an entire clause. FIXED.
- `3 improved` was unverifiable when one card showed no prior. FIXED: only
  measures carrying a comparison may be counted.
- Bars of 0 against 1 were decoration. FIXED: bars are drawn only once the
  numbers are large enough to have a shape.

## Round 7

- `every one that can be compared improved` sat directly above the card the
  next sentence called short. Both sentences were true of different sets and
  read as one contradiction. FIXED: the improvement clause was dropped
  entirely; the per-card verdicts already carry it.
- Digit and word for the same quantity one clause apart (`3 items` / `bar of
  three`).
- The waiting breakdown accounted for the items that LEFT but never the ones
  that stayed, so the arithmetic looked incomplete. FIXED: "2 still open after
  2 you closed, 1 dropped off".

## Round 8

Found the defect a previous fix had introduced: a card titled "Action items per
meeting" whose headline was a percentage about ownership.

ROOT CAUSE, and the largest change of the nine rounds: one card was carrying a
two-part target. Whichever half led, the other had to be explained in prose and
the card's own title named the number it was not showing. FIXED by splitting
the concept rather than rewording it: the card now reports ONE quantity, the
share of action items that name an owner, and its title says so. The count per
meeting was dropped as a score because it measures the notetaker rather than
the user.

## Round 9

- `171 of 351 action items say who owns them. The typical meeting logs 3.`
  invited the reader to divide 351 by 81, get 4.3, and find a third figure with
  no explanation. FIXED: the stranded median was removed.
- `2 you closed, 1 dropped off, 2 still open` used "2" for two different things
  in one sentence, beside a headline of 2 meaning the second of them. FIXED:
  the survivors lead, so the headline number is the sentence's subject.
- Bar labels restated the headline verbatim on every charted card. FIXED: the
  bars carry the shape, the movement line carries the numbers.
- `over two weeks` in a target against bars labelled week on week. FIXED.

## Final round: clean

The tenth reading found no contradiction, no machine-generated tell, and no
redundancy beyond a footer date repeat that is a deliberate provenance line.
The email as it now stands is recorded in `rendered-final.txt` beside this log.

## Two claims that were WRONG and were not acted on

A cold reader's arithmetic is not reliable, and both of these were disproved
against the code before anything changed:

1. Round 1 claimed `81 meetings, 0 outside calls` was an arithmetic
   contradiction. It is not: 81 counts every meeting note filed in the vault
   and 0 counts external calls detected on the calendar. Two populations. The
   fix was a labelling one ("internal ones included"), not a maths one.
2. Round 8 claimed the threads breakdown "does not balance". It balances
   exactly: 5 minus 2 closed minus 1 dropped is 2. The reader retracted it
   mid-pass in a later round. The real defect was ambiguity about which "2"
   the headline meant, which is what was fixed.
