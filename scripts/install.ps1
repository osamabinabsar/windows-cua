<#
.SYNOPSIS
    Install and register codex-cua-mcp on a Windows machine.

.DESCRIPTION
    Sets up the MCP server on a target PC and registers it with one or more
    agent harnesses. Intended for the user's own machines.

    Requirements on the target PC:
      * Python 3.11+ (or uv, which can fetch one)
      * Codex CLI installed (supplies codex-computer-use.exe)

.PARAMETER Harness
    Which harness to register with. One of: claude, codex, opencode, all.

.PARAMETER RepoPath
    Path to a checkout of this repository on the target machine.

.PARAMETER User
    Local Windows account to install for. Defaults to the current user.

.EXAMPLE
    .\scripts\install.ps1 -Harness all -RepoPath C:\code\windows-cua
#>
[CmdletBinding()]
param(
    [ValidateSet('claude', 'codex', 'opencode', 'all')]
    [string]$Harness = 'claude',

    [string]$RepoPath = (Split-Path -Parent (Split-Path -Parent $PSCommandPath)),

    [string]$User = $env:USERNAME
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    !  $Message" -ForegroundColor Yellow
}

Write-Step "codex-cua-mcp installer"
Write-Host "    repo : $RepoPath"
Write-Host "    user : $User"
Write-Host "    harness: $Harness"

# --------------------------------------------------------------------------
# 1. Tooling
# --------------------------------------------------------------------------

Write-Step "Checking prerequisites"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    Write-Ok "uv found: $($uv.Source)"
} else {
    Write-Warn "uv not found. Install it with:  winget install --id=astral-sh.uv"
    Write-Warn "  (or: powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\")"
    exit 1
}

$pythonOk = $false
try {
    $ver = & uv python list 2>$null | Select-String '3\.1[1-9]|3\.2[0-9]' | Select-Object -First 1
    if ($ver) { $pythonOk = $true }
} catch { }
if ($pythonOk) {
    Write-Ok "a suitable Python is available to uv"
} else {
    Write-Warn "uv will download a Python 3.14 on first run"
}

# --------------------------------------------------------------------------
# 2. The helper binary
# --------------------------------------------------------------------------

Write-Step "Locating codex-computer-use.exe"

$helperCandidates = @()
$localAppData = $env:LOCALAPPDATA
if ($localAppData) {
    $helperCandidates += Get-ChildItem -Path "$localAppData\OpenAI\Codex\runtimes\cua_node" `
        -Filter "codex-computer-use.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}

$helper = $helperCandidates | Sort-Object { (Get-Item $_).LastWriteTime } -Descending | Select-Object -First 1

if ($helper) {
    Write-Ok "helper: $helper"
} else {
    Write-Warn "codex-computer-use.exe not found."
    Write-Warn "Install Codex CLI on this machine:  npm install -g @openai/codex"
    Write-Warn "Then re-run this script. If you install it elsewhere, set"
    Write-Warn "CODEX_COMPUTER_USE_BIN to the executable's full path."
}

# The codex CLI is used for the approval handshake.
$codexCli = $null
if ($localAppData) {
    $codexCli = Get-ChildItem -Path "$localAppData\OpenAI\Codex\bin" -Filter "codex.exe" `
        -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $codexCli) {
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) { $codexCli = $cmd.Source }
}
if ($codexCli) {
    Write-Ok "codex cli: $codexCli"
} else {
    Write-Warn "codex CLI not found -- the approval handshake may not work."
    Write-Warn "Install it with:  npm install -g @openai/codex"
}

# --------------------------------------------------------------------------
# 3. Install the package
# --------------------------------------------------------------------------

Write-Step "Installing codex-cua-mcp"

Push-Location $RepoPath
try {
    & uv venv --python 3.14 | Out-Null
    & uv pip install --python .\.venv\Scripts\python.exe -e . | Out-Null
    Write-Ok "installed into .venv"
} finally {
    Pop-Location
}

$serverCmd = Join-Path $RepoPath ".venv\Scripts\python.exe"
if (-not (Test-Path $serverCmd)) {
    $serverCmd = "uv"
    $serverArgs = @("run", "--project", $RepoPath, "python", "-m", "codex_cua_mcp.server")
} else {
    $serverArgs = @("-m", "codex_cua_mcp.server")
}

# --------------------------------------------------------------------------
# 4. Register with harnesses
# --------------------------------------------------------------------------

$register = {
    param($HarnessName)
    switch ($HarnessName) {
        'claude' {
            $claude = Get-Command claude -ErrorAction SilentlyContinue
            if (-not $claude) {
                Write-Warn "claude CLI not found; skipping Claude Code registration"
                return
            }
            & claude mcp remove codex-cua --scope user 2>$null | Out-Null
            & claude mcp add --scope user codex-cua -- $serverCmd @serverArgs
            Write-Ok "registered with Claude Code (user scope)"
        }
        'codex' {
            $config = Join-Path $env:USERPROFILE ".codex\config.toml"
            if (-not (Test-Path $config)) {
                Write-Warn "$config not found; skipping Codex registration"
                return
            }
            $block = @"

[mcp_servers.codex-cua]
command = '$serverCmd'
args = [$(($serverArgs | ForEach-Object { "'$_'" }) -join ', ')]
"@
            Add-Content -Path $config -Value $block
            Write-Ok "registered with Codex CLI (~/.codex/config.toml)"
        }
        'opencode' {
            $cfgDir = Join-Path $env:USERPROFILE ".config\opencode"
            $cfg = Join-Path $cfgDir "opencode.json"
            if (-not (Test-Path $cfgDir)) {
                New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
            }
            $entry = @"
  ""codex-cua"": {
    ""type"": ""local"",
    ""command"": [$(($serverArgs | ForEach-Object { "'$_'" }) -join ', ')],
    ""enabled"": true
  }
"@
            if (Test-Path $cfg) {
                Add-Content -Path $cfg -Value $entry
            } else {
                @"{`n  ""mcp"": {`n$entry  }`n}"@ | Set-Content -Path $cfg -Encoding utf8
            }
            Write-Ok "registered with OpenCode (~/.config/opencode/opencode.json)"
        }
    }
}

if ($Harness -eq 'all') {
    foreach ($h in @('claude', 'codex', 'opencode')) { & $register $h }
} else {
    & $register $Harness
}

# --------------------------------------------------------------------------
# 5. Verify
# --------------------------------------------------------------------------

Write-Step "Verifying"
Push-Location $RepoPath
try {
    & $serverCmd @serverArgs --check
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "server check passed"
    } else {
        Write-Warn "server check failed (exit $LASTEXITCODE)"
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Start your harness and ask it to list apps, e.g.:"
Write-Host '  "List the open apps on my PC"'
