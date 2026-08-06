"""Safety and reproducibility corrections for US-grid research execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .accounting import CashPosition
from .backtest import (
    ApprovedOrder,
    BacktestResult,
    DesiredOrder,
    GridBacktester as _BaseGridBacktester,
)
from .benchmark import BenchmarkResult, _close_on_or_before
from .config import GridConfig
from .costs import commission_usd
from .fills import Bar


def _buy_execution_cost(grid: GridConfig, limit_price: float, quantity: int) -> float:
    bps = grid.costs.spread_bps + grid.costs.slippage_bps
    execution_price = limit_price * (1 + bps / 10000)
    notional = execution_price * quantity
    return notional + commission_usd(grid.costs, notional, quantity)


class ResearchGridBacktester(_BaseGridBacktester):
    """Backtester with portfolio-wide reservations and canonical actions."""

    def __init__(self, grid: GridConfig, data: dict[str, list[dict]], fx=None):
        super().__init__(grid, data, fx)
        self._orders_created_by_day: dict[str, int] = {}
        self._pre_action_positions: dict[str, dict[str, int]] = {}

    def _total_reserved_cash(self) -> float:
        return sum(self._reserved_by_code.values())

    def _active_order_count(self, code: str | None = None) -> int:
        return sum(
            1
            for order in self.orders
            if order.active and (code is None or order.code == code)
        )

    def _active_sell_quantity(self, code: str) -> int:
        return sum(
            order.quantity
            for order in self.orders
            if order.active and order.code == code and order.side == "SELL"
        )

    def _approve_buy(
        self,
        desired: DesiredOrder,
        state: CashPosition,
        prices: dict[str, float],
        regime,
        day: str,
        bars_by_code: dict[str, list[Bar]],
    ) -> ApprovedOrder | None:
        required_cash = _buy_execution_cost(
            self.grid,
            desired.limit_price,
            desired.quantity,
        )
        total_reserved = self._total_reserved_cash()
        available = state.cash_usd - total_reserved
        if required_cash > available + 1e-9:
            self.cash_shortage_count += 1
            self.orders_rejected += 1
            return None

        equity = state.total_equity_usd(prices)
        held_value = (
            state.positions.get(desired.code, 0)
            * prices.get(desired.code, desired.limit_price)
        )
        symbol_reserved = self._reserved_by_code.get(desired.code, 0.0)
        projected_symbol = held_value + symbol_reserved + required_cash
        symbol_limit = equity * self.grid.risk.max_symbol_allocation_pct / 100.0
        if symbol_limit > 0 and projected_symbol > symbol_limit + 1e-9:
            self.orders_rejected += 1
            return None

        deployed = state.market_value_usd(prices) + total_reserved + required_cash
        deployed_limit = equity * self.grid.risk.max_total_deployed_pct / 100.0
        if deployed_limit > 0 and deployed > deployed_limit + 1e-9:
            self.orders_rejected += 1
            return None

        remaining_cash = state.cash_usd - total_reserved - required_cash
        reserve_floor = equity * self.grid.risk.minimum_cash_reserve_pct / 100.0
        if remaining_cash + 1e-9 < reserve_floor:
            self.orders_rejected += 1
            return None

        return super()._approve_buy(
            desired,
            state,
            prices,
            regime,
            day,
            bars_by_code,
        )

    def _approve_sell(
        self,
        desired: DesiredOrder,
        state: CashPosition,
    ) -> ApprovedOrder | None:
        held = state.positions.get(desired.code, 0)
        core = self._core_positions.get(desired.code, 0)
        already_reserved = self._active_sell_quantity(desired.code)
        if already_reserved + desired.quantity > held - core:
            self.orders_rejected += 1
            return None
        return super()._approve_sell(desired, state)

    def _place_order(self, approved: ApprovedOrder, day: str) -> None:
        limits = self.grid.risk
        if self._active_order_count() >= limits.max_open_orders_total:
            self.orders_rejected += 1
            return
        if self._active_order_count(approved.code) >= limits.max_open_orders_per_symbol:
            self.orders_rejected += 1
            return
        if self._orders_created_by_day.get(day, 0) >= limits.max_orders_per_day:
            self.orders_rejected += 1
            return

        before = self._reserved_by_code.get(approved.code, 0.0)
        super()._place_order(approved, day)
        self._orders_created_by_day[day] = self._orders_created_by_day.get(day, 0) + 1
        if approved.side == "BUY":
            expected = _buy_execution_cost(
                self.grid,
                approved.limit_price,
                approved.quantity,
            )
            nominal = approved.limit_price * approved.quantity
            self._reserved_by_code[approved.code] = before + expected
            if expected + 1e-9 < nominal:
                raise AssertionError("buy reservation cannot be below nominal")

    def _release_reservation(self, order) -> None:
        if order.side != "BUY":
            return
        current = self._reserved_by_code.get(order.code, 0.0)
        reserved = _buy_execution_cost(
            self.grid,
            order.limit_price,
            order.quantity,
        )
        remaining = max(0.0, current - reserved)
        if remaining <= 1e-9:
            self._reserved_by_code.pop(order.code, None)
        else:
            self._reserved_by_code[order.code] = remaining

    def _process_fills(self, day: str, prices, bars_by_code, date_index, state, calendar):
        self._pre_action_positions[day] = dict(state.positions)
        return super()._process_fills(
            day,
            prices,
            bars_by_code,
            date_index,
            state,
            calendar,
        )

    def _apply_corporate_actions(self, day: str, state: CashPosition) -> None:
        """Credit dividends once using the position held before the ex-date.

        Split events are intentionally not applied because the canonical raw
        Yahoo OHLC series is already split-adjusted.
        """
        prior_positions = self._pre_action_positions.get(day, {})
        actions = getattr(self, "_corporate_actions", {})
        for code, action_list in actions.items():
            for action in action_list:
                if str(action.get("date")) != day:
                    continue
                if action.get("kind") != "dividend":
                    self.warnings.append(
                        f"{day}: ignored non-dividend action for split-adjusted {code}"
                    )
                    continue
                quantity = prior_positions.get(code, 0)
                if quantity > 0:
                    state.apply_dividend(
                        code,
                        quantity,
                        float(action["per_share"]),
                    )

    def run(self, start_date: str, end_date: str, seed: int = 0) -> BacktestResult:
        result = super().run(start_date, end_date, seed=seed)
        payload: dict[str, Any] = {
            "grid": asdict(self.grid),
            "data": self.data,
            "fx": self.fx,
            "start_date": start_date,
            "end_date": end_date,
            "seed": seed,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        result.run_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return result


def buy_and_hold_with_dividends(
    grid: GridConfig,
    bars_by_code: dict[str, list[Bar]],
    fx_rate_series: dict[str, float],
    start_date: str,
    end_date: str,
    calendar: list[str],
    corporate_actions: dict[str, list[dict]] | None = None,
) -> BenchmarkResult:
    """Whole-share benchmark using the same split/dividend policy as the grid."""
    capital_jpy = grid.capital_jpy
    initial_fx = fx_rate_series.get(calendar[0], 150.0)
    initial_cash_usd = capital_jpy / initial_fx
    state = CashPosition(
        cash_usd=initial_cash_usd,
        usd_jpy=initial_fx,
        initial_cash_jpy=capital_jpy,
    )
    purchase_dates: dict[str, str] = {}

    for code, bars in bars_by_code.items():
        first = next((bar for bar in bars if bar.date >= start_date), None)
        if first is None or first.close <= 0:
            continue
        budget = initial_cash_usd / max(len(bars_by_code), 1)
        quantity = int(budget / first.close)
        while quantity > 0:
            fee = commission_usd(grid.costs, first.close * quantity, quantity)
            if first.close * quantity + fee <= state.cash_usd + 1e-9:
                break
            quantity -= 1
        if quantity <= 0:
            continue
        state.buy(grid, code, quantity, first.close, initial_fx, first.date)
        purchase_dates[code] = first.date

    actions_by_day: dict[str, list[tuple[str, dict]]] = {}
    for code, actions in (corporate_actions or {}).items():
        for action in actions:
            actions_by_day.setdefault(str(action.get("date")), []).append((code, action))

    curve: list[dict] = []
    first_prices = {
        code: bars[0].close for code, bars in bars_by_code.items() if bars
    }
    peak = state.total_equity_usd(first_prices)
    previous = peak
    daily_returns: list[float] = []

    for day in calendar:
        for code, action in actions_by_day.get(day, []):
            if action.get("kind") != "dividend":
                continue
            if purchase_dates.get(code, day) >= day:
                continue
            quantity = state.positions.get(code, 0)
            if quantity > 0:
                state.apply_dividend(code, quantity, float(action["per_share"]))

        prices = {
            code: _close_on_or_before(bars, day)
            for code, bars in bars_by_code.items()
            if bars
        }
        equity = state.total_equity_usd(prices)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if previous > 0:
            daily_returns.append((equity - previous) / previous)
        previous = equity
        curve.append(
            {
                "date": day,
                "total_equity_usd": equity,
                "total_equity_jpy": equity * fx_rate_series.get(day, initial_fx),
                "drawdown_pct": drawdown,
            }
        )

    final_prices = {
        code: _close_on_or_before(bars, calendar[-1])
        for code, bars in bars_by_code.items()
        if bars
    }
    final_equity = state.total_equity_usd(final_prices)
    final_fx = fx_rate_series.get(calendar[-1], initial_fx)
    usd_return = (
        (final_equity - initial_cash_usd) / initial_cash_usd * 100
        if initial_cash_usd > 0
        else 0.0
    )
    jpy_return = (
        (final_equity * final_fx - capital_jpy) / capital_jpy * 100
        if capital_jpy > 0
        else 0.0
    )
    years = max(len(calendar), 1) / 252.0
    cagr = (
        ((final_equity / initial_cash_usd) ** (1 / years) - 1) * 100
        if years > 0 and initial_cash_usd > 0
        else 0.0
    )
    mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    variance = (
        sum((value - mean) ** 2 for value in daily_returns)
        / (len(daily_returns) - 1)
        if len(daily_returns) > 1
        else 0.0
    )
    standard_deviation = variance**0.5
    sharpe = mean / standard_deviation * (252**0.5) if standard_deviation > 0 else 0.0
    downside = [value for value in daily_returns if value < 0]
    downside_variance = (
        sum(value * value for value in downside) / (len(daily_returns) - 1)
        if len(daily_returns) > 1
        else 0.0
    )
    downside_deviation = downside_variance**0.5
    sortino = mean / downside_deviation * (252**0.5) if downside_deviation > 0 else 0.0

    return BenchmarkResult(
        name="buy_and_hold",
        total_return_pct_usd=usd_return,
        total_return_pct_jpy=jpy_return,
        cagr_pct=cagr,
        max_drawdown_pct=max(
            (point["drawdown_pct"] for point in curve),
            default=0.0,
        ),
        sharpe=sharpe,
        sortino=sortino,
        equity_curve=curve,
    )
