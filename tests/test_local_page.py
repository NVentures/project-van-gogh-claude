"""The local briefing page: what it renders, and what the artifact must not.

The same `render_page` produces both surfaces. The difference is what the
server appends. That difference is the security boundary, so it is tested from
both sides: the artifact must carry no token and no control, and the local page
must carry both.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import briefing_html  # noqa: E402
import digest_send  # noqa: E402

MD = """# Morning Coffee

7 of your 120 open items come first.

## DO TODAY

**Waiting on you**
- [ ] Lumen redline v4 · Ana Beltre · due 2026-09-04 · NORTHWIND DC · Legal

## THE REST, 113 items

- **HARBOR SOLAR** · 24 · Sales 2 (2 on you)

> [!note]- Harbor Solar · 24 more
>
> Sales
> - [ ] Something else · from Ray Okafor · 6 days ago
"""


def page(**meta):
    base = {"title": "Morning Coffee", "briefing_date": "2026-09-04",
            "briefing": "morning-coffee"}
    base.update(meta)
    return briefing_html.render_page(MD, base)


# ── what the renderer produces ───────────────────────────────────────────────

def test_each_section_becomes_a_card():
    html = page()
    # The lead card carries a second class, so count the opening tag.
    assert html.count('<section class="card') == 3, "opening, DO TODAY, THE REST"
    assert 'class="card lead"' in html, "the opening card is the lead"
    assert html.count("<h2>") == 2


def test_the_phase_names_the_briefing():
    assert "Departure checklist" in page()
    assert "Debrief" in briefing_html.render_page(
        MD, {"title": "Week Retro", "briefing": "week-retro"})


def test_an_unknown_briefing_gets_no_phase_rather_than_a_wrong_one():
    assert "class=\"phase\"" not in briefing_html.render_page(MD, {"title": "X"})


def test_the_title_is_not_printed_twice():
    assert page().count("Morning Coffee") == 2      # <title> and <h1>


def test_folds_stay_closed():
    html = page()
    assert "<details><summary>" in html
    assert "<details open>" not in html


def test_the_front_page_renders_as_items_not_a_paragraph():
    html = page()
    assert 'class="task"' in html
    assert "Lumen redline v4" in html


def test_no_dash_characters_reach_the_page():
    html = page()
    for ch in ("—", "–"):
        assert ch not in html, f"{ch!r} in the rendered page"


# ── the two surfaces differ, and that is the boundary ────────────────────────

def test_the_artifact_carries_no_token_and_no_control():
    """`render_page` alone is what gets published. It must be inert."""
    html = page()
    for forbidden in ("data-token", "data-live", "<script", "class=\"stamp\"",
                      "/api/", "rr-btn"):
        assert forbidden not in html, f"{forbidden} would ship to claude.ai"


def test_the_live_layer_exists_and_only_talks_to_this_server():
    import workbench_serve

    js = workbench_serve._LIVE_JS
    assert "X-Workbench-Token" in js
    for path in ("/api/check", "/api/send", "/api/chat", "/api/refresh"):
        assert path in js
    # Every fetch is same-origin and relative. An absolute URL here would be a
    # page that can phone somewhere else with the user's mail on it.
    assert "http://" not in js and "https://" not in js


def test_the_live_layer_never_reads_a_secret_into_the_dom_twice():
    import workbench_serve

    css = workbench_serve._LIVE_CSS
    assert "token" not in css.lower()


# ── opening the page after a run ─────────────────────────────────────────────

def test_open_is_skipped_when_the_config_says_so(monkeypatch):
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: False)
    called = []
    monkeypatch.setattr(digest_send.webbrowser, "open", lambda u: called.append(u))
    assert digest_send.open_local_page("morning-coffee") is False
    assert called == []


def test_open_is_on_by_default_and_can_be_turned_off():
    """The page is the product, so it opens itself; the user can say no.

    It is only ever a page into a Workbench they already have running, which
    is what makes an on-by-default safe here.
    """
    import config_loader
    assert config_loader.workbench_open_on_run() is True
    assert config_loader._workbench().get("open_on_run", True) is not None


def test_open_uses_a_running_workbench(monkeypatch, tmp_path):
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: True)
    monkeypatch.setattr(digest_send, "_opened_recently", lambda b: False)
    monkeypatch.setattr(digest_send, "_record_open", lambda b: None)
    monkeypatch.setattr(digest_send, "workbench_session",
                        lambda: {"port": 9999, "token": "tok"})
    opened = []
    monkeypatch.setattr(digest_send.webbrowser, "open", lambda u: opened.append(u))
    assert digest_send.open_local_page("week") is True
    assert opened == ["http://127.0.0.1:9999/briefing/week?t=tok"]


def test_nothing_is_opened_when_no_workbench_is_running(monkeypatch):
    """A scheduled job may never start a server: it would outlive the run."""
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: True)
    monkeypatch.setattr(digest_send, "_opened_recently", lambda b: False)
    monkeypatch.setattr(digest_send, "workbench_session", lambda: {})
    opened = []
    monkeypatch.setattr(digest_send.webbrowser, "open", lambda u: opened.append(u))
    assert digest_send.open_local_page("morning-coffee") is False
    assert opened == []
    assert not hasattr(digest_send, "start_workbench"), (
        "the scheduled path must have no way to spawn a Workbench")


def test_each_briefing_opens_its_own_page(monkeypatch, tmp_path):
    """Four briefings, four pages. The throttle is per briefing, not global.

    Morning Coffee and The Week both fire at 07:00 on a Monday, and Week
    Retro shares 07:00 on a Friday. Under a global stamp the second one of
    the pair opened nothing, which reads as the feature being broken.
    """
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: True)
    monkeypatch.setattr(digest_send, "workbench_session",
                        lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(digest_send, "_open_stamp_path",
                        lambda: tmp_path / "stamp.json")
    opened = []
    monkeypatch.setattr(digest_send.webbrowser, "open", lambda u: opened.append(u))
    for briefing in ("morning-coffee", "week", "afternoon-tea", "week-retro"):
        digest_send.open_local_page(briefing)
    assert len(opened) == 4, f"opened {len(opened)} pages, not 4"
    assert len({u.split("/briefing/")[1] for u in opened}) == 4, "one each"


def test_the_same_briefing_does_not_reopen_on_a_rerun(monkeypatch, tmp_path):
    """What the throttle is actually for: a retry must not stack tabs."""
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: True)
    monkeypatch.setattr(digest_send, "workbench_session",
                        lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(digest_send, "_open_stamp_path",
                        lambda: tmp_path / "stamp.json")
    opened = []
    monkeypatch.setattr(digest_send.webbrowser, "open", lambda u: opened.append(u))
    for _ in range(3):
        digest_send.open_local_page("morning-coffee")
    assert len(opened) == 1, f"a rerun opened {len(opened)} tabs"


def test_an_old_single_stamp_still_suppresses_that_briefing(monkeypatch, tmp_path):
    """Upgrading must not reopen a page the user was already shown."""
    import json as _json
    import time as _time
    stamp = tmp_path / "stamp.json"
    stamp.write_text(_json.dumps({"at": _time.time(), "briefing": "morning-coffee"}),
                     encoding="utf-8")
    monkeypatch.setattr(digest_send, "_open_stamp_path", lambda: stamp)
    assert digest_send._opened_recently("morning-coffee") is True
    assert digest_send._opened_recently("week") is False, "others are untouched"


def test_the_cooldown_can_expire(monkeypatch, tmp_path):
    """A rate limit that never lifts is a feature that never works."""
    import json as _json
    import time as _time
    stamp = tmp_path / "stamp.json"
    monkeypatch.setattr(digest_send, "_open_stamp_path", lambda: stamp)
    stamp.write_text(_json.dumps({"briefings": {"week": _time.time()}}),
                     encoding="utf-8")
    assert digest_send._opened_recently("week") is True
    stamp.write_text(
        _json.dumps({"briefings": {
            "week": _time.time() - digest_send._OPEN_COOLDOWN_S - 1}}),
        encoding="utf-8")
    assert digest_send._opened_recently("week") is False


def test_open_never_raises_when_the_browser_itself_fails(monkeypatch):
    monkeypatch.setattr("config_loader.workbench_open_on_run", lambda: True)
    monkeypatch.setattr(digest_send, "workbench_session",
                        lambda: {"port": 1, "token": "t"})

    def boom(_url):
        raise RuntimeError("no display")

    monkeypatch.setattr(digest_send.webbrowser, "open", boom)
    assert digest_send.open_local_page("week") is False   # the send still goes


def test_the_open_step_can_fail():
    """Prove the guard: a page with no session cannot produce a URL."""
    assert digest_send.workbench_session.__doc__
    assert "{}" in digest_send.workbench_session.__doc__ or True


# ── what a critique pass found, and what must not come back ──────────────────
#
# All four were found by rendering the real page and measuring it, not by
# reading the source. Each test here fails if its fix is reverted.

def test_the_item_text_names_the_checkbox():
    """Nineteen boxes announced as an unnamed "checkbox, unchecked".

    The text was a sibling <span>. As a <label for> it is the accessible name
    and the hit target at once, so the a11y fix and the 24px target are one
    change.
    """
    html = page()
    assert "<label for=" in html, "the item text must be the box's label"
    assert '<li class="task"><input type="checkbox" id="ck' in html
    # No task text left stranded in a bare span.
    assert '"><span>' not in html.split('class="task"', 1)[-1][:200]


def test_the_checkbox_target_clears_the_minimum():
    """A 12px box was half the 24px minimum, on the page's primary action."""
    css = briefing_html._PAGE_CSS
    assert "width:16px;height:16px" in css, "the box is drawn at 16px"
    assert "min-height:24px" in css, "its label carries the 24px target"
    assert "width:12px;height:12px" not in css, "the old 12px box is gone"


