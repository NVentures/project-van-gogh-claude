"""
digest_send.py — render one briefing headlessly and email it to the user.

Scheduled per-briefing by `scheduler_setup.py sync-digests` (launchd on macOS,
Task Scheduler on Windows) at the cadences in the config `digest` block, which
`/van-gogh:update-digest-preferences` manages. Flow:

1. Exit quietly (0) unless the digest opt-in and this briefing's cadence are on.
2. Run `claude -p "/van-gogh:<briefing>"` so the skill renders its markdown
   into the vault (`{vault}/van-gogh/*.md`, or `weekly_dir/retro-*.md` for
   week-retro), and verify the file is fresh.
3. Email the rendered briefing (email-safe HTML via digest_html.py, with the
   raw markdown as the plain-text alternative) from the configured sender
   account (Google or Microsoft OAuth client) to the configured recipient.
   Subject:
   `[Project Van Gogh] {Briefing Name} MM-DD-YYYY` in local time.

Requires Tier 1 (OAuth refresh tokens): there is no connector fallback in a
headless scheduled run. `--no-render` emails the existing rendered file
without re-running the skill (used for test sends).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import claude_update
import config_loader
import digest_html
import run_ledger
import self_anneal
from platform_compat import NO_WINDOW, claude_bin
# The mail primitives live in send_email.py, the sanctioned entry point for
# ad-hoc sends, and are re-exported here for this module's scheduled sends.
from send_email import (  # noqa: F401 (build_* kept for tests/back-compat)
    build_gmail_raw,
    build_graph_message,
    clean_address,
    send_email,
)

RENDER_TIMEOUT_S = 30 * 60
# A render is retried; a send never is. See self_anneal for why.
RENDER_ATTEMPTS = 2
RENDER_BACKOFF_S = (30, 120)
# Filesystem/clock slack when checking the rendered file was refreshed.
MTIME_SLACK_S = 5
# How long to wait behind another digest run (same-minute cadences fire two
# jobs at once on Mon/Fri) before giving up; also the stale-lock threshold.
# This MUST cover the whole retry budget: digest_lock breaks a lock it judges
# stale, so a timeout shorter than the retries lets a second run start behind a
# first one that is merely slow, and the user gets the briefing twice.
LOCK_TIMEOUT_S = RENDER_TIMEOUT_S * RENDER_ATTEMPTS + sum(RENDER_BACKOFF_S) + 300
LOCK_POLL_S = 15
# Gmail clips HTML bodies over ~102KB behind "[Message clipped]"; above this
# we send plain-text-only rather than let the digest truncate silently.
HTML_MAX_BYTES = 95_000

BRIEFING_NAMES = {
    "morning-coffee": "Morning Coffee",
    "afternoon-tea": "Afternoon Tea",
    "week": "Week",
    "week-retro": "Week Retro",
}


def briefing_md_path(briefing: str):
    """The rendered markdown file a briefing skill writes (resolve AFTER
    rendering — week-retro writes a new dated file each run)."""
    if briefing == "morning-coffee":
        return config_loader.morning_coffee_md_path()
    if briefing == "afternoon-tea":
        return config_loader.afternoon_tea_md_path()
    if briefing == "week":
        return config_loader.workspace_week_md_path()
    if briefing == "week-retro":
        retros = sorted(config_loader.weekly_dir().glob("retro-*.md"))
        if not retros:
            raise RuntimeError(
                f"no retro-*.md found in {config_loader.weekly_dir()}")
        return retros[-1]
    # Named, not a fall-through. The last case used to be bare, so ANY name
    # that was not one of the three above landed in the retro branch and
    # failed with a message about retro files, which sends whoever reads it
    # looking in the wrong place entirely. argparse restricts the CLI to the
    # four known names, so this was never reachable from the command line, and
    # that is exactly why it would have gone unnoticed until a fifth briefing
    # existed. Found by the mechanic agent reading this function, 2026-09-09.
    raise RuntimeError(
        f"no rendered file is defined for the briefing {briefing!r}; "
        f"known briefings are {', '.join(sorted(BRIEFING_NAMES))}")


def build_subject(briefing: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"[Project Van Gogh] {BRIEFING_NAMES[briefing]} {now.strftime('%m-%d-%Y')}"


@contextlib.contextmanager
def digest_lock():
    """Serialize concurrent digest runs on one machine.

    The default cadences fire two jobs in the same minute on Mon/Fri; two
    concurrent renders both write vault state, and two concurrent Microsoft
    sends race the refresh-token rotation in the state .env. Portable
    O_CREAT|O_EXCL lockfile; a lock older than LOCK_TIMEOUT_S belongs to a
    crashed run and is broken."""
    import user_state
    lock = user_state.state_dir() / "digest.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_TIMEOUT_S:
                    lock.unlink(missing_ok=True)  # stale — holder crashed
                    continue
            except OSError:
                continue  # holder released between our check and stat
            if time.time() > deadline:
                raise RuntimeError(
                    f"another digest run has held {lock} for over "
                    f"{LOCK_TIMEOUT_S // 60} minutes; giving up")
            time.sleep(LOCK_POLL_S)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def workbench_session() -> dict:
    """The running Workbench's port and token, or {} when none is running."""
    try:
        import user_state
        session = user_state.state_dir() / "workbench_session.json"
        data = json.loads(session.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("port") else {}
    except Exception:                                           # noqa: BLE001
        return {}


# Each briefing may open its own page once per cooldown. The throttle is keyed
# BY BRIEFING, not global: Morning Coffee and The Week both fire at 07:00 on a
# Monday, and Week Retro shares 07:00 on a Friday, so a single global stamp
# would let the first one through and silently swallow the second. What it
# still prevents is the same briefing reopening its page on a rerun or a retry.
_OPEN_STAMP = "last-page-open.json"
_OPEN_COOLDOWN_S = 6 * 60 * 60


def _open_stamp_path():
    import user_state
    return user_state.state_dir() / _OPEN_STAMP


def _read_open_stamps() -> dict:
    """`{briefing: epoch}`. Tolerates the pre-0.55 single-stamp shape."""
    try:
        data = json.loads(_open_stamp_path().read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    stamps = data.get("briefings")
    if isinstance(stamps, dict):
        return stamps
    # The old shape was one {at, briefing} pair for whichever ran last. Carry
    # it forward for that briefing only, so an upgrade cannot reopen a page
    # the user has already been shown.
    if "at" in data and data.get("briefing"):
        return {str(data["briefing"]): data["at"]}
    return {}


def _opened_recently(briefing: str) -> bool:
    """True when THIS briefing already put its page in front of the user."""
    try:
        at = float(_read_open_stamps().get(briefing, 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - at) < _OPEN_COOLDOWN_S


def _record_open(briefing: str) -> None:
    try:
        stamps = _read_open_stamps()
        stamps[briefing] = time.time()
        _open_stamp_path().write_text(
            json.dumps({"briefings": stamps}), encoding="utf-8")
    except Exception:                                           # noqa: BLE001
        pass


def open_local_page(briefing: str) -> bool:
    """Open this briefing's local page. Never raises, never blocks the send.

    Deliberately conservative, because the failure mode is a window appearing
    on someone's screen unbidden:

    * the user can turn it off (`workbench.open_on_run`);
    * only into a Workbench that is ALREADY running, never one this starts, so
      a scheduled job can never leave a server behind on a port;
    * at most one page per briefing in any six hours, so a rerun or a retry
      cannot reopen a page the user has already been shown.

    Every other path prints one line and returns False. The briefing is still
    written and the digest still goes.
    """
    try:
        from config_loader import workbench_open_on_run
        if not workbench_open_on_run():
            return False
        if _opened_recently(briefing):
            print(f"NOTE: the {briefing} page was already opened recently; "
                  "not opening it again.", file=sys.stderr)
            return False
        session = workbench_session()
        if not session:
            print("NOTE: the Workbench is not running, so the page was not "
                  "opened. Start it with /van-gogh:workbench.", file=sys.stderr)
            return False
        url = (f"http://127.0.0.1:{session['port']}/briefing/{briefing}"
               f"?t={session['token']}")
        webbrowser.open(url)
        _record_open(briefing)
        print(f"Opened {url.split('?')[0]}", file=sys.stderr)
        return True
    except Exception as exc:                                    # noqa: BLE001
        print(f"NOTE: could not open the local page ({exc}).", file=sys.stderr)
        return False


def render_briefing(briefing: str, claude: str | None = None) -> None:
    """Run the skill headlessly so it writes its markdown into the vault.

    `claude` is the absolute CLI path the scheduler resolved at sync time
    (launchd/Task Scheduler PATH is too sparse to rely on at fire time);
    fall back to a PATH lookup for manual runs.
    """
    # Unset ANTHROPIC_API_KEY so the claude CLI routes via the subscription.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Unattended marker: release notes (meta.whats_new) are a chat moment and
    # must never land in an emailed digest (see plugin_update.whats_new).
    env["VAN_GOGH_UNATTENDED"] = "1"
    # bypassPermissions: nobody is present to answer a permission prompt, and a
    # denied tool call would fail the render silently (see scheduler_setup).
    result = subprocess.run(
        [claude or claude_bin(), "-p", f"/van-gogh:{briefing}",
         "--permission-mode", "bypassPermissions"],
        cwd=str(config_loader.repo_root()),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=RENDER_TIMEOUT_S,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        # A CLI too old for the current model 400s on every render until it is
        # updated; heal it now so the retry runs on a current CLI.
        claude_update.heal_if_stale(
            (result.stdout or "") + (result.stderr or ""), claude)
        raise RuntimeError(
            f"claude -p /van-gogh:{briefing} exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[-2000:]}"
        )


def _run_digest(argv: list[str] | None = None) -> tuple[int, str, str]:
    """The digest body. Returns (rc, briefing, detail) for the run ledger."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("briefing", choices=sorted(BRIEFING_NAMES))
    parser.add_argument("--no-render", action="store_true",
                        help="email the existing rendered file without re-running the skill")
    parser.add_argument("--claude", default=None,
                        help="absolute path to the claude CLI (embedded by sync-digests; "
                             "scheduler environments have too sparse a PATH to resolve it)")
    args = parser.parse_args(argv)

    if not config_loader.digest_enabled():
        reason = "digest not enabled in config"
        print(json.dumps({"status": "skipped", "reason": reason}))
        return 0, args.briefing, f"skipped: {reason}"
    cadence = config_loader.digest_briefings().get(args.briefing, {})
    if not cadence.get("enabled", False):
        reason = f"digest for {args.briefing} is disabled"
        print(json.dumps({"status": "skipped", "reason": reason}))
        return 0, args.briefing, f"skipped: {reason}"
    # Scheduled runs can fire on the wrong day (launchd coalesces missed runs
    # to wake time; a config edit without re-sync leaves stale triggers), so
    # re-check the cadence here. Manual test sends (--no-render) bypass this.
    today = datetime.now().strftime("%A").lower()
    if not args.no_render and today not in [str(d).lower() for d in cadence.get("days", [])]:
        reason = f"today ({today}) is not in this digest's days"
        print(json.dumps({"status": "skipped", "reason": reason}))
        return 0, args.briefing, f"skipped: {reason}"

    account = config_loader.digest_sender_account()
    if not account:
        detail = "no sender account configured for digests"
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1, args.briefing, detail
    to = config_loader.digest_recipient_email()
    if not to:
        detail = ("no recipient email resolvable (set digest.recipient_email "
                  "or configure a primary account)")
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1, args.briefing, detail

    start = time.time()
    try:
        to = clean_address(to, "recipient")
        with digest_lock():
            if not args.no_render:
                # Nobody is watching a scheduled render: keep the CLI current
                # (throttled to once a day) before spending the render on a
                # build the API will reject.
                claude_update.ensure_current(args.claude)
                self_anneal.with_retries(
                    lambda: render_briefing(args.briefing, claude=args.claude),
                    attempts=RENDER_ATTEMPTS,
                    backoff_s=RENDER_BACKOFF_S,
                    component=f"digest.render.{args.briefing}",
                )
            path = briefing_md_path(args.briefing)
            if not path.exists():
                raise RuntimeError(f"briefing did not produce {path}")
            # Fresh = rewritten by this run, or already written today (a skill
            # may legitimately skip rewriting a briefing that's still current).
            mtime = path.stat().st_mtime
            if (not args.no_render and mtime < start - MTIME_SLACK_S
                    and datetime.fromtimestamp(mtime).date() != datetime.now().date()):
                raise RuntimeError(
                    f"{path} was not refreshed by the /van-gogh:{args.briefing} run "
                    "(stale file; not sending)")
            body = path.read_text(encoding="utf-8")
            # The briefing exists and is current. Open it, because this is the
            # moment it came into being and a page nobody was told about is a
            # page nobody reads. Fails open in every direction: no Workbench, no
            # browser, no display, and the email still goes.
            open_local_page(args.briefing)
            # The email names the page: its link when the page is current, and
            # why it did not update plus the day it still shows when it is not.
            # A bookmarked link that silently goes stale is worse than no link,
            # and a page nobody was given a link to is worse than both.
            try:
                import briefing_html
                note = briefing_html.page_line(args.briefing)
                if note:
                    body = f"{body.rstrip()}\n\n{note}\n"
            except Exception:                                   # noqa: BLE001
                pass
            subject = build_subject(args.briefing)
            try:
                html = digest_html.md_to_email_html(body)
            except Exception as render_err:
                # An unattended scheduled send must not die on a render bug —
                # degrade to the plain-text-only email instead.
                print(f"WARNING: HTML render failed ({render_err}); "
                      "sending plain text", file=sys.stderr)
                html = None
            if html is not None and len(html.encode("utf-8")) > HTML_MAX_BYTES:
                print(f"WARNING: rendered HTML exceeds {HTML_MAX_BYTES} bytes "
                      "(Gmail clips it); sending plain text", file=sys.stderr)
                html = None
            try:
                send_email(account, to, subject, body, html=html)
            except Exception as send_exc:                       # noqa: BLE001
                # Deliberately not retried. A lost response after the server
                # accepted the message looks exactly like a failure, and the
                # cost of guessing wrong is a duplicate in someone's inbox.
                self_anneal.record_failure(
                    "digest_send.send", send_exc, attempts=1,
                    log_tail="Send may or may not have gone out. Check the Sent folder.")
                raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1, args.briefing, f"{type(e).__name__}: {e}"

    print(json.dumps({
        "status": "sent",
        "briefing": args.briefing,
        "subject": subject,
        "from": account["email"],
        "to": to,
        "file": str(path),
    }))
    return 0, args.briefing, "sent"


def main(argv: list[str] | None = None) -> int:
    """Run the digest and leave exactly one run-ledger row behind.

    The row is what makes "did the automations fire?" answerable. A skipped
    run counts as a run: it reached the job and decided correctly, which is a
    working scheduler, not a missing one.
    """
    started = run_ledger.now_stamp()
    briefing, rc, detail = "", 0, ""
    try:
        rc, briefing, detail = _run_digest(argv)
    except SystemExit:
        # argparse rejected the arguments: no job ran, so no row is owed.
        raise
    except Exception as exc:                                    # noqa: BLE001
        rc, detail = 1, f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {exc}", file=sys.stderr)
    finally:
        if briefing or rc:
            run_ledger.record_run(f"digest.{briefing or 'unknown'}", started,
                                  run_ledger.now_stamp(), rc, detail)
    return rc


if __name__ == "__main__":
    sys.exit(main())
