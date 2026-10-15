"""Every Codex flag and subcommand host.py uses exists in the installed binary.

A frozen fixture of a vendor's command line is a hypothesis about that
vendor. This test asks the binary itself, and it is the one test here that
is allowed to skip: on a machine with no Codex it says so. In the CI lane
that installs Codex, `VAN_GOGH_REQUIRE_CODEX=1` turns the skip into a
failure, so a renamed flag is red there before a client meets it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import host  # noqa: E402


def _binary() -> str:
    try:
        return host.bin("codex")
    except FileNotFoundError:
        if os.environ.get("VAN_GOGH_REQUIRE_CODEX") == "1":
            raise
        pytest.skip("no codex binary on this machine (set VAN_GOGH_REQUIRE_CODEX=1 to fail)")


def _help(*words) -> str:
    r = subprocess.run([_binary(), *words, "--help"], capture_output=True, text=True,
                       encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
    return (r.stdout or "") + (r.stderr or "")


def test_exec_flags_exist():
    text = _help("exec")
    missing = [f for f in host.CODEX_EXEC_FLAGS if f not in text]
    assert not missing, f"codex exec --help lacks {missing}"


def test_subcommands_exist():
    text = _help()
    missing = [s for s in host.CODEX_SUBCOMMANDS if f"\n  {s} " not in text]
    assert not missing, f"codex --help lacks {missing}"
    plugin = _help("plugin")
    missing = [s for s in host.CODEX_PLUGIN_SUBCOMMANDS if f"\n  {s} " not in plugin]
    assert not missing, f"codex plugin --help lacks {missing}"


def test_exec_resume_exists():
    assert "Usage: codex exec resume" in _help("exec", "resume")


def test_debug_prompt_input_exists():
    assert "prompt-input" in _help("debug")


def test_every_flag_host_builds_is_declared():
    """A flag added to a builder without adding it to CODEX_EXEC_FLAGS would
    dodge the check above. Build every Codex argv and compare."""
    built = set()
    for argv in (host.skill_cmd("x", binary="codex", host="codex"),
                 host.completion_cmd("claude-haiku-4-5-20251001", binary="codex",
                                     host="codex", schema_file="s.json"),
                 host.readonly_cmd(binary="codex", host="codex"),
                 host.readonly_cmd(resume="t", binary="codex", host="codex"),
                 host.agent_cmd("p", "vincent", host._PLUGIN_ROOT, ["/d"],
                                binary="codex", host="codex")[0]):
        built.update(a for a in argv if a.startswith("-"))
    undeclared = sorted(built - set(host.CODEX_EXEC_FLAGS))
    assert not undeclared, undeclared
