<#
.SYNOPSIS
    Regression tests for the installer's raw-text config surgery.

.DESCRIPTION
    scripts/install.ps1 edits the user's ~/.claude.json,
    ~/.codex/config.toml and ~/.config/opencode/opencode.json in place,
    so that everything else in those files survives byte for byte. That
    promise is only as good as the surgery underneath it, and the surgery
    is the kind of code that fails by deleting something it should have
    kept.

    These tests take the functions out of install.ps1 by parsing it, so
    they exercise the shipped code rather than a copy of it. Nothing here
    touches a real config file: every input and every output is a string
    in memory.

.PARAMETER InstallScript
    Path to install.ps1. Defaults to the one next to this file.

.EXAMPLE
    pwsh ./tests/test_install_config.ps1
#>
[CmdletBinding()]
param(
    [string]$InstallScript = (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\install.ps1')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $InstallScript)) {
    throw "install.ps1 not found at $InstallScript"
}

# --- pull the functions out of the script ------------------------------
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $InstallScript, [ref]$tokens, [ref]$parseErrors)

if ($parseErrors) {
    throw ("install.ps1 does not parse: {0}" -f $parseErrors[0].Message)
}

$wanted = @(
    'Get-JsonStringEnd'
    'Get-JsonValueEnd'
    'Find-JsonMember'
    'Remove-JsonProperty'
    'Test-JsonText'
    'Remove-TomlTable'
)

$definitions = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $wanted -contains $node.Name
}, $true)

if ($definitions.Count -ne $wanted.Count) {
    $found = ($definitions | ForEach-Object { $_.Name }) -join ', '
    throw ("install.ps1 no longer defines every function these tests need. Found: {0}" -f $found)
}

$definitions | ForEach-Object {
    . ([scriptblock]::Create($_.Extent.Text))
}

# --- a tiny test harness ----------------------------------------------
$script:passed = 0
$script:failed = 0

function Assert-Equal {
    param(
        [string]$Name,
        [AllowNull()][string]$Expected,
        [AllowNull()][string]$Actual
    )
    if ($Expected -ceq $Actual) {
        $script:passed++
        Write-Host ("  ok   {0}" -f $Name)
    } else {
        $script:failed++
        Write-Host ("  FAIL {0}" -f $Name) -ForegroundColor Red
        Write-Host ("       expected: [{0}]" -f ($Expected -replace "`r", '\r' -replace "`n", '\n')) -ForegroundColor Red
        Write-Host ("       actual  : [{0}]" -f ($Actual   -replace "`r", '\r' -replace "`n", '\n')) -ForegroundColor Red
    }
}

function Assert-True {
    param([string]$Name, [bool]$Condition)
    Assert-Equal -Name $Name -Expected 'True' -Actual ([string]$Condition)
}

# ======================================================================
# Remove-TomlTable
# ======================================================================
Write-Host ''
Write-Host 'Remove-TomlTable'
Write-Host '----------------'

# The table and its sub-tables go; everything else stays.
$toml = @"
model = "gpt-5"

[mcp_servers.codex-cua]
command = 'python'
args = ['-m', 'codex_cua_mcp.server']

[mcp_servers.codex-cua.env]
FOO = 'bar'

[other]
x = 1
"@
Assert-Equal -Name 'the table and its sub-tables are removed' -Expected @"
model = "gpt-5"

[other]
x = 1
"@ -Actual (Remove-TomlTable -Text $toml -TableName 'mcp_servers.codex-cua')

# An array-of-tables header after the target is a different section.
# It used to be swallowed along with the target.
$tomlArray = "[mcp_servers.codex-cua]`ncommand = 'x'`n`n[[servers]]`nname = 'a'"
Assert-Equal -Name 'a following [[array-of-tables]] section survives' `
    -Expected "[[servers]]`nname = 'a'" `
    -Actual (Remove-TomlTable -Text $tomlArray -TableName 'mcp_servers.codex-cua')

