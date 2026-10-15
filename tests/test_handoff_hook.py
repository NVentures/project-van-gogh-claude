"""The stop hook holds Vincent to the line code gave him, or this fails.

`app/handoff_hook.py` exists because a written rule was not enough: on a real
run the skill started every time and the `Handing to:` line reached the reply
one time in three. The fixtures here are built in the shape of that real
session's transcript (captured, then emptied of everything personal): a typed
message, the hand-off command and its result, the skill's own instructions
arriving as a user-shaped entry in the middle of the turn, thinking blocks,
attachments, and the final text.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import handoff_hook

ROOT = Path(__file__).resolve().parent.parent
LINE = "Handing to: /van-gogh:meeting-prep"
COMMAND = ('"$HOME/.config/van-gogh/venv/bin/python" '
           '"$(cat "$HOME/.config/van-gogh/plugin-root")/app/vincent.py" '
           '--handoff /van-gogh:meeting-prep')


def typed(text):
    return {"type": "user", "isSidechain": False, "promptSource": "sdk",
            "message": {"role": "user", "content": text}}


def use(tool_id, name, **inputs):
    return {"type": "assistant", "isSidechain": False,
            "message": {"content": [{"type": "tool_use", "id": tool_id,
                                     "name": name, "input": inputs}]}}


def result(tool_id, text):
    return {"type": "user", "isSidechain": False,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                     "content": [{"type": "text", "text": text}]}]}}


def said(text):
    return {"type": "assistant", "isSidechain": False,
            "message": {"content": [{"type": "text", "text": text}]}}


def thinking():
    return {"type": "assistant", "isSidechain": False,
            "message": {"content": [{"type": "thinking", "thinking": ""}]}}


def skill_instructions(tool_id):
    """What a skill's own text looks like in the transcript: a user entry."""
    return {"type": "user", "isSidechain": False, "isMeta": True,
            "turnCompanion": True, "sourceToolUseID": tool_id,
            "message": {"content": [{"type": "text",
                                     "text": "Base directory for this skill: /x\n\n# Prep"}]}}


def a_real_hand_off(final_text):
    """One turn, in the order the captured session had it."""
    return [
        {"type": "agent-setting"}, {"type": "attachment", "isSidechain": False},
        typed("prep my next meeting"),
        thinking(),
        use("t1", "Bash", command='".../python" ".../app/vincent.py" --situation --page 1'),
        result("t1", "VINCENT SITUATION, gathered by code. Page 1 of 4."),
        use("t2", "Bash", command=COMMAND),
        result("t2", LINE + "\n\nBegin your reply to the person with the line above."),
        thinking(),
        use("t3", "Skill", skill="van-gogh:meeting-prep"),
        result("t3", "Launching skill: van-gogh:meeting-prep"),
        skill_instructions("t3"),
        use("t4", "Bash", command='".../python" ".../app/meeting_prep.py"'),
        result("t4", "rc=0"),
        said("Data pull finished cleanly; reading the result now."),
        use("t5", "Read", file_path="/x/prep.json"),
        result("t5", "{}"),
        thinking(),
        said(final_text),
        {"type": "last-prompt"},
    ]


def test_a_reply_that_dropped_the_line_is_named():
    turn = a_real_hand_off("## The lender call\nMon 10:00 AM PT / 1:00 PM ET\n...")
    assert handoff_hook.missing_line(turn) == LINE


def test_the_skills_own_instructions_are_not_the_person_speaking():
    """The defect the first version shipped with, found on the real capture:
    the skill's text is a user-shaped entry, and reading it as a new message
    moved the start of the turn past the hand-off command."""
    turn = a_real_hand_off("The brief, with no line.")
    assert any(e.get("isMeta") for e in turn)
    assert not handoff_hook._is_person(skill_instructions("t3"))
    assert handoff_hook._is_person(typed("prep my next meeting"))
    assert not handoff_hook._is_person(result("t1", "x"))
    assert handoff_hook.missing_line(turn) == LINE


@pytest.mark.parametrize("final", [
    "Everything scheduled has run.\n\n" + LINE + "\n\n## The lender call",
    LINE + "\nThe brief.",
    "Opening.\n\n**" + LINE + "**\n\nThe brief.",
    "Opening.\n\n`" + LINE + "`\n\nThe brief.",
    "> " + LINE + "\n\nThe brief.",
])
def test_a_reply_that_carries_the_line_passes(final):
    assert handoff_hook.missing_line(a_real_hand_off(final)) == ""


def test_the_line_inside_a_sentence_is_not_the_line():
    final = "I was told to write " + LINE + " and here is the brief."
    assert handoff_hook.missing_line(a_real_hand_off(final)) == LINE


def test_a_turn_with_no_hand_off_command_is_never_touched():
    turn = [typed("what is on today"), use("t1", "Bash", command="ls"),
            result("t1", "Handing to: /van-gogh:week"), said("Here is your day.")]
    assert handoff_hook.missing_line(turn) == ""


def test_an_earlier_turns_hand_off_is_not_owed_by_a_later_turn():
    turn = a_real_hand_off("Opening.\n\n" + LINE + "\n\nThe brief.")
    turn += [typed("thanks, what time is it in New York"), said("It is 1:00 PM ET.")]
    assert handoff_hook.missing_line(turn) == ""


