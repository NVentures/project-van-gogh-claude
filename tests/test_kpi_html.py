"""The scorecard email: is it safe to send, and does it say what it means?

Two families of test. One checks the things email clients break (a `<style>`
block Gmail strips, a flex layout Outlook ignores, a payload Gmail clips). The
other checks the writing: that a number never appears without its direction,
that a bad week reads as a bad week, and that amber, which in this product
means "waiting on you", never appears on a page about what already happened.
"""
from __future__ import annotations

import re

import pytest

import kpi_html
import kpi_report
import send_email

# Written as ESCAPES, not literals. A blanket dash sweep over the repo would
# rewrite a literal here and silently redefine what this test hunts for, which
# has happened before: a checker's own dash test was turned into a hyphen count
# and then reported 1728 findings in a clean file.
EM_DASH = "\u2014"
EN_DASH = "\u2013"

AMBER_LIGHT = "#9C5F0A"
AMBER_DARK = "#E8A33D"


def kpi(slug, **kw):
    row = kpi_report._result(slug)
    row.update(kw)
    return row


def good_report():
    return {
        "window_start": "2026-08-26", "window_end": "2026-09-09",
        "generated_at": "2026-09-09T16:00:00", "window_days": 14,
        "malformed_events": 0, "measured_count": 6,
        "opening": ["Threads waiting on you fell from 21 to 12.",
                    "Nothing moved the wrong way."],
        "kpis": [
            kpi("waiting", measured=True, value=12, prior=21, value_text="12",
                prior_text="21", n_text="12 open now", direction="better",
                direction_text="down from", target_text="down 40 percent",
                met=True, detail="7 you closed, 2 arrived"),
            kpi("late", measured=True, value=3, prior=5, value_text="3",
                prior_text="5", n_text="3 late now", direction="better",
                direction_text="down from", target_text="under three",
                met=True, detail="4 you cleared, 2 newly late"),
            kpi("time_to_close", measured=True, value=31.0, prior=58.0,
                value_text="31h", prior_text="58h", n_text="7 of 10 closed",
                direction="better", direction_text="down from",
                target_text="half of the previous fortnight", met=True,
                measured_as="counted from first appearing on your front page "
                            "to being ticked off",
                detail="3 of the 10 are still open"),
            kpi("meeting_actions", measured=True, value=3.4, prior=2.8,
                value_text="3.4", prior_text="2.8", n_text="across 9 meetings",
                direction="better", direction_text="up from",
                target_text="three or more", met=True,
                detail="92 percent name an owner"),
            kpi("prep_coverage", measured=True, value=83, prior=60,
                value_text="83%", prior_text="60%", n_text="5 of 6 calls",
                direction="better", direction_text="up from",
                target_text="nine in ten", met=False,
                detail="1 went in without one"),
            kpi("closed", measured=True, value=14, prior=8, value_text="14",
                prior_text="8", n_text="14 this week", direction="better",
                direction_text="up from", target_text="more than last week",
                met=True, detail="6 more than last week"),
        ],
        "spotlight": {
            "all_used": False, "skill": "relationship-radar",
            "title": "Relationship Radar",
            "what": "Finds the people you have not spoken to in a month.",
            "when": "Worth a run on a quiet Friday.",
            "command": "/van-gogh:relationship-radar",
        },
    }


def unmeasured_report():
    r = good_report()
    r["opening"] = ["Van Gogh has not gathered enough yet to score this week."]
    r["measured_count"] = 0
    r["kpis"] = [kpi(s, measured=False, reason="not enough history yet")
                 for s in kpi_report.KPI_ORDER]
    return r


# ── Email client safety ──────────────────────────────────────────────────────

def test_no_dashes_anywhere():
    html = kpi_html.render(good_report())
    assert EM_DASH not in html and EN_DASH not in html


def test_the_dash_scanner_can_actually_find_one():
    """MUTATION CONTROL: prove the scan works on a planted instance."""
    planted = kpi_html.render(good_report()) + EM_DASH
    assert EM_DASH in planted


def test_no_script_and_no_external_resource():
    html = kpi_html.render(good_report())
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert "http://" not in html
    # The only https allowed would be an image or font; there are none.
    assert "https://" not in html


def test_no_style_block_and_every_style_is_inline():
    """Gmail strips a <style> block, so nothing may depend on one."""
    html = kpi_html.render(good_report())
    assert "<style" not in html.lower()
    assert 'style="' in html


