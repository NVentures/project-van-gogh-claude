#!/usr/bin/env python3
"""The Note tick: look, decide, write, mail, record. Every fifteen minutes.

    read      the last briefing's front page (its own sidecar), the hotcache,
              the ledger of Notes already sent or folded today, and the
              calendar snapshot the previous tick left
    fetch     inbound mail since the last tick, one list call per account,
              and today's calendar
    judge     `note_watch` decides what fires, what folds past the cap, and
              whether everything holds because a meeting is underway
    write     the line for each event into the briefing on screen, inside
              the Notes block, so the page stops being a snapshot
    render    `/van-gogh:note` headlessly, once per firing event: it drafts
              the reply (never sends), writes the Note in the house voice and
              republishes the page
    mail      the Note to the digest recipient, from the digest sender
    record    the ledger row, the calendar snapshot, the scorecard event

The rules live in `note_watch.py`, which is pure and tested. This module owns
the side effects: the mailbox, the subprocess, the files. Everything here
swallows its own errors. A tick that cannot build one Note still builds the
others; a tick that cannot mail records nothing and tries again in fifteen
minutes, because an unsent Note is recoverable and a crashed poller is not.

Off until `notes.enabled` is true in config. Quiet outside `notes.from` to
`notes.until`, local time.

CLI:
    note_send.py --claude /abs/claude        one tick (what the scheduler runs)
    note_send.py --dry-run                   fetch and judge, print, change nothing
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import claude_update
import config_loader
import digest_html
import note_watch
import run_ledger
from platform_compat import NO_WINDOW, claude_bin
from send_email import send_email

# Gmail clips a message past roughly 100KB. Same bound digest_send uses.
HTML_MAX_BYTES = 95_000

# Matches prep_send: a headless render is slow and sometimes needs a retry.
RENDER_TIMEOUT_S = 30 * 60
RENDER_ATTEMPTS = 2
RENDER_BACKOFF_S = (30, 120)

# The inbound fetch reaches back past the last tick by this much, so a message
# that landed while the previous tick was running is seen once, by this one.
# The ledger makes the overlap harmless.
FETCH_SLACK_MIN = 5
# With no previous tick on record, look back this far and no further.
FIRST_TICK_LOOKBACK_MIN = 20

_SAFE_ID = re.compile(r"[^A-Za-z0-9]+")


# ── Files ────────────────────────────────────────────────────────────────────

def ledger_path() -> Path:
    return config_loader.logs_dir() / "notes-sent.json"


def state_path() -> Path:
    """The calendar snapshot and the last tick time, kept apart from the
    ledger so pruning one never touches the other."""
    return config_loader.logs_dir() / "notes-state.json"


def events_dir() -> Path:
    return config_loader.logs_dir() / "notes"


def _read_json(path: Path) -> dict:
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    """Best effort: a file that cannot be written must not wedge the tick."""
    import json
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def prune_event_files(now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - note_watch.LEDGER_RETENTION_DAYS * 86400
    try:
        for path in events_dir().glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass


# ── The watch list ───────────────────────────────────────────────────────────

def _briefing_outputs() -> list:
    """(name, output) for each briefing that left a sidecar, dailies first."""
    out = []
    try:
        import workbench_data
        for name in ("morning-coffee", "afternoon-tea"):
            data = workbench_data.read_sidecar(name)
            if data:
                out.append((name, data))
    except Exception:                                           # noqa: BLE001
        pass
    week = _read_json(config_loader.full_sidecar_path())
    if week:
        out.append(("week", week))
    return out


def newest_briefing(outputs: list) -> tuple:
    """(name, output) of the briefing whose page is on screen: the last daily
    one listed with a front page (the caller lists them in render order), else
    the first with one, else ("", {})."""
    best = ("", {})
    for name, output in outputs:
        if not (output.get("front_page") or []):
            continue
        if name in ("morning-coffee", "afternoon-tea") or not best[0]:
            best = (name, output)
    return best


def briefing_md_path(name: str) -> Path | None:
    if name == "afternoon-tea":
        return config_loader.afternoon_tea_md_path()
    if name in ("morning-coffee", "week"):
        # The Week renders beside Morning Coffee on Mondays and has no page of
        # its own that a Note would correct; the morning page is on screen.
        return config_loader.morning_coffee_md_path()
    return None


def deal_headings() -> list:
    try:
        import collectors
        return [d["heading"] for d in
                collectors.hotcache_read(str(config_loader.hotcache_path()))]
    except Exception:                                           # noqa: BLE001
        return []


# ── Fetching ─────────────────────────────────────────────────────────────────

def _is_self(addr: str, account_email: str) -> bool:
    return bool(account_email) and account_email.lower() in (addr or "").lower()


def _internal(addr: str) -> bool:
    try:
        domain = (addr or "").rsplit("@", 1)[-1].lower()
        return domain in {d.lower() for d in config_loader.internal_domains()}
    except Exception:                                           # noqa: BLE001
        return False


def fetch_gmail_since(gclient, account_email: str, label: str,
                      since_utc: datetime) -> list:
    """New inbox messages since `since_utc`, newest last.

    One list call, then one metadata read per new message. On a quiet quarter
    hour that is one call; on a busy one it is a handful. Never the thread
    walk the briefings do: this asks "what just arrived", not "what is open".
    """
    from email.utils import parsedate_to_datetime
    epoch = int(since_utc.timestamp())
    try:
        data = gclient.gmail.users().messages().list(
            userId="me", q=f"in:inbox after:{epoch}", maxResults=50).execute()
    except Exception:                                           # noqa: BLE001
        return []
    out = []
    for stub in data.get("messages", []) or []:
        mid = stub.get("id")
        if not mid:
            continue
        try:
            msg = gclient.gmail.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
        except Exception:                                       # noqa: BLE001
            continue
        hdrs = {h["name"]: h["value"] for h in
                (msg.get("payload") or {}).get("headers", [])}
        from_raw = hdrs.get("From", "")
        m = re.search(r"<([^>]+)>", from_raw)
        from_addr = (m.group(1) if m else from_raw).strip()
        from_name = (from_raw[: from_raw.index("<")].strip().strip('"')
                     if "<" in from_raw else from_addr)
        if not from_addr or _is_self(from_addr, account_email):
            continue
        try:
            received = datetime.fromtimestamp(
                int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            try:
                received = parsedate_to_datetime(hdrs.get("Date", "")).astimezone(timezone.utc)
            except Exception:                                   # noqa: BLE001
                received = since_utc
        out.append({
            "subject": hdrs.get("Subject", "(No subject)"),
            "from": from_name or from_addr,
            "from_email": from_addr,
            "account": label,
            "snippet": msg.get("snippet", "") or "",
            "received": received.isoformat(timespec="seconds"),
            "message_id": str(mid),
            "internal": _internal(from_addr),
        })
    out.sort(key=lambda r: r["received"])
    return out


def fetch_outlook_since(label: str, since_utc: datetime) -> list:
    """New inbox messages since `since_utc`. One call."""
    from microsoft_client import microsoft_client
    stamp = since_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = microsoft_client(label).folder_messages(
            "inbox",
            filter=f"receivedDateTime ge {stamp}",
            select="id,subject,receivedDateTime,from,bodyPreview",
            top=50,
        )
    except Exception:                                           # noqa: BLE001
        return []
    out = []
    for msg in data.get("value", []) or []:
        ea = (msg.get("from") or {}).get("emailAddress") or {}
        from_addr = ea.get("address", "") or ""
        if not from_addr:
            continue
        out.append({
            "subject": msg.get("subject", "") or "(No subject)",
            "from": ea.get("name") or from_addr,
            "from_email": from_addr,
            "account": label,
            "snippet": msg.get("bodyPreview", "") or "",
            "received": (msg.get("receivedDateTime", "") or "").replace("Z", "+00:00"),
            "message_id": str(msg.get("id") or ""),
            "internal": _internal(from_addr),
        })
    out.sort(key=lambda r: r["received"])
    return out


def fetch_inbound(since_utc: datetime) -> list:
    """Everything that arrived since `since_utc`, across every account."""
    items = []
    for a in config_loader.google_accounts():
        try:
            from google_client import google_client
            items += fetch_gmail_since(google_client(a["label"]), a["email"],
                                       a["label"], since_utc)
        except Exception:                                       # noqa: BLE001
            continue
    for a in config_loader.microsoft_accounts():
        try:
            items += fetch_outlook_since(a["label"], since_utc)
        except Exception:                                       # noqa: BLE001
            continue
    return items


def _calendar():
    """Today's calendar, or an empty list if it cannot be read."""
    import meeting_prep
    return meeting_prep.merge_events(meeting_prep.fetch_all_events(days_ahead=1))


