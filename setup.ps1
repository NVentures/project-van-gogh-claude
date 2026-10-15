# Van Gogh setup (Windows). Run from the marketplace clone, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File "$HOME\.claude\plugins\marketplaces\van-gogh\setup.ps1"
# Flags: -PrepareOnly (stop before opening the client), -Check (readiness
# only), -Host claude|codex (which client this computer uses; kept in the
# state dir and read by every later run), -FromHook (the Codex session-start
# hook preparing a computer whose plugin was installed from the Codex app:
# prepare only, and the in-session and Command Prompt refusals do not apply).
#
# Two lanes at once: the plugin install, and the Python environment (an
# existing Python 3.10+ on PATH, else a private uv under the state dir with a
# managed Python). Then app\setup_flow.py prepare, then Claude opens on the
# install skill. No winget, choco, RunAs or admin dialog anywhere. Works on
# PowerShell 5.1 and 7.
param(
    [switch]$PrepareOnly,
    [switch]$Check,
    [switch]$FromHook,
    # -Host on the command line; the variable is $Client because $Host is
    # PowerShell's own read-only automatic variable.
    [Alias("Host")][string]$Client = ""
)
# Continue, not Stop: on PowerShell 5.1 a native command that merely writes
# a warning to stderr (pip does) throws under Stop. Every exit code is read.
$ErrorActionPreference = "Continue"
if ($FromHook) { $PrepareOnly = [switch]$true }

$Root = $PSScriptRoot
$S = if ($env:VAN_GOGH_STATE_DIR) { $env:VAN_GOGH_STATE_DIR } else { Join-Path $HOME ".config\van-gogh" }
$Logs = if ($env:VAN_GOGH_SETUP_LOGS) { $env:VAN_GOGH_SETUP_LOGS } else {
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
    Join-Path $base "VanGogh\logs"
}
$Log = Join-Path $Logs "setup-bootstrap.log"
$Vpy = Join-Path $S "venv\Scripts\python.exe"

# The client: the flag, else the environment, else what an earlier run kept,
# else Claude, which is what every install before 0.89.0 was.
$HostName = $Client
if (-not $HostName -and $env:VAN_GOGH_HOST) { $HostName = $env:VAN_GOGH_HOST }
if (-not $HostName -and (Test-Path (Join-Path $S "host"))) {
    try { $HostName = (Get-Content -LiteralPath (Join-Path $S "host") -Raw).Trim() } catch { $HostName = "" }
}
if (-not $HostName) { $HostName = "claude" }
$HostName = $HostName.ToLower()
if ($HostName -notin @("claude", "codex")) { Write-Output "Unknown host: $HostName (claude or codex)"; exit 2 }
$env:VAN_GOGH_HOST = $HostName
$HostWord = if ($HostName -eq "codex") { "Codex" } else { "Claude" }

if (-not $FromHook -and ($env:CLAUDECODE -or $env:CLAUDE_CODE_ENTRYPOINT -or $env:CODEX_THREAD_ID)) {
    $inside = if ($env:CODEX_THREAD_ID) { "Codex" } else { "Claude" }
    Write-Output "This line goes in PowerShell, not in $inside. Press the Windows key, type PowerShell, press Enter, then paste it there."
    exit 2
}

# Where a Codex binary lives when it is not on PATH: the installer's place.
function Find-Codex {
    if ($env:VAN_GOGH_CODEX_BIN -and (Test-Path -LiteralPath $env:VAN_GOGH_CODEX_BIN)) { return $env:VAN_GOGH_CODEX_BIN }
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $standalone = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex\packages\standalone\current\bin\codex.exe"
    if (Test-Path -LiteralPath $standalone) { return $standalone }
    return $null
}
# Pasted into Command Prompt: the parent of this PowerShell is cmd.exe.
try {
    $parentId = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId = $parentId").Name
    if (-not $FromHook -and $parent -and $parent -ieq "cmd.exe") {
        Write-Output "This line goes in PowerShell, not in Command Prompt. Press the Windows key, type PowerShell, press Enter, then paste it there."
        exit 2
    }
} catch {}

if ($Check) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "check.ps1") -Host $HostName
    exit $LASTEXITCODE
}

