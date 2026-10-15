"""The one file that spells a client: Claude Code or OpenAI Codex.

Van Gogh runs inside a chat client and also spawns that client headlessly
(scheduled briefings, the mechanic, Vincent on the Workbench, every
classification call). Two clients are supported, and everything that differs
between them lives here: which binary, which command line, how a skill is
named in a prompt, where personal skills live, which instructions file a
vault carries, and what the client needs written into its own config.

Every other module asks this one. `tests/test_no_host_literals.py` fails a
module that spells a client itself, and `tests/test_host_branches.py` fails
a function here that answers for one client and not the other, so a new
feature cannot be Claude-only by accident.

The install records its client in `~/.config/van-gogh/host`; a session can
override it with `VAN_GOGH_HOST`; with neither, a Codex session is
recognised by the `CODEX_THREAD_ID` it puts in every command's environment,
and anything else is Claude, which is what every install before 0.89.0 was.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ImportError:                                            # pragma: no cover
    tomllib = None

from platform_compat import NO_WINDOW

HOSTS = ("claude", "codex")
HOST_ENV = "VAN_GOGH_HOST"
CODEX_BIN_ENV = "VAN_GOGH_CODEX_BIN"
PLUGIN_NAME = "van-gogh"
PLUGIN_ID = "van-gogh@van-gogh"
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# The reasoning effort a cheap classification call asks of Codex, where
# Claude would be asked for the Haiku model. "minimal" is refused by the
# current default model; "low" is the cheapest it accepts (audit, 2026-10-14).
CHEAP_EFFORT_DEFAULT = "low"
# Codex's default model when the user's config is ignored is not the one in
# their config, and that is fine for a completion: `config.json codex.model`
# names one when the account default is not wanted.
CODEX_CONFIG_KEY = "codex"

# Everything the Codex branches below pass to `codex`. `tests/test_codex_surface.py`
# checks each one against the installed binary's own --help, so a flag Codex
# renames is a red test here before it is a dead job on a client's machine.
CODEX_EXEC_FLAGS = (
    "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
    "--output-schema", "--add-dir", "-c", "-s", "-C", "-m",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
)
CODEX_SUBCOMMANDS = ("exec", "login", "plugin", "update", "debug")
CODEX_PLUGIN_SUBCOMMANDS = ("add", "list", "marketplace", "remove")

# The managed block Codex needs in its config.toml. The plugin's skills name
# `${CLAUDE_PLUGIN_ROOT}` in every command, Codex sets it for hooks only, and
# `shell_environment_policy.set` is how a value reaches every command the
# model runs. The sandbox tables let a session in the vault write the state
# directory and the logs and reach the network, which the Claude install
# gets from `--add-dir` and permission rules instead.
BLOCK_BEGIN = "# van-gogh:begin (managed by Project Van Gogh; rewritten on every plugin update)"
BLOCK_END = "# van-gogh:end"
_MANAGED_TABLES = ("shell_environment_policy", "sandbox_workspace_write")


# ── Which client ─────────────────────────────────────────────────────────────

def host_file() -> Path:
    import user_state
    return user_state.state_dir() / "host"


def current() -> str:
    """The client this install runs under: env, then the host file, then the
    session's own signal, then Claude."""
    raw = os.environ.get(HOST_ENV, "").strip().lower()
    if raw in HOSTS:
        return raw
    try:
        recorded = host_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        recorded = ""
    if recorded in HOSTS:
        return recorded
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "claude"


def is_claude(host: str | None = None) -> bool:
    return (host or current()) == "claude"


def is_codex(host: str | None = None) -> bool:
    return (host or current()) == "codex"


def record(host: str) -> Path:
    """Write the host file. The setup lane calls this before anything else."""
    if host not in HOSTS:
        raise ValueError(f"unknown host {host!r}; one of {HOSTS}")
    path = host_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(host + "\n", encoding="utf-8", newline="\n")
    return path


def _d(name: str, host: str | None):
    return _DISPATCH[name][host or current()]


# ── Words ────────────────────────────────────────────────────────────────────

def _word_claude() -> str:
    return "Claude"


def _word_codex() -> str:
    return "Codex"


def host_word(host: str | None = None) -> str:
    """The client's short name as a person reads it ("Claude", "Codex")."""
    return _d("host_word", host)()


def _client_claude() -> str:
    return "Claude Code"


def _client_codex() -> str:
    return "Codex"


def client_name(host: str | None = None) -> str:
    return _d("client_name", host)()


