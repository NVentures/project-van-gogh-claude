"""The confined connector fetch: flags, verdicts, and what it refuses to trust.

This module spawns an agent that can read the user's financial records while
nobody is watching, so most of what is tested here is what it CANNOT do. The
flags are the guarantee, not the prompt, and a flag that quietly stops being
passed would be invisible in every other test in the suite.

The other half is the verdict. A session that answers without calling anything,
or calls a tool with wider arguments than it was given, produces a confident
paragraph and no data. Reading the answer out of the event stream rather than
the model's prose is what makes that a failure instead of a number.

Every fixture stream below is synthetic but shaped from real captured runs on
2026-09-10, including the detail that a successful tool_result carries no
`is_error` key at all.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import connector_fetch as cf

APP = Path(__file__).resolve().parent.parent / "app"
SERVER = "claude_ai_Intuit_QuickBooks"


# ── Stream builders ──────────────────────────────────────────────────────────

def _init(servers, tools=94, session="sess-1"):
    return json.dumps({
        "type": "system", "subtype": "init", "session_id": session,
        "mcp_servers": servers, "tools": ["t"] * tools,
    })


def _use(name, args, uid="u1"):
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": name, "input": args}]},
    })


def _result_ok(uid="u1", content="RAW"):
    # No `is_error` key at all: that is what a real successful result looks
    # like, and reading absence as failure would fail every good fetch.
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "content": content}]},
    })


def _result_err(uid="u1", content="boom"):
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "is_error": True,
             "content": content}]},
    })


def _final(text="DONE", session="sess-1", cost=0.01, turns=2):
    return json.dumps({
        "type": "result", "result": text, "session_id": session,
        "total_cost_usd": cost, "num_turns": turns,
    })


def _connected(status="connected"):
    return [{"name": "claude.ai Intuit QuickBooks", "status": status},
            {"name": "claude.ai Google Drive", "status": "needs-auth"}]


def _stream(*lines):
    return "\n".join(lines) + "\n"


CALL = [{"tool": "company_info", "args": {}}]


class _Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


@pytest.fixture
def spawned(monkeypatch):
    """Capture the argv and env of every spawn; return canned stdout."""
    calls = []
    queue = []

    def _fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return _Proc(queue.pop(0) if queue else "")

    # claude_bin() resolves the CLI from PATH and raises when Claude Code is
    # absent, which is the CI condition. Stubbing subprocess.run alone is not
    # enough: the argv is built by calling the resolver first.
    monkeypatch.setattr(cf, "claude_bin", lambda: "claude")
    monkeypatch.setattr(cf.subprocess, "run", _fake_run)
    monkeypatch.setattr(cf.claude_update, "ensure_current", lambda *a, **k: None)
    monkeypatch.setattr(cf, "_delete_transcript", lambda *a, **k: True)
    return {"calls": calls, "queue": queue}


# ── P1: the flags ARE the confinement ────────────────────────────────────────

def test_every_confinement_flag_is_passed(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(), _final()))
    cf.fetch(SERVER, CALL)
    argv = spawned["calls"][0]["argv"]

    assert argv[0] == "claude" and argv[1] == "-p"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv, "stream-json requires --verbose in print mode"
    # An empty, NON-strict mcp-config: adds no servers, keeps the user's own,
    # and buys the documented pre-turn wait for a remote connector.
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--strict-mcp-config" not in argv, \
        "strict would disable the very connector this call exists to read"
    assert argv[argv.index("--tools") + 1] == "", "built-in tools must be off"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--disable-slash-commands" in argv, \
        "--tools does not gate skill auto-invocation, and it recurses"
    # bypassPermissions is for the scheduler's whole-briefing runs. A fetch
    # reads five numbers; it gets nothing.
    assert "--permission-mode" not in argv
    assert "--dangerously-skip-permissions" not in argv


def test_the_allowlist_names_every_tool_in_full_and_never_a_wildcard(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()),
        _use(f"mcp__{SERVER}__company_info", {}, "a"), _result_ok("a"),
        _use(f"mcp__{SERVER}__qbo_accounting_get_balance_sheet", {}, "b"),
        _result_ok("b"), _final()))
    cf.fetch(SERVER, [{"tool": "company_info", "args": {}},
                      {"tool": "qbo_accounting_get_balance_sheet", "args": {}}])
    argv = spawned["calls"][0]["argv"]
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--permission-prompts")]

    assert allowed == [f"mcp__{SERVER}__company_info",
                       f"mcp__{SERVER}__qbo_accounting_get_balance_sheet"]
    # The CLI rejects a wildcard in an allow rule, and that is a mercy: this
    # server also exposes invoice deletes, payroll edits and a loan application.
    for entry in allowed:
        assert "*" not in entry


def test_the_prompt_travels_on_stdin_as_utf8_and_never_in_argv(spawned):
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    cf.fetch(SERVER, CALL)
    kwargs = spawned["calls"][0]["kwargs"]
    argv = spawned["calls"][0]["argv"]

    assert "Call each of the following tools" in kwargs["input"]
    assert not any("Call each of the following" in str(a) for a in argv), \
        "a multi-line prompt in argv is truncated at the first newline on Windows"
    assert kwargs["encoding"] == "utf-8", \
        "the locale codec raises on any character it cannot map"


def test_the_child_environment_is_scrubbed_and_told_to_wait(spawned, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-travel")
    monkeypatch.delenv("MCP_TIMEOUT", raising=False)
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    cf.fetch(SERVER, CALL)
    env = spawned["calls"][0]["kwargs"]["env"]

    assert "ANTHROPIC_API_KEY" not in env, "model calls route via the subscription"
    assert env["MCP_TIMEOUT"] == cf.MCP_TIMEOUT_MS
    assert env["VAN_GOGH_UNATTENDED"] == "1"


def test_an_existing_mcp_timeout_is_left_alone(spawned, monkeypatch):
    monkeypatch.setenv("MCP_TIMEOUT", "90000")
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    cf.fetch(SERVER, CALL)
    assert spawned["calls"][0]["kwargs"]["env"]["MCP_TIMEOUT"] == "90000"


def test_it_runs_from_a_private_directory_with_no_console_window(spawned):
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    cf.fetch(SERVER, CALL)
    kwargs = spawned["calls"][0]["kwargs"]

    assert kwargs["cwd"] == cf._WORK_DIR, \
        "a shared temp cwd lets another local user plant .claude/settings.json"
    assert kwargs["creationflags"] == cf.NO_WINDOW


def test_the_cli_is_brought_up_to_date_before_an_unattended_spawn(monkeypatch):
    """A CLI too old for the model fails every fetch until a human notices."""
    seen = []
    monkeypatch.setattr(cf, "claude_bin", lambda: "claude")
    monkeypatch.setattr(cf.subprocess, "run",
                        lambda *a, **k: _Proc(_stream(_init(_connected()),
                                                      _use(f"mcp__{SERVER}__company_info", {}),
                                                      _result_ok(), _final())))
    monkeypatch.setattr(cf, "_delete_transcript", lambda *a, **k: True)
    monkeypatch.setattr(cf.claude_update, "ensure_current",
                        lambda *a, **k: seen.append(True))
    cf.fetch(SERVER, CALL)
    assert seen == [True]


def test_a_failing_update_check_never_costs_the_fetch(monkeypatch):
    monkeypatch.setattr(cf, "claude_bin", lambda: "claude")
    monkeypatch.setattr(cf.subprocess, "run",
                        lambda *a, **k: _Proc(_stream(_init(_connected()),
                                                      _use(f"mcp__{SERVER}__company_info", {}),
                                                      _result_ok(), _final())))
    monkeypatch.setattr(cf, "_delete_transcript", lambda *a, **k: True)

    def boom(*_a, **_k):
        raise RuntimeError("no network")

    monkeypatch.setattr(cf.claude_update, "ensure_current", boom)
    assert cf.fetch(SERVER, CALL)["ok"] is True


# ── P2: code owns the arguments and the verdict ──────────────────────────────

def test_a_confident_answer_with_no_tool_call_is_a_failure(spawned):
    """The exact shape of the race: an articulate reply, and no data at all."""
    spawned["queue"].append(_stream(_init(_connected()), _final("DONE")))
    out = cf.fetch(SERVER, CALL)

    assert out["ok"] is False
    assert "without calling" in out["reason"]


def test_the_scanner_for_that_can_actually_see_a_real_call(spawned):
    """MUTATION CONTROL: prove the check above is not vacuous."""
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    assert cf.fetch(SERVER, CALL)["ok"] is True


def test_wider_arguments_than_were_asked_for_are_refused(spawned):
    """The fiscal-year-to-date defect, caught at its only detectable moment.

    A P&L asked for one month and answered for the year to date is a correct
    tool, a correct-looking label, and a number four times too large. Nothing
    downstream can tell; only the arguments can.
    """
    calls = [{"tool": "profit_loss_quickbooks_account",
              "args": {"start_date": "2026-09-01", "end_date": "2026-09-10"}}]
    spawned["queue"].append(_stream(
        _init(_connected()),
        _use(f"mcp__{SERVER}__profit_loss_quickbooks_account",
             {"date_macro": "This Fiscal Year-to-date"}),
        _result_ok(content='{"net": 84210}'), _final()))
    out = cf.fetch(SERVER, calls)

    assert out["ok"] is False
    assert "instead of" in out["reason"]
    assert "date_macro" in out["reason"]


def test_the_same_tool_called_twice_is_refused(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()),
        _use(f"mcp__{SERVER}__company_info", {}, "a"), _result_ok("a"),
        _use(f"mcp__{SERVER}__company_info", {}, "b"), _result_ok("b"),
        _final()))
    out = cf.fetch(SERVER, CALL)

    assert out["ok"] is False
    assert "more than once" in out["reason"]


def test_a_tool_nobody_asked_for_is_refused(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()),
        _use(f"mcp__{SERVER}__qbo_sales_delete_invoice", {"id": "7"}, "a"),
        _result_ok("a"), _final()))
    out = cf.fetch(SERVER, CALL)

    assert out["ok"] is False
    assert "not requested" in out["reason"]


def test_a_missing_call_names_the_tool_that_never_ran(spawned):
    calls = [{"tool": "company_info", "args": {}},
             {"tool": "qbo_accounting_get_balance_sheet", "args": {}}]
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}, "a"),
        _result_ok("a"), _final()))
    out = cf.fetch(SERVER, calls)

    assert out["ok"] is False
    assert "qbo_accounting_get_balance_sheet" in out["reason"]


# ── P3: raw results, never the model's retelling ─────────────────────────────

def test_the_raw_tool_result_is_returned_verbatim(spawned):
    raw = '{"CompanyName":"Acme Solar","Id":"9130"}'
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=raw),
        _final("The company is Acme Solar and it looks healthy.")))
    out = cf.fetch(SERVER, CALL)

    assert out["data"]["company_info"] == raw
    assert "looks healthy" not in json.dumps(out["data"]), \
        "the model's prose must never reach the data"


def test_a_structured_content_block_keeps_every_word(spawned):
    """Blocks are flattened to their text, losing nothing but the wrapper.

    Flattening happens at the edge because a large result is not delivered
    inline at all: it arrives as a notice naming a file, and the pointer can
    only be followed once the block is text.
    """
    blocks = [{"type": "text", "text": "Line one"}, {"type": "text", "text": "Line two"}]
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=blocks), _final()))
    out = cf.fetch(SERVER, CALL)

    assert out["data"]["company_info"] == "Line one\nLine two"


def test_a_persisted_pointer_is_followed_to_the_real_payload(spawned, tmp_path):
    """A report of any size arrives as a pointer, not as data.

    Measured against a real 55KB balance sheet: without this the fetch hands
    back a sentence about a file where the books should be, and every section
    reads "not measured" against a perfectly good response.
    """
    payload = tmp_path / "big.json"
    payload.write_text('{"reportTitle":"Balance Sheet","rows":9}', encoding="utf-8")
    notice = (f"<persisted-output>\nOutput too large (55.1KB). Full output "
              f"saved to: {payload}\n\nPreview (first 2KB):\n{{\"rep\n"
              f"</persisted-output>")
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=notice), _final()))
    out = cf.fetch(SERVER, CALL)

    assert out["data"]["company_info"] == '{"reportTitle":"Balance Sheet","rows":9}'
    assert "persisted-output" not in out["data"]["company_info"]


def test_an_unfollowable_pointer_returns_what_it_had(spawned, tmp_path):
    """A pointer we cannot follow is data we do not have, and the caller's own
    emptiness checks are what should notice, not a crash here."""
    notice = ("<persisted-output>\nFull output saved to: "
              f"{tmp_path / 'gone.json'}\n</persisted-output>")
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=notice), _final()))
    out = cf.fetch(SERVER, CALL)

    assert "persisted-output" in out["data"]["company_info"]


def test_the_whole_session_subtree_goes_not_just_the_transcript(monkeypatch,
                                                                tmp_path):
    """A large result is written to its own file under the session directory,
    so deleting the transcript alone leaves a copy of the books behind."""
    monkeypatch.setattr(cf, "_WORK_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    projects = tmp_path / "projects"
    monkeypatch.setattr(cf, "_project_dir", lambda: projects)
    projects.mkdir()
    (projects / "sess-1.jsonl").write_text("{}", encoding="utf-8")
    results = projects / "sess-1" / "tool-results"
    results.mkdir(parents=True)
    leaked = results / "toolu_x.txt"
    leaked.write_text('{"cash": 230834.78}', encoding="utf-8")

    assert cf._delete_transcript("sess-1") is True
    assert not leaked.exists(), "the persisted result outlived the transcript"
    assert not (projects / "sess-1").exists()


# ── P5: the verdicts a reader has to act on ──────────────────────────────────

def test_an_expired_token_is_named_as_such_with_its_token(spawned):
    """Captured from the real connector on 2026-09-10."""
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_err(content='MCP server "claude.ai Intuit QuickBooks" requires '
                            're-authorization (token expired)'),
        _final("ERR")))
    out = cf.fetch(SERVER, CALL)

    assert out["status"] == cf.NEEDS_AUTH
    assert out["reason"].startswith(cf.UNAVAILABLE_TOKEN)
    assert "re-authoriz" in out["reason"]


def test_a_server_absent_from_the_session_is_not_an_auth_problem(spawned):
    """Two different fixes: reconnect a dead token, or add a missing server."""
    spawned["queue"].append(_stream(_init([]), _final("UNAVAILABLE")))
    out = cf.fetch("some_server_nobody_added", CALL)

    assert out["status"] == cf.ABSENT
    assert "not connected to this machine" in out["reason"]


def test_a_server_the_session_reports_as_needing_auth(spawned):
    spawned["queue"].append(_stream(
        _init([{"name": "claude.ai Intuit QuickBooks", "status": "needs-auth"}]),
        _final("UNAVAILABLE")))
    out = cf.fetch(SERVER, CALL)
    assert out["status"] == cf.NEEDS_AUTH


def test_a_still_connecting_server_is_retried_then_called_unavailable(spawned):
    """The race, when the wait flag did not win. A bounded number of tries.

    Measured against the real connector: the wait usually lands first time,
    sometimes second, and a budget of two failed a whole week's brief for a
    handshake. Only this cause retries; the budget is still bounded.
    """
    pending = _stream(_init(_connected("pending")), _final("NOT_READY"))
    spawned["queue"].extend([pending] * cf.CONNECT_ATTEMPTS)
    out = cf.fetch(SERVER, CALL, wait_s=0)

    assert len(spawned["calls"]) == cf.CONNECT_ATTEMPTS, "bounded, not a loop"
    assert out["ok"] is False
    assert out["reason"].startswith(cf.UNAVAILABLE_TOKEN)


def test_only_the_connect_race_is_retried(spawned):
    """An expired token retried four times is four times the wait and the
    same answer."""
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_err(content="requires re-authorization (token expired)"),
        _final("ERR")))
    out = cf.fetch(SERVER, CALL, wait_s=0)

    assert len(spawned["calls"]) == 1
    assert out["status"] == cf.NEEDS_AUTH


def test_the_retry_succeeds_when_the_server_finishes_connecting(spawned):
    spawned["queue"].append(_stream(_init(_connected("pending")), _final("NOT_READY")))
    spawned["queue"].append(_stream(_init(_connected()),
                                    _use(f"mcp__{SERVER}__company_info", {}),
                                    _result_ok(), _final()))
    out = cf.fetch(SERVER, CALL, wait_s=0)

    assert out["ok"] is True
    assert out["meta"]["attempts"] == 2


def test_a_tool_side_error_is_not_an_auth_failure(spawned):
    """QuickBooks refusing one report is worth retrying; a dead token is not."""
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_err(content="Internal server error, please try again"),
        _final("ERR")))
    out = cf.fetch(SERVER, CALL)

    assert out["status"] == cf.ERROR
    assert out["reason"].startswith(cf.TOOL_ERROR_TOKEN)
    assert cf.UNAVAILABLE_TOKEN not in out["reason"]


def test_one_failed_report_does_not_lose_the_others(spawned):
    """A brief with four sections beats no brief because a fifth timed out."""
    calls = [{"tool": "company_info", "args": {}},
             {"tool": "qbo_accounting_get_balance_sheet", "args": {}}]
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}, "a"),
        _result_ok("a", "GOOD"),
        _use(f"mcp__{SERVER}__qbo_accounting_get_balance_sheet", {}, "b"),
        _result_err("b", "report timed out"), _final()))
    out = cf.fetch(SERVER, calls)

    assert out["ok"] is True
    assert out["data"]["company_info"] == "GOOD"
    assert "qbo_accounting_get_balance_sheet" not in out["data"]
    assert any("timed out" in e for e in out["tool_errors"])


def test_a_session_with_no_init_event_is_an_error_not_a_success(spawned):
    spawned["queue"].append(_stream(_final("DONE")))
    out = cf.fetch(SERVER, CALL)

    assert out["ok"] is False
    assert "no init event" in out["reason"]


def test_a_timeout_is_reported_rather_than_raised(monkeypatch):
    monkeypatch.setattr(cf, "claude_bin", lambda: "claude")
    monkeypatch.setattr(cf.claude_update, "ensure_current", lambda *a, **k: None)

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(cf.subprocess, "run", _boom)
    out = cf.fetch(SERVER, CALL, timeout_s=1)

    assert out["ok"] is False
    assert "did not finish" in out["reason"]


# ── Stream parsing oddities seen in the wild ─────────────────────────────────

def test_a_plain_warning_line_on_the_stream_is_ignored(spawned):
    """The CLI writes "no stdin data received in 3s" onto this same stream."""
    spawned["queue"].append(
        "Warning: no stdin data received in 3s, proceeding without it.\n"
        + _stream(_init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
                  _result_ok(), _final()))
    assert cf.fetch(SERVER, CALL)["ok"] is True


def test_a_server_named_by_its_display_spelling_still_matches():
    """`claude.ai Intuit QuickBooks` and the tool prefix differ by punctuation."""
    parsed = {"servers": _connected()}
    assert cf._server_status(parsed, "claude_ai_Intuit_QuickBooks") == "connected"
    assert cf._server_status(parsed, "claude.ai Intuit QuickBooks") == "connected"


def test_the_accounting_is_carried_back_for_the_sidecar(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(), _final(cost=0.0987, turns=3)))
    meta = cf.fetch(SERVER, CALL)["meta"]

    assert meta["cost_usd"] == 0.0987
    assert meta["num_turns"] == 3
    assert meta["tools_seen"] == 94
    assert meta["duration_s"] is not None


# ── P8: the transcript does not outlive the fetch ────────────────────────────

def test_the_session_transcript_is_deleted_after_parsing(monkeypatch, tmp_path):
    """A headless run leaves a full copy of whatever it read on disk."""
    monkeypatch.setattr(cf, "_WORK_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    projects = tmp_path / "projects"
    slug = "".join(ch if ch.isalnum() else "-"
                   for ch in str((tmp_path / "work").resolve()))
    session_dir = projects / slug
    session_dir.mkdir(parents=True)
    transcript = session_dir / "sess-1.jsonl"
    transcript.write_text('{"secret":"cash 412,908.33"}', encoding="utf-8")
    monkeypatch.setattr(cf.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(cf, "_project_dir", lambda: session_dir)

    assert transcript.exists()
    assert cf._delete_transcript("sess-1") is True
    assert not transcript.exists()
    assert not session_dir.exists(), "the empty per-session dir goes too"


def test_deleting_a_transcript_that_is_not_there_is_not_an_error(monkeypatch,
                                                                 tmp_path):
    monkeypatch.setattr(cf, "_project_dir", lambda: tmp_path / "nope")
    assert cf._delete_transcript("sess-absent") is False
    assert cf._delete_transcript("") is False


def test_the_transcript_slug_resolves_symlinked_temp_dirs(monkeypatch, tmp_path):
    """On macOS /var resolves to /private/var, and the CLI files the resolved
    spelling. The first version of this guessed, and silently deleted nothing."""
    monkeypatch.setattr(cf, "_WORK_DIR", str(tmp_path))
    expected = "".join(ch if ch.isalnum() else "-" for ch in str(tmp_path.resolve()))
    assert cf._project_dir().name == expected


# ── P6 (static half) and the read-only rule ──────────────────────────────────

# A literal tool name, as opposed to an f-string template that BUILDS one.
# `mcp__{server}__{tool}` is the builder in connector_fetch and is not a rule;
# anything without a brace is claiming to name a real tool and must name
# exactly one.
_LITERAL_MCP_RE = re.compile(r'"(mcp__[^"{}]*)"')
_ONE_TOOL_RE = re.compile(r"mcp__[A-Za-z0-9_]+__[a-z0-9_]+\Z")


def _wildcard_offenders(text, label=""):
    out = []
    for match in _LITERAL_MCP_RE.finditer(text):
        entry = match.group(1)
        if entry.endswith("__"):
            continue                    # a prefix, completed elsewhere
        if not _ONE_TOOL_RE.match(entry):
            out.append(f"{label}: {entry}" if label else entry)
    return out


def test_no_allowlist_anywhere_in_app_carries_a_wildcard():
    """A whitelist with a star in it is not a whitelist."""
    offenders = []
    for path in APP.glob("*.py"):
        offenders += _wildcard_offenders(path.read_text(encoding="utf-8"),
                                         path.name)
    assert not offenders, "allowlist entries must name one tool exactly: " + \
        ", ".join(offenders)


def test_the_wildcard_scanner_would_catch_a_planted_one():
    """MUTATION CONTROL: the scan above is proven before its zero is trusted.

    Run against the real scanner, not a retyped copy of its regex, so a scanner
    that stops matching stops passing this too.
    """
    planted = 'ALLOW = ["mcp__claude_ai_Intuit_QuickBooks__*"]'
    assert _wildcard_offenders(planted) == ["mcp__claude_ai_Intuit_QuickBooks__*"]
    # And it does not cry wolf over a legitimate single-tool entry.
    good = 'ALLOW = ["mcp__claude_ai_Intuit_QuickBooks__company_info"]'
    assert _wildcard_offenders(good) == []


# ── The prompt states the arguments it will be graded on ─────────────────────

def test_the_prompt_spells_out_every_call_and_its_arguments():
    prompt = cf.build_prompt(SERVER, [
        {"tool": "company_info", "args": {}},
        {"tool": "profit_loss_quickbooks_account",
         "args": {"start_date": "2026-09-01", "end_date": "2026-09-30"}}])

    assert f"mcp__{SERVER}__company_info" in prompt
    assert '"start_date": "2026-09-01"' in prompt
    assert "exactly once" in prompt
    assert "Never invent a result" in prompt


def test_the_check_helper_refuses_a_name_it_does_not_know():
    out = cf.check("sagemath")
    assert out["ok"] is False and out["status"] == cf.ABSENT


def test_every_known_probe_names_a_read_only_tool():
    for name, probe in cf.PROBES.items():
        assert name in cf.SERVERS
        assert not any(w in probe["tool"] for w in
                       ("create", "update", "delete", "submit", "send"))


# ── The probe: verifying a connector nobody has written code for ─────────────
#
# The job these serve is "we just connected something at a client, does it
# work and what can it do", so they must answer for a server this codebase has
# never heard of. That is why none of them consults SERVERS or PROBES.

def test_a_display_name_maps_to_the_tool_prefix():
    """`claude.ai Intuit QuickBooks` names its tools
    `mcp__claude_ai_Intuit_QuickBooks__...`: punctuation becomes underscores."""
    assert cf._prefix_for("claude.ai Intuit QuickBooks") == \
        "mcp__claude_ai_Intuit_QuickBooks"
    assert cf._prefix_for("context7") == "mcp__context7"
    assert cf._prefix_for("plugin:claude-mem:mcp-search") == \
        "mcp__plugin_claude_mem_mcp_search"


@pytest.mark.parametrize("name, is_read", [
    # Reads, including the ones whose namespace hides the verb.
    ("mcp__x__qbo_accounting_get_balance_sheet", True),
    ("mcp__x__qbo_payroll_get_employee_timeoff_assignments", True),
    ("mcp__x__company_info", True),
    ("mcp__x__profit_loss_quickbooks_account", True),
    ("mcp__x__search_messages", True),
    # Writes, including the one a verb-only denylist misses.
    ("mcp__x__qbo_sales_create_invoice", False),
    ("mcp__x__qbo_sales_delete_invoice", False),
    ("mcp__x__qbo_sales_send_invoice_email", False),
    ("mcp__x__money_onboarding_application_submit", False),
    ("mcp__x__qbo_payroll_update_employee", False),
])
def test_the_read_write_split_sorts_real_tool_names(name, is_read):
    """Measured against the real 78-tool QuickBooks list."""
    assert cf._looks_read_only(name) is is_read


def test_the_split_errs_toward_calling_a_read_a_write():
    """`estimate_loan_payments` only reads and is flagged anyway. For a list
    someone reads before deciding what to allow, that is the right way to be
    wrong, and it is why nothing authorizes anything from this."""
    assert cf._looks_read_only("mcp__x__qbo_lending_estimate_loan_payments") is False


def test_a_listing_that_comes_back_empty_is_retried(spawned):
    """A session that starts before the server connects truthfully reports no
    tools, and "this connector offers nothing" is the most misleading answer
    this command could give."""
    empty = _stream(_init(_connected("pending")), _final("NONE"))
    full = _stream(_init(_connected()),
                   _final("mcp__claude_ai_Intuit_QuickBooks__company_info"))
    spawned["queue"].extend([empty, full])
    out = cf.tools_of(SERVER, claude="claude")

    assert len(spawned["calls"]) == 2
    assert out["tools"] == ["mcp__claude_ai_Intuit_QuickBooks__company_info"]


def test_names_are_trusted_over_a_pending_status(spawned):
    """The real connector reports `pending` in the init snapshot and hands
    over its whole tool list in the same session. An early return on status
    reported zero tools for a connector offering 78."""
    spawned["queue"].append(_stream(
        _init(_connected("pending")),
        _final("mcp__claude_ai_Intuit_QuickBooks__company_info\n"
               "mcp__claude_ai_Intuit_QuickBooks__qbo_sales_create_invoice")))
    out = cf.tools_of(SERVER, claude="claude")

    assert len(out["tools"]) == 2
    assert len(out["reads"]) == 1 and len(out["writes"]) == 1


def test_a_server_that_is_not_there_says_so(spawned):
    spawned["queue"].extend([_stream(_init([]), _final("NONE"))]
                            * cf.CONNECT_ATTEMPTS)
    out = cf.tools_of("nobody_added_this", claude="claude")

    assert out["ok"] is False
    assert out["status"] == cf.ABSENT


def test_the_probe_describes_a_response_without_printing_it():
    """The shape is what a parser is written against; the contents may be
    someone's bank balances and this is printed to a terminal."""
    shape = cf._describe(json.dumps({
        "reportTitle": "Balance Sheet",
        "reportData": {"rows": [{"cells": [{"name": "ACCOUNT_NAME",
                                            "value": "Business Checking"},
                                           {"name": "TOTAL", "value": 412908.33}]}]},
        "realmId": "9130000000000001"}))

    assert shape["kind"] == "json"
    assert shape["top_level_keys"] == ["realmId", "reportData", "reportTitle"]
    rendered = json.dumps(shape)
    assert "412908.33" not in rendered, "a figure reached the shape sketch"
    assert "Business Checking" not in rendered, "an account name reached it"
    assert "ACCOUNT_NAME" in rendered, "the cell names ARE the shape"


