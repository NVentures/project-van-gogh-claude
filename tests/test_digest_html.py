"""app/digest_html.py — the email-safe markdown renderer for digest sends."""
import digest_html


def render(md):
    return digest_html.md_to_email_html(md)


def test_frontmatter_does_not_leak():
    html = render("---\nweek: 2026-08-10\ngenerated: now\n---\n# Week\n")
    assert "week: 2026-08-10" not in html
    assert "generated" not in html
    assert "<h1" in html and "Week" in html


def test_html_comments_stripped_per_line_only():
    html = render("- [ ] ping Alex <!-- met:2026-08-01 -->\n")
    assert "met:2026-08-01" not in html
    assert "ping Alex" in html
    # A stray <!-- in quoted external content must NOT swallow later lines.
    html = render("subject with <!-- in it\nimportant next line\n--> tail\n")
    assert "important next line" in html


def test_leading_divider_is_not_frontmatter():
    html = render("---\nimportant top section\n---\nafter\n")
    assert "important top section" in html
    assert "after" in html


def test_unclosed_frontmatter_falls_through():
    html = render("---\nkey: value\n# Title\n")
    assert "Title" in html


def test_empty_input_renders_wrapper():
    html = render("")
    assert html.startswith('<div style="max-width:680px')


def test_headings_have_inline_styles():
    html = render("# One\n## Two\n### Three\n#### Four\n")
    for tag in ("<h1 style=", "<h2 style=", "<h3 style=", "<h4 style="):
        assert tag in html
    assert "border-bottom" in html  # h2 underline


def test_table_rendered_with_inline_borders_and_header():
    html = render("| Day | Event |\n|---|---|\n| Mon | Standup |\n| Tue | 1:1 |\n")
    assert '<table style="border-collapse:collapse' in html
    assert "<th style=" in html and "Day" in html
    assert "border:1px solid" in html
    assert html.count("<tr>") == 3  # separator row dropped
    assert "---" not in html


def test_checkboxes_are_glyphs_not_inputs():
    html = render("- [ ] open item\n- [x] done item\n  - [ ] nested\n")
    assert "&#9744;" in html  # ☐
    assert "&#9745;" in html  # ☑
    assert "<s>done item</s>" in html
    assert "<input" not in html
    assert "margin:3px 0 3px 24px" in html  # nested indent


def test_terminal_residue_normalized():
    html = render("──────────\n• a bullet\n═══════\n")
    assert "─" not in html and "═" not in html
    assert html.count("<hr") == 2
    assert "&#8226; a bullet" in html
    assert "•" not in html


def test_inline_bold_and_code():
    html = render("**Filter stats:** 3 hidden, run `week_review.py` again\n")
    assert "<b>Filter stats:</b>" in html
    assert "<code style=" in html and "week_review.py" in html


def test_italics_and_paragraph_indent():
    html = render("*(none)*\n  → nudge Alex on the deck\n")
    assert "<i>(none)</i>" in html
    assert "*(none)*" not in html
    assert "margin-left:24px" in html  # continuation line keeps its indent


def test_literal_asterisks_not_italicized():
    html = render("run *.py and *.md files\ncompute 3 * 4 * 2\n")
    assert "<i>" not in html
    assert "*.py and *.md files" in html


def test_inline_formatting_never_spans_table_cells():
    # A ** opened in one cell and closed in another must not wrap <b>
    # around the </th><th> markup between them.
    html = render("| a** | **b |\n|---|---|\n| x | y |\n")
    assert "<b>" not in html
    # Well-formed inline markup inside a single cell still works.
    html = render("| **Day** | Event |\n|---|---|\n| Mon | `standup` |\n")
    assert "<b>Day</b>" in html
    assert "<code" in html


def test_markdown_links_render_as_anchors():
    html = render("see [Granola notes](https://notes.granola.ai/x?a=1&b=2)\n")
    assert '<a href="https://notes.granola.ai/x?a=1&amp;b=2"' in html
    assert ">Granola notes</a>" in html
    # Non-http schemes stay literal text.
    assert "<a" not in render("[bad](javascript:alert(1))\n")


def test_table_rows_normalized_to_header_width():
    html = render("| Subject | From |\n|---|---|\n"
                  "| Re: A | B deal | alice |\n"
                  "| short |\n")
    # Overflow cells merge into the last column; short rows pad with empties.
    assert "Re: A" in html and "B deal | alice" in html
    for row in html.split("<tr>")[1:]:
        assert row.count("<td") + row.count("<th") == 2


def test_content_is_escaped():
    html = render("<script>alert(1)</script>\n")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_style_block_or_head():
    html = render("# Week\n\n- item\n")
    assert "<style" not in html
    assert "<head" not in html
    assert html.startswith('<div style="max-width:680px')


# ── P16: folds in the email ──────────────────────────────────────────────────

def test_callout_block():
    """An email client cannot open a fold, so it becomes a shaded block."""
    from digest_html import md_to_email_html
    html = md_to_email_html(
        "Front page.\n\n> [!note]- Harbor Solar\n> - [ ] a thing\n\nAfter.\n")
    assert "<details" not in html
    assert "Harbor Solar" in html
    assert "background:#f6f8fa" in html
    assert "a thing" in html
    # The block sits after the front page, in order.
    assert html.index("Front page.") < html.index("Harbor Solar") < html.index("After.")


def test_callout_block_closes_before_following_prose():
    from digest_html import md_to_email_html
    html = md_to_email_html("> [!note]- One\n> - [ ] a\n\nOutside.\n")
    assert html.index("</div>") < html.index("Outside.")
