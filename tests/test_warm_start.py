"""warm_start: what it proposes, what it refuses, and that it writes once.

The load-bearing properties, in order of what they cost when wrong:

1. Only ticked rows are written. The confirm gate IS the precision guarantee,
   so a build that writes every candidate regardless must fail a test.
2. A seeded deal carries both the counterparty EMAIL and a `[[Name]]` wikilink,
   because two different readers scan that section for two different things.
3. A seeded entity's `updated:` is the real last-contact date, or the
   relationship radar treats everyone as freshly touched and stays silent.
4. A second run adds nothing.
"""
import json
from datetime import datetime
from pathlib import Path

import config_loader as cl
import warm_start as ws

# The real hot-cache reader, captured before any test stubs it.
REAL_EXISTING_HEADINGS = ws._existing_headings


TODAY = "2026-09-16"


def _sidecar(**over):
    base = {
        "waiting_on_user": [
            {"counterparty_email": "alice@counsel.com", "counterparty_name": "Alice Ng",
             "subject": "Re: Harbor redline", "reply_age_days": 9, "source": "Gmail"},
        ],
        "inbox_pending": [
            {"counterparty_email": "bob@cedar.com", "counterparty_name": "Bob Reed",
             "subject": "Cedar diligence list", "reply_age_days": 3, "source": "Gmail"},
        ],
        "cold_urgent": [
            {"counterparty_email": "dana@grid.com", "counterparty_name": "Dana Vu",
             "subject": "Interconnect study", "age_days": 30, "source": "Outlook"},
        ],
        "cold_monitor": [],
    }
    base.update(over)
    return base


# ── propose ──────────────────────────────────────────────────────────────────

def test_proposes_a_deal_per_live_thread():
    out = ws.propose(_sidecar(), TODAY)
    emails = {d["email"] for d in out["deals"]}
    assert emails == {"alice@counsel.com", "bob@cedar.com", "dana@grid.com"}


def test_waiting_threads_get_a_due_date_and_cold_ones_do_not():
    """A reply the reader owes has a real next action. Inventing one for a
    thread the counterparty went quiet on manufactures urgency."""
    by_email = {d["email"]: d for d in ws.propose(_sidecar(), TODAY)["deals"]}
    assert by_email["alice@counsel.com"]["next_action_due"] == "2026-09-19"
    assert by_email["alice@counsel.com"]["stage"] == "active"
    assert by_email["dana@grid.com"]["next_action_due"] == ""
    assert by_email["dana@grid.com"]["stage"] == "outreach"


def test_last_contact_is_derived_from_thread_age():
    by_email = {d["email"]: d for d in ws.propose(_sidecar(), TODAY)["deals"]}
    assert by_email["alice@counsel.com"]["last_contact"] == "2026-09-07"   # 9d
    assert by_email["dana@grid.com"]["last_contact"] == "2026-08-17"       # 30d


def test_cold_monitor_is_never_a_deal():
    """7 to 14 days quiet with no reply is the weakest signal in the scan.
    Seeding it would put the reader's whole backlog in hotcache as if they
    were actively managing it."""
    sc = _sidecar(waiting_on_user=[], inbox_pending=[], cold_urgent=[],
                  cold_monitor=[{"counterparty_email": "faint@x.com",
                                 "counterparty_name": "Faint Signal",
                                 "subject": "Checking in", "age_days": 10}])
    assert ws.propose(sc, TODAY)["deals"] == []


def test_robots_internal_and_self_are_skipped(monkeypatch):
    monkeypatch.setattr(cl, "internal_team_emails", lambda: {"mate@company.com"})
    monkeypatch.setattr(cl, "account_emails_lower", lambda: {"me@company.com"})
    sc = _sidecar(waiting_on_user=[
        {"counterparty_email": "noreply@bank.com", "counterparty_name": "Bank",
         "subject": "Statement", "reply_age_days": 2},
        {"counterparty_email": "mate@company.com", "counterparty_name": "Team Mate",
         "subject": "Standup", "reply_age_days": 1},
        {"counterparty_email": "me@company.com", "counterparty_name": "Me",
         "subject": "Note to self", "reply_age_days": 1},
    ], inbox_pending=[], cold_urgent=[], cold_monitor=[])
    assert ws.propose(sc, TODAY)["deals"] == []


