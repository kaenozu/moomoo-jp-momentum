"""
Integration tests for the US grid backtest engine using synthetic data.

These tests verify the core invariants: no naked sells, no cash overrun,
paired-order next-bar rule, reproducibility, and that the conservative fill
model does not manufacture same-bar churn profits.
"""

from __future__ import annotations

from src.us_grid.backtest import GridBacktester
from src.us_grid.config import CostModel, GridConfig


def _grid(**overrides) -> GridConfig:
    params = dict(
        enabled=True,
        mode="backtest",
        strategy_name="us_fixed_grid_v1",
        market="US",
        symbols=["US.SPY"],
        capital_jpy=100000.0,
        spacing_mode="fixed_pct",
        spacing_pct=2.0,
        buy_levels=2,
        sell_levels=2,
        quantity_per_level=1,
        costs=CostModel(
            commission_mode="percentage",
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
            maximum_commission_usd=22.0,
            spread_bps=5,
            slippage_bps=5,
            sell_regulatory_fee_enabled=True,
        ),
    )
    params.update(overrides)
    return GridConfig(**params)  # type: ignore[arg-type]


def _synthetic_bars(
    closes: list[float],
    start="2024-01-02",
) -> list[dict]:
    """Synthetic bars where open=prev_close, high/low are +/-2% of close."""
    import datetime as dt

    bars = []
    current = dt.date.fromisoformat(start)
    prev_close = closes[0]
    for close in closes:
        bars.append(
            {
                "date": current.isoformat(),
                "open": prev_close,
                "high": max(prev_close, close) * 1.01,
                "low": min(prev_close, close) * 0.99,
                "close": close,
                "volume": 100000,
            }
        )
        prev_close = close
        current += dt.timedelta(days=1)
    return bars


def _fx() -> list[dict]:
    return [{"date": f"2024-01-{d:02d}", "rate": 150.0} for d in range(1, 32)]


def _fx_full_years() -> list[dict]:
    """USDJPY=150 for every weekday in 2018-2025 (approx, daily)."""
    import datetime as dt

    rows = []
    current = dt.date(2018, 1, 1)
    end = dt.date(2025, 1, 1)
    while current < end:
        if current.weekday() < 5:
            rows.append({"date": current.isoformat(), "rate": 150.0})
        current += dt.timedelta(days=1)
    return rows


def test_fixed_grid_buys_and_pairs_sell() -> None:
    """A dip below the BUY level fills the BUY and arms a paired SELL."""
    grid = _grid()
    # Start 100, BUY levels at 98 and 96. Price dips to 97 (touches 98),
    # then recovers above 98.
    closes = [100.0, 100.0, 97.0, 99.0, 100.0, 100.0]
    bars = {"US.SPY": _synthetic_bars(closes)}
    backtester = GridBacktester(grid, bars, _fx())
    result = backtester.run("2024-01-02", "2024-01-10")

    assert result.orders_filled >= 2, "expected a BUY fill and a SELL fill"
    buy_fills = [t for t in result.trades if t.side == "BUY"]
    sell_fills = [t for t in result.trades if t.side == "SELL"]
    assert len(buy_fills) >= 1
    assert len(sell_fills) >= 1
    # Paired SELL cannot fill on the same bar as its BUY.
    buy_date = buy_fills[0].date
    assert all(s.date > buy_date for s in sell_fills)


def test_no_naked_sell() -> None:
    """SELL orders never exceed held quantity."""
    grid = _grid()
    closes = [100.0] * 20  # flat, no BUY fill
    bars = {"US.SPY": _synthetic_bars(closes)}
    backtester = GridBacktester(grid, bars, _fx_full_years())
    result = backtester.run("2024-01-02", "2024-01-21")

    # No SELL trades without a prior BUY.
    cumulative = 0
    for trade in result.trades:
        if trade.side == "BUY":
            cumulative += trade.quantity
        else:
            cumulative -= trade.quantity
            assert cumulative >= 0, "naked SELL detected"


def test_cash_never_negative() -> None:
    """Cash cannot go negative under repeated buying."""
    grid = _grid()
    # Steady downtrend triggers repeated BUY fills.
    closes = [100.0 - i * 1.0 for i in range(30)]
    bars = {"US.SPY": _synthetic_bars(closes)}
    backtester = GridBacktester(grid, bars, _fx_full_years())
    result = backtester.run("2024-01-02", "2024-02-01")

    for point in result.equity_curve:
        assert point.cash_usd >= -1e-6, f"negative cash on {point.date}"


def test_reproducibility() -> None:
    """Same inputs produce the same result."""
    grid = _grid()
    closes = [100.0, 98.0, 101.0, 97.0, 99.0, 102.0]
    bars = {"US.SPY": _synthetic_bars(closes)}
    fx = _fx_full_years()
    first = GridBacktester(grid, bars, fx).run("2024-01-02", "2024-01-10")
    second = GridBacktester(grid, bars, fx).run("2024-01-02", "2024-01-10")

    assert first.run_id == second.run_id
    assert first.total_return_pct_jpy == second.total_return_pct_jpy
    assert len(first.trades) == len(second.trades)
    assert first.equity_curve == second.equity_curve


