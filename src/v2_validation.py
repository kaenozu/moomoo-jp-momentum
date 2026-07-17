"""Non-destructive migration and backtest parity validation for V2 changes."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .config import Config
from .data_store import DataStore

REQUIRED_BACKTEST_RUN_COLUMNS = {
    "market",
    "git_commit",
    "config_hash",
    "universe_hash",
    "data_snapshot_hash",
    "data_max_date",
    "engine_version",
    "adjustment_policy",
}

MIGRATION_MANAGED_TABLES = {"corporate_actions", "data_quality_flags"}


@dataclass(frozen=True)
class TableProjection:
    table: str
    columns: tuple[str, ...]
    row_count: int
    rows_digest: str
    rows: tuple[tuple[Any, ...], ...] = field(repr=False)


@dataclass(frozen=True)
class MigrationValidationReport:
    status: str
    source_database: str
    migrated_copy: str
    source_sha256_before: str
    source_sha256_after: str
    source_unchanged: bool
    integrity_check: str
    foreign_key_violations: tuple[tuple[Any, ...], ...]
    required_columns_present: bool
    preserved_tables: tuple[str, ...]
    changed_tables: tuple[str, ...]
    idempotent: bool
    errors: tuple[str, ...]
    generated_at: str

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestDifference:
    section: str
    key: str
    field: str
    legacy_value: Any
    candidate_value: Any
    expected: bool = False


@dataclass(frozen=True)
class BacktestComparisonReport:
    status: str
    legacy_database: str
    legacy_run_id: int
    candidate_database: str
    candidate_run_id: int
    tolerance: float
    differences: tuple[BacktestDifference, ...]
    generated_at: str

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "DIFF_EXPECTED"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float):
        if math.isnan(value):
            return {"__float__": "nan"}
        if math.isinf(value):
            return {"__float__": "inf" if value > 0 else "-inf"}
    return value


def _canonical_row(row: Sequence[Any]) -> str:
    return json.dumps(
        [_normalize_value(value) for value in row],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def list_user_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    quoted = _quote_identifier(table)
    return tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted})"))


def project_table(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[str] | None = None,
) -> TableProjection:
    selected = tuple(columns or table_columns(conn, table))
    if not selected:
        return TableProjection(table, (), 0, hashlib.sha256(b"").hexdigest(), ())
    select_clause = ", ".join(_quote_identifier(column) for column in selected)
    order_clause = ", ".join(_quote_identifier(column) for column in selected)
    table_name = _quote_identifier(table)
    rows = tuple(
        tuple(row)
        for row in conn.execute(
            f"SELECT {select_clause} FROM {table_name} ORDER BY {order_clause}"
        ).fetchall()
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical_row(row).encode("utf-8"))
        digest.update(b"\n")
    return TableProjection(table, selected, len(rows), digest.hexdigest(), rows)


def logical_database_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(path) as conn:
        for table in list_user_tables(conn):
            projection = project_table(conn, table)
            digest.update(table.encode("utf-8"))
            digest.update(b"\0")
            digest.update("\0".join(projection.columns).encode("utf-8"))
            digest.update(b"\0")
            digest.update(projection.rows_digest.encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def online_backup(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        destination_path.unlink()
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source_conn:
        with sqlite3.connect(destination_path) as destination_conn:
            source_conn.backup(destination_conn)
    return destination_path


def _write_copy_config(
    original_config: str | Path,
    database_path: Path,
    output_path: Path,
) -> Path:
    with Path(original_config).open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config root must be a mapping")
    database = loaded.setdefault("database", {})
    if not isinstance(database, dict):
        raise ValueError("database config must be a mapping")
    database["path"] = str(database_path)
    output_path.write_text(
        yaml.safe_dump(loaded, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    return output_path


def _rows_preserved(before: TableProjection, after: TableProjection) -> bool:
    return not (Counter(before.rows) - Counter(after.rows))


def _schema_and_data_snapshot(path: str | Path) -> dict[str, TableProjection]:
    with sqlite3.connect(path) as conn:
        return {
            table: project_table(conn, table)
            for table in list_user_tables(conn)
        }


def validate_database_migration(
    source_database: str | Path,
    config_path: str | Path,
    output_directory: str | Path,
) -> MigrationValidationReport:
    """Apply DataStore migrations to an online backup and validate preservation."""
    source = Path(source_database).resolve()
    if not source.exists():
        raise FileNotFoundError(f"database not found: {source}")
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    copied_database = output / f"{source.stem}.v2-validation{source.suffix or '.db'}"
    copied_config = output / "config.v2-validation.yaml"

    source_hash_before = file_sha256(source)
    online_backup(source, copied_database)
    before = _schema_and_data_snapshot(copied_database)
    _write_copy_config(config_path, copied_database, copied_config)

    errors: list[str] = []
    changed_tables: list[str] = []
    preserved_tables: list[str] = []
    integrity = "not_run"
    foreign_key_violations: tuple[tuple[Any, ...], ...] = ()
    required_columns_present = False
    idempotent = False

    try:
        DataStore(Config(str(copied_config)))
        after_first = _schema_and_data_snapshot(copied_database)

        for table, before_projection in before.items():
            if table not in after_first:
                errors.append(f"table removed by migration: {table}")
                changed_tables.append(table)
                continue
            after_columns = set(after_first[table].columns)
            missing_columns = set(before_projection.columns) - after_columns
            if missing_columns:
                errors.append(
                    f"columns removed from {table}: {sorted(missing_columns)}"
                )
                changed_tables.append(table)
                continue
            with sqlite3.connect(copied_database) as conn:
                comparable_after = project_table(
                    conn, table, before_projection.columns
                )
            if table in MIGRATION_MANAGED_TABLES:
                preserved = _rows_preserved(before_projection, comparable_after)
            else:
                preserved = (
                    before_projection.row_count == comparable_after.row_count
                    and before_projection.rows_digest == comparable_after.rows_digest
                )
            if preserved:
                preserved_tables.append(table)
            else:
                changed_tables.append(table)
                errors.append(f"existing rows changed during migration: {table}")

        with sqlite3.connect(copied_database) as conn:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "missing"
            foreign_key_violations = tuple(
                tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
            )
            run_columns = set(table_columns(conn, "backtest_runs"))
            required_columns_present = REQUIRED_BACKTEST_RUN_COLUMNS.issubset(
                run_columns
            )

        first_digest = logical_database_digest(copied_database)
        DataStore(Config(str(copied_config)))
        second_digest = logical_database_digest(copied_database)
        idempotent = first_digest == second_digest
    except Exception as exc:  # pragma: no cover - defensive report path
        errors.append(f"migration failed: {type(exc).__name__}: {exc}")

    source_hash_after = file_sha256(source)
    source_unchanged = source_hash_before == source_hash_after
    if not source_unchanged:
        errors.append("source database changed")
    if integrity != "ok":
        errors.append(f"integrity_check failed: {integrity}")
    if foreign_key_violations:
        errors.append(
            f"foreign_key_check found {len(foreign_key_violations)} violation(s)"
        )
    if not required_columns_present:
        errors.append("required backtest_runs metadata columns are missing")
    if not idempotent:
        errors.append("migration is not idempotent")

    return MigrationValidationReport(
        status="PASS" if not errors else "MIGRATION_FAILED",
        source_database=str(source),
        migrated_copy=str(copied_database),
        source_sha256_before=source_hash_before,
        source_sha256_after=source_hash_after,
        source_unchanged=source_unchanged,
        integrity_check=integrity,
        foreign_key_violations=foreign_key_violations,
        required_columns_present=required_columns_present,
        preserved_tables=tuple(sorted(preserved_tables)),
        changed_tables=tuple(sorted(set(changed_tables))),
        idempotent=idempotent,
        errors=tuple(errors),
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


def _fetch_rows(
    database: str | Path,
    query: str,
    params: tuple[Any, ...],
) -> tuple[dict[str, Any], ...]:
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        return tuple(dict(row) for row in conn.execute(query, params).fetchall())


def _run_snapshot(database: str | Path, run_id: int) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "orders": _fetch_rows(
            database,
            """
            SELECT code, side, quantity, order_type, status, signal_date, exit_reason
            FROM backtest_orders WHERE run_id=?
            ORDER BY code, side, signal_date, id
            """,
            (run_id,),
        ),
        "fills": _fetch_rows(
            database,
            """
            SELECT code, side, quantity, price, filled_at, fill_mode
            FROM backtest_fills WHERE run_id=?
            ORDER BY filled_at, code, side, id
            """,
            (run_id,),
        ),
        "positions": _fetch_rows(
            database,
            """
            SELECT code, quantity, avg_cost, market_price, market_value,
                   unrealized_pl, realized_pl
            FROM backtest_positions WHERE run_id=?
            ORDER BY code
            """,
            (run_id,),
        ),
        "equity": _fetch_rows(
            database,
            """
            SELECT date, cash, position_value, total_equity, drawdown_pct
            FROM backtest_equity_curve WHERE run_id=?
            ORDER BY date
            """,
            (run_id,),
        ),
    }


def _values_equal(legacy: Any, candidate: Any, tolerance: float) -> bool:
    if legacy is None or candidate is None:
        return legacy is candidate
    if isinstance(legacy, (int, float)) and isinstance(candidate, (int, float)):
        return math.isclose(
            float(legacy),
            float(candidate),
            rel_tol=tolerance,
            abs_tol=tolerance,
        )
    return legacy == candidate


def compare_backtest_runs(
    legacy_database: str | Path,
    legacy_run_id: int,
    candidate_database: str | Path,
    candidate_run_id: int,
    *,
    tolerance: float = 1e-6,
    expected_difference_fields: Iterable[str] = (),
) -> BacktestComparisonReport:
    """Compare normalized order, fill, position, and equity outputs."""
    expected = set(expected_difference_fields)
    legacy = _run_snapshot(legacy_database, legacy_run_id)
    candidate = _run_snapshot(candidate_database, candidate_run_id)
    differences: list[BacktestDifference] = []

    for section in ("orders", "fills", "positions", "equity"):
        left_rows = legacy[section]
        right_rows = candidate[section]
        max_rows = max(len(left_rows), len(right_rows))
        for index in range(max_rows):
            key = str(index)
            if index >= len(left_rows):
                field_name = "__row__"
                path = f"{section}.{field_name}"
                differences.append(
                    BacktestDifference(
                        section,
                        key,
                        field_name,
                        None,
                        right_rows[index],
                        path in expected,
                    )
                )
                continue
            if index >= len(right_rows):
                field_name = "__row__"
                path = f"{section}.{field_name}"
                differences.append(
                    BacktestDifference(
                        section,
                        key,
                        field_name,
                        left_rows[index],
                        None,
                        path in expected,
                    )
                )
                continue
            left = left_rows[index]
            right = right_rows[index]
            fields = sorted(set(left) | set(right))
            for field_name in fields:
                if _values_equal(left.get(field_name), right.get(field_name), tolerance):
                    continue
                path = f"{section}.{field_name}"
                differences.append(
                    BacktestDifference(
                        section=section,
                        key=key,
                        field=field_name,
                        legacy_value=left.get(field_name),
                        candidate_value=right.get(field_name),
                        expected=path in expected,
                    )
                )

    unexpected = [difference for difference in differences if not difference.expected]
    if unexpected:
        status = "DIFF_UNEXPECTED"
    elif differences:
        status = "DIFF_EXPECTED"
    else:
        status = "PASS"
    return BacktestComparisonReport(
        status=status,
        legacy_database=str(Path(legacy_database).resolve()),
        legacy_run_id=legacy_run_id,
        candidate_database=str(Path(candidate_database).resolve()),
        candidate_run_id=candidate_run_id,
        tolerance=tolerance,
        differences=tuple(differences),
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


def write_json_report(report: MigrationValidationReport | BacktestComparisonReport, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output


def write_markdown_report(report: MigrationValidationReport | BacktestComparisonReport, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(report, MigrationValidationReport):
        lines = [
            "# V2 Migration Validation",
            "",
            f"- Status: **{report.status}**",
            f"- Source unchanged: `{report.source_unchanged}`",
            f"- Integrity check: `{report.integrity_check}`",
            f"- Foreign-key violations: `{len(report.foreign_key_violations)}`",
            f"- Required columns present: `{report.required_columns_present}`",
            f"- Idempotent: `{report.idempotent}`",
            f"- Migrated copy: `{report.migrated_copy}`",
            "",
            "## Errors",
        ]
        lines.extend(f"- {error}" for error in report.errors)
        if not report.errors:
            lines.append("- None")
        lines.extend(
            [
                "",
                "## Preserved tables",
                ", ".join(report.preserved_tables) or "None",
                "",
                "## Changed tables",
                ", ".join(report.changed_tables) or "None",
            ]
        )
    else:
        lines = [
            "# V2 Backtest Comparison",
            "",
            f"- Status: **{report.status}**",
            f"- Legacy run: `{report.legacy_database}` / `{report.legacy_run_id}`",
            f"- Candidate run: `{report.candidate_database}` / `{report.candidate_run_id}`",
            f"- Tolerance: `{report.tolerance}`",
            f"- Differences: `{len(report.differences)}`",
            "",
            "## Differences",
        ]
        if not report.differences:
            lines.append("- None")
        else:
            for difference in report.differences:
                marker = "expected" if difference.expected else "unexpected"
                lines.append(
                    f"- `{difference.section}[{difference.key}].{difference.field}` "
                    f"({marker}): `{difference.legacy_value}` → `{difference.candidate_value}`"
                )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
