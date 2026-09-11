"""The finance brief's edges: config, the scheduled job, the watcher, the send.

Everything here guards a promise made somewhere else. The job must disappear
when the feature is turned off, the watcher must know it exists at all (a job
nobody grades is a job that can stop silently), and the send must be impossible
to duplicate and impossible to send stale.

The last one is specific to this feature. Its data comes from a third party
over a connector whose token expires, so the interesting failure is not a crash
but a week where nothing arrives and nothing says why.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from xml.dom.minidom import parseString

import pytest

import config_loader
import finance_brief
import finance_send
import job_watch
import scheduler_setup as S

APP = Path(__file__).resolve().parent.parent / "app"
MONDAY = datetime(2026, 9, 7, 7, 0)          # ISO week 37


# ── Config ───────────────────────────────────────────────────────────────────

def test_the_template_and_the_code_agree_on_every_default():
    template = json.loads(
        (APP.parent / "config.template.json").read_text(encoding="utf-8"))
    assert template["finance"] == config_loader.FINANCE_DEFAULTS


def test_the_feature_ships_off():
    """It reads the user's books. It starts when they ask, not before."""
    assert config_loader.FINANCE_DEFAULTS["enabled"] is False


def test_a_junk_day_or_time_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(config_loader, "_finance",
                        lambda: {"day": "someday", "time": "99:99"})
    assert config_loader.finance_day() == "monday"
    assert config_loader.finance_time() == "07:00"


def test_the_recipient_falls_back_to_wherever_briefings_go(monkeypatch):
    monkeypatch.setattr(config_loader, "_finance", lambda: {"recipient_email": ""})
    monkeypatch.setattr(config_loader, "digest_recipient_email",
                        lambda: "someone@example.com")
    assert config_loader.finance_recipient_email() == "someone@example.com"


def test_its_own_recipient_wins_when_set(monkeypatch):
    """The books are not the briefings: a user who points daily mail at an
    assistant may well not want the cash position going there."""
    monkeypatch.setattr(config_loader, "_finance",
                        lambda: {"recipient_email": "owner@example.com"})
    monkeypatch.setattr(config_loader, "digest_recipient_email",
                        lambda: "assistant@example.com")
    assert config_loader.finance_recipient_email() == "owner@example.com"


# ── The scheduled job ────────────────────────────────────────────────────────

def test_the_plist_is_weekly_and_names_the_right_script(tmp_path):
    xml = S._finance_plist_content("/repo", "/py", "/bin/claude", tmp_path,
                                   "monday", 7, 0)
    parseString(xml)                                  # must be valid XML

    # launchd counts weekdays from Sunday (Monday is 1); Python counts from
    # Monday (Monday is 0). Both conventions appear in this feature, so the
    # shared table is asserted rather than a literal.
    assert (f"<key>Weekday</key><integer>{S.LAUNCHD_WEEKDAY['monday']}</integer>"
            in xml)
    assert "<key>Hour</key><integer>7</integer>" in xml
    assert "finance_send.py" in xml
    assert S.FINANCE_LABEL in xml
    assert "StartInterval" not in xml, "this is a calendar job, not a ticker"


def test_the_job_carries_the_resolved_claude_path():
    """Unlike the scorecard, this one reaches a model: launchd hands a job a
    PATH too sparse to find the binary at fire time."""
    xml = S._finance_plist_content("/repo", "/py", "/opt/homebrew/bin/claude",
                                   Path("/logs"), "monday", 7, 0)
    assert "/opt/homebrew/bin/claude" in xml


def test_the_windows_task_is_weekly():
    ps = S._finance_task_ps("/repo", Path("/x.vbs"), "monday", 7, 0)
    assert "-Weekly" in ps and "-DaysOfWeek Monday" in ps
    assert S.FINANCE_TASK in ps


def test_install_and_uninstall_use_the_same_names():
    """A rename that misses one side leaves an orphan firing forever."""
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert src.count("FINANCE_LABEL") >= 4
    assert src.count("FINANCE_TASK") >= 3


def test_turning_it_off_removes_the_job_rather_than_leaving_it():
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert "the weekly finance brief is off" in src


def test_uninstall_removes_the_plist_on_macos():
    """Every macOS job is removed by hand here; a missed one fires forever."""
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    uninstall = src[src.index("def cmd_uninstall("):]
    assert "_finance_launch_agent_path()" in uninstall


def test_the_job_appears_in_the_installed_state_listing():
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    states = src[src.index("def job_states("):]
    assert "FINANCE_LABEL" in states


def test_neither_optional_job_can_short_circuit_the_other():
    """An early return once meant disabling prep skipped every later job."""
    src = (APP / "scheduler_setup.py").read_text(encoding="utf-8")
    assert src.count("_register_optional_win_task(") >= 4


# ── The watcher ──────────────────────────────────────────────────────────────

def test_the_watcher_ignores_the_job_while_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(config_loader, "finance_enabled", lambda: False)
    assert not any(j["name"] == "finance-email" for j in job_watch.roster())


