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
from .models import CREATE_TABLES_SQL, DailyBar, Quote, Symbol

logger = logging.getLogger(__name__)

SymbolParams = tuple[
    str,
    str,
    str,
    str,
    str,
    int,
    str | None,
    str | None,
    str | None,
    int,
]

_SYMBOL_UPSERT_SQL = """
    INSERT INTO symbols
    (code, name, market, type, role, tradable, sector,
     benchmark_group, notes, enabled, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ON CONFLICT(code) DO UPDATE SET
        name = excluded.name,
        market = excluded.market,
        type = excluded.type,
        role = excluded.role,
        tradable = excluded.tradable,
        sector = excluded.sector,
        benchmark_group = excluded.benchmark_group,
        notes = excluded.notes,
        enabled = excluded.enabled,
        updated_at = datetime('now', 'localtime')
"""


class DataStore:
    """SQLiteデータベース操作クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)
        self._ensure_directory()
        self._init_db()

    def _ensure_directory(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """データベースを初期化する（テーブル作成 + 軽量マイグレーション）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CREATE_TABLES_SQL)
            self._ensure_columns(conn)
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

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # === 銘柄リスト関連 ===

    @staticmethod
    def _read_symbol_params(json_path: str) -> list[SymbolParams]:
        """watchlist全体を検証してからSQLパラメータへ変換する。"""
        with open(json_path, encoding="utf-8") as file:
            symbols_data = json.load(file)

        if not isinstance(symbols_data, list):
            raise ValueError("watchlist JSONのトップレベルはlistである必要があります")
        if not symbols_data:
            raise ValueError("watchlist JSONが空です。既存銘柄は変更しません")

        params: list[SymbolParams] = []
        seen_codes: set[str] = set()
        for index, item in enumerate(symbols_data):
            if not isinstance(item, dict):
                raise ValueError(f"watchlist[{index}]はobjectである必要があります")

            def required_text(field: str, default: str | None = None) -> str:
                value = item.get(field, default)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"watchlist[{index}].{field}が不正です")
                return value.strip()

            def optional_text(field: str) -> str | None:
                value = item.get(field)
                if value is None:
                    return None
                if not isinstance(value, str):
                    raise ValueError(
                        f"watchlist[{index}].{field}は文字列またはnullで指定してください"
                    )
                return value

            def boolean_flag(field: str, default: bool) -> int:
                value = item.get(field, default)
                if not isinstance(value, bool):
                    raise ValueError(
                        f"watchlist[{index}].{field}はbooleanで指定してください"
                    )
                return 1 if value else 0

            code = required_text("code")
            if code in seen_codes:
                raise ValueError(f"watchlistに重複したcodeがあります: {code}")
            seen_codes.add(code)

            params.append((
                code,
                required_text("name"),
                required_text("market", "JP"),
                required_text("type", "stock"),
                required_text("role", "trade_candidate"),
                boolean_flag("tradable", True),
                optional_text("sector"),
                optional_text("benchmark_group"),
                optional_text("notes"),
                boolean_flag("enabled", True),
            ))

        return params

    @staticmethod
    def _upsert_symbols(
        conn: sqlite3.Connection,
        params: list[SymbolParams],
    ) -> None:
        conn.executemany(_SYMBOL_UPSERT_SQL, params)

    def load_symbols_from_json(self, json_path: str) -> int:
        """JSONの銘柄を追加・更新し、未記載の既存銘柄は変更しない。"""
        params = self._read_symbol_params(json_path)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_symbols(conn, params)

        logger.info("銘柄リストを読み込みました: %s件", len(params))
        return len(params)

    def sync_symbols_from_json(self, json_path: str | None = None) -> int:
        """watchlistを権威データとしてsymbolsテーブルへ原子的に同期する。

        JSONから削除された銘柄は履歴を残したままenabled=0にする。再追加時は
        JSONのenabled値で更新する。入力検証に失敗した場合はDBを変更しない。
        """
        if json_path is None:
            json_path = self.config.watchlist_file
        params = self._read_symbol_params(json_path)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TEMP TABLE watchlist_sync_codes "
                "(code TEXT PRIMARY KEY)"
            )
            conn.executemany(
                "INSERT INTO watchlist_sync_codes(code) VALUES (?)",
                [(row[0],) for row in params],
            )
            cursor = conn.execute(
                """
                UPDATE symbols
                SET enabled = 0,
                    updated_at = datetime('now', 'localtime')
                WHERE enabled != 0
                  AND code NOT IN (SELECT code FROM watchlist_sync_codes)
                """
            )
            disabled_count = max(cursor.rowcount, 0)
            self._upsert_symbols(conn, params)

        logger.info(
            "銘柄リストを同期しました: input=%d, disabled_missing=%d",
            len(params),
            disabled_count,
        )
        return len(params)

    def get_enabled_symbols(self, include_benchmarks: bool = False) -> list[Symbol]:
        """有効な銘柄リストを取得する。"""
        query = """
            SELECT * FROM symbols
            WHERE enabled = 1
        """
        if not include_benchmarks:
            query += " AND COALESCE(role, 'trade_candidate') != 'benchmark'"
        query += " ORDER BY code"

        with self._get_connection() as conn:
            rows = conn.execute(query).fetchall()

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
            return pd.read_sql_query(query, conn, params=params)

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
            return pd.read_sql_query(query, conn, params=params)