def test_layout_is_tables_not_flexbox():
    """Outlook renders through Word: flex, grid and position do not exist."""
    html = kpi_html.render(good_report()).lower()
    assert "<table" in html
    for banned in ("display:flex", "display: flex", "display:grid",
                   "display: grid", "position:absolute", "position:fixed"):
        assert banned not in html, f"{banned} will not survive Outlook"


def test_the_real_mime_payload_stays_under_the_clip_bound():
    """Measured through the actual builder, not the HTML string.

    Gmail clips on the assembled message, and base64 inflates it by a third,
    so measuring the raw HTML would understate the real payload.
    """
    html = kpi_html.render(good_report())
    text = kpi_html.render_text(good_report())
    raw = send_email.build_gmail_raw("me@example.com", "you@example.com",
                                     "[Van Gogh] Your week, Sep 9", text,
                                     html=html)
    assert len(raw.encode("utf-8")) < 80_000, "the message would be clipped"


def test_a_text_alternative_exists_and_carries_every_number():
    text = kpi_html.render_text(good_report())
    for expected in ("12", "3", "31h", "3.4", "83%", "14"):
        assert expected in text
    assert "relationship-radar" in text


# ── The palette ──────────────────────────────────────────────────────────────

def _colours(html: str) -> set:
    return {m.upper() for m in re.findall(r"#[0-9A-Fa-f]{6}", html)}


def test_every_colour_comes_from_the_declared_palette():
    """Set membership, not a two-string check.

    A near-amber introduced by hand would slip past a test that only looks for
    the two known amber values.
    """
    allowed = {v.upper() for v in kpi_html.PALETTE.values()}
    used = _colours(kpi_html.render(good_report()))
    assert used, "no colours found, the scan is not working"
    assert used <= allowed, f"colours outside the palette: {used - allowed}"


def test_amber_is_not_in_the_palette_at_all():
    """Amber means work waiting on the reader. Nothing here is waiting."""
    values = {v.upper() for v in kpi_html.PALETTE.values()}
    assert AMBER_LIGHT.upper() not in values
    assert AMBER_DARK.upper() not in values


def test_amber_never_reaches_the_rendered_email():
    for report in (good_report(), unmeasured_report()):
        html = kpi_html.render(report).upper()
        assert AMBER_LIGHT.upper() not in html
        assert AMBER_DARK.upper() not in html


def test_the_colour_scan_can_actually_find_a_planted_amber():
    """MUTATION CONTROL for the two tests above."""
    planted = kpi_html.render(good_report()) + f'<i style="color:{AMBER_LIGHT}">x</i>'
    allowed = {v.upper() for v in kpi_html.PALETTE.values()}
    assert not _colours(planted) <= allowed


# ── The writing ──────────────────────────────────────────────────────────────

def test_every_card_states_its_direction_in_words_not_only_colour():
    """A number with a colour and no word is unreadable in greyscale.

    The movement is stated factually ("down from 21") and the judgement lives
    in the verdict line ("Met."), so the two never say the same thing twice
    and a move toward a floor target cannot read as a decline.
    """
    html = kpi_html.render(good_report())
    assert html.count("down from") >= 2
    assert html.count("up from") >= 2
    assert "Met." in html or "Not yet." in html


def test_a_regression_says_the_wrong_way_and_is_drawn_in_red():
    report = good_report()
    report["kpis"][1] = kpi(
        "late", measured=True, value=9, prior=3, value_text="9",
        prior_text="3", n_text="9 late now", direction="worse",
        direction_text="up from", target_text="under three", met=False,
        detail="6 newly late")
    html = kpi_html.render(report)
    assert "the wrong way" in html
    assert kpi_html.PALETTE["red"] in html


def test_a_rising_bad_number_is_never_drawn_in_green():
    """The trap the polarity table exists to prevent."""
    report = good_report()
    report["kpis"] = [kpi("late", measured=True, value=9, prior=3,
                          value_text="9", prior_text="3", n_text="9 late now",
                          direction="worse", direction_text="up from",
                          target_text="under three", met=False, detail="")]
    html = kpi_html.render(report)
    body = html[html.index("Late items"):]
    assert kpi_html.PALETTE["green"] not in body


