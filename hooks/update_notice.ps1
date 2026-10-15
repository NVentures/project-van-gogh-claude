# Van Gogh session-start hook, PowerShell form (hooks/codex-hooks.json,
# commandWindows): ask about a newer Van Gogh at the start of a chat. Same
# behaviour as update_notice.sh -Codex: one live check with a short timeout, silent when nothing
# is pending or there is no venv yet.

param([switch]$Codex)

if ($env:VAN_GOGH_UNATTENDED -or $env:VAN_GOGH_SCHEDULED) { exit 0 }
if ($Codex) { $env:VAN_GOGH_HOST = "codex" }

$state = if ($env:VAN_GOGH_STATE_DIR) { $env:VAN_GOGH_STATE_DIR } else { Join-Path $HOME ".config\van-gogh" }
$py = ""
foreach ($candidate in @((Join-Path $state "venv\Scripts\python.exe"), (Join-Path $state "venv\bin\python"))) {
  if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
}
if (-not $py) { exit 0 }

$root = $env:CLAUDE_PLUGIN_ROOT
if (-not $root) { $root = Split-Path -Parent $PSScriptRoot }
$script = Join-Path $root "app\plugin_update.py"
if (-not (Test-Path -LiteralPath $script)) { exit 0 }

$text = ""
try { $text = (& $py $script --session-notice 2>$null | Out-String).Trim() } catch { $text = "" }
if ($text) { Write-Output $text }
exit 0
