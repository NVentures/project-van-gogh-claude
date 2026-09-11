"""interview_capture.py — an interview that survives the session ending.

The append is verified by reading the file back before the next question is
asked, so these tests care about the failure paths: a write that cannot land,
a file that has been replaced by a directory, and a capture that was closed.
"""
import json
import sys
from pathlib import Path

import pytest

import interview_capture as ic

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture
def logs(tmp_path, monkeypatch):
    import config_loader as cl
    monkeypatch.setattr(cl, "logs_dir", lambda: tmp_path)
    return tmp_path


def run(argv):
    return ic.main(argv)


def out(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# ── P17: the CLI ─────────────────────────────────────────────────────────────

def test_start_creates_a_capture_before_the_first_question(logs, capsys):
    assert run(["start", "--skill", "voice-bootstrap", "--goal", "find the voice"]) == 0
    got = out(capsys)
    assert got["ok"] and got["resumable"] is False
    text = Path(got["path"]).read_text(encoding="utf-8")
    assert "status: in_progress" in text and "goal: find the voice" in text


def test_start_on_an_open_capture_resumes_and_creates_nothing(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    first = out(capsys)["path"]
    run(["append", "--skill", "voice-bootstrap", "--question", "Q1", "--answer", "A1"])
    capsys.readouterr()
    before = Path(first).read_bytes()

    assert run(["start", "--skill", "voice-bootstrap", "--goal", "b"]) == 0
    got = out(capsys)
    assert got["resumable"] is True
    assert got["path"] == first and got["entries"] == 1 and got["last_question"] == "Q1"
    assert Path(first).read_bytes() == before
    assert len(list((logs / "captures").glob("*.md"))) == 1


def test_start_force_new_makes_a_second_file_the_same_day(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    first = Path(out(capsys)["path"])
    before = first.read_bytes()
    run(["start", "--skill", "voice-bootstrap", "--goal", "b", "--force-new"])
    second = Path(out(capsys)["path"])
    assert second != first and second.name.endswith("-2.md")
    assert first.read_bytes() == before


def test_append_adds_one_entry_and_reads_it_back(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    capsys.readouterr()
    assert run(["append", "--skill", "voice-bootstrap",
                "--question", "What do you write?", "--answer", "Memos."]) == 0
    got = out(capsys)
    assert got["entries"] == 1
    text = Path(got["path"]).read_text(encoding="utf-8")
    assert "**Question:** What do you write?" in text
    assert "**Answer:** Memos." in text


def test_append_numbers_entries_in_order(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    for i in range(3):
        run(["append", "--skill", "voice-bootstrap", "--question", f"Q{i}",
             "--answer", f"A{i}"])
    got = out(capsys)
    assert got["entries"] == 3
    text = Path(got["path"]).read_text(encoding="utf-8")
    assert text.index("### 1.") < text.index("### 2.") < text.index("### 3.")


def test_append_without_a_capture_fails_clearly(logs, capsys):
    assert run(["append", "--skill", "voice-bootstrap", "--question", "Q",
                "--answer", "A"]) == 2
    assert out(capsys)["error"] == "no_open_capture"


def test_append_reads_an_answer_from_a_file(logs, tmp_path, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    answer = tmp_path / "answer.txt"
    answer.write_text("A long answer from a file.", encoding="utf-8")
    assert run(["append", "--skill", "voice-bootstrap", "--question", "Q",
                "--answer-file", str(answer)]) == 0
    text = Path(out(capsys)["path"]).read_text(encoding="utf-8")
    assert "A long answer from a file." in text


def test_append_fails_when_the_path_became_a_directory(logs, capsys):
    """Runs on every platform. The point is that a write that cannot land is
    reported as a failure rather than as a successful checkpoint."""
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    path = Path(out(capsys)["path"])
    path.unlink()
    path.mkdir()
    assert run(["append", "--skill", "voice-bootstrap", "--question", "Q",
                "--answer", "A"]) == 2
    assert out(capsys)["error"] in ("write_failed", "no_open_capture")


@pytest.mark.skipif(sys.platform == "win32", reason="chmod is a POSIX story")
def test_append_fails_on_a_read_only_file(logs, capsys):
    import os
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    path = Path(out(capsys)["path"])
    os.chmod(path, 0o444)
    try:
        assert run(["append", "--skill", "voice-bootstrap", "--question", "Q",
                    "--answer", "A"]) == 2
        assert out(capsys)["error"] == "write_failed"
    finally:
        os.chmod(path, 0o644)


def test_readback_failure_is_reported(logs, capsys, monkeypatch):
    """If the append reported success but nothing landed, that must be an
    error: the whole value of a checkpoint is being able to trust it."""
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    path = Path(out(capsys)["path"])
    original = Path.read_text

    def lying_read(self, *a, **k):
        text = original(self, *a, **k)
        # Simulate a write that vanished between append and readback.
        return text.split("### 1.")[0] if self == path else text

    monkeypatch.setattr(Path, "read_text", lying_read)
    assert run(["append", "--skill", "voice-bootstrap", "--question", "Q",
                "--answer", "A"]) == 2
    monkeypatch.undo()
    assert out(capsys)["error"] == "readback_failed"


def test_show_returns_the_entries_in_order(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    run(["append", "--skill", "voice-bootstrap", "--question", "Q1", "--answer", "A1"])
    run(["append", "--skill", "voice-bootstrap", "--question", "Q2", "--answer", "A2"])
    capsys.readouterr()
    assert run(["show", "--skill", "voice-bootstrap"]) == 0
    got = out(capsys)
    assert [e["question"] for e in got["log"]] == ["Q1", "Q2"]
    assert [e["answer"] for e in got["log"]] == ["A1", "A2"]


def test_close_flips_the_status_and_stops_further_appends(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    run(["append", "--skill", "voice-bootstrap", "--question", "Q", "--answer", "A"])
    capsys.readouterr()
    assert run(["close", "--skill", "voice-bootstrap", "--status", "complete"]) == 0
    got = out(capsys)
    assert "status: complete" in Path(got["path"]).read_text(encoding="utf-8")
    assert run(["append", "--skill", "voice-bootstrap", "--question", "Q2",
                "--answer", "A2"]) == 2
    assert out(capsys)["error"] == "no_open_capture"


def test_a_paused_capture_is_not_resumed(logs, capsys):
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    first = out(capsys)["path"]
    run(["close", "--skill", "voice-bootstrap", "--status", "paused"])
    capsys.readouterr()
    run(["start", "--skill", "voice-bootstrap", "--goal", "b"])
    got = out(capsys)
    assert got["resumable"] is False and got["path"] != first


def test_a_capture_is_per_interview_not_per_day(logs, capsys):
    """An interview begun last night and continued this morning is one
    interview. A per-day file would ask the user everything over again."""
    run(["start", "--skill", "voice-bootstrap", "--goal", "a"])
    path = Path(out(capsys)["path"])
    renamed = path.with_name("voice-bootstrap-2026-09-01.md")
    path.rename(renamed)
    run(["start", "--skill", "voice-bootstrap", "--goal", "b"])
    got = out(capsys)
    assert got["resumable"] is True and got["path"] == str(renamed)


# ── P18 and P19: the skills call it in the right places ──────────────────────

def _text(skill):
    return (SKILL_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


# The commands appear as `.../interview_capture.py" start`, quote included.
START, APPEND, CLOSE = ('interview_capture.py" start',
                        'interview_capture.py" append',
                        'interview_capture.py" close')


def test_skill_order_voice_bootstrap():
    """The capture opens before the first question and closes after the write,
    with a checkpoint at each of the six answers worth keeping."""
    text = _text("voice-bootstrap")
    assert text.index(START) < text.index("Step 1: Ask for writing samples")
    for step in ("Step 1:", "Step 2:", "Step 4:", "Step 5:", "Step 6:", "Step 7:"):
        marker = text.index(step)
        before = text.rfind("> Checkpoint:", 0, marker)
        assert before != -1, f"{step} has no checkpoint before it"
    assert text.index(CLOSE) > text.index("Step 7")
    assert text.count("> Checkpoint:") >= 6
    assert text.count(APPEND) >= 1


def test_skill_order_update_settings():
    text = _text("update-settings")
    assert text.index(START) < text.index(APPEND) < text.index(CLOSE)
    assert text.index(START) < text.index("## How to run the skill")


def test_skill_order_create_skill():
    text = _text("create-skill")
    assert text.index(START) < text.index(APPEND) < text.index(CLOSE)
    assert text.index(START) < text.index("## Step 1")


@pytest.mark.parametrize("skill", ["voice-bootstrap", "update-settings", "create-skill"])
def test_every_capture_command_has_a_powershell_twin(skill):
    text = _text(skill)
    bash = text.count('"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/interview_capture.py')
    ps = text.count('\\app\\interview_capture.py')
    assert bash >= 1 and ps >= 1, f"{skill}: {bash} bash, {ps} powershell"
