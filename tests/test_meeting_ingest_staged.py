"""meeting_ingest: the nightly path is unchanged (P19), the two-phase path
files the same vault (P2), and a usage cap during the prefetch defers the
rest with a count (P12).

tests/snapshots/nightly_ingest_golden.json was produced by running the
three-meeting fixture in tests/ingest_fixture.py through the pinned base
commit's module (`git show 85b11d5:project-van-gogh/app/meeting_ingest.py`)
with the same frozen clock and the same stubbed model. The first test runs
the CURRENT module over the same fixture and compares every vault file and
the normalized stdout byte for byte. It is a literal snapshot on purpose:
changing it means re-pinning against the base commit (see the fixture
module's docstring), never regenerating from the code under test.
"""
import json
import sys
import types
from pathlib import Path

import pytest

import claude_cli
import ingest_fixture as fx
import item_ids
import meeting_ingest as gi
import meeting_synthesis

GOLDEN = Path(__file__).parent / "snapshots" / "nightly_ingest_golden.json"
CAP_TEXT = (Path(__file__).parent / "fixtures" / "outage"
            / "2026-09-09-claude-usage-cap.txt").read_text(encoding="utf-8").strip()


def _golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setattr(claude_cli, "_spawn",
                        lambda *a, **k: pytest.fail("a model was called"))
    monkeypatch.setattr(fx.meeting_synthesis, "LAST_FAILURE", "")
    gi._STAGED.clear()
    monkeypatch.delenv("VAN_GOGH_SERIAL", raising=False)


# ── P19: the scheduled path is byte-identical to the base commit ─────────────
#
# Identical apart from one deliberate addition: every to-do now carries its id
# marker (tests/test_meeting_ingest_stamps.py owns what that marker says).
# Removing the marker must give back the base commit's files exactly, which
# is the same promise as before for everything the marker is not.

def _without_ids(files):
    return {name: item_ids.strip(text) for name, text in files.items()}


def test_the_nightly_path_matches_the_pinned_base_commit(tmp_path, monkeypatch):
    golden = _golden()
    assert len(golden["files"]) >= 8 and "note_a1" in golden["stdout"], \
        "the golden must hold a real run, not an empty vault"
    now = fx.run_nightly(gi, tmp_path / "vault", monkeypatch)
    assert _without_ids(now["files"]) == golden["files"]
    assert now["files"] != golden["files"], "the to-dos carry their ids"
    assert now["stdout"] == golden["stdout"]


def test_the_golden_was_made_from_three_filed_meetings():
    golden = _golden()
    sources = [k for k in golden["files"] if k.startswith("wiki/sources/") and "catalog" not in k]
    assert len(sources) == 3
    assert "[auto] Done. 3 ingested, 0 skipped." in golden["stdout"]


# ── P2: prefetch then staged filing leaves the same vault ────────────────────

def _prefetch_then_file(vault, staged, monkeypatch, provider=None, limit=3):
    with fx.prepared(gi, vault, monkeypatch, provider=provider) as prov:
        manifest = gi.run_prefetch(limit, staged)
        args = fx.nightly_args(limit)
        args.staged = str(staged)
        result = gi.run_staged(args, staged)
    return manifest, result, prov


def test_prefetch_then_staged_filing_produces_the_golden_vault(tmp_path, monkeypatch, capsys):
    vault, staged = tmp_path / "vault", tmp_path / "staged"
    manifest, result, prov = _prefetch_then_file(vault, staged, monkeypatch)
    capsys.readouterr()
    assert [r["id"] for r in manifest["filed"]] == ["note_a1", "note_b2", "note_c3"]
    assert manifest["failed"] == [] and manifest["deferred"] == []
    assert manifest["degraded"] is None
    assert result["filed"] == ["Harbor Energy site walk", "Acme widget pricing call",
                               "Weekly internal sync"]
    # The transcript was fetched once per meeting, at prefetch, never at filing.
    assert prov.fetched == [("note_a1", True), ("note_b2", True), ("note_c3", True)]
    assert _without_ids(fx.snapshot(vault)["files"]) == _golden()["files"]


