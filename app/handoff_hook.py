#!/usr/bin/env python3
"""Stop hook: a reply that handed work over must carry the line that says so.

Vincent gets his `Handing to:` line from `vincent.py --handoff <name>` before
he starts a skill or an agent. The independent evaluation of the first build
ran one real hand-off three times: the skill ran every time, and the line
reached the reply the person reads once. A model that has just run a skill
narrates the skill's result and does not go back for a line it was given
several tool calls earlier. The rule in the agent file is necessary and was
not sufficient, so this is the guard.

It reads the session's own transcript, never the model's account of it. When
the turn that is ending ran the hand-off command, and the line that command
printed is not in the last thing the assistant said, the stop is refused once
with the exact line to put at the top. Everything else passes untouched:

- no hand-off command in the turn (which is every session that is not Vincent)
- the line is already there
- this is already the second attempt (`stop_hook_active`), so it can never loop

Stdlib only and no import of the config: it runs at the end of every turn of
every session the plugin is loaded in, and must cost nothing and never fail a
session. Any doubt at all answers "let it stop".

Reads the hook's JSON on stdin. Prints a block decision, or nothing.
"""

from __future__ import annotations

import json
import re
import sys

LINE_RE = re.compile(r"(?m)^Handing to: (\S+)[ \t]*$")
# The tail of a long session is enough: the turn that is ending is at the end.
TAIL_BYTES = 4_000_000


def _text_of(content) -> str:
    """The plain text of a message's content, whichever shape it arrived in."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
        elif isinstance(part, str):
            parts.append(part)
    return "\n".join(parts)


def _is_person(entry: dict) -> bool:
    """A message a person typed, as opposed to a tool result sent back."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    # A skill's own instructions arrive as a user-shaped entry in the middle
    # of the turn, marked as coming from a tool call. Reading that as the
    # person speaking put the start of the turn after the hand-off command,
    # which is how the first version of this hook saw nothing to check.
    if entry.get("isMeta") or entry.get("sourceToolUseID"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return not any(isinstance(p, dict) and p.get("type") == "tool_result"
                       for p in content)
    return False


def missing_line(entries: list, final: str | None = None) -> str:
    """The line this turn was given and did not say, or "" when all is well.

    `final` is the reply that is ending, as the hook's own input carries it
    (`last_assistant_message`). It is graded in place of the transcript's last
    text whenever it is there, because the transcript is written a moment
    behind: in two real runs of eleven the hook read the file before the
    final reply had reached it, found either no text at all or the earlier
    opening, which did carry the line, and let a reply with no line through.
    The lines given are safe to read from the file: they are tool results
    from earlier in the turn.
    """
    start = 0
    for i, entry in enumerate(entries):
        if _is_person(entry):
            start = i
    asked = {}          # tool_use id -> True, for the hand-off command only
    given = []          # every line the command printed this turn, in order
    started = set()     # names a Skill or agent call really started this turn
    last_said = ""
    for entry in entries[start:]:
        if entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "assistant" and isinstance(content, list):
            said = _text_of(content)
            if said.strip():
                last_said = said
            for part in content:
                if (isinstance(part, dict) and part.get("type") == "tool_use"
                        and part.get("name") == "Bash"):
                    command = str((part.get("input") or {}).get("command", ""))
                    if "vincent.py" in command and "--handoff" in command:
                        asked[part.get("id")] = True
                elif isinstance(part, dict) and part.get("type") == "tool_use":
                    inputs = part.get("input") or {}
                    name = str(inputs.get("skill") or inputs.get("subagent_type") or "")
                    if name:
                        started.add(name.lstrip("/"))
        elif entry.get("type") == "user" and isinstance(content, list):
            for part in content:
                if (isinstance(part, dict) and part.get("type") == "tool_result"
                        and asked.get(part.get("tool_use_id"))):
                    m = LINE_RE.search(_text_of(part.get("content")))
                    if m and f"Handing to: {m.group(1)}" not in given:
                        given.append(f"Handing to: {m.group(1)}")
    if final is not None:
        last_said = final
    if not given or not last_said.strip():
        return ""
    said_lines = {ln.strip().strip("*_`> ").rstrip(".") for ln in last_said.splitlines()}
    said_lines |= {ln.replace("**", "").replace("`", "").strip() for ln in last_said.splitlines()}
    # A turn can ask for the line more than once, for different names (a
    # prep that hit trouble and looked at the mechanic). Any one of them in
    # the reply is a reply that said who had the work. Demanding the last one
    # given put a second, different line beside a correct first, and two
    # different lines are no hand-off at all to the code that reads them.
    #
    # But a line for work that really STARTED is owed whatever else was said.
    # Round 4: the prep ran, the turn then asked for the mechanic's line to
    # propose a repair, and the reply carried only that second line. So the
    # lines whose skill or agent was actually started are checked first, each
    # one, and "any one will do" is left for a turn that started nothing.
    def was_started(line: str) -> bool:
        target = line[len("Handing to: "):].lstrip("/")
        return any(s == target or s.endswith(":" + target) for s in started)

    for line in given:
        if was_started(line) and line not in said_lines:
            return line
    if any(line in said_lines for line in given):
        return ""
    return given[0]


def read_entries(path: str) -> list:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        raw = f.read().decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue            # a torn first line of the tail, or a partial write
        if isinstance(entry, dict):
            out.append(entry)
    return out


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if event.get("stop_hook_active"):
            return 0
        path = event.get("transcript_path") or ""
        if not path:
            return 0
        final = event.get("last_assistant_message")
        line = missing_line(read_entries(path),
                            final if isinstance(final, str) else None)
        if line:
            sys.stdout.write(json.dumps({
                "decision": "block",
                "reason": ("Your reply left out the line code gave you. Send the "
                           "reply again in full: your opening first if this is "
                           "your first reply, then this line on a line of its "
                           f"own, exactly:\n\n{line}\n\nthen the result as you "
                           "wrote it. Change nothing else and run nothing."),
            }))
    except Exception:                                            # noqa: BLE001
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