def test_a_thread_already_in_hotcache_is_not_proposed_again():
    out = ws.propose(_sidecar(), TODAY,
                     existing_headings=["Counsel: Harbor redline"])
    assert "alice@counsel.com" not in {d["email"] for d in out["deals"]}


def test_a_counterparty_with_a_live_deal_is_not_proposed_again():
    out = ws.propose(_sidecar(), TODAY, active_emails={"alice@counsel.com"})
    assert "alice@counsel.com" not in {d["email"] for d in out["deals"]}


def test_entities_need_a_full_name():
    """A bare first name makes a page nobody can wikilink and the radar cannot
    name, so it is worse than no page."""
    sc = _sidecar(waiting_on_user=[
        {"counterparty_email": "solo@x.com", "counterparty_name": "Solo",
         "subject": "Hello", "reply_age_days": 2}], inbox_pending=[],
        cold_urgent=[], cold_monitor=[])
    assert ws.propose(sc, TODAY)["entities"] == []


def test_deal_counterparties_always_get_a_page():
    names = {e["name"] for e in ws.propose(_sidecar(), TODAY)["entities"]}
    assert {"Alice Ng", "Bob Reed", "Dana Vu"} <= names


def test_headings_are_deduplicated_per_counterparty():
    """Two threads with one person is one deal, not two competing headings."""
    sc = _sidecar(waiting_on_user=[
        {"counterparty_email": "alice@counsel.com", "counterparty_name": "Alice Ng",
         "subject": "Harbor redline", "reply_age_days": 9},
        {"counterparty_email": "alice@counsel.com", "counterparty_name": "Alice Ng",
         "subject": "Harbor schedule", "reply_age_days": 4}],
        inbox_pending=[], cold_urgent=[], cold_monitor=[])
    deals = ws.propose(sc, TODAY)["deals"]
    assert [d["email"] for d in deals] == ["alice@counsel.com"]


# ── rendering ────────────────────────────────────────────────────────────────

def test_deal_block_carries_both_the_email_and_the_wikilink():
    """Two readers scan this section for two different things:
    week_review.load_active_thread_emails wants addresses, and
    relationship_radar.get_hotcache_active_names wants [[Name]]. A block with
    one of them fixes half the product."""
    row = ws.propose(_sidecar(), TODAY)["deals"][0]
    block = ws.render_deal_block(row)
    assert "alice@counsel.com" in block
    assert "[[Alice Ng]]" in block
    assert block.startswith("### ")
    assert "<!-- deal: stage=active last_contact=2026-09-07" in block


def test_deal_block_never_says_dead():
    for row in ws.propose(_sidecar(), TODAY)["deals"]:
        assert "stage=dead" not in ws.render_deal_block(row)


def test_entity_page_updated_is_the_last_contact_not_today():
    """relationship_radar reads `updated:` when sources is empty. Stamping
    today would make every seeded contact look freshly touched, and the radar
    would stay silent about exactly the people it exists to surface."""
    ent = [e for e in ws.propose(_sidecar(), TODAY)["entities"]
           if e["name"] == "Dana Vu"][0]
    page = ws.render_entity_page(ent, TODAY)
    assert "updated: 2026-08-17" in page
    assert "created: 2026-09-16" in page


def test_entity_page_carries_no_message_body():
    """Subjects and dates only. A body preview in a vault page is content the
    reader never chose to file."""
    ent = ws.propose(_sidecar(), TODAY)["entities"][0]
    page = ws.render_entity_page(ent, TODAY)
    assert "body_preview" not in page
    assert "preview" not in page.lower()


def test_rendered_output_has_no_dashes():
    prop = ws.propose(_sidecar(), TODAY)
    for row in prop["deals"]:
        block = ws.render_deal_block(row)
        assert "—" not in block and "–" not in block
    for ent in prop["entities"]:
        page = ws.render_entity_page(ent, TODAY)
        assert "—" not in page and "–" not in page


