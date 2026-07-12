"""
ETFローテーション戦術

ファイルパス: src/strategies/etf_rotation.py
何をするか: 主要ETFを比較し、相対的に強いETFを候補化する
なぜ存在するか: セクターローテーションでリターンを狙うため
関連ファイル: base.py, signals.py, scoring.py
"""

from typing import Optional

from ..indicators import StockIndicators
from ..config import Config
from . import BaseStrategy, StrategyResult, StrategyRegistry


# 主要ETFコード
MAJOR_ETFS = [
    "JP.2559",  # MAXIS全世界株式（オール・カントリー）
    "JP.1306",  # TOPIX連動ETF
    "JP.1320",  # iFreeETF日経225
    "JP.2558",  # MAXIS米国株式（S&P500）
    "JP.2563",  # iFreeNEXT TOPIX連動
]


@StrategyRegistry.register("etf_rotation")
class ETFRotationStrategy(BaseStrategy):
    """ETFローテーション戦術"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.strategy_name = "etf_rotation"

        # 設定値
        screening = config.get("screening", {})
        self.min_history_days = screening.get("min_history_days", 25)

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """ETFローテーション戦術で評価する"""
        result = StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name=self.strategy_name,
            signal_type="EXCLUDE",
            price_at_signal=indicators.close,
        )

        # ETFかどうかチェック
        if not self._is_etf(indicators.code):
            result.signal_type = "EXCLUDE"
            result.reason = "除外: ETFではない"
            return result

        # データ不足チェック
        if indicators.history_days < self.min_history_days:
            result.reason = f"除外: データ不足（{indicators.history_days}日分）"
            return result

        if indicators.close is None or indicators.close <= 0:
            result.reason = "除外: 現在値が無効"
            return result

        # ベンチマーク超過リターンの計算
        if benchmark_returns:
            result.return_5d_vs_benchmark = self._calc_vs_benchmark(
                indicators.return_5d,
                benchmark_returns.get("return_5d"),
            )

        # ETFローテーション条件
        buy_reasons = []
        is_buy_candidate = True

        # 条件1: close > MA25（上昇トレンド）
        if indicators.ma25 is not None and indicators.close > indicators.ma25:
            buy_reasons.append("MA25之上（上昇トレンド）")
        else:
            is_buy_candidate = False

        # 条件2: MA5 > MA25（トレンド良好）
        if (
            indicators.ma5 is not None
            and indicators.ma25 is not None
            and indicators.ma5 > indicators.ma25
        ):
            buy_reasons.append("MA5>MA25（トレンド良好）")
        else:
            is_buy_candidate = False

        # 条件3: 5日リターンがプラス
        if indicators.return_5d is not None and indicators.return_5d > 0:
            buy_reasons.append(f"5日リターン{indicators.return_5d:.1f}%")
        else:
            is_buy_candidate = False

        # 条件4: 設定ベンチマークを上回るリターン
        if result.return_5d_vs_benchmark is not None and result.return_5d_vs_benchmark > 0:
            buy_reasons.append(f"ベンチマーク比+{result.return_5d_vs_benchmark:.1f}%")
        else:
            # ベンチマーク比が劣後しても、絶対リターンが良ければ候補にする
            pass

        # 判定
        if is_buy_candidate:
            result.signal_type = "BUY_CANDIDATE"
            result.reason = "買い候補: " + ", ".join(buy_reasons)
            return result

        # 監視候補
        watch_reasons = []

        if (
            indicators.close is not None
            and indicators.ma25 is not None
            and indicators.close > indicators.ma25
            and indicators.ma5 is not None
            and indicators.ma5 <= indicators.ma25
        ):
            watch_reasons.append("MA25之上だがMA5之下")

        if indicators.return_5d is not None and indicators.return_5d > 0:
            watch_reasons.append("リターンはプラスだがトレンド未確立")

        if watch_reasons:
            result.signal_type = "WATCH"
            result.reason = "監視候補: " + ", ".join(watch_reasons)
            return result

        # 除外
        exclude_reasons = []

        if indicators.ma25 is not None and indicators.close < indicators.ma25:
            exclude_reasons.append("MA25之下（下降トレンド）")

        if indicators.return_5d is not None and indicators.return_5d < -5:
            exclude_reasons.append(f"5日リターン{indicators.return_5d:.1f}%（大幅下落）")

        if exclude_reasons:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: " + ", ".join(exclude_reasons)
        else:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: 買い候補条件を充足せず"

        return result
