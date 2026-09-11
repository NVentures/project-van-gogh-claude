"""The three things the local briefing page can do to the world.

A tick closes an item in the user's own notes. A stamp sends one reply. Both
write somewhere the user reads by hand, so the tests here care most about the
cases where the code should REFUSE: an item it cannot place, a draft id it does
not have, a provider it does not know.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import completion_scan  # noqa: E402
import morning_coffee as mc  # noqa: E402
import send_email  # noqa: E402


WEEK_FILE = """# Week of 2026-08-31

## Open Tasks (Obsidian)

- [ ] [harbor] Price the SOW scope change from Tim Roberts
- [ ] [harbor] Send Harbor the revised SOW
- [ ] [cedar] Send Marco Lind the grid study
- [x] [cedar] Book the Cedar site walk
"""


@pytest.fixture()
def week(tmp_path, monkeypatch):
    path = tmp_path / "week.md"
    path.write_text(WEEK_FILE, encoding="utf-8")
    monkeypatch.setattr(mc, "WORKSPACE_WEEK_MD", str(path))
    return path


# ── the resolver ─────────────────────────────────────────────────────────────

def test_a_deal_bridges_on_email_without_touching_the_week_file(week):
    got = completion_scan.resolve_front_item("Anything at all", "dana@meridian.com")
    assert got == {"kind": "deal", "email": "dana@meridian.com"}


def test_a_task_resolves_to_its_raw_week_file_text(week):
    got = completion_scan.resolve_front_item("Send Harbor the revised SOW")
    assert got == {"kind": "task", "tag": "harbor",
                   "text": "Send Harbor the revised SOW"}


def test_a_near_miss_still_resolves(week):
    """The front page rewords an item; the week file has the original."""
    got = completion_scan.resolve_front_item("Send the revised SOW to Harbor")
    assert got is not None
    assert got["text"] == "Send Harbor the revised SOW"


def test_an_item_that_is_not_there_returns_none(week):
    assert completion_scan.resolve_front_item("Renegotiate the office lease") is None


def test_an_already_checked_item_is_not_offered_again(week):
    assert completion_scan.resolve_front_item("Book the Cedar site walk") is None


def test_the_tag_narrows_the_search(week):
    """Two businesses can hold similar text; the tag is the tiebreak."""
    assert completion_scan.resolve_front_item(
        "Send Harbor the revised SOW", tag="cedar") is None
    assert completion_scan.resolve_front_item(
        "Send Harbor the revised SOW", tag="harbor") is not None


def test_a_missing_week_file_resolves_to_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "WORKSPACE_WEEK_MD", str(tmp_path / "nope.md"))
    assert completion_scan.resolve_front_item("Send Harbor the revised SOW") is None


def test_the_resolver_can_fail():
    """A matcher that matches everything is not a matcher."""
    a = mc._item_tokens("Send Harbor the revised SOW")
    b = mc._item_tokens("Renegotiate the office lease")
    overlap = len(a & b) / len(a | b)
    assert overlap < completion_scan.PROJECT_MATCH_THRESHOLD, (
        "two unrelated strings score above the match threshold")


# ── mark_done is importable, not only a CLI ──────────────────────────────────

def test_mark_done_flips_the_box_and_is_idempotent(week, monkeypatch):
    monkeypatch.setattr(mc, "sync_hotcache_from_week", lambda: [])
    monkeypatch.setattr(mc, "OBSIDIAN_PROJECTS", {})
    monkeypatch.setattr(mc, "find_current_week_file", lambda: None)

    item = completion_scan.resolve_front_item("Send Harbor the revised SOW")
    first = completion_scan.mark_done([item])
    assert "Send Harbor the revised SOW" in first["marked"]
    assert "- [x] [harbor] Send Harbor the revised SOW" in week.read_text(encoding="utf-8")

    second = completion_scan.mark_done([item])
    assert second["marked"] == [], "a second tick must change nothing"


# ── the stamp ────────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.calls = []


@pytest.fixture()
def gmail(monkeypatch):
    rec = _Recorder()

    class _Drafts:
        def send(self, userId, body):
            rec.calls.append(("google", userId, body))
            return types.SimpleNamespace(execute=lambda: {"id": "sent-1"})

    class _Users:
        def drafts(self):
            return _Drafts()

    fake = types.SimpleNamespace(
        google_client=lambda label: types.SimpleNamespace(
            gmail=types.SimpleNamespace(users=lambda: _Users())))
    monkeypatch.setitem(sys.modules, "google_client", fake)
    return rec


@pytest.fixture()
def graph(monkeypatch):
    rec = _Recorder()
    fake = types.SimpleNamespace(
        microsoft_client=lambda label: types.SimpleNamespace(
            send_draft=lambda mid: rec.calls.append(("microsoft", mid)) or {}))
    monkeypatch.setitem(sys.modules, "microsoft_client", fake)
    return rec


def test_send_draft_sends_gmail_by_id(gmail):
    send_email.send_draft({"provider": "google", "label": "Gmail"}, "r-123")
    assert gmail.calls == [("google", "me", {"id": "r-123"})]


def test_send_draft_sends_graph_by_id(graph):
    send_email.send_draft({"provider": "microsoft", "label": "Outlook"}, "AAMk-9")
    assert graph.calls == [("microsoft", "AAMk-9")]


def test_send_draft_refuses_an_empty_id(gmail):
    with pytest.raises(RuntimeError, match="no draft id"):
        send_email.send_draft({"provider": "google", "label": "Gmail"}, "")
    assert gmail.calls == [], "nothing may reach the wire without an id"


def test_send_draft_refuses_an_unknown_provider():
    with pytest.raises(RuntimeError, match="unknown sender provider"):
        send_email.send_draft({"provider": "carrier-pigeon", "label": "X"}, "id")


def test_send_draft_sends_the_stored_draft_not_a_new_message(gmail, graph):
    """The whole point: no body is composed, so a user edit survives."""
    send_email.send_draft({"provider": "google", "label": "Gmail"}, "r-1")
    body = gmail.calls[0][2]
    assert set(body) == {"id"}, f"a body was composed: {body}"


# ── sending needs a scope that asks for it ───────────────────────────────────

def test_google_asks_for_permission_to_send():
    """gmail.modify covers reading, drafting and labelling but NOT sending.

    The page's stamp calls drafts().send, which needs gmail.send (or compose).
    Shipped without it the stamp fails with a 403 the first time it is pressed,
    on the one action the whole surface exists for.
    """
    import auth_bootstrap
    import google_client
    send = "https://www.googleapis.com/auth/gmail.send"
    assert send in auth_bootstrap.GOOGLE_SCOPES, "consent must request it"
    assert send in google_client.GOOGLE_SCOPES, "and the client must carry it"
    # The client's scopes must remain a superset of what consent requested, or
    # a refresh asks for more than the token was granted.
    assert set(auth_bootstrap.GOOGLE_SCOPES) <= set(google_client.GOOGLE_SCOPES)


def test_microsoft_already_asks_to_send():
    import auth_bootstrap
    import microsoft_client
    send = "https://graph.microsoft.com/Mail.Send"
    assert send in auth_bootstrap.MS_SCOPES
    assert send in microsoft_client.GRAPH_SCOPES


def test_a_token_minted_before_the_scope_says_so():
    """A bare 403 reads as "the send failed" when the truth is "this install
    was never allowed to send", which is a re-consent, not a retry."""
    import send_email

    class Resp:
        status = 403

    class Denied(Exception):
        resp = Resp()

    assert send_email._is_missing_send_scope(
        Denied("Request had insufficient authentication scopes."))
    # A 403 for any other reason must NOT be reported as a scope problem:
    # telling someone to re-consent when they are rate limited wastes the one
    # action they were given.
    assert not send_email._is_missing_send_scope(
        Denied("User rate limit exceeded"))

    class Other(Exception):
        resp = type("R", (), {"status": 500})()

    assert not send_email._is_missing_send_scope(Other("insufficient scope"))


