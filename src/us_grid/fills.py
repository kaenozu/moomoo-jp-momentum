"""
Conservative OHLC fill simulation.

A daily bar only tells us high and low, not the intraday order of events.
To avoid manufacturing phantom profits:

- A resting BUY limit fills when ``low <= limit``.
- A resting SELL limit fills when ``high >= limit``.
- Gap-through fills are executed at the limit price (no optimistic price
  improvement).
- New paired orders (e.g. a SELL created after a BUY fill) become effective
  from the next bar, unless an intraday ordering can be proven.
- When multiple levels are touched inside one bar we compute two order paths
  (Open->High->Low->Close and Open->Low->High->Close) and the engine keeps
  the worst-case (conservative) result as the primary metric.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import GridConfig


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class FillDecision:
    filled: bool
    price: float
    fill_date: str
    mode: str  # limit_touch / gap_fill / next_bar_open / partial
    quantity: int = 0


def bar_from_dict(row: dict) -> Bar:
    return Bar(
        date=str(row["date"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume") or 0),
    )


def resting_buy_fill(
    limit_price: float,
    quantity: int,
    bar: Bar,
    conservative: bool = True,
) -> FillDecision:
    """A resting BUY limit fills at the limit price if low <= limit."""
    if bar.low <= limit_price:
        return FillDecision(
            filled=True,
            price=limit_price,
            fill_date=bar.date,
            mode="limit_touch",
            quantity=quantity,
        )
    return FillDecision(filled=False, price=0.0, fill_date="", mode="", quantity=0)


def resting_sell_fill(
    limit_price: float,
    quantity: int,
    bar: Bar,
    conservative: bool = True,
) -> FillDecision:
    """A resting SELL limit fills at the limit price if high >= limit."""
    if bar.high >= limit_price:
        return FillDecision(
            filled=True,
            price=limit_price,
            fill_date=bar.date,
            mode="limit_touch",
            quantity=quantity,
        )
    return FillDecision(filled=False, price=0.0, fill_date="", mode="", quantity=0)


def apply_cost_adjustment(
    fill: FillDecision,
    side: str,
    grid: GridConfig,
) -> float:
    """Apply spread + slippage to a fill price.

    BUY pays up: price * (1 + (spread+slippage)/10000).
    SELL receives less: price * (1 - (spread+slippage)/10000).
    """
    bps = grid.costs.spread_bps + grid.costs.slippage_bps
    if side == "BUY":
        return fill.price * (1 + bps / 10000)
    return fill.price * (1 - bps / 10000)


def pair_fill_next_bar(
    buy_fill_bar_index: int,
    bar_index: int,
) -> bool:
    """Whether a paired order created after bar ``buy_fill_bar_index`` can
    fill on ``bar_index``.

    Conservative default: the paired order is only eligible from the next bar
    (bar_index >= buy_fill_bar_index + 1).
    """
    return bar_index >= buy_fill_bar_index + 1


def both_paths_reachable(
    buy_limits: list[float], sell_limits: list[float], bar: Bar
) -> bool:
    """If both a BUY and a SELL limit are touched in the same bar, at least
    one ordering must be assumed. The conservative path (BUY first, then SELL)
    is used by the engine as the worst case for the paired-order rule.
    """
    buy_touched = any(bar.low <= p for p in buy_limits)
    sell_touched = any(bar.high >= p for p in sell_limits)
    return buy_touched and sell_touched
