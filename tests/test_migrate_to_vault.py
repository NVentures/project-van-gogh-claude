"""app/migrate_to_vault.py — move repo-root state into {vault}/van-gogh/.

Drives migrate() against fake repo + vault dirs under tmp_path so the real
checkout and the user's real vault are never touched.
"""
import json

import migrate_to_vault as m


def _seed_repo(repo):
    """Create a representative set of repo-root state to migrate."""
    (repo / "config.json").write_text(json.dumps({"user": {"first_name": "X"}}), encoding="utf-8")
    (repo / "week.md").write_text("# week", encoding="utf-8")
    (repo / "morning-coffee.md").write_text("# coffee", encoding="utf-8")
    (repo / "afternoon-tea.md").write_text("# tea", encoding="utf-8")
    (repo / ".ingest_state.json").write_text('{"last_ingest": "2026-01-01"}', encoding="utf-8")
    logs = repo / "logs"
    logs.mkdir()
    (logs / "week_review_latest.json").write_text("{}", encoding="utf-8")
    projects = repo / "projects" / "Project Van Gogh"
    projects.mkdir(parents=True)
    (projects / "memory.md").write_text("# memory", encoding="utf-8")


def test_moves_files_and_dirs_then_deletes_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_repo(repo)

    m.migrate(vault, repo_root=repo)

    vg = vault / "van-gogh"
    # Files landed in the vault...
    assert (vg / "config.json").is_file()
    assert (vg / "week.md").read_text(encoding="utf-8") == "# week"
    assert (vg / "logs" / "week_review_latest.json").is_file()
    assert (vg / "projects" / "Project Van Gogh" / "memory.md").read_text(
        encoding="utf-8"
    ) == "# memory"
    assert (vg / ".ingest_state.json").is_file()

    # ...and the repo-root originals are gone (single source of truth).
    for name in ("config.json", "week.md", "morning-coffee.md",
                 "afternoon-tea.md", ".ingest_state.json", "logs", "projects"):
        assert not (repo / name).exists(), f"{name} should have been removed"


def test_writes_pointer_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))

    m.migrate(vault, repo_root=repo)

    pointer = tmp_path / "state" / "vault-pointer"
    assert pointer.is_file()
    assert pointer.read_text(encoding="utf-8").strip() == str(vault)


def test_idempotent_second_run_is_noop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_repo(repo)

    m.migrate(vault, repo_root=repo)
    vg = vault / "van-gogh"
    first = (vg / "config.json").read_text(encoding="utf-8")

    # Second run must not raise and must not clobber the migrated copy.
    summary = m.migrate(vault, repo_root=repo)
    assert (vg / "config.json").read_text(encoding="utf-8") == first
    assert any("already in vault" in line for line in summary)


def test_idempotent_removes_stale_repo_copy(tmp_path):
    """If a repo-root copy reappears after migration, the second run drops it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_repo(repo)

    m.migrate(vault, repo_root=repo)
    # Simulate a stray repo-root config.json reappearing.
    (repo / "config.json").write_text("stale", encoding="utf-8")

    m.migrate(vault, repo_root=repo)
    assert not (repo / "config.json").exists()
    # The vault copy (the real one) is preserved, not overwritten by "stale".
    assert (vault / "van-gogh" / "config.json").read_text(encoding="utf-8") != "stale"


def test_tolerates_missing_sources(tmp_path, monkeypatch):
    """A fresh install has nothing to move — must still create the tree + pointer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path / "state"))

    summary = m.migrate(vault, repo_root=repo)

    assert (vault / "van-gogh").is_dir()
    assert (tmp_path / "state" / "vault-pointer").is_file()
    assert any("nothing to do" in line for line in summary)


def test_seeds_memory_from_template_when_absent(tmp_path):
    """Fresh install: nothing to move, but memory.md is seeded from the template."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "memory.template.md").write_text("# template memory", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()

    summary = m.migrate(vault, repo_root=repo)

    seeded = vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md"
    assert seeded.read_text(encoding="utf-8") == "# template memory"
    assert any("seeded" in line for line in summary)


def test_does_not_overwrite_migrated_memory(tmp_path):
    """A real memory.md moved from the repo must not be clobbered by the template."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "memory.template.md").write_text("# template memory", encoding="utf-8")
    _seed_repo(repo)  # includes projects/Project Van Gogh/memory.md = "# memory"
    vault = tmp_path / "vault"
    vault.mkdir()

    m.migrate(vault, repo_root=repo)

    seeded = vault / "van-gogh" / "projects" / "Project Van Gogh" / "memory.md"
    assert seeded.read_text(encoding="utf-8") == "# memory"  # the real one, not the template


def test_expanduser_on_vault_arg(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows expanduser
    m.migrate("~/myvault", repo_root=repo)
    assert (tmp_path / "myvault" / "van-gogh").is_dir()
