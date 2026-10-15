"""A draft that answers a message is written into that message's thread.

The report this file exists for: a reply to a request arrived in Drafts as a
new email to the same person, with no thread around it and nothing quoted
beneath it. Both providers are faked here in the shapes a live run returned on
2026-10-12 (Gmail metadata headers and a raw draft, Graph createReply handing
back an HTML body with the original already quoted).
"""
import base64
import email
import json

import pytest

import draft_email as de

THEM = "dana@intersolar.example"
MID = "<CAF-original-123@mail.intersolar.example>"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _gmail_message(mid, thread, subject, sender=f"Dana Ortiz <{THEM}>",
                   labels=("INBOX",), text="Can you send the booth layout?\n\nDana"):
    return {
        "id": mid, "threadId": thread, "labelIds": list(labels),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 5 Oct 2026 09:14:00 -0700"},
                {"name": "Message-Id", "value": MID},
                {"name": "References", "value": "<root@mail.intersolar.example>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(text)}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
            ],
        },
    }


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGmail:
    """users().messages().list/get and users().drafts().create, nothing else."""

    def __init__(self, messages, fail_list=None, signature="",
                 fail_signature=None):
        self.signature = signature
        self.fail_signature = fail_signature
        self.store = {m["id"]: m for m in messages}
        self.order = [m["id"] for m in messages]            # newest first
        self.fail_list = fail_list
        self.queries = []
        self.created = []

    def users(self):
        return self

    def messages(self):
        return self

    def drafts(self):
        return _Drafts(self)

    def settings(self):
        return _Settings(self)

    def list(self, userId, q, maxResults):
        def run():
            self.queries.append(q)
            if self.fail_list:
                raise self.fail_list
            return {"messages": [{"id": i} for i in self.order]}
        return _Call(run)

    def get(self, userId, id, format, metadataHeaders=None):
        return _Call(lambda: self.store[id])


class _Drafts:
    def __init__(self, gmail):
        self.gmail = gmail

    def create(self, userId, body):
        def run():
            self.gmail.created.append(body)
            return {"id": "draft-1"}
        return _Call(run)


class _Settings:
    def __init__(self, gmail):
        self.gmail = gmail

    def sendAs(self):
        return self

    def list(self, userId):
        def run():
            if self.gmail.fail_signature:
                raise self.gmail.fail_signature
            return {"sendAs": [
                {"sendAsEmail": "alias@example.com", "signature": "<b>Alias</b>"},
                {"sendAsEmail": "me@example.com", "isPrimary": True,
                 "signature": self.gmail.signature},
            ]}
        return _Call(run)


class _GoogleClient:
    email = "me@example.com"

    def __init__(self, gmail):
        self.gmail = gmail


def _gmail_draft(monkeypatch, gmail, subject="Re: Intersolar booth layout"):
    monkeypatch.setattr("google_client.google_client",
                        lambda label: _GoogleClient(gmail))
    result = de.draft_gmail("Gmail", THEM, subject, "Layout attached.\n\nSam")
    sent = gmail.created[0]["message"]
    return result, sent, email.message_from_bytes(
        base64.urlsafe_b64decode(sent["raw"]))


# ── Gmail ────────────────────────────────────────────────────────────────────

def test_gmail_reply_lands_in_the_thread(monkeypatch):
    gmail = FakeGmail([_gmail_message("m9", "t-intersolar",
                                      "Intersolar booth layout")])
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["threaded"] is True and result["thread_note"] == ""
    assert sent["threadId"] == "t-intersolar"
    # The mail header, never Gmail's own id: the other side's mail client
    # threads on this and cannot follow an API id.
    assert msg["In-Reply-To"] == MID
    # A long header is folded across lines on the wire, so compare the ids.
    assert msg["References"].split() == ["<root@mail.intersolar.example>", MID]
    assert msg["Subject"] == "Re: Intersolar booth layout"
    assert msg["To"] == THEM


