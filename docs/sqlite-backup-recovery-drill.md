# Production SQLite backup and recovery drill

This runbook is the execution and evidence checklist for Issue #27.

The drill remains **pending** until it is executed on the production Windows PC against the configured virtual-trading SQLite database and the evidence template is completed. Repository tests and GitHub Actions do not substitute for this production-PC drill.

## Scope and safety boundary

The drill proves that a verified backup can be copied to secondary storage and restored to a separate unused path without replacing the live database.

The following constraints are mandatory:

- SQLite virtual trading only.
- Do not enable, import, instantiate, or call any moomoo/Futu order API or trade context.
- Do not restore to the configured live database path.
- Do not overwrite an existing destination file.
- Do not change `database.path` during the drill.
- Do not perform an automatic cutover.
- Run from one supported Windows host only. The cycle lock is host-local and does not protect a shared database from a second host.
- Do not claim success without production-PC command output and file evidence.
- Store logs and temporary corruption-test files outside the repository.

## Status rules

Use one of these statuses in the Issue evidence:

- `passed`: every acceptance check passed on the production PC.
- `pending`: the production-PC drill has not been executed or evidence is incomplete.
- `blocked`: a prerequisite is unavailable, such as missing storage, ambiguous paths, or inability to establish a no-write window.
- `correction_required`: a command, path, implementation, or document defect must be fixed before retrying.
- `failed`: the drill was executed and an acceptance check failed.

Any unresolved path ambiguity, unexpected source change, verification error, or accepted corrupted copy prevents `passed`.

## What the current CLI guarantees

The explicit CLI is:

```text
python database_backup.py --config <config> backup --kind daily
python database_backup.py --config <config> verify <backup.sqlite3>
python database_backup.py --config <config> restore <backup.sqlite3> <new-path> --strategy momentum --dry-run
python database_backup.py --config <config> restore <backup.sqlite3> <new-path> --strategy momentum
```

The implementation:

- runs `PRAGMA quick_check` against the source database before backup;
- uses SQLite Online Backup API rather than a file copy;
- optionally runs `quick_check` against the created snapshot;
- publishes backup and metadata through temporary files and atomic rename;
- records SHA-256, schema version, latest virtual-fill date, and latest equity date in JSON metadata;
- requires metadata and a matching SHA-256 for restore;
- refuses a restore destination equal to the configured live database path;
- refuses any existing restore destination;
- runs `quick_check` and the virtual-trade integrity checker before publishing the restored database;
- never performs an automatic production cutover.

`database_backup.enabled` controls automatic daily-cycle backup behavior. The explicit `database_backup.py backup` command remains an operator-invoked action, so record the production setting but do not infer that the manual CLI is disabled when the setting is `false`.

## Required prerequisites

Record and verify all items before running the first write command.

1. Production checkout is on the intended commit.
2. There are no uncommitted repository changes that could affect the command being executed.
3. The active Python environment can import the repository dependencies.
4. The exact production `config.yaml` path is known.
5. The configured `database.path` resolves to the intended live SQLite file.
6. The configured backup directory is known.
7. A secondary-storage directory is mounted and writable.
8. A restore destination is selected on a different, unused path.
9. The restore destination is not the live DB, backup file, metadata file, or secondary copy.
10. Sufficient free space exists for the backup, secondary copy, restored DB, corruption-test copy, metadata, and logs.
11. Only one host accesses the operational SQLite DB during the drill.
12. A no-write maintenance window is established for the source-state before/after comparison. WAL mode may remain enabled; the requirement is to avoid application writes during the comparison window.
13. Scheduler, Streamlit, manual cycle execution, and other repository processes that write SQLite are stopped or otherwise prevented from writing for the comparison window.
14. The operator has a separate evidence directory outside the repository.

If a normal production write occurs during the comparison window, do not explain away a mismatch. Mark the attempt `blocked` or `failed`, re-establish a no-write window, and repeat.

## Stop conditions

