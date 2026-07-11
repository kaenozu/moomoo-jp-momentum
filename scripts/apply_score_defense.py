from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


runner_path = Path("src/backtest_runner.py")
runner = runner_path.read_text(encoding="utf-8")
runner = replace_once(
    runner,
    "    evaluated.sort(key=lambda item: (-item[2].score, item[0]))",
    "    evaluated.sort(key=lambda item: (-(item[2].score or 0.0), item[0]))",
    "nullable strategy score",
)
runner_path.write_text(runner, encoding="utf-8")

test_path = Path("tests/test_backtest_ranking.py")
test = test_path.read_text(encoding="utf-8")
test += '''

class _LooseScoreStrategy:
    """Test double that can violate the runtime score annotation."""

    def __init__(self, scores: dict[str, float | None]) -> None:
        self.scores = scores

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: dict | None = None,
    ) -> StrategyResult:
        result = StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name="loose-test",
            signal_type="BUY_CANDIDATE",
            score=0.0,
            price_at_signal=indicators.close,
        )
        result.score = self.scores[indicators.code]  # type: ignore[assignment]
        return result


def test_none_score_is_treated_as_zero_instead_of_crashing() -> None:
    pairs = [
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
    ]
    strategy = _LooseScoreStrategy({"JP.0001": None, "JP.0002": 10.0})

    ranked = [code for code, _ in _rank_buy_candidates(pairs, strategy)]
    assert ranked == ["JP.0002", "JP.0001"]
'''
test_path.write_text(test, encoding="utf-8")
