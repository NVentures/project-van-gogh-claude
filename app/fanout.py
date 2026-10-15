#!/usr/bin/env python3
"""fanout.py: run one function over many items, a few at a time, losing none.

Every batch in the install pipeline (reply lookups, thread fetches, meeting
summaries) used to be either a plain loop or a bare ThreadPoolExecutor whose
failures were swallowed into "no reply" or dropped on the floor. This is the
one place that shape lives now, with three guarantees the callers can test:

* every input item ends up in exactly one of ``filed``, ``failed`` or
  ``deferred``, and the three lists together are the input, in input order,
  whatever order the work actually finished in;
* a throttle (``retry_on``) is retried with a growing pause, honouring a
  Retry-After header when the provider sends one, capped so a provider cannot
  park the install for a minute;
* a Claude usage cap (``claude_cli.UsageCap``) stops new work at once. The
  item that hit it and every item not yet started land in ``deferred`` with
  a count, and the result says ``degraded`` so the caller can report it.

``VAN_GOGH_SERIAL=1`` is the control arm and reproduces the SHIPPED code, not
a slower one: each call site names the width the base commit used
(``serial_width``, 1 for what was a plain loop, 8 for what was already an
8-wide pool) and no retry runs, because the base had none. The bookkeeping
is the same in both arms, so only the timing differs.
"""

import os
import re
import threading
import time

import claude_cli

# A provider's Retry-After is honoured but never past this: the install has a
# clock on it, and a 120 s hint is a reason to move on, not to wait.
RETRY_AFTER_CAP_S = 30

# The HTTP statuses that mean "try again shortly" rather than "this is wrong".
THROTTLE_STATUSES = (429, 503)

_THROTTLE_TEXT_RE = re.compile(
    r"\b429\b|\b503\b|rate[ _-]?limit|too many requests|throttl|quota exceeded",
    re.I)

# Replaceable in tests so a retry does not actually wait.
_sleep = time.sleep


def serial() -> bool:
    """True when the control arm asked for the old one-at-a-time path."""
    return os.environ.get("VAN_GOGH_SERIAL") == "1"


def width_for(width: int, serial_width: int = 1) -> int:
    """The width a pool really gets: `serial_width` under VAN_GOGH_SERIAL
    (what the base commit ran at that call site), else `width`."""
    return max(1, int(serial_width)) if serial() else max(1, int(width))


# ── Reading a throttle off whatever exception a client raised ────────────────

def _status_of(exc) -> int | None:
    """The HTTP status carried by `exc`, from any of the shapes the clients use.

    googleapiclient's HttpError keeps it on ``exc.resp.status``; requests (and
    so the Microsoft client) on ``exc.response.status_code``; a few wrappers
    put it on ``status_code``, ``status`` or ``code`` directly.
    """
    resp = getattr(exc, "resp", None)
    for obj, attr in ((resp, "status"), (getattr(exc, "response", None), "status_code"),
                      (exc, "status_code"), (exc, "status"), (exc, "code")):
        if obj is None:
            continue
        val = getattr(obj, attr, None)
        if isinstance(val, int) and 100 <= val <= 599:
            return val
    return None


def _headers_of(exc) -> dict:
    for obj in (getattr(exc, "resp", None), getattr(exc, "response", None), exc):
        if obj is None:
            continue
        headers = getattr(obj, "headers", None)
        if headers is None and hasattr(obj, "get") and hasattr(obj, "keys"):
            headers = obj
        if headers is not None:
            try:
                return {str(k).lower(): str(v) for k, v in dict(headers).items()}
            except Exception:                                   # noqa: BLE001
                continue
    return {}


def retry_after(exc) -> float | None:
    """Seconds a provider asked us to wait, capped at RETRY_AFTER_CAP_S.

    Only the delta-seconds form is read; an HTTP-date Retry-After is rare on
    these APIs and a wrong parse of one would wait for the wrong thing.
    """
    raw = _headers_of(exc).get("retry-after", "")
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_CAP_S)


def is_throttle(exc) -> bool:
    """True when `exc` is a provider saying "not right now".

    An HTTP 429 or 503 on any client shape, or throttle wording in the text
    (a bare "rate limit", "too many requests"). A Claude usage cap is its own
    exception type and never reaches this predicate as a throttle.
    """
    if isinstance(exc, claude_cli.UsageCap):
        return False
    status = _status_of(exc)
    if status in THROTTLE_STATUSES:
        return True
    return bool(_THROTTLE_TEXT_RE.search(str(exc) or ""))


