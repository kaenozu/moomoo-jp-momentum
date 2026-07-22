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
    - JP市場ではこのモジュールは停止する
    - US市場向けにexperimentalとして残す
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from futu import (
    OpenSecTradeContext,
    OrderType,
    RET_OK,
    SecurityFirm,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

from .config import Config

logger = logging.getLogger(__name__)

UNSUPPORTED_MARKETS = {"JP"}


@dataclass
class PaperOrder:
    """ペーパートレード注文"""
    order_id: str
    code: str
    side: str
    quantity: int
    price: float
    order_type: str
    status: str
    trd_env: str = "SIMULATE"
    submitted_at: str = ""
    updated_at: str = ""
    raw_response: str = ""


class PaperTradeManager:
    """ペーパートレード管理クラス"""

    def __init__(self, config: Config):
        self.config = config
        self.db_path = Path(config.database_path)

        pt_config = config.get("paper_trade", {})
        self.enabled = pt_config.get("enabled", False)
        self.allow_market_order = pt_config.get("allow_market_order", False)
        self.max_order_quantity = pt_config.get("max_order_quantity", 10)
        self.max_order_amount = pt_config.get("max_order_amount", 50000)

        self.host = config.opend_host
        self.port = config.opend_port

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得する"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_trade_context(self, market: str = "US") -> Optional[OpenSecTradeContext]:
        """SIMULATE環境の取引コンテキストを取得する"""
        market = market.upper()

        if market in UNSUPPORTED_MARKETS:
            logger.error(
                "moomoo JP / FUTUJP では、OpenAPI経由の%s市場SIMULATE注文が利用できないため、この機能は無効です。",
                market,
            )
            return None

        try:
            security_firm = SecurityFirm.FUTUINC if market == "US" else SecurityFirm.FUTUSG
            trd_market = TrdMarket.US if market == "US" else TrdMarket.HK

            return OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=self.host,
                port=self.port,
                security_firm=security_firm,
            )
        except Exception as e:
            logger.error("取引コンテキスト作成失敗: %s", e)
            return None

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "LIMIT",
        market: str = "US",
    ) -> Optional[PaperOrder]:
        """ペーパートレード注文を出す"""
        market = market.upper()
        side = side.upper()
        order_type = order_type.upper()

        if not self.enabled:
            logger.error("ペーパートレードが無効です")
            return None

        if market in UNSUPPORTED_MARKETS:
            logger.error(
                "moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できないため、この機能は無効です。\n"
                "アプリ内デモ取引とAPI SIMULATEは別物です。\n"
                "取引はmoomooアプリで手動実行してください。"
            )
            return None

        if side not in {"BUY", "SELL"}:
            logger.error("sideは BUY または SELL を指定してください")
            return None

        if order_type not in {"LIMIT", "MARKET"}:
            logger.error("order_typeは LIMIT または MARKET を指定してください")
            return None

        if order_type == "MARKET" and not self.allow_market_order:
            logger.error("成行注文は無効です（allow_market_order=false）")
            return None

        if quantity <= 0:
            logger.error("注文数量は1以上を指定してください")
            return None

        if quantity > self.max_order_quantity:
            logger.error("注文数量が上限を超えています: %s > %s", quantity, self.max_order_quantity)
            return None

        if price * quantity > self.max_order_amount:
            logger.error("注文金額が上限を超えています: %s > %s", price * quantity, self.max_order_amount)
            return None

        ctx = self._get_trade_context(market)
        if ctx is None:
            return None

        try:
            trd_side = TrdSide.BUY if side == "BUY" else TrdSide.SELL
            order_type_enum = OrderType.MARKET if order_type == "MARKET" else OrderType.NORMAL

            ret, data = ctx.place_order(
                price=price,
                qty=quantity,
                code=code,
                trd_side=trd_side,
                order_type=order_type_enum,
                trd_env=TrdEnv.SIMULATE,
            )

            if ret != RET_OK:
                logger.error("注文失敗: %s", data)
                return None
            assert isinstance(data, pd.DataFrame)

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
            logger.info("ペーパートレード注文完了: %s", order_id)
            return order

        finally:
            ctx.close()

    def list_orders(self, market: str = "US") -> list[PaperOrder]:
        """注文一覧を取得する"""
        ctx = self._get_trade_context(market)
        if ctx is None:
            return []

        try:
            ret, data = ctx.order_list_query(trd_env=TrdEnv.SIMULATE)
            if ret != RET_OK:
                logger.error("注文一覧取得失敗: %s", data)
                return []
            assert isinstance(data, pd.DataFrame)

            orders = []
            for _, row in data.iterrows():
                orders.append(PaperOrder(
                    order_id=str(row.get("order_id", "")),
                    code=str(row.get("code", "")),
                    side="BUY" if row.get("trd_side") == TrdSide.BUY else "SELL",
                    quantity=int(row.get("qty", 0) or 0),
                    price=float(row.get("price", 0) or 0),
                    order_type="MARKET" if row.get("order_type") == OrderType.MARKET else "LIMIT",
                    status=str(row.get("order_status", "")),
                    trd_env="SIMULATE",
                    submitted_at=str(row.get("create_time", "")),
                    updated_at=str(row.get("updated_time", "")),
                ))

            return orders

        finally:
            ctx.close()

    def cancel_order(self, order_id: str, market: str = "US") -> bool:
        """注文をキャンセルする"""
        ctx = self._get_trade_context(market)
        if ctx is None:
            return False

        try:
            ret, data = ctx.modify_order(
                modify_order_op=1,
                order_id=order_id,
                qty=0,
                price=0,
                trd_env=TrdEnv.SIMULATE,
            )

            if ret != RET_OK:
                logger.error("キャンセル失敗: %s", data)
                return False

            logger.info("注文キャンセル完了: %s", order_id)
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
                logger.error("注文保存エラー: %s", e)
