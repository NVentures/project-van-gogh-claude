"""The advisor: what it may suggest, what counts as a yes, and what a yes does.

Nothing here reaches a model, a mailbox or a real vault. The look is a
planted reply, the vault is a temp folder, and the Sent folder is a list.

The tests that matter most are the refusals. A suggestion engine that is
wrong in the generous direction drafts mail to an invented address, edits a
page on the strength of a quote that was never there, or reads a stranger's
"Y" as the user's. Each of those has a test sitting past its edge, and each
names what to delete to watch it fail.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import advisor
import workbench_store as store

NOW = datetime(2026, 9, 9, 9, 0)            # a Wednesday, after 08:30

# Written as code points so a sweep for these characters can never rewrite
# the very thing these tests plant.
EM, EN = chr(0x2014), chr(0x2013)


# ── Plumbing ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    """A vault, a ledger, a logs folder and a state folder, all temporary."""
    import config_loader as cl
    import user_state

    vault = tmp_path / "vault"
    (vault / "wiki" / "deals").mkdir(parents=True)
    (vault / "van-gogh").mkdir()
    (vault / "wiki" / "deals" / "kestrel.md").write_text(
        "# Kestrel\n\n"
        "Dana Park (dana@kestrel.example) asked for the site visit dates\n"
        "on the 14th.   We promised an answer by Friday.\n\n"
        "- [ ] Send Dana the site visit dates\n",
        encoding="utf-8")

    monkeypatch.setattr(advisor, "_vault", lambda: vault)
    monkeypatch.setattr(store, "van_gogh_root", lambda: vault / "van-gogh")
    monkeypatch.setattr(store, "deal_critical_domains", lambda: set())
    monkeypatch.setattr(cl, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(user_state, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(cl, "advisor_enabled", lambda: True)
    monkeypatch.setattr(cl, "advisor_time", lambda: "08:30")
    monkeypatch.setattr(cl, "advisor_daily_cap", lambda: 3)
    monkeypatch.setattr(cl, "advisor_allow_send", lambda: False)
    monkeypatch.setattr(advisor, "situation", lambda now=None: {
        "front_page": [], "accounts": ["Gmail"]})
    # No email unless a test asks for one.
    monkeypatch.setattr(advisor, "_mail",
                        lambda subject, body: pytest.fail("mailed"))
    return vault


QUOTE = "We promised an answer by Friday."


def draft(**kw):
    base = {"kind": "draft", "title": "Answer Dana on the site visit",
            "why": "You promised Dana an answer by Friday.",
            "plan": "Draft a short reply offering two dates.",
            "evidence": [{"path": "wiki/deals/kestrel.md", "quote": QUOTE}],
            "to": "dana@kestrel.example", "to_name": "Dana Park",
            "account": "Gmail", "subject": "Site visit dates",
            "brief": "Offer two dates for the site visit."}
    base.update(kw)
    return base


def edit(**kw):
    base = {"kind": "vault", "title": "Mark the Kestrel dates as sent",
            "why": "The record still shows it open.",
            "plan": "Tick the item on the Kestrel page.",
            "evidence": [{"path": "wiki/deals/kestrel.md", "quote": QUOTE}],
            "edits": [{"path": "wiki/deals/kestrel.md",
                       "find": "- [ ] Send Dana the site visit dates",
                       "replace": "- [x] Send Dana the site visit dates"}]}
    base.update(kw)
    return base


def reply(*proposals):
    """A model session's answer, with prose in front as the CLI often has."""
    text = "Here is what I found.\n" + json.dumps({"proposals": list(proposals)})
    return lambda prompt: SimpleNamespace(returncode=0, stdout=text, stderr="")


def check(raw):
    return advisor.validate(raw, advisor.situation())


# ── Reading an answer ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("Y 1", [("yes", [1])]),
    ("y 1 3", [("yes", [1, 3])]),
    ("Yes 1, 3", [("yes", [1, 3])]),
    ("yes 1 and 2", [("yes", [1, 2])]),
    ("N 2", [("no", [2])]),
    ("No.", [("no", None)]),
    ("Y ALL", [("yes", "all")]),
    ("y all!", [("yes", "all")]),
    ("SEND 1", [("send", [1])]),
    ("Y 1\nN 2", [("yes", [1]), ("no", [2])]),
    ("\n\n  Y 2  \n", [("yes", [2])]),
])
def test_an_answer_is_read(text, want):
    assert advisor.parse_commands(text) == want


