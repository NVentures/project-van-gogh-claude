"""Meeting prep's live context: the mail and the connectors, wired in.

The lookups are tested where they live. What is tested here is the wiring a
real morning exposed: which people the caps are spent on, that a person nobody
looked up is reported as that and not as someone with no mail, and that a
broken lookup costs its section and never the prep.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import attendee_mail
import connector_lookup
import meeting_prep
import prep_send

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def _event(title, *emails):
    return {"title": title, "time_str": "9:00 AM", "day_str": "Thu Oct 1",
            "source": "Outlook",
            "attendees": [{"name": e.split("@")[0].title(), "email": e} for e in emails]}


@pytest.fixture
def lookups(monkeypatch):
    """Record who is asked about, in order, and answer plainly."""
    asked = {"mail": [], "connectors": []}

    def threads_with(email):
        asked["mail"].append(email)
        return {"checked": True, "threads": [], "last_word": "", "why": ""}

    monkeypatch.setattr(attendee_mail, "threads_with", threads_with)
    monkeypatch.setattr(connector_lookup, "configured", lambda use="people": [])
    monkeypatch.setattr(connector_lookup, "lookup_people",
                        lambda people: asked["connectors"].append(people) or {})
    return asked


def test_the_smallest_meeting_is_looked_up_first(lookups, monkeypatch):
    """A real day: a 13 person call at 8:00 took every lookup and left three
    small outside calls with none."""
    monkeypatch.setattr(meeting_prep, "MAIL_PEOPLE_CAP", 3)
    big = _event("All hands", *[f"p{n}@big.example" for n in range(5)])
    small = _event("Intro", "dana@quarry.example")
    pair = _event("Diligence", "ada@engine.example", "bob@engine.example")
    meeting_prep.live_context([big, pair, small])
    assert lookups["mail"] == ["dana@quarry.example", "ada@engine.example",
                               "bob@engine.example"]


def test_a_person_on_two_calls_is_read_once(lookups):
    meeting_prep.live_context([_event("A", "dana@quarry.example"),
                               _event("B", "dana@quarry.example", "ada@engine.example")])
    assert lookups["mail"] == ["dana@quarry.example", "ada@engine.example"]


def test_a_person_past_the_cap_is_reported_as_not_read(lookups, monkeypatch):
    monkeypatch.setattr(meeting_prep, "MAIL_PEOPLE_CAP", 1)
    event = _event("Pair", "ada@engine.example", "bob@engine.example")
    out = meeting_prep.resolve_event(event, meeting_prep.live_context([event]))
    read, unread = out["attendees"]
    assert read["mail"]["checked"] is True
    assert unread["mail"]["checked"] is False
    assert "only the first 1 people" in unread["mail"]["why"]


def test_without_direct_mail_the_reason_is_carried(lookups):
    event = _event("Intro", "dana@quarry.example")
    live = meeting_prep.live_context([event], mail=False)
    assert not lookups["mail"]
    assert live["mail"]["dana@quarry.example"]["checked"] is False
    assert "not connected" in live["mail"]["dana@quarry.example"]["why"]


def test_connectors_are_asked_only_when_one_is_set_up(lookups, monkeypatch):
    event = _event("Intro", "dana@quarry.example")
    meeting_prep.live_context([event])
    assert lookups["connectors"] == []
    monkeypatch.setattr(connector_lookup, "configured", lambda use="people": [
        {"name": "crm", "about": "contacts", "server": "s", "tools": ["get_x"]}])
    monkeypatch.setattr(connector_lookup, "lookup_people", lambda people: {
        "dana@quarry.example": [{"name": "crm", "status": "nothing", "findings": []}]})
    live = meeting_prep.live_context([event])
    assert live["configured"] == [{"name": "crm", "about": "contacts"}]
    out = meeting_prep.resolve_event(event, live)
    assert out["attendees"][0]["connectors"][0]["status"] == "nothing"


def test_a_lookup_that_blows_up_costs_its_section_not_the_prep(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("token ya29.SECRET")

    monkeypatch.setattr(attendee_mail, "threads_with", boom)
    monkeypatch.setattr(connector_lookup, "configured", boom)
    event = _event("Intro", "dana@quarry.example")
    live = meeting_prep.live_context([event])
    out = meeting_prep.resolve_event(event, live)
    assert out["attendees"][0]["mail"]["checked"] is False
    assert out["attendees"][0]["connectors"] == []
    assert "SECRET" not in capsys.readouterr().err


def test_a_meeting_with_nobody_outside_asks_nothing(lookups):
    live = meeting_prep.live_context([_event("Focus block")])
    assert live == {"mail": {}, "connectors": {}, "configured": []}
    assert not lookups["mail"]


def test_resolving_without_live_context_still_carries_both_keys():
    out = meeting_prep.resolve_event(_event("Intro", "dana@quarry.example"))
    attendee = out["attendees"][0]
    assert attendee["mail"]["checked"] is False and attendee["connectors"] == []


# ── The prep is kept in the vault ────────────────────────────────────────────

def test_a_mailed_prep_is_filed_and_only_after_the_send(monkeypatch, tmp_path):
    target = tmp_path / "van-gogh" / "meeting-prep.md"
    monkeypatch.setattr(prep_send.config_loader, "meeting_prep_md_path", lambda: target)
    monkeypatch.setattr(prep_send, "_events", lambda: [])
    monkeypatch.setattr(prep_send.prep_email, "daily_due", lambda rows=None: True)
    monkeypatch.setattr(prep_send.prep_email, "todays_events",
                        lambda events, now=None: [{"title": "Intro"}])
    monkeypatch.setattr(prep_send.prep_email, "_write_ledger", lambda rows: None)
    monkeypatch.setattr(prep_send, "render_with_retry", lambda arg, claude=None: "## Today\n")
    order = []

    def mail(subject, body):
        order.append("mail")
        assert not target.exists()

    monkeypatch.setattr(prep_send, "mail", mail)
    prep_send.run_daily({})
    assert order == ["mail"]
    assert target.read_text(encoding="utf-8") == "## Today\n"


def test_a_prep_that_was_not_sent_is_not_filed(monkeypatch, tmp_path):
    target = tmp_path / "meeting-prep.md"
    monkeypatch.setattr(prep_send.config_loader, "meeting_prep_md_path", lambda: target)
    monkeypatch.setattr(prep_send, "_events", lambda: [])
    monkeypatch.setattr(prep_send.prep_email, "daily_due", lambda rows=None: True)
    monkeypatch.setattr(prep_send.prep_email, "todays_events",
                        lambda events, now=None: [{"title": "Intro"}])
    monkeypatch.setattr(prep_send, "render_with_retry", lambda arg, claude=None: "body")

    def mail(subject, body):
        raise RuntimeError("no sender")

    monkeypatch.setattr(prep_send, "mail", mail)
    prep_send.run_daily({})
    assert not target.exists()


def test_filing_fails_open(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(prep_send.config_loader, "meeting_prep_md_path",
                        lambda: blocker / "under-a-file" / "meeting-prep.md")
    assert prep_send.file_prep("body") is False


# ── The skill reads what the script writes ───────────────────────────────────

def test_the_prep_skill_reports_every_key_the_script_emits():
    text = (SKILLS / "meeting-prep" / "SKILL.md").read_text(encoding="utf-8")
    for key in ("mail.threads", "last_from", "accounts_failed", "`checked` false",
                "findings[].text", "`nothing`", "--today"):
        assert key in text, f"the skill never mentions {key}"
    assert "must never be replaced by it" in text      # not read vs nothing there
    out = meeting_prep.resolve_event(_event("Intro", "dana@quarry.example"))
    assert {"mail", "connectors"} <= set(out["attendees"][0])


def test_the_connector_skill_only_uses_commands_that_exist():
    text = (SKILLS / "add-connector" / "SKILL.md").read_text(encoding="utf-8")
    lookup_flags = set(re.findall(r"connector_lookup\.py\"? (--[a-z]+)", text))
    assert lookup_flags == {"--list", "--add", "--test", "--remove"}
    for flag in lookup_flags | {"--server", "--tools", "--about", "--name", "--email"}:
        assert connector_lookup.main.__code__ and flag in \
            Path(connector_lookup.__file__).read_text(encoding="utf-8")
    assert "connector_fetch.py\" --list --json" in text
    assert "connector_fetch.py\" --tools SERVER --json" in text


# ── Found by the first real render ───────────────────────────────────────────

def test_outside_people_are_looked_up_before_colleagues(lookups, monkeypatch):
    """Smallest first sent the lookups to teammates on two out-of-office
    blocks, and the outside firm on the big call was never read."""
    monkeypatch.setattr(meeting_prep, "MAIL_PEOPLE_CAP", 2)
    monkeypatch.setattr(meeting_prep, "USER_EMAILS", {"me@ownco.example", "me@gmail.com"})
    ooo = _event("Out of office", "teammate@ownco.example")
    big = _event("Monthly call", "t2@ownco.example", "t3@ownco.example",
                 "rachel@advisor.example", "friend@gmail.com")
    meeting_prep.live_context([ooo, big])
    assert lookups["mail"] == ["rachel@advisor.example", "friend@gmail.com"]


def test_sharing_a_mail_provider_does_not_make_someone_a_colleague(monkeypatch):
    monkeypatch.setattr(meeting_prep, "USER_EMAILS", {"me@gmail.com", "me@ownco.example"})
    assert meeting_prep._is_own_domain("x@ownco.example")
    assert not meeting_prep._is_own_domain("x@gmail.com")
    assert not meeting_prep._is_own_domain("x@other.example")
    assert not meeting_prep._is_own_domain("")


def test_the_brief_is_cut_at_its_end_mark_and_at_its_first_heading():
    from prep_email import END_MARK, trim_brief
    body = ("Here is the brief.\n\n## Today's calls\n\nOne call.\n\n"
            f"{END_MARK}\n\nThree defects showed up in this run.\nBrief is complete.")
    assert trim_brief(body) == "## Today's calls\n\nOne call."
    # No marker: the tail is kept, because cutting a real brief short is worse.
    assert trim_brief("## Today's calls\n\nOne call.\n\nA closing line.") == \
        "## Today's calls\n\nOne call.\n\nA closing line."
    # A marker at the very top never produces an empty email.
    assert trim_brief(f"{END_MARK}\nNothing today.") == "Nothing today."
    assert trim_brief(f"## Brief\n\nBody.\n\n`{END_MARK}`\nstray") == "## Brief\n\nBody."
    assert trim_brief("") == ""


def test_a_render_is_trimmed_before_it_is_mailed(monkeypatch):
    class _Done:
        returncode = 0
        stdout = "## Brief\n\nBody.\n\nEND OF BRIEF\n\nrun notes that must not be mailed\n"
        stderr = ""

    monkeypatch.setattr(prep_send.subprocess, "run", lambda *a, **k: _Done())
    assert prep_send.render_prep("--today", claude="claude") == "## Brief\n\nBody."


def test_the_script_tells_the_skill_when_nobody_is_there(monkeypatch):
    import config_loader
    monkeypatch.setenv("VAN_GOGH_UNATTENDED", "1")
    assert config_loader.resolved_meta()["unattended"] is True
    monkeypatch.delenv("VAN_GOGH_UNATTENDED")
    assert config_loader.resolved_meta()["unattended"] is False
    text = (SKILLS / "meeting-prep" / "SKILL.md").read_text(encoding="utf-8")
    from prep_email import END_MARK
    assert "`meta.unattended`" in text and END_MARK in text


def test_a_day_with_no_calls_is_marked_done_on_disk(monkeypatch, tmp_path):
    """The mark was returned and never written, so every later tick read the
    calendar again."""
    ledger = tmp_path / "prep_ledger.json"
    monkeypatch.setattr(prep_send.prep_email, "ledger_path", lambda: ledger)
    monkeypatch.setattr(prep_send, "_events", lambda: [])
    # Due from midnight, so the test does not depend on the hour it runs at.
    monkeypatch.setattr(prep_send.config_loader, "prep_daily_time", lambda: "00:00")
    reads = []
    monkeypatch.setattr(prep_send.prep_email, "todays_events",
                        lambda events, now=None: reads.append(1) or [])
    prep_send.run_daily(prep_send.prep_email._read_ledger())
    assert ledger.exists()
    prep_send.run_daily(prep_send.prep_email._read_ledger())
    assert len(reads) == 1


def test_a_person_with_no_filed_meeting_is_a_first_meeting(monkeypatch):
    event = _event("Intro", "dana@quarry.example")
    assert meeting_prep.resolve_event(event)["attendees"][0]["first_meeting"] is True
    monkeypatch.setattr(meeting_prep, "find_meeting_sources",
                        lambda name: ["/vault/wiki/sources/Dana - 2026-09-01.md"])
    monkeypatch.setattr(meeting_prep, "extract_action_items", lambda src: [])
    assert meeting_prep.resolve_event(event)["attendees"][0]["first_meeting"] is False


def test_the_skill_states_a_first_meeting_once_and_drops_empty_sections():
    text = (SKILLS / "meeting-prep" / "SKILL.md").read_text(encoding="utf-8")
    assert "`first_meeting`" in text and "**[Name]**: First meeting." in text
    assert "A section with nothing to report is left out." in text
    assert 'Never write "no entity page" about anyone' in text
    assert "No entity page:" not in text
    assert "leave the whole section out" in text
    assert "holds nothing on [Name]" not in text
    assert "was not read" in text.split("Three sentences are never dropped", 1)[1][:400]


def test_morning_coffee_uses_the_same_words_for_a_first_meeting():
    text = (SKILLS / "morning-coffee" / "SKILL.md").read_text(encoding="utf-8")
    assert "`first_meeting`" in text and 'say "First meeting." once' in text
    assert 'say "No prior history in vault."' not in text