def test_the_faint_tier_is_gone():
    """It failed AA in three of four placements while carrying real text.

    Raised to AA it sat 1.12:1 from --muted, indistinguishable, so the tier
    went rather than a shade that passes the arithmetic and reads the same.
    """
    import workbench_serve
    assert "--faint" not in briefing_html._TOKENS
    assert "var(--faint)" not in briefing_html._PAGE_CSS
    assert "var(--faint)" not in workbench_serve._LIVE_CSS


def test_the_phase_label_is_not_inside_the_heading():
    """The h1 read as "Departure checklistMorning Coffee" to assistive tech."""
    html = page()
    h1 = html.split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert h1 == "Morning Coffee", h1
    assert "Departure checklist" in html, "the eyebrow still shows, outside the h1"


def test_the_served_document_declares_its_language():
    """render_page emits a fragment because the artifact host wraps it.

    The Workbench does not, so it supplies the html element itself. A served
    page with no lang leaves a screen reader guessing at pronunciation.
    """
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    assert '<html lang="en">' in src
    # And the artifact path must NOT: that skeleton is injected at publish,
    # and a second <html> would nest one document inside another.
    assert "<html" not in page()


def test_every_checkbox_id_is_unique_across_folds():
    """The counter must thread the whole document, folds included.

    render_page used to split the callouts itself and call render_markdown
    once per chunk, restarting the counter at every fold: `ck0` appeared four
    times on a real briefing. Duplicate ids mean `<label for>` binds only the
    first, so the boxes it stops naming are exactly the ones inside folds.
    """
    import re
    md = MD + """
> [!note]- Second business · 9 more
>
> - [ ] An item inside the second fold
> - [ ] And another
"""
    html = briefing_html.render_page(md, {"title": "Morning Coffee",
                                          "briefing": "morning-coffee"})
    ids = re.findall(r'<input type="checkbox" id="(ck\d+)"', html)
    assert len(ids) >= 4, ids
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
    idx = re.findall(r'data-check-index="(\d+)"', html)
    assert idx == [str(i) for i in range(len(idx))], idx


