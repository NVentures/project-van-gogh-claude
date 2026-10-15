"""The install's meeting prefetch is bounded whatever the meetings are.

One very long recording among the ten newest meetings once held an install
for 624 seconds (a 600 second timeout, then a retry). The prefetch now reads
under a budget fixed when it starts: each read gets what is left of it, one
attempt, and a meeting that runs out of time is STILL FILED, from the
notetaker's own summary, and queued for a full read by the next batch run.

Everything here is hermetic: the provider is a fake, `claude_cli._spawn` is
a stub that "takes" a scripted number of seconds on a fake clock (and raises
`subprocess.TimeoutExpired` when that exceeds the timeout it was given), and
the state dir is the test's own, so no queue file leaks into another test.
"""
import json
import subprocess

import pytest

import claude_cli
import ingest_fixture as fx
import item_ids
import meeting_ingest as gi
import meeting_synthesis

REAL_SYNTHESIZE = meeting_synthesis.synthesize

GOOD = json.dumps({
    "discussed": "The plan and the numbers, read from the transcript.",
    "decisions": ["Go ahead"], "changes": [],
    "my_commitments": ["Send the follow up"], "their_commitments": [],
    "next_steps": [], "open_questions": [], "subtext": [],
})


def _notes(n=10):
    out = []
    for i in range(1, n + 1):
        out.append({
            "id": f"m{i:02d}",
            "title": f"Planning call {i:02d}",
            "created_at": f"2026-09-{30 - i:02d}T17:00:00Z",
            "updated_at": f"2026-09-{30 - i:02d}T18:00:00Z",
            "summary_markdown": f"## Summary\n\nVendor summary of call {i:02d}.\n\n"
                                "## Action Items\n\n- **Test User**: follow up\n",
            "attendees": [{"name": "Test User", "email": "primary@gmail.com"}],
            "calendar_event": {"scheduled_start_time": f"2026-09-{30 - i:02d}T17:00:00Z"},
            "owner": None, "folder_membership": [], "share_url": None,
            "source": "granola",
        })
    return out


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t


class Model:
    """The stubbed CLI: scripted seconds per meeting, on the fake clock."""

    def __init__(self, clock, seconds=None, default=30, failing=(), capped=(), reply=GOOD):
        self.clock, self.seconds, self.default = clock, dict(seconds or {}), default
        self.failing, self.capped, self.reply = set(failing), set(capped), reply
        self.calls = []  # (meeting id, timeout it was given)

    def spawn(self, prompt, model, timeout):
        title = next(n["title"] for n in _notes(40) if n["title"] in prompt)
        mid = "m" + title[-2:]
        self.calls.append((mid, timeout))
        took = self.seconds.get(mid, self.default)
        if took > timeout:
            self.clock.t += timeout
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        self.clock.t += took
        if mid in self.capped:
            return subprocess.CompletedProcess(
                ["claude"], 1, "You're out of extra usage · resets 3:40pm", "")
        if mid in self.failing:
            return subprocess.CompletedProcess(["claude"], 1, "", "API Error: 500")
        return subprocess.CompletedProcess(["claude"], 0, self.reply, "")

    def attempts(self, mid):
        return [t for m, t in self.calls if m == mid]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    monkeypatch.delenv("VAN_GOGH_PREFETCH_BUDGET_S", raising=False)
    clock = Clock()
    monkeypatch.setattr(gi, "_clock", clock.now)
    monkeypatch.setattr(claude_cli, "_now", clock.now)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: setattr(clock, "t", clock.t + s))
    monkeypatch.setattr(claude_cli.claude_update, "heal_if_stale", lambda text: False)
    claude_cli._reset_limiter()
    gi._STAGED.clear()

    class Rig:
        vault = tmp_path / "vault"
        staged = tmp_path / "staged"

        def __init__(self):
            self.clock = clock
            self.provider = fx.FakeProvider(_notes(10))
            self.model = None

        def use(self, model):
            self.model = model
            monkeypatch.setattr(claude_cli, "_spawn", model.spawn)
            return model

        def prepared(self):
            ctx = fx.prepared(gi, self.vault, monkeypatch, provider=self.provider)

            class _Ctx:
                def __enter__(_s):
                    ctx.__enter__()
                    # The fixture stubs synthesize; these tests want the real
                    # one, over the stubbed CLI.
                    monkeypatch.setattr(meeting_synthesis, "synthesize", REAL_SYNTHESIZE)
                    return self

                def __exit__(_s, *exc):
                    return ctx.__exit__(*exc)
            return _Ctx()

        def install(self, budget_s, limit=10):
            with self.prepared():
                manifest = gi.run_prefetch(limit, self.staged, width=1, budget_s=budget_s)
                args = fx.nightly_args(limit)
                args.staged = str(self.staged)
                filed = gi.run_staged(args, self.staged)
            return manifest, filed

        def batch(self, limit=10):
            with self.prepared():
                gi.run_batch(fx.nightly_args(limit))

        def page(self, mid):
            return gi._page_for_meeting(mid)

        def pages(self):
            return sorted(p.name for p in (self.vault / "wiki" / "sources").glob("*.md")
                          if "catalog" not in p.name)

    yield Rig()
    claude_cli._reset_limiter()


