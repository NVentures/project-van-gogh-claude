"""Gates that read the delivered page, not the code's account of itself.

Every defect pinned here shipped while the suite was green, and each one was
invisible for the same reason: the existing gate asserted what the producer
declares rather than what the artifact renders.

`test_amber_means_only_waiting_on_you` greps the stylesheet for selectors
carrying `var(--amber)` and compares them to an allowlist. That allowlist held a
bare `.open`, reasoned about as "the button on a travel line". But `.open` is
one class and two cards use it, so the drafts button rendered amber, with an
amber hover fill, on every Morning Coffee. The test could not see it: no
selector had leaked, the selector's meaning had.

`test_lateness_carries_the_late_colour` calls `_task_label` directly with a
string in exactly the shape its regex expects. It proves the fix works where it
already worked, and cannot notice that three of the four briefings never reach
that code path at all.

So these tests render pages and read the HTML. They are deliberately about the
set: a fix that lands on one briefing's shape and not the others is the failure
mode this file exists to catch.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import briefing_html  # noqa: E402
import workbench_serve  # noqa: E402

# Amber's meaning, per DESIGN.md: waiting on you, and nothing else. Three things
# carry it and the list is closed: the phase label, the send stamp, and the
# travel line's button plus its card outline.
AMBER_OK = {"phase", "stamp", "hard", "travel"}


def render(md: str, briefing: str = "morning-coffee") -> str:
    return briefing_html.render_page(
        md, {"title": "T", "briefing_date": "2026-09-04", "briefing": briefing})


def amber_elements(html: str) -> list:
    """Every element on the rendered page the stylesheet paints amber.

    Matched per element against whole compound selectors, not loose tokens. A
    token-level check cannot tell `.open` from `.open.hard`: it would read the
    neutral drafts button as amber because the amber rule mentions `open`, which
    is the same class of imprecision that let the real leak through.
    """
    css = briefing_html._TOKENS + briefing_html._PAGE_CSS + workbench_serve._LIVE_CSS
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # Each amber rule becomes the set of classes an element must carry ALL of.
    required = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "var(--amber)" not in body:
            continue
        for part in sel.split(","):
            # The rightmost compound is the element the rule paints.
            last = part.strip().split()[-1] if part.strip() else ""
            classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", last))
            if classes:
                required.append(classes)
    painted = []
    for attr in re.findall(r'class="([^"]+)"', html):
        have = set(attr.split())
        if any(need <= have for need in required):
            painted.append(attr)
    return painted


# ── the send stamp ───────────────────────────────────────────────────────────

LEDGER_MD = """# Afternoon Tea

## DONE TODAY

- [x] Lumen redline v4 · Ana Beltre · NORTHWIND DC · Legal

## STILL OPEN