def _should_retry(exc, retry_on) -> bool:
    if not retry_on:
        return False
    if isinstance(retry_on, (tuple, list)):
        types = tuple(t for t in retry_on if isinstance(t, type))
        preds = [t for t in retry_on if callable(t) and not isinstance(t, type)]
        if types and isinstance(exc, types):
            return True
        return any(p(exc) for p in preds)
    if isinstance(retry_on, type):
        return isinstance(exc, retry_on)
    return bool(retry_on(exc))


def _pause(exc, backoff, index) -> float:
    base = 0.0
    if backoff:
        base = float(backoff[min(index, len(backoff) - 1)])
    hinted = retry_after(exc)
    if hinted is not None:
        base = max(base, hinted)
    return min(base, RETRY_AFTER_CAP_S)


def _describe(exc) -> str:
    """The exception's type and a short slice of its text.

    The text is kept because a failed lookup needs to say why in the errors
    list; it is sliced because exception text quotes its arguments.
    """
    return f"{type(exc).__name__}: {str(exc)[:200]}".rstrip(": ")


# ── The pool ─────────────────────────────────────────────────────────────────

def run(items, fn, width, key=None, retry_on=(), attempts=3,
        backoff=(2, 5, 10), on_cap=None, serial_width=1):
    """Apply `fn` to every item, at most `width` at a time.

    Returns ``{"filed": [...], "failed": [...], "deferred": [...],
    "degraded": None | {"reason": "usage_cap", "deferred": N}}``. Each entry
    is a dict carrying ``key`` (``key(item)``, else the input index), the
    ``item`` itself, and for ``filed`` the ``result``, for ``failed`` the
    ``error`` text and ``attempts`` made, for ``deferred`` the ``reason``.

    `retry_on` is a tuple of exception types, a predicate, or a mix; a
    matching exception is retried up to `attempts` times with `backoff`
    pauses, a Retry-After header raising the pause up to RETRY_AFTER_CAP_S.
    `on_cap(exc)` is called once, from the thread that first saw the cap.
    Under VAN_GOGH_SERIAL=1 the pool is `serial_width` wide and makes one
    attempt per item: the control arm is the base commit's behaviour.

    The lists are built from a per-index outcome table after every worker has
    stopped, so output order is input order whatever the completion order,
    and an item is in exactly one list because it has exactly one outcome.
    """
    items = list(items)
    n = len(items)
    outcomes = [None] * n
    cap = threading.Event()
    lock = threading.Lock()
    cursor = [0]
    attempts = 1 if serial() else max(1, int(attempts))

    def _next_index():
        with lock:
            if cap.is_set() or cursor[0] >= n:
                return None
            i = cursor[0]
            cursor[0] += 1
            return i

    def _one(item):
        last = None
        for attempt in range(1, attempts + 1):
            try:
                return ("filed", fn(item), attempt)
            except claude_cli.UsageCap as exc:
                with lock:
                    first = not cap.is_set()
                    cap.set()
                if first and on_cap is not None:
                    try:
                        on_cap(exc)
                    except Exception:                           # noqa: BLE001
                        pass
                return ("deferred", exc, attempt)
            except (Exception, SystemExit) as exc:             # noqa: BLE001
                last = exc
                if attempt < attempts and _should_retry(exc, retry_on):
                    _sleep(_pause(exc, backoff, attempt - 1))
                    continue
                return ("failed", exc, attempt)
        return ("failed", last, attempts)

    def _worker():
        while True:
            i = _next_index()
            if i is None:
                return
            outcomes[i] = _one(items[i])

    width = width_for(width, serial_width)
    if width == 1 or n <= 1:
        # The plain loop: same bookkeeping, no threads, which is what a
        # call site that was a loop at base and every single-item call get.
        _worker()
    else:
        threads = [threading.Thread(target=_worker, daemon=True)
                   for _ in range(min(width, n))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    filed, failed, deferred = [], [], []
    for i, item in enumerate(items):
        k = key(item) if key is not None else i
        outcome = outcomes[i]
        if outcome is None:
            deferred.append({"key": k, "item": item, "reason": "usage_cap"})
            continue
        kind, payload, made = outcome
        if kind == "filed":
            filed.append({"key": k, "item": item, "result": payload,
                          "attempts": made})
        elif kind == "deferred":
            deferred.append({"key": k, "item": item, "reason": "usage_cap"})
        else:
            failed.append({"key": k, "item": item,
                           "error": _describe(payload) if payload is not None
                           else "unknown", "attempts": made})

    degraded = None
    if cap.is_set():
        degraded = {"reason": "usage_cap", "deferred": len(deferred)}
    return {"filed": filed, "failed": failed, "deferred": deferred,
            "degraded": degraded}
