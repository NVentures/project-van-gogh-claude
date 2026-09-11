#!/usr/bin/env python3
"""The weekly scorecard send: compute, render, mail, write it down.

Runs once a week under launchd or Task Scheduler. The interesting part is not
the sending, it is the two ways this could quietly go wrong.

**A duplicate is the expensive failure.** A send that succeeds and then dies
before recording itself looks exactly like a send that never happened, and the
watcher exists precisely to re-run jobs that look like they never happened. So
the week is CLAIMED in the ledger before the mail is attempted and STAMPED
after it returns. A crash between the two leaves a claim with no stamp, which
is read as "this may have gone out" and is never retried. The cost of that
choice is a missed scorecard, once, which is much cheaper than two identical
emails and a reader who no longer trusts the thing.

**Silence is the other failure.** An unattended weekly email that stops
arriving is invisible: nobody notices the absence of a thing. So every run
records through `run_ledger`, the same file `job_watch` grades, and a failure
lands in the failure ledger with its class. Auth failures are never retried,
because a dead token is not fixed by trying again in thirty seconds.

The first send waits for a full window. Six grey cards reading "not enough
yet" is a poor first impression of a product whose promise is that the work is
already done.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import kpi_events                                               # noqa: E402
import kpi_html                                                 # noqa: E402
import kpi_report                                               # noqa: E402
import run_ledger                                               # noqa: E402
import self_anneal                                              # noqa: E402

LEDGER_NAME = "kpi_send_ledger.json"
JOB_NAME = "kpi.email"

# Gmail clips a message past roughly 100KB. The same bound digest_send and
# prep_send use, measured on the assembled payload rather than the HTML.
MIME_MAX_BYTES = 95_000


def ledger_path() -> Path:
    return config_loader.logs_dir() / LEDGER_NAME


def scorecard_dir() -> Path:
    return config_loader.van_gogh_root() / "kpi"


def latest_path() -> Path:
    """The artifact `job_watch` grades this job by. Written after the send."""
    return scorecard_dir() / "latest.html"


def _read_ledger() -> dict:
    try:
        with open(ledger_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_ledger(data: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def week_id(now: datetime) -> str:
    """ISO year and week. The unit of "already sent this one"."""
    year, week, _day = now.isocalendar()
    return f"{year}-W{week:02d}"


def already_handled(now: datetime, data: dict | None = None) -> bool:
    """True when this week has been claimed, whether or not the send finished.

    A claim with no stamp means a send may have gone out and the process died
    before it could say so. Retrying that is how a reader gets two copies, so
    an ambiguous claim counts as handled and the week is skipped.
    """
    data = _read_ledger() if data is None else data
    return week_id(now) in (data.get("weeks") or {})


def shown_spotlights(data: dict | None = None) -> list:
    """Skills already spotlighted, oldest first."""
    data = _read_ledger() if data is None else data
    weeks = data.get("weeks") or {}
    out = []
    for key in sorted(weeks):
        skill = (weeks[key] or {}).get("spotlight")
        if skill:
            out.append(skill)
    return out


def claim(now: datetime, spotlight_skill: str) -> None:
    """Write the claim BEFORE the send. The disarm is the claim."""
    data = _read_ledger()
    weeks = data.setdefault("weeks", {})
    weeks[week_id(now)] = {
        "claimed_at": now.isoformat(timespec="seconds"),
        "spotlight": spotlight_skill,
        "sent_at": "",
    }
    _write_ledger(data)


def release(now: datetime) -> None:
    """Give the week back, for a failure that provably sent nothing.

    Only ever called when no address received the message, so this can never
    open the door to a duplicate. It exists because a claim that outlives a
    dead credential silently costs the reader a scorecard they could have had
    the moment the token was fixed.
    """
    try:
        data = _read_ledger()
        weeks = data.get("weeks") or {}
        row = weeks.get(week_id(now))
        if row is not None and not row.get("sent_at"):
            del weeks[week_id(now)]
            _write_ledger(data)
    except Exception:                                           # noqa: BLE001
        pass


def stamp_sent(now: datetime, recipients: list) -> None:
    """Record that the send returned. Best effort: the mail already went."""
    try:
        data = _read_ledger()
        row = (data.get("weeks") or {}).get(week_id(now))
        if row is None:
            return
        row["sent_at"] = datetime.now().isoformat(timespec="seconds")
        # Addresses, never content. A misdirected scorecard has to be
        # diagnosable without the log holding anything private.
        row["recipients"] = list(recipients)
        _write_ledger(data)
    except Exception:                                           # noqa: BLE001
        pass


def _write_artifacts(html: str, now: datetime) -> Path:
    """Save the sent email into the vault. Only ever called after a send."""
    out_dir = scorecard_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / f"scorecard-{now.date().isoformat()}.html"
    dated.write_text(html, encoding="utf-8")
    latest = latest_path()
    latest.write_text(html, encoding="utf-8")
    return latest


def recipients() -> list:
    """Who gets it: the scorecard's own address, plus support when shared."""
    out = []
    primary = (config_loader.kpi_recipient_email() or "").strip()
    if primary:
        out.append(primary)
    if config_loader.kpi_share_with_support():
        team = (config_loader.support_team_email() or "").strip()
        if team and team not in out:
            out.append(team)
    return out