@pytest.mark.parametrize("text", [
    "Sure, go ahead with the first one",
    "Yes please",
    "Not yet",
    "Y1",
    "Yeah 1",
    "I think Y 1",
    "Y 1 because it matters",
    "",
])
def test_anything_that_is_not_exactly_an_answer_is_not_one(text):
    """Narrow on purpose. Each of these costs the user a second, shorter
    reply. Read generously, one of them would cost an action nobody asked for.
    """
    assert advisor.parse_commands(text) == []


def test_the_quoted_original_underneath_is_never_read():
    """The email being replied to is full of examples like `Y 1 3`. Reading
    stops at the first line that is not an answer, so only the user's own
    leading lines count.

    MUTATION: change `break` to `continue` in parse_commands and this fails
    with the quoted `Y ALL` read as the user's.
    """
    text = ("N 2\n\nOn Wed, Sep 9, Van Gogh wrote:\n"
            "> A reply of `Y 1 3` is a yes\n> Y ALL\nY ALL\n")
    assert advisor.parse_commands(text) == [("no", [2])]


def test_the_suggestion_email_itself_reads_as_no_answer(world):
    ticket, _ = check(draft())
    body = advisor.email_body([ticket], "VG-ABC123", allow_send=True)
    assert "VG-ABC123" in body
    assert advisor.parse_commands(body) == []


# ── Paths stay inside the vault ──────────────────────────────────────────────

@pytest.mark.parametrize("rel", ["/etc/passwd", "../outside.md",
                                 "wiki/../../outside.md", "", None])
def test_a_path_that_leaves_the_vault_is_refused(world, rel, tmp_path):
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    assert advisor._vault_file(rel, world) is None


def test_a_link_that_points_out_of_the_vault_is_refused(world, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = world / "wiki" / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this platform will not make a symlink here")
    assert advisor._vault_file("wiki/link.md", world) is None


# ── Checking a suggestion ────────────────────────────────────────────────────

def test_a_well_formed_draft_becomes_a_ticket_waiting_on_a_nod(world):
    ticket, reason = check(draft())
    assert reason == ""
    assert ticket["state"] == "staged" and ticket["source"] == "advisor"
    # Not the ledger's `email` type: the page draws SEND on a waiting email
    # ticket, and a yes here only writes a draft.
    assert ticket["type"] == "draft"
    assert ticket["staged"]["to"] == "dana@kestrel.example"


def test_evidence_survives_a_change_of_whitespace_only(world):
    spaced = "on the 14th. We promised an answer by Friday."
    ticket, reason = check(draft(evidence=[
        {"path": "wiki/deals/kestrel.md", "quote": spaced}]))
    assert ticket is not None, reason


def test_a_tidied_quote_is_not_evidence(world):
    """A model asked to quote improves what it quotes. The page says
    "We promised an answer by Friday."; this says the same thing better, and
    it is not on the page.

    MUTATION: make `_checked_evidence` keep an entry without the
    `quote in _norm(page)` test and this fails.
    """
    tidy = "We promised Dana an answer by Friday."
    ticket, reason = check(draft(evidence=[
        {"path": "wiki/deals/kestrel.md", "quote": tidy}]))
    assert ticket is None
    assert "evidence" in reason


def test_a_quote_too_short_to_mean_anything_is_not_evidence(world):
    ticket, _ = check(draft(evidence=[
        {"path": "wiki/deals/kestrel.md", "quote": "Friday."}]))
    assert ticket is None


def test_an_address_the_vault_does_not_hold_is_refused(world):
    """The expensive mistake: a draft to an address the model worked out.

    MUTATION: delete the `who_email not in _known_addresses` check in
    validate() and this fails.
    """
    ticket, reason = check(draft(to="dana.park@kestrel-energy.example"))
    assert ticket is None
    assert "address" in reason


def test_an_address_from_the_front_page_is_accepted(world, monkeypatch):
    monkeypatch.setattr(advisor, "situation", lambda now=None: {
        "front_page": [{"address": "lee@acme.example"}], "accounts": ["Gmail"]})
    ticket, reason = check(draft(to="lee@acme.example"))
    assert ticket is not None, reason


def test_a_header_smuggled_into_an_address_is_refused(world):
    ticket, _ = check(draft(to="dana@kestrel.example\nBcc: x@evil.example"))
    assert ticket is None


def test_dashes_never_reach_a_person(world):
    ticket, _ = check(draft(title=f"Answer Dana {EM} the site visit",
                            why=f"Promised {EN} by Friday."))
    assert EM not in json.dumps(ticket, ensure_ascii=False)
    assert EN not in json.dumps(ticket, ensure_ascii=False)


def test_an_unknown_kind_of_work_is_refused(world):
    ticket, reason = check(draft(kind="send"))
    assert ticket is None and "kind" in reason


def test_a_file_type_nothing_can_build_is_refused(world):
    raw = draft(kind="file", file_type="xlsx", done_when="A model.")
    ticket, reason = check(raw)
    assert ticket is None and "xlsx" in reason


def test_a_vault_edit_that_applies_cleanly_is_accepted(world):
    ticket, reason = check(edit())
    assert ticket is not None, reason
    assert ticket["type"] == "vault"


@pytest.mark.parametrize("change,why", [
    ({"find": "Dana"}, "twice on the page"),
    ({"find": "text that is not there"}, "not on the page"),
    ({"path": "van-gogh/config.json"}, "the product's own folder"),
    ({"path": "wiki/deals/new-page.md"}, "a page that does not exist"),
    ({"path": "../outside.md"}, "outside the vault"),
    ({"replace": "- [ ] Send Dana the site visit dates"}, "changes nothing"),
    ({"replace": "x" * 2001}, "too large to be a correction"),
])
def test_a_vault_edit_that_is_not_a_clean_correction_is_refused(world, change, why):
    """MUTATION: drop the `page.count(find) != 1` test in `_checked_edits`
    and the first two rows fail."""
    row = dict(edit()["edits"][0], **change)
    ticket, _ = check(edit(edits=[row]))
    assert ticket is None, why


@pytest.mark.parametrize("folder", ["van-gogh", ".obsidian"])
def test_only_a_note_may_be_edited(world, folder):
    """A markdown file that exists, with the text on it exactly once, in a
    folder that is machinery rather than notes. Everything about it is valid
    except where it lives, so only the folder rule can refuse it.

    MUTATION: delete the folder check in `_editable` and this fails. The row
    above naming `van-gogh/config.json` does not catch that on its own: it is
    refused for not being markdown, which is a different rule.
    """
    target = world / folder / "page.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- [ ] Send Dana the site visit dates\n", encoding="utf-8")
    row = dict(edit()["edits"][0], path=f"{folder}/page.md")
    ticket, reason = check(edit(edits=[row]))
    assert ticket is None
    assert "may not be changed" in reason


