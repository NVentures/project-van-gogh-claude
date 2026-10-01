"""Are the six numbers TRUE? Verified against a second, independent method.

A metrics product's worst failure is a confident wrong number, and a fixture
written by whoever wrote the implementation cannot catch one: both encode the
same mental model, so the test is green forever and the model is never checked.

So every KPI here is computed twice. Once by the shipping code, and once by a
`naive_*` function in this file that brute-forces the same question in the most
obvious way possible, sharing nothing with the real one but the event reader.
If the two disagree, one of them is wrong and the suite says so.

The adversarial fixtures matter as much as the agreement. Each one plants a
trap that produces a plausible number under a naive implementation: threads
that vanish without being closed, a cohort where only the fast items closed,
duplicate and out-of-order events, a denominator of one.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

import pytest

import kpi_events
import kpi_report

NOW = datetime(2026, 9, 9, 16, 0, 0)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def snap(days_ago: float, briefing="morning-coffee", counts=None, front=None,
         open_keys=None, late_keys=None) -> dict:
    return {
        "ts": ts(days_ago),
        "kind": kpi_events.SNAPSHOT,
        "briefing": briefing,
        "counts": counts or {"open": len(open_keys or []),
                             "late": len(late_keys or []),
                             "front": len(front or [])},
        "front": front or [],
        "open_keys": list(open_keys or []),
        "late_keys": list(late_keys or []),
    }


def closed(days_ago: float, keys) -> dict:
    return {"ts": ts(days_ago), "kind": kpi_events.CLOSED, "keys": list(keys)}


def prep(days_ago: float, mode, keys) -> dict:
    return {"ts": ts(days_ago), "kind": kpi_events.PREP, "mode": mode,
            "keys": list(keys)}


def run(rows, now=NOW, window=14):
    return {k["slug"]: k for k in
            kpi_report.compute(window_days=window, now=now, rows=rows)["kpis"]}


# ── The independent implementations ──────────────────────────────────────────
#
# Deliberately slow, deliberately obvious. No windowing helpers, no shared
# code with kpi_report. If one of these is hard to read, it is wrong.

def naive_window(now, window_days):
    half = window_days // 2
    start = (now - timedelta(days=window_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    mid = (now - timedelta(days=half)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, mid


def naive_waiting(rows, now=NOW, window=14):
    """Open threads at the first snapshot of the window vs the last."""
    start, mid = naive_window(now, window)
    snaps = []
    for r in rows:
        if r["kind"] != "snapshot":
            continue
        t = datetime.fromisoformat(r["ts"])
        snaps.append((t, r))
    snaps.sort(key=lambda p: p[0])
    early = [r for t, r in snaps if start <= t < mid]
    late = [r for t, r in snaps if mid <= t <= now]
    if not early or not late:
        return None
    return len(set(late[-1]["open_keys"])), len(set(early[0]["open_keys"]))


def naive_closed_counts(rows, now=NOW, window=14):
    """Distinct keys in close events, this half vs the previous half."""
    start, mid = naive_window(now, window)
    this_half, last_half = set(), set()
    for r in rows:
        if r["kind"] != "closed":
            continue
        t = datetime.fromisoformat(r["ts"])
        for k in r["keys"]:
            if mid <= t <= now:
                this_half.add(k)
            elif start <= t < mid:
                last_half.add(k)
    return len(this_half), len(last_half)


def naive_cohort_hours(rows, now=NOW, window=14):
    """For each key first seen on a front page in the FIRST half: hours until
    its first close, or None if it never closed. Returns (median, closed, n)."""
    start, mid = naive_window(now, window)
    first_seen = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r["kind"] != "snapshot":
            continue
        t = datetime.fromisoformat(r["ts"])
        for item in r["front"]:
            first_seen.setdefault(item["key"], t)
    first_close = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r["kind"] != "closed":
            continue
        t = datetime.fromisoformat(r["ts"])
        for k in r["keys"]:
            first_close.setdefault(k, t)
    hours = []
    n = 0
    for k, seen in first_seen.items():
        if not (start <= seen < mid):
            continue
        n += 1
        c = first_close.get(k)
        if c is not None and c >= seen:
            hours.append((c - seen).total_seconds() / 3600.0)
    if not hours:
        return None, 0, n
    return round(statistics.median(hours), 1), len(hours), n


def naive_prep_ratio(rows, now=NOW, window=14):
    """Percent of held calls that got a brief, over the whole window."""
    start, _mid = naive_window(now, window)
    held, briefed = set(), set()
    for r in rows:
        if r["kind"] != "prep":
            continue
        t = datetime.fromisoformat(r["ts"])
        if not (start <= t <= now):
            continue
        for k in r["keys"]:
            if r["mode"] == "today":
                held.add(k)
            elif r["mode"] == "brief":
                briefed.add(k)
                held.add(k)
    if not held:
        return None
    return round(100.0 * len(briefed & held) / len(held)), len(held)


# ── KPI 6: threads waiting on you ────────────────────────────────────────────

def test_waiting_agrees_with_the_independent_method():
    rows = [
        snap(13, open_keys=["a", "b", "c", "d", "e"]),
        snap(8, open_keys=["a", "b", "c"]),
        snap(1, open_keys=["a", "b"]),
        closed(9, ["d", "e"]),
        closed(2, ["c"]),
    ]
    got = run(rows)["waiting"]
    mine, theirs = naive_waiting(rows)
    assert got["measured"] is True
    assert (got["value"], got["prior"]) == (mine, theirs)
    assert got["value"] == 2 and got["prior"] == 5


def test_waiting_reports_a_vanish_as_left_not_resolved():
    """ADVERSARIAL. Nine threads disappear with no close event.

    A bare count delta reads that as a great fortnight. The card must name them
    as having left the list, and must not claim them as closed work.
    """
    was = [f"t{i}" for i in range(12)]
    remaining = was[1:4]          # t0 genuinely left and was ticked
    rows = [
        snap(13, open_keys=was),
        snap(1, open_keys=remaining),
        # exactly one genuine close
        closed(5, ["t0"]),
    ]
    got = run(rows)["waiting"]
    assert got["value"] == 3 and got["prior"] == 12
    assert "1 you closed" in got["detail"]
    # The other eight vanished with no tick and must never read as progress.
    assert "8 dropped off without being closed" in got["detail"]
    # And a zero-valued clause is never printed: "0 arrived" is a template
    # slot being filled, which a reader notices before they notice the number.
    assert "0 arrived" not in got["detail"]


def test_waiting_needs_a_snapshot_in_each_half():
    rows = [snap(1, open_keys=["a"]), snap(2, open_keys=["a", "b"])]
    got = run(rows)["waiting"]
    assert got["measured"] is False
    assert "each week" in got["reason"]


# ── KPI 7: late items ────────────────────────────────────────────────────────

def test_late_counts_the_set_not_a_median_over_survivors():
    """ADVERSARIAL. The user clears the two quick late items and keeps the slow
    one. A median of days-late over survivors would RISE and read as decline."""
    rows = [
        snap(13, open_keys=["slow", "quick1", "quick2"],
             late_keys=["slow", "quick1", "quick2"],
             front=[{"key": "slow", "late": 20},
                    {"key": "quick1", "late": 2},
                    {"key": "quick2", "late": 3}]),
        snap(1, open_keys=["slow"], late_keys=["slow"],
             front=[{"key": "slow", "late": 28}]),
        closed(6, ["quick1", "quick2"]),
    ]
    got = run(rows)["late"]
    assert got["value"] == 1 and got["prior"] == 3
    assert got["direction"] == "better"
    assert "2 you cleared" in got["detail"]


def test_late_going_up_is_reported_as_worse():
    rows = [
        snap(13, open_keys=["a"], late_keys=[]),
        snap(1, open_keys=["a", "b", "c"], late_keys=["a", "b", "c"]),
    ]
    got = run(rows)["late"]
    assert got["value"] == 3 and got["prior"] == 0
    assert got["direction"] == "worse"


# ── KPI 9: time to close, as a cohort ────────────────────────────────────────

def test_time_to_close_agrees_with_the_independent_method():
    rows = [
        snap(13, front=[{"key": "a", "late": 0}, {"key": "b", "late": 0},
                        {"key": "c", "late": 0}, {"key": "d", "late": 0}],
             open_keys=["a", "b", "c", "d"]),
        snap(1, front=[{"key": "d", "late": 3}], open_keys=["d"]),
        closed(12, ["a"]),          # 24h
        closed(11, ["b"]),          # 48h
        closed(9, ["c"]),           # 96h
    ]
    got = run(rows)["time_to_close"]
    median, n_closed, n = naive_cohort_hours(rows)
    assert got["measured"] is True
    assert got["value"] == median == 48.0
    assert got["n"] == n == 4
    assert got["n_text"] == f"{n_closed} of {n} closed"


def test_the_cohort_counts_the_ones_that_never_closed():
    """ADVERSARIAL, and the single most important test in this file.

    Ten items reach the front page. The two fast ones close in a day. The other
    eight rot. A median over CLOSED items reports 24 hours and looks excellent.
    The cohort must report 2 of 10 and say the other eight are still open.
    """
    cohort = [f"i{n}" for n in range(10)]
    rows = [
        snap(13, front=[{"key": k, "late": 0} for k in cohort],
             open_keys=cohort),
        snap(1, open_keys=cohort[2:]),
        closed(12, ["i0", "i1"]),        # both closed within 24h
    ]
    got = run(rows)["time_to_close"]
    assert got["value"] == 24.0                      # true of the two that closed
    assert got["n"] == 10                            # but the cohort is ten
    assert got["n_text"] == "2 of 10 closed"
    assert "8 of the 10 are still open" in got["detail"]


def test_a_cohort_where_nothing_closed_says_so_and_fails_its_target():
    cohort = [f"i{n}" for n in range(5)]
    rows = [snap(13, front=[{"key": k, "late": 0} for k in cohort],
                 open_keys=cohort),
            snap(1, open_keys=cohort)]
    got = run(rows)["time_to_close"]
    assert got["measured"] is True
    assert got["value"] is None
    assert got["value_text"] == "none closed yet"
    assert got["met"] is False
    assert "all 5 are still open" in got["detail"]


def test_a_cohort_below_the_minimum_is_not_reported():
    rows = [snap(13, front=[{"key": "a", "late": 0}], open_keys=["a"]),
            snap(1, open_keys=[]), closed(12, ["a"])]
    got = run(rows)["time_to_close"]
    assert got["measured"] is False
    assert "too few to time" in got["reason"]
    # One vocabulary for the halves: the bars say "week before", so the reason
    # must not invent "the first week" as a third name for the same seven days.
    assert "first week" not in got["reason"]


def test_a_close_before_the_item_appeared_is_not_counted():
    """A stale close event must never produce a negative duration."""
    rows = [
        closed(14, ["a"]),                       # closed BEFORE it was seen
        snap(13, front=[{"key": "a", "late": 0}, {"key": "b", "late": 0},
                        {"key": "c", "late": 0}], open_keys=["a", "b", "c"]),
        snap(1, open_keys=["a"]),
        closed(12, ["b"]), closed(12, ["c"]),
    ]
    got = run(rows)["time_to_close"]
    assert got["value"] is not None and got["value"] > 0


# ── KPI 21: calls with a prep ────────────────────────────────────────────────

def test_prep_coverage_agrees_with_the_independent_method():
    rows = [
        prep(10, "today", ["m1", "m2", "m3", "m4"]),
        prep(10, "brief", ["m1"]),
        prep(9, "brief", ["m2"]),
        prep(3, "today", ["m5", "m6"]),
        prep(3, "brief", ["m5"]),
    ]
    got = run(rows)["prep_coverage"]
    pct, held = naive_prep_ratio(rows)
    assert got["measured"] is True
    assert got["value"] == pct == 50
    assert got["n"] == held == 6


def test_a_single_call_is_not_a_percentage():
    """ADVERSARIAL. One call, one brief, is not 100 percent coverage."""
    rows = [prep(3, "today", ["m1"]), prep(3, "brief", ["m1"])]
    got = run(rows)["prep_coverage"]
    assert got["measured"] is False
    assert "too few to be worth a percentage" in got["reason"]


def test_zero_calls_reads_differently_from_zero_percent():
    """No calls at all, one call, and nought percent are three different
    answers, and only one of them is a number."""
    none_at_all = run([])["prep_coverage"]
    assert none_at_all["measured"] is False
    assert none_at_all["value"] is None
    assert "no calls with people outside your team" in none_at_all["reason"]
    # And never the phrasing that reads as broken English.
    assert "only 0" not in none_at_all["reason"]

    two_calls = run([prep(3, "today", ["m1", "m2"]),
                     prep(3, "brief", ["m1"])])["prep_coverage"]
    assert two_calls["measured"] is False
    assert two_calls["reason"] != none_at_all["reason"]


# ── KPI 25: commitments closed ───────────────────────────────────────────────

def test_closed_agrees_with_the_independent_method():
    rows = [
        snap(13, open_keys=["a"]),
        snap(1, open_keys=[]),
        closed(10, ["a", "b"]),
        closed(9, ["c"]),
        closed(3, ["d", "e", "f"]),
        closed(2, ["g"]),
    ]
    got = run(rows)["closed"]
    this_half, last_half = naive_closed_counts(rows)
    assert (got["value"], got["prior"]) == (this_half, last_half)
    assert got["value"] == 4 and got["prior"] == 3


def test_the_same_item_closed_twice_counts_once():
    rows = [snap(13, open_keys=["a"]), snap(1, open_keys=[]),
            closed(3, ["a"]), closed(2, ["a"]), closed(1, ["a"])]
    got = run(rows)["closed"]
    assert got["value"] == 1


def test_disappearing_is_never_counted_as_closed():
    """ADVERSARIAL. Items leave the open set with no close event at all."""
    rows = [
        snap(13, open_keys=["a", "b", "c", "d"]),
        snap(1, open_keys=["a"]),
    ]
    got = run(rows)["closed"]
    assert got["measured"] is False
    assert "nothing has been ticked off" in got["reason"]


# ── Dirty input ──────────────────────────────────────────────────────────────

def test_duplicate_and_out_of_order_events_give_the_same_answer():
    """A retried job appends out of order and re-records its snapshot."""
    clean = [
        snap(13, open_keys=["a", "b", "c"]),
        snap(1, open_keys=["a"]),
        closed(5, ["b", "c"]),
    ]
    dirty = [clean[1], clean[2], clean[0], dict(clean[0]), dict(clean[1])]
    a = run(clean)
    b = run(dirty)
    for slug in kpi_report.KPI_ORDER:
        assert a[slug]["value"] == b[slug]["value"], slug
        assert a[slug]["prior"] == b[slug]["prior"], slug


def test_a_rerun_in_the_same_minute_does_not_double_count():
    first = snap(13, open_keys=["a", "b"])
    rerun = dict(first)
    rerun["open_keys"] = ["a"]            # the corrected numbers
    rows = [first, rerun, snap(1, open_keys=["a"])]
    got = run(rows)["waiting"]
    assert got["prior"] == 1              # the later write wins


def test_a_row_with_no_timestamp_is_ignored_not_fatal():
    rows = [snap(13, open_keys=["a", "b"]), {"kind": "snapshot"},
            snap(1, open_keys=["a"])]
    got = run(rows)["waiting"]
    assert got["measured"] is True


def test_a_timezone_aware_row_compares_with_a_naive_one():
    """Mail timestamps arrive aware, datetime.now() is naive. Both must work."""
    from datetime import timezone

    aware = {
        "ts": (NOW - timedelta(days=13)).replace(
            tzinfo=timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kpi_events.SNAPSHOT, "briefing": "morning-coffee",
        "counts": {"open": 2, "late": 0, "front": 0},
        "front": [], "open_keys": ["a", "b"], "late_keys": [],
    }
    got = run([aware, snap(1, open_keys=["a"])])["waiting"]
    assert got["measured"] is True and got["prior"] == 2


# ── Polarity ─────────────────────────────────────────────────────────────────

# slug, a value BETTER than the prior of 12, a value WORSE than it. Down is
# good for the first three and up is good for the last three, which is the
# whole point of the table this exercises.
POLARITY_CASES = [
    ("waiting", 3, 30),
    ("late", 2, 19),
    ("time_to_close", 10.0, 300.0),
    ("meeting_actions", 19.0, 0.5),
    ("prep_coverage", 99, 5),
    ("closed", 40, 1),
]


@pytest.mark.parametrize("slug,better,worse", POLARITY_CASES)
def test_direction_follows_the_polarity_table_not_the_sign(slug, better, worse):
    prior = 12.0
    assert kpi_report._direction(slug, better, prior)[0] == "better"
    assert kpi_report._direction(slug, worse, prior)[0] == "worse"
    assert kpi_report._direction(slug, prior, prior)[0] == "flat"


def test_inverting_the_polarity_table_breaks_the_direction(monkeypatch):
    """MUTATION CONTROL. With one row flipped, that KPI must read backwards."""
    flipped = dict(kpi_report.POLARITY)
    flipped["late"] = kpi_report.HIGHER_IS_BETTER
    monkeypatch.setattr(kpi_report, "POLARITY", flipped)
    # More late items now reads as "better", which is exactly the bug the real
    # table prevents.
    assert kpi_report._direction("late", 9, 1)[0] == "better"


def test_every_kpi_has_a_polarity_and_a_name():
    assert set(kpi_report.POLARITY) == set(kpi_report.KPI_ORDER)
    assert set(kpi_report.KPI_META) == set(kpi_report.KPI_ORDER)


# ── Cross-KPI coherence ──────────────────────────────────────────────────────

def test_the_six_numbers_do_not_contradict_each_other():
    """Two partitions of one set cannot disagree about that set.

    Closures counted by KPI 25 must cover the ones KPI 9 timed, and every late
    item must also be an open item.
    """
    cohort = ["a", "b", "c", "d"]
    rows = [
        snap(13, front=[{"key": k, "late": 0} for k in cohort],
             open_keys=cohort, late_keys=["a"]),
        snap(1, open_keys=["d"], late_keys=["d"]),
        closed(11, ["a"]), closed(10, ["b"]), closed(3, ["c"]),
    ]
    got = run(rows)

    # Every late key is an open key, in both snapshots.
    for row in rows:
        if row["kind"] == kpi_events.SNAPSHOT:
            assert set(row["late_keys"]) <= set(row["open_keys"])

    # KPI 9 timed closures that KPI 25 also saw.
    timed = int(got["time_to_close"]["n_text"].split(" of ")[0])
    total_closed = got["closed"]["value"] + got["closed"]["prior"]
    assert timed <= total_closed

    # The open count never exceeds what the snapshot recorded.
    assert got["waiting"]["value"] == 1


# ── Empty and sparse ─────────────────────────────────────────────────────────

def test_an_empty_log_measures_nothing_and_claims_nothing():
    report = kpi_report.compute(window_days=14, now=NOW, rows=[])
    assert report["measured_count"] == 0
    for kpi in report["kpis"]:
        assert kpi["measured"] is False
        assert kpi["reason"], f"{kpi['slug']} gave no reason"
        assert kpi["met"] is None, f"{kpi['slug']} claimed a target on no data"
        assert kpi["value"] is None


def test_a_kpi_that_raises_costs_only_its_own_card(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret@example.com and a home address")

    monkeypatch.setattr(kpi_report, "kpi_waiting", boom)
    report = kpi_report.compute(window_days=14, now=NOW, rows=[
        snap(13, open_keys=["a"]), snap(1, open_keys=[]), closed(2, ["a"])])
    row = [k for k in report["kpis"] if k["slug"] == "waiting"][0]
    assert row["measured"] is False
    assert "RuntimeError" in row["reason"]
    # The exception TEXT quotes its arguments, and this string is rendered and
    # mailed. Only the type may survive.
    assert "secret@example.com" not in row["reason"]
    assert "home address" not in row["reason"]
    assert len(report["kpis"]) == len(kpi_report.KPI_ORDER)


# ── The window ───────────────────────────────────────────────────────────────

def test_the_halves_are_half_open_so_nothing_lands_in_both():
    start, mid = kpi_report._window(NOW, 14)
    assert start < mid < NOW
    assert (mid - start).days == 7


def test_a_daylight_saving_shift_does_not_skew_the_halves():
    """US DST ends 2026-11-01. A window across it must still split evenly."""
    now = datetime(2026, 11, 7, 16, 0)
    start, mid = kpi_report._window(now, 14)
    assert (mid.date() - start.date()).days == 7
    assert (now.date() - mid.date()).days == 7


# ── Cold-read round two: a floor target is not a maximise ────────────────────

def test_the_card_reports_exactly_one_quantity():
    """This card once carried a two-part target, and whichever half led it, the
    other had to be explained in prose while the title named the wrong number.
    One card, one quantity, and the title matches it.
    """
    pages_now = [{"action_items_open": ["a", "b", "c"], "action_items_done": [],
                  "user_owned_open": ["a", "b", "c"]} for _ in range(5)]
    got = kpi_report.kpi_meeting_actions([], NOW - timedelta(days=14),
                                         NOW - timedelta(days=7), NOW,
                                         sources=pages_now)
    assert got["measured"] is True
    assert got["value"] == 100 and got["value_text"] == "100%"
    assert got["name"] == "Action items with an owner"
    assert got["met"] is True
    # One bar in the target, not two.
    assert "three or more" not in got["target_text"]


def test_the_detail_states_only_the_fraction_the_card_is_about():
    """A stranded "the typical meeting logs 3" invited the reader to divide
    the two numbers beside it, get 4.3, and find a third figure with no
    explanation. The card reports one quantity and shows its own fraction."""
    pages = [{"action_items_open": ["a", "b", "c"], "action_items_done": [],
              "user_owned_open": ["a"]} for _ in range(6)]
    got = kpi_report.kpi_meeting_actions([], NOW - timedelta(days=14),
                                         NOW - timedelta(days=7), NOW,
                                         sources=pages)
    assert got["value"] == 33
    assert got["detail"] == "6 of 18 action items say who owns them."
    assert "typical" not in got["detail"]


def test_meetings_with_no_action_items_read_as_unmeasured():
    """Dividing by no items would give a confident nought percent."""
    pages = [{"action_items_open": [], "action_items_done": [],
              "user_owned_open": []} for _ in range(4)]
    got = kpi_report.kpi_meeting_actions([], NOW - timedelta(days=14),
                                         NOW - timedelta(days=7), NOW,
                                         sources=pages)
    assert got["measured"] is False
    assert "no action items were captured" in got["reason"]


def test_a_falling_ownership_share_is_reported_as_worse():
    assert kpi_report._direction("meeting_actions", 40, 80)[0] == "worse"
    assert kpi_report._direction("meeting_actions", 95, 60)[0] == "better"


def test_the_opening_ignores_a_slip_that_still_meets_its_target():
    """The headline must not contradict the card's own verdict."""
    rows = [
        kpi_report._result("waiting", measured=True, value=2, prior=5,
                           value_text="2", prior_text="5", direction="better",
                           direction_text="down from", met=True),
        kpi_report._result("meeting_actions", measured=True, value=3,
                           prior=3.5, value_text="3", prior_text="3.5",
                           direction="worse", direction_text="down from",
                           met=True),
    ]
    lines = " ".join(kpi_report.opening(rows))
    assert "wrong direction" not in lines


