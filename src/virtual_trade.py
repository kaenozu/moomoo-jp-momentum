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
from .migrations import (
    migrate_virtual_orders_pending_index,
    migrate_virtual_orders_reserved_amount,
)

logger = logging.getLogger(__name__)


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
    reserved_amount: Optional[float] = None


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
class _PositionReplayState:
    quantity: int = 0
    avg_cost: float = 0.0
    realized_pl: float = 0.0
    last_price: float = 0.0


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
        self.reserve_buffer_pct = float(vt_config.get("reserve_buffer_pct", 2.0))
        self.default_benchmark = vt_config.get("default_benchmark", "JP.1306")

        universe_config = config.get("universe", {})
        self.min_trade_price = float(universe_config.get("min_trade_price", 500))
        self.max_trade_price = float(universe_config.get("max_trade_price", 20000))

        with self._get_connection() as conn:
            migrate_virtual_orders_reserved_amount(conn)
            migrate_virtual_orders_pending_index(conn)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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


    def _snapshot_position_cache_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        rows = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ?
            ORDER BY code
            """,
            (strategy_name,),
        ).fetchall()
        return {str(row["code"]): self._row_to_position(row) for row in rows}

    def _snapshot_positions_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        return {
            code: position
            for code, position in self._snapshot_position_cache_with_conn(
                conn,
                strategy_name,
            ).items()
            if position.quantity > 0
        }

    def _position_cache_matches_replay(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        replayed: dict[str, VirtualPosition],
    ) -> bool:
        cached = self._snapshot_position_cache_with_conn(conn, strategy_name)
        if set(cached) != set(replayed):
            return False
        for code, cached_position in cached.items():
            replayed_position = replayed[code]
            if cached_position.quantity != replayed_position.quantity:
                return False
            if abs(cached_position.avg_cost - replayed_position.avg_cost) > 1e-6:
                return False
            if abs(cached_position.realized_pl - replayed_position.realized_pl) > 1e-6:
                return False
        return True

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
        exclude_order_id: int | None = None,
    ) -> tuple[dict[str, VirtualPosition], bool]:
        """Replay fills in chronological order and return positions plus completeness.

        ``complete`` is false when a SELL exceeds the quantity reconstructed from
        earlier fills. That indicates a legacy/imported opening position which
        cannot be dated from fill history alone.
        """
        if as_of_date:
            rows = conn.execute(
                """
                SELECT id, order_id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND COALESCE(substr(filled_at, 1, 10), '') <= ?
                  AND (? IS NULL OR order_id <> ?)
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (
                    strategy_name,
                    as_of_date,
                    exclude_order_id,
                    exclude_order_id,
                ),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, order_id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND (? IS NULL OR order_id <> ?)
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name, exclude_order_id, exclude_order_id),
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
            current_replayed, current_complete = self._replay_positions_with_conn(
                conn,
                strategy_name,
            )
            cache_complete = (
                current_complete
                and self._position_cache_matches_replay(
                    conn,
                    strategy_name,
                    current_replayed,
                )
            )
            if cache_complete:
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
                "仮想ポジション履歴と現在キャッシュの整合性を確認できないため"
                "現在スナップショットへフォールバックします: strategy=%s, date=%s",
                strategy_name,
                reference_date,
            )
        return self._snapshot_positions_with_conn(conn, strategy_name)

    def _rebuild_position_cache_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        exclude_order_id: int | None = None,
    ) -> bool:
        """Rebuild the cache only when existing state is fully fill-derived."""
        if not self._has_fill_history_with_conn(conn, strategy_name):
            return False
        previous_replayed, previous_complete = self._replay_positions_with_conn(
            conn,
            strategy_name,
            exclude_order_id=exclude_order_id,
        )
        if not previous_complete or not self._position_cache_matches_replay(
            conn,
            strategy_name,
            previous_replayed,
        ):
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

    def _get_pending_buy_reserved(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
        exclude_order_id: int | None = None,
    ) -> float:
        """Return reservations for pending BUY orders.

        ``exclude_order_id`` is used at fill time so the order being filled
        does not reserve cash against itself.
        """
        rows = conn.execute(
            """
            SELECT id, code, quantity, order_type, limit_price, reserved_amount
            FROM virtual_orders
            WHERE strategy_name = ?
              AND side = 'BUY'
              AND status = 'PENDING'
              AND (? IS NULL OR COALESCE(substr(submitted_at, 1, 10), '') <= ?)
              AND (? IS NULL OR id <> ?)
            """,
            (
                strategy_name,
                as_of_date,
                as_of_date,
                exclude_order_id,
                exclude_order_id,
            ),
        ).fetchall()
        buffer = 1.0 + self.reserve_buffer_pct / 100.0
        total = 0.0
        unresolved: list[str] = []
        for row in rows:
            stored = row["reserved_amount"]
            if stored is not None and float(stored) > 0:
                total += float(stored)
                continue
            if row["order_type"] == "LIMIT_SIM" and row["limit_price"] is not None:
                total += (
                    float(row["limit_price"]) * int(row["quantity"]) * buffer
                    + self.commission
                )
                continue
            price = self._latest_close(conn, row["code"], as_of_date)
            if price is None:
                unresolved.append(row["code"])
                continue
            total += price * int(row["quantity"]) * buffer + self.commission
        if unresolved:
            logger.warning(
                "仮想予約: 参照価格が取得できないため予約をスキップ: %s",
                unresolved,
            )
        return total

    def get_available_cash(
        self,
        strategy_name: str = "default",
        as_of_date: str | None = None,
        conn: sqlite3.Connection | None = None,
        exclude_order_id: int | None = None,
    ) -> float:
        """Return cash after pending BUY reservations are deducted."""
        if conn is None:
            with self._get_connection() as owned_conn:
                return self.get_available_cash(
                    strategy_name,
                    as_of_date,
                    owned_conn,
                    exclude_order_id,
                )
        cash = self._get_cash_with_conn(conn, strategy_name, as_of_date)
        reserved = self._get_pending_buy_reserved(
            conn,
            strategy_name,
            as_of_date,
            exclude_order_id,
        )
        available = cash - reserved
        logger.debug(
            "available_cash: cash=%.2f, reserved=%.2f, available=%.2f",
            cash,
            reserved,
            available,
        )
        return max(0.0, available)

    def _validate_buy_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float],
        submitted_at: str | None = None,
        exclude_order_id: int | None = None,
    ) -> tuple[bool, str]:
        ok, reason = self._symbol_universe_status(conn, code)
        if not ok:
            return False, reason

        reference_date = submitted_at[:10] if submitted_at else None
        ref_price = (
            limit_price
            if order_type == "LIMIT_SIM"
            else self._latest_close(conn, code, reference_date)
        )
        if ref_price is None or ref_price <= 0:
            return False, "参照価格を取得できません"
        if ref_price < self.min_trade_price:
            return False, f"価格が下限{self.min_trade_price:,.0f}円未満です"
        if ref_price > self.max_trade_price:
            return False, f"価格が上限{self.max_trade_price:,.0f}円を超えています"
        if ref_price * quantity > self.max_position_amount:
            return False, f"注文金額が1銘柄上限{self.max_position_amount:,.0f}円を超えています"

        buffer = 1.0 + self.reserve_buffer_pct / 100.0
        required_reservation = ref_price * quantity * buffer + self.commission
        available_cash = self.get_available_cash(
            strategy_name,
            reference_date,
            conn,
            exclude_order_id,
        )
        if required_reservation > available_cash:
            return False, "仮想cashが不足しています（予約バッファ・pending BUYを控除済み）"

        positions = self._positions_for_reference_with_conn(
            conn,
            strategy_name,
            reference_date,
        )
        position_codes = set(positions)
        current_quantity = positions.get(code, VirtualPosition()).quantity
        if current_quantity >= self.max_position_per_symbol:
            return False, "同一銘柄の保有上限に達しています"

        pending_rows = conn.execute(
            """
            SELECT DISTINCT code
            FROM virtual_orders
            WHERE strategy_name = ?
              AND side = 'BUY'
              AND status = 'PENDING'
              AND (? IS NULL OR COALESCE(substr(submitted_at, 1, 10), '') <= ?)
              AND (? IS NULL OR id <> ?)
            """,
            (
                strategy_name,
                reference_date,
                reference_date,
                exclude_order_id,
                exclude_order_id,
            ),
        ).fetchall()
        pending_codes = {row["code"] for row in pending_rows}
        if code in pending_codes:
            return False, "同一銘柄の未約定BUY注文が既に存在します"

        prospective_codes = position_codes | pending_codes | {code}
        if len(prospective_codes) > self.max_total_positions:
            return False, f"保有銘柄数上限{self.max_total_positions}に達しています"
        return True, ""

    def _validate_sell_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
        reference_date: str | None = None,
        exclude_order_id: int | None = None,
    ) -> tuple[bool, str]:
        positions = self._positions_for_reference_with_conn(
            conn,
            strategy_name,
            reference_date,
        )
        position = positions.get(code)
        if position is None or position.quantity < quantity:
            return False, "売却可能な仮想ポジションが不足しています"

        pending = conn.execute(
            """
            SELECT 1 FROM virtual_orders
            WHERE strategy_name = ?
              AND code = ?
              AND side = 'SELL'
              AND status = 'PENDING'
              AND (? IS NULL OR COALESCE(substr(submitted_at, 1, 10), '') <= ?)
              AND (? IS NULL OR id <> ?)
            LIMIT 1
            """,
            (
                strategy_name,
                code,
                reference_date,
                reference_date,
                exclude_order_id,
                exclude_order_id,
            ),
        ).fetchone()
        if pending:
            return False, "同一銘柄の未約定SELL注文が既に存在します"
        return True, ""

    def get_cash(
        self,
        strategy_name: str = "default",
        as_of_date: str | None = None,
    ) -> float:
        with self._get_connection() as conn:
            return self._get_cash_with_conn(conn, strategy_name, as_of_date)

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

    def _position_value_with_conn(
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

    def _set_cash(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        target_date: str,
        new_cash: float,
    ) -> None:
        position_value = self._position_value_with_conn(
            conn, strategy_name, target_date
        )
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

    def _apply_cash_delta(self, conn: sqlite3.Connection, strategy_name: str, target_date: str, delta: float) -> None:
        current_cash = self._get_cash_with_conn(conn, strategy_name, target_date)
        self._set_cash(conn, strategy_name, target_date, current_cash + delta)

    def get_positions(
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
            reserved_amount=row["reserved_amount"],
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
        """Create a virtual order without sending anything to moomoo."""
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

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        submit_value = submitted_at or now
        if len(submit_value) == 10:
            submit_value = f"{submit_value} 15:30:00"
        reference_date = submit_value[:10]

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if side == "BUY":
                ok, reason = self._validate_buy_order(
                    conn,
                    strategy_name,
                    code,
                    quantity,
                    order_type,
                    limit_price,
                    reference_date,
                )
            else:
                ok, reason = self._validate_sell_order(
                    conn,
                    strategy_name,
                    code,
                    quantity,
                    reference_date=reference_date,
                )
            if not ok:
                logger.warning("仮想注文拒否: %s %s - %s", code, side, reason)
                return None

            reserved_amount: float | None = None
            if side == "BUY":
                reference_price = (
                    float(limit_price)
                    if order_type == "LIMIT_SIM" and limit_price is not None
                    else self._latest_close(conn, code, reference_date)
                )
                if reference_price is None or reference_price <= 0:
                    logger.warning("仮想注文拒否: %s BUY - 参照価格を取得できません", code)
                    return None
                buffer = 1.0 + self.reserve_buffer_pct / 100.0
                reserved_amount = reference_price * quantity * buffer + self.commission

            cursor = conn.execute(
                """
                INSERT INTO virtual_orders
                (strategy_name, code, side, quantity, order_type, limit_price,
                 status, signal_id, exit_reason, submitted_at, reserved_amount,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
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
                    reserved_amount,
                    now,
                    now,
                ),
            )
            order_id = cursor.lastrowid

        logger.info(
            "仮想注文作成: %s %s %s %s株 (予約額: %s)",
            order_id,
            code,
            side,
            quantity,
            reserved_amount,
        )
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
            reserved_amount=reserved_amount,
        )

    def cancel_order(self, order_id: int) -> bool:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE virtual_orders
                SET status = 'CANCELLED', cancelled_at = ?, reserved_amount = NULL, updated_at = ?
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

    def _try_fill_order(
        self,
        order: VirtualOrder,
        target_date: str,
    ) -> Optional[VirtualFill]:
        submitted_date = self._submitted_date(order)
        if target_date < submitted_date:
            return None

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if order.side == "SELL":
                ok, reason = self._validate_sell_order(
                    conn,
                    order.strategy_name,
                    order.code,
                    order.quantity,
                    reference_date=target_date,
                    exclude_order_id=order.id,
                )
                if not ok:
                    logger.warning("SELL約定拒否: %s - %s", order.code, reason)
                    return None

            existing_fill = conn.execute(
                "SELECT 1 FROM virtual_fills WHERE order_id = ? LIMIT 1",
                (order.id,),
            ).fetchone()
            if existing_fill:
                return None

            bars = self._load_candidate_bars(conn, order, target_date)
            if not bars:
                return None

            if order.order_type == "MARKET_SIM":
                fill_price, filled_at, fill_mode = self._calc_market_fill(
                    order,
                    bars,
                    target_date,
                )
            else:
                fill_price, filled_at, fill_mode = self._calc_limit_fill(
                    order,
                    bars,
                    target_date,
                )
            if fill_price is None:
                return None

            if order.order_type == "MARKET_SIM":
                if order.side == "BUY":
                    fill_price *= 1 + self.slippage_bps / 10000
                else:
                    fill_price *= 1 - self.slippage_bps / 10000
            fill_price = round(float(fill_price), 1)

            if order.side == "BUY":
                fill_cost = fill_price * order.quantity + self.commission
                available_cash = self.get_available_cash(
                    order.strategy_name,
                    filled_at,
                    conn,
                    exclude_order_id=order.id,
                )
                if fill_cost > available_cash:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        """
                        UPDATE virtual_orders
                        SET status = 'CANCELLED', cancelled_at = ?,
                            reserved_amount = NULL, fill_reason = ?, updated_at = ?
                        WHERE id = ? AND status = 'PENDING'
                        """,
                        (now, "insufficient_cash_at_fill", now, order.id),
                    )
                    logger.warning(
                        "BUY約定拒否(資金不足): %s 必要額%.2f 利用可能額%.2f",
                        order.code,
                        fill_cost,
                        available_cash,
                    )
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
                (order_id, strategy_name, code, side, quantity, price,
                 filled_at, fill_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.order_id,
                    fill.strategy_name,
                    fill.code,
                    fill.side,
                    fill.quantity,
                    fill.price,
                    fill.filled_at,
                    fill.fill_mode,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE virtual_orders
                SET status = 'FILLED', filled_at = ?, fill_price = ?,
                    reserved_amount = NULL, fill_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (filled_at, fill_price, f"{fill_mode}で約定", now, order.id),
            )
            self._update_position_and_cash(conn, order, fill)

        logger.info(
            "仮想約定: %s %s %s株 @%.1f (%s)",
            order.code,
            order.side,
            order.quantity,
            fill_price,
            filled_at,
        )
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

    def _update_position_and_cash(
        self,
        conn: sqlite3.Connection,
        order: VirtualOrder,
        fill: VirtualFill,
    ) -> None:
        gross = fill.price * fill.quantity
        if self._rebuild_position_cache_from_fills(
            conn,
            order.strategy_name,
            exclude_order_id=order.id,
        ):
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
        for pos in self.get_positions(strategy_name, as_of_date=target_date):
            with self._get_connection() as conn:
                current_price = self._latest_close(conn, pos.code, target_date)
                if current_price is None:
                    continue
                already_pending = conn.execute(
                    """
                    SELECT 1 FROM virtual_orders
                    WHERE strategy_name = ? AND code = ? AND side = 'SELL' AND status = 'PENDING'
                      AND COALESCE(substr(submitted_at, 1, 10), '') <= ?
                    LIMIT 1
                    """,
                    (strategy_name, pos.code, target_date),
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
            cash = self._get_cash_with_conn(conn, strategy_name, target_date)
            position_value = self._position_value_with_conn(
                conn, strategy_name, target_date
            )
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
