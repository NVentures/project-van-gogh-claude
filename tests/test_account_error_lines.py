"""What a briefing says when one account's sign-in has failed.

Three things have to hold at once, and each was broken in its own way:

  * the reader is told which account and what to do. A raw
    `RefreshError: ('invalid_grant: ...', {...})` tuple in the errors list
    left the model rendering the briefing to invent a cause and a remedy;
  * the exception's own text never reaches the page. Exception text quotes
    the arguments the call failed on, and a briefing is published;
  * the rewritten line still reads as an auth failure to anything that
    classifies it afterwards, or the rewrite would turn a job the watcher
    holds back into one it retries forever.
"""
import pytest

import afternoon_tea
import calendar_stub_check
import completion_scan
import failure_class
import voice_generator
import week_retro
import week_review
from failure_class import account_error_line


class RefreshError(Exception):
    """Shaped like google.auth.exceptions.RefreshError, which quotes its args."""


SECRET = "1//0g-REFRESH-TOKEN-SHAPED-STRING"
AUTH_EXC = RefreshError(
    f"('invalid_grant: Token has been expired or revoked.', "
    f"{{'error': 'invalid_grant', 'refresh_token': '{SECRET}'}})")


def test_an_auth_failure_names_the_account_and_the_one_command():
    line = account_error_line("Personal calendar", "Personal", AUTH_EXC)
    assert "Personal needs you to sign in again" in line
    assert "/van-gogh:add-account" in line
    assert "Retrying cannot fix this" in line
    assert "your other accounts are unaffected" in line


def test_the_exceptions_own_text_never_reaches_the_briefing():
    """MUTATION: return f"{prefix}: {exc}" for the auth case and this fails."""
    line = account_error_line("Personal calendar", "Personal", AUTH_EXC)
    assert SECRET not in line
    assert "Token has been expired or revoked" not in line
    # What survives is fixed vocabulary: the type and the OAuth code.
    assert "RefreshError, invalid_grant" in line


def test_the_rewritten_line_still_classifies_as_auth():
    """The watcher reads these lines back. A sentence a person can act on that
    no longer looks like auth would be retried every half hour."""
    line = account_error_line("Personal inbox", "Personal", AUTH_EXC)
    assert failure_class.classify(line) == "auth"
    assert "auth" in failure_class.NO_RETRY


def test_anything_that_is_not_auth_keeps_the_shape_it_always_had():
    line = account_error_line("Personal calendar", "Personal",
                              ValueError("bad calendar id"))
    assert line == "Personal calendar: bad calendar id"


def test_a_plain_message_works_too():
    """voice_generator hands back the message, not the exception."""
    line = account_error_line("Work", "Work", "invalid_grant: token revoked")
    assert "Work needs you to sign in again" in line
    assert "invalid_grant" in line


@pytest.mark.parametrize("module", [
    week_review, afternoon_tea, week_retro, calendar_stub_check,
    completion_scan, voice_generator,
])
def test_every_briefing_that_fetches_an_account_uses_the_helper(module):
    """A fetcher that still formats the exception raw is one page that leaks.

    MUTATION: revert any one of these call sites and its row fails.
    """
    assert getattr(module, "account_error_line", None) is account_error_line, (
        f"{module.__name__} fetches per account but does not import the helper")


def test_no_account_fetch_still_formats_its_exception_raw():
    """Reads the source, so a NEW fetcher cannot quietly reintroduce this."""
    import re
    from pathlib import Path
    app = Path(failure_class.__file__).resolve().parent
    # `label` in the text is the tell: these are the per-account sites.
    raw = re.compile(r"errors\"?\]?\.append\(f\"[^\"]*\{[^}]*label[^}]*\}"
                     r"[^\"]*\{(?:e|exc|err)\}")
    hits = [f"{p.name}:{i}" for p in sorted(app.glob("*.py"))
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if raw.search(line)]
    assert not hits, "per-account error sites still leaking exception text: " + str(hits)


def test_the_source_scan_can_find_a_real_instance():
    """Proving the scan above on the shape it hunts, since a scan that can
    never match reports a clean zero forever."""
    import re
    raw = re.compile(r"errors\"?\]?\.append\(f\"[^\"]*\{[^}]*label[^}]*\}"
                     r"[^\"]*\{(?:e|exc|err)\}")
    planted = [
        '            output["errors"].append(f"{a[\'label\']} calendar: {e}")',
        '                    errors.append(f"Sent mail ({label}): {e}")',
        '            errors.append(f"{gcal_label} calendar exception: {e}")',
    ]
    for line in planted:
        assert raw.search(line), line


# ── a missing scope costs one feature, never the mailbox ─────────────────────

@pytest.mark.parametrize("text", [
    "403 Request had insufficient authentication scopes.",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    "insufficientPermissions",
])
def test_a_permission_a_token_never_had_is_held_not_retried(text):
    """The other half of the promise in oauth_scopes.py. Adding a scope is
    safe for existing accounts because the feature that wants it fails alone,
    with its own sentence, and is never retried against a wall."""
    assert failure_class.classify(text) == "auth"
    assert "auth" in failure_class.NO_RETRY


def test_contact_capture_says_what_to_do_about_it():
    import contact_capture
    assert "Sign in again" in contact_capture.SCOPE_HELP
    assert "contacts" in contact_capture.SCOPE_HELP.lower()
