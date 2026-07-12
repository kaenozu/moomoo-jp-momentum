from pathlib import Path


source_path = Path("src/virtual_trade.py")
source = source_path.read_text(encoding="utf-8")

state_marker = "\n\n@dataclass\nclass VirtualFill:"
if source.count(state_marker) != 1:
    raise RuntimeError("VirtualFill insertion marker mismatch")
source = source.replace(
    state_marker,
    '''\n\n@dataclass\nclass _PositionReplayState:\n    quantity: int = 0\n    avg_cost: float = 0.0\n    realized_pl: float = 0.0\n    last_price: float = 0.0\n\n\n@dataclass\nclass VirtualFill:''',
)

latest_close_end = '        return float(row["close"]) if row and row["close"] is not None else None\n'
if source.count(latest_close_end) != 1:
    raise RuntimeError("latest-close insertion marker mismatch")
helpers = r'''

    def _snapshot_positions_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        rows = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND quantity > 0
            ORDER BY code
            """,
            (strategy_name,),
        ).fetchall()
        return {str(row["code"]): self._row_to_position(row) for row in rows}

    def _has_fill_history_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> bool:
        row = conn.execute(
            "SELECT 1 FROM virtual_fills WHERE strategy_name = ? LIMIT 1",
            (strategy_name,),
        ).fetchone()
        return row is not None

    def _replay_positions_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> tuple[dict[str, VirtualPosition], bool]:
        """Replay fills in chronological order and return positions plus completeness.

        ``complete`` is false when a SELL exceeds the quantity reconstructed from
        earlier fills. That indicates a legacy/imported opening position which
        cannot be dated from fill history alone.
        """
        if as_of_date:
            rows = conn.execute(
                """
                SELECT id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND COALESCE(substr(filled_at, 1, 10), '') <= ?
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name, as_of_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name,),
            ).fetchall()

        states: dict[str, _PositionReplayState] = {}
        complete = True
        for row in rows:
            code = str(row["code"])
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
            state = states.setdefault(code, _PositionReplayState())

            if side == "BUY":
                new_quantity = state.quantity + quantity
                if new_quantity <= 0:
                    complete = False
                    continue
                state.avg_cost = (
                    state.avg_cost * state.quantity + price * quantity
                ) / new_quantity
                state.quantity = new_quantity
                state.last_price = price
                continue

            if side == "SELL":
                if quantity > state.quantity:
                    complete = False
                    continue
                state.quantity -= quantity
                state.realized_pl += (
                    (price - state.avg_cost) * quantity - self.commission
                )
                state.last_price = price
                continue

            complete = False

        positions: dict[str, VirtualPosition] = {}
        for code, state in states.items():
            market_price = (
                self._latest_close(conn, code, as_of_date)
                or state.last_price
                or state.avg_cost
            )
            market_value = market_price * state.quantity
            unrealized_pl = (
                (market_price - state.avg_cost) * state.quantity
                if state.quantity > 0
                else 0.0
            )
            positions[code] = VirtualPosition(
                strategy_name=strategy_name,
                code=code,
                quantity=state.quantity,
                avg_cost=state.avg_cost,
                market_price=market_price,
                market_value=market_value,
                unrealized_pl=unrealized_pl,
                realized_pl=state.realized_pl,
            )
        return positions, complete

    def _positions_for_reference_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        reference_date: str | None,
    ) -> dict[str, VirtualPosition]:
        if reference_date and self._has_fill_history_with_conn(conn, strategy_name):
            replayed, complete = self._replay_positions_with_conn(
                conn,
                strategy_name,
                reference_date,
            )
            if complete:
                return {
                    code: position
                    for code, position in replayed.items()
                    if position.quantity > 0
                }
            logger.warning(
                "仮想ポジション履歴をfillsだけで再構築できないため"
                "現在スナップショットへフォールバックします: strategy=%s, date=%s",
                strategy_name,
                reference_date,
            )
        return self._snapshot_positions_with_conn(conn, strategy_name)

    def _rebuild_position_cache_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> bool:
        """Rebuild the current-position cache when fill history is self-contained."""
        if not self._has_fill_history_with_conn(conn, strategy_name):
            return False
        replayed, complete = self._replay_positions_with_conn(conn, strategy_name)
        if not complete:
            return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "DELETE FROM virtual_positions WHERE strategy_name = ?",
            (strategy_name,),
        )
        for position in replayed.values():
            conn.execute(
                """
                INSERT INTO virtual_positions
                (strategy_name, code, quantity, avg_cost, market_price,
                 market_value, unrealized_pl, realized_pl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_name,
                    position.code,
                    position.quantity,
                    position.avg_cost,
                    position.market_price,
                    position.market_value,
                    position.unrealized_pl,
                    position.realized_pl,
                    now,
                ),
            )
        return True
'''
source = source.replace(latest_close_end, latest_close_end + helpers)

