"""Shared helper for invoking the `claude` CLI across scripts."""

import atexit
import os
import shutil
import subprocess
import tempfile

import claude_update
from platform_compat import NO_WINDOW, claude_bin

# The default classification model. One home for the id so a bump is one edit.
HAIKU_MODEL = "claude-haiku-4-5-20251001"

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


def run_claude_readonly(prompt, root, model=HAIKU_MODEL, timeout=240):
    """Run an agentic `claude -p` that may READ `root` and do nothing else.

    `root` becomes the working directory, which is what confines the file
    tools: --restricted allows them only inside the working directories, and
    no --add-dir is passed, so the call cannot reach the rest of the disk.

    Returns the CompletedProcess, like run_claude. The caller owns the prompt
    and any cleaning of the reply.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return subprocess.run(
        [claude_bin(), "-p", "--model", model, *_READ_ONLY_FLAGS],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        cwd=str(root),
        creationflags=NO_WINDOW,
    )


def claude_cmd(model=HAIKU_MODEL):
    """Build the argv for a NON-AGENTIC `claude -p` completion.

    The prompt is deliberately NOT part of the argv — it is delivered on STDIN
    by run_claude(). See run_claude for why; callers should use run_claude
    rather than wiring up subprocess themselves.
    """
    return [claude_bin(), "-p", "--model", model, *_NON_AGENTIC_FLAGS]


def run_claude(prompt, model=HAIKU_MODEL, timeout=120):
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
    """
    result = _spawn(prompt, model, timeout)
    # The CLI reports this API error on stdout on some paths and stderr on
    # others, and does not always exit nonzero, so both streams are checked;
    # only a failed call is retried.
    healed = claude_update.heal_if_stale((result.stdout or "") + (result.stderr or ""))
    if healed and result.returncode != 0:
        result = _spawn(prompt, model, timeout)
    return result


def _spawn(prompt, model, timeout):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return subprocess.run(
        claude_cmd(model),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        cwd=_WORK_DIR,
        creationflags=NO_WINDOW,
    )
