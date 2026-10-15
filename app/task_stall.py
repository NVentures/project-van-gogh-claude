#!/usr/bin/env python3
"""Work that stopped moving, as opposed to jobs that stopped running.

`job_watch` answers "did the machinery run". This answers the other half of
the same question: the machinery ran fine every morning, and a commitment has
been sitting in the vault for three weeks anyway. Nothing re-runs that. It is
not a failure, it is a fact about the work, and the only useful response is to
tell the person once and stop.

Three rules shape everything here, and each one exists because its opposite
produced something the user would have learned to skip.

**A stall is measured from the ledger, never from a vibe.** Every finding here
is a line in a file with a date on it, and the finding carries that file. A
"this seems stuck" with no traceable source is an accusation the reader cannot
check.

**Direction travels with the count, always.** "Three items waiting" is not
information: waiting on whom. Every row here says whose move it is, because a
reader who has to work that out themselves will not read the second one.

**Silence is the normal answer.** Most days nothing here is worth a sentence,
and a surface that speaks every day teaches the reader to skip it, including
on the day it matters.

CLI:
    task_stall.py             the stalled work, as JSON
    task_stall.py --days 21   change the threshold
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402

# How long a dated commitment may sit past its trigger before it is a stall
# rather than a late item. The follow-up radar already chases things the day
# they come due, and every briefing shows them; this is for the ones that
# survived all of that, so the bar is deliberately much further out. Two weeks
# past due means fourteen briefings mentioned it and it did not move.
STALL_DAYS = 14

# How long a ticket may sit in a state that is waiting on the user before it
# counts as stalled. A draft written and never read for a week is work the
# system did that was thrown away, which is the most expensive quiet failure
# in the product.
TICKET_STALL_DAYS = 7

# Ticket states where the next move belongs to the user. A ticket the machine
# still owns is not stalled, however old: it is either working or it failed,
# and `failed` is a job-watch question rather than this one.
WAITING_ON_USER = ("proposed", "staged", "delivered")

# What each of those states means in a person's words. The state name is a
# ledger key the reader has never seen, and printing it would be the exact
# data-model vocabulary the written voice rules forbid.
_STATE_WORDS = {
    "proposed": "was suggested and never picked up",
    "staged": "has a draft ready that nobody has read",
    "delivered": "was finished and never rated",
}


def _today() -> date:
    try:
        return datetime.now(config_loader.user_tz()).date()
    except Exception:                                           # noqa: BLE001
        return date.today()


def stalled_commitments(today: date | None = None,
                        days: int = STALL_DAYS) -> list:
    """Dated follow-ups that went past due and stayed there.

    Reuses the radar's own parser rather than reading the ledger again. Two
    definitions of overdue that can disagree is how a system starts nudging
    about work that is finished, and the radar already owns that definition.
    """
    today = today or _today()
    out = []
    try:
        import follow_up_radar

        path = config_loader.follow_ups_memory_path()
        if not path.is_file():
            return []
        items = follow_up_radar.parse_follow_ups(
            path.read_text(encoding="utf-8"), today,
            config_loader.follow_ups_horizon_days())
        for item in follow_up_radar.overdue(items, today):
            late = follow_up_radar.days_late(item, today)
            if late < days:
                continue
            out.append({
                "kind": "commitment",
                "title": item.get("title", "")[:120],
                "days": late,
                # Whose move it is. Everything in this ledger is something the
                # user owes, by construction: the radar's own parser only
                # treats a date as a trigger when the cue says the user acts.
                "whose_move": "yours",
                "what": "has been past its date for "
                        f"{late} days without moving",
                "source": str(path),
            })
    except Exception:                                           # noqa: BLE001
        return out
    out.sort(key=lambda r: -r["days"])
    return out


def stalled_tickets(now: datetime | None = None,
                    days: int = TICKET_STALL_DAYS) -> list:
    """Workbench tickets sitting in a state that is waiting on the user.

    Reads the ledger file, never the server: this runs from a scheduled tick
    with nothing listening on the port, and importing the store is cheap.
    """
    now = now or datetime.now()
    out = []
    try:
        import workbench_store

        data = workbench_store._read()
        for tid, ticket in (data.get("tickets") or {}).items():
            state = ticket.get("state", "")
            if state not in WAITING_ON_USER:
                continue
            history = ticket.get("history") or []
            stamp = (history[-1].get("at") if history
                     else ticket.get("created_at"))
            try:
                since = datetime.fromisoformat(str(stamp))
            except (TypeError, ValueError):
                continue
            age = (now - since).days
            if age < days:
                continue
            out.append({
                "kind": "ticket",
                "title": str(ticket.get("title", ""))[:120],
                "days": age,
                "whose_move": "yours",
                "what": f"{_STATE_WORDS.get(state, 'is waiting on you')} "
                        f"for {age} days",
                "source": str(workbench_store.tickets_path()),
                "ticket_id": tid,
            })
    except Exception:                                           # noqa: BLE001
        return out
    out.sort(key=lambda r: -r["days"])
    return out


def stalled(now: datetime | None = None) -> list:
    """Everything that stopped moving, worst first. Never raises."""
    now = now or datetime.now()
    try:
        rows = stalled_commitments(now.date()) + stalled_tickets(now)
        rows.sort(key=lambda r: -r["days"])
        return rows
    except Exception:                                           # noqa: BLE001
        return []


def summary(now: datetime | None = None, cap: int = 3) -> str:
    """One plain line for the briefing footer, or "" on a quiet day.

    Names the oldest few rather than counting them. A count with no names is
    something the reader cannot act on without going and looking, and the
    whole point of the line is to save them that trip.
    """
    rows = stalled(now)
    if not rows:
        return ""
    shown = rows[:cap]
    parts = [f"{r['title']} {r['what']}" for r in shown]
    if len(parts) == 1:
        body = parts[0]
    elif len(parts) == 2:
        body = f"{parts[0]}, and {parts[1]}"
    else:
        body = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    rest = len(rows) - len(shown)
    tail = (f" {rest} more have been sitting as long." if rest > 1
            else " One more has been sitting as long." if rest == 1 else "")
    # Every sentence here is about work waiting on the reader, so it says so
    # once at the end rather than on every clause.
    return f"Nothing is chasing these: {body}.{tail} They are all waiting on you."


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Work that stopped moving (as opposed to jobs that stopped running).")
    parser.add_argument("--days", type=int, default=None,
                        help=f"days past due before a commitment counts (default {STALL_DAYS})")
    parser.add_argument("--now", default="",
                        help="grade against this moment (ISO) instead of now")
    args = parser.parse_args(argv)

    now = datetime.now()
    if args.now:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            print(f"ERROR: --now is not a date I can read: {args.now}",
                  file=sys.stderr)
            return 1

    rows = (stalled_commitments(now.date(), args.days) + stalled_tickets(now)
            if args.days is not None else stalled(now))
    print(json.dumps({"stalled": rows, "line": summary(now)},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
