"""Shared helper for invoking the `claude` CLI across scripts."""

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import claude_update
import failure_class
import host
from platform_compat import NO_WINDOW, claude_bin

# The default classification model. One home for the id so a bump is one edit.
HAIKU_MODEL = "claude-haiku-4-5-20251001"


class UsageCap(RuntimeError):
    """The Claude subscription cap was reached. Nothing retries this.

    Raised by run_claude when the CLI's own output is the cap message
    (failure_class.is_usage_cap). A caller running a batch stops submitting
    work and defers what has not started (app/fanout.py); a caller filing one
    page falls back the way it always did. The text is the CLI's, which names
    the reset time, so failure_class.quota_reset_at can read it.
    """


# ── Concurrency: one process, a bounded number of model calls in flight ──────
#
# Several scripts now fan out across threads (app/fanout.py), and each thread
# may call the model. Without a bound a 10-meeting prefetch opens 10 CLI
# sessions at once, which is how a throttle is earned. The limit is per
# process and shared by every caller of run_claude, so the bound holds whoever
# is calling; VAN_GOGH_MODEL_SLOTS overrides it. VAN_GOGH_SERIAL=1 is the
# control arm and reproduces the base commit, which had NO limiter (its
# classifier ran six calls at once), so under it the limiter is inert: the
# callers' own pool widths are the only bound, exactly as before.
# Six is what the shipped code already ran: classify_threads fans out six
# Haiku batches at once and has for months on a Pro-class plan. A cap is a
# budget, not a rate, so concurrency does not spend it faster; three here
# measured 90 s slower on a 488-thread scan with nothing gained.
DEFAULT_MODEL_SLOTS = 6
SERIAL_MODEL_SLOTS = 64


def serial() -> bool:
    return os.environ.get("VAN_GOGH_SERIAL") == "1"

_limiter = None
_limiter_lock = threading.Lock()


def model_slots() -> int:
    """How many model calls may be in flight at once in this process."""
    if serial():
        return SERIAL_MODEL_SLOTS
    raw = os.environ.get("VAN_GOGH_MODEL_SLOTS", "")
    try:
        return max(1, int(raw)) if raw.strip() else DEFAULT_MODEL_SLOTS
    except ValueError:
        return DEFAULT_MODEL_SLOTS


