"""Regression coverage for as-of-date virtual-order validation."""

import sqlite3
from pathlib import Path

from src.config import Config
from src.data_store import DataStore
from src.virtual_trade import VirtualTradeManager


def _make_manager(
    tmp_path: Path,
    *,
    max_total_positions: int = 5,
    position_quantity: int = 0,
) -> tuple[VirtualTradeManager, Path]:
    db_path = tmp_path / "virtual_order_dates.db"
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(db_path)}
    virtual_config = dict(config.get("virtual_trade", {}))
    virtual_config.update(
        {
            "initial_cash": 100000,
            "max_position_amount": 50000,
            "max_total_positions": max_total_positions,
            "max_position_per_symbol": 10,
            "commission": 0,
            "reserve_buffer_pct": 2.0,
            "market_fill_mode": "next_day_open",
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
        conn.executemany(
            """
            INSERT INTO daily_bars
            (code, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, ?, ?, ?, ?, 10000, 10000000)
            """,
            [
                ("JP.0001", "2026-01-05", 1000, 1010, 990, 1000),
                ("JP.0001", "2026-01-06", 1010, 1020, 1000, 1010),
                ("JP.0002", "2026-01-05", 1000, 1010, 990, 1000),
            ],
        )
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-04', 100000, 0, 100000,
                    '2026-01-04T00:00:00')
            """
        )
        if position_quantity:
            conn.execute(
                """
                INSERT INTO virtual_positions
                (strategy_name, code, quantity, avg_cost, market_price,
                 market_value, unrealized_pl, realized_pl, updated_at)
                VALUES ('default', 'JP.0001', ?, 1000, 1000, ?, 0, 0,
                        '2026-01-05T00:00:00')
                """,
                (position_quantity, position_quantity * 1000),
            )

    return VirtualTradeManager(config), db_path


def _insert_pending(
    db_path: Path,
    *,
    code: str,
    side: str,
    submitted_at: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_orders
            (strategy_name, code, side, quantity, order_type, status,
             submitted_at, reserved_amount, created_at, updated_at)
            VALUES ('default', ?, ?, 1, 'MARKET_SIM', 'PENDING', ?,
                    NULL, ?, ?)
            """,
            (code, side, submitted_at, submitted_at, submitted_at),
        )


def test_future_pending_buy_does_not_block_historical_same_symbol(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_pending(
        db_path,
        code="JP.0001",
        side="BUY",
        submitted_at="2026-01-10 15:30:00",
    )

    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )

    assert order is not None


def test_future_pending_buy_does_not_consume_historical_position_slot(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path, max_total_positions=1)
    _insert_pending(
        db_path,
        code="JP.0002",
        side="BUY",
        submitted_at="2026-01-10 15:30:00",
    )

    order = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )

    assert order is not None


def test_same_day_pending_buy_still_blocks_duplicate(tmp_path: Path) -> None:
    manager, _ = _make_manager(tmp_path)
    first = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )
    second = manager.place_order(
        "default",
        "JP.0001",
        "BUY",
        1,
        submitted_at="2026-01-05",
    )

    assert first is not None
    assert second is None


def test_future_pending_sell_does_not_block_historical_order(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path, position_quantity=2)
    _insert_pending(
        db_path,
        code="JP.0001",
        side="SELL",
        submitted_at="2026-01-10 15:30:00",
    )

    order = manager.place_order(
        "default",
        "JP.0001",
        "SELL",
        1,
        submitted_at="2026-01-05",
    )

    assert order is not None


def test_same_day_pending_sell_still_blocks_duplicate(tmp_path: Path) -> None:
    manager, _ = _make_manager(tmp_path, position_quantity=2)
    first = manager.place_order(
        "default",
        "JP.0001",
        "SELL",
        1,
        submitted_at="2026-01-05",
    )
    second = manager.place_order(
        "default",
        "JP.0001",
        "SELL",
        1,
        submitted_at="2026-01-05",
    )

    assert first is not None
    assert second is None


def test_future_pending_sell_does_not_block_historical_fill(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path, position_quantity=2)
    order = manager.place_order(
        "default",
        "JP.0001",
        "SELL",
        1,
        submitted_at="2026-01-05",
    )
    assert order is not None
    _insert_pending(
        db_path,
        code="JP.0001",
        side="SELL",
        submitted_at="2026-01-10 15:30:00",
    )

    fills = manager.process_fills("default", "2026-01-06")

    assert len(fills) == 1
    assert fills[0].order_id == order.id
