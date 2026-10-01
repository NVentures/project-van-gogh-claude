#!/usr/bin/env python3
"""Checkpoint an interview to disk after every answer.

The voice bootstrap asks for writing samples, reads a year of sent mail,
builds a device inventory, and gets five sentences approved, and until its
very last step none of it exists anywhere but the conversation. A session that
ends early throws all of it away, and the user is asked the same questions
again from the top.

So each answer is appended to a capture file as it is given, and the append is
verified by reading the file back before the next question is asked. A write
that silently did not land is worse than no capture at all, because the whole
point is to be able to trust it after a crash.

    interview_capture.py start  --skill voice-bootstrap --goal "..."
    interview_capture.py append --skill voice-bootstrap --question "..." --answer "..."
    interview_capture.py show   --skill voice-bootstrap
    interview_capture.py close  --skill voice-bootstrap --status complete
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_loader                                            # noqa: E402

QA_HEADING = "## Question and answer log"
_STATUS_RE = re.compile(r"^status:\s*(\S+)\s*$", re.MULTILINE)
_ENTRY_RE = re.compile(r"^### (\d+)\.\s", re.MULTILINE)
_GOAL_RE = re.compile(r"^goal:\s*(.*)$", re.MULTILINE)


def captures_dir() -> Path:
    return config_loader.logs_dir() / "captures"


def _fail(code: str, detail: str = "") -> int:
    print(json.dumps({"ok": False, "error": code, "detail": detail}))
    return 2


def _ok(**fields) -> int:
    print(json.dumps({"ok": True, **fields}, default=str))
    return 0


def find_open(skill: str) -> Path | None:
    """The newest capture for this skill that is still in progress.

    Deliberately not scoped to today: an interview begun last night and
    resumed this morning is the same interview, and a per-day file would ask
    the user everything again.
    """
    directory = captures_dir()
    if not directory.is_dir():
        return None
    best = None
    for path in sorted(directory.glob(f"{skill}-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        m = _STATUS_RE.search(text)
        if m and m.group(1) == "in_progress":
            best = path
    return best


def entry_count(text: str) -> int:
    return len(_ENTRY_RE.findall(text))


def last_question(text: str) -> str:
    hits = re.findall(r"^\*\*Question:\*\*\s*(.+)$", text, re.MULTILINE)
    return hits[-1].strip() if hits else ""


def _new_path(skill: str, today: str) -> Path:
    """An unused filename for today, by exclusive create at write time.

    A second interview on the same day gets `-2`. Two processes racing for the
    same name is why the create is exclusive rather than checked-then-written.
    """
    directory = captures_dir()
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"{skill}-{today}.md"
    if not base.exists():
        return base
    n = 2
    while (directory / f"{skill}-{today}-{n}.md").exists():
        n += 1
    return directory / f"{skill}-{today}-{n}.md"


def cmd_start(args) -> int:
    existing = None if args.force_new else find_open(args.skill)
    if existing is not None:
        text = existing.read_text(encoding="utf-8")
        return _ok(resumable=True, path=str(existing),
                   entries=entry_count(text),
                   last_question=last_question(text),
                   goal=(_GOAL_RE.search(text).group(1).strip()
                         if _GOAL_RE.search(text) else ""))

    today = datetime.now().strftime("%Y-%m-%d")
    path = _new_path(args.skill, today)
    header = "\n".join([
        "---",
        f"skill: {args.skill}",
        f"date: {today}",
        f"goal: {args.goal}",
        "status: in_progress",
        "---",
        "",
        f"# {args.skill} interview",
        "",
        "## Summary",
        "",
        "## Open flags",
        "",
        QA_HEADING,
        "",
    ])
    try:
        # Exclusive create: if another process won the race for this name, we
        # take the next one rather than writing over their header.
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write(header)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        path = _new_path(args.skill, today)
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write(header)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        return _fail("write_failed", f"{type(exc).__name__}: {exc}")
    return _ok(resumable=False, path=str(path), entries=0)


def cmd_append(args) -> int:
    path = find_open(args.skill)
    if path is None:
        return _fail("no_open_capture",
                     f"no in-progress capture for {args.skill}; run start first")
    answer = args.answer
    if answer is None and args.answer_file:
        try:
            answer = Path(args.answer_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _fail("write_failed", f"{type(exc).__name__}: {exc}")
    if answer is None:
        answer = sys.stdin.read()

    try:
        before = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _fail("write_failed", f"{type(exc).__name__}: {exc}")
    n = entry_count(before) + 1
    stamp = datetime.now().isoformat(timespec="seconds")
    block = "\n".join([
        f"### {n}. {stamp}",
        "",
        f"**Question:** {args.question.strip()}",
        "",
        f"**Answer:** {answer.strip()}",
        "",
        "",
    ])
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(block)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        return _fail("write_failed", f"{type(exc).__name__}: {exc}")

    # Read it back. An append that reported success but landed nowhere is the
    # one failure this whole file exists to make impossible.
    try:
        after = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _fail("readback_failed", f"{type(exc).__name__}: {exc}")
    if block.strip() not in after or entry_count(after) != n:
        return _fail("readback_failed",
                     f"expected {n} entries, found {entry_count(after)}")
    return _ok(path=str(path), entries=n)


def cmd_show(args) -> int:
    path = find_open(args.skill)
    if path is None:
        directory = captures_dir()
        found = sorted(directory.glob(f"{args.skill}-*.md")) if directory.is_dir() else []
        if not found:
            return _fail("no_open_capture", f"no capture for {args.skill}")
        path = found[-1]
    text = path.read_text(encoding="utf-8")
    entries = []
    for chunk in re.split(r"^### \d+\.\s", text, flags=re.MULTILINE)[1:]:
        q = re.search(r"\*\*Question:\*\*\s*(.+)", chunk)
        a = re.search(r"\*\*Answer:\*\*\s*([\s\S]*)", chunk)
        entries.append({"question": q.group(1).strip() if q else "",
                        "answer": a.group(1).strip() if a else ""})
    return _ok(path=str(path), entries=len(entries), log=entries)


def cmd_close(args) -> int:
    path = find_open(args.skill)
    if path is None:
        return _fail("no_open_capture", f"no in-progress capture for {args.skill}")
    text = path.read_text(encoding="utf-8")
    # Line-anchored, so the substitution can never run past this one line.
    new = _STATUS_RE.sub(f"status: {args.status}", text, count=1)
    try:
        tmp = path.with_name(path.name + ".van-gogh-tmp")
        tmp.write_text(new, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        return _fail("write_failed", f"{type(exc).__name__}: {exc}")
    return _ok(path=str(path), status=args.status, entries=entry_count(new))


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checkpoint an interview to disk.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--skill", required=True)
    start.add_argument("--goal", default="")
    start.add_argument("--force-new", action="store_true")

    append = sub.add_parser("append")
    append.add_argument("--skill", required=True)
    append.add_argument("--question", required=True)
    append.add_argument("--answer", default=None)
    append.add_argument("--answer-file", default=None)

    show = sub.add_parser("show")
    show.add_argument("--skill", required=True)

    close = sub.add_parser("close")
    close.add_argument("--skill", required=True)
    close.add_argument("--status", default="complete",
                       choices=["complete", "paused"])

    args = parser.parse_args(argv)
    return {"start": cmd_start, "append": cmd_append,
            "show": cmd_show, "close": cmd_close}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
