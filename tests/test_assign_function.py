"""P11 and P13: which kind of work an item is, inside its business."""

import copy

import pytest
import config_loader as cl
import briefing_fixtures as bf
import week_review as wr


@pytest.fixture(autouse=True)
def _restore_config():
    """These tests swap the business list; put the suite's fixture back after.

    config_loader._config is module state primed once by conftest, so a test
    that edits it leaks into every test that runs later.
    """
    saved = copy.deepcopy(cl._config)
    yield
    cl._config = saved



def _fn(subject, body="", given=""):
    return wr.assign_function({"subject": subject, "body_preview": body,
                               "function": given})


def test_assign_function_keyword_table():
    """With no model, the keyword table files the planted four."""
    assert _fn("Harbor Q3 invoice 4417") == "Accounting"
    assert _fn("Kestrel term sheet for signature") == "Legal"
    assert _fn("RFP response due Friday") == "Sales"
    assert _fn("Lunch on Thursday") == "Other"


def test_classifier_answer_outside_the_list_lands_in_other():
    assert _fn("Something nobody named", given="Vibes") == "Other"
    assert _fn("Something nobody named", given="Legal") == "Legal"


def test_nothing_is_dropped_for_being_unclassified():
    """Every item gets exactly one function from the configured list."""
    bf.set_businesses(cl)
    sections = bf.random_sections(seed=4, n_items=150)
    allowed = set(wr.briefing_functions())
    for entries in sections.values():
        for e in entries:
            assert wr.assign_function(e) in allowed


def test_every_folded_item_carries_one_function():
    bf.set_businesses(cl)
    sections = bf.random_sections(seed=6, n_items=90)
    for entries in sections.values():
        for e in entries:
            e["bucket"] = wr.assign_bucket(e)
    buckets = wr.group_by_bucket(sections)
    built = wr.build_front_page(buckets, "morning-coffee")
    allowed = set(wr.briefing_functions())
    for bucket in buckets:
        for entry in bucket["items"]:
            assert entry["function"] in allowed
    for row in built["fold_rows"]:
        for fn in row["functions"]:
            assert fn["name"] in allowed


def test_functions_override():
    """A renamed list renders its own names only."""
    bf.set_businesses(cl)
    cl._config["briefing"] = {"functions": ["Deals", "Paper", "Other"]}
    try:
        assert wr.briefing_functions() == ["Deals", "Paper", "Other"]
        # A default name that is no longer configured cannot come back.
        assert _fn("Kestrel term sheet") == "Other"
        assert _fn("Kestrel term sheet", given="Paper") == "Paper"
        sections = bf.random_sections(seed=8, n_items=60)
        for entries in sections.values():
            for e in entries:
                e["bucket"] = wr.assign_bucket(e)
        built = wr.build_front_page(wr.group_by_bucket(sections), "morning-coffee")
        names = {f["name"] for r in built["fold_rows"] for f in r["functions"]}
        assert names <= {"Deals", "Paper", "Other"}, names
    finally:
        cl._config.pop("briefing", None)


def test_other_is_always_available():
    bf.set_businesses(cl)
    cl._config["briefing"] = {"functions": ["Deals"]}
    try:
        assert wr.briefing_functions() == ["Deals", "Other"]
        assert _fn("nothing in particular") == "Other"
    finally:
        cl._config.pop("briefing", None)


def test_empty_override_falls_back_to_the_defaults():
    bf.set_businesses(cl)
    cl._config["briefing"] = {"functions": []}
    try:
        assert wr.briefing_functions() == cl.BRIEFING_FUNCTIONS_DEFAULT
    finally:
        cl._config.pop("briefing", None)


def test_cap_reads_config_and_clamps():
    cl._config = dict(cl._config)
    for value, expected in ((7, 7), (1, 3), (40, 9), ("nonsense", 7)):
        cl._config["briefing"] = {"front_page_cap": value}
        assert cl.briefing_front_page_cap() == expected
    cl._config.pop("briefing", None)
    assert cl.briefing_front_page_cap() == 7


