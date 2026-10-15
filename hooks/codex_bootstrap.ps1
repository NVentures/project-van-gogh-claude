# Van Gogh first-run preparation under Codex, PowerShell form, started hidden
# by setup_notice.ps1 -Codex when the plugin was installed from the Codex app
# and nothing has prepared this computer. Runs setup.ps1 -FromHook and leaves
# bootstrap.pid while it runs, bootstrap.out and bootstrap.rc when it is done.
param([string]$State, [string]$Root)
$b = Join-Path $State "setup"
New-Item -ItemType Directory -Force -Path $b | Out-Null
Set-Content -LiteralPath (Join-Path $b "bootstrap.pid") -Value $PID -Encoding ASCII
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "setup.ps1") -Host codex -FromHook *> (Join-Path $b "bootstrap.out")
$rc = $LASTEXITCODE
if ($null -eq $rc) { $rc = 0 }
Set-Content -LiteralPath (Join-Path $b "bootstrap.rc") -Value $rc -Encoding ASCII
Remove-Item -LiteralPath (Join-Path $b "bootstrap.pid") -ErrorAction SilentlyContinue
exit $rc