def test_one_bad_edit_rejects_the_whole_suggestion(world):
    good = edit()["edits"][0]
    bad = dict(good, find="text that is not there")
    ticket, _ = check(edit(edits=[good, bad]))
    assert ticket is None


# ── The day's look ───────────────────────────────────────────────────────────

def test_a_look_files_what_survives_and_mails_once(world, monkeypatch):
    mailed = []
    monkeypatch.setattr(advisor, "_mail", lambda s, b: mailed.append((s, b)))
    monkeypatch.setattr(advisor, "reply_account",
                        lambda: {"label": "Gmail", "provider": "google"})
    invented = draft(title="Chase Lee", to="lee@nowhere.example",
                     subject="Checking in")
    out = advisor.propose(NOW, run=reply(draft(), invented, edit()))

    assert out["asked"] == 2
    assert len(out["dropped"]) == 1
    filed = store.by_source("advisor")
    assert sorted(t["state"] for t in filed) == ["staged", "staged"]
    assert len(mailed) == 1
    subject, body = mailed[0]
    assert "Two suggestions" in subject
    code = subject[subject.index("[") + 1:subject.index("]")]
    batch = advisor._read_state()["batches"][code]
    assert batch["items"] == [t["id"] for t in out["tickets"]]
    assert "Chase Lee" not in body, "a refused suggestion never reaches the reader"


def test_the_cap_is_a_cap(world, monkeypatch):
    monkeypatch.setattr(advisor, "_mail", lambda s, b: None)
    many = [draft(title=f"Thing {i}", subject=f"Subject {i}") for i in range(6)]
    out = advisor.propose(NOW, run=reply(*many))
    assert out["asked"] == 3


def test_a_quiet_day_mails_nothing(world):
    out = advisor.propose(NOW, run=reply())
    assert out["asked"] == 0
    assert out["why"] == "nothing was worth suggesting"


def test_a_no_is_remembered(world, monkeypatch):
    """MUTATION: have `add_suggestion` overwrite an existing ticket and this
    fails: the declined suggestion comes back the next morning."""
    monkeypatch.setattr(advisor, "_mail", lambda s, b: None)
    advisor.propose(NOW, run=reply(draft()))
    tid = store.by_source("advisor")[0]["id"]
    store.transition(tid, "dismissed", "no")

    out = advisor.propose(NOW + timedelta(days=1), run=reply(draft()))
    assert out["asked"] == 0
    assert store.get(tid)["state"] == "dismissed"