# ── One slow meeting ─────────────────────────────────────────────────────────

def test_a_slow_meeting_is_filed_from_the_summary_with_one_attempt(rig, capsys):
    model = rig.use(Model(rig.clock, seconds={"m05": 5000}))
    manifest, filed = rig.install(budget_s=1000)
    capsys.readouterr()

    # 10 of 10 staged and filed; nothing failed, nothing deferred.
    assert [r["id"] for r in manifest["filed"]] == [f"m{i:02d}" for i in range(1, 11)]
    assert manifest["failed"] == [] and manifest["deferred"] == []
    assert manifest["degraded"] is None and manifest["budget_s"] == 1000
    assert len(filed["filed"]) == 10 and len(rig.pages()) == 10
    # Exactly one model attempt for the slow one, capped at the per-call cap.
    assert model.attempts("m05") == [gi.PREFETCH_CALL_CAP_S]
    assert len(model.calls) == 10
    # It is reported, with the reason, and queued for the full read.
    assert manifest["summary_only"] == [{
        "id": "m05", "title": "Planning call 05",
        "reason": gi.BUDGET_REASON, "over_budget": True}]
    queue = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert [(r["id"], r["title"]) for r in queue] == [("m05", "Planning call 05")]
    assert queue[0]["queued"]
    text = rig.page("m05").read_text(encoding="utf-8")
    assert "synthesis: summary" in text and "Vendor summary of call 05" in text
    assert "synthesis: transcript" in rig.page("m04").read_text(encoding="utf-8")


def test_the_whole_prefetch_is_bounded_by_the_budget(rig, capsys):
    """Three slow meetings in a row cannot cost three timeouts."""
    model = rig.use(Model(rig.clock, seconds={"m03": 5000, "m04": 5000, "m05": 5000}))
    start = rig.clock.t
    manifest, filed = rig.install(budget_s=200)
    capsys.readouterr()

    # m01 and m02 read (30 s each); m03 gets the 140 s that are left and
    # times out; nothing after it is sent to the model at all.
    assert model.calls == [("m01", 180), ("m02", 170), ("m03", 140)]
    assert rig.clock.t - start == 200, "the model time spent is the budget, no more"
    assert len(filed["filed"]) == 10 and len(rig.pages()) == 10
    assert manifest["failed"] == [] and manifest["deferred"] == []
    over = [r["id"] for r in manifest["summary_only"] if r["over_budget"]]
    assert over == [f"m{i:02d}" for i in range(3, 11)]
    assert all(r["reason"] == gi.BUDGET_REASON for r in manifest["summary_only"])
    queue = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert [r["id"] for r in queue] == over


