[CmdletBinding()]
param(
    [string]$DiscoveryScriptPath = (
        Join-Path $PSScriptRoot "moomoo_production_readonly_discovery_v4.ps1"
    ),
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedDiscoverySha256,
    [switch]$RunDiscovery,
    [Parameter(Mandatory = $true)][string]$RepoPath,
    [Parameter(Mandatory = $true)][string]$ProtectedCheckoutPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedHead,
    [Parameter(Mandatory = $true)][string]$ExpectedRemote,
    [Parameter(Mandatory = $true)][string]$ConfigSearchRootsJson,
    [string]$ProductionWorkingDirectoryCandidatesJson = "[]"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-ParserError {
    param([Parameter(Mandatory = $true)]$ErrorRecord)
    [pscustomobject]@{
        message = $ErrorRecord.Message
        start_line = $ErrorRecord.Extent.StartLineNumber
        start_column = $ErrorRecord.Extent.StartColumnNumber
        end_line = $ErrorRecord.Extent.EndLineNumber
        end_column = $ErrorRecord.Extent.EndColumnNumber
        text = $ErrorRecord.Extent.Text
    }
}

$ConfigSearchRoots = @($ConfigSearchRootsJson | ConvertFrom-Json)
$ProductionWorkingDirectoryCandidates = @(
    $ProductionWorkingDirectoryCandidatesJson | ConvertFrom-Json
)
if ($ConfigSearchRoots.Count -eq 0) {
    throw "ConfigSearchRootsJson must contain at least one path"
}

$tokens = $null
$parseErrors = $null
$ast = $null
$actualSha256 = $null
$gateError = $null
$discovery = $null
$discoveryError = $null

try {
    if (-not (Test-Path -LiteralPath $DiscoveryScriptPath -PathType Leaf)) {
        throw "Discovery script does not exist: $DiscoveryScriptPath"
    }
    $actualSha256 = (
        Get-FileHash -LiteralPath $DiscoveryScriptPath -Algorithm SHA256
    ).Hash
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $DiscoveryScriptPath,
        [ref]$tokens,
        [ref]$parseErrors
    )
} catch {
    $gateError = $_.Exception.Message
}

$parseErrorRows = @(
    $parseErrors | ForEach-Object { Convert-ParserError $_ }
)
$hashMatches = (
    $actualSha256 -and
    $actualSha256.ToUpperInvariant() -eq $ExpectedDiscoverySha256.ToUpperInvariant()
)
$parserPassed = ($null -ne $ast -and $parseErrorRows.Count -eq 0)
$gatePassed = (-not $gateError -and $hashMatches -and $parserPassed)

if ($RunDiscovery -and $gatePassed) {
    try {
        $raw = & $DiscoveryScriptPath `
            -RepoPath $RepoPath `
            -ProtectedCheckoutPath $ProtectedCheckoutPath `
            -ExpectedHead $ExpectedHead `
            -ExpectedRemote $ExpectedRemote `
            -ConfigSearchRoots $ConfigSearchRoots `
            -ProductionWorkingDirectoryCandidates $ProductionWorkingDirectoryCandidates
        $discovery = ($raw -join "`n") | ConvertFrom-Json
    } catch {
        $discoveryError = $_.Exception.Message
    }
}

$result = [pscustomobject]@{
    report_type = "moomoo_discovery_v4_gated_result"
    captured_at = (Get-Date).ToString("o")
    gate = [pscustomobject]@{
        report_type = "moomoo_discovery_v4_parser_gate"
        powershell = [pscustomobject]@{
            version = $PSVersionTable.PSVersion.ToString()
            edition = if ($PSVersionTable.PSObject.Properties.Name -contains "PSEdition") { [string]$PSVersionTable.PSEdition } else { "Desktop" }
            is_64_bit_process = [Environment]::Is64BitProcess
        }
        script_path = [IO.Path]::GetFullPath($DiscoveryScriptPath)
        expected_sha256 = $ExpectedDiscoverySha256
        actual_sha256 = $actualSha256
        hash_matches = $hashMatches
        parser_type = "System.Management.Automation.Language.Parser"
        parser_passed = $parserPassed
        parser_error_count = $parseErrorRows.Count
        parser_errors = $parseErrorRows
        gate_error = $gateError
        gate_passed = $gatePassed
        discovery_requested = [bool]$RunDiscovery
        discovery_executed = ($RunDiscovery -and $gatePassed -and -not $discoveryError -and $null -ne $discovery)
    }
    discovery = $discovery
    discovery_error = $discoveryError
    authorization = [pscustomobject]@{
        production_readiness = "BLOCKED"
        preflight_authorized = $false
        production_drill_authorized = $false
        cutover_authorized = $false
    }
}

$result | ConvertTo-Json -Depth 16
if (-not $gatePassed -or ($RunDiscovery -and ($discoveryError -or $null -eq $discovery))) {
    exit 1
}
exit 0