def test_the_look_happens_once_a_day_and_not_before_its_time(world, monkeypatch):
    monkeypatch.setattr(advisor, "_mail", lambda s, b: None)
    early = advisor.propose(datetime(2026, 9, 9, 7, 0), run=reply(draft()))
    assert early["why"] == "before the time it is set to look"
    assert advisor.propose(NOW, run=reply(draft()))["asked"] == 1
    again = advisor.propose(NOW + timedelta(hours=1),
                            run=reply(draft(title="Other", subject="Other")))
    assert again["why"] == "already looked today"


def test_a_look_that_dies_still_spends_the_day(world):
    """The stamp is written before the session starts. A look that hangs or
    crashes must not be tried again every half hour.

    MUTATION: move the `state["day"] = ...` block below `run(prompt)` in
    propose() and this fails.
    """
    def boom(prompt):
        raise RuntimeError("the session died")
    out = advisor.propose(NOW, run=boom)
    assert out["asked"] == 0
    assert advisor.propose(NOW + timedelta(minutes=30),
                           run=reply(draft()))["why"] == "already looked today"


def test_a_dry_run_files_and_mails_nothing(world):
    out = advisor.propose(NOW, dry_run=True, run=reply(draft()))
    assert out["asked"] == 1
    assert store.by_source("advisor") == []
    assert advisor._read_state() == {}


def test_an_answer_that_is_not_json_is_a_failed_look_not_a_crash(world):
    out = advisor.propose(NOW, run=lambda p: SimpleNamespace(
        returncode=0, stdout="I could not decide.", stderr=""))
    assert out["asked"] == 0 and "could not be read" in out["why"]


def test_the_prompt_carries_the_agents_own_rules(world):
    prompt = advisor.build_prompt({"accounts": ["Gmail"]}, 3)
    assert "Quote the vault exactly" in prompt
    assert not prompt.lstrip().startswith("---"), "frontmatter is not a rule"
    assert "At most 3 proposals" in prompt


# ── Answers from the Sent folder ─────────────────────────────────────────────

@pytest.fixture
def asked(world, monkeypatch):
    """Two suggestions filed and mailed, with the batch code."""
    monkeypatch.setattr(advisor, "_mail", lambda s, b: None)
    monkeypatch.setattr(advisor, "reply_account",
                        lambda: {"label": "Gmail", "provider": "google"})
    out = advisor.propose(NOW, run=reply(draft(), edit()))
    code = next(iter(advisor._read_state()["batches"]))
    return SimpleNamespace(code=code, ids=[t["id"] for t in out["tickets"]])


def sent(*messages):
    rows = [{"id": f"m{i}", "text": text} for i, text in enumerate(messages)]
    return lambda account, code: rows


def test_a_yes_and_a_no_are_acted_on(asked):
    done = advisor.collect_answers(NOW, fetch=sent("Y 1\nN 2\n\n> quoted"))
    assert [(r["ok"], r["did"]) for r in done][0] == (True, "approved")
    assert store.get(asked.ids[0])["state"] == "approved"
    assert store.get(asked.ids[1])["state"] == "dismissed"


def test_the_same_reply_is_acted_on_once(asked):
    """MUTATION: remove the `seen` claim in collect_answers and the second
    call acts again, which for SEND is a second email."""
    fetch = sent("Y 1")
    assert len(advisor.collect_answers(NOW, fetch=fetch)) == 1
    assert advisor.collect_answers(NOW, fetch=fetch) == []


def test_a_bare_yes_means_nothing_when_there_are_two_things(asked):
    assert advisor.collect_answers(NOW, fetch=sent("Y")) == []
    assert store.get(asked.ids[0])["state"] == "staged"


def test_a_number_that_names_nothing_is_ignored(asked):
    assert advisor.collect_answers(NOW, fetch=sent("Y 7")) == []


def test_a_mailbox_that_cannot_be_read_leaves_the_question_open(asked):
    def boom(account, code):
        raise RuntimeError("token expired")
    assert advisor.collect_answers(NOW, fetch=boom) == []
    assert asked.code in advisor._read_state()["batches"]
    assert len(advisor.collect_answers(NOW, fetch=sent("Y 1"))) == 1


