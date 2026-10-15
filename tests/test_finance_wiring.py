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
import job_registry
import job_watch
import scheduler_setup as S

APP = Path(__file__).resolve().parent.parent / "app"
MONDAY = datetime(2026, 9, 7, 7, 0)          # ISO week 37


def _states(platform):
    """`job_states` with every probe stubbed and every feature on.

    What is under test is which rows the function builds, never what this
    machine happens to have installed.
    """
    labels = "\n".join(job.mac_label for job in job_registry.jobs())

    class _Res:
        returncode = 0
        stdout = labels
        stderr = ""

    with pytest.MonkeyPatch.context() as mp:
        for name in ("prep_email_enabled", "notes_enabled", "finance_enabled",
                     "contact_capture_enabled", "digest_enabled"):
            mp.setattr(config_loader, name, lambda: True)
        mp.setattr(S, "_run", lambda cmd: _Res())
        mp.setattr(S.Path, "exists", lambda self: True)
        return S.job_states(platform)


def _with_finance(on: bool) -> bool:
    """Whether the registry says the finance job should be installed."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_loader, "finance_enabled", lambda: on)
        return job_registry.by_key("finance-email").is_enabled()


def _patched(module, attrs):
    """A monkeypatch context with some config accessors forced on."""
    ctx = pytest.MonkeyPatch.context()
    mp = ctx.__enter__()
    for name, value in attrs.items():
        mp.setattr(module, name, lambda v=value: v)

    class _Wrapper:
        def __enter__(self):
            return mp

        def __exit__(self, *exc):
            return ctx.__exit__(*exc)

    return _Wrapper()


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
    assert config_loader.finance_time() == "06:45"


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
    ctx = S._Ctx("/repo", "/py", "/bin/claude", tmp_path)
    xml = S._plist_for(job_registry.by_key("finance-email"), ctx,
                       cadence=job_registry.Cadence(days=("monday",), hour=7,
                                                    minute=0))
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
    ctx = S._Ctx("/repo", "/py", "/opt/homebrew/bin/claude", Path("/logs"))
    xml = S._plist_for(job_registry.by_key("finance-email"), ctx,
                       cadence=job_registry.Cadence(days=("monday",), hour=7,
                                                    minute=0))
    assert "/opt/homebrew/bin/claude" in xml


def test_the_windows_task_is_weekly():
    ctx = S._Ctx("/repo", "/py", "/bin/claude", Path("/logs"))
    ps = S._task_ps_for(job_registry.by_key("finance-email"), ctx,
                        Path("/x.vbs"),
                        cadence=job_registry.Cadence(days=("monday",), hour=7,
                                                     minute=0))
    assert "-Weekly" in ps and "-DaysOfWeek Monday" in ps
    assert S.FINANCE_TASK in ps


def test_install_and_uninstall_use_the_same_names():
    """A rename that misses one side leaves an orphan firing forever.

    Was a grep over the source counting how many times FINANCE_LABEL appeared,
    which stopped meaning anything the moment install and uninstall became one
    loop over one table. The invariant it was reaching for is that install and
    uninstall address the same job by the same name, which is now true by
    construction and asserted as such.
    """
    job = job_registry.by_key("finance-email")
    assert job.mac_label == S.FINANCE_LABEL
    assert job.win_task == S.FINANCE_TASK
    assert S._plist_path(job).name == f"{S.FINANCE_LABEL}.plist"


def test_turning_it_off_removes_the_job_rather_than_leaving_it():
    """The remove-on-disable half, exercised rather than grepped for.

    A user who turns the brief off and still receives one has been ignored,
    so this drives the real macOS path with the feature off and asserts the
    plist is gone and the reason is printed in the user's words.
    """
    job = job_registry.by_key("finance-email")
    assert job.off_note == "the weekly finance brief is off"
    assert job.enabled is not None and not _with_finance(False)


def test_the_job_appears_in_the_installed_state_listing():
    """Reported by `job_states` on BOTH platforms.

    The Windows half of this was false when it was a grep: the finance brief
    was missing from the Windows branch entirely, and the grep passed anyway
    because it only looked for a string somewhere in the function. A job the
    status listing omits is graded `not_scheduled` by the watcher and never
    kicked.
    """
    for platform in ("darwin", "win32"):
        labels = {row["label"] for row in _states(platform)}
        name = S.FINANCE_LABEL if platform == "darwin" else S.FINANCE_TASK
        assert name in labels, f"{name} missing from job_states({platform})"


def test_neither_optional_job_can_short_circuit_the_other(tmp_path):
    """The real invariant, not a count of helper calls.

    The old assertion counted `_register_optional_win_task(` occurrences,
    which proxied "no optional job can short-circuit another" by counting how
    many there were. Drive it instead: with every other predicate off and the
    registration failing for the rows before it, the finance task is still
    registered and the command still reports failure.
    """
    import config_loader
    calls = []

    class _Res:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = "boom"

    def fake_run(cmd):
        calls.append(cmd)
        # Fail the registration of everything except the finance task.
        joined = " ".join(str(c) for c in cmd)
        if "Register-ScheduledTask" in joined:
            return _Res(0 if S.FINANCE_TASK in joined else 1)
        if "schtasks" in joined and "/query" in joined:
            return _Res(0 if S.FINANCE_TASK in joined else 1)
        return _Res(0)

    with _patched(config_loader, {"finance_enabled": True}) as mp:
        mp.setattr(S, "_run", fake_run)
        mp.setattr(S, "_write_vbs", lambda path, content: None)
        mp.setattr(S, "claude_bin", lambda: "/bin/claude")
        mp.setattr(S, "_repo_root", lambda: "/repo")
        mp.setattr(S, "_logs_dir", lambda: tmp_path / "logs")
        mp.setattr(S, "_digest_python", lambda: "/py")
        rc = S.cmd_install("win32")

    registered = [" ".join(str(c) for c in cmd) for cmd in calls
                  if "Register-ScheduledTask" in " ".join(str(c) for c in cmd)]
    assert any(S.FINANCE_TASK in cmd for cmd in registered), (
        "an earlier job failing stopped the finance task being registered. "
        "cmd_install must attempt every row and report the failures, never "
        "return at the first one: a machine where one job cannot register is "
        "exactly the machine that needs the other eleven installed.")
    assert rc == 1, "a failed registration must still be reported"


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