Stop immediately and record the result when any of the following occurs:

- the live DB path cannot be resolved unambiguously;
- two path variables resolve to the same path;
- the restore destination already exists;
- the secondary copy target already exists unexpectedly;
- the source `quick_check`, backup verification, restore dry-run, restore, or integrity check returns nonzero;
- backup metadata is missing or its SHA-256 does not match;
- the source logical snapshot changes during the no-write window;
- the restored logical snapshot differs from the source snapshot;
- the source DB or source WAL hash changes during the no-write window without an identified production write;
- a deliberately corrupted copy is accepted by `verify` or `restore --dry-run`;
- any command attempts to instantiate or call an order/trade API;
- any step would overwrite the live DB or an existing file.

Do not continue merely to collect more output after a safety invariant fails.

## PowerShell preparation

Run from the repository root in the intended virtual environment. Replace every placeholder before execution.

```powershell
$ErrorActionPreference = "Stop"

$Python = "python"
$Config = (Resolve-Path ".\config.yaml").Path
$Strategy = "momentum"
$EvidenceDir = "D:\moomoo-drill-evidence\2026-07-13"
$SecondaryDir = "E:\moomoo-backups"
$RestorePath = "D:\moomoo-recovery\moomoo-restored-20260713.db"

New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $RestorePath -Parent) | Out-Null

$HeadSha = git rev-parse HEAD
$GitStatus = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw "git state could not be read" }
if ($GitStatus) { throw "working tree is not clean" }

$ConfigInfoRaw = & $Python -c @'
from pathlib import Path
from src.config import Config
import json, sys
p = Path(sys.argv[1]).resolve()
c = Config(str(p))
print(json.dumps({
    "config_path": str(p),
    "database_path": str(Path(c.database_path).resolve()),
    "database_backup_enabled": c.get("database_backup.enabled", False),
    "database_backup_directory": str(Path(c.get("database_backup.directory", "backups")).resolve()),
    "cycle_control_enabled": c.get("cycle_control.enabled", False),
}, ensure_ascii=False))
'@ $Config
if ($LASTEXITCODE -ne 0) { throw ($ConfigInfoRaw -join "`n") }
$ConfigInfo = ($ConfigInfoRaw -join "`n") | ConvertFrom-Json

$LiveDb = [IO.Path]::GetFullPath([string]$ConfigInfo.database_path)
$ConfiguredBackupDir = [IO.Path]::GetFullPath([string]$ConfigInfo.database_backup_directory)
$SecondaryDir = [IO.Path]::GetFullPath($SecondaryDir)
$RestorePath = [IO.Path]::GetFullPath($RestorePath)

if (-not (Test-Path -LiteralPath $LiveDb -PathType Leaf)) {
    throw "live DB does not exist: $LiveDb"
}
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore destination already exists: $RestorePath"
}
if ($RestorePath -eq $LiveDb) {
    throw "restore destination equals live DB"
}

$ConfigHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Config).Hash
$JournalMode = & $Python -c @'
from pathlib import Path
import sqlite3, sys
p = Path(sys.argv[1]).resolve()
with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True) as c:
    print(c.execute("PRAGMA journal_mode").fetchone()[0])
'@ $LiveDb
if ($LASTEXITCODE -ne 0) { throw "journal mode query failed" }