def _cmd_claude(skill: str) -> str:
    return f"/{PLUGIN_NAME}:{skill}"


def _cmd_codex(skill: str) -> str:
    return f"${PLUGIN_NAME}:{skill}"


def personal_cmd(name: str, host: str | None = None) -> str:
    """How a personal skill (no plugin prefix) is named to the client:
    `/x` in Claude Code, `$x` in Codex."""
    return ("/" if is_claude(host) else "$") + name


def cmd(skill: str, host: str | None = None) -> str:
    """How a skill is named to the client: `/van-gogh:x` or `$van-gogh:x`.

    `skill` may carry arguments ("note \"path\""); they are appended as is.
    Every string a script prints or puts in a prompt that names a skill goes
    through here, and nowhere else spells the prefix.
    """
    return _d("cmd", host)(skill)


# ── Binary ───────────────────────────────────────────────────────────────────

def _bin_claude() -> str:
    path = shutil.which("claude")
    if not path:
        raise FileNotFoundError(
            "The 'claude' CLI was not found on PATH. Install Claude Code and "
            "make sure `claude` is on your PATH before running this script."
        )
    return path


def codex_candidates() -> list[Path]:
    """Where a Codex binary lives when it is not on PATH, most specific first."""
    exe = "codex.exe" if sys.platform == "win32" else "codex"
    out = [Path.home() / ".codex" / "packages" / "standalone" / "current" / "bin" / exe]
    if sys.platform == "darwin":
        # The ChatGPT app first, then any other app that bundles Codex (the
        # standalone Codex app), in /Applications or ~/Applications.
        out.append(Path("/Applications/ChatGPT.app/Contents/Resources/codex"))
        for apps in _apps_dirs():
            for found in sorted(apps.glob("*.app/Contents/Resources/codex")):
                if found not in out:
                    out.append(found)
    return out


def _apps_dirs() -> list[Path]:
    return [Path("/Applications"), Path.home() / "Applications"]


def _native_beside_shim(found: str) -> str | None:
    """The npm package's native binary, when `found` is its node shim.

    `npm install -g @openai/codex` puts a JavaScript launcher on PATH that
    needs `node`, which launchd and Task Scheduler do not have on theirs. The
    real binary sits in a platform package beside it, and a job that records
    that path runs with no node anywhere.
    """
    try:
        real = Path(found).resolve()
    except OSError:
        return None
    parts = real.parts
    if "node_modules" not in parts:
        return None
    modules = Path(*parts[: len(parts) - parts[::-1].index("node_modules")])
    # The binary is named exactly `codex` (or `codex.exe`); the same folder
    # holds `codex-package.json`, which a looser glob picked first.
    for pattern in ("@openai/codex-*/vendor/*/codex", "@openai/codex-*/vendor/*/codex.exe",
                    "@openai/codex-*/vendor/*/bin/codex", "@openai/codex-*/vendor/*/bin/codex.exe"):
        for cand in sorted(modules.glob(pattern)):
            if cand.is_file() and cand.name in ("codex", "codex.exe"):
                return str(cand)
    return None


def _bin_codex() -> str:
    override = os.environ.get(CODEX_BIN_ENV, "").strip()
    if override and Path(override).exists():
        return override
    found = shutil.which("codex")
    if found:
        return _native_beside_shim(found) or found
    for cand in codex_candidates():
        if cand.exists():
            return str(cand)
    raise FileNotFoundError(
        "The 'codex' CLI was not found. Install Codex (the ChatGPT app on macOS, "
        "`npm install -g @openai/codex`, or the installer at chatgpt.com/codex) "
        "and make sure `codex` is on your PATH, or set VAN_GOGH_CODEX_BIN."
    )


def bin(host: str | None = None) -> str:                       # noqa: A001
    """The client's binary, an absolute path. Raises FileNotFoundError naming it."""
    return _d("bin", host)()


