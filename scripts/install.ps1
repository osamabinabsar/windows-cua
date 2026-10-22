<#
.SYNOPSIS
    Install and register codex-cua-mcp on a Windows machine.

.DESCRIPTION
    Sets up the MCP server on a target PC and registers it with one or more
    agent harnesses. Intended for the user's own machines.

    Requirements on the target PC:
      * Python 3.11+ (or uv, which can fetch one)
      * For the 'codex' backend: the Codex desktop app, which supplies
        codex-computer-use.exe. The Codex CLI on its own does not.

.PARAMETER Harness
    Which harness to register with. One of: claude, codex, opencode, all.

.PARAMETER Backend
    Which driver to register. 'codex' (default) uses the helper that ships
    with the Codex desktop app; 'cua' uses the MIT-licensed trycua/cua
    driver, which needs no OpenAI binary at all.

.PARAMETER RepoPath
    Path to a checkout of this repository on the target machine.

.PARAMETER User
    Local Windows account to install for. Defaults to the current user.

.PARAMETER ConfigRoot
    Directory that holds the per-user config (.codex, .config). Defaults to
    the user profile. Redirect it to test the registration without touching
    a real config.

.PARAMETER Uninstall
    Remove the registrations this script added (the Claude Code, Codex CLI
    and OpenCode entries named 'codex-cua') and exit. The virtualenv, the
    repository and every other setting are left alone. Safe to run twice.

.EXAMPLE
    .\scripts\install.ps1 -Harness all -RepoPath C:\code\windows-cua

.EXAMPLE
    .\scripts\install.ps1 -Uninstall

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

    [string]$ConfigRoot = $env:USERPROFILE,

    [switch]$Uninstall
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

function Write-Err {
    param([string]$Message)
    Write-Host "    X  $Message" -ForegroundColor Red
}

# Windows PowerShell 5.1 has no 'utf8NoBOM' encoding: -Encoding utf8 writes
# a BOM, and a BOM breaks the JSON and TOML parsers used by MCP clients.
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# Anything that stopped the install from finishing. The script still runs to
# the end, but the exit code and the final summary have to be honest about
# what did not work.
$Failures = New-Object System.Collections.Generic.List[string]

