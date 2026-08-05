"""
Technical indicators for the US grid strategy.

All functions are vectorised over plain lists of floats and never look at
future bars (each value at index i uses only bars <= i).
"""

from __future__ import annotations

from typing import Sequence


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Simple moving average. None until ``period`` bars are available."""
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = []
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i + 1 >= period:
            result.append(running / period)
        else:
            result.append(None)
    return result


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    """True range for each bar (first bar uses high-low)."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high/low/close lengths must match")
    result: list[float] = []
    prev_close: float | None = None
    for high, low, close in zip(highs, lows, closes):
        if prev_close is None:
            result.append(high - low)
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            result.append(tr)
        prev_close = close
    return result


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float | None]:
    """Wilder's ATR. None until ``period`` bars are available."""
    tr = true_range(highs, lows, closes)
    result: list[float | None] = []
    for i, value in enumerate(tr):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            result.append(sum(tr[:period]) / period)
        else:
            prev = result[-1]
            assert prev is not None
            result.append((prev * (period - 1) + value) / period)
    return result


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    """Wilder's ADX. None until enough bars are available."""
    if period < 2:
        raise ValueError("period must be >= 2")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high/low/close lengths must match")

    n = len(highs)
    if n < period + 1:
        return [None] * n

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr: list[float] = []
    prev_high = highs[0]
    prev_low = lows[0]
    prev_close = closes[0]
    for i in range(1, n):
        up = highs[i] - prev_high
        down = prev_low - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - prev_close),
                abs(lows[i] - prev_close),
            )
        )
        prev_high = highs[i]
        prev_low = lows[i]
        prev_close = closes[i]

    # Wilder smoothing for +DM, -DM, TR
    def _wilder(values: list[float], period: int) -> list[float]:
        out: list[float] = []
        first = sum(values[:period]) / period
        out.append(first)
        for value in values[period:]:
            out.append((out[-1] * (period - 1) + value) / period)
        return out

    sm_plus = _wilder(plus_dm, period)
    sm_minus = _wilder(minus_dm, period)
    sm_tr = _wilder(tr, period)

    result: list[float | None] = [None] * n
    # sm_*[0] corresponds to bar index `period` (0-based original index).
    # +DI/-DI are defined from bar `period`; DX/ADX need one more period.
    di_buffer: list[float] = []
    for i in range(len(sm_plus)):
        if sm_tr[i] <= 0:
            di_buffer.append(0.0)
            continue
        plus_di = 100.0 * sm_plus[i] / sm_tr[i]
        minus_di = 100.0 * sm_minus[i] / sm_tr[i]
        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        di_buffer.append(dx)

    if len(di_buffer) >= period:
        adx_first = sum(di_buffer[:period]) / period
        adx_values = [adx_first]
        for dx in di_buffer[period:]:
            adx_values.append((adx_values[-1] * (period - 1) + dx) / period)
        # di_buffer[0] corresponds to bar index `period` (0-based original).
        # ADX[0] is the average of di_buffer[0..period-1], so it corresponds
        # to bar index `period + period - 1`.
        start_index = period + period - 1
        for offset, value in enumerate(adx_values):
            idx = start_index + offset
            if idx < n:
                result[idx] = value

    return result


def realized_volatility(closes: Sequence[float], period: int) -> list[float | None]:
    """Annualised realised volatility from daily log returns (None early)."""
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < period:
            result.append(None)
            continue
        returns: list[float] = []
        for j in range(i - period + 1, i + 1):
            prev = closes[j - 1]
            cur = closes[j]
            if prev > 0 and cur > 0:
                import math

                returns.append(math.log(cur / prev))
        if len(returns) < 2:
            result.append(None)
            continue
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        import math

        result.append(math.sqrt(variance) * math.sqrt(252))
    return result


def rolling_percentile(
    values: Sequence[float | None], window: int
) -> list[float | None]:
    """Percentile rank (0-100) of each value within the trailing window."""
    result: list[float | None] = []
    window_buffer: list[float] = []
    for i, value in enumerate(values):
        if value is None:
            result.append(None)
            continue
        window_buffer.append(value)
        if len(window_buffer) > window:
            window_buffer.pop(0)
        below = sum(1 for v in window_buffer if v <= value)
        result.append(below * 100.0 / len(window_buffer))
    return result
