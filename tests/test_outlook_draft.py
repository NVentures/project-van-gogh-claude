"""Unit tests for app/outlook_draft.py — the Outlook draft script skills run.

The script is the Tier 1 probe: skills always attempt it and key their paste
fallback off the machine-readable JSON it prints. These tests pin that
contract — the Graph message shape, the vault-markdown body transform, the
success payload, and the exit-1 "no_token" / "draft_failed" error shapes.
Fully offline: microsoft_client is monkeypatched, no auth.
"""
import json

import microsoft_client as mc
import outlook_draft


class _FakeClient:
    def __init__(self, result=None, exc=None):
        self.result = result or {}
        self.exc = exc
        self.messages = []

    def create_draft(self, message):
        self.messages.append(message)
        if self.exc:
            raise self.exc
        return self.result


def _run(monkeypatch, capsys, argv, client):
    monkeypatch.setattr("microsoft_client.microsoft_client", lambda label: client)
    monkeypatch.setattr("sys.argv", ["outlook_draft.py"] + argv)
    code = outlook_draft.main()
    return code, json.loads(capsys.readouterr().out)


def test_create_draft_posts_to_me_messages(monkeypatch):
    seen = {}
    client = object.__new__(mc.MicrosoftClient)
    monkeypatch.setattr(
        type(client), "post", lambda self, path, body=None: seen.update(path=path, body=body) or {}, raising=False
    )
    client.create_draft({"subject": "s"})
    assert seen["path"] == "/me/messages"
    assert seen["body"] == {"subject": "s"}


def test_draft_success_message_shape_and_output(monkeypatch, capsys, tmp_path):
    body_file = tmp_path / "body.txt"
    body_file.write_text("Hello there.\n", encoding="utf-8")
    client = _FakeClient(result={"webLink": "https://outlook.example/draft/1"})
    code, out = _run(
        monkeypatch,
        capsys,
        ["--label", "Outlook", "--to", "a@b.c", "--subject", "Weekly 5:15", "--body-file", str(body_file)],
        client,
    )
    assert code == 0
    assert out == {
        "ok": True,
        "web_link": "https://outlook.example/draft/1",
        "label": "Outlook",
        "to": "a@b.c",
        "subject": "Weekly 5:15",
    }
    (msg,) = client.messages
    assert msg["subject"] == "Weekly 5:15"
    assert msg["toRecipients"] == [{"emailAddress": {"address": "a@b.c"}}]
    assert msg["body"] == {"contentType": "Text", "content": "Hello there.\n"}


def test_strip_frontmatter_removes_yaml_and_headings(tmp_path):
    md = tmp_path / "report.md"
    md.write_text(
        "---\nclient: Acme\nweek_ending: 2026-08-14\n---\n"
        "# 5:15 Report\n\n## Look back\n\nShipped the widget.\n",
        encoding="utf-8",
    )
    body = outlook_draft.load_body(md, strip_frontmatter=True)
    assert "client: Acme" not in body
    assert "#" not in body
    assert "5:15 Report" in body
    assert "Shipped the widget." in body
    assert body.endswith("\n")


def test_plain_body_is_untouched(tmp_path):
    txt = tmp_path / "body.txt"
    txt.write_text("Line one --- with dashes\n# not a heading strip\n", encoding="utf-8")
    body = outlook_draft.load_body(txt, strip_frontmatter=False)
    assert body == "Line one --- with dashes\n# not a heading strip\n"


def test_missing_token_exits_1_with_no_token_error(monkeypatch, capsys, tmp_path):
    body_file = tmp_path / "body.txt"
    body_file.write_text("hi\n", encoding="utf-8")

    def raise_missing(label):
        raise RuntimeError(f"Missing refresh token: env var MS_GRAPH_REFRESH_TOKEN_{label.upper()} is not set.")

    monkeypatch.setattr("microsoft_client.microsoft_client", raise_missing)
    monkeypatch.setattr(
        "sys.argv",
        ["outlook_draft.py", "--label", "Pemetic", "--to", "a@b.c", "--subject", "x", "--body-file", str(body_file)],
    )
    code = outlook_draft.main()
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["ok"] is False
    assert out["error"] == "no_token"
    assert "MS_GRAPH_REFRESH_TOKEN_PEMETIC" in out["message"]


def test_graph_failure_exits_1_with_draft_failed(monkeypatch, capsys, tmp_path):
    body_file = tmp_path / "body.txt"
    body_file.write_text("hi\n", encoding="utf-8")
    client = _FakeClient(exc=RuntimeError("Token refresh failed for Outlook: invalid_grant"))
    code, out = _run(
        monkeypatch,
        capsys,
        ["--label", "Outlook", "--to", "a@b.c", "--subject", "x", "--body-file", str(body_file)],
        client,
    )
    assert code == 1
    assert out["ok"] is False
    assert out["error"] == "draft_failed"
    assert "invalid_grant" in out["message"]
