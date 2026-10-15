# Windows (PowerShell) command forms

Every command in SKILL.md is shown in both forms, side by side: the
macOS / Linux (bash/zsh) block first, the Windows (PowerShell) block right
under it. There is nothing to translate by hand. The interpreter on Windows
is `"$HOME\.config\van-gogh\venv\Scripts\python.exe"` and the plugin root is
`$env:CLAUDE_PLUGIN_ROOT`; `setup.ps1` at the plugin root builds both before
this skill ever runs, so no winget, Store Python or admin prompt is involved.

One PowerShell detail matters: an answer carrying a notetaker key goes to
`next --answer-stdin` through a here-string (`@' ... '@ |`), never as an
argument, exactly as the bash form uses a quoted heredoc.
