"""user_state under both clients: the vault's instructions file and the
Codex config block self-heal."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host as hostmod  # noqa: E402
import user_state  # noqa: E402


def _vault(tmp_path, with_claude=True, with_agents=False):
    v = tmp_path / "vault"; (v / "van-gogh").mkdir(parents=True)
    if with_claude:
        (v / "CLAUDE.md").write_text("# mine\n\nKeep claude.\n", encoding="utf-8")
    if with_agents:
        (v / "AGENTS.md").write_text("# My own notes\n\nKeep this.\n", encoding="utf-8")
    return v


def test_codex_syncs_agents_md_and_leaves_claude_md_alone(host, tmp_path):
    v = _vault(tmp_path, with_claude=True, with_agents=True)
    before_claude = hashlib.sha256((v / "CLAUDE.md").read_bytes()).hexdigest()
    before_agents = hashlib.sha256((v / "AGENTS.md").read_bytes()).hexdigest()
    assert user_state.sync_claude_block_into(v) is True
    target = v / hostmod.instructions_filename(host)
    text = target.read_text(encoding="utf-8")
    assert user_state.CLAUDE_BLOCK_BEGIN in text and "Keep" in text
    assert len(text.encode("utf-8")) < 32768
    if host == "codex":
        assert "$van-gogh:" in text and "/van-gogh:" not in text
        assert hashlib.sha256((v / "CLAUDE.md").read_bytes()).hexdigest() == before_claude
    else:
        assert "/van-gogh:" in text and "$van-gogh:" not in text
        assert hashlib.sha256((v / "AGENTS.md").read_bytes()).hexdigest() == before_agents
    assert user_state.sync_claude_block_into(v) is False
    assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(target.read_bytes()).hexdigest()


def test_no_instructions_file_means_no_write(host, tmp_path):
    v = _vault(tmp_path, with_claude=(host == "codex"), with_agents=(host == "claude"))
    assert user_state.sync_claude_block_into(v) is False
    assert not (v / hostmod.instructions_filename(host)).exists()


def test_codex_block_sync_uses_the_pointer_roots(host, monkeypatch, tmp_path):
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(state))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex")); (tmp_path / "codex").mkdir()
    (state / "vault-pointer").write_text(str(tmp_path / "vault") + "\n")
    user_state.sync_codex_block(tmp_path / "cache" / "0.9.9")
    cfg = tmp_path / "codex" / "config.toml"
    if host == "codex":
        text = cfg.read_text()
        assert str(tmp_path / "cache" / "0.9.9") in text
        assert str(tmp_path / "vault") in text and str(state) in text
    else:
        assert not cfg.exists()
