"""
Cost model: commissions, regulatory fees, FX conversion costs.

Commissions use the moomoo JP basic course as the default (0.132% of the
executed amount, tax inclusive, capped at 22 USD), but every number is
configurable so the stress scenarios (1.5x / 2x / stress) are just config
variants.
"""

from __future__ import annotations

from .config import CostModel


def commission_usd(cost: CostModel, notional_usd: float, quantity: int) -> float:
    """Commission for one execution (USD)."""
    if notional_usd <= 0:
        return 0.0
    if cost.commission_mode == "per_share":
        raw = cost.per_share_rate_usd * max(quantity, 0)
        return max(raw, cost.per_share_minimum_usd)
    raw = notional_usd * cost.commission_rate
    if cost.maximum_commission_usd > 0:
        raw = min(raw, cost.maximum_commission_usd)
    return max(raw, cost.minimum_commission_usd)


def sell_regulatory_fee_usd(cost: CostModel, notional_usd: float) -> float:
    """US SEC Section 31 fee on sells (approximate, configurable)."""
    if not cost.sell_regulatory_fee_enabled:
        return 0.0
    # SEC fee is extremely small (~2 bp of the amount); modelled as a fixed
    # bps add-on so stress scenarios can inflate it.
    return notional_usd * 0.00002


def fx_conversion_cost(cost: CostModel, jpy_amount: float) -> float:
    """One-way FX conversion cost in JPY (fx_cost_bps of the amount)."""
    if cost.fx_cost_bps <= 0:
        return 0.0
    return jpy_amount * cost.fx_cost_bps / 10000


def round_trip_cost_bps(cost: CostModel) -> float:
    """Approximate per-cycle cost in bps (commission + spread + slippage)."""
    return cost.round_trip_bps()


def min_profitable_spacing_bps(cost: CostModel, safety_multiple: float = 1.5) -> float:
    """Minimum grid spacing (in bps) to beat round-trip costs with a margin."""
    return cost.round_trip_bps() * safety_multiple