def test_insert_deals_lands_under_the_configured_heading():
    text = ("# Hot Cache\n\n## Active Threads\n\n<!-- a comment -->\n\n"
            "## Key Numbers\n\n| a | b |\n")
    out = ws.insert_deals(text, ["### New: thing\n"], "Active Threads")
    assert out.index("### New: thing") > out.index("## Active Threads")
    assert out.index("### New: thing") < out.index("## Key Numbers")


def test_insert_deals_appends_a_section_when_none_exists():
    out = ws.insert_deals("# Hot Cache\n", ["### New: thing\n"], "Active Threads")
    assert "## Active Threads" in out
    assert "### New: thing" in out


# ── apply ────────────────────────────────────────────────────────────────────

def _vault(tmp_path, monkeypatch):
    hot = tmp_path / "hotcache.md"
    hot.write_text("# Hot Cache\n\n## Active Threads\n\n## Key Numbers\n",
                   encoding="utf-8")
    ents = tmp_path / "entities"
    ents.mkdir()
    monkeypatch.setattr(cl, "hotcache_path", lambda: hot)
    monkeypatch.setattr(cl, "entities_dir", lambda: ents)
    monkeypatch.setattr(ws, "_existing_headings", lambda: [])
    monkeypatch.setattr(ws, "_existing_entity_names", lambda: set())
    return hot, ents


def test_apply_writes_only_the_ticked_rows(tmp_path, monkeypatch):
    """The confirm gate is the precision guarantee. A build that writes every
    candidate regardless of the tick must fail here."""
    hot, ents = _vault(tmp_path, monkeypatch)
    prop = ws.propose(_sidecar(), TODAY)
    written = ws.apply(prop, deal_idx=[0], entity_idx=[0], today=TODAY)
    assert (written["entities"], written["deals"]) == (1, 1)
    assert written["written"] == [prop["deals"][0]["heading"]] and written["skipped"] == []
    text = hot.read_text(encoding="utf-8")
    assert "alice@counsel.com" in text
    assert "bob@cedar.com" not in text
    assert "dana@grid.com" not in text
    assert len(list(ents.glob("*.md"))) == 1


def test_apply_with_nothing_ticked_writes_nothing(tmp_path, monkeypatch):
    hot, ents = _vault(tmp_path, monkeypatch)
    before = hot.read_text(encoding="utf-8")
    written = ws.apply(ws.propose(_sidecar(), TODAY), deal_idx=[], entity_idx=[],
                       today=TODAY)
    assert (written["entities"], written["deals"]) == (0, 0)
    assert hot.read_text(encoding="utf-8") == before
    assert list(ents.glob("*.md")) == []


def test_apply_is_idempotent(tmp_path, monkeypatch):
    hot, ents = _vault(tmp_path, monkeypatch)
    prop = ws.propose(_sidecar(), TODAY)
    ws.apply(prop, deal_idx=[0, 1], entity_idx=[0, 1], today=TODAY)
    after_first = hot.read_text(encoding="utf-8")
    n_files = len(list(ents.glob("*.md")))

    # Second run sees what the first wrote.
    headings = [d["heading"] for d in prop["deals"][:2]]
    monkeypatch.setattr(ws, "_existing_headings", lambda: headings)
    second = ws.apply(prop, deal_idx=[0, 1], entity_idx=[0, 1], today=TODAY)
    assert (second["entities"], second["deals"]) == (0, 0)
    assert second["written"] == []
    assert [(r["heading"], r["matched"], r["reason"]) for r in second["skipped"]] == [
        (h, h, "already in the hot cache") for h in headings]
    assert hot.read_text(encoding="utf-8") == after_first
    assert len(list(ents.glob("*.md"))) == n_files


def test_apply_writes_the_entity_before_the_deal_links_it(tmp_path, monkeypatch):
    """The deal body wikilinks the person, so the page has to exist or the link
    is red on the reader's first ever look at hotcache."""
    hot, ents = _vault(tmp_path, monkeypatch)
    prop = ws.propose(_sidecar(), TODAY)
    ws.apply(prop, deal_idx=[0], entity_idx=[0], today=TODAY)
    linked = [ln for ln in hot.read_text(encoding="utf-8").splitlines()
              if "[[" in ln]
    assert linked
    name = linked[0].split("[[")[1].split("]]")[0]
    assert (ents / f"{name}.md").exists()


