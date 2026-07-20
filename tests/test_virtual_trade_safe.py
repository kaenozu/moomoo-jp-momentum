import sqlite3

from src.virtual_trade import VirtualOrder, VirtualTradeManager
from src.virtual_trade_safe import OperationalVirtualTradeManager


def _create_validation_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE virtual_positions (
            strategy_name TEXT NOT NULL,
            code TEXT NOT NULL,
            quantity INTEGER NOT NULL
        );
        CREATE TABLE virtual_orders (
            id INTEGER PRIMARY KEY,
            strategy_name TEXT NOT NULL,
            code TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL
        );
        INSERT INTO virtual_positions(strategy_name, code, quantity)
        VALUES ('default', 'JP.1111', 10);
        INSERT INTO virtual_orders(id, strategy_name, code, side, status)
        VALUES (1, 'default', 'JP.1111', 'SELL', 'PENDING');
        """
    )
    return conn


def test_sell_validation_rejects_another_pending_order():
    manager = OperationalVirtualTradeManager.__new__(OperationalVirtualTradeManager)
    manager._sell_order_being_filled = None

    with _create_validation_connection() as conn:
        ok, reason = manager._validate_sell_order(
            conn,
            "default",
            "JP.1111",
            1,
        )

    assert ok is False
    assert "未約定SELL注文" in reason


def test_sell_validation_excludes_order_currently_being_filled():
    manager = OperationalVirtualTradeManager.__new__(OperationalVirtualTradeManager)
    manager._sell_order_being_filled = 1

    with _create_validation_connection() as conn:
        ok, reason = manager._validate_sell_order(
            conn,
            "default",
            "JP.1111",
            1,
        )

    assert ok is True
    assert reason == ""


def test_try_fill_sets_and_restores_current_sell_order(monkeypatch):
    seen_order_ids = []

    def fake_try_fill(self, order, target_date):
        seen_order_ids.append(self._sell_order_being_filled)
        return None

    monkeypatch.setattr(VirtualTradeManager, "_try_fill_order", fake_try_fill)
    manager = OperationalVirtualTradeManager.__new__(OperationalVirtualTradeManager)
    manager._sell_order_being_filled = None
    order = VirtualOrder(id=7, side="SELL")

    manager._try_fill_order(order, "2026-07-20")

    assert seen_order_ids == [7]
    assert manager._sell_order_being_filled is None
