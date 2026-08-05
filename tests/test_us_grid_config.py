"""Unit tests for US grid config validation."""

from __future__ import annotations

import pytest

from src.us_grid.config import (
    GridConfig,
    UsGridConfigError,
    load_us_grid_config,
    validate_us_grid_config,
)


def _base_config() -> dict:
    return {
        "us_grid": {
            "enabled": True,
            "mode": "backtest",
            "strategy_name": "us_fixed_grid_v1",
            "market": "US",
            "symbols": ["US.SPY", "US.QQQ"],
            "capital_jpy": 100000.0,
            "grid": {
                "center_mode": "initial_close",
                "spacing_mode": "fixed_pct",
                "spacing_pct": 1.5,
                "buy_levels": 3,
                "sell_levels": 3,
                "quantity_per_level": 1,
            },
        }
    }


def test_load_minimal_config() -> None:
    grid = load_us_grid_config(_base_config())
    assert grid.enabled is True
    assert grid.market == "US"
    assert grid.spacing_mode == "fixed_pct"
    assert grid.symbols == ["US.SPY", "US.QQQ"]


def test_default_disabled() -> None:
    grid = load_us_grid_config({"us_grid": {"enabled": False}})
    assert grid.enabled is False
    assert grid.strategy_name == "us_adaptive_grid_v1"


def test_market_must_be_us() -> None:
    cfg = _base_config()
    cfg["us_grid"]["market"] = "JP"
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_negative_capital_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["capital_jpy"] = -100
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_invalid_spacing_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["grid"]["spacing_pct"] = -1.0
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_min_greater_than_max_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["grid"]["spacing_mode"] = "atr_pct"
    cfg["us_grid"]["grid"]["min_spacing_pct"] = 5.0
    cfg["us_grid"]["grid"]["max_spacing_pct"] = 2.0
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_excessive_levels_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["grid"]["buy_levels"] = 15
    cfg["us_grid"]["grid"]["sell_levels"] = 15
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_leveraged_etf_rejected_by_default() -> None:
    cfg = _base_config()
    cfg["us_grid"]["symbols"] = ["US.TQQQ"]
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_inverse_etf_rejected_by_default() -> None:
    cfg = _base_config()
    cfg["us_grid"]["symbols"] = ["US.SQQQ"]
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_leveraged_etf_allowed_when_enabled() -> None:
    cfg = _base_config()
    cfg["us_grid"]["symbols"] = ["US.TQQQ"]
    cfg["us_grid"]["risk"] = {"allow_leveraged_etf": True}
    grid = load_us_grid_config(cfg)
    assert grid.symbols == ["US.TQQQ"]


def test_non_us_symbol_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["symbols"] = ["JP.1306"]
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_duplicate_symbols_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["symbols"] = ["US.SPY", "US.SPY"]
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_invalid_mode_rejected() -> None:
    cfg = _base_config()
    cfg["us_grid"]["mode"] = "real"
    with pytest.raises(UsGridConfigError):
        load_us_grid_config(cfg)


def test_validate_ok() -> None:
    grid = GridConfig(symbols=["US.SPY"], market="US")
    validate_us_grid_config(grid)  # should not raise