function Read-TextFile {
    <#
    .SYNOPSIS
        Read a UTF-8 file and report whether it started with a BOM.

    .DESCRIPTION
        Reads the raw bytes so a byte-order mark can be preserved (or
        dropped) on the way back out.
    #>
    param([string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    if ($hasBom) { $text = $text.Substring(1) }
    return [pscustomobject]@{ Text = $text; HasBom = $hasBom }
}

function Write-TextFile {
    <#
    .SYNOPSIS
        Write a UTF-8 file, with or without a BOM.
    #>
    param([string]$Path, [string]$Text, [bool]$HasBom)

    $enc = New-Object System.Text.UTF8Encoding($HasBom)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Get-JsonStringEnd {
    <#
    .SYNOPSIS
        Return the index just past the closing quote of a JSON string.

    .DESCRIPTION
        $Start points at the opening quote. An escaped quote (a backslash
        before the quote) does not end the string.
    #>
    param([string]$Text, [int]$Start)

    $i = $Start + 1
    while ($i -lt $Text.Length) {
        $c = $Text[$i]
        if ($c -eq '\') { $i += 2; continue }
        if ($c -eq '"') { return $i + 1 }
        $i++
    }
    throw "unterminated JSON string at offset $Start"
}

function Get-JsonValueEnd {
    <#
    .SYNOPSIS
        Return the index just past the end of a JSON value.
    #>
    param([string]$Text, [int]$Start)

    $c = $Text[$Start]
    if ($c -eq '"') { return Get-JsonStringEnd -Text $Text -Start $Start }

    if ($c -eq '{' -or $c -eq '[') {
        $depth = 0
        $i = $Start
        while ($i -lt $Text.Length) {
            $ch = $Text[$i]
            if ($ch -eq '"') { $i = Get-JsonStringEnd -Text $Text -Start $i; continue }
            if ($ch -eq '{' -or $ch -eq '[') {
                $depth++
            } elseif ($ch -eq '}' -or $ch -eq ']') {
                $depth--
                if ($depth -eq 0) { return $i + 1 }
            }
            $i++
        }
        throw "unterminated JSON value at offset $Start"
    }

    # A number, true, false or null: runs up to the next delimiter.
    $i = $Start
    while ($i -lt $Text.Length -and $Text[$i] -notmatch '[\s,\]\}]') { $i++ }
    return $i
}

function Find-JsonMember {
    <#
    .SYNOPSIS
        Find a named member of a JSON object in raw text.

    .DESCRIPTION
        Returns the offsets of the member's key and its value, or $null when
        the object has no such member. Working on the raw text is what lets
        the rest of a config file survive a change byte for byte, which a
        ConvertFrom-Json/ConvertTo-Json round trip cannot do.
    #>
    param([string]$Text, [int]$ObjectStart, [string]$Name)

    $i = $ObjectStart + 1
    while ($i -lt $Text.Length) {
        $c = $Text[$i]
        if ($c -eq '}') { return $null }
        if ($c -eq ',') { $i++; continue }
        if ([char]::IsWhiteSpace($c)) { $i++; continue }
        if ($c -ne '"') { throw "unexpected character '$c' at offset $i" }

        $keyStart = $i
        $keyEnd = Get-JsonStringEnd -Text $Text -Start $i
        $key = $Text.Substring($keyStart + 1, $keyEnd - $keyStart - 2)
        $i = $keyEnd

        while ($i -lt $Text.Length -and [char]::IsWhiteSpace($Text[$i])) { $i++ }
        if ($i -ge $Text.Length -or $Text[$i] -ne ':') { throw "expected ':' at offset $i" }
        $i++
        while ($i -lt $Text.Length -and [char]::IsWhiteSpace($Text[$i])) { $i++ }

        $valueStart = $i
        $valueEnd = Get-JsonValueEnd -Text $Text -Start $i
        if ($key -eq $Name) {
            return [pscustomobject]@{
                KeyStart   = $keyStart
                KeyEnd     = $keyEnd
                ValueStart = $valueStart
                ValueEnd   = $valueEnd
            }
        }
        $i = $valueEnd
    }
    return $null
}

function Remove-JsonProperty {
    <#
    .SYNOPSIS
        Remove a member from a JSON document, leaving the rest untouched.

    .DESCRIPTION
        $Path is the chain of object keys, e.g. @('mcpServers', 'codex-cua').
        The member is cut out of the raw text, so every other byte of the
        file survives unchanged. Returns the new text, or $null when the
        document has no such member.
    #>
    param([string]$Text, [string[]]$Path)

    # Refuse to touch a file that is not valid JSON in the first place.
    if (-not (Test-JsonText -Text $Text)) {
        throw "the file is not valid JSON"
    }

    $i = 0
    while ($i -lt $Text.Length -and [char]::IsWhiteSpace($Text[$i])) { $i++ }
    if ($i -ge $Text.Length -or $Text[$i] -ne '{') { throw "the document is not a JSON object" }
    $obj = $i

    for ($k = 0; $k -lt $Path.Count - 1; $k++) {
        $m = Find-JsonMember -Text $Text -ObjectStart $obj -Name $Path[$k]
        if (-not $m) { return $null }
        if ($Text[$m.ValueStart] -ne '{') { throw "'$($Path[$k])' is not a JSON object" }
        $obj = $m.ValueStart
    }

    $m = Find-JsonMember -Text $Text -ObjectStart $obj -Name $Path[$Path.Count - 1]
    if (-not $m) { return $null }

    $keyStart = $m.KeyStart
    $valueEnd = $m.ValueEnd

    # Is the member the only thing on its line?
    $lineStart = $keyStart
    while ($lineStart -gt 0 -and $Text[$lineStart - 1] -ne "`n") { $lineStart-- }
    $ownLine = $true
    for ($k = $lineStart; $k -lt $keyStart; $k++) {
        if ($Text[$k] -ne ' ' -and $Text[$k] -ne "`t") { $ownLine = $false; break }
    }

    # Is there a comma after the value?
    $j = $valueEnd
    while ($j -lt $Text.Length -and [char]::IsWhiteSpace($Text[$j])) { $j++ }
    $hasComma = ($j -lt $Text.Length -and $Text[$j] -eq ',')

    if ($hasComma) {
        # What is left of the line after that comma?
        $k = $j + 1
        while ($k -lt $Text.Length -and ($Text[$k] -eq ' ' -or $Text[$k] -eq "`t")) { $k++ }
        $lineEnds = ($k -lt $Text.Length -and ($Text[$k] -eq "`r" -or $Text[$k] -eq "`n"))
        if ($ownLine -and $lineEnds) {
            # The member has a line to itself: take the whole line.
            $end = $k
            if ($Text[$end] -eq "`r" -and ($end + 1) -lt $Text.Length -and $Text[$end + 1] -eq "`n") {
                $end += 2
            } else {
                $end++
            }
            return $Text.Substring(0, $lineStart) + $Text.Substring($end)
        }
        # Inline: take the member and the comma after it.
        $start = $keyStart
        $p = $keyStart - 1
        while ($p -ge 0 -and ($Text[$p] -eq ' ' -or $Text[$p] -eq "`t")) { $p-- }
        if ($p -ge 0 -and ($Text[$p] -eq '{' -or $Text[$p] -eq ',')) { $start = $p + 1 }
        return $Text.Substring(0, $start) + $Text.Substring($j + 1)
    }

    # The last member of its object: take the comma in front of it.
    $p = $keyStart - 1
    while ($p -ge 0 -and [char]::IsWhiteSpace($Text[$p])) { $p-- }
    if ($p -ge 0 -and $Text[$p] -eq ',') {
        if ($ownLine) {
            # Take the whole line, and the comma on the line above.
            $end = $valueEnd
            $k = $end
            while ($k -lt $Text.Length -and ($Text[$k] -eq ' ' -or $Text[$k] -eq "`t")) { $k++ }
            if ($k -lt $Text.Length -and $Text[$k] -eq "`r") { $k++ }
            if ($k -lt $Text.Length -and $Text[$k] -eq "`n") { $k++ }
            $end = $k
            return $Text.Substring(0, $p) + $Text.Substring($p + 1, $lineStart - $p - 1) + $Text.Substring($end)
        }
        return $Text.Substring(0, $p) + $Text.Substring($valueEnd)
    }

    # The only member of its object.
    $start = $keyStart
    $p = $keyStart - 1
    while ($p -ge 0 -and ($Text[$p] -eq ' ' -or $Text[$p] -eq "`t")) { $p-- }
    if ($p -ge 0 -and ($Text[$p] -eq '{' -or $Text[$p] -eq ',')) { $start = $p + 1 }
    # If the member had a line to itself, take that line with it: the
    # indentation in front of the key and the newline that ended the
    # line above. Without this an emptied container is left holding a
    # blank line where the member used to be.
    if ($ownLine -and $lineStart -gt 0) {
        $cut = $lineStart - 1
        if ($cut -gt 0 -and $Text[$cut - 1] -eq "`r") { $cut-- }
        $start = $cut
    }
    return $Text.Substring(0, $start) + $Text.Substring($valueEnd)
}

function Test-JsonText {
    <#
    .SYNOPSIS
        Check that a string holds exactly one well-formed JSON value.

    .DESCRIPTION
        Used to confirm that a rewritten config is still parseable before
        it replaces the user's file. It is deliberately structural rather
        than ConvertFrom-Json: PS 5.1's ConvertFrom-Json throws on keys
        that differ only in case, which are perfectly legal JSON.
    #>
    param([string]$Text)

    $i = 0
    while ($i -lt $Text.Length -and [char]::IsWhiteSpace($Text[$i])) { $i++ }
    if ($i -ge $Text.Length) { return $false }
    try {
        $end = Get-JsonValueEnd -Text $Text -Start $i
    } catch {
        return $false
    }
    while ($end -lt $Text.Length -and [char]::IsWhiteSpace($Text[$end])) { $end++ }
    return ($end -eq $Text.Length)
}

function Remove-TomlTable {
    <#
    .SYNOPSIS
        Remove a TOML table, and its sub-tables, from a document.

    .DESCRIPTION
        $TableName is the table name without brackets, e.g.
        'mcp_servers.codex-cua'. A TOML table runs until the next table
        header, so the header and everything up to the next header is cut
        out of the raw text and the rest of the file (line endings
        included) survives unchanged. Returns the new text, or $null when
        the table is not there.

        Two kinds of header end a table, and both have to be recognised:
        a plain table `[name]` and an array of tables `[[name]]`. The
        target itself is matched case-sensitively, because TOML keys are
        case sensitive and `[MCP_SERVERS.CODEX-CUA]` is a different
        table from `[mcp_servers.codex-cua]`.
    #>
    param([string]$Text, [string]$TableName)

    $header = '^\s*\[' + [regex]::Escape($TableName) + '(\.[^\]\r\n]*)?\]'
    $anyHeader = '^\s*\[{1,2}[A-Za-z0-9_."''-]+\]{1,2}'

    $lines = [regex]::Matches($Text, '(?m)^[^\r\n]*')
    $cutStart = -1
    $cutEnd = -1

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i].Value
        if ($cutStart -lt 0) {
            if ($line -cmatch $header) { $cutStart = $lines[$i].Index }
            continue
        }
        # A sub-table of the target belongs to it, so keep going.
        if ($line -cmatch $header) { continue }
        if ($line -match $anyHeader) { $cutEnd = $lines[$i].Index; break }
    }

    if ($cutStart -lt 0) { return $null }
    if ($cutEnd -lt 0) { $cutEnd = $Text.Length }
    return $Text.Substring(0, $cutStart) + $Text.Substring($cutEnd)
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

if ($Uninstall) {
    Write-Step "Removing the codex-cua registrations"
    Write-Host "    config root: $ConfigRoot"
    Write-Host ""

    # Claude Code: the user-scope MCP server lives in ~/.claude.json under
    # "mcpServers". Cut the entry out of the raw text so every other
    # setting in the file is preserved exactly.
    $claudeCfg = Join-Path $ConfigRoot ".claude.json"
    if (-not (Test-Path $claudeCfg)) {
        Write-Ok "claude code : $claudeCfg not present, nothing to remove"
    } else {
        try {
            $raw = Read-TextFile -Path $claudeCfg
            $newText = Remove-JsonProperty -Text $raw.Text -Path @('mcpServers', 'codex-cua')
            if ($null -eq $newText) {
                Write-Ok "claude code : no codex-cua entry in $claudeCfg"
            } else {
                if (-not (Test-JsonText -Text $newText)) {
                    throw "the rewritten file would not be valid JSON"
                }
                Write-TextFile -Path $claudeCfg -Text $newText -HasBom $raw.HasBom
                Write-Ok "claude code : removed codex-cua from $claudeCfg"
            }
        } catch {
            Write-Err "claude code : could not remove codex-cua from $claudeCfg : $($_.Exception.Message)"
            Write-Err "The file was left unchanged. Fix it by hand, or fix the JSON and re-run."
            $Failures.Add("claude code entry not removed from $claudeCfg")
        }
    }

    # Codex CLI: the [mcp_servers.codex-cua] table in ~/.codex/config.toml.
    $codexCfg = Join-Path $ConfigRoot ".codex\config.toml"
    if (-not (Test-Path $codexCfg)) {
        Write-Ok "codex       : $codexCfg not present, nothing to remove"
    } else {
        try {
            $raw = Read-TextFile -Path $codexCfg
            $newText = Remove-TomlTable -Text $raw.Text -TableName 'mcp_servers.codex-cua'
            if ($null -eq $newText) {
                Write-Ok "codex       : no [mcp_servers.codex-cua] table in $codexCfg"
            } else {
                Write-TextFile -Path $codexCfg -Text $newText -HasBom $raw.HasBom
                Write-Ok "codex       : removed [mcp_servers.codex-cua] from $codexCfg"
            }
        } catch {
            Write-Err "codex       : could not remove [mcp_servers.codex-cua] from $codexCfg : $($_.Exception.Message)"
            Write-Err "The file was left unchanged. Fix it by hand, or fix the TOML and re-run."
            $Failures.Add("codex entry not removed from $codexCfg")
        }
    }

    # OpenCode: the "codex-cua" entry under "mcp" in opencode.json.
    $openCfg = Join-Path $ConfigRoot ".config\opencode\opencode.json"
    if (-not (Test-Path $openCfg)) {
        Write-Ok "opencode    : $openCfg not present, nothing to remove"
    } else {
        try {
            $raw = Read-TextFile -Path $openCfg
            $newText = Remove-JsonProperty -Text $raw.Text -Path @('mcp', 'codex-cua')
            if ($null -eq $newText) {
                Write-Ok "opencode    : no codex-cua entry in $openCfg"
            } else {
                if (-not (Test-JsonText -Text $newText)) {
                    throw "the rewritten file would not be valid JSON"
                }
                Write-TextFile -Path $openCfg -Text $newText -HasBom $raw.HasBom
                Write-Ok "opencode    : removed codex-cua from $openCfg"
            }
        } catch {
            Write-Err "opencode    : could not remove codex-cua from $openCfg : $($_.Exception.Message)"
            Write-Err "The file was left unchanged. Fix it by hand, or fix the JSON and re-run."
            $Failures.Add("opencode entry not removed from $openCfg")
        }
    }

    Write-Host ""
    if ($Failures.Count -gt 0) {
        Write-Host "Uninstall INCOMPLETE." -ForegroundColor Red
        foreach ($f in $Failures) { Write-Host "    - $f" -ForegroundColor Red }
        Write-Host ""
        Write-Host "The .venv and the repository were left untouched." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Uninstall complete." -ForegroundColor Green
    Write-Host "The .venv, the repository and every other harness setting were left untouched."
    exit 0
}

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
        Write-Warn "That helper ships with the Codex desktop app, not with the"
        Write-Warn "npm CLI -- 'npm install -g @openai/codex' will NOT provide it."
        Write-Warn "Install the Codex app, then re-run this script. If you have"
        Write-Warn "the helper somewhere else, set CODEX_COMPUTER_USE_BIN to"
        Write-Warn "the executable's full path. Or use the MIT backend, which"
        Write-Warn "needs no OpenAI binary:  -Backend cua"
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
                $Failures.Add("Claude Code not registered: the 'claude' CLI was not found")
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
            $codexDir = Join-Path $ConfigRoot ".codex"
            $config = Join-Path $codexDir "config.toml"
            # A missing config is not a reason to skip the registration:
            # create the directory and the file, then register into it.
            if (-not (Test-Path $codexDir)) {
                New-Item -ItemType Directory -Force -Path $codexDir | Out-Null
                Write-Ok "created $codexDir"
            }
            $existing = ''
            if (Test-Path $config) {
                $existing = (Read-TextFile -Path $config).Text
            } else {
                Write-Ok "no $config yet; creating it"
            }
            # Drop any previous entry for this server so re-running the
            # installer does not leave a duplicate TOML table. A TOML table
            # runs until the next table header, so remove the header and
            # every following line that is not a new table. Working on the
            # raw text keeps the rest of the file, line endings included.
            $kept = Remove-TomlTable -Text $existing -TableName 'mcp_servers.codex-cua'
            if ($null -eq $kept) { $kept = $existing }
            $block = @"
[mcp_servers.codex-cua]
command = '$($serverCommand[0])'
args = [$(($serverCommand | Select-Object -Skip 1 | ForEach-Object { "'$_'" }) -join ', ')]
"@
            # Match the file's own line endings, so the rest of it is
            # untouched.
            $nl = "`n"
            if ($existing.Contains("`r`n")) { $nl = "`r`n" }
            $block = ($block -replace "`r`n", "`n") -replace "`n", $nl
            $body = $kept -replace '(\r?\n)+$', ''
            if ($body.Trim().Length -eq 0) {
                $text = $block
            } else {
                $text = $body + $nl + $nl + $block
            }
            # Write UTF-8 without a BOM. A BOM makes tomllib (and Rust's
            # toml crate) reject the file outright.
            [System.IO.File]::WriteAllText($config, $text, $Utf8NoBom)
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
                $raw = [System.IO.File]::ReadAllText($cfg)
                if ([string]::IsNullOrWhiteSpace($raw)) {
                    $existing = [pscustomobject]@{}
                } else {
                    try {
                        $existing = $raw | ConvertFrom-Json
                    } catch {
                        Write-Err "cannot parse $cfg : $($_.Exception.Message)"
                        Write-Err "The file was left unchanged. Fix the JSON (or delete the"
                        Write-Err "file) and re-run this script to register the server."
                        $Failures.Add("opencode config could not be updated: $cfg")
                        return
                    }
                }
                if ($null -eq $existing -or $existing -is [System.Array] -or $existing -is [string] -or $existing -is [bool] -or $existing -is [ValueType]) {
                    Write-Err "$cfg does not hold a JSON object; the file was left unchanged."
                    Write-Err "Fix it by hand, then re-run this script."
                    $Failures.Add("opencode config could not be updated: $cfg")
                    return
                }
                $mcpProp = $existing.PSObject.Properties['mcp']
                if ($mcpProp) {
                    $mcp = $mcpProp.Value
                    if ($null -eq $mcp -or $mcp -is [string] -or $mcp -is [System.Array]) {
                        Write-Err "'mcp' in $cfg is not a JSON object; the file was left unchanged."
                        Write-Err "Fix it by hand, then re-run this script."
                        $Failures.Add("opencode config could not be updated: $cfg")
                        return
                    }
                } else {
                    $mcp = [pscustomobject]@{}
                    $existing | Add-Member -NotePropertyName mcp -NotePropertyValue $mcp
                }
                # Replace the entry if it is already there, so re-running
                # the installer updates it instead of adding a second one.
                if ($mcp.PSObject.Properties['codex-cua']) {
                    $mcp.PSObject.Properties['codex-cua'].Value = [pscustomobject]$entry
                } else {
                    $mcp | Add-Member -NotePropertyName 'codex-cua' -NotePropertyValue ([pscustomobject]$entry)
                }
                $json = $existing | ConvertTo-Json -Depth 10
            } else {
                $doc = [ordered]@{ mcp = [ordered]@{ 'codex-cua' = $entry } }
                $json = $doc | ConvertTo-Json -Depth 10
            }
            # Only replace the file once the new content is known to be
            # valid JSON, so a bad merge cannot corrupt the config.
            if (-not (Test-JsonText -Text $json)) {
                Write-Err "the new opencode config would not be valid JSON; $cfg was left unchanged."
                $Failures.Add("opencode config could not be updated: $cfg")
                return
            }
            # Write UTF-8 without a BOM. On PS 5.1, -Encoding utf8 adds one,
            # and a BOM breaks the JSON parsers in several MCP clients.
            [System.IO.File]::WriteAllText($cfg, $json, $Utf8NoBom)
            Write-Ok "registered with OpenCode (~/.config/opencode/opencode.json)"
        }
    }
}