@pytest.mark.parametrize("budget, sent, timeout", [
    (49, False, None),   # 19 s left after the first read: not sent
    (50, True, 20),      # exactly 20 s left: sent, with those 20 s
])
def test_a_meeting_is_not_sent_with_less_than_twenty_seconds_left(rig, capsys, budget, sent, timeout):
    rig.provider = fx.FakeProvider(_notes(2))
    model = rig.use(Model(rig.clock, seconds={"m02": 10}))
    manifest, _ = rig.install(budget_s=budget, limit=2)
    capsys.readouterr()
    assert (("m02", timeout) in model.calls) is sent
    assert [r["id"] for r in manifest["summary_only"]] == ([] if sent else ["m02"])
    assert len(rig.pages()) == 2


def test_a_usage_cap_still_defers_and_is_not_a_budget_row(rig, capsys, monkeypatch):
    rig.provider = fx.FakeProvider(_notes(4))
    rig.use(Model(rig.clock, capped={"m02"}))
    with rig.prepared():
        manifest = gi.run_prefetch(4, rig.staged, width=1, budget_s=200)
    capsys.readouterr()
    assert [r["id"] for r in manifest["filed"]] == ["m01"]
    assert [r["id"] for r in manifest["deferred"]] == ["m02", "m03", "m04"]
    assert manifest["degraded"] == {"reason": "usage_cap", "deferred": 3}
    assert manifest["summary_only"] == []


# ── The full read happens later, in the batch path ───────────────────────────

def test_the_batch_path_reads_the_queued_meeting_and_overwrites_its_page(rig, capsys):
    rig.use(Model(rig.clock, seconds={"m05": 5000}))
    rig.install(budget_s=1000)
    rig.provider.fetched.clear()
    model = rig.use(Model(rig.clock, seconds={"m05": 400}))  # long, but it finishes
    capsys.readouterr()

    rig.batch()
    out = capsys.readouterr().out

    text = rig.page("m05").read_text(encoding="utf-8")
    assert "synthesis: transcript" in text
    assert "## What Was Discussed" in text and "read from the transcript" in text
    assert not gi.deep_read_path().exists(), "the queue is emptied"
    # The normal read: full timeout, the transcript fetched for that meeting.
    assert model.calls == [("m05", 600)]
    assert ("m05", True) in rig.provider.fetched
    assert "[deep read] 1 meeting(s)" in out and "deep read: Planning call 05 (m05)" in out
    assert "[deep read] Done. 1 read from the transcript, 0 still queued." in out
    assert len(rig.pages()) == 10, "overwritten in place, not filed twice"
    # A second run has nothing to drain and says nothing about it.
    rig.batch()
    assert "deep read" not in capsys.readouterr().out


def test_a_failed_deep_read_stays_queued_and_touches_nothing(rig, capsys):
    rig.use(Model(rig.clock, seconds={"m05": 5000}))
    rig.install(budget_s=1000)
    before = rig.page("m05").read_bytes()
    rig.use(Model(rig.clock, failing={"m05"}))
    capsys.readouterr()

    rig.batch()
    out = capsys.readouterr().out
    queue = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert [(r["id"], r["attempts"]) for r in queue] == [("m05", 1)]
    assert rig.page("m05").read_bytes() == before
    assert "still queued" in out and "0 read from the transcript, 1 still queued" in out

    # It does not retry forever: after the last allowed try it is let go,
    # and the notetaker's summary stays on the page.
    for _ in range(gi.DEEP_READ_MAX_ATTEMPTS - 1):
        rig.batch()
    assert not gi.deep_read_path().exists()
    assert "keeping the notetaker's summary" in capsys.readouterr().out
    assert rig.page("m05").read_bytes() == before


