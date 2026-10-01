#!/usr/bin/env python3
"""The watcher: notices when a scheduled job stopped running, and re-runs it.

Van Gogh could already retry a briefing twice inside one run, write down that
it failed, and mail a content-free notice about it. What it could not do was
notice. A job that burned both attempts, or never fired at all, waited for its
next calendar slot: a day for a daily digest, a week for the Monday one. The
only thing that ever compared "what should have run" against "what did" was
the vault audit, and nothing schedules the vault audit.

So this runs every thirty minutes and does one cheap, stateless pass:

    evaluate   for each scheduled job, when was it last due, did it run, and
               did it leave the file it exists to leave?
    act        re-run what failed or never fired, with a cap, holding the
               classes of failure a re-run cannot fix
    record     one row per tick, so the audit can grade cadence from evidence
               and the next briefing can say what happened

Three ideas carry the whole design, and all three are load-bearing.

**Deliverables beat exit codes, in both directions.** A run that exited zero
and left a stale file did not deliver. A file written this morning with no run
record beside it did deliver, and re-running it would be worse than useless.
Grading the artifact rather than the exit status is what makes a hand-run,
a coalesced launchd wake, and a torn ledger row all read correctly.

**A class decides the response, not a counter.** A usage cap is a clock: hold
until it resets, and spend no attempts waiting. A stale CLI is a fault to fix
once: hold until the binary actually clears the bar the error named. A safety
classifier outage flaps: defer a tick, because only a successful run proves
recovery. An expired login cannot be fixed from here, so it is held until a
real check shows the reader has signed in again.

**A running job is left alone.** A row is written when a run ends, so
silence used to read as "never fired", and the restart that followed killed a
briefing that was merely slow. Each clock job now leaves a marker while it
works, and a job with a live marker is graded `running` and never restarted.

**It fails open, always.** Every path swallows its own errors. A watcher that
can wedge a briefing is worse than no watcher, so the worst thing this is
allowed to do is nothing.

When the watching is done it hands over to the advisor (`advisor.tick`),
the layer above this one: this keeps the jobs running, that asks what is
worth doing and does it on a yes. The hand-over comes after the tick is
graded and recorded, so a slow look can never cost a check.

It never announces itself. Its own liveness reaches the user through the
briefing footer, which says so when the last tick is too old, and through the
vault audit. That is why there is no second process watching this one.

CLI:
    job_watch.py                      one tick: grade, act, record
    job_watch.py --report-only        grade and print, change nothing
    job_watch.py --now 2026-09-09T14:00   grade against a given moment
    job_watch.py --json               machine-readable rows
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                             # noqa: E402
import failure_class                                             # noqa: E402
import run_ledger                                                # noqa: E402
import scheduler_setup                                           # noqa: E402
from platform_compat import NO_WINDOW                            # noqa: E402

# A slot younger than this is still in flight. launchd and Task Scheduler both
# fire on their own schedule, the job takes minutes to render, and grading it
# missing while it is still working would re-run it on top of itself.
GRACE_MIN = 25

# How long a tick may be missing before the briefing says so. Two hours is
# four missed ticks: enough that a single skipped wake is not news, few
# enough that a dead watcher is caught the next morning.
STALE_TICK_H = 2

# How many of its own intervals a poller may miss before it is graded dead.
# A poller owes no artifact on a quiet day, so silence is its only symptom,
# and one skipped wake on a laptop that slept is not a symptom at all. Four
# is an hour for the prep poller: long enough to be real, short enough that a
# dead poller is caught before the next morning's calls.
POLLER_MISSED_TICKS = 4

# How far back to read the ledgers. Everything graded here is a slot from the
# last few days; a longer window costs a bigger read every half hour and
# answers no question this asks.
LOOKBACK_DAYS = 10

# Rows kept in the watcher's own log. At one tick per half hour this is about
# three months, which is the window the audit reads over.
LOG_CAP_ROWS = 4000

WATCH_LOG_NAME = "job_watch.jsonl"
STATE_NAME = "job-watch-state.json"

# Statuses that call for a re-run. `held` and `stuck` are deliberately absent:
# both mean the watcher has already decided not to act.
ACTIONABLE = frozenset({"missing", "failed", "no_deliverable"})

_WEEKDAY_INDEX = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}


# ── Where things live ────────────────────────────────────────────────────────

def watch_log_path() -> Path:
    return config_loader.logs_dir() / WATCH_LOG_NAME


def state_path() -> Path:
    import user_state
    return user_state.state_dir() / STATE_NAME


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, default=str),
                        encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass  # fails open: state we cannot save costs a repeated kick, not a run


# ── The roster ───────────────────────────────────────────────────────────────

def _deliverable_of(job):
    """The file this job is graded by, or None when it cannot be resolved.

    Resolving a deliverable must never cost a job its grading. A briefing that
    has never run has no file to point at yet and the resolver raises saying
    so -- which is precisely the state the watcher exists to notice, so
    letting that exception drop the row would make the watcher blind exactly
    when it is needed. A row with `deliverable: None` is still graded on its
    run record.
    """
    if not job.deliverable:
        return None
    try:
        return job.deliverable()
    except Exception:                                            # noqa: BLE001
        return None


def roster() -> list:
    """Every job this machine is supposed to run, derived, never hand-kept.

    One source: `job_registry.jobs()`, the same table the scheduler installs
    from. A hand-maintained list drifts the first time either changes, and the
    drift is invisible, because a job missing from the roster is a job nobody
    grades. That is not hypothetical -- the Note tick was installed by the
    scheduler and graded by nothing, because it was never added here.

    Each entry is `{name, title, kind, label, task, ledger_job, days, hour,
    minute, deliverable}`, plus `interval_min` for a poller. `days` is a set of
    weekday indexes, Monday zero.

    Two shapes, and the difference is load-bearing. A **clock job** is graded
    by its slot: it was due at a time, did it run, did it deliver. A **poller**
    carries `interval_min` instead of an hour and is graded on liveness alone,
    because on a day with no calls the prep poller correctly does nothing and
    an absent artifact is the healthy case.

    Per-row `try`, so a briefing whose config cannot be read costs its own
    grading and never the whole roster. A raising roster would take the
    watcher down with it, and the watcher's whole contract is to fail open.
    """
    import job_registry

    jobs = []
    for job in job_registry.jobs():
        # The watcher itself. It must not grade and kick itself, and its own
        # liveness already reaches the user through the briefing footer's
        # stale-tick branch, which is why there is no second process watching
        # this one.
        if not job.graded:
            continue
        try:
            if not job.is_enabled():
                continue
            cadence = job.resolved_cadence()
            row = {
                "name": job.key,
                "title": job.title,
                "kind": job.kind,
                "label": job.mac_label,
                "task": job.win_task,
                "ledger_job": job.ledger_job,
                "days": cadence.watch_days,
                "hour": None if cadence.kind == "interval" else cadence.hour,
                "minute": None if cadence.kind == "interval" else cadence.minute,
                "deliverable": _deliverable_of(job),
            }
            if cadence.kind == "interval":
                row["interval_min"] = cadence.interval_min
            jobs.append(row)
        except Exception:                                        # noqa: BLE001
            continue
    return jobs


# What each job is called when a person is being told about it. A briefing
# footer that says "digest-morning-coffee" is telling the reader a config key,
# which is a string they have never seen and cannot act on. The digest names
# come from digest_send, which already had to solve this for email subjects;
# only the two background skills need their own entry.
# Lowercase on purpose: these appear mid-sentence in the recovery line, and a
# capital there ("... and Syncing your project notes ...") is the tell that a
# name was pasted into prose. `_said` capitalizes when one leads a sentence.
_SKILL_TITLES = {
    "meeting-ingest": "the filing of your meeting notes",
    "ingest-workspace": "the sync of your project notes",
}


def _briefing_title(briefing: str) -> str:
    try:
        import digest_send
        name = digest_send.BRIEFING_NAMES.get(briefing, "")
    except Exception:                                            # noqa: BLE001
        name = ""
    return f"your {name} briefing" if name else f"your {briefing} briefing"


def _briefing_file(briefing: str):
    """The rendered file a digest sends, or None when it cannot be resolved."""
    try:
        import digest_send
        return digest_send.briefing_md_path(briefing)
    except Exception:                                            # noqa: BLE001
        return None


def latest_slot(job: dict, now: datetime):
    """The most recent moment this job was due, or None if it never has been.

    A poller has no due moment at all: `hour` is None, and asking "was it late
    for 4:30" of something that runs every fifteen minutes is the wrong
    question. Those are graded by `_poller_row` instead.

    Walks back a day at a time rather than computing it, because a job can run
    on any subset of weekdays and the arithmetic for "the previous Friday when
    today is Tuesday" is where an off-by-one hides. Ten days is further back
    than anything here is graded.
    """
    if job.get("hour") is None:
        return None
    for back in range(0, LOOKBACK_DAYS + 1):
        day = (now - timedelta(days=back)).date()
        if day.weekday() not in job["days"]:
            continue
        slot = datetime.combine(day, datetime.min.time()).replace(
            hour=job["hour"], minute=job["minute"])
        if slot <= now:
            return slot
    return None


# ── Grading ──────────────────────────────────────────────────────────────────

def _runs_for(runs: list, ledger_job: str, slot: datetime) -> list:
    """Ledger rows for this job that started at or after its due slot."""
    out = []
    for row in runs:
        if row.get("job") != ledger_job:
            continue
        try:
            started = datetime.fromisoformat(str(row.get("started", "")))
        except ValueError:
            continue
        if started >= slot:
            out.append(row)
    return out


def _delivered(job: dict, slot: datetime):
    """Did the artifact this job exists to write land on or after its slot?

    Three answers, not two. True and False mean what they say; **None means
    this job declares no artifact, so the question cannot be asked of it.**

    The third answer is the whole point. A missing deliverable is not evidence
    of a stale one, so a job with nothing declared must never be graded as
    failing this test. But it must not be graded as passing it either: reading
    "nothing declared" as True let a job that had never run once be called
    delivered, on the strength of a file that does not exist.
    """
    target = job.get("deliverable")
    if not target:
        return None
    try:
        path = Path(target)
        if not path.is_file():
            return False
        written = datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, ValueError, OverflowError):
        return False
    return written.date() >= slot.date()


def _class_of(runs: list) -> str:
    """The failure class of the most recent failed run in this slot."""
    for row in reversed(runs):
        if row.get("rc") in (0, None):
            continue
        detail = str(row.get("detail", ""))
        stored = str(row.get("error_class", "") or "")
        # A row written by an older build can carry a class this build no
        # longer agrees with: a scope rejected on refresh was stored as auth
        # and is now known to be cured by a re-run. The text decides.
        if stored and not failure_class.REFRESH_SCOPE_RE.search(detail):
            return stored
        return failure_class.classify(detail)
    return ""


def _poller_row(job: dict, runs: list, now: datetime) -> dict:
    """Grade a job that runs on an interval rather than at a time.

    A clock job is graded by asking "its slot passed, did it deliver". Neither
    half of that question applies here. A poller has no slot, and on a day
    with no calls it correctly does nothing at all, so an absent artifact is
    the healthy case rather than the failing one.

    What can be asked is whether it is still alive: the ledger row it writes
    every tick. Silence for several of its own intervals means the job stopped,
    and that is the failure this exists to catch, because a prep poller that
    dies quietly costs the user every prep from then on and says nothing.

    The slot key is the calendar day, not a moment. A poller has no slot to
    key on, and every candidate derived from the ledger moves: keyed on the
    last run seen, or on that plus the window, a single new stale row shifts
    the key, orphans the kick count attached to the old one, and resets the
    budget to zero. The cap would then never be reached, so the job would be
    kicked every half hour forever and the mechanic would never be called.
    One key per job per day is stable while the job stays dead, and the state
    pruner already forgets it after the lookback.
    """
    interval = max(1, int(job.get("interval_min") or 15))
    window = timedelta(minutes=interval * POLLER_MISSED_TICKS)
    rows = [r for r in runs if r.get("job") == job["ledger_job"]]
    stamps = []
    for row in rows:
        try:
            stamps.append(datetime.fromisoformat(str(row.get("started", ""))))
        except ValueError:
            continue
    last = max(stamps) if stamps else None

    out = {"job": job["name"], "title": job.get("title", job["name"]),
           "kind": job["kind"], "slot": None, "status": "ok", "detail": "",
           "kicks": 0, "error_class": "", "label": job["label"],
           "task": job["task"], "_job": job}

    if last is None:
        # Never once, which is not the same as stopped. A poller installed
        # minutes ago has not had a turn yet, and the ledger cannot tell those
        # apart, so this reports rather than kicks: kicking a job that has
        # simply not fired yet spends the budget on nothing.
        out["status"] = "no_history"
        out["detail"] = "no record that it has run yet"
        return out

    failed = [r for r in rows if r.get("rc") not in (0, None)]
    if now - last > window:
        out["status"] = "missing"
        out["slot"] = last + window
        hours = (now - last).total_seconds() / 3600
        out["detail"] = (f"last ran {hours:.0f} hours ago, and it is supposed "
                         f"to run every {interval} minutes")
    elif failed and max(
            (datetime.fromisoformat(str(r.get("started"))) for r in failed
             if _parsable(r.get("started"))), default=datetime.min) >= last:
        out["status"] = "failed"
        out["slot"] = last
        out["error_class"] = _class_of(rows)
        out["detail"] = str(rows[-1].get("detail", ""))[:200]

    if out["slot"] is not None:
        # Midnight today, so the key is one per job per day. `_prune_state`
        # parses the stamp after the "@", so this must stay an ISO datetime.
        day = datetime.combine(now.date(), datetime.min.time())
        out["_skey"] = f"{job['name']}@{day.isoformat(timespec='minutes')}"
    return out


def _parsable(value) -> bool:
    try:
        datetime.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def evaluate(now: datetime | None = None, state: dict | None = None,
             runs: list | None = None, job_rows: list | None = None,
             in_flight=None) -> list:
    """Grade every job in the roster. Pure read: changes nothing.

    Every argument is injectable so the whole grader can be tested against a
    planted moment, a planted ledger and a planted scheduler, with no clock
    and no launchd anywhere in the test.
    """
    now = now or datetime.now()
    state = state if state is not None else _read_state()
    if runs is None:
        since = (now - timedelta(days=LOOKBACK_DAYS)).date()
        runs = run_ledger.read_runs(since=since)
    if job_rows is None:
        try:
            job_rows = scheduler_setup.job_states(sys.platform)
        except Exception:                                        # noqa: BLE001
            job_rows = []
    installed = {row.get("label", ""): row.get("state", "")
                 for row in job_rows}
    if in_flight is None:
        in_flight = run_ledger.in_flight

    rows = []
    for job in roster():
        row = {"job": job["name"], "title": job.get("title", job["name"]),
               "kind": job["kind"], "slot": None,
               "status": "ok", "detail": "", "kicks": 0, "error_class": "",
               "label": job["label"], "task": job["task"], "_job": job}
        state_word = installed.get(job["label"], "")
        if state_word in ("not installed", "on disk, not loaded", ""):
            # Nothing holds this job. Re-running it once would produce one
            # briefing and change nothing about tomorrow, so this is reported
            # and never kicked: the fix is to install the scheduler.
            #
            # `_absent` is set only when the scheduler itself said so. An
            # empty word means the scheduler could not be read at all, and a
            # question that went unanswered is not evidence the job is gone,
            # so that case stays silent rather than telling the reader to
            # reinstall something that may be perfectly fine. A job written
            # to disk and not loaded is as absent as one never written: the
            # restart below goes through the scheduler, which refuses it.
            row["status"] = "not_scheduled"
            row["detail"] = "no scheduled job is installed for this"
            row["_absent"] = bool(state_word)
            rows.append(row)
            continue

        if job.get("interval_min"):
            prow = _poller_row(job, runs, now)
            skey = prow.get("_skey")
            if skey:
                prow["kicks"] = int(
                    state.get("slots", {}).get(skey, {}).get("kicks", 0) or 0)
            rows.append(prow)
            continue

        slot = latest_slot(job, now)
        row["slot"] = slot
        if slot is None:
            rows.append(row)
            continue

        skey = f"{job['name']}@{slot.isoformat(timespec='minutes')}"
        srec = state.get("slots", {}).get(skey, {})
        row["kicks"] = int(srec.get("kicks", 0) or 0)
        row["_skey"] = skey

        slot_runs = _runs_for(runs, job["ledger_job"], slot)
        ok_runs = [r for r in slot_runs if r.get("rc") == 0]
        delivered = _delivered(job, slot)

        flying = in_flight(job["ledger_job"], now) if in_flight else None

        if ok_runs and delivered is not False:
            row["status"] = "ok"
        elif flying:
            # Still working. This sits above every failing grade on purpose:
            # a restart goes through the scheduler with a kill, so restarting
            # here would end the very run that is about to deliver. It covers
            # a first run that is simply slow, and a re-run in flight behind
            # an earlier failure, which would otherwise be killed by the next
            # tick reading that failure again.
            row["status"] = "running"
            row["detail"] = ("started at "
                             f"{str(flying.get('started', ''))[11:16]} "
                             "and is still running")
        elif ok_runs and delivered is False:
            # The vacuity guard: an exit code of zero that left yesterday's
            # file on disk is a job that ran and did not deliver.
            row["status"] = "no_deliverable"
            row["detail"] = "it ran but the file it writes is not from today"
        elif delivered is True and not slot_runs:
            # A fresh file with no run row: a hand run, or a row that was
            # never written. The work is done either way. This needs
            # `is True`, never a plain truth test: a job with no declared
            # artifact answers None here, and reading that as "delivered"
            # would grade a job that has never run as healthy.
            row["status"] = "ok"
            row["detail"] = "the file is current"
        elif slot_runs:
            row["status"] = "failed"
            row["error_class"] = _class_of(slot_runs)
            row["detail"] = str(slot_runs[-1].get("detail", ""))[:200]
        elif (now - slot) < timedelta(minutes=GRACE_MIN):
            row["status"] = "pending"
        else:
            row["status"] = "missing"
            row["detail"] = (f"due {slot.strftime('%a %H:%M')}, "
                             "no record that it ran")
        rows.append(row)
    return rows


# ── Acting ───────────────────────────────────────────────────────────────────

def kick_cap() -> int:
    try:
        return max(1, int(config_loader.job_watch_kick_cap()))
    except Exception:                                            # noqa: BLE001
        return 3


# Connector names as the CLI spells them, mapped to what a person calls the
# service. The detail text carries the machine spelling
# (`claude_ai_Intuit_QuickBooks`); telling a reader to reconnect that is
# telling them a string they have never seen.
_CONNECTOR_NAMES = {
    "quickbooks": "QuickBooks",
    "google_drive": "Google Drive",
    "drive": "Google Drive",
    "slack": "Slack",
    "hubspot": "HubSpot",
}


def _connector_name(row: dict) -> str:
    """Which service a connector failure is about, in the reader's words."""
    detail = (row.get("detail") or "").lower()
    for key, pretty in _CONNECTOR_NAMES.items():
        if key in detail:
            return pretty
    return "the connector it needs"