- [ ] Harbor Solar countersignature · Ray Okafor · HARBOR · Legal
"""


def _ledger(monkeypatch, entries):
    import draft_email
    monkeypatch.setattr(draft_email, "load_ledger", lambda: entries)


def test_no_send_stamp_on_a_row_the_reader_already_closed(monkeypatch):
    """The one irreversible control in the product, offered on shipped work.

    `_stamps` matched `<li class="task"` exactly, so a closed row, which is
    `class="task done"`, never matched. The miss fell through to a bare
    str.replace with no <li> awareness, which spliced a live SEND into that
    closed row, inside a label the `done` rule strikes through. The page then
    said the reply had gone and offered to send it again, in the same line.
    """
    _ledger(monkeypatch, {"k1": {"subject": "Lumen redline v4", "sent_at": ""}})
    html = workbench_serve._stamps(render(LEDGER_MD, "afternoon-tea"),
                                   "afternoon-tea")
    done_row = re.search(r'<li class="task done".*?</li>', html, re.S)
    assert done_row, "fixture no longer renders a closed row"
    assert "stamp" not in done_row.group(0), (
        "a live SEND was spliced into a row the reader had already closed")


def test_the_stamp_still_reaches_an_open_row(monkeypatch):
    """The narrowing must not cost the control its actual job."""
    _ledger(monkeypatch, {
        "k1": {"subject": "Harbor Solar countersignature", "sent_at": ""}})
    html = workbench_serve._stamps(render(LEDGER_MD, "afternoon-tea"),
                                   "afternoon-tea")
    open_row = re.search(r'<li class="task"(?:(?!</li>).)*?</li>', html, re.S)
    assert open_row and "stamp" in open_row.group(0), (
        "the stamp no longer reaches the rows it exists for")


def test_a_closed_row_still_says_when_the_reply_went(monkeypatch):
    """The ban is on the live button, not on the record of what happened.

    Caught on the live page, not by the suite: the first fix skipped closed rows
    outright, which also swallowed the SENT marker. A row then read "reply sent,
    cap held at 2x fees" with no time and no green state word, so the page had
    quietly stopped saying when. The marker is a statement and belongs exactly
    where the thing happened; only the control is dangerous there.
    """
    _ledger(monkeypatch, {
        "k1": {"subject": "Lumen redline v4", "sent_at": "07:12 PT / 10:12 AM ET"}})
    html = workbench_serve._stamps(render(LEDGER_MD, "afternoon-tea"),
                                   "afternoon-tea")
    done_row = re.search(r'<li class="task done".*?</li>', html, re.S).group(0)
    assert "state sent" in done_row, "the closed row stopped saying when it sent"
    assert "07:12 PT / 10:12 AM ET" in done_row
    assert "stamp" not in done_row, "a statement must not become a control"


def test_a_subject_that_matches_no_open_row_gets_no_control(monkeypatch):
    """No blind fallback. A control that cannot be placed is not drawn.

    The old fallback fired str.replace on the first occurrence anywhere in the
    document, including inside a heading or a closed row.
    """
    _ledger(monkeypatch, {"k1": {"subject": "Lumen redline v4", "sent_at": ""}})
    html = workbench_serve._stamps(render(LEDGER_MD, "afternoon-tea"),
                                   "afternoon-tea")
    assert html.count('class="stamp"') == 0


# ── lateness, across the set ─────────────────────────────────────────────────

# Taken from the live vault files, not invented: The Week and Week Retro really
# write these, and none of them matched the original two-phrase regex.
LATE_PHRASINGS = [
    "Submit the models, both now overdue since 8/28 and 9/4",
    "Submit August expenses (due 2026-09-05, 2d overdue)",
    "Deck cleanup: due 2026-09-01, now 3 days past",
    "Reach out to Foster: due 2026-09-01, 3 days late",
    "Hanwha: last contact is 2026-08-07, 28 days stale",
    "Realogix (88d since contact)",
]

# Lines that must stay in ink. Painting a reassurance red is worse than missing
# a late item: it tells the reader something is wrong when nothing is.
NOT_LATE = [
    "Waiting on a confirm reply: 187 (completion 167, overdue 3, met 3)",
    "No threads over 14 days cold.",
    "Idemitsu: send revised terms, due 2026-09-08",
]


def test_every_briefing_can_render_lateness_in_red():
    """The cross-page form, and the reason the old gate could not see this.

    `test_lateness_carries_the_late_colour` calls `_task_label` with a synthetic
    string shaped exactly like the regex it feeds. It proves the front-page path
    works and is structurally incapable of noticing that The Week and Week Retro
    never reach that path: they write plain bullets, so every overdue figure on
    the two pages about decay rendered at the weight of the tag beside it.
    """
    # Three shapes, because the renderer has three paths and the fix had to
    # reach all of them. A plain bullet (Week Retro), a checkbox task with no
    # filing tail (The Week, whose items are one sentence), and a checkbox task
    # with a middot tail (the front page). The first version of this test used
    # only the plain bullet and passed while every "3d overdue" on The Week was
    # still rendering in ink.
    shapes = [
        "- {p}",
        "- [ ] {p}",
        "- [ ] {p} · Ana Beltre · NORTHWIND DC · Legal",
    ]
    for briefing in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
        for phrase in LATE_PHRASINGS:
            for shape in shapes:
                line = shape.format(p=phrase)
                html = render(f"# T\n\n## Open\n\n{line}\n", briefing)
                assert 'class="late"' in html, (
                    f"{briefing}: no red for {line!r}")


def test_a_reassurance_never_renders_as_late():
    """Digits must lead. `over 14 days cold` is the good news."""
    for phrase in NOT_LATE:
        html = render(f"# T\n\n## Open\n\n- {phrase}\n", "week-retro")
        assert 'class="late"' not in html, f"marked a non-late line: {phrase!r}"


def test_the_late_mark_carries_the_colour_wherever_it_lands():
    """The rule was scoped to the front-page task shape, which was the bug."""
    assert ".late{color:var(--red)" in briefing_html._PAGE_CSS


def test_the_two_rank_grid_only_applies_where_two_ranks_exist():
    """A one-sentence item must not be split into columns.

    The wide-screen rule made `li.task label` a two-column grid for `.what` and
    `.filed`. The Week emits neither: its items are `[TAG] **Name**: sentence`.
    An unscoped grid then treated that label's own children as columns, threw
    the bolded name to the right margin, and left the sentence starting with a
    colon on the next row. Desktop only, which is why it survived a mobile pass.
    """
    assert "li.task label:has(.filed){display:grid" in briefing_html._PAGE_CSS, (
        "the two-rank grid is unscoped again")

    # And the shape that triggered it still renders as one run.
    html = render("# T\n\n## Top\n\n- [ ] [DEAL] **Duke models**: submit them, "
                  "both now overdue since 8/28\n", "week")
    label = re.search(r"<label[^>]*>(.*?)</label>", html, re.S).group(1)
    assert 'class="filed"' not in label, "fixture stopped being a one-rank item"


# ── amber, across the set ────────────────────────────────────────────────────

def test_the_drafts_button_is_not_amber():
    """Amber on a draft link says a reply is a departure. It is not.

    Both buttons were one `.open` class, so the drafts card rendered in the send
    stamp's own treatment while briefing_html.py's comment said that card
    "carries no colour, because amber is reserved for the deadline that cannot
    move".
    """
    md = ("# Morning Coffee\n\n## Drafts\n\n"
          "- Reply to Ana Beltre "
          "[Open](https://mail.google.com/mail/u/0/#drafts/x)\n")
    html = workbench_serve._draft_buttons(render(md))
    assert 'class="open"' in html, "the drafts button should still be a button"
    assert "open hard" not in html, "the drafts button took amber"


def test_the_travel_button_keeps_amber():
    """The one deadline the reader cannot renegotiate still carries it."""
    md = ("# Morning Coffee\n\n## Travel\n\n"
          "- No flight booked yet "
          "[Search](https://www.google.com/travel/flights?q=x)\n")
    html = workbench_serve._draft_buttons(render(md))
    assert "open hard" in html, "the travel button lost amber"


def test_amber_reaches_nothing_unowned_on_any_briefing():
    """The cross-page form of the scarcity rule.

    The CSS test asserts over the stylesheet, so it passes while a *card* adopts
    an amber class it was never meant to have. This one reads four rendered
    pages and asks which amber-painted classes actually reach a reader.
    """
    md = ("# T\n\n## Travel\n\n"
          "- No flight booked yet "
          "[Search](https://www.google.com/travel/flights?q=x)\n\n"
          "## DO TODAY\n\n"
          "- [ ] Something · Ana · NORTHWIND · Legal\n\n## Drafts\n\n"
          "- Reply to Ray "
          "[Open](https://mail.google.com/mail/u/0/#drafts/x)\n")
    for briefing in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
        html = workbench_serve._draft_buttons(render(md, briefing))

        # Both buttons must actually be on the page, or this test passes by
        # rendering nothing and proves nothing about the rule.
        assert "Find flights" in html and "Open draft" in html, (
            f"{briefing}: fixture stopped rendering both buttons")

        for attr in amber_elements(html):
            owner = set(attr.split()) & AMBER_OK
            assert owner, f"{briefing}: amber reached an element classed {attr!r}"

        # `hard` is an allowed token, so a drafts button wearing it would pass
        # the loop above. The rule is about which card carries amber, not which
        # token exists, so name the offender directly.
        drafts = re.search(r'<section class="card drafts".*?</section>', html, re.S)
        assert drafts, f"{briefing}: fixture stopped rendering a drafts card"
        assert not amber_elements(drafts.group(0)), (
            f"{briefing}: the drafts card carries amber")
