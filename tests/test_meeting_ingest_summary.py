"""The catalog one-liner must be a summary, never the page template's prompt.

The source template seeds a synthesis section with an Obsidian comment
("%% Add ad-hoc observations, contradictions, or open questions here. %%").
first_line_of_summary skipped headings but not comments, so a meeting whose
synthesis came back empty filed that prompt as its catalog description, and
the quality linter passed it clean because it only rejected markdown
formatting characters.

Both halves are tested here: the extractor must not select a comment, and
the linter must reject one independently if it ever reaches the entry.
"""
import importlib


def _mod():
    return importlib.import_module("meeting_ingest")


PLACEHOLDER = "%% Add ad-hoc observations, contradictions, or open questions here. %%"


def test_comment_only_summary_is_not_used_as_the_description():
    """An empty synthesis yields "(no summary)", never the template prompt."""
    mi = _mod()
    body = f"## What was discussed\n\n{PLACEHOLDER}\n"
    assert mi.first_line_of_summary(body) == "(no summary)"


def test_comment_is_skipped_in_favour_of_real_content_below_it():
    """A comment above real content must not shadow it: the summary is the content."""
    mi = _mod()
    body = (
        "## What was discussed\n\n"
        f"{PLACEHOLDER}\n"
        "- The parcel is still under option through November.\n"
    )
    assert mi.first_line_of_summary(body) == (
        "The parcel is still under option through November."
    )


def test_ordinary_summary_is_unaffected():
    """Control: the fix must not change a normal first bullet."""
    mi = _mod()
    body = "- Demo plus overview; interconnection now 4-5 years.\n"
    assert mi.first_line_of_summary(body) == (
        "Demo plus overview; interconnection now 4-5 years."
    )


def test_linter_rejects_a_comment_as_a_description():
    """The linter is the backstop and must catch it without the extractor's help."""
    mi = _mod()
    issues = mi.lint_new_index_entry("Some Meeting - 2026-09-14", PLACEHOLDER, "solar")
    assert any("Obsidian comment" in i for i in issues), issues


def test_linter_passes_a_real_description():
    """Control: a genuine summary of adequate length raises nothing."""
    mi = _mod()
    desc = "Demo plus overview; interconnection now 4-5 years with costs at risk."
    assert mi.lint_new_index_entry("Northlight OS Demo - 2026-09-16", desc, "solar") == []
