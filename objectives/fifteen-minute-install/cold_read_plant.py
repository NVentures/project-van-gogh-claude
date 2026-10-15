#!/usr/bin/env python3
"""cold_read_plant.py: render the install's user-visible text, and plant two
defects into a copy for the cold read's control (P20).

    .venv/bin/python objectives/fifteen-minute-install/cold_read_plant.py --render
    .venv/bin/python objectives/fifteen-minute-install/cold_read_plant.py --write-md
    .venv/bin/python objectives/fifteen-minute-install/cold_read_plant.py --plant OUT.md

`--render` prints every prompt, wait line, error line, closing text and nudge
line by calling the orchestrator's own functions, so the evaluator grades
what the code emits, not a hand copy. The client in it is invented and
matches the sample pre-call checklist (Jane Doe, Acme Energy); no real name
or path may appear. `--write-md` puts that render into cold_read.md between
its markers (a test fails when the two drift). `--plant` writes a copy of
cold_read.md carrying one contradiction and one unactionable line, and the
answer key beside it as `<OUT>.answers.json`; the fresh agent sees only OUT.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "app"))

SETUP_LINE_MAC = (
    "claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; "
    "claude plugin marketplace update van-gogh; "
    "sh ~/.claude/plugins/marketplaces/van-gogh/setup.sh")
SETUP_LINE_WINDOWS = (
    "claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git; "
    "claude plugin marketplace update van-gogh; "
    'powershell -ExecutionPolicy Bypass -File "$HOME\\.claude\\plugins\\marketplaces\\van-gogh\\setup.ps1"')

# The invented client, the same one the sample pre-call checklist shows.
CLIENT = {"full_name": "Jane Doe", "first_name": "Jane", "company": "Acme Energy",
          "timezone": "America/New_York", "vault_path": "/Users/jane/van-gogh-vault",
          "accounts": ["jane@acme-energy.example"]}
SUPPORT = "team@example.com"
SAMPLE_DEALS = [(0, {"heading": "Harbor Point solar PPA", "name": "Dana Reyes", "business": "acme-energy",
                     "stage": "negotiating", "last_contact": "2026-09-28"}),
                (1, {"heading": "Northwind storage lease", "name": "Lee Park", "business": "acme-energy",
                     "stage": "proposal", "last_contact": "2026-09-20"})]

BEGIN = "<!-- render:begin (written by cold_read_plant.py --write-md, do not edit by hand) -->"
END = "<!-- render:end -->"
CONTRADICTION = ("Keep this window open until tomorrow's email has arrived; "
                 "closing it cancels the install.")
UNACTIONABLE = ("Open the Van Gogh admin console and approve your own install "
                "before continuing.")


def _prompt_lines(spec: dict) -> list:
    out = [f"## {spec['id']} (header: {spec['header']})"]
    if spec.get("show"):
        out += ["Shown before the question:", spec["show"], ""]
    out.append(spec["question"])
    out += [f"- {o['label']}: {o['description']}" for o in spec["options"]]
    out.append("")
    return out


def render() -> str:
    """The whole user-visible text. Leaves the process as it found it."""
    import setup_flow as sf
    import setup_state as ss
    saved_env = {k: os.environ.get(k) for k in ("VAN_GOGH_STATE_DIR", "VAN_GOGH_SETUP_LOGS")}
    saved_now = sf.NOW
    state = tempfile.mkdtemp(prefix="cold-read-")
    os.environ["VAN_GOGH_STATE_DIR"] = state
    os.environ["VAN_GOGH_SETUP_LOGS"] = os.path.join(state, "logs")
    sf.NOW = lambda: datetime(2026, 10, 1, 14, 45, tzinfo=ZoneInfo("America/New_York"))
    try:
        return _render(sf, ss)
    finally:
        sf.NOW = saved_now
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _render(sf, ss) -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert SETUP_LINE_MAC in readme and SETUP_LINE_WINDOWS in readme, "setup line drifted from the README"
    out = ["# The setup line (on the pre-call checklist, under \"The setup line\")", "",
           "Mac, in Terminal:", SETUP_LINE_MAC, "",
           "Windows, in PowerShell:", SETUP_LINE_WINDOWS, "",
           "# The five prompts, in the order they are asked", ""]
    out += _prompt_lines(sf.notetaker_prompt_spec())
    out += _prompt_lines(sf.confirm_prompt_spec(CLIENT))
    out += _prompt_lines(sf.schedule_prompt_spec())
    out += _prompt_lines(sf.priorities_prompt_spec(CLIENT["company"]))
    out += _prompt_lines(sf.deals_prompt_spec(SAMPLE_DEALS))
    out += ["# Asked only when the signed-in address differs from the key file", ""]
    out += _prompt_lines(sf.mismatch_prompt_spec("jane@gmail.example", CLIENT["accounts"][0]))
    out += ["# Asked only when no browser could open on this computer", ""]
    out += _prompt_lines(sf.signin_link_prompt_spec("google", {
        "auth_url": "https://accounts.google.com/o/oauth2/auth?client_id=EXAMPLE&state=EXAMPLE"}))
    out += _prompt_lines(sf.signin_link_prompt_spec("microsoft", {
        "verification_uri": "https://microsoft.com/devicelogin", "user_code": "ABCD-EFGH"}))
    out += ["# When a prompt is asked again, the reason leads the question", ""]
    samples = {"name": "Granola", "what": "your timezone", "answer": "Springfield",
               "path": CLIENT["vault_path"]}
    for key, template in sf.REASONS.items():
        if key == "missing_many":
            out.append("- " + sf._missing_reason(["first_name", "timezone"]))
        else:
            out.append("- " + template.format(**samples))
    out += ["", "# Wait and dispatch lines", "", sf.SAY_SCAN, sf.SAY_MEETINGS, sf.SAY_BRIEFINGS, "",
            "# Error lines", ""]
    for line in sf.all_error_lines(SUPPORT):
        assert len(line) <= 120, (len(line), line)
        assert sf.RESUME not in line or f"in Claude, type {sf.RESUME}" in line, line
        assert "Terminal" not in line or "Terminal (PowerShell on Windows)" in line, line
        out.append(f"- {line}")

    def checklist(**statuses):
        data = ss.new("cold-read", "darwin", "0.81.0")
        data["answers"] = {"keyfile": {"accounts": [], "client": dict(CLIENT), "support_email": SUPPORT}}
        for key in ss.KEYS:
            ss.set_status(data, key, ss.DONE)
        for key, (status, detail) in statuses.items():
            ss.set_status(data, key, status, detail)
        return data

    on = {"digest": {"enabled": True, "briefings": {"morning-coffee": {"time": "07:00"}}},
          "support": {"team_email": SUPPORT}}
    off = {"digest": {"enabled": False}, "support": {"team_email": SUPPORT}}
    out += ["", "# Closing text", "", "## A complete run",
            sf.closing_text(checklist(), on, total="14:45"), "",
            "## The client answered \"Schedule nothing\"",
            sf.closing_text(checklist(jobs=(ss.SKIPPED, "no background jobs, by choice")), off, total="12:10"), "",
            "## The client ticked \"None of these\" and has no notetaker",
            sf.closing_text(checklist(deals=(ss.SKIPPED, "none ticked"),
                                      meetings=(ss.NOT_APPLICABLE, "no notetaker")), on, total="11:30"), ""]
    long_one = checklist(meetings=(ss.DONE, sf.meetings_detail(10, 0, 1)))
    long_one["answers"]["meetings_summary_only"] = 1
    long_two = checklist(meetings=(ss.DONE, sf.meetings_detail(10, 0, 2)),
                         jobs=(ss.SKIPPED, "no background jobs, by choice"))
    long_two["answers"]["meetings_summary_only"] = 2
    out += ["## One meeting was too long to read during the install",
            sf.closing_text(long_one, on, total="13:20"), "",
            "## Two were, for a client who answered \"Schedule nothing\"",
            sf.closing_text(long_two, off, total="12:40"), ""]
    capped = checklist(meetings=(ss.PENDING, "6 filed, 4 waiting"))
    capped["degraded"] = {"box": "meetings", "reason": "6 filed, 4 waiting", "filed": 6, "waiting": 4}
    capped_no_jobs = checklist(meetings=(ss.PENDING, "6 filed, 4 waiting"),
                               jobs=(ss.SKIPPED, "no background jobs, by choice"))
    capped_no_jobs["degraded"] = {**capped["degraded"], "reset": "3:40 PM"}
    capped_scan = checklist(deals=(ss.PENDING, "usage cap during the mail scan"))
    capped_scan["degraded"] = {"box": "deals", "reason": "usage cap during the mail scan"}
    out += ["## The usage limit was reached while meetings were being filed",
            sf.closing_text(capped, on), "",
            "## The same, for a client who answered \"Schedule nothing\"",
            sf.closing_text(capped_no_jobs, off), "",
            "## The usage limit was reached while the mail was being read, with no reset time given",
            sf.closing_text(capped_scan, on), "",
            "## Run again on a finished install", sf.ALREADY_INSTALLED, "",
            "# The reminder line (session start, any Van Gogh command, the reminder email)", ""]
    for key in ss.KEYS:
        data = ss.new("cold-read")
        for k in ss.KEYS:
            if k == key:
                break
            ss.set_status(data, k, ss.DONE)
        line = ss.one_line(data)
        assert len(line) <= 120, line
        out.append(line)
    text = "\n".join(out) + "\n"
    # Only the invented client: no path of whoever ran this, on any machine.
    assert str(Path.home()) not in text and Path.home().name not in text
    assert set(re.findall(r"/Users/([A-Za-z0-9_.]+)", text)) <= {"jane"}
    return text


def write_md() -> Path:
    path = HERE / "cold_read.md"
    text = path.read_text(encoding="utf-8")
    a, b = text.index(BEGIN) + len(BEGIN), text.index(END)
    path.write_text(text[:a] + "\n```text\n" + render() + "```\n" + text[b:], encoding="utf-8")
    return path


def rendered_block(text: str) -> str:
    a, b = text.index(BEGIN) + len(BEGIN), text.index(END)
    return text[a:b].strip().removeprefix("```text").removesuffix("```").lstrip("\n")


def plant(out_path: Path) -> dict:
    text = (HERE / "cold_read.md").read_text(encoding="utf-8")
    anchor_a = "You can close this window.\n"
    anchor_b = "\n\n# Closing text"
    assert text.count(anchor_a) >= 1 and text.count(anchor_b) == 1
    planted = text.replace(anchor_a, f"You can close this window.\n{CONTRADICTION}\n", 1)
    planted = planted.replace(anchor_b, f"\n- {UNACTIONABLE}{anchor_b}")
    assert planted.count(CONTRADICTION) == 1 and planted.count(UNACTIONABLE) == 1
    out_path.write_text(planted, encoding="utf-8")
    answers = {"contradiction": CONTRADICTION,
               "contradicts": "You can close this window.",
               "unactionable": UNACTIONABLE,
               "why": "there is no Van Gogh admin console the client can open"}
    Path(str(out_path) + ".answers.json").write_text(json.dumps(answers, indent=2) + "\n",
                                                     encoding="utf-8")
    return answers


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--write-md", action="store_true")
    ap.add_argument("--plant", default="")
    args = ap.parse_args(argv)
    if args.render:
        sys.stdout.write(render())
        return 0
    if args.write_md:
        print(json.dumps({"ok": True, "written": str(write_md())}))
        return 0
    if args.plant:
        answers = plant(Path(args.plant))
        print(json.dumps({"ok": True, "planted": str(args.plant),
                          "answers": str(args.plant) + ".answers.json",
                          "count": len(answers) - 2}))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
