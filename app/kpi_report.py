#!/usr/bin/env python3
"""The six numbers on the weekly scorecard, and the rules that keep them true.

A metrics product fails in a particular way: not by crashing, but by printing a
confident number that means something other than what the reader thinks. Every
rule below exists because a plausible wrong number was possible without it.

**Direction is not uniform, so it is data.** Down is good for three of these and
up is good for the other three. A shared comparison bar that paints any
increase green renders "late items up 40 percent" as a success. `POLARITY` is
the single source of that fact and the renderer reads it rather than guessing.

**Disappearance is not closure.** An item can leave the open set because the
user finished it, or because a scan missed it, a deal was reorganized, or a
list was tidied. Only an explicit close event counts as done. Everything else
that leaves is reported as "left the list", separately, in the reader's words.

**A median over survivors lies in the flattering direction.** The obvious way
to measure time-to-close (median over items that closed) improves as the worst
items rot, because an item that never closes is never in the sample. So KPI 9
is a cohort: of the items that first appeared in a fixed earlier week, how many
have since closed, and how long did those take, with the still-open ones
counted and shown. A cohort can look bad. That is the point.

**Below its evidence bar, a KPI says so.** Two data points do not make a
median and one meeting does not make a ratio. Each KPI declares what it needs;
under that it returns `measured=False` with a reason in plain words, and the
card renders no value, no bar and no verdict. "Not enough yet" is an honest
answer and 100 percent of one is not.

No model is called anywhere in this file. The opening sentences are chosen from
a rule table, because a scorecard that gushes about a bad week teaches the
reader to stop believing it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kpi_events                                              # noqa: E402

# Which direction is good, per KPI. Read by the renderer; never inferred from
# the sign of a delta. Inverting one row here must turn a test red.
LOWER_IS_BETTER = "lower"
HIGHER_IS_BETTER = "higher"

POLARITY = {
    "waiting": LOWER_IS_BETTER,
    "late": LOWER_IS_BETTER,
    "time_to_close": LOWER_IS_BETTER,
    "meeting_actions": HIGHER_IS_BETTER,
    "prep_coverage": HIGHER_IS_BETTER,
    "closed": HIGHER_IS_BETTER,
}

# The user-facing name of each KPI, and the original number from the list these
# six were chosen from, kept so a conversation about "KPI 9" still resolves.
KPI_META = {
    "waiting":         (6,  "Threads waiting on you"),
    "late":            (7,  "Late items"),
    "time_to_close":   (9,  "Time to close"),
    "meeting_actions": (20, "Action items with an owner"),
    "prep_coverage":   (21, "Calls with a prep"),
    "closed":          (25, "Commitments closed"),
}

KPI_ORDER = ("waiting", "late", "time_to_close", "meeting_actions",
             "prep_coverage", "closed")

# Minimum evidence. Below these a KPI reports "not enough yet" rather than a
# number: a ratio off one meeting and a median of two numbers are noise wearing
# the clothes of a result.
MIN_COHORT = 3          # items in a cohort before a time-to-close is meaningful
MIN_RATIO_DENOM = 3     # meetings or calls before a percentage is meaningful
MIN_MEETINGS = 2        # meetings before an average per meeting is meaningful

# How long an item gets to close before the cohort stops waiting for it. The
# cohort is drawn from the FIRST half of the window so every member has had at
# least this long to close, which is what makes the number comparable week to
# week.
COHORT_MATURITY_DAYS = 7


def _parse_ts(value) -> datetime | None:
    """A timestamp from the events file, or None. Mixed naive and aware inputs
    are normalized to naive local, because the file is written by one machine
    and a tz-aware row would otherwise refuse to compare with a naive one."""
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _window(now: datetime, window_days: int) -> tuple:
    """Half-open [start, mid) and [mid, now]. Local time, dated boundaries.

    Boundaries are half-open so an event exactly on the midpoint belongs to
    exactly one half. Computed by date subtraction rather than by adding
    seconds, so a daylight-saving shift inside the window cannot make one half
    an hour shorter than the other.
    """
    half = window_days // 2
    start = (now - timedelta(days=window_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    mid = (now - timedelta(days=half)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, mid


def _snapshots(rows: list) -> list:
    """Snapshot events, oldest first, deduplicated within a run.

    Sorted by their own timestamp rather than by file order: a retried job
    appends out of order, and "the first snapshot of the window" must be the
    earliest by clock, not the earliest by line number. Two snapshots of the
    same briefing in the same minute are one run recorded twice; the later one
    wins so a re-run's corrected numbers are what count.
    """
    seen = {}
    for row in rows:
        if row.get("kind") != kpi_events.SNAPSHOT:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        stamped = dict(row)
        stamped["_ts"] = ts
        # Same briefing, same minute: one run. Keep the last written.
        dedup = (row.get("briefing"), ts.replace(second=0, microsecond=0))
        seen[dedup] = stamped
    return sorted(seen.values(), key=lambda r: r["_ts"])


def _events_of(rows: list, kind: str) -> list:
    out = []
    for row in rows:
        if row.get("kind") != kind:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        stamped = dict(row)
        stamped["_ts"] = ts
        out.append(stamped)
    return sorted(out, key=lambda r: r["_ts"])


def _in(rows: list, start: datetime, end: datetime) -> list:
    return [r for r in rows if start <= r["_ts"] < end]


def _closed_keys(rows: list, start: datetime, end: datetime) -> set:
    """Every key explicitly closed in a period. The ONLY definition of closed."""
    keys = set()
    for row in _in(_events_of(rows, kpi_events.CLOSED), start, end):
        for k in (row.get("keys") or []):
            if k:
                keys.add(str(k))
    return keys


def _first_closure_at(rows: list) -> dict:
    """key -> the first moment it was explicitly closed."""
    out = {}
    for row in _events_of(rows, kpi_events.CLOSED):
        for k in (row.get("keys") or []):
            k = str(k)
            if k and k not in out:
                out[k] = row["_ts"]
    return out


def _result(slug: str, **kw) -> dict:
    """One KPI row in the report's shape. Unmeasured rows still carry every
    field, so the renderer never has to test for a missing key."""
    number, name = KPI_META[slug]
    row = {
        "slug": slug,
        "number": number,
        "name": name,
        "polarity": POLARITY[slug],
        "measured": False,
        "reason": "",
        "value": None,
        "value_text": "",
        "prior": None,
        "prior_text": "",
        "unit": "",
        "unit_note": "",
        "n": 0,
        "n_text": "",
        "target_text": "",
        "met": None,
        "detail": "",
        "measured_as": "",
        "direction": "none",
        "direction_text": "",
    }
    row.update(kw)
    return row


def _direction(slug: str, value, prior) -> tuple:
    """(direction, words) for a change, read from POLARITY rather than the sign.

    Returns one of better, worse, flat, none. The words say which way the
    number moved AND whether that is good, because a bare "down 3" leaves the
    reader to work out the direction of virtue for themselves.
    """
    if value is None or prior is None:
        return "none", ""
    if value == prior:
        return "flat", "unchanged from last week"
    rose = value > prior
    good = (rose and POLARITY[slug] == HIGHER_IS_BETTER) or \
           (not rose and POLARITY[slug] == LOWER_IS_BETTER)
    return ("better" if good else "worse"), ("up from" if rose else "down from")


# ── KPI 6: threads waiting on you ────────────────────────────────────────────

def kpi_waiting(rows, start, mid, now) -> dict:
    """Open threads at the start of the window versus now, as SETS.

    A bare count delta cannot tell "you replied to nine people" from "nine
    threads dropped out of the scan", and those deserve opposite reactions. So
    this reports resolved (left AND has a close event), left without a close,
    and newly arrived, each from key sets.
    """
    snaps = _snapshots(rows)
    early = _in(snaps, start, mid)
    late = _in(snaps, mid, now + timedelta(seconds=1))
    if not early or not late:
        return _result("waiting", reason="not enough history yet, this needs a "
                                         "briefing from each week")
    first, last = early[0], late[-1]
    was = {str(k) for k in (first.get("open_keys") or [])}
    now_set = {str(k) for k in (last.get("open_keys") or [])}
    if not was and not now_set:
        return _result("waiting", reason="no open threads were recorded in "
                                         "either week")

    gone = was - now_set
    closed = _closed_keys(rows, first["_ts"], now + timedelta(seconds=1))
    resolved = gone & closed
    left_unclosed = gone - closed
    arrived = now_set - was

    value, prior = len(now_set), len(was)
    direction, words = _direction("waiting", value, prior)
    # Only the parts that happened. A zero-valued clause is a template slot
    # being filled, and a reader notices that faster than they notice the
    # number. "Dropped off" rather than "left the list without being ticked":
    # the second describes the software's bookkeeping, not the reader's world.
    # Accounts for the WHOLE change, not just the departures: a line reading
    # "2 you closed, 1 dropped off" against a fall from 5 to 2 looks like it
    # has lost two items, because the ones still open are never mentioned.
    # The headline number leads, so the sentence's subject is the number the
    # card is about. Listing the departures first put "2 you closed" beside a
    # headline of 2 that meant something else entirely.
    moves = []
    if resolved:
        moves.append(f"{len(resolved)} you closed")
    if left_unclosed:
        moves.append(f"{len(left_unclosed)} dropped off without being closed")
    if arrived:
        moves.append(f"{len(arrived)} arrived")
    if value and moves:
        detail = f"{', '.join(moves)}, leaving {value} open"
    elif moves:
        detail = f"Nothing left open: {', '.join(moves)}"
    else:
        detail = "no change either way"
    return _result(
        "waiting", measured=True, value=value, prior=prior,
        value_text=str(value), prior_text=str(prior), unit="threads",
        n=value, n_text=f"{value} open now",
        direction=direction, direction_text=words,
        target_text="down 40 percent against the week before",
        met=(prior > 0 and value <= prior * 0.6),
        detail=detail,
    )


# ── KPI 7: late items ────────────────────────────────────────────────────────

def kpi_late(rows, start, mid, now) -> dict:
    """How many items are late now versus at the start, as sets.

    Deliberately NOT a median of days-late over what is still open: closing
    your quick items raises that median, so working well would look like
    slipping. The count of the late set plus what entered and left it says the
    true thing without that trap.
    """
    snaps = _snapshots(rows)
    early = _in(snaps, start, mid)
    late_snaps = _in(snaps, mid, now + timedelta(seconds=1))
    if not early or not late_snaps:
        return _result("late", reason="not enough history yet, this needs a "
                                      "briefing from each week")
    first, last = early[0], late_snaps[-1]
    was = {str(k) for k in (first.get("late_keys") or [])}
    now_set = {str(k) for k in (last.get("late_keys") or [])}

    closed = _closed_keys(rows, first["_ts"], now + timedelta(seconds=1))
    cleared = (was - now_set) & closed
    newly = now_set - was

    value, prior = len(now_set), len(was)
    direction, words = _direction("late", value, prior)
    if value == 0 and prior == 0:
        detail = "nothing has gone late in either week"
    else:
        parts = []
        if cleared:
            parts.append(f"{len(cleared)} you cleared")
        if newly:
            parts.append(f"{len(newly)} newly late")
        detail = ", ".join(parts) or "no change either way"
    return _result(
        "late", measured=True, value=value, prior=prior,
        value_text=str(value), prior_text=str(prior), unit="items",
        n=value, n_text=f"{value} late now",
        direction=direction, direction_text=words,
        target_text="down by half, and under three",
        met=(value <= 3 and (prior == 0 or value <= prior * 0.5)),
        detail=detail,
    )


# ── KPI 9: time to close, as a cohort ────────────────────────────────────────

def kpi_time_to_close(rows, start, mid, now) -> dict:
    """Of the items that first reached the front page in the earlier week, how
    many have since closed and how long did they take.

    A cohort, not a survivor median. Every member has had at least a week to
    close, the ones that have not are counted and shown, and the median is
    stated as "of the N that closed" so it can never read as the whole story.
    """
    snaps = _snapshots(rows)
    if not snaps:
        return _result("time_to_close", reason="no briefings have run yet")

    first_seen = {}
    for snap in snaps:
        for item in (snap.get("front") or []):
            k = str(item.get("key") or "")
            if k and k not in first_seen:
                first_seen[k] = snap["_ts"]

    cohort = {k: t for k, t in first_seen.items() if start <= t < mid}
    if len(cohort) < MIN_COHORT:
        return _result(
            "time_to_close",
            reason=f"only {len(cohort)} items both opened and closed in this "
                   f"fortnight, too few to time"
                   if len(cohort) else
                   "nothing opened and closed in this fortnight, so there is "
                   "nothing to time")

    closures = _first_closure_at(rows)
    hours, still_open = [], 0
    for k, seen_at in cohort.items():
        closed_at = closures.get(k)
        if closed_at is not None and closed_at >= seen_at:
            hours.append((closed_at - seen_at).total_seconds() / 3600.0)
        else:
            still_open += 1

    if not hours:
        return _result(
            "time_to_close", measured=True, value=None, unit="hours",
            n=len(cohort), n_text=f"0 of {len(cohort)} closed",
            value_text="none closed yet",
            target_text="half of the previous fortnight",
            met=False,
            measured_as="counted from first appearing on your front page to "
                        "being ticked off",
            detail=f"all {len(cohort)} are still open",
        )

    value = round(statistics.median(hours), 1)

    # The prior cohort: items first seen in the window before this one, given
    # the same maturity. Comparing a matured cohort with a fresh one would
    # flatter whichever had longer to close.
    prior_start = start - (mid - start)
    prior_cohort = {k: t for k, t in first_seen.items() if prior_start <= t < start}
    prior_hours = []
    for k, seen_at in prior_cohort.items():
        closed_at = closures.get(k)
        if closed_at is not None and closed_at >= seen_at:
            prior_hours.append((closed_at - seen_at).total_seconds() / 3600.0)
    prior = round(statistics.median(prior_hours), 1) if len(prior_hours) >= MIN_COHORT else None

    direction, words = _direction("time_to_close", value, prior)
    # The target is RELATIVE ("half of the previous fortnight"), so with no
    # prior there is nothing to be half of. Rendering "Not yet." against a
    # comparison the card says it does not have is a verdict on no evidence.
    met = (value <= prior * 0.5) if prior is not None else None
    return _result(
        "time_to_close", measured=True, value=value, prior=prior,
        value_text=f"{value:g}h",
        unit_note="median",
        prior_text=(f"{prior:g}h" if prior is not None else ""),
        unit="hours",
        n=len(cohort), n_text=f"{len(hours)} of {len(cohort)} closed",
        direction=direction, direction_text=words,
        target_text=("half of the previous fortnight" if prior is not None
                     else "half of the previous fortnight, once there is one "
                          "to compare"),
        met=met,
        measured_as="counted from first appearing on your front page to being "
                    "ticked off",
        # DEFECT: this said "every one of them closed" directly above an
        # n_text of "7 of 7 closed", which is the same statement twice.
        detail=(f"{still_open} of the {len(cohort)} are still open"
                if still_open else ""),
    )


# ── KPI 20: action items that name an owner ──────────────────────────────────

def kpi_meeting_actions(rows, start, mid, now, sources=None) -> dict:
    """The share of action items from meetings that name who owns them.

    ONE card, ONE quantity. This began as "action items per meeting, and the
    share naming an owner", and a two-part target cannot be reported by one
    number: whichever half led the card, the other half had to be explained in
    prose, and the card's own title then described the number it was not
    showing. A cold reader stopped on it every time.

    Ownership is the half worth keeping. The raw count per meeting is set by
    how a transcript happened to be written, so it measures the notetaker
    rather than the user, while an item with no owner is a real loose end the
    reader can do something about. The typical count still appears as context
    on the card, as a fact rather than as a second score.
    """
    pages = _meeting_pages(start, now) if sources is None else sources
    if len(pages) < MIN_MEETINGS:
        return _result(
            "meeting_actions",
            reason=f"only {len(pages)} meeting notes were filed in this "
                   f"fortnight")

    def _tally(rows_):
        """(median items per meeting, owned, total) over a set of pages."""
        per_meeting, owned, total = [], 0, 0
        for page in rows_:
            items = list(page.get("action_items_open") or []) + \
                    list(page.get("action_items_done") or [])
            per_meeting.append(len(items))
            total += len(items)
            owned += len(page.get("user_owned_open") or [])
        median_ = round(statistics.median(per_meeting), 1) if per_meeting else 0
        return median_, owned, total

    median, owned, total = _tally(pages)
    if not total:
        return _result(
            "meeting_actions",
            reason=f"no action items were captured from {len(pages)} meetings")
    share = round(100.0 * owned / total)

    # The same measure over the previous fortnight, so the comparison is
    # like for like. Skipped when the caller supplied pages directly.
    prior_share = None
    if sources is None:
        prior_pages = _meeting_pages(start - (now - start), start)
        if len(prior_pages) >= MIN_MEETINGS:
            _pm, p_owned, p_total = _tally(prior_pages)
            if p_total:
                prior_share = round(100.0 * p_owned / p_total)

    direction, words = _direction("meeting_actions", share, prior_share)
    return _result(
        "meeting_actions", measured=True, value=share, prior=prior_share,
        value_text=f"{share}%",
        prior_text=(f"{prior_share}%" if prior_share is not None else ""),
        unit="percent",
        n=len(pages),
        n_text=f"across {len(pages)} meeting notes, internal ones included",
        direction=direction, direction_text=words,
        target_text="nine in ten name an owner",
        met=(share >= 90),
        detail=f"{owned} of {total} action items say who owns them.",
    )


def _meeting_pages(start: datetime, end: datetime) -> list:
    """Meeting source pages filed between two moments. Fails open to empty."""
    try:
        import week_retro

        pages, _open_items, _errs = week_retro.fetch_meeting_sources(
            start.date(), end.date())
        return pages or []
    except Exception:                                           # noqa: BLE001
        return []


# ── KPI 21: calls with a prep ────────────────────────────────────────────────

def kpi_prep_coverage(rows, start, mid, now) -> dict:
    """External calls that got a prep brief, over external calls held."""
    preps = _in(_events_of(rows, kpi_events.PREP), start, now + timedelta(seconds=1))
    held, briefed = set(), set()
    for row in preps:
        keys = {str(k) for k in (row.get("keys") or []) if k}
        if row.get("mode") == "today":
            held |= keys
        elif row.get("mode") == "brief":
            briefed |= keys
            held |= keys      # a call that got a brief was certainly held
    if not held:
        return _result(
            "prep_coverage",
            reason="no calls with people outside your team in this fortnight")
    if len(held) < MIN_RATIO_DENOM:
        return _result(
            "prep_coverage",
            reason=f"{len(held)} outside calls is too few to be worth a "
                   f"percentage")

    covered = len(briefed & held)
    value = round(100.0 * covered / len(held))

    prior = _in(_events_of(rows, kpi_events.PREP), start - (mid - start), start)
    p_held, p_brief = set(), set()
    for row in prior:
        keys = {str(k) for k in (row.get("keys") or []) if k}
        if row.get("mode") == "today":
            p_held |= keys
        elif row.get("mode") == "brief":
            p_brief |= keys
            p_held |= keys
    prior_value = (round(100.0 * len(p_brief & p_held) / len(p_held))
                   if len(p_held) >= MIN_RATIO_DENOM else None)

    direction, words = _direction("prep_coverage", value, prior_value)
    return _result(
        "prep_coverage", measured=True, value=value, prior=prior_value,
        value_text=f"{value}%",
        prior_text=(f"{prior_value}%" if prior_value is not None else ""),
        unit="percent",
        n=len(held), n_text=f"{covered} of {len(held)} outside calls",
        direction=direction, direction_text=words,
        target_text="nine in ten",
        met=(value >= 90),
        detail=(f"{len(held) - covered} went in without one"
                if len(held) - covered else "every one had a brief"),
    )


# ── KPI 25: commitments closed ───────────────────────────────────────────────

def kpi_closed(rows, start, mid, now) -> dict:
    """Distinct items explicitly ticked off, this week against last.

    Explicit closes only. An item that quietly left the list is not counted
    here, which is why this number can be lower than the drop in open threads.
    """
    snaps = _snapshots(rows)
    if not snaps:
        return _result("closed", reason="no briefings have run yet")

    this_week = _closed_keys(rows, mid, now + timedelta(seconds=1))
    last_week = _closed_keys(rows, start, mid)
    value, prior = len(this_week), len(last_week)
    if value == 0 and prior == 0:
        return _result("closed", reason="nothing has been ticked off yet in "
                                        "either week")

    direction, words = _direction("closed", value, prior)
    return _result(
        "closed", measured=True, value=value, prior=prior,
        value_text=str(value), prior_text=str(prior), unit="items",
        n=value, n_text=f"{value} this week",
        direction=direction, direction_text=words,
        target_text="more than last week",
        met=(value > prior),
        # The movement line already says "up from 0" and the bars show both
        # halves, so a third rendering of the same comparison goes here only
        # when the two weeks are equal and the bars say nothing on their own.
        detail=("the same as last week" if value == prior else ""),
    )


# ── The opening ──────────────────────────────────────────────────────────────

def opening(kpis: list, skipped: int = 0) -> list:
    """Two sentences at most, chosen by rule from what actually happened.

    Never gushes. A week where nothing improved gets a plain, true sentence,
    because a scorecard that calls every week strong is one the reader stops
    believing by the third Friday. Superlatives are only ever attached to a
    measured value that earned them.
    """
    measured = [k for k in kpis if k["measured"] and k["value"] is not None]
    if not measured:
        return ["Van Gogh has not gathered enough yet to score this week.",
                "The numbers start once a full fortnight of briefings is behind you."]

    better = [k for k in measured if k["direction"] == "better"]
    # A KPI only counts as dragging the week down if it moved the wrong way AND
    # is missing its target. One that slipped but still clears its bar is not
    # the headline bad news, and calling it that reads as the email arguing
    # with its own verdict line.
    worse = [k for k in measured
             if k["direction"] == "worse" and k["met"] is not True]
    met = [k for k in measured if k["met"]]

    # Counts, not a retelling of one card. An opening that repeats a card
    # verbatim is preamble the reader has to read twice, so this says what the
    # fortnight amounted to and then names at most one thing to look at.
    hit = len(met)
    total = len(measured)
    lines = []
    # "None of the 1 measures" is what a count reads like when it is dropped
    # into a sentence built for many. With a single measure, name it.
    if total == 1:
        only = measured[0]
        verdict = "on target" if only["met"] else "not on target yet"
        lines.append(f"One measure had enough behind it this fortnight. "
                     f"{only['name']} is {verdict}, at {only['value_text']}.")
        lines.append("The rest start counting once more of the fortnight is "
                     "behind them.")
        return lines[:2]
    if hit == total:
        lines.append(f"Every measure with enough behind it came in on target, "
                     f"all {total} of them.")
    elif hit:
        # Only measures that actually carry a comparison may be counted as
        # improved: a card with no prior cannot be said to have moved, and a
        # claim the reader cannot check from the cards is a claim not worth
        # making.
        # No improvement count here. Any phrasing of it sat directly above a
        # card the next sentence calls short, and a reader takes the two as
        # describing one set. The per-card verdicts already say who moved.
        lines.append(f"{hit} of {total} measures on target.")
    elif better:
        lines.append(f"None of the {total} measures is on target yet, though "
                     f"{len(better)} moved the right way.")
    else:
        lines.append(f"None of the {total} measures is on target yet.")
    # Below, `worse` and `met` decide the second line.

    if worse:
        drag = max(worse, key=lambda k: _move_size(k))
        moved = "rose" if drag["polarity"] == LOWER_IS_BETTER else "slipped"
        lines.append(f"{drag['name']} {moved} to {drag['value_text']} "
                     f"from {drag['prior_text']}, which is the wrong "
                     f"direction.")
    elif not met:
        lines.append("Nothing moved the wrong way.")
    else:
        shortfall = [k for k in measured if k["met"] is False]
        if len(shortfall) == 1:
            lines.append(f"{shortfall[0]['name']} is the one still short.")
        elif shortfall:
            # Never "the one" when there are several: the reader takes that
            # sentence at face value and walks away believing the rest are
            # fine. Name them, or say how many.
            names = [k["name"] for k in shortfall]
            if len(names) == 2:
                lines.append(f"{names[0]} and {names[1]} are still short.")
            else:
                lines.append(f"{names[0]}, {names[1]} and "
                             f"{len(names) - 2} more are still short.")
        else:
            lines.append("Nothing moved the wrong way.")
    return lines[:2]


def _move_size(kpi: dict) -> float:
    """How far a KPI moved, as a fraction of where it started. Used only to
    rank sentences, never shown."""
    try:
        if kpi["prior"] in (None, 0):
            return abs(float(kpi["value"] or 0))
        return abs(float(kpi["value"]) - float(kpi["prior"])) / abs(float(kpi["prior"]))
    except (TypeError, ValueError):
        return 0.0


def _sentence(kpi: dict) -> str:
    """One sentence about a KPI that moved, naming both numbers.

    A rise from nothing is stated as a plain count rather than as growth: "rose
    from 0 to 2" invites the reader to hear a doubling where the honest fact is
    that two things happened.
    """
    if kpi["prior"] is None or kpi["prior_text"] == "":
        return f"{kpi['name']} came in at {kpi['value_text']}."
    try:
        started_at_nothing = float(kpi["prior"]) == 0
    except (TypeError, ValueError):
        started_at_nothing = False
    if started_at_nothing:
        return (f"{kpi['name']}: {kpi['value_text']}, against none at all "
                f"last week.")
    fell = "fell" if kpi["polarity"] == LOWER_IS_BETTER else "rose"
    return (f"{kpi['name']} {fell} from {kpi['prior_text']} to "
            f"{kpi['value_text']}.")


# ── The spotlight ────────────────────────────────────────────────────────────
#
# The last block of the email is one part of Van Gogh the reader has not used
# lately. Three rules keep it from becoming nagging, which is the failure mode
# that gets a weekly email filtered:
#
#   * a skill the user cannot use is never suggested (no Outlook account, no
#     configured client, no notetaker key), because advice you cannot take is
#     worse than silence;
#   * a skill shown recently is not shown again, tracked in the send ledger;
#   * when everything has been used, the section says so and stops, rather
#     than inventing a suggestion to fill the space.
#
# `script_stems` are the file names that count as "used": a skill is used when
# a human ran its script, which is what user_state.record_skill_use writes.

SPOTLIGHTS = [
    {
        "skill": "relationship-radar",
        "script_stems": ["relationship_radar"],
        "title": "Relationship Radar",
        "what": "Finds the people you have not spoken to in a month and drafts "
                "a check-in to each one in your own voice.",
        "when": "Takes about a minute, whenever you have one.",
        "command": "/van-gogh:relationship-radar",
        "requires": None,
    },
    {
        "skill": "follow-up-radar",
        "script_stems": ["follow_up_radar"],
        "title": "Follow-up Radar",
        "what": "Surfaces the follow-ups whose date has arrived, and drafts "
                "the nudge for each one.",
        "when": "Good first thing, before the day fills up.",
        "command": "/van-gogh:follow-up-radar",
        "requires": None,
    },
    {
        "skill": "meeting-prep",
        "script_stems": ["meeting_prep"],
        "title": "Meeting Prep",
        "what": "One page before a call: who is on it, what you owe them, and "
                "what a good outcome looks like.",
        "when": "Run it the morning of any call that matters.",
        "command": "/van-gogh:meeting-prep",
        "requires": None,
    },
    {
        "skill": "five-fifteen",
        "script_stems": ["five_fifteen"],
        "title": "The 5:15 report",
        "what": "Writes the weekly client report from your mail, meetings and "
                "notes, measured against what you agreed to deliver.",
        "when": "Useful if you write a weekly update for anyone.",
        "command": "/van-gogh:five-fifteen",
        "requires": "clients",
    },
    {
        "skill": "voice-generator",
        "script_stems": ["voice_generator"],
        "title": "Voice Generator",
        "what": "Reads three months of your sent mail and writes down how you "
                "actually sound, so every draft matches it.",
        "when": "Once, early. Every draft after it is better.",
        "command": "/van-gogh:voice-generator",
        "requires": None,
    },
    {
        "skill": "workbench",
        "script_stems": ["workbench_serve"],
        "title": "The Workbench",
        "what": "Your briefings, your open threads and the map of your vault, "
                "on one local page you can act from.",
        "when": "Open it once and leave it in a tab for a day.",
        "command": "/van-gogh:workbench",
        "requires": None,
    },
    {
        "skill": "meeting-ingest",
        "script_stems": ["meeting_ingest"],
        "title": "Meeting Ingest",
        "what": "Files a meeting into your vault: the notes, who was there, "
                "what was decided, and who owes what.",
        "when": "After any call worth remembering.",
        "command": "/van-gogh:meeting-ingest",
        "requires": "notetaker",
    },
    {
        "skill": "vault-audit",
        "script_stems": ["vault_gardener"],
        "title": "Vault Audit",
        "what": "Checks your vault for stale pages, orphans and notes that "
                "were started and never finished.",
        "when": "Monthly is plenty.",
        "command": "/van-gogh:vault-audit",
        "requires": None,
    },
]

# How long a skill stays "recently used", and how long a spotlight stays spent.
UNUSED_AFTER_DAYS = 30
SPOTLIGHT_COOLDOWN_WEEKS = 6


def _requirement_met(requirement) -> bool:
    """Whether the reader could act on this suggestion at all.

    Fails toward NOT suggesting: if the check itself breaks, the skill is
    treated as unavailable, because a suggestion the reader cannot follow is
    the one thing this block must never produce.
    """
    if not requirement:
        return True
    try:
        import config_loader

        if requirement == "clients":
            return bool(config_loader.clients())
        if requirement == "notetaker":
            import notetaker

            return bool(notetaker.configured())
    except Exception:                                           # noqa: BLE001
        return False
    return True


def read_usage() -> dict:
    """stem -> {"last": iso, "attended": iso}, or {} when nothing is recorded."""
    try:
        import config_loader

        path = config_loader.logs_dir() / "skill_usage.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, Exception):                    # noqa: BLE001
        return {}


def spotlight(usage: dict | None = None, shown: list | None = None,
              now: datetime | None = None) -> dict:
    """The one skill to suggest, or a note saying there is nothing to suggest.

    `shown` is the list of skill names already spotlighted, newest last, from
    the send ledger.
    """
    now = now or datetime.now()
    usage = read_usage() if usage is None else usage
    shown = list(shown or [])
    recent_shown = set(shown[-SPOTLIGHT_COOLDOWN_WEEKS:])
    cutoff = now - timedelta(days=UNUSED_AFTER_DAYS)

    unused = []
    for entry in SPOTLIGHTS:
        if entry["skill"] in recent_shown:
            continue
        if not _requirement_met(entry.get("requires")):
            continue
        # "Used" means a human ran it. A nightly scheduled job is not the user
        # forming a habit, so only the attended stamp counts here.
        touched = None
        for stem in entry["script_stems"]:
            stamp = _parse_ts((usage.get(stem) or {}).get("attended"))
            if stamp and (touched is None or stamp > touched):
                touched = stamp
        if touched is not None and touched >= cutoff:
            continue
        unused.append(entry)

    if not unused:
        return {"all_used": True,
                "note": "You have used every part of Van Gogh this month. "
                        "There is nothing here you are missing."}

    pick = unused[0]
    return {
        "all_used": False,
        "skill": pick["skill"],
        "title": pick["title"],
        "what": pick["what"],
        "when": pick["when"],
        "command": pick["command"],
    }


# ── The report ───────────────────────────────────────────────────────────────

def compute(window_days: int | None = None, now: datetime | None = None,
            rows: list | None = None, skipped: int | None = None) -> dict:
    """Every KPI, plus the opening. Each one isolated: a KPI that raises costs
    its own card and never the report."""
    import config_loader

    now = now or datetime.now()
    if window_days is None:
        try:
            window_days = config_loader.kpi_window_days()
        except Exception:                                       # noqa: BLE001
            window_days = 14
    if rows is None:
        rows, skipped = kpi_events.read()
    skipped = skipped or 0

    start, mid = _window(now, window_days)

    builders = {
        "waiting": kpi_waiting,
        "late": kpi_late,
        "time_to_close": kpi_time_to_close,
        "meeting_actions": kpi_meeting_actions,
        "prep_coverage": kpi_prep_coverage,
        "closed": kpi_closed,
    }
    kpis = []
    for slug in KPI_ORDER:
        try:
            kpis.append(builders[slug](rows, start, mid, now))
        except Exception as exc:                                # noqa: BLE001
            # The type, never the message: an exception's text quotes the
            # arguments it failed on, and this string is rendered and mailed.
            kpis.append(_result(slug, reason=f"this one could not be counted "
                                             f"({type(exc).__name__})"))

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_days": window_days,
        "window_start": start.date().isoformat(),
        "window_end": now.date().isoformat(),
        "midpoint": mid.date().isoformat(),
        "kpis": kpis,
        "opening": opening(kpis, skipped),
        "malformed_events": skipped,
        "measured_count": sum(1 for k in kpis if k["measured"]),
    }


def has_enough_history(rows: list, window_days: int,
                       now: datetime | None = None) -> bool:
    """True when a full window of evidence exists.

    The first Friday after opting in has a few days of history and would
    produce six grey cards, which is a bad first impression of a product whose
    whole promise is that the work is already done. So the first send waits.
    """
    now = now or datetime.now()
    snaps = _snapshots(rows)
    if not snaps:
        return False
    return (now - snaps[0]["_ts"]).days >= window_days


def first_send_date(rows: list, window_days: int,
                    now: datetime | None = None):
    """The date a full window will exist, or None when one already does."""
    now = now or datetime.now()
    snaps = _snapshots(rows)
    if not snaps:
        return (now + timedelta(days=window_days)).date()
    if has_enough_history(rows, window_days, now):
        return None
    return (snaps[0]["_ts"] + timedelta(days=window_days)).date()


def main(argv: list | None = None) -> int:
    import config_loader

    config_loader.force_utf8_io()
    parser = argparse.ArgumentParser(description="Compute the weekly scorecard.")
    parser.add_argument("--window-days", type=int, default=None)
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    parser.add_argument("--html", metavar="PATH",
                        help="also render the email to this path")
    args = parser.parse_args(argv)

    report = compute(window_days=args.window_days)
    if args.html:
        import kpi_html

        Path(args.html).write_text(kpi_html.render(report), encoding="utf-8")
        report["html_path"] = args.html

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for line in report["opening"]:
            print(line)
        print()
        for kpi in report["kpis"]:
            if kpi["measured"]:
                print(f"  {kpi['name']}: {kpi['value_text']} "
                      f"({kpi['n_text']})")
            else:
                print(f"  {kpi['name']}: not measured, {kpi['reason']}")
        if args.html:
            print(f"\nEmail written to {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
