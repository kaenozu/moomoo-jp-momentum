"""
スクリーナーモジュール

ファイルパス: src/screener.py
何をするか: indicatorsテーブルから候補を抽出し、スコア順に並び替える
なぜ存在するか: 売買候補の一覧を生成するため
関連ファイル: signals.py, scoring.py, data_store.py, config.py
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .indicators import StockIndicators
from .scoring import ScoreBreakdown, Scorer
from .signals import SignalDetector, SignalResult

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


class Screener:
    """スクリーナークラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

    def get_latest_indicators(
        self,
        date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        最新の指標データを取得する

        Args:
            date: 基準日（YYYY-MM-DD）。Noneなら最新

        Returns:
            pd.DataFrame: 指標データ
        """
        if not self.db_path.exists():
            logger.error(f"データベースが見つかりません: {self.db_path}")
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            if date:
                query = """
                    SELECT i.*, s.name
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = ?
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn, params=[date])
            else:
                query = """
                    SELECT i.*, s.name
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = (SELECT MAX(date) FROM indicators)
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn)

        return df

    def get_indicators_history(
        self,
        code: str,
        days: int = 30,
    ) -> pd.DataFrame:
        """
        銘柄の指標履歴を取得する

        Args:
            code: 銘柄コード
            days: 取得日数

        Returns:
            pd.DataFrame: 指標履歴
        """
        if not self.db_path.exists():
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            query = """
                SELECT *
                FROM indicators
                WHERE code = ?
                ORDER BY date DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=[code, days])

        return df

    def screen_candidates(
        self,
        date: Optional[str] = None,
    ) -> list[Candidate]:
        """
        候補をスクリーニングする

        Args:
            date: 基準日（YYYY-MM-DD）。Noneなら最新

        Returns:
            list[Candidate]: 候補リスト（スコア降順）
        """
        # 指標データ取得
        indicators_df = self.get_latest_indicators(date)

        if indicators_df.empty:
            logger.warning("指標データがありません")
            return []

        logger.info(f"指標データ: {len(indicators_df)}銘柄")

        # シグナル検出器とスコアラー
        signal_detector = SignalDetector(self.config)
        scorer = Scorer(self.config)

        candidates = []

        for _, row in indicators_df.iterrows():
            # StockIndicatorsに変換
            indicators = StockIndicators(
                code=row.get("code", ""),
                name=row.get("name"),
                date=row.get("date", ""),
                close=row.get("close"),
                open=0.0,  # 日足のopenはここでは不要
                high=0.0,
                low=0.0,
                ma5=row.get("ma5"),
                ma25=row.get("ma25"),
                volume=0,
                volume_ma20=row.get("volume_ma20"),
                volume_ratio=row.get("volume_ratio"),
                turnover=row.get("turnover", 0),
                high_20d=row.get("high_20d"),
                high_20d_distance=row.get("distance_from_high_20d"),
                daily_return=row.get("daily_return"),
                return_5d=row.get("return_5d"),
            )

            # シグナル判定
            signal = signal_detector.detect_signal(indicators)

            # スコアリング
            score_breakdown = scorer.score(indicators, signal)

            # リスク警告を文字列に変換
            risk_warnings_str = "; ".join(signal.risk_warnings) if signal.risk_warnings else ""

            # Candidateに変換
            candidate = Candidate(
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
                updated_at=row.get("updated_at", ""),
            )

            candidates.append(candidate)

        # スコア降順にソート
        candidates.sort(key=lambda x: x.score, reverse=True)

        logger.info(
            f"スクリーニング完了: {len(candidates)}銘柄 "
            f"(候補: {sum(1 for c in candidates if c.signal_type == 'BUY_CANDIDATE')}, "
            f"監視: {sum(1 for c in candidates if c.signal_type == 'WATCH')}, "
            f"除外: {sum(1 for c in candidates if c.signal_type == 'EXCLUDE')})"
        )

        return candidates

    def save_signals_to_db(
        self,
        candidates: list[Candidate],
    ) -> int:
        """
        シグナルをSQLiteに保存する

        Args:
            candidates: 候補リスト

        Returns:
            int: 保存件数
        """
        count = 0

        with sqlite3.connect(self.db_path) as conn:
            for candidate in candidates:
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO signals
                        (code, date, signal_type, score, reason, risk_warnings,
                         price_at_signal, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            candidate.code,
                            candidate.date,
                            candidate.signal_type,
                            candidate.score,
                            candidate.reason,
                            candidate.risk_warnings,
                            candidate.close,
                            datetime.now().isoformat(),
                        ),
                    )
                    count += 1
                except sqlite3.Error as e:
                    logger.error(
                        f"シグナル保存エラー: {candidate.code} - {e}"
                    )

        return count
