"""Anti-drift coverage guard.

Every chief-of-staff briefing must consult every source its role requires. A
coverage audit found Granola and the kill scan had silently fallen out of some
briefings; this test keeps that from re-opening.

Each data-pipeline briefing declares a COLLECTORS_USED set (the source categories
it composes via collectors.py). COVERAGE below is the REQUIRED set per briefing.

morning-coffee is a renderer of week_review's JSON (it shells out to week_review
and reads hotcache directly), so its source coverage flows from week_review; it
is intentionally not a first-class collector composer and is not in the manifest.
"""
import importlib

import collectors

# Required source coverage per briefing (canonical collector names).
COVERAGE = {
    "week_review": {                       # the hub: every source category
        collectors.SENT_MAIL, collectors.INBOUND_MAIL, collectors.MEETINGS,
        collectors.CALENDAR, collectors.HOTCACHE_READ, collectors.OBSIDIAN_TASKS,
        collectors.KILL_SCAN, collectors.DEAL_MATCH,
    },
    "afternoon_tea": {                     # daily retro: no obsidian roadmap tasks
        collectors.SENT_MAIL, collectors.INBOUND_MAIL, collectors.MEETINGS,
        collectors.CALENDAR, collectors.HOTCACHE_READ, collectors.KILL_SCAN,
        collectors.DEAL_MATCH,
    },
    "week_retro": {                        # weekly retro: mail/deal half via the
                                           # week_review shell-out + obsidian tasks
        collectors.SENT_MAIL, collectors.INBOUND_MAIL, collectors.MEETINGS,
        collectors.CALENDAR, collectors.HOTCACHE_READ, collectors.OBSIDIAN_TASKS,
        collectors.KILL_SCAN, collectors.DEAL_MATCH,
    },
}


def covers(used, required):
    """Coverage predicate: a briefing covers its role iff it declares every
    required collector. Pulled out so the negative test can exercise it."""
    return required.issubset(used)


def test_required_names_are_canonical():
    # Every required name is a real canonical collector name (catches a typo that
    # would silently weaken the contract).
    for briefing, required in COVERAGE.items():
        assert required.issubset(collectors.ALL), briefing


def test_each_briefing_covers_required():
    for briefing, required in COVERAGE.items():
        mod = importlib.import_module(briefing)
        used = getattr(mod, "COLLECTORS_USED", None)
        assert isinstance(used, set), f"{briefing} missing COLLECTORS_USED"
        assert getattr(mod, "collectors", None) is collectors, \
            f"{briefing} must compose the collectors layer"
        missing = required - used
        assert covers(used, required), f"{briefing} missing {sorted(missing)}"


def test_negative_unwiring_breaks_coverage():
    # Unwiring one collector breaks coverage; restoring it fixes coverage. Proves
    # the predicate has teeth (T3-6).
    full = COVERAGE["week_review"]
    assert covers(full, full) is True
    one = sorted(full)[0]
    degraded = full - {one}
    assert covers(degraded, full) is False
    assert covers(degraded | {one}, full) is True
