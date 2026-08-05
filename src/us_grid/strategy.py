"""
Grid strategy: level generation, spacing, recentering, and desired orders.

The strategy produces ``DesiredOrder`` objects. A separate risk engine
approves them and an execution adapter sends them; the strategy never talks
to a broker.
"""

from __future__ import annotations


from .config import GridConfig
from .indicators import atr
from .model import DesiredOrder, GridInstance, GridLevel, GridLevelStatus


def compute_spacing_pct(
    grid: GridConfig,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
    center_price: float | None = None,
) -> float:
    """Spacing as a percentage of the center price.

    fixed_pct returns ``spacing_pct`` directly. atr_pct returns
    ``clamp(ATR/center * multiplier, min, max)``. If not enough bars are
    available for ATR, returns ``spacing_pct`` as a fallback.
    """
    if grid.spacing_mode == "fixed_pct":
        return grid.spacing_pct

    if not (highs and lows and closes and center_price and center_price > 0):
        return grid.spacing_pct

    atr_values = atr(highs, lows, closes, grid.atr_period)
    last_atr = next((v for v in reversed(atr_values) if v is not None), None)
    if last_atr is None or last_atr <= 0:
        return grid.spacing_pct

    ratio = last_atr / center_price
    raw = ratio * grid.atr_multiplier * 100.0
    return max(grid.min_spacing_pct, min(grid.max_spacing_pct, raw))


def build_grid_levels(
    grid: GridConfig,
    code: str,
    center_price: float,
    spacing_pct: float,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
    core_quantity: int = 0,
    grid_quantity: int = 0,
) -> GridInstance:
    """Create a fresh grid instance centred on ``center_price``.

    BUY levels sit below the center, SELL levels above. In a cash-start
    grid, only BUY levels are armed initially; SELL levels are created as
    pairs when a BUY fills (handled by the engine).
    """
    instance = GridInstance(
        strategy_name=grid.strategy_name,
        code=code,
        mode=grid.mode,
        center_price=center_price,
        spacing_pct=spacing_pct,
        core_quantity=core_quantity,
        grid_quantity=grid_quantity,
    )

    step = center_price * spacing_pct / 100.0
    instance.lower_bound = center_price - step * grid.buy_levels
    instance.upper_bound = center_price + step * grid.sell_levels

    levels: list[GridLevel] = []
    # BUY levels: index 1..buy_levels below center
    for i in range(1, grid.buy_levels + 1):
        levels.append(
            GridLevel(
                level_index=i,
                side="BUY",
                target_price=round(center_price - step * i, 6),
                quantity=grid_quantity,
                status=GridLevelStatus.ACTIVE,
            )
        )
    # SELL levels: index -1..-sell_levels above center (created later)
    # We still reserve their slots for deterministic ordering.
    for i in range(1, grid.sell_levels + 1):
        levels.append(
            GridLevel(
                level_index=-i,
                side="SELL",
                target_price=round(center_price + step * i, 6),
                quantity=grid_quantity,
                status=GridLevelStatus.INACTIVE,  # armed only after a BUY fill
            )
        )

    instance.levels = levels
    return instance


def find_sell_slot(instance: GridInstance) -> GridLevel | None:
    """Return the lowest INACTIVE SELL slot to pair with a BUY fill."""
    for level in instance.levels:
        if level.side == "SELL" and level.status == GridLevelStatus.INACTIVE:
            return level
    return None


def arm_paired_sell(
    instance: GridInstance,
    grid: GridConfig,
    buy_level_index: int,
    buy_fill_price: float,
) -> GridLevel | None:
    """Create (or arm) the SELL level one spacing above a BUY fill.

    The paired SELL price is ``buy_fill_price * (1 + spacing_pct/100)`` so the
    round trip captures one grid spacing, per the strategy spec.
    """
    spacing = instance.spacing_pct or grid.spacing_pct
    sell_price = buy_fill_price * (1 + spacing / 100.0)

    # Reuse an INACTIVE SELL slot if present, otherwise append a new level.
    slot = find_sell_slot(instance)
    if slot is not None:
        slot.target_price = round(sell_price, 6)
        slot.quantity = instance.grid_quantity
        slot.status = GridLevelStatus.ACTIVE
        slot.paired_level_index = buy_level_index
        return slot

    new_level = GridLevel(
        level_index=-(len([lv for lv in instance.levels if lv.side == "SELL"]) + 1),
        side="SELL",
        target_price=round(sell_price, 6),
        quantity=instance.grid_quantity,
        status=GridLevelStatus.ACTIVE,
        paired_level_index=buy_level_index,
    )
    instance.levels.append(new_level)
    return new_level


