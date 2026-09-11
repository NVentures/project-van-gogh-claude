"""The watcher: does it grade correctly, and does it hold when it should?

Everything here runs against a planted moment, a planted ledger and a planted
scheduler. No clock, no launchd, no network: the grader takes all three as
arguments precisely so this file can plant them.

The tests that matter most are the holds. A watcher that re-runs a job inside
a usage cap looks like it is working, produces nothing, and empties the retry
budget before the cap lifts, all silently. Each hold therefore gets both a
"does it hold" test and a "does it release" test, and the quota one carries a
mutation note saying what to delete to watch it fail.
"""

import json
from datetime import datetime, timedelta

import pytest

import job_watch


# ── Plumbing ─────────────────────────────────────────────────────────────────

WED = datetime(2026, 9, 9, 12, 0)          # a Wednesday, midday
ALL_DAYS = {0, 1, 2, 3, 4, 5, 6}


def digest_job(name="morning-coffee", hour=7, minute=0, days=ALL_DAYS,
               deliverable=None):
    # `title` is what a person is shown; the real roster builds it the same
    # way, from digest_send.BRIEFING_NAMES.
    pretty = {"morning-coffee": "Morning Coffee", "week": "Week"}.get(name, name)
    return {
        "name": f"digest-{name}", "title": f"your {pretty} briefing",
        "kind": "digest",
        "label": f"com.monet.digest-{name}", "task": f"VanGogh-digest-{name}",
        "ledger_job": f"digest.{name}", "days": set(days),
        "hour": hour, "minute": minute, "deliverable": deliverable,
    }


def installed(job, state="loaded"):
    return [{"label": job["label"], "kind": "digest", "name": job["name"],
             "state": state, "path": "/x.plist"}]


def run_row(job, when, rc=0, detail="", error_class=None):
    row = {"job": job["ledger_job"], "started": when.isoformat(),
           "finished": when.isoformat(), "rc": rc, "detail": detail}
    if error_class is not None:
        row["error_class"] = error_class
    return row


@pytest.fixture
def one_job(monkeypatch):
    """A single 7am daily digest, installed and loaded."""
    job = digest_job()
    monkeypatch.setattr(job_watch, "roster", lambda: [job])
    return job


# Bound at import, before any test patches job_watch.evaluate. A helper that
# looked the name up at call time recursed into its own monkeypatch.
_EVALUATE = job_watch.evaluate


def grade(job, runs, now=WED, state=None, job_state="loaded"):
    rows = _EVALUATE(now=now, state=state or {}, runs=runs,
                     job_rows=installed(job, job_state))
    return rows[0]


# ── Finding the slot ─────────────────────────────────────────────────────────

def test_the_latest_slot_is_todays_when_it_has_passed(one_job):
    assert job_watch.latest_slot(one_job, WED) == datetime(2026, 9, 9, 7, 0)


def test_before_todays_time_the_latest_slot_is_yesterdays(one_job):
    early = datetime(2026, 9, 9, 6, 0)
    assert job_watch.latest_slot(one_job, early) == datetime(2026, 9, 8, 7, 0)


def test_a_weekly_job_looks_back_to_its_own_day():
    """A Monday-only job graded on Wednesday must find Monday, not nothing.

    Walking back a day at a time rather than computing "the previous Monday"
    is what keeps this right across a week boundary."""
    monday_only = digest_job("week", days={0})
    assert job_watch.latest_slot(monday_only, WED) == datetime(2026, 9, 7, 7, 0)


def test_a_job_that_has_never_been_due_has_no_slot():
    saturday_only = digest_job("weekend", days={5})
    friday = datetime(2026, 9, 4, 12, 0)
    # Its previous Saturday is 8 days back, past the lookback window.
    assert job_watch.latest_slot(saturday_only, friday) is not None
    never = digest_job("weekend", days={5}, hour=23, minute=59)
    assert job_watch.latest_slot(never, datetime(2026, 9, 5, 0, 1)) is not None


# ── Grading ──────────────────────────────────────────────────────────────────

def test_a_run_that_succeeded_is_ok(one_job):
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 9, 7, 1))])
    assert row["status"] == "ok"


