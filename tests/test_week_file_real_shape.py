"""The week file as the Week skill actually writes it, not as a fixture imagines it.

Every other test of the parser invents its own week file, so the whole suite
stayed green while `parse_week_file` returned nothing at all against the real
one: the headings, the task line and the deal line had each drifted, and a
fixture written from the same head as the parser could never catch it.

The strings below are pasted verbatim out of a live
`{vault}/van-gogh/week.md`, only the names changed. They are the hypothesis
under test: if the Week skill's template changes, these must be re-pasted from
a real file, never edited to suit the parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import completion_scan  # noqa: E402
import morning_coffee as mc  # noqa: E402


# Verbatim shape. The task line carries a bolded tag with a trailing colon, a
# due date and a `cb=` ledger id; the deal line uses a colon after the quoted
# subject, not the em dash the old pattern demanded; every heading carries a
# comma and most carry a trailing count.
REAL_WEEK_FILE = """# Week of 2026-09-14

## Calendar

- Mon Sep 15 - Site walk

## Deals, waiting on Dana

- [ ] [Personal] "Re: the press release": Jo Mercer (jo.mercer@example.edu), 38d ago
  Jo Mercer is waiting on the draft.
  -> Send the draft back.

## Inbox, pending (inbound, Dana hasn't replied) - 141

### External

- [ ] [Ridgeline] "Summit USA panel: EMS, SCADA, and who does what.": Northgate Team (contact@northgate-example.com), 6d
  Northgate Team promoting a panel.
  -> RSVP if interested.

## Deals, cold urgent (>14 days)

- [ ] [Ridgeline] "Grid study scope": Lee Park (lee@example.com), 21d cold

## Open Tasks (Obsidian) - 1178

