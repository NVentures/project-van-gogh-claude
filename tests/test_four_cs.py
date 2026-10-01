"""FOUR-CS.md names every capability and every schedule, or this fails.

The page is the product's map: what it knows (context), what it can read
(connectors), what it can do (capabilities) and when each thing fires
(cadence). A map that is right on the day it is written and never again is
worse than none, so the two lists it makes are checked against the tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import job_registry

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "FOUR-CS.md").read_text(encoding="utf-8")


def _skills() -> list:
    return sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))


def _table_names(heading: str) -> set:
    """First-column names of the table under a heading."""
    section = PAGE.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return {m.group(1).strip() for m in re.finditer(r"^\| ([a-z0-9\-]+) \|", section, re.M)}


def test_every_skill_is_on_the_page():
    section = PAGE.split("## Capabilities and their cadence\n", 1)[1].split("\n## ", 1)[0]
    missing = [s for s in _skills()
               if not re.search(rf"(?<![a-z0-9\-]){re.escape(s)}(?![a-z0-9\-])", section)]
    assert _skills(), "found no skills, so this would pass on nothing"
    assert not missing, f"FOUR-CS.md does not name these skills: {missing}"


def test_the_page_names_no_skill_that_does_not_exist():
    listed = _table_names("Capabilities and their cadence")
    assert listed, "found no capability rows"
    assert not listed - set(_skills()), sorted(listed - set(_skills()))


def test_every_scheduled_job_is_on_the_page_and_no_other():
    keys = {job.key for job in job_registry.jobs()}
    assert keys, "the registry is empty, so this would pass on nothing"
    assert _table_names("The schedule itself") == keys


# The only capabilities allowed to have no cadence, with the reason. A new
# skill that does recurring work and is marked on demand fails here, which is
# the point: everything fires on a schedule unless someone says why not.
NO_CADENCE = {
    "voice-bootstrap": "runs once, to build the first voice guide",
    "release-notes": "re-shows something the user asks to see again",
}


def test_only_the_named_capabilities_run_on_demand():
    section = PAGE.split("## Capabilities and their cadence\n", 1)[1].split("\n## ", 1)[0]
    on_demand = {m.group(1) for m in re.finditer(
        r"^\| ([a-z0-9\-]+) \|[^|]*\| On demand[^|]*\|$", section, re.M)}
    assert on_demand == set(NO_CADENCE), sorted(on_demand ^ set(NO_CADENCE))


def test_a_capability_the_page_gives_a_time_really_has_a_job():
    """A row that names a clock time must be a row in the job table."""
    section = PAGE.split("## Capabilities and their cadence\n", 1)[1].split("\n## ", 1)[0]
    keys = {job.key for job in job_registry.jobs()}
    timed = {m.group(1) for m in re.finditer(
        r"^\| ([a-z0-9\-]+) \|[^|]*\| (?:Daily|Weekdays|Monday|Friday|Sunday) [^|]*\|$",
        section, re.M)}
    assert len(timed) >= 12, sorted(timed)
    aliases = {"morning-coffee": "digest-morning-coffee", "afternoon-tea": "digest-afternoon-tea",
               "week": "digest-week", "week-retro": "digest-week-retro",
               "finance-brief": "finance-email", "suggest": "job-watch"}
    missing = [s for s in timed if aliases.get(s, s) not in keys]
    assert not missing, f"FOUR-CS.md gives these a time but no job runs them: {missing}"


def test_the_readme_and_the_dev_guide_point_here():
    for name in ("README.md", "CLAUDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "FOUR-CS.md" in text, f"{name} does not point at FOUR-CS.md"
        for word in ("Context", "Connectors", "Capabilities", "Cadence"):
            assert word in text


def test_the_page_carries_no_dash_as_punctuation():
    assert chr(0x2014) not in PAGE and chr(0x2013) not in PAGE