def _slot():
    """The process-wide semaphore, built on first use so tests can reset it."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = threading.BoundedSemaphore(model_slots())
        return _limiter


def _reset_limiter():
    """Forget the semaphore so the next call rebuilds it from the env."""
    global _limiter
    with _limiter_lock:
        _limiter = None


# ── Retry on a throttle, once more on a missing model ────────────────────────
#
# A throttle is the API saying "not right now": a 429, an overloaded 529, or
# the words "rate limit" in the CLI's output. It is retried after a short
# pause, with the same prompt and model, and the pause grows. The subscription
# cap also says 429 on some paths, which is why the cap test runs first: a
# cap is never retried, because the next success is at the reset time, not in
# ten seconds.
THROTTLE_BACKOFF = (2, 5, 10)
# Process-wide counters a caller may read into its own ledger, so a slow
# run can say whether the model was throttled rather than just slow.
STATS = {"calls": 0, "throttle_retries": 0, "fallbacks": 0, "seconds": 0.0}
_THROTTLE_RE = re.compile(
    r"\b429\b|\b529\b|rate[ _-]?limit|overloaded_error|too many requests",
    re.I)

# The requested model is not one this account can use: the API's 404 for a
# model id, or the CLI's own wording for one it does not know. The call is
# made once more on the classification model rather than failing the whole
# briefing on a model-name mismatch. Deliberately disjoint from the stale-CLI
# message ("does not support this model"), which claude_update heals first.
_MODEL_UNAVAILABLE_RE = re.compile(
    r"not_found_error[^\n]{0,120}model"
    r"|model[^\n]{0,60}(?:not found|does not exist|is not available|unavailable)"
    r"|(?:unknown|invalid|unsupported) model",
    re.I)

# Replaceable in tests so a retry does not actually wait.
_sleep = time.sleep


def is_throttle(text) -> bool:
    """True for a throttle the caller should pause and retry, never a cap."""
    if not text:
        return False
    return bool(_THROTTLE_RE.search(text)) and not failure_class.is_usage_cap(text)


def is_model_unavailable(text) -> bool:
    if not text:
        return False
    return (bool(_MODEL_UNAVAILABLE_RE.search(text))
            and not claude_update.is_stale_cli_error(text))


def _text(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _cap_hit(result) -> bool:
    """The CLI answered with the cap message instead of an answer.

    Checked on a nonzero exit, and on a zero exit whose whole output is short
    enough to be the message itself: a real completion is never forty words.
    An answer that merely quotes the words inside a long reply is an answer.
    """
    text = _text(result)
    if not failure_class.is_usage_cap(text):
        return False
    return result.returncode != 0 or len((result.stdout or "").strip()) < 400

# A private, user-only working dir (mode 0700) so the child `claude -p` never
# discovers a `.claude/settings.json` — with hooks that run arbitrary shell —
# planted by another local user in the shared, world-writable temp dir. Created
# once and reused (it stays empty); removed at interpreter exit.
_WORK_DIR = tempfile.mkdtemp(prefix="van-gogh-claude-")
atexit.register(lambda: shutil.rmtree(_WORK_DIR, ignore_errors=True))

# Flags that turn the headless `claude -p` agent into a plain text completion.
# All three matter:
#  - `--tools ""` disables every built-in tool (Bash/Edit/Task/...).
#  - `--strict-mcp-config` (with no --mcp-config) disables ALL MCP servers, so
#    the agent has no Gmail/Slack/etc. connector to reach for. Without it the
#    model treats a "classify these email threads" prompt as a project, decides
#    it needs Gmail access, and returns a question instead of the JSON.
#  - `--disable-slash-commands` disables skills. Skill auto-invocation is NOT
#    gated by --tools: a classification prompt matching the voice-generator
#    skill description auto-triggers that skill, which re-runs the whole
#    pipeline, which calls claude again — an unbounded recursive fork-bomb.
# NOTE: these flags are for classification/completion calls only. The digest
# renderer (digest_send.py) and scheduler deliberately run AGENTIC `claude -p`
# with slash-command dispatch — never route those through run_claude.
_NON_AGENTIC_FLAGS = ["--tools", "", "--strict-mcp-config", "--disable-slash-commands"]


# Flags for a READ-ONLY agentic call: the model may look things up, and can do
# nothing else. Every guarantee here is enforced by the CLI, never by the
# prompt, because a prompt instruction is necessary and never sufficient.
#  - `--restricted` removes every command-running tool (Bash, PowerShell, REPL)
#    and WebFetch, IGNORES the user's own settings files (so a personal
#    permission rule cannot widen this call), confines the file tools to the
#    working directory, and refuses bypassPermissions.
#  - `--tools "Read,Grep,Glob"` is a whitelist, not a blocklist: a tool absent
#    from this list does not exist for the call. There is no Write and no Edit,
#    so the session cannot alter the vault it is reading.
#  - `--permission-prompts none` denies anything that would prompt, because
#    nobody is at a terminal to answer; without it a prompt hangs until timeout.
#  - `--strict-mcp-config` and `--disable-slash-commands` carry the same weight
#    they do above: no connector to reach for, and no skill auto-invocation
#    (which --tools does NOT gate, and which recurses).
# Probed on 2026-09-08 against a real vault: reading a path outside the working
# directory is DENIED, Bash is DENIED, grep inside it works, and the session
# reports exactly three tools. Sending mail is impossible because nothing in
# that set can execute anything, which is a structural fact and not the model's
# good judgement.
_READ_ONLY_FLAGS = [
    "--restricted",
    "--tools", "Read,Grep,Glob",
    "--permission-prompts", "none",
    "--strict-mcp-config",
    "--disable-slash-commands",
]

READ_ONLY_TOOLS = ("Read", "Grep", "Glob")


def readonly_cmd(model=HAIKU_MODEL, agent=None, plugin_dir=None,
                 session_id=None, resume=None):
    """The argv for a read-only call. The prompt is NOT in it (see run_claude).

    The four optional names never widen what the call can do: the read-only
    flags are appended whatever is passed, and they are what decide the tool
    set, so an agent file listing Bash still gets Read, Grep and Glob here.

    - `model=None` leaves the choice to the CLI, which is how an agent file's
      own `model:` line gets to apply.
    - `agent` with `plugin_dir` makes a shipped agent file the session's own
      rules. The pair is not optional: a plugin's agents are found only when
      the plugin is loaded, and a scripted session loads nothing (the same
      fact `repair._command` documents).
    - `session_id` names a new conversation and `resume` continues one, so a
      second message can refer to the first. Pass one or neither.
    """
    # The binary comes from this module's own resolver so a test that stubs
    # `claude_cli.claude_bin` still decides what is spawned.
    return host.readonly_cmd(model, agent, plugin_dir, session_id, resume,
                             binary=claude_bin())


def run_claude_readonly(prompt, root, model=HAIKU_MODEL, timeout=240,
                        agent=None, plugin_dir=None, session_id=None,
                        resume=None):
    """Run an agentic `claude -p` that may READ `root` and do nothing else.

    `root` becomes the working directory, which is what confines the file
    tools: --restricted allows them only inside the working directories, and
    no --add-dir is passed, so the call cannot reach the rest of the disk.

    Returns the CompletedProcess, like run_claude. The caller owns the prompt
    and any cleaning of the reply. The keyword arguments are `readonly_cmd`'s.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Codex has no --agent: the agent file's body travels at the top of the
    # prompt instead (host.agent_prompt), and the reply comes back as JSONL
    # that host.finish turns into the plain stdout every caller reads.
    if agent and host.is_codex() and not resume:
        prompt = host.agent_prompt(agent, plugin_dir) + "\n\n" + prompt
    return host.finish(subprocess.run(
        readonly_cmd(model, agent, plugin_dir, session_id, resume),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        cwd=str(root),
        creationflags=NO_WINDOW,
    ))


