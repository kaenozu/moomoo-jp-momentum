# Production SQLite backup and recovery drill

This runbook is the execution and evidence checklist for Issue #27.

The operational result remains **pending** until the drill is executed on the production Windows PC and the evidence is reviewed. Repository tests and GitHub Actions do not substitute for production-PC evidence.

## Safety boundary

The drill is limited to the SQLite virtual-trading database.

Mandatory constraints:

- Do not enable, import, instantiate, or call any moomoo/Futu order API or trade context.
- Do not overwrite or replace the configured live database.
- Do not change the production `config.yaml`.
- Do not restore to an existing path.
- Do not perform a production cutover.
- Run from one supported Windows host only.
- Stop all scheduler, Streamlit, manual-cycle, and other SQLite writers for the comparison window.
- Store the drill config, primary backup, logs, restored DB, and corruption-test files outside the repository.
- Do not claim success without command output, hashes, paths, and timestamps from the production PC.

Use these statuses:

- `passed`: all acceptance checks have direct production-PC evidence.
- `pending`: the production drill or evidence review is incomplete.
- `blocked`: a prerequisite, path, storage target, maintenance window, or access condition is unavailable.
- `correction_required`: a command, implementation, or runbook defect must be fixed before retrying.
- `failed`: the drill ran and an acceptance check failed.

Any unresolved ambiguity or mismatch prevents `passed`.

## Why the drill uses an isolated config copy

`database_backup.py backup` performs retention pruning after a successful backup. To avoid deleting or rotating existing production backup generations during this drill, the procedure creates an evidence-directory copy of the production config and changes only:

- `database.path` to the resolved absolute live DB path;
- `database_backup.enabled` to `true`;
- `database_backup.directory` to a new empty drill-primary directory;
- backup retention values to positive values that cannot prune the single drill backup;
- `database_backup.verify_after_backup` to `true`.

The production config remains unchanged. The isolated config is evidence, not a cutover config.

## Current CLI guarantees

The implementation used by this procedure:

- runs `PRAGMA quick_check` against the source before backup;
- uses SQLite Online Backup API rather than copying the live DB file;
- verifies the created snapshot;
- publishes backup and metadata through temporary files and atomic rename;
- records SHA-256, schema version, latest virtual-fill date, and latest equity date;
- requires metadata and matching SHA-256 for restore;
- refuses the configured live DB path and every existing restore destination;
- runs `quick_check` and the read-only virtual-trade integrity checker before publishing the restored DB;
- never performs an automatic cutover.

## Stop conditions

Stop immediately and record the result when:

- the approved Git SHA, production config, or live DB path is ambiguous;
- the working tree is not clean;
- any evidence, primary, secondary, corrupt-test, or restore path is inside the repository;
- two file targets resolve to the same path;
- the evidence directory, drill-primary directory, secondary target, or restore target already exists unexpectedly;
- only one host and a no-write window cannot be confirmed;
- free space is insufficient;
- source snapshot, backup verification, restore dry-run, restore, or integrity validation fails;
- metadata is missing or a checksum differs;
- the live logical state, DB hash, or WAL presence/hash changes during the no-write window;
- the restored logical state differs from the live snapshot;
- a corrupted copy is accepted;
- a command would overwrite an existing file or invoke an order/trade API.

Do not continue merely to collect more output after a safety invariant fails.

## 1. Preflight

Run from the repository root in the intended Python environment. Use PowerShell 7 or Windows PowerShell 5.1. Replace all placeholders first.

