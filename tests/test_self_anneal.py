"""self_anneal: retries, the failure ledger, and the opt-in alert.

`time.sleep` is stubbed everywhere so the whole file runs in well under a
second; the real backoff is 5 + 30 + 120 seconds. `digest_send.send_email` is
stubbed everywhere too, so no test here can ever attempt to send mail.
"""
import json

import pytest

import config_loader as cl
import self_anneal


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(self_anneal.time, "sleep", lambda _s: None)


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(self_anneal.config_loader, "logs_dir", lambda: tmp_path)
    return tmp_path


def read_ledger(logs):
    path = logs / "failures.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── with_retries ─────────────────────────────────────────────────────────────

def test_retries_then_succeeds(logs):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    assert self_anneal.with_retries(flaky, attempts=3, backoff_s=(0, 0)) == "ok"
    assert len(calls) == 3
    assert read_ledger(logs) == []          # a recovered run is not a failure


def test_exhausts_and_reraises_the_last_error(logs):
    def always():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        self_anneal.with_retries(always, attempts=2, backoff_s=(0,), component="x")
    assert len(read_ledger(logs)) == 1


def test_keyboard_interrupt_is_never_retried(logs):
    calls = []

    def interrupted():
        calls.append(1)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        self_anneal.with_retries(interrupted, attempts=3, backoff_s=(0, 0))
    assert calls == [1]
    assert read_ledger(logs) == []          # an interrupt is not a defect to ledger


def test_system_exit_is_never_retried(logs):
    calls = []

    def exiting():
        calls.append(1)
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        self_anneal.with_retries(exiting, attempts=3, backoff_s=(0, 0))
    assert calls == [1]


# ── signature_for ────────────────────────────────────────────────────────────

def test_signature_normalizes_digits_and_paths():
    """The same failure on two runs differs only in numbers and paths. If those
    were not collapsed, every recurrence would look like a brand new problem and
    the cooldown would never fire."""
    a = self_anneal.signature_for(RuntimeError("timed out after 1800s at /tmp/ab12/run.log"))
    b = self_anneal.signature_for(RuntimeError("timed out after 1801s at /tmp/zz99/run.log"))
    assert a == b


def test_signature_separates_genuinely_different_failures():
    assert (self_anneal.signature_for(RuntimeError("disk full"))
            != self_anneal.signature_for(RuntimeError("permission denied")))
    assert (self_anneal.signature_for(ValueError("same text"))
            != self_anneal.signature_for(RuntimeError("same text")))


# ── The ledger ───────────────────────────────────────────────────────────────

def test_ledger_writes_one_well_formed_line(logs):
    self_anneal.record_failure("digest.render.week", RuntimeError("boom"),
                               attempts=2, log_tail="secret briefing text")
    rows = read_ledger(logs)
    assert len(rows) == 1
    row = rows[0]
    assert row["component"] == "digest.render.week"
    assert row["attempts"] == 2
    assert row["alerted"] is False
    assert "boom" in row["error"]
    assert row["log_tail"] == "secret briefing text"      # local only, never mailed