def claude_cmd(model=HAIKU_MODEL, schema_file=None):
    """Build the argv for a NON-AGENTIC `claude -p` completion.

    Under Codex the same call is `codex exec` with the user's config ignored
    (no plugin, no skills, no connectors in the prompt); `schema_file` is a
    JSON Schema Codex is told the reply must match, and Claude ignores it.

    The prompt is deliberately NOT part of the argv — it is delivered on STDIN
    by run_claude(). See run_claude for why; callers should use run_claude
    rather than wiring up subprocess themselves.
    """
    return host.completion_cmd(model, binary=claude_bin(), schema_file=schema_file)


def run_claude(prompt, model=HAIKU_MODEL, timeout=120, deadline=None,
               schema=None):
    """Run a non-agentic `claude` completion and return the CompletedProcess.

    The prompt is delivered on STDIN, never as an argv `-p <prompt>` argument:
    a multi-line prompt passed through the Windows subprocess argv path is
    truncated at the first newline (the child only ever sees the first line),
    so the model silently classifies nothing — it sees just "Analyze these
    email threads..." with no data and asks for the threads. STDIN delivers the
    whole prompt intact on every OS.

    Runs from a neutral temp cwd so project skills under ./.claude/skills are
    not discovered (belt-and-suspenders with --disable-slash-commands).
    ANTHROPIC_API_KEY is dropped from the child env to force subscription
    routing via the `claude` CLI.

    `encoding="utf-8"` is mandatory, not cosmetic: with bare `text=True`,
    subprocess encodes the stdin write with the OS locale codec (cp1252 on
    Windows), which raises UnicodeEncodeError on any character it can't map —
    zero-width spaces, smart quotes, em-dashes, emoji, all common in real
    email. That crashes the stdin writer thread mid-prompt, so the child gets a
    truncated prompt and that whole batch silently fails to classify (observed:
    230 threads in, only 199 classified — the dropped batches were the ones
    containing non-cp1252 characters). utf-8 encodes every codepoint on every
    OS, for both the stdin write and the stdout read.

    A CLI too old for the requested model fails every call on the machine with
    a 400 ("Claude Code X does not support this model"), so that one case is
    healed rather than returned: `claude update` runs once per process and the
    call is retried on the current CLI (see claude_update).

    Three more things happen here and nowhere else, so every caller gets them:
    the call holds one of the process's model slots (model_slots) for its
    whole duration; a throttle is retried up to len(THROTTLE_BACKOFF) times
    with the pause between attempts growing; a model this account cannot use
    is tried once more on HAIKU_MODEL. A subscription cap raises UsageCap.
    The slot is held through the pauses on purpose: a throttle means the API
    is saturated, and releasing the slot would let another thread add to it.
    Under VAN_GOGH_SERIAL=1 (the control arm) neither the retry nor the
    fallback runs, because the base commit had neither.

    `deadline` (a time.monotonic value) bounds the WHOLE call, retries and
    the wait for a slot included: no spawn outlives it and none starts
    after it. The install's meeting prefetch passes one so a single very
    long recording cannot hold the install; everything else leaves it off.
    """
    # Passed only when a schema was given, so a test stub of the three-argument
    # _spawn keeps working.
    extra = {"schema_file": _schema_file(schema)} if schema else {}
    with _slot():
        result = _spawn(prompt, model, _bounded(timeout, deadline), **extra)
        # The CLI reports this API error on stdout on some paths and stderr on
        # others, and does not always exit nonzero, so both streams are
        # checked; only a failed call is retried.
        healed = claude_update.heal_if_stale(_text(result))
        if healed and result.returncode != 0:
            result = _spawn(prompt, model, _bounded(timeout, deadline), **extra)
        if not serial():
            for pause in THROTTLE_BACKOFF:
                if result.returncode == 0 or not is_throttle(_text(result)):
                    break
                if deadline is not None and _now() + pause >= deadline:
                    break
                STATS["throttle_retries"] += 1
                _sleep(pause)
                result = _spawn(prompt, model, _bounded(timeout, deadline), **extra)
            if (result.returncode != 0 and model != HAIKU_MODEL
                    and is_model_unavailable(_text(result))
                    and (deadline is None or _now() < deadline)):
                STATS["fallbacks"] += 1
                result = _spawn(prompt, HAIKU_MODEL, _bounded(timeout, deadline), **extra)
        if _cap_hit(result):
            raise UsageCap(_text(result).strip()[:500])
    return result