_AUTH_LABEL_RES = (
    re.compile(r"Token refresh failed for ([^:\n]+):"),
    re.compile(r": ([^:\n]+?) needs you to sign in again"),
    re.compile(r"auth_bootstrap\.py --provider \w+ --label (\S+)"),
)
_MICROSOFT_MARKERS = ("aadsts", "microsoft", "msal", "graph.",
                      "token refresh failed for", "ms_graph_refresh_token")
_GOOGLE_MARKERS = ("google", "gmail", "refresherror",
                   "access_token_scope_insufficient", "insufficientpermissions",
                   "insufficient authentication")


def _auth_target(row: dict) -> tuple:
    """(provider, label) an auth failure names. Either may be "".

    Provider is "microsoft", "google", or "" for Claude's own login. One
    reading of the detail text, shared by the sentence the reader sees and
    the check for whether they have since signed in, so the two can never
    disagree about which account is meant.
    """
    detail = str(row.get("detail") or "")
    low = detail.lower()
    if any(m in low for m in _MICROSOFT_MARKERS):
        provider = "microsoft"
    elif any(m in low for m in _GOOGLE_MARKERS):
        provider = "google"
    else:
        return "", ""
    for rx in _AUTH_LABEL_RES:
        m = rx.search(detail)
        if m:
            return provider, m.group(1).strip()
    return provider, ""


