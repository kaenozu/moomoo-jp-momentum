"""
Regime filter for the US grid strategy.

The filter classifies each bar into ACTIVE_RANGE / TREND_UP / RISK_OFF based
only on data available at that bar (close <= i). The backtest engine applies
the regime computed on bar ``i`` only to orders that would fill from bar
``i+1`` onward, so today's close never decides today's fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import GridConfig
from .indicators import adx, realized_volatility, rolling_percentile, sma
from .model import Regime


@dataclass
class RegimeSeries:
    """Per-bar regime labels and supporting values."""

    regimes: list[Regime] = field(default_factory=list)
    sma_long: list[float | None] = field(default_factory=list)
    adx_values: list[float | None] = field(default_factory=list)
    vol_values: list[float | None] = field(default_factory=list)
    vol_percentile: list[float | None] = field(default_factory=list)


def classify_regime(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    grid: GridConfig,
    vol_window: int = 120,
) -> RegimeSeries:
    """Classify each bar using only data up to that bar."""
    series = RegimeSeries()
    n = len(closes)
    if n == 0:
        return series

    sma_long_values = sma(closes, grid.sma_long_period)
    adx_values = adx(highs, lows, closes, grid.adx_period)
    vol_values = realized_volatility(closes, 20)
    vol_percentile = rolling_percentile(vol_values, vol_window)

    series.sma_long = sma_long_values
    series.adx_values = adx_values
    series.vol_values = vol_values
    series.vol_percentile = vol_percentile

    for i in range(n):
        close = closes[i]
        sma_val = sma_long_values[i]
        adx_val = adx_values[i]
        vol_pct = vol_percentile[i]

        if sma_val is None or adx_val is None or vol_pct is None:
            series.regimes.append(Regime.UNKNOWN)
            continue

        above_sma = close > sma_val * (1 + 1e-9)  # tolerance for float equality
        adx_strong = adx_val > grid.adx_trend_threshold
        vol_in_range = (
            grid.volatility_percentile_low <= vol_pct <= grid.volatility_percentile_high
        )

        if not above_sma:
            series.regimes.append(Regime.RISK_OFF)
        elif adx_strong:
            series.regimes.append(Regime.TREND_UP)
        elif vol_in_range:
            series.regimes.append(Regime.ACTIVE_RANGE)
        else:
            # Above SMA but volatility outside the preferred band:
            # treat as risk-off (do not deploy more into a stressed tape).
            series.regimes.append(Regime.RISK_OFF)

    return series


def regime_for_bar(series: RegimeSeries, index: int) -> Regime:
    """Regime computed from bar ``index`` (None-safe)."""
    if index < 0 or index >= len(series.regimes):
        return Regime.UNKNOWN
    return series.regimes[index]


def apply_regime(
    regime: Regime,
    buy_allowed: bool,
    sell_allowed: bool,
    grid: GridConfig,
) -> tuple[bool, bool]:
    """Map a regime to allowed BUY/SELL actions.

    Defaults to the caller-provided flags; each regime narrows them:
    - ACTIVE_RANGE: unchanged
    - TREND_UP: new BUY is suppressed (no averaging into a strong trend);
      SELL stays as configured.
    - RISK_OFF: no new BUY; SELL restricted to closing existing inventory.
    """
    if not grid.regime_filter_enabled or regime == Regime.UNKNOWN:
        return buy_allowed, sell_allowed
    if regime == Regime.TREND_UP:
        return False, sell_allowed
    if regime == Regime.RISK_OFF:
        return False, sell_allowed
    return buy_allowed, sell_allowed
