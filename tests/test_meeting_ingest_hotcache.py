"""meeting_ingest.py hotcache write tests.

Two writes reach the vault outside the source page: the deal comment
(update_hotcache_last_contact) and the promoted action items
(append_under_heading). Both got a live meeting wrong on 2026-09-18 and both
reported success, so these tests plant the shapes that failed rather than
synthetic ones.
"""
import pytest

import meeting_ingest as gi


@pytest.fixture(autouse=True)
def _hotcache_dir():
    """The fixture vault is a temp dir; wiki/ is not created for us."""
    gi.HOTCACHE.parent.mkdir(parents=True, exist_ok=True)


# The live hotcache shape that caused the misattribution: several threads
# share the generic word "project", and one contains "structure" only as a
# substring of "Infrastructure".
HOTCACHE_TIED = """---
title: hotcache
---

## Active Threads

### Project Van Gogh
<!-- deal: stage=active last_contact=2026-09-09 -->
- the chief-of-staff stack

### Project Southgate
<!-- deal: stage=loi last_contact=2026-08-01 -->
- a solar site

### Diamond Infrastructure Solutions
<!-- deal: stage=active last_contact=2026-08-02 -->
- a counterparty

### Patriot Ridge (Solstice / Brock)
<!-- deal: stage=active last_contact=2026-09-10 -->
- the term sheet in negotiation
"""


def _thread(result):
    return None if result is None else result[0]


def test_generic_token_alone_never_matches_a_deal():
    """"Development project term sheet..." must not win a deal on "project".

    Three threads tie at one generic token and a fourth matches "structure"
    inside "Infrastructure". File order made Project Van Gogh the winner.
    """
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Development project term sheet and deal structure with partner",
        [],
        "2026-09-17",
        summary_text="we discussed exclusivity terms",
    )
    assert result is None, f"expected no match, got {_thread(result)}"


def test_no_stage_advance_on_a_refused_match():
    """A refused match must leave stage= untouched, not just last_contact."""
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    gi.update_hotcache_last_contact(
        "Development project term sheet and deal structure with partner",
        [],
        "2026-09-17",
        summary_text="we agreed exclusivity",
    )
    text = gi.HOTCACHE.read_text(encoding="utf-8")
    assert "stage=active last_contact=2026-09-09" in text
    assert "stage=exclusivity" not in text


def test_lone_generic_token_refused_even_with_no_tie():
    """Isolates the evidence bar from the tie rule.

    "Southgate" is removed from the field so exactly ONE thread hits, and it
    hits only on the generic word "project". With no tie to abstain on, the
    evidence bar is the only thing that can refuse it.
    """
    single = """---
title: hotcache
---

## Active Threads

### Project Van Gogh
<!-- deal: stage=active last_contact=2026-09-09 -->
- the chief-of-staff stack

### Patriot Ridge (Solstice / Brock)
<!-- deal: stage=active last_contact=2026-09-10 -->
- the term sheet in negotiation
"""
    gi.HOTCACHE.write_text(single, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Development project term sheet with partner", [], "2026-09-17"
    )
    assert result is None, f"expected no match, got {_thread(result)}"


def test_tie_between_two_qualifying_threads_abstains():
    """Isolates the tie rule from the evidence bar.

    Both threads clear the evidence bar on the same distinctive token
    ("ridge"), so only abstention can stop file order from deciding.
    """
    ambiguous = """---
title: hotcache
---

## Active Threads

### Patriot Ridge (Solstice)
<!-- deal: stage=active last_contact=2026-09-10 -->
- one deal

### Cedar Ridge (Other Counterparty)
<!-- deal: stage=active last_contact=2026-09-11 -->
- a different deal
"""
    gi.HOTCACHE.write_text(ambiguous, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Ridge term sheet review", [], "2026-09-17"
    )
    assert result is None, f"expected abstention, got {_thread(result)}"
    text = gi.HOTCACHE.read_text(encoding="utf-8")
    assert "last_contact=2026-09-10" in text
    assert "last_contact=2026-09-11" in text


def test_substring_of_a_longer_word_is_not_a_match():
    """A DISTINCTIVE token must still not match inside a longer word.

    "diamon" is long and non-generic, so it clears the evidence bar and the
    only thing standing between it and "Diamond Infrastructure Solutions" is
    word-boundary matching. A generic token like "structure" would be refused
    earlier and would test nothing here.
    """
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "diamon rockies briefing", [], "2026-09-17"
    )
    assert _thread(result) != "Diamond Infrastructure Solutions"


