# Production SQLite backup and recovery drill

This runbook is the execution and evidence checklist for Issue #27.

The operational result remains **pending** until the script is executed on the production Windows PC and every evidence item is reviewed. Repository tests and GitHub Actions validate the tooling, but they do not replace production-PC evidence.

## Safety boundary

The drill is limited to the SQLite virtual-trading database.

Mandatory constraints:

- Do not enable, import, instantiate, or call any moomoo/Futu order API or trade context.
- Do not overwrite or replace the configured live database.
- Do not change the production `config.yaml`.
- Do not restore to an existing path.
- Do not perform a production cutover.
- Run from one supported Windows host only.
- Stop the scheduler, Streamlit, manual-cycle execution, and every other SQLite writer for the comparison window.
- Keep the evidence directory, isolated primary backup, secondary copy, restored DB, and corruption-test files outside the repository.
- Do not claim success without production-PC command output, paths, hashes, timestamps, and operator confirmation.

Use these statuses:

- `passed`: every acceptance check has direct production-PC evidence.
- `pending`: the production drill or evidence review is incomplete.
- `blocked`: a required path, storage target, maintenance window, or access condition is unavailable.
- `correction_required`: a command, implementation, or runbook defect must be fixed before retrying.
- `failed`: the drill ran and an acceptance check failed.

Any unresolved ambiguity, unexpected write, warning, or mismatch prevents `passed`.

## Executable script

Use:

```text
scripts/sqlite_backup_recovery_drill.ps1
```

The script has two explicit modes:

1. `-PreflightOnly` performs read-only inspection and prints JSON. It does not create an evidence directory, backup, secondary copy, or restored DB.
2. `-ConfirmProductionExecution` performs the drill. It is rejected unless explicitly supplied.

The script requires an exact approved Git SHA and rejects a dirty working tree. It resolves the real checkout root from its own script location with `git rev-parse --show-toplevel`, so repository-containment checks do not depend on the operator's current directory. Relative production-config paths are resolved from that checkout root; relative evidence, secondary, and restore paths are resolved from the invocation directory. It rejects evidence, secondary, and restore paths anywhere inside the repository.

## Isolated backup configuration

The explicit backup CLI performs retention pruning after a successful backup. The drill therefore creates a copy of the production configuration inside the evidence directory and changes only:

- `database.path` to the resolved absolute live DB path;
- `database_backup.enabled` to `true`;
- `database_backup.directory` to a new empty drill-primary directory;
- retention values to positive values that cannot prune the single drill backup;
- `database_backup.verify_after_backup` to `true`.

The production configuration remains unchanged. The isolated configuration is evidence and is never used as a cutover configuration.

## Prerequisites

Before preflight:

- identify the exact production checkout and expected Git SHA;
- identify the production `config.yaml`;
- choose a new evidence directory outside the repository;
- choose a new secondary-storage directory outside the repository;
- choose a new unused restore path outside the repository;
- confirm the secondary directory is on the intended storage device;
- confirm sufficient free space for the primary backup, secondary copy, restored DB, corruption copy, metadata, and logs;
- confirm only one host uses the operational SQLite DB;
- plan a no-write maintenance window;
- know how to stop every SQLite writer;
- confirm no real-order API or trade context is enabled.

## 1. Run read-only preflight

Replace the values before execution.

```powershell
pwsh -File .\scripts\sqlite_backup_recovery_drill.ps1 `
  -ExpectedHead "<approved-git-sha>" `
  -ProductionConfig ".\config.yaml" `
  -EvidenceDir "D:\moomoo-drill-evidence\2026-07-13T190000" `
  -SecondaryDir "E:\moomoo-backups\recovery-drill-20260713" `
  -RestorePath "D:\moomoo-recovery\moomoo-restored-20260713.db" `
  -Strategy "momentum" `
  -PreflightOnly
