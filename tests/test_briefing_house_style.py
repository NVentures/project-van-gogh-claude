"""The authoring contract each briefing gives the model.

A briefing's words are written at run time by the Claude CLI from a JSON blob,
so the only place their quality can be specified is the SKILL.md. That makes
these files code, and this suite treats them as code.

What prompted it: Week Retro shipped `stalled: Realogix (88d since contact)`,
`loi -> closing`, `(beta tags)` and a single bullet chaining ten deals with
semicolons, all of it against DESIGN.md's "no vocabulary from the data model"
rule. None of it was a rendering bug. The spec simply never said not to, and an
unspecified section gets written in the vocabulary of the JSON it came from.

These tests cannot check what the model writes on any given Friday. They check
that the instruction it is given actually says the thing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
BRIEFINGS = ("morning-coffee", "afternoon-tea", "week", "week-retro")

EM, EN = chr(0x2014), chr(0x2013)


def skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


# Headings that address the model rather than the reader: workflow steps, the
# spec's own scaffolding. These never reach a briefing, so a dash in one is a
# documentation matter, not a product one.
_SPEC_SCAFFOLD = re.compile(
    r"^(step\b|tier \d|workflow|json keys|python runtime|error handling|"
    r"after rendering|resolved paths|detect tier|retro format|house style|"
    r"the front page|the rest|the web page|when to ask|known behavior|"
    r"what the script|what you still need)", re.I)


def headings(md: str) -> list:
    """Only the headings a briefing reproduces into its own output."""
    return [h for h in re.findall(r"^#{2,3} (.+)$", md, re.M)
            if not _SPEC_SCAFFOLD.match(h.strip())]


@pytest.mark.parametrize("name", BRIEFINGS)
def test_no_dash_reaches_a_heading_a_briefing_will_copy(name):
    """A heading in the spec is a heading in the product.

    The retro spec said "No em-dashes anywhere in the output" and then titled
    its own section 7 with one, which the model is told to reproduce verbatim.
    A rule a file breaks in its own headings is a rule with a hole in it.
    """
    for h in headings(skill_text(name)):
        assert EM not in h and EN not in h, (
            f"{name}: heading would ship a dash: {h!r}")


def test_the_retro_bans_the_words_that_shipped():
    """Every token here was on the delivered page on 2026-09-04."""
    md = skill_text("week-retro").lower()
    for word in ("stalled", "loi", "beta tags"):
        assert word in md, (
            f"the retro spec no longer names {word!r} as banned vocabulary")
    assert "never print a word from the data model" in md


def test_the_retro_requires_one_thing_per_bullet():
    """Ten deals chained with semicolons is a paragraph wearing a bullet.

    On a phone that single bullet ran twelve lines and buried every deal in it.
    """
    md = skill_text("week-retro").lower()
    assert "one thing per bullet" in md
    assert "semicolon" in md


def test_the_retro_requires_acronyms_to_be_expanded():
    md = skill_text("week-retro").lower()
    assert "expand an acronym" in md


def test_the_retro_teaches_the_phrasings_the_page_can_colour():
    """The writer has to know which words the renderer marks red.

    `_LATE_RE` recognises a fixed set of phrasings. A model inventing a new way
    to say the same thing silently costs the reader the only colour on the page
    that means "this is slipping", and nothing anywhere would report it.
    """
    md = skill_text("week-retro")
    for phrasing in ("days late", "overdue", "stale", "since contact",
                     "due today"):
        assert phrasing in md, f"the spec stopped naming {phrasing!r}"


def test_every_lateness_phrasing_the_spec_teaches_actually_renders_red():
    """The contract must agree with the code, in both directions.

    This is the join the two halves never had: the spec can teach a phrasing the
    renderer does not recognise, and both sides would keep passing their own
    tests while the page quietly lost its colour.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
    import workbench_data

    # The examples the spec offers the writer, verbatim from its own text.
    for example in ("3 days late", "2d overdue", "overdue since 9/1",
                    "28 days stale", "88d since contact", "due today"):
        assert 'class="late"' in workbench_data._mark_late(example), (
            f"the spec teaches {example!r} but the renderer will not mark it")
