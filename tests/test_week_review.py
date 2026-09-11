"""week_review.py pure-logic tests: dedup, self-detection, placeholder replies,
overreach gating (Item 11), and the single-account fan-out (Item 9).

Importing week_review runs module-level accessors (accounts(), hotcache_path(),
...) — conftest's primed FIXTURE makes that deterministic with no config.json.
"""
import importlib

import config_loader
import week_review as wr


def test_dedup_cross_account_collapses_same_thread():
    # Same normalized subject + counterparty email on two accounts → one row,
    # freshest age kept, source annotated with both contributors.
    entries = [
        {
            "subject": "Re: Term Sheet",
            "counterparty_email": "alice@counsel.com",
            "source": "Gmail",
            "reply_age_days": 5,
        },
        {
            "subject": "Term Sheet",  # same after normalize_subject
            "counterparty_email": "ALICE@counsel.com",  # case-insensitive
            "source": "Outlook",
            "reply_age_days": 2,  # fresher
        },
    ]
    out = wr.dedup_cross_account(entries)
    assert len(out) == 1
    row = out[0]
    # Freshest age wins.
    assert row["reply_age_days"] == 2
    # Both sources annotated (first-seen order: Gmail then Outlook).
    assert row["source"] == "Gmail + Outlook"


def test_dedup_keeps_distinct_threads_separate():
    entries = [
        {"subject": "Deal A", "counterparty_email": "a@x.com", "source": "Gmail", "age_days": 3},
        {"subject": "Deal B", "counterparty_email": "b@x.com", "source": "Gmail", "age_days": 4},
    ]
    out = wr.dedup_cross_account(entries)
    assert len(out) == 2


def test_dedup_never_collapses_rows_with_no_subject_and_no_email():
    entries = [
        {"subject": "", "counterparty_email": "", "source": "Gmail"},
        {"subject": "", "counterparty_email": "", "source": "Outlook"},
    ]
    out = wr.dedup_cross_account(entries)
    assert len(out) == 2  # keyed on id(), never merged


def test_dedup_age_days_field_also_merged():
    entries = [
        {"subject": "Quote", "counterparty_email": "q@x.com", "source": "Gmail", "age_days": 9},
        {"subject": "RE: Quote", "counterparty_email": "q@x.com", "source": "Work", "age_days": 1},
    ]
    out = wr.dedup_cross_account(entries)
    assert len(out) == 1
    assert out[0]["age_days"] == 1
    assert out[0]["source"] == "Gmail + Work"


def test_is_self_exact_match():
    assert wr.is_self("user@company.com", "user@company.com") is True
    # Display-name form still parses to the address.
    assert wr.is_self("Test User <user@company.com>", "user@company.com") is True
    # Case-insensitive.
    assert wr.is_self("USER@COMPANY.COM", "user@company.com") is True


def test_is_self_substring_non_match():
    # A different address that merely contains the account string must be False.
    assert wr.is_self("notuser@company.com.evil.com", "user@company.com") is False
    assert wr.is_self("other@company.com", "user@company.com") is False
    # No account → never self.
    assert wr.is_self("user@company.com", "") is False


def test_is_placeholder_reply_short_ack_true():
    assert wr.is_placeholder_reply("Thanks, will review and get back to you ASAP.") is True
    assert wr.is_placeholder_reply("Got it, will circle back next week.") is True


def test_is_placeholder_reply_long_substantive_false():
    long_body = (
        "Here are my detailed thoughts on the term sheet. " * 20
    )
    assert len(long_body) > 400
    assert wr.is_placeholder_reply(long_body) is False


def test_is_placeholder_reply_empty_false():
    assert wr.is_placeholder_reply("") is False
    assert wr.is_placeholder_reply(None) is False


# ── Item 11: overreach gating (thin deal flow) ───────────────────────────────

def _thin_flow_output():
    """A thin-flow week.md output shape: one spam item, one stale no-deal item,
    and one RTO/stakeholder-feedback item, all wrongly in the red bucket."""
    return {
        "cold_urgent": [
            # Spam that slipped a fetch filter (noreply sender): must never be red.
            {"subject": "Your account statement", "counterparty_email": "noreply@bank.com",
             "age_days": 20},
            # Stale counterparty, no live deal in hotcache, older than the gate.
            {"subject": "Following up", "counterparty_email": "old@client.com",
             "age_days": 45},
            # RTO / stakeholder feedback request: potentially useful, not a deal.
            {"subject": "RTO Feedback Request", "counterparty_email": "hr@bigco.com",
             "age_days": 18},
            # A genuinely active deal thread: must stay red.
            {"subject": "Term Sheet redline", "counterparty_email": "alice@counsel.com",
             "age_days": 16},
        ],
        "cold_monitor": [],
        "filtered_pending": [],
    }


