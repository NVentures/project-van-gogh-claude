"""meeting_synthesis tests: the transcript read, its fallbacks, and its shape.

The load-bearing property is that EVERY failure degrades to None so the caller
files the provider summary. A synthesis that raises would cost the meeting; a
synthesis that returns a confident empty page over a real transcript would be
worse, because it reads as a quiet meeting.
"""
import subprocess

import meeting_synthesis as ms


def _note(turns=40, title="Q3 Planning"):
    """A note whose transcript is long enough to be a real recording."""
    return {
        "id": "m1",
        "title": title,
        "created_at": "2026-09-15T17:00:00Z",
        "transcript": [
            {"speaker": {"source": "Nobel Chang" if i % 2 else "Jane Smith"},
             "text": f"This is turn {i} of a real conversation about the project."}
            for i in range(turns)
        ],
    }


def _reply(payload, rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=payload, stderr="")


GOOD = """{
  "discussed": "Budget for Q3 and whether to keep the vendor.",
  "decisions": ["Keep the vendor through Q3"],
  "changes": ["Launch moved from October to November"],
  "my_commitments": ["Send the revised budget to Jane Smith by Friday"],
  "their_commitments": ["Jane Smith will confirm headcount"],
  "next_steps": ["Nobel Chang: budget, Friday"],
  "open_questions": ["Who owns renewal?"],
  "subtext": ["Jane Smith hedged on headcount, said 'I guess we could'"]
}"""


def test_reads_transcript_into_eight_sections(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(GOOD))
    out = ms.synthesize(_note(), user_name="Nobel Chang")
    assert out is not None
    for key, _h, _e in ms.SECTIONS:
        assert key in out
    assert out["decisions"] == ["Keep the vendor through Q3"]
    assert out["changes"] == ["Launch moved from October to November"]
    assert out["subtext"]
    assert out["truncated"] is False


def test_prose_wrapped_json_still_parses(monkeypatch):
    """The model prefixes prose despite being told not to, and a fence-strip
    that only handles an opening fence misses the trailing paragraph."""
    wrapped = "Here is the synthesis you asked for:\n```json\n" + GOOD + "\n```\nHope that helps."
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(wrapped))
    out = ms.synthesize(_note(), user_name="Nobel Chang")
    assert out is not None
    assert out["decisions"] == ["Keep the vendor through Q3"]


def test_no_transcript_falls_back(monkeypatch):
    called = []
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda *a, **k: called.append(1) or _reply(GOOD))
    assert ms.synthesize({"id": "m1", "transcript": []}) is None
    # The model is never called when there is nothing to read.
    assert not called


def test_short_transcript_falls_back(monkeypatch):
    """A near-empty transcript is a failed recording, not a short meeting.
    Guessing from nothing is how a confident, wrong page gets written."""
    called = []
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda *a, **k: called.append(1) or _reply(GOOD))
    tiny = {"id": "m1", "transcript": [{"speaker": {"source": "A"}, "text": "hi"}]}
    assert ms.synthesize(tiny) is None
    assert not called


def test_cli_failure_falls_back(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply("", rc=1))
    assert ms.synthesize(_note(), user_name="N") is None


def test_cli_exception_falls_back(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=600)
    monkeypatch.setattr(ms.claude_cli, "run_claude", boom)
    assert ms.synthesize(_note(), user_name="N") is None


def test_unparseable_reply_falls_back(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda *a, **k: _reply("I could not read that transcript."))
    assert ms.synthesize(_note(), user_name="N") is None


def test_all_empty_sections_falls_back(monkeypatch):
    """A valid shape with nothing in it, over a transcript that plainly held
    something, is a failed read wearing a passing costume."""
    empty = ('{"discussed": "", "decisions": [], "changes": [], '
             '"my_commitments": [], "their_commitments": [], "next_steps": [], '
             '"open_questions": [], "subtext": []}')
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(empty))
    assert ms.synthesize(_note(), user_name="N") is None


def test_string_where_array_expected_is_coerced(monkeypatch):
    one = GOOD.replace('["Keep the vendor through Q3"]', '"Keep the vendor through Q3"')
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(one))
    out = ms.synthesize(_note(), user_name="N")
    assert out["decisions"] == ["Keep the vendor through Q3"]


