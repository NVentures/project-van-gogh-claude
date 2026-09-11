"""Contact capture: the filter, the enrichment, and the scope migration.

The point of this suite is that each test fails when its rule is deleted. The
filter tests in particular are written as mutation targets: promoting the wrong
people is the failure that costs a user an afternoon of manual cleanup, and a
test that passes with the filter removed would not have caught it.
"""

import re
import sys
import types

import pytest

import contact_capture as cc
import failure_class


# ------------------------------------------------------------- the filter


def test_robot_addresses_are_never_promoted():
    """Each shape asserted on its own: a set-level scan passes if any match."""
    robots = [
        "noreply@stripe.com", "no-reply@google.com", "do-not-reply@bank.com",
        "donotreply@united.com", "notifications@slack.com", "alerts@aws.com",
        "mailer-daemon@gmail.com", "postmaster@corp.com", "bounce@sendgrid.net",
        "support@zendesk.com", "billing@vendor.com", "invoices@vendor.com",
        "newsletter@substack.com", "marketing@hubspot.com", "careers@corp.com",
        "calendar-notification@google.com", "info@agency.com",
        "b7f3a91c8e2d4f6a0b1c3d5e7f9a1b3c5d7e9f1a2b@bounces.example.com",
    ]
    for addr in robots:
        assert cc.is_robot_address(addr), f"should be filtered: {addr}"


def test_real_people_survive_the_robot_filter():
    """The filter must not be so greedy it eats the people we exist to save."""
    people = [
        "a.mercer@northwind.example", "tquinn@lawfirm.example",
        "priya@brightline.example", "j.chen@gmail.com", "rlouis@utility.example",
        # A person at a domain whose NAME contains a filtered word:
        "sarah@mailchimp.com", "dan@supportivecare.example",
        # A person whose local part merely starts with a filtered substring:
        "infowright@corp.com", "newton@corp.com", "teamer@corp.com",
        "helper@corp.com", "salesman.dave@corp.com",
    ]
    for addr in people:
        assert not cc.is_robot_address(addr), f"should survive: {addr}"


def _msg(headers, body=""):
    import base64
    payload = {
        "headers": [{"name": k, "value": v} for k, v in headers.items()],
        "mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
    }
    return {"payload": payload}


class FakeGmail:
    """The two Gmail calls scan_account makes, over a scripted mailbox."""

    def __init__(self, sent, received):
        self._sent, self._recv = sent, received

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId=None, q=None, pageToken=None, maxResults=None):
        pool = self._sent if "in:sent" in q and "-in:sent" not in q else self._recv
        ids = [{"id": f"{'s' if pool is self._sent else 'r'}{i}"}
               for i in range(len(pool))]
        return types.SimpleNamespace(execute=lambda: {"messages": ids})

    def get(self, userId=None, id=None, format=None, metadataHeaders=None):
        pool = self._sent if id.startswith("s") else self._recv
        return types.SimpleNamespace(execute=lambda: pool[int(id[1:])])


class FakeClient:
    def __init__(self, sent, received):
        self.gmail = FakeGmail(sent, received)


OWNER = "owner@company.com"


def test_two_way_filter_is_the_whole_point():
    """Only a real exchange is promoted: sent AND replied.

    Four people, one of each shape. Delete the two-way rule and this fails on
    the one-way cases, which is the mutation that matters.
    """
    sent = [
        _msg({"To": "replied@corp.com", "Date": "Mon, 1 Sep 2026 10:00:00 +0000"}),
        _msg({"To": "silent@corp.com", "Date": "Mon, 1 Sep 2026 10:00:00 +0000"}),
        _msg({"To": "noreply@corp.com", "Date": "Mon, 1 Sep 2026 10:00:00 +0000"}),
    ]
    recv = [
        _msg({"From": "Rita Replied <replied@corp.com>",
              "Subject": "Re: the term sheet",
              "Date": "Tue, 2 Sep 2026 11:00:00 +0000"}, "Thanks!\n\nBest,\nRita"),
        # Wrote to us but we never wrote to them: a cold inbound, not a contact.
        _msg({"From": "Cold Caller <inbound@vendor.com>",
              "Date": "Tue, 2 Sep 2026 11:00:00 +0000"}, "Hi!"),
    ]
    out = cc.scan_account(FakeClient(sent, recv), OWNER, 365, log=lambda *_: None)

    assert set(out) == {"replied@corp.com"}, (
        "only the two-way exchange should promote; got " + repr(sorted(out)))
    assert out["replied@corp.com"]["name"] == "Rita Replied"
    assert out["replied@corp.com"]["last_subject"] == "Re: the term sheet"


