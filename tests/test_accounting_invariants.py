"""
会計不変条件（accounting invariants）を検証するテスト群。

ファイルパス: tests/test_accounting_invariants.py
何をするか: cash、position、fill、P/L、equity、分割、idle sleeveの整合性を検証する
なぜ存在するか: フォワード検証中の会計不整合を日次で自動検出するため
関連ファイル: src/virtual_trade.py, src/backtest_runner.py, data/moomoo.db
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import Config
from src.virtual_trade import VirtualFill, VirtualOrder, VirtualTradeManager


DB_PATH = Path("data/moomoo.db")
TEST_CONFIG_PATH = Path("tests/fixtures/config.test.yaml")
RUNTIME_CONFIG_PATH = Path("config.yaml")

pytestmark = pytest.mark.integration


def tolerance(expected: float) -> float:
    """数値比較に使う絶対許容誤差を返す。"""
    return max(0.01, abs(expected) * 1e-9)


def assert_close(actual: float, expected: float) -> None:
    """指定された会計許容誤差で2値が一致することを検証する。"""
    assert actual == pytest.approx(expected, abs=tolerance(expected))


def db_connection() -> sqlite3.Connection:
    """実際のDBへの読み取り専用接続を返す。存在しなければskipする。"""
    if not DB_PATH.exists():
        pytest.skip(f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """実DBの読み取り専用接続をテストごとに提供する。"""
    connection = db_connection()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def snapshot_db(tmp_path: Path) -> Path:
    """実DBをread-onlyで開き、変更テスト用のSQLiteバックアップを作る。"""
    source = db_connection()
    destination_path = tmp_path / "moomoo.snapshot.db"
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return destination_path


@pytest.fixture
def manager(snapshot_db: Path, tmp_path: Path) -> VirtualTradeManager:
    """実DBスナップショットを使うVirtualTradeManagerを作る。"""
    source_config = (
        RUNTIME_CONFIG_PATH
        if RUNTIME_CONFIG_PATH.exists()
        else TEST_CONFIG_PATH
    )
    if not source_config.exists():
        pytest.skip(f"Config not found: {source_config}")
    with source_config.open(encoding="utf-8") as file:
        config_data = yaml.safe_load(file) or {}
    config_data.setdefault("database", {})["path"] = str(snapshot_db)
    config_data.setdefault("virtual_trade", {})["enabled"] = True
    config_data["virtual_trade"]["max_position_per_symbol"] = 1000
    config_path = tmp_path / "config.accounting.yaml"
    with config_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(config_data, file, allow_unicode=True, sort_keys=False)
    return VirtualTradeManager(Config(str(config_path)))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """指定テーブルがDBに存在するかを返す。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """指定テーブルのカラム名集合を返す。"""
    if not _table_exists(conn, table):
        return set()
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _require_tables(conn: sqlite3.Connection, *tables: str) -> None:
    """必要テーブルがなければ対象外としてskipする。"""
    missing = [table for table in tables if not _table_exists(conn, table)]
    if missing:
        pytest.skip(f"Required tables not found: {', '.join(missing)}")


def _config_data() -> dict[str, Any]:
    """実行configがあれば優先し、なければテストconfigを読み込む。"""
    config_path = (
        RUNTIME_CONFIG_PATH
        if RUNTIME_CONFIG_PATH.exists()
        else TEST_CONFIG_PATH
    )
    if not config_path.exists():
        pytest.skip(f"Config not found: {config_path}")
    with config_path.open(encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}
    if not isinstance(loaded, dict):
        pytest.fail(f"Config root must be mapping: {config_path}")
    return loaded


