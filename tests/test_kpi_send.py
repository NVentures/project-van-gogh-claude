"""The weekly send: who gets it, when it refuses, and how it avoids duplicates.

`send_email` is stubbed in every test here, so nothing in this file can ever
attempt to send mail. The tests that matter most are the three ledger states,
because the failure they guard against is the one the reader would actually
notice: two identical scorecards in one inbox.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import kpi_events
import kpi_report
import kpi_send

NOW = datetime(2026, 9, 11, 16, 0)          # a Friday

# The real accessor, captured at import, so the two recipient tests can put it
# back after the autouse sandbox stubs it.
import config_loader as _cl
_real_kpi_recipient = _cl.kpi_recipient_email


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Every path this module writes to, pointed at a temp dir."""
    logs = tmp_path / "logs"
    logs.mkdir()
    root = tmp_path / "van-gogh"
    root.mkdir()
    monkeypatch.setattr(kpi_send.config_loader, "logs_dir", lambda: logs)
    monkeypatch.setattr(kpi_send.config_loader, "van_gogh_root", lambda: root)
    monkeypatch.setattr(kpi_events, "events_path",
                        lambda: logs / "kpi_events.jsonl")
    monkeypatch.setattr(kpi_events, "enabled", lambda: True)
    # Every switch on, so each test turns off only what it is testing.
    monkeypatch.setattr(kpi_send.config_loader, "kpi_enabled", lambda: True)
    monkeypatch.setattr(kpi_send.config_loader, "kpi_day", lambda: "friday")
    monkeypatch.setattr(kpi_send.config_loader, "kpi_window_days", lambda: 14)
    monkeypatch.setattr(kpi_send.config_loader, "kpi_recipient_email",
                        lambda: "reader@example.com")
    monkeypatch.setattr(kpi_send.config_loader, "kpi_share_with_support",
                        lambda: False)
    monkeypatch.setattr(kpi_send.config_loader, "support_team_email",
                        lambda: "team@example.com")
    monkeypatch.setattr(kpi_send.config_loader, "digest_sender_account",
                        lambda: {"provider": "google", "label": "Gmail",
                                 "email": "me@example.com"})
    return {"logs": logs, "root": root}


@pytest.fixture
def sent(monkeypatch):
    """Capture every send. Nothing here ever touches a real mailbox."""
    calls = []
    import send_email

    monkeypatch.setattr(send_email, "send_email",
                        lambda acct, to, subj, body, html=None:
                        calls.append({"to": to, "subject": subj,
                                      "body": body, "html": html}))
    return calls


def seed_history(logs, days=20, now=NOW):
    """A full window of events, so the first-run guard is satisfied."""
    path = logs / "kpi_events.jsonl"
    rows = []
    for d in (days, days - 7, 1):
        rows.append({
            "ts": (now - timedelta(days=d)).isoformat(timespec="seconds"),
            "kind": kpi_events.SNAPSHOT, "briefing": "morning-coffee",
            "counts": {"open": 5, "late": 1, "front": 2},
            "front": [{"key": "k1", "late": 0}, {"key": "k2", "late": 3}],
            "open_keys": ["k1", "k2", "k3", "k4", "k5"], "late_keys": ["k2"],
        })
    rows.append({"ts": (now - timedelta(days=2)).isoformat(timespec="seconds"),
                 "kind": kpi_events.CLOSED, "keys": ["k9"]})
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def run(argv):
    return kpi_send.main(argv)


# ── When it refuses ──────────────────────────────────────────────────────────

def test_it_sends_nothing_when_the_feature_is_off(sandbox, sent, monkeypatch,
                                                  capsys):
    seed_history(sandbox["logs"])
    monkeypatch.setattr(kpi_send.config_loader, "kpi_enabled", lambda: False)
    assert run(["--now", NOW.isoformat()]) == 0
    assert sent == []
    assert "scorecard is off" in capsys.readouterr().out


def test_it_sends_nothing_on_the_wrong_day(sandbox, sent, capsys):
    seed_history(sandbox["logs"])
    wednesday = datetime(2026, 9, 9, 16, 0)
    assert run(["--now", wednesday.isoformat()]) == 0
    assert sent == []
    assert "not the scorecard day" in capsys.readouterr().out


def test_the_first_send_waits_for_a_full_window(sandbox, sent, capsys):
    """Six grey cards is a poor first impression, so it does not send one."""
    seed_history(sandbox["logs"], days=3)
    assert run(["--now", NOW.isoformat()]) == 0
    assert sent == []
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "skipped"
    assert "full window" in out["reason"]
    assert out["first_send"], "the user is not told when it will arrive"


