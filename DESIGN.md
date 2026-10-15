# Design System: Van Gogh

Codename **Kneeboard**. This file is the source of truth for every visual and UI
decision in this repo. Read it before touching any HTML, CSS, or rendered artifact.

## Product Context

- **What this is:** the local briefing page and the Van Gogh Workbench, served
  from `127.0.0.1` by a Python process in the plugin. A scheduled run writes the
  briefing and opens the page; the reader acts on it and closes it.
- **Who it's for:** founders and operators running several businesses. Same look
  for every plugin user. See `PRODUCT.md` for the full record.
- **Space:** local-first personal chief of staff. Category peers are Linear,
  Superhuman, Raycast, Attio, Obsidian.
- **Project type:** an operator's working document, desktop first.
- **Usage pattern:** a twice-daily ritual (Morning Coffee, Afternoon Tea), not an
  all-day resident app. This drives every choice below.

**The memorable thing:** *it's already done, and the page is a checklist you work
down.* Someone competent worked overnight and was tidy about it. Every row states
what it is and what was done for you, and the only thing between finished work and
shipped work is your press.

## Aesthetic Direction

- **Direction:** the pilot's kneeboard. A checklist strapped to your leg, read at
  arm's length, in a cockpit at night.
- **Decoration level:** none. Cards, rules, and one color per state do the work.
- **Mood:** procedural and calm. A checklist is not a to-do list: it is a
  sequence someone competent already validated, and you are confirming it.
- **The structure:** challenge on the left, dotted leader, response on the right.
  Every row of the product reads that way. The challenge is the thing; the
  response is what was done about it, or the control that does it.
- **The phases:** the four briefings are the four phases of a flight, and each
  page says which it is. Morning Coffee is the departure checklist, Afternoon Tea
  the shutdown checklist, The Week the flight plan, Week Retro the debrief.
- **The deliberate break:** the category is uniformly dark-first with a neon
  accent. This is dark-first too, but for the opposite reason: not because you
  live inside it, but because a kneeboard is read under a red map light and the
  page opens by itself while the room is still dark. The light theme is the paper
  kneeboard, fully supported.

## Typography

**Two families, both from one designer's cockpit brief.** B612 and B612 Mono were
commissioned by Airbus for aircraft cockpit displays and tested for legibility at
arm's length under vibration and poor light. That is the actual reading condition
of this product, so the metaphor is literal rather than decorative.

- **Prose (headings, body, item titles):** B612. Humanist, open apertures, holds
  at 15px, designed to be unambiguous when read fast.
- **Everything else:** B612 Mono. Every number, label, table cell, status word,
  response, and control. All figures tabular, always.
- **Loading:** self-hosted woff2 in `app/workbench_static/fonts/`. No CDN, ever
  (the server must work with no network). Ship the license file alongside.
- **Licensing:** B612 and B612 Mono are SIL OFL 1.1, so bundling and
  redistribution with software are permitted. Retain the copyright notice.

```css
--font-prose:"B612","Helvetica Neue",Arial,sans-serif;
--font-mono: "B612 Mono","SF Mono",Menlo,"Cascadia Mono",Consolas,monospace;
```

### Scale

The scale is coarse on purpose. A kneeboard is read at arm's length, not at
reading distance.

| Token | Size / line-height | Family | Treatment | Use |
|---|---|---|---|---|
| `--t-label` | 11 / 1.2 | mono 700 | uppercase, `letter-spacing:.1em` | Card bars, phase labels, column heads |
| `--t-resp` | 12 / 1.35 | mono 700 | uppercase for a state word | The response column, status words |
| `--t-data` | 12.5 / 1.35 | mono 400 | | Table body, tags, times |
| `--t-body` | 15 / 1.45 | prose 400 | max 62ch measure | Briefing prose |
| `--t-item` | 15 / 1.35 | prose 700 | | An item title, the challenge |
| `--t-sect` | 18 / 1.3 | prose 700 | | Section heads inside a briefing |
| `--t-page` | 26 / 1.1 | prose 700 | | The briefing name |
| `--t-hero` | 48 / 1 | mono 700 | tabular, `-0.02em` | The front-page count, the week line |

One prose weight (400) plus one bold (700). Emphasis comes from size, weight, and
the response color, never from bolding a run of body text.