def refresh_spacing(
    instance: GridInstance,
    grid: GridConfig,
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> None:
    """Update the adaptive spacing on the instance (ATR mode)."""
    if grid.spacing_mode != "atr_pct" or instance.center_price is None:
        return
    spacing = compute_spacing_pct(grid, highs, lows, closes, instance.center_price)
    instance.spacing_pct = spacing


def should_recenter(
    instance: GridInstance,
    grid: GridConfig,
    current_price: float,
    atr_value: float | None,
    last_recenter_date: str | None,
    today: str,
    recenter_count_today: int,
) -> bool:
    """Rules to avoid hiding losses with aggressive recentering.

    Recentering is only allowed when:
    - it is a new week (or the configured frequency in days has passed),
    - there is no unfilled BUY order below (handled by caller),
    - inventory is below a threshold,
    - price has drifted at least ``min_distance_atr`` ATRs from center,
    - at most once per day.
    """
    if recenter_count_today >= grid.recenter_max_per_day:
        return False
    if instance.center_price is None or instance.center_price <= 0:
        return False
    if last_recenter_date:
        try:
            days_diff = (
                __import__("datetime").date.fromisoformat(today)
                - __import__("datetime").date.fromisoformat(last_recenter_date)
            ).days
        except ValueError:
            days_diff = 0
        if days_diff < grid.recenter_frequency_days:
            return False
    if atr_value is None or atr_value <= 0:
        return False
    distance_atr = abs(current_price - instance.center_price) / atr_value
    if distance_atr < grid.recenter_min_distance_atr:
        return False
    # Do not recenter while holding inventory at/above the level count
    # threshold (no infinite averaging down).
    held = sum(1 for lv in instance.levels if lv.status == GridLevelStatus.FILLED)
    if held >= grid.risk.max_inventory_levels_per_symbol:
        return False
    return True


def recenter_instance(
    instance: GridInstance,
    grid: GridConfig,
    new_center: float,
    today: str,
) -> None:
    """Rebuild the grid around ``new_center`` (all levels reset)."""
    instance.center_price = new_center
    step = new_center * (instance.spacing_pct or grid.spacing_pct) / 100.0
    instance.lower_bound = new_center - step * grid.buy_levels
    instance.upper_bound = new_center + step * grid.sell_levels
    instance.levels = []
    for i in range(1, grid.buy_levels + 1):
        instance.levels.append(
            GridLevel(
                level_index=i,
                side="BUY",
                target_price=round(new_center - step * i, 6),
                quantity=instance.grid_quantity,
                status=GridLevelStatus.ACTIVE,
            )
        )
    for i in range(1, grid.sell_levels + 1):
        instance.levels.append(
            GridLevel(
                level_index=-i,
                side="SELL",
                target_price=round(new_center + step * i, 6),
                quantity=instance.grid_quantity,
                status=GridLevelStatus.INACTIVE,
            )
        )
    instance.last_recenter_at = today


def desired_buy_orders(instance: GridInstance) -> list[DesiredOrder]:
    """Desired BUY orders for all ACTIVE BUY levels without a resting order."""
    result: list[DesiredOrder] = []
    for level in instance.levels:
        if level.side != "BUY" or level.status != GridLevelStatus.ACTIVE:
            continue
        if level.last_order_id is not None:
            continue
        result.append(
            DesiredOrder(
                code=instance.code,
                side="BUY",
                quantity=level.quantity,
                limit_price=level.target_price,
                grid_instance_id=instance.id,
                grid_level_index=level.level_index,
                reason="grid_buy",
            )
        )
    return result


def desired_sell_order(instance: GridInstance, level: GridLevel) -> DesiredOrder:
    """Desired SELL for an armed SELL level."""
    return DesiredOrder(
        code=instance.code,
        side="SELL",
        quantity=level.quantity,
        limit_price=level.target_price,
        grid_instance_id=instance.id,
        grid_level_index=level.level_index,
        reason="grid_sell",
    )
