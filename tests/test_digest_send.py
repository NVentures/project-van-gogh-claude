"""app/digest_send.py — the emailed-briefing sender.

CI-safe: never runs the claude CLI or an OAuth client. The send path is
exercised down to the provider-message payloads; the network call itself is
monkeypatched out.
"""
import base64
import json
from datetime import datetime
from email import message_from_bytes

import config_loader as cl
import digest_send
import self_anneal


def test_subject_format_is_briefing_name_and_mm_dd_yyyy():
    now = datetime(2026, 7, 22, 7, 0)
    assert digest_send.build_subject("morning-coffee", now) == \
        "[Project Van Gogh] Morning Coffee 07-22-2026"
    assert digest_send.build_subject("week-retro", now) == \
        "[Project Van Gogh] Week Retro 07-22-2026"


def test_briefing_md_path_mapping():
    assert digest_send.briefing_md_path("morning-coffee") == cl.morning_coffee_md_path()
    assert digest_send.briefing_md_path("afternoon-tea") == cl.afternoon_tea_md_path()
    assert digest_send.briefing_md_path("week") == cl.workspace_week_md_path()


def test_briefing_md_path_week_retro_picks_newest_dated_file(monkeypatch, tmp_path):
    (tmp_path / "retro-2026-07-10.md").write_text("old", encoding="utf-8")
    (tmp_path / "retro-2026-07-17.md").write_text("new", encoding="utf-8")
    monkeypatch.setattr(cl, "weekly_dir", lambda: tmp_path)
    assert digest_send.briefing_md_path("week-retro").name == "retro-2026-07-17.md"


def test_gmail_raw_roundtrips_headers_and_body():
    raw = digest_send.build_gmail_raw(
        "me@gmail.com", "you@gmail.com", "[Project Van Gogh] Week 07-22-2026",
        "# Week\n\n- item\n")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["From"] == "me@gmail.com"
    assert msg["To"] == "you@gmail.com"
    assert msg["Subject"] == "[Project Van Gogh] Week 07-22-2026"
    assert msg.get_payload(decode=True).decode("utf-8") == "# Week\n\n- item\n"


def test_gmail_raw_with_html_is_multipart_alternative():
    raw = digest_send.build_gmail_raw(
        "me@gmail.com", "you@gmail.com", "subj", "# Week\n",
        html="<div><h1>Week</h1></div>")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["From"] == "me@gmail.com"
    assert msg["Subject"] == "subj"
    assert msg.is_multipart()
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    # Plain first, HTML last — clients prefer the last alternative.
    assert parts[0].get_content_type() == "text/plain"
    assert parts[0].get_payload(decode=True).decode("utf-8") == "# Week\n"
    assert parts[1].get_content_type() == "text/html"
    assert "<h1>Week</h1>" in parts[1].get_payload(decode=True).decode("utf-8")


def test_graph_message_shape():
    m = digest_send.build_graph_message("you@x.com", "subj", "body")
    assert m == {
        "subject": "subj",
        "body": {"contentType": "Text", "content": "body"},
        "toRecipients": [{"emailAddress": {"address": "you@x.com"}}],
    }


def test_graph_message_html():
    m = digest_send.build_graph_message("you@x.com", "subj", "body",
                                        html="<div>hi</div>")
    assert m["body"] == {"contentType": "HTML", "content": "<div>hi</div>"}


def _enable_digest(overrides=None):
    """A fixture-config copy with the digest opted in."""
    digest = {"enabled": True, "sender_label": "", "recipient_email": "",
              "briefings": {}}
    digest.update(overrides or {})
    return {**cl._config, "digest": digest}


