"""
アプリ内ペーパートレードモジュール

ファイルパス: src/virtual_trade.py
何をするか: アプリ内で独自の仮想売買を管理する
なぜ存在するか: moomoo APIのSIMULATE環境が利用できないため、独自にペーパートレードを実現するため
関連ファイル: config.py, data_store.py

注意:
    - moomoo APIの注文系APIは使わない
    - TrdEnv.REAL/SIMULATEは使わない
    - これはアプリ内の仮想注文であり、実注文ではない
    - 約定判定は日足データに基づく簡易シミュレーション
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class VirtualOrder:
    """仮想注文"""
    id: Optional[int] = None
    strategy_name: str = ""
    code: str = ""
    side: str = ""  # "BUY" or "SELL"
    quantity: int = 0
    order_type: str = ""  # "MARKET_SIM" or "LIMIT_SIM"
    limit_price: Optional[float] = None
    status: str = "PENDING"  # PENDING, FILLED, CANCELLED
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
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

        # 仮想トレード設定
        vt_config = config.get("virtual_trade", {})
        self.enabled = vt_config.get("enabled", True)
        self.initial_cash = vt_config.get("initial_cash", 100000)
        self.max_position_amount = vt_config.get("max_position_amount", 20000)
        self.max_total_positions = vt_config.get("max_total_positions", 5)
        self.max_position_per_symbol = vt_config.get("max_position_per_symbol", 1)
        self.market_fill_mode = vt_config.get("market_fill_mode", "next_day_open")
        self.slippage_bps = vt_config.get("slippage_bps", 10)
        self.commission = vt_config.get("commission", 0)
        self.default_benchmark = vt_config.get("default_benchmark", "JP.2559")

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_cash(self, strategy_name: str = "default") -> float:
        """
        残高を取得する

        Args:
            strategy_name: 戦術名

        Returns:
            float: 残高
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name = ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name,),
            )
            row = cursor.fetchone()
            if row:
                return row["cash"]
            return self.initial_cash

    def get_positions(self, strategy_name: str = "default") -> list[VirtualPosition]:
        """
        ポジション一覧を取得する

        Args:
            strategy_name: 戦術名

        Returns:
            list[VirtualPosition]: ポジションリスト
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM virtual_positions
                WHERE strategy_name = ? AND quantity > 0
                ORDER BY code
                """,
                (strategy_name,),
            )
            rows = cursor.fetchall()

        return [
            VirtualPosition(
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
            for row in rows
        ]

    def get_pending_orders(self, strategy_name: str = "default") -> list[VirtualOrder]:
        """
        未約定注文一覧を取得する

        Args:
            strategy_name: 戦術名

        Returns:
            list[VirtualOrder]: 未約定注文リスト
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM virtual_orders
                WHERE strategy_name = ? AND status = 'PENDING'
                ORDER BY submitted_at
                """,
                (strategy_name,),
            )
            rows = cursor.fetchall()

        return [
            VirtualOrder(
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
            for row in rows
        ]

    def place_order(
        self,
        strategy_name: str,
        code: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET_SIM",
        limit_price: Optional[float] = None,
        signal_id: Optional[int] = None,
    ) -> Optional[VirtualOrder]:
        """
        仮想注文を作成する

        注意: これはアプリ内の仮想注文です。moomooには注文を送信しません。

        Args:
            strategy_name: 戦術名
            code: 銘柄コード
            side: "BUY" or "SELL"
            quantity: 数量
            order_type: "MARKET_SIM" or "LIMIT_SIM"
            limit_price: 指値価格（LIMIT_SIMの場合）
            signal_id: シグナルID

        Returns:
            VirtualOrder: 作成された注文
        """
        if not self.enabled:
            logger.error("仮想トレードが無効です")
            return None

        # バリデーション
        if quantity <= 0:
            logger.error(f"数量が無効です: {quantity}")
            return None

        if order_type == "LIMIT_SIM" and limit_price is None:
            logger.error("LIMIT_SIMには指値価格が必要です")
            return None

        # ポジション上限チェック（買いの場合）
        if side == "BUY":
            positions = self.get_positions(strategy_name)
            if len(positions) >= self.max_total_positions:
                logger.warning(f"ポジション上限に達しています: {self.max_total_positions}")
                return None

            # 同一銘柄の保有チェック
            for pos in positions:
                if pos.code == code and pos.quantity >= self.max_position_per_symbol:
                    logger.warning(f"同一銘柄の保有上限に達しています: {code}")
                    return None

        # 注文作成
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        order = VirtualOrder(
            strategy_name=strategy_name,
            code=code,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            status="PENDING",
            signal_id=signal_id,
            submitted_at=now,
        )

        # DBに保存
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO virtual_orders
                (strategy_name, code, side, quantity, order_type, limit_price,
                 status, signal_id, submitted_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.strategy_name,
                    order.code,
                    order.side,
                    order.quantity,
                    order.order_type,
                    order.limit_price,
                    order.status,
                    order.signal_id,
                    order.submitted_at,
                    now,
                    now,
                ),
            )
            order.id = cursor.lastrowid

        logger.info(f"仮想注文作成: {order.id} {code} {side} {quantity}株 @{limit_price or 'MARKET'}")
        return order

    def cancel_order(self, order_id: int) -> bool:
        """
        仮想注文をキャンセルする

        Args:
            order_id: 注文ID

        Returns:
            bool: 成功ならTrue
        """
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

    def process_fills(
        self,
        strategy_name: str,
        target_date: str,
    ) -> list[VirtualFill]:
        """
        未約定注文の約定判定を行う

        Args:
            strategy_name: 戦術名
            target_date: 約定判定日（YYYY-MM-DD）

        Returns:
            list[VirtualFill]: 約定リスト
        """
        fills = []

        # 未約定注文を取得
        pending_orders = self.get_pending_orders(strategy_name)

        for order in pending_orders:
            fill = self._try_fill_order(order, target_date)
            if fill:
                fills.append(fill)

        return fills

    def _try_fill_order(
        self,
        order: VirtualOrder,
        target_date: str,
    ) -> Optional[VirtualFill]:
        """
        個別注文の約定判定を行う

        Args:
            order: 仮想注文
            target_date: 約定判定日

        Returns:
            VirtualFill: 約定情報。約定しなかった場合はNone
        """
        # 日足データを取得
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT date, open, high, low, close FROM daily_bars
                WHERE code = ? AND date >= ?
                ORDER BY date ASC
                LIMIT 10
                """,
                (order.code, target_date),
            )
            bars = cursor.fetchall()

        if not bars:
            return None

        fill_price = None
        fill_mode = ""
        filled_at = ""

        if order.order_type == "MARKET_SIM":
            # MARKET_SIM: 設定されたモードで約定
            fill_price, filled_at = self._calc_market_fill(order, bars)

        elif order.order_type == "LIMIT_SIM":
            # LIMIT_SIM: 指値に到達したら約定
            fill_price, filled_at = self._calc_limit_fill(order, bars)

        if fill_price is None:
            return None

        # スリッページ適用
        if order.side == "BUY":
            fill_price = fill_price * (1 + self.slippage_bps / 10000)
        else:
            fill_price = fill_price * (1 - self.slippage_bps / 10000)

        # 約定保存
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        fill = VirtualFill(
            order_id=order.id,
            strategy_name=order.strategy_name,
            code=order.code,
            side=order.side,
            quantity=order.quantity,
            price=round(fill_price, 1),
            filled_at=filled_at,
            fill_mode=fill_mode,
        )

        # DBに保存
        with self._get_connection() as conn:
            # 約定記録
            conn.execute(
                """
                INSERT INTO virtual_fills
                (order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode, created_at)
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

            # 注文ステータス更新
            conn.execute(
                """
                UPDATE virtual_orders
                SET status = 'FILLED', filled_at = ?, fill_price = ?,
                    fill_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (filled_at, fill_price, f"{fill_mode}で約定", now, order.id),
            )

            # ポジション更新
            self._update_position(conn, order, fill)

        logger.info(
            f"仮想約定: {order.code} {order.side} {order.quantity}株 @{fill_price:.0f} ({filled_at})"
        )
        return fill

    def _calc_market_fill(
        self,
        order: VirtualOrder,
        bars: list,
    ) -> tuple[Optional[float], str]:
        """
        MARKET_SIMの約定価格を計算する

        Args:
            order: 仮想注文
            bars: 日足データ

        Returns:
            tuple: (約定価格, 約定日時)
        """
        if not bars:
            return None, ""

        if self.market_fill_mode == "same_day_close":
            # 当日終値
            fill_price = bars[0]["close"]
            filled_at = bars[0]["date"]

        elif self.market_fill_mode == "next_day_open":
            # 翌営業日始値
            if len(bars) >= 2:
                fill_price = bars[1]["open"]
                filled_at = bars[1]["date"]
            else:
                fill_price = bars[0]["open"]
                filled_at = bars[0]["date"]

        elif self.market_fill_mode == "next_day_vwap_approx":
            # 翌営業日の概算VWAP（(high + low + close) / 3）
            if len(bars) >= 2:
                bar = bars[1]
                fill_price = (bar["high"] + bar["low"] + bar["close"]) / 3
                filled_at = bar["date"]
            else:
                bar = bars[0]
                fill_price = (bar["high"] + bar["low"] + bar["close"]) / 3
                filled_at = bar["date"]

        else:
            return None, ""

        return fill_price, filled_at

    def _calc_limit_fill(
        self,
        order: VirtualOrder,
        bars: list,
    ) -> tuple[Optional[float], str]:
        """
        LIMIT_SIMの約定価格を計算する

        Args:
            order: 仮想注文
            bars: 日足データ

        Returns:
            tuple: (約定価格, 約定日時)
        """
        if not bars or order.limit_price is None:
            return None, ""

        for bar in bars:
            if order.side == "BUY":
                # 買い: low <= limit_price
                if bar["low"] <= order.limit_price:
                    return order.limit_price, bar["date"]
            else:
                # 売り: high >= limit_price
                if bar["high"] >= order.limit_price:
                    return order.limit_price, bar["date"]

        return None, ""

    def _update_position(
        self,
        conn: sqlite3.Connection,
        order: VirtualOrder,
        fill: VirtualFill,
    ) -> None:
        """
        ポジションを更新する

        Args:
            conn: データベース接続
            order: 仮想注文
            fill: 仮想約定
        """
        # 現在のポジションを取得
        cursor = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (order.strategy_name, order.code),
        )
        position = cursor.fetchone()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if order.side == "BUY":
            if position:
                # ポジション増加
                new_quantity = position["quantity"] + fill.quantity
                new_avg_cost = (
                    (position["avg_cost"] * position["quantity"] + fill.price * fill.quantity)
                    / new_quantity
                )
                conn.execute(
                    """
                    UPDATE virtual_positions
                    SET quantity = ?, avg_cost = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_quantity, new_avg_cost, now, position["id"]),
                )
            else:
                # 新規ポジション
                conn.execute(
                    """
                    INSERT INTO virtual_positions
                    (strategy_name, code, quantity, avg_cost, realized_pl, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (order.strategy_name, order.code, fill.quantity, fill.price, now),
                )

        elif order.side == "SELL":
            if position:
                # ポジション減少
                new_quantity = position["quantity"] - fill.quantity
                realized_pl = (fill.price - position["avg_cost"]) * fill.quantity

                if new_quantity <= 0:
                    # ポジションクローズ
                    conn.execute(
                        """
                        UPDATE virtual_positions
                        SET quantity = 0, realized_pl = realized_pl + ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (realized_pl, now, position["id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE virtual_positions
                        SET quantity = ?, realized_pl = realized_pl + ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_quantity, realized_pl, now, position["id"]),
                    )

    def get_strategy_performance(
        self,
        strategy_name: str = "default",
    ) -> dict:
        """
        戦術別成績を取得する

        Args:
            strategy_name: 戦術名

        Returns:
            dict: 成績データ
        """
        positions = self.get_positions(strategy_name)
        cash = self.get_cash(strategy_name)

        # ポジション評価額
        position_value = sum(
            (p.market_price or p.avg_cost) * p.quantity
            for p in positions
        )

        # 総資産
        total_equity = cash + position_value

        # 実現損益
        realized_pl = sum(p.realized_pl for p in positions)

        # 未実現損益
        unrealized_pl = sum(
            ((p.market_price or p.avg_cost) - p.avg_cost) * p.quantity
            for p in positions
        )

        return {
            "strategy_name": strategy_name,
            "cash": cash,
            "position_value": position_value,
            "total_equity": total_equity,
            "realized_pl": realized_pl,
            "unrealized_pl": unrealized_pl,
            "total_pl": realized_pl + unrealized_pl,
            "position_count": len([p for p in positions if p.quantity > 0]),
            "initial_cash": self.initial_cash,
            "return_pct": (total_equity - self.initial_cash) / self.initial_cash * 100,
        }
