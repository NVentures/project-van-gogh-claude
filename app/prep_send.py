#!/usr/bin/env python3
"""The prep email tick: render what is due, mail it, write it down.

Runs every fifteen minutes under launchd or Task Scheduler. One pass:

    read      the calendar, and the ledger of what already went out
    select    meetings inside the lead window (or the whole day, in daily mode)
    render    each prep headlessly, the way digest_send renders a briefing
    send      one email per meeting, or one for the day
    record    the event id, so the next three ticks skip it

Selection and subject building live in `prep_email.py`, which is pure and
testable. This module owns the side effects: the subprocess, the mailbox, the
ledger file. Everything here swallows its own errors. A tick that cannot build
one prep still builds the others; a tick that cannot mail at all still records
nothing and tries again next time, because an unsent prep is recoverable and a
crashed poller is not.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import config_loader
import digest_html
import prep_email
from platform_compat import NO_WINDOW, claude_bin
from send_email import send_email

# Gmail clips a message past roughly 100KB. Same bound digest_send uses.
HTML_MAX_BYTES = 95_000


def render_prep(meeting_arg: str, claude: str | None = None) -> str:
    """Run the meeting-prep skill headlessly and return the brief it printed.

    The brief is written by the model, not by `meeting_prep.py`: the script
    gathers entity pages, prior meetings, hotcache threads and week lines, and
    the skill synthesizes the win condition and talking points from them. That
    synthesis is the half worth mailing, so this pays for a real render.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Unattended marker: release notes are a chat moment and must never land in
    # a mailed prep (same contract digest_send relies on).
    env["VAN_GOGH_UNATTENDED"] = "1"
    result = subprocess.run(
        [claude or claude_bin(), "-p", f"/van-gogh:meeting-prep {meeting_arg}",
         "--permission-mode", "bypassPermissions"],
        cwd=str(config_loader.repo_root()),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=prep_email.RENDER_TIMEOUT_S,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"meeting-prep exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[-2000:]}"
        )
    body = (result.stdout or "").strip()
    if not body:
        raise RuntimeError("meeting-prep produced no output")
    return body


def render_with_retry(meeting_arg: str, claude: str | None = None) -> str:
    """Render, retrying once on failure with backoff.

    A headless render fails for reasons that clear on their own, chiefly a
    safety classifier that flaps. One retry is the whole budget: past that the
    call is close enough that a late prep is not worth another two minutes.
    """
    last = None
    for attempt in range(prep_email.RENDER_ATTEMPTS):
        try:
            return render_prep(meeting_arg, claude=claude)
        except Exception as exc:                                # noqa: BLE001
            last = exc
            if attempt + 1 < prep_email.RENDER_ATTEMPTS:
                time.sleep(prep_email.RENDER_BACKOFF_S[attempt])
    raise last if last else RuntimeError("render failed")


def mail(subject: str, body: str) -> None:
    """Send one prep. Sender and recipient come from the digest block.

    Reusing the digest's addresses is deliberate: a user who already told Van
    Gogh where to mail briefings should not have to answer the same question
    twice, and a second recipient setting is a second thing to get wrong.
    """
    account = config_loader.digest_sender_account()
    to = config_loader.digest_recipient_email()
    if not account or not to:
        raise RuntimeError("no configured sender or recipient")
    try:
        html = digest_html.md_to_email_html(body)
        if len(html.encode("utf-8")) > HTML_MAX_BYTES:
            html = None
    except Exception:                                           # noqa: BLE001
        html = None
    # Not retried on failure: a lost response after the server accepted the
    # message is indistinguishable from a real failure, and guessing wrong puts
    # a duplicate in the inbox.
    send_email(account, to, subject, body, html=html)


def _events():
    """Today's calendar, or an empty list if it cannot be read."""
    import meeting_prep
    return meeting_prep.merge_events(meeting_prep.fetch_all_events(days_ahead=1))


def run_each(rows: dict, claude: str | None = None,
             now: datetime | None = None) -> dict:
    """One email per meeting inside the lead window."""
    now = now or datetime.now(timezone.utc)
    events = _events()
    for event in prep_email.due(events, rows, now=now):
        subject = prep_email.subject_for(event, now=now)
        try:
            body = render_with_retry(str(event.get("title") or "next"), claude=claude)
            mail(subject, body)
        except Exception as exc:                                # noqa: BLE001
            # One prep failing costs that prep. The ledger is deliberately not
            # written, so the next tick tries again while the meeting is still
            # ahead of us.
            print(f"WARNING: prep failed for {subject}: {exc}", file=sys.stderr)
            continue
        rows = prep_email.mark_sent(rows, event, now=now)
        prep_email._write_ledger(rows)
    return rows


def run_daily(rows: dict, claude: str | None = None,
              now: datetime | None = None) -> dict:
    """One email covering every remaining meeting today."""
    now = now or datetime.now(timezone.utc)
    if not prep_email.daily_due(rows=rows):
        return rows
    events = prep_email.todays_events(_events(), now=now)
    if not events:
        # Nothing to prep is not a failure, and it is not an email either. Mark
        # the day done so a quiet Tuesday does not retry every fifteen minutes.
        return prep_email.mark_daily_sent(rows)
    try:
        body = render_with_retry("--today", claude=claude)
        mail(prep_email.daily_subject(len(events)), body)
    except Exception as exc:                                    # noqa: BLE001
        print(f"WARNING: daily prep failed: {exc}", file=sys.stderr)
        return rows
    rows = prep_email.mark_daily_sent(rows, now=None)
    prep_email._write_ledger(rows)
    return rows


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send meeting prep emails.")
    parser.add_argument("--claude", metavar="PATH",
                        help="absolute path to the claude CLI (the scheduler "
                             "resolves this at sync time; launchd PATH is too "
                             "sparse to rely on at fire time)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent, mail nothing")
    args = parser.parse_args(argv)

    if not config_loader.prep_email_enabled():
        return 0

    rows = prep_email.prune(prep_email._read_ledger())

    if args.dry_run:
        now = datetime.now(timezone.utc)
        events = _events()
        if config_loader.prep_mode() == "daily":
            due = prep_email.todays_events(events, now=now)
            print(f"daily mode, due now: {prep_email.daily_due(rows=rows)}")
        else:
            due = prep_email.due(events, rows, now=now)
        for event in due:
            print(prep_email.subject_for(event, now=now))
        if not due:
            print("nothing due")
        return 0

    try:
        if config_loader.prep_mode() == "daily":
            run_daily(rows, claude=args.claude)
        else:
            run_each(rows, claude=args.claude)
    except Exception as exc:                                    # noqa: BLE001
        # The poller must never wedge. A tick that dies here is one missed prep;
        # a traceback that stops the job is every future one.
        print(f"WARNING: prep tick failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
