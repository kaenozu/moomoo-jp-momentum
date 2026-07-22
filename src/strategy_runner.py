"""複数戦略を同一条件で実行し、シグナルを保存する。"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .indicators import StockIndicators
from .strategies import StrategyRegistry

logger = logging.getLogger(__name__)


class StrategyRunner:
    """戦略ランナー。"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

    def run_all(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_names: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """全戦略を同じベンチマーク条件で実行し、保存件数を返す。"""
        del target_date  # 各StockIndicators.dateを保存時の基準日として使用する。
        if strategy_names is None:
            strategy_names = StrategyRegistry.list_names()

        benchmark_code = self.config.get(
            "signals.relative_strength.benchmark_code", "JP.1306"
        )
        benchmark = next(
            (item for item in indicators_list if item.code == benchmark_code), None
        )
        benchmark_returns = {
            "return_5d": benchmark.return_5d if benchmark else None,
            "return_20d": benchmark.return_20d if benchmark else None,
            "return_60d": benchmark.return_60d if benchmark else None,
        }

        saved_counts: dict[str, int] = {}
        for name in strategy_names:
            strategy = StrategyRegistry.get(name, self.config)
            results = [
                strategy.evaluate(indicator, benchmark_returns)
                for indicator in indicators_list
            ]
            saved = self._save_signals(results, name)
            saved_counts[name] = saved
            logger.info("戦略 %s: %d件保存", name, saved)
        return saved_counts

    def run_one(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_name: str,
    ) -> int:
        """単一戦略を実行し、保存件数を返す。"""
        results = self.run_all(indicators_list, target_date, [strategy_name])
        return int(results.get(strategy_name, 0))

    def _save_signals(self, signals: list, strategy_name: str) -> int:
        """既存のsignals.idを維持するUPSERTで保存する。"""
        if not signals:
            return 0

        now = datetime.now().isoformat()
        sql = """
            INSERT INTO signals
            (code, date, signal_type, strategy_name, score, reason,
             risk_warnings, price_at_signal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_name, code, date) DO UPDATE SET
                signal_type = excluded.signal_type,
                score = excluded.score,
                reason = excluded.reason,
                risk_warnings = excluded.risk_warnings,
                price_at_signal = excluded.price_at_signal,
                created_at = excluded.created_at
        """
        params = [
            (
                signal.code,
                signal.date,
                signal.signal_type,
                strategy_name,
                signal.score,
                signal.reason,
                "; ".join(signal.risk_warnings) if signal.risk_warnings else "",
                signal.price_at_signal,
                now,
            )
            for signal in signals
        ]
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executemany(sql, params)
        return len(signals)
