"""claude_cli: the slot limiter, the throttle retry, the model fallback and
the usage cap, against the dated captures under tests/fixtures/outage/.

Every capture is the text a real failing run produced, kept verbatim
(values and all) so the discriminators are tested against the real shapes:
the two cap messages (2026-09-09, 2026-09-21), the 429 and 529 the API sends
when saturated (2026-09-12, 2026-09-14), the 404 for a model the account
cannot use (2026-09-30), a THIRD PARTY's "usage limit exceeded" (Granola,
2026-09-18) which must never read as the Claude cap, and a traceback nobody
recognises (2026-09-15) which must stay unknown.

_spawn is stubbed everywhere: no CLI is run and no model is called.
"""
import subprocess
import threading
import time
from pathlib import Path

import pytest

import claude_cli
import failure_class as fc

FIX = Path(__file__).parent / "fixtures" / "outage"


def capture(name):
    return (FIX / name).read_text(encoding="utf-8").strip()


CAP = capture("2026-09-09-claude-usage-cap.txt")
CAP_WEEKLY = capture("2026-09-21-claude-usage-cap-weekly.txt")
THROTTLE = capture("2026-09-12-claude-throttle-429.txt")
OVERLOADED = capture("2026-09-14-claude-overloaded-529.txt")
NO_MODEL = capture("2026-09-30-claude-model-not-found.txt")
GRANOLA = capture("2026-09-18-granola-usage-limit.txt")
UNKNOWN = capture("2026-09-15-unknown-traceback.txt")


def _cp(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(claude_cli.claude_update, "heal_if_stale", lambda text: False)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: None)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    monkeypatch.delenv("VAN_GOGH_MODEL_SLOTS", raising=False)
    claude_cli._reset_limiter()
    yield
    claude_cli._reset_limiter()


def _script(replies, log):
    it = iter(replies)

    def spawn(prompt, model, timeout):
        log.append(model)
        return next(it)
    return spawn


# ── The slot limiter ─────────────────────────────────────────────────────────

def _max_in_flight(monkeypatch, n_threads):
    in_flight, peak, lock = [0], [0], threading.Lock()

    def spawn(prompt, model, timeout):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.02)
        with lock:
            in_flight[0] -= 1
        return _cp(0, "[]")

    monkeypatch.setattr(claude_cli, "_spawn", spawn)
    threads = [threading.Thread(target=claude_cli.run_claude, args=("p",))
               for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return peak[0]


def test_at_most_three_model_calls_in_flight_by_default(monkeypatch):
    assert claude_cli.model_slots() == 6
    assert _max_in_flight(monkeypatch, 8) == 6


def test_the_slot_count_is_configurable(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_MODEL_SLOTS", "2")
    claude_cli._reset_limiter()
    assert _max_in_flight(monkeypatch, 6) == 2


def test_the_control_arm_has_no_limiter_like_the_base_commit(monkeypatch):
    """The base ran its classifier six calls at once with no limiter; a
    one-slot control arm would be slower than the code it is meant to
    reproduce, so under VAN_GOGH_SERIAL the limiter is inert."""
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    monkeypatch.setenv("VAN_GOGH_MODEL_SLOTS", "2")
    claude_cli._reset_limiter()
    assert claude_cli.model_slots() == claude_cli.SERIAL_MODEL_SLOTS
    assert _max_in_flight(monkeypatch, 6) == 6


def test_the_control_arm_neither_retries_nor_falls_back(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    claude_cli._reset_limiter()
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stderr=THROTTLE), _cp(0, "ok")], models))
    assert claude_cli.run_claude("p").returncode == 1 and len(models) == 1
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stderr=NO_MODEL), _cp(0, "ok")], models))
    assert claude_cli.run_claude("p", "claude-sonnet-5").returncode == 1
    assert models == ["claude-sonnet-5"]
    # A cap is still a cap on the control arm.
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stdout=CAP)], []))
    with pytest.raises(claude_cli.UsageCap):
        claude_cli.run_claude("p")


def test_a_bad_slot_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_MODEL_SLOTS", "lots")
    assert claude_cli.model_slots() == claude_cli.DEFAULT_MODEL_SLOTS
    monkeypatch.setenv("VAN_GOGH_MODEL_SLOTS", "0")
    assert claude_cli.model_slots() == 1


# ── Throttle retry ───────────────────────────────────────────────────────────

def test_a_429_is_retried_with_a_growing_pause(monkeypatch):
    pauses, models = [], []
    monkeypatch.setattr(claude_cli, "_sleep", pauses.append)
    monkeypatch.setattr(claude_cli, "_spawn", _script(
        [_cp(1, stderr=THROTTLE), _cp(1, stderr=THROTTLE), _cp(0, "[]")], models))
    r = claude_cli.run_claude("p", "claude-sonnet-5")
    assert r.returncode == 0 and r.stdout == "[]"
    assert models == ["claude-sonnet-5"] * 3
    assert pauses == [2, 5]


def test_an_overloaded_529_is_a_throttle_too(monkeypatch):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script(
        [_cp(1, stdout=OVERLOADED), _cp(0, "ok")], models))
    assert claude_cli.run_claude("p").returncode == 0
    assert len(models) == 2


