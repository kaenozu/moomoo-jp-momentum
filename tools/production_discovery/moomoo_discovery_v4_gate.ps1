[CmdletBinding()]
param(
    [string]$DiscoveryScriptPath = (
        Join-Path $PSScriptRoot "moomoo_production_readonly_discovery_v4.ps1"
    ),
    [Parameter(Mandatory = $true)][string]$ExpectedFileHashesJson,
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

$ExpectedFileHashes = $ExpectedFileHashesJson | ConvertFrom-Json
$ConfigSearchRoots = @($ConfigSearchRootsJson | ConvertFrom-Json)
$ProductionWorkingDirectoryCandidates = @(
    $ProductionWorkingDirectoryCandidatesJson | ConvertFrom-Json
)
if ($ConfigSearchRoots.Count -eq 0) {
    throw "ConfigSearchRootsJson must contain at least one path"
}

$fileChecks = @()
$gateError = $null
try {
    foreach ($Property in $ExpectedFileHashes.PSObject.Properties) {
        $Name = [string]$Property.Name
        $ExpectedHash = ([string]$Property.Value).ToUpperInvariant()
        $Path = Join-Path $PSScriptRoot $Name
        $tokens = $null
        $parseErrors = $null
        $ast = $null
        $actualHash = $null
        $fileError = $null
        try {
            if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
                throw "Required discovery file does not exist: $Path"
            }
            $actualHash = (
                Get-FileHash -LiteralPath $Path -Algorithm SHA256
            ).Hash
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $Path,
                [ref]$tokens,
                [ref]$parseErrors
            )
        } catch {
            $fileError = $_.Exception.Message
        }
        $parseRows = @(
            $parseErrors | ForEach-Object { Convert-ParserError $_ }
        )
        $fileChecks += [pscustomobject]@{
            name = $Name
            path = [IO.Path]::GetFullPath($Path)
            expected_sha256 = $ExpectedHash
            actual_sha256 = $actualHash
            hash_matches = (
                $actualHash -and
                $actualHash.ToUpperInvariant() -eq $ExpectedHash
            )
            parser_passed = ($null -ne $ast -and $parseRows.Count -eq 0)
            parser_error_count = $parseRows.Count
            parser_errors = $parseRows
            error = $fileError
        }
    }
} catch {
    $gateError = $_.Exception.Message
}

$wrapperName = [IO.Path]::GetFileName($DiscoveryScriptPath)
$wrapperCheck = @($fileChecks | Where-Object { $_.name -eq $wrapperName } | Select-Object -First 1)
$allHashesMatch = (
    $fileChecks.Count -gt 0 -and
    @($fileChecks | Where-Object { -not $_.hash_matches }).Count -eq 0
)
$allParsersPassed = (
    $fileChecks.Count -gt 0 -and
    @($fileChecks | Where-Object { -not $_.parser_passed }).Count -eq 0
)
$gatePassed = (-not $gateError -and $allHashesMatch -and $allParsersPassed)

$discovery = $null
$discoveryError = $null
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
        file_checks = $fileChecks
        expected_file_count = @($ExpectedFileHashes.PSObject.Properties).Count
        checked_file_count = $fileChecks.Count
        hash_matches = $allHashesMatch
        parser_passed = $allParsersPassed
        parser_error_count = @($fileChecks | ForEach-Object { $_.parser_error_count } | Measure-Object -Sum).Sum
        actual_sha256 = if ($wrapperCheck.Count -eq 1) { $wrapperCheck[0].actual_sha256 } else { $null }
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

$result | ConvertTo-Json -Depth 18
if (-not $gatePassed -or ($RunDiscovery -and ($discoveryError -or $null -eq $discovery))) {
    exit 1
}
exit 0