## Color

**Approach:** an instrument panel. Cool neutrals, four status colors that each
mean exactly one thing, and nothing else. Zero pure black in dark; pure white is
permitted on exactly one surface, the card in light mode, because a paper
kneeboard is white paper.

```css
:root{
  --bg:#F3F4F1; --card:#FFFFFF; --wash:#EEF0EE;
  --text:#1A1F24; --muted:#5C6670;
  --rule:#C9CFD3; --rule-soft:#E1E5E8;
  --amber:#9C5F0A; --cyan:#1F7A99; --red:#B8321F; --green:#2E7A4A;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#101418; --card:#171C22; --wash:#1D2329;
  --text:#E6E9EC; --muted:#8B95A0;
  --rule:#2A323B; --rule-soft:#222930;
  --amber:#E8A33D; --cyan:#5FB8D9; --red:#EA6A57; --green:#58B57A;
}}
:root[data-theme="dark"]{ /* repeat the dark block so the toggle wins both ways */ }
```

**Semantic meaning, fixed:**

| Token | Meaning | Never used for |
|---|---|---|
| `--amber` | Waiting on you: your action, your press, your signature | Anything already done |
| `--cyan` | Waiting on them, in progress, links | Success or your action |
| `--green` | Done: sent, delivered, checked off, kept | Anything in progress |
| `--red` | Late, died, failed, at risk | Decoration or emphasis |

**The scarcity rule.** Amber marks work that is waiting on the reader, and
nothing else. Not the logo, not the active tab, not a hover on something inert,
not a heading, not a chart series. That is what lets an unfinished row pull the
eye down a dense page with no glow and no animation. The stamp is amber because
pressing it is the one thing only the reader can do. Break this once and the
thesis dies.

Three things carry it, and the list is closed: the stamp, the button on a
travel line, and the outline on the travel card. The two additions are the
same meaning at a different scale. A trip is the one thing on the page whose
deadline the reader cannot renegotiate, because a flight leaves whether or not
the briefing was read carefully; everything else can slip a day. Outlining
that card says of the whole block what the stamp says of one row.

The rule that keeps this from being a licence: amber may outline a card only
when nothing inside it can wait. A card holding a mix takes no outline,
because then the colour would be describing the heading rather than the work,
which is the drift this rule exists to stop. The drafts card is the worked
example: its replies are genuinely waiting on the reader and its links are
still buttons, but a reply can sit another day without anything being lost, so
it is marked for the Workbench and left uncoloured.

`tests/test_local_page.py::test_amber_means_only_waiting_on_you` holds that
list, so widening it is a deliberate edit to this file and that test together,
never a CSS change on its own.

**Contrast, measured (WCAG):**

| Pair | Light | Dark | Verdict |
|---|---|---|---|
| `--text` on `--bg` | 15.0:1 | 15.2:1 | passes AAA |
| `--text` on `--card` | 16.6:1 | 14.1:1 | passes AAA |
| `--muted` on `--card` | 5.9:1 | 5.6:1 | passes AA |
| `--amber` on `--card` | 5.2:1 | 7.9:1 | passes AA |
| `--cyan` on `--card` | 4.9:1 | 7.6:1 | passes AA |
| `--red` on `--card` | 6.0:1 | 5.5:1 | passes AA |
| `--green` on `--card` | 5.3:1 | 6.8:1 | passes AA |

Measured by `tests/test_design_contrast.py`, which recomputes every figure in
this table from the token block above and fails on drift. The first draft of
this file carried eight invented figures, two of which hid a real failure.

Every status color carries body-text contrast on the card in both themes, which
is what lets a state word be set in its own color rather than in ink beside a
swatch.

**Status is never color-only.** Every state also carries its word in mono
(`SENT 9:12`, `DRAFTED 06:40`, `4 DAYS LATE`, `KEPT`), so the product survives
grayscale printing and color blindness with a single system.

## Spacing

- **Base unit:** 8px, with 4px permitted for dense card interiors.
- **Density:** compact. An item row is 6px vertical padding inside a 1px rule;
  cards breathe more than rows do.
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64)

## Layout

- **Approach:** a stack of portrait cards on a panel, flush left inside each
  card, one column. The kneeboard is a narrow strip, not a spread.
