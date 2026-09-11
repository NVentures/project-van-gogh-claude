"""Every data key a SKILL.md tells the model to paste must actually be emitted.

A skill that says "paste `travel_md` VERBATIM" is a consumer of that key's
contract. Nothing gated that consumer, so two briefings instructed a paste of
a key their module never emitted: the model had nothing to paste, and the
whole Travel block vanished from the file, the Workbench page and the digest
at once. 1749 tests passed over it, because every one of them graded a
producer against its own declaration and none crossed the prose-to-code line.

These tests read both sides: the skill file for the key names it names, and
the module for the keys it emits.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
APP = ROOT / "app"

# The skill file that drives each briefing, and the module that feeds it.
BRIEFINGS = {
    "morning-coffee": "morning_coffee.py",
    "afternoon-tea": "afternoon_tea.py",
    "week": "week_review.py",
    "week-retro": "week_retro.py",
}

# Keys a skill can name that are supplied by something other than the
# briefing module itself (shared config, the plugin runtime, the fold
# renderer), so their absence from the module is not drift.
_EXEMPT = {
    "front_page_md", "front_page_file_md", "fold_rows_md", "fold_rows_file_md",
    "folded_md", "buckets_md", "whats_new", "job_watch", "update_notice",
}

# `paste it VERBATIM` / "`key_md` is the finished block": the constructions a
# skill uses when it hands a code-rendered string straight to the reader.
_PASTE_RE = re.compile(r"`([a-z_]+_(?:file_)?md)`\s+(?:is the finished block|verbatim)",
                       re.I)


def _module_keys(module_name: str) -> set[str]:
    src = (APP / module_name).read_text(encoding="utf-8")
    # Any string literal used as a dict key or assigned into output[...].
    return set(re.findall(r'["\']([a-z_]+_(?:file_)?md)["\']', src))


def _skill_paste_keys(briefing: str) -> set[str]:
    text = (SKILLS / briefing / "SKILL.md").read_text(encoding="utf-8")
    return {k for k in _PASTE_RE.findall(text)} - _EXEMPT


@pytest.mark.parametrize("briefing,module", sorted(BRIEFINGS.items()))
def test_pasted_keys_are_emitted(briefing: str, module: str) -> None:
    """A key the skill says to paste must exist in the module that feeds it."""
    if not (APP / module).exists():          # week-retro has no module of its own
        pytest.skip(f"{module} does not exist")
    named = _skill_paste_keys(briefing)
    if not named:
        pytest.skip(f"{briefing} names no pasted keys")
    emitted = _module_keys(module)
    missing = sorted(named - emitted)
    assert not missing, (
        f"{briefing}/SKILL.md tells the model to paste {missing}, but "
        f"app/{module} never emits {'it' if len(missing) == 1 else 'them'}. "
        "The block silently disappears from the file, the page and the digest."
    )


@pytest.mark.parametrize("briefing", ["morning-coffee", "afternoon-tea", "week"])
def test_travel_pair_is_emitted(briefing: str) -> None:
    """Both travel keys, or neither. The file key carries the booking link.

    A briefing that emits only the terminal key ships a file whose unbooked
    trip has no way to reach a booking page.
    """
    module = BRIEFINGS[briefing]
    emitted = _module_keys(module)
    assert ("travel_md" in emitted) == ("travel_file_md" in emitted), (
        f"app/{module} emits one travel key but not the other; the terminal "
        "render and the written file would disagree."
    )
    assert "travel_md" in emitted, f"app/{module} emits no rendered travel block"


def test_render_order_is_consecutive() -> None:
    """The shared render order is the contract four skills cite. Numbers drift.

    An insertion renumbered nothing and left 1,2,3,4,5,4,5,6,7,8,9: two
    ordinals used twice, each asserting a different block came first.
    """
    text = (SKILLS / "_shared" / "front-page.md").read_text(encoding="utf-8")
    section = text.split("## Render order", 1)[1].split("\n## ", 1)[0]
    nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", section, re.M)]
    assert nums == list(range(1, len(nums) + 1)), (
        f"render order is not consecutive: {nums}"
    )


@pytest.mark.parametrize("briefing", sorted(BRIEFINGS))
def test_skill_ordered_lists_have_no_duplicate_numbers(briefing: str) -> None:
    """A hand-numbered list that repeats an ordinal encodes two orders at once."""
    text = (SKILLS / briefing / "SKILL.md").read_text(encoding="utf-8")
    # A list starts at 1 and every later entry must be exactly one higher.
    # Splitting a run on ANY non-consecutive number was vacuous: a repeated
    # ordinal is itself non-consecutive, so it opened a fresh run and could
    # never collide with the number it duplicated. A new run may only begin
    # at 1; anything else is a break inside the list that owns it.
    nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", text, re.M)]
    runs, current = [], []
    for n in nums:
        if n == 1:
            if current:
                runs.append(current)
            current = [n]
        else:
            current.append(n)
    if current:
        runs.append(current)
    for run in runs:
        assert run == list(range(1, len(run) + 1)), (
            f"{briefing}/SKILL.md has a numbered list that is not "
            f"consecutive from 1: {run}"
        )