def _fmt(dt_utc: datetime) -> str:
    """`10:42 AM PT / 1:42 PM ET`, both zones, from the user's config."""
    from platform_compat import fmt_local_time
    return fmt_local_time(dt_utc, config_loader.user_tz(),
                          config_loader.user_secondary_tz())


def _parse(iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Rendering and mailing ────────────────────────────────────────────────────

def render_note(event_path: Path, claude: str | None = None) -> str:
    """Run the note skill headlessly and return the Note it printed."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["VAN_GOGH_UNATTENDED"] = "1"
    result = subprocess.run(
        [claude or claude_bin(), "-p", f'/van-gogh:note "{event_path}"',
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
            f"note exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[-2000:]}")
    body = (result.stdout or "").strip()
    if not body:
        raise RuntimeError("note produced no output")
    return body


def render_with_retry(event_path: Path, claude: str | None = None) -> str:
    last = None
    for attempt in range(RENDER_ATTEMPTS):
        try:
            return render_note(event_path, claude=claude)
        except Exception as exc:                                # noqa: BLE001
            last = exc
            if attempt + 1 < RENDER_ATTEMPTS:
                time.sleep(RENDER_BACKOFF_S[attempt])
    raise last if last else RuntimeError("render failed")


def mail(subject: str, body: str) -> None:
    """Send one Note. Sender and recipient come from the digest block, for
    the same reason prep emails do: one place to say where mail goes."""
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
    # message is indistinguishable from a real failure.
    send_email(account, to, subject, body, html=html)


def subject_for(event: dict) -> str:
    """`Note: Foster Lin replied on Duke study results`. Name first, because it
    is the word the eye catches; no time, because the body carries both zones."""
    kind = event.get("kind")
    if kind == "reply":
        item = event.get("item") or {}
        who = item.get("counterparty_name") or (event.get("mail") or {}).get("from") or "They"
        text = f"Note: {who} replied on {item.get('subject') or 'a thread'}"
    elif kind == "deal":
        text = f"Note: {event.get('thread') or 'a deal'} reads like a pass"
    elif kind == "calendar":
        title = event.get("title") or "A meeting"
        text = (f"Note: {title} is off the calendar" if event.get("change") == "gone"
                else f"Note: {title} moved to {event.get('new_time') or 'a new time'}")
    else:
        text = "Note"
    return note_watch._strip_dashes(text)


# ── The tick ─────────────────────────────────────────────────────────────────

def _enrich(event: dict, now: datetime) -> dict:
    """Add the rendered times the line and the skill both need."""
    event = dict(event)
    if event["kind"] == "calendar":
        old = _parse(event.get("old_start", ""))
        new = _parse(event.get("new_start", ""))
        event["old_time"] = _fmt(old) if old else ""
        event["new_time"] = _fmt(new) if new else ""
        event["time"] = _fmt(now)
    else:
        received = _parse(event.get("received", "")) or now
        event["time"] = _fmt(received)
    return event


def _account_for(label: str) -> dict:
    for a in config_loader.accounts():
        if a.get("label", "").lower() == (label or "").lower():
            return {"label": a.get("label", ""), "provider": a.get("provider", ""),
                    "email": a.get("email", "")}
    return {}


def _write_event_file(event: dict, extra: dict, now: datetime) -> Path:
    stem = f"{now.strftime('%Y%m%d-%H%M%S')}-{_SAFE_ID.sub('-', event['id'])[:40]}"
    path = events_dir() / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note_watch.event_file_payload(event, extra), encoding="utf-8")
    return path


def _patch_briefing(md_path: Path | None, lines: list) -> bool:
    """Rewrite the Notes block in the briefing on screen. Best effort."""
    if md_path is None:
        return False
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    new = note_watch.patch_notes(text, lines)
    if new == text:
        return True
    try:
        md_path.write_text(new, encoding="utf-8")
        return True
    except OSError:
        return False


def tick(now: datetime | None = None, claude: str | None = None,
         dry_run: bool = False) -> dict:
    """One pass. Returns a small summary for the log and the dry run."""
    now = now or datetime.now(timezone.utc)
    summary = {"fired": [], "folded": [], "held": 0, "skipped": ""}
    if not config_loader.notes_enabled():
        summary["skipped"] = "notes are off"
        return summary
    now_local = now.astimezone(config_loader.user_tz())
    start, end = config_loader.notes_window()
    if not note_watch.in_window(now_local, start, end):
        summary["skipped"] = f"outside {start} to {end}"
        return summary
    today = now_local.date().isoformat()

    ledger = note_watch.prune(_read_json(ledger_path()), now=now)
    state = _read_json(state_path())
    last_tick = _parse(state.get("last_tick", ""))
    since = (last_tick - timedelta(minutes=FETCH_SLACK_MIN) if last_tick
             else now - timedelta(minutes=FIRST_TICK_LOOKBACK_MIN))

    try:
        calendar = _calendar()
    except Exception as exc:                                    # noqa: BLE001
        print(f"WARNING: calendar read failed: {exc}", file=sys.stderr)
        calendar = []
    snapshot = note_watch.calendar_snapshot(calendar)
    previous = state.get("snapshot") or {}
    # A snapshot from another day describes another day's meetings.
    if state.get("day") != today:
        previous = {}

    outputs = _briefing_outputs()
    name, _ = newest_briefing([(n, o) for n, o in outputs
                               if note_watch.covers_today(o, today)])
    watched = note_watch.watch_list(
        note_watch.union_front_page([o for _, o in outputs], today))
    inbound = fetch_inbound(since)

    from deal_status import detect_kills
    events = (note_watch.reply_events(inbound, watched)
              + note_watch.deal_events(inbound, deal_headings(), detect_kills)
              + note_watch.calendar_events(previous, snapshot, now))

    meeting_now = note_watch.in_meeting(calendar, now)
    fire, fold, hold = note_watch.select(
        events, ledger, today, config_loader.notes_daily_cap(), meeting_now)
    summary["held"] = len(hold)
    fire = [_enrich(e, now) for e in fire]
    fold = [_enrich(e, now) for e in fold]

    if dry_run:
        summary["fired"] = [subject_for(e) for e in fire]
        summary["folded"] = [note_watch.note_line(e, e["time"]) for e in fold]
        summary["watching"] = len(watched)
        summary["inbound"] = len(inbound)
        summary["meeting_now"] = meeting_now
        return summary

    if hold:
        # Nothing written, not even the snapshot: the same events come back
        # on the next tick, once the meeting is over.
        return summary

    md_path = briefing_md_path(name)
    if fire:
        # A render is about to spawn the CLI unattended; make sure it is the
        # current one (throttled to once a day, fails open).
        try:
            claude_update.ensure_current(claude)
        except Exception:                                       # noqa: BLE001
            pass
    for event in fire:
        started = run_ledger.now_stamp()
        others = note_watch.sent_today(ledger, today) + len(fold) + len(fire) - 1
        extra = {
            "time": event["time"],
            "today": today,
            "briefing": name,
            "briefing_md": str(md_path) if md_path else "",
            "account": _account_for((event.get("mail") or {}).get("account", "")),
            "others_moved_today": others,
        }
        try:
            path = _write_event_file(event, extra, now)
            body = render_with_retry(path, claude=claude)
            mail(subject_for(event), body)
        except Exception as exc:                                # noqa: BLE001
            # One Note failing costs that Note. The ledger is deliberately not
            # written, so the next tick tries again.
            print(f"WARNING: note failed for {subject_for(event)}: {exc}",
                  file=sys.stderr)
            run_ledger.record_run(f"note.{event['kind']}", started,
                                  run_ledger.now_stamp(), 1, str(exc))
            continue
        run_ledger.record_run(f"note.{event['kind']}", started,
                              run_ledger.now_stamp(), 0, subject_for(event))
        line = note_watch.note_line(event, event["time"],
                                    drafted=(event["kind"] == "reply"))
        ledger = note_watch.record(ledger, event, "sent", today, line, now=now)
        _write_json(ledger_path(), ledger)
        summary["fired"].append(subject_for(event))
        try:
            import kpi_events
            kpi_events.record("note", note_kind=event["kind"], status="sent")
        except Exception:                                       # noqa: BLE001
            pass
    for event in fold:
        line = note_watch.note_line(event, event["time"])
        ledger = note_watch.record(ledger, event, "folded", today, line, now=now)
        _write_json(ledger_path(), ledger)
        summary["folded"].append(line)

    _patch_briefing(md_path, [r["line"] for r in note_watch.notes_today(ledger, today)])
    _write_json(state_path(), {"snapshot": snapshot, "day": today,
                               "last_tick": now.isoformat(timespec="seconds")})
    prune_event_files(now)
    return summary


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Note tick.")
    parser.add_argument("--claude", metavar="PATH",
                        help="absolute path to the claude CLI (the scheduler "
                             "resolves this at sync time)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and judge, print what would fire, change nothing")
    args = parser.parse_args(argv)
    try:
        summary = tick(claude=args.claude, dry_run=args.dry_run)
    except Exception as exc:                                    # noqa: BLE001
        # The poller must never wedge. A tick that dies here is one missed
        # look; a traceback that stops the job is every future one.
        print(f"WARNING: note tick failed: {exc}", file=sys.stderr)
        return 0
    if args.dry_run:
        if summary.get("skipped"):
            print(f"skipped: {summary['skipped']}")
            return 0
        print(f"watching {summary.get('watching', 0)} front-page items, "
              f"{summary.get('inbound', 0)} inbound since last tick, "
              f"meeting now: {summary.get('meeting_now')}")
        for s in summary["fired"]:
            print(f"would send: {s}")
        for s in summary["folded"]:
            print(f"would fold: {s}")
        if summary["held"]:
            print(f"would hold {summary['held']} (meeting in progress)")
        if not (summary["fired"] or summary["folded"] or summary["held"]):
            print("nothing moved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
