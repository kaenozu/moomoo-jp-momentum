"""
シグナル判定モジュール

ファイルパス: src/signals.py
何をするか: 指標データから売買シグナルを判定する
なぜ存在するか: 売買候補の抽出ロジックを一元管理するため
関連ファイル: indicators.py, scoring.py, config.py

注意:
    - このモジュールは「買い指示」を出しません
    - 「買い候補」「監視候補」「除外」「リスク警告」を判定します
    - 最終的な投資判断はユーザーが行います
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .indicators import StockIndicators

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """シグナル判定結果"""
    code: str
    name: Optional[str]
    date: str
    signal_type: str
    strategy_name: str = "momentum"
    score: float = 0.0
    reason: str = ""
    risk_warnings: list[str] = field(default_factory=list)
    price_at_signal: Optional[float] = None


class SignalDetector:
    """シグナル検出クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.screening_config = config.get("screening", {})

        self.min_turnover = self.screening_config.get("min_turnover", 1_000_000_000)
        self.min_volume_ratio = self.screening_config.get("min_volume_ratio", 1.2)
        self.max_distance_from_high_20d = self.screening_config.get(
            "max_distance_from_high_20d", 5.0
        )
        self.risk_daily_return_threshold = self.screening_config.get(
            "risk_daily_return_threshold", 8.0
        )
        self.risk_return_5d_threshold = self.screening_config.get(
            "risk_return_5d_threshold", 15.0
        )
        self.risk_volume_ratio_threshold = self.screening_config.get(
            "risk_volume_ratio_threshold", 5.0
        )
        self.min_history_days = self.screening_config.get("min_history_days", 25)
        self.downgrade_extreme_volume_ratio = self.screening_config.get(
            "downgrade_extreme_volume_ratio", True
        )
        self.max_return_5d = self.screening_config.get("max_return_5d", 10.0)
        self.max_return_20d = self.screening_config.get("max_return_20d", 30.0)

        # volume条件設定（config.yaml signals.volume から読み込み）
        volume_cfg = config.get("signals.volume", {})
        self.volume_hard_gate = volume_cfg.get("hard_gate", False)
        self.volume_use_percentile = volume_cfg.get("use_percentile", True)
        self.volume_percentile_threshold = volume_cfg.get("percentile_threshold", 60)
        self.volume_market_low_threshold = volume_cfg.get("market_low_volume_threshold", 0.8)

    def _is_etf(self, code: str) -> bool:
        """設定に明示されたETFコードだけをETFとして扱う。"""
        configured = self.config.get("strategies.etf_rotation.codes", None)
        if configured is None:
            configured = ["JP.2559", "JP.1306", "JP.1320", "JP.2558", "JP.2563"]
        return code in {str(item) for item in configured}

    def check_data_quality(self, indicators: StockIndicators) -> tuple[bool, str]:
        """データ品質チェック"""
        if indicators.close is None or indicators.close <= 0:
            return False, "closeがNULLまたは無効"

        if indicators.history_days and indicators.history_days < self.min_history_days:
            return False, f"履歴が不足しています（{indicators.history_days}日分）"

        if indicators.volume_ratio is None or indicators.volume_ratio <= 0:
            if indicators.volume is None or indicators.volume <= 0:
                return False, "出来高が0またはNULL"

        if indicators.turnover is None or indicators.turnover <= 0:
            return False, "売買代金が0またはNULL"

        if indicators.ma25 is None:
            return False, "25営業日未満（ma25なし）"

        if indicators.high_20d is None:
            return False, "20日高値データなし"

        return True, ""

    def check_risk_warnings(self, indicators: StockIndicators) -> list[str]:
        """リスク警告をチェックする"""
        warnings = []

        if indicators.daily_return is not None and indicators.daily_return >= self.risk_daily_return_threshold:
            warnings.append(f"当日リターン{indicators.daily_return:.1f}%（急騰警告）")

        if indicators.return_5d is not None and indicators.return_5d >= self.risk_return_5d_threshold:
            warnings.append(f"5日リターン{indicators.return_5d:.1f}%（過熱警告）")

        if indicators.volume_ratio is not None and indicators.volume_ratio >= self.risk_volume_ratio_threshold:
            warnings.append(f"出来高比率{indicators.volume_ratio:.1f}倍（出来高急増）")

        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance > 10
        ):
            warnings.append(f"20日高値を{indicators.high_20d_distance:.1f}%更新")

        return warnings

    def detect_signal(self, indicators: StockIndicators) -> SignalResult:
        """シグナルを判定する"""
        quality_ok, quality_reason = self.check_data_quality(indicators)

        result = SignalResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            signal_type="EXCLUDE",
            price_at_signal=indicators.close,
        )

        if not quality_ok:
            result.reason = f"除外: {quality_reason}"
            return result

        result.risk_warnings = self.check_risk_warnings(indicators)

        buy_reasons = []
        is_buy_candidate = True

        if indicators.ma5 is not None and indicators.close > indicators.ma5:
            buy_reasons.append("MA5より上")
        else:
            is_buy_candidate = False

        if indicators.ma25 is not None and indicators.close > indicators.ma25:
            buy_reasons.append("MA25より上")
        else:
            is_buy_candidate = False

        if indicators.ma5 is not None and indicators.ma25 is not None and indicators.ma5 > indicators.ma25:
            buy_reasons.append("MA5>MA25（上昇トレンド）")
        else:
            is_buy_candidate = False

        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
        ):
            buy_reasons.append(f"20日高値圏（{indicators.high_20d_distance:.1f}%）")
        else:
            is_buy_candidate = False

        if indicators.return_5d is not None and 0 < indicators.return_5d < self.max_return_5d:
            buy_reasons.append(f"5日リターン{indicators.return_5d:.1f}%")
        else:
            is_buy_candidate = False

        # volume_ratio: ハードゲートかスコア加点か
        vol_ok = False
        market_is_low = (
            indicators.market_median_volume_ratio is not None
            and indicators.market_median_volume_ratio < self.volume_market_low_threshold
        )
        if indicators.volume_ratio is not None:
            if self.volume_hard_gate and not market_is_low:
                vol_ok = indicators.volume_ratio >= self.min_volume_ratio
            elif self.volume_use_percentile and indicators.volume_ratio_percentile is not None:
                vol_ok = indicators.volume_ratio_percentile >= self.volume_percentile_threshold
            else:
                vol_ok = indicators.volume_ratio >= self.min_volume_ratio * 0.5
        if vol_ok:
            if indicators.volume_ratio_percentile is not None:
                buy_reasons.append(f"出来高{indicators.volume_ratio:.1f}倍(P{indicators.volume_ratio_percentile:.0f})")
            else:
                buy_reasons.append(f"出来高{indicators.volume_ratio:.1f}倍")
        else:
            is_buy_candidate = False

        if indicators.turnover >= self.min_turnover:
            turnover_oku = indicators.turnover / 100_000_000
            buy_reasons.append(f"売買代金{turnover_oku:.1f}億円")
        else:
            is_buy_candidate = False

        if is_buy_candidate:
            if (
                self.downgrade_extreme_volume_ratio
                and indicators.volume_ratio is not None
                and indicators.volume_ratio >= self.risk_volume_ratio_threshold
                and not self._is_etf(indicators.code)
            ):
                result.signal_type = "WATCH"
                result.reason = (
                    "監視候補: " + ", ".join(buy_reasons)
                    + f"（出来高比率{indicators.volume_ratio:.1f}倍のため監視に格下げ）"
                )
            else:
                result.signal_type = "BUY_CANDIDATE"
                result.reason = "買い候補: " + ", ".join(buy_reasons)
            return result

        watch_reasons = []

        if (
            indicators.ma25 is not None
            and indicators.close > indicators.ma25
            and indicators.ma5 is not None
            and indicators.ma5 <= indicators.ma25
        ):
            watch_reasons.append("MA25より上だがMA5がMA25以下（トレンド弱め）")

        if (
            indicators.return_5d is not None
            and indicators.return_5d > 0
            and indicators.volume_ratio is not None
            and indicators.volume_ratio < self.min_volume_ratio
        ):
            watch_reasons.append("リターンは良いが出来高不足")

        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
            and indicators.volume_ratio is not None
            and indicators.volume_ratio < self.min_volume_ratio
        ):
            watch_reasons.append("20日高値圏だが出来高不足")

        if (
            indicators.ma25 is not None
            and indicators.close > indicators.ma25
            and indicators.turnover < self.min_turnover
            and indicators.turnover >= self.min_turnover * 0.5
        ):
            watch_reasons.append("トレンド良好だが売買代金やや不足")

        if watch_reasons:
            result.signal_type = "WATCH"
            result.reason = "監視候補: " + ", ".join(watch_reasons)
            return result

        exclude_reasons = []

        if indicators.history_days < self.min_history_days:
            exclude_reasons.append(f"出来高データが不足しています（{indicators.history_days}日分）")

        if indicators.ma25 is not None and indicators.close < indicators.ma25:
            exclude_reasons.append("終値が25日移動平均線を下回っています")

        if indicators.ma5 is not None and indicators.ma25 is not None and indicators.ma5 < indicators.ma25:
            exclude_reasons.append("5日移動平均線が25日移動平均線を下回っています")

        if indicators.return_5d is not None and indicators.return_5d < -10:
            exclude_reasons.append(f"5日リターンが{indicators.return_5d:.1f}%と大幅にマイナスです")

        if indicators.turnover < self.min_turnover * 0.3:
            exclude_reasons.append("売買代金が基準未満です")

        result.signal_type = "EXCLUDE"
        result.reason = "除外: " + ", ".join(exclude_reasons) if exclude_reasons else "除外: 買い候補条件を充足せず"
        return result


def detect_signals_batch(
    indicators_list: list[StockIndicators],
    config: Config,
) -> list[SignalResult]:
    """複数銘柄のシグナルを一括判定する"""
    detector = SignalDetector(config)
    results = [detector.detect_signal(indicators) for indicators in indicators_list]

    logger.info(
        "シグナル判定完了: %s銘柄 (候補: %s, 監視: %s, 除外: %s)",
        len(results),
        sum(1 for r in results if r.signal_type == "BUY_CANDIDATE"),
        sum(1 for r in results if r.signal_type == "WATCH"),
        sum(1 for r in results if r.signal_type == "EXCLUDE"),
    )

    return results
