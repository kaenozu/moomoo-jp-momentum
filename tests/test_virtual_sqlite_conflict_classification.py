"""
Issue #73: SQLite注文競合の安全な分類テスト

期待される重複注文制約だけを通常の注文拒否として扱い、
予期しないCHECK / NOT NULL / trigger / schema不整合や
無関係な ``OperationalError`` は握りつぶさず再raiseすることを検証する。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from src.config import Config
from src.models import CREATE_TABLES_SQL
from src.virtual_trade import (
    is_expected_duplicate_conflict,
    is_sqlite_busy_or_locked,
    VirtualTradeManager,
)


class _Config:
    def __init__(self, database_path: Path) -> None:
        self.database_path = str(database_path)
        self._values: dict[str, Any] = {
            "virtual_trade": {
                "enabled": True,
                "initial_cash": 100000,
                "max_position_amount": 100000,
                "max_total_positions": 10,
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


def _config(database_path: Path) -> Config:
    return cast(Config, _Config(database_path))


def _prepare_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(CREATE_TABLES_SQL)
        conn.executemany(
            "INSERT INTO symbols (code, name, role, tradable, type) "
            "VALUES (?, ?, 'trade_candidate', 1, 'stock')",
            [("JP.1111", "A"), ("JP.2222", "B")],
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
            "VALUES ('default', '2026-07-01', 100000, 0, 100000, 0)",
        )


def _pending_count(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM virtual_orders WHERE status='PENDING'"
            ).fetchone()[0]
        )


# ── 分類ヘルパーの単体テスト ──


def _operational_error(
    message: str, code: int | None, name: str | None
) -> sqlite3.OperationalError:
    """sqlite_errorcode / sqlite_errorname属性を持つOperationalErrorを生成する。"""
    error = sqlite3.OperationalError(message)
    if code is not None:
        setattr(error, "sqlite_errorcode", code)
    if name is not None:
        setattr(error, "sqlite_errorname", name)
    return error


def test_is_sqlite_busy_by_error_code() -> None:
    error = _operational_error("database is locked", 5, "SQLITE_BUSY")
    assert is_sqlite_busy_or_locked(error)


def test_is_sqlite_locked_by_error_code() -> None:
    error = _operational_error("database table is locked", 6, "SQLITE_LOCKED")
    assert is_sqlite_busy_or_locked(error)


@pytest.mark.parametrize(
    ("code", "name"),
    [(517, "SQLITE_BUSY_SNAPSHOT"), (262, "SQLITE_LOCKED_SHAREDCACHE")],
)
def test_sqlite_extended_codes_are_classified_by_primary_code(
    code: int, name: str
) -> None:
    assert is_sqlite_busy_or_locked(_operational_error("opaque", code, name))


def test_sqlite_code_takes_precedence_over_unrelated_name() -> None:
    assert not is_sqlite_busy_or_locked(_operational_error("opaque", 1, "SQLITE_BUSY"))


def test_unrelated_extended_code_is_not_classified() -> None:
    assert not is_sqlite_busy_or_locked(
        _operational_error("opaque", 769, "SQLITE_ERROR")
    )


def test_extended_busy_name_is_classified_without_code() -> None:
    assert is_sqlite_busy_or_locked(
        _operational_error("opaque", None, "SQLITE_BUSY_SNAPSHOT")
    )


def test_is_sqlite_busy_fallback_to_name_only() -> None:
    error = _operational_error("database is locked", None, "SQLITE_BUSY")
    assert is_sqlite_busy_or_locked(error)


def test_unrelated_operational_error_not_classified() -> None:
    error = _operational_error("no such table: foo", 1, "SQLITE_ERROR")
    assert not is_sqlite_busy_or_locked(error)


def test_busy_fallback_string_match_when_no_attributes() -> None:
    error = sqlite3.OperationalError("database is locked")
    assert is_sqlite_busy_or_locked(error)


def test_unrelated_locked_word_is_not_classified() -> None:
    error = sqlite3.OperationalError("database is not locked")
    assert not is_sqlite_busy_or_locked(error)


def test_duplicate_conflict_classified_by_sql() -> None:
    error = sqlite3.IntegrityError(
        "UNIQUE constraint failed: "
        "virtual_orders.strategy_name, virtual_orders.code, virtual_orders.side"
    )
    assert is_expected_duplicate_conflict(error)


def test_duplicate_conflict_classified_by_index_name() -> None:
    error = sqlite3.IntegrityError(
        "UNIQUE constraint failed: idx_virtual_orders_pending"
    )
    assert is_expected_duplicate_conflict(error)


@pytest.mark.parametrize(
    ("code", "name"),
    [(1811, "SQLITE_CONSTRAINT_TRIGGER"), (1555, "SQLITE_CONSTRAINT_PRIMARYKEY")],
)
def test_duplicate_looking_trigger_or_primary_key_error_is_not_classified(
    code: int, name: str
) -> None:
    error = sqlite3.IntegrityError(
        "UNIQUE constraint failed: "
        "virtual_orders.strategy_name, virtual_orders.code, virtual_orders.side"
    )
    setattr(error, "sqlite_errorcode", code)
    setattr(error, "sqlite_errorname", name)

    assert not is_expected_duplicate_conflict(error)


def test_check_violation_not_classified() -> None:
    error = sqlite3.IntegrityError("CHECK constraint failed: quantity > 0")
    assert not is_expected_duplicate_conflict(error)


def test_not_null_violation_not_classified() -> None:
    error = sqlite3.IntegrityError("NOT NULL constraint failed: virtual_orders.side")
    assert not is_expected_duplicate_conflict(error)


# ── place_orderの挙動テスト ──


def test_expected_duplicate_conflict_returns_normal_rejection(tmp_path: Path) -> None:
    database_path = tmp_path / "dup.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

    first = manager.place_order(
        "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
    )
    assert first is not None

    second = manager.place_order(
        "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
    )
    assert second is None
    assert _pending_count(database_path) == 1


def test_unexpected_check_violation_is_reraisied(tmp_path: Path) -> None:
    database_path = tmp_path / "check.db"
    _prepare_database(database_path)
    # 合法なBUY注文が常にCHECK違反になる制約を仕込む。
    # 早期バリデーションを通過してもDB層でIntegrityErrorが発生する。
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE virtual_orders")
        conn.execute(
            """
            CREATE TABLE virtual_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side = 'SELL'),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                order_type TEXT NOT NULL,
                limit_price REAL,
                status TEXT NOT NULL,
                signal_id INTEGER,
                exit_reason TEXT,
                submitted_at TEXT NOT NULL,
                filled_at TEXT,
                cancelled_at TEXT,
                fill_price REAL,
                fill_reason TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
    manager = VirtualTradeManager(_config(database_path))

    with pytest.raises(sqlite3.IntegrityError):
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )


def test_trigger_constraint_with_duplicate_message_is_reraised(tmp_path: Path) -> None:
    database_path = tmp_path / "trigger.db"
    _prepare_database(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_with_duplicate_message
            BEFORE INSERT ON virtual_orders
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'UNIQUE constraint failed: virtual_orders.strategy_name, virtual_orders.code, virtual_orders.side'
                );
            END
            """
        )
    manager = VirtualTradeManager(_config(database_path))

    with pytest.raises(sqlite3.IntegrityError) as raised:
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )

    assert getattr(raised.value, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_TRIGGER"
    assert _pending_count(database_path) == 0


def test_unexpected_not_null_violation_is_reraisied(tmp_path: Path) -> None:
    database_path = tmp_path / "notnull.db"
    _prepare_database(database_path)
    # submitted_at を NOT NULL に変更する代わりに、必要な列を欠落させる
    # テーブルを再作成して NOT NULL 違反を発生させる。
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE virtual_orders")
        conn.execute(
            """
            CREATE TABLE virtual_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                status TEXT NOT NULL,
                signal_id INTEGER,
                exit_reason TEXT,
                submitted_at TEXT NOT NULL,
                filled_at TEXT,
                cancelled_at TEXT,
                fill_price REAL,
                fill_reason TEXT,
                created_at TEXT,
                updated_at TEXT,
                mandatory_note TEXT NOT NULL
            )
            """
        )
    manager = VirtualTradeManager(_config(database_path))

    with pytest.raises(sqlite3.IntegrityError):
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )


def test_unrelated_operational_error_is_reraisied(tmp_path: Path) -> None:
    database_path = tmp_path / "op.db"
    _prepare_database(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE virtual_positions")
        conn.execute("DROP TABLE virtual_orders")
        conn.execute("DROP TABLE virtual_equity_curve")
    manager = VirtualTradeManager(_config(database_path))

    with pytest.raises(sqlite3.OperationalError):
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )


def test_unexpected_operational_error_log_excludes_raw_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database_path = tmp_path / "op-log.db"
    _prepare_database(database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE virtual_positions")
        conn.execute("DROP TABLE virtual_orders")
        conn.execute("DROP TABLE virtual_equity_curve")
    manager = VirtualTradeManager(_config(database_path))
    caplog.set_level("ERROR")
    with pytest.raises(sqlite3.OperationalError):
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )
    assert "no such table: virtual_orders" not in caplog.text
    assert "OperationalError" in caplog.text


def test_sqlite_busy_is_safe_rejection_without_row(tmp_path: Path) -> None:
    database_path = tmp_path / "busy.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

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
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert result is None
    assert _pending_count(database_path) == 0


def test_sqlite_locked_is_safe_rejection_without_row(tmp_path: Path) -> None:
    database_path = tmp_path / "locked.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

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
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert result is None
    assert _pending_count(database_path) == 0


def test_rollback_leaves_no_order_row(tmp_path: Path) -> None:
    database_path = tmp_path / "rollback.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

    # 例外再raise後に注文行が残らないことを検証する。
    # 合法なBUY注文が常にCHECK違反になる制約を仕込む。
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE virtual_orders")
        conn.execute(
            """
            CREATE TABLE virtual_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side = 'SELL'),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                order_type TEXT NOT NULL,
                limit_price REAL,
                status TEXT NOT NULL,
                signal_id INTEGER,
                exit_reason TEXT,
                submitted_at TEXT NOT NULL,
                filled_at TEXT,
                cancelled_at TEXT,
                fill_price REAL,
                fill_reason TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )

    with sqlite3.connect(database_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0]
    assert count == 0


