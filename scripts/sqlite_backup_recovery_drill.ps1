[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead,

    [string]$ProductionConfig = ".\config.yaml",

    [string]$ProductionWorkingDirectory = "",

    [Parameter(Mandatory = $true)]
    [string]$EvidenceDir,

    [Parameter(Mandatory = $true)]
    [string]$SecondaryDir,

    [Parameter(Mandatory = $true)]
    [string]$RestorePath,

    [string]$Strategy = "momentum",

    [string]$Python = "python",

    [switch]$PreflightOnly,

    [switch]$ConfirmProductionExecution
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($fullPath)
    if ($fullPath -eq $root) {
        return $root
    }
    return $fullPath.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Assert-DifferentPath {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $leftPath = Get-NormalizedPath $Left
    $rightPath = Get-NormalizedPath $Right
    if ($leftPath.Equals($rightPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw $Message
    }
}

function Assert-OutsideRepository {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    $candidate = Get-NormalizedPath $Path
    $root = Get-NormalizedPath $RepositoryRoot
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if (
        $candidate.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
        $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "path must be outside the repository: $candidate"
    }
}

function Invoke-PythonJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [string]$LogPath
    )

    $raw = & $Python @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($LogPath) {
        $raw | Set-Content -Encoding utf8 -LiteralPath $LogPath
    }
    if ($exitCode -ne 0) {
        throw "$FailureMessage (exit=$exitCode): $($raw -join '`n')"
    }
    return (($raw -join "`n") | ConvertFrom-Json)
}

if ($PreflightOnly -and $ConfirmProductionExecution) {
    throw "choose exactly one mode: -PreflightOnly or -ConfirmProductionExecution"
}

$InvocationDirectory = Get-NormalizedPath (Get-Location).Path
$repoRootRaw = & git -C $PSScriptRoot rev-parse --show-toplevel 2>&1
$repoRootExit = $LASTEXITCODE
if ($repoRootExit -ne 0) {
    throw ($repoRootRaw -join "`n")
}
$RepoRoot = Get-NormalizedPath (($repoRootRaw -join "").Trim())

if ([IO.Path]::IsPathRooted($ProductionConfig)) {
    $ProductionConfig = (Resolve-Path -LiteralPath $ProductionConfig).Path
} else {
    $ProductionConfig = (
        Resolve-Path -LiteralPath (Join-Path $RepoRoot $ProductionConfig)
    ).Path
}

if ([string]::IsNullOrWhiteSpace($ProductionWorkingDirectory)) {
    $ProductionWorkingDirectory = $RepoRoot
} elseif (-not [IO.Path]::IsPathRooted($ProductionWorkingDirectory)) {
    $ProductionWorkingDirectory = Join-Path $RepoRoot $ProductionWorkingDirectory
}
if (-not (Test-Path -LiteralPath $ProductionWorkingDirectory -PathType Container)) {
    throw "production working directory does not exist: $ProductionWorkingDirectory"
}
$ProductionWorkingDirectory = Get-NormalizedPath (
    (Resolve-Path -LiteralPath $ProductionWorkingDirectory).Path
)

if (-not [IO.Path]::IsPathRooted($EvidenceDir)) {
    $EvidenceDir = Join-Path $InvocationDirectory $EvidenceDir
}
if (-not [IO.Path]::IsPathRooted($SecondaryDir)) {
    $SecondaryDir = Join-Path $InvocationDirectory $SecondaryDir
}
if (-not [IO.Path]::IsPathRooted($RestorePath)) {
    $RestorePath = Join-Path $InvocationDirectory $RestorePath
}
$EvidenceDir = Get-NormalizedPath $EvidenceDir
$SecondaryDir = Get-NormalizedPath $SecondaryDir
$RestorePath = Get-NormalizedPath $RestorePath

Set-Location -LiteralPath $RepoRoot

if ($ExpectedHead -like "<*") {
    throw "replace ExpectedHead with the explicitly approved Git SHA"
}
if ($ExpectedHead -notmatch "^[0-9a-fA-F]{40}$") {
    throw "ExpectedHead must be an exact 40-character Git SHA"
}

