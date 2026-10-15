"""The nudge for a stalled install: cadence, body, channels, the job (P26, P27).

Everything here is hermetic: a fake clock goes in as `now`, the sign-in probe
and the send are stubbed, the scheduler is a recorder standing in for
launchctl and schtasks, and the desktop notification is a counter.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

import send_email
import setup_nudge as sn
import setup_state as ss

TZ = timezone(timedelta(hours=-7))
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=TZ)        # the client's last activity
FIRE = T0.replace(hour=9)                          # the job's daily hour


def day(n: int, hour: int = 9, minute: int = 0, second: int = 0) -> datetime:
    return (FIRE + timedelta(days=n)).replace(hour=hour, minute=minute, second=second)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VAN_GOGH_SETUP_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    monkeypatch.delenv("VAN_GOGH_UNATTENDED", raising=False)
    monkeypatch.delenv("VAN_GOGH_SCHEDULED", raising=False)
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


class FakeScheduler:
    """launchctl and schtasks as a recorder: bootstrap registers the label,
    bootout and unload forget it, `list` prints what is registered."""

    def __init__(self):
        self.calls = []
        self.loaded = set()
        self.tasks = set()
        self.bootstrap_fails = False

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        rc, out = 0, ""
        if cmd[0] == "launchctl":
            verb = cmd[1]
            if verb == "list":
                out = "\n".join(f"-\t0\t{label}" for label in sorted(self.loaded))
            elif verb == "bootstrap":
                if self.bootstrap_fails:
                    rc = 5
                else:
                    self.loaded.add(sn.MAC_LABEL)
            elif verb == "load":
                self.loaded.add(sn.MAC_LABEL)
            elif verb in ("bootout", "unload"):
                self.loaded.discard(sn.MAC_LABEL)
        elif cmd[0] == "schtasks":
            if cmd[1] == "/query":
                rc = 0 if cmd[3] in self.tasks else 1
            elif cmd[1] == "/delete":
                self.tasks.discard(cmd[3])
        elif cmd[0] == "powershell":
            if "Register-ScheduledTask" in cmd[-1]:
                self.tasks.add(sn.WIN_TASK)
        elif cmd[0] in ("osascript", "notify-send"):
            pass
        return subprocess.CompletedProcess(cmd, rc, out, "")


@pytest.fixture
def scheduler(state_dir, tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(sn, "_run", fake)
    monkeypatch.setattr(sn, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")
    return fake


@pytest.fixture
def no_machine_steps(monkeypatch):
    """A setup_flow whose machine pass does nothing, so the cadence tests
    measure the cadence and not Lane C1's module."""
    monkeypatch.setitem(sys.modules, "setup_flow",
                        types.SimpleNamespace(finish_machine_boxes=lambda data: []))


def stalled_checklist(updated: datetime = T0, done=("plugin", "python", "credentials")) -> dict:
    data = ss.new("20261001-170000-a1b2", "darwin", "0.81.0")
    for key in done:
        ss.set_status(data, key, ss.DONE, "ok")
    data["updated"] = updated.isoformat(timespec="seconds")
    ss.save(data, touch=False)
    return data


class Mailbox:
    def __init__(self):
        self.sent = []

    def __call__(self, account, to, subject, body, html=None):
        self.sent.append({"account": account, "to": to, "subject": subject,
                          "body": body, "html": html})
        return {"id": f"msg-{len(self.sent)}", "threadId": "t1"}


