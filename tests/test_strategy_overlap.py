"""戦略重複度分析の回帰テスト。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.strategy_overlap import (
    calculate_strategy_overlap,
    load_backtest_equity,
    load_backtest_fills,
)


def _fills(rows: list[tuple[str, str, int, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["code", "side", "quantity", "filled_at"])


def _equity(values: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(values, columns=["date", "total_equity"])


def test_quantifies_daily_holdings_symbol_and_entry_overlap() -> None:
    result = calculate_strategy_overlap(
        _fills(
            [
                ("JP.X", "BUY", 1, "2026-01-01"),
                ("JP.Y", "BUY", 1, "2026-01-02"),
                ("JP.X", "SELL", 1, "2026-01-03"),
            ]
        ),
        _fills(
            [
                ("JP.X", "BUY", 1, "2026-01-01"),
                ("JP.Z", "BUY", 1, "2026-01-02"),
                ("JP.X", "SELL", 1, "2026-01-03"),
            ]
        ),
        _equity(
            [
                ("2026-01-01", 100.0),
                ("2026-01-02", 101.0),
                ("2026-01-03", 102.0),
            ]
        ),
        _equity(
            [
                ("2026-01-01", 100.0),
                ("2026-01-02", 102.0),
                ("2026-01-03", 104.0),
            ]
        ),
    )

    assert list(result.daily["common_positions"]) == [1, 1, 0]
    assert list(result.daily["holdings_jaccard_pct"]) == pytest.approx(
        [100.0, 100.0 / 3.0, 0.0]
    )

    summary = result.summary.iloc[0]
    assert summary["traded_symbols_a"] == 2
    assert summary["traded_symbols_b"] == 2
    assert summary["common_traded_symbols"] == 1
    assert summary["traded_symbol_jaccard_pct"] == pytest.approx(100.0 / 3.0)
    assert summary["exact_common_entry_events"] == 1
    assert summary["exact_entry_jaccard_pct"] == pytest.approx(100.0 / 3.0)
    assert summary["avg_daily_holdings_jaccard_pct"] == pytest.approx(
        (100.0 + 100.0 / 3.0) / 3.0
    )
    assert summary["avg_overlap_coefficient_pct"] == pytest.approx(50.0)


def test_distinguishes_symbol_overlap_from_exact_entry_overlap() -> None:
    result = calculate_strategy_overlap(
        _fills([("JP.X", "BUY", 1, "2026-01-01")]),
        _fills([("JP.X", "BUY", 1, "2026-01-02")]),
        _equity([("2026-01-01", 100.0), ("2026-01-02", 101.0)]),
        _equity([("2026-01-01", 100.0), ("2026-01-02", 101.0)]),
    )

    summary = result.summary.iloc[0]
    assert summary["traded_symbol_jaccard_pct"] == pytest.approx(100.0)
    assert summary["exact_common_entry_events"] == 0
    assert summary["exact_entry_jaccard_pct"] == pytest.approx(0.0)


def test_rejects_negative_reconstructed_holdings() -> None:
    with pytest.raises(ValueError, match="負の保有"):
        calculate_strategy_overlap(
            _fills([("JP.X", "SELL", 1, "2026-01-01")]),
            _fills([]),
            _equity([("2026-01-01", 100.0)]),
            _equity([("2026-01-01", 100.0)]),
        )


def test_reports_perfect_daily_return_correlation() -> None:
    result = calculate_strategy_overlap(
        _fills([]),
        _fills([]),
        _equity(
            [
                ("2026-01-01", 100.0),
                ("2026-01-02", 101.0),
                ("2026-01-03", 99.0),
                ("2026-01-04", 102.0),
            ]
        ),
        _equity(
            [
                ("2026-01-01", 200.0),
                ("2026-01-02", 202.0),
                ("2026-01-03", 198.0),
                ("2026-01-04", 204.0),
            ]
        ),
    )

    assert result.summary.loc[0, "daily_return_correlation"] == pytest.approx(1.0)


def test_loads_fills_and_equity_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE backtest_fills (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                filled_at TEXT NOT NULL,
                fill_mode TEXT
            );
            CREATE TABLE backtest_equity_curve (
                run_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                total_equity REAL NOT NULL
            );
            INSERT INTO backtest_fills
                (id, run_id, code, side, quantity, price, filled_at, fill_mode)
            VALUES (1, 7, 'JP.X', 'BUY', 2, 100, '2026-01-01', 'next_open');
            INSERT INTO backtest_equity_curve
                (run_id, date, total_equity)
            VALUES (7, '2026-01-01', 100000);
            """
        )

    fills = load_backtest_fills(db_path, 7)
    equity = load_backtest_equity(db_path, 7)

    assert fills.loc[0, "code"] == "JP.X"
    assert equity.loc[0, "total_equity"] == pytest.approx(100000.0)