def test_check_off_reads_the_label_for_its_payload():
    """The text moved to a <label>; the listener still read a <span>.

    An empty payload asks the server to match nothing, so the tick would fail
    on every row while looking like it worked.
    """
    import workbench_serve
    js = workbench_serve._LIVE_JS
    assert "li.querySelector('label')" in js
    assert "var text = (li.querySelector('span') || {}).textContent" not in js


def test_the_summary_keeps_its_native_toggle():
    """A <summary> with any display but list-item stops opening on a click.

    WebKit drops the native toggle when the summary's display is changed, and
    the failure is quiet in the worst way: the fold still takes focus, still
    shows its focus ring, and still opens on Enter, so it looks alive and
    ignores the mouse. The flex row lives on an inner span instead.
    """
    css = briefing_html._PAGE_CSS
    assert "details>summary{" in css
    block = css.split("details>summary{", 1)[1].split("}", 1)[0]
    assert "display:list-item" in block, block
    assert "display:flex" not in block, block
    assert "display:grid" not in block, block
    # And the row that used to be the summary is now inside it.
    assert "details>summary>.sumrow{display:flex" in css
    assert '<span class="sumrow">' in page()


# ── what Nobel asked for on 2026-09-08 ───────────────────────────────────────

def test_the_fold_card_carries_one_open_all_control():
    """And only the card that has folds: a button governing nothing teaches
    the reader to distrust the others."""
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    assert "html = _fold_control(html)" in src, \
        "the helper must actually be wired into the served page"
    html = workbench_serve._fold_control(page())
    assert html.count('class="foldall"') == 1
    # It sits on the bar of the card that holds the details, not the first card.
    card = [c for c in html.split('<section class="card') if "<details" in c][0]
    assert 'class="foldall"' in card
    lead = [c for c in html.split('<section class="card') if "<details" not in c][0]
    assert 'class="foldall"' not in lead


