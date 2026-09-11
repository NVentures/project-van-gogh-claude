#!/usr/bin/env python3
"""The weekly finance send: read the books, mail the brief, write it down.

Runs once a week under launchd or Task Scheduler. Three things could go quietly
wrong, and each has a rule.

**A duplicate is the expensive failure**, so the week is CLAIMED before the
mail is attempted and STAMPED after it returns, exactly as the scorecard does.
A crash between the two leaves a claim with no stamp, read as "this may have
gone out" and never retried. The cost is one missed brief, which is far cheaper
than two contradictory emails about the same cash position.

**The claim comes after the numbers, not before.** The scorecard computes from
a local log that cannot fail; this reads a third party over a connector that
can. Claiming first would burn the week on a fetch that never produced a page,
and the reader would get nothing this week and no explanation either.

**Silence is the failure this feature is most prone to.** The connector token
expires and only a person with a browser can renew it, so a scheduled run can
fail every week forever while the machine is working perfectly. A run that
fails for that reason mails a short note saying so, at most once a week, which
is the difference between a product that stopped and a product that told you.

**A stale brief is worse than a late one.** If the watcher re-runs this days
after the slot, the numbers would arrive under a subject line implying they are
today's. A brief whose figures are more than three days old is refused rather
than sent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402
import finance_brief                                            # noqa: E402
import finance_html                                             # noqa: E402
import run_ledger                                               # noqa: E402

LEDGER_NAME = "finance_send_ledger.json"
JOB_NAME = "finance.email"

# Gmail clips a message past roughly 100KB. The bound digest_send and kpi_send
# already use, measured on the assembled payload rather than the HTML.
MIME_MAX_BYTES = 95_000

# How old the figures may be at the moment of sending. A watcher re-run days
# after the slot would otherwise mail Monday's cash position on Thursday under
# a subject line that reads as today's.
MAX_AGE_DAYS = 3

# How often the "reconnect QuickBooks" note may go out. Often enough to be
# useful, rarely enough that an unreconnected connector does not become a
# weekly nag the reader learns to ignore.
REMINDER_COOLDOWN_DAYS = 7


def ledger_path() -> Path:
    return config_loader.logs_dir() / LEDGER_NAME


def finance_dir() -> Path:
    return config_loader.van_gogh_root() / "finance"


def latest_path() -> Path:
    """The artifact `job_watch` grades this job by. Written after the send."""
    return finance_dir() / "latest.html"


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


def claim(now: datetime, as_of: str) -> None:
    """Write the claim BEFORE the send. The disarm is the claim."""
    data = _read_ledger()
    weeks = data.setdefault("weeks", {})
    weeks[week_id(now)] = {
        "claimed_at": now.isoformat(timespec="seconds"),
        "as_of": as_of,
    }
    _write_ledger(data)


def release(now: datetime) -> None:
    """Give the week back after a send that certainly did not happen.

    The claim exists so an ambiguous crash never sends twice, and that rule
    stands. But a send that raised before the transport accepted anything is
    not ambiguous: nothing went out, and keeping the claim costs the reader a
    whole week for a fault that may be gone in an hour. Observed live, with a
    credential missing at the moment of sending: the week was burned, the
    ledger recorded a claim with no stamp, and no mail existed anywhere.

    Only ever called when the failure is known to precede delivery. An error
    raised after the server accepted the message keeps its claim.
    """
    try:
        data = _read_ledger()
        weeks = data.get("weeks") or {}
        row = weeks.get(week_id(now)) or {}
        if row.get("sent_at"):
            return                      # it went out; the claim is the record
        weeks.pop(week_id(now), None)
        _write_ledger(data)
    except Exception:                                           # noqa: BLE001
        pass


def stamp_sent(now: datetime, recipients_: list) -> None:
    """Record that the send returned. Best effort: the mail already went."""
    try:
        data = _read_ledger()
        row = (data.get("weeks") or {}).get(week_id(now))
        if row is None:
            return
        row["sent_at"] = datetime.now().isoformat(timespec="seconds")
        # Addresses, never figures. A misdirected brief has to be diagnosable
        # without the log holding anything about the books.
        row["recipients"] = list(recipients_)
        _write_ledger(data)
    except Exception:                                           # noqa: BLE001
        pass


def reminder_due(now: datetime, data: dict | None = None) -> bool:
    """Has it been long enough since the last reconnect note?"""
    data = _read_ledger() if data is None else data
    last = (data.get("reminder") or {}).get("sent_at") or ""
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - when).days >= REMINDER_COOLDOWN_DAYS


def stamp_reminder(now: datetime) -> None:
    try:
        data = _read_ledger()
        data["reminder"] = {"sent_at": now.isoformat(timespec="seconds")}
        _write_ledger(data)
    except Exception:                                           # noqa: BLE001
        pass


def recipients() -> list:
    out = []
    primary = (config_loader.finance_recipient_email() or "").strip()
    if primary:
        out.append(primary)
    return out


def subject_for(report: dict) -> str:
    """The subject carries the as-of date, so a late arrival cannot mislead."""
    company = (report.get("company") or {}).get("name") or "Your books"
    return f"[Van Gogh] {company}, where the money stands {report.get('as_of', '')}"


def is_stale(report: dict, now: datetime, max_days: int = MAX_AGE_DAYS) -> bool:
    """Are these figures too old to mail under today's date?"""
    try:
        as_of = date.fromisoformat(str(report.get("as_of") or ""))
    except ValueError:
        return False
    return (now.date() - as_of).days > max_days