def test_the_throttle_retry_is_bounded(monkeypatch):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stderr=THROTTLE)] * 6, models))
    r = claude_cli.run_claude("p")
    assert r.returncode == 1
    assert len(models) == 1 + len(claude_cli.THROTTLE_BACKOFF)


def test_an_ordinary_failure_is_not_retried(monkeypatch):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stderr=UNKNOWN)], models))
    r = claude_cli.run_claude("p")
    assert r.returncode == 1 and len(models) == 1
    assert fc.classify(UNKNOWN) == "unknown"


# ── Model fallback ───────────────────────────────────────────────────────────

def test_an_unavailable_model_is_tried_once_more_on_haiku(monkeypatch):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script(
        [_cp(1, stderr=NO_MODEL), _cp(0, "{}")], models))
    r = claude_cli.run_claude("p", "claude-sonnet-5")
    assert r.returncode == 0
    assert models == ["claude-sonnet-5", claude_cli.HAIKU_MODEL]


def test_no_fallback_when_haiku_itself_is_the_missing_model(monkeypatch):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stderr=NO_MODEL)] * 2, models))
    r = claude_cli.run_claude("p", claude_cli.HAIKU_MODEL)
    assert r.returncode == 1 and models == [claude_cli.HAIKU_MODEL]


def test_a_stale_cli_is_not_read_as_a_missing_model():
    stale = ("API Error: 400 Claude Code 2.1.139 does not support this model; "
             "version 2.1.251 or newer is required")
    assert not claude_cli.is_model_unavailable(stale)
    assert claude_cli.is_model_unavailable(NO_MODEL)


# ── The usage cap ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [CAP, CAP_WEEKLY])
def test_the_cap_raises_usage_cap_with_the_reset_time_readable(monkeypatch, text):
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stdout=text)], models))
    with pytest.raises(claude_cli.UsageCap) as info:
        claude_cli.run_claude("p")
    assert len(models) == 1, "a cap is never retried"
    assert fc.quota_reset_at(str(info.value)) is not None
    assert isinstance(info.value, RuntimeError)


def test_the_cap_is_recognised_on_a_zero_exit_too(monkeypatch):
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(0, stdout=CAP)], []))
    with pytest.raises(claude_cli.UsageCap):
        claude_cli.run_claude("p")


def test_a_long_answer_quoting_the_words_is_an_answer(monkeypatch):
    """A classification of an email that itself says "out of extra usage,
    resets 3pm" is a real reply, not a cap."""
    rows = ", ".join('{"id": %d, "summary": "%s"}' % (i, CAP) for i in range(12))
    answer = "[" + rows + "]"
    assert len(answer) > 400
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(0, stdout=answer)], []))
    assert claude_cli.run_claude("p").returncode == 0


def test_another_providers_usage_limit_is_not_the_cap(monkeypatch):
    """Granola's 429 says "usage limit exceeded". failure_class still calls
    it quota (hold the job), but it is a throttle here: retried, never raised
    as the Claude cap."""
    assert fc.classify(GRANOLA) == "quota"
    assert not fc.is_usage_cap(GRANOLA)
    models = []
    monkeypatch.setattr(claude_cli, "_spawn", _script(
        [_cp(1, stderr=GRANOLA), _cp(0, "[]")], models))
    r = claude_cli.run_claude("p")
    assert r.returncode == 0 and len(models) == 2


def test_the_discriminator_on_every_capture():
    assert fc.is_usage_cap(CAP) and fc.is_usage_cap(CAP_WEEKLY)
    for text in (THROTTLE, OVERLOADED, NO_MODEL, GRANOLA, UNKNOWN):
        assert not fc.is_usage_cap(text), text
    assert fc.classify(UNKNOWN) == "unknown"
    assert fc.classify(THROTTLE) == "quota"
    assert claude_cli.is_throttle(THROTTLE) and not claude_cli.is_throttle(CAP)


def test_the_non_agentic_flags_are_unchanged():
    assert claude_cli._NON_AGENTIC_FLAGS == [
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands"]


def test_the_slot_is_released_after_a_cap(monkeypatch):
    """A raise inside the limiter must not leak the slot."""
    monkeypatch.setenv("VAN_GOGH_MODEL_SLOTS", "1")
    claude_cli._reset_limiter()
    monkeypatch.setattr(claude_cli, "_spawn", _script([_cp(1, stdout=CAP), _cp(0, "ok")], []))
    with pytest.raises(claude_cli.UsageCap):
        claude_cli.run_claude("p")
    assert claude_cli.run_claude("p").returncode == 0


def test_the_judge_still_says_the_usage_limit_was_reached_on_a_raised_cap():
    import priority_judge

    def run(prompt, model=None, timeout=None):
        raise claude_cli.UsageCap(CAP)
    out = priority_judge.judge_bucket(
        {"display_name": "Acme", "priorities": [{"name": "Close the widget deal"}]},
        [{"subject": "Widget redline", "counterparty_name": "A", "body_preview": ""}],
        run=run)
    assert out["degraded"] and out["degraded_reason"] == "the usage limit was reached"