# ── feedback from the foot of the page ───────────────────────────────────────

def test_feedback_goes_to_the_reader_not_an_author():
    """The plugin has no server. A note about the reader's own briefing is
    theirs, and it lands in the inbox they already read."""
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    fn = src.split("def _feedback(", 1)[1].split("\ndef ", 1)[0]
    assert "config_primary_account()" in fn
    assert 'account["email"]' in fn, "recipient is the sender's own address"
    # No hardcoded address anywhere: this ships to other people.
    assert "nobeljchang" not in src
    assert "@gmail.com" not in src


def test_feedback_refuses_an_empty_or_oversized_note():
    """Both refusals must happen BEFORE the send is attempted.

    Asserting only that an error came back is vacuous on a machine with no
    OAuth, where every send fails anyway: the first version of this test
    passed with the size cap deleted. So assert the specific refusal.
    """
    import workbench_serve
    assert workbench_serve._feedback("morning-coffee", "   ") == {
        "error": "nothing to send"}
    assert workbench_serve._feedback("morning-coffee", "x" * 20001) == {
        "error": "too long to send"}


def test_the_artifact_has_no_feedback_control():
    """Same boundary as every other control: the shared page cannot act."""
    import briefing_html
    html = briefing_html.render_page("# T\n\ntext\n",
                                     {"title": "T", "briefing": "morning-coffee"})
    for forbidden in ('class="fb"', "/api/feedback", "fb-send"):
        assert forbidden not in html, forbidden