- [ ] **ridgeline:** Reply to Tim Roberts: Tim Roberts confirms a Tuesday call. (due 2026-05-05) <!-- cb=cb-b2fd390b -->
- [ ] **Channel Strategy Expansion (Ana):** Hold the pushed one-on-one with Ana Sandoval. (due 2026-07-15) <!-- cb=cb-54e2c9be -->
- [x] **ridgeline:** Book the Alder site walk (due 2026-07-15) <!-- cb=cb-505cf721 -->
"""


@pytest.fixture()
def real_week(tmp_path, monkeypatch):
    # `DEAL_RE` bakes in the account labels at import, so on a developer
    # machine it carries that person's real labels and on CI it carries the
    # fixture's. Rebuild it here for the two labels this file uses, so the test
    # asserts the SHAPE of the line rather than whose mailbox wrote it.
    import re

    labels = "|".join(re.escape(x) for x in ("Personal", "Ridgeline"))
    monkeypatch.setattr(mc, "DEAL_RE", re.compile(
        rf"^\s*- \[(.)\] \[({labels})\] \"(.+?)\"\s*[:\u2014]\s*(.+?) \(([^)]+@[^)]+)\)"
        rf"(?:\s*[,\u2014]\s*(.*))?$"))
    path = tmp_path / "week.md"
    path.write_text(REAL_WEEK_FILE, encoding="utf-8")
    monkeypatch.setattr(mc, "WORKSPACE_WEEK_MD", str(path))
    return path


# -- the parser sees the file at all -----------------------------------------

def test_the_real_headings_are_recognised(real_week):
    """Not one section parsed before this: every heading had drifted."""
    _lines, deals, tasks, _prio = mc.parse_week_file(str(real_week))
    assert tasks, "the Open Tasks section parsed as empty"
    assert deals, "the deal sections parsed as empty"


def test_a_real_task_line_parses_into_tag_and_text(real_week):
    _lines, _deals, tasks, _prio = mc.parse_week_file(str(real_week))
    by_text = {t["task"]: t for t in tasks}
    got = next((t for t in tasks if "Tim Roberts" in t["task"]), None)
    assert got is not None, f"no Tim Roberts task, parsed: {list(by_text)}"
    assert got["project"] == "ridgeline"
    assert got["checked"] is False
    # The filing metadata belongs to the file, not to the sentence the user reads.
    assert "cb=" not in got["task"]
    assert "(due " not in got["task"]


def test_a_tag_with_spaces_and_parens_survives(real_week):
    _lines, _deals, tasks, _prio = mc.parse_week_file(str(real_week))
    got = next((t for t in tasks if "Ana Sandoval" in t["task"]), None)
    assert got is not None
    assert got["project"] == "Channel Strategy Expansion (Ana)"


def test_a_checked_task_reads_as_checked(real_week):
    _lines, _deals, tasks, _prio = mc.parse_week_file(str(real_week))
    got = next((t for t in tasks if "Alder site walk" in t["task"]), None)
    assert got is not None and got["checked"] is True


def test_a_real_deal_line_parses_with_its_email(real_week):
    _lines, deals, _tasks, _prio = mc.parse_week_file(str(real_week))
    emails = {d["email"] for d in deals}
    assert "jo.mercer@example.edu" in emails
    assert "lee@example.com" in emails


def test_inbox_pending_is_a_section_the_parser_can_see(real_week):
    """The item the user actually ticked came from here."""
    _lines, deals, _tasks, _prio = mc.parse_week_file(str(real_week))
    inbox = [d for d in deals if d["section"] == "inbox"]
    assert inbox, "the Inbox, pending section parsed as empty"
    assert inbox[0]["email"] == "contact@northgate-example.com"


# -- the tick, end to end -----------------------------------------------------

def test_a_real_task_resolves_for_the_tick(real_week):
    got = completion_scan.resolve_front_item("Reply to Tim Roberts")
    assert got is not None, "the tick could not place a task that is in the file"
    assert got["kind"] == "task"
    assert got["tag"] == "ridgeline"


def test_an_inbox_item_resolves_by_its_subject(real_week):
    """The case from the bug report: an inbox line, ticked from the page."""
    got = completion_scan.resolve_front_item(
        "Summit USA panel: EMS, SCADA, and who does what.")
    assert got is not None, "an inbox item still cannot be placed"
    assert got["kind"] == "deal"
    assert got["email"] == "contact@northgate-example.com"


def test_ticking_a_real_task_flips_the_real_line(real_week, monkeypatch):
    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)

    item = completion_scan.resolve_front_item("Reply to Tim Roberts")
    result = completion_scan.mark_done([item])
    assert result["marked"], "nothing was marked"

    text = real_week.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "Tim Roberts" in l)
    assert line.startswith("- [x]"), f"box not flipped: {line}"
    # The write must not damage the rest of the line.
    assert "cb=cb-b2fd390b" in line, "the ledger id was lost by the write"
    assert "(due 2026-05-05)" in line, "the due date was lost by the write"

    again = completion_scan.mark_done([item])
    assert again["marked"] == [], "a second tick must change nothing"


def test_ticking_an_inbox_item_flips_its_line(real_week, monkeypatch):
    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)

    item = completion_scan.resolve_front_item(
        "Summit USA panel: EMS, SCADA, and who does what.")
    completion_scan.mark_done([item])

    text = real_week.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "Summit USA panel" in l)
    assert line.startswith("- [x]"), f"box not flipped: {line}"


def test_the_resolver_still_refuses_what_is_not_there(real_week):
    """Narrowing is the safe direction; matching everything is not a matcher."""
    assert completion_scan.resolve_front_item(
        "Renegotiate the office lease in Lisbon") is None


# -- the tick on a mail item the week file never listed -----------------------

def _stub_sidecar(monkeypatch, rows):
    """Point the page's sidecar reader at a fixed front_page."""
    import workbench_data
    monkeypatch.setattr(workbench_data, "read_sidecar",
                        lambda name: {"front_page": rows})


