# Van Gogh readiness check (Windows). Runs before anything is installed:
#   irm https://raw.githubusercontent.com/NVentures/project-van-gogh-claude/main/check.ps1 | iex
# Prints READY when every check passes, otherwise one plain line per missing
# item. Self-contained on purpose: served from the repo root, works with no
# plugin, no Python and no state dir present, on PowerShell 5.1 and 7.
param([Alias("Host")][string]$Client = "")
$ErrorActionPreference = "Continue"
$script:missing = 0
# Which client: -Host claude|codex, else VAN_GOGH_HOST, else Claude.
$HostName = if ($Client) { $Client.ToLower() } elseif ($env:VAN_GOGH_HOST) { $env:VAN_GOGH_HOST.ToLower() } else { "claude" }
function Miss([string]$text) { $script:missing += 1; Write-Output "MISSING: $text" }
function Ok([string]$text) { Write-Output "ok: $text" }

# Old .NET defaults to TLS 1.0, which every host below refuses.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}

# 1. The client's command, present and signed in.
$claude = if ($HostName -eq "codex") { $null } else { Get-Command claude -ErrorAction SilentlyContinue }
if ($HostName -eq "codex") {
    $codex = $null
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) { $codex = $cmd.Source }
    $standalone = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex\packages\standalone\current\bin\codex.exe"
    if (-not $codex -and (Test-Path -LiteralPath $standalone)) { $codex = $standalone }
    if ($codex) {
        $status = ""
        try { $status = (& $codex login status 2>&1) -join "" } catch { $status = "" }
        if ($status -match "Logged in") {
            Ok "Codex is installed and signed in"
        } else {
            Miss "Codex is installed but not signed in. Open PowerShell, type codex login, press Enter and follow the sign-in."
        }
    } else {
        Miss "The Codex command is not installed. Install it with OpenAI's installer: paste this line into PowerShell and press Enter: irm https://chatgpt.com/codex/install.ps1 | iex   Then open a new PowerShell window and run this check again."
    }
} elseif ($claude) {
    $status = ""
    try { $status = (& claude auth status --json 2>$null) -join "" } catch { $status = "" }
    if ($status -match '"loggedIn":\s*true') {
        Ok "Claude is installed and signed in"
    } else {
        Miss "Claude is installed but not signed in. Open PowerShell, type claude, press Enter and follow the sign-in."
    }
} elseif (Test-Path (Join-Path ([Environment]::GetFolderPath("UserProfile")) ".local\bin\claude.exe")) {
    # The installer's own location (code.claude.com/docs/en/troubleshoot-install,
    # "Verify your PATH"). Installed there but not on this window's PATH is a
    # different fault from not installed; the fix is the documented two
    # commands, joined so they paste as one line.
    Miss ('Claude is installed but this window cannot find it yet. Paste this line into PowerShell and press Enter: $currentPath = [Environment]::GetEnvironmentVariable(''PATH'', ''User''); [Environment]::SetEnvironmentVariable(''PATH'', "$currentPath;$env:USERPROFILE\.local\bin", ''User'')   Then open a new window.')
} else {
    Miss "The Claude command is not installed. Install Claude with Anthropic's official installer: paste this line into PowerShell and press Enter: irm https://claude.ai/install.ps1 | iex   Then open a new PowerShell window and run this check again."
}

# 2. git.
$git = Get-Command git -ErrorAction SilentlyContinue
$gitOk = $false
if ($git) { try { & git --version *> $null; $gitOk = ($LASTEXITCODE -eq 0) } catch { $gitOk = $false } }
if ($gitOk) { Ok "git is installed" }
else { Miss "git is not installed. Install it from git-scm.com/download/win (keep every default), then open a new PowerShell window." }

# 3. The Microsoft Store shortcut must not be posing as Python. (No Python at
#    all is fine: the install brings its own.)
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py -and $py.Source -like "*WindowsApps*") {
    Miss "Windows is showing a Microsoft Store shortcut instead of Python. Open Settings, search for 'App execution aliases' and turn off both python entries, then open a new PowerShell window."
} else {
    Ok "no Microsoft Store shortcut is posing as Python"
}

# 4. The key file, in Downloads, Desktop, the current folder or the private
#    folder a previous attempt moved it to. Renamed variants count.
$home_ = [Environment]::GetFolderPath("UserProfile")
$downloads = Join-Path $home_ "Downloads"
try { $downloads = (New-Object -ComObject Shell.Application).NameSpace("shell:Downloads").Self.Path } catch {}
$state = if ($env:VAN_GOGH_STATE_DIR) { $env:VAN_GOGH_STATE_DIR } else { Join-Path $home_ ".config\van-gogh" }
$places = @($downloads, [Environment]::GetFolderPath("Desktop"), (Get-Location).Path, (Join-Path $state "keyfile"))
$found = $null
foreach ($dir in $places) {
    if ($dir -and (Test-Path $dir)) {
        $hit = Get-ChildItem -Path $dir -Filter "van-gogh-key*.json" -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { $found = $hit.FullName }
    }
}
if ($found) { Ok "key file found ($found)" }
else { Miss "No key file found. Save the van-gogh-key.json file you were sent into your Downloads folder." }

# 5. The five hosts the install talks to, over HTTPS, five seconds each. Any
#    HTTP answer counts as reachable (a 404 still proves the path is open); a
#    timeout or refused connection is the shape of a corporate proxy.
$hosts = @("github.com", "astral.sh", "pypi.org", "accounts.google.com", "login.microsoftonline.com")
if ($HostName -eq "codex") { $hosts += "chatgpt.com" }
foreach ($host_ in $hosts) {
    $reachable = $false
    try {
        Invoke-WebRequest -Uri "https://$host_/" -UseBasicParsing -TimeoutSec 5 -MaximumRedirection 0 -ErrorAction Stop | Out-Null
        $reachable = $true
    } catch {
        if ($_.Exception.Response) { $reachable = $true }
    }
    if ($reachable) { Ok "$host_ is reachable" }
    else { Miss "Cannot reach $host_ over HTTPS. A corporate proxy or firewall is probably blocking it: ask IT to allow $host_, or try from a home network." }
}

if ($script:missing -eq 0) {
    Write-Output "READY"
} else {
    Write-Output "NOT READY: $($script:missing) item(s) to fix, listed above."
}
