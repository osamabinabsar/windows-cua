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

.PARAMETER Backend
    Which driver to register. 'codex' (default) uses the helper that ships
    with Codex CLI; 'cua' uses the MIT-licensed trycua/cua driver, which
    needs no OpenAI binary at all.

.PARAMETER RepoPath
    Path to a checkout of this repository on the target machine.

.PARAMETER User
    Local Windows account to install for. Defaults to the current user.

.PARAMETER ConfigRoot
    Directory that holds the per-user config (.codex, .config). Defaults to
    the user profile. Redirect it to test the registration without touching
    a real config.

.EXAMPLE
    .\scripts\install.ps1 -Harness all -RepoPath C:\code\windows-cua

.EXAMPLE
    .\scripts\install.ps1 -Harness claude -Backend cua
#>
[CmdletBinding()]
param(
    [ValidateSet('claude', 'codex', 'opencode', 'all')]
    [string]$Harness = 'claude',

    [ValidateSet('codex', 'cua')]
    [string]$Backend = 'codex',

    [string]$RepoPath = (Split-Path -Parent (Split-Path -Parent $PSCommandPath)),

    [string]$User = $env:USERNAME,

    [string]$ConfigRoot = $env:USERPROFILE
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

function ConvertTo-ArgumentString {
    <#
    .SYNOPSIS
        Join arguments into a Windows command line, quoting as needed.

    .DESCRIPTION
        ProcessStartInfo.ArgumentList does not exist in .NET Framework, so
        Windows PowerShell 5.1 needs a single argument string. This quotes
        per the CommandLineToArgvW rules: wrap in double quotes, escape
        embedded quotes with a backslash, and double any backslashes that
        sit immediately before a quote or the closing quote.
    #>
    param([string[]]$Arguments)

    $parts = foreach ($arg in $Arguments) {
        if ($null -eq $arg) { '""'; continue }
        if ($arg -notmatch '[\s"]') { $arg; continue }

        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        $slashes = 0
        foreach ($c in $arg.ToCharArray()) {
            if ($c -eq '\') { $slashes++; continue }
            if ($c -eq '"') {
                [void]$sb.Append('\' * ($slashes + 1))
                [void]$sb.Append('"')
                $slashes = 0
                continue
            }
            if ($slashes -gt 0) { [void]$sb.Append('\' * $slashes); $slashes = 0 }
            [void]$sb.Append($c)
        }
        if ($slashes -gt 0) { [void]$sb.Append('\' * ($slashes * 2)) }
        [void]$sb.Append('"')
        $sb.ToString()
    }
    return ($parts -join ' ')
}

function Invoke-Native {
    <#
    .SYNOPSIS
        Run a native executable, capturing stdout and stderr.

    .DESCRIPTION
        Windows PowerShell 5.1 wraps a native command's stderr as
        ErrorRecord objects, and with $ErrorActionPreference = 'Stop' a
        command that merely *writes* to stderr (uv does, on every run)
        becomes a terminating error even though it exited 0. This runs the
        command with stderr redirected separately, so progress chatter is
        captured as text instead of raising.

    .PARAMETER FilePath
        The executable.

    .PARAMETER Arguments
        Arguments to pass.

    .PARAMETER WorkingDirectory
        The directory to run in. PowerShell's Set-Location does NOT change
        the .NET current directory that child processes inherit, so this
        must be set explicitly whenever a child resolves a relative path.

    .PARAMETER IgnoreExitCode
        Do not throw on a non-zero exit code.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $null,
        [switch]$IgnoreExitCode
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ConvertTo-ArgumentString -Arguments $Arguments
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }

    $proc = [System.Diagnostics.Process]::Start($psi)
    # Read both pipes concurrently: reading them in sequence can deadlock
    # when the child fills the stderr buffer while we wait on stdout.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    $proc.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result

    if (-not $IgnoreExitCode -and $proc.ExitCode -ne 0) {
        throw "$FilePath exited $($proc.ExitCode): $stderr"
    }
    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $stdout
        StdErr   = $stderr
    }
}

Write-Step "codex-cua-mcp installer"
Write-Host "    repo : $RepoPath"
Write-Host "    user : $User"
Write-Host "    harness: $Harness"
Write-Host "    backend: $Backend"

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

# Look for an already-installed CPython 3.11+ that uv can use. The output
# lines look like "cpython-3.14.7-windows-x86_64-none  <path>"; a version
# with no path is only downloadable, which is still fine.
$pythonOk = $false
try {
    $list = Invoke-Native -FilePath $uv.Source -Arguments @("python", "list")
    $ver = ($list.StdOut + "`n" + $list.StdErr) -split "`n" |
        Where-Object { $_ -match 'cpython-3\.(1[1-9]|[2-9][0-9])' } |
        Select-Object -First 1
    if ($ver) {
        $pythonOk = $true
        Write-Ok "python available: $($ver.Trim())"
    }
} catch { }
if ($pythonOk) {
    Write-Ok "a suitable Python is available to uv"
} else {
    Write-Warn "uv will download a Python 3.14 on first run"
}

# --------------------------------------------------------------------------
# 2. The driver binary
# --------------------------------------------------------------------------

if ($Backend -eq 'cua') {
    Write-Step "Locating cua-driver.exe"

    # The driver's own installer puts it here. The 'current' package is a
    # pointer; an archived release sits beside it.
    $driverRoot = Join-Path $env:USERPROFILE ".cua-driver\packages"
    $driver = $null
    if (Test-Path $driverRoot) {
        $candidate = Join-Path $driverRoot "current\cua-driver.exe"
        if (Test-Path $candidate) {
            $driver = $candidate
        } else {
            $release = Get-ChildItem -Path $driverRoot -Filter "cua-driver.exe" `
                -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match 'releases' } |
                Sort-Object { (Get-Item $_.FullName).LastWriteTime } -Descending |
                Select-Object -First 1
            if ($release) { $driver = $release.FullName }
        }
    }
    if (-not $driver) {
        $cmd = Get-Command cua-driver -ErrorAction SilentlyContinue
        if ($cmd) { $driver = $cmd.Source }
    }

    if ($driver) {
        Write-Ok "driver: $driver"
    } else {
        Write-Warn "cua-driver.exe not found."
        Write-Warn "Install the driver from https://github.com/trycua/cua, then"
        Write-Warn "re-run this script. If you install it elsewhere, set"
        Write-Warn "CUA_DRIVER_BIN to the executable's full path."
    }
} else {
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
}

# --------------------------------------------------------------------------
# 3. Install the package
# --------------------------------------------------------------------------

Write-Step "Installing codex-cua-mcp"

Push-Location $RepoPath
try {
    $venvPython = Join-Path $RepoPath ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        Write-Ok "reusing existing .venv"
    } else {
        $venv = Invoke-Native -FilePath $uv.Source -Arguments @("venv", "--python", "3.14") `
            -WorkingDirectory $RepoPath
        if (-not (Test-Path $venvPython)) {
            throw "uv venv did not produce $venvPython`n$($venv.StdErr)"
        }
        Write-Ok "created .venv"
    }
    # Install by absolute path: the child's working directory is not
    # guaranteed to be the repo, and "." would then resolve elsewhere.
    $install = Invoke-Native -FilePath $uv.Source -Arguments @(
        "pip", "install", "--python", $venvPython, "-e", $RepoPath
    ) -WorkingDirectory $RepoPath
    Write-Ok "installed into .venv"
} finally {
    Pop-Location
}

$venvPython = Join-Path $RepoPath ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $serverCmd = $venvPython
    $serverArgs = @("-m", "codex_cua_mcp.server", "--backend", $Backend)
} else {
    $serverCmd = "uv"
    $serverArgs = @("run", "--project", $RepoPath, "python", "-m", "codex_cua_mcp.server", "--backend", $Backend)
}

# The full command as a single array, for harnesses that want one.
$serverCommand = @($serverCmd) + $serverArgs

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
            # Removing first makes the script idempotent: re-running it
            # replaces the entry instead of failing on a duplicate.
            Invoke-Native -FilePath $claude.Source `
                -Arguments @("mcp", "remove", "codex-cua", "--scope", "user") `
                -IgnoreExitCode | Out-Null
            $add = Invoke-Native -FilePath $claude.Source `
                -Arguments (@("mcp", "add", "--scope", "user", "codex-cua", "--") + $serverCommand)
            Write-Ok "registered with Claude Code (user scope)"
            $add.StdOut.Trim() -split "`n" | Select-Object -First 3 | ForEach-Object { Write-Host "      $_" }
        }
        'codex' {
            $config = Join-Path $ConfigRoot ".codex\config.toml"
            if (-not (Test-Path $config)) {
                Write-Warn "$config not found; skipping Codex registration"
                return
            }
            # Drop any previous entry for this server so re-running the
            # installer does not leave a duplicate TOML table. A TOML table
            # runs until the next table header, so remove the header and
            # every following line that is not a new table.
            $lines = @(Get-Content -Path $config)
            $kept = New-Object System.Collections.Generic.List[string]
            $skipping = $false
            foreach ($line in $lines) {
                $trimmed = $line.Trim()
                if ($trimmed -match '^\[') {
                    $skipping = ($trimmed -eq '[mcp_servers.codex-cua]')
                }
                if (-not $skipping) { $kept.Add($line) }
            }
            $block = @"
[mcp_servers.codex-cua]
command = '$($serverCommand[0])'
args = [$(($serverCommand | Select-Object -Skip 1 | ForEach-Object { "'$_'" }) -join ', ')]
"@
            # Write UTF-8 without a BOM. A BOM makes tomllib (and Rust's
            # toml crate) reject the file outright.
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            $text = ($kept -join "`n").TrimEnd() + "`n`n" + $block
            [System.IO.File]::WriteAllText($config, $text, $utf8NoBom)
            Write-Ok "registered with Codex CLI (~/.codex/config.toml)"
        }
        'opencode' {
            $cfgDir = Join-Path $ConfigRoot ".config\opencode"
            $cfg = Join-Path $cfgDir "opencode.json"
            if (-not (Test-Path $cfgDir)) {
                New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
            }
            # Build the entry as a JSON object and merge it into the config,
            # so an existing opencode.json stays valid JSON. Appending raw
            # text to it does not.
            $entry = [ordered]@{
                type    = 'local'
                command = @($serverCommand)
                enabled = $true
            }
            if (Test-Path $cfg) {
                try {
                    $existing = Get-Content -Path $cfg -Raw | ConvertFrom-Json
                } catch {
                    Write-Warn "existing opencode.json is not valid JSON; leaving it alone"
                    Write-Warn "add manually: mcp.codex-cua -> $cfg"
                    return
                }
                if (-not $existing.PSObject.Properties['mcp']) {
                    $existing | Add-Member -NotePropertyName mcp -NotePropertyValue ([pscustomobject]@{})
                }
                $mcp = $existing.mcp
                if ($mcp.PSObject.Properties['codex-cua']) {
                    $mcp.PSObject.Properties['codex-cua'].Value = [pscustomobject]$entry
                } else {
                    $mcp | Add-Member -NotePropertyName 'codex-cua' -NotePropertyValue ([pscustomobject]$entry)
                }
                $existing | ConvertTo-Json -Depth 10 | Set-Content -Path $cfg -Encoding utf8
            } else {
                $doc = [ordered]@{ mcp = [ordered]@{ 'codex-cua' = $entry } }
                $doc | ConvertTo-Json -Depth 10 | Set-Content -Path $cfg -Encoding utf8
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
    $check = Invoke-Native -FilePath $serverCommand[0] `
        -Arguments (($serverCommand | Select-Object -Skip 1) + @("--check")) `
        -WorkingDirectory $RepoPath `
        -IgnoreExitCode
    if ($check.ExitCode -eq 0) {
        Write-Ok "server check passed"
        $check.StdOut.Trim() -split "`n" | ForEach-Object { Write-Host "      $_" }
    } else {
        Write-Warn "server check failed (exit $($check.ExitCode))"
        $check.StdErr.Trim() -split "`n" | Select-Object -First 5 | ForEach-Object { Write-Host "      $_" }
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Start your harness and ask it to list apps, e.g.:"
Write-Host '  "List the open apps on my PC"'