@pytest.fixture
def mailbox(monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(send_email, "send_email", box)
    return box


def good_probe(provider, label):
    return "jane@example.com"


def dead_probe(provider, label):
    raise RuntimeError("invalid_grant: Token has been expired or revoked.")


def plant_env(state_dir):
    (state_dir / ".env").write_text(
        "GOOGLE_REFRESH_TOKEN_GMAIL=1//0gsecretRefreshValue\n"
        "MS_GRAPH_REFRESH_TOKEN_OUTLOOK=0.AXsecretGraphValue\n"
        "VAN_GOGH_CUSTOM_TOKEN=github_pat_secretCompanionValue\n",
        encoding="utf-8")


def fire(now, probe=good_probe, notify=None, **kw):
    return sn.run_once(now=now, probe=probe, notify=notify or (lambda line, platform: True),
                       checks={}, platform="darwin", **kw)


def send_days(mailbox) -> list:
    return [(datetime.fromisoformat(r["when"]) - FIRE).days
            for r in ss.load()["nudges"]["sent"]]


# ── P26: the cadence ─────────────────────────────────────────────────────────

def test_sends_on_day_1_3_and_7_only_then_goes_quiet(scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    stalled_checklist()
    assert sn.job_install("darwin")["state"] == "loaded"
    assert sn.job_status("darwin")["state"] == "loaded"      # registered before

    results = [fire(day(n)) for n in range(31)]

    assert [r["sent"] for r in results].count(1) == 3
    assert send_days(mailbox) == [1, 3, 7]
    assert [r["when"] for r in ss.load()["nudges"]["sent"]] == [
        day(1).isoformat(timespec="seconds"), day(3).isoformat(timespec="seconds"),
        day(7).isoformat(timespec="seconds")]
    assert len(mailbox.sent) == 3
    for m in mailbox.sent:
        assert m["subject"] == sn.SUBJECT
        assert m["to"] == m["account"]["email"] == "jane@example.com"
        assert m["account"]["provider"] == "google" and m["account"]["label"] == "GMAIL"
    sent_rows = ss.load()["nudges"]["sent"]
    assert [r["message_id"] for r in sent_rows] == ["msg-1", "msg-2", "msg-3"]
    assert all(r["channel"] == "email" and r["box"] == "accounts" for r in sent_rows)

    # Day 0 saw the stall (an hour since the last update) and did not send.
    assert results[0]["sent"] == 0 and results[0]["stall_started"] == day(0).isoformat(timespec="seconds")
    # Every run left a row; the third send left the quiet row after it.
    runs = ss.load()["nudges"]["runs"]
    assert len(runs) == 32
    assert all(set(r) >= {"when", "sent", "reason"} for r in runs)
    assert [r["reason"] for r in runs if r["sent"] == 0][:1] == [
        "ran, 0 to send (day 0 of the stall, next on day 1)"]
    quiet = [r for r in runs if r["reason"] == "went quiet"]
    assert len(quiet) == 1 and quiet[0]["sent"] == 0
    assert runs.index(quiet[0]) == runs.index(next(r for r in runs if r["reason"].startswith("sent by email (nudge 3"))) + 1
    assert ss.load()["nudges"]["quiet"] is True
    assert all(r["reason"] == "ran, 0 to send (went quiet)" for r in runs[runs.index(quiet[0]) + 1:])

    # The job is gone: the scheduler was told, and the plist is off disk.
    assert sn.job_status("darwin")["state"] == "not installed"
    assert ["launchctl", "bootout", f"gui/{sn._uid()}/{sn.MAC_LABEL}"] in scheduler.calls
    assert not sn.plist_path().exists()
    # The checklist itself survives: finishing the install is not this job's call.
    assert ss.exists()


def test_three_fires_after_a_week_asleep_send_once(scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    stalled_checklist()
    fire(day(0))
    results = [fire(day(7, second=s)) for s in (0, 1, 2)]
    assert [r["sent"] for r in results] == [1, 0, 0]
    assert results[1]["reason"] == results[2]["reason"] == "ran, 0 to send (sent within 24 h)"
    assert len(mailbox.sent) == 1
    # The next morning the second nudge goes, because 24 h have passed.
    assert fire(day(8))["sent"] == 1
    assert fire(day(8, minute=30))["sent"] == 0


def test_a_resume_on_day_5_restarts_the_cadence(scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    stalled_checklist()
    for n in range(5):
        fire(day(n))
    assert send_days(mailbox) == [1, 3]
    # The client comes back on day 5 at 10:00 and ticks a box.
    data = ss.load()
    ss.set_status(data, "accounts", ss.DONE, "1 signed in")
    data["updated"] = day(5, hour=10).isoformat(timespec="seconds")
    ss.save(data, touch=False)
    # Then stops again. The first fire after that sees a resume, then a fresh
    # stall (23 h since the update), day 0 of a new cadence.
    r = fire(day(6))
    assert r["resumed"] is True and r["sent"] == 0
    assert r["stall_started"] == day(6).isoformat(timespec="seconds")
    for n in range(7, 31):
        fire(day(n))
    assert send_days(mailbox) == [1, 3, 7, 9, 13]
    assert ss.load()["nudges"]["quiet"] is True
    # The second cadence nudged about the next open human box.
    assert [r["box"] for r in ss.load()["nudges"]["sent"]] == [
        "accounts", "accounts", "vault", "vault", "vault"]


def test_an_install_in_progress_is_never_nudged(scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    stalled_checklist(updated=T0)
    r = fire(T0 + timedelta(minutes=10))
    assert r["sent"] == 0 and r["stall_started"] == ""
    assert r["reason"] == "ran, 0 to send (install in progress)"
    r = fire(T0 + timedelta(minutes=29))
    assert r["stall_started"] == ""
    r = fire(T0 + timedelta(minutes=30))
    assert r["stall_started"] == (T0 + timedelta(minutes=30)).isoformat(timespec="seconds")
    assert mailbox.sent == []


def test_the_body_carries_the_action_the_place_and_the_help_address_and_no_secret(
        scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    key = {
        "kind": "van-gogh-key", "version": 2, "issued": "2026-10-01",
        "repo": "NVentures/project-van-gogh-custom-jane-doe",
        "token": "github_pat_11AAAAAAA0secretTokenValue",
        "credentials": {"kind": "van-gogh-credentials",
                        "google_client_secret": {"installed": {
                            "client_id": "1234-secretclient.apps.googleusercontent.com",
                            "client_secret": "GOCSPX-secretGoogleSecret"}},
                        "ms_client_secret": {"client_id": "aaaa-bbbb-secret-ms-id",
                                             "tenant_id": "common"},
                        "maps_api_key": "AIzaSecretMapsKey"},
        "accounts": [{"provider": "google", "email_hint": "jane@example.com"}],
        "client": {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme",
                   "timezone": "America/New_York", "notetaker": "granola"},
        "support_email": "team@vangogh.example",
    }
    ss.keyfile_dir().mkdir(parents=True)
    (ss.keyfile_dir() / "van-gogh-key.json").write_text(json.dumps(key), encoding="utf-8")
    stalled_checklist()
    fire(day(0))
    assert fire(day(1))["sent"] == 1
    body = mailbox.sent[0]["body"]

    assert "Van Gogh is not finished setting up" in body
    assert "The next step: sign in to your accounts." in body
    assert "Open Claude and type /van-gogh:install-van-gogh" in body
    assert "Need a hand? Write to team@vangogh.example." in body
    assert chr(0x2014) not in body and chr(0x2013) not in body
    # One action, not a list of everything unfinished.
    for _key, title, _needs, action, _m in ss.BOXES:
        assert title not in body
        if action != "sign in to your accounts":
            assert action not in body
    # No value from the key file or the state .env, grepped by VALUE.
    values = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k != "support_email":
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and len(node) >= 4:
            values.append(node)
    walk(key)
    for line in (state_dir / ".env").read_text(encoding="utf-8").splitlines():
        values.append(line.split("=", 1)[1])
    assert len(values) >= 15
    leaked = [v for v in values if v in body]
    assert leaked == [], leaked
    # The sign-in address is not in the body either (it is the envelope).
    assert "jane@example.com" not in body


def test_who_must_act_names_the_admin_the_team_and_the_notetaker(state_dir):
    data = ss.new("r1")
    for key in ("plugin", "python", "credentials"):
        ss.set_status(data, key, ss.DONE)
    ss.set_status(data, "accounts", ss.OPEN,
                  "AADSTS65001: admin consent is required for this app")
    body = sn.body_for(data, "team@example.com")
    assert "Your IT admin must approve Van Gogh" in body
    ss.set_status(data, "accounts", ss.OPEN,
                  "access_denied: the app has not completed the Google verification process")
    body = sn.body_for(data, "team@example.com")
    assert "The Van Gogh team must add your account" in body
    assert "IT admin" not in body
    ss.set_status(data, "accounts", ss.OPEN, "")
    ss.set_status(data, "meetings", ss.OPEN, "notetaker sign-in failed (401)")
    body = sn.body_for(data, "")
    assert "Your notetaker sign-in is not working" in body
    assert "Need a hand" not in body          # no help address known: line omitted
    assert [b["key"] for b in sn.nudge_targets(data)] == [
        "accounts", "vault", "jobs", "deals", "meetings"]


def test_help_address_falls_back_to_the_config_when_a_vault_exists(state_dir, tmp_path):
    assert sn.support_email() == ""
    (state_dir / "vault-pointer").write_text(str(tmp_path / "vault"), encoding="utf-8")
    # conftest primes the config with support.team_email
    assert sn.support_email() == "support@example.com"
    ss.keyfile_dir().mkdir(parents=True)
    (ss.keyfile_dir() / "van-gogh-key.json").write_text(
        json.dumps({"kind": "van-gogh-key", "support_email": "team@vangogh.example"}),
        encoding="utf-8")
    assert sn.support_email() == "team@vangogh.example"


def test_once_the_checklist_is_gone_nothing_is_sent_and_the_ledger_says_so(
        scheduler, mailbox, no_machine_steps, state_dir, capsys):
    plant_env(state_dir)
    stalled_checklist()
    sn.job_install("darwin")
    ss.remove()
    assert fire(day(1)) is None
    assert mailbox.sent == []
    rows = [r for r in ss.read_ledger() if r["event"] == "nudge"]
    assert rows and rows[-1]["sent"] == 0 and rows[-1]["reason"] == "ran, 0 to send"
    # A straggling fire also takes the job with it.
    assert sn.job_status("darwin")["state"] == "not installed"
    # And through the CLI it is silent: exit 0, empty stdout, empty stderr.
    assert sn.main(["run", "--now", day(2).isoformat()]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_stop_reminders_stops_the_email_but_not_the_line(
        scheduler, mailbox, no_machine_steps, state_dir, capsys):
    plant_env(state_dir)
    stalled_checklist()
    sn.job_install("darwin")
    fire(day(0))
    assert sn.main(["stop"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["stopped"]
    data = ss.load()
    assert data["nudges"]["stop_reminders"] is True
    assert sn.job_status("darwin")["state"] == "not installed"
    for n in range(1, 15):
        r = fire(day(n))
        assert r["sent"] == 0 and r["reason"] == "ran, 0 to send (reminders stopped)"
    assert mailbox.sent == []
    assert ss.one_line().startswith("Van Gogh setup: 6 of 9 install steps left.")
    assert sn.main(["line"]) == 0
    assert capsys.readouterr().out.strip() == ss.one_line()


def test_with_no_working_sign_in_a_desktop_notification_goes_instead(
        scheduler, mailbox, no_machine_steps, state_dir):
    plant_env(state_dir)
    stalled_checklist()
    shown = []

    def notify(line, platform):
        shown.append(line)
        return True

    for n in range(31):
        fire(day(n), probe=dead_probe, notify=notify)
    assert mailbox.sent == []
    rows = ss.load()["nudges"]["sent"]
    assert [r["channel"] for r in rows] == ["desktop"] * 3
    assert [r["message_id"] for r in rows] == ["", "", ""]
    whens = [datetime.fromisoformat(r["when"]) for r in rows]
    assert all(b - a >= timedelta(hours=24) for a, b in zip(whens, whens[1:]))
    assert send_days(mailbox) == [1, 3, 7]
    assert shown == [ss.one_line()] * 3
    assert ss.load()["nudges"]["quiet"] is True


def test_the_desktop_notification_is_the_os_command_and_fails_open(scheduler, state_dir):
    assert sn._notify_desktop("Van Gogh setup: 6 of 9 install steps left.", "darwin") is True
    assert scheduler.calls[-1][:2] == ["osascript", "-e"]
    assert 'display notification "Van Gogh setup: 6 of 9 install steps left." with title "Van Gogh"' \
        == scheduler.calls[-1][2]
    assert sn._notify_desktop("x", "win32") is True
    assert scheduler.calls[-1][0] == "powershell"
    assert "ToastNotificationManager" in scheduler.calls[-1][-1]

    def boom(cmd):
        raise OSError("no such binary")
    sn._run = boom
    assert sn._notify_desktop("x", "darwin") is False


def test_a_failed_send_is_a_row_not_a_crash_and_is_retried_next_day(
        scheduler, no_machine_steps, state_dir, monkeypatch):
    plant_env(state_dir)
    stalled_checklist()
    calls = {"n": 0}

    def flaky(account, to, subject, body, html=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HttpError 500 with a token in its text")
        return {"id": "ok"}
    monkeypatch.setattr(send_email, "send_email", flaky)
    fire(day(0))
    r = fire(day(1))
    assert r["sent"] == 0 and r["reason"] == "ran, 0 to send (send failed: RuntimeError)"
    assert "token" not in json.dumps(ss.load()["nudges"])
    assert fire(day(2))["sent"] == 1


def test_dry_run_decides_and_writes_nothing(scheduler, mailbox, no_machine_steps, state_dir, capsys):
    plant_env(state_dir)
    stalled_checklist()
    fire(day(0))
    before = ss.checklist_path().read_text(encoding="utf-8")
    assert sn.main(["run", "--dry-run", "--now", day(1).isoformat()]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reason"].startswith("would send by ")
    assert "Open Claude and type" in out["body"]
    assert ss.checklist_path().read_text(encoding="utf-8") == before
    assert mailbox.sent == []


# ── the hook's silent cases through the CLI ──────────────────────────────────

@pytest.mark.parametrize("var", ["VAN_GOGH_UNATTENDED", "VAN_GOGH_SCHEDULED"])
def test_run_maybe_is_silent_inside_an_unattended_run(var, scheduler, no_machine_steps,
                                                       state_dir, monkeypatch, capsys):
    stalled_checklist()
    monkeypatch.setenv(var, "1")
    assert sn.main(["run", "--maybe"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert ss.load()["nudges"]["runs"] == []


def test_run_is_silent_with_no_checklist_and_no_setup_dir(scheduler, state_dir, capsys):
    assert sn.main(["run"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert not ss.setup_dir().exists()


# ── P27: machine boxes finished before nudging ───────────────────────────────

def _machine_case(monkeypatch, outcome, tmp_path=None):
    """The real setup_flow.finish_machine_boxes with the NOTETAKER stubbed:
    the `warm_start.py ingest-finish` child is replaced by Lane B's
    three-note FakeProvider driving the real meeting_ingest prefetch and
    staged filing into a temp vault (P27). `none` is a provider with no
    notes, `signin` a provider whose key is refused."""
    import setup_flow
    import ingest_fixture as fx
    import meeting_ingest as gi
    monkeypatch.setitem(ss.CHECKS, "python", lambda: (ss.DONE, "ready"))
    monkeypatch.setitem(ss.CHECKS, "plugin", lambda: (ss.DONE, "van-gogh"))
    monkeypatch.setitem(ss.CHECKS, "jobs", lambda: (None, ""))
    # plant_env's tokens exist for the secret scan, not as a sign-in: the
    # accounts box must stay open here so the nudge is about it.
    monkeypatch.setitem(ss.CHECKS, "accounts", lambda: (None, ""))
    # The real finisher re-verifies every box from disk first: give the
    # credentials box its file so it stays done, as on a real install.
    (ss.user_state.state_dir() / "google_client_secret.json").write_text(
        json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}), encoding="utf-8")
    vault = tmp_path / "ingest-vault"
    env = ss.user_state.env_file()
    env.write_text(env.read_text(encoding="utf-8") + "GRANOLA_API_KEY=fake\n", encoding="utf-8")

    def child(name, args, timeout=600, input_text=None, cwd=None):
        assert (name, args[0]) == ("warm_start.py", "ingest-finish"), (name, args)
        if outcome == "signin":
            reply = {"filed": [], "failed": [{"id": "note_a1", "error": "401 Unauthorized"}],
                     "deferred": [], "ingested": False, "errors": [],
                     "counts": {"filed": 0, "failed": 1, "deferred": 0}}
            return {"rc": 0, "json": reply, "text": json.dumps(reply)}
        staged = ss.setup_dir() / "staged"
        staged.mkdir(parents=True, exist_ok=True)
        provider = fx.FakeProvider()
        if outcome == "none":
            provider.notes, provider.order = {}, []
        with fx.prepared(gi, vault, monkeypatch, provider=provider):
            manifest = gi.run_prefetch(3, staged)
            ns = fx.nightly_args(3)
            ns.staged = str(staged)
            gi.run_staged(ns, staged)
        filed = [p.stem for p in sorted((vault / "wiki" / "sources").glob("*.md"))
                 if not p.name.startswith("catalog")]
        reply = {"filed": filed, "failed": manifest.get("failed") or [],
                 "deferred": [], "ingested": True, "errors": [],
                 "counts": {"filed": len(filed), "failed": 0, "deferred": 0}}
        return {"rc": 0, "json": reply, "text": json.dumps(reply)}

    monkeypatch.setattr(setup_flow, "_run_script_raw", child)
    return vault


@pytest.mark.parametrize("outcome", ["none", "signin", "three"])
def test_machine_boxes_are_finished_first_and_the_nudge_names_who_must_act(
        outcome, scheduler, mailbox, state_dir, monkeypatch, tmp_path):
    plant_env(state_dir)
    vault = _machine_case(monkeypatch, outcome, tmp_path)
    data = stalled_checklist()
    ss.set_status(data, "meetings", ss.PENDING, "prefetching")
    ss.save(data, touch=False)
    r0 = fire(day(0))
    r = fire(day(1))
    # The first fire files the meetings (the real finisher, once); the next
    # fire finds the box resolved and has nothing machine-only left.
    assert r0["machine"] == ("0 machine boxes finished" if outcome == "signin"
                             else "1 machine boxes finished")
    assert r["machine"] == "0 machine boxes finished"
    meetings = ss.box(ss.load(), "meetings")
    body = mailbox.sent[0]["body"]
    if outcome == "none":
        assert meetings["status"] == ss.DONE and meetings["detail"] == "0 filed"
        assert any(row.get("detail") == "0 filed" for row in ss.read_ledger())
        assert "notetaker" not in body
    elif outcome == "three":
        assert meetings["status"] == ss.DONE and meetings["detail"] == "3 filed"
        pages = sorted(p.name for p in (vault / "wiki" / "sources").glob("*.md")
                       if not p.name.startswith("catalog"))
        assert pages == ["Acme widget pricing call - 2026-09-29.md",
                         "Harbor Energy site walk - 2026-09-30.md",
                         "Weekly internal sync - 2026-09-28.md"]
        assert "notetaker" not in body
    else:
        assert meetings["status"] == ss.OPEN
        assert "Your notetaker sign-in is not working" in body
        assert "meetings" in [b["key"] for b in sn.nudge_targets(ss.load())]
    # The human boxes stayed open and the nudge is about the first of them.
    assert r["sent"] == 1 and r["box"] == "accounts"
    assert "The next step: sign in to your accounts." in body
    assert ss.box(ss.load(), "accounts")["status"] == ss.OPEN


def test_without_setup_flow_the_run_row_says_so(scheduler, mailbox, state_dir, monkeypatch):
    plant_env(state_dir)
    monkeypatch.setitem(sys.modules, "setup_flow", None)
    stalled_checklist()
    r = fire(day(0))
    assert r["machine"] == "setup_flow unavailable, machine boxes untouched"
    assert ss.load()["nudges"]["runs"][-1]["machine"] == r["machine"]


def test_a_crashing_machine_pass_does_not_stop_the_nudge(scheduler, mailbox, state_dir, monkeypatch):
    plant_env(state_dir)

    def finish(data):
        raise ValueError("boom with a secret in it")
    monkeypatch.setitem(sys.modules, "setup_flow",
                        types.SimpleNamespace(finish_machine_boxes=finish))
    stalled_checklist()
    fire(day(0))
    r = fire(day(1))
    assert r["sent"] == 1
    assert r["machine"] == "finish_machine_boxes failed (ValueError)"
    assert "secret" not in json.dumps(ss.load())


# ── the standalone job ───────────────────────────────────────────────────────

def test_mac_job_install_writes_a_daily_0900_plist_under_the_logs_dir(scheduler, state_dir, tmp_path):
    import plistlib
    (state_dir / "venv" / "bin").mkdir(parents=True)
    (state_dir / "venv" / "bin" / "python").write_text("", encoding="utf-8")
    out = sn.job_install("darwin")
    assert out["ok"] and out["state"] == "loaded"
    doc = plistlib.loads(sn.plist_path().read_bytes())
    assert doc["Label"] == "com.monet.setup-nudge"
    assert doc["ProgramArguments"] == [
        str(state_dir / "venv" / "bin" / "python"),
        str(tmp_path / "plugin" / "app" / "setup_nudge.py"), "run"]
    assert doc["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    assert doc["RunAtLoad"] is False
    assert doc["StandardOutPath"] == str(tmp_path / "logs" / "setup-nudge.log")
    assert doc["StandardErrorPath"] == str(tmp_path / "logs" / "setup-nudge.err")
    assert "Documents" not in doc["StandardOutPath"]
    assert doc["EnvironmentVariables"]["VAN_GOGH_STATE_DIR"] == str(state_dir)
    uid = sn._uid()
    assert scheduler.calls[-3:] == [
        ["launchctl", "bootout", f"gui/{uid}/com.monet.setup-nudge"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(sn.plist_path())],
        ["launchctl", "list"]]
    # Remove: told to the scheduler, gone from disk, status agrees.
    out = sn.job_remove("darwin")
    assert out["state"] == "not installed" and not sn.plist_path().exists()
    assert ["launchctl", "unload", str(sn.plist_path())] in scheduler.calls


def test_mac_job_install_falls_back_to_launchctl_load(scheduler, state_dir):
    scheduler.bootstrap_fails = True
    out = sn.job_install("darwin")
    assert out["ok"] and out["state"] == "loaded"
    assert ["launchctl", "load", str(sn.plist_path())] in scheduler.calls


def test_mac_job_status_distinguishes_on_disk_from_loaded(scheduler, state_dir):
    assert sn.job_status("darwin")["state"] == "not installed"
    sn.plist_path().parent.mkdir(parents=True, exist_ok=True)
    sn.plist_path().write_bytes(sn.plist_content())
    assert sn.job_status("darwin")["state"] == "on disk, not loaded"
    scheduler.loaded.add(sn.MAC_LABEL)
    assert sn.job_status("darwin")["state"] == "loaded"


def test_windows_job_registers_a_daily_task_that_runs_on_battery(scheduler, state_dir, tmp_path):
    out = sn.job_install("win32")
    assert out["ok"] and out["state"] == "installed" and out["name"] == "VanGogh-setup-nudge"
    ps = next(c[-1] for c in scheduler.calls if c[0] == "powershell")
    assert "New-ScheduledTaskTrigger -Daily -At 9:00am" in ps
    assert "-StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries" in ps
    assert "Register-ScheduledTask -TaskName 'VanGogh-setup-nudge'" in ps
    assert "-Execute 'wscript.exe'" in ps
    vbs = state_dir / "scheduler" / "VanGogh-setup-nudge.vbs"
    assert vbs.exists()
    inner = vbs.read_text(encoding="utf-16")
    assert "setup_nudge.py" in inner and " run " in inner
    assert "setup-nudge.log" in inner and "setup-nudge.err" in inner
    assert ["schtasks", "/query", "/tn", "VanGogh-setup-nudge"] in scheduler.calls
    out = sn.job_remove("win32")
    assert out["state"] == "not installed" and not vbs.exists()
    assert ["schtasks", "/delete", "/tn", "VanGogh-setup-nudge", "/f"] in scheduler.calls


def test_job_cli_prints_one_json_object(scheduler, state_dir, capsys, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sn.main(["job", "status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "not installed" and out["name"] == sn.MAC_LABEL


def test_a_cadence_test_cannot_pass_with_the_cadence_gone(scheduler, mailbox, no_machine_steps,
                                                           state_dir, monkeypatch):
    """Mutation: drop the 24 h gap and the asleep-week case sends three times."""
    plant_env(state_dir)
    stalled_checklist()
    fire(day(0))
    monkeypatch.setattr(sn, "MIN_GAP", timedelta(0))
    results = [fire(day(7, second=s)) for s in (0, 1, 2)]
    assert [r["sent"] for r in results] == [1, 1, 1]