def version(binary: str | None = None, host: str | None = None) -> str:
    """`<binary> --version`'s version string, or "" when it cannot be read."""
    try:
        result = subprocess.run(
            [binary or bin(host), "--version"], capture_output=True, text=True,
            encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", result.stdout or "")
    return match.group(0) if match else ""


# ── Command lines ────────────────────────────────────────────────────────────
#
# Each builder returns the argv. Where the two clients want the prompt in
# different places, the builder returns (argv, stdin_text): None means the
# prompt is inside argv and the child's stdin is closed; a string is what the
# caller must write to stdin. Codex reads "additional input" from an open
# non-TTY stdin even with a positional prompt and waits forever for it
# (audit, 2026-10-14), so every Codex spawn passes stdin=DEVNULL or input=.

def _toml_str(value: str) -> str:
    """A TOML string literal. Single quotes take Windows backslashes as is."""
    if "'" not in value:
        return f"'{value}'"
    return json.dumps(value)


def plugin_root_override(root: Path | str | None = None) -> list[str]:
    """The `-c` pair that gives every command the model runs `CLAUDE_PLUGIN_ROOT`.

    Passed on every Codex spawn as well as written to config.toml, so a job
    never depends on the file being intact.
    """
    root = str(root or _PLUGIN_ROOT)
    return ["-c", f"shell_environment_policy.set.CLAUDE_PLUGIN_ROOT={_toml_str(root)}"]


def _skill_cmd_claude(skill, args, binary):
    prompt = _cmd_claude(skill) + (f" {args}" if args else "")
    return [binary or _bin_claude(), "-p", prompt,
            "--permission-mode", "bypassPermissions"]


def _skill_cmd_codex(skill, args, binary):
    prompt = _cmd_codex(skill) + (f" {args}" if args else "")
    return [binary or _bin_codex(), "exec",
            "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
            "-C", str(_PLUGIN_ROOT), *plugin_root_override(), prompt]


def skill_cmd(skill: str, args: str = "", binary: str | None = None,
              host: str | None = None) -> list[str]:
    """The argv that runs one skill headlessly with nobody present.

    The prompt is in argv on both clients (it is one short line). The Claude
    form is byte for byte what 0.86.1 ran.
    """
    return _d("skill_cmd", host)(skill, args, binary)


_CLAUDE_NON_AGENTIC = ["--tools", "", "--strict-mcp-config", "--disable-slash-commands"]


def _completion_cmd_claude(model, binary, schema_file):
    return [binary or _bin_claude(), "-p", "--model", model, *_CLAUDE_NON_AGENTIC]


def _codex_settings() -> dict:
    try:
        import config_loader
        raw = config_loader.cfg().get(CODEX_CONFIG_KEY) or {}
    except Exception:                                            # noqa: BLE001
        raw = {}
    return raw if isinstance(raw, dict) else {}


def codex_model() -> str:
    """The model `config.json codex.model` names, else "" for the account default."""
    return str(_codex_settings().get("model") or "").strip()


def cheap_effort() -> str:
    return str(_codex_settings().get("cheap_effort") or CHEAP_EFFORT_DEFAULT).strip()


def is_cheap_model(model: str | None) -> bool:
    """A Claude caller asking for Haiku is asking for the cheap tier."""
    return bool(model) and "haiku" in model.lower()


def _completion_cmd_codex(model, binary, schema_file):
    argv = [binary or _bin_codex(), "exec", "--json", "--ephemeral",
            "--ignore-user-config", "--skip-git-repo-check", "-s", "read-only"]
    chosen = codex_model()
    if chosen:
        argv += ["-m", chosen]
    if is_cheap_model(model):
        argv += ["-c", f"model_reasoning_effort={_toml_str(cheap_effort())}"]
    if schema_file:
        argv += ["--output-schema", str(schema_file)]
    return argv


def completion_cmd(model: str, binary: str | None = None, host: str | None = None,
                   schema_file: str | Path | None = None) -> list[str]:
    """A plain text completion: no tools, no skills, no connectors.

    The prompt goes on stdin on both clients. On Codex `--ignore-user-config`
    is what keeps plugins, skills, AGENTS.md and MCP servers out of the call,
    the structural stand-in for Claude's `--tools "" --strict-mcp-config
    --disable-slash-commands`; `--json` is how the reply is read back.
    """
    return _d("completion_cmd", host)(model, binary, schema_file)


_CLAUDE_READ_ONLY = ["--restricted", "--tools", "Read,Grep,Glob",
                     "--permission-prompts", "none", "--strict-mcp-config",
                     "--disable-slash-commands"]


def _readonly_cmd_claude(model, agent, plugin_dir, session_id, resume, binary):
    argv = [binary or _bin_claude(), "-p"]
    if model:
        argv += ["--model", model]
    if agent:
        argv += ["--agent", agent]
    if plugin_dir:
        argv += ["--plugin-dir", str(plugin_dir)]
    if resume:
        argv += ["--resume", resume]
    elif session_id:
        argv += ["--session-id", session_id]
    return argv + list(_CLAUDE_READ_ONLY)


def _readonly_cmd_codex(model, agent, plugin_dir, session_id, resume, binary):
    b = binary or _bin_codex()
    if resume:
        argv = [b, "exec", "resume", "--json", "--skip-git-repo-check", resume]
    else:
        argv = [b, "exec", "--json", "--skip-git-repo-check", "-s", "read-only"]
    chosen = codex_model()
    if chosen:
        argv += ["-m", chosen]
    if is_cheap_model(model):
        argv += ["-c", f"model_reasoning_effort={_toml_str(cheap_effort())}"]
    return argv


def readonly_cmd(model=None, agent=None, plugin_dir=None, session_id=None,
                 resume=None, binary: str | None = None,
                 host: str | None = None) -> list[str]:
    """An agentic call that may read its working directory and do nothing else.

    On Claude the agent file is the session's own rules (`--agent`); Codex
    has no such flag, so the caller prefixes `agent_prompt()` to the prompt.
    On Claude the caller names a new session (`session_id`) or continues one
    (`resume`); Codex names its own thread in the `thread.started` event
    (`thread_id()`), which is what `resume` must be given back.
    """
    return _d("readonly_cmd", host)(model, agent, plugin_dir, session_id, resume, binary)


def _agent_cmd_claude(prompt, agent, plugin_dir, add_dirs, binary):
    argv = [binary or _bin_claude(), "-p", prompt, "--agent", agent,
            "--plugin-dir", str(plugin_dir)]
    for d in add_dirs:
        argv += ["--add-dir", str(d)]
    argv += ["--permission-mode", "bypassPermissions", "--disable-slash-commands"]
    return argv, None


def _agent_cmd_codex(prompt, agent, plugin_dir, add_dirs, binary):
    argv = [binary or _bin_codex(), "exec", "--skip-git-repo-check",
            "-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true",
            *plugin_root_override(plugin_dir)]
    for d in add_dirs:
        argv += ["--add-dir", str(d)]
    return argv, agent_prompt(agent, plugin_dir) + "\n\n" + prompt


def agent_cmd(prompt: str, agent: str, plugin_dir: Path | str, add_dirs=(),
              binary: str | None = None, host: str | None = None):
    """A session that is one of the plugin's agents, able to write where the
    caller's working directory and `add_dirs` say and nowhere else.

    Returns (argv, stdin_text). Claude takes the prompt in argv and the
    agent by name; Codex takes the agent file's body and the prompt on
    stdin, inside a workspace-write sandbox whose roots are the working
    directory and `add_dirs`, which is the same boundary `--add-dir` draws.
    """
    return _d("agent_cmd", host)(prompt, agent, plugin_dir, tuple(add_dirs), binary)


_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)