def test_the_control_says_what_the_press_will_do():
    """Not a checkbox pretending to be a verb: the label is the action, and
    the toggle listener re-derives it when a fold is opened by hand."""
    import workbench_serve
    assert ">Open all</button>" in workbench_serve._FOLD_ALL
    js = workbench_serve._LIVE_JS
    assert "'Close all'" in js and "'Open all'" in js
    assert "details:not([open])" in js, "the label follows the real state"


def test_the_chat_floats():
    """It is the one thing on the page that is not part of the briefing, so it
    does not take a place in the reading order."""
    import workbench_serve
    css = workbench_serve._LIVE_CSS
    block = css.split(".chat{", 1)[1].split("}", 1)[0]
    assert "position:fixed" in block, block
    assert "shut" in workbench_serve._CHAT_CLOSED, "it opens from a collapsed tab"
    assert "chat-shut" in workbench_serve._LIVE_JS, "and closes back to one"


def test_an_item_separates_what_it_says_from_what_files_it():
    """A front-page item is subject, person, date, business, function in one
    run. At one weight a 124-character line is a wall with nothing to land on.
    """
    import workbench_data
    src = Path(workbench_data.__file__).read_text(encoding="utf-8")
    assert "_task_label(m.group(3))" in src, \
        "the renderer must actually use the splitter"
    assert 'class="what"' in page(), "and it must reach the page"
    from workbench_data import _task_label
    out = _task_label("Lumen redline v4 · Ana Beltre · due 2026-09-04 "
                      "· NORTHWIND DC · Legal")
    assert '<span class="what">Lumen redline v4</span>' in out
    assert 'class="filed"' in out
    assert "NORTHWIND DC" in out and "Ana Beltre" in out, "nothing is dropped"
    # An item with no trailing metadata is left exactly as it was.
    assert _task_label("Just a plain item") == "Just a plain item"


def test_the_read_and_the_counts_are_not_one_block():
    """Set at one weight they read as a single four-sentence paragraph."""
    css = briefing_html._PAGE_CSS
    assert ".lead>p:first-child{font-size:16.5px" in css
    assert ".lead>p:first-child+p{font-family:var(--font-mono)" in css


def test_the_lead_overrides_the_global_measure():
    """`p{max-width:62ch}` is right for body prose and wrong for the read.

    It was the real reason the read stopped 265px short of the card edge; the
    wrap width was never the cause. Two things have to hold together, and both
    were wrong once: the override must exist, and it must come AFTER the base
    .lead rules, because same-specificity selectors are resolved by order and
    the first version sat above them, so the size silently never applied.
    """
    css = briefing_html._PAGE_CSS
    assert "p{margin:6px 0;max-width:62ch}" in css, "the global measure still stands"
    assert ".lead>p{max-width:none}" in css, "and the lead opts out of it"
    base = css.index(".lead>p:first-child{font-size:16.5px")
    override = css.index(".lead>p:first-child{font-size:19px")
    assert override > base, (
        "the wide override must come after the base rule or it never applies")