def _select_trade_bar(conn: sqlite3.Connection) -> sqlite3.Row:
    """合成約定に使える正の価格を持つ日足を1件返す。"""
    _require_tables(conn, "daily_bars")
    row = conn.execute(
        """
        SELECT code, date, open, close
        FROM daily_bars
        WHERE COALESCE(open, 0) > 0 AND COALESCE(close, 0) > 0
        ORDER BY close ASC, code ASC, date ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("No positive daily bar available")
    return row


def _select_fill_window(conn: sqlite3.Connection) -> tuple[str, str, str]:
    """翌営業日始値約定を作れる銘柄・signal日・fill日を返す。"""
    _require_tables(conn, "daily_bars")
    row = conn.execute(
        """
        SELECT first.code AS code,
               first.date AS signal_date,
               MIN(next.date) AS fill_date
        FROM daily_bars AS first
        JOIN daily_bars AS next
          ON next.code = first.code
         AND next.date > first.date
         AND COALESCE(next.open, 0) > 0
        WHERE COALESCE(first.close, 0) > 0
        GROUP BY first.code, first.date
        HAVING MIN(next.date) IS NOT NULL
        ORDER BY first.close ASC, first.code ASC, first.date ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("No two-day fill window available")
    return str(row["code"]), str(row["signal_date"]), str(row["fill_date"])


def _clear_strategy(conn: sqlite3.Connection, strategy_name: str) -> None:
    """スナップショット内の合成戦略データだけを削除する。"""
    for table in (
        "virtual_fills",
        "virtual_orders",
        "virtual_positions",
        "virtual_equity_curve",
    ):
        if _table_exists(conn, table) and "strategy_name" in _columns(conn, table):
            conn.execute(
                f"DELETE FROM {table} WHERE strategy_name=?",
                (strategy_name,),
            )


def _direct_fill(
    manager: VirtualTradeManager,
    *,
    strategy_name: str,
    code: str,
    side: str,
    quantity: int,
    price: float,
    filled_at: str,
) -> None:
    """約定後会計更新を直接呼び、独立したBUY/SELLテストを構築する。"""
    order = VirtualOrder(
        strategy_name=strategy_name,
        code=code,
        side=side,
        quantity=quantity,
        order_type="MARKET_SIM",
        status="FILLED",
        submitted_at=filled_at,
    )
    fill = VirtualFill(
        order_id=0,
        strategy_name=strategy_name,
        code=code,
        side=side,
        quantity=quantity,
        price=price,
        filled_at=filled_at,
        fill_mode="accounting_invariant_test",
    )
    with manager._get_connection() as connection:
        manager._update_position_and_cash(connection, order, fill)


def _insert_pending_buy(
    manager: VirtualTradeManager,
    strategy_name: str,
) -> tuple[int, str]:
    """process_fills用の翌日始値BUY注文をスナップショットへ追加する。"""
    with manager._get_connection() as connection:
        _clear_strategy(connection, strategy_name)
        code, signal_date, fill_date = _select_fill_window(connection)
        manager.initial_cash = max(manager.initial_cash, 10_000_000.0)
        cursor = connection.execute(
            """
            INSERT INTO virtual_orders (
                strategy_name, code, side, quantity, order_type, limit_price,
                status, signal_id, submitted_at, created_at, updated_at
            )
            VALUES (?, ?, 'BUY', 1, 'MARKET_SIM', NULL,
                    'PENDING', NULL, ?, ?, ?)
            """,
            (
                strategy_name,
                code,
                f"{signal_date} 15:30:00",
                f"{signal_date} 15:30:00",
                f"{signal_date} 15:30:00",
            ),
        )
        order_id = cursor.lastrowid
    if order_id is None:
        pytest.fail("Failed to insert pending order")
    return int(order_id), fill_date


def test_cash_roll_forward(conn: sqlite3.Connection) -> None:
    """ending_cashが前日cashと当日売買・手数料・税の増減に一致することを検証する。"""
    _require_tables(conn, "virtual_equity_curve", "virtual_fills")
    fill_columns = _columns(conn, "virtual_fills")
    commission_sql = (
        "COALESCE(commission, 0)" if "commission" in fill_columns else "0"
    )
    taxes_sql = "COALESCE(taxes, 0)" if "taxes" in fill_columns else "0"
    fills = conn.execute(
        f"""
        SELECT strategy_name,
               substr(filled_at, 1, 10) AS fill_date,
               SUM(CASE WHEN side='SELL' THEN price * quantity ELSE 0 END)
                   AS sell_proceeds,
               SUM(CASE WHEN side='BUY' THEN price * quantity ELSE 0 END)
                   AS buy_gross,
               SUM({commission_sql}) AS commission,
               SUM({taxes_sql}) AS taxes
        FROM virtual_fills
        GROUP BY strategy_name, substr(filled_at, 1, 10)
        ORDER BY strategy_name, fill_date
        """
    ).fetchall()
    checked = 0
    for fill_row in fills:
        ending = conn.execute(
            """
            SELECT cash FROM virtual_equity_curve
            WHERE strategy_name=? AND date=?
            """,
            (fill_row["strategy_name"], fill_row["fill_date"]),
        ).fetchone()
        beginning = conn.execute(
            """
            SELECT cash FROM virtual_equity_curve
            WHERE strategy_name=? AND date<?
            ORDER BY date DESC LIMIT 1
            """,
            (fill_row["strategy_name"], fill_row["fill_date"]),
        ).fetchone()
        if ending is None or beginning is None:
            continue
        expected = (
            float(beginning["cash"])
            + float(fill_row["sell_proceeds"] or 0.0)
            - float(fill_row["buy_gross"] or 0.0)
            - float(fill_row["commission"] or 0.0)
            - float(fill_row["taxes"] or 0.0)
        )
        assert_close(float(ending["cash"]), expected)
        checked += 1
    if checked == 0:
        pytest.skip("No fill date with beginning and ending cash snapshots")


def test_equity_identity(conn: sqlite3.Connection) -> None:
    """total_equityがcash・alpha position・idle benchmarkの合計に一致することを検証する。"""
    _require_tables(conn, "virtual_equity_curve")
    columns = _columns(conn, "virtual_equity_curve")
    idle_column = next(
        (
            name
            for name in ("idle_benchmark_value", "idle_position_value")
            if name in columns
        ),
        None,
    )
    select_idle = f", COALESCE({idle_column}, 0) AS idle_value" if idle_column else ", 0 AS idle_value"
    rows = conn.execute(
        f"""
        SELECT strategy_name, date, cash, position_value, total_equity
               {select_idle}
        FROM virtual_equity_curve
        WHERE cash IS NOT NULL
          AND position_value IS NOT NULL
          AND total_equity IS NOT NULL
        """
    ).fetchall()
    if not rows:
        pytest.skip("No virtual equity rows")
    for row in rows:
        expected = (
            float(row["cash"])
            + float(row["position_value"])
            + float(row["idle_value"])
        )
        assert_close(float(row["total_equity"]), expected)


def test_buy_fill(manager: VirtualTradeManager) -> None:
    """BUY約定でcash減少・quantity増加・加重平均取得単価が整合することを検証する。"""
    strategy = "__invariant_buy_fill__"
    with manager._get_connection() as connection:
        _clear_strategy(connection, strategy)
        bar = _select_trade_bar(connection)
    code = str(bar["code"])
    date = str(bar["date"])
    first_price = float(bar["close"])
    second_price = first_price * 1.1
    manager.initial_cash = max(1_000_000.0, first_price * 100.0)
    beginning_cash = manager.get_cash(strategy)

    _direct_fill(
        manager,
        strategy_name=strategy,
        code=code,
        side="BUY",
        quantity=2,
        price=first_price,
        filled_at=date,
    )
    _direct_fill(
        manager,
        strategy_name=strategy,
        code=code,
        side="BUY",
        quantity=3,
        price=second_price,
        filled_at=date,
    )

    with manager._get_connection() as connection:
        position = connection.execute(
            """
            SELECT quantity, avg_cost FROM virtual_positions
            WHERE strategy_name=? AND code=?
            """,
            (strategy, code),
        ).fetchone()
    assert position is not None
    expected_cost = (first_price * 2 + second_price * 3) / 5
    expected_cash = (
        beginning_cash
        - first_price * 2
        - second_price * 3
        - manager.commission * 2
    )
    assert int(position["quantity"]) == 5
    assert_close(float(position["avg_cost"]), expected_cost)
    assert_close(manager.get_cash(strategy, date), expected_cash)


def test_sell_fill(manager: VirtualTradeManager) -> None:
    """SELL約定でcash増加・quantity減少・realized P/Lが整合することを検証する。"""
    strategy = "__invariant_sell_fill__"
    with manager._get_connection() as connection:
        _clear_strategy(connection, strategy)
        bar = _select_trade_bar(connection)
    code = str(bar["code"])
    date = str(bar["date"])
    buy_price = float(bar["close"])
    sell_price = buy_price * 1.2
    manager.initial_cash = max(1_000_000.0, buy_price * 100.0)

    _direct_fill(
        manager,
        strategy_name=strategy,
        code=code,
        side="BUY",
        quantity=5,
        price=buy_price,
        filled_at=date,
    )
    cash_before_sell = manager.get_cash(strategy, date)
    _direct_fill(
        manager,
        strategy_name=strategy,
        code=code,
        side="SELL",
        quantity=2,
        price=sell_price,
        filled_at=date,
    )

    with manager._get_connection() as connection:
        position = connection.execute(
            """
            SELECT quantity, realized_pl FROM virtual_positions
            WHERE strategy_name=? AND code=?
            """,
            (strategy, code),
        ).fetchone()
    assert position is not None
    assert int(position["quantity"]) == 3
    assert_close(
        manager.get_cash(strategy, date),
        cash_before_sell + sell_price * 2 - manager.commission,
    )
    assert_close(
        float(position["realized_pl"]),
        (sell_price - buy_price) * 2 - manager.commission,
    )


def test_position_non_negative(conn: sqlite3.Connection) -> None:
    """quantityが負でなく、同一strategy_name/codeの重複positionがないことを検証する。"""
    _require_tables(conn, "virtual_positions")
    negative = conn.execute(
        "SELECT strategy_name, code, quantity FROM virtual_positions WHERE quantity < 0"
    ).fetchall()
    duplicates = conn.execute(
        """
        SELECT strategy_name, code, COUNT(*) AS count
        FROM virtual_positions
        GROUP BY strategy_name, code
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert negative == []
    assert duplicates == []


def test_cash_non_negative(conn: sqlite3.Connection) -> None:
    """レバレッジ無効時にvirtual/backtest cashが負にならないことを検証する。"""
    config = _config_data()
    leverage_enabled = bool(
        config.get("virtual_trade", {}).get("allow_leverage", False)
    )
    if leverage_enabled:
        pytest.skip("Leverage is enabled")

    checked = 0
    for table in ("virtual_equity_curve", "backtest_equity_curve"):
        if not _table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT MIN(cash) AS minimum_cash FROM {table}").fetchone()
        if row is None or row["minimum_cash"] is None:
            continue
        assert float(row["minimum_cash"]) >= -tolerance(0.0)
        checked += 1
    if checked == 0:
        pytest.skip("No cash history")


def test_reserved_cash(manager: VirtualTradeManager) -> None:
    """reserved_cashがpending BUYの推定約定金額合計に一致することを検証する。"""
    strategy = "__invariant_reserved_cash__"
    order_id, fill_date = _insert_pending_buy(manager, strategy)
    del order_id
    reservation_method = getattr(
        manager,
        "_pending_buy_reservation_with_conn",
        None,
    )
    if reservation_method is None:
        pytest.skip("Reservation API is not implemented on this branch")

    with manager._get_connection() as connection:
        order = connection.execute(
            """
            SELECT code, quantity, order_type, limit_price, submitted_at
            FROM virtual_orders
            WHERE strategy_name=? AND side='BUY' AND status='PENDING'
            """,
            (strategy,),
        ).fetchone()
        assert order is not None
        reference_date = str(order["submitted_at"])[:10]
        price_row = connection.execute(
            """
            SELECT close FROM daily_bars
            WHERE code=? AND date<=?
            ORDER BY date DESC LIMIT 1
            """,
            (order["code"], reference_date),
        ).fetchone()
        assert price_row is not None
        expected = (
            float(price_row["close"]) * int(order["quantity"])
            + manager.commission
        )
        actual = float(reservation_method(connection, strategy, fill_date))
    assert_close(actual, expected)


def test_max_positions(conn: sqlite3.Connection) -> None:
    """filled position数とpending BUY数の合計がmax_positions以下であることを検証する。"""
    _require_tables(conn, "virtual_positions", "virtual_orders")
    config = _config_data()
    max_positions = int(
        config.get("virtual_trade", {}).get(
            "max_total_positions",
            config.get("backtest", {}).get("max_positions", 5),
        )
    )
    strategies = conn.execute(
        """
        SELECT strategy_name FROM virtual_positions
        UNION
        SELECT strategy_name FROM virtual_orders
        """
    ).fetchall()
    if not strategies:
        pytest.skip("No virtual strategies")
    for row in strategies:
        strategy = str(row["strategy_name"])
        filled_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM virtual_positions
                WHERE strategy_name=? AND quantity>0
                """,
                (strategy,),
            ).fetchone()[0]
        )
        pending_count = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT code) FROM virtual_orders
                WHERE strategy_name=? AND side='BUY' AND status='PENDING'
                """,
                (strategy,),
            ).fetchone()[0]
        )
        assert filled_count + pending_count <= max_positions


def test_fill_idempotency(manager: VirtualTradeManager) -> None:
    """同一target_dateでprocess_fillsを2回実行してもfillが重複しないことを検証する。"""
    strategy = "__invariant_fill_idempotency__"
    order_id, fill_date = _insert_pending_buy(manager, strategy)

    first = manager.process_fills(strategy, fill_date)
    second = manager.process_fills(strategy, fill_date)

    with manager._get_connection() as connection:
        fill_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM virtual_fills WHERE order_id=?",
                (order_id,),
            ).fetchone()[0]
        )
    assert len(first) == 1
    assert second == []
    assert fill_count == 1


def test_order_fill_cash_atomicity(
    manager: VirtualTradeManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """約定会計更新で例外が起きた場合にorder/fill/cashが部分保存されないことを検証する。"""
    strategy = "__invariant_atomicity__"
    order_id, fill_date = _insert_pending_buy(manager, strategy)

    def fail_update(*args: object, **kwargs: object) -> None:
        """position/cash更新直前に意図的な例外を発生させる。"""
        raise RuntimeError("injected accounting failure")

    monkeypatch.setattr(manager, "_update_position_and_cash", fail_update)
    with pytest.raises(RuntimeError, match="injected accounting failure"):
        manager.process_fills(strategy, fill_date)

    with manager._get_connection() as connection:
        order = connection.execute(
            "SELECT status, filled_at, fill_price FROM virtual_orders WHERE id=?",
            (order_id,),
        ).fetchone()
        fill_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM virtual_fills WHERE order_id=?",
                (order_id,),
            ).fetchone()[0]
        )
        position_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM virtual_positions WHERE strategy_name=?",
                (strategy,),
            ).fetchone()[0]
        )
        equity_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM virtual_equity_curve WHERE strategy_name=?",
                (strategy,),
            ).fetchone()[0]
        )
    assert order is not None
    assert order["status"] == "PENDING"
    assert order["filled_at"] is None
    assert order["fill_price"] is None
    assert fill_count == 0
    assert position_count == 0
    assert equity_count == 0


def test_time_series_integrity(conn: sqlite3.Connection) -> None:
    """すべての約定でsignal_dateまたはsubmitted_atがfill_dateより前であることを検証する。"""
    checked = 0
    if _table_exists(conn, "virtual_orders") and _table_exists(conn, "virtual_fills"):
        invalid_virtual = conn.execute(
            """
            SELECT o.id, o.submitted_at, f.filled_at
            FROM virtual_orders AS o
            JOIN virtual_fills AS f ON f.order_id = o.id
            WHERE date(substr(o.submitted_at, 1, 10)) >= date(substr(f.filled_at, 1, 10))
            """
        ).fetchall()
        assert invalid_virtual == []
        checked += 1
    if _table_exists(conn, "backtest_orders") and _table_exists(conn, "backtest_fills"):
        invalid_backtest = conn.execute(
            """
            SELECT o.id, o.signal_date, f.filled_at
            FROM backtest_orders AS o
            JOIN backtest_fills AS f ON f.order_id = o.id
            WHERE o.signal_date IS NOT NULL
              AND date(substr(o.signal_date, 1, 10)) >= date(substr(f.filled_at, 1, 10))
            """
        ).fetchall()
        assert invalid_backtest == []
        checked += 1
    if checked == 0:
        pytest.skip("No order/fill tables")


def test_pl_identity(manager: VirtualTradeManager) -> None:
    """equity-initial_cashがrealized+unrealized-total_costに一致することを検証する。"""
    strategy = "__invariant_pl_identity__"
    with manager._get_connection() as connection:
        _clear_strategy(connection, strategy)
        bar = _select_trade_bar(connection)
    code = str(bar["code"])
    date = str(bar["date"])
    price = float(bar["close"])
    quantity = 2
    manager.initial_cash = max(1_000_000.0, price * 100.0)
    manager.commission = 5.0

    _direct_fill(
        manager,
        strategy_name=strategy,
        code=code,
        side="BUY",
        quantity=quantity,
        price=price,
        filled_at=date,
    )
    market_price = price * 1.1
    unrealized = (market_price - price) * quantity
    with manager._get_connection() as connection:
        connection.execute(
            """
            UPDATE virtual_positions
            SET market_price=?, market_value=?, unrealized_pl=?
            WHERE strategy_name=? AND code=?
            """,
            (
                market_price,
                market_price * quantity,
                unrealized,
                strategy,
                code,
            ),
        )
        cash = float(
            connection.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name=? AND date=?
                """,
                (strategy, date),
            ).fetchone()[0]
        )
        realized = float(
            connection.execute(
                """
                SELECT COALESCE(realized_pl, 0) FROM virtual_positions
                WHERE strategy_name=? AND code=?
                """,
                (strategy, code),
            ).fetchone()[0]
        )
    equity = cash + market_price * quantity
    expected = realized + unrealized - manager.commission
    assert_close(equity - manager.initial_cash, expected)