```powershell
$ErrorActionPreference = "Stop"

$Python = "python"
$ExpectedHead = "<approved-master-or-release-sha>"
$ProductionConfig = (Resolve-Path ".\config.yaml").Path
$Strategy = "momentum"
$EvidenceDir = "D:\moomoo-drill-evidence\2026-07-13T190000"
$SecondaryDir = "E:\moomoo-backups\recovery-drill-20260713"
$RestorePath = "D:\moomoo-recovery\moomoo-restored-20260713.db"

if ($ExpectedHead -like "<*") {
    throw "replace ExpectedHead with the explicitly approved Git SHA"
}

function Get-NormalizedPath {
    param([Parameter(Mandatory=$true)][string]$Path)
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Assert-DifferentPath {
    param(
        [Parameter(Mandatory=$true)][string]$Left,
        [Parameter(Mandatory=$true)][string]$Right,
        [Parameter(Mandatory=$true)][string]$Message
    )
    if ((Get-NormalizedPath $Left) -eq (Get-NormalizedPath $Right)) {
        throw $Message
    }
}

function Assert-OutsideRepository {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$RepositoryRoot
    )
    $candidate = Get-NormalizedPath $Path
    $root = Get-NormalizedPath $RepositoryRoot
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if ($candidate -eq $root -or $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "path must be outside the repository: $candidate"
    }
}

$RepoRoot = (Get-Location).Path
$HeadRaw = & git rev-parse HEAD 2>&1
$HeadExit = $LASTEXITCODE
if ($HeadExit -ne 0) { throw ($HeadRaw -join "`n") }
$HeadSha = ($HeadRaw -join "").Trim()
if ($HeadSha -ne $ExpectedHead) {
    throw "HEAD differs from approved SHA: expected=$ExpectedHead actual=$HeadSha"
}

$GitStatusRaw = & git status --porcelain 2>&1
$GitStatusExit = $LASTEXITCODE
if ($GitStatusExit -ne 0) { throw ($GitStatusRaw -join "`n") }
if ($GitStatusRaw) { throw "working tree is not clean" }

if (Test-Path -LiteralPath $EvidenceDir) {
    throw "use a new evidence directory: $EvidenceDir"
}
if (Test-Path -LiteralPath $SecondaryDir) {
    throw "use a new secondary directory: $SecondaryDir"
}
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore destination already exists: $RestorePath"
}

$EvidenceDir = Get-NormalizedPath $EvidenceDir
$SecondaryDir = Get-NormalizedPath $SecondaryDir
$RestorePath = Get-NormalizedPath $RestorePath
Assert-OutsideRepository $EvidenceDir $RepoRoot
Assert-OutsideRepository $SecondaryDir $RepoRoot
Assert-OutsideRepository $RestorePath $RepoRoot

New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Path (Split-Path $RestorePath -Parent) -Force | Out-Null

$ConfigInfoScript = @'
from pathlib import Path
from src.config import Config
import json
import sys

config_path = Path(sys.argv[1]).resolve()
config = Config(str(config_path))
print(json.dumps({
    "config_path": str(config_path),
    "database_path": str(Path(config.database_path).resolve()),
    "database_backup_enabled": config.get("database_backup.enabled", False),
    "database_backup_directory": str(Path(config.get("database_backup.directory", "backups")).resolve()),
    "cycle_control_enabled": config.get("cycle_control.enabled", False),
}, ensure_ascii=False))
'@

$ConfigInfoRaw = & $Python -c $ConfigInfoScript $ProductionConfig 2>&1
$ConfigInfoExit = $LASTEXITCODE
if ($ConfigInfoExit -ne 0) { throw ($ConfigInfoRaw -join "`n") }
$ConfigInfo = ($ConfigInfoRaw -join "`n") | ConvertFrom-Json

$LiveDb = Get-NormalizedPath ([string]$ConfigInfo.database_path)
$ConfiguredBackupDir = Get-NormalizedPath ([string]$ConfigInfo.database_backup_directory)
$PrimaryDrillDir = Get-NormalizedPath (Join-Path $EvidenceDir "primary-backup")
$DrillConfig = Get-NormalizedPath (Join-Path $EvidenceDir "drill-config.yaml")

if (-not (Test-Path -LiteralPath $LiveDb -PathType Leaf)) {
    throw "live DB does not exist: $LiveDb"
}
Assert-DifferentPath $LiveDb $RestorePath "restore destination equals live DB"
Assert-OutsideRepository $PrimaryDrillDir $RepoRoot
Assert-OutsideRepository $DrillConfig $RepoRoot

$ProductionConfigHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionConfig).Hash

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
out_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
'@