def test_a_command_that_refused_the_name_owes_nothing():
    turn = [typed("run the bagel poller"), use("t1", "Bash", command=COMMAND),
            result("t1", "Nothing here is called /van-gogh:bagel-poller."),
            said("There is no such skill here.")]
    assert handoff_hook.missing_line(turn) == ""


def _asked_twice(final_text):
    """A turn that asked for the line for two different names, as a real run
    did: meeting prep first, then the mechanic part way through the work."""
    turn = a_real_hand_off(final_text)
    extra = [use("t9", "Bash", command=COMMAND.replace("/van-gogh:meeting-prep",
                                                        "van-gogh-mechanic")),
             result("t9", "Handing to: van-gogh-mechanic\n\nBegin your reply...")]
    return turn[:-3] + extra + turn[-3:]


def test_a_reply_carrying_one_of_two_lines_given_is_left_alone():
    """Round 2 of the evaluation: the reply opened with the correct line, the
    hook demanded the other one, and the result carried two different lines,
    which code reads as no hand-off."""
    assert handoff_hook.missing_line(_asked_twice("Opening.\n\n" + LINE + "\n\nBrief.")) == ""


def test_the_line_for_work_that_really_started_is_owed_whatever_else_was_said():
    """Round 4: the prep ran, the turn then asked for the mechanic's line to
    propose a repair, and the printed reply carried only the mechanic's."""
    only_second = _asked_twice("## The brief\n...\n\nHanding to: van-gogh-mechanic\n\nShall I?")
    assert any(e["message"]["content"][0].get("name") == "Skill"
               for e in only_second if e.get("type") == "assistant"
               and isinstance(e["message"]["content"], list)
               and e["message"]["content"][0].get("type") == "tool_use")
    assert handoff_hook.missing_line(only_second) == LINE
    both = _asked_twice(LINE + "\n\n## The brief\n\nHanding to: van-gogh-mechanic\n\nShall I?")
    assert handoff_hook.missing_line(both) == ""


def test_a_turn_that_started_nothing_may_say_either_line():
    turn = [typed("afternoon tea died, sort it out"),
            use("t1", "Bash", command=COMMAND),
            result("t1", LINE + "\n\nBegin your reply..."),
            use("t2", "Bash", command=COMMAND.replace("/van-gogh:meeting-prep", "van-gogh-mechanic")),
            result("t2", "Handing to: van-gogh-mechanic\n\nBegin your reply..."),
            said("Handing to: van-gogh-mechanic\n\nShall I?")]
    assert handoff_hook.missing_line(turn) == ""


def test_an_agent_started_under_its_plugin_name_counts_as_started():
    turn = [typed("what should I be working on"),
            use("t1", "Bash", command=COMMAND.replace("/van-gogh:meeting-prep", "advisor")),
            result("t1", "Handing to: advisor\n\nBegin your reply..."),
            use("t2", "Task", subagent_type="van-gogh:advisor", prompt="look"),
            result("t2", "Three things."),
            said("Three things are worth doing.")]
    assert handoff_hook.missing_line(turn) == "Handing to: advisor"


def test_a_reply_carrying_neither_is_asked_for_the_first_one_given():
    assert handoff_hook.missing_line(_asked_twice("The brief, no line.")) == LINE


def test_work_inside_a_subagent_is_ignored():
    turn = a_real_hand_off("Opening.\n\n" + LINE + "\n\nThe brief.")
    side = said("A subagent's last words, with no line.")
    side["isSidechain"] = True
    assert handoff_hook.missing_line(turn + [side]) == ""


def test_the_reply_that_is_ending_is_graded_not_the_file_a_moment_behind():
    """Round 3 of the evaluation, 2 runs in 11: the hook ran before the final
    reply reached the transcript. The file then ended either with no text at
    all, or with the opening, which carried the line. Both let a final reply
    with no line through. The hook's own input holds the reply that is
    ending, and that is what is graded."""
    whole = a_real_hand_off("## The lender call\nThe brief, with no line.")
    cut = [e for e in whole[:-2]]                # the final text is not on disk yet
    def is_said(e):
        content = (e.get("message") or {}).get("content")
        return (e.get("type") == "assistant" and isinstance(content, list)
                and any(p.get("type") == "text" for p in content))

    cut = [e for e in cut if not is_said(e)]
    no_text_yet = cut
    assert handoff_hook.missing_line(no_text_yet) == "", "the file alone shows nothing to grade"
    assert handoff_hook.missing_line(no_text_yet, "## The lender call\nNo line.") == LINE

    opened_with_it = cut[:8] + [said("Everything ran.\n\n" + LINE)] + cut[8:]
    assert handoff_hook.missing_line(opened_with_it) == "", "the stale file looks fine"
    assert handoff_hook.missing_line(opened_with_it, "## The lender call\nNo line.") == LINE
    assert handoff_hook.missing_line(opened_with_it, "Everything ran.\n\n" + LINE + "\n\nBrief.") == ""
    assert handoff_hook.missing_line(whole, "") == "", "an empty final reply is not graded"


