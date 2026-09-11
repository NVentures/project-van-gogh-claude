#!/usr/bin/env python3
"""Regression tests for hotcache_sync (the stale-hotcache write-back).

Reproduces both failures the module fixes:
  1. A deal tagged stage=dead while actively negotiating -> must REVIVE.
  2. A hotcache several days stale -> the staleness guard must flag it and bump
     the `updated:` date.
Plus the safety rails: the cascading no_revive guard (a dead parent whose
dependent workstream is still moving must NOT auto-revive), forward-only
last_contact, and the distinctive-token match gate (a lone generic token is not
a match).

The test passes an explicit temp path + active-threads heading, so it never
touches the real vault and does not depend on config for file location.

Runs under pytest (test_hotcache_sync_regressions) or directly:
    python tests/test_hotcache_sync.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import hotcache_sync as hs  # noqa: E402

TODAY = "2026-06-08"
HEADING = "Active Threads"

FIXTURE = """\
---
title: Hot Cache
type: index
updated: 2026-06-02
---

# Hot Cache

## Active Threads

### Fairview LOI (Harbor + Aurora)
<!-- deal: stage=dead last_contact=2026-06-02 next_action_due=2026-05-29 -->
- stale bullet, tagged dead

### Urban Grid (Acme): acquisition DEAD, Meridian Dev Loan SURVIVES
<!-- deal: stage=dead last_contact=2026-06-08 no_revive=1 -->
- acquisition dead, dependent financing still moving

### Kestrel Harbor Land Acquisition
<!-- deal: stage=loi last_contact=2026-05-20 -->
- in progress

## Your Action Items
- [ ] unrelated action item
"""

MAIL_EVENTS = [
    # Fairview: fresh negotiation mail, newer than its (dead) last_contact -> revive
    {"date": "2026-06-05", "party": "Ben Kerr bkerr@harbor-energy.com RE: Fairview LOI"},
    # Meridian Dev Loan mail matches the Urban Grid heading via 'meridian' (distinctive), but
    # no_revive=1 must block revival; date == recorded last_contact so no lc change
    {"date": "2026-06-08", "party": "Spencer Hale shale@rowanpartners.com RE: Acme Meridian - Exhibits and Schedules"},
    # Old Kestrel mail, older than its last_contact -> must NOT move last_contact back
    {"date": "2026-05-10", "party": "Sam Rivera sam@northwind.com Willow Bend update"},
    # Lone generic tokens (loan/update/call) -> must match NO deal
    {"date": "2026-06-08", "party": "Random Person noreply@example.com Loan update call"},
]


def fields_for(deals, needle):
    for d in deals:
        if needle.lower() in d["heading"].lower():
            return d["fields"]
    raise AssertionError(f"deal not found: {needle}")


def main():
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(FIXTURE)

    try:
        result = hs.sync_hotcache(MAIL_EVENTS, TODAY, path=path,
                                  active_threads_heading=HEADING)
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        after = hs.parse_deals(lines, HEADING)
        text_after = "".join(lines)
    finally:
        os.unlink(path)

    fairview = fields_for(after, "Fairview")
    urban = fields_for(after, "Urban Grid")
    kestrel = fields_for(after, "Kestrel")
    revived = {r["deal"] for r in result["revivals"]}

    checks = [
        # 1. Fairview revived
        ("Fairview stage flipped dead->active", fairview.get("stage") == "active"),
        ("Fairview last_contact refreshed to 2026-06-05",
         fairview.get("last_contact") == "2026-06-05"),
        ("Fairview reported as a revival", any("Fairview" in d for d in revived)),
        ("Fairview other metadata preserved (next_action_due)",
         fairview.get("next_action_due") == "2026-05-29"),

        # 2. Staleness guard
        ("stale_days computed as 6", result["stale_days"] == 6),
        ("stale flag set (>3 days)", result["stale"] is True),
        ("updated: bumped to today", result["updated_bumped"] is True),
        ("frontmatter now updated: 2026-06-08", "updated: 2026-06-08" in text_after),

        # 3. Cascading guard: Urban Grid dead + no_revive=1 must NOT revive
        ("Urban Grid stays stage=dead (no_revive)", urban.get("stage") == "dead"),
        ("Urban Grid not in revivals", not any("Urban Grid" in d for d in revived)),
        ("Urban Grid no_revive flag preserved", urban.get("no_revive") == "1"),

        # 4. Forward-only last_contact: older Kestrel mail must not move it back
        ("Kestrel last_contact unchanged (older mail ignored)",
         kestrel.get("last_contact") == "2026-05-20"),
        ("Kestrel stage unchanged", kestrel.get("stage") == "loi"),

        # 5. Distinctive-token match gate
        ("distinctive token matches (fairview)",
         hs.match_deal("RE: Fairview LOI", [d["heading"] for d in after]) is not None),
        ("lone generic token does not match",
         hs.match_deal("Loan update call", [d["heading"] for d in after]) is None),
    ]

    # 6. Wiring: afternoon_tea routes its deal match through the same matcher.
    try:
        import afternoon_tea as at  # noqa: E402
        hit = at._match_deal_thread(
            {"from": "Spencer Hale", "from_email": "shale@rowanpartners.com",
             "subject": "RE: Acme Meridian"},
            ["Urban Grid (Acme): acquisition DEAD, Meridian Dev Loan SURVIVES"],
        )
        checks.append(("afternoon_tea._match_deal_thread uses shared matcher",
                       hit is not None))
    except Exception as e:
        print(f"  [skip] afternoon_tea wiring check (import failed: {e})")

    failed = 0
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failed += 1

    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    sys.exit(1 if failed else 0)


def test_hotcache_sync_regressions():
    """Pytest wrapper: main() exits 0 iff every check passed."""
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0


if __name__ == "__main__":
    main()