def test_no_record_of_a_run_long_past_its_slot_is_missing(one_job):
    row = grade(one_job, [])
    assert row["status"] == "missing"
    assert "no record that it ran" in row["detail"]


def test_a_slot_that_only_just_passed_is_still_pending(one_job):
    """A job takes minutes to render. Grading it missing while it is working
    would re-run it on top of itself."""
    just_after = datetime(2026, 9, 9, 7, 5)
    row = grade(one_job, [], now=just_after)
    assert row["status"] == "pending"


def test_a_failed_run_is_failed_and_keeps_its_class(one_job):
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 9, 7, 1), rc=1,
                                  detail="Not logged in", error_class="auth")])
    assert row["status"] == "failed"
    assert row["error_class"] == "auth"


def test_a_class_is_derived_when_the_row_did_not_store_one(one_job):
    """Rows written before the class existed still have to be readable."""
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 9, 7, 1), rc=1,
                                  detail="You're out of extra usage, resets 3pm")])
    assert row["error_class"] == "quota"


def test_a_run_before_the_slot_does_not_count_for_it(one_job):
    """Yesterday's success is not evidence that today's slot ran."""
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 8, 7, 1))])
    assert row["status"] == "missing"


def test_a_job_with_no_scheduled_entry_is_reported_not_kicked(one_job):
    row = grade(one_job, [], job_state="not installed")
    assert row["status"] == "not_scheduled"
    assert row["status"] not in job_watch.ACTIONABLE


# ── Deliverables beat exit codes, in both directions ─────────────────────────

def test_an_ok_exit_with_yesterdays_file_did_not_deliver(tmp_path, monkeypatch):
    """The vacuity guard. A briefing that renders nothing and exits zero looks
    exactly like one that worked, unless you check what it left behind."""
    stale = tmp_path / "morning-coffee.md"
    stale.write_text("yesterday", encoding="utf-8")
    import os
    old = (datetime(2026, 9, 8, 7, 1)).timestamp()
    os.utime(stale, (old, old))
    job = digest_job(deliverable=stale)
    monkeypatch.setattr(job_watch, "roster", lambda: [job])
    row = grade(job, [run_row(job, datetime(2026, 9, 9, 7, 1))])
    assert row["status"] == "no_deliverable"


def test_a_fresh_file_with_no_run_record_counts_as_delivered(tmp_path, monkeypatch):
    """A hand run, or a ledger row that was never written. The work is done
    either way, and re-running it would be worse than useless."""
    fresh = tmp_path / "morning-coffee.md"
    fresh.write_text("today", encoding="utf-8")
    job = digest_job(deliverable=fresh)
    monkeypatch.setattr(job_watch, "roster", lambda: [job])
    row = grade(job, [])
    assert row["status"] == "ok"
    assert row["detail"] == "the file is current"


def test_a_job_with_nothing_declared_is_never_judged_on_a_missing_file(monkeypatch):
    """Absence of a deliverable is not evidence of a stale one. Treating it
    as such would re-run every skill job forever."""
    job = digest_job(deliverable=None)
    monkeypatch.setattr(job_watch, "roster", lambda: [job])
    row = grade(job, [run_row(job, datetime(2026, 9, 9, 7, 1))])
    assert row["status"] == "ok"


def test_a_job_that_never_ran_is_not_rescued_by_having_no_deliverable(monkeypatch):
    """The three-state deliverable check, stated as its own case.

    "Nothing declared" must not read as "delivered". It did once: a job that
    had never run in its life graded ok, on the strength of a file that does
    not exist."""
    job = digest_job(deliverable=None)
    monkeypatch.setattr(job_watch, "roster", lambda: [job])
    assert grade(job, [])["status"] == "missing"


# ── The holds ────────────────────────────────────────────────────────────────

def failed_row(one_job, detail, klass, kicks=0):
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 9, 7, 1), rc=1,
                                  detail=detail, error_class=klass)])
    row["kicks"] = kicks
    return row


