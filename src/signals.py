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

from .indicators import StockIndicators
from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """シグナル判定結果"""
    code: str
    name: Optional[str]
    date: str
    signal_type: str  # "BUY_CANDIDATE", "WATCH", "EXCLUDE", "RISK_WARNING"
    score: float = 0.0
    reason: str = ""
    risk_warnings: list[str] = field(default_factory=list)
    price_at_signal: Optional[float] = None


class SignalDetector:
    """シグナル検出クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.screening_config = config.get("screening", {})

        # スクリーニング閾値
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

    def _is_etf(self, code: str) -> bool:
        """
        ETFかどうかを判定する（簡易判定）

        Args:
            code: 銘柄コード

        Returns:
            bool: ETFならTrue
        """
        # ETFコードのパターン（JP.1300番台、JP.2500番台等）
        if code.startswith("JP.13") or code.startswith("JP.25"):
            return True
        return False

    def check_data_quality(
        self,
        indicators: StockIndicators,
    ) -> tuple[bool, str]:
        """
        データ品質チェック

        Args:
            indicators: 指標データ

        Returns:
            tuple[bool, str]: (OKならTrue, 理由)
        """
        # closeがNULL
        if indicators.close is None or indicators.close <= 0:
            return False, "closeがNULLまたは無効"

        # volume_ratioがある場合はvolumeチェックをスキップ
        # （indicatorsテーブルにvolumeカラムがないため）
        if indicators.volume_ratio is None or indicators.volume_ratio <= 0:
            # volume_ratioがない場合のみ、volumeをチェック
            if indicators.volume is None or indicators.volume <= 0:
                return False, "出来高が0またはNULL"

        # turnoverが0
        if indicators.turnover is None or indicators.turnover <= 0:
            return False, "売買代金が0またはNULL"

        # 25営業日以上のデータがあるか（ma25が計算されているか）
        if indicators.ma25 is None:
            return False, f"25営業日未満（ma25なし）"

        # 20日高値がない
        if indicators.high_20d is None:
            return False, "20日高値データなし"

        return True, ""

    def check_risk_warnings(
        self,
        indicators: StockIndicators,
    ) -> list[str]:
        """
        リスク警告をチェックする

        Args:
            indicators: 指標データ

        Returns:
            list[str]: リスク警告のリスト
        """
        warnings = []

        # 当日リターンが急騰
        if (
            indicators.daily_return is not None
            and indicators.daily_return >= self.risk_daily_return_threshold
        ):
            warnings.append(
                f"当日リターン{indicators.daily_return:.1f}%（急騰警告）"
            )

        # 5日リターンが急騰
        if (
            indicators.return_5d is not None
            and indicators.return_5d >= self.risk_return_5d_threshold
        ):
            warnings.append(
                f"5日リターン{indicators.return_5d:.1f}%（過熱警告）"
            )

        # 出来高急増
        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio >= self.risk_volume_ratio_threshold
        ):
            warnings.append(
                f"出来高比率{indicators.volume_ratio:.1f}倍（出来高急増）"
            )

        # 20日高値を大きく更新
        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance > 0
            and indicators.high_20d_distance > 10
        ):
            warnings.append(
                f"20日高値を{indicators.high_20d_distance:.1f}%更新"
            )

        return warnings

    def detect_signal(
        self,
        indicators: StockIndicators,
    ) -> SignalResult:
        """
        シグナルを判定する

        Args:
            indicators: 指標データ

        Returns:
            SignalResult: 判定結果
        """
        # データ品質チェック
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

        # リスク警告チェック
        result.risk_warnings = self.check_risk_warnings(indicators)

        # 買い候補条件チェック
        buy_reasons = []
        is_buy_candidate = True

        # 条件1: close > ma5
        if indicators.ma5 is not None and indicators.close > indicators.ma5:
            buy_reasons.append("MA5之上")
        else:
            is_buy_candidate = False

        # 条件2: close > ma25
        if indicators.ma25 is not None and indicators.close > indicators.ma25:
            buy_reasons.append("MA25之上")
        else:
            is_buy_candidate = False

        # 条件3: ma5 > ma25（ゴールデンクロス状態）
        if (
            indicators.ma5 is not None
            and indicators.ma25 is not None
            and indicators.ma5 > indicators.ma25
        ):
            buy_reasons.append("MA5>MA25（上昇トレンド）")
        else:
            is_buy_candidate = False

        # 条件4: 20日高値圏（5%以内）
        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
        ):
            buy_reasons.append(f"20日高値圏（{indicators.high_20d_distance:.1f}%）")
        else:
            is_buy_candidate = False

        # 条件5: 5日リターンプラス
        if indicators.return_5d is not None and indicators.return_5d > 0:
            buy_reasons.append(f"5日リターン{indicators.return_5d:.1f}%")
        else:
            is_buy_candidate = False

        # 条件6: 出来高比率
        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio >= self.min_volume_ratio
        ):
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
            # 出来高比率5倍以上の場合は、設定に応じて監視に落とす
            if (
                self.downgrade_extreme_volume_ratio
                and indicators.volume_ratio is not None
                and indicators.volume_ratio >= self.risk_volume_ratio_threshold
            ):
                # ETFの場合は警告のみ、個別株の場合は監視に落とす
                if self._is_etf(indicators.code):
                    result.signal_type = "BUY_CANDIDATE"
                    result.reason = "買い候補: " + ", ".join(buy_reasons)
                    result.risk_warnings.append(
                        f"出来高比率{indicators.volume_ratio:.1f}倍（出来高急増）"
                    )
                else:
                    result.signal_type = "WATCH"
                    result.reason = (
                        "監視候補: " + ", ".join(buy_reasons)
                        + f"（出来高比率{indicators.volume_ratio:.1f}倍のため監視に格下げ）"
                    )
            else:
                result.signal_type = "BUY_CANDIDATE"
                result.reason = "買い候補: " + ", ".join(buy_reasons)
            return result

        # 監視候補チェック
        watch_reasons = []

        # close > ma25 だが ma5 <= ma25
        if (
            indicators.close > indicators.ma25
            and indicators.ma5 is not None
            and indicators.ma5 <= indicators.ma25
        ):
            watch_reasons.append("MA25之上だがMA5之下（トレンド弱さ）")

        # return_5d はプラスだが volume_ratio が弱い
        if (
            indicators.return_5d is not None
            and indicators.return_5d > 0
            and indicators.volume_ratio is not None
            and indicators.volume_ratio < self.min_volume_ratio
        ):
            watch_reasons.append("リターンは良いが出来高不足")

        # 20日高値圏には近いが出来高が足りない
        if (
            indicators.high_20d_distance is not None
            and indicators.high_20d_distance >= -self.max_distance_from_high_20d
            and indicators.volume_ratio is not None
            and indicators.volume_ratio < self.min_volume_ratio
        ):
            watch_reasons.append("20日高値圏だが出来高不足")

        # トレンドは良いが売買代金がやや不足
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
        exclude_reasons = []

        # history_daysチェック（25営業日未満）
        if indicators.history_days < self.min_history_days:
            exclude_reasons.append(
                f"出来高データが不足しています（{indicators.history_days}日分）"
            )

        # 終値がMA25を下回る
        if indicators.close < indicators.ma25:
            exclude_reasons.append("終値が25日移動平均線を下回っています")

        # MA5がMA25を下回る
        if (
            indicators.ma5 is not None
            and indicators.ma25 is not None
            and indicators.ma5 < indicators.ma25
        ):
            exclude_reasons.append("5日移動平均線が25日移動平均線を下回っています")

        # 5日リターンが大幅マイナス
        if indicators.return_5d is not None and indicators.return_5d < -10:
            exclude_reasons.append(
                f"5日リターンが{indicators.return_5d:.1f}%と大幅にマイナスです"
            )

        # 売買代金が基準未満
        if indicators.turnover < self.min_turnover * 0.3:
            exclude_reasons.append("売買代金が基準未満です")

        if exclude_reasons:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: " + ", ".join(exclude_reasons)
        else:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: 買い候補条件を充足せず"

        return result


def detect_signals_batch(
    indicators_list: list[StockIndicators],
    config: Config,
) -> list[SignalResult]:
    """
    複数銘柄のシグナルを一括判定する

    Args:
        indicators_list: 指標のリスト
        config: 設定オブジェクト

    Returns:
        list[SignalResult]: 判定結果のリスト
    """
    detector = SignalDetector(config)
    results = []

    for indicators in indicators_list:
        result = detector.detect_signal(indicators)
        results.append(result)

    logger.info(
        f"シグナル判定完了: {len(results)}銘柄 "
        f"(候補: {sum(1 for r in results if r.signal_type == 'BUY_CANDIDATE')}, "
        f"監視: {sum(1 for r in results if r.signal_type == 'WATCH')}, "
        f"除外: {sum(1 for r in results if r.signal_type == 'EXCLUDE')})"
    )

    return results
