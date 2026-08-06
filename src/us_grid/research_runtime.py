"""Runtime adapters that inject the canonical research data context."""

from __future__ import annotations

from .config import CostModel, GridConfig
from .fills import Bar
from .research_context import current_corporate_actions
from .research_safety import (
    ResearchGridBacktester,
    buy_and_hold_with_dividends,
)


def canonical_round_trip_bps(cost: CostModel) -> float:
    """Full BUY+SELL friction used by the grid-width safety gate."""
    commission_bps = (
        cost.commission_rate * 10000
        if cost.commission_mode == "percentage"
        else 0.0
    )
    execution_bps = 2 * (cost.spread_bps + cost.slippage_bps)
    regulatory_bps = 0.2 if cost.sell_regulatory_fee_enabled else 0.0
    return 2 * commission_bps + execution_bps + regulatory_bps


class CanonicalGridBacktester(ResearchGridBacktester):
    def __init__(
        self,
        grid: GridConfig,
        data: dict[str, list[dict]],
        fx: list[dict] | None = None,
    ) -> None:
        super().__init__(grid, data, fx)
        self._corporate_actions = current_corporate_actions()


def canonical_buy_and_hold(
    grid: GridConfig,
    bars_by_code: dict[str, list[Bar]],
    fx_rate_series: dict[str, float],
    start_date: str,
    end_date: str,
    calendar: list[str],
):
    return buy_and_hold_with_dividends(
        grid,
        bars_by_code,
        fx_rate_series,
        start_date,
        end_date,
        calendar,
        corporate_actions=current_corporate_actions(),
    )
