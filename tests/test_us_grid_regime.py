"""Unit tests for the regime filter."""

from __future__ import annotations

from src.us_grid.config import GridConfig
from src.us_grid.model import Regime
from src.us_grid.regime import apply_regime, classify_regime


def _grid(regime_enabled: bool = True) -> GridConfig:
    return GridConfig(
        regime_filter_enabled=regime_enabled,
        sma_long_period=10,
        sma_mid_period=5,
        adx_period=5,
        adx_trend_threshold=25.0,
    )


def test_strong_uptrend_is_trend_up() -> None:
    grid = _grid()
    # Monotonic uptrend: price > SMA, strong ADX -> TREND_UP
    closes = [100.0 + i for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    series = classify_regime(closes, highs, lows, grid)
    last = series.regimes[-1]
    assert last == Regime.TREND_UP


def test_flat_market_is_active_range() -> None:
    grid = _grid()
    # Force ADX to be considered weak (threshold above any realistic ADX) so
    # the ACTIVE_RANGE branch depends only on close > SMA and vol in band.
    grid.adx_trend_threshold = 200.0
    grid.volatility_percentile_low = 0.0
    grid.volatility_percentile_high = 100.0
    # Gentle monotonic drift keeps close above the SMA at every bar.
    closes = [100.0 + i * 0.005 for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    series = classify_regime(closes, highs, lows, grid)
    last = series.regimes[-1]
    assert last == Regime.ACTIVE_RANGE


def test_crash_is_risk_off() -> None:
    grid = _grid()
    # Sharp drop: price below SMA -> RISK_OFF
    closes = [100.0 - i * 1.5 for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    series = classify_regime(closes, highs, lows, grid)
    assert Regime.RISK_OFF in series.regimes[-10:]


def test_regime_uses_only_prior_data() -> None:
    """classify_regime at index i must not depend on bars > i."""
    grid = _grid()
    grid.volatility_percentile_low = 0.0
    grid.volatility_percentile_high = 100.0
    closes = [100.0 + i * 0.005 for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    series = classify_regime(closes, highs, lows, grid)

    # Truncate the same series at k and recompute: values at k must match.
    for k in (20, 40, 59):
        prefix_closes = closes[: k + 1]
        prefix_highs = highs[: k + 1]
        prefix_lows = lows[: k + 1]
        prefix_series = classify_regime(prefix_closes, prefix_highs, prefix_lows, grid)
        assert prefix_series.regimes[k] == series.regimes[k]


def test_apply_regime_disabled_keeps_flags() -> None:
    grid = _grid(regime_enabled=False)
    buy, sell = apply_regime(Regime.RISK_OFF, True, True, grid)
    assert buy is True and sell is True


def test_apply_regime_risk_off_blocks_buy() -> None:
    grid = _grid()
    buy, sell = apply_regime(Regime.RISK_OFF, True, True, grid)
    assert buy is False and sell is True


def test_apply_regime_trend_up_blocks_new_buy() -> None:
    grid = _grid()
    buy, sell = apply_regime(Regime.TREND_UP, True, True, grid)
    assert buy is False and sell is True


def test_apply_regime_active_range_keeps_flags() -> None:
    grid = _grid()
    buy, sell = apply_regime(Regime.ACTIVE_RANGE, True, True, grid)
    assert buy is True and sell is True