old_buy_positions = '''        position_rows = conn.execute(
            """
            SELECT code, quantity
            FROM virtual_positions
            WHERE strategy_name = ? AND quantity > 0
            """,
            (strategy_name,),
        ).fetchall()
        position_codes = {row["code"] for row in position_rows}
        current_quantity = next(
            (int(row["quantity"]) for row in position_rows if row["code"] == code),
            0,
        )
'''
new_buy_positions = '''        positions = self._positions_for_reference_with_conn(
            conn,
            strategy_name,
            reference_date,
        )
        position_codes = set(positions)
        current_quantity = positions.get(code, VirtualPosition()).quantity
'''
if source.count(old_buy_positions) != 1:
    raise RuntimeError("BUY position block mismatch")
source = source.replace(old_buy_positions, new_buy_positions)

old_sell_position = '''        position = conn.execute(
            """
            SELECT quantity FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (strategy_name, code),
        ).fetchone()
        if not position or int(position["quantity"]) < quantity:
            return False, "売却可能な仮想ポジションが不足しています"
'''
new_sell_position = '''        positions = self._positions_for_reference_with_conn(
            conn,
            strategy_name,
            reference_date,
        )
        position = positions.get(code)
        if position is None or position.quantity < quantity:
            return False, "売却可能な仮想ポジションが不足しています"
'''
if source.count(old_sell_position) != 1:
    raise RuntimeError("SELL position block mismatch")
source = source.replace(old_sell_position, new_sell_position)

old_value_method = '''    def _position_value_with_conn(self, conn: sqlite3.Connection, strategy_name: str) -> float:
        rows = conn.execute(
            """
            SELECT quantity, avg_cost, market_price
            FROM virtual_positions
            WHERE strategy_name = ? AND quantity > 0
            """,
            (strategy_name,),
        ).fetchall()
        return sum((float(r["market_price"] or r["avg_cost"]) * int(r["quantity"])) for r in rows)
'''
new_value_method = '''    def _position_value_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        target_date: str | None = None,
    ) -> float:
        positions = self._positions_for_reference_with_conn(
            conn,
            strategy_name,
            target_date,
        )
        return sum(
            float(position.market_value or 0.0)
            for position in positions.values()
        )
'''
if source.count(old_value_method) != 1:
    raise RuntimeError("position value method mismatch")
source = source.replace(old_value_method, new_value_method)
source = source.replace(
    "        position_value = self._position_value_with_conn(conn, strategy_name)\n",
    "        position_value = self._position_value_with_conn(\n            conn, strategy_name, target_date\n        )\n",
    1,
)

get_positions_start = source.index("    def get_positions(")
get_positions_end = source.index("    def _row_to_position(", get_positions_start)
new_get_positions = '''    def get_positions(
        self,
        strategy_name: str = "default",
        as_of_date: str | None = None,
    ) -> list[VirtualPosition]:
        with self._get_connection() as conn:
            positions = self._positions_for_reference_with_conn(
                conn,
                strategy_name,
                as_of_date,
            )
        return [positions[code] for code in sorted(positions)]

'''
source = source[:get_positions_start] + new_get_positions + source[get_positions_end:]

