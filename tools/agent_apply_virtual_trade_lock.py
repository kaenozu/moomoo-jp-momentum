from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "virtual_trade.py"
TEST = ROOT / "tests" / "test_virtual_order_concurrency.py"
WORKFLOW = ROOT / ".github" / "workflows" / "agent-apply-virtual-trade-lock.yml"
SELF = Path(__file__).resolve()


def patch_virtual_trade() -> None:
    text = TARGET.read_text(encoding="utf-8")
    method_start = text.index("    def place_order(")
    block_start = text.index(
        '        with self._get_connection() as conn:\n            if side == "BUY":',
        method_start,
    )
    block_end = text.index('        logger.info("仮想注文作成:', block_start)

    replacement = '''        try:
            with self._get_connection() as conn:
                # Validation and insertion must observe one serialized snapshot.
                # A deferred transaction would allow two processes to validate
                # the same cash/position state before inserting different symbols.
                conn.execute("BEGIN IMMEDIATE")
                if side == "BUY":
                    ok, reason = self._validate_buy_order(
                        conn,
                        strategy_name,
                        code,
                        quantity,
                        order_type,
                        limit_price,
                        submitted_at,
                    )
                else:
                    ok, reason = self._validate_sell_order(
                        conn,
                        strategy_name,
                        code,
                        quantity,
                    )
                if not ok:
                    logger.warning("仮想注文拒否: %s %s - %s", code, side, reason)
                    return None

                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                submit_value = submitted_at or now
                if len(submit_value) == 10:
                    submit_value = f"{submit_value} 15:30:00"

                cursor = conn.execute(
                    """
                    INSERT INTO virtual_orders
                    (strategy_name, code, side, quantity, order_type, limit_price,
                     status, signal_id, exit_reason, submitted_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_name,
                        code,
                        side,
                        quantity,
                        order_type,
                        limit_price,
                        signal_id,
                        exit_reason,
                        submit_value,
                        now,
                        now,
                    ),
                )
                order_id = cursor.lastrowid
        except sqlite3.IntegrityError as error:
            logger.warning(
                "仮想注文拒否: %s %s - DB制約競合: %s",
                code,
                side,
                error,
            )
            return None
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower():
                raise
            logger.warning(
                "仮想注文拒否: %s %s - DBロックを取得できません: %s",
                code,
                side,
                error,
            )
            return None

'''
    TARGET.write_text(
        text[:block_start] + replacement + text[block_end:],
        encoding="utf-8",
    )


def write_tests() -> None:
    TEST.write_text(
        '''from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
        _Config(database_path, initial_cash=1000, max_total_positions=20)
    )

    results = _submit_concurrently(manager)

    assert sum(order is not None for order in results) == 1
    assert _pending_count(database_path) == 1
    assert manager.get_available_cash("default", "2026-07-02") == 0


def test_concurrent_buys_cannot_exceed_position_slots(tmp_path: Path) -> None:
    database_path = tmp_path / "positions.db"
    _prepare_database(database_path, initial_cash=100000)
    manager = VirtualTradeManager(
        _Config(database_path, initial_cash=100000, max_total_positions=1)
    )

    results = _submit_concurrently(manager)

    assert sum(order is not None for order in results) == 1
    assert _pending_count(database_path) == 1


def test_lock_timeout_is_a_safe_order_rejection(tmp_path: Path) -> None:
    database_path = tmp_path / "locked.db"
    _prepare_database(database_path, initial_cash=100000)
    manager = VirtualTradeManager(
        _Config(database_path, initial_cash=100000, max_total_positions=20)
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
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_virtual_trade()
    write_tests()
    WORKFLOW.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