def test_distinctive_token_still_matches():
    """The guard must narrow, never break a legitimate match."""
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Patriot Ridge term sheet", [], "2026-09-17"
    )
    assert _thread(result) == "Patriot Ridge (Solstice / Brock)"
    assert "last_contact=2026-09-17" in gi.HOTCACHE.read_text(encoding="utf-8")


def test_attendee_surname_still_matches():
    """An attendee's full name in a deal heading is distinctive evidence."""
    gi.HOTCACHE.write_text(
        HOTCACHE_TIED.replace("(Solstice / Brock)", "(Solstice / Brock Wallace)"),
        encoding="utf-8",
    )
    result = gi.update_hotcache_last_contact(
        "term sheet review", ["Brock Wallace"], "2026-09-17"
    )
    assert _thread(result) == "Patriot Ridge (Solstice / Brock Wallace)"


def test_attendee_first_name_alone_never_matches_a_deal():
    """Live 2026-09-28: "Maria Hernandez" on an all-hands revived the dead
    "Project Southgate (Halcyon / Maria Okafor)" thread on the first name."""
    gi.HOTCACHE.write_text(
        HOTCACHE_TIED.replace("### Project Southgate", "### Project Southgate (Halcyon / Maria Okafor)"),
        encoding="utf-8",
    )
    before = gi.HOTCACHE.read_text(encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Northfield All-hands Call", ["Maria Hernandez"], "2026-09-28"
    )
    assert result is None
    assert gi.HOTCACHE.read_text(encoding="utf-8") == before


def test_first_name_with_title_corroboration_still_matches():
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Patriot Ridge next steps", ["Brock Felt"], "2026-09-17"
    )
    assert _thread(result) == "Patriot Ridge (Solstice / Brock)"


def test_one_word_alias_does_not_link_a_different_person():
    """Live 2026-09-28: "Ben Hale" was linked as [[Ben Carter|Ben]] Snow."""
    canonical = {"Ben Carter": "entity"}
    aliases = {"Ben": "Ben Carter"}
    text = "Ben will ask Ben Hale, then Ben Carter sends it."
    out = gi.wikilink_entities_in_text(text, canonical, aliases)
    assert out == "[[Ben Carter|Ben]] will ask Ben Hale, then [[Ben Carter]] sends it."
    assert gi.detect_entities_in_text("ask Ben Hale", canonical, aliases) == set()


def test_match_reports_the_tokens_it_matched_on():
    """The caller cannot warn about a weak match it cannot see."""
    gi.HOTCACHE.write_text(HOTCACHE_TIED, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Patriot Ridge term sheet", [], "2026-09-17"
    )
    assert len(result) == 3, "expected (thread, stage_change, evidence)"
    assert "patriot" in result[2]


# A section whose heading is followed by several deeper subsections. The live
# file put ~1360 lines and 300+ "###" blocks between the heading and the next
# "## " heading.
HOTCACHE_NESTED = """---
title: hotcache
---

## Nobel's Action Items

### Open

- [ ] an existing open item

### Resolved (this period)

- [x] something finished in May

## Last Session

- a trailing section
"""


def test_append_lands_under_its_own_heading_not_a_later_subsection():
    """The new line must not land under "### Resolved (this period)"."""
    out = gi.append_under_heading(
        HOTCACHE_NESTED, "Nobel's Action Items", "- [ ] a brand new item"
    )
    lines = out.splitlines()
    new_at = lines.index("- [ ] a brand new item")
    resolved_at = lines.index("### Resolved (this period)")
    assert new_at < resolved_at, "item landed inside a later subsection"


def test_append_stays_inside_its_top_level_section():
    out = gi.append_under_heading(
        HOTCACHE_NESTED, "Nobel's Action Items", "- [ ] a brand new item"
    )
    lines = out.splitlines()
    new_at = lines.index("- [ ] a brand new item")
    last_session_at = lines.index("## Last Session")
    assert new_at < last_session_at


def test_append_is_idempotent():
    once = gi.append_under_heading(
        HOTCACHE_NESTED, "Nobel's Action Items", "- [ ] a brand new item"
    )
    twice = gi.append_under_heading(
        once, "Nobel's Action Items", "- [ ] a brand new item"
    )
    assert once == twice


def test_append_when_heading_is_the_last_section():
    text = "## Nobel's Action Items\n\n- [ ] existing\n"
    out = gi.append_under_heading(text, "Nobel's Action Items", "- [ ] new")
    assert "- [ ] new" in out


# ── 2026-09-24: two generic words matched a deal and a meeting killed it ────
# "Generate term sheet and data center strategy with Mike" matched the thread
# "GA Data Center Gas Line" on `data` + `center` (two generic words passing the
# "two of anything" bar) and the keyword scan then advanced it closing -> dead.
# The meeting never mentioned the gas line.

