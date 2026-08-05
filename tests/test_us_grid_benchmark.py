"""Unit tests for benchmark models (Buy & Hold look-ahead safety)."""

from __future__ import annotations

from src.us_grid.benchmark import buy_and_hold, cash_benchmark
from src.us_grid.config import CostModel, GridConfig
from src.us_grid.fills import Bar


def _grid() -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name="us_fixed_grid_v1",
        market="US",
        symbols=["US.SPY"],
        capital_jpy=300000.0,
        spacing_mode="fixed_pct",
        spacing_pct=1.5,
        costs=CostModel(
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
            maximum_commission_usd=22.0,
            spread_bps=5,
            slippage_bps=5,
            sell_regulatory_fee_enabled=True,
        ),
    )


def test_buy_and_hold_no_look_ahead() -> None:
    """Buy & Hold final mark must use the last bar of the window, not a
    future bar from the full data bundle."""
    grid = _grid()
    bars = {
        "US.SPY": [
            Bar(date="2024-01-02", open=100.0, high=101.0, low=99.0, close=100.0),
            Bar(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0),
            Bar(date="2024-01-04", open=100.0, high=101.0, low=99.0, close=100.0),
            # Future bar outside the window — must be ignored.
            Bar(date="2025-01-06", open=1000.0, high=1010.0, low=990.0, close=1000.0),
        ]
    }
    calendar = ["2024-01-02", "2024-01-03", "2024-01-04"]
    fx = {day: 150.0 for day in calendar}
    result = buy_and_hold(grid, bars, fx, "2024-01-02", "2024-01-04", calendar)

    # Flat price at 100 within the window -> return ~0%, not the +900% that
    # using the future bar (close=1000) would produce.
    assert result.total_return_pct_jpy < 5.0
    assert result.total_return_pct_usd < 5.0


def test_buy_and_hold_positive_when_price_rises() -> None:
    grid = _grid()
    bars = {
        "US.SPY": [
            Bar(date="2024-01-02", open=100.0, high=101.0, low=99.0, close=100.0),
            Bar(date="2024-01-03", open=101.0, high=102.0, low=100.0, close=102.0),
        ]
    }
    calendar = ["2024-01-02", "2024-01-03"]
    fx = {day: 150.0 for day in calendar}
    result = buy_and_hold(grid, bars, fx, "2024-01-02", "2024-01-03", calendar)
    assert result.total_return_pct_usd > 1.0


def test_cash_benchmark_flat() -> None:
    grid = _grid()
    result = cash_benchmark(grid, ["2024-01-02", "2024-01-03"])
    assert result.total_return_pct_jpy == 0.0
    assert result.total_return_pct_usd == 0.0
    assert result.max_drawdown_pct == 0.0