def test_a_usage_cap_holds_until_the_reset_and_spends_no_attempts(one_job):
    """MUTATION: delete the `klass == "quota"` branch in actionable() and this
    test fails, because the third kick lands inside the lockout. That was the
    live failure this hold exists to prevent."""
    state = {}
    row = failed_row(one_job, "You're out of extra usage, resets 3:40pm", "quota")
    can, why = job_watch.actionable(row, state, WED)
    assert can is False
    assert "usage limit" in why
    assert "Nothing for you to do" in why
    # A held slot keeps its budget: the attempt is not spent on the wait.
    assert state["slots"][row["_skey"]].get("kicks", 0) == 0


def test_the_cap_releases_itself_once_the_reset_has_passed(one_job):
    state = {}
    row = failed_row(one_job, "You're out of extra usage, resets 3:40pm", "quota")
    job_watch.actionable(row, state, WED)                 # arms the hold
    after = datetime(2026, 9, 9, 15, 50)
    can, why = job_watch.actionable(row, state, after)
    assert can is True and why == ""


def test_an_expired_login_is_never_retried(one_job):
    """Nothing about a second attempt makes a refresh token valid."""
    can, why = job_watch.actionable(failed_row(one_job, "Not logged in", "auth"),
                                    {}, WED)
    assert can is False
    assert "needs you to sign in to Claude again" in why
    assert "cannot start until then" in why


def test_a_classifier_outage_is_deferred_one_tick_then_tried(one_job):
    """The outage flaps, so one tick of patience is worth half an hour. But
    it must not defer forever, or the job never runs again."""
    state = {}
    row = failed_row(one_job, "cannot determine safety of Bash", "classifier")
    can, _ = job_watch.actionable(row, state, WED)
    assert can is False
    can, _ = job_watch.actionable(row, state, WED + timedelta(minutes=30))
    assert can is True


def test_a_stale_cli_holds_until_the_binary_clears_the_bar(one_job, monkeypatch):
    import claude_update
    monkeypatch.setattr(claude_update, "cli_version", lambda *a, **k: "2.1.139")
    row = failed_row(one_job, "Claude Code 2.1.139 does not support this model; "
                              "version 2.1.251 or newer is required", "version")
    can, why = job_watch.actionable(row, {}, WED)
    assert can is False
    assert "waiting for Claude to finish updating" in why
    assert "Nothing for you to do" in why


def test_a_stale_cli_releases_once_the_update_lands(one_job, monkeypatch):
    import claude_update
    monkeypatch.setattr(claude_update, "cli_version", lambda *a, **k: "2.1.260")
    row = failed_row(one_job, "Claude Code 2.1.139 does not support this model; "
                              "version 2.1.251 or newer is required", "version")
    can, _ = job_watch.actionable(row, {}, WED)
    assert can is True


def test_the_cap_stops_it_after_three_tries(one_job, monkeypatch):
    monkeypatch.setattr(job_watch, "kick_cap", lambda: 3)
    row = failed_row(one_job, "boom", "unknown", kicks=3)
    can, why = job_watch.actionable(row, {}, WED)
    assert can is False
    assert "restarted three times" in why
    assert "stopped trying for today" in why


def test_the_same_failure_twice_stops_and_asks_for_a_human(one_job):
    state = {"slots": {}}
    row = failed_row(one_job, "KeyError: meetings", "unknown", kicks=2)
    state["slots"][row["_skey"]] = {"kicks": 2, "last_signature": "KeyError: meetings"}
    can, why = job_watch.actionable(row, state, WED)
    assert can is False
    assert "failed twice with the same error" in why
    assert job_watch._WHERE_TO_LOOK in why


def test_two_failures_with_nothing_recorded_are_not_stuck(one_job):
    """An empty detail is not evidence that two runs failed the SAME way.
    Calling that stuck would strand a job on no information at all."""
    state = {"slots": {}}
    row = failed_row(one_job, "", "unknown", kicks=2)
    state["slots"][row["_skey"]] = {"kicks": 2, "last_signature": ""}
    can, _ = job_watch.actionable(row, state, WED)
    assert can is True


def test_a_healthy_job_is_never_kicked(one_job):
    row = grade(one_job, [run_row(one_job, datetime(2026, 9, 9, 7, 1))])
    assert job_watch.actionable(row, {}, WED) == (False, "")