if ($Harness -eq 'all') {
    foreach ($h in @('claude', 'codex', 'opencode')) {
        try { & $register $h } catch {
            Write-Err "registration with $h failed: $($_.Exception.Message)"
            $Failures.Add("registration with $h failed")
        }
    }
} else {
    try { & $register $Harness } catch {
        Write-Err "registration with $Harness failed: $($_.Exception.Message)"
        $Failures.Add("registration with $Harness failed")
    }
}

# --------------------------------------------------------------------------
# 5. Verify
# --------------------------------------------------------------------------

Write-Step "Verifying"
$checkOk = $true
Push-Location $RepoPath
try {
    $check = Invoke-Native -FilePath $serverCommand[0] `
        -Arguments (($serverCommand | Select-Object -Skip 1) + @("--check")) `
        -WorkingDirectory $RepoPath `
        -IgnoreExitCode
    if ($check.ExitCode -eq 0) {
        Write-Ok "server check passed"
        if ($check.StdOut.Trim()) {
            $check.StdOut.Trim() -split "`n" | ForEach-Object { Write-Host "      $_" }
        }
    } else {
        # The server is registered but cannot run. Say so loudly and fail,
        # rather than leaving the user with a success message and a server
        # that can never find its driver.
        $checkOk = $false
        Write-Err "server check FAILED (exit $($check.ExitCode))"
        if ($check.StdOut.Trim()) {
            $check.StdOut.Trim() -split "`n" | Select-Object -First 5 | ForEach-Object { Write-Host "      $_" }
        }
        if ($check.StdErr.Trim()) {
            $check.StdErr.Trim() -split "`n" | Select-Object -First 10 | ForEach-Object { Write-Host "      $_" }
        }
        Write-Err "The server is registered but cannot start until this is fixed."
        Write-Err "Fix the problem above, then re-run this script to register it again."
    }
} finally {
    Pop-Location
}

Write-Host ""
if ($Failures.Count -gt 0 -or -not $checkOk) {
    Write-Host "Installation INCOMPLETE." -ForegroundColor Red
    foreach ($f in $Failures) { Write-Host "    - $f" -ForegroundColor Red }
    if (-not $checkOk) { Write-Host "    - the post-install server check failed" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Fix the problems above and re-run this script to finish the install." -ForegroundColor Red
    exit 1
}

Write-Host "Done." -ForegroundColor Green
Write-Host "Start your harness and ask it to list apps, e.g.:"
Write-Host '  "List the open apps on my PC"'
Write-Host ""
Write-Host "Note: the registration points at this checkout by absolute path."
Write-Host "      If you move or rename the repo folder, re-run this script to"
Write-Host "      update the registration."
Write-Host ""
Write-Host "To undo the registration later:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Uninstall"