$headRaw = & git -C $RepoRoot rev-parse HEAD 2>&1
$headExit = $LASTEXITCODE
if ($headExit -ne 0) {
    throw ($headRaw -join "`n")
}
$HeadSha = ($headRaw -join "").Trim()
if ($HeadSha -ne $ExpectedHead) {
    throw "HEAD differs from approved SHA: expected=$ExpectedHead actual=$HeadSha"
}

$statusRaw = & git -C $RepoRoot status --porcelain 2>&1
$statusExit = $LASTEXITCODE
if ($statusExit -ne 0) {
    throw ($statusRaw -join "`n")
}
if ($statusRaw) {
    throw "working tree is not clean"
}

Assert-OutsideRepository $EvidenceDir $RepoRoot
Assert-OutsideRepository $SecondaryDir $RepoRoot
Assert-OutsideRepository $RestorePath $RepoRoot
Assert-DifferentPath $EvidenceDir $SecondaryDir "evidence and secondary paths are equal"
Assert-DifferentPath $EvidenceDir $RestorePath "evidence and restore paths are equal"
Assert-DifferentPath $SecondaryDir $RestorePath "secondary and restore paths are equal"

$ConfigInfoScript = @'
from pathlib import Path
from src.config import Config
import json
import sys

config_path = Path(sys.argv[1]).resolve()
working_directory = Path(sys.argv[2]).resolve()
if not working_directory.is_dir():
    raise SystemExit(f"production working directory not found: {working_directory}")

config = Config(str(config_path))

def resolve_runtime_path(value):
    path = Path(str(value))
    if not path.is_absolute():
        path = working_directory / path
    return path.resolve()

database_path_setting = str(config.database_path)
backup_directory_setting = str(
    config.get("database_backup.directory", "backups")
)

print(json.dumps({
    "config_path": str(config_path),
    "production_working_directory": str(working_directory),
    "database_path_setting": database_path_setting,
    "database_path": str(resolve_runtime_path(database_path_setting)),
    "database_backup_enabled": config.get("database_backup.enabled", False),
    "database_backup_directory_setting": backup_directory_setting,
    "database_backup_directory": str(
        resolve_runtime_path(backup_directory_setting)
    ),
    "cycle_control_enabled": config.get("cycle_control.enabled", False),
}, ensure_ascii=False))
'@

$configInfoRaw = $ConfigInfoScript | & $Python - `
    $ProductionConfig $ProductionWorkingDirectory 2>&1
$configInfoExit = $LASTEXITCODE
if ($configInfoExit -ne 0) {
    throw ($configInfoRaw -join "`n")
}
$ConfigInfo = ($configInfoRaw -join "`n") | ConvertFrom-Json

$LiveDb = Get-NormalizedPath ([string]$ConfigInfo.database_path)
$ConfiguredBackupDir = Get-NormalizedPath (
    [string]$ConfigInfo.database_backup_directory
)
if (-not (Test-Path -LiteralPath $LiveDb -PathType Leaf)) {
    throw "live DB does not exist: $LiveDb"
}
Assert-DifferentPath $LiveDb $RestorePath "restore destination equals live DB"

$JournalModeScript = @'
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1]).resolve()
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    print(connection.execute("PRAGMA journal_mode").fetchone()[0])
'@

$journalRaw = $JournalModeScript | & $Python - $LiveDb 2>&1
$journalExit = $LASTEXITCODE
if ($journalExit -ne 0) {
    throw ($journalRaw -join "`n")
}
$JournalMode = ($journalRaw -join "").Trim()
$ProductionConfigHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionConfig
).Hash

$Preflight = [pscustomobject]@{
    status = if ($PreflightOnly) {
        "preflight_only"
    } else {
        "production_execution_requested"
    }
    head_sha = $HeadSha
    production_config_path = $ProductionConfig
    production_config_sha256 = $ProductionConfigHash
    production_working_directory = $ProductionWorkingDirectory
    configured_database_path = $ConfigInfo.database_path_setting
    live_db = $LiveDb
    configured_backup_directory_setting = (
        $ConfigInfo.database_backup_directory_setting
    )
    configured_backup_directory = $ConfiguredBackupDir
    production_database_backup_enabled = (
        $ConfigInfo.database_backup_enabled
    )
    production_cycle_control_enabled = $ConfigInfo.cycle_control_enabled
    evidence_directory = $EvidenceDir
    secondary_directory = $SecondaryDir
    restore_path = $RestorePath
    journal_mode = $JournalMode
    filesystem_space = @(
        Get-PSDrive -PSProvider FileSystem |
            Select-Object Name, Root, Used, Free
    )
    captured_at = (Get-Date).ToString("o")
}

