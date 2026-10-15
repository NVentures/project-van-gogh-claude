# Python runtime (shared procedure)

Every Van Gogh script runs through the shared per-user venv at
`~/.config/van-gogh/venv`. It lives **outside** the plugin cache
(`${CLAUDE_PLUGIN_ROOT}`) so a plugin update or reinstall — which re-clones the
cache directory — never deletes it. The *code* still comes from the plugin
cache: only the interpreter is stable.

Interpreter paths used by every skill command:

- macOS / Linux (bash/zsh): `"$HOME/.config/van-gogh/venv/bin/python"`
- Windows (PowerShell): `"$HOME\.config\van-gogh\venv\Scripts\python.exe"`

## Ensure the venv exists (start of skill)

Under Codex, before anything else: when `$CLAUDE_PLUGIN_ROOT` is empty in a
command you run (`echo "$CLAUDE_PLUGIN_ROOT"`, or `echo $env:CLAUDE_PLUGIN_ROOT`
on Windows), this chat started before Van Gogh's settings were in place (the
plugin was installed from the Codex app's plugin screen, and Codex reads the
settings only when a chat starts). Run this one command, and nothing else, in
this chat. It decides everything itself: it prepares the computer when nothing
has (about two minutes; it writes Codex's settings and Van Gogh's own folder,
so approve it when Codex asks), and refuses on a computer that runs Van Gogh
under Claude. Then print its one line exactly as given and stop.

macOS / Linux (bash/zsh):
```bash
bash "$(ls -d "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/van-gogh/van-gogh/*/ | sort -V | tail -n 1)hooks/setup_notice.sh" --codex --first-chat
```

Windows (PowerShell):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ((Get-ChildItem "$(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" })\plugins\cache\van-gogh\van-gogh" -Directory | Sort-Object { [version]$_.Name } | Select-Object -Last 1).FullName + "\hooks\setup_notice.ps1") -Codex -FirstChat
```

Run this guard once, before the skill's first script invocation. (The install
skill is the one exception to this guard and to the setup line below: the
README's install line has already built the venv and ticked the setup record,
so that skill runs `setup_flow.py` and the wait it asks for, and nothing from
this page.) It is a no-op
when the venv is healthy; if the venv is missing (fresh machine, or an install
that predates the stable-venv layout), it creates it from the plugin's
`requirements.txt`.

macOS / Linux (bash/zsh):

```bash
[ -x "$HOME/.config/van-gogh/venv/bin/python" ] || { python3 -m venv "$HOME/.config/van-gogh/venv" && "$HOME/.config/van-gogh/venv/bin/python" -m pip install -q -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"; }
```

Windows (PowerShell):

```powershell
if (-not (Test-Path "$HOME\.config\van-gogh\venv\Scripts\python.exe")) { python -m venv "$HOME\.config\van-gogh\venv"; & "$HOME\.config\van-gogh\venv\Scripts\python.exe" -m pip install -q -r "$env:CLAUDE_PLUGIN_ROOT\requirements.txt" }
```

If the guard itself fails (no `python3`/`python` on PATH), stop and tell the
user to run the README's install line in Terminal again (it brings its own
private copy of Python when none is installed); Python setup is that
line's job.

## The setup line (start of skill, right after the guard)

Every skill runs this once, after the guard and before anything else it
prints. It answers one question: is this machine's Van Gogh install finished?
While `~/.config/van-gogh/setup/checklist.json` exists it is not, and the
command prints one line (how many steps are left, the one next action, and
the resume command); when the install is finished it prints nothing.

macOS / Linux (bash/zsh):

```bash
"$HOME/.config/van-gogh/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/app/setup_state.py" line
```

Windows (PowerShell):

```powershell
& "$HOME\.config\van-gogh\venv\Scripts\python.exe" "$env:CLAUDE_PLUGIN_ROOT\app\setup_state.py" line
```

Non-empty output: print it verbatim as the first line of your reply, on its
own, then carry on with the skill. Do not reword it, add to it, or turn it
into a section; the words are chosen so every surface says the same thing
(the session-start hook and the briefing footer print this same line, see
`front-page.md` step 14). Empty output, or a command that fails: say nothing
about setup and carry on. This is informational only: the skill the user asked
for still runs, and nothing here asks a question or sends anything.

An *existing* venv keeps itself current: after a plugin update changes
`requirements.txt`, the next script run pip-installs the difference
automatically (`user_state.sync_runtime_deps()`, called at `config_loader`
import). Skills never need a dependency re-sync step.

Note for maintainers: a development checkout of this repo still uses its own
repo-root `.venv` for pytest and direct script runs (see CLAUDE.md); the
stable venv is what installed *skills* invoke.

## Two clients: Claude Code and Codex

Van Gogh runs in Claude Code and in OpenAI Codex (the Codex app, part of
the ChatGPT app on a Mac, and the `codex` command). Every skill is written once, for Claude's tools, and the
same text runs under Codex with the readings below. The commands, the venv
and `${CLAUDE_PLUGIN_ROOT}` are the same (Codex sets that variable from its
own config, which the install wrote and every plugin update rewrites). You are in
Codex when the session names skills as `$van-gogh:<name>` or when
`CODEX_THREAD_ID` is in a command's environment.

| Written for Claude | Under Codex |
|---|---|
| `/van-gogh:<name>` shown to the person | spell it `$van-gogh:<name>`; that is how a Codex session names a skill |
| AskUserQuestion tool | ask the same question as plain text, the options numbered, and wait for the reply |
| Skill tool, SlashCommand tool | mention `$van-gogh:<name>` in your own turn and follow that skill's SKILL.md |
| Agent tool, Task tool | do the work yourself, here, one entry after another: when the prompt names a skill (`/van-gogh:week`), follow that skill; when it names one of the plugin's agents, read `agents/<name>.md` under the plugin root and follow it |
| TodoWrite tool | keep the list in your reply |
| Artifact tool (a briefing's web page) | skip the publish step and say in one line that the page needs Claude and was not updated this run; everything else in the skill proceeds |
| The finance brief's QuickBooks read, the chat connector fallback for mail, a CRM lookup (claude.ai connectors) | the script answers with one line saying it needs Claude; print that line, skip that step, and carry on with the rest of the skill |
| Read, Write, Edit, Grep, Glob, Bash tools | the same actions with what Codex gives you (shell commands and file edits; when the content is a key or a token, write it with a file edit, never on a command line) |
| WebSearch, WebFetch tools | Codex's web search when it is on; otherwise say the lookup was skipped |
| "In Claude, type ..." or "Claude" in a line you write yourself | say Codex (a line a script prints is shown as it is; the scripts already know which client they run under) |
