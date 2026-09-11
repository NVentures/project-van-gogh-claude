"""P20 to P24 and P26: the one permanent page each briefing owns."""
import copy
import json
import re
from pathlib import Path

import pytest

import briefing_html as bh
import config_loader as cl

SKILLS = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture()
def pages_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "_sidecar_path", lambda: tmp_path / "briefing_pages.json")
    return tmp_path


# ── P20 ──────────────────────────────────────────────────────────────────────

def test_url_reused(pages_dir):
    """A second run republishes to the same URL and creates no second link."""
    bh.record_page("morning-coffee", "https://claude.ai/x/abc", "2026-09-03")
    assert bh.page_url("morning-coffee") == "https://claude.ai/x/abc"
    bh.record_page("morning-coffee", bh.page_url("morning-coffee"), "2026-09-04")
    stored = json.loads((pages_dir / "briefing_pages.json").read_text(encoding="utf-8"))
    assert list(stored) == ["morning-coffee"]
    assert stored["morning-coffee"]["url"] == "https://claude.ai/x/abc"
    assert stored["morning-coffee"]["briefing_date"] == "2026-09-04"


def test_each_briefing_owns_its_own_url(pages_dir):
    for i, name in enumerate(bh.BRIEFINGS):
        bh.record_page(name, f"https://claude.ai/x/{i}", "2026-09-04")
    urls = {bh.page_url(n) for n in bh.BRIEFINGS}
    assert len(urls) == len(bh.BRIEFINGS)


def test_unknown_briefing_is_rejected(pages_dir):
    with pytest.raises(ValueError):
        bh.record_page("brunch", "https://claude.ai/x", "2026-09-04")


def test_a_failed_run_keeps_the_url_it_had(pages_dir):
    bh.record_page("week", "https://claude.ai/x/w", "2026-09-01")
    bh.record_page("week", "", "2026-09-08", published=False,
                   reason="no publishing surface in a scheduled run")
    assert bh.page_url("week") == "https://claude.ai/x/w"
    line = bh.failure_line("week")
    assert "no publishing surface" in line
    assert "2026-09-01" in line
    assert "https://claude.ai/x/w" in line


def test_no_failure_line_when_the_page_is_current(pages_dir):
    bh.record_page("week", "https://claude.ai/x/w", "2026-09-08")
    assert bh.failure_line("week") == ""
    assert bh.failure_line("afternoon-tea") == ""


def test_a_published_page_hands_back_its_link(pages_dir):
    """The failure half existed and the success half did not, so a run that
    published correctly said nothing and the reader had no link to follow."""
    bh.record_page("week", "https://claude.ai/x/w", "2026-09-08")
    link = bh.link_line("week")
    assert "https://claude.ai/x/w" in link
    assert bh.page_line("week") == link

    bh.record_page("week", "", "2026-09-09", published=False, reason="no network")
    assert bh.link_line("week") == ""
    assert bh.page_line("week") == bh.failure_line("week")
    assert "no network" in bh.page_line("week")


def test_exactly_one_page_line_speaks(pages_dir):
    # Never published at all: neither line, and no empty promise of a link.
    assert bh.page_line("morning-coffee") == ""
    for published, wanted in ((True, "up to date"), (False, "did not update")):
        bh.record_page("morning-coffee", "https://claude.ai/x/m", "2026-09-04",
                       published=published, reason="no publishing surface")
        line = bh.page_line("morning-coffee")
        assert wanted in line, line
        other = (bh.failure_line if published else bh.link_line)("morning-coffee")
        assert other == "" 


# ── P21 ──────────────────────────────────────────────────────────────────────

def test_reads_before_publish():
    """Every briefing skill reads the stored page before republishing to it."""
    for name in ("morning-coffee", "afternoon-tea", "week", "week-retro"):
        text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert "briefing_html.py" in text or "briefing_html" in text, name
        read_at = text.find("action: \"read\"")
        publish_at = text.find("publish the page")
        assert read_at != -1, f"{name}: no read step"
        assert publish_at != -1, f"{name}: no publish step"
        assert read_at < publish_at, f"{name}: publishes before reading"