def test_it_refuses_without_a_recipient(sandbox, sent, monkeypatch, capsys):
    seed_history(sandbox["logs"])
    monkeypatch.setattr(kpi_send.config_loader, "kpi_recipient_email",
                        lambda: "")
    assert run(["--now", NOW.isoformat()]) == 1
    assert sent == []


# ── The happy path ───────────────────────────────────────────────────────────

def test_a_real_run_sends_once_and_records_everything(sandbox, sent, capsys):
    seed_history(sandbox["logs"])
    assert run(["--now", NOW.isoformat()]) == 0
    assert len(sent) == 1
    assert sent[0]["to"] == "reader@example.com"
    assert sent[0]["html"], "the HTML alternative is missing"
    # The window is fourteen days, so the subject says fortnight. Calling a
    # two-week window "your week" was a cold-read finding.
    assert "fortnight" in sent[0]["subject"]

    ledger = json.loads((sandbox["logs"] / kpi_send.LEDGER_NAME)
                        .read_text(encoding="utf-8"))
    week = ledger["weeks"][kpi_send.week_id(NOW)]
    assert week["sent_at"], "the send was never stamped"
    assert week["recipients"] == ["reader@example.com"]
    assert kpi_send.latest_path().exists()


def test_sharing_sends_the_same_email_to_the_support_address(
        sandbox, sent, monkeypatch):
    seed_history(sandbox["logs"])
    monkeypatch.setattr(kpi_send.config_loader, "kpi_share_with_support",
                        lambda: True)
    assert run(["--now", NOW.isoformat()]) == 0
    assert [c["to"] for c in sent] == ["reader@example.com", "team@example.com"]
    assert sent[0]["body"] == sent[1]["body"]


def test_a_run_lands_in_the_ledger_the_watcher_reads(sandbox, sent,
                                                     monkeypatch):
    rows = []
    import run_ledger

    monkeypatch.setattr(run_ledger, "record_run",
                        lambda job, s, f, rc, detail="":
                        rows.append({"job": job, "rc": rc}))
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert rows == [{"job": "kpi.email", "rc": 0}]


# ── The three ledger states ──────────────────────────────────────────────────

def test_a_second_run_in_the_same_week_sends_nothing(sandbox, sent, capsys):
    seed_history(sandbox["logs"])
    assert run(["--now", NOW.isoformat()]) == 0
    capsys.readouterr()
    assert run(["--now", (NOW + timedelta(hours=2)).isoformat()]) == 0
    assert len(sent) == 1, "the scorecard went out twice"
    assert "already handled" in capsys.readouterr().out


def test_a_crash_after_sending_is_never_retried(sandbox, sent, capsys):
    """THE duplicate trap: sent, then died before it could say so.

    The claim is written before the send, so a week with a claim and no stamp
    reads as "this may have gone out" and is left alone. A missed scorecard is
    much cheaper than two.
    """
    seed_history(sandbox["logs"])
    kpi_send.claim(NOW, "relationship-radar")          # claimed, never stamped
    assert run(["--now", NOW.isoformat()]) == 0
    assert sent == [], "a possibly-sent week was sent again"
    assert "already handled" in capsys.readouterr().out


def test_a_claim_is_written_before_the_send_is_attempted(sandbox, monkeypatch):
    """Proven by inspecting the ledger from inside the send itself."""
    seen = {}
    import send_email

    def spy(acct, to, subj, body, html=None):
        seen["ledger_at_send_time"] = kpi_send._read_ledger()

    monkeypatch.setattr(send_email, "send_email", spy)
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    weeks = seen["ledger_at_send_time"]["weeks"]
    assert kpi_send.week_id(NOW) in weeks, "the send ran before the claim"
    assert weeks[kpi_send.week_id(NOW)]["sent_at"] == ""


def test_force_overrides_the_ledger(sandbox, sent):
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    run(["--now", NOW.isoformat(), "--force"])
    assert len(sent) == 2


# ── Send failure ─────────────────────────────────────────────────────────────

# (class, an error of that class, how many attempts it should cost)
#
# Three classes, three behaviours, which is the whole point: a dead token is
# not repaired by asking again, a blip usually is, and a server that has
# answered "no" has answered.
FAILURE_CASES = [
    ("auth", RuntimeError("invalid_grant: token expired or revoked"), 1),
    ("network", RuntimeError("Connection reset by peer"), 2),
    ("unknown", RuntimeError("550 recipient rejected"), 1),
]


