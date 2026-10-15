"""A three-meeting notetaker fixture that drives meeting_ingest end to end.

Shared by tests/test_meeting_ingest_staged.py and by the one-off command that
produced tests/snapshots/nightly_ingest_golden.json from the pinned base
commit (85b11d5). The same function runs either revision's module over the
same vault with the same frozen clock and the same stubbed model, so the two
outputs are comparable byte for byte.

Regenerate the golden ONLY to re-pin against the base commit, never to make
a red test green:

    git show 85b11d5:project-van-gogh/app/meeting_ingest.py > /tmp/mi_base.py
    .venv/bin/python -c "import sys; sys.path[:0] = ['app', 'tests'];
        import ingest_fixture as f; f.write_golden('/tmp/mi_base.py',
        'tests/snapshots/nightly_ingest_golden.json')"
"""
import contextlib
import importlib.util
import io
import json
import types
from datetime import datetime, timezone
from pathlib import Path

import meeting_synthesis

FROZEN_DAY = "2026-10-01"
_FROZEN = datetime(2026, 10, 1, 9, 30, 0)

NOTES = [
    {
        "id": "note_a1",
        "title": "Harbor Energy site walk",
        "created_at": "2026-09-30T17:00:00Z",
        "updated_at": "2026-09-30T18:00:00Z",
        "summary_markdown": (
            "## Summary\n\nWalked the Harbor parcel with Dana Vu. The substation "
            "tie-in looks feasible from the north road.\n\n"
            "## Action Items\n\n- **Test User**: send Dana Vu the revised layout\n"
            "- **Dana Vu**: confirm the easement with counsel\n"
        ),
        "attendees": [{"name": "Dana Vu", "email": "dana@grid.com"},
                      {"name": "Test User", "email": "primary@gmail.com"}],
        "calendar_event": {"scheduled_start_time": "2026-09-30T17:00:00Z"},
        "owner": None, "folder_membership": [], "share_url": None,
        "source": "granola",
    },
    {
        "id": "note_b2",
        "title": "Acme widget pricing call",
        "created_at": "2026-09-29T20:00:00Z",
        "updated_at": "2026-09-29T21:00:00Z",
        "summary_markdown": (
            "## Summary\n\nSam Ortiz walked through the widget pricing tiers.\n\n"
            "## Next Steps\n\n- **Test User**: share the acme volume forecast\n"
        ),
        "attendees": [{"name": "Sam Ortiz", "email": "sam@acme.com"},
                      {"name": "Test User", "email": "primary@gmail.com"}],
        "calendar_event": {"scheduled_start_time": "2026-09-29T20:00:00Z"},
        "owner": None, "folder_membership": [], "share_url": None,
        "source": "granola",
    },
    {
        "id": "note_c3",
        "title": "Weekly internal sync",
        "created_at": "2026-09-28T16:00:00Z",
        "updated_at": "2026-09-28T17:00:00Z",
        "summary_markdown": (
            "## Summary\n\nStatus round. Nothing decided.\n\n"
            "## Action Items\n\n- **Test User**: book the offsite room\n"
        ),
        "attendees": [{"name": "Test User", "email": "primary@gmail.com"}],
        "calendar_event": {"scheduled_start_time": "2026-09-28T16:00:00Z"},
        "owner": None, "folder_membership": [], "share_url": None,
        "source": "granola",
    },
]

# A transcript long enough that the real synthesize would not refuse it, so
# the stubbed one is reached on the same branch the real one would be.
_TRANSCRIPT = [
    {"speaker": {"source": "microphone" if i % 2 else "speaker"},
     "text": f"Turn {i}: a sentence about the site, the pricing and the plan."}
    for i in range(40)
]

SYNTHESES = {
    "note_a1": {
        "truncated": False,
        "discussed": "The Harbor parcel walk and the substation tie-in.",
        "decisions": ["Tie in from the north road"],
        "changes": [],
        "my_commitments": ["Send Dana Vu the revised layout by Friday"],
        "their_commitments": ["Dana Vu confirms the easement with counsel"],
        "next_steps": ["Test User: layout, Friday"],
        "open_questions": ["Who owns the access road?"],
        "subtext": [],
    },
    "note_b2": {
        "truncated": False,
        "discussed": "Widget pricing tiers for the Acme volume forecast.",
        "decisions": [],
        "changes": ["Tier 2 pricing moved from 100 to 250 units"],
        "my_commitments": ["Share the acme volume forecast"],
        "their_commitments": [],
        "next_steps": ["Sam Ortiz: revised sheet next week"],
        "open_questions": [],
        "subtext": ["Sam Ortiz hedged on the tier 2 number"],
    },
    # The third meeting has no synthesis: the fallback-to-summary branch.
    "note_c3": None,
}


class FakeProvider:
    NAME = "granola"
    DISPLAY_NAME = "Granola"

    def __init__(self, notes=None, on_fetch=None):
        self.notes = {n["id"]: n for n in (notes or NOTES)}
        self.order = [n["id"] for n in (notes or NOTES)]
        self.on_fetch = on_fetch
        self.fetched = []

    @staticmethod
    def api_key():
        return "fake"

    def iter_stubs(self, since=None, limit=None):
        for nid in self.order[: (limit or len(self.order))]:
            n = self.notes[nid]
            yield {"id": nid, "title": n["title"], "created_at": n["created_at"]}

    def latest_id(self):
        return self.order[0]

    def fetch_note(self, note_id, include_transcript=False):
        self.fetched.append((note_id, include_transcript))
        if self.on_fetch:
            self.on_fetch(note_id)
        note = dict(self.notes[note_id])
        note["transcript"] = list(_TRANSCRIPT) if include_transcript else []
        return note


