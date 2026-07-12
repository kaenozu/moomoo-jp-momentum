"""Apply virtual-fill commission persistence to existing source files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_models() -> None:
    path = ROOT / "src" / "models.py"
    text = path.read_text(encoding="utf-8")
    old = """CREATE TABLE IF NOT EXISTS virtual_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    fill_mode TEXT,
    created_at TEXT
);"""
    new = """CREATE TABLE IF NOT EXISTS virtual_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    fill_mode TEXT,
    commission REAL,
    created_at TEXT
);"""
    path.write_text(replace_once(text, old, new, "models virtual_fills"), encoding="utf-8")


def patch_migrations() -> None:
    path = ROOT / "src" / "migrations.py"
    text = path.read_text(encoding="utf-8")
    if "def migrate_virtual_fills_commission" in text:
        return
    addition = '''


def migrate_virtual_fills_commission(conn: sqlite3.Connection) -> None:
    """Add per-fill commission without guessing values for legacy rows.

    Existing rows remain NULL because their actual historical commission cannot
    be derived safely from the current configuration.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'virtual_fills'"
    ).fetchone()
    if table_exists is None:
        return

    columns = conn.execute("PRAGMA table_info(virtual_fills)").fetchall()
    if any(column[1] == "commission" for column in columns):
        return

    conn.execute("ALTER TABLE virtual_fills ADD COLUMN commission REAL")
    logger.info("migration: added nullable virtual_fills.commission")
'''
    path.write_text(text.rstrip() + addition, encoding="utf-8")


def patch_virtual_trade() -> None:
    path = ROOT / "src" / "virtual_trade.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        """from .migrations import (
    migrate_virtual_orders_pending_index,
    migrate_virtual_orders_reserved_amount,
)""",
        """from .migrations import (
    migrate_virtual_fills_commission,
    migrate_virtual_orders_pending_index,
    migrate_virtual_orders_reserved_amount,
)""",
        "migration import",
    )
    text = replace_once(
        text,
        """    filled_at: str = ""
    fill_mode: str = ""


class VirtualTradeManager:""",
        """    filled_at: str = ""
    fill_mode: str = ""
    commission: Optional[float] = None


class VirtualTradeManager:""",
        "VirtualFill commission field",
    )
    text = replace_once(
        text,
        """        with self._get_connection() as conn:
            migrate_virtual_orders_reserved_amount(conn)
            migrate_virtual_orders_pending_index(conn)""",
        """        with self._get_connection() as conn:
            migrate_virtual_orders_reserved_amount(conn)
            migrate_virtual_orders_pending_index(conn)
            migrate_virtual_fills_commission(conn)""",
        "manager migration call",
    )

    text = text.replace(
        "SELECT id, order_id, code, side, quantity, price, filled_at\n                FROM virtual_fills",
        "SELECT id, order_id, code, side, quantity, price, filled_at, commission\n                FROM virtual_fills",
    )
    if text.count("SELECT id, order_id, code, side, quantity, price, filled_at, commission") != 2:
        raise RuntimeError("position replay SELECT replacements failed")

    replay_values_old = """            code = str(row["code"])
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
            state = states.setdefault(code, _PositionReplayState())
"""
    replay_values_new = """            code = str(row["code"])
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
            commission, commission_valid = self._commission_from_fill_row(row)
            if not commission_valid:
                complete = False
                continue
            state = states.setdefault(code, _PositionReplayState())
"""
    text = replace_once(text, replay_values_old, replay_values_new, "position commission resolution")
    text = replace_once(
        text,
        """                state.realized_pl += (
                    (price - state.avg_cost) * quantity - self.commission
                )""",
        """                state.realized_pl += (
                    (price - state.avg_cost) * quantity - commission
                )""",
        "position realized commission",
    )

    text = text.replace(
        "SELECT order_id, side, quantity, price, filled_at\n                FROM virtual_fills",
        "SELECT order_id, side, quantity, price, filled_at, commission\n                FROM virtual_fills",
    )
    if text.count("SELECT order_id, side, quantity, price, filled_at, commission") != 2:
        raise RuntimeError("cash replay SELECT replacements failed")

    old_cash_function = '''    def _cash_delta_from_fill_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[float, bool]:
        if (
            row["side"] is None
            or row["quantity"] is None
            or row["price"] is None
            or not row["filled_at"]
        ):
            return 0.0, False
        try:
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
        except (TypeError, ValueError):
            return 0.0, False
        if quantity <= 0 or price < 0:
            return 0.0, False
        gross = price * quantity
        if side == "BUY":
            return -(gross + self.commission), True
        if side == "SELL":
            return gross - self.commission, True
        return 0.0, False