```

Review the JSON and directly confirm:

- `head_sha` equals the explicitly approved SHA;
- the production config path and SHA-256 are correct;
- `live_db` is the intended virtual-trading database;
- `configured_backup_directory` is only being recorded, not used by the drill;
- `evidence_directory`, `secondary_directory`, and `restore_path` are distinct and outside the repository;
- the restore path does not exist;
- journal mode is recorded;
- free-space values are sufficient;
- the single-host condition is true;
- the no-write window is ready;
- no order/trade API will be invoked.

If any item is uncertain, stop with `blocked` or `correction_required`.

## 2. Establish the no-write window

Before full execution:

1. stop the scheduler;
2. stop Streamlit and any application process using the DB;
3. stop manual daily-cycle or maintenance commands;
4. confirm no second host accesses the DB;
5. confirm the selected evidence, secondary, and restore targets are still unused;
6. retain the preflight JSON in the operator record.

Do not rely on the host-local cycle lock to protect against another host.

## 3. Execute the drill

Run in the same verified checkout with the same parameter values, replacing `-PreflightOnly` with `-ConfirmProductionExecution`.

```powershell
pwsh -File .\scripts\sqlite_backup_recovery_drill.ps1 `
  -ExpectedHead "<approved-git-sha>" `
  -ProductionConfig ".\config.yaml" `
  -EvidenceDir "D:\moomoo-drill-evidence\2026-07-13T190000" `
  -SecondaryDir "E:\moomoo-backups\recovery-drill-20260713" `
  -RestorePath "D:\moomoo-recovery\moomoo-restored-20260713.db" `
  -Strategy "momentum" `
  -ConfirmProductionExecution
```

The script performs, in order:

1. exact Git SHA and clean-tree checks;
2. production config and live DB resolution;
3. isolated drill-config creation;
4. canonical read-only live snapshot and live DB/WAL hash capture;
5. verified SQLite Online Backup API backup to a new primary drill directory;
6. metadata, SHA-256, and `quick_check` verification;
7. copy of backup and metadata to the new secondary directory;
8. secondary-copy hash and CLI verification;
9. restore dry-run with no destination creation;
10. restore to the new unused path;
11. virtual-trade integrity validation;
12. live-before/live-after/restored logical comparison;
13. live DB and WAL presence/hash comparison;
14. corruption of a third copy and rejection by both `verify` and `restore --dry-run`;
15. production config and `database.path` unchanged checks;
16. final result JSON with command-specific backup and restore elapsed times.

## Evidence files

Expected evidence includes:

- `00-preflight.json`
- `10-live-before.json`
- `20-backup-output.txt`
- `21-primary-verify-output.txt`
- `30-secondary-verify-output.txt`
- `40-restore-dry-run-output.txt`
- `41-restore-output.txt`
- `50-live-after.json`
- `51-restored.json`
- `60-corrupt-verify-output.txt`
- `61-corrupt-restore-output.txt`
- `70-final-result.json`
- `drill-config.yaml`
- isolated primary backup and metadata
- secondary backup and metadata
- restored DB
- deliberately corrupted third copy and metadata

The primary backup command must report no pruned files because the drill uses a new empty directory.

## Stop conditions

Stop immediately when:

- Git SHA or working-tree checks fail;
- any path is ambiguous, inside the repository, reused, or equal to another protected path;
- the isolated config cannot be created;
- the source snapshot or source `quick_check` fails;
- backup, primary verification, secondary verification, restore dry-run, restore, or integrity validation fails;
- metadata is absent or a checksum/path differs;
- the live logical state, DB hash, or WAL presence/hash changes during the no-write window;
- the restored logical state differs from the live state;
- a corrupted copy is accepted;
- the production config or `database.path` changes;
- a command would invoke an order/trade API or overwrite an existing file.

Do not continue merely to collect more output after a safety invariant fails.

## Acceptance review

Mark `passed` only when all of the following are directly evidenced:

- exact approved Git SHA and clean working tree;
- production config hash recorded and unchanged;
- live, primary, secondary, and restore paths are distinct;
- source and backup `quick_check` succeeded;
- metadata and computed backup SHA-256 matched;
- no existing production backup generation was pruned;
- secondary backup and metadata hashes matched the primary files;
- secondary `verify` succeeded;
- restore dry-run succeeded and created no destination;
- actual restore succeeded to a new unused path;
- virtual-trade integrity reported zero errors;
- live logical state and source DB/WAL hashes remained unchanged;
- restored logical state matched the live state;
- corrupted copy was rejected by verification and restore dry-run;
- no production config change, live-DB overwrite, automatic cutover, real-order API, or trade context occurred;
- exact filenames, hashes, recovery point, elapsed times, storage location, and operator actions were retained.

Warnings are not automatically success. Review and explain every warning before deciding whether the result can be accepted.

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
- PowerShell version:
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
- All output paths outside repository: yes/no
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

## Cutover is out of scope

Do not copy the restored DB over the live DB. Do not change `database.path` during this drill.

Any production cutover or rollback requires a separate explicit instruction, a new maintenance procedure, and a fresh verified backup.

Until production-PC evidence is attached and reviewed, Issue #27 remains `pending` and open.