def _write_artifacts(html: str, markdown: str, now: datetime) -> Path:
    """Save the brief into the vault. Only ever called after a send."""
    out_dir = finance_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"finance-{now.date().isoformat()}.html").write_text(
        html, encoding="utf-8")
    latest = latest_path()
    latest.write_text(html, encoding="utf-8")
    finance_brief.output_path().write_text(markdown, encoding="utf-8")
    return latest


def _deliver(account: dict, recipients_: list, subject: str, text: str,
             html: str | None) -> list:
    """Send to each address. Once sent, an address is never retried.

    The recipients are independent messages, so a retry of the whole loop
    after a later failure would deliver a second copy to whoever already had
    one. Returns the addresses that actually received it.
    """
    from send_email import send_email

    delivered = []
    for address in recipients_:
        send_email(account, address, subject, text, html=html)
        delivered.append(address)
    return delivered


REMINDER_SUBJECT = "[Van Gogh] Your finance brief needs QuickBooks reconnected"

REMINDER_TEXT = """Your weekly finance brief could not be sent this morning.

QuickBooks needs reconnecting. Claude holds that connection on your behalf and
it expires from time to time, and only you can renew it.

To fix it, open claude.ai, go to Settings, then Connectors, and reconnect
Intuit QuickBooks. The brief starts arriving again on its own the following
week, with nothing else to do.

Nothing is wrong with your books, and nothing in them was changed.
"""


def send_reminder(now: datetime, reason: str) -> bool:
    """Tell the reader the connector needs them. Never raises.

    Without this the job fails every week in a log nobody opens, and the
    product looks like it simply stopped. Only for an expired connection: a
    server that was never added is a setup step, not a thing to nag about.
    """
    try:
        if not reminder_due(now):
            return False
        account = config_loader.digest_sender_account()
        to = recipients()
        if not account or not to:
            return False
        from send_email import send_email

        for address in to:
            send_email(account, address, REMINDER_SUBJECT, REMINDER_TEXT)
        stamp_reminder(now)
        return True
    except Exception:                                           # noqa: BLE001
        return False