def test_main_skips_quietly_when_digest_disabled(capsys):
    # Fixture default: digest.enabled is False.
    assert digest_send.main(["week"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "skipped"


def test_main_skips_quietly_when_briefing_disabled(capsys):
    saved = cl._config
    try:
        cl._config = _enable_digest({"briefings": {"week": {"enabled": False}}})
        assert digest_send.main(["week"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "skipped"
    finally:
        cl._config = saved


def test_main_no_render_sends_existing_file(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("# Week briefing\n", encoding="utf-8")
    sent = {}

    def fake_send(account, to, subject, body, html=None):
        sent.update(account=account, to=to, subject=subject, body=body, html=html)

    saved = cl._config
    try:
        cl._config = _enable_digest({"recipient_email": "target@x.com"})
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send, "send_email", fake_send)
        assert digest_send.main(["week", "--no-render"]) == 0
    finally:
        cl._config = saved

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "sent"
    assert out["to"] == "target@x.com"
    assert sent["account"]["email"] == "primary@gmail.com"  # primary = sender fallback
    assert sent["body"] == "# Week briefing\n"
    assert "<h1" in sent["html"] and "# Week briefing" not in sent["html"]
    assert sent["subject"].startswith("[Project Van Gogh] Week ")


def test_main_html_render_failure_still_sends_plain(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("# Week briefing\n", encoding="utf-8")
    sent = {}

    def boom(text):
        raise RuntimeError("renderer bug")

    saved = cl._config
    try:
        cl._config = _enable_digest()
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send.digest_html, "md_to_email_html", boom)
        monkeypatch.setattr(
            digest_send, "send_email",
            lambda account, to, subject, body, html=None: sent.update(
                body=body, html=html))
        assert digest_send.main(["week", "--no-render"]) == 0
    finally:
        cl._config = saved
    assert sent["html"] is None and sent["body"] == "# Week briefing\n"
    assert "HTML render failed" in capsys.readouterr().err


def test_main_oversized_html_falls_back_to_plain(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("# Week briefing\n", encoding="utf-8")
    sent = {}

    saved = cl._config
    try:
        cl._config = _enable_digest()
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send.digest_html, "md_to_email_html",
                            lambda text: "x" * (digest_send.HTML_MAX_BYTES + 1))
        monkeypatch.setattr(
            digest_send, "send_email",
            lambda account, to, subject, body, html=None: sent.update(html=html))
        assert digest_send.main(["week", "--no-render"]) == 0
    finally:
        cl._config = saved
    assert sent["html"] is None
    assert "Gmail clips" in capsys.readouterr().err


def test_main_errors_when_render_leaves_stale_file(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("stale", encoding="utf-8")
    import os
    os.utime(md, (1000000, 1000000))  # far in the past

    saved = cl._config
    try:
        cl._config = _enable_with_today()
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send, "render_briefing",
                            lambda briefing, claude=None: None)
        assert digest_send.main(["week"]) == 1
    finally:
        cl._config = saved
    assert "not refreshed" in capsys.readouterr().err


def test_clean_address_rejects_header_injection_and_junk():
    import pytest
    assert digest_send.clean_address("you@x.com", "recipient") == "you@x.com"
    assert digest_send.clean_address("Name <you@x.com>", "recipient") == "you@x.com"
    for bad in ("me@x.com\nBcc: evil@e.com", "me@x.com\r\nX: y", "not-an-address", ""):
        with pytest.raises(RuntimeError):
            digest_send.clean_address(bad, "recipient")


def test_send_email_dispatches_by_provider(monkeypatch):
    import sys as _sys
    import types

    captured = {}

    class FakeSendReq:
        def __init__(self, body):
            captured["gmail_body"] = body

        def execute(self):
            captured["executed"] = True

    fake_gmail = types.SimpleNamespace(users=lambda: types.SimpleNamespace(
        messages=lambda: types.SimpleNamespace(
            send=lambda userId, body: FakeSendReq(body))))
    fake_google_mod = types.SimpleNamespace(
        google_client=lambda label: types.SimpleNamespace(gmail=fake_gmail))
    fake_ms_mod = types.SimpleNamespace(
        microsoft_client=lambda label: types.SimpleNamespace(
            send_mail=lambda message: captured.update(graph_message=message)))
    monkeypatch.setitem(_sys.modules, "google_client", fake_google_mod)
    monkeypatch.setitem(_sys.modules, "microsoft_client", fake_ms_mod)

    digest_send.send_email(
        {"provider": "google", "label": "Gmail", "email": "me@g.com"},
        "you@x.com", "subj", "body")
    assert captured["executed"] and "raw" in captured["gmail_body"]

    digest_send.send_email(
        {"provider": "microsoft", "label": "Outlook", "email": "me@o.com"},
        "you@x.com", "subj", "body")
    assert captured["graph_message"]["subject"] == "subj"

    import pytest
    with pytest.raises(RuntimeError, match="unknown sender provider"):
        digest_send.send_email(
            {"provider": "yahoo", "label": "Y", "email": "m@y.com"}, "t@x.com", "s", "b")


def _enable_with_today(overrides=None):
    """Enabled digest whose 'week' cadence includes today (so the day guard
    passes in render-path tests regardless of which weekday CI runs on)."""
    today = datetime.now().strftime("%A").lower()
    cfg = _enable_digest(overrides)
    cfg["digest"]["briefings"] = {"week": {"enabled": True, "days": [today],
                                           "time": "07:00"}}
    return cfg


def test_main_skips_on_wrong_day_for_scheduled_runs(capsys):
    saved = cl._config
    try:
        cfg = _enable_digest()
        # a cadence whose only day is NOT today
        today = datetime.now().strftime("%A").lower()
        not_today = "monday" if today != "monday" else "tuesday"
        cfg["digest"]["briefings"] = {"week": {"enabled": True, "days": [not_today],
                                               "time": "07:00"}}
        cl._config = cfg
        assert digest_send.main(["week"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "skipped" and "not in this digest's days" in out["reason"]
    finally:
        cl._config = saved


def test_main_returns_1_when_render_fails(monkeypatch, capsys, tmp_path):
    saved = cl._config
    calls = []
    try:
        cl._config = _enable_with_today()

        def boom(briefing, claude=None):
            calls.append(briefing)
            raise RuntimeError("claude -p /van-gogh:week exited 1: denied")

        # The render is retried now, so the real backoff would make this test
        # sit for two minutes. Stub the sleep, not the retry: the point is that
        # main() still returns 1 once the retries are exhausted.
        monkeypatch.setattr(self_anneal.time, "sleep", lambda _s: None)
        monkeypatch.setattr(self_anneal, "record_failure", lambda *a, **k: None)
        monkeypatch.setattr(digest_send, "render_briefing", boom)
        assert digest_send.main(["week"]) == 1
    finally:
        cl._config = saved
    assert calls == ["week"] * digest_send.RENDER_ATTEMPTS
    assert "exited 1" in capsys.readouterr().err


def test_lock_timeout_covers_retry_budget():
    """digest_lock breaks a lock it judges stale. If the timeout were shorter
    than the retry budget, a second scheduled run would start behind a first one
    that is merely retrying, and the user would get the briefing twice."""
    assert digest_send.LOCK_TIMEOUT_S >= (
        digest_send.RENDER_TIMEOUT_S * digest_send.RENDER_ATTEMPTS
        + sum(digest_send.RENDER_BACKOFF_S)
    )
    assert digest_send.LOCK_TIMEOUT_S == 4050


def test_main_full_render_path_sends_fresh_file(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    sent = {}
    saved = cl._config
    try:
        cl._config = _enable_with_today()
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        monkeypatch.setattr(digest_send, "render_briefing",
                            lambda briefing, claude=None: md.write_text("# fresh\n",
                                                                        encoding="utf-8"))
        monkeypatch.setattr(
            digest_send, "send_email",
            lambda account, to, subject, body, html=None: sent.update(
                body=body, html=html))
        assert digest_send.main(["week"]) == 0
    finally:
        cl._config = saved
    assert sent["body"] == "# fresh\n"
    assert "<h1" in sent["html"]
    assert json.loads(capsys.readouterr().out)["status"] == "sent"


def test_main_errors_when_no_accounts(capsys):
    saved = cl._config
    try:
        cl._config = {**_enable_digest(), "accounts": []}
        assert digest_send.main(["week", "--no-render"]) == 1
    finally:
        cl._config = saved
    assert "sender account" in capsys.readouterr().err


def test_main_errors_when_file_missing(monkeypatch, capsys, tmp_path):
    saved = cl._config
    try:
        cl._config = _enable_digest()
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: tmp_path / "absent.md")
        assert digest_send.main(["week", "--no-render"]) == 1
    finally:
        cl._config = saved
    assert "did not produce" in capsys.readouterr().err


def test_main_rejects_injected_recipient(monkeypatch, capsys, tmp_path):
    md = tmp_path / "week.md"
    md.write_text("x", encoding="utf-8")
    saved = cl._config
    try:
        cl._config = _enable_digest({"recipient_email": "me@x.com\nBcc: evil@e.com"})
        monkeypatch.setattr(cl, "workspace_week_md_path", lambda: md)
        assert digest_send.main(["week", "--no-render"]) == 1
    finally:
        cl._config = saved
    assert "invalid recipient address" in capsys.readouterr().err


def test_digest_lock_serializes_and_cleans_up():
    import user_state
    lock = user_state.state_dir() / "digest.lock"
    with digest_send.digest_lock():
        assert lock.exists()
    assert not lock.exists()


def test_digest_lock_breaks_stale_lock():
    import os as _os
    import user_state
    lock = user_state.state_dir() / "digest.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999", encoding="utf-8")
    stale = digest_send.LOCK_TIMEOUT_S + 60
    _os.utime(lock, (digest_send.time.time() - stale, digest_send.time.time() - stale))
    with digest_send.digest_lock():  # must break the stale lock, not wait
        assert lock.exists()
    assert not lock.exists()