# TOML keys are case sensitive, so this is a different table.
$tomlCase = "[mcp_servers.codex-cua]`ncommand = 'x'`n`n[MCP_SERVERS.CODEX-CUA]`ncommand = 'y'"
Assert-Equal -Name 'a case-variant table survives' `
    -Expected "[MCP_SERVERS.CODEX-CUA]`ncommand = 'y'" `
    -Actual (Remove-TomlTable -Text $tomlCase -TableName 'mcp_servers.codex-cua')

# A table that is not there: the function says so with $null.
Assert-True -Name 'a missing table returns $null' `
    -Condition ($null -eq (Remove-TomlTable -Text $tomlArray -TableName 'not.there'))

# The table is the last thing in the file.
$tomlLast = "x = 1`n`n[mcp_servers.codex-cua]`ncommand = 'y'"
Assert-Equal -Name 'the last table in a file is removed' `
    -Expected "x = 1`n`n" `
    -Actual (Remove-TomlTable -Text $tomlLast -TableName 'mcp_servers.codex-cua')

# ======================================================================
# Remove-JsonProperty
# ======================================================================
Write-Host ''
Write-Host 'Remove-JsonProperty'
Write-Host '--------------------'

$json = @'
{
  "mcpServers": {
    "codex-cua": {
      "command": "python"
    }
  },
  "other": 1
}
'@

$out = Remove-JsonProperty -Text $json -Path @('mcpServers', 'codex-cua')
$parsed = $out | ConvertFrom-Json
Assert-True  -Name 'the member is gone' -Condition ($null -eq $parsed.mcpServers.'codex-cua')
Assert-Equal -Name 'the sibling survives' -Expected '1' -Actual ([string]$parsed.other)
Assert-True  -Name 'the result is still valid JSON' -Condition (Test-JsonText -Text $out)

# The member is the only one in its container. The container must end up
# empty -- not holding a blank line where the member used to be.
$jsonOnly = @'
{
  "mcpServers": {
    "codex-cua": {
      "command": "python"
    }
  }
}
'@
$outOnly = Remove-JsonProperty -Text $jsonOnly -Path @('mcpServers', 'codex-cua')
$blankLines = @($outOnly -split "`n" | Where-Object { $_.Trim() -eq '' -and $_.Length -gt 0 })
Assert-Equal -Name 'no whitespace-only line is left behind' -Expected '0' -Actual ([string]$blankLines.Count)
Assert-True  -Name 'the emptied container is still valid JSON' -Condition (Test-JsonText -Text $outOnly)

# A sibling before it survives, and the result stays valid.
$jsonSibling = @'
{
  "mcpServers": {
    "other": 1,
    "codex-cua": {
      "command": "python"
    }
  }
}
'@
$outSibling = Remove-JsonProperty -Text $jsonSibling -Path @('mcpServers', 'codex-cua')
$parsedSibling = $outSibling | ConvertFrom-Json
Assert-Equal -Name 'the sibling before it survives' -Expected '1' -Actual ([string]$parsedSibling.mcpServers.other)
Assert-True  -Name 'the member is gone' -Condition ($null -eq $parsedSibling.mcpServers.'codex-cua')
Assert-True  -Name 'the result is still valid JSON' -Condition (Test-JsonText -Text $outSibling)

# A malformed file is refused, not silently rewritten.
$refused = $false
try { Remove-JsonProperty -Text '{ "a": ' -Path @('a') | Out-Null } catch { $refused = $true }
Assert-True -Name 'a malformed file is refused' -Condition $refused

# A member that is not there: the function says so with $null.
Assert-True -Name 'a missing member returns $null' `
    -Condition ($null -eq (Remove-JsonProperty -Text $json -Path @('mcpServers', 'nope')))

# CRLF line endings survive the edit.
$jsonCrlf = ($json -replace "`n", "`r`n")
$outCrlf = Remove-JsonProperty -Text $jsonCrlf -Path @('mcpServers', 'codex-cua')
Assert-True -Name 'a CRLF file is still valid JSON afterwards' -Condition (Test-JsonText -Text $outCrlf)
Assert-True -Name 'a CRLF file keeps its CRLF line endings' `
    -Condition ($outCrlf.Contains("`r`n"))

# ======================================================================
Write-Host ''
Write-Host ('{0} passed, {1} failed' -f $script:passed, $script:failed)
exit $script:failed
