"""
バックテスト候補ランキングの回帰テスト。

ファイルパス: tests/test_backtest_ranking.py
何をするか: DB入力順ではなくscore DESC・code ASCでBUY候補を選ぶことを検証する
なぜ存在するか: max_positions到達時に低スコア候補が先に枠を消費する不具合を防ぐため
関連ファイル: src/backtest_runner.py, src/ranking.py, src/scoring.py
"""

from src.backtest_runner import _rank_buy_candidates
from src.indicators import StockIndicators
from src.scoring import ScoreBreakdown
from src.strategies import StrategyResult


def _indicator(code: str) -> StockIndicators:
    """ランキングテスト用の指標を作る。"""
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


class _BuyStrategy:
    """全銘柄をBUY候補として返すテスト戦略。"""

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: dict | None = None,
    ) -> StrategyResult:
        """BUY_CANDIDATEを返す。"""
        return StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name="test",
            signal_type="BUY_CANDIDATE",
            price_at_signal=indicators.close,
            risk_warnings=["warning"] if indicators.code == "JP.WARN" else [],
        )


class _ScoreByCode:
    """銘柄コードごとの固定スコアを返すテストスコアラー。"""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def score(
        self,
        indicators: StockIndicators,
        signal: object | None = None,
    ) -> ScoreBreakdown:
        """固定スコアをScoreBreakdownで返す。"""
        return ScoreBreakdown(total=self.scores[indicators.code])


class _RequireSignalScorer:
    """戦略評価結果がスコアラーへ渡ることを検証する。"""

    def score(
        self,
        indicators: StockIndicators,
        signal: StrategyResult | None = None,
    ) -> ScoreBreakdown:
        assert signal is not None
        assert signal.code == indicators.code
        if indicators.code == "JP.WARN":
            assert signal.risk_warnings == ["warning"]
        return ScoreBreakdown(total=80.0)


def test_score_order_replaces_database_input_order() -> None:
    """修正前のDB入力順と修正後のスコア順が異なることを確認する。"""
    pairs = [
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
        ("JP.0003", _indicator("JP.0003")),
    ]
    database_order = [code for code, _ in pairs]

    ranked = _rank_buy_candidates(
        pairs,
        _BuyStrategy(),
        _ScoreByCode({"JP.0001": 60.0, "JP.0002": 95.0, "JP.0003": 80.0}),
    )
    ranked_order = [code for code, _, _ in ranked]

    assert database_order == ["JP.0001", "JP.0002", "JP.0003"]
    assert ranked_order == ["JP.0002", "JP.0003", "JP.0001"]
    assert ranked_order != database_order


def test_equal_scores_use_code_ascending_tie_breaker() -> None:
    """同点候補はcode昇順で安定して並ぶことを確認する。"""
    pairs = [
        ("JP.9000", _indicator("JP.9000")),
        ("JP.1000", _indicator("JP.1000")),
        ("JP.5000", _indicator("JP.5000")),
    ]

    ranked = _rank_buy_candidates(
        pairs,
        _BuyStrategy(),
        _ScoreByCode({"JP.9000": 80.0, "JP.1000": 80.0, "JP.5000": 80.0}),
    )

    assert [code for code, _, _ in ranked] == [
        "JP.1000",
        "JP.5000",
        "JP.9000",
    ]


def test_ranking_is_independent_of_input_order() -> None:
    """入力順を反転してもランキング結果が変わらないことを確認する。"""
    pairs = [
        ("JP.0003", _indicator("JP.0003")),
        ("JP.0001", _indicator("JP.0001")),
        ("JP.0002", _indicator("JP.0002")),
    ]
    scorer = _ScoreByCode(
        {"JP.0001": 75.0, "JP.0002": 95.0, "JP.0003": 85.0}
    )

    forward = _rank_buy_candidates(pairs, _BuyStrategy(), scorer)
    reverse = _rank_buy_candidates(list(reversed(pairs)), _BuyStrategy(), scorer)

    expected = ["JP.0002", "JP.0003", "JP.0001"]
    assert [code for code, _, _ in forward] == expected
    assert [code for code, _, _ in reverse] == expected


def test_strategy_result_is_passed_to_backtest_scorer() -> None:
    ranked = _rank_buy_candidates(
        [("JP.WARN", _indicator("JP.WARN"))],
        _BuyStrategy(),
        _RequireSignalScorer(),
    )

    assert [code for code, _, _ in ranked] == ["JP.WARN"]
