from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from src.yfinance_data import (
    fetch_adjusted_history,
    record_splits,
    to_yfinance_ticker,
    upsert_yfinance_bars,
)


def test_to_yfinance_ticker() -> None:
    assert to_yfinance_ticker("JP.7203") == "7203.T"


def test_fetch_adjusted_history_detects_split() -> None:
    captured = {}

    class FakeTicker:
        def history(self, **kwargs):
            captured.update(kwargs)
            return pd.DataFrame(
                {
                    "Open": [100.0, 51.0],
                    "High": [102.0, 53.0],
                    "Low": [99.0, 50.0],
                    "Close": [101.0, 52.0],
                    "Volume": [1000, 2000],
                    "Stock Splits": [0.0, 2.0],
                },
                index=pd.to_datetime(["2022-01-04", "2022-01-05"]),
            )

    result = fetch_adjusted_history(
        "JP.7203",
        "2022-01-01",
        "2022-01-05",
        ticker_factory=lambda ticker: FakeTicker(),
    )

    assert captured["auto_adjust"] is True
    assert captured["actions"] is True
    assert captured["end"] == "2022-01-06"
    assert list(result.bars["time_key"]) == ["2022-01-04", "2022-01-05"]
    assert result.splits[0].effective_date == "2022-01-05"
    assert result.splits[0].ratio == 2.0


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE daily_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                turnover REAL,
                source TEXT NOT NULL DEFAULT 'moomoo',
                turnover_source TEXT NOT NULL DEFAULT 'actual',
                UNIQUE(code, date)
            );
            CREATE TABLE corporate_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                action_type TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                ratio_before REAL NOT NULL,
                ratio_after REAL NOT NULL,
                adjustment_factor REAL NOT NULL,
                source_name TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                notes TEXT,
                UNIQUE(code, action_type, effective_date)
            );
            """
        )


def test_upsert_preserves_moomoo_and_updates_yfinance(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO daily_bars (
                code, date, open, high, low, close, volume, turnover,
                source, turnover_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("JP.7203", "2022-01-04", 1, 1, 1, 999, 1, 999, "moomoo", "actual"),
                ("JP.7203", "2022-01-05", 1, 1, 1, 10, 1, 10, "yfinance", "estimated"),
            ],
        )

    bars = pd.DataFrame(
        [
            {
                "time_key": "2022-01-04",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 10,
                "turnover": 1000,
            },
            {
                "time_key": "2022-01-05",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20,
                "volume": 20,
                "turnover": 400,
            },
            {
                "time_key": "2022-01-06",
                "open": 30,
                "high": 31,
                "low": 29,
                "close": 30,
                "volume": 30,
                "turnover": 900,
            },
        ]
    )

    stats = upsert_yfinance_bars(db_path, "JP.7203", bars)
    assert stats.inserted == 1
    assert stats.updated == 1
    assert stats.preserved == 1

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, close, source FROM daily_bars ORDER BY date"
        ).fetchall()
    assert rows == [
        ("2022-01-04", 999.0, "moomoo"),
        ("2022-01-05", 20.0, "yfinance"),
        ("2022-01-06", 30.0, "yfinance"),
    ]


def test_record_splits_is_idempotent(tmp_path: Path) -> None:
    from src.yfinance_data import DetectedSplit

    db_path = tmp_path / "test.db"
    _create_db(db_path)
    splits = (DetectedSplit("2022-01-05", 2.0),)
    assert record_splits(db_path, "JP.7203", splits) == 1
    assert record_splits(db_path, "JP.7203", splits) == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT ratio_after, adjustment_factor FROM corporate_actions"
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert count == 1
    assert row == (2.0, 0.5)


def test_upsert_handles_more_than_sqlite_variable_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _create_db(db_path)
    dates = pd.date_range("2022-01-01", periods=1200, freq="D")
    bars = pd.DataFrame(
        {
            "time_key": dates.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100,
            "turnover": 10000.0,
        }
    )

    stats = upsert_yfinance_bars(db_path, "JP.7203", bars)

    assert stats.inserted == 1200
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    assert count == 1200


def test_fetch_adjusted_history_skips_invalid_price_row() -> None:
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {
                    "Open": [100.0, None],
                    "High": [102.0, 10.0],
                    "Low": [99.0, 9.0],
                    "Close": [101.0, 9.5],
                    "Volume": [1000, 2000],
                    "Stock Splits": [0.0, 0.0],
                },
                index=pd.to_datetime(["2022-01-04", "2022-01-05"]),
            )

    result = fetch_adjusted_history(
        "JP.7203",
        "2022-01-01",
        "2022-01-05",
        ticker_factory=lambda ticker: FakeTicker(),
    )

    assert list(result.bars["time_key"]) == ["2022-01-04"]


def test_record_splits_preserves_user_confirmed_action(tmp_path: Path) -> None:
    from src.yfinance_data import DetectedSplit

    db_path = tmp_path / "test.db"
    _create_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO corporate_actions (
                code, action_type, effective_date, ratio_before, ratio_after,
                adjustment_factor, source_name, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "JP.1306",
                "split",
                "2026-04-01",
                1.0,
                10.0,
                0.1,
                "user-confirmed",
                "confirmed",
                "verified",
            ),
        )

    record_splits(
        db_path,
        "JP.1306",
        (DetectedSplit("2026-04-01", 5.0),),
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT ratio_after, adjustment_factor, source_name, notes
            FROM corporate_actions
            WHERE code = 'JP.1306'
            """
        ).fetchone()
    assert row == (10.0, 0.1, "user-confirmed", "verified")