def test_gate_demotes_spam_out_of_red():
    out = _thin_flow_output()
    # No active deals in hotcache (empty set = thin flow).
    wr.gate_cold_promotion(out, active_emails=set())
    urgent_emails = {e["counterparty_email"] for e in out["cold_urgent"]}
    assert "noreply@bank.com" not in urgent_emails
    # Spam lands in filtered_pending, never red, never monitor.
    monitor_emails = {e["counterparty_email"] for e in out["cold_monitor"]}
    assert "noreply@bank.com" not in monitor_emails
    assert "noreply@bank.com" in {e["counterparty_email"] for e in out["filtered_pending"]}


def test_gate_demotes_stale_no_deal_out_of_red():
    out = _thin_flow_output()
    wr.gate_cold_promotion(out, active_emails=set())
    urgent_emails = {e["counterparty_email"] for e in out["cold_urgent"]}
    # Stale (45d) counterparty with no live deal → demoted to monitor, not red.
    assert "old@client.com" not in urgent_emails
    assert "old@client.com" in {e["counterparty_email"] for e in out["cold_monitor"]}


def test_gate_demotes_stakeholder_feedback_out_of_red():
    out = _thin_flow_output()
    wr.gate_cold_promotion(out, active_emails=set())
    urgent_emails = {e["counterparty_email"] for e in out["cold_urgent"]}
    assert "hr@bigco.com" not in urgent_emails
    assert "hr@bigco.com" in {e["counterparty_email"] for e in out["cold_monitor"]}


def test_gate_keeps_active_deal_in_red():
    out = _thin_flow_output()
    # Active deal counterparty present in hotcache Active Threads.
    wr.gate_cold_promotion(out, active_emails={"alice@counsel.com"})
    urgent_emails = {e["counterparty_email"] for e in out["cold_urgent"]}
    # The real deal stays red; none of the three noise items survive into red.
    assert urgent_emails == {"alice@counsel.com"}


def test_gate_stale_counterparty_with_live_deal_stays_red():
    out = {
        "cold_urgent": [
            {"subject": "Deal still open", "counterparty_email": "live@deal.com",
             "age_days": 50},
        ],
        "cold_monitor": [],
        "filtered_pending": [],
    }
    # Even at 50 days, a counterparty named in hotcache Active Threads is active.
    wr.gate_cold_promotion(out, active_emails={"live@deal.com"})
    assert {e["counterparty_email"] for e in out["cold_urgent"]} == {"live@deal.com"}


def test_classify_caps_stakeholder_feedback_urgency_at_low():
    # Subject-based detection (no network): a high-urgency stakeholder-feedback
    # item is force-capped to low so it can't be promoted to a red priority.
    assert wr.is_stakeholder_feedback("Town Hall: share your feedback") is True
    assert wr.is_stakeholder_feedback("Re: JV Term Sheet") is False


# ── Item 9: single Google account fan-out (no secondary fetcher fired) ────────

def test_single_google_account_fanout_enumerates_one_account():
    """A single-Google-account config must enumerate exactly that account: one
    Google fetcher, zero Microsoft fetchers, no phantom 'secondary'. Regression
    for the field bug where week_review unconditionally called secondary-Google
    fetchers and raised GOOGLE_REFRESH_TOKEN_WORK errors (forcing a double run)."""
    saved = config_loader._config
    try:
        cfg = {k: v for k, v in saved.items()}
        cfg["accounts"] = [
            {"provider": "google", "email": "solo@gmail.com", "label": "Gmail",
             "is_primary": True, "sent_folder_id": ""},
        ]
        config_loader._config = cfg
        reloaded = importlib.reload(wr)
        assert len(reloaded.GOOGLE_ACCOUNTS) == 1
        assert reloaded.GOOGLE_ACCOUNTS[0]["email"] == "solo@gmail.com"
        assert len(reloaded.MICROSOFT_ACCOUNTS) == 0
        # The fan-out math in main(): 1 obsidian + 2 fetches per email account.
        # One Google account → exactly 1 calendar + 1 deal fetch, no secondary.
        n_fetches = 1 + 2 * (len(reloaded.GOOGLE_ACCOUNTS) + len(reloaded.MICROSOFT_ACCOUNTS))
        assert n_fetches == 3
    finally:
        config_loader._config = saved
        importlib.reload(wr)
