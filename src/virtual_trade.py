"""
アプリ内ペーパートレードモジュール

moomoo APIの注文系APIは一切使わず、SQLite上で仮想注文・仮想約定・
仮想ポジション・仮想損益を管理する。
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)

# SQLite extended result codes (stable across Python 3.11+).
_SQLITE_BUSY_CODE = 5
_SQLITE_LOCKED_CODE = 6


def is_expected_duplicate_conflict(error: sqlite3.IntegrityError) -> bool:
    """既知の重複注文制約違反だけを「通常の注文拒否」として分類する。

    現在のスキーマで期待される唯一の制約は、未約定注文の一意インデックス
    ``idx_virtual_orders_pending`` 違反である。それ以外の ``IntegrityError``
    （CHECK / NOT NULL / trigger / schema不整合）は予期しないDB障害として
    握りつぶさず、呼び出し元へ再raiseする。
    """
    message = str(error)
    marker = "virtual_orders.strategy_name, virtual_orders.code, virtual_orders.side"
    if marker in message:
        return True
    # 古いSQLite/バージョンで一意インデックス名が本文に現れる場合の互換fallback。
    # 新しいSQLiteではSQLが本文に含まれるため、通常は到達しない。
    if "idx_virtual_orders_pending" in message:
        return True
    return False


def is_sqlite_busy_or_locked(error: sqlite3.OperationalError) -> bool:
    """``SQLITE_BUSY`` / ``SQLITE_LOCKED`` をSQLite error code/nameで判定する。

    error code属性が存在しない実行環境向けに、旧来の ``"locked"`` 文字列
    マッチを狭いfallbackとしてのみ維持する。
    """
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (int(code) & 0xFF) in (_SQLITE_BUSY_CODE, _SQLITE_LOCKED_CODE)
    name = getattr(error, "sqlite_errorname", None)
    if name is not None:
        normalized_name = str(name)
        return normalized_name in ("SQLITE_BUSY", "SQLITE_LOCKED") or normalized_name.startswith(
            ("SQLITE_BUSY_", "SQLITE_LOCKED_")
        )
    return "locked" in str(error).lower()


@dataclass
class VirtualOrder:
    """仮想注文"""
    id: Optional[int] = None
    strategy_name: str = ""
    code: str = ""
    side: str = ""  # BUY / SELL
    quantity: int = 0
    order_type: str = ""  # MARKET_SIM / LIMIT_SIM
    limit_price: Optional[float] = None
    status: str = "PENDING"  # PENDING / FILLED / CANCELLED
    signal_id: Optional[int] = None
    submitted_at: str = ""
    filled_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    fill_price: Optional[float] = None
    fill_reason: str = ""


@dataclass
class VirtualPosition:
    """仮想ポジション"""
    id: Optional[int] = None
    strategy_name: str = ""
    code: str = ""
    quantity: int = 0
    avg_cost: float = 0.0
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pl: Optional[float] = None
    realized_pl: float = 0.0


@dataclass
class VirtualFill:
    """仮想約定"""
    id: Optional[int] = None
    order_id: int = 0
    strategy_name: str = ""
    code: str = ""
    side: str = ""
    quantity: int = 0
    price: float = 0.0
    filled_at: str = ""
    fill_mode: str = ""


class VirtualTradeManager:
    """仮想トレード管理クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

        vt_config = config.get("virtual_trade", {})
        self.enabled = vt_config.get("enabled", True)
        self.initial_cash = float(vt_config.get("initial_cash", 100000))
        self.max_position_amount = float(vt_config.get("max_position_amount", 20000))
        self.max_total_positions = int(vt_config.get("max_total_positions", 5))
        self.max_position_per_symbol = int(vt_config.get("max_position_per_symbol", 1))
        self.market_fill_mode = vt_config.get("market_fill_mode", "next_day_open")
        self.slippage_bps = float(vt_config.get("slippage_bps", 10))
        self.commission = float(vt_config.get("commission", 0))
        self.default_benchmark = vt_config.get("default_benchmark", "JP.2559")

        universe_config = config.get("universe", {})
        self.min_trade_price = float(universe_config.get("min_trade_price", 500))
        self.max_trade_price = float(universe_config.get("max_trade_price", 20000))

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _submitted_date(self, order: VirtualOrder) -> str:
        return str(order.submitted_at)[:10]

    def _latest_close(self, conn: sqlite3.Connection, code: str, target_date: Optional[str] = None) -> Optional[float]:
        if target_date:
            row = conn.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (code, target_date),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ?
                ORDER BY date DESC LIMIT 1
                """,
                (code,),
            ).fetchone()
        return float(row["close"]) if row and row["close"] is not None else None

    def _symbol_universe_status(self, conn: sqlite3.Connection, code: str) -> tuple[bool, str]:
        row = conn.execute(
            """
            SELECT code, type, role, tradable, name
            FROM symbols
            WHERE code = ?
            """,
            (code,),
        ).fetchone()
        if not row:
            return False, "symbolsテーブルに銘柄が存在しません"

        role = row["role"] or "trade_candidate"
        tradable = bool(row["tradable"])
        if role != "trade_candidate":
            return False, f"role={role} のため仮想注文対象外です"
        if not tradable:
            return False, "tradable=false のため仮想注文対象外です"
        return True, ""

    def _pending_buy_reservation_with_conn(
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

    def _validate_buy_order(
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

    def _validate_sell_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
    ) -> tuple[bool, str]:
        pos = conn.execute(
            """
            SELECT quantity FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (strategy_name, code),
        ).fetchone()
        if not pos or pos["quantity"] < quantity:
            return False, "売却可能な仮想ポジションが不足しています"

        pending = conn.execute(
            """
            SELECT 1 FROM virtual_orders
            WHERE strategy_name = ? AND code = ? AND side = 'SELL' AND status = 'PENDING'
            LIMIT 1
            """,
            (strategy_name, code),
        ).fetchone()
        if pending:
            return False, "同一銘柄の未約定SELL注文が既に存在します"
        return True, ""

    def get_cash(self, strategy_name: str = "default", as_of_date: str | None = None) -> float:
        with self._get_connection() as conn:
            if as_of_date:
                row = conn.execute(
                    """
                    SELECT cash FROM virtual_equity_curve
                    WHERE strategy_name = ? AND date <= ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (strategy_name, as_of_date),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT cash FROM virtual_equity_curve
                    WHERE strategy_name = ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (strategy_name,),
                ).fetchone()
            if row and row["cash"] is not None:
                return float(row["cash"])
        return self.initial_cash

    def _get_cash_with_conn(
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

    def _position_value_with_conn(self, conn: sqlite3.Connection, strategy_name: str) -> float:
        rows = conn.execute(
            """
            SELECT quantity, avg_cost, market_price
            FROM virtual_positions
            WHERE strategy_name = ? AND quantity > 0
            """,
            (strategy_name,),
        ).fetchall()
        return sum((float(r["market_price"] or r["avg_cost"]) * int(r["quantity"])) for r in rows)

    def _set_cash(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        target_date: str,
        new_cash: float,
    ) -> None:
        position_value = self._position_value_with_conn(conn, strategy_name)
        total_equity = new_cash + position_value
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, benchmark_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_name, date) DO UPDATE SET
                cash = excluded.cash,
                position_value = excluded.position_value,
                total_equity = excluded.total_equity,
                benchmark_code = excluded.benchmark_code,
                created_at = excluded.created_at
            """,
            (strategy_name, target_date, new_cash, position_value, total_equity, self.default_benchmark, now),
        )

    def _apply_cash_delta(
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

    def get_positions(self, strategy_name: str = "default") -> list[VirtualPosition]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM virtual_positions
                WHERE strategy_name = ? AND quantity > 0
                ORDER BY code
                """,
                (strategy_name,),
            ).fetchall()
        return [self._row_to_position(row) for row in rows]

    def _row_to_position(self, row: sqlite3.Row) -> VirtualPosition:
        return VirtualPosition(
            id=row["id"],
            strategy_name=row["strategy_name"],
            code=row["code"],
            quantity=row["quantity"],
            avg_cost=row["avg_cost"],
            market_price=row["market_price"],
            market_value=row["market_value"],
            unrealized_pl=row["unrealized_pl"],
            realized_pl=row["realized_pl"],
        )

    def get_pending_orders(self, strategy_name: str = "default") -> list[VirtualOrder]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM virtual_orders
                WHERE strategy_name = ? AND status = 'PENDING'
                ORDER BY submitted_at, id
                """,
                (strategy_name,),
            ).fetchall()
        return [self._row_to_order(row) for row in rows]

    def _row_to_order(self, row: sqlite3.Row) -> VirtualOrder:
        return VirtualOrder(
            id=row["id"],
            strategy_name=row["strategy_name"],
            code=row["code"],
            side=row["side"],
            quantity=row["quantity"],
            order_type=row["order_type"],
            limit_price=row["limit_price"],
            status=row["status"],
            signal_id=row["signal_id"],
            submitted_at=row["submitted_at"],
            filled_at=row["filled_at"],
            cancelled_at=row["cancelled_at"],
            fill_price=row["fill_price"],
            fill_reason=row["fill_reason"],
        )

    def place_order(
        self,
        strategy_name: str,
        code: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET_SIM",
        limit_price: Optional[float] = None,
        signal_id: Optional[int] = None,
        submitted_at: Optional[str] = None,
        exit_reason: Optional[str] = None,
    ) -> Optional[VirtualOrder]:
        """仮想注文を作成する。moomooには注文を送信しない。"""
        if not self.enabled:
            logger.error("仮想トレードが無効です")
            return None
        if side not in {"BUY", "SELL"}:
            logger.error("sideが無効です: %s", side)
            return None
        if order_type not in {"MARKET_SIM", "LIMIT_SIM"}:
            logger.error("order_typeが無効です: %s", order_type)
            return None
        if quantity <= 0:
            logger.error("数量が無効です: %s", quantity)
            return None
        if order_type == "LIMIT_SIM" and limit_price is None:
            logger.error("LIMIT_SIMには指値価格が必要です")
            return None

        try:
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
            if not is_expected_duplicate_conflict(error):
                logger.error(
                    "仮想注文で予期しないDB制約違反: symbol=%s side=%s sqlite_code=%s sqlite_name=%s exception_type=%s",
                    code,
                    side,
                    getattr(error, "sqlite_errorcode", None),
                    getattr(error, "sqlite_errorname", None),
                    type(error).__name__,
                )
                raise
            logger.warning(
                "仮想注文拒否（期待される重複注文制約）: %s %s - DB制約競合",
                code,
                side,
            )
            return None
        except sqlite3.OperationalError as error:
            if not is_sqlite_busy_or_locked(error):
                logger.error(
                    "仮想注文で予期しないDB操作エラー: symbol=%s side=%s sqlite_code=%s sqlite_name=%s exception_type=%s",
                    code,
                    side,
                    getattr(error, "sqlite_errorcode", None),
                    getattr(error, "sqlite_errorname", None),
                    type(error).__name__,
                )
                raise
            logger.warning(
                "仮想注文拒否（SQLite lock/busy）: %s %s - DBロックを取得できません",
                code,
                side,
            )
            return None

        logger.info("仮想注文作成: %s %s %s %s株", order_id, code, side, quantity)
        return VirtualOrder(
            id=order_id,
            strategy_name=strategy_name,
            code=code,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            status="PENDING",
            signal_id=signal_id,
            submitted_at=submit_value,
        )

    def cancel_order(self, order_id: int) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE virtual_orders
                SET status = 'CANCELLED', cancelled_at = ?, updated_at = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (now, now, order_id),
            )
            return cursor.rowcount > 0

    def process_fills(self, strategy_name: str, target_date: str) -> list[VirtualFill]:
        fills: list[VirtualFill] = []
        for order in self.get_pending_orders(strategy_name):
            fill = self._try_fill_order(order, target_date)
            if fill:
                fills.append(fill)
        if fills:
            self.save_equity_curve(strategy_name, target_date)
        return fills

    def _load_candidate_bars(self, conn: sqlite3.Connection, order: VirtualOrder, target_date: str) -> list[sqlite3.Row]:
        submitted_date = self._submitted_date(order)
        rows = conn.execute(
            """
            SELECT date, open, high, low, close
            FROM daily_bars
            WHERE code = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (order.code, submitted_date, target_date),
        ).fetchall()
        return rows

    def _try_fill_order(self, order: VirtualOrder, target_date: str) -> Optional[VirtualFill]:
        submitted_date = self._submitted_date(order)
        if target_date < submitted_date:
            return None

        with self._get_connection() as conn:
            if order.side == "SELL":
                ok, reason = self._validate_sell_order(conn, order.strategy_name, order.code, order.quantity)
                if not ok:
                    logger.warning("SELL約定拒否: %s - %s", order.code, reason)
                    return None

            bars = self._load_candidate_bars(conn, order, target_date)
            if not bars:
                return None

            fill_price: Optional[float]
            filled_at: str
            fill_mode: str
            if order.order_type == "MARKET_SIM":
                fill_price, filled_at, fill_mode = self._calc_market_fill(order, bars, target_date)
            else:
                fill_price, filled_at, fill_mode = self._calc_limit_fill(order, bars, target_date)

            if fill_price is None:
                return None

            if order.order_type == "MARKET_SIM":
                if order.side == "BUY":
                    fill_price = fill_price * (1 + self.slippage_bps / 10000)
                else:
                    fill_price = fill_price * (1 - self.slippage_bps / 10000)
            fill_price = round(float(fill_price), 1)

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

            existing_fill = conn.execute(
                "SELECT 1 FROM virtual_fills WHERE order_id = ? LIMIT 1",
                (order.id,),
            ).fetchone()
            if existing_fill:
                return None

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fill = VirtualFill(
                order_id=order.id or 0,
                strategy_name=order.strategy_name,
                code=order.code,
                side=order.side,
                quantity=order.quantity,
                price=fill_price,
                filled_at=filled_at,
                fill_mode=fill_mode,
            )

            conn.execute(
                """
                INSERT INTO virtual_fills
                (order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fill.order_id, fill.strategy_name, fill.code, fill.side, fill.quantity, fill.price, fill.filled_at, fill.fill_mode, now),
            )
            conn.execute(
                """
                UPDATE virtual_orders
                SET status = 'FILLED', filled_at = ?, fill_price = ?, fill_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (filled_at, fill_price, f"{fill_mode}で約定", now, order.id),
            )
            self._update_position_and_cash(conn, order, fill)

        logger.info("仮想約定: %s %s %s株 @%.1f (%s)", order.code, order.side, order.quantity, fill_price, filled_at)
        return fill

    def _calc_market_fill(
        self,
        order: VirtualOrder,
        bars: list[sqlite3.Row],
        target_date: str,
    ) -> tuple[Optional[float], str, str]:
        submitted_date = self._submitted_date(order)
        if self.market_fill_mode == "same_day_close":
            for bar in bars:
                if bar["date"] >= submitted_date and bar["date"] <= target_date:
                    return float(bar["close"]), bar["date"], "same_day_close"
        elif self.market_fill_mode == "next_day_open":
            for bar in bars:
                if bar["date"] > submitted_date and bar["date"] <= target_date:
                    return float(bar["open"]), bar["date"], "next_day_open"
        elif self.market_fill_mode == "next_day_vwap_approx":
            for bar in bars:
                if bar["date"] > submitted_date and bar["date"] <= target_date:
                    price = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3
                    return price, bar["date"], "next_day_vwap_approx"
        return None, "", ""

    def _calc_limit_fill(
        self,
        order: VirtualOrder,
        bars: list[sqlite3.Row],
        target_date: str,
    ) -> tuple[Optional[float], str, str]:
        if order.limit_price is None:
            return None, "", ""
        submitted_date = self._submitted_date(order)
        for bar in bars:
            if bar["date"] <= submitted_date or bar["date"] > target_date:
                continue
            if order.side == "BUY" and float(bar["low"]) <= order.limit_price:
                return float(order.limit_price), bar["date"], "limit_low_touch"
            if order.side == "SELL" and float(bar["high"]) >= order.limit_price:
                return float(order.limit_price), bar["date"], "limit_high_touch"
        return None, "", ""

    def _update_position_and_cash(self, conn: sqlite3.Connection, order: VirtualOrder, fill: VirtualFill) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pos = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (order.strategy_name, order.code),
        ).fetchone()

        gross = fill.price * fill.quantity
        if order.side == "BUY":
            if pos:
                new_quantity = int(pos["quantity"]) + fill.quantity
                new_avg_cost = (float(pos["avg_cost"]) * int(pos["quantity"]) + gross) / new_quantity
                conn.execute(
                    """
                    UPDATE virtual_positions
                    SET quantity = ?, avg_cost = ?, market_price = ?, market_value = ?,
                        unrealized_pl = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_quantity, new_avg_cost, fill.price, fill.price * new_quantity, (fill.price - new_avg_cost) * new_quantity, now, pos["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO virtual_positions
                    (strategy_name, code, quantity, avg_cost, market_price, market_value,
                     unrealized_pl, realized_pl, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (order.strategy_name, order.code, fill.quantity, fill.price, fill.price, gross, now),
                )
            self._apply_cash_delta(conn, order.strategy_name, fill.filled_at, -(gross + self.commission))

        elif order.side == "SELL" and pos:
            current_qty = int(pos["quantity"])
            sell_qty = min(fill.quantity, current_qty)
            new_quantity = current_qty - sell_qty
            realized_pl = (fill.price - float(pos["avg_cost"])) * sell_qty - self.commission
            market_value = fill.price * new_quantity
            unrealized_pl = (fill.price - float(pos["avg_cost"])) * new_quantity if new_quantity > 0 else 0
            conn.execute(
                """
                UPDATE virtual_positions
                SET quantity = ?, market_price = ?, market_value = ?, unrealized_pl = ?,
                    realized_pl = COALESCE(realized_pl, 0) + ?, updated_at = ?
                WHERE id = ?
                """,
                (new_quantity, fill.price, market_value, unrealized_pl, realized_pl, now, pos["id"]),
            )
            self._apply_cash_delta(conn, order.strategy_name, fill.filled_at, gross - self.commission)

    def get_strategy_performance(self, strategy_name: str = "default") -> dict:
        cash = self.get_cash(strategy_name)
        positions = self.get_positions(strategy_name)
        position_value = sum((p.market_price or p.avg_cost) * p.quantity for p in positions)
        unrealized_pl = sum(((p.market_price or p.avg_cost) - p.avg_cost) * p.quantity for p in positions)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT SUM(COALESCE(realized_pl, 0)) AS realized_pl FROM virtual_positions WHERE strategy_name = ?",
                (strategy_name,),
            ).fetchone()
            realized_pl = float(row["realized_pl"] or 0) if row else 0.0
        total_equity = cash + position_value
        return {
            "strategy_name": strategy_name,
            "cash": cash,
            "position_value": position_value,
            "total_equity": total_equity,
            "realized_pl": realized_pl,
            "unrealized_pl": unrealized_pl,
            "total_pl": realized_pl + unrealized_pl,
            "position_count": len(positions),
            "initial_cash": self.initial_cash,
            "return_pct": (total_equity - self.initial_cash) / self.initial_cash * 100 if self.initial_cash else 0,
        }

    def get_fills(self, strategy_name: str = "default", limit: int = 100) -> list[VirtualFill]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM virtual_fills
                WHERE strategy_name = ?
                ORDER BY filled_at DESC, id DESC
                LIMIT ?
                """,
                (strategy_name, limit),
            ).fetchall()
        return [VirtualFill(id=r["id"], order_id=r["order_id"], strategy_name=r["strategy_name"], code=r["code"], side=r["side"], quantity=r["quantity"], price=r["price"], filled_at=r["filled_at"], fill_mode=r["fill_mode"]) for r in rows]

    def get_equity_curve(self, strategy_name: str = "default", limit: int = 200) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM virtual_equity_curve
                WHERE strategy_name = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (strategy_name, limit),
            ).fetchall()
        return [{"date": r["date"], "cash": r["cash"], "position_value": r["position_value"], "total_equity": r["total_equity"], "daily_return": r["daily_return"], "benchmark_code": r["benchmark_code"], "benchmark_return": r["benchmark_return"], "excess_return": r["excess_return"]} for r in rows]

    def generate_exits(
        self,
        strategy_name: str = "default",
        target_date: Optional[str] = None,
        stop_loss_pct: float = -5.0,
    ) -> list[VirtualOrder]:
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        exit_orders: list[VirtualOrder] = []
        for pos in self.get_positions(strategy_name):
            with self._get_connection() as conn:
                current_price = self._latest_close(conn, pos.code, target_date)
                if current_price is None:
                    continue
                already_pending = conn.execute(
                    """
                    SELECT 1 FROM virtual_orders
                    WHERE strategy_name = ? AND code = ? AND side = 'SELL' AND status = 'PENDING'
                    LIMIT 1
                    """,
                    (strategy_name, pos.code),
                ).fetchone()
                if already_pending:
                    continue

                should_exit = current_price <= pos.avg_cost * (1 + stop_loss_pct / 100)
                if not should_exit:
                    bars = conn.execute(
                        """
                        SELECT close FROM daily_bars
                        WHERE code = ? AND date <= ?
                        ORDER BY date DESC LIMIT 25
                        """,
                        (pos.code, target_date),
                    ).fetchall()
                    if len(bars) >= 25:
                        ma25 = sum(float(b["close"]) for b in bars) / 25
                        should_exit = current_price < ma25

            if should_exit:
                reason = "stop_loss" if current_price <= pos.avg_cost * (1 + stop_loss_pct / 100) else "ma25_cross"
                order = self.place_order(strategy_name, pos.code, "SELL", pos.quantity, "MARKET_SIM", submitted_at=target_date, exit_reason=reason)
                if order:
                    exit_orders.append(order)
        return exit_orders

    def save_equity_curve(
        self,
        strategy_name: str = "default",
        target_date: Optional[str] = None,
        benchmark_code: Optional[str] = None,
    ) -> dict:
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        if benchmark_code is None:
            benchmark_code = self.default_benchmark

        with self._get_connection() as conn:
            cash = self._get_cash_with_conn(conn, strategy_name)
            position_value = self._position_value_with_conn(conn, strategy_name)
            total_equity = cash + position_value
            prev = conn.execute(
                """
                SELECT total_equity FROM virtual_equity_curve
                WHERE strategy_name = ? AND date < ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name, target_date),
            ).fetchone()
            prev_equity = float(prev["total_equity"]) if prev and prev["total_equity"] else total_equity
            daily_return = (total_equity - prev_equity) / prev_equity * 100 if prev_equity else 0

            bench = conn.execute(
                """
                SELECT daily_return FROM benchmark_prices
                WHERE benchmark_code = ? AND date = ?
                """,
                (benchmark_code, target_date),
            ).fetchone()
            benchmark_return = bench["daily_return"] if bench else None
            excess_return = daily_return - benchmark_return if benchmark_return is not None else None
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO virtual_equity_curve
                (strategy_name, date, cash, position_value, total_equity, daily_return,
                 benchmark_code, benchmark_return, excess_return, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_name, date) DO UPDATE SET
                    cash = excluded.cash,
                    position_value = excluded.position_value,
                    total_equity = excluded.total_equity,
                    daily_return = excluded.daily_return,
                    benchmark_code = excluded.benchmark_code,
                    benchmark_return = excluded.benchmark_return,
                    excess_return = excluded.excess_return,
                    created_at = excluded.created_at
                """,
                (strategy_name, target_date, cash, position_value, total_equity, daily_return, benchmark_code, benchmark_return, excess_return, now),
            )

        return {"date": target_date, "cash": cash, "position_value": position_value, "total_equity": total_equity, "daily_return": daily_return, "benchmark_return": benchmark_return, "excess_return": excess_return}

    def generate_report(self, strategy_name: str = "default") -> dict:
        perf = self.get_strategy_performance(strategy_name)
        equity = list(reversed(self.get_equity_curve(strategy_name, limit=500)))
        fills = self.get_fills(strategy_name, limit=500)
        pending = self.get_pending_orders(strategy_name)

        max_dd = 0.0
        peak = None
        for e in equity:
            eq = e["total_equity"] or 0
            if peak is None or eq > peak:
                peak = eq
            if peak and peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak * 100)

        sell_fills = [f for f in fills if f.side == "SELL"]
        return {
            "period_return": perf["return_pct"],
            "excess_return_vs_2559": None,
            "excess_return_vs_1306": None,
            "win_count": None,
            "total_trades": len(fills),
            "avg_win": None,
            "max_drawdown": max_dd,
            "position_count_history": len(equity),
            "cash_history": [e["cash"] for e in equity],
            "fill_count": len(fills),
            "sell_fill_count": len(sell_fills),
            "pending_count": len(pending),
        }

    def update_market_prices(self, strategy_name: str = "default", target_date: Optional[str] = None) -> int:
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        updated = 0
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM virtual_positions
                WHERE strategy_name = ? AND quantity > 0
                """,
                (strategy_name,),
            ).fetchall()
            for pos in rows:
                market_price = self._latest_close(conn, pos["code"], target_date)
                if market_price is None:
                    continue
                market_value = market_price * int(pos["quantity"])
                unrealized_pl = (market_price - float(pos["avg_cost"])) * int(pos["quantity"])
                conn.execute(
                    """
                    UPDATE virtual_positions
                    SET market_price = ?, market_value = ?, unrealized_pl = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (market_price, market_value, unrealized_pl, datetime.now().isoformat(), pos["id"]),
                )
                updated += 1
        if updated:
            self.save_equity_curve(strategy_name, target_date)
        return updated