[pscustomobject]@{
    head_sha = $HeadSha
    config_path = $Config
    config_sha256 = $ConfigHash
    live_db = $LiveDb
    configured_backup_dir = $ConfiguredBackupDir
    database_backup_enabled = $ConfigInfo.database_backup_enabled
    cycle_control_enabled = $ConfigInfo.cycle_control_enabled
    secondary_dir = $SecondaryDir
    restore_path = $RestorePath
    journal_mode = ($JournalMode -join "").Trim()
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 "$EvidenceDir\00-preflight.json"
```

Confirm manually that:

- `$LiveDb`, `$SecondaryDir`, and `$RestorePath` are the intended paths;
- the live DB is not a symlink or junction into the restore directory;
- the restore path is unused;
- no second host is using the DB;
- no repository SQLite writer will run during the comparison window;
- free-space evidence has been captured.

Example free-space evidence:

```powershell
Get-Volume | Select-Object DriveLetter, FileSystemLabel, Size, SizeRemaining |
    ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\01-free-space.json"

Get-Item -LiteralPath $LiveDb |
    Select-Object FullName, Length, LastWriteTimeUtc |
    ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\02-live-db-file.json"
```

## Canonical read-only logical snapshot

Use the same snapshot function for the live DB and restored DB. It opens SQLite with `mode=ro` and does not instantiate `VirtualTradeManager`, avoiding migrations and writes.

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

with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
    conn.row_factory = sqlite3.Row
    quick = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    if quick != ["ok"]:
        raise SystemExit(f"quick_check failed: {quick}")

    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    def scalar(sql: str, params: tuple[object, ...] = ()):
        row = conn.execute(sql, params).fetchone()
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
        row = conn.execute(
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
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM virtual_orders "
                "WHERE strategy_name = ? GROUP BY status ORDER BY status",
                (strategy,),
            ).fetchall()
        ]

    positions = []
    if "virtual_positions" in tables:
        positions = [
            dict(row)
            for row in conn.execute(
                "SELECT code, quantity, avg_cost, market_price, market_value, "
                "unrealized_pl, realized_pl FROM virtual_positions "
                "WHERE strategy_name = ? AND quantity > 0 ORDER BY code",
                (strategy,),
            ).fetchall()
        ]

    payload = {
        "strategy": strategy,
        "schema_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
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
    $Raw = $SnapshotScript | & $Python - $DbPath $Strategy 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($Raw -join "`n") }
    ($Raw -join "`n").Trim() | Set-Content -Encoding utf8 $OutputPath
}
```

Capture the live state and live file hashes before backup:

```powershell
Write-DbSnapshot -DbPath $LiveDb -OutputPath "$EvidenceDir\10-live-before.json"

$LiveDbHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb).Hash
$LiveWal = "$LiveDb-wal"
$LiveWalHashBefore = if (Test-Path -LiteralPath $LiveWal -PathType Leaf) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else { $null }

[pscustomobject]@{
    live_db_sha256 = $LiveDbHashBefore
    live_wal_present = [bool](Test-Path -LiteralPath $LiveWal -PathType Leaf)
    live_wal_sha256 = $LiveWalHashBefore
    captured_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\11-live-hashes-before.json"
```

## Create the verified production backup

```powershell
$BackupStarted = Get-Date
$BackupRaw = & $Python database_backup.py --config $Config backup --kind daily 2>&1
$BackupExit = $LASTEXITCODE
$BackupRaw | Set-Content -Encoding utf8 "$EvidenceDir\20-backup-output.txt"
if ($BackupExit -ne 0) { throw "backup failed with exit code $BackupExit" }

$BackupResult = ($BackupRaw -join "`n") | ConvertFrom-Json
$BackupPath = [IO.Path]::GetFullPath([string]$BackupResult.backup_path)
$MetadataPath = [IO.Path]::GetFullPath([string]$BackupResult.metadata_path)

if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw "backup file was not created"
}
if (-not (Test-Path -LiteralPath $MetadataPath -PathType Leaf)) {
    throw "backup metadata was not created"
}
if ($BackupPath -eq $LiveDb) { throw "backup path equals live DB" }
if ($MetadataPath -eq $LiveDb) { throw "metadata path equals live DB" }

$VerifyRaw = & $Python database_backup.py --config $Config verify $BackupPath 2>&1
$VerifyExit = $LASTEXITCODE
$VerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\21-primary-verify-output.txt"
if ($VerifyExit -ne 0) { throw "primary backup verify failed" }

