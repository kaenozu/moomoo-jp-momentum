"""
スクリーナーモジュール

ファイルパス: src/screener.py
何をするか: indicatorsテーブルから候補を抽出し、スコア順に並び替える
なぜ存在するか: 売買候補の一覧を生成するため
関連ファイル: signals.py, scoring.py, data_store.py, config.py
"""

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .indicators import StockIndicators
from .scoring import Scorer
from .signals import SignalDetector

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """売買候補"""
    code: str
    name: Optional[str]
    date: str
    close: Optional[float] = None
    daily_return: Optional[float] = None
    ma5: Optional[float] = None
    ma25: Optional[float] = None
    high_20d: Optional[float] = None
    distance_from_high_20d: Optional[float] = None
    volume_ratio: Optional[float] = None
    return_5d: Optional[float] = None
    turnover: Optional[float] = None
    score: float = 0.0
    signal_type: str = "EXCLUDE"
    reason: str = ""
    risk_warnings: str = ""
    updated_at: str = ""


def _none_if_nan(value):
    """pandasのNaNをNoneに変換する"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


class Screener:
    """スクリーナークラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

    def get_latest_indicators(self, date: Optional[str] = None) -> pd.DataFrame:
        """最新の指標データを取得する"""
        if not self.db_path.exists():
            logger.error("データベースが見つかりません: %s", self.db_path)
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            if date:
                query = """
                    SELECT i.*, s.name
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = ?
                      AND COALESCE(s.enabled, 1) = 1
                      AND COALESCE(s.role, 'trade_candidate') != 'benchmark'
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn, params=[date])
            else:
                query = """
                    SELECT i.*, s.name
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = (SELECT MAX(date) FROM indicators)
                      AND COALESCE(s.enabled, 1) = 1
                      AND COALESCE(s.role, 'trade_candidate') != 'benchmark'
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn)

        return df

    def get_indicators_history(self, code: str, days: int = 30) -> pd.DataFrame:
        """銘柄の指標履歴を取得する"""
        if not self.db_path.exists():
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT *
                FROM indicators
                WHERE code = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                conn,
                params=[code, days],
            )

        return df

    def _row_to_indicators(self, row: pd.Series) -> StockIndicators:
        """indicators行をStockIndicatorsへ変換する"""
        return StockIndicators(
            code=_none_if_nan(row.get("code")) or "",
            name=_none_if_nan(row.get("name")),
            date=_none_if_nan(row.get("date")) or "",
            close=_none_if_nan(row.get("close")),
            open=0.0,
            high=0.0,
            low=0.0,
            ma5=_none_if_nan(row.get("ma5")),
            ma25=_none_if_nan(row.get("ma25")),
            volume=int(_none_if_nan(row.get("volume")) or 0),
            volume_ma20=_none_if_nan(row.get("volume_ma20")),
            volume_ratio=_none_if_nan(row.get("volume_ratio")),
            turnover=_none_if_nan(row.get("turnover")) or 0,
            high_20d=_none_if_nan(row.get("high_20d")),
            high_20d_distance=_none_if_nan(row.get("distance_from_high_20d")),
            daily_return=_none_if_nan(row.get("daily_return")),
            return_5d=_none_if_nan(row.get("return_5d")),
            history_days=int(_none_if_nan(row.get("history_days")) or 0),
        )

    def screen_candidates(self, date: Optional[str] = None) -> list[Candidate]:
        """候補をスクリーニングする"""
        indicators_df = self.get_latest_indicators(date)

        if indicators_df.empty:
            logger.warning("指標データがありません")
            return []

        logger.info("指標データ: %s銘柄", len(indicators_df))

        signal_detector = SignalDetector(self.config)
        scorer = Scorer(self.config)

        candidates = []

        for _, row in indicators_df.iterrows():
            indicators = self._row_to_indicators(row)
            signal = signal_detector.detect_signal(indicators)
            score_breakdown = scorer.score(indicators, signal)

            risk_warnings_str = "; ".join(signal.risk_warnings) if signal.risk_warnings else ""

            candidates.append(Candidate(
                code=indicators.code,
                name=indicators.name,
                date=indicators.date,
                close=indicators.close,
                daily_return=indicators.daily_return,
                ma5=indicators.ma5,
                ma25=indicators.ma25,
                high_20d=indicators.high_20d,
                distance_from_high_20d=indicators.high_20d_distance,
                volume_ratio=indicators.volume_ratio,
                return_5d=indicators.return_5d,
                turnover=indicators.turnover,
                score=score_breakdown.total,
                signal_type=signal.signal_type,
                reason=signal.reason,
                risk_warnings=risk_warnings_str,
                updated_at=_none_if_nan(row.get("updated_at")) or "",
            ))

        candidates.sort(key=lambda x: x.score, reverse=True)

        logger.info(
            "スクリーニング完了: %s銘柄 (候補: %s, 監視: %s, 除外: %s)",
            len(candidates),
            sum(1 for c in candidates if c.signal_type == "BUY_CANDIDATE"),
            sum(1 for c in candidates if c.signal_type == "WATCH"),
            sum(1 for c in candidates if c.signal_type == "EXCLUDE"),
        )

        return candidates

    def save_signals_to_db(self, candidates: list[Candidate]) -> int:
        """シグナルをSQLiteに保存する"""
        if not candidates:
            return 0

        now = datetime.now().isoformat()
        sql = """
            INSERT OR REPLACE INTO signals
            (code, date, signal_type, score, reason, risk_warnings,
             price_at_signal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (c.code, c.date, c.signal_type, c.score, c.reason,
             c.risk_warnings, c.close, now)
            for c in candidates
        ]

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(sql, params)

        return len(candidates)