def test_a_usage_cap_during_the_deep_read_keeps_the_queue_and_spends_no_try(rig, capsys):
    rig.use(Model(rig.clock, seconds={"m03": 5000, "m05": 5000}))
    rig.install(budget_s=1000)
    model = rig.use(Model(rig.clock, capped={"m03"}))
    capsys.readouterr()
    rig.batch()
    out = capsys.readouterr().out
    queue = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert [r["id"] for r in queue] == ["m03", "m05"]
    assert all("attempts" not in r for r in queue)
    assert "usage limit reached, still queued" in out
    assert not model.attempts("m05"), "nothing is read after the cap"


def test_with_no_queue_file_the_batch_path_does_nothing_extra(rig, capsys):
    """The nightly run on every machine that was never over budget: no
    output, no fetch, no model call before the ordinary batch."""
    rig.provider = fx.FakeProvider(_notes(1), on_fetch=lambda nid: pytest.fail("fetched"))
    rig.use(Model(rig.clock))
    assert not gi.deep_read_path().exists()
    with rig.prepared():
        result = gi.drain_deep_reads(fx.nightly_args(1))
    assert result == {"read": [], "kept": [], "dropped": []}
    assert capsys.readouterr().out == ""
    assert rig.model.calls == []


def test_a_dry_run_never_drains(rig, capsys):
    rig.use(Model(rig.clock, seconds={"m05": 5000}))
    rig.install(budget_s=1000)
    model = rig.use(Model(rig.clock))
    capsys.readouterr()
    with rig.prepared():
        args = fx.nightly_args(10)
        args.dry_run, args.apply = True, False
        gi.run_batch(args)
    assert model.calls == [] and gi.deep_read_path().exists()


# ── The control arm and the budget's own knobs ───────────────────────────────

