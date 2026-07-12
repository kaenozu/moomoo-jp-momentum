"""Regression tests for virtual-order cash reservations and fills."""

import sqlite3

from src.config import Config
from src.data_store import DataStore
from src.migrations import migrate_virtual_orders_reserved_amount
from src.virtual_trade import VirtualTradeManager


def _make_manager(tmp_path, cash: float = 100000.0) -> tuple[VirtualTradeManager, str]:
    db_path = tmp_path / "virtual_trade.db"
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(db_path)}
    virtual_config = dict(config.get("virtual_trade", {}))
    virtual_config.update(
        {
            "initial_cash": cash,
            "max_position_amount": 50000,
            "max_total_positions": 5,
            "max_position_per_symbol": 10,
            "market_fill_mode": "next_day_open",
            "commission": 0,
            "slippage_bps": 10,
            "reserve_buffer_pct": 2.0,
        }
    )
    config._config["virtual_trade"] = virtual_config
    config._config["universe"] = {
        "min_trade_price": 1,
        "max_trade_price": 50000,
    }
    DataStore(config)
    with sqlite3.connect(db_path) as conn:
        for code in ("JP.0001", "JP.0002"):
            conn.execute(
                """
                INSERT INTO symbols
                (code, name, type, role, tradable, enabled)
                VALUES (?, ?, 'stock', 'trade_candidate', 1, 1)
                """,
                (code, code),
            )
        bars = [
            ("JP.0001", "2026-01-05", 1000, 1000, 1000, 1000),
            ("JP.0001", "2026-01-06", 1000, 1010, 990, 1000),
            ("JP.0001", "2026-01-07", 1100, 1110, 1090, 1100),
            ("JP.0001", "2026-01-10", 5000, 5010, 4990, 5000),
            ("JP.0002", "2026-01-05", 1000, 1000, 1000, 1000),
        ]
        conn.executemany(
            """
            INSERT INTO daily_bars
            (code, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, 10000, 10000000)
            """,
            bars,
        )
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-04', ?, 0, ?, '2026-01-04T00:00:00')
            """,
            (cash, cash),
        )
    return VirtualTradeManager(config), str(db_path)


def test_fill_excludes_current_buy_reservation(tmp_path):
    manager, _ = _make_manager(tmp_path, cash=1020.0)
    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    assert order is not None
    assert manager.get_available_cash("default", "2026-01-05") == 0

    fills = manager.process_fills("default", "2026-01-06")

    assert len(fills) == 1
    assert manager.get_positions("default")[0].quantity == 1
    assert manager.get_cash("default", "2026-01-06") == 19.0


def test_buy_then_sell_fills_and_recovers_cash(tmp_path):
    manager, _ = _make_manager(tmp_path, cash=1020.0)
    buy = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    assert buy is not None
    assert len(manager.process_fills("default", "2026-01-06")) == 1

    sell = manager.place_order(
        "default",
        "JP.0001",
        "SELL",
        1,
        submitted_at="2026-01-06",
    )
    assert sell is not None
    fills = manager.process_fills("default", "2026-01-07")

    assert len(fills) == 1
    assert manager.get_positions("default") == []
    assert manager.get_cash("default", "2026-01-07") > 1020.0


def test_reservation_uses_submission_date_without_lookahead(tmp_path):
    manager, _ = _make_manager(tmp_path)
    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    assert order is not None
    assert order.reserved_amount == 1020.0
    assert manager.get_available_cash("default", "2026-01-05") == 98980.0


def test_cancel_releases_reservation(tmp_path):
    manager, _ = _make_manager(tmp_path)
    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    assert order is not None and order.id is not None
    assert manager.get_available_cash("default") == 98980.0

    assert manager.cancel_order(order.id)
    assert manager.get_available_cash("default") == 100000.0


def test_migration_is_idempotent_for_legacy_pending_orders(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE virtual_orders (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT,
                code TEXT,
                side TEXT,
                quantity INTEGER,
                order_type TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO virtual_orders
            (strategy_name, code, side, quantity, order_type, status)
            VALUES ('default', 'JP.0001', 'BUY', 1, 'MARKET_SIM', 'PENDING')
            """
        )
        migrate_virtual_orders_reserved_amount(conn)
        migrate_virtual_orders_reserved_amount(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(virtual_orders)").fetchall()
        }
        value = conn.execute(
            "SELECT reserved_amount FROM virtual_orders WHERE id = 1"
        ).fetchone()[0]

    assert "reserved_amount" in columns
    assert value is None


def test_order_requires_buffered_reservation(tmp_path):
    manager, _ = _make_manager(tmp_path, cash=1019.0)

    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )

    assert order is None
    assert manager.get_available_cash("default", "2026-01-05") == 1019.0


def test_future_pending_order_is_ignored_for_past_cash(tmp_path):
    manager, db_path = _make_manager(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_orders
            (strategy_name, code, side, quantity, order_type, status,
             submitted_at, reserved_amount, created_at, updated_at)
            VALUES ('default', 'JP.0002', 'BUY', 1, 'MARKET_SIM', 'PENDING',
                    '2026-01-10 15:30:00', 5000,
                    '2026-01-10T00:00:00', '2026-01-10T00:00:00')
            """
        )

    assert manager.get_available_cash("default", "2026-01-05") == 100000.0
    assert manager.get_available_cash("default") == 95000.0


def test_historical_fill_uses_cash_as_of_fill_date(tmp_path):
    manager, db_path = _make_manager(tmp_path)
    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    assert order is not None

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-10', 50000, 0, 50000,
                    '2026-01-10T00:00:00')
            """
        )

    fills = manager.process_fills("default", "2026-01-06")

    assert len(fills) == 1
    assert manager.get_cash("default", "2026-01-06") == 98999.0
    assert manager.get_cash("default", "2026-01-10") == 50000.0


def test_historical_equity_curve_uses_cash_as_of_target_date(tmp_path):
    manager, db_path = _make_manager(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-10', 50000, 0, 50000,
                    '2026-01-10T00:00:00')
            """
        )

    snapshot = manager.save_equity_curve("default", "2026-01-06")

    assert snapshot["cash"] == 100000.0
    assert manager.get_cash("default", "2026-01-06") == 100000.0
    assert manager.get_cash("default", "2026-01-10") == 50000.0
