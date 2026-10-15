"""The intake gate and the check-off keys, wired into the three briefings.

test_intake_gate.py proves the gate. These prove each briefing actually calls
it, at the point the plan names, and that the five keys the skills paste
(`avoided`, `avoided_md`, `closed_today`, `closed_today_md`, `judge_note`)
are in the JSON a real run prints. Afternoon Tea is driven through its real
`main()` rather than by calling the helper, because a helper test never
exercises the output dict.

app/done_judge.py is another module's to write, so its three reads are
monkeypatched here, including the case where the module is missing.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from types import ModuleType, SimpleNamespace

import pytest

import afternoon_tea as tea
import claude_cli
import config_loader
import intake_gate as ig
import morning_coffee as mc
import week_review as wr

STRANGER = "robin.vale@quillfeather-holdings.example"
LURE_TEXT = ("Robin Vale Managing Partner Quillfeather Holdings +1 415 555 0142 "
             "https://forms.office.com/r/Zk3Qx9Lm2a")
CLOSED_ROWS = [{"cb": "cb-1a2b3c4d", "text": "Send the load study to Morgan Ashby",
                "reason": "sent_mail"}]
CLOSED_MD = "Closed today (1)\n- [x] Send the load study to Morgan Ashby (cb-1a2b3c4d)"


class Runner:
    def __init__(self, verdict="suspect"):
        self.verdict, self.prompts = verdict, []

    def __call__(self, prompt, model=None, timeout=None, **_k):
        self.prompts.append(prompt)
        ids = re.findall(r"^--- (m\d+) ---$", prompt, re.M)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"items": [{"id": i, "verdict": self.verdict,
                        "reason": "Only a signature and a form link."} for i in ids]}))


def fake_judge(closed=None, md=CLOSED_MD, note="", raises=()):
    mod = ModuleType("done_judge")

    def closed_today(today):
        if "closed_today" in raises:
            raise RuntimeError("judge broke")
        mod.asked_for = today
        return list(CLOSED_ROWS if closed is None else closed)

    def render_closed_md(rows):
        if "render_closed_md" in raises:
            raise RuntimeError("render broke")
        return md if rows else ""

    def judge_note(today):
        if "judge_note" in raises:
            raise RuntimeError("note broke")
        return note

    mod.closed_today, mod.render_closed_md, mod.judge_note = (
        closed_today, render_closed_md, judge_note)
    return mod


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    monkeypatch.setattr(config_loader, "logs_dir", lambda: logs)
    monkeypatch.setattr(config_loader, "entities_dir", lambda: tmp_path / "entities")
    monkeypatch.setattr(ig, "_fetch_sent_recipients", lambda s, t: (set(), True))
    monkeypatch.setattr(claude_cli, "_spawn",
                        lambda *a, **k: pytest.fail("a real model was spawned"))
    monkeypatch.setitem(sys.modules, "done_judge", fake_judge())
    return tmp_path


# ── closed_today_keys: a missing or broken judge never breaks a briefing ─────

def test_the_three_keys_come_from_the_judge():
    judge = fake_judge(note="The check-off did not run today: quota")
    sys.modules["done_judge"] = judge
    got = wr.closed_today_keys(date(2026, 10, 9))
    assert got == {"closed_today": CLOSED_ROWS, "closed_today_md": CLOSED_MD,
                   "judge_note": "The check-off did not run today: quota"}
    assert judge.asked_for == date(2026, 10, 9)


def test_a_missing_judge_module_falls_back_to_empty(monkeypatch):
    monkeypatch.setitem(sys.modules, "done_judge", None)     # import raises
    assert wr.closed_today_keys(date(2026, 10, 9)) == {
        "closed_today": [], "closed_today_md": "", "judge_note": ""}


@pytest.mark.parametrize("broken,survives", [
    ("closed_today", {"closed_today": [], "closed_today_md": "", "judge_note": "N"}),
    ("render_closed_md", {"closed_today": CLOSED_ROWS, "closed_today_md": "", "judge_note": "N"}),
    ("judge_note", {"closed_today": CLOSED_ROWS, "closed_today_md": CLOSED_MD, "judge_note": ""}),
])
def test_each_read_fails_on_its_own(monkeypatch, broken, survives):
    monkeypatch.setitem(sys.modules, "done_judge", fake_judge(note="N", raises={broken}))
    assert wr.closed_today_keys(date(2026, 10, 9)) == survives


def test_wrong_typed_answers_from_the_judge_are_not_passed_on(monkeypatch):
    mod = fake_judge()
    mod.closed_today = lambda today: {"not": "a list"}
    mod.render_closed_md = lambda rows: None
    mod.judge_note = lambda today: 7
    monkeypatch.setitem(sys.modules, "done_judge", mod)
    assert wr.closed_today_keys(date(2026, 10, 9)) == {
        "closed_today": [], "closed_today_md": "", "judge_note": ""}


# ── week_review ──────────────────────────────────────────────────────────────

def _week_output():
    return {
        "waiting_on_user": [{
            "source": "Gmail", "subject": "Re: Term sheet",
            "counterparty_name": "Sam Rivera", "counterparty_email": "sam@unlisted-firm.example",
            "body_preview": "Sam Rivera https://na4.docusign.net/Signing/abc"}],
        "inbox_pending": [
            {"source": "Outlook", "subject": "Document for your review",
             "counterparty_name": "Robin Vale", "counterparty_email": STRANGER,
             "body_preview": LURE_TEXT[:150], "kill_text": LURE_TEXT},
            {"source": "Gmail", "subject": "Following up",
             "counterparty_name": "Lee Park", "counterparty_email": "lee@cold-thread.example",
             "body_preview": "Lee Park https://na4.docusign.net/Signing/xyz",
             "kill_text": "Lee Park https://na4.docusign.net/Signing/xyz"},
        ],
        "cold_urgent": [{"subject": "Earlier thread", "counterparty_email": "lee@cold-thread.example"}],
        "cold_monitor": [],
        "errors": [],
    }


def test_week_review_holds_the_lure_and_never_asks_about_a_thread_the_user_started(monkeypatch):
    runner = Runner("suspect")
    monkeypatch.setattr(claude_cli, "run_claude", runner)
    output = _week_output()
    wr._run_intake_gate(output, date(2026, 10, 9))
    # The lure left the inbox; the DocuSign reply from someone the user wrote
    # to (a cold thread of theirs) stayed and was never put to the model.
    assert [e["subject"] for e in output["inbox_pending"]] == ["Following up"]
    assert [r["from_email"] for r in output["avoided"]] == [STRANGER]
    assert len(runner.prompts) == 1 and "Lee Park" not in runner.prompts[0]
    assert "Sam Rivera" not in runner.prompts[0]
    # A thread the user started gets its body checks recorded, never unknown_sender.
    assert output["waiting_on_user"][0]["intake_signals"] == [ig.SIGNATURE_ONLY, ig.LURE_LINK]
    assert output["errors"] == []


def test_week_review_keeps_all_mail_when_the_model_is_down(monkeypatch):
    def down(*a, **k):
        raise claude_cli.UsageCap("out of usage")
    monkeypatch.setattr(claude_cli, "run_claude", down)
    output = _week_output()
    wr._run_intake_gate(output, date(2026, 10, 9))
    assert len(output["inbox_pending"]) == 2 and output["avoided"] == []
    assert output["errors"] == ["Lure check did not finish (UsageCap); "
                                "unscreened mail was kept on the page"]


def test_week_review_shows_held_mail_once_and_a_feeding_run_does_not_spend_it(monkeypatch):
    monkeypatch.setattr(claude_cli, "run_claude", Runner("suspect"))

    def render(no_extensions):
        output = _week_output()
        wr._run_intake_gate(output, date(2026, 10, 9))
        wr._attach_intake_and_closed(output, SimpleNamespace(no_extensions=no_extensions))
        return output

    for _ in range(2):                          # feeding Morning Coffee: reported, unmarked
        fed = render(True)
        assert len(fed["avoided"]) == 1 and fed["avoided_md"].startswith("One to avoid: Robin Vale")
    shown = render(False)
    assert len(shown["avoided"]) == 1
    assert shown["closed_today"] == CLOSED_ROWS and shown["closed_today_md"] == CLOSED_MD
    assert shown["judge_note"] == ""
    again = render(False)
    assert again["avoided"] == [] and again["avoided_md"] == ""
    assert [e["subject"] for e in again["inbox_pending"]] == ["Following up"]   # still held


def test_the_kill_scan_can_leave_its_wider_text_for_the_gate():
    output = {"inbox_pending": [{"subject": "S", "kill_text": "wide", "body_preview": "w"}],
              "errors": []}
    wr._run_kill_scan(output, keep_text=True)
    assert output["inbox_pending"][0]["kill_text"] == "wide"
    wr._pop_kill_text(output)
    assert "kill_text" not in output["inbox_pending"][0]
    output["inbox_pending"][0]["kill_text"] = "wide"
    wr._run_kill_scan(output)                   # the default still pops
    assert "kill_text" not in output["inbox_pending"][0]


def test_a_scan_snapshot_carries_held_mail_so_a_reused_scan_can_show_it():
    assert "avoided" in wr.SCAN_SECTIONS
    snap = wr.scan_snapshot({"avoided": [{"key": "k"}]})
    assert snap["sections"]["avoided"] == [{"key": "k"}]
    assert wr.window_sections(snap["sections"], 14, 7)["avoided"] == [{"key": "k"}]


def test_week_review_main_orders_kill_scan_then_gate_then_classify():
    src = open(wr.__file__, encoding="utf-8").read()
    main = src[src.index("\ndef main():"):]
    kill = main.index("_run_kill_scan(output, keep_text=True)")
    gate = main.index("_run_intake_gate(output, today_local.date())")
    pop = main.index("_pop_kill_text(output)")
    classify = main.index("classify_threads(output[\"waiting_on_user\"])")
    assert kill < gate < pop < classify


# ── afternoon_tea: the real main(), Tier 2 input, no network ─────────────────

def _run_tea(monkeypatch, capsys, tmp_path, inbound):
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"sent_today": [], "inbound_today": inbound,
                                   "calendar_tomorrow": []}), encoding="utf-8")
    hot = tmp_path / "hotcache.md"
    hot.write_text("# Hot Cache\n\n## Active Threads\n\n### Quillfeather: site lease\n"
                   "<!-- deal: stage=active last_contact=2026-09-20 -->\n", encoding="utf-8")
    monkeypatch.setattr(tea, "HOTCACHE_PATH", str(hot))
    monkeypatch.setattr(tea, "MORNING_COFFEE_MD", str(tmp_path / "missing.md"))
    monkeypatch.setattr(tea, "fetch_meetings", lambda *a, **k: [])
    monkeypatch.setattr(tea, "sync_hotcache", lambda *a, **k: {})
    monkeypatch.setattr(tea, "collect_nudges", lambda *a, **k: ([], ""))
    monkeypatch.setattr(tea, "travel_enabled", lambda: False)
    monkeypatch.setattr(tea.workbench_data, "write_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(tea.kpi_events, "record_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(tea.extensions, "attach", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["afternoon_tea.py", "--input", str(payload)])
    tea.main()
    return json.loads(capsys.readouterr().out)


def _tea_inbound():
    today = date.today().isoformat()
    return [
        {"subject": "Quillfeather site lease", "from": "Robin Vale", "from_email": STRANGER,
         "account": "Outlook", "snippet": LURE_TEXT, "date": today, "internal": False},
        {"subject": "Invoice question", "from": "Teammate", "from_email": "teammate@company.com",
         "account": "Gmail", "snippet": "Can you look at the invoice total before Friday?",
         "date": today, "internal": False},
    ]


def test_afternoon_tea_main_holds_the_lure_before_it_can_match_a_deal(
        monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(claude_cli, "run_claude", Runner("suspect"))
    monkeypatch.setitem(sys.modules, "done_judge",
                        fake_judge(note="The check-off did not run today: auth"))
    out = _run_tea(monkeypatch, capsys, tmp_path, _tea_inbound())
    # The lure names an open deal in its subject, and it still reached neither
    # the inbound view nor the deal view.
    assert [i["from_email"] for i in out["inbound_today"]] == ["teammate@company.com"]
    assert out["deal_inbound"] == [] and out["inbound_count"] == 1
    assert [r["from_email"] for r in out["avoided"]] == [STRANGER]
    assert out["avoided_md"].splitlines()[0] == (
        f'One to avoid: Robin Vale ({STRANGER}), "Quillfeather site lease". '
        "Only a signature and a form link.")
    assert out["closed_today"] == CLOSED_ROWS and out["closed_today_md"] == CLOSED_MD
    assert out["judge_note"] == "The check-off did not run today: auth"

    # A second run the same day: still held, and not shown again.
    out = _run_tea(monkeypatch, capsys, tmp_path, _tea_inbound())
    assert [i["from_email"] for i in out["inbound_today"]] == ["teammate@company.com"]
    assert out["avoided"] == [] and out["avoided_md"] == ""


def test_control_afternoon_tea_with_the_gate_off_lists_the_lure_on_its_deal(
        monkeypatch, capsys, tmp_path):
    real = config_loader.cfg()
    monkeypatch.setattr(config_loader, "cfg",
                        lambda: {**real, "intake_gate": {"enabled": False}})
    out = _run_tea(monkeypatch, capsys, tmp_path, _tea_inbound())
    assert out["inbound_count"] == 2
    assert [i["from_email"] for i in out["deal_inbound"]] == [STRANGER]
    assert out["avoided"] == [] and out["avoided_md"] == ""


def test_afternoon_tea_main_with_no_judge_module_still_renders(monkeypatch, capsys, tmp_path):
    monkeypatch.setitem(sys.modules, "done_judge", None)
    out = _run_tea(monkeypatch, capsys, tmp_path, _tea_inbound()[1:])
    assert (out["closed_today"], out["closed_today_md"], out["judge_note"]) == ([], "", "")
    assert out["inbound_count"] == 1


# ── morning_coffee ───────────────────────────────────────────────────────────

def test_morning_coffee_shows_what_the_scan_held_and_marks_it_shown():
    row = {"key": "abc123", "from": "Robin Vale", "from_email": STRANGER,
           "subject": "Document for your review", "reason": "Only a signature and a form link.",
           "signals": [ig.UNKNOWN_SENDER, ig.LURE_LINK]}
    output = {"today": "2026-10-09", "errors": [], "avoided": [], "avoided_md": ""}
    mc._attach_avoided(output, {"avoided": [row]})
    assert output["avoided"] == [row]
    assert output["avoided_md"].startswith("One to avoid: Robin Vale")
    again = {"today": "2026-10-10", "errors": [], "avoided": [], "avoided_md": ""}
    mc._attach_avoided(again, {"avoided": [row]})
    assert again["avoided"] == [] and again["avoided_md"] == "" and again["errors"] == []


def test_morning_coffee_carries_the_three_check_off_keys(monkeypatch):
    judge = fake_judge(note="The check-off did not run today: quota")
    monkeypatch.setitem(sys.modules, "done_judge", judge)
    output = {"today": "2026-10-09", "closed_today": [], "closed_today_md": "", "judge_note": ""}
    mc._attach_closed_today(output)
    assert output["closed_today"] == CLOSED_ROWS and output["closed_today_md"] == CLOSED_MD
    assert output["judge_note"] == "The check-off did not run today: quota"
    assert judge.asked_for == date(2026, 10, 9)


def test_morning_coffee_with_a_broken_judge_keeps_its_empty_keys(monkeypatch):
    monkeypatch.setitem(sys.modules, "done_judge", None)
    output = {"today": "2026-10-09", "closed_today": [], "closed_today_md": "", "judge_note": ""}
    mc._attach_closed_today(output)
    assert (output["closed_today"], output["closed_today_md"], output["judge_note"]) == ([], "", "")
    bad_day = {"today": "not a date", "closed_today": [], "closed_today_md": "", "judge_note": ""}
    mc._attach_closed_today(bad_day)            # never raises
    assert bad_day["closed_today"] == []


@pytest.mark.parametrize("module", [wr, tea, mc])
def test_every_briefing_emits_the_five_keys_on_its_output(module):
    src = open(module.__file__, encoding="utf-8").read()
    for key in ("avoided", "avoided_md", "closed_today", "closed_today_md", "judge_note"):
        assert f'"{key}"' in src, f"{module.__name__} never emits {key}"
