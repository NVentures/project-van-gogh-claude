"""fanout: nothing lost under concurrency (P10).

Twenty randomized runs, each with a different item count, a different
completion order (workers sleep a random few milliseconds), injected
throttles that fail once and then succeed, and hard failures. Every run must
partition the input exactly, in input order, retry the throttled items
(asserted by the attempt counter the pool reports), and give the serial arm
the same answer.
"""
import os
import random
import threading
import time
from pathlib import Path

import pytest

import claude_cli
import fanout

CAP_TEXT = (Path(__file__).parent / "fixtures" / "outage"
            / "2026-09-09-claude-usage-cap.txt").read_text(encoding="utf-8").strip()


class Throttle(Exception):
    """A requests-shaped 429 with a Retry-After header."""

    def __init__(self, retry_after="7"):
        super().__init__("429 Too Many Requests")
        self.response = type("R", (), {"status_code": 429,
                                       "headers": {"Retry-After": retry_after}})()


class HttpErrorShaped(Exception):
    """googleapiclient.errors.HttpError keeps the status on resp.status."""

    def __init__(self, status):
        super().__init__(f"<HttpError {status}>")
        self.resp = type("Resp", (), {"status": status, "headers": {}})()


def _scenario(seed):
    rng = random.Random(seed)
    n = rng.randint(5, 30)
    items = [f"item-{i:02d}" for i in range(n)]
    throttled = set(rng.sample(items, k=min(n, rng.randint(1, 4))))
    failing = set(rng.sample([i for i in items if i not in throttled],
                             k=min(n - len(throttled), rng.randint(0, 3))))
    return items, throttled, failing, rng


def _worker(throttled, failing, calls, lock, jitter):
    def fn(item):
        with lock:
            calls[item] = calls.get(item, 0) + 1
            n = calls[item]
        if jitter:
            time.sleep(random.random() * 0.003)
        if item in failing:
            raise ValueError(f"hard failure on {item}")
        if item in throttled and n == 1:
            raise Throttle()
        return item.upper()
    return fn


def _keys(result, kind):
    return [e["key"] for e in result[kind]]


@pytest.mark.parametrize("seed", range(20))
def test_every_item_lands_in_exactly_one_list_in_input_order(seed, monkeypatch):
    monkeypatch.setattr(fanout, "_sleep", lambda s: None)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    items, throttled, failing, rng = _scenario(seed)
    calls, lock = {}, threading.Lock()
    width = rng.randint(2, 6)

    res = fanout.run(items, _worker(throttled, failing, calls, lock, jitter=True),
                     width=width, key=lambda s: s, retry_on=(Throttle,))

    filed, failed, deferred = _keys(res, "filed"), _keys(res, "failed"), _keys(res, "deferred")
    # Disjoint, complete, and in input order whatever order the work finished.
    assert not (set(filed) & set(failed)) and not (set(filed) & set(deferred))
    assert not (set(failed) & set(deferred))
    assert sorted(filed + failed + deferred) == sorted(items)
    assert filed == [i for i in items if i not in failing]
    assert failed == [i for i in items if i in failing]
    assert deferred == []
    assert res["degraded"] is None
    # A throttled item was retried: its entry reports two attempts, and the
    # function was really called twice for it (the branch is hit, not just
    # declared).
    by_key = {e["key"]: e for e in res["filed"]}
    for item in throttled:
        assert by_key[item]["attempts"] == 2
        assert calls[item] == 2
    for item in items:
        if item not in throttled and item not in failing:
            assert by_key[item]["attempts"] == 1
    # A hard failure is not retried and keeps its reason.
    for e in res["failed"]:
        assert e["attempts"] == 1 and "ValueError" in e["error"]


@pytest.mark.parametrize("seed", range(20))
def test_the_serial_arm_gives_the_identical_answer(seed, monkeypatch):
    """With nothing to retry, the control arm partitions exactly as the
    fast arm does. (Throttles are a separate case below: the control arm
    reproduces the base commit, which never retried.)"""
    monkeypatch.setattr(fanout, "_sleep", lambda s: None)
    items, _throttled, failing, rng = _scenario(seed)
    lock = threading.Lock()

    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    calls_p = {}
    parallel = fanout.run(items, _worker(set(), failing, calls_p, lock, True),
                          width=4, key=lambda s: s, retry_on=(Throttle,))

    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    calls_s = {}
    threads_before = threading.active_count()
    serial = fanout.run(items, _worker(set(), failing, calls_s, lock, False),
                        width=4, key=lambda s: s, retry_on=(Throttle,))
    assert threading.active_count() == threads_before, \
        "serial_width 1 is a plain loop and spawns nothing"

    strip = lambda r: {k: [(e["key"], e.get("result"), e.get("attempts"),
                            e.get("error"), e.get("reason")) for e in r[k]]
                       for k in ("filed", "failed", "deferred")}
    assert strip(parallel) == strip(serial)
    assert calls_p == calls_s


def test_the_control_arm_never_retries_because_the_base_did_not(monkeypatch):
    pauses = []
    monkeypatch.setattr(fanout, "_sleep", pauses.append)
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    calls = {}
    res = fanout.run(["a", "b"], _worker({"a"}, set(), calls, threading.Lock(), False),
                     width=4, key=lambda s: s, retry_on=(Throttle,), attempts=3)
    assert _keys(res, "failed") == ["a"] and res["failed"][0]["attempts"] == 1
    assert _keys(res, "filed") == ["b"]
    assert calls == {"a": 1, "b": 1} and pauses == []