def agent_prompt(agent: str, plugin_dir: Path | str | None = None) -> str:
    """The agent file's body with its frontmatter removed, for a client that
    cannot load the file as the session's own definition."""
    path = Path(plugin_dir or _PLUGIN_ROOT) / "agents" / f"{agent}.md"
    text = path.read_text(encoding="utf-8")
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


# ── Reading a reply back ─────────────────────────────────────────────────────

def _events(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def thread_id(result) -> str:
    """The Codex thread a `--json` run opened, else "" (Claude names its own)."""
    kept = getattr(result, "thread_id", None)
    if kept:
        return str(kept)
    for e in _events(getattr(result, "stdout", "") or ""):
        if e.get("type") == "thread.started":
            return str(e.get("thread_id") or e.get("id") or "")
    return ""


def _finish_claude(result, forbid_commands):
    return result


def _finish_codex(result, forbid_commands):
    """Turn a `--json` run into what a Claude caller expects: the reply on
    stdout, an error on stderr, and a nonzero code when the turn failed.

    `forbid_commands` is the completion's rule: a classification that ran a
    command is not a classification, whatever it answered.
    """
    events = _events(result.stdout or "")
    if not events:
        return result
    result.thread_id = thread_id(result)
    reply, errors, ran_command, completed = "", [], False, False
    for e in events:
        item = e.get("item") or {}
        if e.get("type") == "item.completed" and item.get("type") == "agent_message":
            reply = item.get("text") or reply
        if item.get("type") == "command_execution":
            ran_command = True
        if e.get("type") == "turn.completed":
            completed = True
        if e.get("type") in ("turn.failed", "error"):
            msg = e.get("message") or (e.get("error") or {}).get("message") or ""
            if msg:
                errors.append(str(msg))
        if item.get("type") == "error" and "bypass" not in str(item.get("message", "")):
            errors.append(str(item.get("message")))
    result.stdout = reply
    stderr = (result.stderr or "")
    if errors:
        stderr = (stderr + "\n" + "\n".join(errors)).strip()
        if result.returncode == 0:
            result.returncode = 1
    if forbid_commands and ran_command:
        stderr = (stderr + "\nvan-gogh: the completion ran a command, which a "
                  "completion never does; the reply was discarded").strip()
        result.stdout = ""
        result.returncode = result.returncode or 1
    if not completed and result.returncode == 0 and not reply:
        result.returncode = 1
        stderr = (stderr + "\nvan-gogh: the turn never completed").strip()
    result.stderr = stderr
    return result


def finish(result, forbid_commands: bool = False, host: str | None = None):
    """Normalise a spawn's result so callers read one shape on both clients."""
    return _d("finish", host)(result, forbid_commands)


# ── Auth, update, plugin lifecycle ───────────────────────────────────────────

def _auth_ok_claude(binary):
    result = subprocess.run([binary or _bin_claude(), "auth", "status", "--json"],
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=60, stdin=subprocess.DEVNULL,
                            creationflags=NO_WINDOW)
    return bool(json.loads(result.stdout).get("loggedIn"))


def _auth_ok_codex(binary):
    # A status line is health, not auth: the one thing that proves a sign-in
    # works is a real call, and this is the cheapest one.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [binary or _bin_codex(), "exec", "--json", "--ephemeral", "--ignore-user-config",
         "--skip-git-repo-check", "-s", "read-only", "-c",
         f"model_reasoning_effort={_toml_str(cheap_effort())}"],
        input="Reply with the single word ok.", capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=120, creationflags=NO_WINDOW,
        cwd=tempfile.gettempdir())
    return any(e.get("type") == "turn.completed" for e in _events(result.stdout))


