"""
ベンチマーク管理モジュール

ファイルパス: src/benchmark.py
何をするか: ベンチマーク価格を取得・保存する
なぜ存在するか: ポートフォリオ評価と比較検証のため
関連ファイル: quote_service.py, data_store.py, config.py

ベンチマーク:
- JP.1306: TOPIX連動ETF - 第一ベンチマーク
- JP.2559: MAXIS全世界株式（オール・カントリー）- 補助
- JP.1320: 日経平均連動ETF - 補助
- JP.2558: MAXIS米国株式（S&P500）- 補助
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .split_adjustment import SplitAdjustmentService

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkRecord:
    """ベンチマーク価格記録"""
    id: Optional[int] = None
    benchmark_code: str = ""
    date: str = ""
    close: Optional[float] = None
    daily_return: Optional[float] = None
    created_at: str = ""
    updated_at: str = ""


class BenchmarkManager:
    """ベンチマーク管理クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)
        self.split_adjustments = SplitAdjustmentService(self.db_path)

        # ベンチマークコードを設定から取得
        benchmark_config = config.get("benchmark", {})
        primary = benchmark_config.get("primary", {})
        secondary = benchmark_config.get("secondary", [])

        self.benchmark_codes = [primary.get("code", "JP.1306")]
        for s in secondary:
            code = s.get("code", "")
            if code and code not in self.benchmark_codes:
                self.benchmark_codes.append(code)

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_benchmark_prices(
        self,
        code: str,
        df: pd.DataFrame,
    ) -> int:
        """
        ベンチマーク価格を保存する

        Args:
            code: ベンチマークコード
            df: 日足DataFrame

        Returns:
            int: 保存件数
        """
        count = 0
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            prev_close: Optional[float] = None
            for _, row in df.iterrows():
                date = str(row.get("time_key", ""))[:10]
                close = row.get("close")

                # 前日終値から日次リターンを計算
                daily_return: Optional[float] = None
                if prev_close is not None and close is not None and prev_close > 0:
                    daily_return = (float(close) - float(prev_close)) / float(prev_close) * 100
                if close is not None:
                    prev_close = float(close)

                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO benchmark_prices
                        (benchmark_code, date, close, daily_return, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (code, date, close, daily_return, now, now),
                    )
                    count += 1
                except sqlite3.Error as e:
                    logger.error(f"ベンチマーク保存エラー: {code} {date} - {e}")

        return count

    def update_daily_returns(self, code: str) -> int:
        """
        ベンチマークの分割調整後前日比を更新する

        Args:
            code: ベンチマークコード

        Returns:
            int: 更新件数
        """
        count = 0
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, date, close FROM benchmark_prices
                WHERE benchmark_code = ?
                ORDER BY date
                """,
                (code,),
            )
            rows = cursor.fetchall()

            adjusted_closes = [
                self.split_adjustments.adjust_price(code, str(row["date"]), row["close"])
                for row in rows
            ]

            for i, row in enumerate(rows):
                if i == 0:
                    continue

                prev_close = adjusted_closes[i - 1]
                current_close = adjusted_closes[i]

                if prev_close and current_close and prev_close > 0:
                    daily_return = (current_close - prev_close) / prev_close * 100

                    conn.execute(
                        """
                        UPDATE benchmark_prices
                        SET daily_return = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (daily_return, now, row["id"]),
                    )
                    count += 1

        return count

    def get_benchmark_prices(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        ベンチマーク価格を取得する

        Args:
            code: ベンチマークコード
            start_date: 開始日
            end_date: 終了日

        Returns:
            pd.DataFrame: 分割調整済みベンチマーク価格データ
        """
        if not self.db_path.exists():
            return pd.DataFrame()

        query = "SELECT * FROM benchmark_prices WHERE benchmark_code = ?"
        params: list = [code]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date"

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(query, conn, params=params)

        adjusted = self.split_adjustments.apply_to_dataframe(
            df,
            code,
            date_column="date",
            price_columns=("close",),
        )
        if not adjusted.empty and "close" in adjusted.columns:
            adjusted["daily_return"] = adjusted["close"].pct_change() * 100
        return adjusted

    def get_benchmark_return(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> Optional[float]:
        """
        指定期間のベンマークリターンを計算する

        Args:
            code: ベンチマークコード
            start_date: 開始日
            end_date: 終了日

        Returns:
            Optional[float]: リターン（%）。データ不足の場合はNone
        """
        df = self.get_benchmark_prices(code, start_date, end_date)

        if df.empty or len(df) < 2:
            return None

        start_price = df.iloc[0]["close"]
        end_price = df.iloc[-1]["close"]

        if start_price and end_price and start_price > 0:
            return (end_price - start_price) / start_price * 100

        return None

    def fetch_and_save_benchmarks(
        self,
        quote_service,
        num_days: int = 120,
    ) -> dict[str, int]:
        """
        全ベンチマークの価格を取得・保存する

        Args:
            quote_service: 相場サービス
            num_days: 取得日数

        Returns:
            dict[str, int]: {ベンチマークコード: 保存件数}
        """
        results = {}

        for code in self.benchmark_codes:
            if not code:
                continue

            logger.info(f"ベンチマーク取得: {code}")
            df = quote_service.get_daily_klines(code, num=num_days)

            if df.empty:
                logger.warning(f"ベンチマーク取得失敗: {code}")
                results[code] = 0
                continue

            count = self.save_benchmark_prices(code, df)
            self.update_daily_returns(code)
            logger.info(f"ベンチマーク保存完了: {code} - {count}件")
            results[code] = count

        return results
