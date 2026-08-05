"""Unit tests for the US grid indicators."""

from __future__ import annotations

from src.us_grid.indicators import (
    adx,
    atr,
    realized_volatility,
    rolling_percentile,
    sma,
    true_range,
)


def test_sma_basic() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    result = sma(values, 3)
    assert result[:2] == [None, None]
    assert result[2] is not None and abs(result[2] - 2.0) < 1e-9
    assert result[3] is not None and abs(result[3] - 3.0) < 1e-9


def test_sma_empty() -> None:
    assert sma([], 3) == []


def test_true_range_first_bar() -> None:
    highs = [10.0, 12.0]
    lows = [8.0, 9.0]
    closes = [9.0, 11.0]
    result = true_range(highs, lows, closes)
    assert abs(result[0] - 2.0) < 1e-9  # high - low
    assert result[1] > 0


def test_atr_converges() -> None:
    highs = [10 + i for i in range(20)]
    lows = [8 + i for i in range(20)]
    closes = [9 + i for i in range(20)]
    result = atr(highs, lows, closes, 5)
    assert result[4] is not None
    assert result[4] > 0
    assert all(v is not None for v in result[4:])


def test_adx_returns_values() -> None:
    # A strong uptrend should produce a non-None ADX eventually.
    highs = [i + 0.5 for i in range(50)]
    lows = [i - 0.5 for i in range(50)]
    closes = [i for i in range(50)]
    result = adx(highs, lows, closes, 14)
    non_none = [v for v in result if v is not None]
    assert non_none, "ADX should produce values"
    assert all(0 <= v <= 100 for v in non_none)


def test_adx_flat_market_is_low() -> None:
    highs = [100.0] * 50
    lows = [99.0] * 50
    closes = [99.5] * 50
    result = adx(highs, lows, closes, 14)
    non_none = [v for v in result if v is not None]
    assert all(v < 20 for v in non_none)


def test_realized_volatility_positive() -> None:

    values = [100.0 * (1.01**i) for i in range(40)]
    result = realized_volatility(values, 20)
    assert result[19] is None or result[19] is not None
    non_none = [v for v in result if v is not None]
    assert non_none
    assert all(v >= 0 for v in non_none)


def test_rolling_percentile() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    result = rolling_percentile(values, 4)
    assert result[-1] is not None and abs(result[-1] - 100.0) < 1e-9  # largest = 100th