$Metadata = Get-Content -Raw -LiteralPath $MetadataPath | ConvertFrom-Json
$BackupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $BackupPath).Hash.ToLowerInvariant()
if ($BackupHash -ne ([string]$Metadata.sha256).ToLowerInvariant()) {
    throw "backup hash does not match metadata"
}

[pscustomobject]@{
    backup_path = $BackupPath
    metadata_path = $MetadataPath
    backup_sha256 = $BackupHash
    metadata_file_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $MetadataPath).Hash
    metadata = $Metadata
    elapsed_seconds = ((Get-Date) - $BackupStarted).TotalSeconds
} | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 "$EvidenceDir\22-backup-record.json"
```

The backup command may prune older generations according to configuration. Review `pruned_files` in the JSON output and confirm every deletion was expected before proceeding.

## Copy backup and metadata to secondary storage

Copy both files without changing the backup filename. The sidecar metadata must remain adjacent as `<backup>.json`.

```powershell
New-Item -ItemType Directory -Force -Path $SecondaryDir | Out-Null

$SecondaryBackup = Join-Path $SecondaryDir (Split-Path $BackupPath -Leaf)
$SecondaryMetadata = "$SecondaryBackup.json"

if (Test-Path -LiteralPath $SecondaryBackup) {
    throw "secondary backup target already exists: $SecondaryBackup"
}
if (Test-Path -LiteralPath $SecondaryMetadata) {
    throw "secondary metadata target already exists: $SecondaryMetadata"
}

Copy-Item -LiteralPath $BackupPath -Destination $SecondaryBackup
Copy-Item -LiteralPath $MetadataPath -Destination $SecondaryMetadata

$SecondaryBackupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryBackup).Hash.ToLowerInvariant()
$SecondaryMetadataHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SecondaryMetadata).Hash

if ($SecondaryBackupHash -ne $BackupHash) {
    throw "secondary backup SHA-256 differs from primary backup"
}
if ($SecondaryMetadataHash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $MetadataPath).Hash) {
    throw "secondary metadata SHA-256 differs from primary metadata"
}

$SecondaryVerifyRaw = & $Python database_backup.py --config $Config verify $SecondaryBackup 2>&1
$SecondaryVerifyExit = $LASTEXITCODE
$SecondaryVerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\30-secondary-verify-output.txt"
if ($SecondaryVerifyExit -ne 0) { throw "secondary backup verify failed" }

[pscustomobject]@{
    secondary_backup = $SecondaryBackup
    secondary_metadata = $SecondaryMetadata
    backup_sha256 = $SecondaryBackupHash
    metadata_file_sha256 = $SecondaryMetadataHash
    copied_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\31-secondary-copy.json"
```

## Restore dry-run and restore to the unused path

The dry-run validates metadata, SHA-256, `quick_check`, and virtual-trade integrity against the backup without creating the destination.

```powershell
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore destination exists before dry-run"
}

$DryRunRaw = & $Python database_backup.py --config $Config restore `
    $SecondaryBackup $RestorePath --strategy $Strategy --dry-run 2>&1
$DryRunExit = $LASTEXITCODE
$DryRunRaw | Set-Content -Encoding utf8 "$EvidenceDir\40-restore-dry-run-output.txt"
if ($DryRunExit -ne 0) { throw "restore dry-run failed" }
if (Test-Path -LiteralPath $RestorePath) {
    throw "restore dry-run unexpectedly created the destination"
}

$RestoreStarted = Get-Date
$RestoreRaw = & $Python database_backup.py --config $Config restore `
    $SecondaryBackup $RestorePath --strategy $Strategy 2>&1
$RestoreExit = $LASTEXITCODE
$RestoreRaw | Set-Content -Encoding utf8 "$EvidenceDir\41-restore-output.txt"
if ($RestoreExit -ne 0) { throw "restore failed" }
if (-not (Test-Path -LiteralPath $RestorePath -PathType Leaf)) {
    throw "restore command returned success but destination is missing"
}

