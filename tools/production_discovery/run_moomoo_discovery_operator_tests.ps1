[CmdletBinding()]
param(
    [string]$PythonExecutable = "python",
    [string]$PowerShellExecutable = "powershell.exe",
    [string]$DiagnosticsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Discovery = Join-Path $Root "moomoo_production_readonly_discovery_v4.ps1"
$Gate = Join-Path $Root "moomoo_discovery_v4_gate.ps1"
$Operator = Join-Path $Root "moomoo_discovery_operator.py"
$Tests = Join-Path $Root "test_moomoo_discovery_operator.py"
$Validator = Join-Path $Root "validate_moomoo_discovery_operator.py"

$RequestedEngine = [IO.Path]::GetFileName($PowerShellExecutable).ToLowerInvariant()
if ($RequestedEngine -eq "powershell.exe" -and $PSVersionTable.PSVersion.Major -ne 5) {
    throw "powershell.exe validation must run inside Windows PowerShell 5.1"
}
if ($RequestedEngine -eq "pwsh.exe" -and $PSVersionTable.PSVersion.Major -lt 7) {
    throw "pwsh.exe validation must run inside PowerShell 7 or newer"
}

if ($DiagnosticsDir) {
    $DiagnosticsDir = [IO.Path]::GetFullPath($DiagnosticsDir)
    [IO.Directory]::CreateDirectory($DiagnosticsDir) | Out-Null
}

function Write-DiagnosticText {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Text
    )
    if (-not $DiagnosticsDir) { return }
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        (Join-Path $DiagnosticsDir $Name),
        [string]$Text,
        $Encoding
    )
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $Root
    )
    $PreviousPreference = $ErrorActionPreference
    $Raw = @()
    $ExitCode = $null
    Push-Location $WorkingDirectory
    try {
        $ErrorActionPreference = "Continue"
        $Raw = @(& $FilePath @Arguments 2>&1)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
        Pop-Location
    }
    $Lines = @(
        $Raw | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            } else {
                [string]$_
            }
        }
    )
    $Text = $Lines -join [Environment]::NewLine
    Write-DiagnosticText -Name ($Name + ".log") -Text $Text
    if ($ExitCode -ne 0) {
        throw "$Name failed with exit code $ExitCode. Output: $Text"
    }
    [pscustomobject]@{
        exit_code = $ExitCode
        lines = $Lines
        text = $Text
    }
}

$PowerShellFiles = @(
    $Discovery,
    (Join-Path $Root "moomoo_discovery_v4_common.ps1"),
    (Join-Path $Root "moomoo_discovery_v4_runtime.ps1"),
    (Join-Path $Root "moomoo_discovery_v4_storage.ps1"),
    $Gate
)
$ParserRows = @()
foreach ($Path in $PowerShellFiles) {
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $Path,
        [ref]$Tokens,
        [ref]$Errors
    ) | Out-Null
    $ErrorRows = @($Errors)
    $ParserRows += [pscustomobject]@{
        path = $Path
        error_count = $ErrorRows.Count
        errors = @($ErrorRows | ForEach-Object { $_.Message })
    }
    if ($ErrorRows.Count -gt 0) {
        throw "PowerShell parser errors in ${Path}: $($ErrorRows.Message -join '; ')"
    }
}
Write-DiagnosticText -Name "powershell-parser.json" -Text (
    $ParserRows | ConvertTo-Json -Depth 8
)

$PythonFiles = @(
    $Operator,
    (Join-Path $Root "moomoo_operator_common.py"),
    (Join-Path $Root "moomoo_operator_review.py"),
    (Join-Path $Root "moomoo_operator_cli.py"),
    $Tests,
    (Join-Path $Root "test_bundle_builder.py"),
    $Validator
)
Invoke-NativeCapture -Name "python-compile" `
    -FilePath $PythonExecutable `
    -Arguments (@("-m", "py_compile") + $PythonFiles) | Out-Null
Invoke-NativeCapture -Name "static-validation" `
    -FilePath $PythonExecutable `
    -Arguments @($Validator) | Out-Null
Invoke-NativeCapture -Name "python-tests" `
    -FilePath $PythonExecutable `
    -Arguments @("-m", "unittest", "-v") | Out-Null

