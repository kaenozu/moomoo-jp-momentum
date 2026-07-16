"""Temporary correction script for the staged V2 refactor."""

from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{path}: correction boundaries not found")
    path.write_text(
        text[:start_index] + replacement + text[end_index:],
        encoding="utf-8",
    )


backtest_path = ROOT / "src/backtest_runner.py"
replace_once(
    backtest_path,
    "from .execution_engine import ExecutionEngine, PositionState\n",
    "from .execution_engine import ExecutionEngine, PositionState, Side\n",
)
replace_once(
    backtest_path,
    '    side: str  # "BUY" or "SELL"\n',
    '    side: Side  # "BUY" or "SELL"\n',
)

virtual_path = ROOT / "src/virtual_trade.py"
replacement = '''    def _update_position_and_cash(
        self,
        conn: sqlite3.Connection,
        order: VirtualOrder,
        fill: VirtualFill,
    ) -> None:
        """Persist the pure ExecutionEngine transition in one DB transaction."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (order.strategy_name, order.code),
        ).fetchone()
        current_position = PositionState(
            quantity=int(pos["quantity"]) if pos else 0,
            avg_cost=float(pos["avg_cost"]) if pos else 0.0,
            realized_pl=float(pos["realized_pl"] or 0.0) if pos else 0.0,
        )
        current_cash = self._get_cash_with_conn(
            conn,
            order.strategy_name,
            fill.filled_at,
        )
        if order.side == "BUY":
            side = "BUY"
        elif order.side == "SELL":
            side = "SELL"
        else:
            raise ValueError(f"unsupported side: {order.side}")
        transition = self.execution_engine.apply_fill(
            current_cash,
            current_position,
            side,
            fill.price,
            fill.quantity,
        )
        market_value = fill.price * transition.position.quantity
        unrealized_pl = (
            (fill.price - transition.position.avg_cost)
            * transition.position.quantity
            if transition.position.quantity > 0
            else 0.0
        )

        if pos:
            conn.execute(
                """
                UPDATE virtual_positions
                SET quantity = ?, avg_cost = ?, market_price = ?, market_value = ?,
                    unrealized_pl = ?, realized_pl = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    transition.position.quantity,
                    transition.position.avg_cost,
                    fill.price,
                    market_value,
                    unrealized_pl,
                    transition.position.realized_pl,
                    now,
                    pos["id"],
                ),
            )
        elif transition.position.quantity > 0:
            conn.execute(
                """
                INSERT INTO virtual_positions
                (strategy_name, code, quantity, avg_cost, market_price, market_value,
                 unrealized_pl, realized_pl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.strategy_name,
                    order.code,
                    transition.position.quantity,
                    transition.position.avg_cost,
                    fill.price,
                    market_value,
                    unrealized_pl,
                    transition.position.realized_pl,
                    now,
                ),
            )
        self._set_cash(
            conn,
            order.strategy_name,
            fill.filled_at,
            transition.cash,
        )

'''
replace_between(
    virtual_path,
    "    def _update_position_and_cash(",
    "    def get_strategy_performance(",
    replacement,
)

print("Applied V2 correction patch.")
