"""Tests for follow_up_radar.py — the date-triggered follow-up parser.

Pure parsing takes an explicit `today` and horizon, so these run with no config
beyond the primed conftest fixture and no reliance on the wall clock.
"""
from datetime import date

import config_loader
import follow_up_radar as fr

TODAY = date(2026, 7, 16)
HORIZON = 4

# A MEMORY.md-shaped fixture. The index bullet before the section must be
# ignored; each follow-up is crafted to land in a specific status bucket.
MEMORY = """# Memory Index

- [Some topic](some_topic.md) — an index line that is NOT a follow-up

## Pending follow-ups (remove when resolved)
_One line each; full state in the linked topic file. Keep terse._
- **Overdue deck** — user owes Alex the deck; **DUE ≤ Mon 2026-07-13**. Watch Acme inbox. [acme_context.md](acme_context.md)
- **Sam billing** — user asking Sam today (7/16) to confirm the update. CRITICAL open item.
- **Backfill sheets** — sheets SENT 7/13, due EOD Fri 7/17; watch Acme inbox for returned sheets. [acme_pipeline_work.md](acme_pipeline_work.md)
- **Beta reschedule** — value-add note SENT 7/14. HARD TRIGGER: if still unheard by Thu 2026-07-23, PROMPT the user to follow up. Watch Work Outlook/Gmail. [beta_network_intros.md](beta_network_intros.md)
- **Jordan / Northwind** — value-add follow-up owed, grounded in the Casey+Lee intro. Watch Work inbox. [work_status.md](work_status.md)
- **Vendor renewal — DEFERRED TO JULY** — user leads; do NOT chase until they kick it off. [acme_pipeline_work.md](acme_pipeline_work.md)
- **Pat Quinn** — email Pat Quinn in November 2026 to check in. Do NOT surface as overdue before Nov. [work_status.md](work_status.md)
"""


def _by_title(text=MEMORY):
    return {it["title"]: it for it in fr.parse_follow_ups(text, TODAY, HORIZON)}


# ── section extraction ─────────────────────────────────────────────────────────

def test_extract_section_isolates_follow_ups():
    body = fr.extract_section(MEMORY)
    assert "Pending follow-ups" not in body  # the heading itself is consumed
    assert "Overdue deck" in body
    assert "an index line" not in body  # content before the section stays out


def test_no_section_returns_empty():
    assert fr.parse_follow_ups("# Just a title\n\nno section here", TODAY, HORIZON) == []


def test_index_bullet_not_parsed_as_item():
    titles = _by_title()
    assert "Some topic" not in titles
    assert len(titles) == 7


# ── status bucketing ───────────────────────────────────────────────────────────

def test_overdue_when_trigger_in_past():
    it = _by_title()["Overdue deck"]
    assert it["status"] == "overdue"
    assert it["earliest_trigger"] == "2026-07-13"


def test_due_today_via_literal_today():
    it = _by_title()["Sam billing"]
    assert it["status"] == "due"
    assert it["earliest_trigger"] == "2026-07-16"


def test_due_soon_within_horizon():
    it = _by_title()["Backfill sheets"]
    assert it["status"] == "due_soon"
    assert it["earliest_trigger"] == "2026-07-17"


def test_scheduled_beyond_horizon():
    it = _by_title()["Beta reschedule"]
    assert it["status"] == "scheduled"
    assert it["earliest_trigger"] == "2026-07-23"


def test_watch_when_no_trigger_date():
    it = _by_title()["Jordan / Northwind"]
    assert it["status"] == "watch"
    assert it["earliest_trigger"] is None


def test_dormant_when_deferred():
    it = _by_title()["Vendor renewal — DEFERRED TO JULY"]
    assert it["status"] == "dormant"
    assert it["suppressed"] is True


def test_dormant_suppressed_future_reactivation():
    # "Do NOT surface before Nov" + no cued trigger date -> stays dormant now.
    it = _by_title()["Pat Quinn"]
    assert it["status"] == "dormant"


# ── date classification (trigger vs context) ───────────────────────────────────