if ($PreflightOnly) {
    $Preflight | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $ConfirmProductionExecution) {
    throw "use -PreflightOnly first; production execution requires -ConfirmProductionExecution"
}

if (Test-Path -LiteralPath $EvidenceDir) {
    throw "use a new evidence directory: $EvidenceDir"
}
if (Test-Path -LiteralPath $SecondaryDir) {
    throw "use a new secondary directory: $SecondaryDir"
}
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore destination already exists: $RestorePath"
}

New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Path (Split-Path $RestorePath -Parent) -Force |
    Out-Null
$Preflight | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding utf8 -LiteralPath "$EvidenceDir\00-preflight.json"

$PrimaryDrillDir = Get-NormalizedPath (
    Join-Path $EvidenceDir "primary-backup"
)
$DrillConfig = Get-NormalizedPath (
    Join-Path $EvidenceDir "drill-config.yaml"
)
Assert-OutsideRepository $PrimaryDrillDir $RepoRoot
Assert-OutsideRepository $DrillConfig $RepoRoot

$BuildDrillConfigScript = @'
from pathlib import Path
import sys
import yaml

production_config = Path(sys.argv[1]).resolve()
live_db = Path(sys.argv[2]).resolve()
primary_dir = Path(sys.argv[3]).resolve()
out_path = Path(sys.argv[4]).resolve()

payload = yaml.safe_load(production_config.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("production config must be a mapping")
payload.setdefault("database", {})["path"] = str(live_db)
backup = payload.setdefault("database_backup", {})
backup.update({
    "enabled": True,
    "directory": str(primary_dir),
    "retain_daily": 100,
    "retain_weekly": 100,
    "retain_pre_cycle": 100,
    "retain_post_cycle": 100,
    "verify_after_backup": True,
})
out_path.write_text(
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
'@

$buildRaw = $BuildDrillConfigScript | & $Python - `
    $ProductionConfig $LiveDb $PrimaryDrillDir $DrillConfig 2>&1
$buildExit = $LASTEXITCODE
if ($buildExit -ne 0) {
    throw ($buildRaw -join "`n")
}
if (-not (Test-Path -LiteralPath $DrillConfig -PathType Leaf)) {
    throw "isolated drill config was not created"
}
$DrillConfigHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $DrillConfig
).Hash

$SnapshotScript = @'
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
strategy = sys.argv[2]
if not path.is_file():
    raise SystemExit(f"database not found: {path}")

with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    connection.row_factory = sqlite3.Row
    quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    if quick != ["ok"]:
        raise SystemExit(f"quick_check failed: {quick}")

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    def scalar(sql: str, params: tuple[object, ...] = ()):
        row = connection.execute(sql, params).fetchone()
        return None if row is None else row[0]

    latest_fill_date = None
    fill_count = None
    if "virtual_fills" in tables:
        latest_fill_date = scalar(
            "SELECT MAX(substr(filled_at, 1, 10)) FROM virtual_fills "
            "WHERE strategy_name = ?",
            (strategy,),
        )
        fill_count = scalar(
            "SELECT COUNT(*) FROM virtual_fills WHERE strategy_name = ?",
            (strategy,),
        )

    latest_equity = None
    equity_count = None
    if "virtual_equity_curve" in tables:
        row = connection.execute(
            "SELECT date, cash, position_value, total_equity "
            "FROM virtual_equity_curve WHERE strategy_name = ? "
            "ORDER BY date DESC LIMIT 1",
            (strategy,),
        ).fetchone()
        latest_equity = dict(row) if row is not None else None
        equity_count = scalar(
            "SELECT COUNT(*) FROM virtual_equity_curve WHERE strategy_name = ?",
            (strategy,),
        )

    order_counts = []
    if "virtual_orders" in tables:
        order_counts = [
            dict(row)
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM virtual_orders "
                "WHERE strategy_name = ? GROUP BY status ORDER BY status",
                (strategy,),
            )
        ]

    positions = []
    if "virtual_positions" in tables:
        positions = [
            dict(row)
            for row in connection.execute(
                "SELECT code, quantity, avg_cost, market_price, market_value, "
                "unrealized_pl, realized_pl FROM virtual_positions "
                "WHERE strategy_name = ? AND quantity > 0 ORDER BY code",
                (strategy,),
            )
        ]

    payload = {
        "strategy": strategy,
        "schema_version": int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        ),
        "quick_check": "ok",
        "latest_virtual_fill_date": latest_fill_date,
        "virtual_fill_count": fill_count,
        "latest_equity": latest_equity,
        "virtual_equity_row_count": equity_count,
        "virtual_order_counts": order_counts,
        "open_positions": positions,
    }