def _auth_subject(row: dict) -> str:
    """Whose sign-in an auth failure is about, or "" when it is Claude's own.

    "Sign in to Claude again" is the right sentence for an expired Claude
    login and the wrong one for a mail account: it sends the reader to do a
    thing that does not fix anything, the same mistake the connector branch
    below was written to avoid.
    """
    provider, label = _auth_target(row)
    if not provider:
        return ""
    who = f"your {'Microsoft' if provider == 'microsoft' else 'Google'} account"
    return f"{who} ({label})" if label else who


# A failure about what a login is ALLOWED to do, as opposed to whether it
# works. The token still refreshes, so a refresh proves nothing here.
_SCOPE_MARKERS = ("access_token_scope_insufficient", "insufficientpermissions",
                  "insufficient authentication")

# One answer per account per tick. Two briefings held on the same mailbox
# would otherwise refresh the same token twice in the same second.
_PROBE_CACHE: dict = {}


def _labels_for(provider: str, label: str) -> list:
    if label:
        return [label]
    accounts = (config_loader.microsoft_accounts() if provider == "microsoft"
                else config_loader.google_accounts())
    return [a["label"] for a in accounts]


def _token_fingerprint(provider: str, labels: list) -> str:
    """A hash of the stored sign-in for these accounts. Never the token."""
    import hashlib
    import google_client
    import microsoft_client
    import user_state
    user_state.load_env()
    read = (microsoft_client._refresh_token_for if provider == "microsoft"
            else google_client._refresh_token_for)
    parts = []
    for label in labels:
        try:
            parts.append(read(label))
        except Exception:                                        # noqa: BLE001
            parts.append("")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _refresh_works(provider: str, label: str) -> bool:
    """Make the one real call that proves a sign-in works: refresh it."""
    import user_state
    user_state.load_env()
    if provider == "microsoft":
        import microsoft_client
        microsoft_client.MicrosoftClient(label)._token()
        return True
    import google_auth_httplib2
    import httplib2
    import google_client
    from certs import resolve_ca_bundle
    # The same CA bundle every other Google call uses, so a machine behind a
    # proxy does not read its own certificate error as a dead sign-in.
    creds = google_client._credentials(label)
    creds.refresh(google_auth_httplib2.Request(
        httplib2.Http(ca_certs=resolve_ca_bundle())))
    return bool(creds.valid)


