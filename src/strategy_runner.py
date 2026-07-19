"""
戦略ランナー。

ファイルパス: src/strategy_runner.py
何をするか: 複数戦略を一括実行し、スコア付きシグナルを保存する
なぜ存在するか: 戦略比較とフォワード検証を同じランキング規則で実行するため
関連ファイル: strategies/*.py, scoring.py, ranking.py, screener.py
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .indicators import StockIndicators
from .ranking import sort_scored_candidates
from .scoring import Scorer
from .strategies import StrategyRegistry, StrategyResult

logger = logging.getLogger(__name__)


class StrategyRunner:
    """戦略ランナー。"""

    def __init__(self, config: Config):
        """設定、DBパス、共通スコアラーを初期化する。"""
        self.config = config
        self.db_path = Path(config.database_path)
        self.scorer = Scorer(config)

    def run_all(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_names: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """全戦略を実行し、score DESC・code ASCの順でシグナルを保存する。"""
        if strategy_names is None:
            strategy_names = StrategyRegistry.list_names()

        all_signals: dict[str, int] = {}

        for name in strategy_names:
            strategy = StrategyRegistry.get(name, self.config)
            signals: list[StrategyResult] = []
            bench_return_5d = None

            for indicators in indicators_list:
                if (
                    bench_return_5d is None
                    and indicators.return_5d_vs_benchmark is not None
                ):
                    bench_return_5d = indicators.return_5d

                result = strategy.evaluate(
                    indicators,
                    {"return_5d": bench_return_5d},
                )
                result.score = self.scorer.score(indicators, result).total
                signals.append(result)

            ranked_signals = sort_scored_candidates(signals)
            saved = self._save_signals(ranked_signals, name, target_date)
            all_signals[name] = saved
            logger.info("戦略 %s: %d件保存", name, saved)

        return all_signals

    def run_one(
        self,
        indicators_list: list[StockIndicators],
        target_date: str,
        strategy_name: str,
    ) -> int:
        """単一戦略を実行して保存件数を返す。"""
        results = self.run_all(indicators_list, target_date, [strategy_name])
        return results.get(strategy_name, 0)

    def _save_signals(
        self,
        signals: list[StrategyResult],
        strategy_name: str,
        target_date: str,
    ) -> int:
        """戦略のシグナルをsignalsテーブルにランキング順で保存する。"""
        if not signals:
            return 0

        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM signals WHERE strategy_name = ? AND date = ?",
                (strategy_name, target_date),
            )

            sql = """
                INSERT INTO signals
                (code, date, signal_type, strategy_name, score, reason,
                 risk_warnings, price_at_signal, created_at)
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