def test_the_serial_arm_applies_no_budget(rig, capsys, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    claude_cli._reset_limiter()
    assert gi.prefetch_budget() == 0
    rig.provider = fx.FakeProvider(_notes(3))
    model = rig.use(Model(rig.clock, seconds={"m02": 5000}))
    with rig.prepared():
        manifest = gi.run_prefetch(3, rig.staged)
        args = fx.nightly_args(3)
        gi.run_staged(args, rig.staged)
    capsys.readouterr()
    # The old read: the nightly timeout and both attempts, no deadline.
    assert model.attempts("m02") == [600, 600]
    assert model.attempts("m01") == [600]
    assert manifest["budget_s"] == 0
    assert manifest["summary_only"] == [{
        "id": "m02", "title": "Planning call 02",
        "reason": "the model call failed (TimeoutExpired) (tried 2 times)",
        "over_budget": False}]
    assert not gi.deep_read_path().exists(), "only a budget cut is queued"


def test_the_budget_default_and_its_override(monkeypatch):
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    monkeypatch.delenv("VAN_GOGH_PREFETCH_BUDGET_S", raising=False)
    assert gi.prefetch_budget() == gi.PREFETCH_BUDGET_S == 200
    assert gi.PREFETCH_CALL_CAP_S == 180 and gi.PREFETCH_MIN_REMAINING_S == 20
    monkeypatch.setenv("VAN_GOGH_PREFETCH_BUDGET_S", "75")
    assert gi.prefetch_budget() == 75
    monkeypatch.setenv("VAN_GOGH_PREFETCH_BUDGET_S", "0")
    assert gi.prefetch_budget() == 0
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    monkeypatch.setenv("VAN_GOGH_PREFETCH_BUDGET_S", "90")
    assert gi.prefetch_budget() == 90, "an explicit value wins on the control arm too"
    monkeypatch.setenv("VAN_GOGH_PREFETCH_BUDGET_S", "soon")
    monkeypatch.delenv("VAN_GOGH_SERIAL")
    assert gi.prefetch_budget() == 200


def test_the_queue_is_deduplicated_and_removed_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    assert gi.read_deep_read_queue() == []
    assert gi.queue_deep_read("a", "A") is True
    assert gi.queue_deep_read("a", "A") is False
    assert gi.queue_deep_read("b", "B") is True
    assert [r["id"] for r in gi.read_deep_read_queue()] == ["a", "b"]
    assert gi.deep_read_path() == tmp_path / "setup" / "deep-read.json"
    gi.write_deep_read_queue([])
    assert not gi.deep_read_path().exists()


# ── The deadline inside claude_cli ───────────────────────────────────────────

def test_a_deadline_clips_every_spawn_and_stops_the_retries(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(claude_cli, "_now", clock.now)
    monkeypatch.setattr(claude_cli, "_sleep", lambda s: setattr(clock, "t", clock.t + s))
    monkeypatch.setattr(claude_cli.claude_update, "heal_if_stale", lambda text: False)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    claude_cli._reset_limiter()
    seen = []

    def spawn(prompt, model, timeout):
        seen.append(timeout)
        clock.t += 4
        return subprocess.CompletedProcess(["claude"], 1, "", "API Error: 429 rate_limit_error")
    monkeypatch.setattr(claude_cli, "_spawn", spawn)

    # 10 s of deadline: first spawn gets 10 s (not 600), one 2 s pause and a
    # retry with the 4 s that remain, then the 5 s pause would cross it.
    r = claude_cli.run_claude("p", "claude-sonnet-5", timeout=600, deadline=clock.t + 10)
    assert r.returncode == 1
    assert seen == [10, 4]

    # Past the deadline before the call starts: a timeout, and no CLI at all.
    seen.clear()
    with pytest.raises(subprocess.TimeoutExpired):
        claude_cli.run_claude("p", timeout=600, deadline=clock.t - 1)
    assert seen == []

    # No deadline: the call is the call it always was.
    seen.clear()
    monkeypatch.setattr(claude_cli, "_spawn",
                        lambda p, m, t: seen.append(t) or subprocess.CompletedProcess([], 0, "ok", ""))
    claude_cli.run_claude("p", timeout=600)
    assert seen == [600]
    claude_cli._reset_limiter()


def test_synthesize_reports_a_timeout_per_call_not_through_the_global(monkeypatch):
    def run(prompt, model=None, timeout=None, deadline=None):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
    monkeypatch.setattr(claude_cli, "run_claude", run)
    note = {"id": "x", "title": "t", "transcript": [
        {"speaker": {"source": "microphone"}, "text": "word " * 200}]}
    info = {}
    assert REAL_SYNTHESIZE(note, user_name="T", attempts=1, deadline=5.0, info=info) is None
    assert info == {"failure": "the model call failed (TimeoutExpired)", "timed_out": True}
    # The nightly call names no deadline, so a stub with the old signature
    # still works, and the sentence it prints is the old one.
    monkeypatch.setattr(claude_cli, "run_claude",
                        lambda prompt, model=None, timeout=None: (_ for _ in ()).throw(
                            subprocess.TimeoutExpired(cmd="claude", timeout=timeout)))
    assert REAL_SYNTHESIZE(note, user_name="T") is None
    assert meeting_synthesis.LAST_FAILURE == \
        "the model call failed (TimeoutExpired) (tried 2 times)"


# ── The full read REPLACES what the first filing wrote ───────────────────────
#
# The first filing promotes the notetaker summary's action items to the hot
# cache and lists the meeting in the catalog. The full read must not leave a
# second, reworded set of action items beside the first, nor a second catalog
# line. Untouched first-filing lines are removed and rewritten; anything the
# user has ticked or edited since is kept as they left it and nothing is
# added beside it.

REWORDED = json.dumps({
    "discussed": "Pricing and the deck, read from the transcript.",
    "decisions": [], "changes": [],
    "my_commitments": ["Email the revised deck to the group by Friday",
                       "Chase the pricing answer from finance"],
    "their_commitments": [], "next_steps": [], "open_questions": [], "subtext": [],
})


def _two_action_notes():
    # The FIRST meeting filed: its owner name is not yet an entity page, so
    # the summary path's bold-owner filter promotes both items.
    notes = _notes(10)
    notes[0]["summary_markdown"] = (
        "## Summary\n\nVendor summary of call 01.\n\n## Action Items\n\n"
        "- **Test User**: send the deck\n- **Test User**: follow up on pricing\n")
    return notes


SLOW = "m01"


def _installed_with_two_actions(rig):
    rig.provider = fx.FakeProvider(_two_action_notes())
    rig.use(Model(rig.clock, seconds={SLOW: 5000}))
    rig.install(budget_s=1000)
    stem = rig.page(SLOW).stem
    hot = rig.vault / "wiki" / "hotcache.md"
    return stem, hot


def _action_lines(hot, stem):
    # The raw lines, id marker and all: the queue records them exactly.
    return [ln for ln in hot.read_text(encoding="utf-8").splitlines()
            if item_ids.strip(ln).rstrip().endswith(f"(from [[{stem}]])")]


def _catalog_lines(rig, stem):
    out = []
    for cat in (rig.vault / "wiki" / "sources").glob("catalog-*.md"):
        out += [ln for ln in cat.read_text(encoding="utf-8").splitlines()
                if ln.startswith(f"- [[{stem}]]")]
    return out


def test_the_deep_read_replaces_the_action_items_and_the_catalog_line(rig, capsys):
    stem, hot = _installed_with_two_actions(rig)
    first = _action_lines(hot, stem)
    assert len(first) == 2 and "send the deck" in first[0] and "pricing" in first[1]
    assert len(_catalog_lines(rig, stem)) == 1
    queue = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert queue[0]["source_title"] == stem and queue[0]["promoted"] == first
    assert queue[0]["catalog"]["line"] == _catalog_lines(rig, stem)[0]
    others_before = [ln for ln in hot.read_text(encoding="utf-8").splitlines()
                     if "(from [[" in ln and ln not in first]

    rig.use(Model(rig.clock, reply=REWORDED))
    capsys.readouterr()
    rig.batch()
    capsys.readouterr()

    now = _action_lines(hot, stem)
    assert all(item_ids.CB_RE.search(ln) for ln in now), "each carries its id"
    assert [item_ids.strip(ln) for ln in now] == [f"- [ ] Email the revised deck to the group by Friday (from [[{stem}]])",
                   f"- [ ] Chase the pricing answer from finance (from [[{stem}]])"]
    text = hot.read_text(encoding="utf-8")
    assert "send the deck" not in text and "follow up on pricing" not in text, \
        "the first, summary-based set is gone, not left beside the new one"
    assert len(_catalog_lines(rig, stem)) == 1, "exactly one catalog line"
    assert "synthesis: transcript" in rig.page(SLOW).read_text(encoding="utf-8")
    # Every other meeting's promoted items are exactly as they were.
    assert [ln for ln in text.splitlines()
            if "(from [[" in ln and ln not in now] == others_before
    assert "- [ ] existing item" in text


def test_action_items_the_user_has_ticked_are_kept_and_nothing_is_added(rig, capsys):
    stem, hot = _installed_with_two_actions(rig)
    first = _action_lines(hot, stem)
    ticked = first[0].replace("- [ ]", "- [x]", 1)
    hot.write_text(hot.read_text(encoding="utf-8").replace(first[0], ticked), encoding="utf-8")

    rig.use(Model(rig.clock, reply=REWORDED))
    capsys.readouterr()
    rig.batch()
    out = capsys.readouterr().out

    assert _action_lines(hot, stem) == [ticked, first[1]], \
        "the user's list stands as they left it"
    assert "Email the revised deck" not in hot.read_text(encoding="utf-8")
    assert "Kept your existing action items" in out
    assert "synthesis: transcript" in rig.page(SLOW).read_text(encoding="utf-8")
    assert len(_catalog_lines(rig, stem)) == 1


def test_a_catalog_line_the_user_has_edited_is_kept_and_no_second_one_is_added(rig, capsys):
    stem, hot = _installed_with_two_actions(rig)
    line = _catalog_lines(rig, stem)[0]
    edited = line + " (the pricing one)"
    cat = next((rig.vault / "wiki" / "sources").glob("catalog-*.md"))
    cat.write_text(cat.read_text(encoding="utf-8").replace(line, edited), encoding="utf-8")

    rig.use(Model(rig.clock, reply=REWORDED))
    capsys.readouterr()
    rig.batch()
    out = capsys.readouterr().out
    assert _catalog_lines(rig, stem) == [edited]
    assert "Kept your existing catalog entry" in out
    # The action items were untouched by the user, so they are still replaced.
    assert len(_action_lines(hot, stem)) == 2
    assert "Email the revised deck" in hot.read_text(encoding="utf-8")


def test_a_deep_read_refiles_onto_its_own_page_when_a_neighbour_shares_the_title(rig, capsys):
    """Two meetings with one title and date: the second is filed under a
    suffixed name. A forced re-file by title alone would overwrite the
    FIRST meeting's page with the second meeting's read."""
    a, b = _notes(6)[4], dict(_notes(6)[4])
    b["id"] = "m06"
    rig.provider = fx.FakeProvider([a, b])
    rig.use(Model(rig.clock, seconds={"m05": 5000}))  # both prompts carry the same title
    rig.install(budget_s=1000, limit=2)
    page_a, page_b = rig.page("m05"), rig.page("m06")
    assert page_a != page_b and "(m06)" in page_b.name

    rig.use(Model(rig.clock))
    capsys.readouterr()
    rig.batch(limit=2)
    capsys.readouterr()
    assert rig.page("m05") == page_a and rig.page("m06") == page_b
    for page, mid in ((page_a, "m05"), (page_b, "m06")):
        text = page.read_text(encoding="utf-8")
        assert f"meeting_id: {mid}" in text and "synthesis: transcript" in text
    assert len(rig.pages()) == 2
    assert not gi.deep_read_path().exists()


def test_a_failed_read_that_is_not_a_timeout_keeps_its_retry_and_is_queued(monkeypatch):
    """Under the budget a timeout is never retried, but a failed call or an
    unreadable reply still gets its second try, and a meeting whose transcript
    no read survived is queued for the next batch run (found live: one
    attempt for every failure dropped a meeting to the notetaker's summary
    with no later read)."""
    import meeting_synthesis as ms

    calls = []

    def fake_attempt(prompt, model, timeout, truncated, raise_cap=False, deadline=None, step=None):
        calls.append(timeout)
        if step is not None:
            step.update({"failure": "the model's reply was not readable JSON", "timed_out": False})
        return None

    monkeypatch.setattr(ms, "_one_attempt", fake_attempt)
    note = {"id": "n1", "title": "T", "attendees": [],
            "transcript": [{"speaker": {"source": "microphone"}, "text": "word " * 20}
                           for _ in range(10)]}
    info = {}
    out = ms.synthesize(note, raise_cap=True, deadline=10_000.0, info=info, timeout=180)
    assert out is None
    assert len(calls) == ms.SYNTHESIS_ATTEMPTS == 2
    assert info["timed_out"] is False and "tried 2 times" in info["failure"]

    calls.clear()

    def timing_out(prompt, model, timeout, truncated, raise_cap=False, deadline=None, step=None):
        calls.append(timeout)
        if step is not None:
            step.update({"failure": "the model call failed (TimeoutExpired)", "timed_out": True})
        return None

    monkeypatch.setattr(ms, "_one_attempt", timing_out)
    info = {}
    assert ms.synthesize(note, raise_cap=True, deadline=10_000.0, info=info, timeout=180) is None
    assert len(calls) == 1 and info["timed_out"] is True
    assert "tried" not in info["failure"]

    # No deadline (the nightly path): a timeout keeps both attempts, as before.
    calls.clear()
    assert ms.synthesize(note, raise_cap=True, info={}) is None
    assert len(calls) == 2

    assert gi._has_transcript(note) is True
    assert gi._has_transcript({"transcript": [{"text": "too short"}]}) is False
    assert gi._has_transcript({"transcript": "not the canonical shape"}) is False
    assert gi._has_transcript({}) is False


def test_one_run_reads_at_most_its_share_of_the_queue(rig, capsys):
    """Setup files 60 days, so the queue can be long. An hourly run reads a
    bounded share and leaves the rest queued for the next hour."""
    rig.use(Model(rig.clock, seconds={f"m{i:02d}": 5000 for i in range(1, 11)}))
    rig.install(budget_s=1)
    queued = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert len(queued) > gi.DEEP_READ_PER_RUN
    rig.use(Model(rig.clock))
    capsys.readouterr()
    rig.batch()
    left = json.loads(gi.deep_read_path().read_text(encoding="utf-8"))
    assert len(left) == len(queued) - gi.DEEP_READ_PER_RUN
    assert [r["id"] for r in left] == [r["id"] for r in queued][gi.DEEP_READ_PER_RUN:]
    assert all("attempts" not in r for r in left), "an unread row spends no try"


def test_setup_reads_every_meeting_from_the_last_sixty_days(monkeypatch):
    import setup_flow as sf
    seen = {}

    class _P:
        def iter_stubs(self, since=None, limit=None):
            seen.update(since=since, limit=limit)
            return iter([])

    monkeypatch.setattr(gi, "_provider", lambda: _P())
    monkeypatch.setattr(gi, "get_ingested_meeting_ids", lambda: set())
    assert sf.INGEST_START == ["ingest-start", "--days", "60", "--limit", "200"]
    gi.fetch_uningested_stubs(200, since_days=60)
    from datetime import datetime, timedelta, timezone
    got = datetime.fromisoformat(seen["since"])
    want = datetime.now(timezone.utc) - timedelta(days=60)
    assert abs((got - want).total_seconds()) < 120 and seen["limit"] == 200
    gi.fetch_uningested_stubs(30)
    assert seen["since"] is None, "the hourly scan keeps reading the newest 30"


def test_the_history_is_filed_newest_first_a_few_at_a_time(rig, monkeypatch, tmp_path):
    """After setup, quiet hours file the older meetings: the newest unfiled
    first, `per_run` at a time, until the state says done."""
    monkeypatch.setattr(gi, "history_path", lambda: tmp_path / "meeting-history.json")
    rig.use(Model(rig.clock))
    with rig.prepared():
        first = gi.run_history(fx.nightly_args(10), per_run=4)
        filed = gi.get_ingested_meeting_ids()
    assert filed == {"m01", "m02", "m03", "m04"}, "the newest four, first"
    assert first["left"] == 6 and first["done"] is False
    with rig.prepared():
        gi.run_history(fx.nightly_args(10), per_run=4)
        last = gi.run_history(fx.nightly_args(10), per_run=4)
        assert len(gi.get_ingested_meeting_ids()) == 10
    assert last["done"] is True and last["left"] == 0 and last["filed_total"] == 10
    assert gi.read_history()["done"] is True


def test_a_meeting_that_never_files_does_not_hold_the_history_back(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(gi, "history_path", lambda: tmp_path / "meeting-history.json")
    rig.use(Model(rig.clock))
    real = gi.run_ingest

    def flaky(args):
        if args.meeting == "m01":
            raise SystemExit(7)
        return real(args)
    monkeypatch.setattr(gi, "run_ingest", flaky)
    with rig.prepared():
        for _ in range(gi.HISTORY_MAX_ATTEMPTS):
            gi.run_history(fx.nightly_args(10), per_run=1)
        assert gi.read_history()["attempts"]["m01"] == gi.HISTORY_MAX_ATTEMPTS
        gi.run_history(fx.nightly_args(10), per_run=1)
        assert "m02" in gi.get_ingested_meeting_ids(), "the next one gets its turn"
