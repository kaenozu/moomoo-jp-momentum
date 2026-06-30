"""
相対強度計算モジュール

ファイルパス: src/relative_strength.py
何をするか: ベンチマーク比較ベースの相対強度を計算する
なぜ存在するか: 戦術の検証精度を上げるため
関連ファイル: benchmark.py, indicators.py, config.py

計算する指標:
- 5営業日相対リターン
- 20営業日相対リターン
- 60営業日相対リターン
- 相対強度順位
"""

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class RelativeStrength:
    """相対強度データ"""
    code: str
    date: str
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None
    benchmark_return_5d: Optional[float] = None
    benchmark_return_20d: Optional[float] = None
    benchmark_return_60d: Optional[float] = None
    vs_benchmark_5d: Optional[float] = None
    vs_benchmark_20d: Optional[float] = None
    vs_benchmark_60d: Optional[float] = None
    rank_5d: Optional[int] = None
    rank_20d: Optional[int] = None
    rank_60d: Optional[int] = None


class RelativeStrengthCalculator:
    """相対強度計算クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

        # ベンチマークコード
        rs_config = config.get("relative_strength", {})
        self.default_benchmark = rs_config.get(
            "default_benchmark_for_screening", "JP.1306"
        )
        self.periods = rs_config.get("periods", [5, 20, 60])

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _calc_return(
        self,
        code: str,
        target_date: str,
        days: int,
    ) -> Optional[float]:
        """
        指定日からのリターンを計算する

        Args:
            code: 銘柄コード
            target_date: 基準日
            days: 営業日数

        Returns:
            float: リターン（%）
        """
        with self._get_connection() as conn:
            # 基準日より前のデータを取得
            cursor = conn.execute(
                """
                SELECT date, close FROM daily_bars
                WHERE code = ? AND date <= ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (code, target_date, days + 1),
            )
            rows = cursor.fetchall()

        if len(rows) < 2:
            return None

        # 最新の終値とdays前の終値を比較
        latest_close = rows[0]["close"]
        past_close = rows[-1]["close"]

        if past_close and past_close > 0:
            return (latest_close - past_close) / past_close * 100

        return None

    def calc_benchmark_return(
        self,
        benchmark_code: str,
        target_date: str,
        days: int,
    ) -> Optional[float]:
        """
        ベンチマークのリターンを計算する

        Args:
            benchmark_code: ベンチマークコード
            target_date: 基準日
            days: 営業日数

        Returns:
            float: リターン（%）
        """
        return self._calc_return(benchmark_code, target_date, days)

    def calc_relative_strength(
        self,
        code: str,
        target_date: str,
        benchmark_code: Optional[str] = None,
    ) -> RelativeStrength:
        """
        相対強度を計算する

        Args:
            code: 銘柄コード
            target_date: 基準日
            benchmark_code: ベンチマークコード

        Returns:
            RelativeStrength: 相対強度データ
        """
        if benchmark_code is None:
            benchmark_code = self.default_benchmark

        rs = RelativeStrength(code=code, date=target_date)

        # 各期間のリターンを計算
        for period in self.periods:
            stock_return = self._calc_return(code, target_date, period)
            benchmark_return = self._calc_return(
                benchmark_code, target_date, period
            )

            if period == 5:
                rs.return_5d = stock_return
                rs.benchmark_return_5d = benchmark_return
                if stock_return is not None and benchmark_return is not None:
                    rs.vs_benchmark_5d = stock_return - benchmark_return
            elif period == 20:
                rs.return_20d = stock_return
                rs.benchmark_return_20d = benchmark_return
                if stock_return is not None and benchmark_return is not None:
                    rs.vs_benchmark_20d = stock_return - benchmark_return
            elif period == 60:
                rs.return_60d = stock_return
                rs.benchmark_return_60d = benchmark_return
                if stock_return is not None and benchmark_return is not None:
                    rs.vs_benchmark_60d = stock_return - benchmark_return

        return rs

    def calc_all_ranks(
        self,
        codes: list[str],
        target_date: str,
        benchmark_code: Optional[str] = None,
    ) -> dict[str, RelativeStrength]:
        """
        全銘柄の相対強度と順位を計算する

        Args:
            codes: 銘柄コードのリスト
            target_date: 基準日
            benchmark_code: ベンチマークコード

        Returns:
            dict[str, RelativeStrength]: 銘柄コードをキーとした相対強度データ
        """
        results = {}

        for code in codes:
            rs = self.calc_relative_strength(code, target_date, benchmark_code)
            results[code] = rs

        # 順位を計算
        for period in self.periods:
            attr = f"vs_benchmark_{period}"
            rank_attr = f"rank_{period}"

            # リターンでソート（降順）
            sorted_items = sorted(
                [(code, getattr(rs, attr)) for code, rs in results.items()
                 if getattr(rs, attr) is not None],
                key=lambda x: x[1],
                reverse=True,
            )

            for rank, (code, _) in enumerate(sorted_items, 1):
                setattr(results[code], rank_attr, rank)

        return results

    def save_to_indicators(
        self,
        rs_data: dict[str, RelativeStrength],
    ) -> int:
        """
        相対強度データをindicatorsテーブルに保存する

        Args:
            rs_data: 相対強度データの辞書

        Returns:
            int: 保存件数
        """
        count = 0
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            for code, rs in rs_data.items():
                try:
                    conn.execute(
                        """
                        UPDATE indicators SET
                            return_5d_vs_benchmark = ?,
                            return_20d_vs_benchmark = ?,
                            return_60d_vs_benchmark = ?,
                            relative_strength_rank = ?,
                            updated_at = ?
                        WHERE code = ? AND date = ?
                        """,
                        (
                            rs.vs_benchmark_5d,
                            rs.vs_benchmark_20d,
                            rs.vs_benchmark_60d,
                            rs.rank_5d,
                            now,
                            code,
                            rs.date,
                        ),
                    )
                    count += 1
                except sqlite3.Error as e:
                    logger.error(f"相対強度保存エラー: {code} - {e}")

        return count


# datetimeインポートを追加
from datetime import datetime
