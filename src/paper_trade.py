"""
ペーパートレードモジュール（experimental）

ファイルパス: src/paper_trade.py
何をするか: moomoo OpenAPIのSIMULATE環境でペーパートレードを行う
なぜ存在するか: 実資金を使わずに取引ロジックを検証するため
関連ファイル: config.py, data_store.py

注意:
    - TrdEnv.SIMULATE のみ使用
    - TrdEnv.REAL は一切使用しない
    - 実注文機能は実装しない
    - これはペーパートレードであり、実資金の取引ではない

制限:
    - moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できない
    - アプリ内デモ取引とAPI SIMULATEは別物
    - JP市場ではこのモジュールは停止する
    - US市場向けにexperimentalとして残す
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from futu import (
    OpenSecTradeContext,
    RET_OK,
    SecurityFirm,
    TrdEnv,
    TrdMarket,
    TrdSide,
    OrderType,
)

from .config import Config

logger = logging.getLogger(__name__)

# moomoo JP / FUTUJP では日本株のAPI SIMULATE注文が利用できない
UNSUPPORTED_MARKETS = ["JP"]


@dataclass
class PaperOrder:
    """ペーパートレード注文"""
    order_id: str
    code: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: float
    order_type: str  # "LIMIT" or "MARKET"
    status: str
    trd_env: str = "SIMULATE"
    submitted_at: str = ""
    updated_at: str = ""
    raw_response: str = ""


class PaperTradeManager:
    """ペーパートレード管理クラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.config = config
        self.db_path = Path(config.database_path)

        # ペーパートレード設定
        pt_config = config.get("paper_trade", {})
        self.enabled = pt_config.get("enabled", False)
        self.allow_market_order = pt_config.get("allow_market_order", False)
        self.max_order_quantity = pt_config.get("max_order_quantity", 10)
        self.max_order_amount = pt_config.get("max_order_amount", 50000)

        # ポート
        self.host = config.opend_host
        self.port = config.opend_port

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_trade_context(
        self,
        market: str = "JP",
    ) -> Optional[OpenSecTradeContext]:
        """
        SIMULATE環境の取引コンテキストを取得する

        Args:
            market: 市場（"JP" or "US"）

        Returns:
            OpenSecTradeContext: 取引コンテキスト。失敗時はNone
        """
        # JP市場はサポートされていない
        if market in UNSUPPORTED_MARKETS:
            logger.error(
                f"moomoo JP / FUTUJP では、OpenAPI経由の{market}市場SIMULATE注文が利用できないため、この機能は無効です。"
            )
            return None

        try:
            # US市場はFUTUINCを使用
            security_firm = SecurityFirm.FUTUINC if market == "US" else SecurityFirm.FUTUSG

            ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.JP if market == "JP" else TrdMarket.US,
                host=self.host,
                port=self.port,
                security_firm=security_firm,
            )
            return ctx
        except Exception as e:
            logger.error(f"取引コンテキスト作成失敗: {e}")
            return None

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "LIMIT",
        market: str = "JP",
    ) -> Optional[PaperOrder]:
        """
        ペーパートレード注文を出す

        注意: これはSIMULATE環境でのみ動作します。
        実資金を使う注文ではありません。

        Args:
            code: 銘柄コード
            side: "BUY" or "SELL"
            quantity: 数量
            price: 価格
            order_type: "LIMIT" or "MARKET"
            market: 市場（"JP" or "US"）

        Returns:
            PaperOrder: 注文結果
        """
        if not self.enabled:
            logger.error("ペーパートレードが無効です")
            return None

        # JP市場はサポートされていない
        if market in UNSUPPORTED_MARKETS:
            logger.error(
                "moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できないため、この機能は無効です。\n"
                "アプリ内デモ取引とAPI SIMULATEは別物です。\n"
                "取引はmoomooアプリで手動実行してください。"
            )
            return None

        # バリデーション
        if order_type == "MARKET" and not self.allow_market_order:
            logger.error("成行注文は無効です（allow_market_order=false）")
            return None

        if quantity > self.max_order_quantity:
            logger.error(
                f"注文数量が上限を超えています: {quantity} > {self.max_order_quantity}"
            )
            return None

        if price * quantity > self.max_order_amount:
            logger.error(
                f"注文金額が上限を超えています: {price * quantity} > {self.max_order_amount}"
            )
            return None

        # 注文実行
        ctx = self._get_trade_context(market)
        if ctx is None:
            return None

        try:
            # SIMULATE環境で注文
            trd_side = TrdSide.BUY if side == "BUY" else TrdSide.SELL
            order_type_enum = (
                OrderType.MARKET if order_type == "MARKET" else OrderType.NORMAL
            )

            ret, data = ctx.place_order(
                price=price,
                qty=quantity,
                code=code,
                trd_side=trd_side,
                order_type=order_type_enum,
                trd_env=TrdEnv.SIMULATE,
            )

            if ret != RET_OK:
                logger.error(f"注文失敗: {data}")
                return None

            # 注文結果を保存
            order_id = str(data["order_id"].iloc[0])
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            order = PaperOrder(
                order_id=order_id,
                code=code,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                status="SUBMITTED",
                trd_env="SIMULATE",
                submitted_at=now,
                updated_at=now,
                raw_response=str(data.to_dict()),
            )

            self._save_order(order)
            logger.info(f"ペーパートレード注文完了: {order_id}")
            return order

        finally:
            ctx.close()

    def list_orders(self) -> list[PaperOrder]:
        """
        注文一覧を取得する

        Returns:
            list[PaperOrder]: 注文リスト
        """
        ctx = self._get_trade_context()
        if ctx is None:
            return []

        try:
            ret, data = ctx.order_list_query(trd_env=TrdEnv.SIMULATE)
            if ret != RET_OK:
                logger.error(f"注文一覧取得失敗: {data}")
                return []

            orders = []
            for _, row in data.iterrows():
                order = PaperOrder(
                    order_id=str(row.get("order_id", "")),
                    code=row.get("code", ""),
                    side="BUY" if row.get("trd_side") == TrdSide.BUY else "SELL",
                    quantity=int(row.get("qty", 0)),
                    price=float(row.get("price", 0)),
                    order_type="MARKET" if row.get("order_type") == OrderType.MARKET else "LIMIT",
                    status=str(row.get("order_status", "")),
                    trd_env="SIMULATE",
                    submitted_at=str(row.get("create_time", "")),
                    updated_at=str(row.get("updated_time", "")),
                )
                orders.append(order)

            return orders

        finally:
            ctx.close()

    def cancel_order(self, order_id: str) -> bool:
        """
        注文をキャンセルする

        Args:
            order_id: 注文ID

        Returns:
            bool: 成功ならTrue
        """
        ctx = self._get_trade_context()
        if ctx is None:
            return False

        try:
            ret, data = ctx.modify_order(
                modify_order_op=1,  # キャンセル
                order_id=order_id,
                qty=0,
                price=0,
                trd_env=TrdEnv.SIMULATE,
            )

            if ret != RET_OK:
                logger.error(f"キャンセル失敗: {data}")
                return False

            logger.info(f"注文キャンセル完了: {order_id}")
            return True

        finally:
            ctx.close()

    def _save_order(self, order: PaperOrder) -> None:
        """注文をSQLiteに保存する"""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper_orders
                    (order_id, code, side, quantity, price, order_type,
                     status, trd_env, submitted_at, updated_at, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.order_id,
                        order.code,
                        order.side,
                        order.quantity,
                        order.price,
                        order.order_type,
                        order.status,
                        order.trd_env,
                        order.submitted_at,
                        order.updated_at,
                        order.raw_response,
                    ),
                )
            except sqlite3.Error as e:
                logger.error(f"注文保存エラー: {e}")
