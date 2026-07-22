"""QFQ価格と受益権分割メタデータの契約テスト。"""

import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore


def _create_store(tmp_path: Path) -> DataStore:
    db_path = tmp_path / "moomoo.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f'database:\n  path: "{db_path.as_posix()}"\n',
        encoding="utf-8",
    )
    return DataStore(Config(str(config_path)))


def _insert_prices(
    store: DataStore,
    code: str,
    rows: list[tuple[str, float]],
) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.executemany(
            """
            INSERT INTO daily_bars
                (code, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (code, date, close, close, close, close, 1000, close * 1000)
                for date, close in rows
            ],
        )


def _return_pct(store: DataStore, code: str, start: str, end: str) -> float:
    bars = store.get_daily_bars(code, start_date=start, end_date=end, limit=10)
    bars = bars.sort_values("date")
    start_close = float(bars.iloc[0]["close"])
    end_close = float(bars.iloc[-1]["close"])
    return (end_close - start_close) / start_close * 100


def test_1306_qfq_prices_are_not_adjusted_twice(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    _insert_prices(
        store,
        "JP.1306",
        [("2026-03-31", 250.0), ("2026-04-01", 252.0)],
    )

    observed_return = _return_pct(
        store,
        "JP.1306",
        "2026-03-31",
        "2026-04-01",
    )

    assert observed_return == pytest.approx(0.8, abs=0.01)


def test_2559_qfq_prices_are_not_adjusted_twice(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    _insert_prices(
        store,
        "JP.2559",
        [("2026-06-08", 2000.0), ("2026-06-09", 2010.0)],
    )

    observed_return = _return_pct(
        store,
        "JP.2559",
        "2026-06-08",
        "2026-06-09",
    )

    assert observed_return == pytest.approx(0.5, abs=0.01)


def test_schema_and_confirmed_actions_are_initialized(tmp_path: Path) -> None:
    store = _create_store(tmp_path)

    with sqlite3.connect(store.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        actions = conn.execute(
            """
            SELECT code, effective_date, adjustment_factor, status
            FROM corporate_actions
            ORDER BY code
            """
        ).fetchall()

    assert "corporate_actions" in tables
    assert "data_quality_flags" in tables
    assert actions == [
        ("JP.1306", "2026-04-01", 0.1, "confirmed"),
        ("JP.2559", "2026-06-09", 0.1, "confirmed"),
    ]
