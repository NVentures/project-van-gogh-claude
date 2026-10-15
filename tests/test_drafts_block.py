"""The Drafts block: built by code, outlined on the page, linked in the file.

Three surfaces read the same markdown. The block is rendered once, by code,
because everything in it is a fact the draft ledger already holds; the only
thing that differs per surface is whether the target is a link (the file) or
a button (the Workbench).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import briefing_html  # noqa: E402
import draft_email  # noqa: E402
import workbench_serve  # noqa: E402

WAITING = {"counterparty": "ana.beltre@lumen.example", "subject": "Lumen redline v4",
           "web_link": "https://mail.google.com/a", "label": "primary",
           "created": "2026-09-07T06:58:00"}
SENT = {"counterparty": "ray.okafor@harborsolar.example", "subject": "Harbor Solar term sheet",
        "web_link": "https://mail.google.com/b", "label": "primary",
        "created": "2026-09-07T06:58:00", "sent_at": "07:12 PT / 10:12 AM ET"}


# ── the block itself ─────────────────────────────────────────────────────────

def test_nothing_drafted_renders_nothing():
    """A heading over an empty list is worse than no heading."""
    assert draft_email.render_drafts_md([]) == ""
    assert draft_email.render_drafts_md(None) == ""


def test_the_file_links_the_subject_and_the_terminal_does_not():
    md = draft_email.render_drafts_md([WAITING], mode="md")
    term = draft_email.render_drafts_md([WAITING], mode="terminal")
    assert "[Lumen redline v4](https://mail.google.com/a)" in md
    assert "https://mail.google.com/a" not in term, "a URL you cannot click is noise"
    assert "Lumen redline v4" in term


def test_the_lead_counts_what_is_still_waiting():
    assert "One reply is written" in draft_email.render_drafts_md([WAITING])
    assert "1 of 2 replies are still waiting" in draft_email.render_drafts_md(
        [WAITING, SENT])
    both_sent = dict(WAITING, sent_at="08:00 PT / 11:00 AM ET")
    assert "have been sent" in draft_email.render_drafts_md([both_sent, SENT])


def test_unsent_replies_come_first():
    md = draft_email.render_drafts_md([SENT, WAITING])
    assert md.index("Lumen redline v4") < md.index("Harbor Solar term sheet")


def test_a_draft_with_no_link_still_gets_a_line():
    """No credential for that account is not a reason to hide the reply."""
    md = draft_email.render_drafts_md([{k: v for k, v in WAITING.items()
                                        if k != "web_link"}], mode="md")
    assert "Lumen redline v4" in md
    assert "](" not in md, "no link, and no empty link either"


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        draft_email.render_drafts_md([WAITING], mode="email")


# ── the card on the page ─────────────────────────────────────────────────────
# The amber card is TRAVEL. It is the one block whose deadline the reader
# cannot move: a flight leaves whether or not the briefing was read. Drafts
# renders after the front page, unoutlined.

import travel  # noqa: E402

DEPARTURE = {"kind": "departure", "trip": "Flight to Dallas",
             "text": "Flight from SNA, Terminal A. Leave home by 4:06 AM PDT.",
             "leave_by": "4:06 AM PDT", "drive_minutes": 34}
UNBOOKED = {"kind": "flights", "trip": "Flight to Newark",
            "text": "No flight booked yet for SNA to EWR on Sep 13.",
            "link": "https://www.google.com/travel/flights?q=x"}


def _page(md_block):
    md = ("# Morning Coffee\n\nThe read.\n\n## SUGGESTED FOCUS TASK\n\n"
          "Do the thing.\n\n" + md_block +
          "\n## THE REST\n\n- an [other link](https://example.com/x) here\n")
    return briefing_html.render_page(md, {"title": "Morning Coffee",
                                          "briefing": "morning-coffee"})


def test_the_travel_card_is_outlined_in_amber():
    html = _page(travel.render_travel_md([DEPARTURE], mode="md"))
    assert '<section class="card travel">' in html
    assert ".card.travel{border:1.5px solid var(--amber)}" in html


def test_the_travel_card_sits_under_the_focus_task():
    html = _page(travel.render_travel_md([DEPARTURE], mode="md"))
    assert html.index('class="card focus"') < html.index('class="card travel"')
    assert html.index('class="card travel"') < html.index("THE REST")


def test_the_focus_card_is_still_the_focus_card():
    """Two heading matchers over one title: neither may claim the other's."""
    html = _page(travel.render_travel_md([DEPARTURE], mode="md"))
    assert html.count('class="card focus"') == 1
    assert html.count('class="card travel"') == 1