def test_split_adjustment(conn: sqlite3.Connection) -> None:
    """確認済みsplit境界でQFQ価格の理論時価が大幅に毀損しないことを検証する。"""
    _require_tables(conn, "corporate_actions", "daily_bars")
    actions = conn.execute(
        """
        SELECT code, effective_date, ratio_before, ratio_after
        FROM corporate_actions
        WHERE action_type='split' AND status='confirmed'
        ORDER BY code, effective_date
        """
    ).fetchall()
    checked = 0
    for action in actions:
        before = conn.execute(
            """
            SELECT close FROM daily_bars
            WHERE code=? AND date<? AND close>0
            ORDER BY date DESC LIMIT 1
            """,
            (action["code"], action["effective_date"]),
        ).fetchone()
        after = conn.execute(
            """
            SELECT close FROM daily_bars
            WHERE code=? AND date>=? AND close>0
            ORDER BY date ASC LIMIT 1
            """,
            (action["code"], action["effective_date"]),
        ).fetchone()
        if before is None or after is None:
            continue
        before_value = float(before["close"])
        after_value = float(after["close"])
        # QFQ系列では数量も価格も分割後単位に正規化されるため、同一synthetic数量の
        # 時価が分割比率だけで90%毀損・900%膨張してはならない。
        relative_change = abs(after_value / before_value - 1.0)
        assert relative_change < 0.5, (
            f"split continuity broken: {action['code']} "
            f"{before_value} -> {after_value}"
        )
        checked += 1
    if checked == 0:
        pytest.skip("No confirmed split with surrounding prices")


