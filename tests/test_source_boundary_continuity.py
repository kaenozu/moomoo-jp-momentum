"""ソース境界（moomoo/yfinance）の価格連続性回帰テスト。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from pathlib import Path

import pandas as pd
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


def _insert_daily_bars(
    db_path: Path,
    code: str,
    rows: list[tuple[str, float, str]],
) -> None:
    """Insert daily bars with source tag."""
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO daily_bars
                (code, date, open, high, low, close, volume, turnover, source, turnover_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    code,
                    date,
                    close,
                    close,
                    close,
                    close,
                    1000,
                    close * 1000,
                    source,
                    "actual" if source == "moomoo" else "estimated",
                )
                for date, close, source in rows
            ],
        )


def test_moomoo_yfinance_boundary_is_continuous(tmp_path: Path) -> None:
    """moomoo と yfinance の境界で価格が不連続でないことを確認する。"""
    store = _create_store(tmp_path)
    code = "JP.7203"

    _insert_daily_bars(
        store.db_path,
        code,
        [
            ("2026-05-20", 1000.0, "moomoo"),
            ("2026-05-21", 1005.0, "moomoo"),
            ("2026-05-22", 1010.0, "yfinance"),
            ("2026-05-23", 1015.0, "yfinance"),
        ],
    )

    df = store.get_daily_bars(code, start_date="2026-05-20", end_date="2026-05-23")
    df = df.sort_values("date").reset_index(drop=True)

    boundary_return = (df.loc[2, "close"] - df.loc[1, "close"]) / df.loc[1, "close"] * 100
    assert abs(boundary_return) < 50.0, (
        f"moomoo/yfinance boundary discontinuity: {boundary_return:.1f}%"
    )


def test_large_gap_at_boundary_is_detected(tmp_path: Path) -> None:
    """10倍以上の不連続が境界に存在することを検出できる。"""
    store = _create_store(tmp_path)
    code = "JP.2559"

    _insert_daily_bars(
        store.db_path,
        code,
        [
            ("2024-12-30", 21860.0, "yfinance"),
            ("2025-01-06", 2196.5, "moomoo"),
        ],
    )

    df = store.get_daily_bars(code, start_date="2024-12-30", end_date="2025-01-06")
    df = df.sort_values("date").reset_index(drop=True)

    boundary_return = (df.loc[1, "close"] - df.loc[0, "close"]) / df.loc[0, "close"] * 100
    assert abs(boundary_return) > 80.0, (
        f"expected large boundary gap, got {boundary_return:.1f}%"
    )


def test_yfinance_auto_adjust_contract() -> None:
    """fetch_adjusted_history が auto_adjust=True で呼び出されることを確認する。"""
    from src.yfinance_data import fetch_adjusted_history

    captured: dict[str, object] = {}

    class FakeTicker:
        def history(self, **kwargs):
            captured.update(kwargs)
            return pd.DataFrame(
                {
                    "Open": [100.0],
                    "High": [102.0],
                    "Low": [99.0],
                    "Close": [101.0],
                    "Volume": [1000],
                    "Stock Splits": [0.0],
                },
                index=pd.to_datetime(["2022-01-04"]),
            )

    fetch_adjusted_history(
        "JP.7203",
        "2022-01-01",
        "2022-01-05",
        ticker_factory=lambda ticker: FakeTicker(),
    )

    assert captured["auto_adjust"] is True
    assert captured["actions"] is True
    assert captured["interval"] == "1d"


def test_upsert_yfinance_bars_does_not_overwrite_moomoo(tmp_path: Path) -> None:
    """upsert_yfinance_bars が moomoo 行を保持することを確認する。"""
    from src.yfinance_data import upsert_yfinance_bars

    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
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
            """
        )
        conn.executemany(
            """
            INSERT INTO daily_bars
                (code, date, open, high, low, close, volume, turnover, source, turnover_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("JP.7203", "2022-01-04", 100, 100, 100, 100, 100, 10000, "moomoo", "actual"),
                ("JP.7203", "2022-01-05", 100, 100, 100, 100, 100, 10000, "yfinance", "estimated"),
            ],
        )

    bars = pd.DataFrame(
        [
            {
                "time_key": "2022-01-04",
                "open": 200,
                "high": 201,
                "low": 199,
                "close": 200,
                "volume": 200,
                "turnover": 40000,
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
        ]
    )

    stats = upsert_yfinance_bars(db_path, "JP.7203", bars)
    assert stats.inserted == 0
    assert stats.updated == 1
    assert stats.preserved == 1

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, close, source FROM daily_bars ORDER BY date"
        ).fetchall()
    assert rows == [
        ("2022-01-04", 100.0, "moomoo"),
        ("2022-01-05", 20.0, "yfinance"),
    ]


def test_source_boundary_continuity_across_all_jp_codes() -> None:
    """本番 DB の全 JP yfinance 銘柄で境界が連続であることを確認する。"""
    db_path = Path("data/moomoo.db")
    if not db_path.exists():
        pytest.skip("本番DBが存在しません")

    conn = sqlite3.connect(str(db_path))
    codes = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT code FROM daily_bars
            WHERE source = 'moomoo'
              AND code IN (
                  SELECT code FROM daily_bars WHERE source = 'yfinance' AND code LIKE 'JP.%'
              )
            ORDER BY code
            """
        ).fetchall()
    ]

    discontinuous = []
    for code in codes:
        first_moomoo = conn.execute(
            "SELECT MIN(date), close FROM daily_bars WHERE code = ? AND source = 'moomoo'",
            (code,),
        ).fetchone()

        if not first_moomoo or not first_moomoo[0]:
            continue

        prev_y = conn.execute(
            """
            SELECT date, close FROM daily_bars
            WHERE code = ? AND source = 'yfinance' AND date < ?
            ORDER BY date DESC LIMIT 1
            """,
            (code, first_moomoo[0]),
        ).fetchone()

        if prev_y and prev_y[1] and first_moomoo[1]:
            ratio = first_moomoo[1] / prev_y[1]
            if abs(ratio) > 1.5 or abs(ratio) < 0.67:
                discontinuous.append(
                    {
                        "code": code,
                        "yfinance_date": prev_y[0],
                        "yfinance_close": prev_y[1],
                        "moomoo_date": first_moomoo[0],
                        "moomoo_close": first_moomoo[1],
                        "ratio": ratio,
                    }
                )

    conn.close()

    assert len(discontinuous) == 0, (
        f"moomoo/yfinance 境界で不連続な銘柄: {discontinuous}"
    )
