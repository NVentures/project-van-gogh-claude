#!/usr/bin/env python3
"""Retries, a failure ledger, and an opt-in alert, for the unattended paths.

Nothing scheduled has a human watching it. A briefing that dies at 07:00 to a
one-off network blip should retry rather than leave the user with yesterday's
desk, and a briefing that dies the same way every morning should be visible
somewhere other than a log file nobody opens.

Three deliberate limits:

* **Retries wrap renders, never sends.** A retried render costs tokens. A
  retried send costs the user a duplicate email in someone else's inbox, and a
  lost response after the server already accepted the message is
  indistinguishable from a failure. `digest_send.send_email` is never wrapped.
* **The ledger fails open.** Recording a failure must never turn into a second
  failure, so every path here swallows its own errors.
* **The alert body carries no content.** `render_briefing` raises with the last
  2000 characters of the child's output, which is briefing text: names, deals,
  amounts. That tail goes in the local ledger and never into an email.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import config_loader
import failure_class

# Digits and paths are the two things that differ between two runs of the same
# underlying failure ("timed out after 1800s" vs "1801s", a tmp path with a
# fresh suffix). Collapsing both is what lets a signature dedupe across runs.
_DIGITS_RE = re.compile(r"\d+")
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w .@%+-]+){2,}")


def signature_for(exc: BaseException) -> str:
    """A stable id for "this same failure", across runs and across machines."""
    message = _PATH_RE.sub("PATH", str(exc))
    message = _DIGITS_RE.sub("N", message)
    raw = f"{type(exc).__name__}|{message}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ledger_path() -> Path:
    return config_loader.logs_dir() / "failures.jsonl"


def record_failure(component: str, exc: BaseException, attempts: int,
                   log_tail: str = "") -> None:
    """Append one line to the failure ledger. Never raises.

    Written with a single `write` call: two writes can interleave when a
    scheduled job and a Workbench job both fail in the same second.
    """
    try:
        signature = signature_for(exc)
        alerted = False
        alert_error = ""
        try:
            alerted = maybe_alert(component, signature, exc, attempts)
        except Exception as alert_exc:                          # noqa: BLE001
            alert_error = f"{type(alert_exc).__name__}: {alert_exc}"

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "component": component,
            "signature": signature,
            # What kind of failure, in the one vocabulary the watcher reads.
            # Classified from the message and the log tail together: the
            # useful text is sometimes in one and sometimes in the other.
            "error_class": failure_class.classify(f"{exc}\n{log_tail or ''}"),
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "attempts": attempts,
            "log_tail": (log_tail or "")[-4000:],
            "alerted": alerted,
        }
        if alert_error:
            record["alert_error"] = alert_error

        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:                                           # noqa: BLE001
        pass


def _alerted_recently(signature: str, days: int) -> bool:
    try:
        cutoff = datetime.now() - timedelta(days=days)
        with open(ledger_path(), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("signature") != signature or not row.get("alerted"):
                    continue
                if datetime.fromisoformat(str(row.get("ts"))) >= cutoff:
                    return True
    except (OSError, ValueError):
        return False
    return False


def maybe_alert(component: str, signature: str, exc: BaseException,
                attempts: int) -> bool:
    """Mail a content-free failure notice, if alerts are on and it is not a repeat.

    Ships off. `digest_send` imports this module, so its import here is lazy on
    purpose: a module-level import would break both files.
    """
    if not config_loader.alerts_enabled():
        return False
    if _alerted_recently(signature, config_loader.alert_cooldown_days()):
        return False

    import digest_send

    account = config_loader.digest_sender_account()
    if not account:
        return False
    recipients = [config_loader.digest_recipient_email(),
                  config_loader.support_team_email()]
    recipients = [r for r in dict.fromkeys(r.strip() for r in recipients) if r]
    if not recipients:
        return False

    # Content-free by construction: type, count, timestamp, signature. No
    # message text, no log tail, nothing from inside the user's mail or vault.
    body = (
        f"A scheduled Van Gogh step failed and did not recover.\n\n"
        f"Component: {component}\n"
        f"Failure type: {type(exc).__name__}\n"
        f"Attempts: {attempts}\n"
        f"Signature: {signature}\n"
        f"First seen: {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"Details were written to the local failure ledger at\n"
        f"{ledger_path()}\n"
        f"on the machine that ran it. Nothing from the briefing itself is in\n"
        f"this message.\n\n"
        f"If this was a send step, check your Sent folder before resending\n"
        f"anything by hand.\n"
    )
    for to in recipients:
        digest_send.send_email(account, to, f"Van Gogh: {component} failed", body)
    return True


def with_retries(fn: Callable[[], Any], attempts: int = 3,
                 backoff_s: tuple = (5, 30, 120), component: str = "",
                 log_tail: str = "", stop_on: frozenset = failure_class.NO_RETRY
                 ) -> Any:
    """Call `fn`, retrying on failure, then ledger the last error and re-raise.

    An interrupt is never retried: `KeyboardInterrupt` and `SystemExit` mean a
    human or the OS asked this to stop, and sleeping for two minutes before
    trying again is the opposite of what was asked.

    Neither is a failure whose class says a retry cannot help. A usage cap and
    an expired login both fail again in exactly the same way two minutes
    later, so a second attempt buys nothing and costs the budget the watcher
    needs after the cap resets. `stop_on=frozenset()` restores blind retries.
    """
    last: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:                            # noqa: BLE001
            last = exc
            if attempt >= attempts:
                break
            if failure_class.classify(f"{exc}\n{log_tail or ''}") in stop_on:
                break
            delay = backoff_s[min(attempt - 1, len(backoff_s) - 1)] if backoff_s else 0
            time.sleep(delay)

    if last is not None:
        record_failure(component or getattr(fn, "__name__", "unknown"),
                       last, attempts, log_tail)
        raise last
    return None
