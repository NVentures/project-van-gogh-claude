"""van-gogh/ path resolution and the wiki/ artifacts that must NOT move.

conftest primes config_loader._config = FIXTURE (a per-session temp vault) at
collection time, so these resolve with NO config.json and NO vault pointer
present — the path surface derives from cfg(), never the pointer file.
"""
import tempfile
from pathlib import Path

import config_loader as cl

from conftest import FIXTURE

VAULT = Path(FIXTURE["obsidian"]["vault_path"])
VG = VAULT / "van-gogh"


def test_van_gogh_root():
    assert cl.van_gogh_root() == VG


def test_state_paths_under_van_gogh():
    assert cl.workspace_week_md_path() == VG / "week.md"
    assert cl.morning_coffee_md_path() == VG / "morning-coffee.md"
    assert cl.afternoon_tea_md_path() == VG / "afternoon-tea.md"
    assert cl.logs_dir() == VG / "logs"
    assert cl.full_sidecar_path() == VG / "logs" / "week_review_latest.full.json"
    assert cl.projects_dir() == VG / "projects"
    assert cl.vangogh_memory_path() == VG / "projects" / "Project Van Gogh" / "memory.md"
    assert cl.ingest_state_path() == VG / ".ingest_state.json"


def test_done_log_still_in_wiki_weekly():
    # done-{year}.md tracks completed deals in the user's weekly notes — wiki/,
    # not van-gogh/.
    p = cl.done_log_path()
    assert p.parent == VAULT / "wiki/weekly"
    assert p.name.startswith("done-")


def test_wiki_artifacts_did_not_move():
    """Living Obsidian notes stay under wiki/ — guard against accidental moves."""
    assert cl.hotcache_path() == VAULT / "wiki/hotcache.md"
    assert cl.sources_dir() == VAULT / "wiki/sources"
    assert cl.weekly_dir() == VAULT / "wiki/weekly"
    assert cl.entities_dir() == VAULT / "wiki/entities"
    assert cl.voice_guide_path() == VAULT / "wiki/sources/voice-guide.md"
    assert cl.tone_profile_path() == VAULT / "wiki/sources/tone-profile.md"
    assert cl.relationship_radar_path() == Path(
        VAULT / "wiki/sources/relationship-radar.md"
    )
    # None of these may have leaked under van-gogh/.
    for p in (
        cl.hotcache_path(), cl.sources_dir(), cl.weekly_dir(),
        cl.entities_dir(), cl.voice_guide_path(),
    ):
        assert "van-gogh" not in p.parts


def test_resolved_meta_state_paths_are_vault_rooted():
    meta = cl.resolved_meta()
    assert meta["van_gogh_root"] == str(VG)
    assert meta["workspace_week_md"] == str(VG / "week.md")
    assert meta["morning_coffee_md"] == str(VG / "morning-coffee.md")
    assert meta["afternoon_tea_md"] == str(VG / "afternoon-tea.md")
    assert meta["logs_dir"] == str(VG / "logs")
    assert meta["projects_dir"] == str(VG / "projects")
    assert meta["ingest_state_path"] == str(VG / ".ingest_state.json")


def test_ensure_van_gogh_root_creates_dir(tmp_path, monkeypatch):
    """ensure_van_gogh_root() mkdirs the folder so writers never hit ENOENT."""
    monkeypatch.setitem(cl.cfg()["obsidian"], "vault_path", str(tmp_path))
    original = cl.cfg()["obsidian"]["vault_path"]
    try:
        created = cl.ensure_van_gogh_root()
        assert created == tmp_path / "van-gogh"
        assert created.is_dir()
    finally:
        # Put back what was there. Restoring a LITERAL is how a shared path
        # creeps back in after the fixture has moved on.
        cl.cfg()["obsidian"]["vault_path"] = original


# ── the fixture vault is this session's alone ────────────────────────────────

def test_the_fixture_vault_is_not_a_shared_path():
    """It was a hardcoded "/tmp/vault": one fixed directory, world-writable,
    shared by every run on the machine and by every user of it.

    Tests that walk the vault (context_pack iterates sources_dir() and reads
    entities_dir()) therefore saw whatever a previous run, a parallel run, or
    another user had left behind, which is how a suite fails once and then
    passes twelve times in a row. The state dir already used mkdtemp; the
    vault is the one that did not.
    """
    from conftest import FIXTURE
    root = FIXTURE["obsidian"]["vault_path"]
    assert root != "/tmp/vault"
    assert "van-gogh-test-vault-" in root, root
    # It must be under the OS temp dir, not a path anyone could guess.
    assert Path(root).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_no_test_hardcodes_a_shared_vault_path():
    """One file getting this right is not the property; every file is."""
    here = Path(__file__).resolve().parent
    me = Path(__file__).resolve()
    bad = "/tmp/" + "vault"          # split so this file does not match itself
    offenders = []
    for f in sorted(here.glob("test_*.py")) + [here / "conftest.py"]:
        if f.resolve() == me:
            continue                 # the file stating the rule names the path
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if bad in line and not line.lstrip().startswith("#"):
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert not offenders, "hardcoded shared vault path:\n  " + "\n  ".join(offenders)
