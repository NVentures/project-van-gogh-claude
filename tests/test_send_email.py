"""Unit tests for app/send_email.py — the sanctioned ad-hoc send entry point.

Outbound mail is drafts-first; this script is the one supported way to
actually send, and only on an explicit user instruction. These tests pin the
CLI contract: account resolution from config, the success payload, and the
machine-readable "unknown_account" / "no_token" / "send_failed" error shapes.
Fully offline: the OAuth clients / send function are monkeypatched.
"""
import json

import send_email


def _run(monkeypatch, capsys, argv):
    monkeypatch.setattr("sys.argv", ["send_email.py"] + argv)
    code = send_email.main()
    return code, json.loads(capsys.readouterr().out)


def _body(tmp_path, text="Hello there.\n"):
    body_file = tmp_path / "body.txt"
    body_file.write_text(text, encoding="utf-8")
    return str(body_file)


def test_account_for_label_is_case_insensitive():
    assert send_email.account_for_label("gmail")["email"] == "primary@gmail.com"
    assert send_email.account_for_label("OUTLOOK")["provider"] == "microsoft"
    assert send_email.account_for_label("nope") is None


def test_send_success_payload(monkeypatch, capsys, tmp_path):
    sent = {}

    def fake_send(account, to, subject, body, html=None):
        sent.update(account=account, to=to, subject=subject, body=body)

    monkeypatch.setattr(send_email, "send_email", fake_send)
    code, out = _run(monkeypatch, capsys, [
        "--label", "Gmail", "--to", "Name <a@b.c>", "--subject", "Intro",
        "--body-file", _body(tmp_path)])
    assert code == 0
    assert out == {"ok": True, "from": "primary@gmail.com", "to": "a@b.c",
                   "subject": "Intro", "label": "Gmail"}
    assert sent["account"]["provider"] == "google"
    assert sent["to"] == "a@b.c"  # display name stripped by clean_address
    assert sent["body"] == "Hello there.\n"


def test_unknown_label_exits_1(monkeypatch, capsys, tmp_path):
    code, out = _run(monkeypatch, capsys, [
        "--label", "NoSuch", "--to", "a@b.c", "--subject", "x",
        "--body-file", _body(tmp_path)])
    assert code == 1
    assert out["ok"] is False
    assert out["error"] == "unknown_account"
    assert "NoSuch" in out["message"]


def test_missing_token_maps_to_no_token(monkeypatch, capsys, tmp_path):
    def raise_missing(label):
        raise RuntimeError(
            f"Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_{label.upper()} is not set.")

    monkeypatch.setattr("microsoft_client.microsoft_client", raise_missing)
    code, out = _run(monkeypatch, capsys, [
        "--label", "Outlook", "--to", "a@b.c", "--subject", "x",
        "--body-file", _body(tmp_path)])
    assert code == 1
    assert out["error"] == "no_token"
    assert "MS_GRAPH_REFRESH_TOKEN_OUTLOOK" in out["message"]


def test_send_failure_maps_to_send_failed(monkeypatch, capsys, tmp_path):
    def fake_send(account, to, subject, body, html=None):
        raise RuntimeError("Token refresh failed for Gmail: invalid_grant")

    monkeypatch.setattr(send_email, "send_email", fake_send)
    code, out = _run(monkeypatch, capsys, [
        "--label", "Gmail", "--to", "a@b.c", "--subject", "x",
        "--body-file", _body(tmp_path)])
    assert code == 1
    assert out["error"] == "send_failed"
    assert "invalid_grant" in out["message"]


def test_header_injection_recipient_is_refused(monkeypatch, capsys, tmp_path):
    called = []
    monkeypatch.setattr(send_email, "send_email",
                        lambda *a, **k: called.append(a))
    code, out = _run(monkeypatch, capsys, [
        "--label", "Gmail", "--to", "a@b.c\r\nBcc: evil@x.com",
        "--subject", "x", "--body-file", _body(tmp_path)])
    assert code == 1
    assert out["error"] == "send_failed"
    assert called == []


def test_digest_send_reuses_these_primitives():
    # digest_send must not grow a second send implementation.
    import digest_send

    assert digest_send.send_email is send_email.send_email
    assert digest_send.build_gmail_raw is send_email.build_gmail_raw
    assert digest_send.clean_address is send_email.clean_address
