"""Every host-dependent function answers for every host.

`host._DISPATCH` is the one table of client differences. This test holds
every row to exactly `host.HOSTS`, so a function that gains a Claude branch
and no Codex branch (or a third host added to HOSTS with no rows for it) is
a red test before anything calls it. It then calls every public function
that takes a host under each host with a stubbed binary, so a branch that
exists but raises is caught here too.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import host  # noqa: E402


def test_every_row_has_every_host():
    assert host.HOSTS == ("claude", "codex")
    bad = {name: sorted(table) for name, table in host._DISPATCH.items()
           if set(table) != set(host.HOSTS)}
    assert not bad, f"rows missing a host: {bad}"
    for name, table in host._DISPATCH.items():
        for h, fn in table.items():
            assert callable(fn), (name, h)


def test_every_branch_is_a_distinct_function():
    """A row whose two branches are the same object is not a branch."""
    same = [name for name, t in host._DISPATCH.items() if t["claude"] is t["codex"]]
    assert not same, same


@pytest.mark.parametrize("h", host.HOSTS)
def test_public_builders_answer_under_each_host(h, tmp_path):
    binary = f"/bin/{h}"
    assert host.cmd("week", h).endswith("van-gogh:week")
    assert host.host_word(h) and host.client_name(h)
    argv = host.skill_cmd("week", "--force", binary=binary, host=h)
    assert argv[0] == binary and any("van-gogh:week --force" in a for a in argv)
    argv = host.completion_cmd("claude-haiku-4-5-20251001", binary=binary, host=h)
    assert argv[0] == binary
    argv = host.readonly_cmd(agent="vincent", plugin_dir=host._PLUGIN_ROOT,
                             session_id="s", binary=binary, host=h)
    assert argv[0] == binary
    argv = host.readonly_cmd(resume="t", binary=binary, host=h)
    assert argv[0] == binary and "t" in argv
    argv, stdin = host.agent_cmd("prompt", "vincent", host._PLUGIN_ROOT, [tmp_path],
                                 binary=binary, host=h)
    assert argv[0] == binary and str(tmp_path) in argv
    assert (stdin is None) == (h == "claude")
    assert host.update_argv(binary, h) == [binary, "update"]
    assert host.plugin_list_argv(binary, h)[1:] == ["plugin", "list", "--json"]
    two = host.plugin_update_argvs(binary, h)
    assert len(two) == 2 and all(a[0] == binary for a in two)
    assert host.plugin_remove_argv(binary, h)[-1] == host.PLUGIN_ID
    assert host.personal_skills_dir(h).name == "skills"
    assert host.instructions_filename(h).endswith(".md")
    assert host.plugin_listed("", h) is None
    outcome, _ = host.plugin_update_outcome({}, "0.1.0", h)
    assert outcome in ("ok", "unchanged", "error")


def test_every_dispatching_public_function_takes_host():
    """A public function that reads the table must let a caller name the host,
    which is what the both-hosts fixture and the scheduler rely on."""
    src = inspect.getsource(host)
    for name in host._DISPATCH:
        fn = getattr(host, {"host_word": "host_word", "client_name": "client_name"}.get(name, name), None)
        assert fn is not None, name
        assert "host" in inspect.signature(fn).parameters, name
