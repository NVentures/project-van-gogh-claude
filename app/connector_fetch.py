#!/usr/bin/env python3
"""Fetch data from a Claude MCP connector with nobody present.

Van Gogh reaches mail and calendar through the user's own OAuth, and that does
not change. Some sources have no OAuth client to reach for: the books live in
QuickBooks, and the only way in is the connector the user authorized on
claude.ai, whose token Anthropic holds. Until now that put those sources out of
reach of anything scheduled, because a connector was only ever used by a person
typing in a chat window.

A headless `claude -p` inherits those connectors, so a scheduled job can use
one. That is worth doing carefully, because the session this spawns is an agent
with access to the user's financial records, and no one is watching it.

**Every guarantee here is a flag, never a sentence in the prompt.**

* `--tools ""` removes every built-in tool. No Bash, no Write, no WebFetch.
* `--allowedTools` names each permitted tool in full. It is a whitelist: a tool
  absent from it does not exist for this call. `mcp__<server>__*` is rejected
  by the CLI, which is a good thing, because the QuickBooks connector alone
  exposes 94 tools including invoice deletes, payroll edits and a loan
  application. The caller passes read tools; `finance_brief` proves its own
  list is read-only by construction.
* `--permission-prompts none` denies anything that would ask. Nobody is here
  to answer, and a prompt would hang until the timeout.
* `--disable-slash-commands` stops skill auto-invocation, which `--tools` does
  NOT gate and which recurses.
* No `--permission-mode`. The scheduler's skill path uses bypassPermissions
  because a whole briefing needs to write files; this call needs to read five
  numbers, so it gets nothing.

**The empty `--mcp-config` is load-bearing and looks like a typo.** A remote
MCP server connects during the session, and without this flag the first turn
does not wait for it: measured on 2026-09-10, three confined runs answered in
4 to 6 seconds with the server still `pending`, the model cheerfully reporting
that no QuickBooks tools existed. Passing a `--mcp-config` (even an empty one,
WITHOUT `--strict-mcp-config`, so the user's own servers still load) opts into
the documented pre-turn wait: the same call then reports `connected` with 92
tools. An empty config that adds no servers, purely to make the CLI wait, is
the whole trick.

**Code owns the arguments and the verdict.** The model is asked to call named
tools with arguments this module supplies, and the answer is read out of the
event stream, never out of the model's prose. A session that returns a
confident paragraph without calling anything is a failure, not a result. So is
one that calls a tool with arguments other than the ones it was given: asking
for a month and receiving a fiscal year to date is exactly the failure that
renders a plausible wrong number under a correct-looking label.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_update                                            # noqa: E402
from claude_cli import HAIKU_MODEL                              # noqa: E402
from platform_compat import NO_WINDOW, claude_bin               # noqa: E402

# A private, user-only working dir (mode 0700) so the child never discovers a
# `.claude/settings.json` planted by another local user in shared temp. Same
# reasoning as claude_cli._WORK_DIR, and the same lifetime.
_WORK_DIR = tempfile.mkdtemp(prefix="van-gogh-connector-")
atexit.register(lambda: shutil.rmtree(_WORK_DIR, ignore_errors=True))

# Long enough for a remote server to hand over its tool list on a slow morning.
# The CLI's own default is 30s; a scheduled job can afford to wait longer once.
MCP_TIMEOUT_MS = "60000"

# The whole fetch, including the model's turns. A read of five reports is a
# minute of work; ten is a wall, not a slow day.
FETCH_TIMEOUT_S = 600

# How long to wait before retrying when the server was still connecting.
RECONNECT_WAIT_S = 15

# How many times to try when the only thing wrong is that the remote server had
# not finished connecting. Measured against the real QuickBooks connector: the
# pre-turn wait usually lands on the first attempt, sometimes on the second,
# and a run that gave up after two failed a whole week's brief for a handshake.
# Only this one cause is retried; every other failure still stops at once.
CONNECT_ATTEMPTS = 4

# Verdicts. `ok` is the only one that carries data.
OK = "ok"
NEEDS_AUTH = "needs-auth"      # server present, its token is dead
ABSENT = "absent"              # server not in this session at all
ERROR = "error"                # it ran and something else went wrong

# The literal tokens a failure detail carries, so failure_class can name the
# class from the text alone and the watcher can say what the reader must do.
UNAVAILABLE_TOKEN = "CONNECTOR-UNAVAILABLE:"
TOOL_ERROR_TOKEN = "CONNECTOR-ERROR:"

# What the CLI says when a connector's stored token has expired. Captured from
# a real run on 2026-09-10; `claude mcp list` said "Connected" at the same
# moment, because that is a reachability check and not an authorization one.
_NEEDS_AUTH_MARKERS = (
    "requires re-authorization",
    "requires reauthorization",
    "needs-auth",
    "unauthorized",
    "token expired",
    "not authorized",
)


def _flags(server: str, tools: tuple, model: str) -> list:
    """The argv for one confined fetch. Every entry here is a guarantee."""
    allowed = [f"mcp__{server}__{tool}" for tool in tools]
    return [
        claude_bin(), "-p",
        "--output-format", "stream-json",
        "--verbose",                    # stream-json requires it in print mode
        # Empty, and deliberately NOT --strict-mcp-config: adds no servers,
        # keeps the user's own, and buys the pre-turn wait. See the docstring.
        "--mcp-config", '{"mcpServers":{}}',
        "--tools", "",
        "--allowedTools", *allowed,
        "--permission-prompts", "none",
        "--disable-slash-commands",
        "--model", model,
    ]


def build_prompt(server: str, calls: list) -> str:
    """Ask for exactly these calls, with exactly these arguments.

    The arguments are the caller's, spelled out as JSON. The model's job is to
    pass them through, and `_verdict` checks that it did.
    """
    lines = [
        "Call each of the following tools exactly once, in order, passing the "
        "arguments exactly as given. Do not add, remove or alter an argument. "
        "Do not call any tool twice. Do not call any other tool.",
        "",
    ]
    for i, call in enumerate(calls, 1):
        args = json.dumps(call.get("args") or {}, sort_keys=True)
        lines.append(f"{i}. mcp__{server}__{call['tool']} with arguments: {args}")
    lines += [
        "",
        "When every call has returned, reply with the single word DONE.",
        "If the tools are not available to you, reply with the single word "
        "UNAVAILABLE and stop. Never invent a result.",
    ]
    return "\n".join(lines)


def _parse_stream(stdout: str) -> dict:
    """Pull the facts out of the event stream. The model's prose is not one.

    Returns init status, every tool_use with its arguments, every tool_result
    with its raw content, and the result event's own accounting.
    """
    out = {
        "servers": [], "tools_seen": 0, "uses": [], "results": [],
        "final_text": "", "session_id": "", "cost_usd": None,
        "num_turns": None, "saw_init": False,
    }
    pending = []                        # tool_use ids, in call order
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            # The CLI has been seen writing a plain warning onto this stream
            # ("no stdin data received in 3s"). A non-JSON line is noise, not
            # a failure.
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["saw_init"] = True
            out["servers"] = event.get("mcp_servers") or []
            out["tools_seen"] = len(event.get("tools") or [])
            out["session_id"] = event.get("session_id") or out["session_id"]
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    pending.append(block.get("id"))
                    out["uses"].append({
                        "id": block.get("id"),
                        "name": block.get("name") or "",
                        "input": block.get("input") or {},
                    })
                elif block.get("type") == "text":
                    out["final_text"] = block.get("text") or out["final_text"]
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_result":
                    continue
                content = block.get("content")
                # A successful tool_result carries no `is_error` key at all
                # rather than `is_error: false` (observed against a real
                # connector), so absence means success and only an explicit
                # true is a failure.
                out["results"].append({
                    "id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error")),
                    "content": content,
                })
        elif kind == "result":
            out["session_id"] = event.get("session_id") or out["session_id"]
            out["cost_usd"] = event.get("total_cost_usd")
            out["num_turns"] = event.get("num_turns")
            if isinstance(event.get("result"), str) and not out["final_text"]:
                out["final_text"] = event["result"]
    return out


def _server_status(parsed: dict, server: str) -> str:
    """What the init event said about this server: connected, pending, or gone."""
    for entry in parsed.get("servers") or []:
        if _server_key(entry.get("name") or "") == _server_key(server):
            return entry.get("status") or ""
    return ""


def _server_key(name: str) -> str:
    """Compare server names the way the tool prefix does.

    The CLI displays `claude.ai Intuit QuickBooks` and prefixes its tools
    `mcp__claude_ai_Intuit_QuickBooks__`, so the display name and the prefix
    differ by punctuation alone. Normalizing both ways round means a caller can
    pass either spelling.
    """
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _text_says_needs_auth(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _NEEDS_AUTH_MARKERS)


def _verdict(parsed: dict, server: str, calls: list) -> dict:
    """Decide what happened. Code decides; the model's DONE is not evidence.

    Order matters. An unauthorized connector must be named as such before any
    talk of missing results, because "the token is dead" and "the model did not
    call anything" want different actions from the reader.
    """
    by_id = {r["id"]: r for r in parsed["results"]}
    status = _server_status(parsed, server)

    # An expired token surfaces as an errored tool_result, not as an exit code.
    for result in parsed["results"]:
        if result["is_error"] and _text_says_needs_auth(_as_text(result["content"])):
            return {"status": NEEDS_AUTH, "reason":
                    f"{UNAVAILABLE_TOKEN} {server} requires re-authorization "
                    "(the stored token has expired). Reconnect it in Claude's "
                    "connector settings; nothing can be fetched until you do."}

    if not parsed["saw_init"]:
        return {"status": ERROR, "reason":
                "the session produced no init event, so nothing is known about "
                "which servers it had"}

    if not status:
        return {"status": ABSENT, "reason":
                f"{UNAVAILABLE_TOKEN} {server} is not connected to this "
                "machine at all. Add it in Claude's connector settings."}

    if status == "needs-auth":
        return {"status": NEEDS_AUTH, "reason":
                f"{UNAVAILABLE_TOKEN} {server} needs authorizing. Connect it "
                "in Claude's connector settings."}

    if status != "connected":
        # Pending: the wait did not land. The caller retries once.
        return {"status": ERROR, "reason":
                f"{server} was still connecting when the turn began "
                f"(status {status!r})", "retryable": True}

    # From here the server was up, so anything missing is about the calls.
    wanted = {f"mcp__{server}__{c['tool']}": c for c in calls}
    seen: dict = {}
    for use in parsed["uses"]:
        name = use["name"]
        if _normalize_name(name) not in {_normalize_name(k) for k in wanted}:
            return {"status": ERROR, "reason":
                    f"the session called {name}, which was not requested"}
        key = _match_key(name, wanted)
        if key in seen:
            return {"status": ERROR, "reason":
                    f"the session called {key} more than once"}
        seen[key] = use

    missing = [k for k in wanted if k not in seen]
    if missing:
        return {"status": ERROR, "reason":
                "the session returned without calling " + ", ".join(sorted(missing))}

    data, failures = {}, []
    for key, use in seen.items():
        expected = wanted[key].get("args") or {}
        if use["input"] != expected:
            return {"status": ERROR, "reason":
                    f"{key} was called with {json.dumps(use['input'], sort_keys=True)} "
                    f"instead of {json.dumps(expected, sort_keys=True)}"}
        result = by_id.get(use["id"])
        if result is None:
            return {"status": ERROR, "reason": f"{key} never returned a result"}
        tool = wanted[key]["tool"]
        if result["is_error"]:
            failures.append(f"{tool}: {_as_text(result['content'])[:200]}")
            continue
        data[tool] = result["content"]

    if not data:
        return {"status": ERROR, "reason":
                f"{TOOL_ERROR_TOKEN} every call failed. " + "; ".join(failures)}
    return {"status": OK, "data": data, "tool_errors": failures}


def _normalize_name(name: str) -> str:
    return (name or "").lower()


def _match_key(name: str, wanted: dict) -> str:
    for key in wanted:
        if _normalize_name(key) == _normalize_name(name):
            return key
    return name


# A large tool result is not delivered inline: the harness replaces it with a
# notice naming a file it wrote. A report of any size hits this, so a fetch that
# did not follow the pointer would silently hand back a sentence about a file
# instead of the books. Measured against a real 55KB balance sheet.
_PERSISTED_RE = re.compile(
    r"Full output saved to:\s*(?P<path>\S+)")


def _resolve_persisted(text: str) -> str:
    """Follow a persisted-output pointer to the file it names.

    Returns the original text when there is no pointer, or when the file
    cannot be read: a pointer we cannot follow is data we do not have, and the
    caller's own emptiness checks are what should notice.
    """
    if "<persisted-output>" not in text:
        return text
    match = _PERSISTED_RE.search(text)
    if not match:
        return text
    try:
        return Path(match.group("path")).read_text(encoding="utf-8")
    except OSError:
        return text


def _as_text(content) -> str:
    """A tool_result's content as text, whatever shape it arrived in."""
    if isinstance(content, str):
        return _resolve_persisted(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(_resolve_persisted(block.get("text") or ""))
            else:
                parts.append(json.dumps(block, sort_keys=True))
        return "\n".join(parts)
    return json.dumps(content, sort_keys=True)


def _project_dir() -> Path:
    """The ~/.claude/projects directory the CLI files this cwd's sessions under.

    The slug is the resolved absolute cwd with every character that is not a
    letter or digit replaced by a dash. Resolving matters on macOS, where the
    temp dir is under `/var` and resolves to `/private/var`: the CLI files the
    session under the resolved spelling, so a path built from the unresolved
    one points at a directory that never existed. Measured against a real run
    rather than assumed, after the first guess silently deleted nothing.
    """
    resolved = str(Path(_WORK_DIR).resolve())
    slug = "".join(ch if ch.isalnum() else "-" for ch in resolved)
    return Path.home() / ".claude" / "projects" / slug


def _transcript_path(session_id: str) -> Path | None:
    """Where the CLI filed this session's transcript, if it can be worked out.

    A headless run leaves a full JSONL transcript of everything it saw. For a
    financial fetch that is a copy of the user's books sitting in a directory
    nothing else cleans up, so the fetch deletes its own.
    """
    if not session_id:
        return None
    return _project_dir() / f"{session_id}.jsonl"


def _delete_transcript(session_id: str) -> bool:
    """Remove the session transcript. Best effort: never fails the fetch.

    The per-session directory goes too when it empties, because an empty
    directory named after a temp path is litter that accumulates one entry per
    scheduled run forever.
    """
    deleted = False
    try:
        path = _transcript_path(session_id)
        if path and path.exists():
            path.unlink()
            deleted = True
        # A large result is written to its own file under the session, and
        # deleting the transcript alone leaves that behind: a 55KB copy of the
        # user's balance sheet in a directory nothing else cleans up. The whole
        # session subtree goes.
        session_dir = _project_dir() / session_id
        if session_dir.is_dir():
            shutil.rmtree(session_dir, ignore_errors=True)
            deleted = True
        parent = _project_dir()
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return deleted
    return deleted


def _spawn(server: str, tools: tuple, prompt: str, model: str,
           timeout_s: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.setdefault("MCP_TIMEOUT", MCP_TIMEOUT_MS)
    env["VAN_GOGH_UNATTENDED"] = "1"
    return subprocess.run(
        _flags(server, tools, model),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout_s,
        cwd=_WORK_DIR,
        creationflags=NO_WINDOW,
    )


def fetch(server: str, calls: list, model: str = HAIKU_MODEL,
          timeout_s: int = FETCH_TIMEOUT_S, claude: str | None = None,
          wait_s: int = RECONNECT_WAIT_S) -> dict:
    """Run one confined fetch and return what the tools actually returned.

    `calls` is a list of `{"tool": str, "args": dict}`. The arguments belong to
    the caller: they are put in the prompt and then checked against what the
    model actually passed, so a model that improvises a wider date range is a
    failure rather than a surprising number.

    The return carries `status` (ok / needs-auth / absent / error), `data`
    (raw tool_result content per tool name, only when ok), `reason` when not
    ok, and `meta` with the accounting the sidecar records.

    Never raises for an unavailable connector: that is a verdict, not an
    exception, because the caller has to record it and tell the user.
    """
    tools = tuple(call["tool"] for call in calls)
    prompt = build_prompt(server, calls)
    # Nothing is watching a scheduled run, so a CLI too old for the model would
    # fail every fetch on this machine until a human noticed. Throttled daily.
    try:
        claude_update.ensure_current(claude)
    except Exception:                                           # noqa: BLE001
        pass                            # an update check never fails a fetch

    started = time.time()
    attempts = 0
    parsed: dict = {}
    verdict: dict = {}
    while attempts < CONNECT_ATTEMPTS:
        attempts += 1
        try:
            proc = _spawn(server, tools, prompt, model, timeout_s)
        except subprocess.TimeoutExpired:
            return _result(ERROR, reason=f"the fetch did not finish within "
                           f"{timeout_s} seconds", started=started,
                           attempts=attempts)
        parsed = _parse_stream(proc.stdout or "")
        # Resolve any persisted pointers FIRST: cleanup removes the very files
        # those pointers name, so deleting before reading loses the data.
        for item in parsed.get("results") or []:
            item["content"] = _as_text(item["content"])
        verdict = _verdict(parsed, server, calls)
        deleted = _delete_transcript(parsed.get("session_id") or "")
        if verdict["status"] != ERROR or not verdict.get("retryable"):
            break
        if attempts < CONNECT_ATTEMPTS:
            time.sleep(wait_s)

    if verdict.get("status") == ERROR and verdict.get("retryable"):
        # Second attempt still found it connecting. That is unavailable, and
        # saying so names something the reader can act on.
        verdict = {"status": NEEDS_AUTH if _server_status(parsed, server)
                   else ABSENT,
                   "reason": f"{UNAVAILABLE_TOKEN} {server} did not finish "
                             f"connecting on {attempts} attempts. Check it in "
                             "Claude's connector settings."}

    return _result(verdict["status"], reason=verdict.get("reason", ""),
                   data=verdict.get("data"), started=started,
                   attempts=attempts, parsed=parsed, deleted=deleted,
                   tool_errors=verdict.get("tool_errors") or [])


def _result(status: str, reason: str = "", data: dict | None = None,
            started: float = 0.0, attempts: int = 1, parsed: dict | None = None,
            deleted: bool = False, tool_errors: list | None = None) -> dict:
    parsed = parsed or {}
    return {
        "status": status,
        "ok": status == OK,
        "reason": reason,
        "data": data or {},
        "tool_errors": tool_errors or [],
        "meta": {
            "attempts": attempts,
            "duration_s": round(time.time() - started, 1) if started else None,
            "cost_usd": parsed.get("cost_usd"),
            "num_turns": parsed.get("num_turns"),
            "tools_seen": parsed.get("tools_seen"),
            "transcript_deleted": deleted,
        },
    }


# ── The install-time check ───────────────────────────────────────────────────

# Servers a skill can name by a short word, so the install wizard and the
# SKILL.md do not have to spell the CLI's display name.
SERVERS = {
    "quickbooks": "claude_ai_Intuit_QuickBooks",
}

# The cheapest real call each server answers. A health check is not an
# authorization check: `claude mcp list` reported this connector "Connected"
# while every call returned an expired token, so readiness means a tool
# returning data and nothing less.
PROBES = {
    "quickbooks": {"tool": "company_info", "args": {}},
}


def servers(claude: str | None = None, timeout_s: int = 120) -> dict:
    """Every connector this machine can see, and whether it is usable.

    Reads the session's own init event rather than `claude mcp list`, because
    that listing reports reachability and has shown "Connected" for a connector
    whose every call was refused. Status here is what a scheduled job would
    actually find.
    """
    try:
        claude_update.ensure_current(claude)
    except Exception:                                           # noqa: BLE001
        pass
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.setdefault("MCP_TIMEOUT", MCP_TIMEOUT_MS)
    env["VAN_GOGH_UNATTENDED"] = "1"
    argv = [claude or claude_bin(), "-p",
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", '{"mcpServers":{}}',
            "--tools", "", "--permission-prompts", "none",
            "--disable-slash-commands"]
    # A server still handshaking is not an answer, and this command exists to
    # give one. Retried on that cause alone, the same way a fetch is.
    rows: list = []
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            proc = subprocess.run(argv, input="Reply with the single word OK.",
                                  capture_output=True, text=True,
                                  encoding="utf-8", env=env, timeout=timeout_s,
                                  cwd=_WORK_DIR, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "the session did not start in time",
                    "servers": [], "tools": []}
        parsed = _parse_stream(proc.stdout or "")
        _delete_transcript(parsed.get("session_id") or "")
        rows = [{"name": entry.get("name") or "",
                 "prefix": _prefix_for(entry.get("name") or ""),
                 "status": entry.get("status") or ""}
                for entry in parsed.get("servers") or []]
        if not any(r["status"] == "pending" for r in rows):
            break
        if attempt < CONNECT_ATTEMPTS:
            time.sleep(RECONNECT_WAIT_S)
    # A server that never leaves `pending` in the init snapshot may still
    # answer perfectly: measured against the real QuickBooks connector, which
    # reported pending on every listing while a fetch against it succeeded.
    # The snapshot is a hint; a call is the fact. So anything still pending
    # gets asked directly rather than reported as a maybe.
    for row in rows:
        if row["status"] != "pending":
            continue
        answered = tools_of(row["prefix"].removeprefix("mcp__"), claude=claude)
        if answered.get("ok"):
            row["status"] = "connected"
            row["note"] = ("reported as still connecting, but answered when "
                           "asked directly")
        elif answered.get("status") == NEEDS_AUTH:
            row["status"] = "needs-auth"
    return {"ok": bool(rows), "servers": rows,
            "reason": "" if rows else "no connectors are attached to this machine"}


def _prefix_for(display_name: str) -> str:
    """The tool prefix a display name corresponds to.

    `claude.ai Intuit QuickBooks` prefixes its tools
    `mcp__claude_ai_Intuit_QuickBooks__`: punctuation becomes underscores.
    """
    out = []
    for ch in display_name:
        out.append(ch if ch.isalnum() else "_")
    return "mcp__" + "".join(out).strip("_")


def tools_of(server: str, claude: str | None = None,
             timeout_s: int = 180) -> dict:
    """What this connector actually offers, asked of the connector itself.

    For a server nothing here has code for yet. Names come from the session's
    own tool list, so they are what a fetch would have to name, spelled the way
    the CLI spells them.
    """
    try:
        claude_update.ensure_current(claude)
    except Exception:                                           # noqa: BLE001
        pass
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.setdefault("MCP_TIMEOUT", MCP_TIMEOUT_MS)
    env["VAN_GOGH_UNATTENDED"] = "1"
    prefix = server if server.startswith("mcp__") else f"mcp__{server}__"
    prompt = ("List every tool available to you whose name begins with "
              f"{prefix}. Output ONLY the exact full tool names, one per line, "
              "nothing else. Call none of them. If there are none, output "
              "exactly NONE.")
    argv = [claude or claude_bin(), "-p",
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", '{"mcpServers":{}}',
            "--tools", "", "--permission-prompts", "none",
            "--disable-slash-commands"]
    # Same race as everywhere else here: a session that begins before the
    # server finishes connecting truthfully reports that it has no tools, and
    # "this connector offers nothing" is the most misleading answer this
    # command could give. Retried on that cause alone.
    parsed: dict = {}
    names: list = []
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            proc = subprocess.run(argv, input=prompt, capture_output=True,
                                  text=True, encoding="utf-8", env=env,
                                  timeout=timeout_s, cwd=_WORK_DIR,
                                  creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "the connector did not answer in "
                                           "time", "tools": []}
        parsed = _parse_stream(proc.stdout or "")
        _delete_transcript(parsed.get("session_id") or "")
        names = sorted({line.strip() for line in
                        (parsed.get("final_text") or "").splitlines()
                        if line.strip().startswith("mcp__")})
        if names or _server_status(parsed, server) not in ("pending", ""):
            break
        if attempt < CONNECT_ATTEMPTS:
            time.sleep(RECONNECT_WAIT_S)
    # The names are trusted over the init snapshot: a server can report
    # `pending` and still hand over its whole tool list in the same session,
    # which is what the real QuickBooks connector does, and an early return on
    # status alone reported zero tools for a connector offering 94.
    status = _server_status(parsed, server)
    if not names and not status:
        return {"ok": False, "status": ABSENT, "tools": [],
                "reason": f"{UNAVAILABLE_TOKEN} {server} is not connected to "
                          "this machine at all."}
    return {"ok": bool(names), "status": status, "tools": names,
            "reads": [n for n in names if _looks_read_only(n)],
            "writes": [n for n in names if not _looks_read_only(n)],
            "reason": "" if names else "the connector listed no tools"}


# Verbs that mean a tool changes something. Used only to SORT a listing for a
# human to read, never to authorize anything: an allowlist is written by hand
# and checked by its own test, because a name is not a permission model.
_WRITE_VERBS = ("create", "update", "delete", "submit", "save", "assign",
                "duplicate", "send", "void", "email", "apply", "refund",
                "transfer", "deposit", "upload", "attach", "payment", "post",
                "add", "remove", "set", "write", "invite", "archive")


def _looks_read_only(tool_name: str) -> bool:
    """A guess from the name, deliberately biased toward caution.

    It over-flags rather than under-flags: `estimate_loan_payments` and
    `get_employee_timeoff_assignments` both read, and both land on the
    changes-things side because "estimate" and "assign" appear in them. That
    is the right way to be wrong for a list a person reads before deciding
    what to allow, and it is why nothing authorizes anything from this.
    """
    tail = tool_name.split("__")[-1].lower()
    # The read verb is rarely first: these names carry a namespace, as in
    # `qbo_payroll_get_employee_timeoff_assignments`. An explicit read verb
    # anywhere in the name settles it, whatever else the name contains.
    if re.search(r"(?:^|_)(get|list|search|read|query|fetch|find)_", tail):
        return True
    return not any(verb in tail for verb in _WRITE_VERBS)


def probe(server: str, tool: str, args: dict | None = None,
          claude: str | None = None, save_to: str | None = None) -> dict:
    """Make one real call and show what came back.

    This is the step that was skipped when the finance brief was built from a
    vendor's documented shape rather than its actual one: a parser and sixty
    tests agreed with each other and disagreed with the response, and every
    section of the first live brief would have read as unmeasured. A capture is
    the only thing that settles what a response looks like.
    """
    out = fetch(server, [{"tool": tool, "args": args or {}}], claude=claude)
    if not out["ok"]:
        return out
    text = _as_text(out["data"].get(tool))
    out["bytes"] = len(text)
    out["shape"] = _describe(text)
    if save_to:
        try:
            Path(save_to).write_text(text, encoding="utf-8")
            out["saved_to"] = save_to
        except OSError as exc:
            out["saved_to"] = f"could not save: {exc}"
    return out


def _describe(text: str) -> dict:
    """A response's structure, without its contents.

    Enough to write a parser against, and deliberately not the data: this is
    printed to a terminal and may be read over someone's shoulder, and the
    responses it describes are somebody's bank balances.

    The rule is keys and labels in, values out. A report keeps the things a
    parser must match on in its KEYS (`reportData`, `cells`) and in the NAMES
    inside its rows (`ACCOUNT_NAME`, `Current`, `1-30`), while every actual
    figure and customer name is a value. So a cell's `name` survives and its
    `value` never does, which is also why depth alone was the wrong control:
    it either stopped before the names or let the numbers through.
    """
    try:
        data = json.loads(text)
    except ValueError:
        head = [line[:80] for line in text.strip().splitlines()[:3]]
        return {"kind": "text", "first_lines": head}
    if not isinstance(data, dict):
        return {"kind": type(data).__name__,
                "length": len(data) if isinstance(data, list) else None}

    # Keys whose value is a label rather than content. A cell's `name` is the
    # thing a parser matches on, so it is shown; everything else is typed.
    LABEL_KEYS = {"name", "coltitle", "title", "key", "id", "type", "label"}

    def sketch(value, depth=0, key=""):
        if depth > 6:
            return "..."
        if isinstance(value, dict):
            return {k: sketch(v, depth + 1, k) for k, v in list(value.items())[:14]}
        if isinstance(value, list):
            # One element stands for the list: the rest are the same shape,
            # and printing more only spreads the contents further.
            return [sketch(value[0], depth + 1, key),
                    f"... {len(value)} items"] if value else []
        if isinstance(value, str) and key.lower() in LABEL_KEYS \
                and len(value) <= 40 and "\n" not in value:
            return value
        return type(value).__name__

    return {"kind": "json", "top_level_keys": sorted(data.keys()),
            "sketch": sketch(data)}


def check(name: str, claude: str | None = None) -> dict:
    """Is this connector actually usable right now? Answered by using it."""
    server = SERVERS.get(name)
    if not server:
        return {"status": ABSENT, "ok": False,
                "reason": f"no connector is configured under the name {name!r}"}
    probe = PROBES[name]
    out = fetch(server, [probe], claude=claude)
    if out["ok"]:
        out["summary"] = _as_text(out["data"].get(probe["tool"]))[:2000]
    return out


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", metavar="NAME",
                        help="prove a known connector is usable by making a "
                             "real call (quickbooks)")
    parser.add_argument("--list", action="store_true",
                        help="every connector attached to this machine, and "
                             "whether it is actually usable")
    parser.add_argument("--tools", metavar="SERVER",
                        help="what one connector offers, asked of the "
                             "connector itself")
    parser.add_argument("--probe", nargs=2, metavar=("SERVER", "TOOL"),
                        help="make one real call and show the shape that came "
                             "back, before anything is written to parse it")
    parser.add_argument("--args", default="{}",
                        help="JSON arguments for --probe")
    parser.add_argument("--save", metavar="PATH",
                        help="write the probed response to a file")
    parser.add_argument("--claude", default=None,
                        help="absolute path to the claude CLI")
    parser.add_argument("--json", action="store_true",
                        help="print the whole result as JSON")
    args = parser.parse_args(argv)

    if args.list:
        out = servers(claude=args.claude)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
            return 0 if out["ok"] else 1
        if not out["ok"]:
            print(out["reason"], file=sys.stderr)
            return 1
        for row in out["servers"]:
            state = {"connected": "OK       ",
                     "needs-auth": "NEEDS-AUTH",
                     "pending": "CONNECTING"}.get(row["status"],
                                                  row["status"] or "UNKNOWN")
            print(f"{state}  {row['name']}")
            print(f"            tools are named {row['prefix']}__<tool>")
            if row.get("note"):
                print(f"            ({row['note']})")
        print()
        if any(r["status"] == "pending" for r in out["servers"]):
            print("A connector still showing CONNECTING did not finish its "
                  "handshake even after several tries. That usually clears on "
                  "its own; if it does not, reconnect it in Claude's "
                  "connector settings.")
            print()
        print("Attached is not the same as working: this reads the session's "
              "own view, which has shown a connector as fine while every call "
              "to it was refused. Prove one with --tools <server>, then "
              "--probe <server> <tool> before writing anything to parse it.")
        return 0

    if args.tools:
        out = tools_of(args.tools, claude=args.claude)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
            return 0 if out["ok"] else 1
        if not out["ok"]:
            print(out["reason"], file=sys.stderr)
            return 1
        print(f"{len(out['tools'])} tools on {args.tools}")
        print()
        print(f"Read-only by name ({len(out['reads'])}):")
        for name in out["reads"]:
            print(f"  {name}")
        if out["writes"]:
            print()
            print(f"These change things ({len(out['writes'])}). Never put one "
                  "in an allowlist:")
            for name in out["writes"]:
                print(f"  {name}")
        print()
        print("Sorted by what the NAMES suggest, and deliberately cautious: a "
              "tool that only reads can still land on the second list if its "
              "name contains a word like estimate or assign. This is a "
              "reading aid, not a permission model. Check a tool against its "
              "own documentation before allowing it, and write the allowlist "
              "by hand.")
        return 0

    if args.probe:
        server, tool = args.probe
        try:
            call_args = json.loads(args.args)
        except ValueError as exc:
            print(f"--args is not valid JSON: {exc}", file=sys.stderr)
            return 1
        out = probe(server, tool, call_args, claude=args.claude,
                    save_to=args.save)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
            return 0 if out["ok"] else 1
        if not out["ok"]:
            print(out["reason"], file=sys.stderr)
            return 1
        print(f"OK {tool} answered with {out['bytes']} bytes")
        print()
        print(json.dumps(out["shape"], indent=2)[:3000])
        if out.get("saved_to"):
            print()
            print(f"Saved to {out['saved_to']}. Build the fixtures from this "
                  "file, keeping its structure and replacing its values.")
        return 0

    if not args.check:
        parser.error("nothing to do: pass --check, --list, --tools or --probe")

    out = check(args.check, claude=args.claude)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["ok"] else 1

    if out["ok"]:
        print(f"OK {out.get('summary', '').strip()[:400]}")
        return 0
    label = {NEEDS_AUTH: "NEEDS-AUTH", ABSENT: "ABSENT"}.get(out["status"], "ERROR")
    print(f"{label} {out['reason']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
