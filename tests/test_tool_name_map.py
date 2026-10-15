"""Every Claude tool a skill or agent leans on has a Codex reading.

Skills are written once, for Claude's tools, and run under Codex with the
table at the end of skills/_shared/python-runtime.md. This test collects
every tool name the skills and agents mention or declare and fails on one
the table does not map, so a new skill that leans on a new Claude tool is
red until the table says what Codex does instead.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTE = ROOT / "skills" / "_shared" / "python-runtime.md"

# The Claude Code tool vocabulary a skill might name. Kept wide on purpose:
# a name that is in here and never used costs nothing, one that is missing
# would let a new dependence slip past.
CLAUDE_TOOLS = ("AskUserQuestion", "Agent", "Task", "Skill", "SlashCommand", "TodoWrite",
                "Read", "Write", "Edit", "MultiEdit", "Grep", "Glob", "Bash", "WebSearch",
                "WebFetch", "Artifact", "NotebookEdit", "ExitPlanMode", "EnterPlanMode",
                "SendUserFile", "LS")
_WORD = re.compile(r"\b(" + "|".join(CLAUDE_TOOLS) + r")\b(?: tool)?")


def mentioned() -> dict:
    """{tool name: first file that names it}."""
    out = {}
    files = sorted(ROOT.glob("skills/**/*.md")) + sorted(ROOT.glob("agents/*.md"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in _WORD.finditer(text):
            out.setdefault(m.group(1), path.relative_to(ROOT).as_posix())
        front = text.split("---", 2)
        if len(front) >= 3:
            for line in front[1].splitlines():
                if line.startswith("tools:"):
                    for name in line.split(":", 1)[1].split(","):
                        out.setdefault(name.strip(), path.relative_to(ROOT).as_posix())
                if line.strip().startswith("- ") and "(" in line:
                    out.setdefault(line.strip()[2:].split("(")[0], path.relative_to(ROOT).as_posix())
    return {k: v for k, v in out.items() if k in CLAUDE_TOOLS}


def table_rows() -> str:
    text = NOTE.read_text(encoding="utf-8")
    start = text.index("## Two clients: Claude Code and Codex")
    return text[start:]


def test_every_tool_named_in_a_skill_or_agent_is_mapped():
    rows = table_rows()
    names = mentioned()
    assert names, "no tool names found: the scan is broken"
    missing = {n: f for n, f in names.items() if not re.search(r"\b" + n + r"\b", rows)}
    assert not missing, ("tools with no Codex reading in skills/_shared/python-runtime.md: "
                         f"{missing}")


def test_the_table_maps_the_tools_the_product_depends_on():
    rows = table_rows()
    for name in ("AskUserQuestion", "Skill", "Agent", "TodoWrite", "Artifact", "Bash"):
        assert name in rows
    assert "$van-gogh:<name>" in rows
    assert "Under Codex" in rows


def test_the_scan_sees_a_planted_tool(tmp_path, monkeypatch):
    skill = tmp_path / "skills" / "planted"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: planted\n---\nUse the NotebookEdit tool here.\n")
    (tmp_path / "agents").mkdir()
    monkeypatch.setattr("test_tool_name_map.ROOT", tmp_path)
    assert mentioned() == {"NotebookEdit": "skills/planted/SKILL.md"}