def test_idle_sleeve_no_double_count(conn: sqlite3.Connection) -> None:
    """1306 idle sleeve資金がcash/reservedとalpha positionへ二重計上されないことを検証する。"""
    _require_tables(
        conn,
        "backtest_runs",
        "backtest_positions",
        "backtest_equity_curve",
    )
    runs = conn.execute(
        """
        SELECT id, strategy_name FROM backtest_runs
        WHERE strategy_name != 'etf_rotation'
        ORDER BY id
        """
    ).fetchall()
    if not runs:
        pytest.skip("No non-ETF backtest runs")
    checked = 0
    for run in runs:
        duplicate = conn.execute(
            """
            SELECT COUNT(*) FROM backtest_positions
            WHERE run_id=? AND code='JP.1306' AND quantity>0
            """,
            (run["id"],),
        ).fetchone()[0]
        assert int(duplicate) == 0
        rows = conn.execute(
            """
            SELECT cash, position_value, total_equity
            FROM backtest_equity_curve
            WHERE run_id=?
            """,
            (run["id"],),
        ).fetchall()
        for row in rows:
            if None in (row["cash"], row["position_value"], row["total_equity"]):
                continue
            assert_close(
                float(row["total_equity"]),
                float(row["cash"]) + float(row["position_value"]),
            )
            checked += 1
    if checked == 0:
        pytest.skip("No backtest equity rows")