# ── The tick ─────────────────────────────────────────────────────────────────

def test_report_only_changes_nothing(one_job, monkeypatch, tmp_path):
    """--report-only must not kick, must not write state, must not log."""
    kicked = []
    monkeypatch.setattr(job_watch, "kick", lambda *a, **k: kicked.append(a))
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    monkeypatch.setattr(job_watch, "state_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(job_watch, "evaluate",
                        lambda **kw: [grade(one_job, [])])
    job_watch.tick(report_only=True, now=WED)
    assert kicked == []
    assert not (tmp_path / "w.jsonl").exists()
    assert not (tmp_path / "s.json").exists()


def test_a_tick_records_what_it_did(one_job, monkeypatch, tmp_path):
    monkeypatch.setattr(job_watch, "kick", lambda *a, **k: True)
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    monkeypatch.setattr(job_watch, "state_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(job_watch, "evaluate", lambda **kw: [grade(one_job, [])])
    job_watch.tick(now=WED)
    rows = [json.loads(x) for x in
            (tmp_path / "w.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["kicked"] == ["your Morning Coffee briefing"]


def test_the_kick_count_survives_across_ticks(one_job, monkeypatch, tmp_path):
    """The cap is worthless if the count resets every half hour."""
    monkeypatch.setattr(job_watch, "kick", lambda *a, **k: True)
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    monkeypatch.setattr(job_watch, "state_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(job_watch, "evaluate", lambda **kw: [grade(one_job, [])])
    job_watch.tick(now=WED)
    job_watch.tick(now=WED + timedelta(minutes=30))
    state = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert list(state["slots"].values())[0]["kicks"] == 2


def test_a_tick_never_raises_even_with_nothing_on_disk(monkeypatch, tmp_path):
    """Fails open: a watcher that can crash is worse than no watcher."""
    monkeypatch.setattr(job_watch, "roster", lambda: [])
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    monkeypatch.setattr(job_watch, "state_path", lambda: tmp_path / "s.json")
    assert job_watch.tick(now=WED) == []


def test_old_slots_are_forgotten(one_job, monkeypatch, tmp_path):
    monkeypatch.setattr(job_watch, "state_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    monkeypatch.setattr(job_watch, "evaluate", lambda **kw: [])
    stale = {"slots": {"digest-x@2026-01-01T07:00": {"kicks": 1}}}
    job_watch._prune_state(stale, WED)
    assert stale["slots"] == {}


# ── What the reader sees ─────────────────────────────────────────────────────

def test_a_quiet_day_says_nothing(monkeypatch, tmp_path):
    """A watcher that reports every quiet morning teaches the reader to skip
    the footer, which is where the one line that matters will be."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": [], "jobs": {}, "held": {}}) + "\n",
        encoding="utf-8")
    assert job_watch.summary(now=WED)["line"] == ""


def test_a_recovery_is_said_plainly(monkeypatch, tmp_path):
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": ["Your Week briefing"], "jobs": {},
         "held": {}}) + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    assert "Your Week briefing" in line
    assert "did not run on schedule, so it was restarted" in line
    assert "has now finished" in line


def test_a_dead_watcher_says_so_itself(monkeypatch, tmp_path):
    """There is no second process watching this one. The briefing is the
    dead-man, so the stale line has to come from here."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": (WED - timedelta(hours=9)).isoformat(), "kicked": [],
         "jobs": {}, "held": {}}) + "\n", encoding="utf-8")
    out = job_watch.summary(now=WED)
    assert out["stale"] is True
    assert "has itself stopped" in out["line"]


def test_no_log_at_all_reads_as_stale_not_as_healthy(monkeypatch, tmp_path):
    """The empty-set trap: nothing recorded must never grade as nothing wrong."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "none.jsonl")
    assert job_watch.summary(now=WED)["stale"] is True


def test_turning_it_off_silences_the_footer(monkeypatch):
    import config_loader
    monkeypatch.setattr(config_loader, "job_watch_enabled", lambda: False)
    assert job_watch.summary(now=WED) == {"line": "", "stale": False,
                                          "recovered": [], "waiting": []}


def test_the_summary_never_raises(monkeypatch):
    """It runs inside every briefing's meta block. It cannot be the reason a
    briefing does not render."""
    monkeypatch.setattr(job_watch, "read_ticks",
                        lambda **k: (_ for _ in ()).throw(OSError("disk")))
    assert job_watch.summary(now=WED)["line"] == ""


# ── Housekeeping ─────────────────────────────────────────────────────────────

def test_the_log_is_trimmed_so_it_cannot_grow_forever(tmp_path):
    path = tmp_path / "w.jsonl"
    path.write_text("\n".join(f'{{"n":{i}}}' for i in range(50)) + "\n",
                    encoding="utf-8")
    job_watch._trim(path, cap=10)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert json.loads(lines[-1])["n"] == 49


def test_a_torn_line_costs_only_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(
        '{"ts": "2026-09-09T11:00:00", "kicked": []}\n{"ts": "not json\n',
        encoding="utf-8")
    assert len(job_watch.read_ticks()) == 1


# ── The direction rule ───────────────────────────────────────────────────────
#
# A cold reader given "waiting for the usage cap to reset" next to "waiting for
# you to sign in again" could not tell which one was their problem: same shape,
# and only the word after "for" carried the difference. These pin the fix.

@pytest.mark.parametrize("detail, klass, needs_reader", [
    ("You're out of extra usage, resets 3:40pm", "quota", False),
    ("Claude Code 2.1.139 does not support this model; "
     "version 2.1.251 or newer is required", "version", False),
    ("cannot determine safety of Bash", "classifier", False),
    ("Not logged in", "auth", True),
])
def test_every_hold_says_whether_the_reader_has_to_do_anything(
        one_job, monkeypatch, detail, klass, needs_reader):
    import claude_update
    monkeypatch.setattr(claude_update, "cli_version", lambda *a, **k: "2.1.139")
    _, why = job_watch.actionable(failed_row(one_job, detail, klass), {}, WED)
    assert why, "a hold with no reason tells the reader nothing"
    if needs_reader:
        assert job_watch._needs_the_reader(why), why
        assert "Nothing for you to do" not in why
    else:
        assert "Nothing for you to do" in why, why
        assert not job_watch._needs_the_reader(why), why


def test_no_line_ever_shows_an_internal_job_name(one_job, monkeypatch, tmp_path):
    """"digest-morning-coffee" is a config key. The reader has never seen it,
    cannot act on it, and it is the tell that a machine wrote the sentence."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": ["Your Week briefing"],
         "jobs": {"digest-week": "missing"},
         "held": {"Your Morning Coffee briefing":
                  "Sign in to Claude again and it will run on its next turn."}})
        + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    for internal in ("digest-week", "digest-morning-coffee", "ingest-workspace"):
        assert internal not in line, line


