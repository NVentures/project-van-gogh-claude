"""The failure classifier: does it name the right wall?

Every pattern here is checked against the text a real failing run produced,
and against the near misses that would make it useless. A classifier that
calls a dropped connection a usage cap holds a job for an hour for nothing;
one that calls a usage cap a network blip burns the retry budget inside the
lockout. Both are silent, so both get a test.
"""

from datetime import datetime

import pytest

import failure_class as fc


# ── The captured signatures ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # quota: the real subscription cap message, and its variants
    ("You're out of extra usage, resets 3:40pm (America/Los_Angeles)", "quota"),
    ("Claude usage limit reached. Your limit will reset at 3pm", "quota"),
    ("weekly limit reached", "quota"),
    ("You have reached your weekly limit", "quota"),
    ("429 rate_limit_error", "quota"),
    # auth
    ("Not logged in. Please run /login", "auth"),
    ("google.auth.exceptions.RefreshError: invalid_grant", "auth"),
    ("OAuth access token has expired", "auth"),
    # version: the exact 400 the API returns for a stale CLI
    ("API Error: 400 Claude Code 2.1.139 does not support this model; "
     "version 2.1.251 or newer is required. Run 'claude update'", "version"),
    # classifier
    ("temporarily unavailable, auto mode cannot determine safety of Bash",
     "classifier"),
    ("safety-classifier outage", "classifier"),
    # network
    ("nodename nor servname provided, or not known", "network"),
    ("Operation timed out", "network"),
    ("HTTPSConnectionPool: Max retries exceeded", "network"),
])
def test_the_real_signatures_get_the_right_class(text, expected):
    assert fc.classify(text) == expected


def test_nothing_seen_is_not_the_same_as_nothing_recognised():
    """Empty is "", not "unknown".

    A run that failed with no output has told us nothing; a run that failed
    with output we cannot parse has told us something we could not read. The
    caller treats those differently, so they cannot share a value."""
    assert fc.classify("") == ""
    assert fc.classify(None) == ""
    assert fc.classify("Traceback: KeyError: 'meetings'") == "unknown"


# ── The near misses ──────────────────────────────────────────────────────────

def test_a_dropped_connection_is_not_a_usage_cap():
    """"Connection reset by peer" carries the word "reset", which the quota
    pattern also looks for. The quota form requires a clock time after it."""
    assert fc.classify("ConnectionResetError: Connection reset by peer") == "network"


def test_a_stale_cli_beats_a_reset_time():
    """The stale-CLI message names version numbers, and a version number can
    look like a time to a loose pattern. Version is tested first for exactly
    this reason, so the job holds for an update rather than for a clock."""
    text = ("Claude Code 2.1.139 does not support this model; version 2.1.251 "
            "or newer is required")
    assert fc.classify(text) == "version"


def test_a_cap_beats_a_login_mention():
    """A message can name both. Only one of them is something to wait out."""
    assert fc.classify("usage limit reached; you may need to /login") == "quota"


# ── Reading the reset time ───────────────────────────────────────────────────

def test_it_reads_the_reset_time_the_message_names():
    now = datetime(2026, 9, 9, 11, 40)
    assert fc.quota_reset_at("resets 3:40pm", now=now) == datetime(2026, 9, 9, 15, 40)


def test_a_reset_already_past_today_means_tomorrow():
    """A cap that "resets at 3pm", read at 4pm, cannot mean an hour ago.
    Holding until a moment in the past is the same as not holding."""
    now = datetime(2026, 9, 9, 16, 0)
    assert fc.quota_reset_at("resets at 3pm", now=now) == datetime(2026, 9, 10, 15, 0)


def test_midnight_and_noon_are_not_confused():
    now = datetime(2026, 9, 9, 6, 0)
    assert fc.quota_reset_at("resets 12:30am", now=now).hour == 0
    assert fc.quota_reset_at("resets 12:30pm", now=now).hour == 12


def test_no_time_named_means_no_time_returned():
    """The caller picks its own hold when the message names none. Guessing a
    time here would produce a confident wrong sentence in the briefing."""
    assert fc.quota_reset_at("usage limit reached", now=datetime(2026, 9, 9)) is None
    assert fc.quota_reset_at("", now=datetime(2026, 9, 9)) is None


def test_an_impossible_clock_time_is_refused():
    assert fc.quota_reset_at("resets 99:99pm", now=datetime(2026, 9, 9)) is None


# ── Versions ─────────────────────────────────────────────────────────────────

