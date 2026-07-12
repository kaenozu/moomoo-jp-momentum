"""
戦略ランナー

ファイルパス: src/strategy_runner.py
何をするか: 複数戦略を一括実行し、シグナルを保存する
なぜ存在するか: 戦略比較のため、同じ条件で複数戦略を検証するため
関連ファイル: strategies/*.py, relative_strength.py, screener.py
"""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .indicators import StockIndicators
from .relative_strength import RelativeStrengthCalculator
from .strategies import StrategyRegistry, StrategyResult

logger = logging.getLogger(__name__)


class StrategyRunner:
    """戦略ランナー"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self.relative_strength = RelativeStrengthCalculator(config)

    def _benchmark_code(self) -> str:
        """Return the configured benchmark used for strategy comparison."""
        return str(
            self.config.get(
                "signals.relative_strength.benchmark_code",
                "JP.1306",
            )
        )

    def _benchmark_returns(
        self,
        target_date: str,
    ) -> dict[str, Optional[float]]:
        """Calculate benchmark returns using only bars on or before target_date."""
        benchmark_code = self._benchmark_code()
        return {
            f"return_{period}d": self.relative_strength.calc_benchmark_return(
                benchmark_code,
                target_date,
                period,
            )
            for period in self.relative_strength.periods
        }

    def run_all(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_names: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """全戦略を実行し、シグナルを保存"""
        if strategy_names is None:
            strategy_names = StrategyRegistry.list_names()

        all_signals: dict[str, int] = {}
        benchmark_returns = self._benchmark_returns(target_date)

        for name in strategy_names:
            strategy = StrategyRegistry.get(name, self.config)
            signals: list[StrategyResult] = []

            for indicators in indicators_list:
                result = strategy.evaluate(indicators, benchmark_returns)
                signals.append(result)

            saved = self._save_signals(
                signals,
                name,
                target_date,
            )
            all_signals[name] = saved
            logger.info("戦略 %s: %d件保存", name, saved)

        return all_signals

    def run_one(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_name: str,
    ) -> int:
        """単一戦略を実行し、シグナルを保存"""
        return self.run_all(
            indicators_list,
            target_date,
            [strategy_name],
        ).get(strategy_name, 0)

    def _save_signals(
        self,
        signals: list[StrategyResult],
        strategy_name: str,
        target_date: str,
    ) -> int:
        """Replace one strategy's signals for the requested target date."""
        now = datetime.now().isoformat()

        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "DELETE FROM signals WHERE strategy_name = ? AND date = ?",
                (strategy_name, target_date),
            )

            if not signals:
                return 0

            sql = """
                INSERT INTO signals
                (code, date, signal_type, strategy_name, score, reason, risk_warnings, price_at_signal, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = []
            for signal in signals:
                warnings_str = (
                    "; ".join(signal.risk_warnings)
                    if signal.risk_warnings
                    else ""
                )
                params.append(
                    (
                        signal.code,
                        signal.date,
                        signal.signal_type,
                        strategy_name,
                        signal.score,
                        signal.reason,
                        warnings_str,
                        signal.price_at_signal,
                        now,
                    )
                )

            conn.executemany(sql, params)

        return len(signals)
