"""
Holdings Beta Floor を適用するバックテストランナー。

既存 BacktestRunner の売買・約定・損益計算は維持し、日次終値時点の
holdings_implied_beta に基づいて翌営業日の新規買付枠だけを縮小する。
既存保有を強制売却しないため、目標投資比率への収束は通常のexit後となる。
"""

from __future__ import annotations

import logging
from typing import Optional

from .backtest_runner import BM1306, BM2559, BacktestRunner, _PendingOrder
from .config import Config
from .indicators import StockIndicators, add_cross_sectional_stats, calculate_indicators
from .portfolio_beta import HoldingsBetaFloor, PortfolioBetaSnapshot
from .strategies import StrategyRegistry

logger = logging.getLogger(__name__)


class BetaFloorBacktestRunner(BacktestRunner):
    """日次の保有銘柄β下限制御を追加した BacktestRunner。"""

    def __init__(
        self,
        config: Config,
        *,
        enabled_override: Optional[bool] = None,
    ) -> None:
        super().__init__(config)
        self.beta_floor = HoldingsBetaFloor(
            config,
            self.db_path,
            enabled_override=enabled_override,
        )
        self.beta_floor_trigger_days = 0
        self.beta_floor_missing_days = 0
        self.beta_floor_min_beta: Optional[float] = None
        self.beta_floor_last_snapshot: Optional[PortfolioBetaSnapshot] = None

    def _position_values_at_close(self, day: str) -> dict[str, float]:
        values: dict[str, float] = {}
        for position in self._get_all_positions():
            if position["quantity"] <= 0:
                continue
            bars = self._bars_up_to(position["code"], day)
            if bars.empty:
                continue
            values[position["code"]] = (
                float(bars.iloc[0]["close"]) * int(position["quantity"])
            )
        return values

    def _evaluate_beta_floor(
        self,
        day: str,
        pending_orders: list[_PendingOrder],
    ) -> tuple[PortfolioBetaSnapshot, float, float]:
        position_values = self._position_values_at_close(day)
        snapshot = self.beta_floor.evaluate(position_values, day)
        self.beta_floor_last_snapshot = snapshot

        if snapshot.missing_codes:
            self.beta_floor_missing_days += 1
        if snapshot.target_investment_ratio < 1.0:
            self.beta_floor_trigger_days += 1
            beta = snapshot.holdings_implied_beta
            if beta is not None:
                self.beta_floor_min_beta = (
                    beta
                    if self.beta_floor_min_beta is None
                    else min(self.beta_floor_min_beta, beta)
                )
            logger.info(
                "beta floor発動: date=%s holdings_beta=%.4f target_ratio=%.4f",
                day,
                beta if beta is not None else float("nan"),
                snapshot.target_investment_ratio,
            )

        current_position_value = sum(position_values.values())
        pending_buy_value = sum(
            order.fill_price * order.quantity + self.commission
            for order in pending_orders
            if order.side == "BUY"
        )
        return snapshot, current_position_value, pending_buy_value

    def run(self, strategy_name: str, start_date: str, end_date: str) -> int:
        """β下限を適用してバックテストを実行する。"""
        self.strategy_name = strategy_name
        self.cash = self.initial_cash
        self.reserved_cash = 0.0
        self.beta_floor_trigger_days = 0
        self.beta_floor_missing_days = 0
        self.beta_floor_min_beta = None
        self.beta_floor_last_snapshot = None
        run_id = self.create_run(strategy_name, start_date, end_date)

        strategy = StrategyRegistry.get(strategy_name, self.config)
        days = self._trading_days(start_date, end_date)
        logger.info(
            "beta floorバックテスト: %s %s〜%s (%d日, strategy=%s, enabled=%s)",
            start_date,
            end_date,
            len(days),
            strategy_name,
            self.beta_floor.enabled,
        )
        if not days:
            raise ValueError(
                f"バックテスト対象日のデータがありません: {start_date}〜{end_date}"
            )

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, name, role, tradable FROM symbols WHERE enabled=1"
            ).fetchall()

        trade_candidates = [
            row
            for row in rows
            if row["role"] == "trade_candidate" and row["tradable"]
        ]
        benchmark_codes = (
            {row["code"] for row in rows if row["role"] == "benchmark"}
            if strategy_name == "etf_rotation"
            else set()
        )

        total_days = len(days)
        orders_created = 0
        fills_processed = 0
        exits_generated = 0
        peak_equity = self.initial_cash
        pending_orders: list[_PendingOrder] = []
        idle_code = self._idle_cash_benchmark_code()
        idle_bench_prev = None
        trailing_highs: dict[str, float] = {}
        target_pos_value = self.initial_cash / self.max_total_positions

        for index, day in enumerate(days):
            if (index + 1) % 20 == 0:
                logger.info("  [%d/%d] %s ...", index + 1, total_days, day)

            candidates = trade_candidates
            if strategy_name == "etf_rotation":
                candidates = [row for row in rows if row["code"] in benchmark_codes]

            today_fills = [order for order in pending_orders if order.fill_date == day]
            pending_orders = [order for order in pending_orders if order.fill_date != day]

            for order in today_fills:
                if order.side == "BUY":
                    cost = order.fill_price * order.quantity + self.commission
                    with self._conn() as conn:
                        cursor = conn.execute(
                            "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                self.run_id,
                                strategy_name,
                                order.code,
                                "BUY",
                                order.quantity,
                                "MARKET_SIM",
                                "FILLED",
                                order.signal_date,
                            ),
                        )
                        order_id = cursor.lastrowid
                        conn.execute(
                            "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                self.run_id,
                                order_id,
                                strategy_name,
                                order.code,
                                "BUY",
                                order.quantity,
                                order.fill_price,
                                day,
                                "next_day_open",
                            ),
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO backtest_positions (run_id, strategy_name, code, quantity, avg_cost, realized_pl) VALUES (?,?,?,?,?,0)",
                            (
                                self.run_id,
                                strategy_name,
                                order.code,
                                order.quantity,
                                order.fill_price,
                            ),
                        )
                    trailing_highs[order.code] = order.fill_price
                    self.cash -= cost
                    self.reserved_cash = max(0.0, self.reserved_cash - cost)
                    fills_processed += 1

                elif order.side == "SELL":
                    with self._conn() as conn:
                        cursor = conn.execute(
                            "INSERT INTO backtest_orders (run_id, strategy_name, code, side, quantity, order_type, status, signal_date, exit_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                self.run_id,
                                strategy_name,
                                order.code,
                                "SELL",
                                order.quantity,
                                "MARKET_SIM",
                                "FILLED",
                                order.signal_date,
                                order.exit_reason,
                            ),
                        )
                        order_id = cursor.lastrowid
                        conn.execute(
                            "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                self.run_id,
                                order_id,
                                strategy_name,
                                order.code,
                                "SELL",
                                order.quantity,
                                order.fill_price,
                                day,
                                "exit",
                            ),
                        )
                        realized_pl = (order.fill_price - order.avg_cost) * order.quantity
                        conn.execute(
                            "UPDATE backtest_positions SET quantity=0, realized_pl=realized_pl + ? WHERE run_id=? AND strategy_name=? AND code=?",
                            (
                                realized_pl,
                                self.run_id,
                                strategy_name,
                                order.code,
                            ),
                        )
                    self.cash += order.fill_price * order.quantity - self.commission
                    trailing_highs.pop(order.code, None)
                    exits_generated += 1
                    fills_processed += 1

            positions = self._get_all_positions()
            held_codes = {
                position["code"]
                for position in positions
                if position["quantity"] > 0
            }

            for position in positions:
                code = position["code"]
                if position["quantity"] <= 0:
                    continue
                if any(
                    order.code == code and order.side == "SELL"
                    for order in pending_orders
                ):
                    continue

                bars = self._bars_up_to(code, day)
                if bars.empty or len(bars) < 2:
                    continue
                current_price = float(bars.iloc[0]["close"])
                avg_cost = position["avg_cost"]

                if code in trailing_highs:
                    trailing_highs[code] = max(trailing_highs[code], current_price)
                else:
                    trailing_highs[code] = current_price

                exit_reason = None
                trail_level = trailing_highs[code] * (
                    1.0 - self.stop_loss_pct / 100.0
                )
                if current_price <= trail_level:
                    exit_reason = "trailing_stop"

                if not exit_reason and len(bars) >= 25:
                    ma25 = bars["close"].iloc[:25].mean()
                    if current_price < ma25:
                        exit_reason = "ma25_cross"

                if exit_reason:
                    next_bar = self._next_open_bar(code, day)
                    if next_bar:
                        fill_date, next_open = next_bar
                        fill_price_sell = next_open * (1 - self.slippage_bps / 10000)
                        pending_orders.append(
                            _PendingOrder(
                                code=code,
                                side="SELL",
                                quantity=position["quantity"],
                                fill_price=fill_price_sell,
                                fill_date=fill_date,
                                signal_date=day,
                                exit_reason=exit_reason,
                                avg_cost=avg_cost,
                            )
                        )

            snapshot, current_position_value, pending_buy_value = self._evaluate_beta_floor(
                day, pending_orders
            )
            target_ratio = snapshot.target_investment_ratio
            equity_before_orders = self.cash + current_position_value
            target_total_position_value = equity_before_orders * target_ratio
            desired_cash_reserve = max(
                0.0, equity_before_orders - target_total_position_value
            )
            remaining_exposure = max(
                0.0,
                target_total_position_value - current_position_value - pending_buy_value,
            )

            if (
                target_ratio < 1.0
                and current_position_value > target_total_position_value
            ):
                logger.info(
                    "beta floor目標超過: date=%s current=%.0f target=%.0f; 強制売却せずexit後に収束",
                    day,
                    current_position_value,
                    target_total_position_value,
                )

            day_indicators: list[StockIndicators] = []
            valid_pairs = []
            for symbol in candidates:
                code = symbol["code"]
                if code in held_codes:
                    continue
                if any(
                    order.code == code and order.side == "BUY"
                    for order in pending_orders
                ):
                    continue

                bars = self._bars_up_to(code, day)
                if bars.empty or len(bars) < 25:
                    continue

                indicator = calculate_indicators(bars, code, symbol["name"])
                if indicator is None or indicator.ma25 is None:
                    continue
                if (
                    indicator.close is None
                    or indicator.close > self.max_trade_price
                    or indicator.close < self.min_trade_price
                ):
                    continue

                day_indicators.append(indicator)
                valid_pairs.append((symbol, indicator))

            if day_indicators:
                add_cross_sectional_stats(day_indicators)

            current_pos_count = self._count_positions()
            pending_buy_count = sum(
                1 for order in pending_orders if order.side == "BUY"
            )
            slots_available = (
                self.max_total_positions - current_pos_count - pending_buy_count
            )

            for symbol, indicator in valid_pairs:
                if slots_available <= 0:
                    break
                code = symbol["code"]

                available_cash = self.cash - self.reserved_cash
                if target_ratio < 1.0:
                    available_cash = max(0.0, available_cash - desired_cash_reserve)
                if indicator.close and indicator.close > available_cash:
                    continue

                result = strategy.evaluate(indicator)
                if result.signal_type != "BUY_CANDIDATE":
                    continue

                next_bar = self._next_open_bar(code, day)
                if next_bar is None:
                    continue
                fill_date, next_open = next_bar
                fill_price = next_open * (1 + self.slippage_bps / 10000)

                if target_ratio < 1.0:
                    max_order_value = min(
                        target_pos_value * target_ratio,
                        remaining_exposure,
                        available_cash,
                    )
                    quantity = int(max_order_value / fill_price)
                    if quantity <= 0:
                        continue
                else:
                    quantity = max(1, int(target_pos_value / fill_price))

                cost = fill_price * quantity + self.commission
                if cost > available_cash:
                    continue

                pending_orders.append(
                    _PendingOrder(
                        code=code,
                        side="BUY",
                        quantity=quantity,
                        fill_price=fill_price,
                        fill_date=fill_date,
                        signal_date=day,
                    )
                )
                self.reserved_cash += cost
                if target_ratio < 1.0:
                    remaining_exposure = max(0.0, remaining_exposure - cost)
                slots_available -= 1
                orders_created += 1

            if idle_code:
                benchmark_today = self._benchmark_value(idle_code, day)
                if benchmark_today is not None and idle_bench_prev is not None:
                    daily_benchmark_return = (
                        benchmark_today - idle_bench_prev
                    ) / idle_bench_prev
                    if target_ratio < 1.0:
                        protected_cash = min(self.cash, desired_cash_reserve)
                        benchmark_cash = self.cash - protected_cash
                        self.cash = protected_cash + benchmark_cash * (
                            1 + daily_benchmark_return
                        )
                    else:
                        self.cash = self.cash * (1 + daily_benchmark_return)
                idle_bench_prev = benchmark_today

            position_value = 0.0
            for position in self._get_all_positions():
                if position["quantity"] <= 0:
                    continue
                bars = self._bars_up_to(position["code"], day)
                if bars.empty:
                    logger.warning(
                        "valuation price missing: %s on %s",
                        position["code"],
                        day,
                    )
                    continue
                position_value += float(bars.iloc[0]["close"]) * position["quantity"]
            total_equity = self.cash + position_value

            benchmark_2559 = self._benchmark_value(BM2559, day)
            benchmark_1306 = self._benchmark_value(BM1306, day)
            peak_equity = max(peak_equity, total_equity)
            drawdown = (
                max(0, (peak_equity - total_equity) / peak_equity * 100)
                if peak_equity
                else 0
            )

            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO backtest_equity_curve (run_id, strategy_name, date, cash, position_value, total_equity, benchmark_2559_value, benchmark_1306_value, drawdown_pct) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        self.run_id,
                        strategy_name,
                        day,
                        self.cash,
                        position_value,
                        total_equity,
                        benchmark_2559,
                        benchmark_1306,
                        drawdown,
                    ),
                )

        final_equity = self.cash + sum(
            (
                self._bars_up_to(position["code"], days[-1]).iloc[0]["close"]
                if not self._bars_up_to(position["code"], days[-1]).empty
                else 0
            )
            * position["quantity"]
            for position in self._get_all_positions()
            if position["quantity"] > 0
        )
        total_return = (
            (final_equity - self.initial_cash) / self.initial_cash * 100
        )

        benchmark_2559_start = self._benchmark_value(BM2559, start_date)
        benchmark_2559_end = self._benchmark_value(BM2559, days[-1])
        benchmark_2559_return = (
            (benchmark_2559_end - benchmark_2559_start)
            / benchmark_2559_start
            * 100
            if benchmark_2559_start and benchmark_2559_end is not None
            else None
        )
        benchmark_1306_start = self._benchmark_value(BM1306, start_date)
        benchmark_1306_end = self._benchmark_value(BM1306, days[-1])
        benchmark_1306_return = (
            (benchmark_1306_end - benchmark_1306_start)
            / benchmark_1306_start
            * 100
            if benchmark_1306_start and benchmark_1306_end is not None
            else None
        )
        stats = self._calculate_run_stats()

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE backtest_runs
                SET final_equity=?, total_return_pct=?, max_drawdown_pct=?,
                    win_rate=?, profit_factor=?, trade_count=?,
                    benchmark_2559_return=?, excess_vs_2559=?,
                    benchmark_1306_return=?, excess_vs_1306=?
                WHERE id=?
                """,
                (
                    final_equity,
                    total_return,
                    stats["max_drawdown_pct"],
                    stats["win_rate"],
                    stats["profit_factor"],
                    stats["trade_count"],
                    benchmark_2559_return,
                    total_return - benchmark_2559_return
                    if benchmark_2559_return is not None
                    else None,
                    benchmark_1306_return,
                    total_return - benchmark_1306_return
                    if benchmark_1306_return is not None
                    else None,
                    self.run_id,
                ),
            )

        logger.info(
            "beta floorバックテスト完了: return=%.2f%%, orders=%d, fills=%d, exits=%d, trigger_days=%d, missing_days=%d",
            total_return,
            orders_created,
            fills_processed,
            exits_generated,
            self.beta_floor_trigger_days,
            self.beta_floor_missing_days,
        )
        return run_id