def test_owner_is_never_their_own_contact():
    sent = [_msg({"To": OWNER, "Date": "Mon, 1 Sep 2026 10:00:00 +0000"})]
    recv = [_msg({"From": f"Me <{OWNER}>", "Date": "Mon, 1 Sep 2026 10:00:00 +0000"})]
    out = cc.scan_account(FakeClient(sent, recv), OWNER, 365, log=lambda *_: None)
    assert out == {}


def test_owner_match_is_case_insensitive():
    """A header spelling the owner's own address in caps is still the owner."""
    sent = [_msg({"To": "Owner@Company.com", "Date": "Mon, 1 Sep 2026 10:00:00 +0000"})]
    recv = [_msg({"From": f"Me <OWNER@COMPANY.COM>",
                  "Date": "Mon, 1 Sep 2026 10:00:00 +0000"})]
    out = cc.scan_account(FakeClient(sent, recv), OWNER, 365, log=lambda *_: None)
    assert out == {}


# ------------------------------------------------------------- enrichment


def test_signature_yields_title_and_phone():
    body = (
        "Sounds good, let's talk Thursday.\n\n"
        "Best,\n"
        "Marcus Webb\n"
        "Chief Financial Officer\n"
        "Northwind Energy\n"
        "(415) 555-0198\n"
    )
    sig = cc.parse_signature(body, "Marcus Webb")
    assert sig["title"].lower() == "chief financial officer"
    assert "555-0198" in sig["phone"]


def test_signature_ignores_boilerplate_and_years():
    """A disclaimer is not a title and a copyright year is not a phone number."""
    body = (
        "Regards,\nDana\n"
        "This email and any attachments are confidential and privileged.\n"
        "If you are not the intended recipient, delete it.\n"
        "© 2026 Example Corp. All rights reserved.\n"
    )
    sig = cc.parse_signature(body, "Dana")
    assert sig["title"] == ""
    assert sig["phone"] == ""


def test_org_from_domain_never_guesses_a_consumer_mailbox():
    """A gmail address says nothing about an employer, so it must stay empty."""
    for addr in ("j.chen@gmail.com", "x@yahoo.com", "y@icloud.com",
                 "z@outlook.com", "q@protonmail.com"):
        assert cc.org_from_domain(addr) == "", addr


def test_org_from_domain_reads_a_company():
    assert cc.org_from_domain("ben@palladiumenergy.com") == "Palladiumenergy"
    assert cc.org_from_domain("a@suncast-energy.com") == "Suncast Energy"
    assert cc.org_from_domain("a@team.example.co.uk") == "Example"


def test_note_records_provenance_and_recency():
    note = cc.build_note(
        {"email": "r@corp.com", "last_contact": "2026-09-02", "sent_count": 3,
         "reply_count": 2, "last_subject": "Re: the term sheet"}, OWNER)
    assert OWNER in note                      # which mailbox this came from
    assert "2026-09-02" in note               # when they were last heard from
    assert "5 messages" in note               # how much history there is
    assert "term sheet" in note               # what it was about


# ------------------------------------------------------- the scope migration


@pytest.mark.parametrize("text", [
    "403 Request had insufficient authentication scopes.",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    "insufficientPermissions",
])
def test_missing_scope_is_an_auth_failure_and_is_never_retried(text):
    """A token minted before the scope existed cannot be fixed by retrying.

    Without this, the watcher reads the 403 as "unknown", treats it as
    transient, and re-runs a job that can never succeed until a human consents
    again.
    """
    assert failure_class.classify(text) == "auth"
    assert "auth" in failure_class.NO_RETRY


@pytest.mark.parametrize("text,expected", [
    ("Connection reset by peer", "network"),
    ("You're out of extra usage, resets 3:40pm", "quota"),
    ("Some unrelated explosion", "unknown"),
])
def test_scope_regex_does_not_swallow_neighbouring_classes(text, expected):
    """The near misses: a broadened auth regex must not eat other classes."""
    assert failure_class.classify(text) == expected


def test_both_scope_lists_stay_identical():
    """google_client and auth_bootstrap must request the same scopes.

    They are two literal lists in two files; a scope added to one and not the
    other means consent grants a permission the client never uses, or the
    client uses one consent never requested.
    """
    import auth_bootstrap
    import google_client
    assert auth_bootstrap.GOOGLE_SCOPES == google_client.GOOGLE_SCOPES