def test_gmail_reply_quotes_the_original_beneath(monkeypatch):
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout")])
    _, _, msg = _gmail_draft(monkeypatch, gmail)
    body = msg.get_payload(decode=True).decode("utf-8")
    assert body.startswith("Layout attached.\n\nSam\n\nOn Mon, 5 Oct 2026")
    assert f"Dana Ortiz <{THEM}> wrote:" in body
    assert "> Can you send the booth layout?\n>\n> Dana" in body


def test_gmail_same_person_other_subject_stays_a_new_message(monkeypatch):
    """The search is loose, so the subject is checked here. A reply filed
    into the wrong conversation is worse than a new message."""
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout 2027")])
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["threaded"] is False
    assert "threadId" not in sent
    assert msg["In-Reply-To"] is None and msg["References"] is None
    assert msg["Subject"] == "Re: Intersolar booth layout"
    assert ">" not in msg.get_payload(decode=True).decode("utf-8")


def test_gmail_skips_a_draft_and_answers_the_real_message(monkeypatch):
    gmail = FakeGmail([
        _gmail_message("d1", "t-other", "Intersolar booth layout",
                       labels=("DRAFT",)),
        _gmail_message("m9", "t-real", "RE: Intersolar booth layout"),
    ])
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert sent["threadId"] == "t-real"
    assert msg["Subject"] == "RE: Intersolar booth layout"


def test_gmail_search_names_the_person_and_leaves_out_drafts(monkeypatch):
    gmail = FakeGmail([])
    result, sent, _ = _gmail_draft(monkeypatch, gmail)
    assert result["threaded"] is False and result["thread_note"] == ""
    query = gmail.queries[0]
    assert f"from:{THEM}" in query and f"to:{THEM}" in query
    assert "subject:(intersolar booth layout)" in query
    assert "-in:drafts" in query


def test_gmail_failed_lookup_is_a_new_message_and_says_so(monkeypatch):
    gmail = FakeGmail([], fail_list=TimeoutError(f"reading {THEM}"))
    result, sent, _ = _gmail_draft(monkeypatch, gmail)
    assert result["ok"] is True and result["threaded"] is False
    assert result["thread_note"] == "thread lookup failed (TimeoutError)"
    assert THEM not in result["thread_note"]
    assert "threadId" not in sent


def test_gmail_original_with_no_plain_text_still_threads(monkeypatch):
    original = _gmail_message("m9", "t1", "Intersolar booth layout")
    original["payload"]["parts"] = original["payload"]["parts"][1:]
    gmail = FakeGmail([original])
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["threaded"] is True and sent["threadId"] == "t1"
    assert msg.get_payload(decode=True).decode("utf-8") == "Layout attached.\n\nSam\n"


# ── Outlook ──────────────────────────────────────────────────────────────────

def _graph_message(mid, subject, sender=THEM, received="2026-10-05T16:14:00Z",
                   draft=False, to="me@example.com"):
    return {"id": mid, "subject": subject, "isDraft": draft,
            "receivedDateTime": received,
            "from": {"emailAddress": {"address": sender}},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "ccRecipients": []}


QUOTED = ('<html><head></head><body lang="EN-US"><hr><div><b>From:</b> Dana'
          "</div><div>Can you send the booth layout?</div></body></html>")


class FakeGraph:
    def __init__(self, found, patch_error=None):
        self.found = found
        self.patch_error = patch_error
        self.calls = []

    def messages(self, **odata):
        self.calls.append(("search", odata))
        return {"value": self.found}

    def post(self, path, body=None):
        self.calls.append(("post", path, body))
        return {"id": "reply-1", "webLink": "https://outlook.example/reply-1",
                "body": {"contentType": "html", "content": QUOTED}}

    def patch(self, path, body=None):
        self.calls.append(("patch", path, body))
        if self.patch_error:
            raise self.patch_error
        return {"id": "reply-1"}

    def delete(self, path):
        self.calls.append(("delete", path))

    def create_draft(self, message):
        self.calls.append(("create_draft", message))
        return {"id": "new-1", "webLink": "https://outlook.example/new-1"}