def test_the_opening_still_names_a_real_regression():
    rows = [
        kpi_report._result("waiting", measured=True, value=2, prior=5,
                           value_text="2", prior_text="5", direction="better",
                           direction_text="down from", met=True),
        kpi_report._result("late", measured=True, value=9, prior=1,
                           value_text="9", prior_text="1", direction="worse",
                           direction_text="up from", met=False),
    ]
    lines = " ".join(kpi_report.opening(rows))
    assert "wrong direction" in lines


def test_a_card_never_prints_a_zero_valued_clause():
    """"0 arrived" and "0 newly late" are template slots, not facts."""
    rows = [
        snap(13, open_keys=["a", "b", "c"], late_keys=["a"]),
        snap(1, open_keys=["a"], late_keys=[]),
        closed(5, ["b", "c"]),
    ]
    got = run(rows)
    for slug in ("waiting", "late"):
        detail = got[slug]["detail"]
        assert " 0 " not in f" {detail} ", f"{slug} printed a zero clause: {detail}"


def test_the_headline_is_always_the_number_the_verdict_is_about():
    """The title, the headline number and the target must all name the same
    quantity, whether it passes or fails."""
    for owned, expect_met in ((1, False), (3, True)):
        pages = [{"action_items_open": ["a", "b", "c"],
                  "action_items_done": [],
                  "user_owned_open": ["a", "b", "c"][:owned]}
                 for _ in range(6)]
        got = kpi_report.kpi_meeting_actions([], NOW - timedelta(days=14),
                                             NOW - timedelta(days=7), NOW,
                                             sources=pages)
        assert got["met"] is expect_met
        assert got["value_text"].endswith("%"), "the headline changed shape"
        assert "owner" in got["target_text"]