def build(now: datetime | None = None) -> dict:
    """The report, its HTML and its text, with the spotlight attached."""
    now = now or datetime.now()
    rows, skipped = kpi_events.read()
    report = kpi_report.compute(now=now, rows=rows, skipped=skipped)
    report["spotlight"] = kpi_report.spotlight(shown=shown_spotlights(), now=now)
    meta = {}
    try:
        meta["version"] = config_loader.resolved_meta().get("version", "")
    except Exception:                                           # noqa: BLE001
        pass
    return {
        "report": report,
        "html": kpi_html.render(report, meta),
        "text": kpi_html.render_text(report),
        "rows": rows,
    }


def subject_for(report: dict, now: datetime | None = None) -> str:
    """The subject line. Dated through kpi_html._pretty_date rather than
    strftime: `%-d` is POSIX-only and renders as a literal on Windows."""
    now = now or datetime.now()
    return (f"[Van Gogh] Your fortnight to "
            f"{kpi_html._pretty_date(now.date().isoformat())}")


class SendFailed(Exception):
    """A send that did not finish, carrying why and what already went out."""

    def __init__(self, kind: str, cause: BaseException, attempts: int,
                 log_tail: str, delivered: list | None = None):
        super().__init__(f"{kind}: {cause}")
        self.kind = kind
        self.cause = cause
        self.attempts = attempts
        self.log_tail = log_tail
        # Addresses that DID receive it before the failure. A week that put a
        # message in someone's inbox is never released, whatever the class.
        self.delivered = list(delivered or [])


# A transient server error is worth one more go; anything else is not. Two
# attempts rather than three because the whole job runs weekly: a wall that
# survives thirty seconds will still be there, and the watcher can re-run the
# slot once the cause is gone.
TRANSIENT_ATTEMPTS = 2
TRANSIENT_BACKOFF_S = 30


def _deliver(account: dict, recipients_: list, subject: str, text: str,
             html: str | None) -> list:
    """Send to each address, deciding what to do about a failure by its CLASS.

    Three behaviours, because three things are true:

    * **auth**, and anything else no retry can fix, stops on the first attempt.
      A dead token is not repaired by asking again, and burning the budget on
      it only delays the notice the reader needs.
    * **network** and a transient server error get one more attempt after a
      pause, since the commonest cause of both is a blip.
    * **a permanent rejection** is never retried. The server has answered.

    Once an address has been sent to, it is never retried, even if a later
    address in the list fails: the recipients are independent messages and a
    retry of the whole loop would deliver a second copy to whoever already
    got one. Returns the addresses that actually received it.
    """
    import failure_class
    from send_email import send_email

    delivered = []
    for address in recipients_:
        attempt = 0
        while True:
            attempt += 1
            try:
                send_email(account, address, subject, text, html=html)
                delivered.append(address)
                break
            except Exception as exc:                            # noqa: BLE001
                kind = failure_class.classify(f"{type(exc).__name__}: {exc}")
                retryable = (kind in ("network", "classifier")
                             and attempt < TRANSIENT_ATTEMPTS)
                if retryable:
                    time.sleep(TRANSIENT_BACKOFF_S)
                    continue
                # A send that raised may still have been accepted, so the tail
                # says so rather than implying the message certainly failed.
                tail = ("Send may or may not have gone out. Check the Sent "
                        "folder before resending by hand.")
                if kind in failure_class.NO_RETRY:
                    tail = (f"Not retried: a {kind} failure is not fixed by "
                            f"trying again. Nothing was sent to this address.")
                raise SendFailed(kind or "unknown", exc, attempt, tail,
                                 delivered=delivered) from exc
    return delivered


