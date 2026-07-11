"""
低リスク品質戦術

ファイルパス: src/strategies/quality_low_risk.py
何をするか: 急騰銘柄を避け、出来高・流動性・安定上昇を重視する戦術
なぜ存在するか: リスクを抑えつつ堅実にリターンを狙うため
関連ファイル: base.py, signals.py, scoring.py
"""

from typing import Optional

from ..indicators import StockIndicators
from ..config import Config
from . import BaseStrategy, StrategyResult, StrategyRegistry


@StrategyRegistry.register("quality_low_risk")
class QualityLowRiskStrategy(BaseStrategy):
    """低リスク品質戦術"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.strategy_name = "quality_low_risk"

        # 設定値
        screening = config.get("screening", {})
        self.min_turnover = screening.get("min_turnover", 1_000_000_000)
        self.min_volume_ratio = screening.get("min_volume_ratio", 1.0)
        self.min_history_days = screening.get("min_history_days", 25)
        self.max_daily_return = 5.0  # 急騰銘柄を避ける閾値
        self.max_return_5d = 10.0  # 5日リターンの上限

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """低リスク品質戦術で評価する"""
        result = StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name=self.strategy_name,
            signal_type="EXCLUDE",
            price_at_signal=indicators.close,
        )

        # データ不足チェック
        if indicators.history_days < self.min_history_days:
            result.reason = f"除外: データ不足（{indicators.history_days}日分）"
            return result

        if indicators.close is None or indicators.close <= 0:
            result.reason = "除外: 現在値が無効"
            return result

        if indicators.ma25 is None:
            result.reason = "除外: MA25なし（データ不足）"
            return result

        if indicators.turnover is None or indicators.turnover <= 0:
            result.reason = "除外: 売買代金が無効"
            return result

        # ベンチマーク超過リターンの計算
        if benchmark_returns:
            result.return_5d_vs_benchmark = self._calc_vs_benchmark(
                indicators.return_5d,
                benchmark_returns.get("return_5d"),
            )

        # 急騰チェック（この戦術では回避）
        if (
            indicators.daily_return is not None
            and indicators.daily_return >= self.max_daily_return
        ):
            result.signal_type = "WATCH"
            result.reason = f"監視: 当日リターン{indicators.daily_return:.1f}%（急騰のため様子見）"
            result.risk_warnings.append(f"当日急騰{indicators.daily_return:.1f}%")
            return result

        if (
            indicators.return_5d is not None
            and indicators.return_5d >= self.max_return_5d
        ):
            result.signal_type = "WATCH"
            result.reason = f"監視: 5日リターン{indicators.return_5d:.1f}%（急騰のため様子見）"
            result.risk_warnings.append(f"5日急騰{indicators.return_5d:.1f}%")
            return result

        # 買い候補条件チェック
        buy_reasons = []
        is_buy_candidate = True

        # 条件1: close > MA25（安定上昇）
        if indicators.close > indicators.ma25:
            buy_reasons.append("MA25之上（安定上昇）")
        else:
            is_buy_candidate = False

        # 条件2: MA5 > MA25（トレンド良好）
        if (
            indicators.ma5 is not None
            and indicators.ma5 > indicators.ma25
        ):
            buy_reasons.append("MA5>MA25（トレンド良好）")
        else:
            is_buy_candidate = False

        # 条件3: 出来高が一定以上（流動性確保）
        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio >= self.min_volume_ratio
        ):
            buy_reasons.append(f"出来高{indicators.volume_ratio:.1f}倍（流動性確保）")
        else:
            is_buy_candidate = False

        # 条件4: 売買代金が一定以上
        if indicators.turnover >= self.min_turnover:
            turnover_oku = indicators.turnover / 100_000_000
            buy_reasons.append(f"売買代金{turnover_oku:.1f}億円")
        else:
            is_buy_candidate = False

        # 条件5: 5日リターンがプラス（安定上昇）
        ret_5d = result.return_5d_vs_benchmark if result.return_5d_vs_benchmark is not None else indicators.return_5d
        if ret_5d is not None and ret_5d > 0:
            buy_reasons.append(f"5日リターン{ret_5d:.1f}%（安定上昇）")
        else:
            is_buy_candidate = False

        # 条件6: ボラティリティが低い（出来高比が極端に大きくない）
        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio < 3.0
        ):
            buy_reasons.append("出来高安定（ボラティリティ低め）")
        else:
            is_buy_candidate = False

        # 判定
        if is_buy_candidate:
            result.signal_type = "BUY_CANDIDATE"
            result.reason = "買い候補: " + ", ".join(buy_reasons)
            return result

        # 監視候補チェック
        watch_reasons = []

        if (
            indicators.close > indicators.ma25
            and indicators.ma5 is not None
            and indicators.ma5 <= indicators.ma25
        ):
            watch_reasons.append("MA25之上だがMA5之下")

        if (
            indicators.return_5d is not None
            and indicators.return_5d > 0
            and indicators.volume_ratio is not None
            and indicators.volume_ratio < self.min_volume_ratio
        ):
            watch_reasons.append("リターンは良いが出来高不足")

        if watch_reasons:
            result.signal_type = "WATCH"
            result.reason = "監視候補: " + ", ".join(watch_reasons)
            return result

        # 除外
        exclude_reasons = []

        if indicators.close < indicators.ma25:
            exclude_reasons.append("終値が25日移動平均線を下回っています")

        if indicators.turnover < self.min_turnover * 0.3:
            exclude_reasons.append("売買代金が基準未満です")

        if exclude_reasons:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: " + ", ".join(exclude_reasons)
        else:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: 買い候補条件を充足せず"

        return result
