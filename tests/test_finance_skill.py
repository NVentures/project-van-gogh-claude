"""The finance skill's text, and the one rule the commentary has to keep.

A SKILL.md is prose the model follows, so what is testable about it is that it
still says the things the rest of the feature depends on: that it pastes the
code-rendered block rather than retyping figures, that it knows what to do when
the connector is dead, and that the commentary it adds introduces no number of
its own.

That last rule gets its own checker here, because it is enforced against live
output later and a checker that has never caught a planted instance is not
evidence of anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "finance-brief" / "SKILL.md"
SHARED = ROOT / "skills" / "_shared" / "connectors.md"


@pytest.fixture(scope="module")
def text():
    return SKILL.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict:
    """The frontmatter block, parsed strictly enough to catch the real bug.

    PyYAML is not a dependency of this project, so this is stdlib. It is
    deliberately unforgiving about the one thing that actually breaks: a long
    unquoted `description:` containing a bare colon is invalid YAML, and the
    harness's own loader is lenient enough to list the skill anyway, so the
    break stays silent until something else reads it. A folded block scalar
    (`>-`) is the fix and is what this accepts.
    """
    block = text.split("---")[1]
    out, key, folded = {}, None, []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - "):
            out.setdefault(key, []).append(line[4:].strip())
            continue
        if line.startswith("  ") and key and folded is not None:
            folded.append(line.strip())
            continue
        if ":" not in line:
            raise AssertionError(f"unparseable frontmatter line: {line!r}")
        name, _, value = line.partition(":")
        if key and folded:
            out[key] = " ".join(folded)
            folded = []
        key = name.strip()
        value = value.strip()
        if value in (">-", ">", "|", "|-"):
            out[key] = ""
            folded = []
        elif value:
            # A bare colon inside an unquoted scalar is the invalid case.
            if ": " in value and not (value.startswith(("'", '"'))):
                raise AssertionError(
                    f"{key} holds an unquoted colon, which is invalid YAML: "
                    f"use a folded block scalar (>-)")
            out[key] = value
        else:
            out[key] = []
    if key and folded:
        out[key] = " ".join(folded)
    return out


@pytest.fixture(scope="module")
def frontmatter(text):
    return parse_frontmatter(text)


# ── The frontmatter ──────────────────────────────────────────────────────────

def test_the_frontmatter_is_valid_yaml_with_the_house_keys(frontmatter):
    assert frontmatter["name"] == "finance-brief"
    assert frontmatter["description"].strip()
    assert len(frontmatter["allowed-tools"]) == 3


def test_the_description_names_what_a_person_would_actually_ask(frontmatter):
    """The description is a routing rule, not a summary."""
    description = frontmatter["description"].lower()
    for phrase in ("/van-gogh:finance-brief", "cash position", "owes them",
                   "quickbooks"):
        assert phrase in description, phrase


def test_both_shell_forms_are_shown(text):
    assert '"$HOME/.config/van-gogh/venv/bin/python"' in text
    assert r'& "$HOME\.config\van-gogh\venv\Scripts\python.exe"' in text
    assert "${CLAUDE_PLUGIN_ROOT}/app/finance_brief.py" in text
    assert r"$env:CLAUDE_PLUGIN_ROOT\app\finance_brief.py" in text


def test_the_runtime_guard_is_referenced(text):
    assert "_shared/python-runtime.md" in text


# ── The render contract ──────────────────────────────────────────────────────

def test_the_skill_pastes_the_rendered_block_verbatim(text):
    """Every figure was decided by code. Retyping one is how they drift."""
    assert "finance_md" in text
    assert "verbatim" in text.lower()


def test_the_skill_is_told_not_to_compute(text):
    low = text.lower()
    assert "do not re-order" in low or "do not re-word" in low
    assert "round anything" in low
    assert 'turn a "not measured" into a zero' in low


def test_every_key_the_skill_names_is_actually_emitted():
    """The prose-to-code line: a skill that pastes a key the module never
    emits leaves a hole in the page and no error anywhere."""
    from datetime import date

    import finance_brief
    from test_finance_brief import raw

    report = finance_brief.build(raw(), today=date(2026, 9, 10), history=[])
    text = SKILL.read_text(encoding="utf-8")
    for key in re.findall(r"`(finance_md|company|as_of|basis|currency|cash|ar|"
                          r"ap|pl_mtd|pl_prior|deltas|sections)`", text):
        assert key in report, f"the skill names {key}, which is not emitted"


def test_the_output_path_key_is_named(text):
    assert "meta.output_path" in text


# ── The failure path ─────────────────────────────────────────────────────────

def test_the_skill_knows_both_connector_failures_and_what_to_say(text):
    assert "needs-auth" in text and "absent" in text
    assert "claude.ai" in text and "Connectors" in text


def test_the_skill_is_told_not_to_pass_off_old_figures_as_todays(text):
    """The quietest way this feature could lie."""
    assert "previous run" in text.lower()


def test_the_skill_asks_nothing_when_nobody_is_there(text):
    assert "VAN_GOGH_UNATTENDED" in text


# ── The commentary rule, and its checker ─────────────────────────────────────

def test_the_commentary_rule_is_stated_plainly(text):
    assert "What a controller would say" in text
    assert "bookkeeper-controller" in text
    low = text.lower()
    assert "no number that is not already" in low


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def foreign_numbers(commentary: str, rendered: str) -> list:
    """Numbers in the commentary that are not in the code-rendered block.

    Tokenized, never substring-matched: "12" is inside "2012", and a check that
    accepts it would pass a commentary that invented a figure out of a year.
    """
    known = set(_NUMBER_RE.findall(rendered))
    return [n for n in _NUMBER_RE.findall(commentary) if n not in known]


def test_the_commentary_checker_catches_an_invented_figure():
    """MUTATION CONTROL: proven before a clean pass on live output is trusted."""
    rendered = "Cash $412,908.33 across two accounts, up since 2026-09-03."
    good = "Cash is healthy at $412,908.33 and the trend is the right one."
    bad = "Cash is healthy, and runway looks like 14 months at this burn."

    assert foreign_numbers(good, rendered) == []
    assert foreign_numbers(bad, rendered) == ["14"]


def test_the_checker_is_not_fooled_by_a_substring():
    """"12" appears inside "2012", and a substring test would allow it."""
    rendered = "Founded 2012. Cash $5,000.00."
    assert foreign_numbers("Up 12 percent this month", rendered) == ["12"]


def test_a_percentage_the_page_never_showed_is_caught():
    rendered = "Past due to you $14,650.00, up since 2026-09-03."
    assert foreign_numbers("Receivables rose 65% this week", rendered) == ["65%"]


# ── No dashes, no data-model words ───────────────────────────────────────────

def test_the_skill_carries_no_dash(text):
    em, en = "\u2014", "\u2013"   # escapes: a dash sweep over this file must not redefine what it hunts
    assert em not in text and en not in text


def test_the_dash_scan_can_see_a_planted_one():
    """MUTATION CONTROL: written as escapes so a future dash sweep over this
    file cannot silently redefine what it looks for."""
    em = "\u2014"
    assert em in f"a line with {em} a dash"


# ── The shared guide ─────────────────────────────────────────────────────────

def test_the_connector_guide_tells_an_author_the_whole_procedure():
    text = SHARED.read_text(encoding="utf-8")
    for heading in ("Adding a connector", "What this is not for"):
        assert heading in text
    for point in ("server name", "read-only", "arguments", "needs-auth",
                  "_CONNECTOR_NAMES", "--check"):
        assert point in text, point


def test_the_guide_records_the_two_traps_that_cost_a_day():
    text = SHARED.read_text(encoding="utf-8")
    assert "health check is not an authorization check" in text.lower()
    assert "mcp-config" in text


def test_the_guide_says_mail_stays_on_oauth():
    """The doctrine this feature must not be read as overturning."""
    text = SHARED.read_text(encoding="utf-8")
    assert "data_sources.py" in text
    assert "primary path" in text


def test_the_guide_carries_no_dash():
    em, en = "\u2014", "\u2013"   # escapes: a dash sweep over this file must not redefine what it hunts
    text = SHARED.read_text(encoding="utf-8")
    assert em not in text and en not in text


def test_the_frontmatter_parser_catches_the_bug_it_exists_for():
    """MUTATION CONTROL: a bare colon in an unquoted description is invalid
    YAML, and every loader that matters disagrees about whether to care."""
    bad = ("---\n"
           "name: x\n"
           "description: reads the vault: this skill does things\n"
           "---\n")
    with pytest.raises(AssertionError):
        parse_frontmatter(bad)

    good = ("---\n"
            "name: x\n"
            "description: >-\n"
            "  reads the vault: this skill does things\n"
            "---\n")
    assert parse_frontmatter(good)["name"] == "x"
