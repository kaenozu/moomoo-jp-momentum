from __future__ import annotations

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