# ── P23 and P24 ──────────────────────────────────────────────────────────────

_MD = """# Morning Coffee

Two of your 68 open items are late.

> [!note]- Harbor Solar

> - [ ] Meridian redline v4

After the fold.
"""


def test_stale_banner():
    stale = bh.render_page(_MD, {"title": "Morning Coffee",
                                 "briefing_date": "2026-09-03",
                                 "newest_date": "2026-09-04"})
    assert "2026-09-04" in stale and "did not update" in stale
    current = bh.render_page(_MD, {"title": "Morning Coffee",
                                   "briefing_date": "2026-09-04",
                                   "newest_date": "2026-09-04"})
    assert "did not update" not in current


def test_digest_stale_line(pages_dir):
    bh.record_page("morning-coffee", "https://claude.ai/x/a", "2026-09-03")
    bh.record_page("morning-coffee", "", "2026-09-04", published=False,
                   reason="the session had no publishing surface")
    line = bh.failure_line("morning-coffee")
    assert line.startswith("The web page did not update")
    assert "2026-09-03" in line


def test_page_tokens():
    html = bh.render_page(_MD, {"title": "Morning Coffee",
                                "briefing_date": "2026-09-04"})
    root = html.split("@media", 1)[0]
    used = set(re.findall(r"var\((--[a-z-]+)\)", html))
    defined = set(re.findall(r"(--[a-z-]+)\s*:", root))
    assert used <= defined, sorted(used - defined)
    # Both themes, and the toggle wins in both directions.
    assert "prefers-color-scheme:dark" in html
    assert '[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html


def test_folds_are_closed_details():
    html = bh.render_page(_MD, {"title": "T", "briefing_date": "2026-09-04"})
    assert "<details><summary>" in html
    assert "Harbor Solar" in html
    assert "<details open>" not in html


def test_an_opened_callout_renders_open():
    html = bh.render_page("> [!note]+ Open one\n> - [ ] a\n",
                          {"title": "T", "briefing_date": "2026-09-04"})
    assert "<details open>" in html


# ── P26 ──────────────────────────────────────────────────────────────────────

PLANTED = [
    "ya29.a0AfB_byC3xample_token_value_here",
    "sk-proj-AbCdEfGhIjKlMnOpQrStUv",
    "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "xoxb-1234567890-abcdefghijkl",
    "AKIAIOSFODNN7EXAMPLE",
    "GOOGLE_REFRESH_TOKEN=1//0eXaMpLeReFrEsHtOkEn",
    "client_secret: GOCSPX-abcdefghijklmnop",
    "/Users/someone/Documents/vault/wiki/hotcache.md",
    "/home/someone/vault/wiki/hotcache.md",
    # This project ships to Windows, and the Windows branch of the pattern was
    # over-escaped, so it matched nothing and this exact string reached a
    # published page. A planted path per platform, or the branch nobody tests
    # is the branch that is broken.
    "C:\\Users\\someone\\AppData\\Roaming\\van-gogh\\.env",
]


def test_page_redaction():
    """The control fails the scan first, then the redacted page passes it."""
    body = "# Briefing\n\n" + "\n\n".join(f"A line with {p}" for p in PLANTED)
    control = bh.render_page(body, {"title": "T", "briefing_date": "2026-09-04"})
    leaked = [p for p in PLANTED if p in control]
    assert leaked, "the planted control leaked nothing; the scan proves nothing"

    clean = bh.render_page(bh.redact(body),
                           {"title": "T", "briefing_date": "2026-09-04"})
    still = [p for p in PLANTED if p in clean]
    assert not still, still
    assert "[redacted" in clean or "[local path]" in clean


def test_redaction_covers_every_platform_this_ships_to():
    """One planted path per platform, checked one at a time.

    The set-level test passes as long as SOMETHING redacts, which is how a
    dead Windows branch survived a scan over eight planted strings.
    """
    for path in ("/Users/someone/x/y.md", "/home/someone/x/y.md",
                 "C:\\Users\\someone\\x\\y.md",
                 "D:\\Users\\someone\\AppData\\Roaming\\van-gogh\\.env"):
        redacted = bh.redact(f"the file is at {path} and that is all")
        assert path not in redacted, path
        assert "[local path]" in redacted


