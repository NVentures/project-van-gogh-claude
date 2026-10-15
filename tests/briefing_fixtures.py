"""Deterministic briefing fixtures shared by the snapshot freeze and the tests.

One source of fixture data so a snapshot frozen from the pre-change code and
the test that re-renders it are looking at the same input. The fixtures are
plain dicts shaped like the sections `week_review` emits, built from a seeded
RNG so a "20 seeded random fixtures" claim is reproducible on any machine.
"""
from __future__ import annotations

import random

FOUR_BUSINESSES = [
    {"tag": "harbor", "display_name": "Harbor Solar",
     "project_page": "p.md", "meeting_route": "m",
     "keywords": ["harbor", "meridian", "qx"],
     "priorities": [{"name": "Close Meridian", "detail": "", "added": "2026-08-01"},
                    {"name": "Land the QX diligence", "detail": "", "added": "2026-08-01"}]},
    {"tag": "northwind", "display_name": "Northwind DC",
     "project_page": "p.md", "meeting_route": "m",
     "keywords": ["northwind", "kestrel", "lumen"],
     "priorities": [{"name": "Sign Kestrel", "detail": "", "added": "2026-08-01"}]},
    {"tag": "cedar", "display_name": "Cedar",
     "project_page": "p.md", "meeting_route": "m",
     "keywords": ["cedar", "cedarworks"],
     "priorities": []},
    {"tag": "personal", "display_name": "Personal",
     "project_page": "p.md", "meeting_route": "m",
     "keywords": ["dentist", "summit"],
     "priorities": []},
]


def set_businesses(cl, businesses=None) -> None:
    """Point config_loader at a fixture business list without touching disk."""
    cl._config = dict(cl._config)
    cl._config["businesses"] = list(businesses or FOUR_BUSINESSES)
    cl._config["judgment"] = {"enabled": False, "model": ""}


_SUBJECT_WORDS = [
    "Meridian redline", "QX diligence list", "Kestrel term sheet",
    "Lumen grid study", "Cedar pricing page", "Harbor interconnect filing",
    "Northwind lease exhibit", "Q3 invoice", "Summit travel", "Dentist follow up",
    "engineering finalists", "MIPA signature page", "vendor quote",
    "board packet", "site walk schedule", "payroll question",
]
_NAMES = ["Dana Ruiz", "Tim Roberts", "Ray Okafor", "Marco Lind", "Ana Beltre",
          "Priya N.", "Brandon B.", "Sam Ito"]
_SECTIONS = ("waiting_on_user", "inbox_pending", "cold_urgent", "cold_monitor")


def sample_sections() -> dict:
    """A small, fully deterministic section set. Used for the frozen snapshots."""
    return {
        "waiting_on_user": [
            {"source": "Outlook", "subject": "Meridian redline v4",
             "counterparty_name": "Dana Ruiz", "counterparty_email": "dana@meridian.com",
             "reply_age_days": 3, "days_late": 2, "body_preview": "redline attached"},
            {"source": "Outlook", "subject": "QX diligence list",
             "counterparty_name": "Ray Okafor", "counterparty_email": "ray@qx.com",
             "reply_age_days": 6, "body_preview": "the diligence list is attached"},
            {"source": "Gmail", "subject": "Cedar pricing page copy",
             "counterparty_name": "Marco Lind", "counterparty_email": "marco@cedar.io",
             "reply_age_days": 1, "due": "2026-09-04", "body_preview": "copy for the pricing page"},
        ],
        "inbox_pending": [
            {"source": "Outlook", "subject": "Kestrel term sheet",
             "counterparty_name": "Ana Beltre", "counterparty_email": "ana@kestrel.com",
             "reply_age_days": 2, "due": "2026-09-05", "body_preview": "term sheet for signature"},
            {"source": "Gmail", "subject": "Two engineering finalists for Northwind",
             "counterparty_name": "Priya N.", "counterparty_email": "priya@nw.com",
             "reply_age_days": 3, "body_preview": "two candidates to interview"},
        ],
        "cold_urgent": [
            {"source": "Gmail", "subject": "Austin summit slot",
             "counterparty_name": "Brandon B.", "counterparty_email": "b@summit.org",
             "age_days": 12, "body_preview": "your speaking slot"},
        ],
        "cold_monitor": [
            {"source": "Outlook", "subject": "Harbor Q3 invoice",
             "counterparty_name": "Sam Ito", "counterparty_email": "sam@vendor.com",
             "age_days": 21, "body_preview": "invoice 4417 attached"},
            {"source": "Gmail", "subject": "Dentist follow up",
             "counterparty_name": "Front Desk", "counterparty_email": "desk@dental.com",
             "age_days": 33, "body_preview": "time to book a cleaning"},
        ],
    }


def random_sections(seed: int, n_items: int = 120) -> dict:
    """`n_items` items spread across the four sections, reproducible per seed."""
    rng = random.Random(seed)
    out = {s: [] for s in _SECTIONS}
    for i in range(n_items):
        section = rng.choice(_SECTIONS)
        biz = rng.choice(FOUR_BUSINESSES)
        kw = rng.choice(biz["keywords"])
        entry = {
            "source": rng.choice(["Gmail", "Outlook"]),
            "subject": f"{kw.title()} {rng.choice(_SUBJECT_WORDS)} {i}",
            "counterparty_name": rng.choice(_NAMES),
            "counterparty_email": f"person{i}@example{i % 7}.com",
            "body_preview": "some text about the thing",
        }
        if section in ("waiting_on_user", "inbox_pending"):
            entry["reply_age_days"] = rng.randint(0, 40)
        else:
            entry["age_days"] = rng.randint(8, 90)
        # Roughly 3 percent late and 15 percent dated: the mix the product's
        # own reference render describes (2 late out of 68 open). The late
        # share is the one number that drives the page length, because a late
        # item is never folded, so it is set from the reference rather than
        # picked to clear a threshold. test_late_items_can_outgrow_the_page
        # measures what happens when it is far higher.
        roll = rng.random()
        if roll < 0.03:
            entry["days_late"] = rng.randint(1, 9)
        elif roll < 0.18:
            entry["due"] = f"2026-09-{rng.randint(1, 28):02d}"
        out[section].append(entry)
    return out
