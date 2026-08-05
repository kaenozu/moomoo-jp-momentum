"""
US grid strategy configuration loading and validation.

All numbers are research inputs from ``config.yaml``; the strategy code never
hard-codes parameter values.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any


class UsGridConfigError(ValueError):
    """Invalid US grid configuration."""


LEVERAGED_ETF_MARKERS = (
    "UPRO",
    "TQQQ",
    "SPXL",
    "SPYU",
    "SSO",
    "QLD",
    "TNA",
    "UDOW",
    "SOXL",
    "TECL",
    "TQQQ",
    "FNGU",
    "HIBL",
    "LABU",
    "SPUU",
)
INVERSE_ETF_MARKERS = (
    "SPXS",
    "SQQQ",
    "SPXU",
    "SH",
    "PSQ",
    "DOG",
    "SDOW",
    "SOXS",
    "TZA",
    "FAZ",
    "TBF",
    "SKF",
    "SCO",
    "VIXY",
    "SQQQ",
)


@dataclass
class CostModel:
    commission_mode: str = "percentage"  # percentage | per_share
    commission_rate: float = 0.00132  # 0.132% (moomoo JP basic, tax incl.)
    minimum_commission_usd: float = 0.01
    maximum_commission_usd: float = 22.0  # moomoo JP basic cap (tax incl.)
    per_share_rate_usd: float = 0.0
    per_share_minimum_usd: float = 0.0
    spread_bps: float = 5.0
    slippage_bps: float = 5.0
    sell_regulatory_fee_enabled: bool = True
    fx_cost_bps: float = 0.0

    def round_trip_bps(self) -> float:
        """Approximate per-cycle cost in bps (commission + spread + slippage)."""
        commission_bps = self.commission_rate * 10000
        return commission_bps * 2 + self.spread_bps + self.slippage_bps


@dataclass
class RiskLimits:
    allow_short: bool = False
    allow_margin: bool = False
    allow_leveraged_etf: bool = False
    allow_inverse_etf: bool = False
    max_symbols: int = 3
    max_symbol_allocation_pct: float = 25.0
    max_total_deployed_pct: float = 80.0
    minimum_cash_reserve_pct: float = 20.0
    max_inventory_levels_per_symbol: int = 4
    max_open_orders_per_symbol: int = 8
    max_open_orders_total: int = 20
    max_orders_per_day: int = 30
    daily_loss_limit_pct: float = 3.0
    strategy_drawdown_limit_pct: float = 12.0
    portfolio_drawdown_limit_pct: float = 15.0
    gap_stop_pct: float = 5.0
    stale_quote_seconds: float = 30.0
    max_reconcile_age_seconds: float = 60.0
    cooldown_days: int = 3


@dataclass
class GridConfig:
    enabled: bool = False
    mode: str = "backtest"  # backtest | virtual | paper
    strategy_name: str = "us_adaptive_grid_v1"
    market: str = "US"

    center_mode: str = "initial_close"
    spacing_mode: str = "fixed_pct"  # fixed_pct | atr_pct
    spacing_pct: float = 1.5
    atr_period: int = 14
    atr_multiplier: float = 1.0
    min_spacing_pct: float = 0.75
    max_spacing_pct: float = 4.0
    buy_levels: int = 3
    sell_levels: int = 3
    quantity_per_level: int = 1

    # core + grid
    core_allocation_pct: float = 0.0

    # regime filter
    regime_filter_enabled: bool = False
    sma_long_period: int = 200
    sma_mid_period: int = 50
    adx_period: int = 14
    adx_trend_threshold: float = 25.0
    volatility_percentile_low: float = 20.0
    volatility_percentile_high: float = 80.0

    # recenter
    recenter_frequency_days: int = 5
    recenter_max_per_day: int = 1
    recenter_min_distance_atr: float = 2.0

    risk: RiskLimits = field(default_factory=RiskLimits)
    costs: CostModel = field(default_factory=CostModel)

    symbols: list[str] = field(default_factory=list)
    data_dir: str = "data/us_grid"
    capital_jpy: float = 100000.0
    currency: str = "JPY"
    fx_path: str = ""

    @property
    def tradeable_symbols(self) -> list[str]:
        return list(self.symbols)


_SYMBOL_RE = re.compile(r"^US\.[A-Z0-9.\-]+$")


def _parse_risk(section: dict[str, Any], key: str, default: Any) -> Any:
    return section.get(key, default)


def load_us_grid_config(config: Any) -> GridConfig:
    """Load and validate the ``us_grid`` config section from a Config-like object.

    ``config`` can be the project's ``src.config.Config`` or a plain mapping.
    """
    section = _section(config, "us_grid")
    if not isinstance(section, dict):
        section = {}

    grid_section = section.get("grid", {}) if isinstance(section, dict) else {}
    risk_section = section.get("risk", {}) if isinstance(section, dict) else {}
    cost_section = section.get("costs", {}) if isinstance(section, dict) else {}

    grid = GridConfig(
        enabled=bool(section.get("enabled", False)),
        mode=str(section.get("mode", "backtest")),
        strategy_name=str(section.get("strategy_name", "us_adaptive_grid_v1")),
        market=str(section.get("market", "US")).upper(),
        center_mode=str(grid_section.get("center_mode", "initial_close")),
        spacing_mode=str(grid_section.get("spacing_mode", "fixed_pct")),
        spacing_pct=float(grid_section.get("spacing_pct", 1.5)),
        atr_period=int(grid_section.get("atr_period", 14)),
        atr_multiplier=float(grid_section.get("atr_multiplier", 1.0)),
        min_spacing_pct=float(grid_section.get("min_spacing_pct", 0.75)),
        max_spacing_pct=float(grid_section.get("max_spacing_pct", 4.0)),
        buy_levels=int(grid_section.get("buy_levels", 3)),
        sell_levels=int(grid_section.get("sell_levels", 3)),
        quantity_per_level=int(grid_section.get("quantity_per_level", 1)),
        core_allocation_pct=float(grid_section.get("core_allocation_pct", 0.0)),
        regime_filter_enabled=bool(
            section.get("regime_filter", {}).get("enabled", False)
        ),
        sma_long_period=int(
            section.get("regime_filter", {}).get("sma_long_period", 200)
        ),
        sma_mid_period=int(section.get("regime_filter", {}).get("sma_mid_period", 50)),
        adx_period=int(section.get("regime_filter", {}).get("adx_period", 14)),
        adx_trend_threshold=float(
            section.get("regime_filter", {}).get("adx_trend_threshold", 25.0)
        ),
        volatility_percentile_low=float(
            section.get("regime_filter", {}).get("volatility_percentile_low", 20.0)
        ),
        volatility_percentile_high=float(
            section.get("regime_filter", {}).get("volatility_percentile_high", 80.0)
        ),
        recenter_frequency_days=int(
            section.get("recenter", {}).get("frequency_days", 5)
        ),
        recenter_max_per_day=int(section.get("recenter", {}).get("max_per_day", 1)),
        recenter_min_distance_atr=float(
            section.get("recenter", {}).get("min_distance_atr", 2.0)
        ),
        risk=RiskLimits(
            allow_short=bool(_parse_risk(risk_section, "allow_short", False)),
            allow_margin=bool(_parse_risk(risk_section, "allow_margin", False)),
            allow_leveraged_etf=bool(
                _parse_risk(risk_section, "allow_leveraged_etf", False)
            ),
            allow_inverse_etf=bool(
                _parse_risk(risk_section, "allow_inverse_etf", False)
            ),
            max_symbols=int(_parse_risk(risk_section, "max_symbols", 3)),
            max_symbol_allocation_pct=float(
                _parse_risk(risk_section, "max_symbol_allocation_pct", 25.0)
            ),
            max_total_deployed_pct=float(
                _parse_risk(risk_section, "max_total_deployed_pct", 80.0)
            ),
            minimum_cash_reserve_pct=float(
                _parse_risk(risk_section, "minimum_cash_reserve_pct", 20.0)
            ),
            max_inventory_levels_per_symbol=int(
                _parse_risk(risk_section, "max_inventory_levels_per_symbol", 4)
            ),
            max_open_orders_per_symbol=int(
                _parse_risk(risk_section, "max_open_orders_per_symbol", 8)
            ),
            max_open_orders_total=int(
                _parse_risk(risk_section, "max_open_orders_total", 20)
            ),
            max_orders_per_day=int(_parse_risk(risk_section, "max_orders_per_day", 30)),
            daily_loss_limit_pct=float(
                _parse_risk(risk_section, "daily_loss_limit_pct", 3.0)
            ),
            strategy_drawdown_limit_pct=float(
                _parse_risk(risk_section, "strategy_drawdown_limit_pct", 12.0)
            ),
            portfolio_drawdown_limit_pct=float(
                _parse_risk(risk_section, "portfolio_drawdown_limit_pct", 15.0)
            ),
            gap_stop_pct=float(_parse_risk(risk_section, "gap_stop_pct", 5.0)),
            stale_quote_seconds=float(
                _parse_risk(risk_section, "stale_quote_seconds", 30.0)
            ),
            max_reconcile_age_seconds=float(
                _parse_risk(risk_section, "max_reconcile_age_seconds", 60.0)
            ),
            cooldown_days=int(_parse_risk(risk_section, "cooldown_days", 3)),
        ),
        costs=CostModel(
            commission_mode=str(cost_section.get("commission_mode", "percentage")),
            commission_rate=float(cost_section.get("commission_rate", 0.00132)),
            minimum_commission_usd=float(
                cost_section.get("minimum_commission_usd", 0.01)
            ),
            maximum_commission_usd=float(
                cost_section.get("maximum_commission_usd", 22.0)
            ),
            per_share_rate_usd=float(cost_section.get("per_share_rate_usd", 0.0)),
            per_share_minimum_usd=float(cost_section.get("per_share_minimum_usd", 0.0)),
            spread_bps=float(cost_section.get("spread_bps", 5.0)),
            slippage_bps=float(cost_section.get("slippage_bps", 5.0)),
            sell_regulatory_fee_enabled=bool(
                cost_section.get("sell_regulatory_fee_enabled", True)
            ),
            fx_cost_bps=float(cost_section.get("fx_cost_bps", 0.0)),
        ),
        symbols=[str(s) for s in section.get("symbols", [])],
        data_dir=str(section.get("data_dir", "data/us_grid")),
        capital_jpy=float(section.get("capital_jpy", 100000.0)),
        currency=str(section.get("currency", "JPY")),
        fx_path=str(section.get("fx_path", "")),
    )

    validate_us_grid_config(grid)
    return grid


def _section(config: Any, key: str) -> Any:
    if config is None:
        return {}
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, {})
    if isinstance(config, dict):
        return config.get(key, {})
    return {}


def validate_us_grid_config(grid: GridConfig) -> None:
    """Validate a loaded grid config, raising UsGridConfigError on problems."""
    if grid.market != "US":
        raise UsGridConfigError(f"market must be US, got: {grid.market}")
    if grid.mode not in {"backtest", "virtual", "paper"}:
        raise UsGridConfigError(f"unknown mode: {grid.mode}")
    if grid.capital_jpy <= 0:
        raise UsGridConfigError("capital_jpy must be positive")
    if grid.spacing_mode not in {"fixed_pct", "atr_pct"}:
        raise UsGridConfigError(f"unknown spacing_mode: {grid.spacing_mode}")
    if grid.spacing_mode == "fixed_pct" and grid.spacing_pct <= 0:
        raise UsGridConfigError("spacing_pct must be positive for fixed_pct mode")
    if grid.min_spacing_pct <= 0:
        raise UsGridConfigError("min_spacing_pct must be positive")
    if grid.max_spacing_pct < grid.min_spacing_pct:
        raise UsGridConfigError("max_spacing_pct must be >= min_spacing_pct")
    if grid.buy_levels < 0 or grid.sell_levels < 0:
        raise UsGridConfigError("levels cannot be negative")
    if grid.buy_levels + grid.sell_levels > 20:
        raise UsGridConfigError("too many grid levels (max 20 total)")
    if grid.quantity_per_level <= 0:
        raise UsGridConfigError("quantity_per_level must be positive")
    if not 0 <= grid.core_allocation_pct <= 100:
        raise UsGridConfigError("core_allocation_pct must be within [0, 100]")
    if grid.atr_period < 2:
        raise UsGridConfigError("atr_period must be >= 2")
    if grid.atr_multiplier <= 0:
        raise UsGridConfigError("atr_multiplier must be positive")
    if not 0 <= grid.risk.max_total_deployed_pct <= 100:
        raise UsGridConfigError("max_total_deployed_pct must be within [0, 100]")
    if not 0 <= grid.risk.minimum_cash_reserve_pct <= 100:
        raise UsGridConfigError("minimum_cash_reserve_pct must be within [0, 100]")
    if grid.risk.max_symbols <= 0:
        raise UsGridConfigError("max_symbols must be positive")
    if grid.risk.max_inventory_levels_per_symbol <= 0:
        raise UsGridConfigError("max_inventory_levels_per_symbol must be positive")

    seen: set[str] = set()
    for symbol in grid.symbols:
        if symbol in seen:
            raise UsGridConfigError(f"duplicate symbol: {symbol}")
        seen.add(symbol)
        if not symbol.startswith("US."):
            raise UsGridConfigError(f"symbol must be US.*, got: {symbol}")
        base = symbol.removeprefix("US.")
        if not _SYMBOL_RE.match(symbol):
            raise UsGridConfigError(f"invalid symbol format: {symbol}")
        upper = base.upper()
        if not grid.risk.allow_leveraged_etf and upper in LEVERAGED_ETF_MARKERS:
            raise UsGridConfigError(f"leveraged ETF not allowed: {symbol}")
        if not grid.risk.allow_inverse_etf and upper in INVERSE_ETF_MARKERS:
            raise UsGridConfigError(f"inverse ETF not allowed: {symbol}")


def config_summary(grid: GridConfig) -> dict[str, Any]:
    """A serializable summary of the grid config for the run manifest."""
    return dataclasses.asdict(grid)
