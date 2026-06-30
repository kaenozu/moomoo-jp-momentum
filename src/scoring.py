"""
スコアリングモジュール

ファイルパス: src/scoring.py
何をするか: 銘柄に0〜100点のスコアをつける
なぜ存在するか: 売買候補の優先順位付けのため
関連ファイル: signals.py, indicators.py, config.py

スコアリングルール:
- トレンド: 30点
- 出来高: 20点
- 相対強度: 25点
- 流動性: 15点
- 20日高値圏: 10点
- リスク減点: 最大-30点
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .indicators import StockIndicators
from .signals import SignalResult
from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """スコア内訳"""
    trend: float = 0.0
    volume: float = 0.0
    relative_strength: float = 0.0
    liquidity: float = 0.0
    high_20d: float = 0.0
    risk_penalty: float = 0.0
    total: float = 0.0


class Scorer:
    """スコアリングクラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        scoring_config = config.get("scoring", {})
        self.enable_risk_penalty = scoring_config.get("enable_risk_penalty", True)

        # 閾値
        screening_config = config.get("screening", {})
        self.min_turnover = screening_config.get("min_turnover", 1_000_000_000)
        self.risk_daily_return_threshold = screening_config.get(
            "risk_daily_return_threshold", 8.0
        )
        self.risk_return_5d_threshold = screening_config.get(
            "risk_return_5d_threshold", 15.0
        )
        self.risk_volume_ratio_threshold = screening_config.get(
            "risk_volume_ratio_threshold", 5.0
        )

    def score_trend(self, indicators: StockIndicators) -> float:
        """
        トレンドスコア（最大30点）

        - close > ma5: 10点
        - close > ma25: 10点
        - ma5 > ma25: 10点
        """
        score = 0.0

        # close > ma5
        if (
            indicators.ma5 is not None
            and indicators.close is not None
            and indicators.close > indicators.ma5
        ):
            score += 10.0

        # close > ma25
        if (
            indicators.ma25 is not None
            and indicators.close is not None
            and indicators.close > indicators.ma25
        ):
            score += 10.0

        # ma5 > ma25
        if (
            indicators.ma5 is not None
            and indicators.ma25 is not None
            and indicators.ma5 > indicators.ma25
        ):
            score += 10.0

        return score

    def score_volume(self, indicators: StockIndicators) -> float:
        """
        出来高スコア（最大20点）

        - volume_ratio >= 1.2: 8点
        - volume_ratio >= 1.5: 追加6点
        - volume_ratio >= 2.0: 追加6点
        """
        score = 0.0
        ratio = indicators.volume_ratio

        if ratio is None:
            return score

        if ratio >= 1.2:
            score += 8.0
        if ratio >= 1.5:
            score += 6.0
        if ratio >= 2.0:
            score += 6.0

        return min(score, 20.0)

    def score_relative_strength(self, indicators: StockIndicators) -> float:
        """
        相対強度スコア（最大25点）

        暫定: return_5dで評価
        後続: ベンチマーク比較に置き換え

        - return_5d > 0: 8点
        - return_5d >= 2%: 追加8点
        - return_5d >= 5%: 追加9点
        """
        score = 0.0
        ret = indicators.return_5d

        if ret is None:
            return score

        if ret > 0:
            score += 8.0
        if ret >= 2.0:
            score += 8.0
        if ret >= 5.0:
            score += 9.0

        return min(score, 25.0)

    def score_liquidity(self, indicators: StockIndicators) -> float:
        """
        流動性スコア（最大15点）

        - turnover >= 10億円: 8点
        - turnover >= 30億円: 追加4点
        - turnover >= 100億円: 追加3点
        """
        score = 0.0
        turnover = indicators.turnover

        if turnover is None:
            return score

        turnover_oku = turnover / 100_000_000  # 億円に変換

        if turnover_oku >= 10:
            score += 8.0
        if turnover_oku >= 30:
            score += 4.0
        if turnover_oku >= 100:
            score += 3.0

        return min(score, 15.0)

    def score_high_20d(self, indicators: StockIndicators) -> float:
        """
        20日高値圏スコア（最大10点）

        - 20日高値から5%以内: 5点
        - 20日高値から2%以内: 追加5点
        """
        score = 0.0
        distance = indicators.high_20d_distance

        if distance is None:
            return score

        # 距離は負の値（現在値が高値より低い場合）
        if distance >= -5.0:
            score += 5.0
        if distance >= -2.0:
            score += 5.0

        return min(score, 10.0)

    def calculate_risk_penalty(
        self,
        indicators: StockIndicators,
    ) -> float:
        """
        リスク減点を計算する（最大-30点）

        - daily_return >= 8%: -10点
        - return_5d >= 15%: -10点
        - volume_ratio >= 5: -10点
        """
        if not self.enable_risk_penalty:
            return 0.0

        penalty = 0.0

        # 当日リターン急騰
        if (
            indicators.daily_return is not None
            and indicators.daily_return >= self.risk_daily_return_threshold
        ):
            penalty -= 10.0

        # 5日リターン急騰
        if (
            indicators.return_5d is not None
            and indicators.return_5d >= self.risk_return_5d_threshold
        ):
            penalty -= 10.0

        # 出来高急増
        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio >= self.risk_volume_ratio_threshold
        ):
            penalty -= 10.0

        return max(penalty, -30.0)

    def score(
        self,
        indicators: StockIndicators,
        signal: Optional[SignalResult] = None,
    ) -> ScoreBreakdown:
        """
        スコアを計算する

        Args:
            indicators: 指標データ
            signal: シグナル判定結果（オプション）

        Returns:
            ScoreBreakdown: スコア内訳
        """
        # データ不足の場合は判定不可
        if indicators.ma25 is None or indicators.close is None:
            return ScoreBreakdown(total=0.0)

        trend = self.score_trend(indicators)
        volume = self.score_volume(indicators)
        relative_strength = self.score_relative_strength(indicators)
        liquidity = self.score_liquidity(indicators)
        high_20d = self.score_high_20d(indicators)
        risk_penalty = self.calculate_risk_penalty(indicators)

        total = trend + volume + relative_strength + liquidity + high_20d + risk_penalty
        total = max(0.0, min(100.0, total))  # 0〜100の範囲に丸める

        return ScoreBreakdown(
            trend=trend,
            volume=volume,
            relative_strength=relative_strength,
            liquidity=liquidity,
            high_20d=high_20d,
            risk_penalty=risk_penalty,
            total=total,
        )


def score_batch(
    indicators_list: list[StockIndicators],
    signal_results: list[SignalResult],
    config: Config,
) -> list[tuple[StockIndicators, SignalResult, ScoreBreakdown]]:
    """
    複数銘柄のスコアを一括計算する

    Args:
        indicators_list: 指標のリスト
        signal_results: シグナル判定結果のリスト
        config: 設定オブジェクト

    Returns:
        list[tuple]: (指標, シグナル結果, スコア)のタプルリスト
    """
    scorer = Scorer(config)
    results = []

    for indicators, signal in zip(indicators_list, signal_results):
        score_breakdown = scorer.score(indicators, signal)
        results.append((indicators, signal, score_breakdown))

    logger.info(
        f"スコアリング完了: {len(results)}銘柄 "
        f"(平均スコア: {sum(r[2].total for r in results) / len(results):.1f})"
    )

    return results