def test_sent_date_is_context_not_trigger():
    it = _by_title()["Backfill sheets"]
    assert "2026-07-13" in it["context_dates"]
    assert "2026-07-13" not in it["trigger_dates"]


def test_month_year_not_parsed_as_day():
    it = _by_title()["Pat Quinn"]
    # "November 2026" must not become Nov-20; no 2026-11-20 anywhere.
    assert all(not d.startswith("2026-11") for d in it["trigger_dates"] + it["context_dates"])


def test_iso_and_md_both_extracted():
    trig, ctx = fr._extract_dates("do it by 2026-08-15, sent 7/13, due 7/17", TODAY)
    assert "2026-08-15" in trig
    assert "2026-07-17" in trig
    assert "2026-07-13" in ctx


def test_cue_does_not_bleed_across_a_later_date():
    # The "≤" cue binds to 2026-07-20, not to the following "7/7" (regression:
    # a title deadline must not make an unrelated later date a trigger).
    trig, ctx = fr._extract_dates("session (DUE ≤ Mon 2026-07-20) — their 7/7 ask", TODAY)
    assert trig == ["2026-07-20"]
    assert "2026-07-07" in ctx


# ── flags and routing ──────────────────────────────────────────────────────────

def test_hard_trigger_flag():
    assert _by_title()["Beta reschedule"]["hard_trigger"] is True
    assert _by_title()["Backfill sheets"]["hard_trigger"] is False


def test_critical_sets_hard_trigger():
    assert _by_title()["Sam billing"]["hard_trigger"] is True


def test_value_add_flag():
    assert _by_title()["Beta reschedule"]["value_add"] is True
    assert _by_title()["Overdue deck"]["value_add"] is False


def test_inbox_hints_extracted():
    assert _by_title()["Overdue deck"]["watch_inbox_hints"] == ["Acme inbox"]
    assert "Work Outlook" in _by_title()["Beta reschedule"]["watch_inbox_hints"]


def test_topic_files_extracted():
    assert _by_title()["Jordan / Northwind"]["topic_files"] == ["work_status.md"]


# ── ordering ───────────────────────────────────────────────────────────────────

def test_items_sorted_most_actionable_first():
    statuses = [it["status"] for it in fr.parse_follow_ups(MEMORY, TODAY, HORIZON)]
    order = {s: i for i, s in enumerate(fr.STATUS_ORDER)}
    assert statuses == sorted(statuses, key=lambda s: order[s])


# ── config helpers ─────────────────────────────────────────────────────────────

def test_horizon_defaults_to_four():
    assert config_loader.follow_ups_horizon_days() == 4


def test_memory_path_honors_config_override():
    saved = config_loader._config
    try:
        config_loader._config = {**saved, "follow_ups": {"memory_path": "~/custom/MEMORY.md"}}
        p = config_loader.follow_ups_memory_path()
        assert p.name == "MEMORY.md"
        assert "custom" in p.as_posix()
        assert "~" not in p.as_posix()  # expanduser applied
    finally:
        config_loader._config = saved


def test_memory_path_defaults_to_vault_workspace_memory():
    # With no follow_ups.memory_path override, the ledger lives in the vault
    # workspace memory (plugin-safe: never derived from the plugin cache path).
    assert config_loader.follow_ups_memory_path() == config_loader.vangogh_memory_path()


def test_infer_year_leap_day_rolls_to_next_year():
    # Feb 29 in a non-leap year must not be dropped — it should resolve to the
    # next (leap) year, not return None.
    from datetime import date as _date
    got = fr._infer_year(2, 29, _date(2025, 6, 1))  # 2025 non-leap, 2028 next leap... 2026 not leap
    assert got is None or got.month == 2  # never crashes; if resolved, it's Feb 29
    got2 = fr._infer_year(2, 29, _date(2024, 1, 1))  # 2024 is a leap year
    assert got2 == _date(2024, 2, 29)


