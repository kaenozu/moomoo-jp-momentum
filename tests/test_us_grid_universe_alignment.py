from __future__ import annotations

import pytest

from src.us_grid.backtest import GridBacktester
from src.us_grid.benchmark import buy_and_hold
from src.us_grid.config import CostModel, GridConfig, RiskLimits, UsGridConfigError
from src.us_grid.data import UsDataBundle, attach_corporate_actions
from src.us_grid.fills import bar_from_dict


def _bars(close: float = 100.0) -> list[dict]:
    return [
        {
            "date": "2026-01-05",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
        },
        {
            "date": "2026-01-06",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
        },
    ]


def _grid() -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        symbols=["US.SPY"],
        capital_jpy=150000.0,
        buy_levels=0,
        sell_levels=0,
        core_allocation_pct=100.0,
        risk=RiskLimits(
            max_symbols=1,
            max_symbol_allocation_pct=100.0,
            max_total_deployed_pct=100.0,
            minimum_cash_reserve_pct=0.0,
            max_inventory_levels_per_symbol=1,
            max_open_orders_per_symbol=1,
            max_open_orders_total=1,
            max_orders_per_day=1,
        ),
        costs=CostModel(
            commission_rate=0.0,
            minimum_commission_usd=0.0,
            spread_bps=0.0,
            slippage_bps=0.0,
            sell_regulatory_fee_enabled=False,
        ),
    )


def test_strategy_ignores_data_outside_configured_universe() -> None:
    engine = GridBacktester(
        _grid(),
        {"US.SPY": _bars(), "US.QQQ": _bars(1000.0)},
        [
            {"date": "2026-01-05", "rate": 150.0},
            {"date": "2026-01-06", "rate": 150.0},
        ],
    )
    result = engine.run("2026-01-05", "2026-01-06")
    assert result.symbols == ["US.SPY"]


def test_benchmark_uses_same_configured_universe() -> None:
    grid = _grid()
    attach_corporate_actions(UsDataBundle(bars={"US.SPY": _bars()}))
    bars = {
        "US.SPY": [bar_from_dict(row) for row in _bars()],
        "US.QQQ": [bar_from_dict(row) for row in _bars(1000.0)],
    }
    result = buy_and_hold(
        grid,
        bars,
        {"2026-01-05": 150.0, "2026-01-06": 150.0},
        "2026-01-05",
        "2026-01-06",
        ["2026-01-05", "2026-01-06"],
    )
    assert result.total_return_pct_usd == pytest.approx(0.0)


def test_missing_configured_symbol_is_rejected_for_strategy_and_benchmark() -> None:
    grid = _grid()
    with pytest.raises(UsGridConfigError, match="missing configured symbols"):
        GridBacktester(grid, {}, [])

    with pytest.raises(UsGridConfigError, match="missing configured symbols"):
        buy_and_hold(
            grid,
            {},
            {"2026-01-05": 150.0},
            "2026-01-05",
            "2026-01-05",
            ["2026-01-05"],
        )