$RepoRoot = (
    Invoke-NativeCapture -Name "git-root" `
        -FilePath "git" `
        -Arguments @("-C", $Root, "rev-parse", "--show-toplevel")
).text.Trim()
$ExpectedHead = (
    Invoke-NativeCapture -Name "git-head" `
        -FilePath "git" `
        -Arguments @("-C", $RepoRoot, "rev-parse", "HEAD")
).text.Trim()
$ExpectedRemote = (
    Invoke-NativeCapture -Name "git-remote" `
        -FilePath "git" `
        -Arguments @("-C", $RepoRoot, "remote", "get-url", "origin")
).text.Trim()

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

    $ExpectedFileHashes = [ordered]@{}
    foreach ($Name in @(
        "moomoo_production_readonly_discovery_v4.ps1",
        "moomoo_discovery_v4_common.ps1",
        "moomoo_discovery_v4_runtime.ps1",
        "moomoo_discovery_v4_storage.ps1"
    )) {
        $ExpectedFileHashes[$Name] = (
            Get-FileHash -LiteralPath (Join-Path $Root $Name) -Algorithm SHA256
        ).Hash
    }

    $Utf8 = New-Object System.Text.UTF8Encoding($false)
    $ExpectedFileHashesJson = ConvertTo-Json -Compress -InputObject $ExpectedFileHashes
    $ConfigSearchRootsJson = ConvertTo-Json -Compress -InputObject @($Runtime)
    $RuntimeCandidatesJson = ConvertTo-Json -Compress -InputObject @($Runtime)
    $ExpectedFileHashesBase64 = [Convert]::ToBase64String(
        $Utf8.GetBytes($ExpectedFileHashesJson)
    )
    $ConfigSearchRootsBase64 = [Convert]::ToBase64String(
        $Utf8.GetBytes($ConfigSearchRootsJson)
    )
    $RuntimeCandidatesBase64 = [Convert]::ToBase64String(
        $Utf8.GetBytes($RuntimeCandidatesJson)
    )

    $GateArguments = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $Gate,
        "-ExpectedFileHashesBase64", $ExpectedFileHashesBase64,
        "-RunDiscovery",
        "-RepoPath", $RepoRoot,
        "-ProtectedCheckoutPath", $RepoRoot,
        "-ExpectedHead", $ExpectedHead,
        "-ExpectedRemote", $ExpectedRemote,
        "-ConfigSearchRootsBase64", $ConfigSearchRootsBase64,
        "-ProductionWorkingDirectoryCandidatesBase64", $RuntimeCandidatesBase64
    )
    $GateResult = Invoke-NativeCapture -Name "synthetic-gated-discovery" `
        -FilePath $PowerShellExecutable `
        -Arguments $GateArguments
    try {
        $Payload = $GateResult.text | ConvertFrom-Json
    } catch {
        throw "Synthetic gated discovery emitted invalid JSON: $($_.Exception.Message)"
    }
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
    if ($null -eq $Payload.discovery_diagnostics) {
        throw "Gate did not retain discovery stream diagnostics"
    }

    $RepositorySnapshot = $Payload.discovery.repositories.preflight_candidate
    if ($null -eq $RepositorySnapshot) {
        throw "Synthetic discovery omitted the verified repository snapshot"
    }
    if ($RepositorySnapshot.PSObject.Properties.Name -contains "error_type") {
        throw "Verified repository snapshot failed: $($RepositorySnapshot.error)"
    }
    if ([string]$RepositorySnapshot.status -ne "PASS") {
        throw "Verified repository snapshot status was $($RepositorySnapshot.status), expected PASS"
    }
    if (-not [bool]$RepositorySnapshot.clean) {
        throw "Verified repository snapshot did not report a clean checkout"
    }
    if ([string]$RepositorySnapshot.head -ne $ExpectedHead) {
        throw "Verified repository snapshot HEAD did not match the tested merge ref"
    }
    if (-not [bool]$RepositorySnapshot.origin_matches) {
        throw "Verified repository snapshot origin did not match the expected remote"
    }
    if (-not [bool]$RepositorySnapshot.drill_script_exists) {
        throw "Verified repository snapshot did not contain the recovery drill script"
    }

    $HumanExistingMappings = @(
        $Payload.discovery.runtime_path_evidence |
            Where-Object {
                $_.runtime_human_asserted -eq $true -and
                $_.database_exists -eq $true
            }
    )
    $ExpectedDbPath = [IO.Path]::GetFullPath([string]$Db)
    $MappingDebug = [pscustomobject]@{
        expected_database_path = $ExpectedDbPath
        human_existing_mapping_count = $HumanExistingMappings.Count
        mappings = @($HumanExistingMappings | ForEach-Object {
            [pscustomobject]@{
                resolved_database_path = [string]$_.resolved_database_path
                normalized_database_path = if ($_.resolved_database_path) {
                    [IO.Path]::GetFullPath([string]$_.resolved_database_path)
                } else {
                    $null
                }
                runtime_human_asserted = $_.runtime_human_asserted
                database_exists = $_.database_exists
            }
        })
    }
    Write-DiagnosticText -Name "runtime-mapping-debug.json" -Text (
        $MappingDebug | ConvertTo-Json -Depth 8
    )
    if ($HumanExistingMappings.Count -ne 1) {
        throw "Expected one human-asserted existing runtime mapping, got $($HumanExistingMappings.Count)"
    }
    $ResolvedDbPath = [IO.Path]::GetFullPath(
        [string]$HumanExistingMappings[0].resolved_database_path
    )
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        $ResolvedDbPath,
        $ExpectedDbPath
    )) {
        throw "Resolved DB path mismatch: expected $ExpectedDbPath, got $ResolvedDbPath"
    }

    $OperatorVersion = (
        Invoke-NativeCapture -Name "operator-version" `
            -FilePath $PythonExecutable `
            -Arguments @($Operator, "--version")
    ).text.Trim()
    if ($OperatorVersion -ne "1.2.1") {
        throw "Operator version check failed: expected 1.2.1, got $OperatorVersion"
    }

    Write-DiagnosticText -Name "environment.json" -Text (
        [pscustomobject]@{
            powershell_version = $PSVersionTable.PSVersion.ToString()
            powershell_edition = if (
                $PSVersionTable.PSObject.Properties.Name -contains "PSEdition"
            ) { [string]$PSVersionTable.PSEdition } else { "Desktop" }
            python_executable = $PythonExecutable
            nested_powershell_executable = $PowerShellExecutable
            repository_root = $RepoRoot
            tested_head = $ExpectedHead
            native_json_transport = "utf8_base64"
        } | ConvertTo-Json -Depth 6
    )
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}

Write-Host "moomoo discovery operator validation PASS"
