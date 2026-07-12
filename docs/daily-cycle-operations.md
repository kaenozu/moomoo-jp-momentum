# Guarded daily-cycle operations

The operational entry point remains `run_daily_cycle.py`. Both scheduler and manual CLI execution therefore use the same target-date lock and SQLite run ledger when `cycle_control.enabled` is true.

## Configuration

```yaml
database_backup:
  enabled: true
  directory: backups
  retain_daily: 7
  retain_weekly: 4
  retain_pre_cycle: 7
  retain_post_cycle: 7
  verify_after_backup: true

cycle_control:
  enabled: true
  lock_directory: .runtime/cycle-locks
  stale_after_seconds: 21600
```

JPX non-trading days and `--dry-run` return before lock creation, backup, OpenD connection, or SQLite access.

## Normal execution

```bash
python run_daily_cycle.py --date 2026-07-13 --config config.yaml
```

For a trading day, the controlled sequence is:

1. acquire an atomic target-date file lock when cycle control is enabled
2. perform a read-only duplicate-date preflight before any backup side effect
3. create and verify a pre-cycle backup when the source database existed before cycle setup
4. create a `RUNNING` row in `cycle_runs` when cycle control is enabled
5. record pre-backup and daily-pipeline stages in `cycle_run_stages`
6. execute the existing daily pipeline
7. create and verify a post-cycle backup
8. mark the run `SUCCEEDED` and release the lock

Any exception after a run row is created is handled by a best-effort terminal update to `FAILED` before the lock is released. A failure to write that terminal update is logged without replacing the original exception. Backup failures use the operational event type `database_backup_failure`. Concurrent execution and unapproved same-day reruns use `cycle_concurrency_failure`.

When backup is enabled but cycle control is disabled, the duplicate-date preflight still runs before pre-cycle or post-cycle backup files can be created. No new run ledger row is written in that mode.

## Same-day rerun

A target date with any existing run record is not rerun implicitly. Supply both options and retain a specific audit reason:

```bash
python run_daily_cycle.py \
  --date 2026-07-13 \
  --config config.yaml \
  --force-rerun \
  --rerun-reason "Corrected source market data"
```

`--force-rerun` without a non-empty `--rerun-reason` is rejected.

## Stale lock recovery

A lock is considered for recovery only after `stale_after_seconds`. A same-host lock is not recovered while its PID is alive. A recovered stale lock is recorded in the new run, and any prior `RUNNING` ledger rows for the target date are marked `FAILED` with `error_type=stale_lock_recovered`.

The rerun still requires `--force-rerun` and an explicit reason.

## Host boundary

The file lock is intentionally host-local. It prevents overlap between the scheduler and manual CLI processes on the same Windows machine, which is the supported deployment model.

Do not run two hosts against the same operational SQLite database. Before introducing a primary/failover or shared-database deployment, add a shared atomic lease or database-level cross-host lock and validate its failure recovery separately. A local `.runtime/cycle-locks` directory is not a cross-host mutex.

## Ledger tables

`cycle_runs` records:

- target date and terminal status: `RUNNING`, `SUCCEEDED`, `SKIPPED`, or `FAILED`
- start and finish times
- forced-rerun flag and reason
- Git commit SHA
- SHA-256 fingerprint of the loaded configuration file
- PID and hostname
- stale-lock recovery flag
- error type and message
- final result JSON

`cycle_run_stages` records stage start/finish times, status, details, and failure messages.

## Recovery boundaries

- No automatic database restore is performed.
- Restore must target a new unused path.
- The live database is never overwritten by the recovery CLI.
- No real-order API is introduced or called.