def test_every_measured_card_carries_all_five_fields():
    """Label, value, comparison in words, target, and a verdict.

    The count is shown only when it adds something the headline does not: "2"
    above "2 open now" was one fact rendered twice, which a cold reader counted
    on every card.
    """
    html = kpi_html.render(good_report())
    for row in good_report()["kpis"]:
        assert row["name"] in html
        assert row["value_text"] in html
        assert row["target_text"] in html
        assert row["direction_text"] in html
    assert "Met." in html


def test_a_card_never_prints_its_own_number_twice():
    """The headline, the count line and the bar label were three renderings
    of one value."""
    report = good_report()
    report["kpis"] = [kpi("waiting", measured=True, value=2, prior=5,
                          value_text="2", prior_text="5",
                          n_text="2 open now", direction="better",
                          direction_text="down from", target_text="down 40",
                          met=True, detail="")]
    html = kpi_html.render(report)
    assert "2 open now" not in html, "the headline number is repeated"


def test_a_count_that_adds_information_is_still_shown():
    report = good_report()
    report["kpis"] = [kpi("time_to_close", measured=True, value=31.0,
                          prior=58.0, value_text="31h", prior_text="58h",
                          n_text="7 of 10 closed", direction="better",
                          direction_text="down from", target_text="half",
                          met=True, detail="")]
    html = kpi_html.render(report)
    assert "7 of 10 closed" in html


def test_the_bars_are_named_as_halves_of_the_fortnight():
    """"this week" inside a fortnight header pointed at nothing definite."""
    html = kpi_html.render(good_report())
    assert "latest week" in html and "week before" in html
    assert ">this week<" not in html


def test_an_unmeasured_card_says_so_with_its_reason_and_no_target():
    """The reason IS the message. A "Not enough yet." headline above a reason
    that also begins "not enough" said one thing twice."""
    html = kpi_html.render(unmeasured_report())
    assert html.count("Not enough history yet") == len(kpi_report.KPI_ORDER)
    assert "Target:" not in html
    assert "Not enough yet.<br>" not in html


def test_a_proxy_card_explains_what_was_counted():
    html = kpi_html.render(good_report())
    assert "Counted as:" in html
    assert "first appearing on your front page" in html


def test_the_bar_cannot_render_wider_than_its_track():
    """A value far above its comparator must not draw past the track."""
    report = good_report()
    report["kpis"] = [kpi("closed", measured=True, value=900, prior=1,
                          value_text="900", prior_text="1",
                          n_text="900 this week", direction="better",
                          direction_text="up from", target_text="more",
                          met=True, detail="")]
    html = kpi_html.render(report)
    widths = [int(w) for w in re.findall(r'width:(\d+)px;height:6px', html)]
    assert widths, "no bars were drawn"
    assert max(widths) <= kpi_html.BAR_WIDTH


def test_a_card_with_no_prior_draws_no_bars_at_all():
    """One full-width bar labelled "latest" compares a number with itself and
    reads as a broken chart."""
    report = good_report()
    report["kpis"] = [kpi("closed", measured=True, value=5, prior=None,
                          value_text="5", prior_text="", n_text="5 this week",
                          direction="none", direction_text="",
                          target_text="more", met=None, detail="")]
    html = kpi_html.render(report)
    assert "week before" not in html
    assert "latest week" not in html
    assert "height:6px" not in html, "a lone bar was drawn"


def test_the_footer_says_how_to_stop_the_email():
    html = kpi_html.render(good_report())
    assert "/van-gogh:update-settings" in html
    assert "stop these emails" in html.lower()


def test_the_footer_promises_only_what_is_true():
    html = kpi_html.render(good_report())
    assert "leave your machine" in html


def test_the_spotlight_renders_its_command():
    html = kpi_html.render(good_report())
    assert "One thing you have not tried yet" in html
    assert "/van-gogh:relationship-radar" in html


def test_when_everything_has_been_used_the_section_says_so():
    report = good_report()
    report["spotlight"] = {"all_used": True,
                           "note": "You have used every part of Van Gogh."}
    html = kpi_html.render(report)
    assert "You have used every part of Van Gogh." in html
    assert "One thing you have not tried yet" not in html


def test_skipped_malformed_lines_are_reported_not_hidden():
    report = good_report()
    report["malformed_events"] = 4
    html = kpi_html.render(report)
    assert "4 unreadable lines" in html


def test_html_is_escaped_so_a_stray_bracket_cannot_break_the_layout():
    report = good_report()
    report["opening"] = ["<script>alert(1)</script> and a & sign"]
    html = kpi_html.render(report)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ── Cold-read findings, locked in ────────────────────────────────────────────

