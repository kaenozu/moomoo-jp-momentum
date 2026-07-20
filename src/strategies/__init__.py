"""
戦術基底クラス

ファイルパス: src/strategies/__init__.py
何をするか: 全戦術に共通するインターフェースを定義する
なぜ存在するか: 複数戦術を統一的に扱うため
関連ファイル: signals.py, scoring.py, config.py
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..indicators import StockIndicators
from ..config import Config


@dataclass
class StrategyResult:
    """戦術判定結果"""
    code: str
    name: Optional[str]
    date: str
    strategy_name: str
    signal_type: str  # "BUY_CANDIDATE", "WATCH", "EXCLUDE"
    score: float = 0.0
    reason: str = ""
    risk_warnings: list = field(default_factory=list)
    price_at_signal: Optional[float] = None

    # 相対強度
    return_5d_vs_benchmark: Optional[float] = None
    return_20d_vs_benchmark: Optional[float] = None
    return_60d_vs_benchmark: Optional[float] = None
    relative_strength_rank: Optional[int] = None


class BaseStrategy(ABC):
    """戦術基底クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.strategy_name = "base"

    @abstractmethod
    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """
        銘柄を評価する

        Args:
            indicators: 指標データ
            benchmark_returns: ベンチマークリターンの辞書

        Returns:
            StrategyResult: 評価結果
        """
        pass

    def _is_etf(self, code: str) -> bool:
        """ETFかどうかを判定する"""
        if code.startswith("JP.13") or code.startswith("JP.25"):
            return True
        return False

    def _calc_vs_benchmark(
        self,
        stock_return: Optional[float],
        benchmark_return: Optional[float],
    ) -> Optional[float]:
        """
        ベンチマークに対する超過リターンを計算する

        Args:
            stock_return: 銘柄リターン
            benchmark_return: ベンチマークリターン

        Returns:
            float: 超過リターン
        """
        if stock_return is None or benchmark_return is None:
            return None
        return stock_return - benchmark_return


class StrategyRegistry:
    """戦術レジストリ"""

    _strategies: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, name: str):
        """戦術を登録する（デコレータとして使用）"""
        def decorator(strategy_class: type[BaseStrategy]):
            cls._strategies[name] = strategy_class
            return strategy_class
        return decorator

    @classmethod
    def get(cls, name: str, config: Config) -> BaseStrategy:
        """戦術を取得する"""
        if name not in cls._strategies:
            raise ValueError(f"不明な戦術: {name}")
        return cls._strategies[name](config)

    @classmethod
    def get_all(cls, config: Config) -> dict[str, BaseStrategy]:
        """全戦術を取得する"""
        return {name: cls.get(name, config) for name in cls._strategies}

    @classmethod
    def list_names(cls) -> list[str]:
        """登録済み戦術名の一覧を返す"""
        return list(cls._strategies.keys())


# Cross-sectional戦略はパッケージimport時に登録する。
from . import sector_relative_momentum as sector_relative_momentum  # noqa: E402,F401
