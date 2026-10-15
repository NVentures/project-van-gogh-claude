"""week_review: faithful reuse of a saved scan (P11) and the Outlook reply
check through the pool (P10).

The reuse test replays one fixture two ways. A "fresh 14 day scan" is the
Tier 2 replay (`--input`) with `--save-scan`; the reused briefing renders
from that snapshot with every provider client stubbed to fail on contact. The
thread ids per section must be identical and every section non-empty, or
the test is proving equality over nothing.
"""
import json
import sys
from pathlib import Path

import pytest

import claude_cli
import fanout
import week_review as wr

CAP_TEXT = (Path(__file__).parent / "fixtures" / "outage"
            / "2026-09-09-claude-usage-cap.txt").read_text(encoding="utf-8").strip()


def _entry(subject, email, name, source="Gmail", **extra):
    return {"subject": subject, "counterparty_email": email, "counterparty_name": name,
            "source": source, "is_internal": False, "body_preview": "", **extra}


FIXTURE = {
    "calendar": [{"source": "Google", "title": "Harbor site walk", "day": "Thu Oct 1",
                  "time": "10:00 AM", "location": ""}],
    "waiting_on_user": [
        _entry("Re: Harbor redline", "alice@counsel.com", "Alice Ng", reply_age_days=3, age_days=5),
        _entry("Cedar term sheet", "bob@cedar.com", "Bob Reed", reply_age_days=1, age_days=2),
    ],
    "inbox_pending": [
        _entry("Diligence list", "carla@grid.com", "Carla Diaz", reply_age_days=2, age_days=2,
               urgency="high"),
    ],
    "filtered_pending": [
        _entry("Webinar replay", "news@vendor.com", "Vendor", reply_age_days=1, age_days=1,
               suppress=True),
    ],
    "cold_urgent": [
        _entry("Interconnect study", "dana@grid.com", "Dana Vu", source="Outlook", age_days=16),
    ],
    "cold_monitor": [
        _entry("Intro call follow up", "erin@acme.com", "Erin Shaw", age_days=9),
    ],
}


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_cli, "_spawn",
                        lambda *a, **k: pytest.fail("a model was called"))
    monkeypatch.setattr(fanout, "_sleep", lambda s: None)
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)
    hot = tmp_path / "hotcache.md"
    hot.write_text("# Hot Cache\n\n## Active Threads\n\n### Harbor: redline\n"
                   "<!-- deal: stage=active last_contact=2026-09-20 -->\n"
                   "- [[Alice Ng]] alice@counsel.com\n", encoding="utf-8")
    monkeypatch.setattr(wr, "HOTCACHE_PATH", str(hot))


def _main(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["week_review.py", *argv])
    wr.main()
    return json.loads(capsys.readouterr().out)


# ── P11: faithful reuse ──────────────────────────────────────────────────────
#
# Production reuses the install's 60 day scan for a 14 day briefing, so the
# fixture is a mailbox whose threads span 2 to 40 days, read through a fake
# Graph client that applies the date filters the real one receives. A fresh
# 14 day scan and a 60 day scan are both taken live through it; the 60 day
# snapshot is then reused for a 14 day briefing with every client stubbed to
# fail on contact, and must list exactly the fresh 14 day thread ids.

def _ago(days):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _addr(address, name="Someone"):
    return {"emailAddress": {"address": address, "name": name}}