def auth_ok(binary: str | None = None, host: str | None = None) -> bool:
    """Whether the client's sign-in works right now. May raise on a missing binary."""
    return _d("auth_ok", host)(binary)


def update_argv(binary: str | None = None, host: str | None = None) -> list[str]:
    """How the client updates itself. The same word on both, kept here so a
    rename on either side is one edit."""
    return [binary or bin(host), "update"]


def plugin_list_argv(binary: str | None = None, host: str | None = None) -> list[str]:
    return [binary or bin(host), "plugin", "list", "--json"]


def _plugin_listed_claude(text):
    start = text.find("[")
    if start == -1:
        return None
    rows = json.loads(text[start:])
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict):
            ident = str(row.get("id") or row.get("name") or "")
            if ident in (PLUGIN_ID, PLUGIN_NAME):
                return True
    return False


def _plugin_listed_codex(text):
    start = text.find("{")
    if start == -1:
        return None
    doc = json.loads(text[start:])
    rows = doc.get("installed") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("pluginId") or "") == PLUGIN_ID:
            return bool(row.get("installed", True))
    return False


def plugin_listed(text: str, host: str | None = None) -> bool | None:
    """Read `plugin list --json` output: True, False, or None when unreadable."""
    try:
        return _d("plugin_listed", host)(text or "")
    except ValueError:
        return None


def _plugin_update_argvs_claude(binary):
    b = binary or _bin_claude()
    return [[b, "plugin", "marketplace", "update", PLUGIN_NAME],
            [b, "plugin", "update", PLUGIN_ID, "--json"]]


def _plugin_update_argvs_codex(binary):
    b = binary or _bin_codex()
    return [[b, "plugin", "marketplace", "upgrade", PLUGIN_NAME],
            [b, "plugin", "add", PLUGIN_ID, "--json"]]


_LOCAL_MARKETPLACE_RE = re.compile(r"not configured as a Git marketplace", re.I)


def _skippable_claude(argv, output):
    return False


def _skippable_codex(argv, output):
    """`codex plugin marketplace upgrade` exits 1 on a marketplace added from
    a folder (a developer checkout); the catalog needs no refresh there and
    `plugin add` alone moves the plugin."""
    return argv[1:4] == ["plugin", "marketplace", "upgrade"] and bool(_LOCAL_MARKETPLACE_RE.search(output or ""))


def update_step_skippable(argv, output: str, host: str | None = None) -> bool:
    """True when a failed update step is one the client may skip, so the
    next step still runs (the reply names the step as skipped)."""
    return _d("update_step_skippable", host)(list(argv), output)