def test_the_drafts_card_takes_no_outline():
    """Amber is scarce: the drafts block renders as an ordinary card."""
    html = _page(draft_email.render_drafts_md([WAITING], mode="md"))
    assert '<section class="card travel">' not in html
    assert "Drafts" in html


# ── the block's own shape ────────────────────────────────────────────────────

def test_no_travel_renders_nothing():
    assert travel.render_travel_md([]) == ""
    assert travel.render_travel_md(None) == ""


def test_the_departure_notice_comes_first():
    """It is the notice with a clock on it."""
    md = travel.render_travel_md([UNBOOKED, DEPARTURE], mode="md")
    assert md.index("Leave home by") < md.index("No flight booked")


def test_the_file_links_the_trip_and_the_terminal_does_not():
    md = travel.render_travel_md([UNBOOKED], mode="md")
    term = travel.render_travel_md([UNBOOKED], mode="terminal")
    assert "](https://www.google.com/travel/flights?q=x)" in md
    assert "[No flight booked yet" in md, "the sentence carries the link"
    assert "](" not in term
    assert "https://www.google.com/travel/flights?q=x" in term, "printed, not linked"


def test_a_notice_with_no_link_is_a_plain_line():
    md = travel.render_travel_md([DEPARTURE], mode="md")
    assert md.strip().endswith("Leave home by 4:06 AM PDT.")
    assert "](" not in md


def test_an_unknown_travel_mode_is_refused():
    with pytest.raises(ValueError):
        travel.render_travel_md([DEPARTURE], mode="email")


# ── the button, Workbench only ───────────────────────────────────────────────

def test_an_unbooked_trip_gets_a_find_flights_button():
    html = workbench_serve._draft_buttons(
        _page(travel.render_travel_md([DEPARTURE, UNBOOKED], mode="md")))
    # The travel button is `open hard`: amber is opted into, because a flight
    # leaves whether or not the briefing was read. The drafts button is a bare
    # `open` and stays ink.
    assert html.count('class="open hard"') == 1, "one bookable trip, one button"
    assert ">Find flights</a>" in html
    assert 'href="https://www.google.com/travel/flights?q=x"' in html


def test_the_button_lands_on_the_line_its_link_came_from():
    """Per line, not per card.

    Pairing every link in the card against the lines in order handed the
    FIRST line (a departure notice, which has no link) the second line's
    booking URL, and left the line the link belonged to with a label and
    nothing behind it. Both lines were wrong and neither looked it.
    """
    html = workbench_serve._draft_buttons(
        _page(travel.render_travel_md([DEPARTURE, UNBOOKED], mode="md")))
    lines = re.findall(r"<li>((?:(?!</li>).)*?)</li>", html, re.S)
    departure = [li for li in lines if "Leave home by" in li][0]
    unbooked = [li for li in lines if "No flight booked" in li][0]
    assert 'class="open' not in departure, "nothing to book, nothing to press"
    assert 'class="open hard"' in unbooked


def test_the_button_sits_at_the_end_of_its_own_line():
    html = workbench_serve._draft_buttons(
        _page(travel.render_travel_md([UNBOOKED], mode="md")))
    li = [x for x in re.findall(r"<li>((?:(?!</li>).)*?)</li>", html, re.S)
          if "No flight booked" in x][0]
    assert li.index("Sep 13") < li.index('class="open hard"')


def test_a_waiting_draft_still_gets_an_open_draft_button():
    """The drafts card is not outlined, but its links are still controls."""
    html = workbench_serve._draft_buttons(
        _page(draft_email.render_drafts_md([WAITING, SENT], mode="md")))
    # Bare `open`, no `hard`: a reply can sit another day.
    assert html.count('class="open"') == 1, "one waiting reply, one button"
    assert "open hard" not in html, "a draft link took the travel treatment"
    assert ">Open draft</a>" in html


def test_links_outside_the_amber_card_are_untouched():
    page = _page(travel.render_travel_md([UNBOOKED], mode="md"))
    rest = re.search(r'<section class="card ">.*?</section>', page, re.S).group(0)
    assert rest in workbench_serve._draft_buttons(page), "the rest is byte-identical"


def test_a_page_with_no_amber_card_is_returned_unchanged():
    page = _page("")
    assert workbench_serve._draft_buttons(page) == page