def _claude_signed_in() -> bool:
    from platform_compat import claude_bin
    result = subprocess.run([claude_bin(), "auth", "status", "--json"],
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=60, creationflags=NO_WINDOW)
    return bool(json.loads(result.stdout).get("loggedIn"))


def _auth_recovered(row: dict, srec: dict) -> bool:
    """Has the reader signed in again since this job failed on its login?

    The hold used to be unconditional, and the sentence beside it promised
    the job "restarts on its own once you do". Nothing checked, so a briefing
    that failed at seven stayed lost all day however quickly the reader
    signed back in, and the promise was simply untrue.

    Two questions, because an auth failure is one of two different things.
    A login that stopped working is proved fixed by the one real call that
    uses it, a refresh. A login that works but was never allowed to do what
    the job needs still refreshes perfectly, so there the only evidence is
    that the stored sign-in itself has changed since the hold began.

    Never raises, and every doubt answers False: holding a job that could
    have run costs one briefing until its next slot, which is what happened
    before this existed. Releasing one that cannot run spends its retries.
    """
    try:
        provider, label = _auth_target(row)
        if not provider:
            key = ("claude", "")
            if key not in _PROBE_CACHE:
                _PROBE_CACHE[key] = _claude_signed_in()
            return _PROBE_CACHE[key]

        labels = _labels_for(provider, label)
        if not labels:
            return False
        low = str(row.get("detail") or "").lower()
        if any(m in low for m in _SCOPE_MARKERS):
            now_fp = _token_fingerprint(provider, labels)
            was = srec.get("auth_fingerprint")
            if not was:
                srec["auth_fingerprint"] = now_fp
                return False
            return now_fp != was

        for one in labels:
            key = (provider, one)
            if key not in _PROBE_CACHE:
                try:
                    _PROBE_CACHE[key] = _refresh_works(provider, one)
                except Exception:                                # noqa: BLE001
                    _PROBE_CACHE[key] = False
            if not _PROBE_CACHE[key]:
                return False
        return True
    except Exception:                                            # noqa: BLE001
        return False


