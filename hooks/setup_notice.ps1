# Van Gogh session-start hook, PowerShell form: one line while an install is
# unfinished. Same behaviour as setup_notice.sh. Not referenced from
# hooks.json: the plugin hooks schema has no per-platform condition, so the
# bash form is what Claude Code runs (under Git Bash on Windows). A Windows
# machine without Git Bash gets the in-command line (front-page.md step 14)
# instead; this file exists for a client who registers the hook by hand in
# their own settings with "shell": "powershell".

param([switch]$Codex, [switch]$FirstChat)

if ($env:VAN_GOGH_UNATTENDED -or $env:VAN_GOGH_SCHEDULED) { exit 0 }

# -Codex: the Codex form (hooks/codex-hooks.json, commandWindows): a plain
# line, spelled the way Codex names a skill, handed to the model as is.
$hostWord = "Claude"
$installCmd = "/van-gogh:install-van-gogh"
if ($Codex) { $env:VAN_GOGH_HOST = "codex" }
elseif (-not $env:VAN_GOGH_HOST) {
  $stateEarly = if ($env:VAN_GOGH_STATE_DIR) { $env:VAN_GOGH_STATE_DIR } else { Join-Path $HOME ".config\van-gogh" }
  $hostFile = Join-Path $stateEarly "host"
  if (Test-Path -LiteralPath $hostFile) { try { $env:VAN_GOGH_HOST = (Get-Content -LiteralPath $hostFile -Raw).Trim() } catch {} }
}
if ($env:VAN_GOGH_HOST -eq "codex") { $hostWord = "Codex"; $installCmd = '$van-gogh:install-van-gogh' }

$state = if ($env:VAN_GOGH_STATE_DIR) { $env:VAN_GOGH_STATE_DIR } else { Join-Path $HOME ".config\van-gogh" }
$check = Join-Path $state "setup\checklist.json"

# First run under Codex with the plugin installed from the Codex app's plugin
# screen: the same background preparation as setup_notice.sh (setup.ps1
# -FromHook), on a computer with nothing on it or one this preparation
# started and did not finish. An install before 0.89.0 is never touched.
if ($Codex) {
  $b = Join-Path $state "setup"
  $hostRec = ""
  $hostPath = Join-Path $state "host"
  if (Test-Path -LiteralPath $hostPath) { try { $hostRec = (Get-Content -LiteralPath $hostPath -Raw).Trim() } catch {} }
  $venvOk = (Test-Path -LiteralPath (Join-Path $state "venv\Scripts\python.exe")) -or (Test-Path -LiteralPath (Join-Path $state "venv\bin\python"))
  $prep = ((-not $hostRec) -and (-not $venvOk) -and (-not (Test-Path -LiteralPath $check))) -or (($hostRec -eq "codex") -and (-not $venvOk))
  $ready = "Van Gogh is ready on this computer. Open a new chat and type $installCmd (Codex reads Van Gogh's settings only when a chat starts)."
  if ($FirstChat -and -not $prep) {
    if ($venvOk -and ($hostRec -ne "codex")) {
      Write-Output "This computer already runs Van Gogh under Claude, so nothing was changed. To move it to Codex, paste the README's Codex install line in PowerShell."
    } else { Write-Output $ready }
    exit 0
  }
  if ($prep) {
    $when = "When it is done, open a new chat and type $installCmd (Codex reads Van Gogh's settings only when a chat starts)."
    $pidPath = Join-Path $b "bootstrap.pid"
    $bpid = 0
    if (Test-Path -LiteralPath $pidPath) { try { $bpid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim() } catch { $bpid = 0 } }
    if ($bpid -and (Get-Process -Id $bpid -ErrorAction SilentlyContinue)) {
      Write-Output "Van Gogh is still getting this computer ready (Python and its settings, a minute or two). $when"
      exit 0
    }
    $broot = $env:CLAUDE_PLUGIN_ROOT
    if (-not $broot) { $broot = Split-Path -Parent $PSScriptRoot }
    $wrapper = Join-Path $broot "hooks\codex_bootstrap.ps1"
    if (-not ((Test-Path -LiteralPath (Join-Path $broot "setup.ps1")) -and (Test-Path -LiteralPath $wrapper))) { exit 0 }
    $last = ""
    $rcPath = Join-Path $b "bootstrap.rc"
    if ((Test-Path -LiteralPath $rcPath) -and ((Get-Content -LiteralPath $rcPath -Raw).Trim() -ne "0")) {
      try { $last = (Get-Content -LiteralPath (Join-Path $b "bootstrap.out") | Where-Object { $_.Trim() } | Select-Object -Last 1) } catch { $last = "" }
    }
    New-Item -ItemType Directory -Force -Path $b | Out-Null
    Remove-Item -LiteralPath $rcPath -ErrorAction SilentlyContinue
    if ($FirstChat) {
      & powershell -NoProfile -ExecutionPolicy Bypass -File $wrapper -State $state -Root $broot *> $null
      if ($LASTEXITCODE -eq 0) { Write-Output $ready } else {
        $last = ""
        try { $last = (Get-Content -LiteralPath (Join-Path $b "bootstrap.out") | Where-Object { $_.Trim() } | Select-Object -Last 1) } catch { $last = "" }
        if (-not $last) { $last = "no reason was printed" }
        Write-Output "Van Gogh could not get this computer ready: $last. Open a new chat and type $installCmd to try again."
      }
      exit 0
    }
    try {
      Start-Process -FilePath "powershell" -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $wrapper + '"'), "-State", ('"' + $state + '"'), "-Root", ('"' + $broot + '"')) | Out-Null
    } catch { exit 0 }
    $msg = "Van Gogh is getting this computer ready in the background (Python and its settings, about two minutes). $when"
    if ($last) { $msg = "Van Gogh's last try at getting ready stopped with: $last It is trying again now. $msg" }
    Write-Output $msg
    exit 0
  }
}

