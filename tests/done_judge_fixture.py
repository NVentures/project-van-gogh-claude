"""A planted vault for the check-off tests. Not a test file.

Every path the judge and the one writer resolve is pointed at a temp dir by
patching the narrow accessor it is read through, never the shared config
object, so nothing here can leak into another test in the session.
"""
import json
from types import SimpleNamespace

import config_loader
import context_pack
import done_judge as dj
import morning_coffee as mc

TODAY = "2026-10-10"
HEADING = "Test's Action Items"

CB_PLAN = "cb-aaaa0001"      # has later decision evidence
CB_PERMIT = "cb-aaaa0002"    # old, undated, no deal, no evidence
CB_DATED = "cb-aaaa0003"     # old but carries a due date
CB_DEAL = "cb-aaaa0004"      # old but belongs to a live deal
CB_OWED = "cb-aaaa0005"      # owed to the user

REVIEW = "Site plan review - 2026-09-20"
FOLLOW_UP = "Site plan follow up - 2026-09-27"
PROOF = "The revised site plan went to Priya Raman on the 26th and she signed off."

HOTCACHE = f"""---
updated: 2026-10-09
---
# Hot Cache

## Active Threads

### Marlowe Finch refinancing
<!-- deal: stage=active last_contact=2026-10-01 -->
Waiting on their comments.

## {HEADING}

- [ ] Send Priya Raman the revised site plan (from [[{REVIEW}]]) <!-- cb={CB_PLAN} opened=2026-09-20 -->
- [ ] Renew the parking permit for the office <!-- cb={CB_PERMIT} opened=2026-09-01 -->
- [ ] Order the replacement badge printer (due 2026-10-20) <!-- cb={CB_DATED} opened=2026-09-01 -->
- [ ] Chase the Marlowe Finch term sheet comments <!-- cb={CB_DEAL} opened=2026-09-01 -->

### Owed to Test

- [ ] **Rosa Calder:** Send the boundary survey (from [[{REVIEW}]]) <!-- cb={CB_OWED} opened=2026-09-20 -->

## Key Numbers

- [ ] not a to-do, outside the section
"""

REVIEW_PAGE = f"""---
type: source
title: {REVIEW}
meeting_id: note_review
meeting_source: granola
created: 2026-09-21
attendees: ["[[Priya Raman]]", "[[Test User]]"]
---
# {REVIEW}

## Decisions Made

- Priya Raman gets the revised site plan before the permit hearing.

## What You Owe

- [ ] Send Priya Raman the revised site plan <!-- cb={CB_PLAN} opened=2026-09-20 -->

## What Others Owe You

- [ ] **Rosa Calder:** Send the boundary survey <!-- cb={CB_OWED} opened=2026-09-20 -->
"""

FOLLOW_UP_PAGE = f"""---
type: source
title: {FOLLOW_UP}
created: 2026-09-27
---
# {FOLLOW_UP}

## Decisions Made

- {PROOF}

## Notes
"""

WEEK = f"""# Week of 2026-10-05

## Open Tasks (Obsidian)

- [ ] [Action Items] Send Priya Raman the revised site plan (from [[{REVIEW}]]) <!-- cb={CB_PLAN} opened=2026-09-20 -->
- [ ] [Action Items] Renew the parking permit for the office <!-- cb={CB_PERMIT} opened=2026-09-01 -->
"""


def make_vault(tmp_path, monkeypatch, hotcache=HOTCACHE, newline="\n"):
    vault = tmp_path / "vault"
    sources = vault / "wiki" / "sources"
    root = vault / "van-gogh"
    logs = root / "logs"
    state = tmp_path / "state"
    for folder in (sources, logs, state, vault / "wiki" / "entities"):
        folder.mkdir(parents=True, exist_ok=True)
    hot = vault / "wiki" / "hotcache.md"
    week = root / "week.md"
    files = {hot: hotcache, week: WEEK,
             sources / f"{REVIEW}.md": REVIEW_PAGE,
             sources / f"{FOLLOW_UP}.md": FOLLOW_UP_PAGE}
    for path, text in files.items():
        if text is None:
            continue
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", newline))

    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.delenv("VAN_GOGH_SCHEDULED", raising=False)
    monkeypatch.setattr(config_loader, "vault",
                        lambda sub="": vault / sub if sub else vault)
    monkeypatch.setattr(config_loader, "van_gogh_root", lambda: root)
    monkeypatch.setattr(config_loader, "logs_dir", lambda: logs)
    monkeypatch.setattr(config_loader, "hotcache_path", lambda: hot)
    monkeypatch.setattr(config_loader, "sources_dir", lambda: sources)
    monkeypatch.setattr(config_loader, "workspace_week_md_path", lambda: week)
    monkeypatch.setattr(config_loader, "kpi_enabled", lambda: True)
    monkeypatch.setattr(context_pack, "entities_dir",
                        lambda: vault / "wiki" / "entities")
    monkeypatch.setattr(mc, "HOTCACHE_PATH", str(hot))
    monkeypatch.setattr(mc, "WORKSPACE_WEEK_MD", str(week))
    return SimpleNamespace(
        vault=vault, hot=hot, week=week, logs=logs, state=state,
        sources=sources, review=sources / f"{REVIEW}.md",
        follow_up=sources / f"{FOLLOW_UP}.md")


def ctx(today=TODAY, mail=()):
    """A run context with a fixed user and a mailbox that was read in full."""
    return dj.Ctx(today=today, user_name="Test User",
                  user_variants=["Test User", "Test"],
                  self_emails=["primary@gmail.com"], mail=list(mail))


class Reply:
    stderr = ""

    def __init__(self, text, returncode=0):
        self.stdout = text
        self.returncode = returncode


def answering(rows=None, default=None, log=None):
    """A runner answering per item id. `rows` maps id to the fields of its
    answer; an id with no row gets `default`, or is left out when that is
    None. `log` collects the prompts it was sent."""
    rows = rows or {}

    def run(prompt, model=None, timeout=None):
        if log is not None:
            log.append(prompt)
        import re
        out = []
        for cid in re.findall(r"^--- ITEM (\S+)$", prompt, re.M):
            row = rows.get(cid, default)
            if row is None:
                continue
            out.append({"id": cid, "verdict": "open", "confidence": 0.95,
                        "evidence_class": "none", "citation": "",
                        "reasoning": "stub", "done_date": "", **row})
        return Reply(json.dumps({"items": out}))

    return run


DONE_PLAN = {CB_PLAN: {"verdict": "done", "evidence_class": "decision",
                       "citation": PROOF}}


def read(path) -> bytes:
    return path.read_bytes()


def kpi_rows(v, kind="closed"):
    path = v.logs / "kpi_events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == kind]


def tree(v) -> dict:
    """Every file in the vault, as bytes."""
    return {str(p.relative_to(v.vault)): p.read_bytes()
            for p in sorted(v.vault.rglob("*")) if p.is_file()}
