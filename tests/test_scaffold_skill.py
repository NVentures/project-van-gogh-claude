"""scaffold_skill.py writes a personal skill where the client reads it."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import host as hostmod  # noqa: E402


def _run(home: Path, env_host: str, name: str):
    env = {"HOME": str(home), "USERPROFILE": str(home), "VAN_GOGH_STATE_DIR": str(home / ".config" / "van-gogh"),
           hostmod.HOST_ENV: env_host, "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(ROOT / "app" / "scaffold_skill.py"), "--name", name,
                           "--kind", "prose", "--description", "A probe"],
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_codex_writes_under_agents_skills(host, tmp_path):
    (tmp_path / ".config" / "van-gogh").mkdir(parents=True)
    res = _run(tmp_path, host, "probe-one")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    expected = tmp_path / (".agents" if host == "codex" else ".claude") / "skills" / "probe-one"
    assert Path(out["path"]) == expected
    assert (expected / "SKILL.md").exists()
    other = tmp_path / (".claude" if host == "codex" else ".agents") / "skills"
    assert not other.exists()


def test_personal_skills_dir_is_the_seams(host):
    d = hostmod.personal_skills_dir(host)
    assert d.parts[-2:] == ((".agents" if host == "codex" else ".claude"), "skills")


def test_the_command_is_spelled_for_the_host(host, tmp_path):
    (tmp_path / ".config" / "van-gogh").mkdir(parents=True)
    res = _run(tmp_path, host, "probe-two")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["command"] == ("$probe-two" if host == "codex" else "/probe-two")
    assert hostmod.personal_cmd("x", host) == out["command"].replace("probe-two", "x")
