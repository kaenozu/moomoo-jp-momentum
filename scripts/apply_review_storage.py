"""One-time migration for database, virtual cash, and reporting fixes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_function(path: str, name: str, source: str) -> None:
    text = read(path)
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*)def {re.escape(name)}\s*\(")
    start = indent = None
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            start = index
            indent = len(match.group(1))
            break
    if start is None or indent is None:
        raise RuntimeError(f"{path}: {name} not found")
    end = len(lines)
    next_def = re.compile(rf"^{{{indent}}}(?:def|class)\s+")
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and next_def.match(lines[index]):
            end = index
            break
    write(path, "".join(lines[:start]) + source.rstrip() + "\n\n" + "".join(lines[end:]))


replace_function(
    "src/data_store.py",
    "_init_db",
    '''    def _init_db(self) -> None:
        """DB初期化と旧signals一意制約の移行を行う。"""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(CREATE_TABLES_SQL)
            self._ensure_columns(conn)
            self._migrate_signals_unique_key(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            logger.info("データベースを初期化しました: %s", self.db_path)
''',
)

data_store = read("src/data_store.py")
if "def _migrate_signals_unique_key" not in data_store:
    marker = "    def _get_connection(self) -> sqlite3.Connection:\n"
    migration = '''    def _migrate_signals_unique_key(self, conn: sqlite3.Connection) -> None:
        """旧UNIQUE(code,date)をUNIQUE(strategy_name,code,date)へ移行する。"""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        table_sql = (row[0] if row and row[0] else "").replace(" ", "").lower()
        legacy = (
            "unique(code,date)" in table_sql
            and "unique(strategy_name,code,date)" not in table_sql
        )
        if legacy:
            conn.executescript(
                """
                ALTER TABLE signals RENAME TO signals_legacy;
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    strategy_name TEXT NOT NULL DEFAULT 'momentum',
                    score REAL,
                    reason TEXT,
                    risk_warnings TEXT,
                    price_at_signal REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(strategy_name, code, date)
                );
                INSERT INTO signals
                    (id, code, date, signal_type, strategy_name, score, reason,
                     risk_warnings, price_at_signal, created_at)
                SELECT id, code, date, signal_type,
                       COALESCE(strategy_name, 'momentum'), score, reason,
                       risk_warnings, price_at_signal, created_at
                FROM signals_legacy;
                DROP TABLE signals_legacy;
                """
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_strategy_code_date "
            "ON signals(strategy_name, code, date)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)")

'''
    if marker not in data_store:
        raise RuntimeError("src/data_store.py: connection marker missing")
    write("src/data_store.py", data_store.replace(marker, migration + marker, 1))

replace_function(
    "src/data_store.py",
    "_get_connection",
    '''    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
''',
)

replace_function(
    "src/virtual_trade.py",
    "_get_connection",
    '''    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
''',
)

virtual_trade = read("src/virtual_trade.py")
if "def _pending_buy_reservation_with_conn" not in virtual_trade:
    marker = "    def _validate_buy_order(\n"
    helper = '''    def _pending_buy_reservation_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> float:
        rows = conn.execute(
            """
            SELECT code, quantity, order_type, limit_price, submitted_at
            FROM virtual_orders
            WHERE strategy_name=? AND side='BUY' AND status='PENDING'
            """,
            (strategy_name,),
        ).fetchall()
        reserved = 0.0
        for row in rows:
            ref_date = as_of_date or str(row["submitted_at"])[:10]
            price = (
                float(row["limit_price"])
                if row["order_type"] == "LIMIT_SIM" and row["limit_price"] is not None
                else self._latest_close(conn, row["code"], ref_date)
            )
            if price is not None and price > 0:
                reserved += price * int(row["quantity"]) + self.commission
        return reserved

    def get_available_cash(
        self,
        strategy_name: str = "default",
        as_of_date: str | None = None,
    ) -> float:
        with self._get_connection() as conn:
            cash = self._get_cash_with_conn(conn, strategy_name, as_of_date)
            reserved = self._pending_buy_reservation_with_conn(
                conn, strategy_name, as_of_date
            )
        return max(0.0, cash - reserved)

'''
    if marker not in virtual_trade:
        raise RuntimeError("src/virtual_trade.py: validation marker missing")
    write("src/virtual_trade.py", virtual_trade.replace(marker, helper + marker, 1))

replace_function(
    "src/virtual_trade.py",
    "_get_cash_with_conn",
    '''    def _get_cash_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> float:
        if as_of_date:
            row = conn.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name=? AND date<=?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name, as_of_date),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name=?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name,),
            ).fetchone()
        if row and row["cash"] is not None:
            return float(row["cash"])
        return self.initial_cash
''',
)

replace_function(
    "src/virtual_trade.py",
    "_validate_buy_order",
    '''    def _validate_buy_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float],
        submitted_at: str | None = None,
    ) -> tuple[bool, str]:
        ok, reason = self._symbol_universe_status(conn, code)
        if not ok:
            return False, reason
        ref_price = limit_price if order_type == "LIMIT_SIM" else self._latest_close(
            conn, code, submitted_at
        )
        if ref_price is None or ref_price <= 0:
            return False, "参照価格を取得できません"
        if not self.min_trade_price <= ref_price <= self.max_trade_price:
            return False, "価格が取引可能範囲外です"
        order_amount = ref_price * quantity + self.commission
        if ref_price * quantity > self.max_position_amount:
            return False, "注文金額が1銘柄上限を超えています"

        duplicate = conn.execute(
            """
            SELECT 1 FROM virtual_orders
            WHERE strategy_name=? AND code=? AND side='BUY' AND status='PENDING'
            LIMIT 1
            """,
            (strategy_name, code),
        ).fetchone()
        if duplicate:
            return False, "同一銘柄の未約定BUY注文が既に存在します"

        position = conn.execute(
            "SELECT quantity FROM virtual_positions WHERE strategy_name=? AND code=?",
            (strategy_name, code),
        ).fetchone()
        current_quantity = int(position["quantity"]) if position else 0
        if current_quantity + quantity > self.max_position_per_symbol:
            return False, "同一銘柄の保有上限に達しています"

        held_count = int(conn.execute(
            "SELECT COUNT(*) FROM virtual_positions WHERE strategy_name=? AND quantity>0",
            (strategy_name,),
        ).fetchone()[0])
        pending_count = int(conn.execute(
            """
            SELECT COUNT(DISTINCT code) FROM virtual_orders
            WHERE strategy_name=? AND side='BUY' AND status='PENDING'
            """,
            (strategy_name,),
        ).fetchone()[0])
        if held_count + pending_count >= self.max_total_positions:
            return False, "保有・未約定銘柄数上限に達しています"

        cash = self._get_cash_with_conn(conn, strategy_name, submitted_at)
        reserved = self._pending_buy_reservation_with_conn(
            conn, strategy_name, submitted_at
        )
        if order_amount > max(0.0, cash - reserved):
            return False, "未約定注文を含めると仮想cashが不足しています"
        return True, ""
''',
)

replace_function(
    "src/virtual_trade.py",
    "_apply_cash_delta",
    '''    def _apply_cash_delta(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        target_date: str,
        delta: float,
    ) -> None:
        current_cash = self._get_cash_with_conn(conn, strategy_name, target_date)
        new_cash = current_cash + delta
        if new_cash < -1e-9:
            raise ValueError(
                f"仮想cashがマイナスになります: current={current_cash}, delta={delta}"
            )
        self._set_cash(conn, strategy_name, target_date, max(0.0, new_cash))
''',
)

virtual_trade = read("src/virtual_trade.py")
needle = "            fill_price = round(float(fill_price), 1)\n\n            existing_fill = conn.execute("
if "約定時cash不足" not in virtual_trade:
    replacement = '''            fill_price = round(float(fill_price), 1)

            if order.side == "BUY":
                required_cash = fill_price * order.quantity + self.commission
                actual_cash = self._get_cash_with_conn(
                    conn, order.strategy_name, filled_at
                )
                if required_cash > actual_cash + 1e-9:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        """
                        UPDATE virtual_orders
                        SET status='CANCELLED', cancelled_at=?, fill_reason=?, updated_at=?
                        WHERE id=? AND status='PENDING'
                        """,
                        (now, "約定時cash不足", now, order.id),
                    )
                    logger.warning(
                        "BUY約定をキャンセル: %s required=%.1f cash=%.1f",
                        order.code, required_cash, actual_cash,
                    )
                    return None

            existing_fill = conn.execute('''
    if needle not in virtual_trade:
        raise RuntimeError("src/virtual_trade.py: fill insertion point missing")
    write("src/virtual_trade.py", virtual_trade.replace(needle, replacement, 1))

replace_function(
    "src/virtual_report.py",
    "get_closed_trades",
    '''    def get_closed_trades(
        self,
        strategy_name: str = "default",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[ClosedTrade]:
        """銘柄別FIFOと数量消化でクローズ済みトレードを生成する。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT o.code, o.side, f.quantity, f.price,
                       f.filled_at, o.exit_reason
                FROM virtual_fills f
                JOIN virtual_orders o ON f.order_id=o.id
                WHERE o.strategy_name=?
                ORDER BY f.filled_at, f.id
                """,
                (strategy_name,),
            ).fetchall()

        queues: dict[str, list[dict]] = {}
        closed: list[ClosedTrade] = []
        for row in rows:
            code = str(row["code"])
            fill_date = str(row["filled_at"])[:10]
            if row["side"] == "BUY":
                queues.setdefault(code, []).append({
                    "date": fill_date,
                    "price": float(row["price"]),
                    "quantity": int(row["quantity"]),
                })
                continue
            if row["side"] != "SELL":
                continue
            remaining_sell = int(row["quantity"])
            queue = queues.setdefault(code, [])
            while remaining_sell > 0 and queue:
                buy = queue[0]
                quantity = min(remaining_sell, int(buy["quantity"]))
                entry_price = float(buy["price"])
                exit_price = float(row["price"])
                try:
                    holding_days = (
                        datetime.strptime(fill_date, "%Y-%m-%d")
                        - datetime.strptime(str(buy["date"]), "%Y-%m-%d")
                    ).days
                except ValueError:
                    holding_days = 0
                trade = ClosedTrade(
                    code=code,
                    strategy_name=strategy_name,
                    entry_date=str(buy["date"]),
                    exit_date=fill_date,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    realized_pl=(exit_price-entry_price)*quantity,
                    return_pct=(exit_price-entry_price)/entry_price*100 if entry_price else 0,
                    holding_days=holding_days,
                    exit_reason=row["exit_reason"] or "unknown",
                )
                if (not from_date or trade.exit_date >= from_date) and (
                    not to_date or trade.exit_date <= to_date
                ):
                    closed.append(trade)
                remaining_sell -= quantity
                buy["quantity"] = int(buy["quantity"]) - quantity
                if int(buy["quantity"]) == 0:
                    queue.pop(0)
        return closed
''',
)

replace_function(
    "src/virtual_report.py",
    "_get_equity_curve_sorted",
    '''    def _get_equity_curve_sorted(
        self,
        strategy_name: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[dict]:
        curve = self.manager.get_equity_curve(strategy_name, limit=5000)
        curve = [
            row for row in curve
            if (not from_date or row["date"] >= from_date)
            and (not to_date or row["date"] <= to_date)
        ]
        curve.sort(key=lambda row: row["date"])
        return curve
''',
)

report = read("src/virtual_report.py")
report = report.replace(
    "        report.closed_trades = self.get_closed_trades(strategy_name)\n",
    "        report.closed_trades = self.get_closed_trades(strategy_name, from_date, to_date)\n",
)
report = report.replace(
    "        equity_curve = self._get_equity_curve_sorted(strategy_name)\n",
    "        equity_curve = self._get_equity_curve_sorted(strategy_name, from_date, to_date)\n",
)
write("src/virtual_report.py", report)

print("storage review fixes applied")