class _DatedGraph:
    """Sent mail at 2, 5, 10, 12, 20 and 40 days; replies on some; two inbound
    threads. Filters are applied the way Graph applies them."""

    SENT = [  # (conversation, subject, days ago, counterparty)
        ("c-a", "Harbor redline", 2, "alice@counsel.com"),
        ("c-b", "Quick question", 5, "bob@cedar.com"),
        ("c-c", "Cedar term sheet", 10, "bob@cedar.com"),
        ("c-d", "Grid study scope", 12, "dana@grid.com"),
        ("c-e", "Interconnect study", 20, "erin@acme.com"),
        ("c-f", "Old intro", 40, "frank@old.com"),
    ]
    REPLIES = {"c-a": 1, "c-d": 11, "c-f": 35}  # days ago the counterparty answered
    INBOX = [
        ("c-x", "Diligence list", 1, "carla@grid.com", "Carla Diaz"),
        ("c-y", "Lunch and learn invite", 2, "pat@vendor.com", "Pat Vendor"),
    ]

    def __init__(self):
        self.calls = []

    def calendar_view(self, start, end, top=200):
        return {"value": []}

    @staticmethod
    def _cutoff(flt):
        return flt.split(" ge ")[1].strip().strip("'")

    def folder_messages(self, folder_id, **odata):
        self.calls.append(("sent", odata.get("filter")))
        cutoff = self._cutoff(odata["filter"])
        rows = []
        for cid, subject, days, who in self.SENT:
            when = _ago(days)
            if when >= cutoff:
                rows.append({"id": f"s-{cid}", "conversationId": cid, "subject": subject,
                             "sentDateTime": when, "toRecipients": [_addr(who)],
                             "bodyPreview": "Following up on the attached."})
        return {"value": rows}

    def messages(self, **odata):
        flt = odata.get("filter", "")
        self.calls.append(("messages", flt))
        if flt.startswith("conversationId eq"):
            cid = flt.split("'")[1]
            days = self.REPLIES.get(cid)
            if days is None:
                return {"value": []}
            who = next(w for c, _s, _d, w in self.SENT if c == cid)
            return {"value": [{"id": f"r-{cid}", "receivedDateTime": _ago(days),
                               "from": _addr(who), "bodyPreview": "Thanks, see attached"}]}
        cutoff = self._cutoff(flt)
        rows = []
        for cid, subject, days, who, name in self.INBOX:
            when = _ago(days)
            if when >= cutoff:
                rows.append({"id": f"i-{cid}", "conversationId": cid, "subject": subject,
                             "receivedDateTime": when, "from": _addr(who, name),
                             "bodyPreview": "Can you send it over?"})
        return {"value": rows}


def _classify_stub(threads):
    """Deterministic stand-in for the model: the vendor mail is suppressed."""
    kept = [t for t in threads if "Lunch" not in t.get("subject", "")]
    dropped = [t for t in threads if "Lunch" in t.get("subject", "")]
    return kept, dropped


def _prior_week_file(monkeypatch, tmp_path):
    """A prior week.md with one unchecked cold line, which the carry-forward
    brings into cold_urgent: the only way a 14 day scan gets one."""
    from datetime import datetime, timedelta
    weekly = tmp_path / "weekly"
    weekly.mkdir()
    today = datetime.now(wr.USER_TZ)
    monday = today + timedelta(days=1) if today.weekday() == 6 \
        else today - timedelta(days=today.weekday())
    prior = (monday - timedelta(days=7)).strftime("%Y-%m-%d")
    dash = chr(0x2014)
    (weekly / f"week-{prior}.md").write_text(
        f"# Week\n\n## Deals {dash} Cold Urgent\n\n"
        f'- [ ] [Outlook] "Harbor easement" {dash} Gwen Park (gwen@counsel.com) '
        f"{dash} 21d\n", encoding="utf-8")
    monkeypatch.setattr(wr, "OBSIDIAN_WEEKLY", str(weekly))


def _tier1_outlook_only(monkeypatch, graph):
    outlook = next(a for a in wr.MICROSOFT_ACCOUNTS)
    monkeypatch.setattr(wr, "GOOGLE_ACCOUNTS", [])
    monkeypatch.setattr(wr, "MICROSOFT_ACCOUNTS", [outlook])
    monkeypatch.setattr(wr, "is_tier1", lambda: True)
    monkeypatch.setattr(wr, "microsoft_client", lambda label: graph)
    monkeypatch.setattr(wr, "google_client",
                        lambda label: pytest.fail("a Google client was built"))
    monkeypatch.setattr(wr, "classify_threads", _classify_stub)