$BuildConfigRaw = & $Python -c $BuildDrillConfigScript `
    $ProductionConfig $LiveDb $PrimaryDrillDir $DrillConfig 2>&1
$BuildConfigExit = $LASTEXITCODE
if ($BuildConfigExit -ne 0) { throw ($BuildConfigRaw -join "`n") }
if (-not (Test-Path -LiteralPath $DrillConfig -PathType Leaf)) {
    throw "isolated drill config was not created"
}

$DrillConfigHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $DrillConfig).Hash

$JournalModeScript = @'
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1]).resolve()
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    print(connection.execute("PRAGMA journal_mode").fetchone()[0])
'@

$JournalModeRaw = & $Python -c $JournalModeScript $LiveDb 2>&1
$JournalModeExit = $LASTEXITCODE
if ($JournalModeExit -ne 0) { throw ($JournalModeRaw -join "`n") }
$JournalMode = ($JournalModeRaw -join "").Trim()

Get-Volume | Select-Object DriveLetter, FileSystemLabel, Size, SizeRemaining |
    ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\01-free-space.json"

[pscustomobject]@{
    status = "pending_manual_preflight_confirmation"
    head_sha = $HeadSha
    production_config_path = $ProductionConfig
    production_config_sha256 = $ProductionConfigHash
    drill_config_path = $DrillConfig
    drill_config_sha256 = $DrillConfigHash
    live_db = $LiveDb
    configured_backup_directory = $ConfiguredBackupDir
    production_database_backup_enabled = $ConfigInfo.database_backup_enabled
    production_cycle_control_enabled = $ConfigInfo.cycle_control_enabled
    primary_drill_directory = $PrimaryDrillDir
    secondary_directory = $SecondaryDir
    restore_path = $RestorePath
    journal_mode = $JournalMode
    captured_at = (Get-Date).ToString("o")
} | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$EvidenceDir\00-preflight.json"
```

Before continuing in the same PowerShell session, manually confirm and record:

- the live DB is the intended production virtual-trading DB;
- the expected Git SHA was explicitly approved;
- the production config hash and paths are correct;
- the evidence, primary, secondary, and restore locations are outside the repository;
- the restore path is unused and different from the live DB;
- the secondary storage is the intended storage device/location;
- free space is sufficient for at least four DB-sized files plus metadata and logs;
- only one host accesses the operational DB;
- all SQLite writers are stopped for the comparison window;
- no order/trade API or trade context is enabled or invoked.

If any item is uncertain, stop with `blocked` or `correction_required`.

## 2. Canonical read-only snapshot function

Run in the same PowerShell session.

```powershell
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
    quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
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
        "schema_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "quick_check": "ok",
        "latest_virtual_fill_date": latest_fill_date,
        "virtual_fill_count": fill_count,
        "latest_equity": latest_equity,
        "virtual_equity_row_count": equity_count,
        "virtual_order_counts": order_counts,
        "open_positions": positions,
    }

print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
'@

function Write-DbSnapshot {
    param(
        [Parameter(Mandatory=$true)][string]$DbPath,
        [Parameter(Mandatory=$true)][string]$OutputPath
    )
    $SnapshotRaw = $SnapshotScript | & $Python - $DbPath $Strategy 2>&1
    $SnapshotExit = $LASTEXITCODE
    if ($SnapshotExit -ne 0) { throw ($SnapshotRaw -join "`n") }
    ($SnapshotRaw -join "`n").Trim() | Set-Content -Encoding utf8 $OutputPath
}
```

## 3. Backup, secondary copy, restore, comparison, and corruption rejection

Run only after the manual preflight is complete.

```powershell
Write-DbSnapshot $LiveDb "$EvidenceDir\10-live-before.json"
$LiveDbHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb).Hash
$LiveWal = "${LiveDb}-wal"
$LiveWalPresentBefore = Test-Path -LiteralPath $LiveWal -PathType Leaf
$LiveWalHashBefore = if ($LiveWalPresentBefore) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else { $null }

