"""The live mail with one attendee: who wrote last, and whether anyone looked.

Two ways this could mislead are worth a test each. An unsent draft sits in
the thread it answers, and counting it reports the user as having replied.
And a mailbox that failed to read looks exactly like a person nobody has
written to, unless the result says which of the two it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import attendee_mail as am
import config_loader

NOW = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)
THEM = "dana@quarry.example"
GMAIL = {"provider": "google", "label": "Gmail", "email": "primary@gmail.com"}
OUTLOOK = {"provider": "microsoft", "label": "Outlook", "email": "user@company.com"}


def _ms(days_ago: float) -> str:
    return str(int((NOW - timedelta(days=days_ago)).timestamp() * 1000))


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gmsg(mid, thread, sender, days_ago, subject="Site visit", labels=("INBOX",)):
    return {"id": mid, "threadId": thread, "internalDate": _ms(days_ago),
            "labelIds": list(labels), "snippet": f"snippet {mid} &amp; more",
            "payload": {"headers": [{"name": "From", "value": f"Someone <{sender}>"},
                                    {"name": "Subject", "value": subject}]}}


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _GmailMessages:
    def __init__(self, service):
        self.service = service

    def list(self, **kwargs):
        self.service.seen.append(kwargs)
        data = self.service._data
        if isinstance(data, Exception):
            return _Exec(data)
        return _Exec({"messages": [{"id": m["id"]} for m in data]})

    def get(self, **kwargs):
        return _Exec(next(m for m in self.service._data if m["id"] == kwargs["id"]))


@pytest.fixture
def google(monkeypatch):
    """Install a fake Gmail; returns a setter and the list of queries seen."""
    seen = []

    class _Service:
        def users(self):
            return self

        def messages(self):
            return _GmailMessages(self)

    service = _Service()
    service.seen = seen
    service._data = []

    class _Client:
        gmail = service

    import google_client
    monkeypatch.setattr(google_client, "google_client", lambda label: _Client())

    def set_messages(data):
        service._data = data

    return set_messages, seen


@pytest.fixture
def graph(monkeypatch):
    calls = []
    box = {"value": []}

    class _Client:
        def messages(self, **odata):
            calls.append(odata)
            if isinstance(box["value"], Exception):
                raise box["value"]
            return {"value": box["value"]}

    import microsoft_client
    monkeypatch.setattr(microsoft_client, "microsoft_client", lambda label: _Client())

    def set_messages(data):
        box["value"] = data

    return set_messages, calls


def _graph_msg(mid, conv, sender, days_ago, to=(THEM,), draft=False, subject="Term sheet"):
    return {"id": mid, "conversationId": conv, "subject": subject, "isDraft": draft,
            "from": {"emailAddress": {"address": sender}},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [], "receivedDateTime": _iso(days_ago),
            "bodyPreview": f"preview {mid}"}


# ── Who wrote last ───────────────────────────────────────────────────────────

def test_the_newest_message_of_a_thread_says_who_wrote_last(google):
    set_messages, _ = google
    set_messages([_gmsg("a", "t1", "primary@gmail.com", 5),
                  _gmsg("b", "t1", THEM, 2),
                  _gmsg("c", "t2", "primary@gmail.com", 9, subject="Intro")])
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL])
    assert out["checked"] and out["accounts_read"] == ["Gmail"]
    assert [(t["subject"], t["last_from"], t["messages_seen"]) for t in out["threads"]] == \
        [("Site visit", am.THEM, 2), ("Intro", am.YOU, 1)]
    assert out["last_word"] == am.THEM
    assert out["threads"][0]["last_date"] == (NOW - timedelta(days=2)).date().isoformat()
    assert out["threads"][0]["snippet"] == "snippet b & more"


def test_a_third_party_on_the_thread_is_neither_you_nor_them(google):
    set_messages, _ = google
    set_messages([_gmsg("a", "t1", "lawyer@firm.example", 1)])
    assert am.threads_with(THEM, now=NOW, accounts=[GMAIL])["last_word"] == am.OTHER


def test_any_of_the_users_own_addresses_counts_as_you(google):
    set_messages, _ = google
    set_messages([_gmsg("a", "t1", "USER@company.com", 1)])
    assert am.threads_with(THEM, now=NOW, accounts=[GMAIL])["last_word"] == am.YOU


# ── A draft is not a reply ───────────────────────────────────────────────────

def test_an_unsent_draft_never_counts_as_the_last_word(google):
    set_messages, seen = google
    set_messages([_gmsg("a", "t1", THEM, 3),
                  _gmsg("d", "t1", "primary@gmail.com", 0.1, labels=("DRAFT",))])
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL])
    assert out["last_word"] == am.THEM
    assert out["threads"][0]["messages_seen"] == 1
    assert "-in:drafts" in seen[0]["q"]


def test_an_outlook_draft_is_dropped_too(graph):
    set_messages, _ = graph
    set_messages([_graph_msg("a", "c1", THEM, 3, to=("user@company.com",)),
                  _graph_msg("d", "c1", "user@company.com", 0.1, draft=True)])
    out = am.threads_with(THEM, now=NOW, accounts=[OUTLOOK])
    assert out["last_word"] == am.THEM and out["threads"][0]["messages_seen"] == 1


# ── Outlook's search is a relevance match, so it is checked ──────────────────

def test_an_outlook_hit_that_does_not_carry_the_address_is_dropped(graph):
    set_messages, calls = graph
    set_messages([_graph_msg("x", "c9", "other@firm.example", 1, to=("user@company.com",)),
                  _graph_msg("a", "c1", "user@company.com", 2, to=(THEM,))])
    out = am.threads_with(THEM, now=NOW, accounts=[OUTLOOK])
    assert [t["subject"] for t in out["threads"]] == ["Term sheet"]
    assert out["last_word"] == am.YOU
    assert calls[0]["search"] == f'"participants:{THEM}"'


def test_mail_older_than_the_window_is_not_recent(graph):
    set_messages, _ = graph
    set_messages([_graph_msg("a", "c1", THEM, am.WINDOW_DAYS + 1, to=("user@company.com",))])
    out = am.threads_with(THEM, now=NOW, accounts=[OUTLOOK])
    assert out["checked"] and out["threads"] == [] and out["last_word"] == ""


# ── Could not look is not nothing there ──────────────────────────────────────

def test_a_mailbox_that_fails_is_reported_by_type_and_the_other_still_counts(google, graph):
    set_g, _ = google
    set_m, _ = graph
    set_g(RuntimeError("401 for token ya29.SECRET at 12 Home Street"))
    set_m([_graph_msg("a", "c1", THEM, 1, to=("user@company.com",))])
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL, OUTLOOK])
    assert out["checked"] is True
    assert out["accounts_read"] == ["Outlook"]
    assert out["accounts_failed"] == [{"account": "Gmail", "error": "RuntimeError"}]
    assert "SECRET" not in str(out) and "Home Street" not in str(out)
    assert len(out["threads"]) == 1


def test_every_mailbox_failing_is_not_checked(google, graph):
    set_g, _ = google
    set_m, _ = graph
    set_g(RuntimeError("down"))
    set_m(OSError("down"))
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL, OUTLOOK])
    assert out["checked"] is False and out["why"] == "no mailbox could be read"
    assert out["threads"] == []


def test_read_and_empty_is_checked(google):
    set_messages, _ = google
    set_messages([])
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL])
    assert out["checked"] is True and out["threads"] == [] and out["why"] == ""


# ── What may go into a search string ─────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "not an address", 'a@b.example" OR in:anywhere', "a@b.example OR from:x",
    "a b@c.example", "a@b", "(a@b.example)",
])
def test_anything_that_is_not_plainly_an_address_is_never_searched(google, bad):
    _, seen = google
    out = am.threads_with(bad, now=NOW, accounts=[GMAIL])
    assert out["checked"] is False and not seen
    assert out["why"] == "no usable address for this person"


def test_the_gmail_search_is_bounded_to_the_window_and_the_person(google):
    set_messages, seen = google
    set_messages([])
    am.threads_with("Dana@Quarry.example", now=NOW, accounts=[GMAIL])
    query = seen[0]["q"]
    since = (NOW - timedelta(days=am.WINDOW_DAYS)).strftime("%Y/%m/%d")
    assert f"from:{THEM} OR to:{THEM} OR cc:{THEM}" in query
    assert f"after:{since}" in query
    assert seen[0]["maxResults"] == am.LIST_CAP


def test_only_the_newest_threads_are_kept(google):
    set_messages, _ = google
    set_messages([_gmsg(f"m{n}", f"t{n}", THEM, n + 1, subject=f"S{n}") for n in range(6)])
    out = am.threads_with(THEM, now=NOW, accounts=[GMAIL])
    assert [t["subject"] for t in out["threads"]] == ["S0", "S1", "S2"]
    assert len(out["threads"]) == am.MAX_THREADS


def test_the_accounts_are_read_from_settings_when_not_given(google, graph, monkeypatch):
    set_g, seen = google
    set_m, calls = graph
    set_g([])
    set_m([])
    out = am.threads_with(THEM, now=NOW)
    assert out["accounts_read"] == [a["label"] for a in config_loader.accounts()]
    assert len(seen) == 2 and len(calls) == 1