def test_apply_tolerates_an_out_of_range_index(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    written = ws.apply(ws.propose(_sidecar(), TODAY), deal_idx=[99],
                       entity_idx=[99], today=TODAY)
    assert (written["entities"], written["deals"]) == (0, 0)


def test_seeded_deal_is_readable_by_the_gate_it_exists_to_feed(tmp_path, monkeypatch):
    """End to end against the real consumer: after apply,
    week_review.load_active_thread_emails must find the address, or the seeding
    changed a file without changing the product."""
    import week_review
    hot, _ = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(week_review, "HOTCACHE_PATH", str(hot))
    ws.apply(ws.propose(_sidecar(), TODAY), deal_idx=[0], entity_idx=[0],
             today=TODAY)
    assert "alice@counsel.com" in week_review.load_active_thread_emails()


def test_seeded_deal_parses_as_a_deal(tmp_path, monkeypatch):
    """hotcache_sync.parse_deals is what every stage advance and last_contact
    update reads. A block it cannot parse is decoration."""
    import hotcache_sync
    hot, _ = _vault(tmp_path, monkeypatch)
    ws.apply(ws.propose(_sidecar(), TODAY), deal_idx=[0], entity_idx=[0],
             today=TODAY)
    lines = hot.read_text(encoding="utf-8").splitlines(keepends=True)
    deals = hotcache_sync.parse_deals(lines, "Active Threads")
    assert len(deals) == 1
    assert deals[0]["fields"]["stage"] == "active"
    assert deals[0]["fields"]["last_contact"] == "2026-09-07"


def test_insert_deals_finds_the_heading_in_a_windows_file():
    """A vault synced from Windows carries CRLF and must land one section.

    The heading regex ends in a whitespace-tolerant anchor, so this passes
    with either rstrip: it is a behaviour test, not a proof of that line.
    The repo-wide rstrip guard in test_cross_platform.py is what keeps the
    habit, and it is right to: the next regex here may not be forgiving."""
    text = "# Hot Cache\r\n\r\n## Active Threads\r\n\r\n## Key Numbers\r\n"
    out = ws.insert_deals(text, ["### New: thing\n"], "Active Threads")
    assert out.count("## Active Threads") == 1
    assert "### New: thing" in out
    assert out.index("### New: thing") < out.index("## Key Numbers")


# ── priorities ───────────────────────────────────────────────────────────────
# The single highest-leverage config field: with priorities[] empty the judge
# never runs and the front page ranks by recency forever.

def _config_vault(tmp_path, monkeypatch, priorities=None):
    root = tmp_path / "van-gogh"
    root.mkdir()
    cfg = {"businesses": [
        {"tag": "acme", "display_name": "Acme Corp",
         "priorities": priorities or []},
        {"tag": "beta", "display_name": "Beta Labs", "priorities": []}]}
    (root / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    monkeypatch.setattr(cl, "van_gogh_root", lambda: root)
    return root / "config.json"


def test_priorities_are_written_into_the_named_bucket(tmp_path, monkeypatch):
    path = _config_vault(tmp_path, monkeypatch)
    out = ws.save_priorities({"acme": ["Close the Harbor deal", "Hire a PM"]},
                             today=TODAY)
    assert out["added"] == {"acme": ["Close the Harbor deal", "Hire a PM"]}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    acme = [b for b in cfg["businesses"] if b["tag"] == "acme"][0]
    assert [p["name"] for p in acme["priorities"]] == [
        "Close the Harbor deal", "Hire a PM"]
    assert acme["priorities"][0]["added"] == TODAY
    # An untouched bucket stays untouched.
    beta = [b for b in cfg["businesses"] if b["tag"] == "beta"][0]
    assert beta["priorities"] == []


def test_priorities_are_deduped_case_insensitively(tmp_path, monkeypatch):
    path = _config_vault(tmp_path, monkeypatch,
                         priorities=[{"name": "Close the Harbor deal"}])
    out = ws.save_priorities({"acme": ["close the harbor deal", "Hire a PM"]},
                             today=TODAY)
    assert out["added"] == {"acme": ["Hire a PM"]}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    acme = [b for b in cfg["businesses"] if b["tag"] == "acme"][0]
    assert len(acme["priorities"]) == 2


def test_priorities_backs_up_before_writing(tmp_path, monkeypatch):
    path = _config_vault(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    ws.save_priorities({"acme": ["Close the Harbor deal"]}, today=TODAY)
    assert path.with_suffix(".json.bak").read_text(encoding="utf-8") == before


def test_priorities_writes_nothing_when_nothing_was_chosen(tmp_path, monkeypatch):
    path = _config_vault(tmp_path, monkeypatch)
    before = path.read_text(encoding="utf-8")
    out = ws.save_priorities({"acme": []}, today=TODAY)
    assert out == {"added": {}, "backed_up": False}
    assert path.read_text(encoding="utf-8") == before
    assert not path.with_suffix(".json.bak").exists()


def test_priorities_survives_an_unreadable_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "van_gogh_root", lambda: tmp_path / "nope")
    assert "error" in ws.save_priorities({"acme": ["X"]}, today=TODAY)


# ── ingest ───────────────────────────────────────────────────────────────────

def test_ingest_uses_auto_so_tags_are_inferred_per_meeting(monkeypatch):
    """Each apply writes entity pages the NEXT meeting resolves against, so a
    tag computed up front for the whole batch is stale by the second one.

    This is the serial (nightly-shaped) path, which VAN_GOGH_SERIAL=1 selects;
    the two-phase path has its own tests in test_warm_start_pipeline.py."""
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")
    calls = []

    class _R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(ws.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _R())[1])
    out = ws.run_ingest(limit=10)
    assert out["ingested"] and out["backfilled"]
    assert "--auto" in calls[0]
    assert "--limit" in calls[0] and "10" in calls[0]
    # Backfill reads the source pages ingest just wrote, so it runs after.
    assert "--backfill" in calls[1]


def test_a_failed_ingest_costs_the_backlog_not_the_install(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")

    class _R:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(ws.subprocess, "run", lambda cmd, **kw: _R())
    out = ws.run_ingest()
    assert out["ingested"] is False
    assert out["errors"]


def test_an_ingest_crash_is_caught(monkeypatch):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")

    def boom(cmd, **kw):
        raise OSError("no interpreter")
    monkeypatch.setattr(ws.subprocess, "run", boom)
    out = ws.run_ingest()
    assert out["ingested"] is False
    assert out["backfilled"] is False
    assert out["errors"]


# ── A tick is an answer: dedup only against what was there before ────────────

def _two_waterloo_deals():
    return {"deals": [
        {"heading": "Bearpeakpower: Waterloo", "name": "Rae Lin",
         "email": "rae@bearpeakpower.com", "org": "Bearpeakpower",
         "subject": "Waterloo", "section": "waiting_on_user", "account": "Gmail",
         "business": "ACME", "stage": "active", "last_contact": "2026-09-10",
         "next_action_due": "2026-09-19"},
        {"heading": "Gridprojectsco: 8MW Waterloo Project: Solstice <> Grid Projects Co",
         "name": "Sam Ortiz", "email": "sam@gridprojectsco.com", "org": "Gridprojectsco",
         "subject": "8MW Waterloo Project: Solstice <> Grid Projects Co",
         "section": "inbox_pending", "account": "Gmail", "business": "ACME",
         "stage": "active", "last_contact": "2026-09-12", "next_action_due": "2026-09-19"},
        {"heading": "Bearpeakpower: Waterloo interconnect study", "name": "Rae Lin",
         "email": "rae@bearpeakpower.com", "org": "Bearpeakpower",
         "subject": "Waterloo interconnect study", "section": "cold_urgent",
         "account": "Gmail", "business": "ACME", "stage": "outreach",
         "last_contact": "2026-08-20", "next_action_due": ""},
    ], "entities": [], "today": TODAY}


def test_two_ticked_deals_sharing_a_place_word_are_both_written(tmp_path, monkeypatch):
    """The live defect: the third of three ticked deals was dropped as a
    duplicate of one written seconds earlier in the same batch, on the lone
    shared token "waterloo", and nothing said so."""
    hot, _ = _vault(tmp_path, monkeypatch)
    prop = _two_waterloo_deals()
    out = ws.apply(prop, deal_idx=[0, 1], entity_idx=[], today=TODAY)
    assert out["deals"] == 2 and out["skipped"] == []
    assert out["written"] == [prop["deals"][0]["heading"], prop["deals"][1]["heading"]]
    text = hot.read_text(encoding="utf-8")
    assert "### Bearpeakpower: Waterloo\n" in text
    assert "### Gridprojectsco: 8MW Waterloo Project" in text


def test_a_true_duplicate_of_a_pre_existing_deal_is_skipped_and_said(tmp_path, monkeypatch):
    hot, _ = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(ws, "_existing_headings",
                        lambda: ["Bearpeakpower: Waterloo site study"])
    prop = _two_waterloo_deals()
    out = ws.apply(prop, deal_idx=[2, 1], entity_idx=[], today=TODAY)
    # "bearpeakpower" + "waterloo" shared with the existing deal: the same one.
    assert out["skipped"] == [{
        "heading": "Bearpeakpower: Waterloo interconnect study",
        "matched": "Bearpeakpower: Waterloo site study",
        "reason": "the same deal as one already in the hot cache "
                  "(two shared distinctive tokens)"}]
    # Only "waterloo" shared: a different deal, written.
    assert out["written"] == [prop["deals"][1]["heading"]] and out["deals"] == 1
    assert "Gridprojectsco" in hot.read_text(encoding="utf-8")


def test_verify_picked_names_what_landed_and_what_is_missing(tmp_path, monkeypatch):
    hot, _ = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(ws, "_existing_headings", REAL_EXISTING_HEADINGS)
    import week_review
    monkeypatch.setattr(week_review, "load_active_thread_emails", lambda: set())
    prop = _two_waterloo_deals()
    out = ws.apply(prop, deal_idx=[0, 1, 2], entity_idx=[], today=TODAY)
    assert out["deals"] == 3 and out["skipped"] == []

    v = ws.verify(_sidecar(), TODAY, picked=[0, 1, 2], proposal=prop)
    assert v["picked"] == 3 and v["missing"] == []
    assert v["landed"] == [d["heading"] for d in prop["deals"]]
    assert "new_deals" in v and "new_entities" in v

    # A second apply of the same picks writes nothing and says why.
    again = ws.apply(prop, deal_idx=[0, 1, 2], entity_idx=[], today=TODAY)
    assert again["deals"] == 0 and again["written"] == []
    assert {r["reason"] for r in again["skipped"]} == {"already in the hot cache"}

    # Lose one block from the file: verify names it as missing.
    text = hot.read_text(encoding="utf-8")
    start = text.index("### Gridprojectsco")
    end = text.index("### ", start + 4)
    hot.write_text(text[:start] + text[end:], encoding="utf-8")
    v = ws.verify(_sidecar(), TODAY, picked=[0, 1, 2], proposal=prop)
    assert v["missing"] == [prop["deals"][1]["heading"]]
    assert len(v["landed"]) == 2


def test_the_scaffold_sample_deal_is_removed_on_the_first_real_write(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vg_scaffold", str(Path(ws.__file__).resolve().parent.parent / "vault" / "scaffold.py"))
    scaffold = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scaffold)
    hot, _ = _vault(tmp_path, monkeypatch)
    hot.write_text(scaffold.HOTCACHE_STUB.format(date=TODAY), encoding="utf-8")
    monkeypatch.setattr(ws, "_existing_headings", REAL_EXISTING_HEADINGS)
    assert ws._existing_headings() == ["Thread Name"], "the sample parses as a deal"

    prop = _two_waterloo_deals()
    out = ws.apply(prop, deal_idx=[0], entity_idx=[], today=TODAY)
    assert out["sample_removed"] is True and out["deals"] == 1
    text = hot.read_text(encoding="utf-8")
    assert "### Thread Name" not in text
    assert "Stages: closing" in text, "the rest of the scaffold's comment is kept"
    # The real deal sits after the comment closes, not inside it.
    assert text.index("-->") < text.index("### Bearpeakpower: Waterloo")
    assert ws._existing_headings() == ["Bearpeakpower: Waterloo"]
    # Writing again does not claim to remove it a second time.
    out = ws.apply(prop, deal_idx=[1], entity_idx=[], today=TODAY)
    assert out["sample_removed"] is False and out["deals"] == 1