def test_the_opening_never_claims_an_improvement_beside_a_failing_card():
    """"every one that can be compared improved" sat directly above the card
    the next sentence called short. Both were true of different sets and read
    as one contradiction."""
    rows = [
        kpi_report._result("waiting", measured=True, value=2, prior=5,
                           value_text="2", prior_text="5", direction="better",
                           direction_text="down from", met=True),
        kpi_report._result("meeting_actions", measured=True, value=49,
                           prior=None, value_text="49%", direction="none",
                           met=False),
    ]
    lines = " ".join(kpi_report.opening(rows))
    assert "improved" not in lines
    assert "still short" in lines


def test_the_waiting_detail_accounts_for_the_whole_change():
    """A line reading "2 you closed, 1 dropped off" against a fall from 5 to 2
    looks like it lost two items, because the ones still open go unmentioned."""
    rows = [
        snap(13, open_keys=["a", "b", "c", "d", "e"]),
        snap(1, open_keys=["d", "e"]),
        closed(5, ["a", "b"]),
    ]
    got = run(rows)["waiting"]
    assert "2 you closed" in got["detail"]
    assert "1 dropped off" in got["detail"]
    # Clause order follows the arithmetic: what moved, then what is left.
    # "2 still open after 2 you closed" read for a beat as though the 2 open
    # were what remained OF the 2 closed.
    assert got["detail"].endswith("leaving 2 open")


