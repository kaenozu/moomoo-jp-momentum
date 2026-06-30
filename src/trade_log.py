"""
手動売買ログ管理モジュール

ファイルパス: src/trade_log.py
何をするか: 手動売買ログのCRUD操作を行う
なぜ存在するか: 売買記録を一元管理するため
関連ファイル: models.py, data_store.py, config.py

注意:
    - API発注は行わない
    - すべて手動入力
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """売買記録"""
    id: Optional[int] = None
    code: str = ""
    side: str = ""
    quantity: int = 0
    price: float = 0.0
    executed_at: str = ""
    reason: str = ""
    exit_rule: str = ""
    memo: str = ""
    created_at: str = ""
    updated_at: str = ""


class TradeLog:
    """手動売買ログ管理クラス"""

    def __init__(self, config: Config):
        self.db_path = Path(config.database_path)

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TradeRecord:
        return TradeRecord(
            id=row["id"],
            code=row["code"],
            side=row["side"],
            quantity=row["quantity"],
            price=row["price"],
            executed_at=row["executed_at"],
            reason=row["reason"] or "",
            exit_rule=row["exit_rule"] or "",
            memo=row["memo"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_trade(
        self,
        code: str,
        side: str,
        quantity: int,
        price: float,
        reason: str = "",
        exit_rule: str = "",
        memo: str = "",
        executed_at: Optional[str] = None,
    ) -> int:
        """売買を記録する"""
        side = side.upper()
        code = code.strip()

        if side not in {"BUY", "SELL"}:
            raise ValueError("side は BUY または SELL を指定してください")
        if quantity <= 0:
            raise ValueError("quantity は1以上を指定してください")
        if price <= 0:
            raise ValueError("price は0より大きい値を指定してください")
        if not code:
            raise ValueError("code は必須です")

        if executed_at is None:
            executed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades_manual
                (code, side, quantity, price, executed_at, reason, exit_rule, memo,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    side,
                    quantity,
                    price,
                    executed_at,
                    reason,
                    exit_rule,
                    memo,
                    now,
                    now,
                ),
            )
            return cursor.lastrowid

    def get_trade(self, trade_id: int) -> Optional[TradeRecord]:
        """売買記録を取得する"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trades_manual WHERE id = ?",
                (trade_id,),
            ).fetchone()

        return self._row_to_record(row) if row is not None else None

    def get_all_trades(self) -> list[TradeRecord]:
        """全売買記録を取得する"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades_manual ORDER BY executed_at DESC, id DESC"
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def get_trades_by_code(self, code: str) -> list[TradeRecord]:
        """銘柄別の売買記録を取得する"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades_manual WHERE code = ? ORDER BY executed_at, id",
                (code,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def update_trade(
        self,
        trade_id: int,
        reason: Optional[str] = None,
        exit_rule: Optional[str] = None,
        memo: Optional[str] = None,
    ) -> bool:
        """売買記録を更新する"""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            updates = []
            params = []

            if reason is not None:
                updates.append("reason = ?")
                params.append(reason)
            if exit_rule is not None:
                updates.append("exit_rule = ?")
                params.append(exit_rule)
            if memo is not None:
                updates.append("memo = ?")
                params.append(memo)

            if not updates:
                return False

            updates.append("updated_at = ?")
            params.append(now)
            params.append(trade_id)

            cursor = conn.execute(
                f"UPDATE trades_manual SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        return cursor.rowcount > 0

    def delete_trade(self, trade_id: int) -> bool:
        """売買記録を削除する"""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM trades_manual WHERE id = ?", (trade_id,))
            return cursor.rowcount > 0
