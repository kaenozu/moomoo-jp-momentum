from __future__ import annotations

from dataclasses import dataclass

from .allocator import EqualWeightAllocator
from .contracts import MarketSnapshot, Strategy
from .portfolio import Fill, MemoryPortfolio, OrderIntent


@dataclass(frozen=True, slots=True)
class SimulationResult:
    portfolio: MemoryPortfolio
    equity_curve: tuple[tuple[str, float], ...]


class SimulationEngine:
    """Deterministic, in-memory next-day-open simulation kernel."""

    def __init__(self, allocator: EqualWeightAllocator | None = None) -> None:
        self.allocator = allocator or EqualWeightAllocator()

    def run(
        self,
        snapshots: tuple[MarketSnapshot, ...],
        strategy: Strategy,
        *,
        initial_cash: float,
    ) -> SimulationResult:
        if not snapshots:
            raise ValueError("simulation requires snapshots")
        portfolio = MemoryPortfolio(initial_cash)
        pending: list[OrderIntent] = []
        ordered = tuple(sorted(snapshots, key=lambda snapshot: snapshot.date))
        for index, snapshot in enumerate(ordered):
            bars = {bar.code: bar for bar in snapshot.bars}
            for order in pending:
                bar = bars.get(order.code)
                if bar is None:
                    continue
                portfolio.apply_fill(
                    Fill(
                        order.code,
                        order.side,
                        order.quantity,
                        bar.open,
                        snapshot.date.isoformat(),
                    )
                )
            pending = []
            equity = portfolio.equity(snapshot.bars)
            portfolio.equity_curve.append((snapshot.date.isoformat(), equity))
            if index == len(ordered) - 1:
                continue
            weights = self.allocator.weights(strategy.scores(snapshot))
            pending = self._rebalance_orders(snapshot, portfolio, weights, initial_cash)
            pending.sort(key=lambda order: (order.side == "BUY", order.code))
            portfolio.orders.extend(pending)
        return SimulationResult(portfolio, tuple(portfolio.equity_curve))

    @staticmethod
    def _rebalance_orders(
        snapshot: MarketSnapshot,
        portfolio: MemoryPortfolio,
        weights: dict[str, float],
        initial_cash: float,
    ) -> list[OrderIntent]:
        prices = {bar.code: bar.close for bar in snapshot.bars}
        # Keep a small deterministic cash buffer for next-day-open gaps.  This is
        # a risk rule, not an assumption that close and next open are identical.
        target_values = {
            code: initial_cash * weight * 0.90 for code, weight in weights.items()
        }
        codes = sorted(set(prices) | set(portfolio.positions))
        intents: list[OrderIntent] = []
        for code in codes:
            price = prices.get(code)
            if price is None or price <= 0:
                continue
            current = portfolio.position(code).quantity
            target = int(target_values.get(code, 0.0) / price)
            delta = target - current
            if delta:
                intents.append(
                    OrderIntent(
                        code=code,
                        side="BUY" if delta > 0 else "SELL",
                        quantity=abs(delta),
                        signal_date=snapshot.date.isoformat(),
                    )
                )
        return intents
