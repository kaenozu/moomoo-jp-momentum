"""Configuration-driven benchmarks and corporate-action-aware price access."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import reduce
from operator import mul
from typing import Any, Iterable, Mapping, Optional

from .config import Config


@dataclass(frozen=True)
class BenchmarkSpec:
    role: str
    code: str
    name: str


@dataclass(frozen=True)
class BenchmarkSet:
    primary: BenchmarkSpec
    secondary: BenchmarkSpec
    reference: BenchmarkSpec

    def all(self) -> tuple[BenchmarkSpec, BenchmarkSpec, BenchmarkSpec]:
        return (self.primary, self.secondary, self.reference)

    def by_role(self) -> dict[str, BenchmarkSpec]:
        return {item.role: item for item in self.all()}


_DEFAULTS: dict[str, tuple[str, str]] = {
    "primary": ("JP.1306", "NEXT FUNDS TOPIX連動型上場投信"),
    "secondary": ("JP.1321", "NEXT FUNDS 日経225連動型上場投信"),
    "reference": ("JP.2559", "MAXIS全世界株式（オール・カントリー）上場投信"),
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    action_date TEXT NOT NULL,
    action_type TEXT NOT NULL,
    ratio_before REAL NOT NULL,
    ratio_after REAL NOT NULL,
    adjustment_factor REAL NOT NULL,
    source_name TEXT,
    source_url TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(code, action_date, action_type)
);
CREATE INDEX IF NOT EXISTS idx_corporate_actions_code_date
    ON corporate_actions(code, action_date);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    observed_value REAL,
    expected_value REAL,
    status TEXT NOT NULL DEFAULT 'open',
    details TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    resolved_at TEXT,
    UNIQUE(code, date, flag_type)
);
CREATE INDEX IF NOT EXISTS idx_data_quality_flags_code_date
    ON data_quality_flags(code, date);
CREATE INDEX IF NOT EXISTS idx_data_quality_flags_status
    ON data_quality_flags(status);

CREATE TABLE IF NOT EXISTS backtest_benchmark_results (
    run_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    benchmark_code TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    start_value REAL,
    end_value REAL,
    return_pct REAL,
    excess_return_pct REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY(run_id, role)
);
CREATE INDEX IF NOT EXISTS idx_backtest_benchmark_results_code
    ON backtest_benchmark_results(benchmark_code);

CREATE TABLE IF NOT EXISTS backtest_benchmark_equity (
    run_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    date TEXT NOT NULL,
    role TEXT NOT NULL,
    benchmark_code TEXT NOT NULL,
    adjusted_close REAL,
    PRIMARY KEY(run_id, date, role)
);
CREATE INDEX IF NOT EXISTS idx_backtest_benchmark_equity_run
    ON backtest_benchmark_equity(run_id, date);
"""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _resolve(raw: Mapping[str, Any], role: str) -> BenchmarkSpec:
    default_code, default_name = _DEFAULTS[role]
    value: Any = raw.get(role, {})
    if role == "secondary" and isinstance(value, list):
        value = value[0] if value else {}
    item = _mapping(value)
    code = str(item.get("code") or default_code).strip()
    name = str(item.get("name") or default_name).strip()
    if not code:
        raise ValueError(f"benchmark.{role}.code must not be empty")
    return BenchmarkSpec(role=role, code=code, name=name)


def load_benchmark_specs(config: Config) -> BenchmarkSet:
    raw = _mapping(config.get("benchmark", {}))
    result = BenchmarkSet(
        primary=_resolve(raw, "primary"),
        secondary=_resolve(raw, "secondary"),
        reference=_resolve(raw, "reference"),
    )
    codes = [item.code for item in result.all()]
    if len(codes) != len(set(codes)):
        raise ValueError("primary, secondary and reference benchmark codes must be unique")
    return result


def ensure_benchmark_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)


def upsert_corporate_action(
    connection: sqlite3.Connection,
    *,
    code: str,
    action_date: str,
    action_type: str,
    ratio_before: float,
    ratio_after: float,
    adjustment_factor: float,
    source_name: str | None = None,
    source_url: str | None = None,
    status: str = "confirmed",
    notes: str | None = None,
) -> None:
    if ratio_before <= 0 or ratio_after <= 0 or adjustment_factor <= 0:
        raise ValueError("corporate action ratios and adjustment_factor must be positive")
    connection.execute(
        """
        INSERT INTO corporate_actions (
            code, action_date, action_type, ratio_before, ratio_after,
            adjustment_factor, source_name, source_url, status, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(code, action_date, action_type) DO UPDATE SET
            ratio_before=excluded.ratio_before,
            ratio_after=excluded.ratio_after,
            adjustment_factor=excluded.adjustment_factor,
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            status=excluded.status,
            notes=excluded.notes,
            updated_at=datetime('now', 'localtime')
        """,
        (
            code,
            action_date,
            action_type,
            ratio_before,
            ratio_after,
            adjustment_factor,
            source_name,
            source_url,
            status,
            notes,
        ),
    )