def actionable(row: dict, state: dict, now: datetime) -> tuple:
    """(should we re-run it, why not). The entire hold policy lives here.

    The reason is written for the person who will read it in their briefing,
    and every one of them **names who has to do something**. That is not a
    style preference. A cold reader given "waiting for the usage cap to reset"
    beside "waiting for you to sign in again" cannot tell which of them is
    their problem, because the two sentences have the same shape and only the
    word after "for" separates "ignore this" from "act now".

    So the two cases are built differently on purpose. Something the system is
    handling ends in "Nothing for you to do." Something the reader must fix
    starts with what they need to do. The `_NEEDS_YOU` sentences are exactly
    the second kind, and `summary` uses that split rather than re-deciding it.
    """
    if row["status"] not in ACTIONABLE:
        return False, ""
    srec = state.setdefault("slots", {}).setdefault(row["_skey"], {})

    if row["kicks"] >= kick_cap():
        return False, (f"was restarted {_count(row['kicks'])} times today and "
                       "still did not work, so it has stopped trying for "
                       "today. " + _WHERE_TO_LOOK)

    klass = row.get("error_class", "")

    if klass == "quota":
        # A cap is a clock, not a fault. Hold past the reset the message
        # named, and spend no attempts on the wait: a held slot keeps its
        # full retry budget for after the reset, which is the only time an
        # attempt can succeed.
        hold = srec.get("quota_until")
        if not hold:
            reset = failure_class.quota_reset_at(row.get("detail", ""), now=now)
            hold_at = (reset + timedelta(minutes=5) if reset
                       else now + timedelta(hours=1))
            hold = srec["quota_until"] = hold_at.isoformat()
        try:
            hold_at = datetime.fromisoformat(hold)
        except (TypeError, ValueError):
            hold_at = now
        if now < hold_at:
            from platform_compat import fmt_hour_minute
            return False, ("stopped because you've hit your Claude usage "
                           "limit. It restarts itself after "
                           f"{fmt_hour_minute(hold_at)} today, when the limit "
                           "resets. Nothing for you to do.")
        srec.pop("quota_until", None)

    if klass == "auth" and not _auth_recovered(row, srec):
        who = _auth_subject(row)
        if who:
            return False, (f"needs you to sign in to {who} again, through "
                           "/van-gogh:add-account. It restarts on its own "
                           "once you do, and cannot start until then.")
        return False, ("needs you to sign in to Claude again. It restarts "
                       "on its own once you do, and cannot start until then.")

    if klass == "connector":
        # Anthropic holds the connector's token and only a browser can renew
        # it, so this names the service rather than telling the reader to sign
        # in to Claude, which is a different act and would not fix anything.
        return False, ("needs you to reconnect " + _connector_name(row) +
                       " in Claude's connector settings. It restarts on its "
                       "own once you do, and cannot start until then.")

    if klass == "version":
        # Retrying cannot make a binary newer. Hold until the installed CLI
        # actually clears the bar the error named; claude_update already
        # forces the update, so this releases itself once it lands.
        try:
            import claude_update
            need = failure_class.version_tuple(
                failure_class.required_cli_version(row.get("detail", "")))
            have = failure_class.version_tuple(claude_update.cli_version())
        except Exception:                                        # noqa: BLE001
            need = have = ()
        if need and (not have or have < need):
            return False, ("is waiting for Claude to finish updating itself, "
                           "and restarts once that is done. Nothing for you "
                           "to do.")

    if klass == "classifier":
        # The outage flaps. One tick of patience costs half an hour and
        # avoids spending the budget on a wall that is still up.
        if not srec.get("deferred_at"):
            srec["deferred_at"] = now.isoformat()
            return False, ("could not reach Claude when it tried, so it "
                           "restarts shortly. Nothing for you to do.")

    # Stuck: the same failure twice is not a blip, and a third identical run
    # will not learn anything the first two did not. An empty detail is never
    # stuck, because "two runs failed with nothing recorded" is not evidence
    # that they failed the same way.
    signature = (row.get("detail") or "")[:120]
    if signature and srec.get("last_signature") == signature and row["kicks"] >= 2:
        return False, ("failed twice with the same error, so restarting it "
                       "again will not help and it has stopped trying for "
                       "today. " + _WHERE_TO_LOOK)

    return True, ""