$BackupStarted = Get-Date
$BackupRaw = & $Python database_backup.py --config $DrillConfig backup --kind daily 2>&1
$BackupExit = $LASTEXITCODE
$BackupRaw | Set-Content -Encoding utf8 "$EvidenceDir\20-backup-output.txt"
if ($BackupExit -ne 0) { throw "backup failed: $($BackupRaw -join '`n')" }
$BackupResult = ($BackupRaw -join "`n") | ConvertFrom-Json
if ($BackupResult.pruned_files.Count -ne 0) {
    throw "isolated drill backup unexpectedly pruned files"
}

$BackupPath = Get-NormalizedPath ([string]$BackupResult.backup_path)
$MetadataPath = Get-NormalizedPath ([string]$BackupResult.metadata_path)
if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) { throw "backup missing" }
if (-not (Test-Path -LiteralPath $MetadataPath -PathType Leaf)) { throw "metadata missing" }
Assert-DifferentPath $BackupPath $LiveDb "backup path equals live DB"
Assert-DifferentPath $MetadataPath $LiveDb "metadata path equals live DB"

$VerifyRaw = & $Python database_backup.py --config $DrillConfig verify $BackupPath 2>&1
$VerifyExit = $LASTEXITCODE
$VerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\21-primary-verify-output.txt"
if ($VerifyExit -ne 0) { throw "primary verify failed" }

$Metadata = Get-Content -Raw -LiteralPath $MetadataPath | ConvertFrom-Json
$BackupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $BackupPath).Hash.ToLowerInvariant()
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
$SecondaryBackup = Get-NormalizedPath (Join-Path $SecondaryDir (Split-Path $BackupPath -Leaf))
$SecondaryMetadata = Get-NormalizedPath "${SecondaryBackup}.json"
if (Test-Path -LiteralPath $SecondaryBackup) { throw "secondary backup exists" }
if (Test-Path -LiteralPath $SecondaryMetadata) { throw "secondary metadata exists" }
Copy-Item -LiteralPath $BackupPath -Destination $SecondaryBackup
Copy-Item -LiteralPath $MetadataPath -Destination $SecondaryMetadata

$SecondaryBackupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryBackup).Hash.ToLowerInvariant()
$PrimaryMetadataHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $MetadataPath).Hash
$SecondaryMetadataHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryMetadata).Hash
if ($SecondaryBackupHash -ne $BackupHash) { throw "secondary backup hash mismatch" }
if ($SecondaryMetadataHash -ne $PrimaryMetadataHash) { throw "secondary metadata hash mismatch" }

$SecondaryVerifyRaw = & $Python database_backup.py --config $DrillConfig verify $SecondaryBackup 2>&1
$SecondaryVerifyExit = $LASTEXITCODE
$SecondaryVerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\30-secondary-verify-output.txt"
if ($SecondaryVerifyExit -ne 0) { throw "secondary verify failed" }

$DryRunRaw = & $Python database_backup.py --config $DrillConfig restore `
    $SecondaryBackup $RestorePath --strategy $Strategy --dry-run 2>&1
$DryRunExit = $LASTEXITCODE
$DryRunRaw | Set-Content -Encoding utf8 "$EvidenceDir\40-restore-dry-run-output.txt"
if ($DryRunExit -ne 0) { throw "restore dry-run failed" }
if (Test-Path -LiteralPath $RestorePath) { throw "dry-run created destination" }
$DryRunResult = ($DryRunRaw -join "`n") | ConvertFrom-Json
if ([int]$DryRunResult.integrity_errors -ne 0) { throw "dry-run integrity errors" }

$RestoreStarted = Get-Date
$RestoreRaw = & $Python database_backup.py --config $DrillConfig restore `
    $SecondaryBackup $RestorePath --strategy $Strategy 2>&1
$RestoreExit = $LASTEXITCODE
$RestoreRaw | Set-Content -Encoding utf8 "$EvidenceDir\41-restore-output.txt"
if ($RestoreExit -ne 0) { throw "restore failed" }
if (-not (Test-Path -LiteralPath $RestorePath -PathType Leaf)) { throw "restored DB missing" }
$RestoreResult = ($RestoreRaw -join "`n") | ConvertFrom-Json
if ([int]$RestoreResult.integrity_errors -ne 0) { throw "restored DB integrity errors" }

