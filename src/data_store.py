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
from collections.abc import Collection
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config
from .models import CREATE_TABLES_SQL, DailyBar, Quote, Symbol
from .split_adjustment import SplitAdjustmentService

logger = logging.getLogger(__name__)


class DataStore:
    """SQLiteデータベース操作クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self._ensure_directory()
        self._init_db()
        self.split_adjustments = SplitAdjustmentService(self.db_path)

    def _ensure_directory(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """DB初期化と旧signals一意制約の移行を行う。"""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(CREATE_TABLES_SQL)
            self._ensure_columns(conn)
            self._migrate_signals_unique_key(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            logger.info("データベースを初期化しました: %s", self.db_path)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """既存DB向けに不足カラムを追加する。"""
        def add_missing(table: str, columns: dict[str, str]) -> None:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        add_missing("symbols", {
            "type": "TEXT NOT NULL DEFAULT 'stock'",
            "role": "TEXT NOT NULL DEFAULT 'trade_candidate'",
            "tradable": "INTEGER NOT NULL DEFAULT 1",
            "benchmark_group": "TEXT",
            "notes": "TEXT",
        })
        add_missing("daily_bars", {
            "source": "TEXT NOT NULL DEFAULT 'moomoo'",
            "turnover_source": "TEXT NOT NULL DEFAULT 'actual'",
        })
        add_missing("indicators", {
            "volume": "INTEGER",
            "return_20d": "REAL",
            "return_60d": "REAL",
            "history_days": "INTEGER",
            "return_5d_vs_benchmark": "REAL",
            "return_20d_vs_benchmark": "REAL",
            "return_60d_vs_benchmark": "REAL",
            "relative_strength_rank": "INTEGER",
            "volume_ratio_percentile": "REAL",
            "volume_ratio_rank": "INTEGER",
            "relative_volume_ratio": "REAL",
            "market_median_volume_ratio": "REAL",
        })
        add_missing("virtual_orders", {
            "exit_reason": "TEXT",
            "order_reason": "TEXT",
        })
        add_missing("signals", {
            "strategy_name": "TEXT NOT NULL DEFAULT 'momentum'",
        })

    def _migrate_signals_unique_key(self, conn: sqlite3.Connection) -> None:
        """旧UNIQUE(code,date)をUNIQUE(strategy_name,code,date)へ移行する。"""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        table_sql = (row[0] if row and row[0] else "").replace(" ", "").lower()
        legacy = (
            "unique(code,date)" in table_sql
            and "unique(strategy_name,code,date)" not in table_sql
        )
        if legacy:
            conn.executescript(
                """
                ALTER TABLE signals RENAME TO signals_legacy;
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    strategy_name TEXT NOT NULL DEFAULT 'momentum',
                    score REAL,
                    reason TEXT,
                    risk_warnings TEXT,
                    price_at_signal REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(strategy_name, code, date)
                );
                INSERT INTO signals
                    (id, code, date, signal_type, strategy_name, score, reason,
                     risk_warnings, price_at_signal, created_at)
                SELECT id, code, date, signal_type,
                       COALESCE(strategy_name, 'momentum'), score, reason,
                       risk_warnings, price_at_signal, created_at
                FROM signals_legacy;
                DROP TABLE signals_legacy;
                """
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_strategy_code_date "
            "ON signals(strategy_name, code, date)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def load_symbols_from_json(self, json_path: str) -> int:
        """
        JSONファイルから銘柄リストを読み込んで保存する。

        benchmark銘柄も日足取得・相対強度計算に必要なため enabled=1 で保存する。
        通常スクリーニングでは role=benchmark を別途除外する。
        """
        with open(json_path, encoding="utf-8") as f:
            symbols_data = json.load(f)

        params = []
        for item in symbols_data:
            params.append((
                item["code"],
                item["name"],
                item.get("market", "JP"),
                item.get("type", "stock"),
                item.get("role", "trade_candidate"),
                1 if item.get("tradable", True) else 0,
                item.get("sector"),
                item.get("benchmark_group"),
                item.get("notes"),
                1 if item.get("enabled", True) else 0,
            ))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO symbols
                (code, name, market, type, role, tradable, sector,
                 benchmark_group, notes, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """,
                params,
            )

        logger.info("銘柄リストを読み込みました: %s件", len(params))
        return len(params)

    def sync_symbols_from_json(self, json_path: str | None = None) -> int:
        """
        毎回実行用の同期処理。load_symbols_from_json と同じ。
        日次更新・日次サイクル起動時に毎回呼び出して symbols.json の変更をDBに反映する。
        """
        if json_path is None:
            json_path = self.config.watchlist_file
        return self.load_symbols_from_json(json_path)

    def get_enabled_symbols(
        self,
        include_benchmarks: bool = False,
        markets: Collection[str] | None = None,
    ) -> list[Symbol]:
        """有効な銘柄リストを取得する。"""
        query = """
            SELECT * FROM symbols
            WHERE enabled = 1
        """
        params: list[str] = []

        if markets is not None:
            market_values = [markets] if isinstance(markets, str) else markets
            normalized_markets = sorted({
                market.strip().upper()
                for market in market_values
                if market.strip()
            })
            if not normalized_markets:
                raise ValueError("marketsは空にできません")
            placeholders = ", ".join("?" for _ in normalized_markets)
            query += f" AND UPPER(market) IN ({placeholders})"
            params.extend(normalized_markets)

        if not include_benchmarks:
            query += " AND COALESCE(role, 'trade_candidate') != 'benchmark'"
        query += " ORDER BY code"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            Symbol(
                code=row["code"],
                name=row["name"],
                market=row["market"],
                type=row["type"],
                role=row["role"],
                tradable=bool(row["tradable"]),
                sector=row["sector"],
                benchmark_group=row["benchmark_group"],
                notes=row["notes"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_symbol_codes(self) -> list[str]:
        return [s.code for s in self.get_enabled_symbols()]

    # === リアルタイム株価関連 ===

    def save_quote(self, quote: Quote) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO quotes
                (code, timestamp, price, open, high, low, volume, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (quote.code, quote.timestamp, quote.price, quote.open, quote.high, quote.low, quote.volume, quote.turnover),
            )

    def save_quotes_batch(self, quotes: list[Quote]) -> int:
        if not quotes:
            return 0
        sql = """
            INSERT INTO quotes
            (code, timestamp, price, open, high, low, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [(q.code, q.timestamp, q.price, q.open, q.high, q.low, q.volume, q.turnover) for q in quotes]
        with self._get_connection() as conn:
            conn.executemany(sql, params)
        return len(quotes)

    # === 日足関連 ===

    def save_daily_bar(self, bar: DailyBar) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (code, date, open, high, low, close, volume, turnover, source, turnover_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.code, bar.date, bar.open, bar.high, bar.low, bar.close,
                    bar.volume, bar.turnover, bar.source, bar.turnover_source,
                ),
            )

    def save_daily_bars_batch(self, bars: list[DailyBar]) -> int:
        if not bars:
            return 0
        sql = """
            INSERT OR REPLACE INTO daily_bars
            (code, date, open, high, low, close, volume, turnover, source, turnover_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (
                b.code, b.date, b.open, b.high, b.low, b.close,
                b.volume, b.turnover, b.source, b.turnover_source,
            )
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
        return self.split_adjustments.apply_to_dataframe(df, code, date_column="date")

    def save_dataframe_to_daily_bars(
        self,
        df: pd.DataFrame,
        code: str,
        source: str = "moomoo",
        turnover_source: str = "actual",
    ) -> int:
        if df.empty:
            return 0
        bars = [
            DailyBar(
                code=code,
                date=str(row.get("time_key", row.get("date", "")))[:10],
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=row.get("volume"),
                turnover=row.get("turnover"),
                source=str(row.get("source") or source),
                turnover_source=str(row.get("turnover_source") or turnover_source),
            )
            for _, row in df.iterrows()
        ]
        return self.save_daily_bars_batch(bars)

    # === ベンチマーク関連 ===

    def save_benchmark_price(self, benchmark_code: str, date: str, price: float) -> None:
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
        return self.split_adjustments.apply_to_dataframe(
            df,
            benchmark_code,
            date_column="date",
            price_columns=("close",),
        )