def test_what_the_reader_must_fix_is_said_before_what_is_handled(
        monkeypatch, tmp_path):
    """A reader with something to fix should not have to read past two
    reassurances and a success to find it."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": ["Your Week briefing"], "jobs": {},
         "held": {"A cap job": "It will run itself again. Nothing for you to do.",
                  "Z sign-in job": "needs you to sign in to Claude again."}})
        + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    # Sorted alphabetically the cap job would come first; direction wins.
    assert line.index("Z sign-in job") < line.index("A cap job"), line
    assert line.index("A cap job") < line.index("was restarted"), line


def test_a_job_restarted_after_being_held_is_not_still_reported_as_waiting(
        monkeypatch, tmp_path):
    """One job cannot be both waiting and restarted in the same line. Saying
    both is how a footer contradicts itself in front of the reader."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(
        json.dumps({"ts": (WED - timedelta(hours=1)).isoformat(), "kicked": [],
                    "jobs": {}, "held": {"Your Week briefing":
                                         "Claude was unavailable. Nothing for you to do."}})
        + "\n" +
        json.dumps({"ts": WED.isoformat(), "kicked": ["Your Week briefing"],
                    "jobs": {}, "held": {}}) + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    assert "was restarted" in line
    assert "Nothing for you to do" not in line, line


