from __future__ import annotations

import pytest

from src.us_grid.config import CostModel
from src.us_grid.costs import min_profitable_spacing_bps, round_trip_cost_bps


def test_round_trip_cost_counts_both_sides_and_sell_regulatory_fee() -> None:
    cost = CostModel(
        commission_mode="percentage",
        commission_rate=0.00132,
        spread_bps=5.0,
        slippage_bps=5.0,
        sell_regulatory_fee_enabled=True,
    )
    expected = 2 * 13.2 + 2 * (5.0 + 5.0) + 0.2
    assert round_trip_cost_bps(cost) == pytest.approx(expected)
    assert min_profitable_spacing_bps(cost) == pytest.approx(expected * 1.5)


def test_regulatory_fee_can_be_disabled_in_spacing_gate() -> None:
    cost = CostModel(
        commission_mode="percentage",
        commission_rate=0.001,
        spread_bps=1.0,
        slippage_bps=2.0,
        sell_regulatory_fee_enabled=False,
    )
    assert round_trip_cost_bps(cost) == pytest.approx(26.0)
