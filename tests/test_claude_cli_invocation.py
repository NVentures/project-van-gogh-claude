"""Guard the cross-platform `claude` CLI invocation.

Classification/completion scripts must launch the CLI through
`claude_cli.run_claude()`:
  - a bare `["claude", ...]` subprocess arg list raises FileNotFoundError on
    Windows (the launcher is `claude.CMD`; CreateProcess does not apply PATHEXT);
  - a raw `[claude_bin(), "-p", prompt, ...]` call puts the prompt in argv
    (truncated at the first newline on Windows), omits encoding="utf-8"
    (cp1252 UnicodeEncodeError on smart quotes/em-dashes), and runs agentic —
    a classification prompt can auto-trigger a skill and fork-bomb.

digest_send.py / scheduler_setup.py are exempt: they deliberately run the
AGENTIC `claude -p "/van-gogh:<briefing>"` slash-command path.

connector_fetch.py is exempt for the opposite reason: it must NOT carry
run_claude's flags. `--strict-mcp-config` would disable the very connector it
exists to read, and it needs `--output-format stream-json` so the answer can be
taken from the tool_result events rather than the model's prose. It confines
the session more tightly than run_claude does, not less, and
tests/test_connector_fetch.py asserts every one of those flags.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP = _REPO_ROOT / "app"

# The agentic slash-command renderers — the only sanctioned raw `claude -p` users.
_AGENTIC_EXEMPT = {"claude_cli.py", "digest_send.py", "scheduler_setup.py",
                   "connector_fetch.py"}

# Matches a subprocess arg list that starts with a hardcoded "claude" literal,
# e.g. ["claude", "-p", ...]. DOTALL so a line-wrapped list is still caught.
_BARE_CLAUDE_RE = re.compile(r"""\[\s*["']claude["']\s*,""", re.DOTALL)

# Matches a raw arg list invoking the CLI in print mode via any resolver, e.g.
# [claude_bin(), "-p", ...] or [claude_exe(), "-p", ...]. Every such call must
# go through run_claude()/claude_cmd() so the non-agentic flags and stdin
# prompt delivery apply. DOTALL so a line-wrapped list can't evade the guard.
_RAW_CLAUDE_P_RE = re.compile(r"""\[\s*claude_\w+\(\)\s*,\s*["']-p["']""", re.DOTALL)


def _offenders(pattern, exempt=()):
    # Search the whole file (DOTALL) so a call list broken across lines
    # (`[\n    claude_bin(),\n    "-p", ...]`) can't evade the guard.
    found = []
    for path in _APP.glob("*.py"):
        if path.name in exempt:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            found.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    return found


def test_no_bare_claude_literal_in_subprocess():
    offenders = _offenders(_BARE_CLAUDE_RE)
    assert not offenders, (
        "Bare [\"claude\", ...] subprocess invocation found — use claude_bin() "
        "so the launcher resolves on Windows:\n" + "\n".join(offenders)
    )


def test_claude_invocations_go_through_run_claude():
    offenders = _offenders(_RAW_CLAUDE_P_RE, exempt=_AGENTIC_EXEMPT)
    assert not offenders, (
        'Raw [claude_bin(), "-p", ...] invocation found — use '
        "claude_cli.run_claude() so the prompt travels on stdin, encoding is "
        'utf-8, and --tools "" disables agentic tools:\n' + "\n".join(offenders)
    )


def test_claude_cmd_is_non_agentic_and_promptless(monkeypatch):
    import claude_cli

    # claude_bin() raises when the CLI is absent (CI); resolution is not under test.
    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    cmd = claude_cli.claude_cmd("claude-haiku-4-5-20251001")
    assert "--model" in cmd and "claude-haiku-4-5-20251001" in cmd
    assert "--tools" in cmd, "claude_cmd must pass --tools to disable agentic tools"
    assert cmd[cmd.index("--tools") + 1] == "", (
        "--tools must be followed by an empty string to disable ALL tools"
    )
    # Skill auto-invocation is NOT gated by --tools; without this flag a
    # classification prompt re-triggers the skill and fork-bombs.
    assert "--disable-slash-commands" in cmd, (
        "claude_cmd must pass --disable-slash-commands to stop skill recursion"
    )
    # MCP connectors must be off, or the model treats a classification prompt as
    # a project and asks for Gmail access instead of returning JSON.
    assert "--strict-mcp-config" in cmd, (
        "claude_cmd must pass --strict-mcp-config to disable MCP connectors"
    )
    # The prompt must NOT be an argv argument: a multi-line prompt passed via
    # `-p <prompt>` is truncated at the first newline on the Windows subprocess
    # path, so the child sees only the first line. It is delivered on STDIN by
    # run_claude. Guard that -p is immediately followed by a flag, not a prompt.
    assert "-p" in cmd, "claude_cmd must pass -p for headless print mode"
    assert cmd[cmd.index("-p") + 1].startswith("--"), (
        "claude_cmd must not place the prompt in argv; run_claude sends it on stdin"
    )


class _Result:
    returncode = 0
    stdout = "[]"
    stderr = ""


def test_run_claude_sends_prompt_on_stdin(monkeypatch):
    """run_claude must deliver the prompt via stdin (input=), never as argv."""
    import claude_cli

    captured = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", _fake_run)
    multiline = "line one\nline two\nline three"
    claude_cli.run_claude(multiline, "claude-haiku-4-5-20251001", timeout=30)

    assert captured["kwargs"].get("input") == multiline, (
        "prompt must be passed via input= (stdin), not as an argv argument"
    )
    assert multiline not in captured["argv"], (
        "prompt must never appear in the argv (it gets truncated at newlines)"
    )
    # Without encoding="utf-8", subprocess encodes the stdin write with the OS
    # locale codec (cp1252 on Windows), which raises UnicodeEncodeError on smart
    # quotes / em-dashes / zero-width spaces from real email, crashing the
    # writer thread and silently dropping that batch.
    assert captured["kwargs"].get("encoding") == "utf-8", (
        "run_claude must pass encoding='utf-8' so non-cp1252 chars in the prompt "
        "do not crash the stdin writer thread on Windows"
    )
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"].get("env", {}), (
        "run_claude must drop ANTHROPIC_API_KEY to force subscription routing"
    )


def test_run_claude_handles_non_cp1252_prompt(monkeypatch):
    """A prompt with chars cp1252 cannot encode must not raise on the way in."""
    import claude_cli

    seen = {}

    def _fake_run(argv, **kwargs):
        # Prove the prompt round-trips through a utf-8 encode without error,
        # the exact operation subprocess performs on the stdin write.
        seen["encoded"] = kwargs["input"].encode(kwargs["encoding"])
        return _Result()

    monkeypatch.setattr(claude_cli, "claude_bin", lambda: "claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", _fake_run)
    # zero-width space, smart quote, em-dash, emoji — all absent from cp1252.
    tricky = "subject: I’ve ​seen — the deal \U0001f600"
    claude_cli.run_claude(tricky, "claude-haiku-4-5-20251001")
    assert seen["encoded"], "prompt must encode cleanly under run_claude's codec"


def test_the_connector_fetch_exemption_is_not_a_hole(monkeypatch):
    """Exempt from run_claude's flags, and held to a stricter set of its own.

    The exemption exists because --strict-mcp-config would disable the
    connector this module is built to read. That reason does not license the
    other loosenings, so the flags it must still carry are asserted here as
    well as in its own suite: a future edit that drops the tool whitelist or
    turns on bypassPermissions would otherwise be caught by nothing.
    """
    import connector_fetch

    # The resolver is stubbed as well as the call: _flags() builds the argv by
    # calling claude_bin(), which raises on a machine with no Claude Code
    # installed, which is the CI condition. This is the environment assumption
    # recorded in CLAUDE.md, and it costs a red CI run on a green local suite.
    monkeypatch.setattr(connector_fetch, "claude_bin", lambda: "claude")
    # Read the argv the builder actually produces, not the source text. The
    # module's own docstring says "No --permission-mode", and a scan of the
    # whole file for that string fails on the sentence that forbids it.
    argv = connector_fetch._flags("some_server", ("some_tool",), "some-model")

    assert argv[argv.index("--tools") + 1] == "", "built-in tools must be off"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--disable-slash-commands" in argv
    assert "--permission-mode" not in argv, \
        "a fetch reads reports; it never needs write permissions"
    assert "--dangerously-skip-permissions" not in argv
    assert "--allow-dangerously-skip-permissions" not in argv
    allowed = argv[argv.index("--allowedTools") + 1]
    assert allowed == "mcp__some_server__some_tool" and "*" not in allowed, \
        "tools must arrive as explicit names, never a wildcard"
