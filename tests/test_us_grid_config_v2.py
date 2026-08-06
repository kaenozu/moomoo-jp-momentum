from __future__ import annotations

import math

import pytest

from src.us_grid.config import (
    GridConfig,
    RiskLimits,
    UsGridConfigError,
    load_us_grid_config,
    validate_us_grid_config,
)


def _mapping() -> dict:
    return {
        "us_grid": {
            "enabled": False,
            "mode": "backtest",
            "market": "US",
            "symbols": ["US.SPY"],
            "capital_jpy": 100000,
            "grid": {
                "spacing_mode": "fixed_pct",
                "spacing_pct": 1.5,
                "atr_period": 14,
                "atr_multiplier": 1.0,
                "min_spacing_pct": 0.75,
                "max_spacing_pct": 4.0,
                "buy_levels": 1,
                "sell_levels": 1,
                "quantity_per_level": 1,
            },
            "regime_filter": {
                "enabled": False,
                "sma_long_period": 200,
                "sma_mid_period": 50,
                "adx_period": 14,
                "volatility_percentile_low": 20,
                "volatility_percentile_high": 80,
            },
            "risk": {
                "allow_short": False,
                "allow_margin": False,
                "allow_leveraged_etf": False,
                "allow_inverse_etf": False,
                "max_symbols": 1,
                "max_symbol_allocation_pct": 80,
                "max_total_deployed_pct": 80,
                "minimum_cash_reserve_pct": 20,
                "max_inventory_levels_per_symbol": 2,
                "max_open_orders_per_symbol": 2,
                "max_open_orders_total": 2,
                "max_orders_per_day": 2,
                "cooldown_days": 1,
            },
            "costs": {
                "commission_mode": "percentage",
                "commission_rate": 0.00132,
                "minimum_commission_usd": 0.01,
                "maximum_commission_usd": 22.0,
                "spread_bps": 5,
                "slippage_bps": 5,
                "sell_regulatory_fee_enabled": True,
                "fx_cost_bps": 0,
            },
        }
    }


def test_string_false_is_rejected_instead_of_becoming_true() -> None:
    config = _mapping()
    config["us_grid"]["enabled"] = "false"
    with pytest.raises(UsGridConfigError, match="YAML boolean"):
        load_us_grid_config(config)


def test_non_finite_numeric_input_is_rejected() -> None:
    grid = GridConfig(symbols=["US.SPY"], capital_jpy=math.nan)
    with pytest.raises(UsGridConfigError, match="finite"):
        validate_us_grid_config(grid)


def test_deployed_and_cash_reserve_cannot_exceed_total_equity() -> None:
    grid = GridConfig(
        symbols=["US.SPY"],
        risk=RiskLimits(
            max_symbols=1,
            max_total_deployed_pct=90,
            minimum_cash_reserve_pct=20,
        ),
    )
    with pytest.raises(UsGridConfigError, match="cannot exceed 100"):
        validate_us_grid_config(grid)


def test_unimplemented_fx_cost_is_rejected() -> None:
    config = _mapping()
    config["us_grid"]["costs"]["fx_cost_bps"] = 5
    with pytest.raises(UsGridConfigError, match="not implemented"):
        load_us_grid_config(config)


def test_research_universe_can_exceed_active_symbol_limit() -> None:
    config = _mapping()
    config["us_grid"]["symbols"] = ["US.SPY", "US.QQQ"]
    grid = load_us_grid_config(config)
    assert grid.symbols == ["US.SPY", "US.QQQ"]
    assert grid.risk.max_symbols == 1


def test_valid_mapping_loads() -> None:
    grid = load_us_grid_config(_mapping())
    assert grid.symbols == ["US.SPY"]
    assert grid.risk.minimum_cash_reserve_pct == 20