'''
    new_cash_function = '''    def _commission_from_fill_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[float, bool]:
        raw_commission = (
            row["commission"]
            if "commission" in row.keys()
            else None
        )
        if raw_commission is None:
            return self.commission, True
        try:
            commission = float(raw_commission)
        except (TypeError, ValueError):
            return 0.0, False
        if commission < 0:
            return 0.0, False
        return commission, True

    def _cash_delta_from_fill_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[float, bool]:
        if (
            row["side"] is None
            or row["quantity"] is None
            or row["price"] is None
            or not row["filled_at"]
        ):
            return 0.0, False
        try:
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
        except (TypeError, ValueError):
            return 0.0, False
        commission, commission_valid = self._commission_from_fill_row(row)
        if quantity <= 0 or price < 0 or not commission_valid:
            return 0.0, False
        gross = price * quantity
        if side == "BUY":
            return -(gross + commission), True
        if side == "SELL":
            return gross - commission, True
        return 0.0, False
'''
    text = replace_once(text, old_cash_function, new_cash_function, "cash delta function")

    text = replace_once(
        text,
        """            SELECT side, quantity, price, filled_at
            FROM virtual_fills""",
        """            SELECT side, quantity, price, filled_at, commission
            FROM virtual_fills""",
        "equity rebuild SELECT",
    )

    text = replace_once(
        text,
        """                filled_at=filled_at,
                fill_mode=fill_mode,
            )""",
        """                filled_at=filled_at,
                fill_mode=fill_mode,
                commission=self.commission,
            )""",
        "fill object commission",
    )
    text = replace_once(
        text,
        """                INSERT INTO virtual_fills
                (order_id, strategy_name, code, side, quantity, price,
                 filled_at, fill_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        """                INSERT INTO virtual_fills
                (order_id, strategy_name, code, side, quantity, price,
                 filled_at, fill_mode, commission, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        "fill INSERT columns",
    )
    text = replace_once(
        text,
        """                    fill.filled_at,
                    fill.fill_mode,
                    now,""",
        """                    fill.filled_at,
                    fill.fill_mode,
                    fill.commission,
                    now,""",
        "fill INSERT values",
    )

    text = replace_once(
        text,
        """    ) -> None:
        gross = fill.price * fill.quantity
        requires_rebuild = self._fill_requires_cache_rebuild(""",
        """    ) -> None:
        gross = fill.price * fill.quantity
        commission = (
            fill.commission
            if fill.commission is not None
            else self.commission
        )
        requires_rebuild = self._fill_requires_cache_rebuild(""",
        "update position commission local",
    )
    text = replace_once(
        text,
        """                delta = (
                    -(gross + self.commission)
                    if order.side == "BUY"
                    else gross - self.commission
                )""",
        """                delta = (
                    -(gross + commission)
                    if order.side == "BUY"
                    else gross - commission
                )""",
        "out of order delta commission",
    )
    text = replace_once(
        text,
        """                -(gross + self.commission),
            )""",
        """                -(gross + commission),
            )""",
        "BUY incremental commission",
    )
    text = replace_once(
        text,
        """            realized_pl = (
                (fill.price - float(pos["avg_cost"])) * sell_qty
                - self.commission
            )""",
        """            realized_pl = (
                (fill.price - float(pos["avg_cost"])) * sell_qty
                - commission
            )""",
        "SELL realized commission",
    )
    text = replace_once(
        text,
        """                gross - self.commission,
            )""",
        """                gross - commission,
            )""",
        "SELL incremental commission",
    )

    old_get_fills = 'return [VirtualFill(id=r["id"], order_id=r["order_id"], strategy_name=r["strategy_name"], code=r["code"], side=r["side"], quantity=r["quantity"], price=r["price"], filled_at=r["filled_at"], fill_mode=r["fill_mode"]) for r in rows]'
    new_get_fills = '''return [
            VirtualFill(
                id=row["id"],
                order_id=row["order_id"],
                strategy_name=row["strategy_name"],
                code=row["code"],
                side=row["side"],
                quantity=row["quantity"],
                price=row["price"],
                filled_at=row["filled_at"],
                fill_mode=row["fill_mode"],
                commission=row["commission"],
            )
            for row in rows
        ]'''
    text = replace_once(text, old_get_fills, new_get_fills, "get_fills mapping")

    path.write_text(text, encoding="utf-8")


def patch_pyright() -> None:
    path = ROOT / "pyrightconfig.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    include = config["include"]
    for item in (
        "src/virtual_trade_integrity.py",
        "tests/test_virtual_fill_commission_integrity.py",
    ):
        if item not in include:
            include.append(item)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    patch_models()
    patch_migrations()
    patch_virtual_trade()
    patch_pyright()


if __name__ == "__main__":
    main()
