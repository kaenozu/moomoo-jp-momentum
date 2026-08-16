from datetime import date

from src.momentum_v2.contracts import CanonicalBar, MarketSnapshot
from src.momentum_v2.metrics import METRIC_NAMES
from src.momentum_v2.tournament import StrategyTournament


def make_snapshots() -> tuple[MarketSnapshot, ...]:
    prices = ((100, 100), (102, 101), (105, 103), (107, 102), (110, 106))
    snapshots = []
    for day, (alpha, benchmark) in enumerate(prices, start=1):
        snapshots.append(
            MarketSnapshot.from_bars(
                [
                    CanonicalBar(
                        "JP.A", date(2026, 1, day), alpha, alpha + 1, alpha - 1, alpha
                    ),
                    CanonicalBar(
                        "JP.BENCH",
                        date(2026, 1, day),
                        benchmark,
                        benchmark + 1,
                        benchmark - 1,
                        benchmark,
                    ),
                ],
                benchmark="JP.BENCH",
            )
        )
    return tuple(snapshots)


def test_tournament_runs_all_requested_strategies_on_same_snapshots() -> None:
    rows = StrategyTournament(max_positions=2, benchmark_code="JP.BENCH").run(
        make_snapshots()
    )

    assert [row.strategy for row in rows] == [
        "buy_hold",
        "equal_weight",
        "momentum",
        "volatility_adjusted_momentum",
        "trend_momentum",
        "benchmark_alpha",
    ]
    assert all(tuple(row.metrics) == METRIC_NAMES for row in rows)
    assert all("cagr" in row.metrics and "max_drawdown" in row.metrics for row in rows)


def test_tournament_does_not_choose_a_strategy_or_claim_validation() -> None:
    rows = StrategyTournament(max_positions=2, benchmark_code="JP.BENCH").run(
        make_snapshots()
    )

    assert all(row.metrics["turnover"] >= 0 for row in rows)
    assert all(row.metrics["exposure"] <= 1.0 for row in rows)


def test_tournament_can_split_in_sample_and_out_of_sample_periods() -> None:
    tournament = StrategyTournament(max_positions=2, benchmark_code="JP.BENCH")
    result = tournament.run_oos(make_snapshots(), split_date=date(2026, 1, 4))

    assert result.split_date == date(2026, 1, 4)
    assert result.in_sample_observations == 3
    assert result.out_of_sample_observations == 2
    assert [row.strategy for row in result.out_of_sample] == [
        "buy_hold",
        "equal_weight",
        "momentum",
        "volatility_adjusted_momentum",
        "trend_momentum",
        "benchmark_alpha",
    ]