[pscustomobject]@{
    restore_path = $RestorePath
    restore_file_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $RestorePath).Hash
    elapsed_seconds = ((Get-Date) - $RestoreStarted).TotalSeconds
    output = (($RestoreRaw -join "`n") | ConvertFrom-Json)
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 "$EvidenceDir\42-restore-record.json"
```

Do not expect the restored SQLite file's raw byte hash to equal the backup hash. SQLite Online Backup API may produce a logically identical database with different file layout. The required comparisons are metadata-backed backup SHA-256, `quick_check`, integrity results, and the canonical logical snapshot.

## Compare source and restored logical state

```powershell
Write-DbSnapshot -DbPath $LiveDb -OutputPath "$EvidenceDir\50-live-after.json"
Write-DbSnapshot -DbPath $RestorePath -OutputPath "$EvidenceDir\51-restored.json"

$LiveBeforeText = (Get-Content -Raw "$EvidenceDir\10-live-before.json").Trim()
$LiveAfterText = (Get-Content -Raw "$EvidenceDir\50-live-after.json").Trim()
$RestoredText = (Get-Content -Raw "$EvidenceDir\51-restored.json").Trim()

if ($LiveBeforeText -ne $LiveAfterText) {
    throw "live logical state changed during the no-write window"
}
if ($LiveAfterText -ne $RestoredText) {
    throw "restored logical state differs from live state"
}

$LiveDbHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveDb).Hash
$LiveWalPresentAfter = Test-Path -LiteralPath $LiveWal -PathType Leaf
$LiveWalHashAfter = if ($LiveWalPresentAfter) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LiveWal).Hash
} else { $null }

if ($LiveDbHashBefore -ne $LiveDbHashAfter) {
    throw "live DB file hash changed during the no-write window"
}
if ([bool]$LiveWalHashBefore -ne [bool]$LiveWalHashAfter) {
    throw "live WAL presence changed during the no-write window"
}
if ($LiveWalHashBefore -and $LiveWalHashBefore -ne $LiveWalHashAfter) {
    throw "live WAL hash changed during the no-write window"
}