def main(argv: list | None = None) -> int:
    config_loader.force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="send even if this week was already handled")
    parser.add_argument("--preview", metavar="PATH",
                        help="render to a file and send nothing")
    parser.add_argument("--now", metavar="ISO",
                        help="treat this moment as now (testing)")
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    started = run_ledger.now_stamp()

    if args.preview:
        built = build(now)
        Path(args.preview).write_text(built["html"], encoding="utf-8")
        print(json.dumps({"status": "preview", "path": args.preview,
                          "measured": built["report"]["measured_count"]}))
        return 0

    if not config_loader.kpi_enabled():
        print(json.dumps({"status": "skipped",
                          "reason": "the weekly scorecard is off"}))
        return 0

    today = now.strftime("%A").lower()
    if not args.force and today != config_loader.kpi_day():
        print(json.dumps({"status": "skipped",
                          "reason": f"today ({today}) is not the scorecard day"}))
        return 0

    if not args.force and already_handled(now):
        print(json.dumps({"status": "skipped",
                          "reason": "this week's scorecard was already handled"}))
        return 0

    window = config_loader.kpi_window_days()
    rows, _skipped = kpi_events.read()
    if not args.force and not kpi_report.has_enough_history(rows, window, now):
        when = kpi_report.first_send_date(rows, window, now)
        print(json.dumps({"status": "skipped",
                          "reason": "not a full window of history yet",
                          "first_send": str(when)}))
        return 0

    to = recipients()
    if not to:
        print("ERROR: no recipient is configured for the scorecard",
              file=sys.stderr)
        return 1

    account = config_loader.digest_sender_account()
    if not account:
        print("ERROR: no sender account configured", file=sys.stderr)
        return 1

    built = build(now)
    html, text = built["html"], built["text"]
    if len(html.encode("utf-8")) > MIME_MAX_BYTES:
        html = None                      # plain text still carries every number

    # The claim goes in BEFORE the send. See the module docstring.
    claim(now, (built["report"].get("spotlight") or {}).get("skill", ""))

    subject = subject_for(built["report"], now)
    try:
        sent_to = _deliver(account, to, subject, text, html)
    except SendFailed as failure:
        # The class decides the response, never a counter. An expired login is
        # not fixed by trying again in thirty seconds, and a message the server
        # may already have accepted must not be sent twice on the chance that
        # it did not.
        #
        # The claim is RELEASED only when nothing can have gone out. A missing
        # or dead credential never reached the server, so holding the week
        # would mean fixing the token still costs the reader this fortnight's
        # scorecard. Any other failure keeps the claim, because a send that
        # raised may still have been accepted and a duplicate is worse than a
        # miss.
        if failure.kind == "auth" and not failure.delivered:
            release(now)
        self_anneal.record_failure(
            "kpi_send.send", failure.cause, attempts=failure.attempts,
            log_tail=failure.log_tail)
        run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), 1,
                              f"{failure.kind}: {type(failure.cause).__name__}")
        print(f"ERROR: {failure.kind}: {type(failure.cause).__name__}: "
              f"{failure.cause}", file=sys.stderr)
        return 1
    to = sent_to

    stamp_sent(now, to)
    path = _write_artifacts(built["html"], now)
    kpi_events.prune()
    run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), 0,
                          f"scorecard sent to {len(to)}")

    print(json.dumps({
        "status": "sent",
        "recipients": to,
        "subject": subject,
        "measured": built["report"]["measured_count"],
        "file": str(path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