def plugin_update_argvs(binary: str | None = None, host: str | None = None) -> list[list[str]]:
    """The two commands that move the installed plugin, in order. The first
    alone refreshes the catalog and changes nothing installed, on both."""
    return _d("plugin_update_argvs", host)(binary)


def plugin_update_command(host: str | None = None) -> str:
    """The second command as a person would type it, for a message."""
    argv = plugin_update_argvs("x", host)[1]
    return " ".join([bin_word(host)] + [a for a in argv[1:] if a != "--json"])


def bin_word(host: str | None = None) -> str:
    return "claude" if is_claude(host) else "codex"


def _plugin_update_outcome_claude(reply, version_before):
    outcome = reply.get("updateOutcome")
    new_version = str(reply.get("newVersion") or "")
    if reply.get("outcome") != "ok":
        return "error", new_version
    if outcome == "updated" and new_version:
        return "ok", new_version
    return "unchanged", new_version


def _plugin_update_outcome_codex(reply, version_before):
    new_version = str(reply.get("version") or "")
    installed = str(reply.get("installedPath") or "")
    if not new_version or (installed and not Path(installed).exists()):
        return "error", new_version
    if _vtuple(new_version) > _vtuple(version_before):
        return "ok", new_version
    return "unchanged", new_version


def _vtuple(v: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (-1,)


def plugin_update_outcome(reply: dict, version_before: str,
                          host: str | None = None) -> tuple[str, str]:
    """("ok" | "unchanged" | "error", new_version) from the second command's
    JSON reply. Claude says what it did; Codex is believed only when the
    version it installed is higher than the one that was there."""
    return _d("plugin_update_outcome", host)(reply or {}, version_before or "")


def _plugin_remove_claude(binary):
    return [binary or _bin_claude(), "plugin", "uninstall", PLUGIN_ID]


def _plugin_remove_codex(binary):
    return [binary or _bin_codex(), "plugin", "remove", PLUGIN_ID]


def plugin_remove_argv(binary: str | None = None, host: str | None = None) -> list[str]:
    """How the client uninstalls the plugin (its cache goes with it)."""
    return _d("plugin_remove_argv", host)(binary)


def plugin_remove_command(host: str | None = None) -> str:
    return " ".join([bin_word(host)] + plugin_remove_argv("x", host)[1:])


# ── Where the client keeps things ────────────────────────────────────────────

def _skills_claude() -> Path:
    return Path.home() / ".claude" / "skills"


def _skills_codex() -> Path:
    return Path.home() / ".agents" / "skills"


def personal_skills_dir(host: str | None = None) -> Path:
    """Where a person's own skills are read from."""
    return _d("personal_skills_dir", host)()


def _instr_claude() -> str:
    return "CLAUDE.md"


def _instr_codex() -> str:
    return "AGENTS.md"


def instructions_filename(host: str | None = None) -> str:
    """The file in a vault the client reads as its instructions."""
    return _d("instructions_filename", host)()


def template_instructions(root: Path | str | None = None) -> Path:
    """The plugin's own template for that file (one template, spelled for
    Claude; the writer respells it for the client). `root` is the caller's
    plugin root, so a test that redirects it is honoured."""
    return Path(root or _PLUGIN_ROOT) / "vault" / "CLAUDE.md"


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw) if raw else Path.home() / ".codex"


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def codex_block(root: Path | str | None = None, writable_roots=()) -> str:
    lines = [BLOCK_BEGIN, "[shell_environment_policy]",
             f"set = {{ CLAUDE_PLUGIN_ROOT = {_toml_str(str(root or _PLUGIN_ROOT))} }}",
             "[sandbox_workspace_write]", "network_access = true"]
    roots = [str(r) for r in writable_roots if r]
    if roots:
        lines.append("writable_roots = [" + ", ".join(_toml_str(r) for r in roots) + "]")
    lines.append(BLOCK_END)
    return "\n".join(lines)


_HEADER_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")


def _carve_foreign(inner: str) -> str:
    """The tables inside the markers that are not ours, kept verbatim.

    Codex's own config writer appends a new table at the end of the file,
    which is inside the markers when the block is last: `codex plugin add`
    wrote `[marketplaces.van-gogh]` and `[plugins."van-gogh@van-gogh"]`
    there, and a sync that replaced the whole region silently uninstalled the
    plugin (found by the independent evaluation, 2026-10-14). Anything that
    is not one of the two managed tables is moved out, never dropped.
    """
    keep, current, ours = [], [], True
    for line in inner.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            name = m.group(1).strip()
            ours = name.split(".")[0].strip('"') in _MANAGED_TABLES or name in _MANAGED_TABLES
            current = [] if ours else [line]
            if not ours:
                keep.append(current)
            continue
        if not ours:
            current.append(line)
    out = "\n".join("\n".join(sec).rstrip() for sec in keep if "".join(sec).strip())
    return out.strip("\n")


