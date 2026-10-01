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
import os
import re
from datetime import date, datetime, timedelta
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
    # The run is over on every path that reaches here, so its marker goes
    # whether or not the row above could be written.
    clear_started(job)


# ── A run that has started and not finished ──────────────────────────────────
#
# A row is written when a run ends, so until then the ledger cannot tell a job
# that is working from one that never fired. The watcher read that silence as
# "missing" and restarted the job, and a restart goes through the scheduler
# with a kill, so a briefing that needed forty minutes was killed at twenty
# five and started again. A render alone is allowed thirty.
#
# So each clock job leaves a marker when it starts and `record_run` removes it.
# The marker lives in the state directory, never the vault: it holds a process
# id, which means nothing on any other machine the vault syncs to.

# A marker older than this is not believed even if its process id is alive.
# Every long step inside a job has its own timeout, so nothing legitimately
# runs this long while the machine is awake; the cap is what stops a process
# id reused after a reboot from holding a job forever. It is generous because
# a laptop that sleeps mid-run adds the whole night to the wall clock.
INFLIGHT_MAX_H = 12


def _marker_path(job: str):
    import user_state
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job))
    return user_state.state_dir() / "inflight" / f"{safe}.json"


def mark_started(job: str) -> None:
    """Say this job is running now. Never raises."""
    try:
        path = _marker_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"job": job, "started": now_stamp(),
                                    "pid": os.getpid()}), encoding="utf-8")
    except Exception:                                           # noqa: BLE001
        pass


def clear_started(job: str) -> None:
    """Forget the marker. Never raises; a missing one is the normal case."""
    try:
        _marker_path(job).unlink(missing_ok=True)
    except Exception:                                           # noqa: BLE001
        pass


def in_flight(job: str, now: datetime | None = None):
    """The marker for a run of this job that is still going, or None.

    Three things must all hold: the marker is readable, it is younger than
    `INFLIGHT_MAX_H`, and its process still exists. A marker whose process is
    gone belongs to a run that died without writing its row, which is exactly
    the case the watcher should restart, so it answers None.
    """
    try:
        marker = json.loads(_marker_path(job).read_text(encoding="utf-8"))
        started = datetime.fromisoformat(str(marker.get("started", "")))
        if (now or datetime.now()) - started > timedelta(hours=INFLIGHT_MAX_H):
            return None
        from platform_compat import pid_alive
        if not pid_alive(marker.get("pid")):
            return None
        return marker
    except Exception:                                           # noqa: BLE001
        return None


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


# ── Waiting for what must have happened first ────────────────────────────────
#
# macOS launchd coalesces every missed StartCalendarInterval run and fires them
# on wake; Windows tasks carry -StartWhenAvailable for the same effect. That is
# the behaviour you want: a 07:00 briefing on a laptop opened at 08:30 does run
# at 08:30. But launchd coalesces to ONE run per job and preserves no order
# between jobs, so a machine off from 04:00 to 08:30 on a Monday fires the two
# ingests, Week and Morning Coffee simultaneously, and Coffee can render
# against a vault nothing has filed yet.
#
# The watcher cannot catch this. A Coffee that ran early exits 0 and writes a
# file, so it grades as delivered while being thin. Nothing downstream can tell
# a thin briefing from a quiet morning.
#
# So a dependent job waits for its dependencies' run rows rather than for a
# guessed number of minutes. On a normal morning every dependency ran hours
# earlier and the first check passes immediately; the waiting only happens on a
# catch-up morning, which is the case it exists for.

# How long a dependent job may wait before giving up and running anyway. A
# briefing that arrives late against a half-filed vault is worth more than one
# that never arrives, so this always ends in a run.
DEPENDENCY_TIMEOUT_S = 20 * 60

# How often to re-read the ledger while waiting. The file is small and the read
# is cheap; thirty seconds is fine-grained enough that a briefing does not sit
# idle after its dependency finishes.
DEPENDENCY_POLL_S = 30

# How far back to look for "has this machine ever run anything". A ledger with
# nothing in it over this window is a fresh install, not a late wake.
LEDGER_HISTORY_DAYS = 30