- **The card:** every section is a card. 1px `--rule` border, 2px radius, a bar
  across the top carrying the section name in mono uppercase on the left and its
  count on the right. The bar is the only place a section name appears.
- **The row:** challenge, dotted leader, response. The leader is a CSS dotted
  border, never a run of period characters. On a narrow screen the leader
  disappears and the response drops beneath the challenge.
- **The tick:** a 11px square with a 1.5px border at the far left of every
  actionable row. Filled green when the item is closed. It is a control, not an
  icon: pressing it writes to the vault.
- **The phase label:** each page carries its phase above its name, in mono
  uppercase amber. It is the only always-amber element, because it names what
  the page is for.
- **Max content width:** 720px. This is deliberate and unusually narrow: a
  checklist read fast is read in one column, and a wider measure makes the
  leader long enough to lose your place across.
- **Border radius:** 2px on cards and controls. Nothing is a pill. Nothing is a
  circle except graph nodes.
- **Responsive:** below 560px the row collapses and the leader disappears. The
  page is designed to work at phone width without a second layout.

## The Stamp

The send control is a stamp, not a button. Square-ish (2px radius), mono
uppercase, 0.18em tracking, 1.5px `--amber` border, transparent fill, `--amber`
text. On press it fills with amber, then the row rewrites itself in place to
`SENT 9:12 AM PT / 12:12 PM ET` in green. It should feel like pressing something
onto paper. The product is deliberately procedural everywhere except where the
reader acts.

## Voice and Grammar

**The product speaks in completed aspect. The user owns the only two imperatives.**

Write "Drafted at 06:40. Waiting on you." and "Died at 04:12 on the second retry."
Never "Create draft" or "3 open items". The only present-tense imperatives
anywhere in the interface are `SEND` and `KICK OFF`, the two things only a human
can do. This grammar carries the whole thesis; a style guide that flattens every
string to a consistent imperative would destroy it.

Times are shown in PT and ET side by side. No em-dashes or en-dashes in any
string, label, or generated artifact; use a comma, a colon, or two sentences, and
a middle dot (`&#183;`) for a nil value in a table cell.

## Written Voice (briefings, not just the interface)

**The reader runs a business. They did not ask for software.**

Everything in a briefing is read by someone with fifteen minutes and no
patience for vocabulary that came out of a data model. The interface grammar
above governs labels; this governs sentences.

- **Lead with the point.** The first line of any section says the thing. Not
  what the section is, not how it was assembled.
- **Short sentences.** If a sentence needs a second read to parse, rewrite it.
  Four sentences of prose in a row is the ceiling.
- **No vocabulary from the data model.** Never "unassigned", "bucket key",
  "priority_matched", "cold_urgent", "null", "degraded flag". If a reader would
  not say the word out loud, it does not ship. "Not sorted" and "these did not
  match any part of your work" are the same information in a person's words.
- **Explain a term the first time it appears.** A client who has not read the
  docs is the normal case, not the exception.
- **No filler openers.** Never "Here is a breakdown", "In summary", "It is
  worth noting", "Great question", "Let me". Start with the content.
- **No AI tics.** No "delve", "utilize", "robust", "seamless", "comprehensive",
  "leverage" as a verb, "navigate the complexities", "ever-evolving". These are
  scanned for; see `objectives/*/voice_check.py` for the enforced list.
- **Say when you did less.** A page assembled without part of its machinery
  says so in its first line. A confident page that quietly hid something is
  worse than an honest noisy one.
- **Explain a judgment in one clause.** When something is filtered out, the
  reason travels with it, in the reader's own terms ("not about what you said
  matters in Power Market"), never as a code or a score.

The same rules apply to error text. A stack trace is not an error message, it
is a support call: one line, naming the fix, in words the reader can act on.

## Motion

- **Approach:** minimal-functional. Three animations exist in the entire product.
  1. The stamp fills, 220ms ease-out.
  2. A running job advances a 3px rule in its header row, with a live mono
     elapsed counter in tabular figures.
  3. A row that just changed state (sent, ticked) settles into its new text over
     140ms. No slide, no fade of the whole row.
- **Easing:** enter `ease-out`, exit `ease-in`, move `ease-in-out`.
- **Duration:** micro 50-100ms, short 150-250ms, medium 250-400ms.
- **Forbidden:** spinners, skeleton shimmer, toasts. A loading card shows its bar
  and empty rules, because a checklist with nothing written on it is already the
  correct empty state.