def test_serial_width_reproduces_a_base_pool_not_a_loop(monkeypatch):
    """A call site that was an 8-wide pool at base runs 8 wide under the
    control arm; one that was a loop runs as a loop."""
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    assert fanout.width_for(4, serial_width=8) == 8
    assert fanout.width_for(8, serial_width=1) == 1
    monkeypatch.delenv("VAN_GOGH_SERIAL")
    assert fanout.width_for(4, serial_width=8) == 4

    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    in_flight, peak, lock = [0], [0], threading.Lock()

    def fn(item):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.01)
        with lock:
            in_flight[0] -= 1
        return item

    res = fanout.run(list(range(16)), fn, width=2, serial_width=8)
    assert len(res["filed"]) == 16
    assert 2 < peak[0] <= 8, "the control arm ran at the base width, not 1"


def test_a_usage_cap_stops_new_work_and_defers_the_rest(monkeypatch):
    """The item that hit the cap and every item not yet started are deferred,
    with a count; what had already finished stays filed."""
    monkeypatch.setattr(fanout, "_sleep", lambda s: None)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    items = [f"m{i}" for i in range(12)]
    started, lock = [], threading.Lock()
    seen_cap = []

    def fn(item):
        with lock:
            started.append(item)
        if item == "m3":
            raise claude_cli.UsageCap(CAP_TEXT)
        time.sleep(0.002)
        return item

    res = fanout.run(items, fn, width=2, key=lambda s: s,
                     on_cap=lambda exc: seen_cap.append(str(exc)))

    deferred = set(_keys(res, "deferred"))
    assert "m3" in deferred
    assert set(items) - set(started) <= deferred, "an unstarted item is deferred"
    assert set(_keys(res, "filed")) | deferred == set(items)
    assert not _keys(res, "failed")
    assert res["degraded"] == {"reason": "usage_cap", "deferred": len(deferred)}
    assert len(deferred) >= len(items) - 4, "the pool stopped submitting"
    assert seen_cap == [CAP_TEXT], "on_cap fires once with the CLI's text"
    # Input order within each list.
    assert _keys(res, "deferred") == [i for i in items if i in deferred]


def test_a_usage_cap_in_the_serial_arm_defers_everything_after_it(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    items = list("abcdef")

    def fn(item):
        if item == "c":
            raise claude_cli.UsageCap(CAP_TEXT)
        return item

    res = fanout.run(items, fn, width=5, key=lambda s: s)
    assert _keys(res, "filed") == ["a", "b"]
    assert _keys(res, "deferred") == ["c", "d", "e", "f"]
    assert res["degraded"] == {"reason": "usage_cap", "deferred": 4}


def test_retry_after_is_honoured_and_capped(monkeypatch):
    pauses = []
    monkeypatch.setattr(fanout, "_sleep", pauses.append)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)  # width 1 is a loop anyway
    calls = {"a": 0, "b": 0}

    def fn(item):
        calls[item] += 1
        if calls[item] == 1:
            raise Throttle(retry_after="7" if item == "a" else "120")
        return item

    res = fanout.run(["a", "b"], fn, width=1, retry_on=fanout.is_throttle,
                     backoff=(2, 5, 10))
    assert len(res["filed"]) == 2
    # 7 s beats the 2 s backoff; 120 s is capped at RETRY_AFTER_CAP_S.
    assert pauses == [7.0, float(fanout.RETRY_AFTER_CAP_S)]


def test_attempts_are_bounded(monkeypatch):
    pauses = []
    monkeypatch.setattr(fanout, "_sleep", pauses.append)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    n = {"x": 0}

    def fn(item):
        n["x"] += 1
        raise Throttle(retry_after="")

    res = fanout.run(["x"], fn, width=1, retry_on=(Throttle,), attempts=3,
                     backoff=(2, 5, 10))
    assert n["x"] == 3 and res["failed"][0]["attempts"] == 3
    assert pauses == [2.0, 5.0]


@pytest.mark.parametrize("exc, expected", [
    (Throttle(), True),
    (HttpErrorShaped(429), True),
    (HttpErrorShaped(503), True),
    (HttpErrorShaped(404), False),
    (RuntimeError("rate limit exceeded, slow down"), True),
    (RuntimeError("Too Many Requests"), True),
    (RuntimeError("KeyError: 'value'"), False),
    (claude_cli.UsageCap(CAP_TEXT), False),
])
def test_is_throttle_reads_every_client_shape(exc, expected):
    assert fanout.is_throttle(exc) is expected


def test_retry_on_accepts_types_predicates_and_a_mix():
    exc = HttpErrorShaped(503)
    assert fanout._should_retry(exc, (HttpErrorShaped,))
    assert fanout._should_retry(exc, fanout.is_throttle)
    assert fanout._should_retry(exc, (ValueError, fanout.is_throttle))
    assert not fanout._should_retry(exc, (ValueError,))
    assert not fanout._should_retry(exc, ())


def test_a_system_exit_inside_the_work_is_a_failure_not_a_dead_thread(monkeypatch):
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)

    def fn(item):
        if item == 2:
            raise SystemExit(13)
        return item

    res = fanout.run([1, 2, 3], fn, width=3)
    assert _keys(res, "filed") == [0, 2] and _keys(res, "failed") == [1]
    assert "SystemExit" in res["failed"][0]["error"]


def test_width_for_is_one_under_serial(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    assert fanout.width_for(8) == 1
    monkeypatch.delenv("VAN_GOGH_SERIAL")
    assert fanout.width_for(8) == 8
    assert os.environ.get("VAN_GOGH_SERIAL") is None
