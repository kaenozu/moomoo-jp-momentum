"""
モメンタム候補向け品質・過熱フィルター。

ファイルパス: src/filters/quality_risk_filter.py
何をするか: 当日騰落率、5日リターン、出来高比率から過熱候補を除外する
なぜ存在するか: quality_low_risk戦略の過熱回避条件を他戦略でも再利用するため
関連ファイル: __init__.py, ../strategies/momentum.py, ../indicators.py, ../config.py
"""

from dataclasses import dataclass

from ..indicators import StockIndicators


@dataclass(frozen=True)
class QualityRiskFilter:
    """設定可能な品質・過熱フィルター。"""

    enabled: bool = False
    max_daily_return: float = 5.0
    max_return_5d: float = 10.0
    max_volume_ratio: float = 3.0

    def __post_init__(self) -> None:
        """閾値が正の有限値であることを検証する。"""
        thresholds = {
            "max_daily_return": self.max_daily_return,
            "max_return_5d": self.max_return_5d,
            "max_volume_ratio": self.max_volume_ratio,
        }
        for name, value in thresholds.items():
            if value <= 0:
                raise ValueError(f"{name}は0より大きい値を指定してください: {value}")

    def accept(self, indicators: StockIndicators) -> bool:
        """過熱条件に該当しない場合だけ候補を受理する。"""
        return self.rejection_reason(indicators) is None

    def rejection_reason(self, indicators: StockIndicators) -> str | None:
        """除外理由を返し、受理する場合はNoneを返す。"""
        if not self.enabled:
            return None

        if (
            indicators.daily_return is not None
            and indicators.daily_return >= self.max_daily_return
        ):
            return (
                "daily_return "
                f"{indicators.daily_return:.2f}% >= {self.max_daily_return:.2f}%"
            )

        if (
            indicators.return_5d is not None
            and indicators.return_5d >= self.max_return_5d
        ):
            return (
                "return_5d "
                f"{indicators.return_5d:.2f}% >= {self.max_return_5d:.2f}%"
            )

        if (
            indicators.volume_ratio is not None
            and indicators.volume_ratio >= self.max_volume_ratio
        ):
            return (
                "volume_ratio "
                f"{indicators.volume_ratio:.2f} >= {self.max_volume_ratio:.2f}"
            )

        return None