def test_it_reads_the_version_the_error_demands():
    text = "version 2.1.251 or newer is required"
    assert fc.required_cli_version(text) == "2.1.251"
    assert fc.required_cli_version("something else") == ""


def test_versions_compare_as_numbers_not_as_text():
    """"2.1.9" sorts after "2.1.251" as a string and before it as a version.
    A job held on the string comparison would release exactly when it must
    not: on a CLI that is still too old."""
    assert fc.version_tuple("2.1.9") < fc.version_tuple("2.1.251")
    assert fc.version_tuple("2.1.251") == (2, 1, 251)
    assert fc.version_tuple("") == ()
    assert fc.version_tuple("not-a-version") == ()


def test_a_version_with_a_suffix_stops_at_the_first_non_number():
    assert fc.version_tuple("2.1.251-beta") == (2, 1)


# ── The contract other modules depend on ─────────────────────────────────────

def test_every_class_has_a_remedy_a_person_can_read():
    """The remedy is shown to the user, so a class with no remedy would print
    a blank line where an explanation belongs."""
    for name in ("auth", "quota", "version", "classifier", "network",
                 "connector", "unknown"):
        assert fc.REMEDY[name].strip()


def test_only_the_hopeless_classes_stop_a_retry():
    """Network and classifier failures still retry: the first often works on
    the second attempt, and the second is deferred rather than abandoned.

    A connector joins the hopeless set for the same reason a login does: its
    token lives with Anthropic and only the user, in a browser, can renew it.
    """
    assert fc.NO_RETRY == frozenset({"quota", "auth", "connector"})


def test_the_judge_still_recognises_what_it_used_to():
    """priority_judge shares this pattern instead of keeping its own copy.

    Its old private pattern matched these; sharing must not silently narrow
    what the briefing calls "the usage limit was reached"."""
    for text in ("rate limit exceeded", "quota exceeded", "429",
                 "out of extra usage", "usage limit reached"):
        assert fc.QUOTA_RE.search(text), text


# ── The connector class ──────────────────────────────────────────────────────
#
# A Claude connector's token belongs to Anthropic, not to the user's own OAuth,
# and only a browser can renew it. So this class exists to say a different
# sentence than `auth` does: reconnect QuickBooks, not sign in to Claude. The
# near misses below are what keep it from swallowing an ordinary Google 401.

@pytest.mark.parametrize("text", [
    # The token connector_fetch stamps on every unavailable verdict.
    "CONNECTOR-UNAVAILABLE: claude_ai_Intuit_QuickBooks requires "
    "re-authorization (the stored token has expired).",
    # The CLI's own words, captured from a real expired connector 2026-09-10.
    'MCP server "claude.ai Intuit QuickBooks" requires re-authorization '
    '(token expired)',
    "CONNECTOR-UNAVAILABLE: some_server is not connected to this machine at all.",
])
def test_an_unauthorized_connector_is_its_own_class(text):
    assert fc.classify(text) == "connector"


@pytest.mark.parametrize("text, expected", [
    # The user's own Google token: a different fix, so a different class.
    ("google.auth.exceptions.RefreshError: invalid_grant", "auth"),
    ("401 Unauthorized", "auth"),
    ("Not logged in. Please run /login", "auth"),
    ("OAuth access token has expired", "auth"),
    # A cap is still a clock.
    ("usage limit exceeded", "quota"),
    ("You're out of extra usage, resets 3:40pm (America/Los_Angeles)", "quota"),
    # A dropped connection is still worth retrying.
    ("Connection reset by peer", "network"),
    # The connector answered and ITS OWN call failed. Retrying may well work,
    # so this must not be the no-retry class.
    ("CONNECTOR-ERROR: every call failed. company_info: Internal server error",
     "unknown"),
])
def test_the_near_misses_keep_their_own_class(text, expected):
    assert fc.classify(text) == expected


def test_a_connector_failure_is_never_retried():
    """Anthropic holds that token; a second attempt cannot renew it."""
    assert "connector" in fc.NO_RETRY


def test_the_connector_remedy_names_the_act_that_fixes_it():
    remedy = fc.REMEDY["connector"]
    assert "reconnect" in remedy
    assert "connector settings" in remedy


def test_connector_is_tested_before_auth():
    """A message can carry both, and only one of them names the real fix."""
    both = ('MCP server "claude.ai Intuit QuickBooks" requires '
            're-authorization; the OAuth access token has expired')
    assert fc.classify(both) == "connector"