def test_contacts_scopes_are_actually_requested():
    import auth_bootstrap
    assert "https://www.googleapis.com/auth/contacts" in auth_bootstrap.GOOGLE_SCOPES
    assert ("https://www.googleapis.com/auth/contacts.other.readonly"
            in auth_bootstrap.GOOGLE_SCOPES)


# ------------------------------------------------------------- write safety


def test_dry_run_is_the_default_and_writes_nothing(monkeypatch):
    """The address book is not a scratch pad: no --apply, no writes."""
    calls = []

    def boom(*a, **k):                       # any write attempt fails the test
        calls.append(a)
        raise AssertionError("a dry run must not write a contact")

    monkeypatch.setattr(cc, "promote", boom)
    monkeypatch.setattr(cc, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(cc, "load_state",
                        lambda: {"promoted": {}, "skipped": {}, "last_run": ""})

    fake_ds = types.SimpleNamespace(is_tier1=lambda: True)
    monkeypatch.setitem(sys.modules, "data_sources", fake_ds)
    monkeypatch.setattr(cc.config_loader, "google_accounts",
                        lambda: [{"label": "Gmail", "email": OWNER}])
    monkeypatch.setitem(sys.modules, "google_client",
                        types.SimpleNamespace(google_client=lambda label: object()))
    monkeypatch.setattr(cc, "scan_account", lambda *a, **k: {
        "r@corp.com": {"email": "r@corp.com", "name": "R", "sent_count": 1,
                       "reply_count": 1, "last_contact": "2026-09-01",
                       "last_subject": "", "signature": {}}})
    monkeypatch.setattr(cc, "list_other_contacts", lambda *a, **k: {})
    monkeypatch.setattr(cc, "existing_contact_emails", lambda *a, **k: set())

    summary = cc.run(days=365, dry_run=True, limit=0, log=lambda *_: None)
    assert calls == []
    assert summary["candidates"] == 1
    assert summary["promoted"] == 0
    assert summary["dry_run"] is True


def test_already_saved_contacts_are_skipped(monkeypatch):
    """Someone already in the address book is never added a second time."""
    monkeypatch.setattr(cc, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(cc, "load_state",
                        lambda: {"promoted": {}, "skipped": {}, "last_run": ""})
    monkeypatch.setitem(sys.modules, "data_sources",
                        types.SimpleNamespace(is_tier1=lambda: True))
    monkeypatch.setattr(cc.config_loader, "google_accounts",
                        lambda: [{"label": "Gmail", "email": OWNER}])
    monkeypatch.setitem(sys.modules, "google_client",
                        types.SimpleNamespace(google_client=lambda label: object()))
    monkeypatch.setattr(cc, "scan_account", lambda *a, **k: {
        "known@corp.com": {"email": "known@corp.com", "name": "K",
                           "sent_count": 2, "reply_count": 2,
                           "last_contact": "2026-09-01", "last_subject": "",
                           "signature": {}}})
    monkeypatch.setattr(cc, "list_other_contacts", lambda *a, **k: {})
    monkeypatch.setattr(cc, "existing_contact_emails",
                        lambda *a, **k: {"known@corp.com"})

    summary = cc.run(days=365, dry_run=True, limit=0, log=lambda *_: None)
    assert summary["candidates"] == 0


def test_tier2_install_fails_loudly_rather_than_half_working(monkeypatch):
    """Unattended work requires the real sign-in; it must never degrade."""
    monkeypatch.setitem(sys.modules, "data_sources",
                        types.SimpleNamespace(is_tier1=lambda: False))
    with pytest.raises(RuntimeError, match="install-van-gogh"):
        cc.run(days=30, dry_run=True, limit=0, log=lambda *_: None)


def test_copy_mask_matches_what_the_api_permits():
    """copyMask accepts exactly these three fields; a fourth is a 400."""
    assert set(cc.COPY_MASK.split(",")) == {
        "names", "emailAddresses", "phoneNumbers"}


def test_no_dashes_in_user_facing_strings():
    """House voice: no em or en dashes anywhere in what the user reads."""
    src = (cc.SCOPE_HELP + cc.build_note(
        {"email": "a@b.com", "last_contact": "2026-01-01", "sent_count": 1,
         "reply_count": 1, "last_subject": "x"}, OWNER))
    assert "\u2014" not in src and "\u2013" not in src
