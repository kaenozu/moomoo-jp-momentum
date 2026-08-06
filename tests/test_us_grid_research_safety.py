from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.us_grid.accounting import CashPosition
from src.us_grid.backtest import GridBacktester
from src.us_grid.benchmark import buy_and_hold
from src.us_grid.config import CostModel, GridConfig, RiskLimits
from src.us_grid.data import UsDataBundle, attach_corporate_actions, load_or_fetch
from src.us_grid.data_v2 import PRICE_BASIS, UsDataPolicyError
from src.us_grid.fills import bar_from_dict
from src.us_grid.research_runtime import CanonicalGridBacktester


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


def _risk() -> RiskLimits:
    return RiskLimits(
        max_symbols=2,
        max_symbol_allocation_pct=100.0,
        max_total_deployed_pct=100.0,
        minimum_cash_reserve_pct=0.0,
        max_inventory_levels_per_symbol=2,
        max_open_orders_per_symbol=4,
        max_open_orders_total=4,
        max_orders_per_day=4,
    )


def _grid(*, capital_jpy: float = 15000.0, symbols: list[str] | None = None) -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        symbols=symbols or ["US.SPY", "US.QQQ"],
        capital_jpy=capital_jpy,
        spacing_mode="fixed_pct",
        spacing_pct=2.0,
        buy_levels=1,
        sell_levels=1,
        quantity_per_level=1,
        risk=_risk(),
        costs=CostModel(
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
            spread_bps=5.0,
            slippage_bps=5.0,
        ),
    )


def test_public_backtester_import_is_canonical_runtime() -> None:
    assert GridBacktester is CanonicalGridBacktester


def test_legacy_price_cache_is_rejected(tmp_path: Path) -> None:
    pd.DataFrame(_bars()).to_csv(tmp_path / "SPY.csv", index=False)
    with pytest.raises(UsDataPolicyError, match="price_basis"):
        load_or_fetch(
            ["US.SPY"],
            "2026-01-05",
            "2026-01-06",
            tmp_path,
            fetch=False,
        )


def test_canonical_price_cache_loads_without_network(tmp_path: Path) -> None:
    frame = pd.DataFrame(_bars())
    frame["price_basis"] = PRICE_BASIS
    frame.to_csv(tmp_path / "SPY.csv", index=False)
    pd.DataFrame(
        [
            {"date": "2026-01-05", "rate": 150.0},
            {"date": "2026-01-06", "rate": 150.0},
        ]
    ).to_csv(tmp_path / "USDJPY.csv", index=False)

    bundle = load_or_fetch(
        ["US.SPY"],
        "2026-01-05",
        "2026-01-06",
        tmp_path,
        fetch=False,
    )
    assert bundle.bars["US.SPY"][0]["close"] == 100.0
    assert bundle.sources == ["cache-v2:SPY", "cache:USDJPY"]


def test_reservations_are_portfolio_wide_and_include_execution_costs() -> None:
    data = {"US.SPY": _bars(), "US.QQQ": _bars()}
    fx = [
        {"date": "2026-01-05", "rate": 150.0},
        {"date": "2026-01-06", "rate": 150.0},
    ]
    backtester = GridBacktester(_grid(), data, fx)
    backtester.run("2026-01-05", "2026-01-06")

    active_buys = [
        order for order in backtester.orders if order.active and order.side == "BUY"
    ]
    assert len(active_buys) == 1
    assert sum(backtester._reserved_by_code.values()) <= 100.0
    assert backtester.orders_rejected >= 1

    exact_nominal = GridBacktester(_grid(capital_jpy=14700.0), data, fx)
    exact_nominal.run("2026-01-05", "2026-01-06")
    assert not any(
        order.active and order.side == "BUY" for order in exact_nominal.orders
    )


def test_dividend_is_credited_once_and_split_is_not_double_applied() -> None:
    bundle = UsDataBundle(
        bars={"US.SPY": _bars()},
        dividends={
            "US.SPY": [
                {"date": "2026-01-06", "per_share": 1.0},
            ]
        },
        splits={
            "US.SPY": [
                {"date": "2026-01-06", "ratio": 2.0},
            ]
        },
        fx=[
            {"date": "2026-01-05", "rate": 150.0},
            {"date": "2026-01-06", "rate": 150.0},
        ],
    )
    actions = attach_corporate_actions(bundle)
    assert actions == {
        "US.SPY": [
            {"date": "2026-01-06", "kind": "dividend", "per_share": 1.0}
        ]
    }

    grid = GridConfig(
        enabled=True,
        mode="backtest",
        symbols=["US.SPY"],
        capital_jpy=150000.0,
        core_allocation_pct=100.0,
        buy_levels=0,
        sell_levels=0,
        risk=_risk(),
        costs=CostModel(
            commission_rate=0.0,
            minimum_commission_usd=0.0,
            spread_bps=0.0,
            slippage_bps=0.0,
            sell_regulatory_fee_enabled=False,
        ),
    )
    backtester = GridBacktester(grid, bundle.bars, bundle.fx)
    result = backtester.run("2026-01-05", "2026-01-06")
    assert result.dividend_income_usd == pytest.approx(10.0)
    assert backtester._core_positions["US.SPY"] == 10

    bars_by_code = {
        "US.SPY": [bar_from_dict(row) for row in bundle.bars["US.SPY"]]
    }
    benchmark = buy_and_hold(
        grid,
        bars_by_code,
        {row["date"]: float(row["rate"]) for row in bundle.fx},
        "2026-01-05",
        "2026-01-06",
        ["2026-01-05", "2026-01-06"],
    )
    assert result.total_return_pct_usd == pytest.approx(1.0)
    assert benchmark.total_return_pct_usd == pytest.approx(1.0)


def test_run_id_changes_with_input_data() -> None:
    grid = _grid(symbols=["US.SPY"])
    fx = [
        {"date": "2026-01-05", "rate": 150.0},
        {"date": "2026-01-06", "rate": 150.0},
    ]
    first = GridBacktester(grid, {"US.SPY": _bars()}, fx).run(
        "2026-01-05",
        "2026-01-06",
    )
    changed = _bars()
    changed[-1] = {**changed[-1], "close": 101.0}
    second = GridBacktester(grid, {"US.SPY": changed}, fx).run(
        "2026-01-05",
        "2026-01-06",
    )
    assert first.run_id != second.run_id