[pscustomobject]@{
    live_logical_state_unchanged = $true
    restored_logical_state_matches = $true
    live_db_sha256_before = $LiveDbHashBefore
    live_db_sha256_after = $LiveDbHashAfter
    live_wal_sha256_before = $LiveWalHashBefore
    live_wal_sha256_after = $LiveWalHashAfter
    compared_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\52-comparison.json"
```

The canonical snapshot explicitly compares:

- latest virtual-fill date;
- virtual-fill count;
- latest equity date;
- latest recorded cash, position value, and total equity;
- equity row count;
- virtual-order counts by status;
- every open position and its recorded quantity/cost/value/P&L fields;
- schema version and `quick_check`.

## Deliberately corrupted-copy rejection

Never modify the verified primary or secondary backup. Create a third copy, copy its metadata sidecar, append one byte, and prove that both `verify` and `restore --dry-run` reject it.

```powershell
$CorruptDir = Join-Path $EvidenceDir "corrupt-test"
New-Item -ItemType Directory -Force -Path $CorruptDir | Out-Null
$CorruptBackup = Join-Path $CorruptDir (Split-Path $SecondaryBackup -Leaf)
$CorruptMetadata = "$CorruptBackup.json"
$CorruptRestorePath = Join-Path $CorruptDir "must-not-exist.db"

Copy-Item -LiteralPath $SecondaryBackup -Destination $CorruptBackup
Copy-Item -LiteralPath $SecondaryMetadata -Destination $CorruptMetadata

& $Python -c @'
from pathlib import Path
import sys
p = Path(sys.argv[1])
with p.open("ab") as f:
    f.write(b"\x00")
'@ $CorruptBackup
if ($LASTEXITCODE -ne 0) { throw "failed to create corrupted test copy" }

$CorruptVerifyRaw = & $Python database_backup.py --config $Config verify $CorruptBackup 2>&1
$CorruptVerifyExit = $LASTEXITCODE
$CorruptVerifyRaw | Set-Content -Encoding utf8 "$EvidenceDir\60-corrupt-verify-output.txt"
if ($CorruptVerifyExit -eq 0) {
    throw "corrupted copy was incorrectly accepted by verify"
}

$CorruptRestoreRaw = & $Python database_backup.py --config $Config restore `
    $CorruptBackup $CorruptRestorePath --strategy $Strategy --dry-run 2>&1
$CorruptRestoreExit = $LASTEXITCODE
$CorruptRestoreRaw | Set-Content -Encoding utf8 "$EvidenceDir\61-corrupt-restore-output.txt"
if ($CorruptRestoreExit -eq 0) {
    throw "corrupted copy was incorrectly accepted by restore dry-run"
}
if (Test-Path -LiteralPath $CorruptRestorePath) {
    throw "corrupt restore test unexpectedly created a destination"
}

[pscustomobject]@{
    corrupt_verify_exit_code = $CorruptVerifyExit
    corrupt_restore_dry_run_exit_code = $CorruptRestoreExit
    destination_created = [bool](Test-Path -LiteralPath $CorruptRestorePath)
    result = "rejected"
} | ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\62-corrupt-test.json"
```

Retain the rejection output as evidence. Delete the corrupted test files after the Issue evidence has been attached or archived according to the operator's retention policy.

## Final no-cutover check

```powershell
$FinalConfigInfoRaw = & $Python -c @'
from pathlib import Path
from src.config import Config
import json, sys
p = Path(sys.argv[1]).resolve()
c = Config(str(p))
print(json.dumps({
    "config_path": str(p),
    "database_path": str(Path(c.database_path).resolve()),
}, ensure_ascii=False))
'@ $Config
if ($LASTEXITCODE -ne 0) { throw "final config read failed" }
$FinalConfigInfo = ($FinalConfigInfoRaw -join "`n") | ConvertFrom-Json

if ([IO.Path]::GetFullPath([string]$FinalConfigInfo.database_path) -ne $LiveDb) {
    throw "database.path changed during the drill"
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Config).Hash -ne $ConfigHash) {
    throw "config file changed during the drill"
}