def test_equity_curve_chain(conn: sqlite3.Connection) -> None:
    """日次リターンの複利連結が最終累積リターンと一致することを検証する。"""
    _require_tables(conn, "backtest_runs", "backtest_equity_curve")
    runs = conn.execute(
        """
        SELECT id, initial_cash, total_return_pct
        FROM backtest_runs
        WHERE initial_cash>0 AND total_return_pct IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    checked = 0
    for run in runs:
        rows = conn.execute(
            """
            SELECT total_equity FROM backtest_equity_curve
            WHERE run_id=? AND total_equity>0
            ORDER BY date, id
            """,
            (run["id"],),
        ).fetchall()
        if not rows:
            continue
        previous = float(run["initial_cash"])
        chain = 1.0
        for row in rows:
            current = float(row["total_equity"])
            chain *= current / previous
            previous = current
        chained_return_pct = (chain - 1.0) * 100.0
        assert_close(chained_return_pct, float(run["total_return_pct"]))
        checked += 1
    if checked == 0:
        pytest.skip("No completed backtest with equity curve")


def test_missing_price_handling(manager: VirtualTradeManager) -> None:
    """価格欠損positionを0円評価せず、既存のstale valuationを保持することを検証する。"""
    strategy = "__invariant_missing_price__"
    code = "JP.__MISSING_PRICE__"
    target_date = "2026-07-18"
    stale_price = 1234.5
    with manager._get_connection() as connection:
        _clear_strategy(connection, strategy)
        connection.execute(
            """
            INSERT INTO virtual_positions (
                strategy_name, code, quantity, avg_cost, market_price,
                market_value, unrealized_pl, realized_pl, updated_at
            )
            VALUES (?, ?, 1, ?, ?, ?, 0, 0, ?)
            """,
            (
                strategy,
                code,
                stale_price,
                stale_price,
                stale_price,
                target_date,
            ),
        )

    updated = manager.update_market_prices(strategy, target_date)

    with manager._get_connection() as connection:
        position = connection.execute(
            """
            SELECT market_price, market_value FROM virtual_positions
            WHERE strategy_name=? AND code=?
            """,
            (strategy, code),
        ).fetchone()
    assert position is not None
    assert updated == 0
    assert float(position["market_price"]) > 0
    assert float(position["market_value"]) > 0
    assert_close(float(position["market_price"]), stale_price)
    assert_close(float(position["market_value"]), stale_price)