def test_a_card_says_whether_the_target_was_met():
    """Movement and verdict are two different questions.

    A cold reader found a card reading "down from 3.5, the wrong way" above
    "Target: three or more" while the value WAS three. Showing only the
    movement let a passing number look like a failure, which is the fastest
    way to lose a reader's trust in the other five cards.
    """
    report = good_report()
    report["kpis"] = [kpi("meeting_actions", measured=True, value=3, prior=3.5,
                          value_text="3", prior_text="3.5",
                          n_text="across 9 meetings", direction="worse",
                          direction_text="down from",
                          target_text="three or more", met=True, detail="")]
    html = kpi_html.render(report)
    assert "Met." in html
    assert "Target: three or more" in html


def test_a_missed_target_says_not_yet_rather_than_nothing():
    report = good_report()
    report["kpis"] = [kpi("prep_coverage", measured=True, value=50, prior=40,
                          value_text="50%", prior_text="40%",
                          n_text="3 of 6 outside calls", direction="better",
                          direction_text="up from",
                          target_text="nine in ten", met=False, detail="")]
    html = kpi_html.render(report)
    assert "Not yet." in html


def test_an_unknown_verdict_claims_nothing():
    report = good_report()
    report["kpis"] = [kpi("closed", measured=True, value=5, prior=None,
                          value_text="5", prior_text="", n_text="5 this week",
                          direction="none", direction_text="",
                          target_text="more than last week", met=None,
                          detail="")]
    html = kpi_html.render(report)
    assert "Met." not in html and "Not yet." not in html


def test_a_fourteen_day_window_is_never_called_a_week():
    """The header spans two weeks; the cards compare the two halves."""
    html = kpi_html.render(good_report())
    assert "Your fortnight" in html
    assert "Your week," not in html


def test_a_complete_movement_clause_never_gets_a_number_stapled_on():
    """"holding above three 3.5" shipped once. It is not a sentence.

    A phrase ending in a preposition takes the prior value; one that is already
    a clause must not.
    """
    report = good_report()
    report["kpis"] = [kpi("meeting_actions", measured=True, value=3, prior=3.5,
                          value_text="3", prior_text="3.5", direction="flat",
                          direction_text="holding above three",
                          n_text="across 9 meetings",
                          target_text="three or more", met=False, detail="")]
    html = kpi_html.render(report)
    assert "holding above three 3.5" not in html
    assert "holding above three" in html


def test_a_preposition_phrase_still_takes_its_number():
    report = good_report()
    report["kpis"] = [kpi("waiting", measured=True, value=2, prior=5,
                          value_text="2", prior_text="5", direction="better",
                          direction_text="down from", n_text="",
                          target_text="down 40 percent", met=True, detail="")]
    html = kpi_html.render(report)
    assert "down from 5" in html


def test_bars_are_not_drawn_for_numbers_too_small_to_have_a_shape():
    """A chart of 0 against 1 restates the movement line as decoration."""
    report = good_report()
    report["kpis"] = [kpi("late", measured=True, value=0, prior=1,
                          value_text="0", prior_text="1", n_text="",
                          direction="better", direction_text="down from",
                          target_text="under three", met=True, detail="")]
    html = kpi_html.render(report)
    assert "height:6px" not in html
    assert "down from 1" in html, "the movement must still be stated"


def test_bars_are_drawn_once_the_numbers_are_worth_charting():
    report = good_report()
    report["kpis"] = [kpi("waiting", measured=True, value=12, prior=21,
                          value_text="12", prior_text="21", n_text="",
                          direction="better", direction_text="down from",
                          target_text="down 40 percent", met=True, detail="")]
    html = kpi_html.render(report)
    assert "height:6px" in html


def test_the_bars_do_not_restate_the_numbers_above_them():
    """"2 / down from 5" then "latest week 2 / week before 5" is one pair of
    numbers twice in six lines."""
    report = good_report()
    report["kpis"] = [kpi("waiting", measured=True, value=12, prior=21,
                          value_text="12", prior_text="21", n_text="",
                          direction="better", direction_text="down from",
                          target_text="down 40 percent", met=True, detail="")]
    html = kpi_html.render(report)
    assert "height:6px" in html, "the bars are missing entirely"
    # The value appears in the movement line, once, not again in the chart.
    assert html.count(">21<") <= 1