def test_the_hook_reads_the_ending_reply_from_its_own_input(tmp_path):
    stale = a_real_hand_off("Opening.\n\n" + LINE)       # the file says all is well
    res = _run_hook(tmp_path, stale, final="## The lender call\nNo line here.")
    assert json.loads(res.stdout)["decision"] == "block"
    res = _run_hook(tmp_path, a_real_hand_off("No line on disk."),
                    final="Opening.\n\n" + LINE + "\n\nThe brief.")
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


def _run_hook(tmp_path, entries, active=False, raw=None, final=None):
    transcript = tmp_path / "session.jsonl"
    body = raw if raw is not None else "\n".join(json.dumps(e) for e in entries) + "\n"
    transcript.write_text(body, encoding="utf-8")
    payload = {"transcript_path": str(transcript), "stop_hook_active": active}
    if final is not None:
        payload["last_assistant_message"] = final
    event = json.dumps(payload)
    return subprocess.run([sys.executable, str(ROOT / "app" / "handoff_hook.py")],
                          input=event, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


def test_the_hook_refuses_the_stop_once_and_gives_the_exact_line(tmp_path):
    res = _run_hook(tmp_path, a_real_hand_off("The brief, with no line."))
    assert res.returncode == 0 and res.stderr == ""
    out = json.loads(res.stdout)
    assert out["decision"] == "block"
    assert f"\n\n{LINE}\n\n" in out["reason"]
    assert "run nothing" in out["reason"]
    assert chr(0x2014) not in out["reason"] and chr(0x2013) not in out["reason"]


def test_the_second_attempt_is_always_let_through(tmp_path):
    res = _run_hook(tmp_path, a_real_hand_off("Still no line."), active=True)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


def test_a_good_reply_costs_nothing(tmp_path):
    res = _run_hook(tmp_path, a_real_hand_off("Opening.\n\n" + LINE + "\n\nBrief."))
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


@pytest.mark.parametrize("raw", ["", "not json at all\n{broken", '["a list"]\n'])
def test_a_transcript_it_cannot_read_never_fails_the_session(tmp_path, raw):
    res = _run_hook(tmp_path, [], raw=raw)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


def test_no_transcript_and_no_input_never_fail_the_session():
    for event in ("", "{}", '{"transcript_path": "/does/not/exist.jsonl"}', "garbage"):
        res = subprocess.run([sys.executable, str(ROOT / "app" / "handoff_hook.py")],
                             input=event, capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        assert (res.returncode, res.stdout, res.stderr) == (0, "", ""), event


def test_a_torn_first_line_of_a_long_transcript_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "long.jsonl"
    filler = json.dumps(typed("x" * 200)) + "\n"
    path.write_text(filler * 50 + "\n".join(
        json.dumps(e) for e in a_real_hand_off("No line.")) + "\n", encoding="utf-8")
    monkeypatch.setattr(handoff_hook, "TAIL_BYTES", 6000)
    assert handoff_hook.missing_line(handoff_hook.read_entries(str(path))) == LINE


def test_the_hook_is_registered_and_its_wrapper_gates_before_python():
    doc = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    stop = doc["hooks"]["Stop"]
    assert len(stop) == 1 and len(stop[0]["hooks"]) == 1
    hook = stop[0]["hooks"][0]
    assert hook["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/handoff_line.sh"'
    assert hook["shell"] == "bash" and hook["timeout"] <= 10
    wrapper = ROOT / "hooks" / "handoff_line.sh"
    sh = wrapper.read_text(encoding="utf-8")
    if sys.platform != "win32":      # mode bits are not expressible on Windows
        assert os.access(wrapper, os.X_OK)
    first_python = sh.index('"$PY" "$ROOT')
    assert sh.index("stop_hook_active") < first_python
    assert sh.index("grep -q -- '--handoff'") < first_python, \
        "no Python for a session that never handed anything over"
    assert 'STATE="${VAN_GOGH_STATE_DIR:-$HOME/.config/van-gogh}"' in sh
    assert "venv/Scripts/python.exe" in sh, "the Windows venv layout too"
    assert chr(0x2014) not in sh and chr(0x2013) not in sh


@pytest.mark.skipif(sys.platform == "win32", reason="the wrapper is run under bash")
def test_the_wrapper_is_silent_for_a_session_that_is_not_vincent(tmp_path):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps(typed("hello")) + "\n" + json.dumps(said("Hi.")) + "\n",
                          encoding="utf-8")
    env = dict(os.environ, VAN_GOGH_STATE_DIR=str(tmp_path / "no-state"))
    for event in ({"transcript_path": str(transcript), "stop_hook_active": False},
                  {"transcript_path": str(tmp_path / "missing.jsonl")},
                  {"transcript_path": str(transcript), "stop_hook_active": True}):
        res = subprocess.run(["bash", str(ROOT / "hooks" / "handoff_line.sh")],
                             input=json.dumps(event), capture_output=True, text=True,
                             encoding="utf-8", env=env, timeout=30)
        assert (res.returncode, res.stdout, res.stderr) == (0, "", "")