def test_the_stamp_clock_uses_tzinfo_not_zone_names():
    """fmt_local_time calls astimezone, which rejects a zone NAME.

    Passing strings threw a TypeError on every stamp this module writes (the
    tick, the send, the feedback receipt) and nothing caught it, because no
    test called the function and the failure only appears on a live press.
    """
    import workbench_serve
    out = workbench_serve._now_pt_et()
    assert out and ":" in out, out
    assert "TypeError" not in out
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    fn = src.split("def _now_pt_et(", 1)[1].split("\ndef ", 1)[0]
    assert "user_tz()" in fn and '"America/Los_Angeles"' not in fn


def test_one_status_line_per_row():
    """The tick appended its span to the <li> while the send replaced the
    stamp inside .act, so ticking then sending one item left two spans
    contradicting each other ("OAuth required" beside "not found in your week
    file"). On a page whose authority is saying exactly what happened, a row
    saying two incompatible things is the worst available failure.
    """
    import workbench_serve
    js = workbench_serve._LIVE_JS
    assert "function rowState(row)" in js
    # Neither handler may build its own span any more.
    assert js.count("s.className = 'state'") == 1, "one place creates a status"
    assert "var mark = rowState(" in js
    assert "wrap.replaceChild(mark, btn)" not in js


def test_the_send_failure_does_not_double_its_period():
    """It rendered "install-van-gogh.. The draft is still in Drafts." """
    import workbench_serve
    assert ".replace(/\\.\\s*$/, '')" in workbench_serve._LIVE_JS


# ── the chat can read the vault, and nothing else ────────────────────────────

def test_the_chat_call_is_read_only_by_flag_not_by_prompt():
    """Every guarantee the page makes about the chat is a CLI flag.

    A prompt telling a model not to send is necessary and never sufficient;
    the same class as the em-dash rule. --restricted removes the
    command-running tools AND ignores the user's own settings files, so a
    personal permission rule cannot widen this one call. --tools is a
    whitelist: Write and Edit are absent, so the session cannot alter the
    vault it is reading, and nothing in the set can execute anything, which is
    why sending is impossible structurally rather than by good judgement.
    """
    import claude_cli
    flags = claude_cli._READ_ONLY_FLAGS
    assert "--restricted" in flags
    assert "--permission-prompts" in flags
    assert flags[flags.index("--permission-prompts") + 1] == "none"
    assert flags[flags.index("--tools") + 1] == "Read,Grep,Glob"
    for banned in ("Write", "Edit", "Bash", "WebFetch", "--add-dir"):
        assert banned not in " ".join(flags), banned
    # bypassPermissions is refused by --restricted; never ask for it here.
    assert "bypassPermissions" not in " ".join(flags)
    # The fork-bomb guards from the non-agentic path carry over: skills are not
    # gated by --tools, and an auto-invoked skill re-enters this pipeline.
    assert "--disable-slash-commands" in flags
    assert "--strict-mcp-config" in flags


def test_the_vault_is_the_working_directory_which_is_what_confines_it():
    """--restricted allows the file tools only inside the working directories.
    No --add-dir is passed, so the call cannot reach the rest of the disk."""
    import claude_cli
    src = Path(claude_cli.__file__).read_text(encoding="utf-8")
    fn = src.split("def run_claude_readonly(", 1)[1].split("\ndef ", 1)[0]
    assert "cwd=str(root)" in fn
    assert "ANTHROPIC_API_KEY" in fn, "subscription routing, as everywhere else"


