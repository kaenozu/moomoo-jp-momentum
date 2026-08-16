from datetime import date

import pytest

from src.momentum_v2.allocator import EqualWeightAllocator
from src.momentum_v2.contracts import CanonicalBar, MarketSnapshot
from src.momentum_v2.engine import SimulationEngine
from src.momentum_v2.experiment import Experiment
from src.momentum_v2.metrics import METRIC_NAMES, calculate_metrics
from src.momentum_v2.portfolio_rules import Exposure, RiskPolicy
from src.momentum_v2.portfolio_rules import ExecutionCostModel


class FixedStrategy:
    name = "fixed"

    def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
        return {bar.code: bar.close for bar in snapshot.bars}


def bar(code: str, day: int, open_price: float, close: float) -> CanonicalBar:
    return CanonicalBar(
        code,
        date(2026, 1, day),
        open_price,
        max(open_price, close),
        min(open_price, close),
        close,
    )


def test_canonical_snapshot_is_sorted_and_rejects_mixed_dates() -> None:
    snapshot = MarketSnapshot.from_bars([bar("B", 1, 10, 11), bar("A", 1, 20, 21)])
    assert [item.code for item in snapshot.bars] == ["A", "B"]
    with pytest.raises(ValueError, match="same date"):
        MarketSnapshot.from_bars([bar("A", 1, 10, 11), bar("A", 2, 10, 11)])


def test_allocator_is_deterministic_and_respects_exposure_and_position_limit() -> None:
    allocator = EqualWeightAllocator(
        Exposure(0.6), RiskPolicy(max_positions=2, max_exposure=0.8)
    )
    assert allocator.weights({"B": 2.0, "A": 2.0, "C": 1.0}) == {"A": 0.3, "B": 0.3}


def test_simulation_fills_next_day_open_without_external_io() -> None:
    snapshots = tuple(
        MarketSnapshot.from_bars([bar("A", day, 100 + day, 101 + day)])
        for day in range(1, 4)
    )
    result = SimulationEngine().run(snapshots, FixedStrategy(), initial_cash=1000.0)
    assert result.portfolio.fills[0].filled_at == "2026-01-02"
    assert result.portfolio.fills[0].price == 102
    assert result.equity_curve[-1][1] > result.equity_curve[0][1]


def test_metrics_include_risk_and_benchmark_dimensions() -> None:
    metrics = calculate_metrics(
        [100.0, 101.0, 99.0, 102.0],
        benchmark_equity=[100.0, 100.5, 99.5, 101.0],
        turnover=0.2,
        exposure=0.5,
        periods_per_year=3,
    )
    assert tuple(metrics) == METRIC_NAMES
    assert metrics["excess_cagr"] > 0
    assert metrics["max_drawdown"] > 0


def test_experiment_requires_reproducibility_metadata() -> None:
    experiment = Experiment(
        "exp-1",
        "fixed",
        date(2026, 1, 1),
        date(2026, 1, 3),
        "git-sha",
        "dataset-sha",
        "config-sha",
    )
    assert experiment.end >= experiment.start


def test_execution_costs_are_applied_to_buy_and_sell_fills() -> None:
    costs = ExecutionCostModel(slippage_bps=10.0, commission_bps=5.0)
    buy_price, buy_fee = costs.fill("BUY", 100.0, 10)
    sell_price, sell_fee = costs.fill("SELL", 100.0, 10)

    assert buy_price == pytest.approx(100.10)
    assert sell_price == pytest.approx(99.90)
    assert buy_fee == pytest.approx(0.5005)
    assert sell_fee == pytest.approx(0.4995)


def test_risk_policy_rejects_invalid_drawdown_limit() -> None:
    with pytest.raises(ValueError, match="drawdown"):
        RiskPolicy(max_drawdown=1.1)


def test_drawdown_limit_stops_new_exposure_after_a_loss() -> None:
    class RiskStrategy:
        name = "risk"

        def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
            return {"A": 1.0}

    snapshots = tuple(
        MarketSnapshot.from_bars(
            [CanonicalBar("A", date(2026, 1, day), open_price, high, low, close)]
        )
        for day, open_price, high, low, close in (
            (1, 100.0, 101.0, 99.0, 100.0),
            (2, 100.0, 101.0, 49.0, 50.0),
            (3, 50.0, 51.0, 49.0, 50.0),
        )
    )
    allocator = EqualWeightAllocator(
        Exposure(1.0), RiskPolicy(max_positions=1, max_drawdown=0.2)
    )
    result = SimulationEngine(allocator).run(
        snapshots, RiskStrategy(), initial_cash=1000.0
    )

    assert any(fill.side == "SELL" for fill in result.portfolio.fills)


def test_simulation_partially_fills_buy_when_next_open_exceeds_budget() -> None:
    class BuyStrategy:
        name = "buy"

        def scores(self, snapshot: MarketSnapshot) -> dict[str, float]:
            return {"A": 1.0}

    snapshots = (
        MarketSnapshot.from_bars(
            [CanonicalBar("A", date(2026, 2, 1), 100.0, 101.0, 99.0, 100.0)]
        ),
        MarketSnapshot.from_bars(
            [CanonicalBar("A", date(2026, 2, 2), 200.0, 201.0, 199.0, 200.0)]
        ),
    )
    result = SimulationEngine().run(snapshots, BuyStrategy(), initial_cash=1000.0)

    assert result.portfolio.fills[0].quantity > 0
    assert result.portfolio.cash >= 0