def test_a_sixty_day_scan_reused_for_a_14_day_briefing_lists_the_fresh_14_day_ids(
        tmp_path, monkeypatch, capsys):
    _prior_week_file(monkeypatch, tmp_path)
    snap = tmp_path / "scan-60.json"

    # 1. A fresh 14 day scan, live through the dated fake.
    _tier1_outlook_only(monkeypatch, _DatedGraph())
    fresh = _main(monkeypatch, capsys, "--since", "14", "--no-extensions", "--no-sidecar")
    fresh_ids = wr.thread_ids(fresh)
    for section, ids in fresh_ids.items():
        assert ids, f"{section} must be non-empty for the comparison to mean anything"
    assert fresh["since_days"] == 14

    # 2. The install's 60 day scan, same mailbox, snapshot saved.
    _tier1_outlook_only(monkeypatch, _DatedGraph())
    sixty = _main(monkeypatch, capsys, "--since", "60", "--no-extensions", "--no-sidecar",
                  "--save-scan", str(snap))
    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["since_days"] == 60
    # The wide scan really holds more: the 20 and 40 day threads.
    assert set(saved["thread_ids"]["waiting_on_user"]) > set(fresh_ids["waiting_on_user"])
    assert any("old intro" in t for t in saved["thread_ids"]["waiting_on_user"])
    assert any("interconnect study" in t for t in saved["thread_ids"]["cold_urgent"])
    assert wr.thread_ids(sixty) != fresh_ids

    # 3. The 60 day snapshot reused for a 14 day briefing: no client, no model.
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("a provider client was built on the reuse path")
    monkeypatch.setattr(wr, "google_client", _boom)
    monkeypatch.setattr(wr, "microsoft_client", _boom)
    monkeypatch.setattr(wr, "is_tier1", lambda: False)
    monkeypatch.setattr(wr, "classify_threads",
                        lambda *a, **k: pytest.fail("classified on the reuse path"))
    reused = _main(monkeypatch, capsys, "--reuse-scan", str(snap), "--since", "14",
                   "--no-extensions", "--no-sidecar")
    assert calls == []
    assert reused["reused_scan"]["window_days"] == 14
    assert reused["reused_scan"]["scan_since_days"] == 60, "the reuse path was taken"
    assert reused["since_days"] == 14
    assert wr.thread_ids(reused) == fresh_ids
    assert reused["buckets_md"] == fresh["buckets_md"]
    assert reused["front_page_md"] == fresh["front_page_md"]
    assert not any("Reused scan covers" in e for e in reused["errors"])


def test_window_sections_cuts_by_the_stamp_and_falls_back_to_the_day_count():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    sections = {
        "waiting_on_user": [
            {"subject": "in", "sent_at": "2026-09-20T09:00:00Z"},
            {"subject": "out", "sent_at": "2026-09-16T23:00:00Z"},
            {"subject": "on the day", "sent_at": "2026-09-17T00:30:00Z"},
            {"subject": "unstamped in", "age_days": 14},
            {"subject": "unstamped out", "age_days": 15},
        ],
        "inbox_pending": [
            {"subject": "recent", "received_at": "2026-09-30T09:00:00Z"},
            {"subject": "stale", "received_at": "2026-09-01T09:00:00Z"},
        ],
        "calendar": [{"title": "kept whole"}],
    }
    out = wr.window_sections(sections, 14, 7, now)
    assert [e["subject"] for e in out["waiting_on_user"]] == ["in", "on the day", "unstamped in"]
    assert [e["subject"] for e in out["inbox_pending"]] == ["recent"]
    assert out["calendar"] == sections["calendar"]


def test_a_scan_narrower_than_the_briefing_says_so(tmp_path, monkeypatch, capsys):
    snap = tmp_path / "scan-7.json"
    snap.write_text(json.dumps({"kind": "week_review_scan", "since_days": 7,
                                "sections": {k: [] for k in wr.SCAN_SECTIONS},
                                "thread_ids": {}}), encoding="utf-8")
    monkeypatch.setattr(wr, "is_tier1", lambda: False)
    out = _main(monkeypatch, capsys, "--reuse-scan", str(snap), "--since", "14",
                "--no-extensions", "--no-sidecar")
    assert any("Reused scan covers 7 days" in e for e in out["errors"])


