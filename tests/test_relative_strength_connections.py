"""Regression tests for relative-strength SQLite connection lifecycle."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore
from src.relative_strength import RelativeStrengthCalculator


def test_benchmark_return_closes_database_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "relative.db")}
    DataStore(config)

    with closing(sqlite3.connect(config.database_path)) as seed, seed:
        seed.executemany(
            "INSERT INTO daily_bars (code, date, close) VALUES (?, ?, ?)",
            [
                ("JP.1306", "2026-01-01", 100.0),
                ("JP.1306", "2026-01-02", 102.0),
                ("JP.1306", "2026-01-05", 104.0),
                ("JP.1306", "2026-01-06", 106.0),
                ("JP.1306", "2026-01-07", 108.0),
                ("JP.1306", "2026-01-08", 110.0),
            ],
        )

    calculator = RelativeStrengthCalculator(config)
    tracked_connection = sqlite3.connect(config.database_path)
    tracked_connection.row_factory = sqlite3.Row
    monkeypatch.setattr(
        calculator,
        "_get_connection",
        lambda: tracked_connection,
    )

    result = calculator.calc_benchmark_return(
        "JP.1306",
        "2026-01-08",
        5,
    )

    assert result == pytest.approx(10.0)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        tracked_connection.execute("SELECT 1")