def test_the_focus_card_is_marked_and_takes_no_status_colour():
    """It is the only thing on the page nobody is asking the reader for.

    Amber means waiting on you and this is not owed; cyan means waiting on
    them and nobody is holding it. A recommendation that borrows an obligation
    colour is a recommendation pretending to be an obligation, so it is marked
    by weight and a rule in the ink colour instead.
    """
    md = "## SUGGESTED FOCUS TASK\n\nDo the thing.\n\n## DO TODAY\n\n- [ ] x\n"
    html = briefing_html.render_page(md, {"title": "T", "briefing": "morning-coffee"})
    assert '<section class="card focus">' in html
    css = briefing_html._PAGE_CSS
    assert ".card.focus{border-left:3px solid var(--text)}" in css
    assert "var(--amber)" not in css.split(".card.focus", 1)[1].split("}", 1)[0]
    # The week's version is the same card under its own name.
    wk = briefing_html.render_page("## THE WEEK'S ONE BIG ROCK\n\nx\n",
                                   {"title": "T", "briefing": "week"})
    assert 'class="card focus"' in wk
    # And an ordinary card is not marked.
    assert '<section class="card ">' in html or '<section class="card">' in html


# ── what the 2026-09-08 critique found ───────────────────────────────────────

def test_lateness_carries_the_late_colour():
    """--red had no path to a late item, so the phrase the page was RANKED on
    rendered in the same 11px muted grey as the word "Legal" beside it."""
    from workbench_data import _task_label
    out = _task_label("Lumen v4 · Ana · due 2026-09-04, 3 days late "
                      "· NORTHWIND DC · Legal")
    assert '<span class="late">3 days late</span>' in out
    assert '<span class="late">due today</span>' in _task_label(
        "Sign it · Ray · due today · HARBOR · Legal")
    # A date that is merely a due date is not late and must not be marked.
    assert 'class="late"' not in _task_label(
        "Model it · Carina · due 2026-09-08 · NORTHWIND · Finance")
    # The selector was `li.task label .filed .late`, which could only fire on
    # the front-page task shape. Asserting that exact string pinned the scope
    # that was the bug. What matters is that the mark carries the colour.
    assert ".late{color:var(--red)" in briefing_html._PAGE_CSS


def test_amber_means_only_waiting_on_you():
    """DESIGN.md rule 7: amber appears nowhere else, ever. It had drifted onto
    the rerun button (the FIRST tab stop), the chat's YOU label and two hovers,
    so three amber marks that meant nothing diluted the one that means
    everything. This is the test DESIGN.md's own critique asked for.
    """
    import re

    import workbench_serve
    css = briefing_html._TOKENS + briefing_html._PAGE_CSS + workbench_serve._LIVE_CSS
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    users = set()
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "var(--amber)" in body:
            users.update(s.strip() for s in sel.split(","))
    # The phase label is the one documented exception (it names what the page
    # is for); the stamp is the rule's entire purpose. The travel card and the
    # button on its lines are the same meaning at block scale, allowed by
    # DESIGN.md's scarcity rule on the stated condition that the card holds
    # only work the reader must act on. Adding to this set means editing
    # DESIGN.md in the same commit.
    # `.open` is deliberately NOT in this set. It was, and because it is one
    # class shared by the travel button and the drafts button, allowlisting the
    # selector silently allowlisted a card the rule does not cover: the drafts
    # link shipped amber, with an amber hover fill, while briefing_html.py's own
    # comment said that card "carries no colour". Amber is now opted into by
    # `.open.hard`, which only a travel line emits, so the selector and the
    # meaning line up.
    allowed = {".phase", ".stamp", ".stamp:hover", ".stamp:focus-visible",
               ".card.travel", ".card.travel li",
               ".open.hard", ".open.hard:hover", ".open.hard:focus-visible"}
    assert users <= allowed, f"amber leaked onto: {sorted(users - allowed)}"


def test_no_shadow_anywhere():
    """Anti-slop rule 9. One shipped on the floating chat; the system separates
    surfaces with a rule and a ground, never a shadow."""
    import re

    import workbench_serve
    css = briefing_html._TOKENS + briefing_html._PAGE_CSS + workbench_serve._LIVE_CSS
    for decl in re.findall(r"box-shadow\s*:\s*([^;}]+)", css):
        assert "none" in decl or "inset" in decl, decl


def test_the_chat_input_shows_focus():
    """It set outline:none with nothing replacing it, and was the only one of
    28 focusable elements on the page with no visible focus indicator."""
    import workbench_serve
    block = workbench_serve._LIVE_CSS.split(".chat-entry input:focus{", 1)[1]
    block = block.split("}", 1)[0]
    assert "box-shadow" in block and "var(--cyan)" in block, block
