from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from src.config import Config
from src.models import CREATE_TABLES_SQL
from src.virtual_trade import VirtualTradeManager


class _Config:
    def __init__(
        self,
        database_path: Path,
        *,
        initial_cash: float,
        max_total_positions: int,
    ) -> None:
        self.database_path = str(database_path)
        self._values = {
            "virtual_trade": {
                "enabled": True,
                "initial_cash": initial_cash,
                "max_position_amount": 100000,
                "max_total_positions": max_total_positions,
                "max_position_per_symbol": 1,
                "market_fill_mode": "next_day_open",
                "commission": 0,
                "slippage_bps": 0,
            },
            "universe": {
                "min_trade_price": 1,
                "max_trade_price": 100000,
            },
        }

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def _config(
    database_path: Path,
    *,
    initial_cash: float,
    max_total_positions: int,
) -> Config:
    return cast(
        Config,
        _Config(
            database_path,
            initial_cash=initial_cash,
            max_total_positions=max_total_positions,
        ),
    )


def _prepare_database(path: Path, *, initial_cash: float) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(CREATE_TABLES_SQL)
        conn.executemany(
            "INSERT INTO symbols (code, name, role, tradable, type) "
            "VALUES (?, ?, 'trade_candidate', 1, 'stock')",
            [
                ("JP.1111", "A"),
                ("JP.2222", "B"),
            ],
        )
        conn.executemany(
            "INSERT INTO daily_bars "
            "(code, date, open, high, low, close, volume, turnover) "
            "VALUES (?, '2026-07-01', 1000, 1000, 1000, 1000, 1000, 1000000)",
            [("JP.1111",), ("JP.2222",)],
        )
        conn.execute(
            "INSERT INTO virtual_equity_curve "
            "(strategy_name, date, cash, position_value, total_equity, daily_return) "
            "VALUES ('default', '2026-07-01', ?, 0, ?, 0)",
            (initial_cash, initial_cash),
        )


def _submit_concurrently(manager: VirtualTradeManager):
    barrier = threading.Barrier(2)

    def submit(code: str):
        barrier.wait(timeout=5)
        return manager.place_order(
            "default",
            code,
            "BUY",
            1,
            "MARKET_SIM",
            submitted_at="2026-07-02",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(submit, ["JP.1111", "JP.2222"]))


def _pending_count(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM virtual_orders WHERE status='PENDING'"
            ).fetchone()[0]
        )


def test_concurrent_buys_cannot_over_reserve_cash(tmp_path: Path) -> None:
    database_path = tmp_path / "cash.db"
    _prepare_database(database_path, initial_cash=1000)
    manager = VirtualTradeManager(
        _config(
            database_path,
            initial_cash=1000,
            max_total_positions=20,
        )
    )

    results = _submit_concurrently(manager)

    assert sum(order is not None for order in results) == 1
    assert _pending_count(database_path) == 1
    assert manager.get_available_cash("default", "2026-07-02") == 0


def test_concurrent_buys_cannot_exceed_position_slots(tmp_path: Path) -> None:
    database_path = tmp_path / "positions.db"
    _prepare_database(database_path, initial_cash=100000)
    manager = VirtualTradeManager(
        _config(
            database_path,
            initial_cash=100000,
            max_total_positions=1,
        )
    )

    results = _submit_concurrently(manager)

    assert sum(order is not None for order in results) == 1
    assert _pending_count(database_path) == 1


def test_lock_timeout_is_a_safe_order_rejection(tmp_path: Path) -> None:
    database_path = tmp_path / "locked.db"
    _prepare_database(database_path, initial_cash=100000)
    manager = VirtualTradeManager(
        _config(
            database_path,
            initial_cash=100000,
            max_total_positions=20,
        )
    )

    def fast_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(database_path, timeout=0.01)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10")
        return conn

    manager._get_connection = fast_connection  # type: ignore[method-assign]
    blocker = sqlite3.connect(database_path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        result = manager.place_order(
            "default",
            "JP.1111",
            "BUY",
            1,
            "MARKET_SIM",
            submitted_at="2026-07-02",
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert result is None
    assert _pending_count(database_path) == 0
