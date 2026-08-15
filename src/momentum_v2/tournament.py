from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .allocator import EqualWeightAllocator, ScoreWeightAllocator
from .contracts import MarketSnapshot, Strategy
from .engine import SimulationEngine
from .metrics import calculate_metrics
from .portfolio_rules import Exposure, RiskPolicy


class BuyHoldStrategy:
    name = "buy_hold"
    rebalance = False

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        return {bar.code: 1.0 for bar in snapshot.bars}


class EqualWeightStrategy:
    name = "equal_weight"
    rebalance = True

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        return {bar.code: 1.0 for bar in snapshot.bars}


class MomentumStrategy:
    name = "momentum"
    rebalance = True

    def __init__(self) -> None:
        self.previous: dict[str, float] = {}

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        scores = {
            bar.code: bar.close / self.previous[bar.code] - 1.0
            for bar in snapshot.bars
            if bar.code in self.previous and bar.close > self.previous[bar.code]
        }
        self.previous.update({bar.code: bar.close for bar in snapshot.bars})
        return scores


class VolatilityAdjustedMomentumStrategy(MomentumStrategy):
    name = "volatility_adjusted_momentum"

    def __init__(self) -> None:
        super().__init__()
        self.returns: dict[str, list[float]] = {}

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        raw = super().scores(snapshot)
        adjusted: dict[str, float] = {}
        for code, momentum in raw.items():
            previous = self.previous.get(code)
            if previous is None:
                continue
            history = self.returns.setdefault(code, [])
            history.append(momentum)
            volatility = max((sum((value - sum(history) / len(history)) ** 2 for value in history) / len(history)) ** 0.5, 1e-9)
            adjusted[code] = momentum / volatility
        return adjusted


class TrendMomentumStrategy(MomentumStrategy):
    name = "trend_momentum"

    def __init__(self, window: int = 3) -> None:
        super().__init__()
        self.window = window
        self.history: dict[str, list[float]] = {}

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        raw = super().scores(snapshot)
        result: dict[str, float] = {}
        for bar in snapshot.bars:
            history = self.history.setdefault(bar.code, [])
            history.append(bar.close)
            if bar.code in raw and len(history) >= self.window and bar.close > sum(history[-self.window:]) / self.window:
                result[bar.code] = raw[bar.code]
        return result


class BenchmarkAlphaStrategy:
    name = "benchmark_alpha"
    rebalance = True

    def __init__(self, benchmark_code: str, alpha_weight: float = 0.3) -> None:
        self.benchmark_code = benchmark_code
        self.alpha_weight = alpha_weight

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        alpha_codes = sorted(bar.code for bar in snapshot.bars if bar.code != self.benchmark_code)
        if not alpha_codes:
            return {self.benchmark_code: 1.0}
        alpha_score = self.alpha_weight / len(alpha_codes)
        return {self.benchmark_code: 1.0 - self.alpha_weight, **{code: alpha_score for code in alpha_codes}}


@dataclass(frozen=True, slots=True)
class TournamentRow:
    strategy: str
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class StrategyTournament:
    initial_cash: float = 100000.0
    max_positions: int = 10
    benchmark_code: str | None = None

    def run(self, snapshots: tuple[MarketSnapshot, ...]) -> tuple[TournamentRow, ...]:
        if not snapshots:
            raise ValueError("tournament requires snapshots")
        factories: tuple[Callable[[], Strategy], ...] = (
            BuyHoldStrategy,
            EqualWeightStrategy,
            MomentumStrategy,
            VolatilityAdjustedMomentumStrategy,
            TrendMomentumStrategy,
        )
        strategies: list[Strategy] = [factory() for factory in factories]
        if self.benchmark_code:
            strategies.append(BenchmarkAlphaStrategy(self.benchmark_code))
        rows: list[TournamentRow] = []
        benchmark = self._benchmark_curve(snapshots)
        for strategy in strategies:
            allocator = (ScoreWeightAllocator if strategy.name == "benchmark_alpha" else EqualWeightAllocator)(
                Exposure(1.0), RiskPolicy(max_positions=self.max_positions, max_exposure=1.0)
            )
            result = SimulationEngine(allocator).run(
                snapshots, strategy, initial_cash=self.initial_cash
            )
            curve = [value for _, value in result.equity_curve]
            turnover = sum(fill.quantity * fill.price for fill in result.portfolio.fills) / self.initial_cash
            exposure = min(1.0, sum(fill.quantity * fill.price for fill in result.portfolio.fills) / max(1.0, self.initial_cash))
            rows.append(
                TournamentRow(
                    strategy.name,
                    calculate_metrics(curve, benchmark_equity=benchmark, turnover=turnover, exposure=exposure),
                )
            )
        return tuple(rows)

    def _benchmark_curve(self, snapshots: tuple[MarketSnapshot, ...]) -> list[float] | None:
        if self.benchmark_code is None:
            return None
        benchmark_prices = [
            next((bar.close for bar in snapshot.bars if bar.code == self.benchmark_code), None)
            for snapshot in sorted(snapshots, key=lambda item: item.date)
        ]
        prices = [price for price in benchmark_prices if price is not None]
        if len(prices) != len(benchmark_prices):
            return None
        first = prices[0]
        return [price / first * self.initial_cash for price in prices]


__all__ = ["StrategyTournament", "TournamentRow"]