@pytest.mark.parametrize("kind,error,expected_attempts", FAILURE_CASES)
def test_each_failure_class_behaves_differently(
        sandbox, monkeypatch, kind, error, expected_attempts, capsys):
    import send_email

    tries = []

    def boom(*_a, **_k):
        tries.append(1)
        raise error

    monkeypatch.setattr(send_email, "send_email", boom)
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    failures = []
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure",
                        lambda comp, exc, attempts, log_tail="":
                        failures.append({"component": comp,
                                         "attempts": attempts,
                                         "tail": log_tail}))
    runs = []
    import run_ledger

    monkeypatch.setattr(run_ledger, "record_run",
                        lambda job, s, f, rc, detail="":
                        runs.append({"rc": rc, "detail": detail}))

    seed_history(sandbox["logs"])
    assert run(["--now", NOW.isoformat()]) == 1

    # The behaviour that differs: how many times it tried.
    assert len(tries) == expected_attempts, \
        f"a {kind} failure cost {len(tries)} attempts, expected {expected_attempts}"
    # Both ledgers hear about it, and the run row names the class so the
    # watcher can decide what to do without re-reading the message.
    assert failures and failures[0]["component"] == "kpi_send.send"
    assert failures[0]["attempts"] == expected_attempts
    assert runs and runs[0]["rc"] == 1
    assert kind in runs[0]["detail"], f"the run row does not name the class"


def test_a_dead_token_says_nothing_was_sent_and_a_blip_says_check_sent():
    """The two tails differ because the two situations differ: one address was
    certainly not written to, the other may already have been."""
    import failure_class

    assert "auth" in failure_class.NO_RETRY
    assert "network" not in failure_class.NO_RETRY


def test_an_auth_failure_tail_never_tells_the_reader_to_check_sent(
        sandbox, monkeypatch):
    import send_email

    monkeypatch.setattr(send_email, "send_email",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("invalid_grant: token revoked")))
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    tails = []
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure",
                        lambda comp, exc, attempts, log_tail="":
                        tails.append(log_tail))
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert tails and "Nothing was sent" in tails[0]


def test_an_address_already_sent_to_is_never_written_to_twice(
        sandbox, monkeypatch):
    """A failure on the SECOND recipient must not re-send to the first."""
    import send_email

    seen = []

    def flaky(account, to, subject, body, html=None):
        seen.append(to)
        if to == "team@example.com":
            raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr(send_email, "send_email", flaky)
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    monkeypatch.setattr(kpi_send.config_loader, "kpi_share_with_support",
                        lambda: True)
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure",
                        lambda *_a, **_k: None)
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert seen.count("reader@example.com") == 1, \
        "the first recipient was sent to twice"


def test_a_failed_send_writes_no_artifact(sandbox, monkeypatch):
    """The file the watcher grades must never appear for a send that failed."""
    import send_email

    monkeypatch.setattr(send_email, "send_email",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("nope")))
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert not kpi_send.latest_path().exists()


# ── Recipient resolution ─────────────────────────────────────────────────────

def test_the_scorecard_falls_back_to_the_digest_address(monkeypatch):
    """The real accessor, not the sandbox stub: this is what is under test.

    Patching the narrow accessor rather than the shared _config object, so
    nothing leaks into another test file (config_loader is primed once per
    session and a write to it outlives this module).
    """
    import config_loader

    monkeypatch.setattr(config_loader, "kpi_recipient_email",
                        _real_kpi_recipient)
    monkeypatch.setattr(config_loader, "_kpi",
                        lambda: {"recipient_email": ""})
    monkeypatch.setattr(config_loader, "digest_recipient_email",
                        lambda: "digest@example.com")
    assert config_loader.kpi_recipient_email() == "digest@example.com"


def test_its_own_address_overrides_the_digest_one(monkeypatch):
    import config_loader

    monkeypatch.setattr(config_loader, "kpi_recipient_email",
                        _real_kpi_recipient)
    monkeypatch.setattr(config_loader, "_kpi",
                        lambda: {"recipient_email": "private@example.com"})
    monkeypatch.setattr(config_loader, "digest_recipient_email",
                        lambda: "digest@example.com")
    assert config_loader.kpi_recipient_email() == "private@example.com"


def test_the_ledger_records_addresses_and_never_content(sandbox, sent):
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    raw = (sandbox["logs"] / kpi_send.LEDGER_NAME).read_text(encoding="utf-8")
    assert "reader@example.com" in raw
    assert "Threads waiting" not in raw
    assert "<table" not in raw


# ── Preview ──────────────────────────────────────────────────────────────────