def succeeded_today(job: str, rows: list | None = None,
                    today: date | None = None) -> bool:
    """Whether this job has a successful run row dated today."""
    today = today or date.today()
    rows = rows if rows is not None else read_runs(since=today)
    for row in rows:
        if row.get("job") != job or row.get("rc") != 0:
            continue
        started = str(row.get("started", ""))[:10]
        try:
            if date.fromisoformat(started) == today:
                return True
        except ValueError:
            continue
    return False


def _due_today(job, today: date) -> bool:
    """Whether this job owes a run today, and has ever run at all.

    Two separate reasons not to wait, both of which look the same from inside a
    single briefing:

    * the job is not due today (Week runs on Mondays; on a Thursday there is
      nothing to wait for), and
    * the job has never run, on any day. That is a machine that was installed
      minutes ago, not a machine that woke up late, and holding the first
      briefing for twenty minutes would be the user's first impression of the
      product.
    """
    try:
        cadence = job.resolved_cadence()
    except Exception:                                           # noqa: BLE001
        return False
    if cadence.kind != "clock":
        return False
    if today.weekday() not in cadence.watch_days:
        return False
    return bool(read_runs(since=today - timedelta(days=LEDGER_HISTORY_DAYS)))


# The scheduler sets this on every job it launches. The dependency wait is for
# a job the OS fired, which is the only context where a catch-up pile-up can
# happen; a person running a briefing by hand wants it now, and a test must
# never block. Absent, the gate is a no-op.
SCHEDULED_ENV = "VAN_GOGH_SCHEDULED"


def wait_for_dependencies(job_key: str, sleep=None, now=None,
                          scheduled: bool | None = None) -> dict:
    """Block until this job's dependencies have run today, or until timeout.

    Returns `{waited_s, satisfied, missing}` and never raises: a gate that can
    wedge a briefing is worse than no gate, so every failure here falls through
    to running.

    A dependency that is not installed, or that the user has turned off, is not
    a dependency. Waiting on a job that will never run would hold every
    briefing for the full timeout on a machine that simply does not have it.
    """
    import os
    import time as _time

    if scheduled is None:
        scheduled = os.environ.get(SCHEDULED_ENV) == "1"
    if not scheduled:
        # A hand-run briefing, or a test. Neither waits: the pile-up this
        # guards against is a property of the OS firing several jobs at once.
        return {"waited_s": 0, "satisfied": True, "missing": []}

    sleep = sleep or _time.sleep
    started_at = _time.monotonic()
    try:
        import job_registry
        job = job_registry.by_key(job_key)
        if job is None or not job.depends_on:
            return {"waited_s": 0, "satisfied": True, "missing": []}

        # Only dependencies that are actually installed and switched on.
        wanted = []
        for key in job.depends_on:
            dep = job_registry.by_key(key)
            if dep is None or not dep.is_enabled():
                continue
            wanted.append(dep)
        if not wanted:
            return {"waited_s": 0, "satisfied": True, "missing": []}

        today = (now or datetime.now()).date()

        # Only wait for a job that is DUE today and has not run yet. A job due
        # later (Week is Monday-only; nothing is owed on a Thursday) is not
        # something to hold a briefing for, and a job with no history at all is
        # usually a fresh install rather than a late morning -- waiting twenty
        # minutes on the first run of a machine that has never filed anything
        # would make the product feel broken on day one.
        wanted = [d for d in wanted if _due_today(d, today)]
        if not wanted:
            return {"waited_s": 0, "satisfied": True, "missing": []}

        deadline = started_at + DEPENDENCY_TIMEOUT_S
        while True:
            rows = read_runs(since=today)
            missing = [d.key for d in wanted
                       if not succeeded_today(d.ledger_job, rows, today)]
            if not missing:
                return {"waited_s": int(_time.monotonic() - started_at),
                        "satisfied": True, "missing": []}
            if _time.monotonic() >= deadline:
                # Run anyway, and say so. A briefing that never arrives is a
                # worse failure than one rendered against a stale vault.
                return {"waited_s": int(_time.monotonic() - started_at),
                        "satisfied": False, "missing": missing}
            sleep(min(DEPENDENCY_POLL_S, max(1, deadline - _time.monotonic())))
    except Exception:                                           # noqa: BLE001
        return {"waited_s": int(_time.monotonic() - started_at),
                "satisfied": True, "missing": []}