print(json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
))
'@

function Write-DbSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$DbPath,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )

    $snapshotRaw = $SnapshotScript | & $Python - $DbPath $Strategy 2>&1
    $snapshotExit = $LASTEXITCODE
    if ($snapshotExit -ne 0) {
        throw ($snapshotRaw -join "`n")
    }
    ($snapshotRaw -join "`n").Trim() |
        Set-Content -Encoding utf8 -LiteralPath $OutputPath
}

Write-DbSnapshot $LiveDb "$EvidenceDir\10-live-before.json"
$LiveDbHashBefore = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb
).Hash
$LiveWal = "${LiveDb}-wal"
$LiveWalPresentBefore = Test-Path -LiteralPath $LiveWal -PathType Leaf
$LiveWalHashBefore = if ($LiveWalPresentBefore) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else {
    $null
}

$BackupStarted = Get-Date
$BackupResult = Invoke-PythonJson `
    -Arguments @(
        "database_backup.py",
        "--config", $DrillConfig,
        "backup", "--kind", "daily"
    ) `
    -FailureMessage "backup failed" `
    -LogPath "$EvidenceDir\20-backup-output.txt"
$BackupElapsedSeconds = ((Get-Date) - $BackupStarted).TotalSeconds
if ($BackupResult.pruned_files.Count -ne 0) {
    throw "isolated drill backup unexpectedly pruned files"
}

$BackupPath = Get-NormalizedPath ([string]$BackupResult.backup_path)
$MetadataPath = Get-NormalizedPath ([string]$BackupResult.metadata_path)
if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw "backup missing"
}
if (-not (Test-Path -LiteralPath $MetadataPath -PathType Leaf)) {
    throw "metadata missing"
}
Assert-DifferentPath $BackupPath $LiveDb "backup path equals live DB"
Assert-DifferentPath $MetadataPath $LiveDb "metadata path equals live DB"

$null = Invoke-PythonJson `
    -Arguments @(
        "database_backup.py",
        "--config", $DrillConfig,
        "verify", $BackupPath
    ) `
    -FailureMessage "primary verify failed" `
    -LogPath "$EvidenceDir\21-primary-verify-output.txt"

$Metadata = Get-Content -Raw -LiteralPath $MetadataPath | ConvertFrom-Json
$BackupHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $BackupPath
).Hash.ToLowerInvariant()
if ($BackupHash -ne ([string]$Metadata.sha256).ToLowerInvariant()) {
    throw "primary backup hash differs from metadata"
}
if ((Get-NormalizedPath ([string]$Metadata.source_database_path)) -ne $LiveDb) {
    throw "metadata source DB path differs from approved live DB"
}
if ((Get-NormalizedPath ([string]$Metadata.backup_path)) -ne $BackupPath) {
    throw "metadata backup path differs from created backup"
}

New-Item -ItemType Directory -Path $SecondaryDir | Out-Null
$SecondaryBackup = Get-NormalizedPath (
    Join-Path $SecondaryDir (Split-Path $BackupPath -Leaf)
)
$SecondaryMetadata = Get-NormalizedPath "${SecondaryBackup}.json"
if (Test-Path -LiteralPath $SecondaryBackup) {
    throw "secondary backup exists"
}
if (Test-Path -LiteralPath $SecondaryMetadata) {
    throw "secondary metadata exists"
}
Copy-Item -LiteralPath $BackupPath -Destination $SecondaryBackup
Copy-Item -LiteralPath $MetadataPath -Destination $SecondaryMetadata

$SecondaryBackupHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryBackup
).Hash.ToLowerInvariant()
$PrimaryMetadataHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $MetadataPath
).Hash
$SecondaryMetadataHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryMetadata
).Hash
if ($SecondaryBackupHash -ne $BackupHash) {
    throw "secondary backup hash mismatch"
}
if ($SecondaryMetadataHash -ne $PrimaryMetadataHash) {
    throw "secondary metadata hash mismatch"
}

$null = Invoke-PythonJson `
    -Arguments @(
        "database_backup.py",
        "--config", $DrillConfig,
        "verify", $SecondaryBackup
    ) `
    -FailureMessage "secondary verify failed" `
    -LogPath "$EvidenceDir\30-secondary-verify-output.txt"

$DryRunResult = Invoke-PythonJson `
    -Arguments @(
        "database_backup.py",
        "--config", $DrillConfig,
        "restore", $SecondaryBackup, $RestorePath,
        "--strategy", $Strategy,
        "--dry-run"
    ) `
    -FailureMessage "restore dry-run failed" `
    -LogPath "$EvidenceDir\40-restore-dry-run-output.txt"
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore dry-run created destination"
}
if ([int]$DryRunResult.integrity_errors -ne 0) {
    throw "restore dry-run reported integrity errors"
}

$RestoreStarted = Get-Date
$RestoreResult = Invoke-PythonJson `
    -Arguments @(
        "database_backup.py",
        "--config", $DrillConfig,
        "restore", $SecondaryBackup, $RestorePath,
        "--strategy", $Strategy
    ) `
    -FailureMessage "restore failed" `
    -LogPath "$EvidenceDir\41-restore-output.txt"
$RestoreElapsedSeconds = ((Get-Date) - $RestoreStarted).TotalSeconds
if (-not (Test-Path -LiteralPath $RestorePath -PathType Leaf)) {
    throw "restored DB missing"
}
if ([int]$RestoreResult.integrity_errors -ne 0) {
    throw "restored DB reported integrity errors"
}

Write-DbSnapshot $LiveDb "$EvidenceDir\50-live-after.json"
Write-DbSnapshot $RestorePath "$EvidenceDir\51-restored.json"
$LiveBeforeText = (
    Get-Content -Raw -LiteralPath "$EvidenceDir\10-live-before.json"
).Trim()
$LiveAfterText = (
    Get-Content -Raw -LiteralPath "$EvidenceDir\50-live-after.json"
).Trim()
$RestoredText = (
    Get-Content -Raw -LiteralPath "$EvidenceDir\51-restored.json"
).Trim()
if ($LiveBeforeText -ne $LiveAfterText) {
    throw "live logical state changed"
}
if ($LiveAfterText -ne $RestoredText) {
    throw "restored logical state mismatch"
}

$LiveDbHashAfter = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb
).Hash
$LiveWalPresentAfter = Test-Path -LiteralPath $LiveWal -PathType Leaf
$LiveWalHashAfter = if ($LiveWalPresentAfter) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else {
    $null
}
if ($LiveDbHashBefore -ne $LiveDbHashAfter) {
    throw "live DB hash changed"
}
if ($LiveWalPresentBefore -ne $LiveWalPresentAfter) {
    throw "live WAL presence changed"
}
if ($LiveWalHashBefore -ne $LiveWalHashAfter) {
    throw "live WAL hash changed"
}

$CorruptDir = Get-NormalizedPath (
    Join-Path $EvidenceDir "corrupt-test"
)
$CorruptBackup = Get-NormalizedPath (
    Join-Path $CorruptDir (Split-Path $SecondaryBackup -Leaf)
)
$CorruptMetadata = Get-NormalizedPath "${CorruptBackup}.json"
$CorruptRestorePath = Get-NormalizedPath (
    Join-Path $CorruptDir "must-not-exist.db"
)
if (Test-Path -LiteralPath $CorruptDir) {
    throw "corrupt-test directory already exists"
}
New-Item -ItemType Directory -Path $CorruptDir | Out-Null
Copy-Item -LiteralPath $SecondaryBackup -Destination $CorruptBackup
Copy-Item -LiteralPath $SecondaryMetadata -Destination $CorruptMetadata

$CorruptScript = @'
from pathlib import Path
import sys

path = Path(sys.argv[1])
with path.open("ab") as handle:
    handle.write(b"\x00")
'@
$corruptRaw = $CorruptScript | & $Python - $CorruptBackup 2>&1
$corruptExit = $LASTEXITCODE
if ($corruptExit -ne 0) {
    throw ($corruptRaw -join "`n")
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $corruptVerifyRaw = & $Python database_backup.py `
        --config $DrillConfig verify $CorruptBackup 2>&1
    $CorruptVerifyExit = $LASTEXITCODE
    $corruptVerifyRaw |
        Set-Content -Encoding utf8 `
            -LiteralPath "$EvidenceDir\60-corrupt-verify-output.txt"
    if ($CorruptVerifyExit -eq 0) {
        throw "corrupted copy accepted by verify"
    }

    $corruptRestoreRaw = & $Python database_backup.py `
        --config $DrillConfig restore `
        $CorruptBackup $CorruptRestorePath `
        --strategy $Strategy --dry-run 2>&1
    $CorruptRestoreExit = $LASTEXITCODE
    $corruptRestoreRaw |
        Set-Content -Encoding utf8 `
            -LiteralPath "$EvidenceDir\61-corrupt-restore-output.txt"
    if ($CorruptRestoreExit -eq 0) {
        throw "corrupted copy accepted by restore dry-run"
    }
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if (Test-Path -LiteralPath $CorruptRestorePath) {
    throw "corrupt restore created destination"
}

