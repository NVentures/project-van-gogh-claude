"""Unit tests for the shared kill/pass detector (deal_status.detect_kills).

Covers the load-bearing cases: a real pass tied to an open thread, a newsletter
that must NOT alert, a kill with no thread match (still surfaced, unattributed),
a forwarded-internal kill (internal sender, external pass content), and the
thread-matched-first sort order.
"""
import deal_status as ds

THREADS = ["Northwind Term Sheet", "Meridian Dev Loan", "Fairview LOI"]


def test_positive_kill_detected_and_attributed():
    a = ds.detect_kills([{
        "subject": "Re: Northwind acquisition",
        "snippet": "we have decided not to move forward and are releasing the projects from exclusivity",
        "from": "Alex Reed", "from_email": "areed@northwind.com",
        "account": "Outlook", "internal": False,
    }], THREADS)
    assert len(a) == 1
    assert a[0]["thread"] == "Northwind Term Sheet"
    # signal is the leftmost kill phrase that fired, not necessarily the most
    # dramatic; assert a real phrase was captured.
    assert a[0]["signal"].strip()


def test_newsletter_produces_no_alert():
    b = ds.detect_kills([{
        "subject": "Your weekly energy newsletter",
        "snippet": "top stories in power markets this week",
        "from": "Industry Daily", "from_email": "pro@industrydaily.com",
        "account": "Gmail", "internal": False,
    }], THREADS)
    assert b == []


def test_unattributed_kill_still_surfaces():
    c = ds.detect_kills([{
        "subject": "Re: Some random vendor",
        "snippet": "we have decided to pass on this",
        "from": "Random Vendor", "from_email": "x@randomvendor.com",
        "account": "Gmail", "internal": False,
    }], THREADS)
    assert len(c) == 1 and c[0]["thread"] is None


def test_forwarded_internal_kill_detected():
    # internal sender, external pass content; still attributed via subject tokens.
    d = ds.detect_kills([{
        "subject": "FW: Northwind deal",
        "snippet": "FYI below, they are no longer interested and want to terminate",
        "from": "Internal Teammate", "from_email": "teammate@company.com",
        "account": "Outlook", "internal": True,
    }], THREADS)
    assert len(d) == 1
    assert d[0]["forwarded"] is True
    assert d[0]["thread"] == "Northwind Term Sheet"


def test_thread_matched_sorts_before_unattributed():
    mixed = ds.detect_kills([
        {"subject": "random pass", "snippet": "we'll pass on it",
         "from": "X", "from_email": "x@nomatch.com", "account": "Gmail", "internal": False},
        {"subject": "Meridian update", "snippet": "we are withdrawing from the Meridian deal",
         "from": "Y", "from_email": "y@meridian.com", "account": "Gmail", "internal": False},
    ], THREADS)
    assert len(mixed) == 2
    assert mixed[0]["thread"] is not None and mixed[1]["thread"] is None


# ── Meeting status-change scan (kills AND re-engagement/pivots) ───────────────
# A buyer re-engagement / pivot announced in a meeting reaches no mail, so the
# email scan above can never see it. Meeting alerts require deal attribution
# (meeting summaries are unfiltered, unlike the deal-ish inbound slice), and the
# bare "terminat"/"withdraw" kill terms are context-gated so contract boilerplate
# does not false-fire.
MEETING_THREADS = ["Northwind Acquisition", "Meridian Dev Loan", "Fairview LOI"]


def test_meeting_reengagement_detected_and_attributed():
    a = ds.detect_meeting_status_changes([{
        "title": "Northwind Buyer - Catch Up",
        "date": "2026-06-04",
        "text": ("Northwind CEO interested in pursuing both Project Atlas and "
                 "Project Beacon. Preference for a blended offer as a package "
                 "deal. Team restarting diligence immediately. Nonbinding offer "
                 "by end of next week."),
    }], MEETING_THREADS)
    assert len(a) == 1
    assert a[0]["thread"] == "Northwind Acquisition"
    assert a[0]["source"] == "meeting"
    assert a[0]["signal"].strip()


def test_benign_meeting_no_alert():
    b = ds.detect_meeting_status_changes([{
        "title": "Weekly Eng Standup", "date": "2026-06-04",
        "text": "Reviewed sprint progress, deployment timeline, and code review backlog.",
    }], MEETING_THREADS)
    assert b == []


def test_unattributed_meeting_status_change_suppressed():
    # Status-change language but NO matching deal -> suppressed (the noise gate).
    c = ds.detect_meeting_status_changes([{
        "title": "Random Vendor Sync", "date": "2026-06-04",
        "text": "They are no longer interested and want to terminate the engagement.",
    }], MEETING_THREADS)
    assert c == []


def test_meeting_kill_detected_and_attributed():
    d = ds.detect_meeting_status_changes([{
        "title": "Meridian call", "date": "2026-06-04",
        "text": "Meridian confirmed they are withdrawing from the deal.",
    }], MEETING_THREADS)
    assert len(d) == 1 and d[0]["thread"] == "Meridian Dev Loan"


def test_contract_boilerplate_termination_does_not_alert():
    # Meeting summaries are dense with "termination language" / "if the deal
    # terminates" clause talk; the bare "terminat" term is context-gated so this
    # stays quiet even though it ties to an open deal.
    e = ds.detect_meeting_status_changes([{
        "title": "Fairview LOI", "date": "2026-06-04",
        "text": ("Reviewed contract terms. Security termination language: the "
                 "counterparty keeps 50% of the security if the deal terminates early."),
    }], MEETING_THREADS)
    assert e == []


def test_meeting_terminate_the_agreement_still_fires():
    f = ds.detect_meeting_status_changes([{
        "title": "Meridian call", "date": "2026-06-04",
        "text": "Meridian told us they want to terminate the agreement and move on.",
    }], MEETING_THREADS)
    assert len(f) == 1 and f[0]["thread"] == "Meridian Dev Loan"


def test_empty_summary_meeting_skipped():
    g = ds.detect_meeting_status_changes([{
        "title": "No summary meeting", "date": "2026-06-04", "text": "",
    }], MEETING_THREADS)
    assert g == []
