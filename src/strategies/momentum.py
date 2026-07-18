"""
モメンタム戦術

ファイルパス: src/strategies/momentum.py
何をするか: MA上、出来高増、20日高値圏、相対強度を重視する戦術
なぜ存在するか: 短中期の上昇トレンドを捉えるため
関連ファイル: base.py, signals.py, scoring.py, ../filters/quality_risk_filter.py
"""

from typing import Optional

from ..config import Config
from ..filters import QualityRiskFilter
from ..indicators import StockIndicators
from . import BaseStrategy, StrategyRegistry, StrategyResult


@StrategyRegistry.register("momentum")
class MomentumStrategy(BaseStrategy):
    """モメンタム戦術"""

    def __init__(self, config: Config):
        """設定からモメンタム条件と任意の品質フィルターを初期化する。"""
        super().__init__(config)
        self.strategy_name = "momentum"

        # 設定値
        screening = config.get("screening", {})
        self.min_turnover = screening.get("min_turnover", 1_000_000_000)
        self.min_volume_ratio = screening.get("min_volume_ratio", 1.2)
        self.max_distance_from_high_20d = screening.get("max_distance_from_high_20d", 5.0)
        self.min_history_days = screening.get("min_history_days", 25)
        self.downgrade_extreme_volume_ratio = screening.get("downgrade_extreme_volume_ratio", True)
        self.risk_volume_ratio_threshold = screening.get("risk_volume_ratio_threshold", 5.0)
        self.max_return_5d = screening.get("max_return_5d", 10.0)
        self.max_return_20d = screening.get("max_return_20d", 30.0)

        # volume条件（config.yaml signals.volume と同期）
        volume_cfg = config.get("signals.volume", {})
        self.volume_hard_gate = volume_cfg.get("hard_gate", False)
        self.volume_use_percentile = volume_cfg.get("use_percentile", True)
        self.volume_percentile_threshold = volume_cfg.get("percentile_threshold", 60)
        self.volume_market_low_threshold = volume_cfg.get("market_low_volume_threshold", 0.8)

        # quality_low_risk戦略の過熱回避条件を任意フィルターとして再利用する。
        quality_cfg = config.quality_filter_config
        self.quality_filter = QualityRiskFilter(
            enabled=bool(quality_cfg.get("enabled", False)),
            max_daily_return=float(quality_cfg.get("max_daily_return", 5.0)),
            max_return_5d=float(quality_cfg.get("max_return_5d", 10.0)),
            max_volume_ratio=float(quality_cfg.get("max_volume_ratio", 3.0)),
        )

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """モメンタム戦術で評価する。"""
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

        # 基礎データが有効な候補だけに過熱フィルターを適用する。
        rejection_reason = self.quality_filter.rejection_reason(indicators)
        if rejection_reason is not None:
            result.reason = f"除外: quality_filter: overheat ({rejection_reason})"
            result.risk_warnings.append(f"quality_filter: {rejection_reason}")
            return result

        # ベンチマーク超過リターンの計算
        if benchmark_returns:
            result.return_5d_vs_benchmark = self._calc_vs_benchmark(
                indicators.return_5d,
                benchmark_returns.get("return_5d"),
            )
            result.return_20d_vs_benchmark = self._calc_vs_benchmark(
                None,
                benchmark_returns.get("return_20d"),
            )
            result.return_60d_vs_benchmark = self._calc_vs_benchmark(
                None,
                benchmark_returns.get("return_60d"),
            )

        # 買い候補条件チェック
        buy_reasons: list[str] = []
        is_buy_candidate = True

        # 条件1: close > MA5
        if indicators.ma5 is not None and indicators.close > indicators.ma5:
            buy_reasons.append("MA5之上")
        else:
            is_buy_candidate = False

        # 条件2: close > MA25
        if indicators.close > indicators.ma25:
            buy_reasons.append("MA25之上")
        else:
            is_buy_candidate = False

        # 条件3: MA5 > MA25
        if indicators.ma5 is not None and indicators.ma5 > indicators.ma25:
            buy_reasons.append("MA5>MA25（上昇トレンド）")
        else:
            is_buy_candidate = False

        # 条件4: 20日高値圏
        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
        ):
            buy_reasons.append(f"20日高値圏（{indicators.high_20d_distance:.1f}%）")
        else:
            is_buy_candidate = False

        # 条件5: 5日リターン（プラスだが過熱していないこと）
        ret_5d = (
            result.return_5d_vs_benchmark
            if result.return_5d_vs_benchmark is not None
            else indicators.return_5d
        )
        if ret_5d is not None and 0 < ret_5d < self.max_return_5d:
            buy_reasons.append(f"5日リターン{ret_5d:.1f}%")
        else:
            is_buy_candidate = False

        # 条件6: 出来高比率（percentile / ハードゲート / 緩和条件をconfigに従い判定）
        vol_ok = False
        if indicators.volume_ratio is not None:
            market_is_low = (
                indicators.market_median_volume_ratio is not None
                and indicators.market_median_volume_ratio < self.volume_market_low_threshold
            )
            if self.volume_hard_gate and not market_is_low:
                vol_ok = indicators.volume_ratio >= self.min_volume_ratio
            elif self.volume_use_percentile and indicators.volume_ratio_percentile is not None:
                vol_ok = indicators.volume_ratio_percentile >= self.volume_percentile_threshold
            else:
                vol_ok = indicators.volume_ratio >= self.min_volume_ratio * 0.5
        if vol_ok:
            if indicators.volume_ratio_percentile is not None:
                buy_reasons.append(
                    f"出来高{indicators.volume_ratio:.1f}倍"
                    f"(P{indicators.volume_ratio_percentile:.0f})"
                )
            else:
                buy_reasons.append(f"出来高{indicators.volume_ratio:.1f}倍")
        else:
            is_buy_candidate = False

        # 条件7: 売買代金
        if indicators.turnover >= self.min_turnover:
            turnover_oku = indicators.turnover / 100_000_000
            buy_reasons.append(f"売買代金{turnover_oku:.1f}億円")
        else:
            is_buy_candidate = False

        # 判定
        if is_buy_candidate:
            # 出来高比率5倍以上の場合は格下げ
            if (
                self.downgrade_extreme_volume_ratio
                and indicators.volume_ratio is not None
                and indicators.volume_ratio >= self.risk_volume_ratio_threshold
            ):
                if self._is_etf(indicators.code):
                    result.signal_type = "BUY_CANDIDATE"
                    result.reason = "買い候補: " + ", ".join(buy_reasons)
                    result.risk_warnings.append(
                        f"出来高比率{indicators.volume_ratio:.1f}倍（出来高急増）"
                    )
                else:
                    result.signal_type = "WATCH"
                    result.reason = (
                        "監視候補: "
                        + ", ".join(buy_reasons)
                        + f"（出来高比率{indicators.volume_ratio:.1f}倍のため監視に格下げ）"
                    )
            else:
                result.signal_type = "BUY_CANDIDATE"
                result.reason = "買い候補: " + ", ".join(buy_reasons)
            return result

        # 監視候補チェック
        watch_reasons: list[str] = []

        if (
            indicators.close > indicators.ma25
            and indicators.ma5 is not None
            and indicators.ma5 <= indicators.ma25
        ):
            watch_reasons.append("MA25之上だがMA5之下（トレンド弱さ）")

        vol_low = False
        if indicators.volume_ratio is not None:
            if self.volume_use_percentile and indicators.volume_ratio_percentile is not None:
                vol_low = indicators.volume_ratio_percentile < self.volume_percentile_threshold
            else:
                vol_low = indicators.volume_ratio < self.min_volume_ratio

        if indicators.return_5d is not None and indicators.return_5d > 0 and vol_low:
            watch_reasons.append("リターンは良いが出来高不足")

        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
            and vol_low
        ):
            watch_reasons.append("20日高値圏だが出来高不足")

        if (
            indicators.close > indicators.ma25
            and indicators.turnover < self.min_turnover
            and indicators.turnover >= self.min_turnover * 0.5
        ):
            watch_reasons.append("トレンド良好だが売買代金やや不足")

        if watch_reasons:
            result.signal_type = "WATCH"
            result.reason = "監視候補: " + ", ".join(watch_reasons)
            return result

        # 除外
        exclude_reasons: list[str] = []

        if indicators.close < indicators.ma25:
            exclude_reasons.append("終値が25日移動平均線を下回っています")

        if indicators.ma5 is not None and indicators.ma5 < indicators.ma25:
            exclude_reasons.append("5日移動平均線が25日移動平均線を下回っています")

        if indicators.return_5d is not None and indicators.return_5d < -10:
            exclude_reasons.append(
                f"5日リターンが{indicators.return_5d:.1f}%と大幅にマイナスです"
            )

        if indicators.turnover < self.min_turnover * 0.3:
            exclude_reasons.append("売買代金が基準未満です")

        if exclude_reasons:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: " + ", ".join(exclude_reasons)
        else:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: 買い候補条件を充足せず"

        return result
