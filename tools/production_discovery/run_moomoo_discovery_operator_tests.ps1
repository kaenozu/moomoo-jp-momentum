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
    $GateArguments = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $Gate,
        "-ExpectedFileHashesJson", (ConvertTo-Json -Compress -InputObject $ExpectedFileHashes),
        "-RunDiscovery",
        "-RepoPath", $RepoRoot,
        "-ProtectedCheckoutPath", $RepoRoot,
        "-ExpectedHead", $ExpectedHead,
        "-ExpectedRemote", $ExpectedRemote,
        "-ConfigSearchRootsJson", (ConvertTo-Json -Compress -InputObject @($Runtime)),
        "-ProductionWorkingDirectoryCandidatesJson", (ConvertTo-Json -Compress -InputObject @($Runtime))
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

    $Mapping = @(
        $Payload.discovery.runtime_path_evidence |
            Where-Object {
                [bool]$_.runtime_human_asserted -and
                [bool]$_.database_exists -and
                $_.resolved_database_path -eq $Db
            }
    )
    if ($Mapping.Count -ne 1) {
        throw "Expected one human-asserted existing runtime mapping, got $($Mapping.Count)"
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
        } | ConvertTo-Json -Depth 6
    )
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}

Write-Host "moomoo discovery operator validation PASS"
