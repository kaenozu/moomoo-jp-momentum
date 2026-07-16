"""Pure execution accounting shared by backtests and virtual trading."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal

Side = Literal["BUY", "SELL"]
EXECUTION_ENGINE_VERSION = "2.0.0"


@dataclass(frozen=True)
class PositionState:
    """Minimal position state required for a fill transition."""

    quantity: int = 0
    avg_cost: float = 0.0
    realized_pl: float = 0.0


@dataclass(frozen=True)
class FillTransition:
    """Result of applying one fill to cash and a position."""

    cash: float
    cash_delta: float
    gross: float
    realized_pl_delta: float
    position: PositionState


class ExecutionEngine:
    """Deterministic cash, reservation, capacity, and position transitions."""

    def __init__(
        self,
        commission: float = 0.0,
        max_total_positions: int = 5,
        tolerance: float = 1e-9,
    ) -> None:
        if commission < 0:
            raise ValueError("commission must be non-negative")
        if max_total_positions <= 0:
            raise ValueError("max_total_positions must be positive")
        self.commission = float(commission)
        self.max_total_positions = int(max_total_positions)
        self.tolerance = float(tolerance)

    @staticmethod
    def _validate_price_quantity(price: float, quantity: int) -> None:
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be a positive finite number")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

    def required_cash(self, price: float, quantity: int) -> float:
        """Cash reserved or consumed by a BUY fill."""
        self._validate_price_quantity(price, quantity)
        return float(price) * int(quantity) + self.commission

    def reservation_total(self, reservations: Iterable[tuple[float, int]]) -> float:
        return sum(self.required_cash(price, quantity) for price, quantity in reservations)

    @staticmethod
    def available_cash(cash: float, reserved_cash: float) -> float:
        if not math.isfinite(cash) or not math.isfinite(reserved_cash):
            raise ValueError("cash values must be finite")
        if reserved_cash < 0:
            raise ValueError("reserved_cash must be non-negative")
        return max(0.0, float(cash) - float(reserved_cash))

    def available_slots(self, held_count: int, pending_buy_count: int) -> int:
        if held_count < 0 or pending_buy_count < 0:
            raise ValueError("position counts must be non-negative")
        return max(0, self.max_total_positions - held_count - pending_buy_count)

    def can_afford(self, cash: float, reserved_cash: float, price: float, quantity: int) -> bool:
        return self.required_cash(price, quantity) <= self.available_cash(cash, reserved_cash) + self.tolerance

    def apply_fill(
        self,
        cash: float,
        position: PositionState,
        side: Side,
        price: float,
        quantity: int,
    ) -> FillTransition:
        """Apply a fill without performing I/O."""
        self._validate_price_quantity(price, quantity)
        if not math.isfinite(cash) or cash < -self.tolerance:
            raise ValueError("cash must be non-negative and finite")
        if position.quantity < 0:
            raise ValueError("position quantity must be non-negative")

        gross = float(price) * int(quantity)
        if side == "BUY":
            required = gross + self.commission
            if required > cash + self.tolerance:
                raise ValueError(
                    f"insufficient cash: required={required:.10f}, cash={cash:.10f}"
                )
            new_quantity = position.quantity + quantity
            weighted_cost = position.avg_cost * position.quantity + gross
            new_avg_cost = weighted_cost / new_quantity
            new_cash = max(0.0, cash - required)
            new_position = PositionState(
                quantity=new_quantity,
                avg_cost=new_avg_cost,
                realized_pl=position.realized_pl,
            )
            return FillTransition(
                cash=new_cash,
                cash_delta=-required,
                gross=gross,
                realized_pl_delta=0.0,
                position=new_position,
            )

        if side != "SELL":
            raise ValueError(f"unsupported side: {side}")
        if quantity > position.quantity:
            raise ValueError(
                f"insufficient position: requested={quantity}, held={position.quantity}"
            )

        proceeds = gross - self.commission
        new_cash = cash + proceeds
        if new_cash < -self.tolerance:
            raise ValueError("fill would make cash negative")
        realized_delta = (float(price) - position.avg_cost) * quantity - self.commission
        new_position = PositionState(
            quantity=position.quantity - quantity,
            avg_cost=position.avg_cost,
            realized_pl=position.realized_pl + realized_delta,
        )
        return FillTransition(
            cash=max(0.0, new_cash),
            cash_delta=proceeds,
            gross=gross,
            realized_pl_delta=realized_delta,
            position=new_position,
        )
