"""仮想SELL約定時の自己注文検証を安全に扱う互換マネージャー。"""

from __future__ import annotations

import sqlite3
from typing import Optional

from .config import Config
from .virtual_trade import VirtualFill, VirtualOrder, VirtualTradeManager


class OperationalVirtualTradeManager(VirtualTradeManager):
    """PENDING SELLが約定時に自分自身を重複注文と判定しないマネージャー。

    注文作成時の重複SELL拒否はそのまま維持し、約定処理中だけ現在の
    order_id を除外する。既存 ``VirtualTradeManager`` の約定・損益処理は
    継承し、差分を最小化する。
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self._sell_order_being_filled: Optional[int] = None

    def _validate_sell_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
    ) -> tuple[bool, str]:
        position = conn.execute(
            """
            SELECT quantity FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (strategy_name, code),
        ).fetchone()
        if not position or position["quantity"] < quantity:
            return False, "売却可能な仮想ポジションが不足しています"

        pending = conn.execute(
            """
            SELECT 1 FROM virtual_orders
            WHERE strategy_name = ? AND code = ?
              AND side = 'SELL' AND status = 'PENDING'
              AND (? IS NULL OR id <> ?)
            LIMIT 1
            """,
            (
                strategy_name,
                code,
                self._sell_order_being_filled,
                self._sell_order_being_filled,
            ),
        ).fetchone()
        if pending:
            return False, "同一銘柄の未約定SELL注文が既に存在します"
        return True, ""

    def _try_fill_order(
        self,
        order: VirtualOrder,
        target_date: str,
    ) -> Optional[VirtualFill]:
        previous_order_id = self._sell_order_being_filled
        if order.side == "SELL":
            self._sell_order_being_filled = order.id
        try:
            return super()._try_fill_order(order, target_date)
        finally:
            self._sell_order_being_filled = previous_order_id