def test_a_file_that_is_not_a_scan_is_refused(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"waiting_on_user": []}), encoding="utf-8")
    monkeypatch.setattr(wr, "is_tier1", lambda: False)
    monkeypatch.setattr(sys, "argv", ["week_review.py", "--reuse-scan", str(bad)])
    with pytest.raises(ValueError):
        wr.main()


# ── P10: the Outlook reply check through the pool ────────────────────────────

class _Throttle(Exception):
    def __init__(self):
        super().__init__("429 Too Many Requests")
        self.response = type("R", (), {"status_code": 429, "headers": {"Retry-After": "1"}})()


def _sent(cid, subject, days_ago, to="x@other.com"):
    from datetime import datetime, timedelta, timezone
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"conversationId": cid, "subject": subject, "sentDateTime": when,
            "toRecipients": [{"emailAddress": {"address": to, "name": "Other Person"}}]}


class _FakeGraph:
    """Three conversations: one quiet and old, one whose lookup always fails,
    one throttled once and then answered."""

    def __init__(self):
        self.lookups = {}

    def folder_messages(self, folder_id, **odata):
        return {"value": [
            _sent("c-cold", "Interconnect study", 20),
            _sent("c-broken", "Cedar term sheet", 3),
            _sent("c-throttled", "Harbor redline", 4),
        ]}

    def messages(self, **odata):
        flt = odata.get("filter", "")
        cid = flt.split("'")[1]
        self.lookups[cid] = self.lookups.get(cid, 0) + 1
        if cid == "c-broken":
            raise RuntimeError("Graph 500: internal error")
        if cid == "c-throttled":
            if self.lookups[cid] == 1:
                raise _Throttle()
            # Relative to the clock like the sent message: a literal date here
            # fell behind the "4 days ago" sent time within a week and read
            # as a reply that preceded the message it answered (test-clock rot).
            return {"value": [{"id": "r1", "receivedDateTime": _ago(2),
                               "from": {"emailAddress": {"address": "x@other.com"}},
                               "bodyPreview": "Thanks, see attached"}]}
        return {"value": []}


def _run_outlook(monkeypatch):
    graph = _FakeGraph()
    monkeypatch.setattr(wr, "microsoft_client", lambda label: graph)
    waiting, cold_u, cold_m, conv, errors = wr.fetch_outlook_deals(
        "Outlook", "FOLDERID", "user@company.com", 30)
    return graph, waiting, cold_u, cold_m, conv, errors


def test_a_failed_lookup_is_an_error_never_no_reply(monkeypatch):
    graph, waiting, cold_u, cold_m, conv, errors = _run_outlook(monkeypatch)
    subjects = lambda rows: {r["subject"] for r in rows}
    # The broken lookup is reported, and the thread is in NO list.
    assert len(errors) == 1 and "lookup_failed" in errors[0]
    assert "Cedar term sheet" in errors[0] and "RuntimeError" in errors[0]
    assert "Cedar term sheet" not in subjects(waiting) | subjects(cold_u) | subjects(cold_m)
    # The throttled lookup was retried and landed where its reply put it.
    assert graph.lookups["c-throttled"] == 2, "the retry branch was hit"
    assert subjects(waiting) == {"Harbor redline"}
    # The genuinely quiet thread is cold, as before.
    assert subjects(cold_u) == {"Interconnect study"} and cold_m == []
    assert set(conv) == {"c-cold", "c-broken", "c-throttled"}


def test_the_control_arm_runs_the_reply_check_at_the_base_width(monkeypatch):
    """At the base commit the reply check was ThreadPoolExecutor(8) with no
    retry; the fast arm is 4 wide with a retry. The control must be the
    base, or "half of serial" is measured against a strawman."""
    seen = []
    real_run = fanout.run

    def spy(items, fn, width, **kw):
        seen.append((width, kw.get("serial_width"), fanout.width_for(width, kw.get("serial_width", 1))))
        return real_run(items, fn, width, **kw)
    monkeypatch.setattr(wr.fanout, "run", spy)

    fast = _run_outlook(monkeypatch)
    assert seen[-1] == (8, 8, 8)
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    control = _run_outlook(monkeypatch)
    assert seen[-1] == (8, 8, 8)
    # Same partition for everything that needed no retry; the throttled
    # lookup is retried on the fast arm and reported on the control arm
    # (never filed as "no reply" on either).
    assert [r["subject"] for r in fast[2]] == [r["subject"] for r in control[2]] == ["Interconnect study"]
    assert control[0].lookups["c-throttled"] == 1
    assert [r["subject"] for r in control[1]] == []
    assert len(control[5]) == 2 and all("lookup_failed" in e for e in control[5])


