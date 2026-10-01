"""The Note tick, end to end, with every side effect planted.

No clock, no mailbox, no calendar, no CLI: each is monkeypatched so the test
can assert the ORDER of effects and what survives a failure. The shapes that
matter: a reply seen on four ticks mails once; the third event of a day folds
and still reaches the page; a render that fails leaves no ledger row so the
next tick tries again; a tick during a meeting writes nothing at all; and a
disabled feature is a no-op that touches no file.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import config_loader
import note_send
import note_watch

UTC = timezone.utc
# 10:42 PT on a Wednesday, well inside the default window.
NOW = datetime(2026, 9, 9, 17, 42, tzinfo=UTC)
TODAY = "2026-09-09"


def front_item(subject="Duke study results", email="foster@duke.com"):
    return {"subject": subject, "counterparty_email": email, "account": "Outlook",
            "counterparty_name": "Foster Lin", "age_days": 11,
            "label": "Waiting on them", "why": "asked 2026-08-29"}


def inbound(subject="Re: Duke study results", mid="AAMk-0001",
            received="2026-09-09T17:40:00+00:00"):
    return {"subject": subject, "from": "Foster Lin", "from_email": "foster@duke.com",
            "account": "Outlook", "snippet": "Results attached.",
            "received": received, "message_id": mid, "internal": False}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A planted machine: config on, one front-page item, an empty inbox and
    calendar, a stub renderer and a stub mailbox that both keep a log."""
    logs = tmp_path / "logs"
    md = tmp_path / "morning-coffee.md"
    md.write_text("# Morning Coffee\n\nOpening.\n\n## DO TODAY\n\n- [ ] x\n",
                  encoding="utf-8")
    state = {
        "inbox": [], "calendar": [], "rendered": [], "mailed": [],
        "kpi": [], "runs": [], "render_fail": False, "mail_fail": False,
    }
    monkeypatch.setattr(config_loader, "notes_enabled", lambda: True)
    monkeypatch.setattr(config_loader, "notes_daily_cap", lambda: 2)
    monkeypatch.setattr(config_loader, "notes_window", lambda: ("07:30", "18:00"))
    monkeypatch.setattr(config_loader, "logs_dir", lambda: logs)
    monkeypatch.setattr(config_loader, "morning_coffee_md_path", lambda: md)
    monkeypatch.setattr(config_loader, "afternoon_tea_md_path", lambda: tmp_path / "tea.md")
    monkeypatch.setattr(note_send, "_briefing_outputs", lambda: [
        ("morning-coffee", {"date": TODAY, "front_page": [front_item()]})])
    monkeypatch.setattr(note_send, "fetch_inbound", lambda since: list(state["inbox"]))
    monkeypatch.setattr(note_send, "_calendar", lambda: list(state["calendar"]))
    monkeypatch.setattr(note_send, "deal_headings", lambda: ["Northwind add-on"])
    monkeypatch.setattr(note_send, "_fmt", lambda dt: "10:42 AM PT / 1:42 PM ET")
    monkeypatch.setattr(note_send, "_account_for", lambda label: {
        "label": label, "provider": "microsoft", "email": "me@x.com"})
    monkeypatch.setattr(note_send.claude_update, "ensure_current", lambda *a, **k: {})
    monkeypatch.setattr(note_send.run_ledger, "record_run",
                        lambda *a, **k: state["runs"].append(a))
    monkeypatch.setattr(note_send.time, "sleep", lambda s: None)

    def render(path, claude=None):
        if state["render_fail"]:
            raise RuntimeError("classifier down")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        state["rendered"].append(payload)
        return f"**Note.** {payload['kind']} at {payload['time']}."

    def mail(subject, body):
        if state["mail_fail"]:
            raise RuntimeError("smtp")
        state["mailed"].append((subject, body))

    monkeypatch.setattr(note_send, "render_note", render)
    monkeypatch.setattr(note_send, "mail", mail)

    import kpi_events
    monkeypatch.setattr(kpi_events, "record",
                        lambda kind, **f: state["kpi"].append((kind, f)))
    state["md"] = md
    state["logs"] = logs
    return state


def ledger(world):
    return note_send._read_json(world["logs"] / "notes-sent.json")