HOTCACHE_GAS = """---
title: hotcache
---

## Active Threads

### GA Data Center Gas Line
<!-- deal: stage=closing last_contact=2026-09-10 -->
- Columbus, GA
"""
# The same thread with a distinctive token in its header, so a match is real.
HOTCACHE_GAS_NAMED = HOTCACHE_GAS.replace(
    "### GA Data Center Gas Line", "### GA Data Center Gas Line (Trefz)")


def test_two_generic_tokens_are_not_corroboration():
    gi.HOTCACHE.write_text(HOTCACHE_GAS, encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Generate term sheet and data center strategy with Mike", [],
        "2026-09-23", summary_text="the old plan is dead",
    )
    assert result is None, f"expected no match, got {_thread(result)}"
    assert "stage=closing last_contact=2026-09-10" in gi.HOTCACHE.read_text(encoding="utf-8")


# "Gridline platform development and ISO expansion strategy with Priyank"
# stamped "County Strategy Expansion (Marlow)" on `expansion` + `strategy`, and a
# second title that day won it on `strategy` alone. Neither call was about it.

HOTCACHE_TOPIC = """---
title: hotcache
---

## Active Threads

### County Strategy Expansion (Marlow)
<!-- deal: stage=active last_contact=2026-09-10 -->
- more counties under the agreement

### Northwind Proposal Automation
<!-- deal: stage=active last_contact=2026-09-01 -->
- the SOW generator

### Investor Readiness Program
<!-- deal: stage=active last_contact=2026-08-20 -->
- the data room
"""


@pytest.mark.parametrize("title", [
    "Gridline platform development and ISO expansion strategy with Priyank",
    "Van Gogh deployment and scaling strategy for 1,500-person rollout",
    "Renewable energy development and AI automation tools with Tobin",
    "Investor concerns and presentation feedback with Mike",
])
def test_topic_words_never_identify_a_deal(title):
    gi.HOTCACHE.write_text(HOTCACHE_TOPIC, encoding="utf-8")
    result = gi.update_hotcache_last_contact(title, [], "2026-09-30")
    assert result is None, f"expected no match, got {_thread(result)}"
    assert gi.HOTCACHE.read_text(encoding="utf-8") == HOTCACHE_TOPIC


@pytest.mark.parametrize("title,thread", [
    ("Marlow county outreach", "County Strategy Expansion (Marlow)"),
    ("Northwind SOW generator demo", "Northwind Proposal Automation"),
    ("Readiness program kickoff", "Investor Readiness Program"),
])
def test_every_topic_thread_is_still_reachable_by_its_name(title, thread):
    """Making a word generic must not leave a thread nothing to be found by."""
    gi.HOTCACHE.write_text(HOTCACHE_TOPIC, encoding="utf-8")
    result = gi.update_hotcache_last_contact(title, [], "2026-09-30")
    assert _thread(result) == thread


def test_two_specific_short_tokens_still_corroborate():
    """The two-token path survives for real names shorter than the
    distinctive length: `gas` is too short to tokenize, `line` is not."""
    gi.HOTCACHE.write_text(HOTCACHE_GAS.replace(
        "### GA Data Center Gas Line", "### Acme Mill Gas Line"), encoding="utf-8")
    result = gi.update_hotcache_last_contact("Acme mill review", [], "2026-09-23")
    assert _thread(result) == "Acme Mill Gas Line"


def test_a_meeting_never_marks_a_deal_dead_or_passed():
    for word in ("dead", "passed"):
        gi.HOTCACHE.write_text(HOTCACHE_GAS_NAMED, encoding="utf-8")
        result = gi.update_hotcache_last_contact(
            "Trefz gas line", [], "2026-09-23",
            summary_text=f"the deal is {word}",
        )
        assert _thread(result) == "GA Data Center Gas Line (Trefz)"
        assert result[1] is None
        text = gi.HOTCACHE.read_text(encoding="utf-8")
        assert "stage=closing last_contact=2026-09-23" in text


def test_a_meeting_still_advances_a_deal_forward():
    gi.HOTCACHE.write_text(HOTCACHE_GAS_NAMED.replace("stage=closing", "stage=outreach"),
                           encoding="utf-8")
    result = gi.update_hotcache_last_contact(
        "Trefz gas line", [], "2026-09-23", summary_text="we signed the LOI")
    assert result[1] == {"thread": "GA Data Center Gas Line (Trefz)",
                         "from": "outreach", "to": "closing"}