def test_redaction_leaves_ordinary_text_alone():
    text = "Meridian redline v4 from Dana Ruiz is 2 days late. Reply by Friday."
    assert bh.redact(text) == text


# ── The page has to be built from markdown, not from the terminal render ─────

def _built_briefing(n_items=90, seed=3):
    import config_loader as cl
    import briefing_fixtures as bf
    import week_review as wr
    saved = copy.deepcopy(cl._config)
    try:
        bf.set_businesses(cl)
        sections = bf.random_sections(seed=seed, n_items=n_items)
        for entries in sections.values():
            for e in entries:
                e["bucket"] = wr.assign_bucket(e)
        return wr.build_front_page(wr.group_by_bucket(sections), "morning-coffee")
    finally:
        cl._config = saved


def test_the_page_renders_the_front_page_as_items_not_a_paragraph():
    """The terminal render collapses into one blob on every markdown surface.

    Column alignment is whitespace and markdown collapses whitespace, so the
    aligned front page became a single run-on paragraph on a real published
    page. Every number and name was still there, so no gate could see it; a
    screenshot did.
    """
    built = _built_briefing()
    good = bh.render_page(built["front_page_file_md"] + "\n"
                          + built["fold_rows_file_md"],
                          {"title": "Morning Coffee", "briefing_date": "2026-09-04"})
    assert good.count('class="task"') == len(built["front_page"])
    # The section heading survives as the card's bar. It used to be an <h3>;
    # under Kneeboard every section becomes a card whose bar carries the name.
    assert '<div class="cbar">' in good
    assert "DO TODAY" in good

    bad = bh.render_page(built["front_page_md"] + "\n" + built["fold_rows_md"],
                         {"title": "Morning Coffee", "briefing_date": "2026-09-04"})
    # The control: the terminal render really does collapse, which is what
    # makes the check above mean something.
    assert bad.count('class="task"') == 0


def test_no_rendered_paragraph_swallows_the_whole_page():
    import re as _re
    built = _built_briefing()
    html = bh.render_page(built["front_page_file_md"] + "\n"
                          + built["fold_rows_file_md"] + "\n"
                          + built["folded_md"],
                          {"title": "T", "briefing_date": "2026-09-04"})
    longest = max((len(t) for t in _re.findall(r"<p>(.*?)</p>", html, _re.S)),
                  default=0)
    assert longest < 400, f"a {longest}-character paragraph is a collapsed block"


def test_every_front_page_item_survives_into_the_file_markdown():
    built = _built_briefing()
    for item in built["front_page"]:
        assert item["subject"] in built["front_page_file_md"], item["subject"]
    for row in built["fold_rows"]:
        if row["folded"]:
            assert row["display_name"].upper() in built["fold_rows_file_md"]


def test_the_page_does_not_print_its_title_twice():
    """Meta title above the body's own leading heading showed it twice."""
    html = bh.render_page("# Morning Coffee\n\nA sentence.\n",
                          {"title": "Morning Coffee", "briefing_date": "2026-09-04"})
    # Once in <title>, once in the <h1>. Never a third time from the body.
    assert html.count("Morning Coffee") == 2, html
    assert "<h1>" not in html.split("</h1>", 1)[1]
    # A body heading that is NOT the title is left alone.
    other = bh.render_page("# Something Else\n\nA sentence.\n",
                           {"title": "Morning Coffee", "briefing_date": "2026-09-04"})
    assert "Something Else" in other


def test_a_front_page_item_is_one_list_item():
    """A continuation line indented under a list item renders as a paragraph
    outside the list, which put a stray line under every item on the page."""
    built = _built_briefing()
    for line in built["front_page_file_md"].splitlines():
        assert not (line.startswith("  ") and line.strip()), repr(line)
    html = bh.render_page(built["front_page_file_md"],
                          {"title": "T", "briefing_date": "2026-09-04"})
    body = html.split('class="task"', 1)[-1]
    # Nothing between the list items except the list itself.
    assert "<p>" not in body.split("</ul>", 1)[0]
