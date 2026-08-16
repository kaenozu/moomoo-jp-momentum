from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionCostModel:
    """Explicit proportional costs for deterministic research execution."""

    slippage_bps: float = 0.0
    commission_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.commission_bps < 0:
            raise ValueError("execution costs must not be negative")

    def fill(
        self, side: str, reference_price: float, quantity: int
    ) -> tuple[float, float]:
        if reference_price <= 0 or quantity <= 0:
            raise ValueError("fill price and quantity must be positive")
        slippage = self.slippage_bps / 10000
        if side == "BUY":
            price = reference_price * (1 + slippage)
        elif side == "SELL":
            price = reference_price * (1 - slippage)
        else:
            raise ValueError(f"unsupported side: {side}")
        fee = price * quantity * self.commission_bps / 10000
        return price, fee


@dataclass(frozen=True, slots=True)
class Exposure:
    target: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.target <= 1.0:
            raise ValueError("exposure target must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    max_positions: int = 10
    max_exposure: float = 1.0
    max_drawdown: float | None = None

    def __post_init__(self) -> None:
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")
        if not 0.0 <= self.max_exposure <= 1.0:
            raise ValueError("max_exposure must be between 0 and 1")
        if self.max_drawdown is not None and not 0.0 < self.max_drawdown <= 1.0:
            raise ValueError("max_drawdown must be between 0 and 1")