def test_the_chat_uses_the_read_only_path_and_never_the_agentic_render():
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    fn = src.split("def _chat(", 1)[1].split("\ndef ", 1)[0]
    assert "claude_cli.run_claude_readonly(prompt, root" in fn
    assert "bypassPermissions" not in fn
    # The old lie is gone: it no longer claims to have no tools, because it has.
    assert "You have NO tools" not in fn
    assert "You can read this vault" in fn


def test_tool_markup_never_reaches_the_page():
    """The model may now genuinely use tools, so a reply can still narrate one.
    The strip stays: the reader wants the answer, not the transcript."""
    import workbench_serve as w
    src = Path(w.__file__).read_text(encoding="utf-8")
    assert "raw = _clean_reply(proc.stdout" in src, "the strip is on the path"
    raw = ("<function_calls>\n<invoke name=\"Grep\">\n"
           "<parameter name=\"pattern\">x</parameter>\n</invoke>\n"
           "</function_calls>\n\nIt is a solar project.")
    out = w._clean_reply(raw)
    for bad in ("<function_calls>", "<invoke", "<parameter"):
        assert bad not in out, bad
    assert "It is a solar project." in out


def test_the_answer_names_the_files_it_opened():
    """A sourced answer can be checked against the notes it came from."""
    import workbench_serve as w
    body, cited = w._split_sources(
        "Sunfield is a solar project.\nSOURCES: wiki/entities/Halcyon.md, "
        "wiki/hotcache.md")
    assert body == "Sunfield is a solar project."
    assert cited == "wiki/entities/Halcyon.md, wiki/hotcache.md"
    # Answering from the briefing alone is a real answer, not a failure.
    body, cited = w._split_sources("It does not say.\nSOURCES: none")
    assert body == "It does not say."
    assert cited == ""
    # A reply with no SOURCES line survives intact.
    assert w._split_sources("Plain.")[0] == "Plain."


# ── a deck asked for in chat ─────────────────────────────────────────────────

def test_a_deck_request_is_told_apart_from_a_question_about_one():
    """A false positive turns a question into a ticket, which is worse than
    missing a request the reader can rephrase."""
    import workbench_serve as w
    for yes in ("make me a deck on Northwind",
                "build a 5 slide presentation for the board",
                "draft slides for Harbor Solar",
                "create a pptx about the indemnity cap"):
        assert w._wants_a_deck(yes), yes
    for no in ("what does the deck say?", "what is Sunfield?",
               "who is waiting on me?", "summarise the presentation Ana sent"):
        assert not w._wants_a_deck(no), no


def test_chat_staging_writes_no_file_and_waits_for_the_nod():
    """Staging is the gate. The chat moves the button to where the reader
    asked; it does not remove it."""
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    fn = src.split("def _stage_from_chat(", 1)[1].split("\ndef ", 1)[0]
    assert '"staged"' in fn and "deliverable.stage_plan" in fn
    # Nothing in the staging path may build or approve.
    assert "deliverable.build" not in fn
    assert '"approved"' not in fn
    # The reply reads the slide's own key. `title` is the DECK's, and reading
    # it rendered every line as "Untitled" while the plan itself was fine.
    assert "sl.get('heading')" in fn


def test_the_download_route_is_token_gated_and_ledger_bound():
    """Two separate holes. The first version checked no token at all, so
    anything that could reach the port could pull a finished deck. And a route
    that took a PATH would serve any file on the machine, so the path comes
    from the ledger and must still resolve inside the deliverables folder.
    """
    import workbench_serve
    src = Path(workbench_serve.__file__).read_text(encoding="utf-8")
    disp = src.split('if path.startswith("/download/"):', 1)[1].split("return", 1)[0]
    assert "self._authed(query)" in disp, "the download route must check the token"

    fn = src.split("def _serve_delivery(", 1)[1].split("\ndef ", 1)[0]
    assert '(ticket.get("delivery") or {}).get("path")' in fn, "path from the ledger"
    assert "root not in path.parents" in fn, "and confined to deliverables"
    assert "query" not in fn.split("def ")[0].split("\n")[0], "never a path from the request"


def test_the_chat_footer_states_the_real_boundary():
    """It has been wrong in both directions: it once claimed reach the call
    did not have ("it can read the vault" when the call had no tools), then
    claimed a limit the call no longer has ("it cannot search")."""
    import workbench_serve
    js = workbench_serve._LIVE_JS
    assert "It reads your vault to answer" in js
    assert "cannot change a note" in js
    assert "or send" in js
    assert "It cannot search" not in js, "it can search now"
    assert "write drafts" not in js, "it cannot write"
