#!/usr/bin/env python3
"""The run ledger: one line per unattended run, so cadence is measurable.

Van Gogh had no record that a scheduled job succeeded. `failures.jsonl` records
only failures, and the digest logs record only digests, so "are the automations
firing?" could be answered by guesswork and file mtimes and nothing else. A
briefing that renders fine but never runs looks exactly like a briefing that
runs fine every day: both leave a file on disk with a recent date.

So every unattended entry point appends one row here on every exit path,
success or failure, and the audit reads the rows as evidence.

Written with a single `write` call and wrapped so it never raises: a job that
completed its work must never be reported as failed because its bookkeeping
line could not be written. This mirrors `self_anneal.record_failure`, which
solves the same problem at the other end.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import config_loader

RUNS_NAME = "runs.jsonl"


def runs_path() -> Path:
    return config_loader.logs_dir() / RUNS_NAME


def now_stamp() -> str:
    """One spelling of "now" for every caller, seconds precision."""
    return datetime.now().isoformat(timespec="seconds")


def record_run(job: str, started: str, finished: str, rc: int,
               detail: str = "") -> None:
    """Append one run row. Never raises.

    `job` is the component name in the same dotted shape self_anneal uses
    (`scheduled.<skill>`, `digest.<briefing>`), so a failure row and a run row
    for the same component can be matched by name.

    A failing row carries its failure class, so the watcher can decide what to
    do about it from this file alone. Reading two ledgers to answer one
    question is how they drift apart.
    """
    try:
        record = {
            "job": job,
            "started": started,
            "finished": finished,
            "rc": int(rc),
            "detail": str(detail)[:500],
        }
        if int(rc) != 0:
            import failure_class
            record["error_class"] = failure_class.classify(str(detail))
        path = runs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        _trim(path)
    except Exception:                                           # noqa: BLE001
        pass


# One row per unattended run adds up: at four jobs a day this is about two
# years, and the audit reads a fourteen day window. The docstring at the top
# of this file promised a cap for a while before there was one; the trim lives
# in the same function as the append so the promise cannot drift again.
RUNS_CAP_ROWS = 3000


def _trim(path: Path, cap: int = RUNS_CAP_ROWS) -> None:
    """Keep the newest `cap` rows. Never raises."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= cap:
            return
        path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass


def read_runs(path: Path | None = None, since: date | None = None) -> list:
    """Every run row, oldest first, optionally filtered to `started >= since`.

    A torn last line is normal: the process can die mid-append, and one
    truncated row must not cost the reader every row before it. Unparseable
    lines are skipped rather than raising.
    """
    target = path if path is not None else runs_path()
    out = []
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if since is not None:
            started = str(row.get("started", ""))[:10]
            try:
                if date.fromisoformat(started) < since:
                    continue
            except ValueError:
                continue
        out.append(row)
    return out


def run_dates(rows: list, rc: int | None = 0) -> set:
    """The distinct calendar dates on which these runs started.

    `rc=0` (the default) counts only successes; `rc=None` counts every row.
    Cadence asks "how many different days did this fire", not "how many times",
    because five retries in one morning is not a cadence.
    """
    out = set()
    for row in rows:
        if rc is not None and row.get("rc") != rc:
            continue
        started = str(row.get("started", ""))[:10]
        try:
            out.add(date.fromisoformat(started))
        except ValueError:
            continue
    return out