def kick(row: dict, platform: str = sys.platform) -> bool:
    """Re-run one job through the OS scheduler. Never raises.

    Going through the scheduler rather than spawning the work directly means
    the job runs exactly as it does at its normal hour: same environment, same
    logging, same overlap protection. A watcher that spawned its own copy
    would be a second way to run everything, and the second way is the one
    that breaks.
    """
    try:
        if platform == "darwin":
            cmd = ["launchctl", "kickstart", "-k",
                   f"gui/{os.getuid()}/{row['label']}"]
        elif platform == "win32":
            cmd = ["schtasks", "/run", "/tn", row["task"]]
        else:
            return False
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", timeout=60,
                                creationflags=NO_WINDOW)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


# ── Recording ────────────────────────────────────────────────────────────────

def record_tick(rows: list, kicked: list, now: datetime,
                looked: list | None = None) -> None:
    """Append one row for this tick, and keep the file from growing forever.

    Never raises. The old run ledger promised a cap in its docstring and never
    had one; this one is written with the trim in the same function that
    appends, so the promise cannot drift away from the behaviour.
    """
    try:
        # Titles, not internal names: summary() reads these rows back and
        # prints them to a person, and the internal name is a config key the
        # reader has never seen. `jobs` keeps the internal name because
        # nothing shows it to anyone; it is there for a human debugging a log.
        record = {
            "ts": now.isoformat(timespec="seconds"),
            "kicked": sorted(kicked),
            "jobs": {r["job"]: r["status"] for r in rows},
            "held": {r.get("title", r["job"]): r["_why_not"] for r in rows
                     if r.get("_why_not")},
        }
        # What the mechanic was asked to look at, and what came of each look.
        # A decision NOT to look is recorded too: "already looked at this
        # today" is the difference between a budget working and a feature
        # that silently never fires.
        if looked:
            record["looked"] = sorted(looked)
        decisions = {r.get("title", r["job"]): r["_looked"] for r in rows
                     if r.get("_looked")}
        if decisions:
            record["looked_why"] = decisions
        path = watch_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        _trim(path)
    except Exception:                                            # noqa: BLE001
        pass


