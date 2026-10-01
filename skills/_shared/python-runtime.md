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

Run this guard once, before the skill's first script invocation. It is a no-op
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
user to run `/van-gogh:install-van-gogh` — Python setup is an install concern.

An *existing* venv keeps itself current: after a plugin update changes
`requirements.txt`, the next script run pip-installs the difference
automatically (`user_state.sync_runtime_deps()`, called at `config_loader`
import). Skills never need a dependency re-sync step.

Note for maintainers: a development checkout of this repo still uses its own
repo-root `.venv` for pytest and direct script runs (see CLAUDE.md); the
stable venv is what installed *skills* invoke.