def test_a_week_old_question_stops_listening(asked):
    late = NOW + timedelta(days=8)
    assert advisor.collect_answers(late, fetch=sent("Y ALL")) == []
    assert advisor._read_state()["batches"] == {}
    assert store.get(asked.ids[0])["state"] == "staged"


def test_replies_are_read_from_sent_and_nowhere_else():
    """The whole trust argument: a message in Sent was sent by whoever holds
    the account. If this ever reads an inbox, anyone who can email the user
    can answer for them."""
    calls = []

    class Gmail:
        def users(self): return self
        def messages(self): return self

        def list(self, **kw):
            calls.append(kw)
            return SimpleNamespace(execute=lambda: {"messages": [{"id": "a"},
                                                                 {"id": "b"}]})

        def get(self, id, **kw):
            labels = ["SENT"] if id == "a" else ["INBOX"]
            return SimpleNamespace(execute=lambda: {
                "labelIds": labels,
                "payload": {"mimeType": "text/plain", "body": {"data": "WSAx"}}})

    import google_client
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr(google_client, "google_client",
                   lambda label: SimpleNamespace(gmail=Gmail()))
        out = advisor.fetch_replies({"provider": "google", "label": "Gmail"},
                                    "VG-ABC123")
    finally:
        mp.undo()
    assert calls[0]["q"] == 'in:sent "VG-ABC123"'
    assert out == [{"id": "a", "text": "Y 1"}], "the inbox copy was not read"


def test_an_html_reply_is_read_as_its_text():
    markup = "<div>Y 1</div><div><br></div><div class='q'>On Wed wrote:</div>"
    assert advisor.parse_commands(advisor._html_to_text(markup)) == [("yes", [1])]


# ── The same list, inside a briefing ─────────────────────────────────────────

def test_the_same_list_always_travels_under_the_same_reference():
    """A briefing is rendered in one process and mailed by another. A
    reference minted at send time could number the list differently from the
    page it was printed on; one derived from the ordered ids cannot."""
    assert advisor.batch_code(["a", "b"]) == advisor.batch_code(["a", "b"])
    assert advisor.batch_code(["a", "b"]) != advisor.batch_code(["b", "a"])
    assert advisor.batch_code(["a", "b"]) != advisor.batch_code(["a"])


def test_a_briefing_carries_what_is_waiting_and_a_reply_to_it_is_read(asked, monkeypatch):
    """The whole loop through a briefing instead of the suggestions email:
    the numbers printed in the section are the numbers a reply acts on.

    MUTATION: have `briefing` number its rows in a different order from the
    one it registers (reverse `ids` in its register_batch call) and this
    fails with the wrong ticket approved.
    """
    block = advisor.briefing(NOW)
    assert block["md"].startswith("## Suggested")
    assert [w["n"] for w in block["waiting"]] == [1, 2]
    assert [w["id"] for w in block["waiting"]] == asked.ids
    code = advisor.batch_code(asked.ids)
    assert code in block["md"]
    assert advisor.parse_commands(block["md"]) == [], "the section is not an answer"

    seen = {}

    def fetch(account, wanted):
        seen["code"] = wanted
        return [{"id": "r1", "text": "N 1\n\n> " + block["md"]}]
    done = advisor.collect_answers(NOW, fetch=fetch)
    assert seen["code"] == code
    assert len(done) == 1
    assert store.get(block["waiting"][0]["id"])["state"] == "dismissed"
    assert store.get(block["waiting"][1]["id"])["state"] == "staged"


def test_a_briefing_on_a_quiet_day_carries_nothing(world):
    assert advisor.briefing(NOW) == {"md": "", "waiting": []}


def test_a_briefing_never_promises_a_reply_nothing_will_read(world, monkeypatch):
    """Replies are read by the same tick that makes the daily look. With the
    look switched off, a section that said "reply Y 1" would be a promise
    nothing keeps.

    MUTATION: drop the `advisor_enabled()` test in `briefing` and this fails.
    """
    import config_loader as cl
    monkeypatch.setattr(cl, "advisor_enabled", lambda: False)
    monkeypatch.setattr(advisor, "reply_account",
                        lambda: {"label": "Gmail", "provider": "google"})
    file_one(draft())
    md = advisor.briefing(NOW)["md"]
    assert "Y 1" not in md and "VG-" not in md
    assert "/van-gogh:suggest" in md
    assert advisor._read_state().get("batches", {}) == {}