def test_the_longest_keyword_wins_not_the_first_in_the_table():
    """The same kind of work must file the same way whatever else is in the line.

    First hit wins put "site walk" under Sales whenever the word "pricing"
    appeared earlier in the subject, because Sales sits above Operations in the
    table. A two word phrase is a more specific claim than one common word.
    """
    assert _fn("site walk on Tuesday") == "Operations"
    assert _fn("pricing and the site walk on Tuesday") == "Operations"
    assert _fn("pricing for next quarter") == "Sales"
    # Table order still breaks a genuine tie between equal-length matches.
    assert _fn("nothing in here at all") == "Other"


def test_the_four_the_contract_names_are_unmoved():
    assert _fn("Harbor Q3 invoice 4417") == "Accounting"
    assert _fn("Kestrel term sheet for signature") == "Legal"
    assert _fn("RFP response due Friday") == "Sales"
    assert _fn("Lunch on Thursday") == "Other"


def test_the_same_work_files_the_same_way_whatever_it_is_named():
    """A proper noun in a keyword list overrides the work every time.

    "summit" sat in the Admin list, so "Summit grid study" filed under Admin
    while "Harbor grid study" filed under Other, and "Summit RFP response"
    filed under Admin while every other RFP response filed under Sales. The
    difference was the company name, which is never what a function is.
    """
    for name in ("Summit", "Harbor", "Cedarworks", "Kestrel"):
        assert _fn(f"{name} grid study 60") == "Other", name
        assert _fn(f"{name} RFP response 3") == "Sales", name
        assert _fn(f"{name} Q3 invoice") == "Accounting", name
    # The real admin words still work.
    assert _fn("Conference registration for March") == "Admin"
    assert _fn("Flight booking to Austin") == "Admin"


def test_user_keywords_route_a_custom_function():
    """briefing.function_keywords makes a custom functions[] name routable."""
    cl._config.setdefault("briefing", {})
    cl._config["briefing"]["functions"] = ["Sales", "Ranching", "Other"]
    cl._config["briefing"]["function_keywords"] = {"Ranching": ["cattle brand"]}
    assert _fn("Cattle brand paperwork for the north herd") == "Ranching"
    # Still only names in functions[]: a keyword for an unconfigured name
    # cannot invent a row.
    cl._config["briefing"]["function_keywords"] = {"Vibes": ["cattle brand"]}
    assert _fn("Cattle brand paperwork for the north herd") == "Other"


def test_user_keyword_wins_a_tie_with_the_builtin_table():
    """Same match length, the user's filing beats ours."""
    cl._config.setdefault("briefing", {})["function_keywords"] = {
        "Sales": ["invoice"]}  # ties the builtin Accounting "invoice"
    assert _fn("Harbor Q3 invoice 4417") == "Sales"
    # A strictly longer builtin phrase still outranks a shorter user word.
    cl._config["briefing"]["function_keywords"] = {"Sales": ["due"]}
    assert _fn("Statement of account past due") == "Accounting"


def test_malformed_function_keywords_config_never_crashes():
    """Junk shapes in briefing.function_keywords degrade to no-op, not a crash."""
    cl._config.setdefault("briefing", {})
    for junk in ("nope", ["a"], {"Sales": "invoice"}, {"Sales": [1, "  "]},
                 {"": ["x"]}, None):
        cl._config["briefing"]["function_keywords"] = junk
        cleaned = cl.briefing_function_keywords()
        assert isinstance(cleaned, dict)
        assert _fn("Harbor Q3 invoice 4417") == "Accounting"
    # Numbers and padding are coerced, empties dropped.
    cl._config["briefing"]["function_keywords"] = {"Sales": ["  INVOICE  ", 7]}
    assert cl.briefing_function_keywords() == {"Sales": ["invoice", "7"]}
