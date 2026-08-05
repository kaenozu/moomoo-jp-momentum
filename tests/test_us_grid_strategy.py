"""Unit tests for grid level generation and adaptive spacing."""

from __future__ import annotations

from src.us_grid.config import GridConfig
from src.us_grid.model import GridLevelStatus
from src.us_grid.strategy import (
    build_grid_levels,
    compute_spacing_pct,
    find_sell_slot,
    recenter_instance,
    should_recenter,
)


def _grid(**overrides) -> GridConfig:
    params = dict(
        strategy_name="us_fixed_grid_v1",
        spacing_mode="fixed_pct",
        spacing_pct=1.5,
        buy_levels=3,
        sell_levels=3,
        quantity_per_level=1,
        symbols=["US.SPY"],
        market="US",
    )
    params.update(overrides)
    return GridConfig(**params)  # type: ignore[arg-type]


def test_build_grid_levels_fixed() -> None:
    grid = _grid()
    instance = build_grid_levels(grid, "US.SPY", center_price=100.0, spacing_pct=1.5)
    assert instance.center_price == 100.0
    buys = [lv for lv in instance.levels if lv.side == "BUY"]
    sells = [lv for lv in instance.levels if lv.side == "SELL"]
    assert len(buys) == 3
    assert len(sells) == 3

    # BUY levels below center, descending price with index.
    buy_prices = sorted(lv.target_price for lv in buys)
    assert abs(buy_prices[0] - (100.0 - 1.5 * 3)) < 1e-6
    assert abs(buy_prices[-1] - (100.0 - 1.5)) < 1e-6

    # SELL levels start INACTIVE (paired only).
    assert all(lv.status == GridLevelStatus.INACTIVE for lv in sells)


def test_bounds() -> None:
    grid = _grid(buy_levels=2, sell_levels=4)
    instance = build_grid_levels(grid, "US.SPY", 200.0, 1.0)
    # step = 200 * 1.0% = 2.0; lower = 200 - 2*2 = 196; upper = 200 + 2*4 = 208
    assert instance.lower_bound is not None and abs(instance.lower_bound - 196.0) < 1e-6
    assert instance.upper_bound is not None and abs(instance.upper_bound - 208.0) < 1e-6


def test_find_sell_slot() -> None:
    grid = _grid()
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    slot = find_sell_slot(instance)
    assert slot is not None
    assert slot.side == "SELL"
    assert slot.level_index == -1  # lowest sell slot


def test_compute_spacing_fixed() -> None:
    grid = _grid()
    assert compute_spacing_pct(grid) == 1.5


def test_compute_spacing_atr_clamped() -> None:
    grid = _grid(
        spacing_mode="atr_pct",
        atr_period=5,
        atr_multiplier=0.5,
        min_spacing_pct=0.5,
        max_spacing_pct=4.0,
    )
    # Very low volatility -> ATR small -> clamp to min.
    # TR = max(high-low, |high-prev_close|, |low-prev_close|) = 1.0 with
    # high=100.5, low=99.5, close=100. ATR(5) ~ 1.0 -> ratio 1%, *0.5 -> 0.5%.
    highs = [100.5] * 30
    lows = [99.5] * 30
    closes = [100.0] * 30
    spacing = compute_spacing_pct(grid, highs, lows, closes, 100.0)
    assert spacing == 0.5

    # Very high volatility -> clamp to max.
    highs = [120.0] * 30
    lows = [80.0] * 30
    closes = [100.0] * 30
    spacing = compute_spacing_pct(grid, highs, lows, closes, 100.0)
    assert spacing == 4.0


def test_compute_spacing_atr_fallback_no_data() -> None:
    grid = _grid(spacing_mode="atr_pct")
    assert compute_spacing_pct(grid) == grid.spacing_pct


def test_should_recenter_requires_distance() -> None:
    grid = _grid(recenter_min_distance_atr=2.0, recenter_frequency_days=5)
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    # Price close to center -> no recenter.
    assert not should_recenter(
        instance,
        grid,
        current_price=101.0,
        atr_value=1.0,
        last_recenter_date=None,
        today="2026-01-10",
        recenter_count_today=0,
    )


def test_should_recenter_allows_when_far() -> None:
    grid = _grid(recenter_min_distance_atr=2.0, recenter_frequency_days=5)
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    assert should_recenter(
        instance,
        grid,
        current_price=105.0,
        atr_value=2.0,
        last_recenter_date=None,
        today="2026-01-10",
        recenter_count_today=0,
    )


def test_should_recenter_blocks_within_frequency() -> None:
    grid = _grid(recenter_min_distance_atr=1.0, recenter_frequency_days=5)
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    assert not should_recenter(
        instance,
        grid,
        current_price=103.0,
        atr_value=1.0,
        last_recenter_date="2026-01-08",
        today="2026-01-10",
        recenter_count_today=0,
    )


def test_should_recenter_max_per_day() -> None:
    grid = _grid(recenter_min_distance_atr=1.0, recenter_frequency_days=0)
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    assert not should_recenter(
        instance,
        grid,
        current_price=103.0,
        atr_value=1.0,
        last_recenter_date=None,
        today="2026-01-10",
        recenter_count_today=1,
    )


def test_recenter_rebuilds_levels() -> None:
    grid = _grid()
    instance = build_grid_levels(grid, "US.SPY", 100.0, 1.5)
    recenter_instance(instance, grid, new_center=110.0, today="2026-01-15")
    assert instance.center_price is not None and abs(instance.center_price - 110.0) < 1e-9
    assert instance.last_recenter_at == "2026-01-15"
    buys = [lv for lv in instance.levels if lv.side == "BUY"]
    # spacing_pct=1.5 means 1.5%: step = 110 * 0.015 = 1.65; min buy = 110 - 1.65*3
    assert abs(min(lv.target_price for lv in buys) - (110.0 - 1.65 * 3)) < 1e-6