def test_long_transcript_is_truncated_and_says_so(monkeypatch):
    seen = {}
    def capture(prompt, **k):
        seen["prompt"] = prompt
        return _reply(GOOD)
    monkeypatch.setattr(ms.claude_cli, "run_claude", capture)
    huge = {"id": "m1", "title": "T", "created_at": "2026-09-15T17:00:00Z",
            "transcript": [{"speaker": {"source": "A"}, "text": "x" * 1000}
                           for _ in range(600)]}
    out = ms.synthesize(huge, user_name="N")
    assert out["truncated"] is True
    assert "too long" in seen["prompt"]
    assert ms.MAX_TRANSCRIPT_CHARS <= len(seen["prompt"]) < ms.MAX_TRANSCRIPT_CHARS + 5000


def test_prompt_names_the_reader(monkeypatch):
    """'What am I responsible for' is unanswerable without knowing which
    speaker is the reader; getting it backwards puts a counterparty's promise
    on the user's own to-do list."""
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, **k: (seen.__setitem__("p", prompt), _reply(GOOD))[1])
    ms.synthesize(_note(), user_name="Nobel Chang")
    assert "Nobel Chang" in seen["p"]
    assert "The reader is: Nobel Chang" in seen["p"]


def _channel_note():
    """The live Granola shape: channels, not names."""
    note = _note()
    for i, turn in enumerate(note["transcript"]):
        turn["speaker"]["source"] = "microphone" if i % 2 else "speaker"
    return note


def test_prompt_says_which_channel_is_the_reader(monkeypatch):
    """Naming the reader is not enough when no line carries a name.

    Granola labels turns [microphone] and [speaker]. Unexplained, the model
    guessed, and on a live call it swapped the two sides: three things the
    user owed were filed as owed to them.
    """
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, **k: (seen.__setitem__("p", prompt), _reply(GOOD))[1])
    ms.synthesize(_channel_note(), user_name="Nobel Chang")
    head = seen["p"].split("TRANSCRIPT:")[0]
    assert "[microphone] is Nobel Chang's own microphone" in head
    assert "[speaker] is the far side of the call" in head
    assert "never give a [speaker] line to Nobel Chang" in head


def test_named_speakers_get_no_channel_legend(monkeypatch):
    """A provider that names its speakers must not be told about channels."""
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, **k: (seen.__setitem__("p", prompt), _reply(GOOD))[1])
    ms.synthesize(_note(), user_name="Nobel Chang")
    assert "microphone" not in seen["p"]


def test_channel_word_inside_speech_is_not_a_label():
    """Only a line that OPENS with the label counts, not a mention of one."""
    said = "[Jane Smith] Is your [microphone] on? I think the speaker is off."
    assert ms.channel_legend(said, "Nobel Chang") == ""
    assert ms.channel_legend("[speaker] Hello.\n[microphone] Hi.", "Nobel Chang")


def test_pii_is_stripped_before_the_model_sees_it(monkeypatch):
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, **k: (seen.__setitem__("p", prompt), _reply(GOOD))[1])
    note = _note()
    note["transcript"].append(
        {"speaker": {"source": "A"}, "text": "reach me at bob@example.com or 415-555-0142"})
    ms.synthesize(note, user_name="N")
    assert "bob@example.com" not in seen["p"]
    assert "415-555-0142" not in seen["p"]


def test_action_items_are_only_the_users_own(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(GOOD))
    out = ms.synthesize(_note(), user_name="Nobel Chang")
    items = ms.action_items_from(out)
    assert items == ["Send the revised budget to Jane Smith by Friday"]
    # A counterparty's promise must never become the reader's task.
    assert not any("Jane Smith will confirm" in i for i in items)


def test_render_markdown_has_every_heading(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(GOOD))
    md = ms.render_markdown(ms.synthesize(_note(), user_name="N"))
    for _key, heading, _empty in ms.SECTIONS:
        assert f"## {heading}" in md


def test_render_marks_commitments_as_checkboxes(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(GOOD))
    md = ms.render_markdown(ms.synthesize(_note(), user_name="N"))
    assert "- [ ] Send the revised budget to Jane Smith by Friday" in md
    assert "- [ ] Jane Smith will confirm headcount" in md
    # Prose sections stay bullets, never tasks.
    assert "- [ ] Who owns renewal?" not in md
    assert "- Who owns renewal?" in md


