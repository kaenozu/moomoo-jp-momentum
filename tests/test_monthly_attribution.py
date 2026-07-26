"""月別超過リターン寄与度分析の回帰テスト。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.monthly_attribution import (
    calculate_monthly_attribution,
    load_backtest_equity_curve,
    normalize_benchmark_code,
    resolve_backtest_run,
)


def _curve(
    dates: list[str],
    cash: list[float],
    equity: list[float],
    benchmark: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "cash": cash,
            "total_equity": equity,
            "benchmark_value": benchmark,
            "drawdown_pct": [0.0] * len(dates),
        }
    )


def test_attributes_half_cash_underperformance_to_cash_drag() -> None:
    result = calculate_monthly_attribution(
        _curve(
            ["2026-01-01", "2026-01-02"],
            [50.0, 50.0],
            [100.0, 105.0],
            [100.0, 110.0],
        )
    )

    month = result.monthly.iloc[0]
    assert month["strategy_return_pct"] == pytest.approx(5.0)
    assert month["benchmark_return_pct"] == pytest.approx(10.0)
    assert month["active_return_pct"] == pytest.approx(-5.0)
    assert month["cash_drag_pct"] == pytest.approx(-5.0)
    assert month["residual_effect_pct"] == pytest.approx(0.0, abs=1e-10)
    assert month["reconciliation_error_bps"] == pytest.approx(0.0, abs=1e-10)


def test_attributes_return_above_invested_benchmark_to_residual() -> None:
    result = calculate_monthly_attribution(
        _curve(
            ["2026-01-01", "2026-01-02"],
            [50.0, 50.0],
            [100.0, 108.0],
            [100.0, 110.0],
        )
    )

    month = result.monthly.iloc[0]
    assert month["active_return_pct"] == pytest.approx(-2.0)
    assert month["cash_drag_pct"] == pytest.approx(-5.0)
    assert month["residual_effect_pct"] == pytest.approx(3.0)
    assert (
        month["cash_drag_pct"] + month["residual_effect_pct"]
        == pytest.approx(month["active_return_pct"])
    )


def test_carino_linking_reconciles_each_month_exactly() -> None:
    result = calculate_monthly_attribution(
        _curve(
            [
                "2026-01-30",
                "2026-02-02",
                "2026-02-03",
                "2026-03-02",
                "2026-03-03",
            ],
            [40.0, 38.0, 35.0, 35.0, 30.0],
            [100.0, 102.0, 100.0, 103.0, 107.0],
            [100.0, 101.0, 103.0, 102.0, 106.0],
        )
    )

    assert list(result.monthly["month"]) == ["2026-01", "2026-02", "2026-03"]
    for _, month in result.monthly.iterrows():
        assert (
            month["cash_drag_pct"] + month["residual_effect_pct"]
            == pytest.approx(month["active_return_pct"], abs=1e-10)
        )
        assert month["reconciliation_error_bps"] == pytest.approx(
            0.0,
            abs=1e-8,
        )


def test_rejects_duplicate_dates_and_invalid_cash_weight() -> None:
    duplicate = _curve(
        ["2026-01-01", "2026-01-01"],
        [50.0, 50.0],
        [100.0, 101.0],
        [100.0, 101.0],
    )
    with pytest.raises(ValueError, match="重複"):
        calculate_monthly_attribution(duplicate)

    invalid_cash = _curve(
        ["2026-01-01"],
        [101.0],
        [100.0],
        [100.0],
    )
    with pytest.raises(ValueError, match="cash"):
        calculate_monthly_attribution(invalid_cash)


def test_normalizes_supported_benchmarks() -> None:
    assert normalize_benchmark_code("1306") == "1306"
    assert normalize_benchmark_code("jp.2559") == "2559"
    with pytest.raises(ValueError, match="未対応"):
        normalize_benchmark_code("TOPIX")


def test_loads_run_metadata_and_selected_benchmark(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                initial_cash REAL NOT NULL
            );
            CREATE TABLE backtest_equity_curve (
                run_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                cash REAL,
                total_equity REAL,
                benchmark_2559_value REAL,
                benchmark_1306_value REAL,
                drawdown_pct REAL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO backtest_runs
                (id, strategy_name, start_date, end_date, initial_cash)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "momentum", "2026-01-01", "2026-01-31", 100000.0),
                (2, "momentum", "2026-02-01", "2026-02-28", 100000.0),
            ],
        )
        conn.execute(
            """
            INSERT INTO backtest_equity_curve
                (run_id, date, cash, total_equity,
                 benchmark_2559_value, benchmark_1306_value, drawdown_pct)
            VALUES (2, '2026-02-02', 50000, 100000, 200, 100, 0)
            """
        )

    run = resolve_backtest_run(db_path, strategy_name="momentum")
    assert run.run_id == 2
    assert run.strategy_name == "momentum"

    curve = load_backtest_equity_curve(db_path, run.run_id, "JP.1306")
    assert curve.loc[0, "benchmark_value"] == pytest.approx(100.0)

    with pytest.raises(ValueError, match="どちらか一方"):
        resolve_backtest_run(
            db_path,
            run_id=2,
            strategy_name="momentum",
        )
