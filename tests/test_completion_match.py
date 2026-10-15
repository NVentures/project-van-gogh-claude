"""Regression test for completion_scan's tiered match gate.

Guards against the weak-match leak: a task being flagged "looks done" on a lone
shared first name (e.g. a "Harbor Land Projects" email checking off an unrelated
"Aurora exit messaging" task because both name "Mark"). The gate must require a
distinctive org handle, a full name, name+topic, or content overlap.

Lives in tests/ (not app/) so `.venv/bin/python -m pytest` actually collects it: testpaths is
["tests"], so a standalone app/test_*.py would never run in CI and a regression
would slip through green.
"""
import pytest

import completion_scan as cs

# Em-dash held as a codepoint so this test file adds no literal em-dash to the
# repo's no-dash sweep; it is incidental task text, never load-bearing for match.
EMDASH = chr(0x2014)


def _qualifies(task, sent):
    sources = cs._signal_sources(sent, [])
    return bool(cs.detect_item_candidates([{"checked": False, **task}], sources))


# (name, should_qualify, task, sent)
CASES = [
    ("lone first name, unrelated subject (Mark / Harbor Land)", False,
     {"project": "Dana Whitfield",
      "task": "Handle Aurora-investor messaging on exit scenario; schedule 1:1s w/ Mark + Denise (2-3 wks)"},
     [{"subject": "Re: Harbor Land Projects Review", "to": "Mark Delgado",
       "to_email": "mdelgado@qxcorp.com"}]),

    ("org handle match (kestrel)", True,
     {"project": "Kestrel / Maria", "task": "**Kestrel / Maria:** Send update on Willow Bend"},
     [{"subject": "Willow Bend update", "to": "Sam Rivera", "to_email": "sam@northwind.com"}]),

    ("full-name match, unrelated subject (Spencer Hale)", True,
     {"project": "Spencer Hale", "task": "**Spencer Hale:** turn pledge agreement comments"},
     [{"subject": "Re: catching up", "to": "Spencer Hale", "to_email": "shale@ks.com"}]),

    # 'Wu' is <3 chars so it never tokenizes -> the only anchor is the lone first
    # name 'christopher' against a generic subject. Correctly rejected now.
    ("known counterparty, generic subject, 2-char surname (Christopher Vo)", False,
     {"project": "Christopher Vo",
      "task": "**Christopher Vo:** schedule Meridian 2.0 proposal call end of this week"},
     [{"subject": "Re: Follow up", "to": "Christopher Vo", "to_email": "cvo@lattice.example"}]),

    ("name + topic overlap (Christopher Vo / proposal)", True,
     {"project": "Christopher Vo",
      "task": "**Christopher Vo:** schedule Meridian 2.0 proposal call end of this week"},
     [{"subject": "Re: Meridian 2.0 proposal", "to": "Christopher Vo", "to_email": "cvo@lattice.example"}]),

    ("lone first name, unrelated subject (Ryan / lunch)", False,
     {"project": "Ryan",
      "task": f"**Ryan:** retention package discussion {EMDASH} multiple deal success bonuses"},
     [{"subject": "Re: lunch tomorrow", "to": "Ryan", "to_email": "ryan@qxcorp.com"}]),

    ("name + topic overlap (Ryan / retention)", True,
     {"project": "Ryan",
      "task": f"**Ryan:** retention package discussion {EMDASH} multiple deal success bonuses"},
     [{"subject": "Re: Retention package", "to": "Ryan", "to_email": "ryan@qxcorp.com"}]),
]


@pytest.mark.parametrize("name,expect,task,sent", CASES, ids=[c[0] for c in CASES])
def test_match_gate(name, expect, task, sent):
    assert _qualifies(task, sent) is expect
