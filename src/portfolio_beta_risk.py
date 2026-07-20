"""日次仮想運用へ Holdings Beta Floor を適用するサービス。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config
from .portfolio_beta import (
    HoldingsBetaFloor,
    PortfolioBetaSnapshot,
    allocate_proportional_reduction,
)

if TYPE_CHECKING:
    from .virtual_trade import VirtualTradeManager


@dataclass(frozen=True)
class BetaFloorDecision:
    """当日終値から決定した翌営業日のβ下限制御。"""

    snapshot: PortfolioBetaSnapshot
    current_position_value: float
    target_position_value: float
    max_new_investment: float
    cancelled_buy_orders: int
    trim_orders: int
    scheduled_trim_value: float


class PortfolioBetaRiskManager:
    """仮想ポートフォリオのBUY抑制と比例縮小SELLを管理する。"""

    def __init__(self, config: Config):
        self.db_path = Path(config.database_path)
        self.beta_floor = HoldingsBetaFloor(config, self.db_path)

    def _latest_close(self, code: str, target_date: str) -> float | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                SELECT close FROM daily_bars
                WHERE code = ? AND date <= ? AND close IS NOT NULL
                ORDER BY date DESC LIMIT 1
                """,
                (code, target_date),
            ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def apply(
        self,
        manager: "VirtualTradeManager",
        strategy_name: str,
        target_date: str,
    ) -> BetaFloorDecision:
        """当日終値でβを評価し、必要なら翌日用SELLを登録する。"""
        positions = manager.get_positions(strategy_name)
        prices: dict[str, float] = {}
        position_values: dict[str, float] = {}
        quantities: dict[str, int] = {}
        for position in positions:
            close = self._latest_close(position.code, target_date)
            if close is None or position.quantity <= 0:
                continue
            prices[position.code] = close
            quantities[position.code] = int(position.quantity)
            position_values[position.code] = close * int(position.quantity)

        snapshot = self.beta_floor.evaluate(position_values, target_date)
        current_position_value = sum(position_values.values())
        cash = manager.get_cash(strategy_name, target_date)
        total_equity = cash + current_position_value
        target_position_value = total_equity * snapshot.target_investment_ratio

        cancelled_buy_orders = 0
        trim_orders = 0
        scheduled_trim_value = 0.0
        pending_orders = manager.get_pending_orders(strategy_name)

        if snapshot.target_investment_ratio < 1.0:
            for order in pending_orders:
                if order.side == "BUY" and order.id is not None:
                    if manager.cancel_order(order.id):
                        cancelled_buy_orders += 1

            pending_orders = manager.get_pending_orders(strategy_name)
            pending_sell_codes = {
                order.code for order in pending_orders if order.side == "SELL"
            }
            pending_sell_value = 0.0
            for order in pending_orders:
                if order.side != "SELL" or order.code not in quantities:
                    continue
                held_quantity = quantities[order.code]
                sell_quantity = min(int(order.quantity), held_quantity)
                pending_sell_value += prices[order.code] * sell_quantity

            effective_position_value = max(
                0.0,
                current_position_value - pending_sell_value,
            )
            reduction_value = max(
                0.0,
                effective_position_value - target_position_value,
            )
            eligible_quantities = {
                code: quantity
                for code, quantity in quantities.items()
                if code not in pending_sell_codes
            }
            allocation = allocate_proportional_reduction(
                eligible_quantities,
                prices,
                reduction_value,
            )
            for code, quantity in allocation.items():
                order = manager.place_order(
                    strategy_name=strategy_name,
                    code=code,
                    side="SELL",
                    quantity=quantity,
                    order_type="MARKET_SIM",
                    submitted_at=target_date,
                    exit_reason="beta_floor",
                )
                if order is not None:
                    trim_orders += 1
                    scheduled_trim_value += prices[code] * quantity

        future_position_value = max(
            0.0,
            current_position_value - scheduled_trim_value,
        )
        max_new_investment = max(
            0.0,
            target_position_value - future_position_value,
        )
        if trim_orders > 0:
            # 縮小SELLと新規BUYの同日クロスを避け、約定後に再評価する。
            max_new_investment = 0.0

        return BetaFloorDecision(
            snapshot=snapshot,
            current_position_value=current_position_value,
            target_position_value=target_position_value,
            max_new_investment=max_new_investment,
            cancelled_buy_orders=cancelled_buy_orders,
            trim_orders=trim_orders,
            scheduled_trim_value=scheduled_trim_value,
        )
