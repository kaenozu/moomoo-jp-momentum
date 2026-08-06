"""Strict, finite, fail-closed validation for US-grid research config."""

from __future__ import annotations

import math
from typing import Any

from . import config as _legacy
from .config import GridConfig, UsGridConfigError

_ORIGINAL_LOAD = _legacy.load_us_grid_config
_ORIGINAL_VALIDATE = _legacy.validate_us_grid_config


def _require_bool(section: dict[str, Any], key: str, path: str) -> None:
    if key in section and type(section[key]) is not bool:
        raise UsGridConfigError(f"{path}.{key} must be a YAML boolean")


def _prevalidate_boolean_types(config: Any) -> None:
    section = _legacy._section(config, "us_grid")
    if not isinstance(section, dict):
        return
    _require_bool(section, "enabled", "us_grid")
    regime = section.get("regime_filter", {})
    risk = section.get("risk", {})
    costs = section.get("costs", {})
    if isinstance(regime, dict):
        _require_bool(regime, "enabled", "us_grid.regime_filter")
    if isinstance(risk, dict):
        for key in (
            "allow_short",
            "allow_margin",
            "allow_leveraged_etf",
            "allow_inverse_etf",
        ):
            _require_bool(risk, key, "us_grid.risk")
    if isinstance(costs, dict):
        _require_bool(
            costs,
            "sell_regulatory_fee_enabled",
            "us_grid.costs",
        )


def _finite(value: float, label: str) -> None:
    if not math.isfinite(value):
        raise UsGridConfigError(f"{label} must be finite")


def _finite_nonnegative(value: float, label: str) -> None:
    _finite(value, label)
    if value < 0:
        raise UsGridConfigError(f"{label} cannot be negative")


def validate_us_grid_config(grid: GridConfig) -> None:
    _ORIGINAL_VALIDATE(grid)

    numeric_values = {
        "capital_jpy": grid.capital_jpy,
        "spacing_pct": grid.spacing_pct,
        "atr_multiplier": grid.atr_multiplier,
        "min_spacing_pct": grid.min_spacing_pct,
        "max_spacing_pct": grid.max_spacing_pct,
        "core_allocation_pct": grid.core_allocation_pct,
        "adx_trend_threshold": grid.adx_trend_threshold,
        "volatility_percentile_low": grid.volatility_percentile_low,
        "volatility_percentile_high": grid.volatility_percentile_high,
        "recenter_min_distance_atr": grid.recenter_min_distance_atr,
        "max_symbol_allocation_pct": grid.risk.max_symbol_allocation_pct,
        "max_total_deployed_pct": grid.risk.max_total_deployed_pct,
        "minimum_cash_reserve_pct": grid.risk.minimum_cash_reserve_pct,
        "daily_loss_limit_pct": grid.risk.daily_loss_limit_pct,
        "strategy_drawdown_limit_pct": grid.risk.strategy_drawdown_limit_pct,
        "portfolio_drawdown_limit_pct": grid.risk.portfolio_drawdown_limit_pct,
        "gap_stop_pct": grid.risk.gap_stop_pct,
        "stale_quote_seconds": grid.risk.stale_quote_seconds,
        "max_reconcile_age_seconds": grid.risk.max_reconcile_age_seconds,
        "commission_rate": grid.costs.commission_rate,
        "minimum_commission_usd": grid.costs.minimum_commission_usd,
        "maximum_commission_usd": grid.costs.maximum_commission_usd,
        "per_share_rate_usd": grid.costs.per_share_rate_usd,
        "per_share_minimum_usd": grid.costs.per_share_minimum_usd,
        "spread_bps": grid.costs.spread_bps,
        "slippage_bps": grid.costs.slippage_bps,
        "fx_cost_bps": grid.costs.fx_cost_bps,
    }
    for label, value in numeric_values.items():
        _finite_nonnegative(value, label)

    if not 0 <= grid.risk.max_symbol_allocation_pct <= 100:
        raise UsGridConfigError("max_symbol_allocation_pct must be within [0, 100]")
    if (
        grid.risk.max_total_deployed_pct
        + grid.risk.minimum_cash_reserve_pct
        > 100 + 1e-9
    ):
        raise UsGridConfigError(
            "max_total_deployed_pct + minimum_cash_reserve_pct cannot exceed 100"
        )
    if not (
        0
        <= grid.volatility_percentile_low
        < grid.volatility_percentile_high
        <= 100
    ):
        raise UsGridConfigError(
            "volatility percentiles must satisfy 0 <= low < high <= 100"
        )
    if grid.sma_mid_period < 2 or grid.sma_long_period <= grid.sma_mid_period:
        raise UsGridConfigError("SMA periods must satisfy 2 <= mid < long")
    if grid.adx_period < 2:
        raise UsGridConfigError("adx_period must be >= 2")
    if grid.recenter_frequency_days <= 0 or grid.recenter_max_per_day <= 0:
        raise UsGridConfigError("recenter frequency and max_per_day must be positive")

    positive_limits = {
        "max_open_orders_per_symbol": grid.risk.max_open_orders_per_symbol,
        "max_open_orders_total": grid.risk.max_open_orders_total,
        "max_orders_per_day": grid.risk.max_orders_per_day,
        "cooldown_days": grid.risk.cooldown_days,
    }
    for label, value in positive_limits.items():
        if value <= 0:
            raise UsGridConfigError(f"{label} must be positive")
    if grid.risk.max_open_orders_per_symbol > grid.risk.max_open_orders_total:
        raise UsGridConfigError(
            "max_open_orders_per_symbol cannot exceed max_open_orders_total"
        )
    if len(grid.symbols) > grid.risk.max_symbols:
        raise UsGridConfigError(
            f"symbol count {len(grid.symbols)} exceeds max_symbols {grid.risk.max_symbols}"
        )

    if grid.costs.commission_mode not in {"percentage", "per_share"}:
        raise UsGridConfigError(
            f"unknown commission_mode: {grid.costs.commission_mode}"
        )
    if (
        grid.costs.maximum_commission_usd > 0
        and grid.costs.maximum_commission_usd
        < grid.costs.minimum_commission_usd
    ):
        raise UsGridConfigError(
            "maximum_commission_usd cannot be below minimum_commission_usd"
        )
    if grid.costs.fx_cost_bps != 0:
        raise UsGridConfigError(
            "fx_cost_bps is not implemented in fill accounting; keep it zero"
        )

    if grid.risk.allow_short or grid.risk.allow_margin:
        raise UsGridConfigError("short and margin modes are outside research scope")


def load_us_grid_config(config: Any) -> GridConfig:
    _prevalidate_boolean_types(config)
    grid = _ORIGINAL_LOAD(config)
    validate_us_grid_config(grid)
    return grid