def test_ledger_fails_open_when_it_cannot_write(tmp_path, monkeypatch):
    """A ledger failure must never become the caller's failure."""
    monkeypatch.setattr(self_anneal.config_loader, "logs_dir",
                        lambda: tmp_path / "nested" / "unwritable")
    monkeypatch.setattr(self_anneal.Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    self_anneal.record_failure("x", RuntimeError("boom"), attempts=1)   # must not raise


# ── Alerts ───────────────────────────────────────────────────────────────────

def test_alerts_are_off_by_default(logs, monkeypatch):
    monkeypatch.setattr(self_anneal.config_loader, "alerts_enabled", lambda: False)
    assert self_anneal.maybe_alert("c", "sig", RuntimeError("x"), 1) is False


def test_cooldown_dedups_the_same_signature(logs, monkeypatch):
    sent = []
    monkeypatch.setattr(self_anneal.config_loader, "alerts_enabled", lambda: True)
    monkeypatch.setattr(self_anneal.config_loader, "alert_cooldown_days", lambda: 7)
    monkeypatch.setattr(self_anneal.config_loader, "digest_sender_account",
                        lambda: {"provider": "google", "email": "a@b.com", "label": "Gmail"})
    monkeypatch.setattr(self_anneal.config_loader, "digest_recipient_email", lambda: "a@b.com")
    monkeypatch.setattr(self_anneal.config_loader, "support_team_email", lambda: "")

    import digest_send
    monkeypatch.setattr(digest_send, "send_email",
                        lambda *a, **k: sent.append(a))

    exc = RuntimeError("same failure")
    self_anneal.record_failure("c", exc, attempts=1)
    self_anneal.record_failure("c", exc, attempts=1)

    assert len(sent) == 1, "the second identical failure alerted again"
    rows = read_ledger(logs)
    assert [r["alerted"] for r in rows] == [True, False]


def test_alert_body_never_contains_the_log_tail(logs, monkeypatch):
    """render_briefing raises with the last 2000 chars of the child's output,
    which is briefing content: names, deals, amounts. That must stay local."""
    bodies = []
    monkeypatch.setattr(self_anneal.config_loader, "alerts_enabled", lambda: True)
    monkeypatch.setattr(self_anneal.config_loader, "alert_cooldown_days", lambda: 7)
    monkeypatch.setattr(self_anneal.config_loader, "digest_sender_account",
                        lambda: {"provider": "google", "email": "a@b.com", "label": "Gmail"})
    monkeypatch.setattr(self_anneal.config_loader, "digest_recipient_email", lambda: "a@b.com")
    monkeypatch.setattr(self_anneal.config_loader, "support_team_email", lambda: "ops@b.com")

    import digest_send
    monkeypatch.setattr(digest_send, "send_email",
                        lambda account, to, subject, body: bodies.append(body))

    secret = "Manulife term sheet, 240 MW, do not disclose"
    self_anneal.record_failure("digest.render.week",
                               RuntimeError(f"exited 1: {secret}"),
                               attempts=2, log_tail=secret)

    assert bodies, "no alert was sent"
    for body in bodies:
        assert secret not in body
        assert "240 MW" not in body
    assert len(bodies) == 2, "both the user and the support address should get it"


def test_a_failing_alert_does_not_become_a_second_failure(logs, monkeypatch):
    monkeypatch.setattr(self_anneal.config_loader, "alerts_enabled", lambda: True)
    monkeypatch.setattr(self_anneal.config_loader, "alert_cooldown_days", lambda: 7)
    monkeypatch.setattr(self_anneal.config_loader, "digest_sender_account",
                        lambda: {"provider": "google", "email": "a@b.com", "label": "Gmail"})
    monkeypatch.setattr(self_anneal.config_loader, "digest_recipient_email", lambda: "a@b.com")
    monkeypatch.setattr(self_anneal.config_loader, "support_team_email", lambda: "")

    import digest_send
    monkeypatch.setattr(digest_send, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))

    self_anneal.record_failure("c", RuntimeError("boom"), attempts=1)
    rows = read_ledger(logs)
    assert len(rows) == 1, "the failed alert wrote its own ledger line (recursion)"
    assert rows[0]["alerted"] is False
    assert "smtp down" in rows[0]["alert_error"]


def test_config_accessors_default_safely():
    """These ship off. A junk cooldown must not crash a 07:00 scheduled run."""
    saved = cl._config
    try:
        cl._config = {"user": {"full_name": "T", "first_name": "T"},
                      "obsidian": {"vault_path": "/tmp/v", "hotcache_relpath": "h.md",
                                   "sources_relpath": "s", "weekly_relpath": "w",
                                   "hotcache_action_items_heading": "A"},
                      "accounts": [], "support": {"alert_cooldown_days": "seven"}}
        assert cl.alerts_enabled() is False
        assert cl.support_team_email() == ""
        assert cl.alert_cooldown_days() == 7
    finally:
        cl._config = saved