def test_the_watcher_grades_the_job_when_it_is_on(monkeypatch):
    monkeypatch.setattr(config_loader, "finance_enabled", lambda: True)
    monkeypatch.setattr(config_loader, "finance_day", lambda: "monday")
    monkeypatch.setattr(config_loader, "finance_time", lambda: "07:00")
    rows = [j for j in job_watch.roster() if j["name"] == "finance-email"]

    assert len(rows) == 1
    job = rows[0]
    assert job["days"] == {0}, "Monday is 0 in Python's convention"
    assert job["hour"] == 7 and job["minute"] == 0
    assert job["ledger_job"] == finance_send.JOB_NAME
    assert str(job["deliverable"]).endswith("latest.html")


def test_the_deliverable_is_written_after_the_send_not_before():
    """Grading by a file written first would call a failed send delivered."""
    src = (APP / "finance_send.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    assert body.index("_write_artifacts(") > body.index("_deliver(account,")


# ── The send: claim, stamp, and never twice ──────────────────────────────────

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "logs_dir", lambda: tmp_path)
    return tmp_path


def test_a_week_is_claimed_before_the_send_and_stamped_after(ledger):
    assert finance_send.already_handled(MONDAY) is False
    finance_send.claim(MONDAY, "2026-09-07")
    assert finance_send.already_handled(MONDAY) is True

    data = json.loads((ledger / finance_send.LEDGER_NAME).read_text())
    row = data["weeks"]["2026-W37"]
    assert row["claimed_at"] and "sent_at" not in row

    finance_send.stamp_sent(MONDAY, ["owner@example.com"])
    data = json.loads((ledger / finance_send.LEDGER_NAME).read_text())
    assert data["weeks"]["2026-W37"]["sent_at"]


def test_a_claim_with_no_stamp_still_counts_as_handled(ledger):
    """A crash between the two may have put mail in someone's inbox. One
    missing brief is much cheaper than two contradictory ones."""
    finance_send.claim(MONDAY, "2026-09-07")
    assert finance_send.already_handled(MONDAY) is True


def test_the_ledger_records_addresses_and_never_figures(ledger):
    finance_send.claim(MONDAY, "2026-09-07")
    finance_send.stamp_sent(MONDAY, ["owner@example.com"])
    text = (ledger / finance_send.LEDGER_NAME).read_text()

    assert "owner@example.com" in text
    for figure in ("412908", "cash", "overdue"):
        assert figure not in text.lower()


def test_a_different_week_is_not_handled(ledger):
    finance_send.claim(MONDAY, "2026-09-07")
    assert finance_send.already_handled(datetime(2026, 9, 14, 7, 0)) is False


def test_the_claim_comes_after_the_numbers_not_before():
    """Claiming first would spend the week on a fetch that produced no page,
    and the reader would get nothing and no explanation."""
    src = (APP / "finance_send.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    assert body.index("claim(now,") > body.index("finance_brief.build(")


# ── Staleness ────────────────────────────────────────────────────────────────

def test_figures_older_than_the_window_are_not_sent_under_todays_date():
    """A watcher re-run days later would otherwise mail Monday's cash position
    on Thursday under a subject line that reads as today's."""
    report = {"as_of": "2026-09-01"}
    assert finance_send.is_stale(report, datetime(2026, 9, 7, 7, 0)) is True


def test_todays_figures_are_not_stale():
    report = {"as_of": "2026-09-07"}
    assert finance_send.is_stale(report, datetime(2026, 9, 7, 7, 0)) is False


def test_a_day_late_is_still_sent():
    report = {"as_of": "2026-09-06"}
    assert finance_send.is_stale(report, datetime(2026, 9, 7, 7, 0)) is False


def test_an_unreadable_date_is_not_treated_as_stale():
    """Refusing to send on a parse failure would hide a working brief."""
    assert finance_send.is_stale({"as_of": ""}, MONDAY) is False


def test_the_subject_carries_the_as_of_date():
    subject = finance_send.subject_for(
        {"company": {"name": "Acme"}, "as_of": "2026-09-07"})
    assert "2026-09-07" in subject and "Acme" in subject


# ── The reconnect reminder ───────────────────────────────────────────────────

def test_the_first_reminder_is_due(ledger):
    assert finance_send.reminder_due(MONDAY) is True


def test_a_second_reminder_waits_a_week(ledger):
    """An unreconnected connector must not become a weekly nag."""
    finance_send.stamp_reminder(MONDAY)
    assert finance_send.reminder_due(MONDAY) is False
    assert finance_send.reminder_due(datetime(2026, 9, 10, 7, 0)) is False
    assert finance_send.reminder_due(datetime(2026, 9, 14, 7, 0)) is True


def test_the_reminder_says_what_to_do_and_names_where(ledger):
    text = finance_send.REMINDER_TEXT
    assert "claude.ai" in text and "Connectors" in text
    assert "QuickBooks" in text
    assert "nothing in them was changed" in text.lower()


def test_the_reminder_carries_no_dash():
    em, en = "\u2014", "\u2013"   # escapes: a dash sweep over this file must not redefine what it hunts
    for text in (finance_send.REMINDER_TEXT, finance_send.REMINDER_SUBJECT):
        assert em not in text and en not in text


def test_a_reminder_without_a_sender_is_simply_not_sent(ledger, monkeypatch):
    monkeypatch.setattr(config_loader, "digest_sender_account", lambda: None)
    assert finance_send.send_reminder(MONDAY, "expired") is False


def test_a_raising_reminder_never_costs_the_run(ledger, monkeypatch):
    """The notice is worth less than the run it is reporting on."""
    monkeypatch.setattr(config_loader, "digest_sender_account",
                        lambda: {"label": "x", "email": "a@b.c"})
    monkeypatch.setattr(config_loader, "finance_recipient_email",
                        lambda: "owner@example.com")

    def boom(*_a, **_k):
        raise RuntimeError("smtp is down")

    import send_email

    monkeypatch.setattr(send_email, "send_email", boom)
    assert finance_send.send_reminder(MONDAY, "expired") is False


# ── No model call outside the fetch ──────────────────────────────────────────

def test_the_render_and_send_path_calls_no_model():
    """Only connector_fetch spawns the CLI. A second spawner would inherit the
    whole failure taxonomy again, in a module that has no need of it."""
    for name in ("finance_html.py", "finance_send.py"):
        src = (APP / name).read_text(encoding="utf-8")
        for banned in ("claude_cli", "run_claude", "anthropic", "claude_bin"):
            assert banned not in src, f"{name} reaches for a model via {banned}"


def test_the_scan_actually_read_the_files():
    """A scan over an empty set passes trivially."""
    for name in ("finance_html.py", "finance_send.py", "finance_brief.py"):
        assert len((APP / name).read_text(encoding="utf-8")) > 500


# ── The email ────────────────────────────────────────────────────────────────

def _report():
    from test_finance_brief import TODAY, raw

    return finance_brief.build(raw(), today=TODAY, history=[
        {"date": "2026-09-03", "kind": "weekly",
         "values": {"cash": 400000.0, "ar_overdue": 5000.0,
                    "ap_overdue": 100.0, "net_income": 10000.0}}])


def test_the_email_carries_no_amber():
    """Amber means work waiting on the reader. A record of a position has
    nothing for it to mean."""
    import finance_html

    html = finance_html.render(_report())
    for amber in ("#E8A33D", "#F5A623", "amber", "orange"):
        assert amber.lower() not in html.lower()


def test_every_colour_comes_from_the_closed_palette():
    import re

    import finance_html

    html = finance_html.render(_report())
    allowed = {c.lower() for c in finance_html.PALETTE.values()}
    for colour in set(re.findall(r"#[0-9A-Fa-f]{6}", html)):
        assert colour.lower() in allowed, colour


def test_the_email_never_shows_a_figure_without_its_direction():
    import finance_html

    html = finance_html.render(_report())
    assert "the good way" in html or "the wrong way" in html
    assert "no comparison yet" not in html, "this fixture has a prior week"


def test_an_unmeasured_section_shows_its_reason_not_a_zero():
    from test_finance_brief import TODAY, raw

    import finance_html

    report = finance_brief.build(raw(qbo_accounting_get_balance_sheet=None),
                                 today=TODAY, history=[])
    html = finance_html.render(report)
    assert "not measured" in html
    assert "did not come back" in html


def test_the_email_carries_no_dash():
    import finance_html

    em, en = "\u2014", "\u2013"   # escapes: a dash sweep over this file must not redefine what it hunts
    html = finance_html.render(_report())
    assert em not in html and en not in html


def test_the_email_says_nothing_was_changed():
    """A page that reads a person's books should say that is all it did."""
    import finance_html

    assert "only reads them" in finance_html.render(_report())


def test_a_send_that_never_left_gives_the_week_back(ledger):
    """Observed live: a credential missing at the moment of sending claimed
    the week, left no mail anywhere, and would have cost the reader a whole
    week for a fault that may be gone in an hour."""
    finance_send.claim(MONDAY, "2026-09-07")
    assert finance_send.already_handled(MONDAY) is True

    finance_send.release(MONDAY)
    assert finance_send.already_handled(MONDAY) is False


def test_a_week_that_was_actually_sent_is_never_released(ledger):
    """The whole point of the claim: an ambiguous crash must not send twice."""
    finance_send.claim(MONDAY, "2026-09-07")
    finance_send.stamp_sent(MONDAY, ["owner@example.com"])

    finance_send.release(MONDAY)
    assert finance_send.already_handled(MONDAY) is True, \
        "a sent week must keep its claim"


def test_the_release_is_wired_into_the_send_path():
    """A helper nothing calls is not a fix."""
    src = (APP / "finance_send.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    assert "release(now)" in body
    assert body.index("release(now)") > body.index("claim(now,")
