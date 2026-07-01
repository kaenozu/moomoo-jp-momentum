"""
戦略ランナー

ファイルパス: src/strategy_runner.py
何をするか: 複数戦略を一括実行し、シグナルを保存する
なぜ存在するか: 戦略比較のため、同じ条件で複数戦略を検証するため
関連ファイル: strategies/*.py, relative_strength.py, screener.py
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .strategies import StrategyRegistry
from .indicators import StockIndicators

logger = logging.getLogger(__name__)


class StrategyRunner:
    """戦略ランナー"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

    def run_all(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_names: Optional[list[str]] = None,
    ) -> dict[str, list]:
        """全戦略を実行し、シグナルを保存"""
        if strategy_names is None:
            strategy_names = StrategyRegistry.list_names()

        all_signals = {}

        for name in strategy_names:
            strategy = StrategyRegistry.get(name, self.config)
            signals = []
            bench_return_5d = None

            for ind in indicators_list:
                # 戦略ごとにベンチマークリターンを取得
                if bench_return_5d is None and ind.return_5d_vs_benchmark is not None:
                    bench_return_5d = ind.return_5d

                result = strategy.evaluate(ind, {"return_5d": bench_return_5d})
                signals.append(result)

            saved = self._save_signals(signals, name)
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
        return self.run_all(indicators_list, target_date, [strategy_name]).get(strategy_name, 0)

    def _save_signals(self, signals: list, strategy_name: str) -> int:
        """戦略のシグナルをsignalsテーブルに保存"""
        if not signals:
            return 0

        now = datetime.now().isoformat()
        # 既存の同一strategy_name+dateのシグナルを削除（UPSERT代替）
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM signals WHERE strategy_name = ? AND date = ?",
                (strategy_name, signals[0].date if signals else ""),
            )

            sql = """
                INSERT INTO signals
                (code, date, signal_type, strategy_name, score, reason, risk_warnings, price_at_signal, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = []
            for s in signals:
                warnings_str = "; ".join(s.risk_warnings) if s.risk_warnings else ""
                params.append((
                    s.code, s.date, s.signal_type, strategy_name,
                    s.score, s.reason, warnings_str, s.price_at_signal, now,
                ))

            conn.executemany(sql, params)

        return len(signals)
