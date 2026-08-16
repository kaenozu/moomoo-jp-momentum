import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.momentum_v2.adapters import SQLiteReadOnlyBarSource


def make_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE daily_bars (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("JP.A", "2026-01-01", 100, 102, 99, 101, 1000),
                ("JP.BENCH", "2026-01-01", 200, 202, 199, 201, 2000),
                ("JP.A", "2026-01-02", 101, 103, 100, 102, 1100),
                ("JP.BENCH", "2026-01-02", 201, 203, 200, 202, 2100),
            ],
        )


def test_sqlite_source_loads_bars_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "moomoo.db"
    make_db(db_path)
    before = db_path.stat().st_size

    snapshots = SQLiteReadOnlyBarSource(db_path).load_snapshots(
        date(2026, 1, 1),
        date(2026, 1, 2),
        benchmark_code="JP.BENCH",
    )

    assert len(snapshots) == 2
    assert len(snapshots[0].bars) == 2
    assert snapshots[0].bars[0].code == "JP.A"
    assert db_path.stat().st_size == before


def test_sqlite_source_rejects_missing_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="database not found"):
        SQLiteReadOnlyBarSource(tmp_path / "missing.db").load_snapshots(
            date(2026, 1, 1), date(2026, 1, 2)
        )