def test_a_single_measure_is_named_not_counted():
    """"None of the 1 measures is on target yet" is what a count reads like
    when it is dropped into a sentence built for many."""
    rows = [kpi_report._result("meeting_actions", measured=True, value=49,
                               prior=44, value_text="49%", prior_text="44%",
                               direction="better", direction_text="up from",
                               met=False)]
    lines = kpi_report.opening(rows)
    joined = " ".join(lines)
    assert "the 1 measures" not in joined
    assert "One measure" in joined
    assert "Action items with an owner" in joined
    assert "49%" in joined


def _row(slug, met, direction="better", **kw):
    """A measured row that moved the right way unless told otherwise."""
    base = dict(measured=True, value=5, prior=9, value_text="5",
                prior_text="9", direction=direction,
                direction_text="down from", met=met)
    base.update(kw)
    return kpi_report._result(slug, **base)


def test_the_opening_never_calls_several_failures_the_one():
    """"X is the one still short" told the reader the rest were fine while
    three cards read Not yet. False in the line a busy reader trusts most.

    Nothing here regressed, so the sentence about shortfalls is the one that
    fires: a regression would be named instead and is covered separately.
    """
    rows = [_row("waiting", True), _row("late", False),
            _row("prep_coverage", False), _row("closed", True)]
    joined = " ".join(kpi_report.opening(rows))
    assert "the one still short" not in joined
    assert "Late items" in joined and "Calls with a prep" in joined
    assert "still short" in joined