Write-DbSnapshot $LiveDb "$EvidenceDir\50-live-after.json"
Write-DbSnapshot $RestorePath "$EvidenceDir\51-restored.json"
$LiveBeforeText = (Get-Content -Raw "$EvidenceDir\10-live-before.json").Trim()
$LiveAfterText = (Get-Content -Raw "$EvidenceDir\50-live-after.json").Trim()
$RestoredText = (Get-Content -Raw "$EvidenceDir\51-restored.json").Trim()
if ($LiveBeforeText -ne $LiveAfterText) { throw "live logical state changed" }
if ($LiveAfterText -ne $RestoredText) { throw "restored logical state mismatch" }

$LiveDbHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb).Hash
$LiveWalPresentAfter = Test-Path -LiteralPath $LiveWal -PathType Leaf
$LiveWalHashAfter = if ($LiveWalPresentAfter) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else { $null }
if ($LiveDbHashBefore -ne $LiveDbHashAfter) { throw "live DB hash changed" }
if ($LiveWalPresentBefore -ne $LiveWalPresentAfter) { throw "live WAL presence changed" }
if ($LiveWalHashBefore -ne $LiveWalHashAfter) { throw "live WAL hash changed" }

$CorruptDir = Get-NormalizedPath (Join-Path $EvidenceDir "corrupt-test")
$CorruptBackup = Get-NormalizedPath (Join-Path $CorruptDir (Split-Path $SecondaryBackup -Leaf))
$CorruptMetadata = Get-NormalizedPath "${CorruptBackup}.json"
$CorruptRestorePath = Get-NormalizedPath (Join-Path $CorruptDir "must-not-exist.db")
if (Test-Path -LiteralPath $CorruptDir) { throw "corrupt-test directory already exists" }
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
$CorruptRaw = & $Python -c $CorruptScript $CorruptBackup 2>&1
$CorruptExit = $LASTEXITCODE
if ($CorruptExit -ne 0) { throw ($CorruptRaw -join "`n") }

$CorruptVerifyRaw = & $Python database_backup.py --config $DrillConfig verify $CorruptBackup 2>&1
$CorruptVerifyExit = $LASTEXITCODE
$CorruptVerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\60-corrupt-verify-output.txt"
if ($CorruptVerifyExit -eq 0) { throw "corrupted copy accepted by verify" }

$CorruptRestoreRaw = & $Python database_backup.py --config $DrillConfig restore `
    $CorruptBackup $CorruptRestorePath --strategy $Strategy --dry-run 2>&1
$CorruptRestoreExit = $LASTEXITCODE
$CorruptRestoreRaw | Set-Content -Encoding utf8 "$EvidenceDir\61-corrupt-restore-output.txt"
if ($CorruptRestoreExit -eq 0) { throw "corrupted copy accepted by restore dry-run" }
if (Test-Path -LiteralPath $CorruptRestorePath) { throw "corrupt restore created destination" }

if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionConfig).Hash -ne $ProductionConfigHash) {
    throw "production config changed during drill"
}
$FinalConfigRaw = & $Python -c $ConfigInfoScript $ProductionConfig 2>&1
$FinalConfigExit = $LASTEXITCODE
if ($FinalConfigExit -ne 0) { throw ($FinalConfigRaw -join "`n") }
$FinalConfigInfo = ($FinalConfigRaw -join "`n") | ConvertFrom-Json
if ((Get-NormalizedPath ([string]$FinalConfigInfo.database_path)) -ne $LiveDb) {
    throw "production database.path changed"
}