def main(argv: list | None = None) -> int:
    config_loader.force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="ignore the cadence day (still never sends twice)")
    parser.add_argument("--preview", metavar="PATH",
                        help="render to a file and send nothing")
    parser.add_argument("--input", metavar="PATH",
                        help="pre-fetched raw tool results, for testing")
    parser.add_argument("--claude", default=None,
                        help="absolute path to the claude CLI")
    parser.add_argument("--now", metavar="ISO", help="treat this as now (testing)")
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    started = run_ledger.now_stamp()
    rc, detail = 0, ""

    try:
        if not args.preview and not config_loader.finance_enabled():
            print(json.dumps({"status": "skipped",
                              "reason": "the weekly finance brief is off"}))
            return 0

        today = now.strftime("%A").lower()
        if not args.preview and not args.force and today != config_loader.finance_day():
            print(json.dumps({"status": "skipped",
                              "reason": f"today ({today}) is not the brief's day"}))
            return 0

        # Checked before the work, so an already-handled week costs nothing.
        if not args.preview and already_handled(now):
            print(json.dumps({"status": "skipped",
                              "reason": "this week's brief was already handled"}))
            return 0

        # The numbers first. Claiming before this would spend the week on a
        # fetch that produced no page.
        if args.input:
            from data_sources import load_input
            raw = load_input(args.input)
            fetch_status = "input"
        else:
            out = finance_brief.fetch_raw(now.date(), claude=args.claude)
            fetch_status = out["status"]
            if not out["ok"]:
                detail = out["reason"]
                rc = 1
                mailed = False
                if out["status"] == finance_brief.connector_fetch.NEEDS_AUTH:
                    mailed = send_reminder(now, detail)
                print(json.dumps({"status": out["status"], "reason": detail,
                                  "reminder_sent": mailed}))
                print(detail, file=sys.stderr)
                return rc
            raw = out["data"]

        expected = {"id": config_loader.finance_company_id(),
                    "name": config_loader.finance_company_name()}
        report = finance_brief.build(raw, today=now.date(), kind="weekly",
                                     expected_company=expected)
        html = finance_html.render(report, {"version": _version()})
        text = finance_html.render_text(report)

        if args.preview:
            Path(args.preview).write_text(html, encoding="utf-8")
            print(json.dumps({"status": "preview", "path": args.preview,
                              "as_of": report["as_of"]}))
            return 0

        if not any(s["measured"] for s in report["sections"].values()):
            detail = next((s["reason"] for s in report["sections"].values()
                           if s["reason"]), "nothing could be measured")
            print(json.dumps({"status": "skipped", "reason": detail}))
            print(detail, file=sys.stderr)
            return 1

        if is_stale(report, now):
            detail = (f"the figures are as of {report['as_of']}, more than "
                      f"{MAX_AGE_DAYS} days old, so they were not sent under "
                      "today's date")
            print(json.dumps({"status": "skipped", "reason": detail}))
            return 1

        to = recipients()
        if not to:
            detail = "no recipient is configured for the finance brief"
            print(f"ERROR: {detail}", file=sys.stderr)
            return 1
        account = config_loader.digest_sender_account()
        if not account:
            detail = "no sender account configured"
            print(f"ERROR: {detail}", file=sys.stderr)
            return 1

        if len(html.encode("utf-8")) > MIME_MAX_BYTES:
            html = None                 # the text part still carries every figure

        claim(now, report["as_of"])
        try:
            delivered = _deliver(account, to, subject_for(report), text, html)
        except Exception:                                       # noqa: BLE001
            # Nothing was accepted by the transport, so the week is given back
            # rather than spent on a failure the next run may not hit.
            # _deliver stops at the first address that raises, and an address
            # already delivered to is recorded before any later one can fail.
            release(now)
            raise
        stamp_sent(now, delivered)
        # Written only after the mail is away, so a fresh file is proof the
        # send happened rather than proof the job merely started.
        artifact = _write_artifacts(html or text, report["finance_md"], now)
        finance_brief.write_history({"date": report["as_of"], "kind": "weekly",
                                     "values": report["values"],
                                     "company_id": report["company"]["id"]})
        print(json.dumps({"status": "sent", "recipients": delivered,
                          "as_of": report["as_of"], "artifact": str(artifact),
                          "fetch": fetch_status}))
        return 0
    except Exception as exc:                                    # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"[:400]
        print(detail, file=sys.stderr)
        return 1
    finally:
        # Every exit path leaves a row: a scheduled run that fails silently is
        # indistinguishable from one that never fired.
        run_ledger.record_run(JOB_NAME, started, run_ledger.now_stamp(), rc,
                              detail)


def _version() -> str:
    try:
        return config_loader.resolved_meta().get("version", "")
    except Exception:                                           # noqa: BLE001
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