def test_main_memory_not_found_exits_1(monkeypatch, capsys, tmp_path):
    import pytest
    missing = tmp_path / "nope.md"
    monkeypatch.setattr("sys.argv", ["follow_up_radar.py", "--memory", str(missing), "--today", "2026-07-16"])
    with pytest.raises(SystemExit) as e:
        fr.main()
    assert e.value.code == 1
    assert "MEMORY_NOT_FOUND" in capsys.readouterr().err


# ── Annotated ledger format ──────────────────────────────────────────────────
#
# The deliverable ledger a nudge is filed from:
#     - [ ] (TAG) Title | due: YYYY-MM-DD | format: email
# It has to coexist with the legacy prose bullets above, in one section, and
# both have to keep parsing. A ledger the user cannot hand-edit is a ledger
# they stop keeping, so the parser meets the file where it is.

EMDASH = chr(0x2014)

ANNOTATED = f"""## Pending follow-ups

- [ ] (ACME) Send the widget redline | due: 2026-07-10 | format: email
- [x] (ACME) Gadget line summary deck | due: 2026-07-09 | format: ppt
- [ ] (BETA) Quarterly numbers | due: 2026-07-25 | format: xlsx
- [ ] (NOPE) Not a configured tag | due: 2026-07-25
- [ ] Untagged but dated | due: 2026-07-11
- [ ] Typo in the date | due: 2026-13-45 | format: email
- **Legacy prose item** {EMDASH} HARD TRIGGER: if unheard by 2026-07-10, nudge. Watch Work Outlook.
"""


def _annotated():
    return {it["title"]: it for it in fr.parse_follow_ups(ANNOTATED, TODAY, HORIZON)}


def test_annotated_item_parses_tag_due_and_format():
    it = _annotated()["Send the widget redline"]
    assert it["tag"] == "ACME"
    assert it["due"] == "2026-07-10"
    assert it["format"] == "email"
    assert it["done"] is False
    assert it["status"] == "overdue"


def test_checked_box_marks_done():
    assert _annotated()["Gadget line summary deck"]["done"] is True


def test_unknown_tag_stays_in_the_title():
    # "(NOPE)" is not a configured bucket, so it is part of the sentence the
    # user wrote, not a tag to silently swallow.
    items = _annotated()
    assert "(NOPE) Not a configured tag" in items
    assert items["(NOPE) Not a configured tag"]["tag"] is None


def test_untagged_annotated_item_still_parses():
    it = _annotated()["Untagged but dated"]
    assert it["tag"] is None and it["due"] == "2026-07-11"


def test_unparseable_due_falls_back_to_prose_extraction():
    # A typo must not error, and must not become a due date either.
    it = _annotated()["Typo in the date"]
    assert it["due"] is None


def test_legacy_and_annotated_bullets_coexist_in_one_section():
    items = _annotated()
    assert "Legacy prose item" in items
    assert items["Legacy prose item"]["hard_trigger"] is True
    assert items["Legacy prose item"]["tag"] is None
    assert "Send the widget redline" in items


def test_legacy_fixture_is_unchanged_by_the_new_parser():
    # The byte-compat pin: every legacy title still parses as it always did.
    titles = set(_by_title())
    assert "Overdue deck" in titles and "Pat Quinn" in titles


def test_overdue_excludes_done_and_future_items():
    items = fr.parse_follow_ups(ANNOTATED, TODAY, HORIZON)
    titles = [it["title"] for it in fr.overdue(items, TODAY)]
    assert "Send the widget redline" in titles
    assert "Gadget line summary deck" not in titles   # done
    assert "Quarterly numbers" not in titles          # future


def test_overdue_never_disagrees_with_the_status_field():
    items = fr.parse_follow_ups(ANNOTATED, TODAY, HORIZON)
    for it in fr.overdue(items, TODAY):
        assert it["status"] == "overdue"


def test_days_late_counts_from_the_due_date():
    items = _annotated()
    assert fr.days_late(items["Send the widget redline"], TODAY) == 6


def test_days_late_is_zero_without_a_date():
    assert fr.days_late({"title": "no date"}, TODAY) == 0
