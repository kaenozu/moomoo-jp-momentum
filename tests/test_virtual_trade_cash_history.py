"""Historical virtual-cash reconstruction regression tests."""

import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore
from src.virtual_trade import VirtualTradeManager


def _make_manager(
    tmp_path: Path,
    *,
    commission: float = 0.0,
    seed_equity: bool = True,
) -> tuple[VirtualTradeManager, Path]:
    db_path = tmp_path / "cash_history.db"
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(db_path)}
    virtual_config = dict(config.get("virtual_trade", {}))
    virtual_config.update(
        {
            "initial_cash": 100000,
            "max_position_amount": 50000,
            "max_total_positions": 5,
            "max_position_per_symbol": 10,
            "commission": commission,
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
        conn.execute(
            """
            INSERT INTO symbols
            (code, name, type, role, tradable, enabled)
            VALUES ('JP.0001', 'JP.0001', 'stock', 'trade_candidate', 1, 1)
            """
        )
        conn.executemany(
            """
            INSERT INTO daily_bars
            (code, date, open, high, low, close, volume, turnover)
            VALUES ('JP.0001', ?, ?, ?, ?, ?, 10000, 10000000)
            """,
            [
                ("2026-01-05", 100, 110, 90, 100),
                ("2026-01-10", 200, 210, 190, 200),
                ("2026-01-11", 250, 260, 240, 250),
            ],
        )
        if seed_equity:
            conn.execute(
                """
                INSERT INTO virtual_equity_curve
                (strategy_name, date, cash, position_value, total_equity, created_at)
                VALUES ('default', '2026-01-04', 100000, 0, 100000,
                        '2026-01-04T00:00:00')
                """
            )

    return VirtualTradeManager(config), db_path


def _insert_fill(
    db_path: Path,
    *,
    order_id: int,
    side: str,
    quantity: int,
    price: float,
    filled_at: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_fills
            (order_id, strategy_name, code, side, quantity, price,
             filled_at, fill_mode, created_at)
            VALUES (?, 'default', 'JP.0001', ?, ?, ?, ?, 'test', ?)
            """,
            (order_id, side, quantity, price, filled_at, filled_at),
        )


def _set_snapshot(
    db_path: Path,
    *,
    quantity: int,
    avg_cost: float,
    realized_pl: float,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_positions
            (strategy_name, code, quantity, avg_cost, market_price,
             market_value, unrealized_pl, realized_pl, updated_at)
            VALUES ('default', 'JP.0001', ?, ?, ?, ?, 0, ?, 'snapshot')
            ON CONFLICT(strategy_name, code) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                market_price = excluded.market_price,
                market_value = excluded.market_value,
                realized_pl = excluded.realized_pl
            """,
            (quantity, avg_cost, avg_cost, quantity * avg_cost, realized_pl),
        )


def test_cash_replays_fills_when_equity_snapshots_are_missing(tmp_path: Path) -> None:
    manager, db_path = _make_manager(
        tmp_path,
        commission=5,
        seed_equity=False,
    )
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=2,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=2,
        side="SELL",
        quantity=1,
        price=150,
        filled_at="2026-01-10 10:00:00",
    )

    assert manager.get_cash("default", "2026-01-10") == pytest.approx(99940)


def test_future_fills_do_not_change_historical_cash(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=2,
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )

    assert manager.get_cash("default", "2026-01-05") == pytest.approx(99900)
    assert manager.get_cash("default", "2026-01-10") == pytest.approx(99700)


def test_available_cash_uses_replayed_cash_before_pending_reservations(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_orders
            (strategy_name, code, side, quantity, order_type, limit_price,
             status, submitted_at, reserved_amount)
            VALUES ('default', 'JP.0001', 'BUY', 1, 'LIMIT_SIM', 100,
                    'PENDING', '2026-01-05 12:00:00', 102)
            """
        )

    assert manager.get_available_cash("default", "2026-01-05") == pytest.approx(
        99798
    )


def test_inconsistent_current_snapshot_falls_back_conservatively(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-06', 77777, 0, 77777, 'legacy')
            """
        )

    assert manager.get_cash("default", "2026-01-06") == pytest.approx(77777)


def test_out_of_order_fill_repairs_later_equity_rows(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=2,
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=3,
        side="SELL",
        quantity=1,
        price=250,
        filled_at="2026-01-11 10:00:00",
    )
    _set_snapshot(db_path, quantity=0, avg_cost=200, realized_pl=50)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', ?, ?, 0, ?, 'before-backfill')
            """,
            [
                ("2026-01-10", 99800, 99800),
                ("2026-01-11", 100050, 100050),
            ],
        )

    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )

    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert manager._rebuild_position_cache_from_fills(
            conn,
            "default",
            exclude_order_id=1,
        )
        assert manager._rebuild_equity_curve_from_fills(
            conn,
            "default",
            "2026-01-05",
            exclude_order_id=1,
        )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date, cash, position_value, total_equity, daily_return
            FROM virtual_equity_curve
            WHERE strategy_name = 'default' AND date >= '2026-01-05'
            ORDER BY date
            """
        ).fetchall()

    assert [row["date"] for row in rows] == [
        "2026-01-05",
        "2026-01-10",
        "2026-01-11",
    ]
    assert [row["cash"] for row in rows] == pytest.approx([99900, 99700, 99950])
    assert [row["position_value"] for row in rows] == pytest.approx([100, 400, 250])
    assert [row["total_equity"] for row in rows] == pytest.approx(
        [100000, 100100, 100200]
    )
    assert rows[0]["daily_return"] == pytest.approx(0)
    assert rows[1]["daily_return"] == pytest.approx(0.1)
    assert rows[2]["daily_return"] == pytest.approx(100 / 100100 * 100)


def test_rebuild_refuses_inconsistent_legacy_cash_history(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=2,
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-10', 12345, 0, 12345, 'legacy')
            """
        )
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )

    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rebuilt = manager._rebuild_equity_curve_from_fills(
            conn,
            "default",
            "2026-01-05",
            exclude_order_id=1,
        )

    assert not rebuilt
    assert manager.get_cash("default", "2026-01-10") == pytest.approx(12345)