# ── The filter Graph rejects can never come back ─────────────────────────────

GRAPH_400 = (Path(__file__).parent / "fixtures" / "outage"
             / "2026-10-01-graph-400-reply-filter.txt").read_text(encoding="utf-8")


class _StrictGraph(_FakeGraph):
    """Answers exactly as the live tenant did on 2026-10-01: a 400 for any
    filter carrying receivedDateTime, a 200 with the conversation's messages
    for the bare conversationId form."""

    def messages(self, **odata):
        flt = odata.get("filter", "")
        if "receivedDateTime" in flt:
            import requests
            resp = requests.Response()
            resp.status_code = 400
            resp._content = GRAPH_400.encode("utf-8")
            raise requests.HTTPError("400 Client Error: Bad Request", response=resp)
        assert odata.get("top") == wr.REPLY_LOOKUP_TOP
        assert "receivedDateTime" in odata.get("select", "")
        cid = flt.split("'")[1]
        self.lookups[cid] = self.lookups.get(cid, 0) + 1
        if cid == "c-throttled":
            # One reply BEFORE the sent message and one after: only the
            # later one counts, which the server-side clause used to do.
            return {"value": [
                {"id": "r0", "receivedDateTime": "2020-01-01T00:00:00Z",
                 "from": {"emailAddress": {"address": "x@other.com"}}, "bodyPreview": "old"},
                {"id": "r1", "receivedDateTime": "2099-01-01T00:00:00Z",
                 "from": {"emailAddress": {"address": "x@other.com"}}, "bodyPreview": "new"}]}
        return {"value": []}


def test_the_reply_filter_names_the_conversation_only():
    assert wr.reply_filter("abc") == "conversationId eq 'abc'"
    assert "receivedDateTime" not in wr.reply_filter("abc")
    assert "BadRequest" in GRAPH_400 and "Edm.DateTimeOffset" in GRAPH_400, \
        "the fixture is the real 400 body"


def test_no_filter_string_in_the_source_carries_received_date_time():
    """Source-level guard: every `filter=` keyword argument in week_review's
    reply lookup path is built by reply_filter, and no f-string in the file
    puts receivedDateTime inside a conversationId filter."""
    import ast
    import inspect
    src = inspect.getsource(wr.fetch_outlook_deals)
    tree = ast.parse(src)
    # Only the reply lookups (mc.messages); the sent-folder read legitimately
    # filters on sentDateTime.
    filters = [kw.value for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "messages"
               for kw in node.keywords if kw.arg == "filter"]
    assert filters, "the reply lookup still passes a filter"
    for value in filters:
        assert isinstance(value, ast.Call) and value.func.id == "reply_filter", \
            ast.dump(value)
    # String LITERALS only (an f-string's constant parts included): the
    # comment that explains why the clause is banned must not trip this.
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert literals
    for text in literals:
        assert "receivedDateTime gt" not in text and "receivedDateTime ge" not in text, text


def test_against_the_strict_tenant_every_lookup_succeeds_and_the_date_is_compared_client_side(monkeypatch):
    graph = _StrictGraph()
    monkeypatch.setattr(wr, "microsoft_client", lambda label: graph)
    waiting, cold_u, cold_m, conv, errors = wr.fetch_outlook_deals(
        "Outlook", "FOLDERID", "user@company.com", 30)
    assert errors == [], errors
    assert [w["subject"] for w in waiting] == ["Harbor redline"]
    assert waiting[0]["body_preview"] == "new", "the pre-sent reply was discarded"
    # Cedar (3 days old, no reply) is recent and dropped, as always.
    assert {r["subject"] for r in cold_u} == {"Interconnect study"} and cold_m == []


