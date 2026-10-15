"""The prep email's layout: what it must draw, and what it must not break.

The brief below is the shape the meeting-prep skill writes, taken from a real
render with the people and companies replaced.
"""

from __future__ import annotations

import re
from datetime import datetime

import prep_html
import prep_send

DAY = """## Today's calls: Thu Oct 1, 2026
2 calls, both with outside attendees. Harbor at 2:30 PM PT / 5:30 PM ET matters most.

## 1:00 PM PT / 4:00 PM ET  Dana Reyes // Northwind
Thu Oct 1

### Who's on the call
- Dana Reyes: First meeting. A communications coach, per the scheduling mail of 2026-09-22.
- Tom Okafor: Northwind partner. He set up the call.

### Last touchpoints
- 2026-09-30, Northwind bi-weekly: terms at $350K per MW.

### Open commitments
- Confirm the time with Tom Okafor.
- From 2026-09-03 with Tom Okafor: call the landowner at the second site.

### Week priorities
- [ ] [NORTHWIND] **Hyperscaler RFP**: decide whether to submit by October 5

### Win condition
Leave knowing whether Dana Reyes is the right coach <and> what it costs.

### Talking points
1. What the partners want coaching for.
2. Format, time and price.

## 2:30 PM PT / 5:30 PM ET  Re: Harbor
### Who's on the call
- Ines Vidal: runs Harbor Power.

### Win condition
A straight answer on the operator introduction.

### Talking points
1. What changed since April.

Also on the calendar: Team out of office.
"""

SINGLE = """## Northwind Bi-Weekly Sync
Fri Oct 2 | 12:00 PM PT / 3:00 PM ET

### Who's on the call
**Dana Reyes**: Developer at Northwind.
**Tom Okafor**: Center Director.

### Win condition
A signed services agreement.

### Talking points
1. The red line.
"""


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_brief_parses_into_calls_sections_and_a_closing_line():
    brief = prep_html.parse(DAY)
    assert brief["intro"]["title"].startswith("Today's calls")
    assert [(m["time"], m["title"]) for m in brief["meetings"]] == [
        ("1:00 PM PT / 4:00 PM ET", "Dana Reyes // Northwind"),
        ("2:30 PM PT / 5:30 PM ET", "Re: Harbor")]
    assert [s["name"] for s in brief["meetings"][0]["sections"]] == [
        "Who's on the call", "Last touchpoints", "Open commitments",
        "Week priorities", "Win condition", "Talking points"]
    assert brief["closing"] == ["Also on the calendar: Team out of office."]
    assert "Also on the calendar" not in " ".join(
        brief["meetings"][1]["sections"][-1]["lines"])


def test_nothing_the_brief_says_is_lost_in_the_layout():
    html = prep_html.render(DAY, "Thu, Oct 1")
    text = _text(html)
    for phrase in ("Harbor at 2:30 PM PT / 5:30 PM ET matters most",
                   "A communications coach, per the scheduling mail of 2026-09-22",
                   "terms at $350K per MW", "call the landowner at the second site",
                   "Hyperscaler RFP", "decide whether to submit by October 5",
                   "What the partners want coaching for.", "Format, time and price.",
                   "A straight answer on the operator introduction.",
                   "Also on the calendar: Team out of office."):
        assert phrase in text, phrase


def test_the_day_opens_with_every_call_and_each_call_is_a_card():
    html = prep_html.render(DAY, "Thu, Oct 1")
    text = _text(html)
    assert text.startswith("Daily meeting prep Thu, Oct 1")
    assert "Today 2 calls" in text
    assert text.count("Dana Reyes // Northwind") == 2      # the agenda, then its card
    assert "1:00 PM PT / 4:00 PM ET 2 people" in text      # the card's bar
    assert "Thu Oct 1 Who" not in text                     # the repeated date is dropped


def test_a_first_meeting_is_marked_beside_the_name_not_buried_in_the_line():
    html = prep_html.render(DAY)
    assert re.search(r"<b[^>]*>Dana Reyes</b>&nbsp; <span[^>]*>First meeting</span>", html)
    assert "First meeting. A communications" not in _text(html)
    assert not re.search(r"<b[^>]*>Tom Okafor</b>&nbsp; <span", html)


