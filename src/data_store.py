"""
SQLiteデータ保存モジュール

ファイルパス: src/data_store.py
何をするか: SQLiteデータベースへのデータ保存と取得を行う
なぜ存在するか: 相場データ永続化のため
関連ファイル: models.py, config.py
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .models import (
    CREATE_TABLES_SQL,
    DailyBar,
    Quote,
    Symbol,
)

logger = logging.getLogger(__name__)


class DataStore:
    """SQLiteデータベース操作クラス"""

    def __init__(self, config: Config):
        self.db_path = Path(config.database_path)
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self) -> None:
        """データベースファイルのディレクトリを確保する"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """データベースを初期化する（テーブル作成）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CREATE_TABLES_SQL)
            logger.info("データベースを初期化しました: %s", self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # === 銘柄リスト関連 ===

    def load_symbols_from_json(self, json_path: str) -> int:
        """
        JSONファイルから銘柄リストを読み込んで保存する。

        symbols.json の type / role / tradable / benchmark_group / notes もDBに保存する。
        role=benchmark はデフォルトでは通常スクリーニング対象から外すため enabled=0 にする。
        """
        with open(json_path, encoding="utf-8") as f:
            symbols_data = json.load(f)

        count = 0
        with self._get_connection() as conn:
            for item in symbols_data:
                code = item["code"]
                role = item.get("role", "trade_candidate")
                enabled_default = role != "benchmark"
                enabled = item.get("enabled", enabled_default)

                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO symbols
                        (code, name, market, type, role, tradable, sector,
                         benchmark_group, notes, enabled, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                        """,
                        (
                            code,
                            item["name"],
                            item.get("market", "JP"),
                            item.get("type", "stock"),
                            role,
                            1 if item.get("tradable", True) else 0,
                            item.get("sector"),
                            item.get("benchmark_group"),
                            item.get("notes"),
                            1 if enabled else 0,
                        ),
                    )
                    count += 1
                except sqlite3.Error as e:
                    logger.error("銘柄保存エラー: %s - %s", code, e)

        logger.info("銘柄リストを読み込みました: %s件", count)
        return count

    def get_enabled_symbols(self) -> list[Symbol]:
        """有効な銘柄リストを取得する"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM symbols
                WHERE enabled = 1
                  AND COALESCE(role, 'trade_candidate') != 'benchmark'
                ORDER BY code
                """
            )
            rows = cursor.fetchall()

        return [
            Symbol(
                code=row["code"],
                name=row["name"],
                market=row["market"],
                sector=row["sector"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_symbol_codes(self) -> list[str]:
        """有効な銘柄コードのリストを取得する"""
        return [s.code for s in self.get_enabled_symbols()]

    # === リアルタイム株価関連 ===

    def save_quote(self, quote: Quote) -> None:
        """リアルタイム株価を保存する"""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO quotes
                (code, timestamp, price, open, high, low, volume, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote.code,
                    quote.timestamp,
                    quote.price,
                    quote.open,
                    quote.high,
                    quote.low,
                    quote.volume,
                    quote.turnover,
                ),
            )

    def save_quotes_batch(self, quotes: list[Quote]) -> int:
        """複数の株価データを一括保存する"""
        if not quotes:
            return 0

        sql = """
            INSERT INTO quotes
            (code, timestamp, price, open, high, low, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                q.code, q.timestamp, q.price, q.open,
                q.high, q.low, q.volume, q.turnover,
            )
            for q in quotes
        ]

        with self._get_connection() as conn:
            conn.executemany(sql, params)

        return len(quotes)

    # === 日足関連 ===

    def save_daily_bar(self, bar: DailyBar) -> None:
        """日足データを保存する（重複は上書き）"""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (code, date, open, high, low, close, volume, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.code,
                    bar.date,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.turnover,
                ),
            )

    def save_daily_bars_batch(self, bars: list[DailyBar]) -> int:
        """複数の日足データを一括保存する"""
        if not bars:
            return 0

        sql = """
            INSERT OR REPLACE INTO daily_bars
            (code, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (b.code, b.date, b.open, b.high, b.low, b.close, b.volume, b.turnover)
            for b in bars
        ]

        with self._get_connection() as conn:
            conn.executemany(sql, params)

        return len(bars)

    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """日足データを取得する"""
        query = "SELECT * FROM daily_bars WHERE code = ?"
        params: list = [code]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        return df

    def save_dataframe_to_daily_bars(
        self,
        df: pd.DataFrame,
        code: str,
    ) -> int:
        """DataFrameから日足データを保存する"""
        if df.empty:
            return 0

        bars = [
            DailyBar(
                code=code,
                date=str(row.get("time_key", ""))[:10],
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=row.get("volume"),
                turnover=row.get("turnover"),
            )
            for _, row in df.iterrows()
        ]

        return self.save_daily_bars_batch(bars)

    # === ベンチマーク関連 ===

    def save_benchmark_price(
        self,
        benchmark_code: str,
        date: str,
        price: float,
    ) -> None:
        """ベンチマーク価格を保存する"""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_prices
                (benchmark_code, date, close, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
                """,
                (benchmark_code, date, price),
            )

    def get_benchmark_prices(
        self,
        benchmark_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """ベンチマーク価格を取得する"""
        query = "SELECT * FROM benchmark_prices WHERE benchmark_code = ?"
        params: list = [benchmark_code]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)

        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date"

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        return df
