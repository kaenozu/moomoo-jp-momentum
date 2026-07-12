"""Historical virtual-position reconstruction regression tests."""

import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore
from src.virtual_trade import VirtualTradeManager


def _make_manager(
    tmp_path: Path,
    *,
    max_total_positions: int = 5,
    max_position_per_symbol: int = 10,
) -> tuple[VirtualTradeManager, Path]:
    db_path = tmp_path / "position_history.db"
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(db_path)}
    virtual_config = dict(config.get("virtual_trade", {}))
    virtual_config.update(
        {
            "initial_cash": 100000,
            "max_position_amount": 50000,
            "max_total_positions": max_total_positions,
            "max_position_per_symbol": max_position_per_symbol,
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
                ("JP.0001", "2026-01-05", 100, 110, 90, 100),
                ("JP.0001", "2026-01-06", 180, 190, 170, 180),
                ("JP.0001", "2026-01-10", 200, 210, 190, 200),
                ("JP.0001", "2026-01-11", 250, 260, 240, 250),
                ("JP.0002", "2026-01-05", 100, 110, 90, 100),
                ("JP.0002", "2026-01-10", 200, 210, 190, 200),
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

    return VirtualTradeManager(config), db_path


def _insert_fill(
    db_path: Path,
    *,
    order_id: int,
    code: str,
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
            VALUES (?, 'default', ?, ?, ?, ?, ?, 'test', ?)
            """,
            (order_id, code, side, quantity, price, filled_at, filled_at),
        )


def _set_snapshot(
    db_path: Path,
    *,
    code: str,
    quantity: int,
    avg_cost: float,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_positions
            (strategy_name, code, quantity, avg_cost, market_price,
             market_value, unrealized_pl, realized_pl, updated_at)
            VALUES ('default', ?, ?, ?, ?, ?, 0, 0, 'snapshot')
            ON CONFLICT(strategy_name, code) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                market_price = excluded.market_price,
                market_value = excluded.market_value
            """,
            (code, quantity, avg_cost, avg_cost, quantity * avg_cost),
        )


def _validate_buy(
    manager: VirtualTradeManager,
    code: str,
    reference_date: str,
) -> tuple[bool, str]:
    with manager._get_connection() as conn:
        return manager._validate_buy_order(
            conn,
            "default",
            code,
            1,
            "MARKET_SIM",
            None,
            reference_date,
        )


def test_future_fill_does_not_block_historical_same_symbol_buy(tmp_path: Path) -> None:
    manager, db_path = _make_manager(
        tmp_path,
        max_position_per_symbol=1,
    )
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0001", quantity=1, avg_cost=200)

    ok, reason = _validate_buy(manager, "JP.0001", "2026-01-05")

    assert ok, reason


def test_future_fill_does_not_consume_historical_position_slot(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path, max_total_positions=1)
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0002",
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0002", quantity=1, avg_cost=200)

    ok, reason = _validate_buy(manager, "JP.0001", "2026-01-05")

    assert ok, reason


def test_future_sell_does_not_reduce_historical_sellable_quantity(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=2,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=2,
        code="JP.0001",
        side="SELL",
        quantity=2,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0001", quantity=0, avg_cost=100)

    with manager._get_connection() as conn:
        ok, reason = manager._validate_sell_order(
            conn,
            "default",
            "JP.0001",
            1,
            reference_date="2026-01-06",
        )

    assert ok, reason


def test_generate_exits_ignores_position_opened_after_target_date(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0001", quantity=1, avg_cost=200)

    orders = manager.generate_exits(
        "default",
        target_date="2026-01-05",
        stop_loss_pct=-5,
    )

    assert orders == []


def test_historical_equity_excludes_future_position(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=2,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0001", quantity=2, avg_cost=200)

    result = manager.save_equity_curve("default", "2026-01-05")

    assert result["cash"] == pytest.approx(100000)
    assert result["position_value"] == pytest.approx(0)
    assert result["total_equity"] == pytest.approx(100000)


def test_positions_as_of_replay_weighted_average_and_same_day_sell(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=2,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=2,
        code="JP.0001",
        side="BUY",
        quantity=2,
        price=200,
        filled_at="2026-01-06 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=3,
        code="JP.0001",
        side="SELL",
        quantity=1,
        price=250,
        filled_at="2026-01-06 15:00:00",
    )

    positions = manager.get_positions("default", as_of_date="2026-01-06")

    assert len(positions) == 1
    position = positions[0]
    assert position.quantity == 3
    assert position.avg_cost == pytest.approx(150)
    assert position.realized_pl == pytest.approx(100)
    assert position.market_price == pytest.approx(180)
    assert position.market_value == pytest.approx(540)


def test_rebuild_cache_replays_out_of_order_historical_fill(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=2,
        code="JP.0001",
        side="BUY",
        quantity=10,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=3,
        code="JP.0001",
        side="SELL",
        quantity=10,
        price=250,
        filled_at="2026-01-11 10:00:00",
    )
    _insert_fill(
        db_path,
        order_id=1,
        code="JP.0001",
        side="BUY",
        quantity=5,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )
    _set_snapshot(db_path, code="JP.0001", quantity=0, avg_cost=200)

    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rebuilt = manager._rebuild_position_cache_from_fills(conn, "default")

    positions = manager.get_positions("default")

    assert rebuilt
    assert len(positions) == 1
    assert positions[0].quantity == 5
    assert positions[0].avg_cost == pytest.approx(500 / 3)


def test_snapshot_only_legacy_position_remains_supported(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    _set_snapshot(db_path, code="JP.0001", quantity=2, avg_cost=100)

    with manager._get_connection() as conn:
        ok, reason = manager._validate_sell_order(
            conn,
            "default",
            "JP.0001",
            1,
            reference_date="2026-01-05",
        )

    assert ok, reason