if (-not (Test-Path -LiteralPath $check)) { exit 0 }

$root = $env:CLAUDE_PLUGIN_ROOT
if (-not $root) { $root = Split-Path -Parent $PSScriptRoot }

$py = ""
foreach ($candidate in @((Join-Path $state "venv\Scripts\python.exe"), (Join-Path $state "venv\bin\python"))) {
  if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
}

$line = ""
if ($py -and (Test-Path -LiteralPath (Join-Path $root "app\setup_state.py"))) {
  try { $line = (& $py (Join-Path $root "app\setup_state.py") line 2>$null | Out-String).Trim() } catch { $line = "" }
}

if (-not $line) {
  $actions = @{
    plugin = "install the plugin"; python = "set up Python"; credentials = "install your key file"
    accounts = "sign in to your accounts"; vault = "confirm your settings"
    jobs = "schedule your briefings"; deals = "tick your real deals"
    meetings = "file your meetings"; briefings = "see your first briefings"
  }
  try {
    $doc = Get-Content -LiteralPath $check -Raw -Encoding UTF8 | ConvertFrom-Json
    $open = @($doc.boxes | Where-Object { $_.status -notin @("done", "skipped", "not_applicable") })
    if ($open.Count -eq 0) { exit 0 }
    $first = $open[0].key
    $action = if ($actions.ContainsKey($first)) { $actions[$first] } else { "finish setting up" }
    if ($first -in @("plugin", "python")) {
      $line = "Van Gogh setup: $($open.Count) of $($doc.boxes.Count) install steps left. Next: paste your checklist's setup line in Terminal (PowerShell on Windows)."
    } else {
      $line = "Van Gogh setup: $($open.Count) of $($doc.boxes.Count) install steps left. Next: $action. In $hostWord, type $installCmd"
    }
  } catch { exit 0 }
}

if (-not $line) { exit 0 }
if ($Codex) {
  Write-Output $line
} else {
  $escaped = $line.Replace('\', '\\').Replace('"', '\"')
  Write-Output ('{"systemMessage": "' + $escaped + '"}')
}

if ($py -and (Test-Path -LiteralPath (Join-Path $root "app\setup_nudge.py"))) {
  $logs = if ($env:VAN_GOGH_SETUP_LOGS) { $env:VAN_GOGH_SETUP_LOGS } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "VanGogh\logs" } else { Join-Path $HOME "Library\Logs\VanGogh" }
  New-Item -ItemType Directory -Force -Path $logs | Out-Null
  try {
    Start-Process -FilePath $py -ArgumentList @((Join-Path $root "app\setup_nudge.py"), "run", "--maybe") `
      -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "setup-nudge.log") `
      -RedirectStandardError (Join-Path $logs "setup-nudge.err") | Out-Null
  } catch { }
}
exit 0