def _trim(path: Path, cap: int = LOG_CAP_ROWS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= cap:
            return
        path.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass


def read_ticks(limit: int = 200) -> list:
    """The most recent tick rows, oldest first. A torn line is skipped."""
    out = []
    try:
        lines = watch_log_path().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def last_tick_at():
    """When the watcher last ran, or None. Read by the briefings."""
    ticks = read_ticks(limit=5)
    for row in reversed(ticks):
        try:
            return datetime.fromisoformat(str(row.get("ts", "")))
        except ValueError:
            continue
    return None


# ── The tick ─────────────────────────────────────────────────────────────────

def tick(report_only: bool = False, now: datetime | None = None) -> list:
    """One pass. Returns the graded rows, annotated with what was done."""
    now = now or datetime.now()
    state = _read_state()
    rows = evaluate(now=now, state=state)

    _PROBE_CACHE.clear()
    declined = _declined()
    kicked = []
    for row in rows:
        can, why = actionable(row, state, now) if row.get("_skey") else (False, "")
        if row.get("_absent") and row["job"] not in declined:
            # Switched on in the config and held by nothing. Until now this
            # was graded and then never mentioned, so a briefing whose
            # schedule had been removed simply stopped, and the footer, which
            # only repeats what is held or restarted, had nothing to repeat.
            why = _NOT_SCHEDULED
        row["_kickable"], row["_why_not"] = can, why
        if not can:
            continue
        if report_only:
            kicked.append(row.get("title", row["job"]))
            continue
        if kick(row):
            srec = state["slots"][row["_skey"]]
            srec["kicks"] = srec.get("kicks", 0) + 1
            srec["last_kick"] = now.isoformat()
            srec["last_signature"] = (row.get("detail") or "")[:120]
            row["kicks"] = srec["kicks"]
            kicked.append(row.get("title", row["job"]))

    looked = _escalate(rows, now) if not report_only else []

    if not report_only:
        _prune_state(state, now)
        _write_state(state)
        record_tick(rows, kicked, now, looked)
        _alert_on_stuck(rows, state, now)
    return rows


# What the reader is told about a job that is switched on and installed
# nowhere. It names the one command that fixes it, because "not scheduled" on
# its own is a diagnosis and the reader needs an instruction.
_NOT_SCHEDULED = ("is switched on but not scheduled on this computer, so it "
                  "will not run until it is. Running "
                  "/van-gogh:install-van-gogh again sets it back up.")


def _declined() -> set:
    """Jobs the user said no to at install. Never raises.

    A declined job is absent on purpose. Telling the reader every morning to
    reinstall something they turned down is how a footer gets ignored.
    """
    try:
        return {str(k) for k in config_loader.scheduler_declined()}
    except Exception:                                            # noqa: BLE001
        return set()


# Reasons that mean "a re-run has been proven not to help". Both are the
# watcher's own give-up sentences, and they are matched rather than re-derived
# so that the two can never disagree about when a job is stuck. Anything the
# system is still handling (a usage cap, a CLI mid-update, one deferred tick)
# is deliberately not here: those release themselves, and looking at them
# would spend a model call to conclude "wait".
_STUCK = ("stopped trying for today",)


def _escalate(rows: list, now: datetime) -> list:
    """Ask the mechanic to look at anything a re-run cannot fix. Never raises.

    Only reached at the end of the line: the job has been re-run to its cap,
    or failed identically twice, and the class is not one that releases itself.
    A diagnosis on the first failure would fire on every blip and cost a model
    call to say "try again", which the kick already does for free.

    An expired login is excluded even though it is terminal, because there is
    nothing to diagnose: the watcher's own sentence already names the fix, and
    only the user can do it. An unauthorized connector is excluded for exactly
    the same reason, and the mechanic could not reach a browser anyway.
    """
    out = []
    try:
        import repair

        for row in rows:
            why = row.get("_why_not") or ""
            if not any(marker in why for marker in _STUCK):
                continue
            if row.get("error_class") in ("auth", "quota", "version",
                                          "connector"):
                continue
            result = repair.run(row["job"], detail=row.get("detail", ""),
                                error_class=row.get("error_class", ""),
                                now=now)
            row["_looked"] = result.get("why", "")
            if result.get("spawned"):
                out.append(row.get("title", row["job"]))
    except Exception:                                           # noqa: BLE001
        return out
    return out


def _prune_state(state: dict, now: datetime) -> None:
    """Forget slots older than the lookback. Nothing reads them again."""
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    slots = state.get("slots", {})
    for key in list(slots):
        stamp = key.rsplit("@", 1)[-1]
        try:
            if datetime.fromisoformat(stamp) < cutoff:
                del slots[key]
        except ValueError:
            del slots[key]


def _alert_on_stuck(rows: list, state: dict, now: datetime) -> None:
    """Mail once when a job gives up, if the user turned alerts on.

    Only on the transition: the ledger's own cooldown stops a repeat, but a
    slot that stays stuck for a day would otherwise re-enter this path at
    every tick and lean on that cooldown to stay quiet. Being quiet on
    purpose is better than being quiet by accident.
    """
    try:
        import self_anneal
        for row in rows:
            if "needs you to look" not in row.get("_why_not", ""):
                continue
            srec = state.setdefault("slots", {}).setdefault(row["_skey"], {})
            if srec.get("alerted"):
                continue
            srec["alerted"] = True
            self_anneal.record_failure(
                f"watch.{row['job']}",
                RuntimeError(f"{row['job']} did not recover after "
                             f"{row['kicks']} attempts"),
                row["kicks"], log_tail=str(row.get("detail", ""))[:2000])
        _write_state(state)
    except Exception:                                            # noqa: BLE001
        pass


# ── What the briefing shows ──────────────────────────────────────────────────

# The sentences that mean "you have to do something". A held job either says
# "Nothing for you to do" or it appears here; there is no third kind, and the
# footer sorts on this rather than deciding for itself, so the two can never
# disagree about which is which.
_NEEDS_YOU = ("needs you to sign in", "needs you to reconnect",
              "stopped trying for today", "not scheduled on this computer")

# Being told to act without being told what to do is not an instruction. Both
# give-up messages end here, so there is one answer to "look at what, where".
_WHERE_TO_LOOK = ("Run it yourself to see the error, or look in the logs "
                  "folder in your vault. It will try again on its next "
                  "scheduled run.")

_COUNTS = {2: "twice", 3: "three", 4: "four", 5: "five"}


def _count(n: int) -> str:
    """Small numbers in words. "restarted 3 times" beside "when you have a
    moment" is machine precision next to human vagueness, and the mismatch is
    what makes a sentence read as generated."""
    return _COUNTS.get(n, str(n))


def _needs_the_reader(why: str) -> bool:
    """Case-insensitive on purpose: the same reason reads "sign in ..." mid
    line and "Sign in ..." when it leads one, and a marker that missed the
    capitalized form would sort an urgent line as though it were routine."""
    low = why.lower()
    return any(marker.lower() in low for marker in _NEEDS_YOU)


def _said(title: str, why: str) -> str:
    """Name the job, then finish the sentence about it.

    The reason is written as a predicate, so this reads as one sentence:
    "Your Week briefing stopped because you've hit your Claude usage limit."
    Two earlier shapes were worse. Joining with a space ran two sentences
    together with no break at all ("...briefing It stopped because..."), and
    a colon then a verb read like a log line ("...briefing: is waiting for").
    """
    return f"{title[:1].upper()}{title[1:]} {why}"


def summary(now: datetime | None = None) -> dict:
    """One small dict for the briefings. Never raises, never blocks a render.

    `line` is empty when there is nothing worth saying, which is the normal
    case: a watcher that reports every quiet morning teaches the reader to
    skip the footer, and the footer is where the one line that matters lives.

    When it does say something, it is ordered by what the reader has to do:
    anything needing them comes first, and anything the system is handling
    follows. A run of sentences that mixes the two in arrival order makes the
    reader work out the priority themselves, every morning.
    """
    now = now or datetime.now()
    out = {"line": "", "stale": False, "recovered": [], "waiting": []}
    try:
        if not config_loader.job_watch_enabled():
            return out
        last = last_tick_at()
        if last is None or (now - last) > timedelta(hours=STALE_TICK_H):
            out["stale"] = True
            out["line"] = (
                "The check that makes sure your scheduled jobs are running "
                "has itself stopped, so if one fails now, nothing will "
                "restart it. Running /van-gogh:install-van-gogh again sets "
                "it back up.")
            return out

        ticks = read_ticks(limit=96)
        today = now.date()
        # Rows written before job titles existed carry internal names. Those
        # are config keys the reader has never seen, so they are translated
        # on the way out rather than shown: an upgrade must not put a string
        # like "digest-morning-coffee" in front of anyone.
        titles = {j["name"]: j.get("title", j["name"]) for j in roster()}
        recovered, held = [], {}
        for row in ticks:
            try:
                when = datetime.fromisoformat(str(row.get("ts", "")))
            except ValueError:
                continue
            if when.date() != today:
                continue
            for name in row.get("kicked", []) or []:
                title = titles.get(name, name)
                if title not in recovered:
                    recovered.append(title)
            for name, why in (row.get("held") or {}).items():
                held[titles.get(name, name)] = why

        # A job that was held earlier and has since been restarted is no
        # longer waiting on anything: reporting both states for one job in
        # one line is how a footer contradicts itself.
        for title in recovered:
            held.pop(title, None)

        out["recovered"] = recovered
        out["waiting"] = [_said(t, w) for t, w in sorted(held.items())]

        needs_you = [_said(t, w) for t, w in sorted(held.items())
                     if _needs_the_reader(w)]
        handled = [_said(t, w) for t, w in sorted(held.items())
                   if not _needs_the_reader(w)]
        parts = needs_you + handled
        if recovered:
            said = _recovered_sentence(recovered)
            parts.append(said if parts else said[:1].upper() + said[1:])
        # Every story already ends in a full stop, so joining on a space runs
        # them together into a paragraph whose only boundary marker is a
        # capital letter. Three jobs at once is 60 words, and the one the
        # reader must act on is first but invisible inside the wall.
        out["line"] = "  ".join(parts)
    except Exception:                                            # noqa: BLE001
        return {"line": "", "stale": False, "recovered": [], "waiting": []}
    return out


def _recovered_sentence(titles: list) -> str:
    """What was restarted, as a sentence rather than a comma-joined list.

    Not "was late": late means it turned up on its own, and this one did not.
    It says "did not run" because that is what happened, and it says the job
    was started again rather than leaving the reader to wonder whether they
    are holding a good briefing or a broken one.

    Good news, so it goes last: a reader with something to fix should not
    have to read past a success to find it.
    """
    if len(titles) == 1:
        return (f"{titles[0]} did not run on schedule, so it was restarted "
                "and has now finished.")
    if len(titles) == 2:
        joined = f"{titles[0]} and {titles[1]}"
    else:
        joined = ", ".join(titles[:-1]) + f", and {titles[-1]}"
    return (f"{joined} did not run on schedule, so they were restarted and "
            "have now finished.")


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_rows(rows: list) -> None:
    for row in rows:
        slot = row["slot"].strftime("%a %H:%M") if row.get("slot") else "-"
        why = row.get("_why_not") or ""
        mark = "kick" if row.get("_kickable") else ""
        print(f"{row['job']:<28} {row['kind']:<7} {slot:<10} "
              f"{row['status']:<15} {mark:<5} {why}")



def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check on the scheduled jobs.")
    parser.add_argument("--report-only", action="store_true",
                        help="grade and print; re-run nothing")
    parser.add_argument("--now", default="",
                        help="grade against this moment (ISO) instead of now")
    parser.add_argument("--json", action="store_true",
                        help="print the rows as JSON")
    args = parser.parse_args(argv)

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            sys.stderr.write(f"unreadable --now: {args.now}\n")
            return 2

    started = run_ledger.now_stamp()
    rc = 0
    try:
        rows = tick(report_only=args.report_only, now=now)
        if args.json:
            print(json.dumps(
                [{k: v for k, v in r.items() if not k.startswith("_")}
                 for r in rows], indent=2, default=str))
        else:
            _print_rows(rows)
    except Exception as exc:                                     # noqa: BLE001
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        rc = 1
    finally:
        if not args.report_only:
            # The watcher is a scheduled job too, and grading everything
            # except itself is how a dead watcher stays invisible.
            run_ledger.record_run("watch.tick", started,
                                  run_ledger.now_stamp(), rc)
            _advise()
    return rc


def _advise() -> None:
    """Hand over to the advisor once the watching is done. Never raises.

    Last on purpose, after the tick is graded and recorded: the advisor may
    spend minutes reading the vault, and a slow look must never delay or
    cost the check that keeps the briefings arriving. It decides for itself
    whether it is switched on and whether today's look already happened.
    """
    try:
        import advisor
        advisor.tick()
    except Exception:                                            # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