function Fail([string]$text) {
    Write-Output $text
    Write-Output "Details are in $Log"
    exit 1
}
function Log([string]$text) { Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $text) }
function Run([string]$exe, [string[]]$argv) {
    # One child, output to the log, exit code returned. Never throws.
    try {
        $out = & $exe @argv 2>&1
        if ($out) { Add-Content -Path $Log -Value ($out | Out-String) }
        return $LASTEXITCODE
    } catch {
        Add-Content -Path $Log -Value $_.ToString()
        return 1
    }
}

# From the hook or a chat, never take over a computer that already runs Van
# Gogh under Claude (a venv whose host record is missing or says claude).
if ($FromHook -and (Test-Path -LiteralPath $Vpy)) {
    $prior = ""
    if (Test-Path -LiteralPath (Join-Path $S "host")) { try { $prior = (Get-Content -LiteralPath (Join-Path $S "host") -Raw).Trim() } catch { $prior = "" } }
    if ($prior -ne "codex") {
        Write-Output "This computer already runs Van Gogh under Claude, so nothing was changed. To move it to Codex, paste the README's Codex install line in PowerShell."
        exit 3
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $S "setup"), (Join-Path $S "tools"), $Logs | Out-Null
Add-Content -Path $Log -Value ("`n==== setup.ps1 {0} (host {1}) ====" -f (Get-Date), $HostName)
# The host record, first: the hooks, the readiness line and every script
# read it, and config.json does not exist until the vault step.
[IO.File]::WriteAllText((Join-Path $S "host"), "$HostName`n")

# The clock starts here. The run id names this attempt in every ledger row.
$hex = -join ((1..4) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
$RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $hex
[IO.File]::WriteAllText((Join-Path $S "setup\run-id"), "$RunId`n")
$ts = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
[IO.File]::AppendAllText((Join-Path $S "setup\timing.jsonl"),
    ('{{"ts": "{0}", "run_id": "{1}", "event": "start", "class": "machine"}}' -f $ts, $RunId) + "`n")
Write-Output "Setting up Van Gogh (run $RunId)..."

# ---- lane 1: the plugin, in a background job ---------------------------------
# Under Codex the marketplace is added here too (a Codex install has no
# marketplace clone to run from). VAN_GOGH_MARKETPLACE names a local checkout
# for a rehearsal; clients get the public repository.
$Market = if ($env:VAN_GOGH_MARKETPLACE) { $env:VAN_GOGH_MARKETPLACE } else { "https://github.com/NVentures/project-van-gogh-claude.git" }
$CodexBin = if ($HostName -eq "codex") { Find-Codex } else { $null }
# Lane 1 runs as a plain function, one after the other with lane 2: a
# Start-Job here returned nothing under Windows PowerShell 5.1 when run from
# the README line ("The Persistence Path does not exist" on Receive-Job).
function Plugin-Lane {
    param($log, $hostName, $codex, $market)
    if ($hostName -eq "codex") {
        if (-not $codex) { return @{ rc = 0; line = "The Codex command was not found, so the plugin is not installed yet." } }
        $listed = ""
        try { $listed = (& $codex plugin list --json 2>$null) -join "" } catch {}
        if ($listed -match '"pluginId":\s*"van-gogh@van-gogh"[^}]*"installed":\s*true') { return @{ rc = 0; line = "Plugin already installed." } }
        $out = & $codex plugin marketplace add $market 2>&1
        Add-Content -Path $log -Value ($out | Out-String)
        $out = & $codex plugin add van-gogh@van-gogh 2>&1
        Add-Content -Path $log -Value ($out | Out-String)
        if ($LASTEXITCODE -eq 0) { return @{ rc = 0; line = "Plugin installed." } }
        return @{ rc = 1; line = "The plugin could not be installed. Run this first, then try again: codex plugin marketplace add NVentures/project-van-gogh-claude" }
    }
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) { return @{ rc = 0; line = "The Claude command was not found, so the plugin is not installed yet." } }
    $listed = ""
    try { $listed = (& claude plugin list --json 2>$null) -join "" } catch {}
    if ($listed -match '"id":\s*"van-gogh@van-gogh"') { return @{ rc = 0; line = "Plugin already installed." } }
    $out = & claude plugin install van-gogh@van-gogh 2>&1
    Add-Content -Path $log -Value ($out | Out-String)
    if ($LASTEXITCODE -eq 0) { return @{ rc = 0; line = "Plugin installed." } }
    return @{ rc = 1; line = "The plugin could not be installed. Run this first, then try again: claude plugin marketplace add https://github.com/NVentures/project-van-gogh-claude.git" }
}

# ---- lane 2: the Python environment ------------------------------------------
function Venv-Ok {
    if (-not (Test-Path $Vpy)) { return $false }
    & $Vpy -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# A Python 3.10+ already on PATH, never the Microsoft Store shortcut.
function Find-Python {
    foreach ($name in @("python", "python3", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -like "*WindowsApps*") { continue }
        $argv = @("-c", "import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        if ($name -eq "py") { $argv = @("-3") + $argv }
        try {
            & $cmd.Source @argv 2>$null
            if ($LASTEXITCODE -eq 0) {
                if ($name -eq "py") { return @($cmd.Source, "-3") }
                return @($cmd.Source)
            }
        } catch {}
    }
    return
}

# No usable Python: a private uv under the state dir brings a managed one.
function Uv-Python-Venv {
    $uv = Join-Path $S "tools\uv.exe"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $S "tools\python"
    $env:UV_CACHE_DIR = Join-Path $S "tools\cache"
    $env:UV_PYTHON_BIN_DIR = Join-Path $S "tools\bin"
    $env:UV_NO_MODIFY_PATH = "1"
    if (-not (Test-Path $uv)) {
        Write-Output "Downloading a private Python installer..."
        try {
            $env:UV_UNMANAGED_INSTALL = Join-Path $S "tools"
            $installer = Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -TimeoutSec 120
            $installed = Invoke-Expression $installer 2>&1 | Out-String
            Add-Content -Path $Log -Value $installed
        } catch { Add-Content -Path $Log -Value $_.ToString() }
        if (-not (Test-Path $uv)) {
            Fail "Could not download the Python installer from astral.sh. A corporate proxy may be blocking it: run setup.ps1 -Check and try again."
        }
    }
    Write-Output "Installing Python 3.12 (private copy, a minute or two)..."
    if ((Run $uv @("python", "install", "3.12")) -ne 0) {
        Fail "Could not download Python 3.12. Check the connection (github.com must be reachable) and run again."
    }
    if (Test-Path (Join-Path $S "venv")) { Remove-Item -Recurse -Force (Join-Path $S "venv") }
    if ((Run $uv @("venv", (Join-Path $S "venv"), "--python", "3.12", "--seed")) -ne 0) {
        Fail "Could not create the Python environment under the state dir."
    }
}

function Python-Lane {
    if (Venv-Ok) {
        Log "lane 2: venv present"
    } else {
        $py = @(Find-Python)
        if ($py.Count -gt 0) {
            Log ("lane 2: using " + ($py -join " "))
            if (Test-Path (Join-Path $S "venv")) { Remove-Item -Recurse -Force (Join-Path $S "venv") }
            $exe = $py[0]
            $rest = @($py | Select-Object -Skip 1) + @("-m", "venv", (Join-Path $S "venv"))
            if ((Run $exe $rest) -ne 0) {
                Log "lane 2: venv creation failed, falling back to uv"
                Uv-Python-Venv
            }
        } else {
            Log "lane 2: no Python 3.10+ on PATH, using uv"
            Uv-Python-Venv
        }
    }
    if ((Run $Vpy @("-m", "pip", "install", "-q", "-r", (Join-Path $Root "requirements.txt"))) -ne 0) {
        Fail "Could not install the Python packages (pypi.org must be reachable)."
    }
    # Native hash: Windows PowerShell 5.1 strips the inner quotes of a python
    # one-liner, which then fails with NameError on `rb`.
    $digest = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $Root "requirements.txt")).Hash.ToLower()
    [IO.File]::WriteAllText((Join-Path $S "requirements.sha256"), "$digest`n")
    Write-Output "Python ready."
}

Write-Output "Installing the plugin and preparing Python..."
$result = Plugin-Lane $Log $HostName $CodexBin $Market
if (-not $result) { $result = @{ rc = 1; line = "The plugin step did not report back." } }
Python-Lane
Write-Output $result.line
if ($result.rc -ne 0) { Fail "Setup stopped at the plugin step." }

# ---- prepare: the orchestrator ticks the machine boxes -------------------------
$flow = Join-Path $Root "app\setup_flow.py"
if (Test-Path $flow) {
    if ((Run $Vpy @($flow, "prepare", "--run-id", $RunId)) -ne 0) {
        Fail "Setup could not prepare your install (the key file or plugin step did not finish)."
    }
    Write-Output "Prepared."
} else {
    Log "prepare: app\setup_flow.py not in this build, skipped"
}

if ($PrepareOnly) {
    Write-Output "Prepared only, as asked. $HostWord was not opened."
    exit 0
}

# ---- launch ----------------------------------------------------------------------
# Vincent, the chief of staff, runs setup. He is handed the situation and the
# setup rules in a file, so his first turn fetches nothing and no command he
# runs first needs a permission question. If that file cannot be written, the
# install skill runs the same loop without him.
$SetupMessage = "Set up Van Gogh on this computer."
$SetupDir = Join-Path $S "setup"
$Brief = Join-Path $SetupDir "vincent-brief.txt"
$UseVincent = $false
$vincentPy = Join-Path $Root "app\vincent.py"
if ((Test-Path (Join-Path $Root "agents\vincent.md")) -and (Test-Path $vincentPy)) {
    New-Item -ItemType Directory -Force -Path $SetupDir | Out-Null
    try {
        $text = (& $Vpy $vincentPy --setup-brief 2>$null) -join "`n"
        if ($LASTEXITCODE -eq 0 -and $text.Trim()) {
            [System.IO.File]::WriteAllText($Brief, $text, (New-Object System.Text.UTF8Encoding($false)))
            $UseVincent = $true
        }
    } catch {}
    if (-not $UseVincent) { Log "launch: the brief for Vincent could not be written, opening the install skill" }
}

# The message comes after `--`: placed straight after --allowedTools it is read
# as one more tool name and Claude opens with nothing asked.
if ($UseVincent) {
    $launch = @("--agent", "van-gogh:vincent", "--append-system-prompt-file", $Brief, "--add-dir", $SetupDir,
                "--permission-mode", "acceptEdits", "--allowedTools", "Bash($Vpy *)", "Agent", "--", $SetupMessage)
    $shown = "  claude --agent van-gogh:vincent --append-system-prompt-file '{0}' --add-dir '{1}' --permission-mode acceptEdits --allowedTools 'Bash({2} *)' 'Agent' -- `"{3}`"" -f $Brief, $SetupDir, $Vpy, $SetupMessage
} else {
    $launch = @("--add-dir", $SetupDir, "--permission-mode", "acceptEdits", "--allowedTools", "Bash($Vpy *)", "Agent", "--", "/van-gogh:install-van-gogh")
    $shown = "  claude --add-dir '{0}' --permission-mode acceptEdits --allowedTools 'Bash({1} *)' 'Agent' -- `"/van-gogh:install-van-gogh`"" -f $SetupDir, $Vpy
}
if ($HostName -eq "codex") {
    # Codex has no agent flag and no system-prompt file: the brief (which
    # under Codex opens with Vincent's own rules) is read as the first thing
    # done. The state dir is the working directory, so setup's own files are
    # inside the sandbox; a write anywhere else asks once (-a on-request).
    $message = if ($UseVincent) { "Read the file $Brief in full and follow it. $SetupMessage" } else { '$van-gogh:install-van-gogh' }
    $codexLaunch = @("-C", $S, "-s", "workspace-write", "-a", "on-request", "-c", "sandbox_workspace_write.network_access=true", $message)
    $codex = Find-Codex
    if (-not $codex) {
        Write-Output "The Codex command was not found. Install Codex, open a new PowerShell window, then paste:"
        Write-Output ("  codex -C '{0}' -s workspace-write -a on-request -c sandbox_workspace_write.network_access=true `"{1}`"" -f $S, $message)
        exit 0
    }
    Write-Output "Opening Codex..."
    & $codex @codexLaunch
    exit $LASTEXITCODE
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Output "The Claude command was not found. Install Claude Code, open a new PowerShell window, then paste:"
    Write-Output $shown
    exit 0
}
Write-Output "Opening Claude..."
& claude @launch
exit $LASTEXITCODE