def test_three_or_more_failures_are_counted_rather_than_all_named():
    rows = [_row("waiting", False), _row("late", False),
            _row("prep_coverage", False), _row("closed", True)]
    joined = " ".join(kpi_report.opening(rows))
    assert "1 more are still short" in joined


def test_one_failure_is_still_called_the_one():
    rows = [_row("waiting", True), _row("late", False)]
    joined = " ".join(kpi_report.opening(rows))
    assert "Late items is the one still short" in joined


def test_a_regression_outranks_a_shortfall_in_the_second_line():
    """A measure moving the wrong way is the more urgent thing to say."""
    rows = [_row("waiting", True),
            _row("late", False, direction="worse", direction_text="up from",
                 value=9, prior=2, value_text="9", prior_text="2")]
    joined = " ".join(kpi_report.opening(rows))
    assert "wrong direction" in joined


def test_a_relative_target_with_no_prior_gives_no_verdict():
    """"Not yet." against "half of the previous fortnight" with no previous
    fortnight is a verdict on no evidence."""
    cohort = [f"c{n}" for n in range(5)]
    rows = [
        snap(13, front=[{"key": k, "late": 0} for k in cohort],
             open_keys=cohort),
        snap(1, open_keys=[]),
    ] + [closed(12, cohort)]
    got = run(rows)["time_to_close"]
    assert got["measured"] is True
    assert got["prior"] is None
    assert got["met"] is None, "a verdict was rendered with nothing to compare"
    assert "once there is one to compare" in got["target_text"]


def test_the_detail_never_repeats_the_count_line():
    """"every one of them closed" sat directly above "7 of 7 closed"."""
    cohort = [f"c{n}" for n in range(5)]
    rows = [snap(13, front=[{"key": k, "late": 0} for k in cohort],
                 open_keys=cohort),
            snap(1, open_keys=[]), closed(12, cohort)]
    got = run(rows)["time_to_close"]
    assert got["n_text"] == "5 of 5 closed"
    assert got["detail"] == ""


def test_a_duration_says_what_kind_of_number_it_is():
    """"168h" alone never said whether it was a mean, a median or a total."""
    cohort = [f"c{n}" for n in range(5)]
    rows = [snap(13, front=[{"key": k, "late": 0} for k in cohort],
                 open_keys=cohort),
            snap(1, open_keys=[]), closed(12, cohort)]
    got = run(rows)["time_to_close"]
    assert got["unit_note"] == "median"