- Honor `prefers-reduced-motion`: disable all three, keep the end states.

## The Brain

The vault graph renders in the same ink on the same panel. Nodes are filled
circles sized by degree; edges are hairlines at 8% opacity. **Two inks, one
mark:** `--text` for every note, at full strength when touched in the last 7 days
and at 42% when not, so recency reads as depth of ink rather than a second color;
and `--red` for the current query's hits, the way you would ring an entry. No
amber here (amber means waiting on you, and a note is not). No rainbow taxonomy,
no neon, no glow. The query box is a single ruled line beneath it, no border box.

## Inbox

Sender logos are the only imagery in the product: 16px, grayscale by default,
full color only on the focused row. Deal-stage badges are mono uppercase words
with a 2px colored left rule, never a filled pastel pill.

## Anti-Slop Rules (enforced)

1. No purple, no gradients, no glow. No gradient of any kind ships.
2. No icons anywhere, which makes icon grids structurally impossible. Navigate
   with words and counts. The tick square and the dotted leader are controls
   drawn in CSS, not icons.
3. Nothing is centered except text inside the stamp. Every column and heading is
   flush left to one optical margin.
4. No decorative image assets. The Brain graph and sender logos are data.
5. No glassmorphism, no `backdrop-filter`, no frosted headers. Surfaces are
   opaque.
6. No pure black on any background. Pure white appears on exactly one surface,
   the light-mode card.
7. `--amber` means waiting on you, and appears nowhere else, ever.
8. Border radius is 2px. Nothing is a pill. Nothing is a circle except graph
   nodes.
9. No shadows at all. Separation is a 1px rule and a change of surface.
10. No emoji, no spinners, no shimmer skeletons, no toasts. State changes are
    written into the row where they happened.
11. No color-only status. Every state carries its word.
12. Two families, both B612. If a string seems to need a third family, the
    string is wrong.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-08-24 | Design system created, codename Dear Theo | Two independent design processes ran blind and converged on warm-light paper over the category-standard dark-neon |