def test_render_empty_section_says_so_not_silence(monkeypatch):
    partial = GOOD.replace('["Launch moved from October to November"]', "[]")
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(partial))
    md = ms.render_markdown(ms.synthesize(_note(), user_name="N"))
    assert "## Changes From Previous Plans" in md
    assert "%% No changes from previous plans. %%" in md


def test_render_has_no_dashes(monkeypatch):
    monkeypatch.setattr(ms.claude_cli, "run_claude", lambda *a, **k: _reply(GOOD))
    md = ms.render_markdown(ms.synthesize(_note(), user_name="N"))
    assert "—" not in md and "–" not in md


def test_prompt_forbids_dashes(monkeypatch):
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, **k: (seen.__setitem__("p", prompt), _reply(GOOD))[1])
    ms.synthesize(_note(), user_name="N")
    assert "em dashes" in seen["p"]
    assert "—" not in seen["p"]


def test_synthesis_model_is_not_the_classifier(monkeypatch):
    """Subtext and changed-plans need judgment, which the Haiku classifier
    default does poorly. A silent revert to Haiku would degrade exactly the
    two sections the transcript read exists to produce."""
    seen = {}
    monkeypatch.setattr(ms.claude_cli, "run_claude",
                        lambda prompt, model=None, **k: (seen.__setitem__("m", model),
                                                         _reply(GOOD))[1])
    ms.synthesize(_note(), user_name="N")
    assert seen["m"] == ms.SYNTHESIS_MODEL
    assert seen["m"] != ms.claude_cli.HAIKU_MODEL


# ── Retry and the stated reason ─────────────────────────────────────────────
# One call in a handful came back unusable on a real transcript and the meeting
# silently dropped to the vendor summary. A second call recovers it, and when
# both fail the caller is told why rather than a bare "unavailable".

def _sequence(*replies):
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return replies[min(len(calls), len(replies)) - 1]
    return fake, calls


def test_one_bad_reply_is_retried_and_recovers(monkeypatch):
    fake, calls = _sequence(_reply("not json at all"), _reply(GOOD))
    monkeypatch.setattr(ms.claude_cli, "run_claude", fake)
    out = ms.synthesize(_note(), user_name="N")
    assert out is not None and out["decisions"] == ["Keep the vendor through Q3"]
    assert len(calls) == 2
    assert ms.LAST_FAILURE == ""


def test_two_bad_replies_fall_back_with_a_reason(monkeypatch):
    fake, calls = _sequence(_reply("not json"), _reply("", rc=1))
    monkeypatch.setattr(ms.claude_cli, "run_claude", fake)
    assert ms.synthesize(_note(), user_name="N") is None
    assert len(calls) == ms.SYNTHESIS_ATTEMPTS == 2
    assert "exited 1" in ms.LAST_FAILURE and "tried 2 times" in ms.LAST_FAILURE


def test_missing_transcript_is_not_retried(monkeypatch):
    fake, calls = _sequence(_reply(GOOD))
    monkeypatch.setattr(ms.claude_cli, "run_claude", fake)
    note = _note()
    note["transcript"] = []
    assert ms.synthesize(note, user_name="N") is None
    assert calls == []
    assert "no transcript" in ms.LAST_FAILURE


def test_exception_reason_names_the_type_not_the_text(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret prompt text 123 Main St")
    monkeypatch.setattr(ms.claude_cli, "run_claude", boom)
    assert ms.synthesize(_note(), user_name="N") is None
    assert "RuntimeError" in ms.LAST_FAILURE
    assert "Main St" not in ms.LAST_FAILURE


def test_raw_newline_inside_a_string_still_parses(monkeypatch):
    """A long prose field with a literal line break is invalid strict JSON."""
    broken = GOOD.replace("Budget for Q3 and", "Budget for Q3\nand")
    fake, calls = _sequence(_reply(broken))
    monkeypatch.setattr(ms.claude_cli, "run_claude", fake)
    out = ms.synthesize(_note(), user_name="N")
    assert out is not None and "Budget for Q3" in out["discussed"]
    assert len(calls) == 1
