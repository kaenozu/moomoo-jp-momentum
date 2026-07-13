[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("powershell", "pwsh")]
    [string]$ShellExecutable,

    [Parameter(Mandatory = $true)]
    [string]$CaseName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-DrillProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $raw = & $ShellExecutable -NoProfile -NonInteractive `
        -ExecutionPolicy Bypass @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = ($raw -join "`n")
    }
}

function Assert-Success {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ([int]$Result.ExitCode -ne 0) {
        throw "$Message (exit=$($Result.ExitCode)):`n$($Result.Text)"
    }
}

function Assert-Failure {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ([int]$Result.ExitCode -eq 0) {
        throw "$Message unexpectedly succeeded:`n$($Result.Text)"
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

$RepoRoot = (Resolve-Path ".").Path
$DrillScript = (Resolve-Path ".\scripts\sqlite_backup_recovery_drill.ps1").Path
$HeadSha = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $HeadSha) {
    throw "could not resolve repository HEAD"
}

$Root = Join-Path $env:RUNNER_TEMP (
    "sqlite recovery drill {0} {1}" -f $env:GITHUB_RUN_ID, $CaseName
)
if (Test-Path -LiteralPath $Root) {
    Remove-Item -LiteralPath $Root -Recurse -Force
}
New-Item -ItemType Directory -Path $Root | Out-Null

$LiveDb = Join-Path $Root "live database\moomoo.db"
$Config = Join-Path $Root "production config.yaml"
$ProductionBackupDir = Join-Path $Root "configured production backups"
$ConfigHashBefore = $null

$FixtureScript = @'
from pathlib import Path
import sqlite3
import sys
import yaml
from src.models import CREATE_TABLES_SQL

db_path = Path(sys.argv[1]).resolve()
config_path = Path(sys.argv[2]).resolve()
production_backup_dir = Path(sys.argv[3]).resolve()
db_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(db_path) as connection:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(CREATE_TABLES_SQL)
    connection.commit()
payload = {
    "database": {"path": str(db_path)},
    "database_backup": {
        "enabled": True,
        "directory": str(production_backup_dir),
        "retain_daily": 7,
        "retain_weekly": 4,
        "retain_pre_cycle": 7,
        "retain_post_cycle": 7,
        "verify_after_backup": True,
    },
    "cycle_control": {"enabled": True},
    "virtual_trade": {
        "enabled": True,
        "initial_cash": 150000,
        "commission": 0,
    },
}
config_path.write_text(
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
'@

$fixtureRaw = & python -c $FixtureScript $LiveDb $Config $ProductionBackupDir 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "fixture creation failed:`n$($fixtureRaw -join '`n')"
}
$ConfigHashBefore = Get-Sha256 $Config
$LiveDbHashBefore = Get-Sha256 $LiveDb

$BaseArguments = @(
    "-File", $DrillScript,
    "-ExpectedHead", $HeadSha,
    "-ProductionConfig", $Config,
    "-Strategy", "momentum"
)

# Wrong SHA must fail before any drill path is created.
$WrongShaEvidence = Join-Path $Root "wrong sha evidence"
$WrongShaSecondary = Join-Path $Root "wrong sha secondary"
$WrongShaRestore = Join-Path $Root "wrong sha restore\restored.db"
$wrongShaResult = Invoke-DrillProcess -Arguments @(
    "-File", $DrillScript,
    "-ExpectedHead", ("0" * 40),
    "-ProductionConfig", $Config,
    "-EvidenceDir", $WrongShaEvidence,
    "-SecondaryDir", $WrongShaSecondary,
    "-RestorePath", $WrongShaRestore,
    "-Strategy", "momentum",
    "-PreflightOnly"
)
Assert-Failure $wrongShaResult "wrong-SHA guard"
if (
    (Test-Path -LiteralPath $WrongShaEvidence) -or
    (Test-Path -LiteralPath $WrongShaSecondary) -or
    (Test-Path -LiteralPath $WrongShaRestore)
) {
    throw "wrong-SHA guard created an output path"
}

# Case-insensitive repository containment must be rejected on Windows.
$RepoEvidence = (Join-Path $RepoRoot "temporary recovery evidence").ToUpperInvariant()
$repoPathResult = Invoke-DrillProcess -Arguments @(
    $BaseArguments + @(
        "-EvidenceDir", $RepoEvidence,
        "-SecondaryDir", (Join-Path $Root "repo guard secondary"),
        "-RestorePath", (Join-Path $Root "repo guard restore\restored.db"),
        "-PreflightOnly"
    )
)
Assert-Failure $repoPathResult "repository-path guard"
if (Test-Path -LiteralPath $RepoEvidence) {
    throw "repository-path guard created evidence inside the repository"
}

# Preflight must be read-only and create no requested output paths.
$PreflightEvidence = Join-Path $Root "preflight evidence"
$PreflightSecondary = Join-Path $Root "preflight secondary"
$PreflightRestore = Join-Path $Root "preflight restore\restored.db"
$preflightResult = Invoke-DrillProcess -Arguments @(
    $BaseArguments + @(
        "-EvidenceDir", $PreflightEvidence,
        "-SecondaryDir", $PreflightSecondary,
        "-RestorePath", $PreflightRestore,
        "-PreflightOnly"
    )
)
Assert-Success $preflightResult "read-only preflight"
$preflight = $preflightResult.Text | ConvertFrom-Json
if ($preflight.status -ne "preflight_only") {
    throw "unexpected preflight status: $($preflight.status)"
}
if (
    (Test-Path -LiteralPath $PreflightEvidence) -or
    (Test-Path -LiteralPath $PreflightSecondary) -or
    (Test-Path -LiteralPath $PreflightRestore)
) {
    throw "preflight created an output path"
}
if ((Get-Sha256 $Config) -ne $ConfigHashBefore) {
    throw "preflight changed the production config"
}
if ((Get-Sha256 $LiveDb) -ne $LiveDbHashBefore) {
    throw "preflight changed the live database"
}

# An existing restore target must fail before evidence or secondary output is created.
$ExistingRestore = Join-Path $Root "existing restore\restored.db"
New-Item -ItemType Directory -Path (Split-Path $ExistingRestore -Parent) | Out-Null
Set-Content -LiteralPath $ExistingRestore -Value "must remain" -Encoding ascii
$ExistingRestoreHash = Get-Sha256 $ExistingRestore
$ExistingEvidence = Join-Path $Root "existing target evidence"
$ExistingSecondary = Join-Path $Root "existing target secondary"
$existingResult = Invoke-DrillProcess -Arguments @(
    $BaseArguments + @(
        "-EvidenceDir", $ExistingEvidence,
        "-SecondaryDir", $ExistingSecondary,
        "-RestorePath", $ExistingRestore,
        "-ConfirmProductionExecution"
    )
)
Assert-Failure $existingResult "existing-restore guard"
if (
    (Test-Path -LiteralPath $ExistingEvidence) -or
    (Test-Path -LiteralPath $ExistingSecondary)
) {
    throw "existing-restore guard created drill output"
}
if ((Get-Sha256 $ExistingRestore) -ne $ExistingRestoreHash) {
    throw "existing-restore guard modified the existing target"
}

# Full temporary drill in the selected Windows PowerShell implementation.
$Evidence = Join-Path $Root "full evidence"
$Secondary = Join-Path $Root "full secondary"
$Restore = Join-Path $Root "full restore\moomoo-restored.db"
$fullResult = Invoke-DrillProcess -Arguments @(
    $BaseArguments + @(
        "-EvidenceDir", $Evidence,
        "-SecondaryDir", $Secondary,
        "-RestorePath", $Restore,
        "-ConfirmProductionExecution"
    )
)
Assert-Success $fullResult "full temporary recovery drill"

$ResultPath = Join-Path $Evidence "70-final-result.json"
if (-not (Test-Path -LiteralPath $ResultPath -PathType Leaf)) {
    throw "full drill did not create final evidence"
}
$result = Get-Content -Raw -LiteralPath $ResultPath | ConvertFrom-Json
if ($result.status -ne "pending_operator_review") {
    throw "unexpected full-drill status: $($result.status)"
}
if ([int]$result.integrity_errors -ne 0) {
    throw "full drill reported integrity errors"
}
if (-not [bool]$result.live_state_unchanged) {
    throw "full drill changed the live state"
}
if (-not [bool]$result.restored_state_matches) {
    throw "full drill restored state does not match"
}
if ([int]$result.corrupt_verify_exit_code -eq 0) {
    throw "corrupt backup was accepted by verify"
}
if ([int]$result.corrupt_restore_dry_run_exit_code -eq 0) {
    throw "corrupt backup was accepted by restore dry-run"
}
if (-not (Test-Path -LiteralPath $Restore -PathType Leaf)) {
    throw "full drill restore file is missing"
}
if (Test-Path -LiteralPath $ProductionBackupDir) {
    throw "full drill used the configured production backup directory"
}
if ((Get-Sha256 $Config) -ne $ConfigHashBefore) {
    throw "full drill changed the production config"
}
if ((Get-Sha256 $LiveDb) -ne $LiveDbHashBefore) {
    throw "full drill changed the live database"
}

$gitStatus = & git status --porcelain
if ($LASTEXITCODE -ne 0) {
    throw "could not read final git status"
}
if ($gitStatus) {
    throw "Windows drill left repository artifacts:`n$($gitStatus -join '`n')"
}

[pscustomobject]@{
    case = $CaseName
    shell = $ShellExecutable
    shell_version = $PSVersionTable.PSVersion.ToString()
    status = "passed"
    preflight_read_only = $true
    wrong_sha_rejected = $true
    repository_path_rejected = $true
    existing_restore_rejected = $true
    full_drill_completed = $true
    production_backup_directory_unused = $true
} | ConvertTo-Json -Depth 4