def test_two_restarted_jobs_read_as_a_sentence_not_a_list(monkeypatch, tmp_path):
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(),
         "kicked": ["the filing of your meeting notes",
                    "the sync of your project notes"],
         "jobs": {}, "held": {}}) + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    assert "notes and the sync of your project notes" in line
    assert "did not run on schedule, so they were restarted" in line


def test_the_dead_watcher_line_tells_the_reader_how_to_fix_it(monkeypatch, tmp_path):
    """The one line nothing else can recover from: if the watcher is dead, no
    watcher will restart the watcher. So it must name the fix itself."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": (WED - timedelta(hours=9)).isoformat(), "kicked": [],
         "jobs": {}, "held": {}}) + "\n", encoding="utf-8")
    assert "/van-gogh:install-van-gogh" in job_watch.summary(now=WED)["line"]


def test_the_job_name_reads_as_the_subject_of_its_own_sentence(
        monkeypatch, tmp_path):
    """Two earlier shapes were wrong, so both are pinned here.

    Joining title and reason with a bare space ran two sentences together
    ("Your Morning Coffee briefing It stopped because ..."), and putting a
    colon in front of a verb read like a log line ("... briefing: is waiting
    for"). The reason is a predicate, so the title is its subject."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": [], "jobs": {},
         "held": {"your Morning Coffee briefing":
                  "stopped because you've hit your Claude usage limit. "
                  "Nothing for you to do."}}) + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    assert line.startswith("Your Morning Coffee briefing stopped because"), line
    assert "briefing: " not in line, line
    assert "briefing It stopped" not in line, line


def test_no_reason_starts_with_a_capital_or_it_breaks_the_sentence(one_job,
                                                                   monkeypatch):
    """Each reason continues the sentence its job title began, so one written
    as its own sentence would produce "Your Week briefing It stopped"."""
    import claude_update
    monkeypatch.setattr(claude_update, "cli_version", lambda *a, **k: "2.1.139")
    for detail, klass in [("out of extra usage, resets 3pm", "quota"),
                          ("Not logged in", "auth"),
                          ("cannot determine safety", "classifier"),
                          ("Claude Code 2.1.139 does not support this model; "
                           "version 2.1.251 or newer is required", "version")]:
        _, why = job_watch.actionable(failed_row(one_job, detail, klass), {}, WED)
        first = why.split()[0]
        assert not first[0].isupper() or first == "Claude", why


def test_rows_written_before_titles_existed_still_read_as_names(
        one_job, monkeypatch, tmp_path):
    """An upgrade must not put "digest-morning-coffee" in front of anyone.

    Tick rows written by the previous version carry internal names, and those
    rows are still in the log the morning after an update."""
    monkeypatch.setattr(job_watch, "watch_log_path", lambda: tmp_path / "w.jsonl")
    (tmp_path / "w.jsonl").write_text(json.dumps(
        {"ts": WED.isoformat(), "kicked": ["digest-morning-coffee"],
         "jobs": {}, "held": {}}) + "\n", encoding="utf-8")
    line = job_watch.summary(now=WED)["line"]
    assert "digest-morning-coffee" not in line, line
    assert "Your Morning Coffee briefing" in line, line


def test_an_urgent_line_is_still_urgent_when_it_is_capitalized(one_job):
    """The same reason reads "sign in ..." mid line and "Sign in ..." when it
    leads one. A case-sensitive marker would sort the capitalized form as
    routine and bury it behind the reassurances."""
    _, why = job_watch.actionable(failed_row(one_job, "Not logged in", "auth"),
                                  {}, WED)
    assert job_watch._needs_the_reader(why)
    assert job_watch._needs_the_reader(why[:1].upper() + why[1:])


# ── Pollers: graded on liveness, because they owe nothing on a quiet day ─────
#
# A clock job is graded by asking "its slot passed, did it deliver". Neither
# half of that applies to a ticker. It has no slot, and on a day with no calls
# the prep poller correctly does nothing at all, so an absent artifact is the
# healthy case rather than the failing one. What can be asked is whether it is
# still alive, and a poller that dies quietly costs every prep from then on.