# ── The happy path ───────────────────────────────────────────────────────────

def test_reply_on_a_front_page_item_mails_once_across_four_ticks(world):
    world["inbox"] = [inbound()]
    for i in range(4):
        note_send.tick(now=NOW + timedelta(minutes=15 * i))
    assert len(world["mailed"]) == 1
    subject, body = world["mailed"][0]
    assert subject == "Note: Foster Lin replied on Duke study results"
    assert "10:42 AM PT / 1:42 PM ET" in body
    rows = [r for r in ledger(world).values() if r["status"] == "sent"]
    assert len(rows) == 1 and rows[0]["kind"] == "reply"
    assert world["kpi"] == [("note", {"note_kind": "reply", "status": "sent"})]


def test_the_page_gains_a_notes_block_the_reader_can_see(world):
    world["inbox"] = [inbound()]
    note_send.tick(now=NOW)
    text = world["md"].read_text(encoding="utf-8")
    assert "## Notes" in text and note_watch.NOTES_START in text
    assert "Foster Lin replied on Duke study results, 11 days after you asked. " \
           "Reply drafted, waiting for your nod." in text
    assert text.index("## Notes") < text.index("## DO TODAY")
    assert "- [ ] x" in text


def test_event_file_carries_what_the_skill_needs(world):
    world["inbox"] = [inbound()]
    note_send.tick(now=NOW)
    payload = world["rendered"][0]
    assert payload["kind"] == "reply"
    assert payload["briefing"] == "morning-coffee"
    assert payload["briefing_md"].endswith("morning-coffee.md")
    assert payload["account"]["provider"] == "microsoft"
    assert payload["item"]["counterparty_email"] == "foster@duke.com"
    assert payload["others_moved_today"] == 0


# ── The cap ──────────────────────────────────────────────────────────────────

def test_third_event_folds_and_still_reaches_the_page(world):
    world["inbox"] = [inbound(mid="a", subject="Re: Duke study results"),
                      inbound(mid="b", subject="Re: Duke study results",
                              received="2026-09-09T17:41:00+00:00"),
                      inbound(mid="c", subject="Re: Duke study results",
                              received="2026-09-09T17:42:00+00:00")]
    summary = note_send.tick(now=NOW)
    assert len(summary["fired"]) == 2 and len(summary["folded"]) == 1
    assert len(world["mailed"]) == 2
    text = world["md"].read_text(encoding="utf-8")
    assert text.count("Foster Lin replied") == 3
    # The folded line does not claim a draft that was never made.
    folded = [r for r in ledger(world).values() if r["status"] == "folded"]
    assert len(folded) == 1 and "drafted" not in folded[0]["line"]


def test_the_cap_holds_across_ticks(world):
    world["inbox"] = [inbound(mid="a"), inbound(mid="b")]
    note_send.tick(now=NOW)
    world["inbox"] = [inbound(mid="c")]
    summary = note_send.tick(now=NOW + timedelta(minutes=15))
    assert summary["fired"] == [] and len(summary["folded"]) == 1
    assert len(world["mailed"]) == 2


# ── Holds and skips ──────────────────────────────────────────────────────────

def test_during_a_meeting_nothing_is_written(world):
    world["inbox"] = [inbound()]
    world["calendar"] = [{"title": "Harbor Solar call",
                          "dt_utc": NOW - timedelta(minutes=10), "attendees": []}]
    summary = note_send.tick(now=NOW)
    assert summary["held"] == 1 and world["mailed"] == []
    assert not (world["logs"] / "notes-sent.json").exists()
    assert not (world["logs"] / "notes-state.json").exists()
    assert "## Notes" not in world["md"].read_text(encoding="utf-8")
    # The meeting ends; the same reply is still there and now goes out.
    world["calendar"] = []
    note_send.tick(now=NOW + timedelta(minutes=60))
    assert len(world["mailed"]) == 1


def test_outside_the_window_is_a_no_op(world):
    world["inbox"] = [inbound()]
    two_am = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)          # 2:00 AM PT
    summary = note_send.tick(now=two_am)
    assert summary["skipped"].startswith("outside")
    assert world["mailed"] == [] and not (world["logs"] / "notes-sent.json").exists()


