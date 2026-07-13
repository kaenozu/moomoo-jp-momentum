[CmdletBinding()]
param(
    [string]$PythonExecutable = "python",
    [string]$PowerShellExecutable = "powershell.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Discovery = Join-Path $Root "moomoo_production_readonly_discovery_v4.ps1"
$Gate = Join-Path $Root "moomoo_discovery_v4_gate.ps1"
$Operator = Join-Path $Root "moomoo_discovery_operator.py"
$Tests = Join-Path $Root "test_moomoo_discovery_operator.py"

foreach ($Path in @($Discovery, $Gate)) {
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $Path,
        [ref]$Tokens,
        [ref]$Errors
    ) | Out-Null
    if ($Errors.Count -gt 0) {
        $Errors | ForEach-Object { Write-Error $_.Message }
        throw "PowerShell parser errors in $Path"
    }
}

& $PythonExecutable -m py_compile $Operator $Tests
if ($LASTEXITCODE -ne 0) {
    throw "Python compile validation failed"
}

Push-Location $Root
try {
    & $PythonExecutable -m unittest -v
    if ($LASTEXITCODE -ne 0) {
        throw "Python regression tests failed"
    }
} finally {
    Pop-Location
}

$RepoRoot = (& git -C $Root rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve repository root" }
$ExpectedHead = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve repository HEAD" }
$ExpectedRemote = (& git -C $RepoRoot remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve repository remote" }

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "moomoo-discovery-v4-" + [Guid]::NewGuid().ToString("N")
)
$Runtime = Join-Path $TempRoot "runtime"
$Data = Join-Path $Runtime "data"
$Config = Join-Path $Runtime "config.yaml"
$Db = Join-Path $Data "moomoo.db"

try {
    New-Item -ItemType Directory -Path $Data -Force | Out-Null
    [IO.File]::WriteAllBytes($Db, [byte[]](1, 2, 3, 4))
    @"
database:
  path: data/moomoo.db
database_backup:
  enabled: true
  directory: backups
cycle_control:
  enabled: true
virtual_trade:
  enabled: true
"@ | Set-Content -LiteralPath $Config -Encoding UTF8

    $ExpectedDiscoveryHash = (
        Get-FileHash -LiteralPath $Discovery -Algorithm SHA256
    ).Hash
    $ConfigRootsJson = ConvertTo-Json -Compress -InputObject @($Runtime)
    $RuntimeJson = ConvertTo-Json -Compress -InputObject @($Runtime)

    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Raw = & $PowerShellExecutable -NoProfile -NonInteractive `
            -ExecutionPolicy Bypass `
            -File $Gate `
            -ExpectedDiscoverySha256 $ExpectedDiscoveryHash `
            -RunDiscovery `
            -RepoPath $RepoRoot `
            -ProtectedCheckoutPath $RepoRoot `
            -ExpectedHead $ExpectedHead `
            -ExpectedRemote $ExpectedRemote `
            -ConfigSearchRootsJson $ConfigRootsJson `
            -ProductionWorkingDirectoryCandidatesJson $RuntimeJson 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }

    if ($ExitCode -ne 0) {
        throw "Synthetic gated discovery failed: $($Raw -join "`n")"
    }
    $Payload = ($Raw -join "`n") | ConvertFrom-Json
    if (-not [bool]$Payload.gate.gate_passed) {
        throw "Synthetic parser/hash gate did not pass"
    }
    if (-not [bool]$Payload.gate.discovery_executed) {
        throw "Synthetic discovery did not execute"
    }
    if ([int]$Payload.discovery.schema_version -ne 4) {
        throw "Unexpected discovery schema"
    }
    if ([bool]$Payload.discovery.safety.sqlite_connection_performed) {
        throw "Discovery safety contract reported a SQLite connection"
    }
    if ([bool]$Payload.discovery.authorization.preflight_authorized) {
        throw "Discovery incorrectly authorized preflight"
    }

    $Mapping = @(
        $Payload.discovery.runtime_path_evidence |
            Where-Object {
                [bool]$_.runtime_authoritative -and
                [bool]$_.database_exists -and
                $_.resolved_database_path -eq $Db
            }
    )
    if ($Mapping.Count -ne 1) {
        throw "Expected one authoritative existing runtime mapping, got $($Mapping.Count)"
    }

    $OperatorHash = (& $PythonExecutable $Operator --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $OperatorHash -ne "1.2.0") {
        throw "Operator version check failed"
    }
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}

Write-Host "moomoo discovery operator validation PASS"