def test_a_mail_item_absent_from_the_week_file_is_recorded_closed(
        real_week, monkeypatch):
    """The reported bug: a row the briefing showed and the week file never had.

    Before this the tick refused it, and the refusal was correct but useless:
    every mail-derived row offered a checkbox that could not work.
    """
    import workbench_serve as ws

    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)
    _stub_sidecar(monkeypatch, [{
        "subject": "Quarterly hosting invoice",
        "counterparty_email": "billing@example.net",
        "counterparty_name": "Northgate Team",
        "account": "Personal",
    }])

    got = ws._check_off("Quarterly hosting invoice", "morning-coffee")
    assert got["matched"] is True, got

    text = real_week.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "Quarterly hosting invoice" in l)
    assert line.startswith("- [x]"), f"recorded open, not closed: {line}"
    assert "billing@example.net" in line


def test_recording_the_same_item_twice_adds_one_line(real_week, monkeypatch):
    import workbench_serve as ws

    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)
    _stub_sidecar(monkeypatch, [{
        "subject": "Quarterly hosting invoice",
        "counterparty_email": "billing@example.net",
        "counterparty_name": "Northgate Team",
        "account": "Personal",
    }])

    ws._check_off("Quarterly hosting invoice", "morning-coffee")
    second = ws._check_off("Quarterly hosting invoice", "morning-coffee")
    assert second["matched"] is False, "a second tick must not write again"

    text = real_week.read_text(encoding="utf-8")
    assert text.count("Quarterly hosting invoice") == 1
    assert text.count(ws._CLOSED_HEADING) == 1


def test_an_item_the_sidecar_does_not_know_is_still_refused(
        real_week, monkeypatch):
    """No evidence, no write. Absence of a row is not permission to invent one."""
    import workbench_serve as ws

    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)
    _stub_sidecar(monkeypatch, [])

    got = ws._check_off("Something nobody has ever heard of", "morning-coffee")
    assert got["matched"] is False
    assert "Something nobody" not in real_week.read_text(encoding="utf-8")


def test_the_recorded_line_parses_back_as_a_closed_deal(real_week, monkeypatch):
    """What the tick writes must be readable by the parser that reads the file."""
    import workbench_serve as ws

    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)
    _stub_sidecar(monkeypatch, [{
        "subject": "Quarterly hosting invoice",
        "counterparty_email": "billing@example.net",
        "counterparty_name": "Northgate Team",
        "account": "Personal",
    }])
    ws._check_off("Quarterly hosting invoice", "morning-coffee")

    # The heading is new, so the parser must not mistake it for an open section.
    _lines, deals, _tasks, _prio = mc.parse_week_file(str(real_week))
    assert all(d["email"] != "billing@example.net" or d["checked"]
               for d in deals), "the recorded line reads back as open"


# -- the failure message has to be readable -----------------------------------

def test_a_mark_on_a_bare_row_is_placed_in_the_text_column():
    """The screenshot bug: a reason rendered one LETTER per line.

    `li.task` is a two-column grid whose first column is 16px. A mark appended
    straight to the <li> (which is what happens on a row with no `.act`
    wrapper, the common case) becomes a third grid item in that 16px column, so
    a 42-character sentence wrapped at one character wide. The rule under test
    puts it in the text's column instead.
    """
    import re

    import briefing_html
    import workbench_serve as ws

    md = "## Waiting on you\n\n- [ ] Some item that can fail to close\n"
    html = briefing_html.render_page(
        md, {"title": "T", "briefing_date": "2026-09-14",
             "briefing": "morning-coffee"})
    row = re.search(r'<li class="task".*?</li>', html, re.S).group(0)
    # The premise: this row really does lack the wrapper the other rule covers.
    assert 'class="act"' not in row, "premise gone: rows now carry .act"

    css = ws._LIVE_CSS.replace(" ", "")
    assert "li.task>.state{grid-column:2" in css, (
        "no rule places a mark appended straight to the li, so a failure "
        "reason renders one character per line")
