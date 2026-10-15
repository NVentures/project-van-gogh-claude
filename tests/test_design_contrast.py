"""DESIGN.md's contrast table is recomputed here, not trusted.

The first draft of the Kneeboard palette shipped eight contrast figures that
were written to look plausible. Two of them hid a real AA failure: amber on the
light card measured 3.8:1 against a claimed 4.6:1, and it carries the word
WAITING ON YOU. A table of measurements nobody measures is worse than no table,
because it reads as evidence.

This parses the token blocks and the table out of DESIGN.md itself, so a token
change with a stale table fails here rather than in someone's eyes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DESIGN = Path(__file__).resolve().parent.parent / "DESIGN.md"

# Every pair in the table has to clear this, except the ones the table itself
# marks decorative. AA for body text is 4.5:1.
AA = 4.5


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(text: str) -> tuple[dict, dict]:
    """The light block is bare `:root{`; the dark one is the media query."""
    light_src = re.search(r":root\{(.*?)\}", text, re.S)
    dark_src = re.search(r'prefers-color-scheme:dark\).*?\{(.*?)\}\}', text, re.S)
    assert light_src and dark_src, "DESIGN.md lost one of its token blocks"

    def parse(block: str) -> dict:
        return {m.group(1): m.group(2)
                for m in re.finditer(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block)}

    light = parse(light_src.group(1))
    dark = dict(light)
    dark.update(parse(dark_src.group(1)))   # dark redefines a subset
    return light, dark


def _table_rows(text: str) -> list[tuple[str, str, float, float, str]]:
    rows = []
    pattern = re.compile(
        r"^\|\s*`--([\w-]+)`\s+on\s+`--([\w-]+)`\s*\|\s*([\d.]+):1\s*\|"
        r"\s*([\d.]+):1\s*\|\s*([^|]+?)\s*\|$", re.M)
    for m in pattern.finditer(text):
        rows.append((m.group(1), m.group(2), float(m.group(3)),
                     float(m.group(4)), m.group(5)))
    return rows


def test_the_table_is_present_and_covers_every_status_color():
    rows = _table_rows(DESIGN.read_text(encoding="utf-8"))
    assert rows, "no contrast table found in DESIGN.md"
    measured = {fg for fg, _, _, _, _ in rows}
    for token in ("amber", "cyan", "red", "green", "text", "muted"):
        assert token in measured, f"--{token} carries text but is unmeasured"


def test_every_claimed_figure_matches_the_measurement():
    text = DESIGN.read_text(encoding="utf-8")
    light, dark = _tokens(text)
    drift = []
    for fg, bg, claim_l, claim_d, _verdict in _table_rows(text):
        for palette, claim, name in ((light, claim_l, "light"), (dark, claim_d, "dark")):
            assert fg in palette and bg in palette, f"--{fg} or --{bg} is not a token"
            actual = contrast(palette[fg], palette[bg])
            if abs(actual - claim) >= 0.05:
                drift.append(f"--{fg} on --{bg} ({name}): table says "
                             f"{claim:.1f}:1, measures {actual:.2f}:1")
    assert not drift, "DESIGN.md claims figures it does not have:\n" + "\n".join(drift)


def test_anything_carrying_a_word_clears_AA_in_both_themes():
    """The decorative row says so in its own verdict; everything else must pass."""
    text = DESIGN.read_text(encoding="utf-8")
    light, dark = _tokens(text)
    failures = []
    for fg, bg, _cl, _cd, verdict in _table_rows(text):
        if "decorative" in verdict.lower():
            continue
        for palette, name in ((light, "light"), (dark, "dark")):
            actual = contrast(palette[fg], palette[bg])
            if actual < AA:
                failures.append(f"--{fg} on --{bg} ({name}) is {actual:.2f}:1, "
                                f"under {AA}:1, and its verdict claims {verdict!r}")
    assert not failures, "\n".join(failures)


def test_the_measurement_can_fail():
    """A checker that cannot fail is not a checker. Plant a known-bad pair."""
    assert contrast("#B9740F", "#FFFFFF") < AA, "the original amber should fail AA"
    assert contrast("#9C5F0A", "#FFFFFF") >= AA, "the corrected amber should pass"


@pytest.mark.parametrize("bad", ["#FFFFFF", "#F3F4F1"])
def test_pure_black_never_appears(bad):
    """Anti-slop rule 6: no pure black on any background."""
    text = DESIGN.read_text(encoding="utf-8")
    light, dark = _tokens(text)
    for palette in (light, dark):
        assert "#000000" not in palette.values(), "pure black is banned"