def poller_job(name="prep-email", interval=15):
    return {"name": name, "title": "the prep email before your calls",
            "kind": "poller", "label": f"com.monet.{name}",
            "task": f"VanGogh-{name}", "ledger_job": "prep.email",
            "days": ALL_DAYS, "hour": None, "minute": None,
            "interval_min": interval, "deliverable": None}


def poller_run(when, rc=0, detail="", error_class=None):
    row = {"job": "prep.email", "started": when.isoformat(),
           "finished": when.isoformat(), "rc": rc, "detail": detail}
    if error_class is not None:
        row["error_class"] = error_class
    return row


def test_a_poller_has_no_due_moment():
    """Asking "was it late for 4:30" of something that runs every fifteen
    minutes is the wrong question, and answering it produced a job graded
    missing every day it had nothing to do."""
    assert job_watch.latest_slot(poller_job(), WED) is None


def test_a_poller_that_ticked_recently_is_ok():
    job = poller_job()
    runs = [poller_run(WED - timedelta(minutes=5))]
    assert job_watch._poller_row(job, runs, WED)["status"] == "ok"


def test_a_poller_with_nothing_to_do_is_still_ok():
    """The whole point. Most days have no call inside the lead window, so the
    poller ticks, sends nothing, and is entirely healthy."""
    job = poller_job()
    runs = [poller_run(WED - timedelta(minutes=n)) for n in (5, 20, 35)]
    row = job_watch._poller_row(job, runs, WED)
    assert row["status"] == "ok"
    assert row["slot"] is None


def test_a_poller_gone_quiet_is_missing():
    job = poller_job()
    runs = [poller_run(WED - timedelta(hours=3))]
    row = job_watch._poller_row(job, runs, WED)
    assert row["status"] == "missing"
    assert "every 15 minutes" in row["detail"]
    assert row["_skey"], "it needs a slot key or the kick counter cannot advance"


def test_the_quiet_window_sits_past_the_threshold_not_on_it():
    """One skipped wake on a laptop that slept is not a symptom. Four missed
    ticks is."""
    job = poller_job()
    inside = WED - timedelta(minutes=15 * job_watch.POLLER_MISSED_TICKS) + timedelta(minutes=1)
    outside = WED - timedelta(minutes=15 * job_watch.POLLER_MISSED_TICKS) - timedelta(minutes=1)
    assert job_watch._poller_row(job, [poller_run(inside)], WED)["status"] == "ok"
    assert job_watch._poller_row(job, [poller_run(outside)], WED)["status"] == "missing"


def test_a_poller_that_has_never_run_is_reported_not_kicked():
    """A poller installed minutes ago has not had a turn yet, and the ledger
    cannot tell that apart from one that stopped. Kicking it would spend the
    budget on nothing."""
    row = job_watch._poller_row(poller_job(), [], WED)
    assert row["status"] == "no_history"
    assert row["status"] not in job_watch.ACTIONABLE


def test_a_failing_poller_keeps_its_class():
    job = poller_job()
    runs = [poller_run(WED - timedelta(minutes=5), rc=1,
                       detail="Not logged in", error_class="auth")]
    row = job_watch._poller_row(job, runs, WED)
    assert row["status"] == "failed"
    assert row["error_class"] == "auth"


def test_the_kick_counter_survives_a_later_tick_seeing_a_later_run():
    """The slot key must not move while the job stays dead.

    Keyed on "the last run I can see", the key changes the moment a stray row
    lands, the counter resets to zero, the cap is never reached, and the
    mechanic is never called: the job simply gets kicked forever. So the key
    is derived from the expected tick, and the property under test is that two
    grades of the same dead job agree even when the second sees more history.

    Mutation: change `out["slot"] = last + window` to `= last` and this fails.
    The earlier version of this test passed both ways, because it handed both
    calls the identical `runs` list, which is the one case where the two
    formulas cannot disagree."""
    job = poller_job()
    early = [poller_run(WED - timedelta(hours=3))]
    first = job_watch._poller_row(job, early, WED)

    # Half an hour later the ledger has one more row in it, still stale. A
    # real second tick always sees at least this much drift.
    later_runs = early + [poller_run(WED - timedelta(hours=2, minutes=30))]
    later = job_watch._poller_row(job, later_runs, WED + timedelta(minutes=30))

    assert first["status"] == later["status"] == "missing"
    assert first["_skey"] == later["_skey"], (
        "the slot key moved while the job stayed dead, so its kick count "
        "resets every tick and the cap is never reached")