[pscustomobject]@{
    status = "pending_operator_classification"
    live_database_path_unchanged = $true
    config_sha256_unchanged = $true
    automatic_cutover_performed = $false
    completed_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding utf8 "$EvidenceDir\70-final-boundary.json"
```

Only set the Issue result to `passed` after manually reviewing every evidence file and confirming that no hidden warning or mismatch was ignored.

## Manual cutover and rollback design — do not execute in this drill

A production cutover requires a separate explicit approval and maintenance procedure. The preferred design is to change configuration to point to the already verified restored path rather than overwriting the existing live DB.

Planned cutover sequence:

1. obtain separate cutover approval;
2. stop scheduler, Streamlit, manual cycle processes, and every SQLite writer;
3. create and verify a fresh pre-cutover backup;
4. re-run restore verification and logical comparison;
5. back up `config.yaml` to a timestamped file;
6. manually change only `database.path` to the verified restored DB path;
7. confirm the old live DB remains present and unchanged;
8. start one process at a time and run read-only connection/integrity checks;
9. start scheduler only after validation.

Planned rollback sequence:

1. stop every SQLite reader/writer;
2. restore the timestamped pre-cutover `config.yaml` so `database.path` points to the original DB;
3. verify the original DB with `quick_check` and virtual-trade integrity;
4. restart one scheduler process;
5. retain the failed recovery DB and logs for analysis.

Do not implement cutover by copying the restored file over the live DB. Do not execute either sequence as part of Issue #27 without a new explicit instruction.

## Issue #27 evidence template

Paste a completed copy into Issue #27. Redact usernames, machine names, webhook secrets, and sensitive absolute directory prefixes where necessary, but retain enough information to distinguish the live, backup, secondary, and restore paths.

```markdown
## Production SQLite backup/recovery drill result

Status: pending | passed | blocked | correction_required | failed

### Execution identity

- Operator:
- Production host identifier (redacted if needed):
- Start time (Asia/Tokyo):
- End time (Asia/Tokyo):
- Repository HEAD SHA:
- Expected master SHA:
- Working tree clean: yes/no
- Python version:
- Config SHA-256:
- Single-host condition confirmed: yes/no
- No-write comparison window established: yes/no

### Configuration and paths

- `database_backup.enabled`:
- `cycle_control.enabled`:
- Journal mode:
- Live DB path (redacted):
- Configured backup directory (redacted):
- Secondary storage path (redacted):
- Restore path (redacted):
- Restore path confirmed unused before run: yes/no
- Restore path confirmed different from live DB: yes/no
- Free space sufficient: yes/no
- Free-space evidence attachment:

### Backup

- Backup command exit code:
- Backup start/end/elapsed:
- Exact backup filename:
- Metadata filename:
- Metadata `created_at`:
- Metadata backup kind:
- Metadata schema version:
- Metadata source DB path matches expected: yes/no
- Metadata backup SHA-256:
- Computed backup SHA-256:
- Metadata-file SHA-256:
- Source `quick_check`: ok/fail
- Backup `quick_check`: ok/fail
- Pruned files reviewed: yes/no/not-applicable
- Latest virtual-fill date in metadata:
- Latest equity date in metadata:

### Secondary storage

- Backup copy completed: yes/no
- Metadata copy completed: yes/no
- Secondary backup SHA-256 equals primary: yes/no
- Secondary metadata SHA-256 equals primary: yes/no
- Secondary `verify` exit code:
- Secondary `quick_check`: ok/fail

### Restore and integrity

- Dry-run exit code:
- Dry-run destination remained absent: yes/no
- Restore exit code:
- Restore elapsed:
- Restored `quick_check`: ok/fail
- Integrity exit code:
- Integrity errors:
- Integrity warnings:

### Logical comparison

- Live before/after state identical: yes/no
- Live DB SHA-256 before/after identical: yes/no
- Live WAL presence/hash before/after identical or absent: yes/no
- Restored state equals live state: yes/no
- Latest virtual-fill date:
- Latest equity date:
- Latest recorded cash:
- Open-position count:
- Position details match: yes/no
- Order counts by status match: yes/no
- Evidence attachments:

### Corruption rejection

- Corruption method: appended one byte to a third copy
- `verify` exit code (must be nonzero):
- `restore --dry-run` exit code (must be nonzero):
- Corrupt destination created: no
- Rejection output attachment:

### Safety boundary

- Live DB path changed: no
- Live DB overwritten: no
- `database.path` changed: no
- Automatic cutover performed: no
- Real-order API/trade context enabled or invoked: no
- Temporary repository files added: no

### Recovery point and operator notes

- Backup creation timestamp / recovery point:
- Latest virtual-fill date:
- Latest equity date:
- Estimated data-age at backup time:
- Operational observations:
- Unexpected differences or warnings:
- Follow-up action:

### Final decision

- Acceptance criteria satisfied: yes/no
- Final status:
- Reviewer:
- Review date:
```

## Acceptance decision

Mark `passed` only when all of the following are directly evidenced:

- production backup completed using the explicit CLI;
- the source and backup passed `quick_check`;
- metadata and computed SHA-256 matched;
- primary and secondary copies matched and the secondary copy passed `verify`;
- restore dry-run and actual restore succeeded at a separate unused path;
- virtual-trade integrity reported no errors;
- live logical state and source hashes remained unchanged during the no-write window;
- restored logical state matched the live state;
- the deliberately corrupted third copy was rejected by both verification and restore dry-run;
- no config cutover, live-DB overwrite, real-order API, or trade context was used;
- exact filenames, timestamps, hashes, elapsed time, recovery point, paths, and operator actions were recorded.

Until that evidence exists, Issue #27 remains `pending` and open.