def test_the_staged_file_never_holds_the_transcript(tmp_path, monkeypatch, capsys):
    vault, staged = tmp_path / "vault", tmp_path / "staged"
    with fx.prepared(gi, vault, monkeypatch):
        gi.run_prefetch(3, staged)
    capsys.readouterr()
    for path in staged.glob("note_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "transcript" not in data["note"]
        assert '"transcript"' not in path.read_text(encoding="utf-8"), \
            "no transcript key anywhere in the staged file"
    assert json.loads((staged / "note_c3.json").read_text(encoding="utf-8"))[
        "synthesis_failure"] == "the notetaker returned no transcript"


def test_staged_filing_skips_what_is_already_on_disk(tmp_path, monkeypatch, capsys):
    vault, staged = tmp_path / "vault", tmp_path / "staged"
    _prefetch_then_file(vault, staged, monkeypatch)
    capsys.readouterr()
    before = fx.snapshot(vault)["files"]
    with fx.prepared(gi, vault, monkeypatch):
        args = fx.nightly_args(3)
        result = gi.run_staged(args, staged)
    out = capsys.readouterr().out
    assert result["filed"] == [] and len(result["skipped"]) == 3
    assert out.count("already exists, skipping") == 3
    assert fx.snapshot(vault)["files"] == before


def test_staged_filing_runs_in_manifest_order_not_directory_order(tmp_path, monkeypatch, capsys):
    """The provider lists newest first and the nightly run files in that
    order; a directory listing would be alphabetical."""
    vault, staged = tmp_path / "vault", tmp_path / "staged"
    notes = list(reversed(fx.NOTES))  # oldest first in the provider this time
    _prefetch_then_file(vault, staged, monkeypatch, provider=fx.FakeProvider(notes))
    out = capsys.readouterr().out
    order = [line.split("(")[-1].rstrip(")") for line in out.splitlines()
             if line.startswith("── ")]
    assert order[-3:] == ["note_c3", "note_b2", "note_a1"]


# ── P12: a usage cap during the summaries ────────────────────────────────────

def _capping_synthesize(cap_on):
    def synthesize(note, user_name=None, model=None, timeout=None, raise_cap=False, **_kw):
        if note.get("id") in cap_on:
            raise claude_cli.UsageCap(CAP_TEXT)
        return fx.fake_synthesize(note, raise_cap=raise_cap)
    return synthesize


def test_a_cap_files_what_finished_and_defers_the_rest_with_a_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAN_GOGH_SERIAL", "1")  # deterministic order: a1, b2, c3
    vault, staged = tmp_path / "vault", tmp_path / "staged"
    with fx.prepared(gi, vault, monkeypatch):
        monkeypatch.setattr(meeting_synthesis, "synthesize", _capping_synthesize({"note_b2"}))
        manifest = gi.run_prefetch(3, staged)
        args = fx.nightly_args(3)
        result = gi.run_staged(args, staged)
    capsys.readouterr()
    assert [r["id"] for r in manifest["filed"]] == ["note_a1"]
    assert [r["id"] for r in manifest["deferred"]] == ["note_b2", "note_c3"]
    assert manifest["failed"] == []
    assert manifest["degraded"] == {"reason": "usage_cap", "deferred": 2}
    assert json.loads((staged / "manifest.json").read_text(encoding="utf-8"))["degraded"] \
        == {"reason": "usage_cap", "deferred": 2}
    assert result["filed"] == ["Harbor Energy site walk"]
    pages = sorted(p.name for p in (vault / "wiki" / "sources").glob("*.md")
                   if "catalog" not in p.name)
    assert pages == ["Harbor Energy site walk - 2026-09-30.md"]


def test_the_nightly_path_still_falls_back_on_a_cap(tmp_path, monkeypatch, capsys):
    """Unchanged behaviour: the scheduled run files the provider summary and
    says so, exactly as it did before UsageCap existed."""
    vault = tmp_path / "vault"
    real_synthesize = meeting_synthesis.synthesize
    with fx.prepared(gi, vault, monkeypatch):
        monkeypatch.setattr(claude_cli, "run_claude",
                            lambda *a, **k: (_ for _ in ()).throw(claude_cli.UsageCap(CAP_TEXT)))
        monkeypatch.setattr(meeting_synthesis, "synthesize", real_synthesize)
        gi.run_batch(fx.nightly_args(3))
    out = capsys.readouterr().out
    assert "[auto] Done. 3 ingested, 0 skipped." in out
    assert out.count("Filed the notetaker's summary, not a transcript synthesis: "
                     "the model call failed (UsageCap) (tried 2 times)") == 3


def test_synthesize_raises_the_cap_only_when_asked(monkeypatch):
    monkeypatch.setattr(claude_cli, "run_claude",
                        lambda *a, **k: (_ for _ in ()).throw(claude_cli.UsageCap(CAP_TEXT)))
    note = {"id": "x", "title": "t", "transcript": [
        {"speaker": {"source": "microphone"}, "text": "word " * 200}]}
    assert meeting_synthesis.synthesize(note, user_name="Test") is None
    assert meeting_synthesis.LAST_FAILURE.startswith("the model call failed (UsageCap)")
    with pytest.raises(claude_cli.UsageCap):
        meeting_synthesis.synthesize(note, user_name="Test", raise_cap=True)


# ── The CLI surface ──────────────────────────────────────────────────────────

def test_prefetch_needs_a_staged_dir_and_staged_needs_apply(monkeypatch):
    monkeypatch.setattr("sys.argv", ["meeting_ingest.py", "--prefetch"])
    with pytest.raises(SystemExit) as e:
        gi.main()
    assert e.value.code == 2
    monkeypatch.setattr("sys.argv", ["meeting_ingest.py", "--staged", "/nowhere"])
    with pytest.raises(SystemExit) as e:
        gi.main()
    assert e.value.code == 2


def test_staged_without_a_manifest_exits_not_found(tmp_path, monkeypatch, capsys):
    args = types.SimpleNamespace(**vars(fx.nightly_args(3)))
    with pytest.raises(SystemExit) as e:
        gi.run_staged(args, tmp_path / "empty")
    assert e.value.code == gi.EXIT_NOT_FOUND
    assert "no readable manifest" in capsys.readouterr().err


def test_the_staged_cli_stubs_new_attendees_like_the_nightly_path(tmp_path, monkeypatch):
    """Through the real entry point, not a hand-built args object.

    The nightly path passes --stub-attendees on its command line; the staged
    path is the same unattended batch and must set it itself, or the two arms
    link attendees differently (found live: 9 of 11 pages differed on the
    attendees line alone). The earlier tests built args by hand with the flag
    on, which is exactly how a missing flag in main() stays green.
    """
    seen = {}
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "manifest.json").write_text('{"filed": []}', encoding="utf-8")

    def spy(args, staged_dir):
        seen["auto"] = args.auto
        seen["stub"] = args.stub_attendees
        seen["dir"] = str(staged_dir)

    monkeypatch.setattr(gi, "run_staged", spy)
    monkeypatch.setattr(sys, "argv", ["meeting_ingest.py", "--staged", str(staged), "--apply"])
    gi.main()
    assert seen == {"auto": True, "stub": True, "dir": str(staged)}