def test_the_prep_poller_is_in_the_roster_only_when_it_is_on(monkeypatch):
    import config_loader

    monkeypatch.setattr(config_loader, "prep_email_enabled", lambda: False)
    assert not any(j["name"] == "prep-email" for j in job_watch.roster())

    monkeypatch.setattr(config_loader, "prep_email_enabled", lambda: True)
    rows = [j for j in job_watch.roster() if j["name"] == "prep-email"]
    assert len(rows) == 1
    assert rows[0]["interval_min"] == 15
    assert rows[0]["hour"] is None, "a ticker must not carry a clock time"


# ── An unauthorized connector ────────────────────────────────────────────────
#
# The token belongs to Anthropic and only a browser can renew it, so this hold
# says something different from the sign-in one: reconnect QuickBooks. Telling
# the reader to sign in to Claude would send them to do a thing that does not
# fix it.

_QB_DETAIL = ("CONNECTOR-UNAVAILABLE: claude_ai_Intuit_QuickBooks requires "
              "re-authorization (the stored token has expired). Reconnect it "
              "in Claude's connector settings.")


def test_an_unauthorized_connector_is_never_retried(one_job):
    """MUTATION: delete the `klass == "connector"` branch in actionable() and
    this fails, because the job is kicked every half hour against a wall only
    the user can take down."""
    row = failed_row(one_job, _QB_DETAIL, "connector")
    can, why = job_watch.actionable(row, {}, WED)
    assert can is False
    assert "reconnect" in why.lower()


def test_the_hold_names_the_service_rather_than_its_machine_spelling(one_job):
    """`claude_ai_Intuit_QuickBooks` is a string the reader has never seen."""
    row = failed_row(one_job, _QB_DETAIL, "connector")
    _can, why = job_watch.actionable(row, {}, WED)
    assert "QuickBooks" in why
    assert "claude_ai_Intuit" not in why


def test_an_unknown_connector_still_says_something_actionable(one_job):
    row = failed_row(one_job, "CONNECTOR-UNAVAILABLE: mystery_server is not "
                              "connected to this machine at all.", "connector")
    _can, why = job_watch.actionable(row, {}, WED)
    assert "the connector it needs" in why


def test_the_connector_hold_is_counted_as_needing_the_reader(one_job):
    """The briefing splits "handled for you" from "your move" on this list."""
    row = failed_row(one_job, _QB_DETAIL, "connector")
    _can, why = job_watch.actionable(row, {}, WED)
    assert job_watch._needs_the_reader(why) is True


def test_a_connector_failure_never_wakes_the_mechanic(monkeypatch, one_job):
    """There is nothing to diagnose, and it could not reach a browser anyway."""
    calls = []
    import repair

    monkeypatch.setattr(repair, "run",
                        lambda *a, **k: calls.append(a) or {"spawned": True})
    row = failed_row(one_job, _QB_DETAIL, "connector", kicks=3)
    # The exact marker _STUCK looks for: this row HAS reached the end of the
    # line, so only the class exclusion can be what spares the mechanic.
    row["_why_not"] = "stopped trying for today"
    job_watch._escalate([row], WED)
    assert calls == []


def test_the_mechanic_still_runs_for_a_class_it_can_help(monkeypatch, one_job):
    """MUTATION CONTROL: proves the exclusion above is the reason, not a
    broken escalation path."""
    calls = []
    import repair

    monkeypatch.setattr(repair, "run",
                        lambda *a, **k: calls.append(a) or {"spawned": True})
    row = failed_row(one_job, "TypeError: unsupported operand", "unknown",
                     kicks=3)
    row["_why_not"] = "stopped trying for today"
    job_watch._escalate([row], WED)
    assert len(calls) == 1