_REFUSED_ONCE: set = set()


def _block_span(text: str):
    """Where the managed block sits: (begin, inner_start, inner_end, tail_start)
    or None when there is no begin marker.

    With both markers the span is what they delimit. Codex's own config
    writer (`codex plugin marketplace remove`, seen 2026-10-14) drops a
    trailing comment, so the end marker can vanish while the two managed
    tables stay; the block is then keyed on those tables: it runs from the
    begin marker to the first table header that is not one of ours, or the
    end of the file.
    """
    begin = text.find(BLOCK_BEGIN)
    if begin == -1:
        return None
    inner_start = begin + len(BLOCK_BEGIN)
    end = text.find(BLOCK_END, inner_start)
    if end != -1:
        return begin, inner_start, end, end + len(BLOCK_END)
    pos = inner_start
    for line in text[inner_start:].splitlines(keepends=True):
        m = _HEADER_RE.match(line)
        if m:
            name = m.group(1).strip()
            if name.split(".")[0].strip('"') not in _MANAGED_TABLES and name not in _MANAGED_TABLES:
                break
        pos += len(line)
    return begin, inner_start, pos, pos


def _table_defined_outside(text: str, table: str) -> bool:
    span = _block_span(text)
    pos = 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        inside = span is not None and span[0] <= pos < span[3]
        if not inside and re.match(r"\[\s*" + re.escape(table) + r"\s*\]", s):
            return True
        pos += len(line)
    return False


def sync_codex_config(root: Path | str | None = None, writable_roots=(),
                      path: Path | None = None) -> str:
    """Write the managed block into Codex's config.toml. Returns what happened:
    "written", "unchanged", "refused:<table>" or "invalid".

    Never writes a file Codex could not parse: the result is parsed first and
    lands through a temp file and a rename. A table the person defined
    themselves outside the markers is theirs; the block is refused and one
    line on stderr names the table, and jobs still work because every spawn
    carries the same value as `-c`.
    """
    path = Path(path) if path else codex_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    block = codex_block(root, writable_roots)
    for table in _MANAGED_TABLES:
        if _table_defined_outside(text, table):
            key = (str(path), table)
            if key not in _REFUSED_ONCE:
                _REFUSED_ONCE.add(key)
                print(f"van-gogh: {path} already defines [{table}]; the managed block "
                      "was not written. Every scheduled job passes the same values "
                      "on its command line, so nothing stops.", file=sys.stderr)
            return f"refused:{table}"
    span = _block_span(text)
    if span:
        begin, inner_start, inner_end, tail_start = span
        foreign = _carve_foreign(text[inner_start:inner_end])
        tail = text[tail_start:]
        if tail and not tail.startswith(("\n", "\r")):
            tail = "\n\n" + tail
        if foreign:
            tail = "\n\n" + foreign + "\n" + tail
        new = text[:begin] + block + tail
    elif text.strip():
        new = text.rstrip("\r\n") + "\n\n" + block + "\n"
    else:
        new = block + "\n"
    if new == text:
        return "unchanged"
    if tomllib is not None:
        try:
            tomllib.loads(new)
        except tomllib.TOMLDecodeError as exc:
            print(f"van-gogh: the managed block would leave {path} unparseable "
                  f"({exc}); not written", file=sys.stderr)
            return "invalid"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config.toml.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    os.replace(tmp, path)
    return "written"


def remove_codex_block(path: Path | None = None) -> bool:
    """Take the managed block out of config.toml (uninstall). True if written."""
    path = Path(path) if path else codex_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    span = _block_span(text)
    if span is None:
        return False
    begin, inner_start, inner_end, tail_start = span
    foreign = _carve_foreign(text[inner_start:inner_end])
    rest = text[tail_start:].lstrip("\r\n")
    if foreign:
        rest = foreign + "\n\n" + rest
    new = (text[:begin].rstrip("\r\n") + "\n" + rest).strip("\r\n")
    path.write_text((new + "\n") if new else "", encoding="utf-8", newline="\n")
    return True


def stdin_kwargs(stdin_text):
    """The subprocess keywords for a prompt that travels on stdin, or a closed
    stdin when it does not. Codex waits forever on an open non-TTY stdin."""
    if stdin_text is None:
        return {"stdin": subprocess.DEVNULL}
    return {"input": stdin_text}


