from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import CanonicalBar


@dataclass(frozen=True, slots=True)
class Position:
    quantity: int = 0
    average_cost: float = 0.0


@dataclass(frozen=True, slots=True)
class OrderIntent:
    code: str
    side: str
    quantity: int
    signal_date: str


@dataclass(frozen=True, slots=True)
class Fill:
    code: str
    side: str
    quantity: int
    price: float
    filled_at: str


@dataclass(slots=True)
class MemoryPortfolio:
    initial_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[OrderIntent] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = self.initial_cash

    def position(self, code: str) -> Position:
        return self.positions.get(code, Position())

    def apply_fill(self, fill: Fill) -> None:
        current = self.position(fill.code)
        notional = fill.price * fill.quantity
        if fill.side == "BUY":
            if notional > self.cash + 1e-9:
                raise ValueError("buy fill exceeds available cash")
            quantity = current.quantity + fill.quantity
            average = ((current.quantity * current.average_cost) + notional) / quantity
            self.cash -= notional
            self.positions[fill.code] = Position(quantity, average)
        elif fill.side == "SELL":
            if fill.quantity > current.quantity:
                raise ValueError("sell fill exceeds available position")
            self.cash += notional
            quantity = current.quantity - fill.quantity
            self.positions[fill.code] = Position(
                quantity, current.average_cost if quantity else 0.0
            )
        else:
            raise ValueError(f"unsupported side: {fill.side}")
        self.fills.append(fill)

    def equity(self, bars: tuple[CanonicalBar, ...]) -> float:
        prices = {bar.code: bar.close for bar in bars}
        return self.cash + sum(
            self.position(code).quantity * prices.get(code, 0.0)
            for code in self.positions
        )