def _outlook_draft(monkeypatch, graph, body="Layout attached.\n\nSam"):
    monkeypatch.setattr("microsoft_client.microsoft_client", lambda label: graph)
    return de.draft_outlook("Outlook", THEM, "Re: Intersolar booth layout", body)


def test_outlook_reply_is_created_on_the_newest_matching_message(monkeypatch):
    graph = FakeGraph([
        _graph_message("old", "Intersolar booth layout",
                       received="2026-10-01T10:00:00Z"),
        _graph_message("new", "RE: Intersolar booth layout",
                       received="2026-10-05T16:14:00Z"),
    ])
    result = _outlook_draft(monkeypatch, graph)
    kinds = [c[0] for c in graph.calls]
    assert kinds == ["search", "post", "patch"], kinds
    assert graph.calls[0][1]["search"] == f'"participants:{THEM}"'
    assert graph.calls[1][1] == "/me/messages/new/createReply"
    assert result == {"ok": True, "web_link": "https://outlook.example/reply-1",
                      "id": "reply-1", "threaded": True, "signed": False,
                      "thread_note": ""}


def test_outlook_reply_puts_our_text_above_the_quoted_original(monkeypatch):
    graph = FakeGraph([_graph_message("m1", "Intersolar booth layout")])
    _outlook_draft(monkeypatch, graph, body="Up to <5 MW> & more.\n\nSam")
    _, path, patch = graph.calls[-1]
    assert path == "/me/messages/reply-1"
    assert patch["toRecipients"] == [{"emailAddress": {"address": THEM}}]
    content = patch["body"]["content"]
    assert patch["body"]["contentType"] == "HTML"
    ours = "<div>Up to &lt;5 MW&gt; &amp; more.<br><br>Sam</div><br>"
    assert content.startswith('<html><head></head><body lang="EN-US">' + ours + "<hr>")
    assert content.endswith("Can you send the booth layout?</div></body></html>")


def test_outlook_same_subject_from_someone_else_is_not_the_thread(monkeypatch):
    graph = FakeGraph([_graph_message("m1", "Intersolar booth layout",
                                      sender="other@elsewhere.example")])
    result = _outlook_draft(monkeypatch, graph)
    assert result["threaded"] is False and result["id"] == "new-1"
    assert [c[0] for c in graph.calls] == ["search", "create_draft"]


def test_outlook_follow_up_to_our_own_last_message_goes_to_them(monkeypatch):
    """Nobody answered, so the newest message in the thread is the user's own.
    The reply still goes to the other person, not back to the user."""
    graph = FakeGraph([_graph_message("mine", "Intersolar booth layout",
                                      sender="me@example.com", to=THEM)])
    result = _outlook_draft(monkeypatch, graph)
    assert result["threaded"] is True
    assert graph.calls[1][1] == "/me/messages/mine/createReply"
    assert graph.calls[-1][2]["toRecipients"] == [
        {"emailAddress": {"address": THEM}}]


def test_outlook_skips_drafts_and_other_subjects(monkeypatch):
    graph = FakeGraph([
        _graph_message("d", "Intersolar booth layout", draft=True),
        _graph_message("x", "Intersolar invoice"),
    ])
    assert _outlook_draft(monkeypatch, graph)["threaded"] is False


def test_outlook_reply_that_cannot_be_filled_is_not_left_empty(monkeypatch):
    graph = FakeGraph([_graph_message("m1", "Intersolar booth layout")],
                      patch_error=RuntimeError("graph said no"))
    with pytest.raises(RuntimeError):
        _outlook_draft(monkeypatch, graph)
    assert graph.calls[-1] == ("delete", "/me/messages/reply-1")


# ── Signatures ───────────────────────────────────────────────────────────────

SIG = '<div dir="ltr"><b>Dana Reyes</b><br>CEO, Kestrel &amp; Co</div>'


def _halves(msg):
    assert msg.get_content_type() == "multipart/alternative"
    plain, rich = msg.get_payload()
    return (plain.get_payload(decode=True).decode("utf-8"),
            rich.get_payload(decode=True).decode("utf-8"))