# The clock a deadline is read against; replaceable in tests.
_now = time.monotonic


def _bounded(timeout, deadline):
    """The timeout one spawn really gets when the caller holds a deadline.

    A deadline is a moment on `_now`'s clock past which the caller has
    stopped waiting, however many retries the call has left: each spawn gets
    the time that remains, and a call that reaches the front of the queue
    with none left is a timeout without ever starting the CLI.
    """
    if deadline is None:
        return timeout
    remaining = deadline - _now()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=0)
    return min(timeout, remaining)


# A reply schema whose root is a list is wrapped in an object with one key,
# because the provider's structured output accepts an object root only; the
# reply is unwrapped before the caller sees it (see _spawn).
_WRAP_KEY = "items"


def _schema_file(schema):
    """Write a reply schema beside the work dir once per call; None when absent."""
    if not schema:
        return None
    if schema.get("type") == "array":
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {_WRAP_KEY: schema}, "required": [_WRAP_KEY]}
    fd, path = tempfile.mkstemp(prefix="schema-", suffix=".json", dir=_WORK_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(schema, fh)
    return path


def _unwrap(result, schema_file):
    """A list schema was wrapped on the way in; take the list back out."""
    if not schema_file or not (result.stdout or "").lstrip().startswith("{"):
        return result
    try:
        doc = json.loads(result.stdout)
    except ValueError:
        return result
    if isinstance(doc, dict) and set(doc) == {_WRAP_KEY}:
        result.stdout = json.dumps(doc[_WRAP_KEY])
    return result


def _spawn(prompt, model, timeout, schema_file=None):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    STATS["calls"] += 1
    _t0 = time.monotonic()
    try:
        # A completion never runs a command; under Codex that rule is read
        # off the reply (host.finish), because no flag there removes tools
        # the way `--tools ""` does.
        return _unwrap(host.finish(subprocess.run(
        claude_cmd(model, schema_file),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        cwd=_WORK_DIR,
        creationflags=NO_WINDOW,
        ), forbid_commands=True), schema_file)
    finally:
        STATS["seconds"] += time.monotonic() - _t0