def test_the_probe_describes_a_plain_text_response():
    """company_info answers in prose, and a parser author needs to see that."""
    shape = cf._describe("Company Name: Acme Solar\nIndustry: 813312")
    assert shape["kind"] == "text"
    assert shape["first_lines"][0].startswith("Company Name")


def test_the_probe_counts_the_bytes_it_got(spawned):
    payload = json.dumps({"reportTitle": "Balance Sheet", "rows": []})
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=payload), _final()))
    out = cf.probe(SERVER, "company_info", {}, claude="claude")

    assert out["ok"] is True
    assert out["bytes"] == len(payload)
    assert out["shape"]["top_level_keys"] == ["reportTitle", "rows"]


def test_the_probe_saves_the_capture_for_fixtures(spawned, tmp_path):
    """The capture is the point: fixtures built from a documented shape rather
    than a real response is what cost a whole parser."""
    payload = json.dumps({"reportTitle": "Balance Sheet"})
    target = tmp_path / "capture.json"
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_ok(content=payload), _final()))
    out = cf.probe(SERVER, "company_info", {}, claude="claude",
                   save_to=str(target))

    assert out["saved_to"] == str(target)
    assert target.read_text(encoding="utf-8") == payload


def test_a_failed_probe_reports_the_failure_not_a_shape(spawned):
    spawned["queue"].append(_stream(
        _init(_connected()), _use(f"mcp__{SERVER}__company_info", {}),
        _result_err(content="requires re-authorization (token expired)"),
        _final("ERR")))
    out = cf.probe(SERVER, "company_info", {}, claude="claude")

    assert out["ok"] is False
    assert "shape" not in out