def test_disabled_touches_nothing(world, monkeypatch):
    monkeypatch.setattr(config_loader, "notes_enabled", lambda: False)
    world["inbox"] = [inbound()]
    summary = note_send.tick(now=NOW)
    assert summary["skipped"] == "notes are off"
    assert world["mailed"] == [] and not world["logs"].exists()


# ── Failure leaves the door open ─────────────────────────────────────────────

def test_failed_render_leaves_no_ledger_row_so_the_next_tick_retries(world):
    world["inbox"] = [inbound()]
    world["render_fail"] = True
    note_send.tick(now=NOW)
    assert world["mailed"] == []
    assert all(r["status"] != "sent" for r in ledger(world).values())
    assert world["runs"] and world["runs"][-1][3] == 1
    world["render_fail"] = False
    note_send.tick(now=NOW + timedelta(minutes=15))
    assert len(world["mailed"]) == 1
    assert world["runs"][-1][3] == 0


def test_failed_mail_is_not_recorded_as_sent(world):
    world["inbox"] = [inbound()]
    world["mail_fail"] = True
    note_send.tick(now=NOW)
    assert "Reply drafted" not in world["md"].read_text(encoding="utf-8")
    assert all(r["status"] != "sent" for r in ledger(world).values())


# ── The other two triggers, through the same tick ────────────────────────────

def test_deal_pass_becomes_a_question_not_a_draft(world):
    world["inbox"] = [{"subject": "Northwind add-on LOI", "from": "Ben Roberts",
                       "from_email": "ben@northwind.com", "account": "Outlook",
                       "snippet": "We have decided not to move forward.",
                       "received": "2026-09-09T17:40:00+00:00",
                       "message_id": "AAMk-deal", "internal": False}]
    note_send.tick(now=NOW)
    assert world["mailed"][0][0] == "Note: Northwind add-on reads like a pass"
    line = [r for r in ledger(world).values()][0]["line"]
    assert "reads like a pass on Northwind add-on" in line
    assert "the Note asks whether to mark it" in line


def test_moved_meeting_is_noticed_on_the_second_tick(world):
    world["calendar"] = [{"title": "Harbor Solar call",
                          "dt_utc": NOW + timedelta(hours=3), "attendees": []}]
    note_send.tick(now=NOW)
    assert world["mailed"] == []                       # first sight: a snapshot
    world["calendar"] = [{"title": "Harbor Solar call",
                          "dt_utc": NOW + timedelta(hours=4), "attendees": []}]
    note_send.tick(now=NOW + timedelta(minutes=15))
    assert world["mailed"][0][0] == "Note: Harbor Solar call moved to 10:42 AM PT / 1:42 PM ET"
    assert "moved from" in list(ledger(world).values())[0]["line"]


def test_morning_items_stay_watched_after_afternoon_tea_renders(world, monkeypatch):
    """The 1 PM page is built from checkbox lines with no addresses. The reply
    must still be matched against the morning page, and the Note's line must
    land on the page now on screen (Afternoon Tea)."""
    tea_md = world["md"].parent / "tea.md"
    tea_md.write_text("# Afternoon Tea\n\nOpening.\n\n## BEFORE YOU STOP\n\n- [ ] y\n",
                      encoding="utf-8")
    monkeypatch.setattr(note_send, "_briefing_outputs", lambda: [
        ("morning-coffee", {"date": TODAY, "front_page": [front_item()]}),
        ("afternoon-tea", {"date": TODAY, "front_page": [
            {"subject": "Duke study results", "counterparty_email": "", "account": ""}]}),
    ])
    world["inbox"] = [inbound()]
    note_send.tick(now=NOW)
    assert len(world["mailed"]) == 1
    assert world["rendered"][0]["briefing"] == "afternoon-tea"
    assert "## Notes" in tea_md.read_text(encoding="utf-8")
    assert "## Notes" not in world["md"].read_text(encoding="utf-8")


def test_dry_run_changes_nothing(world):
    world["inbox"] = [inbound()]
    summary = note_send.tick(now=NOW, dry_run=True)
    assert summary["fired"] == ["Note: Foster Lin replied on Duke study results"]
    assert world["mailed"] == [] and world["rendered"] == []
    assert not (world["logs"] / "notes-sent.json").exists()