def test_cash_reservation_remains_correct_after_rejection(tmp_path: Path) -> None:
    database_path = tmp_path / "cash.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

    first = manager.place_order(
        "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
    )
    assert first is not None
    reserved_after_first = manager.get_available_cash("default", "2026-07-02")

    second = manager.place_order(
        "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
    )
    assert second is None
    assert manager.get_available_cash("default", "2026-07-02") == reserved_after_first


def test_concurrent_max_position_enforcement(tmp_path: Path) -> None:
    """並行して同一銘柄へ2件のBUYを投入しても、1件のみ成立する。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    database_path = tmp_path / "concurrent_pos.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

    barrier = threading.Barrier(2)

    def submit(_: int) -> int:
        barrier.wait(timeout=5)
        order = manager.place_order(
            "default", "JP.1111", "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )
        return 1 if order is not None else 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [0, 1]))

    assert sum(results) == 1
    assert _pending_count(database_path) == 1


def test_concurrent_max_cash_enforcement(tmp_path: Path) -> None:
    """並行して異なる銘柄へBUYを投入しても、cash不足時は1件も成立しない。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    database_path = tmp_path / "concurrent_cash.db"
    _prepare_database(database_path)
    manager = VirtualTradeManager(_config(database_path))

    # initial_cashを最小化して cash不足を発生させる
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            "UPDATE virtual_equity_curve SET cash=100 WHERE strategy_name='default'"
        )

    barrier = threading.Barrier(2)

    def submit(code: str) -> int:
        barrier.wait(timeout=5)
        order = manager.place_order(
            "default", code, "BUY", 1, "MARKET_SIM", submitted_at="2026-07-02"
        )
        return 1 if order is not None else 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["JP.1111", "JP.2222"]))

    # cash=100では1000円の注文はどちらも成立しない（cash不足）
    assert sum(results) == 0
    assert _pending_count(database_path) == 0