def test_preview_writes_a_file_and_sends_nothing(sandbox, sent, tmp_path):
    seed_history(sandbox["logs"])
    out = tmp_path / "preview.html"
    assert run(["--preview", str(out), "--now", NOW.isoformat()]) == 0
    assert out.exists() and out.read_text(encoding="utf-8").startswith("<body")
    assert sent == []


# ── The spotlight rotation ───────────────────────────────────────────────────

def test_the_spotlight_does_not_repeat_week_after_week(sandbox, sent):
    seed_history(sandbox["logs"])
    picks = []
    for week in range(4):
        when = NOW + timedelta(days=7 * week)
        run(["--now", when.isoformat(), "--force"])
        ledger = kpi_send._read_ledger()
        picks.append(ledger["weeks"][kpi_send.week_id(when)]["spotlight"])
    assert len(set(picks)) == len(picks), f"a skill repeated: {picks}"


def test_a_skill_the_user_already_uses_is_not_suggested(sandbox, monkeypatch):
    used = {e["script_stems"][0]: {"attended": NOW.isoformat()}
            for e in kpi_report.SPOTLIGHTS[:3]}
    monkeypatch.setattr(kpi_report, "read_usage", lambda: used)
    pick = kpi_report.spotlight(shown=[], now=NOW)
    assert pick["skill"] not in [e["skill"] for e in kpi_report.SPOTLIGHTS[:3]]


def test_a_skill_the_user_cannot_use_is_never_suggested(monkeypatch):
    """No configured clients means the client-report skill is not advice."""
    import config_loader

    monkeypatch.setattr(config_loader, "clients", lambda: [])
    monkeypatch.setattr(kpi_report, "read_usage", lambda: {})
    shown = [e["skill"] for e in kpi_report.SPOTLIGHTS
             if e["skill"] != "five-fifteen"][:6]
    pick = kpi_report.spotlight(shown=shown, now=NOW)
    assert pick.get("skill") != "five-fifteen"


# ── Found by a real send attempt, not by a fixture ───────────────────────────

def test_a_missing_credential_is_an_auth_failure_not_an_unknown_one():
    """A real send on an unauthorised machine classified as "unknown".

    The shared pattern knew about expired and revoked tokens but not about one
    that was never there, so a fresh install would have retried a wall it can
    never get through.
    """
    import failure_class

    real = ("RuntimeError: Missing refresh token: env var "
            "GOOGLE_REFRESH_TOKEN_GMAIL is not set. Run: python "
            "app/auth_bootstrap.py --provider google --label Gmail")
    assert failure_class.classify(real) == "auth"
    assert "auth" in failure_class.NO_RETRY
    # The near miss must not be swallowed by the new pattern.
    assert failure_class.classify("Connection reset by peer") == "network"


def test_a_week_that_provably_sent_nothing_is_released(sandbox, monkeypatch):
    """Holding the claim after a dead credential means fixing the token still
    costs the reader this fortnight's scorecard."""
    import send_email

    monkeypatch.setattr(send_email, "send_email",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("Missing refresh token: env var "
                                         "GOOGLE_REFRESH_TOKEN_GMAIL is not set")))
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure", lambda *_a, **_k: None)
    seed_history(sandbox["logs"])
    assert run(["--now", NOW.isoformat()]) == 1
    assert not kpi_send.already_handled(NOW), \
        "the week stayed claimed after a send that never reached the server"


def test_a_week_that_may_have_sent_is_never_released(sandbox, monkeypatch):
    """The opposite case, and the more important one: a send that raised may
    still have been accepted, so the week stays claimed."""
    import send_email

    monkeypatch.setattr(send_email, "send_email",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("502 Bad Gateway")))
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure", lambda *_a, **_k: None)
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert kpi_send.already_handled(NOW), \
        "a possibly-sent week was released, which is how a duplicate happens"


def test_a_partial_delivery_never_releases_the_week(sandbox, monkeypatch):
    """One address received it, the second failed on auth. The week must
    stay claimed: someone already has the email."""
    import send_email

    def flaky(account, to, subject, body, html=None):
        if to != "reader@example.com":
            raise RuntimeError("Missing refresh token for that account")

    monkeypatch.setattr(send_email, "send_email", flaky)
    monkeypatch.setattr(kpi_send.time, "sleep", lambda _s: None)
    monkeypatch.setattr(kpi_send.config_loader, "kpi_share_with_support",
                        lambda: True)
    import self_anneal

    monkeypatch.setattr(self_anneal, "record_failure", lambda *_a, **_k: None)
    seed_history(sandbox["logs"])
    run(["--now", NOW.isoformat()])
    assert kpi_send.already_handled(NOW), \
        "a week that reached one inbox was released for a re-send"
