"""Regression tests for deterministic backtest candidate ranking."""

from src.backtest_runner import _rank_buy_candidates
from src.indicators import StockIndicators
from src.strategies import StrategyResult


def _indicator(code: str) -> StockIndicators:
    return StockIndicators(
        code=code,
        name=code,
        date="2026-07-01",
        close=1000.0,
        open=995.0,
        high=1010.0,
        low=990.0,
        ma25=980.0,
        history_days=30,
    )


class _ScoreStrategy:
    def __init__(
        self,
        scores: dict[str, float],
        signal_types: dict[str, str] | None = None,
    ) -> None:
        self.scores = scores
        self.signal_types = signal_types or {}

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: dict | None = None,
    ) -> StrategyResult:
        return StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name="test",
            signal_type=self.signal_types.get(
                indicators.code,
                "BUY_CANDIDATE",
            ),
            score=self.scores[indicators.code],
            price_at_signal=indicators.close,
        )


def _codes(
    pairs: list[tuple[str, StockIndicators]],
    strategy: _ScoreStrategy,
) -> list[str]:
    return [code for code, _ in _rank_buy_candidates(pairs, strategy)]


def test_higher_score_wins_even_when_database_order_is_lower_first() -> None:
    pairs = [
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
    ]
    strategy = _ScoreStrategy({"JP.0001": 70.0, "JP.0002": 95.0})

    assert _codes(pairs, strategy) == ["JP.0002", "JP.0001"]


def test_equal_scores_use_symbol_code_as_stable_tie_breaker() -> None:
    pairs = [
        ("JP.9000", _indicator("JP.9000")),
        ("JP.1000", _indicator("JP.1000")),
        ("JP.5000", _indicator("JP.5000")),
    ]
    strategy = _ScoreStrategy(
        {"JP.9000": 80.0, "JP.1000": 80.0, "JP.5000": 80.0}
    )

    assert _codes(pairs, strategy) == ["JP.1000", "JP.5000", "JP.9000"]


def test_non_buy_results_do_not_consume_ranked_slots() -> None:
    pairs = [
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
        ("JP.0003", _indicator("JP.0003")),
    ]
    strategy = _ScoreStrategy(
        {"JP.0001": 100.0, "JP.0002": 90.0, "JP.0003": 80.0},
        {"JP.0001": "WATCH", "JP.0002": "EXCLUDE"},
    )

    assert _codes(pairs, strategy) == ["JP.0003"]


def test_ranking_is_independent_of_input_order() -> None:
    pairs = [
        ("JP.0003", _indicator("JP.0003")),
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
    ]
    strategy = _ScoreStrategy(
        {"JP.0001": 75.0, "JP.0002": 95.0, "JP.0003": 85.0}
    )

    expected = ["JP.0002", "JP.0003", "JP.0001"]
    assert _codes(pairs, strategy) == expected
    assert _codes(list(reversed(pairs)), strategy) == expected


def test_limited_slots_take_only_the_top_n_ranked_candidates() -> None:
    pairs = [
        ("JP.0004", _indicator("JP.0004")),
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0003", _indicator("JP.0003")),
        ("JP.0002", _indicator("JP.0002")),
    ]
    strategy = _ScoreStrategy(
        {
            "JP.0001": 75.0,
            "JP.0002": 95.0,
            "JP.0003": 85.0,
            "JP.0004": 65.0,
        }
    )

    ranked = _codes(pairs, strategy)
    assert ranked[:2] == ["JP.0002", "JP.0003"]
