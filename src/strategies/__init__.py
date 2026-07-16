"""
戦術基底クラスと戦術レジストリ。

ETF判定は銘柄コードの接頭辞ではなく、設定に明示されたETFコードで行う。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..config import Config
from ..indicators import StockIndicators


DEFAULT_ETF_CODES = (
    "JP.2559",
    "JP.1306",
    "JP.1320",
    "JP.2558",
    "JP.2563",
)


@dataclass
class StrategyResult:
    """戦術判定結果。"""

    code: str
    name: Optional[str]
    date: str
    strategy_name: str
    signal_type: str
    score: float = 0.0
    reason: str = ""
    risk_warnings: list[str] = field(default_factory=list)
    price_at_signal: Optional[float] = None
    return_5d_vs_benchmark: Optional[float] = None
    return_20d_vs_benchmark: Optional[float] = None
    return_60d_vs_benchmark: Optional[float] = None
    relative_strength_rank: Optional[int] = None


class BaseStrategy(ABC):
    """全戦術の共通インターフェース。"""

    def __init__(self, config: Config):
        self.config = config
        self.strategy_name = "base"

    @abstractmethod
    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """指標を評価して戦術判定結果を返す。"""
        raise NotImplementedError

    def _is_etf(self, code: str) -> bool:
        """設定に明示されたETFコードだけをETFとして扱う。"""
        configured = self.config.get("strategies.etf_rotation.codes", None)
        codes = configured if configured is not None else DEFAULT_ETF_CODES
        return code in {str(item) for item in codes}

    @staticmethod
    def _calc_vs_benchmark(
        stock_return: Optional[float],
        benchmark_return: Optional[float],
    ) -> Optional[float]:
        if stock_return is None or benchmark_return is None:
            return None
        return stock_return - benchmark_return


class StrategyRegistry:
    """戦術レジストリ。"""

    _strategies: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(strategy_class: type[BaseStrategy]):
            cls._strategies[name] = strategy_class
            return strategy_class

        return decorator

    @classmethod
    def get(cls, name: str, config: Config) -> BaseStrategy:
        if name not in cls._strategies:
            raise ValueError(f"不明な戦術: {name}")
        return cls._strategies[name](config)

    @classmethod
    def get_all(cls, config: Config) -> dict[str, BaseStrategy]:
        return {name: cls.get(name, config) for name in cls._strategies}

    @classmethod
    def list_names(cls) -> list[str]:
        return list(cls._strategies.keys())


# デコレータ登録を必ず実行する。循環importを避けるため定義後に読み込む。
from . import etf_rotation as _etf_rotation  # noqa: E402,F401
from . import momentum as _momentum  # noqa: E402,F401
from . import quality_low_risk as _quality_low_risk  # noqa: E402,F401
