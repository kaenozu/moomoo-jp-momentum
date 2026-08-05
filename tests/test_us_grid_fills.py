"""Unit tests for the conservative OHLC fill model."""

from __future__ import annotations

from src.us_grid.config import CostModel, GridConfig
from src.us_grid.fills import (
    Bar,
    apply_cost_adjustment,
    both_paths_reachable,
    pair_fill_next_bar,
    resting_buy_fill,
    resting_sell_fill,
)


def _bar(date="2026-01-05", open=100.0, high=102.0, low=98.0, close=101.0) -> Bar:
    return Bar(date=date, open=open, high=high, low=low, close=close)


def test_resting_buy_fill_when_low_touches() -> None:
    decision = resting_buy_fill(99.0, 1, _bar(low=98.5))
    assert decision.filled
    assert decision.price == 99.0  # fills at limit, no price improvement
    assert decision.mode == "limit_touch"


def test_resting_buy_no_fill() -> None:
    decision = resting_buy_fill(97.0, 1, _bar(low=98.0))
    assert not decision.filled


def test_resting_sell_fill_when_high_touches() -> None:
    decision = resting_sell_fill(101.5, 1, _bar(high=102.0))
    assert decision.filled
    assert decision.price == 101.5


def test_resting_sell_no_fill() -> None:
    decision = resting_sell_fill(103.0, 1, _bar(high=102.0))
    assert not decision.filled


def test_gap_buy_fills_at_limit_no_improvement() -> None:
    # gap down through the limit: low is far below limit
    decision = resting_buy_fill(
        100.0, 1, _bar(open=99.0, low=95.0, high=99.5, close=96.0)
    )
    assert decision.filled
    assert decision.price == 100.0  # conservative: no price improvement


def test_cost_adjustment_buy_pays_up() -> None:
    grid = GridConfig(costs=CostModel(spread_bps=5, slippage_bps=5))
    decision = resting_buy_fill(100.0, 1, _bar(low=99.0))
    assert decision.filled
    adjusted = apply_cost_adjustment(decision, "BUY", grid)
    assert adjusted > 100.0


def test_cost_adjustment_sell_receives_less() -> None:
    grid = GridConfig(costs=CostModel(spread_bps=5, slippage_bps=5))
    decision = resting_sell_fill(100.0, 1, _bar(high=101.0))
    assert decision.filled
    adjusted = apply_cost_adjustment(decision, "SELL", grid)
    assert adjusted < 100.0


def test_pair_fill_next_bar_contract() -> None:
    # Paired order created at bar 3 can only fill at bar 4 or later.
    assert not pair_fill_next_bar(3, 3)
    assert pair_fill_next_bar(3, 4)
    assert pair_fill_next_bar(3, 5)


def test_both_paths_reachable() -> None:
    assert both_paths_reachable([99.0], [101.0], _bar(low=98.5, high=101.5))
    assert not both_paths_reachable([99.0], [103.0], _bar(low=98.5, high=102.0))
