# V2 migration and parity validation

PR #53 changes execution accounting and extends `backtest_runs`. Before using the branch with a production database, run both gates below.

## 1. Non-destructive migration validation

```bash
python scripts/validate_v2_migration.py \
  --config config.yaml \
  --database data/moomoo.db \
  --output-dir reports/v2_validation
```

The command never initializes or migrates the source database. It uses SQLite's online backup API, writes a validation copy under the output directory, and runs `DataStore` migration only against that copy.

A successful result requires all of the following:

- the source database SHA-256 is unchanged;
- all pre-existing tables and rows are preserved;
- the required V2 columns exist in `backtest_runs`;
- `PRAGMA integrity_check` returns `ok`;
- `PRAGMA foreign_key_check` returns no rows;
- applying the migration a second time produces the same logical database state.

Outputs:

- `migration-report.json` for automation;
- `migration-report.md` for review;
- `*.v2-validation.db`, the migrated copy used by the checks.

`MIGRATION_FAILED` must block production rollout.

## 2. Legacy/candidate backtest comparison

Run the same strategy, date range, configuration, universe, and initial cash with the legacy and candidate versions. Then compare the resulting run IDs:

```bash
python scripts/compare_backtest_runs.py \
  --legacy-db reports/v2_validation/legacy.db \
  --legacy-run-id 1 \
  --candidate-db reports/v2_validation/candidate.db \
  --candidate-run-id 1 \
  --output-dir reports/v2_validation
```

The comparator normalizes and checks:

- orders;
- fills;
- final positions;
- daily cash, position value, total equity, and drawdown.

IDs and creation timestamps are intentionally excluded. Numeric values use a default absolute and relative tolerance of `1e-6`.

Known, reviewed differences may be allowlisted by section and field:

```bash
--allow-field fills.price
```

Statuses:

- `PASS`: no differences;
- `DIFF_EXPECTED`: only explicitly allowlisted differences;
- `DIFF_UNEXPECTED`: at least one unapproved difference.

Only `PASS` or a reviewed `DIFF_EXPECTED` result may proceed.

## Production rollout order

1. Stop the daily cycle and any process writing to SQLite.
2. Back up `data/moomoo.db` and any `-wal`/`-shm` sidecars.
3. Run the migration gate against the stopped database.
4. Run the legacy/candidate backtest comparison over a representative long period.
5. Confirm OpenD data retrieval without enabling any real-order path.
6. Apply the candidate migration to production.
7. Keep the backup until at least one complete daily cycle and report generation finish successfully.

The validation tools do not call OpenD and do not use any real-order API.