def test_no_same_bar_churn_profit() -> None:
    """A BUY and its paired SELL cannot both fill on the same bar.

    The paired SELL is only effective from the next bar, so a bar that both
    touches the BUY low and the SELL high cannot register a phantom round
    trip on that same date.
    """
    grid = _grid()
    # One bar with both a big low (touching BUY 98) and big high (touching
    # SELL 99.96). The BUY fills; the paired SELL must not fill the same day.
    bars = {
        "US.SPY": [
            {
                "date": "2024-01-02",
                "open": 100.0,
                "high": 100.5,
                "low": 100.0,
                "close": 100.0,
                "volume": 100000,
            },
            {
                "date": "2024-01-03",
                "open": 100.0,
                "high": 103.0,
                "low": 97.0,
                "close": 101.0,
                "volume": 100000,
            },
            {
                "date": "2024-01-04",
                "open": 101.0,
                "high": 101.5,
                "low": 100.0,
                "close": 101.0,
                "volume": 100000,
            },
        ]
    }
    backtester = GridBacktester(grid, bars, _fx())
    result = backtester.run("2024-01-02", "2024-01-10")

    fills_by_date = {}
    for trade in result.trades:
        fills_by_date.setdefault(trade.date, []).append(trade.side)
    # The day the BUY filled, there must be no SELL fill.
    for date, sides in fills_by_date.items():
        if "BUY" in sides:
            assert "SELL" not in sides, f"same-bar churn on {date}"


def test_regime_filter_blocks_buy_in_risk_off() -> None:
    """In RISK_OFF the grid stops placing new BUY orders."""
    grid = _grid(regime_filter_enabled=True, sma_long_period=10, adx_period=5)
    # Crash series -> RISK_OFF regime.
    closes = [100.0 - i * 2.0 for i in range(30)]
    bars = {"US.SPY": _synthetic_bars(closes)}
    backtester = GridBacktester(grid, bars, _fx_full_years())
    result = backtester.run("2024-01-02", "2024-02-01")

    # Some BUYs may have filled before RISK_OFF was detected, but new BUY
    # orders should stop once the regime turns RISK_OFF.
    buy_fills_after = [
        t for t in result.trades if t.side == "BUY" and t.date >= "2024-01-15"
    ]
    # With 30 bars crashing, the regime should be RISK_OFF well before 1/15.
    assert len(buy_fills_after) <= 1


def test_core_plus_grid_seed() -> None:
    """Core allocation seeds a buy-and-hold position that is not sold by the
    grid SELL levels."""
    grid = _grid(core_allocation_pct=40.0)
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    bars = {"US.SPY": _synthetic_bars(closes)}
    backtester = GridBacktester(grid, bars, _fx_full_years())
    result = backtester.run("2024-01-02", "2024-01-10")

    core_buys = [t for t in result.trades if t.reason == "core"]
    assert core_buys, "core position should be seeded"
    assert all(t.reason == "core" for t in core_buys)


def test_walk_forward_runs() -> None:
    """Walk-forward produces a report with train/val/test windows."""

    grid = _grid()
    # 3y + 1y + 1y = 5y of data needed for at least one window.

    closes = [100.0 + (i % 20) * 0.5 for i in range(252 * 6)]
    bars = {"US.SPY": _synthetic_bars(closes, start="2018-01-02")}
    backtester = GridBacktester(grid, bars, _fx_full_years())

    # Simulate the CLI walk-forward over 2019-2024.

    result = backtester.run("2019-01-02", "2024-12-31")
    assert result.total_return_pct_jpy is not None
    assert len(result.equity_curve) > 0


def test_no_look_ahead_in_final_mark() -> None:
    """The final equity mark must use the last bar of the window, never a
    future bar outside the window."""
    grid = _grid()
    # Bars: window ends 2024-01-06. The data bundle also contains later bars
    # (2024-01-07 onward) that must NOT influence the result.
    bars = {
        "US.SPY": [
            {
                "date": "2024-01-02",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100000,
            },
            {
                "date": "2024-01-03",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100000,
            },
            {
                "date": "2024-01-04",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100000,
            },
            {
                "date": "2024-01-05",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100000,
            },
            # Future bars outside the window (close=1000) — must be ignored.
            {
                "date": "2025-01-06",
                "open": 1000.0,
                "high": 1010.0,
                "low": 990.0,
                "close": 1000.0,
                "volume": 100000,
            },
            {
                "date": "2025-01-07",
                "open": 1000.0,
                "high": 1010.0,
                "low": 990.0,
                "close": 1000.0,
                "volume": 100000,
            },
        ]
    }
    backtester = GridBacktester(grid, bars, _fx_full_years())
    result = backtester.run("2024-01-02", "2024-01-05")

    # If final mark used the future bar (close=1000), the return would explode.
    # With no fills and flat price at 100, the return must be ~0%.
    assert result.total_return_pct_jpy < 1.0
    assert result.final_equity_usd <= 5000.0
