"""
スクリーナーモジュール。

ファイルパス: src/screener.py
何をするか: indicatorsテーブルから候補を抽出し、スコア順に並び替える
なぜ存在するか: 同じデータから常に同じ候補順を生成し、運用判断を再現可能にするため
関連ファイル: scoring.py, signals.py, ranking.py, strategy_runner.py
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .indicators import StockIndicators
from .ranking import sort_scored_candidates
from .scoring import Scorer
from .signals import SignalDetector

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """売買候補。"""

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
    return_5d_vs_benchmark: Optional[float] = None
    turnover: Optional[float] = None
    score: float = 0.0
    signal_type: str = "EXCLUDE"
    strategy_name: str = "momentum"
    reason: str = ""
    risk_warnings: str = ""
    updated_at: str = ""
    type: str = "stock"
    role: str = "trade_candidate"
    tradable: bool = True
    universe_status: str = ""
    universe_reason: str = ""


def _none_if_nan(value):
    """pandasのNaNをNoneへ変換する。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


class Screener:
    """スクリーナークラス。"""

    def __init__(self, config: Config):
        """設定とDBパス、売買価格帯を初期化する。"""
        self.config = config
        self.db_path = Path(config.database_path)
        universe = config.get("universe", {})
        self.min_trade_price = universe.get("min_trade_price", 500)
        self.max_trade_price = universe.get("max_trade_price", 20000)

    def get_latest_indicators(self, date: Optional[str] = None) -> pd.DataFrame:
        """指定日または最新日の指標を銘柄コード順で取得する。"""
        if not self.db_path.exists():
            logger.error("データベースが見つかりません: %s", self.db_path)
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            if date:
                query = """
                    SELECT i.*, s.name, s.type, s.role, s.tradable
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = ?
                      AND COALESCE(s.enabled, 1) = 1
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn, params=[date])
            else:
                query = """
                    SELECT i.*, s.name, s.type, s.role, s.tradable
                    FROM indicators i
                    LEFT JOIN symbols s ON i.code = s.code
                    WHERE i.date = (SELECT MAX(date) FROM indicators)
                      AND COALESCE(s.enabled, 1) = 1
                    ORDER BY i.code
                """
                df = pd.read_sql_query(query, conn)
        return df

    def get_indicators_history(self, code: str, days: int = 30) -> pd.DataFrame:
        """指定銘柄の指標履歴を新しい順で取得する。"""
        if not self.db_path.exists():
            return pd.DataFrame()
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql_query(
                """
                SELECT * FROM indicators
                WHERE code = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                conn,
                params=[code, days],
            )

    def _row_to_indicators(self, row: pd.Series) -> StockIndicators:
        """DB行をStockIndicatorsへ変換する。必須終値の欠損は明示的に拒否する。"""
        code = _none_if_nan(row.get("code")) or ""
        date = _none_if_nan(row.get("date")) or ""
        close = _none_if_nan(row.get("close"))
        if close is None:
            raise ValueError(f"終値がありません: code={code}, date={date}")

        return StockIndicators(
            code=code,
            name=_none_if_nan(row.get("name")),
            date=date,
            close=float(close),
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
            return_20d=_none_if_nan(row.get("return_20d")),
            return_60d=_none_if_nan(row.get("return_60d")),
            return_5d_vs_benchmark=_none_if_nan(row.get("return_5d_vs_benchmark")),
            return_20d_vs_benchmark=_none_if_nan(row.get("return_20d_vs_benchmark")),
            return_60d_vs_benchmark=_none_if_nan(row.get("return_60d_vs_benchmark")),
            relative_strength_rank=_none_if_nan(row.get("relative_strength_rank")),
            history_days=int(_none_if_nan(row.get("history_days")) or 0),
            volume_ratio_percentile=_none_if_nan(row.get("volume_ratio_percentile")),
            volume_ratio_rank=_none_if_nan(row.get("volume_ratio_rank")),
            relative_volume_ratio=_none_if_nan(row.get("relative_volume_ratio")),
            market_median_volume_ratio=_none_if_nan(row.get("market_median_volume_ratio")),
        )

    def _apply_universe(self, candidate: Candidate) -> Candidate:
        """銘柄ロール・売買可否・価格帯を候補判定へ反映する。"""
        role = candidate.role or "trade_candidate"
        tradable = bool(candidate.tradable)
        close = candidate.close or 0

        if role == "benchmark":
            candidate.universe_status = "BENCHMARK"
            candidate.universe_reason = "ベンチマーク専用のため買い候補にはしません"
            candidate.signal_type = "BENCHMARK"
            candidate.reason = f"ベンチマーク: {candidate.reason}"
            return candidate

        if role == "excluded":
            candidate.universe_status = "EXCLUDE"
            candidate.universe_reason = "role=excluded のため対象外です"
            candidate.signal_type = "EXCLUDE"
            candidate.reason = f"除外: {candidate.universe_reason}"
            return candidate

        if role == "watch_only" or not tradable:
            candidate.universe_status = "WATCH"
            candidate.universe_reason = f"role={role}, tradable={tradable} のため監視扱いです"
            if candidate.signal_type == "BUY_CANDIDATE":
                candidate.signal_type = "WATCH"
                candidate.reason = f"監視候補: {candidate.reason}（{candidate.universe_reason}）"
            return candidate

        if close and close < self.min_trade_price:
            candidate.universe_status = "EXCLUDE"
            candidate.universe_reason = f"1株価格が下限{self.min_trade_price:,.0f}円未満です"
            candidate.signal_type = "EXCLUDE"
            candidate.reason = f"除外: {candidate.universe_reason}"
            return candidate

        if close and close > self.max_trade_price:
            candidate.universe_status = "WATCH"
            candidate.universe_reason = f"1株価格が上限{self.max_trade_price:,.0f}円を超えています"
            if candidate.signal_type == "BUY_CANDIDATE":
                candidate.signal_type = "WATCH"
                candidate.reason = f"監視候補: {candidate.reason}（{candidate.universe_reason}）"
            return candidate

        candidate.universe_status = "TRADE_CANDIDATE"
        candidate.universe_reason = "実売買候補ユニバース内"
        return candidate

    def screen_candidates(self, date: Optional[str] = None) -> list[Candidate]:
        """候補を評価し、score DESC・code ASCで返す。"""
        indicators_df = self.get_latest_indicators(date)
        if indicators_df.empty:
            logger.warning("指標データがありません")
            return []

        signal_detector = SignalDetector(self.config)
        scorer = Scorer(self.config)

        # クロスセクション統計（パーセンタイル等）を計算
        from .indicators import add_cross_sectional_stats

        pairs: list[tuple[pd.Series, StockIndicators]] = []
        for _, row in indicators_df.iterrows():
            ind = self._row_to_indicators(row)
            pairs.append((row, ind))
        all_indicators = [ind for _, ind in pairs]
        add_cross_sectional_stats(all_indicators)

        candidates: list[Candidate] = []

        for row, indicators in pairs:
            signal = signal_detector.detect_signal(indicators)
            score_breakdown = scorer.score(indicators, signal)
            risk_warnings_str = "; ".join(signal.risk_warnings) if signal.risk_warnings else ""

            candidate = Candidate(
                code=indicators.code,
                name=indicators.name,
                date=indicators.date,
                strategy_name=signal.strategy_name,
                close=indicators.close,
                daily_return=indicators.daily_return,
                ma5=indicators.ma5,
                ma25=indicators.ma25,
                high_20d=indicators.high_20d,
                distance_from_high_20d=indicators.high_20d_distance,
                volume_ratio=indicators.volume_ratio,
                return_5d=indicators.return_5d,
                return_5d_vs_benchmark=indicators.return_5d_vs_benchmark,
                turnover=indicators.turnover,
                score=score_breakdown.total,
                signal_type=signal.signal_type,
                reason=signal.reason,
                risk_warnings=risk_warnings_str,
                updated_at=_none_if_nan(row.get("updated_at")) or "",
                type=_none_if_nan(row.get("type")) or "stock",
                role=_none_if_nan(row.get("role")) or "trade_candidate",
                tradable=bool(_none_if_nan(row.get("tradable", 1))),
            )
            candidates.append(self._apply_universe(candidate))

        candidates = sort_scored_candidates(candidates)
        logger.info(
            "スクリーニング完了: %s銘柄 (候補: %s, 監視: %s, 除外: %s, benchmark: %s)",
            len(candidates),
            sum(1 for c in candidates if c.signal_type == "BUY_CANDIDATE"),
            sum(1 for c in candidates if c.signal_type == "WATCH"),
            sum(1 for c in candidates if c.signal_type == "EXCLUDE"),
            sum(1 for c in candidates if c.signal_type == "BENCHMARK"),
        )
        return candidates

    def save_signals_to_db(self, candidates: list[Candidate]) -> int:
        """シグナルをSQLiteに保存する。benchmarkはsignalsには保存しない。"""
        rows = [c for c in candidates if c.signal_type != "BENCHMARK"]
        if not rows:
            return 0
        now = datetime.now().isoformat()
        sql = """
            INSERT OR REPLACE INTO signals
            (code, date, signal_type, strategy_name, score, reason, risk_warnings, price_at_signal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                c.code,
                c.date,
                c.signal_type,
                c.strategy_name,
                c.score,
                c.reason,
                c.risk_warnings,
                c.close,
                now,
            )
            for c in rows
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(sql, params)
        return len(rows)