def test_only_a_person_or_a_marked_lead_is_bolded():
    html = prep_html.render(DAY)
    assert "<b style=\"font-weight:700\">From 2026-09-03 with Tom Okafor</b>" not in html
    assert "<b style=\"font-weight:700\">Hyperscaler RFP</b>" in html
    assert "[ ]" not in html and ">Northwind</span>" in html.replace("NORTHWIND", "Northwind")


def test_the_win_condition_is_the_one_block_set_apart():
    html = prep_html.render(DAY)
    assert html.count(f"border-left:3px solid {prep_html.PALETTE['cyan']}") == 2
    assert html.count(f"background:{prep_html.PALETTE['wash']}") == 2


def test_text_from_a_brief_cannot_become_markup():
    html = prep_html.render(DAY)
    assert "&lt;and&gt;" in html and "<and>" not in html


def test_a_single_prep_takes_its_time_from_the_line_under_the_title():
    html = prep_html.render(SINGLE, "Thu, Oct 1")
    text = _text(html)
    assert text.startswith("Meeting prep Thu, Oct 1")
    assert "Fri Oct 2 12:00 PM PT / 3:00 PM ET 2 people" in text
    assert "Today" not in text                              # no agenda for one call
    assert re.search(r"<b[^>]*>Dana Reyes</b><div[^>]*>Developer at Northwind\.</div>", html)


def test_every_colour_comes_from_the_palette_and_amber_is_not_in_it():
    html = prep_html.render(DAY) + prep_html.render(SINGLE)
    used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", html)}
    assert used and used <= {c.upper() for c in prep_html.PALETTE.values()}
    assert "#9C5F0A" not in used and "amber" not in prep_html.PALETTE


def test_the_layout_keeps_to_the_design_rules():
    html = prep_html.render(DAY)
    assert "box-shadow" not in html and "gradient" not in html
    assert "text-align:center" not in html and 'align="center"' not in html
    assert set(re.findall(r"border-radius:(\w+)", html)) == {"2px"}
    assert "<style" not in html and "<img" not in html
    assert chr(0x2014) not in html and chr(0x2013) not in html


def test_the_table_is_fluid_so_a_phone_does_not_clip_it():
    html = prep_html.render(DAY)
    outer = re.search(r'<!\[endif\]--><table[^>]*>', html).group(0)
    assert 'width="100%"' in outer and f"max-width:{prep_html.WIDTH}px" in outer
    assert f'<!--[if mso]><table role="presentation" width="{prep_html.WIDTH}"' in html


def test_a_brief_it_cannot_lay_out_returns_nothing():
    assert prep_html.render("") == ""
    assert prep_html.render("Just a paragraph with no headings.") == ""
    assert prep_html.render("## Today's calls: Thu Oct 1\nNo calls left today.") == ""


# ── The sender's three steps down ────────────────────────────────────────────

def test_the_sender_uses_the_prep_layout_and_names_the_day():
    html = prep_send.email_html(DAY, now=datetime(2026, 10, 1, 6, 15))
    assert "Daily meeting prep" in html and ">Thu, Oct 1<" in html


def test_an_unrecognised_brief_still_goes_out_through_the_general_converter():
    html = prep_send.email_html("No calls left today.\n\n- one line")
    assert html and "Daily meeting prep" not in html and "one line" in html


def test_a_layout_that_raises_costs_the_layout_not_the_email(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad brief")
    monkeypatch.setattr(prep_send.prep_html, "render", boom)
    html = prep_send.email_html(DAY)
    assert html and "Dana Reyes" in html


def test_an_oversized_email_falls_back_to_plain_text(monkeypatch):
    monkeypatch.setattr(prep_send, "HTML_MAX_BYTES", 100)
    assert prep_send.email_html(DAY) is None


def test_the_section_is_called_context_whichever_heading_the_brief_used():
    for heading in ("Deal context", "Context"):
        brief = DAY.replace("### Last touchpoints",
                            f"### {heading}\n- Harbor expansion: stage active.\n\n### Last touchpoints")
        html = prep_html.render(brief)
        text = _text(html)
        assert "Deal context" not in text and "Context Harbor expansion" in text
        assert '<b style="font-weight:700">Harbor expansion</b>' in html


def test_the_skill_heads_that_section_context():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "skills" / "meeting-prep"
            / "SKILL.md").read_text(encoding="utf-8")
    assert "### Context" in text and "### Deal context" not in text