def seed_configured_actions(connection: sqlite3.Connection, config: Config) -> int:
    ensure_benchmark_schema(connection)
    actions = config.get("corporate_actions", [])
    if not isinstance(actions, list):
        raise ValueError("corporate_actions must be a list")
    count = 0
    for raw in actions:
        item = _mapping(raw)
        upsert_corporate_action(
            connection,
            code=str(item["code"]),
            action_date=str(item["action_date"]),
            action_type=str(item.get("action_type", "split")),
            ratio_before=float(item.get("ratio_before", 1.0)),
            ratio_after=float(item.get("ratio_after", 1.0)),
            adjustment_factor=float(item["adjustment_factor"]),
            source_name=str(item.get("source_name") or "") or None,
            source_url=str(item.get("source_url") or "") or None,
            status=str(item.get("status", "confirmed")),
            notes=str(item.get("notes") or "") or None,
        )
        count += 1
    return count


def adjustment_factor(connection: sqlite3.Connection, code: str, date: str) -> float:
    ensure_benchmark_schema(connection)
    rows = connection.execute(
        """
        SELECT adjustment_factor
        FROM corporate_actions
        WHERE code = ? AND action_date > ? AND status = 'confirmed'
        ORDER BY action_date
        """,
        (code, date),
    ).fetchall()
    return reduce(mul, (float(row[0]) for row in rows), 1.0)


def adjusted_price(
    connection: sqlite3.Connection,
    code: str,
    date: str,
    column: str = "close",
) -> Optional[float]:
    if column not in {"open", "high", "low", "close"}:
        raise ValueError(f"unsupported price column: {column}")
    row = connection.execute(
        f"SELECT date, {column} FROM daily_bars "
        "WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    if row is None or row[1] is None:
        return None
    return float(row[1]) * adjustment_factor(connection, code, str(row[0]))


def benchmark_return(
    connection: sqlite3.Connection,
    code: str,
    start_date: str,
    end_date: str,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    rows = connection.execute(
        """
        SELECT date, close FROM daily_bars
        WHERE code = ? AND date >= ? AND date <= ? AND close IS NOT NULL
        ORDER BY date
        """,
        (code, start_date, end_date),
    ).fetchall()
    if len(rows) < 2:
        return None, None, None
    start_value = float(rows[0][1]) * adjustment_factor(connection, code, str(rows[0][0]))
    end_value = float(rows[-1][1]) * adjustment_factor(connection, code, str(rows[-1][0]))
    if start_value <= 0:
        return start_value, end_value, None
    return start_value, end_value, (end_value - start_value) / start_value * 100.0


def save_benchmark_equity(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    strategy_name: str,
    date: str,
    spec: BenchmarkSpec,
    adjusted_close: Optional[float],
) -> None:
    ensure_benchmark_schema(connection)
    connection.execute(
        """
        INSERT OR REPLACE INTO backtest_benchmark_equity
        (run_id, strategy_name, date, role, benchmark_code, adjusted_close)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, strategy_name, date, spec.role, spec.code, adjusted_close),
    )


def save_benchmark_result(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    spec: BenchmarkSpec,
    start_date: str,
    end_date: str,
    strategy_return_pct: float,
) -> Optional[float]:
    start_value, end_value, return_pct = benchmark_return(
        connection, spec.code, start_date, end_date
    )
    excess = strategy_return_pct - return_pct if return_pct is not None else None
    connection.execute(
        """
        INSERT OR REPLACE INTO backtest_benchmark_results
        (run_id, role, benchmark_code, start_date, end_date, start_value,
         end_value, return_pct, excess_return_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            spec.role,
            spec.code,
            start_date,
            end_date,
            start_value,
            end_value,
            return_pct,
            excess,
        ),
    )
    return return_pct


def load_run_benchmark_results(
    connection: sqlite3.Connection, run_id: int
) -> list[dict[str, Any]]:
    ensure_benchmark_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT role, benchmark_code, start_date, end_date, start_value,
               end_value, return_pct, excess_return_pct
        FROM backtest_benchmark_results
        WHERE run_id = ?
        ORDER BY CASE role WHEN 'primary' THEN 1 WHEN 'secondary' THEN 2 ELSE 3 END
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def scan_data_quality_flags(
    connection: sqlite3.Connection,
    code: str,
    *,
    threshold_pct: float = 50.0,
    source: str = "corporate_action_adjusted_scan",
) -> int:
    ensure_benchmark_schema(connection)
    rows = connection.execute(
        """SELECT date, close FROM daily_bars
           WHERE code = ? AND close IS NOT NULL ORDER BY date""",
        (code,),
    ).fetchall()
    inserted = 0
    previous: tuple[str, float] | None = None
    for raw_date, raw_close in rows:
        date = str(raw_date)
        close = float(raw_close) * adjustment_factor(connection, code, date)
        if previous is not None and previous[1] > 0:
            return_pct = (close - previous[1]) / previous[1] * 100.0
            if abs(return_pct) >= threshold_pct:
                details = json.dumps(
                    {
                        "previous_date": previous[0],
                        "adjusted_return_pct": return_pct,
                        "threshold_pct": threshold_pct,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                before = connection.total_changes
                connection.execute(
                    """
                    INSERT OR IGNORE INTO data_quality_flags
                    (code, date, flag_type, severity, observed_value, expected_value,
                     status, details, source)
                    VALUES (?, ?, 'extreme_adjusted_return', 'error', ?, ?, 'open', ?, ?)
                    """,
                    (code, date, return_pct, threshold_pct, details, source),
                )
                inserted += connection.total_changes - before
        previous = (date, close)
    return inserted