[pscustomobject]@{
    status = "pending_operator_review"
    backup_path = $BackupPath
    backup_sha256 = $BackupHash
    metadata_path = $MetadataPath
    metadata_file_sha256 = $PrimaryMetadataHash
    secondary_backup = $SecondaryBackup
    secondary_metadata = $SecondaryMetadata
    restore_path = $RestorePath
    backup_elapsed_seconds = ((Get-Date) - $BackupStarted).TotalSeconds
    restore_elapsed_seconds = ((Get-Date) - $RestoreStarted).TotalSeconds
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
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 "$EvidenceDir\70-final-result.json"
```

A restored DB may be logically identical while its raw file hash differs from the backup because SQLite Online Backup API may produce a different file layout. The required equality is the canonical logical snapshot, not raw backup/restored-file hash equality.

## Manual cutover and rollback design — do not execute

Cutover requires a separate explicit approval and maintenance procedure. Prefer changing `database.path` to a verified restored path rather than copying a file over the live DB.

Planned cutover:

1. obtain separate approval;
2. stop every SQLite reader/writer;
3. create and verify a fresh pre-cutover backup;
4. repeat integrity and logical comparison;
5. save a timestamped production-config copy;
6. manually change only `database.path`;
7. keep the original DB unchanged;
8. start one process at a time and validate;
9. start one scheduler process last.

Planned rollback:

1. stop every SQLite reader/writer;
2. restore the timestamped production config;
3. verify the original DB;
4. restart one scheduler process;
5. retain the failed recovery DB and evidence.

Do not execute either sequence as part of Issue #27.

## Issue #27 evidence template

```markdown
## Production SQLite backup/recovery drill result

Status: pending | passed | blocked | correction_required | failed

### Identity and preflight

- Operator:
- Host identifier (redacted if needed):
- Start/end time (Asia/Tokyo):
- Approved Git SHA:
- Actual Git SHA:
- Working tree clean: yes/no
- Python version:
- Single-host condition confirmed: yes/no
- No-write window established: yes/no
- Production config SHA-256 before/after:
- Isolated drill config SHA-256:
- Production `database_backup.enabled`:
- Production `cycle_control.enabled`:
- Journal mode:
- Free-space evidence:

### Paths

- Live DB path (redacted):
- Configured production backup directory (redacted):
- Isolated primary drill directory (redacted):
- Secondary storage directory (redacted):
- Restore path (redacted):
- All evidence/output paths outside repository: yes/no
- Restore path unused and different from live DB: yes/no

### Backup and secondary copy

- Backup command exit code:
- Backup filename:
- Metadata filename:
- Backup elapsed seconds:
- Metadata `created_at` / backup kind / schema version:
- Metadata source DB matches approved live DB: yes/no
- Metadata SHA-256 equals computed backup SHA-256: yes/no
- Isolated backup pruned files: none/other
- Latest virtual-fill date:
- Latest equity date:
- Secondary backup hash equals primary: yes/no
- Secondary metadata hash equals primary: yes/no
- Secondary verify exit code:

### Restore and integrity

- Dry-run exit code:
- Dry-run destination remained absent: yes/no
- Restore exit code:
- Restore elapsed seconds:
- Restored `quick_check`: ok/fail
- Integrity exit code:
- Integrity errors:
- Integrity warnings:

### Logical and live-state comparison

- Live before/after canonical state identical: yes/no
- Restored canonical state equals live: yes/no
- Live DB SHA-256 before/after identical: yes/no
- Live WAL presence/hash before/after identical or absent: yes/no
- Latest virtual-fill date:
- Latest equity date and recorded cash:
- Open positions match: yes/no
- Order counts by status match: yes/no

### Corruption rejection and safety

- Corruption method: appended one byte to a third copy
- `verify` exit code (nonzero):
- `restore --dry-run` exit code (nonzero):
- Corrupt destination created: no
- Production config changed: no
- Production `database.path` changed: no
- Live DB overwritten: no
- Automatic cutover performed: no
- Real-order API/trade context invoked: no
- Repository artifacts created: no

### Final decision

- Acceptance criteria satisfied: yes/no
- Final status:
- Reviewer:
- Review date:
- Unexpected warnings/differences:
- Follow-up action:
```

Mark `passed` only after every field is supported by retained production-PC evidence. Until then, Issue #27 remains `pending` and open.