def test_the_old_filter_would_have_been_an_error_line_not_no_reply(monkeypatch):
    """Mutation control: with the date back in the filter, the strict tenant
    400s and the thread is REPORTED, never filed cold."""
    graph = _StrictGraph()
    monkeypatch.setattr(wr, "microsoft_client", lambda label: graph)
    monkeypatch.setattr(wr, "reply_filter",
                        lambda cid: f"conversationId eq '{cid}' and receivedDateTime gt '2026-01-01T00:00:00Z'")
    waiting, cold_u, cold_m, conv, errors = wr.fetch_outlook_deals(
        "Outlook", "FOLDERID", "user@company.com", 30)
    assert len(errors) == 3 and all("lookup_failed" in e and "HTTPError" in e for e in errors)
    assert waiting == [] and cold_u == [] and cold_m == []


def test_a_usage_cap_in_a_lookup_is_reported_not_filed_as_cold(monkeypatch):
    graph = _FakeGraph()

    def messages(**odata):
        raise claude_cli.UsageCap(CAP_TEXT)
    graph.messages = messages
    monkeypatch.setattr(wr, "microsoft_client", lambda label: graph)
    waiting, cold_u, cold_m, conv, errors = wr.fetch_outlook_deals(
        "Outlook", "FOLDERID", "user@company.com", 30)
    assert waiting == [] and cold_u == [] and cold_m == []
    assert len(errors) == 3 and all("usage_cap" in e for e in errors)


def test_outlook_errors_reach_the_briefing_errors_list():
    """main() unpacks the fifth value; a four-tuple unpack would raise."""
    import inspect
    src = inspect.getsource(wr.main)
    assert "d_wait, d_cold_u, d_cold_m, sent_conv_dt, d_errors = res" in src
    assert 'output["errors"].extend(d_errors)' in src


# ── The Gmail thread fetch keeps its contract through the pool ───────────────

def test_pooled_gmail_threads_keep_one_slot_per_stub_in_order(monkeypatch):
    calls = {}

    def get_thread(stub):
        calls[stub["id"]] = calls.get(stub["id"], 0) + 1
        if stub["id"] == "t2":
            return None
        if stub["id"] == "t3" and calls["t3"] == 1:
            raise _Throttle()
        return ({"id": stub["id"]}, "snip")

    stubs = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}, {"id": "t4"}]
    out = wr._pooled_threads(stubs, get_thread)
    assert [o[0]["id"] if o else None for o in out] == ["t1", None, "t3", "t4"]
    assert calls["t3"] == 2


def test_the_snapshot_is_written_after_the_sidecar(tmp_path, monkeypatch, capsys):
    """The install reads the snapshot as "the scan is done" and then opens
    the sidecar, so the snapshot must be the LAST thing the scan writes."""
    fixture = tmp_path / "replay.json"
    fixture.write_text(json.dumps(FIXTURE), encoding="utf-8")
    sidecar = tmp_path / "logs" / "week_review_latest.full.json"
    snap = tmp_path / "scan.json"
    monkeypatch.setattr(wr, "LOGS_DIR", str(sidecar.parent))
    monkeypatch.setattr(wr, "FULL_SIDECAR_PATH", str(sidecar))
    seen = {}
    real = wr._write_saved_scan

    def spy(output, args):
        seen["sidecar_existed"] = sidecar.exists()
        seen["snapshot_existed"] = snap.exists()
        return real(output, args)
    monkeypatch.setattr(wr, "_write_saved_scan", spy)

    _main(monkeypatch, capsys, "--input", str(fixture), "--since", "14",
          "--no-extensions", "--save-scan", str(snap))
    assert seen == {"sidecar_existed": True, "snapshot_existed": False}
    assert snap.exists() and sidecar.exists()
    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["kind"] == "week_review_scan" and all(saved["thread_ids"][k] for k in wr.THREAD_SECTIONS)
    # The content is still the PRE-finalize sections (captured before, written after).
    assert len(saved["sections"]["cold_urgent"]) == len(FIXTURE["cold_urgent"])