def test_gmail_signature_sits_under_the_text_and_above_the_quote(monkeypatch):
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout")],
                      signature=SIG)
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["signed"] is True and result["threaded"] is True
    plain, rich = _halves(msg)
    assert plain.startswith(
        "Layout attached.\n\nSam\n\nDana Reyes\nCEO, Kestrel & Co\n\nOn Mon,")
    assert plain.index("CEO, Kestrel") < plain.index("> Can you send")
    assert rich.startswith(
        "<div>Layout attached.<br><br>Sam</div><br><div>" + SIG + "</div><br>"
        '<div class="gmail_quote">')
    # The original's own HTML is what gets quoted, as a mail client does it.
    assert rich.endswith("<p>html</p></blockquote></div>")
    assert "<b>Alias</b>" not in rich


def test_gmail_new_message_is_signed_too(monkeypatch):
    gmail = FakeGmail([], signature=SIG)
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["signed"] is True and result["threaded"] is False
    plain, rich = _halves(msg)
    assert plain == "Layout attached.\n\nSam\n\nDana Reyes\nCEO, Kestrel & Co\n"
    assert rich.endswith(SIG + "</div>")


def test_gmail_with_no_saved_signature_stays_plain_text(monkeypatch):
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout")])
    result, _, msg = _gmail_draft(monkeypatch, gmail)
    assert result["signed"] is False and result["thread_note"] == ""
    assert msg.get_content_type() == "text/plain"


def test_gmail_signature_that_cannot_be_read_costs_the_signature_only(monkeypatch):
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout")],
                      fail_signature=PermissionError("403 for me@example.com"))
    result, sent, msg = _gmail_draft(monkeypatch, gmail)
    assert result["ok"] is True and result["threaded"] is True
    assert result["signed"] is False
    assert result["thread_note"] == "reading the signature failed (PermissionError)"
    assert sent["threadId"] == "t1"


def test_a_signature_in_config_wins_over_the_mailbox_one(monkeypatch):
    monkeypatch.setattr(de, "_config_signature",
                        lambda label: "Sam Rivera\nKestrel <Energy>")
    gmail = FakeGmail([], signature=SIG,
                      fail_signature=AssertionError("the mailbox was asked"))
    result, _, msg = _gmail_draft(monkeypatch, gmail)
    plain, rich = _halves(msg)
    assert result["signed"] is True and result["thread_note"] == ""
    assert plain.endswith("Sam Rivera\nKestrel <Energy>\n")
    assert rich.endswith("<div>Sam Rivera<br>Kestrel &lt;Energy&gt;</div>")


def test_config_signature_is_read_from_the_account_by_label(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "accounts", lambda: [
        {"label": "Gmail", "provider": "google", "email": "a@b.com"},
        {"label": "Outlook", "provider": "microsoft", "email": "c@d.com",
         "signature": "  Sam Rivera\nKestrel  "},
    ])
    assert de._config_signature("outlook") == "Sam Rivera\nKestrel"
    assert de._config_signature("Gmail") == ""
    assert de._config_signature("Nobody") == ""


def test_outlook_reply_carries_the_config_signature_above_the_quote(monkeypatch):
    monkeypatch.setattr(de, "_config_signature", lambda label: SIG)
    graph = FakeGraph([_graph_message("m1", "Intersolar booth layout")])
    result = _outlook_draft(monkeypatch, graph)
    content = graph.calls[-1][2]["body"]["content"]
    assert result["signed"] is True
    assert ("<div>Layout attached.<br><br>Sam</div><br><div>" + SIG
            + "</div><br><hr>") in content


def test_outlook_new_message_carries_the_config_signature(monkeypatch):
    monkeypatch.setattr(de, "_config_signature", lambda label: "Sam Rivera\nCEO")
    graph = FakeGraph([])
    result = _outlook_draft(monkeypatch, graph)
    body = graph.calls[-1][1]["body"]
    assert result["signed"] is True and result["threaded"] is False
    assert body == {"contentType": "HTML", "content":
                    "<div>Layout attached.</div><br>"
                    "<div>Sam Rivera<br>CEO</div>"}


