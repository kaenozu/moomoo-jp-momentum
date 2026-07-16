from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    write(path, content.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str, *, flags: int = 0) -> None:
    content = read(path)
    updated, count = re.subn(pattern, replacement, content, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one regex match, found {count}: {pattern}")
    write(path, updated)


BENCHMARKING_MODULE = r'''"""Configuration-driven benchmarks and corporate-action-aware price access."""

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
'''


TEST_MODULE = r'''from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from src.benchmarking import (
    adjusted_price,
    benchmark_return,
    ensure_benchmark_schema,
    load_benchmark_specs,
    scan_data_quality_flags,
    seed_configured_actions,
)
from src.config import Config


def _config(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(tmp_path / "test.db")},
                "watchlist": {"symbols_file": str(tmp_path / "symbols.json")},
                "benchmark": {
                    "primary": {"code": "JP.1306", "name": "TOPIX"},
                    "secondary": {"code": "JP.1321", "name": "Nikkei 225"},
                    "reference": {"code": "JP.2559", "name": "All Country"},
                },
                "corporate_actions": [
                    {
                        "code": "JP.2559",
                        "action_date": "2026-06-09",
                        "action_type": "split",
                        "ratio_before": 1,
                        "ratio_after": 10,
                        "adjustment_factor": 0.1,
                        "status": "confirmed",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return Config(str(path))


def test_benchmark_roles_are_configuration_driven(tmp_path: Path) -> None:
    specs = load_benchmark_specs(_config(tmp_path))
    assert [item.code for item in specs.all()] == ["JP.1306", "JP.1321", "JP.2559"]


def test_split_adjustment_removes_false_price_collapse(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "CREATE TABLE daily_bars (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL)"
        )
        connection.executemany(
            "INSERT INTO daily_bars(code, date, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("JP.2559", "2026-06-05", 10000, 10000, 10000, 10000),
                ("JP.2559", "2026-06-09", 1010, 1010, 1010, 1010),
            ],
        )
        ensure_benchmark_schema(connection)
        assert seed_configured_actions(connection, config) == 1
        assert adjusted_price(connection, "JP.2559", "2026-06-05") == pytest.approx(1000.0)
        assert adjusted_price(connection, "JP.2559", "2026-06-09") == pytest.approx(1010.0)
        start, end, result = benchmark_return(
            connection, "JP.2559", "2026-06-05", "2026-06-09"
        )
        assert start == pytest.approx(1000.0)
        assert end == pytest.approx(1010.0)
        assert result == pytest.approx(1.0)
        assert scan_data_quality_flags(connection, "JP.2559") == 0


def test_schema_contains_requested_quality_tables(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "schema.db") as connection:
        ensure_benchmark_schema(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "corporate_actions" in tables
    assert "data_quality_flags" in tables
'''


RERUN_SCRIPT = r'''"""Re-run the documented P2 periods with corrected benchmark roles.

The command works on the configured SQLite database. It first initializes the
corporate-action schema and seeds configured actions, then executes all three
strategies for the four documented P2 windows and writes a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from src.backtest_runner import BacktestRunner
from src.benchmarking import (
    ensure_benchmark_schema,
    load_run_benchmark_results,
    scan_data_quality_flags,
    seed_configured_actions,
)
from src.config import load_config


PERIODS = {
    "A": ("2026-05-21", "2026-06-30"),
    "B": ("2026-01-01", "2026-03-31"),
    "C": ("2026-04-01", "2026-06-30"),
    "D": ("2026-01-01", "2026-06-30"),
}
STRATEGIES = ("momentum", "quality_low_risk", "etf_rotation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    config = load_config(args.config)

    with sqlite3.connect(config.database_path) as connection:
        ensure_benchmark_schema(connection)
        seeded = seed_configured_actions(connection, config)
        flagged = scan_data_quality_flags(connection, "JP.2559")

    results: list[dict] = []
    for label, (start, end) in PERIODS.items():
        for strategy in STRATEGIES:
            runner = BacktestRunner(config)
            run_id = runner.run(strategy, start, end)
            with sqlite3.connect(config.database_path) as connection:
                connection.row_factory = sqlite3.Row
                run = connection.execute(
                    "SELECT strategy_name, total_return_pct, max_drawdown_pct, "
                    "trade_count FROM backtest_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                benchmarks = load_run_benchmark_results(connection, run_id)
            results.append(
                {
                    "period": label,
                    "start_date": start,
                    "end_date": end,
                    "run_id": run_id,
                    "strategy": strategy,
                    "total_return_pct": run["total_return_pct"],
                    "max_drawdown_pct": run["max_drawdown_pct"],
                    "trade_count": run["trade_count"],
                    "benchmarks": benchmarks,
                }
            )

    output = (
        Path(args.output)
        if args.output
        else Path("reports") / f"p2_benchmark_rerun_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "corporate_actions_seeded": seeded,
                "new_data_quality_flags": flagged,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def patch_configs() -> None:
    benchmark_block = """benchmark:\n  primary:\n    code: JP.1306\n    name: NEXT FUNDS TOPIX連動型上場投信\n  secondary:\n    code: JP.1321\n    name: NEXT FUNDS 日経225連動型上場投信\n  reference:\n    code: JP.2559\n    name: MAXIS全世界株式（オール・カントリー）上場投信\ncorporate_actions:\n- code: JP.2559\n  action_date: '2026-06-09'\n  action_type: split\n  ratio_before: 1\n  ratio_after: 10\n  adjustment_factor: 0.1\n  status: confirmed\n  source_name: user-confirmed requirement; issuer notice pending attachment\n  source_url: ''\n  notes: 受益権1口を10口へ分割。分割前価格を0.1倍して連続系列化する。\n"""
    for path in ("config.example.yaml", "config.jp.example.yaml", "tests/fixtures/config.test.yaml"):
        if not (ROOT / path).exists():
            continue
        content = read(path)
        updated, count = re.subn(
            r"(?ms)^benchmark:\n.*?(?=^database:)",
            benchmark_block,
            content,
            count=1,
        )
        if count == 0:
            marker = "database:\n"
            if marker not in content:
                raise RuntimeError(f"{path}: database marker not found")
            updated = content.replace(marker, benchmark_block + marker, 1)
        updated = re.sub(
            r"(?m)^(\s*default_benchmark:\s*)JP\.2559\s*$",
            r"\1JP.1306",
            updated,
        )
        updated = re.sub(
            r"(?m)^(\s*benchmark_code:\s*)JP\.2559\s*$",
            r"\1JP.1306",
            updated,
        )
        write(path, updated)


def patch_models() -> None:
    path = "src/models.py"
    content = read(path)
    if "CREATE TABLE IF NOT EXISTS corporate_actions" in content:
        return
    marker = "CREATE INDEX IF NOT EXISTS idx_quotes_code ON quotes(code);"
    if marker not in content:
        raise RuntimeError("src/models.py: index marker not found")
    ddl = '''CREATE TABLE IF NOT EXISTS corporate_actions (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    code TEXT NOT NULL,\n    action_date TEXT NOT NULL,\n    action_type TEXT NOT NULL,\n    ratio_before REAL NOT NULL,\n    ratio_after REAL NOT NULL,\n    adjustment_factor REAL NOT NULL,\n    source_name TEXT,\n    source_url TEXT,\n    status TEXT NOT NULL DEFAULT 'confirmed',\n    notes TEXT,\n    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),\n    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),\n    UNIQUE(code, action_date, action_type)\n);\n\nCREATE TABLE IF NOT EXISTS data_quality_flags (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    code TEXT NOT NULL,\n    date TEXT NOT NULL,\n    flag_type TEXT NOT NULL,\n    severity TEXT NOT NULL DEFAULT 'warning',\n    observed_value REAL,\n    expected_value REAL,\n    status TEXT NOT NULL DEFAULT 'open',\n    details TEXT,\n    source TEXT,\n    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),\n    resolved_at TEXT,\n    UNIQUE(code, date, flag_type)\n);\n\nCREATE TABLE IF NOT EXISTS backtest_benchmark_results (\n    run_id INTEGER NOT NULL,\n    role TEXT NOT NULL,\n    benchmark_code TEXT NOT NULL,\n    start_date TEXT NOT NULL,\n    end_date TEXT NOT NULL,\n    start_value REAL,\n    end_value REAL,\n    return_pct REAL,\n    excess_return_pct REAL,\n    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),\n    PRIMARY KEY(run_id, role)\n);\n\nCREATE TABLE IF NOT EXISTS backtest_benchmark_equity (\n    run_id INTEGER NOT NULL,\n    strategy_name TEXT NOT NULL,\n    date TEXT NOT NULL,\n    role TEXT NOT NULL,\n    benchmark_code TEXT NOT NULL,\n    adjusted_close REAL,\n    PRIMARY KEY(run_id, date, role)\n);\n\nCREATE INDEX IF NOT EXISTS idx_corporate_actions_code_date ON corporate_actions(code, action_date);\nCREATE INDEX IF NOT EXISTS idx_data_quality_flags_code_date ON data_quality_flags(code, date);\nCREATE INDEX IF NOT EXISTS idx_data_quality_flags_status ON data_quality_flags(status);\nCREATE INDEX IF NOT EXISTS idx_backtest_benchmark_results_code ON backtest_benchmark_results(benchmark_code);\nCREATE INDEX IF NOT EXISTS idx_backtest_benchmark_equity_run ON backtest_benchmark_equity(run_id, date);\n\n'''
    write(path, content.replace(marker, ddl + marker, 1))


def patch_backtest_runner() -> None:
    path = "src/backtest_runner.py"
    replace_once(
        path,
        "from .config import Config\n",
        "from .config import Config\nfrom .benchmarking import (\n    adjusted_price,\n    ensure_benchmark_schema,\n    load_benchmark_specs,\n    save_benchmark_equity,\n    save_benchmark_result,\n    seed_configured_actions,\n)\n",
    )
    replace_once(path, '\nBM2559 = "JP.2559"\nBM1306 = "JP.1306"\n\n', "\n")
    replace_once(
        path,
        "        self.max_trade_price = universe_cfg.get(\"max_trade_price\", 20000)\n",
        "        self.max_trade_price = universe_cfg.get(\"max_trade_price\", 20000)\n"
        "        self.benchmarks = load_benchmark_specs(config)\n"
        "        with self._conn() as connection:\n"
        "            ensure_benchmark_schema(connection)\n"
        "            seed_configured_actions(connection, config)\n",
    )
    replace_once(
        path,
        "    def _benchmark_code(self) -> str:\n        return self.config.get(\"signals.relative_strength.benchmark_code\", BM1306)\n",
        "    def _benchmark_code(self) -> str:\n        return self.config.get(\n            \"signals.relative_strength.benchmark_code\",\n            self.benchmarks.primary.code,\n        )\n",
    )
    old_phase5 = '''            bm_2559 = self._benchmark_value(BM2559, day)\n            bm_1306 = self._benchmark_value(BM1306, day)\n\n            peak_equity = max(peak_equity, total_equity)\n            drawdown = max(0, (peak_equity - total_equity) / peak_equity * 100) if peak_equity else 0\n\n            with self._conn() as conn:\n                conn.execute(\n                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",\n                    (self.run_id, strategy_name, day, self.cash, pos_value, total_equity, bm_2559, bm_1306, drawdown),\n                )\n'''
    new_phase5 = '''            benchmark_values = {\n                spec.role: self._benchmark_value(spec.code, day)\n                for spec in self.benchmarks.all()\n            }\n\n            peak_equity = max(peak_equity, total_equity)\n            drawdown = max(0, (peak_equity - total_equity) / peak_equity * 100) if peak_equity else 0\n\n            with self._conn() as conn:\n                conn.execute(\n                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",\n                    (\n                        self.run_id, strategy_name, day, self.cash, pos_value,\n                        total_equity, benchmark_values[\"reference\"],\n                        benchmark_values[\"primary\"], drawdown,\n                    ),\n                )\n                for spec in self.benchmarks.all():\n                    save_benchmark_equity(\n                        conn,\n                        run_id=int(self.run_id),\n                        strategy_name=strategy_name,\n                        date=day,\n                        spec=spec,\n                        adjusted_close=benchmark_values[spec.role],\n                    )\n'''
    replace_once(path, old_phase5, new_phase5)
    old_final = '''        # ベンチマークリターン: 2559と1306を明示的に別計算\n        bm_2559_start = self._benchmark_value(BM2559, start_date)\n        bm_2559_end = self._benchmark_value(BM2559, days[-1])\n        bm_2559_ret = (\n            (bm_2559_end - bm_2559_start) / bm_2559_start * 100\n            if bm_2559_start and bm_2559_end is not None\n            else None\n        )\n        bm_1306_start = self._benchmark_value(BM1306, start_date)\n        bm_1306_end = self._benchmark_value(BM1306, days[-1])\n        bm_1306_ret = (\n            (bm_1306_end - bm_1306_start) / bm_1306_start * 100\n            if bm_1306_start and bm_1306_end is not None\n            else None\n        )\n        stats = self._calculate_run_stats()\n\n        with self._conn() as conn:\n            conn.execute(\n                """\n                UPDATE backtest_runs\n                SET final_equity=?, total_return_pct=?, max_drawdown_pct=?,\n                    win_rate=?, profit_factor=?, trade_count=?,\n                    benchmark_2559_return=?, excess_vs_2559=?,\n                    benchmark_1306_return=?, excess_vs_1306=?\n                WHERE id=?\n                """,\n                (final_equity, total_return, stats["max_drawdown_pct"],\n                 stats["win_rate"], stats["profit_factor"], stats["trade_count"],\n                 bm_2559_ret, total_return - bm_2559_ret if bm_2559_ret is not None else None,\n                 bm_1306_ret, total_return - bm_1306_ret if bm_1306_ret is not None else None,\n                 self.run_id),\n            )\n'''
    new_final = '''        stats = self._calculate_run_stats()\n\n        with self._conn() as conn:\n            returns = {\n                spec.role: save_benchmark_result(\n                    conn,\n                    run_id=int(self.run_id),\n                    spec=spec,\n                    start_date=start_date,\n                    end_date=days[-1],\n                    strategy_return_pct=total_return,\n                )\n                for spec in self.benchmarks.all()\n            }\n            primary_return = returns["primary"]\n            reference_return = returns["reference"]\n            conn.execute(\n                """\n                UPDATE backtest_runs\n                SET final_equity=?, total_return_pct=?, max_drawdown_pct=?,\n                    win_rate=?, profit_factor=?, trade_count=?,\n                    benchmark_2559_return=?, excess_vs_2559=?,\n                    benchmark_1306_return=?, excess_vs_1306=?\n                WHERE id=?\n                """,\n                (\n                    final_equity, total_return, stats["max_drawdown_pct"],\n                    stats["win_rate"], stats["profit_factor"], stats["trade_count"],\n                    reference_return,\n                    total_return - reference_return if reference_return is not None else None,\n                    primary_return,\n                    total_return - primary_return if primary_return is not None else None,\n                    self.run_id,\n                ),\n            )\n'''
    replace_once(path, old_final, new_final)
    replace_once(
        path,
        '''    def _benchmark_value(self, code: str, date: str) -> Optional[float]:\n        with self._conn() as conn:\n            row = conn.execute(\n                "SELECT close FROM daily_bars WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 1",\n                (code, date),\n            ).fetchone()\n            return float(row[0]) if row and row[0] is not None else None\n''',
        '''    def _benchmark_value(self, code: str, date: str) -> Optional[float]:\n        with self._conn() as conn:\n            return adjusted_price(conn, code, date)\n''',
    )


def patch_benchmark_manager() -> None:
    path = "src/benchmark.py"
    replace_once(
        path,
        "from .config import Config\n",
        "from .config import Config\nfrom .benchmarking import (\n    benchmark_return,\n    ensure_benchmark_schema,\n    load_benchmark_specs,\n    seed_configured_actions,\n)\n",
    )
    regex_once(
        path,
        r"(?ms)        # ベンチマークコードを設定から取得\n.*?            self\.benchmark_codes\.append\(s\.get\(\"code\", \"\"\)\)\n",
        "        self.benchmarks = load_benchmark_specs(config)\n"
        "        self.benchmark_codes = [item.code for item in self.benchmarks.all()]\n"
        "        with self._get_connection() as connection:\n"
        "            ensure_benchmark_schema(connection)\n"
        "            seed_configured_actions(connection, config)\n",
    )
    old = '''        df = self.get_benchmark_prices(code, start_date, end_date)\n\n        if df.empty or len(df) < 2:\n            return None\n\n        start_price = df.iloc[0]["close"]\n        end_price = df.iloc[-1]["close"]\n\n        if start_price and end_price and start_price > 0:\n            return (end_price - start_price) / start_price * 100\n\n        return None\n'''
    new = '''        with self._get_connection() as connection:\n            _, _, result = benchmark_return(\n                connection, code, start_date, end_date\n            )\n        return result\n'''
    replace_once(path, old, new)
    content = read(path)
    content = content.replace(
        "- JP.2559: MAXIS全世界株式（オール・カントリー）- 第一ベンチマーク\n- JP.1306: TOPIX連動ETF - 補助\n- JP.1320: 日経平均連動ETF - 補助\n- JP.2558: MAXIS米国株式（S&P500）- 補助",
        "- JP.1306: TOPIX ETF - primary\n- JP.1321: 日経225 ETF - secondary\n- JP.2559: 全世界株式 ETF - reference",
    )
    write(path, content)


def patch_validated_report() -> None:
    path = "validated_backtest.py"
    replace_once(
        path,
        "from src.backtest_runner import BacktestRunner\n",
        "from src.backtest_runner import BacktestRunner\nfrom src.benchmarking import load_benchmark_specs, load_run_benchmark_results\n",
    )
    replace_once(
        path,
        '''        curve = connection.execute(\n            "SELECT date, total_equity FROM backtest_equity_curve "\n            "WHERE run_id = ? ORDER BY date",\n            (run_id,),\n        ).fetchall()\n''',
        '''        curve = connection.execute(\n            "SELECT date, total_equity FROM backtest_equity_curve "\n            "WHERE run_id = ? ORDER BY date",\n            (run_id,),\n        ).fetchall()\n        benchmark_rows = load_run_benchmark_results(connection, run_id)\n''',
    )
    old = '''    benchmark_1306 = run["benchmark_1306_return"]\n    benchmark_2559 = run["benchmark_2559_return"]\n    excess_1306 = (\n        total_return - float(benchmark_1306)\n        if benchmark_1306 is not None\n        else None\n    )\n    excess_2559 = (\n        total_return - float(benchmark_2559)\n        if benchmark_2559 is not None\n        else None\n    )\n'''
    new = '''    specs = load_benchmark_specs(config)\n    benchmark_by_role = {row["role"]: row for row in benchmark_rows}\n    primary_row = benchmark_by_role.get("primary")\n    primary_return = primary_row["return_pct"] if primary_row else None\n    excess_primary = (\n        total_return - float(primary_return)\n        if primary_return is not None\n        else None\n    )\n    benchmark_payload: dict[str, Any] = {}\n    for spec in specs.all():\n        row = benchmark_by_role.get(spec.role)\n        value = row["return_pct"] if row else None\n        excess = total_return - float(value) if value is not None else None\n        benchmark_payload[f"{spec.code}_return_pct"] = (\n            float(value) if value is not None else None\n        )\n        benchmark_payload[f"excess_vs_{spec.code}_pct"] = excess\n'''
    replace_once(path, old, new)
    replace_once(path, "        excess_vs_1306=excess_1306,\n", "        excess_vs_1306=excess_primary,\n")
    old_payload = '''        "benchmarks": {\n            "JP.1306_return_pct": (\n                float(benchmark_1306)\n                if benchmark_1306 is not None\n                else None\n            ),\n            "excess_vs_JP.1306_pct": excess_1306,\n            "JP.2559_return_pct": (\n                float(benchmark_2559)\n                if benchmark_2559 is not None\n                else None\n            ),\n            "excess_vs_JP.2559_pct": excess_2559,\n        },\n'''
    new_payload = '''        "benchmark_roles": {\n            spec.role: spec.code for spec in specs.all()\n        },\n        "benchmarks": benchmark_payload,\n'''
    replace_once(path, old_payload, new_payload)


def patch_walk_forward() -> None:
    path = "src/walk_forward_validation.py"
    replace_once(
        path,
        "from src.backtest_runner import BacktestRunner\n",
        "from src.backtest_runner import BacktestRunner\nfrom src.benchmarking import load_benchmark_specs\n",
    )
    replace_once(
        path,
        '''    benchmark = report["benchmarks"].get(\n        "excess_vs_JP.1306_cash_matched_pct"\n    )\n''',
        '''    primary_code = report["benchmark_roles"]["primary"]\n    benchmark = report["benchmarks"].get(\n        f"excess_vs_{primary_code}_cash_matched_pct"\n    )\n''',
    )
    old = '''    full_1306 = benchmarks.get("JP.1306_return_pct")\n    full_2559 = benchmarks.get("JP.2559_return_pct")\n    matched_1306 = cash_matched_benchmark_return(\n        float(full_1306) if full_1306 is not None else None,\n        plan.active_cash,\n        plan.account_initial_cash,\n    )\n    matched_2559 = cash_matched_benchmark_return(\n        float(full_2559) if full_2559 is not None else None,\n        plan.active_cash,\n        plan.account_initial_cash,\n    )\n    strategy_return = float(performance["total_return_pct"])\n    benchmarks.update(\n        {\n            "JP.1306_full_investment_return_pct": full_1306,\n            "JP.1306_cash_matched_return_pct": matched_1306,\n            "excess_vs_JP.1306_cash_matched_pct": (\n                strategy_return - matched_1306\n                if matched_1306 is not None\n                else None\n            ),\n            "JP.2559_full_investment_return_pct": full_2559,\n            "JP.2559_cash_matched_return_pct": matched_2559,\n            "excess_vs_JP.2559_cash_matched_pct": (\n                strategy_return - matched_2559\n                if matched_2559 is not None\n                else None\n            ),\n        }\n    )\n'''
    new = '''    specs = load_benchmark_specs(config)\n    strategy_return = float(performance["total_return_pct"])\n    for spec in specs.all():\n        full = benchmarks.get(f"{spec.code}_return_pct")\n        matched = cash_matched_benchmark_return(\n            float(full) if full is not None else None,\n            plan.active_cash,\n            plan.account_initial_cash,\n        )\n        benchmarks[f"{spec.code}_full_investment_return_pct"] = full\n        benchmarks[f"{spec.code}_cash_matched_return_pct"] = matched\n        benchmarks[f"excess_vs_{spec.code}_cash_matched_pct"] = (\n            strategy_return - matched if matched is not None else None\n        )\n'''
    replace_once(path, old, new)
    replace_once(
        path,
        '''    report["benchmark_policy"] = (\n        "Status uses JP.1306 invested only with strategy active cash; "\n        "the same reserve remains zero-return cash."\n    )\n''',
        '''    report["benchmark_policy"] = (\n        f"Status uses primary benchmark {specs.primary.code} invested only "\n        "with strategy active cash; the same reserve remains zero-return cash."\n    )\n''',
    )


def patch_backtest_evaluation() -> None:
    path = "src/backtest_evaluation.py"
    marker = '''def training_selection_key(\n'''
    content = read(path)
    helper = '''def _primary_cash_matched_excess(report: Mapping[str, Any]) -> float | None:\n    roles = report.get("benchmark_roles", {})\n    primary_code = str(roles.get("primary", "JP.1306"))\n    value = report["benchmarks"].get(\n        f"excess_vs_{primary_code}_cash_matched_pct"\n    )\n    return float(value) if value is not None else None\n\n\n'''
    if helper not in content:
        if marker not in content:
            raise RuntimeError("src/backtest_evaluation.py: selection marker missing")
        content = content.replace(marker, helper + marker, 1)
    content = content.replace(
        '''    excess_raw = benchmarks.get("excess_vs_JP.1306_cash_matched_pct")\n    excess = float(excess_raw) if excess_raw is not None else -1.0e100\n''',
        '''    excess_raw = _primary_cash_matched_excess(report)\n    excess = excess_raw if excess_raw is not None else -1.0e100\n''',
        1,
    )
    content = content.replace(
        '''        value = report["benchmarks"].get(\n            "excess_vs_JP.1306_cash_matched_pct"\n        )\n        return float(value) if value is not None else None\n''',
        '''        return _primary_cash_matched_excess(report)\n''',
        1,
    )
    content = content.replace(
        '"median_cash_matched_excess_vs_JP.1306_pct": median_excess,',
        '"median_cash_matched_excess_vs_primary_pct": median_excess,',
    )
    content = content.replace(
        '"stress_median_cash_matched_excess_vs_JP.1306_pct": (',
        '"stress_median_cash_matched_excess_vs_primary_pct": (',
    )
    write(path, content)


def patch_historical_output() -> None:
    path = "historical_backtest.py"
    replace_once(
        path,
        "from src.backtest_runner import BacktestRunner\n",
        "from src.backtest_runner import BacktestRunner\nfrom src.benchmarking import load_run_benchmark_results\n",
    )
    old = '''    if run['benchmark_2559_return']:\n        print(f"  ベンチマークリターン: {run['benchmark_2559_return']:.2f}%")\n        print(f"  ベンチマーク超過: {run['excess_vs_2559']:.2f}%")\n    if run['benchmark_1306_return']:\n        print(f"  副ベンチマーク: {run['benchmark_1306_return']:.2f}%")\n'''
    new = '''    for benchmark in load_run_benchmark_results(conn, run_id):\n        value = benchmark["return_pct"]\n        excess = benchmark["excess_return_pct"]\n        value_text = f"{value:.2f}%" if value is not None else "N/A"\n        excess_text = f"{excess:.2f}%" if excess is not None else "N/A"\n        print(\n            f"  {benchmark['role']}: {benchmark['benchmark_code']} "\n            f"return={value_text}, excess={excess_text}"\n        )\n'''
    replace_once(path, old, new)


def patch_readme() -> None:
    path = "README.md"
    content = read(path)
    content = re.sub(
        r"(?m)^- \*\*主な比較対象\*\*:.*$",
        "- **主な比較対象**: primary=1306（TOPIX）、secondary=1321（日経225）、reference=2559（全世界株式）",
        content,
    )
    if "## ベンチマークと価格調整" not in content:
        content += '''\n\n## ベンチマークと価格調整\n\n比較役割は `benchmark.primary` / `benchmark.secondary` / `benchmark.reference` から解決します。\n初期設定は 1306（TOPIX）/ 1321（日経225）/ 2559（全世界株式）です。\n`corporate_actions` に登録した分割係数は、バックテストのベンチマーク価格系列に自動適用されます。\n生の `daily_bars` は監査可能性のため書き換えません。異常値は `data_quality_flags` に記録します。\n\n```bash\npython scripts/rerun_p2_benchmarks.py --config config.yaml\n```\n'''
    write(path, content)


def main() -> None:
    write("src/benchmarking.py", BENCHMARKING_MODULE)
    write("tests/test_benchmarking.py", TEST_MODULE)
    write("scripts/rerun_p2_benchmarks.py", RERUN_SCRIPT)
    patch_configs()
    patch_models()
    patch_backtest_runner()
    patch_benchmark_manager()
    patch_validated_report()
    patch_walk_forward()
    patch_backtest_evaluation()
    patch_historical_output()
    patch_readme()

    # Bootstrap files must not remain in the implementation commit.
    (ROOT / "scripts/_apply_benchmark_corporate_action_fix.py").unlink()
    workflow = ROOT / ".github/workflows/apply-benchmark-corporate-action-fix.yml"
    if workflow.exists():
        workflow.unlink()


if __name__ == "__main__":
    main()