def _skill_label_claude(skill, args):
    return f"claude -p {_cmd_claude(skill)}" + (f" {args}" if args else "")


def _skill_label_codex(skill, args):
    return f"codex exec {_cmd_codex(skill)}" + (f" {args}" if args else "")


def skill_label(skill: str, args: str = "", host: str | None = None) -> str:
    """How a failed skill run is named in an error: the command as typed."""
    return _d("skill_label", host)(skill, args)


def _terminal_claude() -> str:
    return ("That one needs a terminal: open your vault folder, type "
            "`claude --agent vincent`, and ask again there.")


def _terminal_codex() -> str:
    return ("That one needs a terminal: open your vault folder, type "
            "`codex`, mention $van-gogh:vincent, and ask again there.")


def terminal_hint(host: str | None = None) -> str:
    """What Vincent says when a request needs a session with real tools."""
    return _d("terminal_hint", host)()


def _other_plugins_claude(own):
    path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(path.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        return []
    out = []
    for key in sorted(plugins):
        name = key.split("@", 1)[0]
        if name == own:
            continue
        entries = plugins[key] if isinstance(plugins[key], list) else []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("scope") == "user":
                out.append((name, key, Path(str(entry.get("installPath") or ""))))
    return out


def _other_plugins_codex(own):
    cache = codex_home() / "plugins" / "cache"
    out = []
    if not cache.is_dir():
        return out
    for market in sorted(p for p in cache.iterdir() if p.is_dir()):
        for plugin in sorted(p for p in market.iterdir() if p.is_dir()):
            if plugin.name == own:
                continue
            versions = sorted((v for v in plugin.iterdir() if v.is_dir()),
                              key=lambda v: _vtuple(v.name))
            if versions:
                out.append((plugin.name, f"{plugin.name}@{market.name}", versions[-1]))
    return out


def other_plugin_roots(own: str = PLUGIN_NAME, host: str | None = None) -> list:
    """(name, key, root) of every other plugin installed for this person, so
    Vincent's catalog can name their skills; `key` is `name@marketplace`. A
    root that is gone is still listed, so a missing plugin is reported
    rather than silently absent."""
    return _d("other_plugin_roots", host)(own)


# ── The table ────────────────────────────────────────────────────────────────
#
# One entry per host-dependent function. tests/test_host_branches.py asserts
# every row has exactly HOSTS as its keys, so a function with one branch is a
# red test before it is ever called.

_DISPATCH = {
    "host_word": {"claude": _word_claude, "codex": _word_codex},
    "client_name": {"claude": _client_claude, "codex": _client_codex},
    "cmd": {"claude": _cmd_claude, "codex": _cmd_codex},
    "bin": {"claude": _bin_claude, "codex": _bin_codex},
    "skill_cmd": {"claude": _skill_cmd_claude, "codex": _skill_cmd_codex},
    "completion_cmd": {"claude": _completion_cmd_claude, "codex": _completion_cmd_codex},
    "readonly_cmd": {"claude": _readonly_cmd_claude, "codex": _readonly_cmd_codex},
    "agent_cmd": {"claude": _agent_cmd_claude, "codex": _agent_cmd_codex},
    "finish": {"claude": _finish_claude, "codex": _finish_codex},
    "auth_ok": {"claude": _auth_ok_claude, "codex": _auth_ok_codex},
    "plugin_listed": {"claude": _plugin_listed_claude, "codex": _plugin_listed_codex},
    "plugin_update_argvs": {"claude": _plugin_update_argvs_claude,
                            "codex": _plugin_update_argvs_codex},
    "plugin_update_outcome": {"claude": _plugin_update_outcome_claude,
                              "codex": _plugin_update_outcome_codex},
    "update_step_skippable": {"claude": _skippable_claude, "codex": _skippable_codex},
    "personal_skills_dir": {"claude": _skills_claude, "codex": _skills_codex},
    "instructions_filename": {"claude": _instr_claude, "codex": _instr_codex},
    "skill_label": {"claude": _skill_label_claude, "codex": _skill_label_codex},
    "terminal_hint": {"claude": _terminal_claude, "codex": _terminal_codex},
    "other_plugin_roots": {"claude": _other_plugins_claude, "codex": _other_plugins_codex},
    "plugin_remove_argv": {"claude": _plugin_remove_claude, "codex": _plugin_remove_codex},
}
