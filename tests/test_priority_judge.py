"""The judgment layer: reads meaning, and cannot hurt anyone when it breaks.

Two things are being proven here. First, that it actually understands a
priority written as an intention, which is the failure that motivated it: a
person writes "grow the pipeline" and the email says "redline attached, need
signature by Friday". Those share no words and mean the same thing. Second,
and more important, that every way it can fail leaves the reader seeing MORE
rather than less.
"""
import json
import os
import shutil
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config_loader as cl                                      # noqa: E402
import priority_judge as pj                                     # noqa: E402

BUCKET = {
    "display_name": "Power Market",
    "priorities": [{"name": "grow the pipeline"}, {"name": "keep the team happy"}],
}
ITEMS = [
    {"subject": "Redline attached, need signature by Friday",
     "counterparty_name": "Dana", "body_preview": "Final markup on the QX deal."},
    {"subject": "Your monthly newsletter is here",
     "counterparty_name": "Marketing", "body_preview": "Ten trends to watch."},
]


def _reply(rows):
    return CompletedProcess([], 0, stdout=json.dumps(rows), stderr="")


# ── The model cannot invent anything ─────────────────────────────────────────

def test_invented_priority_is_discarded():
    run = lambda *a, **k: _reply([                              # noqa: E731
        {"id": 0, "relevant": True, "priority": "a priority nobody wrote",
         "reason": "r"}])
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    # Still relevant (every rail leans toward showing), but the invented
    # heading is gone.
    assert out["verdicts"][0]["relevant"] is True
    assert out["verdicts"][0]["priority"] == ""


def test_verbatim_priority_survives():
    run = lambda *a, **k: _reply([                              # noqa: E731
        {"id": 0, "relevant": True, "priority": "grow the pipeline", "reason": "r"}])
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"][0]["priority"] == \
        "grow the pipeline"


def test_out_of_range_id_is_discarded():
    run = lambda *a, **k: _reply([{"id": 99, "relevant": True}])  # noqa: E731
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"] == {}


def test_unjudged_item_gets_no_verdict():
    # An item the model skipped must have no verdict at all, so the caller
    # treats it as unjudged rather than as judged-irrelevant.
    run = lambda *a, **k: _reply([{"id": 0, "relevant": True}])  # noqa: E731
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert 1 not in out["verdicts"]


# ── Every failure mode fails safe ────────────────────────────────────────────

def test_non_zero_exit_is_degraded():
    run = lambda *a, **k: CompletedProcess([], 1, stdout="", stderr="boom")  # noqa: E731
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_timeout_is_degraded():
    def run(*a, **k):
        raise TimeoutError("timed out")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_unparseable_output_is_degraded():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 0, stdout="I would be happy to help with that!", stderr="")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True and out["verdicts"] == {}


def test_quota_wall_is_degraded_and_says_so():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 1, stdout="You're out of extra usage, resets 3:40pm", stderr="")
    out = pj.judge_bucket(BUCKET, ITEMS, run=run)
    assert out["degraded"] is True
    assert "usage limit" in out["degraded_reason"]


def test_degraded_reason_is_written_for_a_person():
    run = lambda *a, **k: CompletedProcess([], 1, stdout="", stderr="boom")  # noqa: E731
    reason = pj.judge_bucket(BUCKET, ITEMS, run=run)["degraded_reason"]
    assert reason and reason[0].islower() and "Exception" not in reason


# ── Prose in front of the JSON ───────────────────────────────────────────────

def test_prose_before_the_json_is_tolerated():
    run = lambda *a, **k: CompletedProcess(  # noqa: E731
        [], 0, stdout='Sure, here you go:\n[{"id": 0, "relevant": true}]\n', stderr="")
    assert pj.judge_bucket(BUCKET, ITEMS, run=run)["verdicts"][0]["relevant"] is True


# ── No work, no call ─────────────────────────────────────────────────────────

def test_bucket_with_no_priorities_never_calls_the_model():
    calls = []
    def run(*a, **k):
        calls.append(1)
        return _reply([])
    pj.judge_bucket({"display_name": "X", "priorities": []}, ITEMS, run=run)
    assert calls == []


def test_no_items_never_calls_the_model():
    calls = []
    def run(*a, **k):
        calls.append(1)
        return _reply([])
    pj.judge_bucket(BUCKET, [], run=run)
    assert calls == []


# ── The deadline detector ────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Countersignature needed by Friday",
    "Contract expires 2026-09-30",
    "Please return by EOD",
    "Deadline for the filing",
    "Docs due 9/30",
])
def test_deadline_language_is_detected(subject):
    assert pj.has_deadline({"subject": subject}) is True


@pytest.mark.parametrize("subject", [
    "Quick question about the office",
    "Newsletter: ten trends to watch",
])
def test_ordinary_subjects_are_not_deadlines(subject):
    assert pj.has_deadline({"subject": subject}) is False


def test_days_late_counts_as_a_deadline():
    assert pj.has_deadline({"subject": "anything", "days_late": 3}) is True


# ── Live: the case the whole layer exists for ────────────────────────────────

# CI has no `claude` binary, so this skips there with a reason rather than
# failing for the absence of a tool the product legitimately requires. It runs
# on any machine that has Claude Code installed, which is every machine this
# actually ships to.
@pytest.mark.skipif(
    os.environ.get("VAN_GOGH_SKIP_LIVE") == "1" or shutil.which("claude") is None,
    reason="needs the claude CLI on PATH (absent in CI)")
def test_live_vague_priority_is_understood():
    """A priority written as an intention, matched against an email that
    shares none of its words. A string matcher scores zero here; that is the
    entire reason this layer was built, so it is checked against the real
    model rather than a stub."""
    out = pj.judge_bucket(BUCKET, ITEMS, model=pj.DEFAULT_JUDGE_MODEL, timeout=180)
    assert out["degraded"] is False, out["degraded_reason"]
    assert out["verdicts"][0]["relevant"] is True, "signature deadline read as off-priority"
    assert out["verdicts"][1]["relevant"] is False, "newsletter read as on-priority"
    assert out["verdicts"][0]["reason"], "a demotion or a keep must be explainable"


# ── Standing user guidance (the vault preamble) ──────────────────────────────

def test_preamble_reaches_the_prompt(monkeypatch):
    monkeypatch.setattr(cl, "judgment_preamble",
                        lambda: "Weekend emails from vendors never count.")
    prompt = pj.build_prompt(BUCKET, ITEMS)
    assert "Weekend emails from vendors never count." in prompt
    assert "standing guidance" in prompt


def test_no_preamble_leaves_the_prompt_unchanged(monkeypatch):
    monkeypatch.setattr(cl, "judgment_preamble", lambda: "")
    assert "standing guidance" not in pj.build_prompt(BUCKET, ITEMS)


def test_preamble_reads_the_vault_file_and_is_capped():
    target = cl.judgment_preamble_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text("guidance here\n", encoding="utf-8")
        assert cl.judgment_preamble() == "guidance here"
        target.write_text("x" * 10000, encoding="utf-8")
        assert len(cl.judgment_preamble()) == cl.JUDGMENT_PREAMBLE_MAX_CHARS
    finally:
        target.unlink(missing_ok=True)


def test_missing_preamble_file_is_empty_not_fatal():
    assert not cl.judgment_preamble_path().exists()
    assert cl.judgment_preamble() == ""
