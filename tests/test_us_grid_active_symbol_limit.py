from __future__ import annotations

from src.us_grid.backtest import GridBacktester
from src.us_grid.config import CostModel, GridConfig, RiskLimits


def _bars() -> list[dict]:
    return [
        {
            "date": "2026-01-05",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        },
        {
            "date": "2026-01-06",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        },
    ]


def test_max_symbols_limits_active_orders_not_research_universe() -> None:
    grid = GridConfig(
        enabled=True,
        mode="backtest",
        symbols=["US.SPY", "US.QQQ"],
        capital_jpy=30000.0,
        spacing_mode="fixed_pct",
        spacing_pct=2.0,
        buy_levels=1,
        sell_levels=1,
        quantity_per_level=1,
        risk=RiskLimits(
            max_symbols=1,
            max_symbol_allocation_pct=100.0,
            max_total_deployed_pct=100.0,
            minimum_cash_reserve_pct=0.0,
            max_inventory_levels_per_symbol=2,
            max_open_orders_per_symbol=4,
            max_open_orders_total=4,
            max_orders_per_day=4,
        ),
        costs=CostModel(
            commission_rate=0.0,
            minimum_commission_usd=0.0,
            spread_bps=0.0,
            slippage_bps=0.0,
            sell_regulatory_fee_enabled=False,
        ),
    )
    fx = [
        {"date": "2026-01-05", "rate": 150.0},
        {"date": "2026-01-06", "rate": 150.0},
    ]
    backtester = GridBacktester(
        grid,
        {"US.SPY": _bars(), "US.QQQ": _bars()},
        fx,
    )
    backtester.run("2026-01-05", "2026-01-06")

    active_symbols = {
        order.code
        for order in backtester.orders
        if order.active and order.side == "BUY"
    }
    assert len(active_symbols) == 1
    assert backtester.orders_rejected >= 1