if (
    (Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionConfig).Hash -ne
    $ProductionConfigHash
) {
    throw "production config changed during drill"
}

$finalConfigRaw = $ConfigInfoScript | & $Python - `
    $ProductionConfig $ProductionWorkingDirectory 2>&1
$finalConfigExit = $LASTEXITCODE
if ($finalConfigExit -ne 0) {
    throw ($finalConfigRaw -join "`n")
}
$FinalConfigInfo = ($finalConfigRaw -join "`n") | ConvertFrom-Json
if ((Get-NormalizedPath ([string]$FinalConfigInfo.database_path)) -ne $LiveDb) {
    throw "production database.path resolution changed"
}
if (
    (Get-NormalizedPath (
        [string]$FinalConfigInfo.production_working_directory
    )) -ne $ProductionWorkingDirectory
) {
    throw "production working directory changed"
}

$FinalResult = [pscustomobject]@{
    status = "pending_operator_review"
    head_sha = $HeadSha
    production_config_sha256 = $ProductionConfigHash
    production_working_directory = $ProductionWorkingDirectory
    configured_database_path = $ConfigInfo.database_path_setting
    live_db = $LiveDb
    drill_config_sha256 = $DrillConfigHash
    backup_path = $BackupPath
    backup_sha256 = $BackupHash
    metadata_path = $MetadataPath
    metadata_file_sha256 = $PrimaryMetadataHash
    secondary_backup = $SecondaryBackup
    secondary_metadata = $SecondaryMetadata
    restore_path = $RestorePath
    backup_elapsed_seconds = $BackupElapsedSeconds
    restore_elapsed_seconds = $RestoreElapsedSeconds
    integrity_exit_code = $RestoreResult.integrity_exit_code
    integrity_errors = $RestoreResult.integrity_errors
    integrity_warnings = $RestoreResult.integrity_warnings
    live_state_unchanged = $true
    restored_state_matches = $true
    corrupt_verify_exit_code = $CorruptVerifyExit
    corrupt_restore_dry_run_exit_code = $CorruptRestoreExit
    production_config_unchanged = $true
    automatic_cutover_performed = $false
    completed_at = (Get-Date).ToString("o")
}
$FinalResult | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding utf8 -LiteralPath "$EvidenceDir\70-final-result.json"
$FinalResult | ConvertTo-Json -Depth 6