def test_outlook_with_no_signature_set_is_unchanged(monkeypatch):
    graph = FakeGraph([])
    result = _outlook_draft(monkeypatch, graph)
    assert result["signed"] is False
    assert graph.calls[-1][1]["body"] == {
        "contentType": "Text", "content": "Layout attached.\n\nSam"}


# A saved signature in the shape a live mailbox returned on 2026-10-12: its own
# closing, the name, and line breaks that carry attributes.
LIVE_SIG = ('<div dir="ltr"><div><font face="arial">Sincerely,<br clear="all">'
            "<span>Sam Rivera</span><br clear=\"all\"><span>C. 555-0100</span>"
            "</font></div></div>")


def test_signature_text_keeps_one_line_per_break_with_attributes():
    assert de._signature_text(LIVE_SIG) == "Sincerely,\nSam Rivera\nC. 555-0100"


@pytest.mark.parametrize("body, signature, kept", [
    # The signature closes and names, so the body's closing and name both go.
    ("See attached.\n\nBest,\nSam", LIVE_SIG, "See attached."),
    # The signature only names, so the body keeps its closing.
    ("See attached.\n\nBest,\nSam", "Sam Rivera\nCEO", "See attached.\n\nBest,"),
    ("See attached.\n\nSam", "Sam Rivera\nCEO", "See attached."),
    # A different name, a sentence, or a one-line body is left alone.
    ("See attached.\n\nBest,\nSam", "Kestrel Energy\nCharlotte",
     "See attached.\n\nBest,\nSam"),
    ("See attached.\n\nTalk Tuesday.", LIVE_SIG, "See attached.\n\nTalk Tuesday."),
    ("Sam", LIVE_SIG, "Sam"),
    # One shared word is not the name: this took a real last line once.
    ("See attached.\n\nKestrel rocks", "Sam Rivera\nKestrel Energy",
     "See attached.\n\nKestrel rocks"),
    ("See attached.\n\nSam Rivera", "Sam Rivera\nCEO", "See attached."),
])
def test_the_name_is_not_signed_twice(body, signature, kept):
    assert de._without_signoff(body, signature) == kept


def test_gmail_draft_drops_the_sign_off_the_signature_repeats(monkeypatch):
    gmail = FakeGmail([], signature=LIVE_SIG)
    _, _, msg = _gmail_draft(monkeypatch, gmail)
    plain, rich = _halves(msg)
    assert plain == "Layout attached.\n\nSincerely,\nSam Rivera\nC. 555-0100\n"
    assert rich.startswith("<div>Layout attached.</div><br><div>" + LIVE_SIG)


# ── What the caller is told ──────────────────────────────────────────────────

def test_the_ledger_and_the_cli_say_whether_it_was_threaded(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(de, "_ledger_path", lambda: tmp_path / "ledger.json")
    monkeypatch.setattr(de, "counterparty_is_terminal", lambda who: False)
    gmail = FakeGmail([_gmail_message("m9", "t1", "Intersolar booth layout")])
    monkeypatch.setattr("google_client.google_client",
                        lambda label: _GoogleClient(gmail))
    body = tmp_path / "b.txt"
    body.write_text("Layout attached.\n", encoding="utf-8")
    rc = de.main(["--provider", "google", "--label", "Gmail", "--to", THEM,
                  "--subject", "Re: Intersolar booth layout",
                  "--body-file", str(body)])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["threaded"] is True
    stored = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert [e["threaded"] for e in stored.values()] == [True]
    assert [e["signed"] for e in stored.values()] == [False]


@pytest.mark.parametrize("subject, core", [
    ("Re: RE: Fwd:  Kestrel   term sheet", "kestrel term sheet"),
    ("Kestrel term sheet", "kestrel term sheet"),
    ("AW: Angebot", "angebot"),
    ("", ""),
])
def test_subject_core(subject, core):
    assert de.subject_core(subject) == core
