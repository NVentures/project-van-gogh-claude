"""workbench_data: the markdown renderer, item normalization, and CRM grouping.

The renderer replaces the one that lived in the retired week_serve.py, so the
escaping and checkbox-ordering cases it used to cover are pinned here.
"""
import workbench_data as wd


# ── render_markdown ──────────────────────────────────────────────────────────

def test_headings_and_paragraphs():
    html = wd.render_markdown("# Title\n\nA sentence.\n")
    assert "<h1>Title</h1>" in html
    assert "<p>A sentence.</p>" in html


def test_frontmatter_is_stripped():
    html = wd.render_markdown("---\ntitle: x\n---\n\n# Real\n")
    assert "title: x" not in html
    assert "<h1>Real</h1>" in html


def test_checkboxes_carry_appearance_order():
    html = wd.render_markdown("- [ ] one\n- [x] two\n- [ ] three\n")
    assert 'data-check-index="0"' in html
    assert 'data-check-index="1"' in html
    assert 'data-check-index="2"' in html
    # The checked one is marked both structurally and visually.
    assert 'data-check-index="1" checked' in html
    assert html.count('class="task done"') == 1


def test_nested_lists_close_cleanly():
    html = wd.render_markdown("- outer\n  - inner\n- outer again\n")
    assert html.count("<ul>") == html.count("</ul>")
    assert html.count("<li>") == 3


def test_table_gets_a_header_row_and_nil_placeholder():
    html = wd.render_markdown("| A | B |\n|---|---|\n| 1 |  |\n")
    assert "<th>A</th>" in html
    assert "<tbody>" in html
    # DESIGN.md: a nil cell is a middle dot, never an em-dash.
    assert "&#183;" in html


def test_html_in_source_is_escaped_not_executed():
    html = wd.render_markdown("Hello <script>alert(1)</script> there\n")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_link_and_wikilink_and_code():
    html = wd.render_markdown("See [docs](https://example.com) and [[Some Note]] and `x=1`.\n")
    assert 'href="https://example.com"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'data-note="Some Note"' in html
    assert "<code>x=1</code>" in html


def test_javascript_url_is_not_turned_into_a_link():
    html = wd.render_markdown("[click](javascript:alert(1))\n")
    assert "javascript:alert" not in html.replace("&#x27;", "") or "<a " not in html


def test_fenced_code_is_not_interpreted_as_markdown():
    html = wd.render_markdown("```\n# not a heading\n- not a list\n```\n")
    assert "<h1>" not in html
    assert "<pre><code>" in html


def test_renderer_never_emits_a_dash_character():
    """The no-dash rule covers generated artifacts, not just prose we type."""
    html = wd.render_markdown("| A |\n|---|\n|   |\n\n- [ ] a task\n")
    # Built from code points so this file itself stays dash-free.
    assert chr(0x2014) not in html and chr(0x2013) not in html


# ── Item normalization ───────────────────────────────────────────────────────

def test_normalize_falls_back_to_reply_age():
    item = wd.normalize_item({"reply_age_days": 5, "counterparty_email": "A@Ex.COM"},
                             "inbox_pending")
    assert item["age_days"] == 5
    assert item["counterparty_email"] == "a@ex.com"
    assert item["domain"] == "ex.com"


def test_normalize_prefers_age_days_when_present():
    item = wd.normalize_item({"age_days": 9, "reply_age_days": 2}, "cold_urgent")
    assert item["age_days"] == 9


def test_attention_items_dedupes_across_lists_first_source_wins():
    sidecar = {
        "inbox_pending": [{"subject": "Re: Deal", "counterparty_email": "a@x.com"}],
        "waiting_on_user": [{"subject": "re: deal", "counterparty_email": "A@X.com"}],
        "cold_urgent": [{"subject": "Other", "counterparty_email": "b@y.com"}],
    }
    items = wd.attention_items(sidecar)
    assert len(items) == 2
    deal = [i for i in items if i["subject"].lower().startswith("re: deal")][0]
    assert deal["source"] == "inbox_pending"


def test_attention_items_survives_a_junk_row():
    items = wd.attention_items({"inbox_pending": ["not a dict", {"subject": "ok"}]})
    assert len(items) == 1


# ── CRM grouping ─────────────────────────────────────────────────────────────

def _item(**kw):
    base = {"source": "inbox_pending", "account": "", "subject": "s",
            "counterparty_name": "N", "counterparty_email": "a@acme.com",
            "domain": "acme.com", "age_days": 1, "body_preview": "", "summary": "",
            "intent": "", "urgency": "low", "suggested_action": "",
            "is_internal": False, "allowlisted": False}
    base.update(kw)
    return base


def test_corporate_domain_groups_people_together():
    groups = wd.crm_groups([
        _item(counterparty_email="a@acme.com", counterparty_name="Ann"),
        _item(counterparty_email="b@acme.com", counterparty_name="Bo"),
    ])
    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert sorted(groups[0]["people"]) == ["Ann", "Bo"]


def test_consumer_mailboxes_are_separate_relationships():
    groups = wd.crm_groups([
        _item(counterparty_email="a@gmail.com", domain="gmail.com", counterparty_name="Ann"),
        _item(counterparty_email="b@gmail.com", domain="gmail.com", counterparty_name="Bo"),
    ])
    assert len(groups) == 2
    assert all(g["logo_domain"] == "" for g in groups)


def test_groups_sort_urgent_and_old_first():
    groups = wd.crm_groups([
        _item(counterparty_email="a@low.com", domain="low.com", urgency="low", age_days=1),
        _item(counterparty_email="b@high.com", domain="high.com", urgency="high", age_days=1),
    ])
    assert groups[0]["domain"] == "high.com"


# ── P16: folds ───────────────────────────────────────────────────────────────

def test_callout_details():
    """A closed callout becomes a closed <details>; `+` opens it."""
    from workbench_data import render_markdown
    closed = render_markdown("> [!note]- Harbor Solar\n> - [ ] a thing\n")
    assert closed.startswith("<details><summary>")
    assert "Harbor Solar" in closed
    assert "<details open>" not in closed
    opened = render_markdown("> [!note]+ Harbor Solar\n> - [ ] a thing\n")
    assert opened.startswith("<details open><summary>")
    assert "Harbor Solar" in opened


def test_callout_body_is_rendered_markdown():
    from workbench_data import render_markdown
    html = render_markdown("> [!note]- T\n> - [ ] a thing\n> - [x] done\n")
    assert 'data-check-index="0"' in html
    assert 'data-check-index="1"' in html
    assert "foldbody" in html


def test_check_index_runs_through_a_fold():
    """A checkbox inside a fold is still the Nth checkbox of the file."""
    from workbench_data import render_markdown
    html = render_markdown(
        "- [ ] before\n\n> [!note]- T\n> - [ ] inside\n\n- [ ] after\n")
    assert html.index('data-check-index="0"') < html.index('data-check-index="1"')
    assert 'data-check-index="2"' in html


def test_a_plain_blockquote_is_still_a_blockquote():
    from workbench_data import render_markdown
    assert "<blockquote>" in render_markdown("> just a quote\n")