update_start = source.index("    def _update_position_and_cash(")
update_end = source.index("    def get_strategy_performance", update_start)
new_update_method = '''    def _update_position_and_cash(
        self,
        conn: sqlite3.Connection,
        order: VirtualOrder,
        fill: VirtualFill,
    ) -> None:
        gross = fill.price * fill.quantity
        if self._rebuild_position_cache_from_fills(conn, order.strategy_name):
            delta = (
                -(gross + self.commission)
                if order.side == "BUY"
                else gross - self.commission
            )
            self._apply_cash_delta(
                conn,
                order.strategy_name,
                fill.filled_at,
                delta,
            )
            return

        # Legacy/imported opening positions may not have matching BUY fills.
        # Preserve the existing incremental behavior for those databases.
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (order.strategy_name, order.code),
        ).fetchone()

        if order.side == "BUY":
            if pos:
                new_quantity = int(pos["quantity"]) + fill.quantity
                new_avg_cost = (
                    float(pos["avg_cost"]) * int(pos["quantity"]) + gross
                ) / new_quantity
                conn.execute(
                    """
                    UPDATE virtual_positions
                    SET quantity = ?, avg_cost = ?, market_price = ?, market_value = ?,
                        unrealized_pl = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_quantity,
                        new_avg_cost,
                        fill.price,
                        fill.price * new_quantity,
                        (fill.price - new_avg_cost) * new_quantity,
                        now,
                        pos["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO virtual_positions
                    (strategy_name, code, quantity, avg_cost, market_price, market_value,
                     unrealized_pl, realized_pl, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        order.strategy_name,
                        order.code,
                        fill.quantity,
                        fill.price,
                        fill.price,
                        gross,
                        now,
                    ),
                )
            self._apply_cash_delta(
                conn,
                order.strategy_name,
                fill.filled_at,
                -(gross + self.commission),
            )

        elif order.side == "SELL" and pos:
            current_qty = int(pos["quantity"])
            sell_qty = min(fill.quantity, current_qty)
            new_quantity = current_qty - sell_qty
            realized_pl = (
                (fill.price - float(pos["avg_cost"])) * sell_qty
                - self.commission
            )
            market_value = fill.price * new_quantity
            unrealized_pl = (
                (fill.price - float(pos["avg_cost"])) * new_quantity
                if new_quantity > 0
                else 0
            )
            conn.execute(
                """
                UPDATE virtual_positions
                SET quantity = ?, market_price = ?, market_value = ?, unrealized_pl = ?,
                    realized_pl = COALESCE(realized_pl, 0) + ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_quantity,
                    fill.price,
                    market_value,
                    unrealized_pl,
                    realized_pl,
                    now,
                    pos["id"],
                ),
            )
            self._apply_cash_delta(
                conn,
                order.strategy_name,
                fill.filled_at,
                gross - self.commission,
            )

'''
source = source[:update_start] + new_update_method + source[update_end:]

if source.count("for pos in self.get_positions(strategy_name):") != 1:
    raise RuntimeError("generate_exits position loop mismatch")
source = source.replace(
    "for pos in self.get_positions(strategy_name):",
    "for pos in self.get_positions(strategy_name, as_of_date=target_date):",
)

old_save_value = "            position_value = self._position_value_with_conn(conn, strategy_name)\n"
if source.count(old_save_value) != 1:
    raise RuntimeError("save_equity_curve value call mismatch")
source = source.replace(
    old_save_value,
    "            position_value = self._position_value_with_conn(\n                conn, strategy_name, target_date\n            )\n",
)

compile(source, "src/virtual_trade.py", "exec")
source_path.write_text(source, encoding="utf-8")


test_content = r'''"""Historical virtual-position reconstruction regression tests."""

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
'''
compile(test_content, "tests/test_virtual_trade_position_history.py", "exec")
Path("tests/test_virtual_trade_position_history.py").write_text(
    test_content,
    encoding="utf-8",
)

pyright_path = Path("pyrightconfig.json")
pyright_text = pyright_path.read_text(encoding="utf-8")
anchor = '    "tests/test_virtual_trade_order_dates.py",\n'
addition = anchor + '    "tests/test_virtual_trade_position_history.py",\n'
if pyright_text.count(anchor) != 1:
    raise RuntimeError("pyright insertion marker mismatch")
pyright_path.write_text(pyright_text.replace(anchor, addition), encoding="utf-8")