| 2026-08-24 | Warm light default, dark supported | Competitors assume all-day residency; this is a twice-daily ritual |
| 2026-08-24 | Zero sans-serif: Charter plus Commit Mono | One rule does more work than a component library |
| 2026-08-24 | Chrome yellow as the single scarce accent | One color, one meaning, so scanning for yellow is scanning for your to-do list |
| 2026-08-25 | Chrome yellow removed from the Brain graph | On a live vault it painted most of the graph yellow and the scarcity died |
| 2026-08-25 | Ledger actions sit under the row, always visible | A control you cannot see does not exist on a touch screen |
| 2026-09-07 | **Replaced Dear Theo with Kneeboard** | Four directions were built as working pages and compared side by side with the live controls in place (send stamp, tick, chat, rerun). Kneeboard won: the challenge/leader/response row states what a thing is and what was done about it in one line, which is exactly what a briefing row has to say, and the card stack reads the same at 720px and at phone width with no second layout. Dear Theo's ledger margin was the better reading page and the worse working page: it had no natural home for a per-row control |
| 2026-09-07 | Dark-first, light fully supported | Inverts Dear Theo's reasoning rather than contradicting it. The scheduled run opens the page by itself at 7am, often before the room is light, and the reader did not choose the moment |
| 2026-09-07 | B612 and B612 Mono, replacing Charter and Commit Mono | Commissioned by Airbus for cockpit displays and tested at arm's length under poor light, which is the real reading condition. The metaphor is functional, not decorative. Costs the warmth Charter carried |
| 2026-09-07 | Four status colors, replacing one scarce accent | A briefing row has four real states (waiting on you, waiting on them, done, late) and the one-accent rule forced three of them into ink. Amber inherits the scarcity rule verbatim: it means waiting on you and appears nowhere else |
| 2026-09-07 | Amber darkened to `#9C5F0A`, dark red lightened to `#EA6A57` | The first palette was written with plausible-looking contrast figures rather than measured ones. Amber on the light card was 3.8:1 and dark red 4.4:1, both below AA for the state words they carry. A test now recomputes the table |
| 2026-09-07 | Max width 720px, down from 1180px | A checklist is read in one column. A wider measure makes the dotted leader long enough to lose your place across |
| 2026-09-08 | The card may reach 1000px; prose stays at 720 | 720 is a reading measure and it still governs every paragraph. An item line is a row, not prose: subject on the left, filing metadata on the right, and a row does not get harder to read when it is wider. At 720 every one of 19 items wrapped to two lines while a third of the window sat empty |
| 2026-09-08 | An item is set in two ranks, not one | Subject, person, date, business and function arrived as one 124-character run at a single weight, so the subject had to be hunted for. Same characters in the same order, the filing half in mono muted. Nothing hidden, nothing reordered |
| 2026-09-08 | The chat floats | It is the only thing on the page that is not part of the briefing, so it no longer takes a place in the reading order. Collapsed it is a tab in the corner; opened it is a panel over the page and the page keeps its scroll position |
| 2026-09-08 | The read leads, the counts caption | Both were set at one weight and read as a single four-sentence block. The read is prose at 16.5px; the counts sentence the code writes is a mono caption under a rule |
| 2026-09-08 | The lead paragraph opts out of the 62ch measure and sets at 19px | The global `p{max-width:62ch}` was what held the read 265px short of the card edge, not the wrap width. The measure is right for body prose and wrong for the one paragraph that IS the card: it fills the card now, and the larger size buys back the tracking the longer line costs |
| 2026-09-08 | The focus card takes no status colour | It is the only thing on the page nobody is asking the reader for. Amber means waiting on you and this is not owed; cyan means waiting on them and nobody is holding it. Borrowing either would make a recommendation pretend to be an obligation, so it is marked by weight and a rule in the ink colour |
| 2026-09-08 | Lateness renders in `--red`, and a test now pins the amber element set | A critique measured `--red` at zero uses: the phrase the page was RANKED on set at 11px muted grey, identical to the word Legal beside it. Amber had meanwhile drifted onto the rerun button (the first tab stop), the chat's YOU label and two hovers, so three marks that meant nothing diluted the one that means everything. The rule now has the enforcement the contrast table always had |
| 2026-09-08 | The chat's shadow becomes a 2px rule | It was the only `box-shadow` in the document, against an absolute ban. The system separates surfaces with a rule and a ground |
| 2026-09-08 | The chat reads the vault, and a deck can be asked for from it | Every limit is a CLI flag rather than a prompt instruction: `--restricted` plus a `Read,Grep,Glob` whitelist, the vault as working directory, no `--add-dir`. Probed live: reading outside the vault denied, Bash denied, three tools reported, sending impossible because nothing in the set can execute. A deck request stages a plan and stops; the Build press is the same gate the dashboard uses |
| 2026-09-09 | The Note: one event-triggered path beside the batches, capped at two a day | A fifteen-minute tick watches only the front page and the hotcache deals, and a Note holds a draft, a question or a re-ranked afternoon, never a bare report. Two a day because Fitz et al. (2019, N=237) found three daily batches beat hourly delivery, which was no better than control; the briefings take the other two slots. Event timing rather than more batches because Ho and Intille (2005) measured receptivity rising from 2.83 to 3.34 at activity transitions and the gain held with the detector degraded to 82 percent. The labor line was left off the front page because Buell and Norton's fifth experiment reversed the labor illusion on a bad outcome |
| 2026-09-07 | All shadows removed | Dear Theo allowed exactly one 1px hairline offset. On a card stack it read as a mistake rather than a hierarchy, so separation is now rule and surface only |
| 2026-09-07 | `--faint` retired; three grey tiers become two | A critique pass measured it at 2.6:1 to 3.0:1, failing AA in three of its four placements, and it carried real text: the ASK/OPEN affordance label and the chat's statement of what it cannot do. Raising it to AA landed it 1.12:1 from `--muted`, which is not a distinguishable colour. The palette had one tier too many, so the tier went rather than a new shade that passes the arithmetic and does nothing for the eye |
| 2026-09-07 | Item text is the checkbox's `<label>` | The box is drawn at 16px but the label carries a 24px hit target. It was a 12px box with the text in a sibling `<span>`: the smallest target on a page whose primary action it is, and nineteen of them announced to a screen reader as an unnamed "checkbox, unchecked" |