def fake_synthesize(note, user_name=None, model=None, timeout=None,
                    raise_cap=False, **_kw):
    synth = SYNTHESES.get(note.get("id"))
    if synth is None:
        meeting_synthesis.LAST_FAILURE = "the notetaker returned no transcript"
        return None
    meeting_synthesis.LAST_FAILURE = ""
    return dict(synth)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FROZEN.replace(tzinfo=timezone.utc).astimezone(tz)
        return _FROZEN


def scaffold_vault(vault: Path) -> None:
    """A minimal vault. Idempotent: a vault that exists is left as it is, so
    a second `prepared()` over the same tree sees what the first run wrote."""
    wiki = vault / "wiki"
    if wiki.exists():
        return
    for sub in ("sources", "entities", "concepts", "projects", "weekly"):
        (wiki / sub).mkdir(parents=True, exist_ok=True)
    (wiki / "entities" / "Dana Vu.md").write_text(
        "---\ntype: entity\ntitle: Dana Vu\ncreated: 2026-09-01\n"
        "updated: 2026-09-20\nsources: []\nbusiness: ACME\ntags: [ACME, person]\n"
        "---\n\n# Dana Vu\n\nCounsel-side contact on the Harbor parcel.\n",
        encoding="utf-8")
    (wiki / "hotcache.md").write_text(
        "# Hot Cache\n\n## Test's Action Items\n\n- [ ] existing item\n\n"
        "## Active Threads\n\n### Harbor: site walk\n"
        "<!-- deal: stage=active last_contact=2026-09-20 -->\n"
        "- [[Dana Vu]] dana@grid.com\n", encoding="utf-8")
    (wiki / "index.md").write_text("# Index\n\n## Sources\n\n", encoding="utf-8")
    (wiki / "log.md").write_text("# Log\n\n", encoding="utf-8")


def rebase_module(module, vault: Path, monkeypatch) -> None:
    """Point every vault-relative Path the module holds at `vault`.

    Through monkeypatch, never a bare setattr: meeting_ingest is imported
    once per session and shared with warm_start's tests, and a path left
    pointing at this fixture's vault made those see entity pages that were
    never theirs.
    """
    old_root = module.WIKI
    for name, value in list(vars(module).items()):
        if isinstance(value, Path) and name.isupper():
            try:
                rel = value.relative_to(old_root)
            except ValueError:
                continue
            monkeypatch.setattr(module, name, vault / rel)
    monkeypatch.setattr(module, "WIKI", vault)


def nightly_args(limit=3):
    return types.SimpleNamespace(
        meeting="uningested", apply=True, dry_run=False, auto=True,
        stub_attendees=True, limit=limit, business=None, type=None,
        force=False, link_deals=False, no_synthesis=False, source=None,
        backfill=False, prefetch=False, staged_dir=None, staged=None)


def snapshot(vault: Path, stdout: str = "") -> dict:
    files = {}
    for path in sorted(vault.rglob("*")):
        if path.is_file():
            files[path.relative_to(vault).as_posix()] = path.read_text(encoding="utf-8")
    norm = stdout.replace(str(vault), "<VAULT>").replace("\\", "/")
    return {"files": files, "stdout": norm}


@contextlib.contextmanager
def prepared(module, vault: Path, monkeypatch, provider=None):
    """The module rebased onto `vault`, its clock frozen, its model stubbed."""
    scaffold_vault(vault)
    provider = provider or FakeProvider()
    rebase_module(module, vault, monkeypatch)
    # A real config carries a personal bucket, which is what --auto files a
    # meeting under when no business can be inferred; the test fixture's
    # config has only the two businesses.
    monkeypatch.setattr(module, "VALID_BUSINESS",
                        set(module.VALID_BUSINESS) | {"personal"})
    monkeypatch.setattr(module, "_provider", lambda: provider)
    monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr(meeting_synthesis, "synthesize", fake_synthesize)
    yield provider


def run_nightly(module, vault: Path, monkeypatch, limit=3) -> dict:
    """The scheduled path: --meeting uningested --apply --auto --stub-attendees."""
    with prepared(module, vault, monkeypatch):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            module.run_batch(nightly_args(limit))
    return snapshot(vault, buf.getvalue())


def load_module(path, name="meeting_ingest_rev"):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_golden(module_path, out_path):
    """One-off: run the fixture through the module at `module_path`."""
    import tempfile

    import pytest  # noqa: F401  (MonkeyPatch lives in pytest)
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    try:
        module = load_module(module_path, "meeting_ingest_golden_source")
        vault = Path(tempfile.mkdtemp(prefix="van-gogh-golden-"))
        snap = run_nightly(module, vault, mp)
    finally:
        mp.undo()
    # ensure_ascii: the base revision's stdout carries an em dash, and the
    # golden is compared DECODED, so escaping it keeps the file free of the
    # literal character without changing what the test compares.
    Path(out_path).write_text(json.dumps(snap, indent=2, ensure_ascii=True) + "\n",
                              encoding="utf-8")
    return snap
