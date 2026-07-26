import sqlite3
from pathlib import Path

import pytest

from src.strategy_overlap import (
    EquityPoint,
    FillEvent,
    calculate_strategy_overlap,
    load_equity_points,
    load_fill_events,
)


def test_calculate_strategy_overlap_reports_entries_holdings_and_returns() -> None:
    equity_a = [
        EquityPoint("2026-01-01", 100.0),
        EquityPoint("2026-01-02", 110.0),
        EquityPoint("2026-01-03", 100.0),
        EquityPoint("2026-01-04", 110.0),
    ]
    equity_b = [
        EquityPoint("2026-01-01", 100.0),
        EquityPoint("2026-01-02", 105.0),
        EquityPoint("2026-01-03", 100.0),
        EquityPoint("2026-01-04", 110.0),
    ]
    fills_a = [
        FillEvent("2026-01-02", "JP.1111", "BUY", 1, "2026-01-01"),
        FillEvent("2026-01-03", "JP.2222", "BUY", 1, "2026-01-02"),
    ]
    fills_b = [
        FillEvent("2026-01-02", "JP.1111", "BUY", 1, "2026-01-01"),
        FillEvent("2026-01-03", "JP.3333", "BUY", 1, "2026-01-02"),
    ]

    result = calculate_strategy_overlap(equity_a, equity_b, fills_a, fills_b)
    summary = result.summary

    assert summary.overlap_start_date == "2026-01-01"
    assert summary.overlap_end_date == "2026-01-04"
    assert summary.aligned_return_days == 3
    assert summary.strategy_a_return_pct == pytest.approx(10.0)
    assert summary.strategy_b_return_pct == pytest.approx(10.0)
    assert summary.combined_50_50_return_pct == pytest.approx(10.0595238095)
    assert summary.daily_return_correlation == pytest.approx(0.9429324947)
    assert summary.same_direction_days_pct == pytest.approx(100.0)
    assert summary.negative_day_jaccard_pct == pytest.approx(100.0)
    assert summary.exact_entry_jaccard_pct == pytest.approx(100 / 3)
    assert summary.code_month_entry_jaccard_pct == pytest.approx(100 / 3)
    assert summary.symbol_jaccard_pct == pytest.approx(100 / 3)
    assert summary.avg_holdings_jaccard_pct == pytest.approx(
        (1 + 1 / 3 + 1 / 3) / 3 * 100
    )
    assert summary.avg_holdings_overlap_coefficient_pct == pytest.approx(
        (1 + 1 / 2 + 1 / 2) / 3 * 100
    )
    assert summary.exact_entry_overlap_count == 1
    assert summary.symbol_overlap_count == 1
    assert len(result.symbol_rows) == 3
    assert len(result.entry_rows) == 3


def test_calculate_strategy_overlap_aligns_on_shared_observations() -> None:
    result = calculate_strategy_overlap(
        [
            EquityPoint("2026-01-01", 100),
            EquityPoint("2026-01-02", 110),
            EquityPoint("2026-01-03", 121),
        ],
        [
            EquityPoint("2026-01-01", 100),
            EquityPoint("2026-01-03", 110),
        ],
        [],
        [],
    )

    assert result.summary.aligned_return_days == 1
    assert result.summary.strategy_a_return_pct == pytest.approx(21.0)
    assert result.summary.strategy_b_return_pct == pytest.approx(10.0)
    assert result.summary.daily_return_correlation is None


def test_entry_overlap_excludes_buys_before_common_period() -> None:
    result = calculate_strategy_overlap(
        [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
        [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 102)],
        [FillEvent("2025-12-30", "JP.1111", "BUY", 1, "2025-12-29")],
        [],
    )

    assert result.summary.strategy_a_buy_entries == 0
    assert result.summary.symbol_jaccard_pct is None
    assert result.daily_rows[0]["strategy_a_holdings"] == 1


def test_calculate_strategy_overlap_rejects_negative_inventory() -> None:
    with pytest.raises(ValueError, match="negative reconstructed holdings"):
        calculate_strategy_overlap(
            [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
            [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
            [FillEvent("2026-01-01", "JP.1111", "SELL", 1, "2026-01-01")],
            [],
        )


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (
            FillEvent("2026-01-01", "JP.1111", "HOLD", 1, "2026-01-01"),
            "unsupported side",
        ),
        (
            FillEvent("2026-01-01", "JP.1111", "BUY", 0, "2026-01-01"),
            "quantity must be positive",
        ),
    ],
)
def test_calculate_strategy_overlap_validates_fills(
    event: FillEvent,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_strategy_overlap(
            [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
            [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
            [event],
            [],
        )


def test_calculate_strategy_overlap_rejects_non_overlapping_curves() -> None:
    with pytest.raises(ValueError, match="overlapping return dates"):
        calculate_strategy_overlap(
            [EquityPoint("2026-01-01", 100), EquityPoint("2026-01-02", 101)],
            [EquityPoint("2026-02-01", 100), EquityPoint("2026-02-02", 101)],
            [],
            [],
        )


def test_loaders_read_persisted_backtest_data(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE backtest_equity_curve (
                run_id INTEGER,
                date TEXT,
                total_equity REAL
            );
            CREATE TABLE backtest_orders (
                id INTEGER PRIMARY KEY,
                signal_date TEXT
            );
            CREATE TABLE backtest_fills (
                id INTEGER PRIMARY KEY,
                run_id INTEGER,
                order_id INTEGER,
                code TEXT,
                side TEXT,
                quantity INTEGER,
                filled_at TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO backtest_equity_curve VALUES (?,?,?)",
            [(1, "2026-01-01", 100), (1, "2026-01-02", 101)],
        )
        conn.execute("INSERT INTO backtest_orders VALUES (1, '2026-01-01')")
        conn.execute(
            "INSERT INTO backtest_fills VALUES (1,1,1,'JP.1111','BUY',2,'2026-01-02')"
        )

    assert load_equity_points(db_path, 1) == [
        EquityPoint("2026-01-01", 100.0),
        EquityPoint("2026-01-02", 101.0),
    ]
    assert load_fill_events(db_path, 1) == [
        FillEvent("2026-01-02", "JP.1111", "BUY", 2, "2026-01-01")
    ]