def test_afternoon_tea_can_say_what_was_done_today(world):
    tid = file_one(edit())
    store.transition(tid, "approved", "yes")
    assert advisor.execute(tid)["ok"]
    today = datetime.fromisoformat(store.get(tid)["delivery"]["at"])
    md = advisor.briefing(today)["md"]
    assert "Done today on your yes:" in md
    assert "Corrected wiki/deals/kestrel.md" in md
    assert "Nothing below has been done yet" not in md
    assert advisor.briefing(today + timedelta(days=1)) == {"md": "", "waiting": []}


def test_the_briefing_meta_carries_the_section(world):
    import config_loader as cl
    file_one(draft())
    meta = cl._suggestions()
    assert meta["suggestions_md"].startswith("## Suggested")
    assert meta["suggestions"][0]["n"] == 1


def test_a_broken_advisor_cannot_stop_a_briefing(monkeypatch):
    import config_loader as cl
    monkeypatch.setattr(advisor, "briefing",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert cl._suggestions() == {"suggestions_md": "", "suggestions": []}


# ── Doing the work ───────────────────────────────────────────────────────────

def file_one(raw):
    ticket, reason = check(raw)
    assert ticket is not None, reason
    store.add_suggestion(ticket)
    return ticket["id"]


def test_a_yes_to_a_vault_edit_changes_the_page_and_keeps_the_old_one(world):
    tid = file_one(edit())
    store.transition(tid, "approved", "yes")
    row = advisor.execute(tid)

    assert row["ok"], row["did"]
    page = (world / "wiki" / "deals" / "kestrel.md").read_text(encoding="utf-8")
    assert "- [x] Send Dana the site visit dates" in page
    ticket = store.get(tid)
    assert ticket["state"] == "delivered"
    kept = list((advisor.backups_dir()).rglob("kestrel.md"))
    assert len(kept) == 1
    assert "- [ ] Send Dana" in kept[0].read_text(encoding="utf-8")


def test_a_page_that_changed_since_the_morning_is_left_alone(world):
    """The yes was given to the page as it stood. If the text is gone or now
    appears twice, the edit no longer means what was approved.

    MUTATION: have `_do_vault` trust the stored edits without re-checking
    and this fails with the page rewritten.
    """
    tid = file_one(edit())
    page = world / "wiki" / "deals" / "kestrel.md"
    changed = page.read_text(encoding="utf-8").replace(
        "- [ ] Send Dana the site visit dates", "- [ ] Send Dana two dates")
    page.write_text(changed, encoding="utf-8")
    store.transition(tid, "approved", "yes")

    row = advisor.execute(tid)
    assert not row["ok"]
    assert page.read_text(encoding="utf-8") == changed
    assert store.get(tid)["state"] == "failed"
    assert not advisor.backups_dir().exists()


def test_a_ticket_is_done_once_however_many_callers_reach_it(world):
    """The move to `running` is the claim. A Workbench press and a watcher
    tick can both reach an approved ticket; only one may do the work."""
    tid = file_one(edit())
    store.transition(tid, "approved", "yes")
    assert advisor.execute(tid)["ok"]
    second = advisor.execute(tid)
    assert not second["ok"] and "not waiting" in second["did"]


def test_a_ticket_that_is_not_the_advisors_is_never_touched(world):
    ticket = store.create("A deck for the board", "pptx")
    row = advisor.execute(ticket["id"])
    assert not row["ok"]
    assert store.get(ticket["id"])["state"] == "proposed"


def writer(text):
    return lambda prompt: SimpleNamespace(returncode=0, stdout=text, stderr="")


@pytest.fixture
def drafts(monkeypatch):
    import draft_email
    made = []

    def draft_once(provider, label, to, subject, body, counterparty=""):
        made.append({"label": label, "to": to, "subject": subject, "body": body})
        return {"ok": True, "id": "draft-9", "key": "k", "web_link": "http://x"}
    monkeypatch.setattr(draft_email, "draft_once", draft_once)
    return made


def test_a_yes_to_a_draft_leaves_a_draft_and_sends_nothing(world, drafts, monkeypatch):
    import send_email
    monkeypatch.setattr(send_email, "send_email",
                        lambda *a, **k: pytest.fail("sent mail"))
    monkeypatch.setattr(send_email, "send_draft",
                        lambda *a, **k: pytest.fail("sent a draft"))
    tid = file_one(draft())
    store.transition(tid, "approved", "yes")
    body = f"Hi Dana,\n\nTuesday or Thursday both work {EM} your call.\n\nTest"
    row = advisor.execute(tid, run=writer(body))

    assert row["ok"], row["did"]
    assert drafts[0]["to"] == "dana@kestrel.example"
    assert EM not in drafts[0]["body"]
    ticket = store.get(tid)
    assert ticket["state"] == "delivered"
    assert ticket["delivery"]["draft_id"] == "draft-9"
    assert "Drafts folder" in row["did"]


@pytest.mark.parametrize("text", [
    "I cannot write this without more information about the deal.",
    "PASS: looks fine",
    "Hi Dana,\n\nWe can meet on [DATE] at [LOCATION]. Does that suit?\n\nTest",
    "Ok.",
])
def test_a_reply_that_is_not_a_usable_message_is_never_drafted(world, drafts, text):
    """A refusal is not empty, so "did it return something" proves nothing.

    MUTATION: remove the refusal check in `_write_reply` and the first row
    puts the model's apology in the user's Drafts folder.
    """
    tid = file_one(draft())
    store.transition(tid, "approved", "yes")
    row = advisor.execute(tid, run=writer(text))
    assert not row["ok"]
    assert drafts == []
    assert store.get(tid)["state"] == "failed"


def test_a_provider_error_never_reaches_the_mail(world, monkeypatch):
    """An exception can quote an address or a token, and this sentence is
    mailed. Only a reason this module wrote is shown."""
    import draft_email

    def boom(*a, **k):
        raise ValueError("token ya29.SECRET rejected for dana@kestrel.example")
    monkeypatch.setattr(draft_email, "draft_once", boom)
    tid = file_one(draft())
    store.transition(tid, "approved", "yes")
    row = advisor.execute(tid, run=writer("Hi Dana,\n\nTuesday works for us.\n\nTest"))
    assert "SECRET" not in row["did"] and "ValueError" in row["did"]


def test_a_yes_to_a_file_builds_a_real_deck_and_checks_it(world, monkeypatch):
    """The whole path with only the model stubbed: plan, native file, and the
    file reopened and counted against its plan."""
    import deliverable
    monkeypatch.setattr(deliverable, "van_gogh_root", lambda: world / "van-gogh")
    plan = {"title": "Kestrel site visit", "subtitle": "Where it stands",
            "slides": [{"heading": f"Point {i}", "bullets": ["One clear sentence."]}
                       for i in range(3)]}

    def planner(prompt, model=None, timeout=None):
        return SimpleNamespace(returncode=0, stdout=json.dumps(plan), stderr="")

    tid = file_one(draft(kind="file", title="A deck on the Kestrel site visit",
                         file_type="pptx", done_when="Three slides on status."))
    assert store.get(tid)["type"] == "pptx"
    store.transition(tid, "approved", "yes")
    row = advisor.execute(tid, run=planner)

    assert row["ok"], row["did"]
    ticket = store.get(tid)
    assert ticket["state"] == "delivered"
    built = Path(ticket["delivery"]["path"])
    assert built.is_file() and built.suffix == ".pptx"
    assert (world / "van-gogh") in built.parents

    from pptx import Presentation
    assert len(Presentation(str(built)).slides) >= 3


# ── Sending is a second, separate yes ────────────────────────────────────────

@pytest.fixture
def delivered(world, drafts, monkeypatch):
    import send_email
    sends = []
    monkeypatch.setattr(send_email, "account_for_label",
                        lambda label: {"label": label, "provider": "google",
                                       "email": "primary@gmail.com"})
    monkeypatch.setattr(send_email, "send_draft",
                        lambda account, draft_id: sends.append(draft_id))
    tid = file_one(draft())
    store.transition(tid, "approved", "yes")
    advisor.execute(tid, run=writer("Hi Dana,\n\nTuesday works for us.\n\nTest"))
    return SimpleNamespace(tid=tid, sends=sends)


def test_send_by_reply_is_refused_while_the_switch_is_off(delivered):
    """MUTATION: drop the `by_reply and not advisor_allow_send()` test in
    send_draft and this fails with mail sent on an unattended reply."""
    out = advisor.send_draft(delivered.tid, by_reply=True)
    assert not out["ok"] and "switched off" in out["did"]
    assert delivered.sends == []


def test_send_by_reply_works_once_the_switch_is_on(delivered, monkeypatch):
    import config_loader as cl
    monkeypatch.setattr(cl, "advisor_allow_send", lambda: True)
    assert advisor.send_draft(delivered.tid, by_reply=True)["ok"]
    assert delivered.sends == ["draft-9"]
    again = advisor.send_draft(delivered.tid, by_reply=True)
    assert not again["ok"] and "already sent" in again["did"]
    assert delivered.sends == ["draft-9"], "one draft, one send"


def test_a_send_whose_outcome_is_unknown_is_never_tried_twice(delivered, monkeypatch):
    """A lost response may mean it went. The ticket is stamped before the
    send, so the retry finds it sent and says to check.

    MUTATION: move the `store.attach(... sent_at ...)` line below the send
    and this fails with a second attempt.
    """
    import send_email

    def lost(account, draft_id):
        delivered.sends.append(draft_id)
        raise TimeoutError("no response")
    monkeypatch.setattr(send_email, "send_draft", lost)
    first = advisor.send_draft(delivered.tid)
    assert not first["ok"] and "check your Sent folder" in first["did"]
    second = advisor.send_draft(delivered.tid)
    assert "already sent" in second["did"]
    assert delivered.sends == ["draft-9"]


def test_send_on_a_ticket_with_no_draft_does_nothing(world):
    tid = file_one(edit())
    out = advisor.send_draft(tid)
    assert not out["ok"] and "no draft" in out["did"]


def test_a_reply_of_send_reaches_the_gate(asked, monkeypatch):
    """SEND in an email is routed through the same refusal, end to end."""
    done = advisor.collect_answers(NOW, fetch=sent("SEND 1"))
    assert len(done) == 1 and not done[0]["ok"]
    assert "no draft" in done[0]["did"]


# ── The tick ─────────────────────────────────────────────────────────────────

def test_switched_off_it_does_nothing_at_all(world, monkeypatch):
    import config_loader as cl
    monkeypatch.setattr(cl, "advisor_enabled", lambda: False)
    monkeypatch.setattr(advisor, "propose",
                        lambda *a, **k: pytest.fail("looked while off"))
    monkeypatch.setattr(advisor, "collect_answers",
                        lambda *a, **k: pytest.fail("read mail while off"))
    assert advisor.tick(NOW)["why"] == "the advisor is switched off"


def test_a_tick_reads_the_answer_does_the_work_and_says_so(asked, monkeypatch):
    mailed = []
    monkeypatch.setattr(advisor, "_mail", lambda s, b: mailed.append((s, b)))
    out = advisor.tick(NOW + timedelta(hours=1), fetch=sent("Y 2\nN 1"))

    assert [r["ok"] for r in out["done"]] == [True]
    assert store.get(asked.ids[1])["state"] == "delivered"
    assert store.get(asked.ids[0])["state"] == "dismissed"
    assert len(mailed) == 1
    subject, body = mailed[0]
    assert "Corrected wiki/deals/kestrel.md" in body
    assert "will not be suggested again" in body
    assert advisor.parse_commands(body) == [], "its own report is not an answer"


def test_a_commented_suggestion_goes_back_to_waiting(world):
    tid = file_one(draft())
    store.comment(tid, "Mention the Tuesday call")
    assert store.get(tid)["state"] == "staging"
    advisor._restage()
    ticket = store.get(tid)
    assert ticket["state"] == "staged"
    assert ticket["comments"][0]["text"] == "Mention the Tuesday call"


def test_the_watcher_hands_over_and_survives_a_broken_advisor(monkeypatch):
    import job_watch
    monkeypatch.setattr(advisor, "tick",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    job_watch._advise()        # must not raise


# ── The ledger it shares with the Workbench ──────────────────────────────────

def test_a_briefing_refresh_never_prunes_a_waiting_suggestion(world):
    tid = file_one(draft())
    store.propose(items=[])
    assert store.get(tid)["state"] == "staged"


def test_the_ledger_lock_is_released_and_a_dead_one_is_broken(world, monkeypatch):
    lock = store.tickets_path().with_suffix(".lock")
    file_one(draft())
    assert not lock.exists(), "released after the write"

    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    import os
    old = datetime.now().timestamp() - store.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    file_one(edit())                      # must not wait or raise
    assert len(store.by_source("advisor")) == 2
    assert not lock.exists()


def test_the_agent_file_is_one_the_cli_can_load():
    """Frontmatter with a bare colon in a long value is invalid YAML that a
    lenient loader still lists, so the shape is checked by hand."""
    from pathlib import Path
    text = (Path(advisor.__file__).resolve().parent.parent / "agents"
            / advisor.AGENT_FILE).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text[4:text.index("\n---", 4)]
    assert "name: chief-of-staff" in front
    assert "description: >-" in front
    assert "tools: Read, Grep, Glob" in front, "it may read and nothing else"
    assert EM not in text and EN not in text
