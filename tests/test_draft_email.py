"""P17, P18 and P19: the draft path, the ledger, and the absence of a send."""
import ast
import json
from pathlib import Path

import pytest

import config_loader as cl
import draft_email as de

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "_ledger_path", lambda: tmp_path / "draft_ledger.json")
    return tmp_path


# ── P19 ──────────────────────────────────────────────────────────────────────

FORBIDDEN = {"send", "sendMail", "send_mail", "send_email", "sendmail"}


def test_no_send_path_in_the_ast():
    tree = ast.parse((APP / "draft_email.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            raise AssertionError(f"draft_email.py reaches {node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            raise AssertionError(f"draft_email.py names {node.id}")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in FORBIDDEN, alias.name


def _code_only(src: str) -> str:
    """Source with docstrings and comments removed.

    The module explains, in prose, that it deliberately has no send path and
    names the one function in the repo that does. A scan over raw text reads
    that explanation as the violation, so the scan has to see code only.
    """
    import io
    import re
    import tokenize
    out = []
    prev_type = tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                tokenize.NL, tokenize.ENCODING):
            continue                                    # a docstring
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_type = tok.type
        else:
            prev_type = tok.type
    return " ".join(out)


def test_no_send_call_in_the_source_text():
    """The text scan, proven by planting one first."""
    import re
    pattern = re.compile(r"\b(send_email|send_mail|sendMail)\b|\.\s*send\s*\(")
    assert pattern.search(_code_only("def f():\n    client.sendMail(body)\n")), \
        "the scanner cannot find a known send call"
    src = (APP / "draft_email.py").read_text(encoding="utf-8")
    assert not pattern.search(_code_only(src))


# ── P17 ──────────────────────────────────────────────────────────────────────

def test_no_token_state(ledger_dir, monkeypatch):
    def boom(*a, **k):
        raise de.DraftUnavailable("no credential for Outlook on this machine")
    monkeypatch.setattr(de, "draft", boom)
    monkeypatch.setattr(de, "counterparty_is_terminal", lambda who: False)
    result = de.draft_once("microsoft", "Outlook", "a@b.com", "Hi", "body")
    assert result["no_token"] is True
    assert "credential" in result["reason"]
    assert not (ledger_dir / "draft_ledger.json").exists()


def test_existing_ledger_entry_makes_zero_api_calls(ledger_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(de, "counterparty_is_terminal", lambda who: False)
    monkeypatch.setattr(de, "draft", lambda *a, **k: (
        calls.append(a) or {"ok": True, "web_link": "L", "id": "1"}))
    first = de.draft_once("google", "Gmail", "a@b.com", "Kestrel term sheet",
                          "body", counterparty="Ana")
    assert first["ok"] and not first.get("existing")
    assert len(calls) == 1
    again = de.draft_once("google", "Gmail", "a@b.com",
                          "Re: Kestrel term sheet", "body", counterparty="Ana")
    assert again["existing"] is True
    assert again["web_link"] == "L"
    assert len(calls) == 1, "a second draft was created for the same thread"


def test_cli_prints_json(ledger_dir, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(de, "counterparty_is_terminal", lambda who: False)
    monkeypatch.setattr(de, "draft", lambda *a, **k: {
        "ok": True, "web_link": "L", "id": "1"})
    body = tmp_path / "b.txt"
    body.write_text("hello\n", encoding="utf-8")
    rc = de.main(["--provider", "google", "--label", "Gmail", "--to", "a@b.com",
                  "--subject", "Hi", "--body-file", str(body)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["web_link"] == "L"


# ── P18 ──────────────────────────────────────────────────────────────────────

def test_no_draft_for_dead_deal(ledger_dir, monkeypatch, tmp_path):
    hotcache = tmp_path / "hotcache.md"
    hotcache.write_text(
        "## Active Threads\n\n"
        "### Kestrel Data Center\n"
        "<!-- deal: stage=dead last_contact=2026-07-01 -->\n\n"
        "### Meridian Solar\n"
        "<!-- deal: stage=closing last_contact=2026-09-01 -->\n",
        encoding="utf-8")
    monkeypatch.setattr(cl, "hotcache_path", lambda: hotcache)
    calls = []
    monkeypatch.setattr(de, "draft", lambda *a, **k: (
        calls.append(a) or {"ok": True, "web_link": "L", "id": "1"}))

    dead = de.draft_once("google", "Gmail", "a@b.com", "Next steps", "body",
                         counterparty="Kestrel Data Center")
    assert dead == {"skipped": "terminal_deal", "counterparty": "Kestrel Data Center"}
    assert calls == []

    live = de.draft_once("google", "Gmail", "a@b.com", "Next steps", "body",
                         counterparty="Meridian Solar")
    assert live["ok"] is True
    assert len(calls) == 1


def test_draft_idempotent(ledger_dir, monkeypatch):
    monkeypatch.setattr(de, "counterparty_is_terminal", lambda who: False)
    monkeypatch.setattr(de, "draft", lambda *a, **k: {
        "ok": True, "web_link": "L", "id": "1"})
    items = [("Gmail", "Ana", "Kestrel term sheet"),
             ("Outlook", "Dana", "Meridian redline v4"),
             ("Gmail", "Ana", "Q3 pricing")]
    for label, who, subject in items:
        de.draft_once("google", label, "a@b.com", subject, "body", counterparty=who)
    before = json.loads((ledger_dir / "draft_ledger.json").read_text(encoding="utf-8"))
    for label, who, subject in items:
        de.draft_once("google", label, "a@b.com", f"RE: {subject}", "body",
                      counterparty=who)
    after = json.loads((ledger_dir / "draft_ledger.json").read_text(encoding="utf-8"))
    assert before.keys() == after.keys()
    assert len(after) == 3


def test_draft_key_normalizes_reply_prefixes():
    base = de.draft_key("Gmail", "Ana", "Kestrel term sheet")
    for variant in ("Re: Kestrel term sheet", "RE: FW:  Kestrel   term sheet",
                    "Fwd: Re: Kestrel term sheet"):
        assert de.draft_key("Gmail", "Ana", variant) == base
    assert de.draft_key("Outlook", "Ana", "Kestrel term sheet") != base


def test_a_missing_hotcache_never_blocks_a_draft(monkeypatch, tmp_path):
    monkeypatch.setattr(cl, "hotcache_path", lambda: tmp_path / "nope.md")
    assert de.counterparty_is_terminal("Anyone") is False
