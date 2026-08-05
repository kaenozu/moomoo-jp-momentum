"""
Backtest engine for the US grid strategy.

Daily loop (per trading day D):

1. Process resting orders against bar D's OHLC using the conservative fill
   model. BUY fills arm the paired SELL level effective from D+1. SELL fills
   re-arm the corresponding BUY level.
2. Apply dividends and splits effective on D.
3. Apply the regime computed on D-1 (never D's own close) to order placement.
4. Generate desired orders from the grid state; risk-engine approve them
   against cash/holdings/limits; record PLANNED orders (filled from D+1).
5. Mark positions to market and append the equity curve.

Two intraday order paths are simulated when multiple levels are touched in
one bar; the conservative (worst-case) path is the primary result.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .accounting import CashPosition, jpy_return
from .config import GridConfig
from .costs import min_profitable_spacing_bps
from .fills import (
    Bar,
    bar_from_dict,
    resting_buy_fill,
    resting_sell_fill,
)
from .model import (
    ApprovedOrder,
    DesiredOrder,
    GridInstance,
    GridLevelStatus,
    Regime,
)
from .regime import RegimeSeries, classify_regime, regime_for_bar
from .strategy import (
    arm_paired_sell,
    build_grid_levels,
    compute_spacing_pct,
    recenter_instance,
    refresh_spacing,
    should_recenter,
)


@dataclass
class EquityPoint:
    date: str
    cash_usd: float
    position_value_usd: float
    total_equity_usd: float
    total_equity_jpy: float
    fx_rate: float
    drawdown_pct: float
    regime: str
    open_orders: int
    filled_levels: int


@dataclass
class TradeRecord:
    date: str
    code: str
    side: str
    quantity: int
    price_usd: float
    fee_usd: float
    reason: str


@dataclass
class BacktestResult:
    run_id: str
    strategy_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    capital_jpy: float
    final_equity_usd: float
    final_equity_jpy: float
    total_return_pct_usd: float
    total_return_pct_jpy: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    calmar: float
    trade_count: int
    round_trip_count: int
    win_rate: float
    profit_factor: float
    avg_gross_cycle_usd: float
    avg_net_cycle_usd: float
    fee_total_usd: float
    fee_drag_pct: float
    dividend_income_usd: float
    equity_curve: list[EquityPoint]
    trades: list[TradeRecord]
    orders_created: int
    orders_filled: int
    orders_cancelled: int
    orders_rejected: int
    cash_shortage_count: int
    inventory_days: list[int]
    benchmark_returns: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class _OrderState:
    """A resting order being tracked by the engine."""

    code: str
    side: str
    quantity: int
    limit_price: float
    instance_id: int
    level_index: int
    client_key: str
    filled_quantity: int = 0
    created_bar_index: int = 0
    active: bool = True
    fill_price: float | None = None
    fill_date: str | None = None


@dataclass
class _FillState:
    """A fill that is pending (fills on the bar it was created)."""

    order: _OrderState
    price: float
    date: str
    mode: str


class GridBacktester:
    def __init__(
        self,
        grid: GridConfig,
        data: dict[str, list[dict]],
        fx: list[dict] | None = None,
    ):
        self.grid = grid
        self.data = data  # code -> list of bar dicts (ascending date)
        self.fx = fx or []  # list of {date, rate} ascending
        self._fx_index: dict[str, float] = {}
        for row in fx or []:
            self._fx_index[str(row["date"])] = float(row["rate"])

        self.instances: dict[str, GridInstance] = {}
        self.orders: list[_OrderState] = []
        self.trades: list[TradeRecord] = []
        self.warnings: list[str] = []
        self.skipped: list[str] = []

        self.instance_counter = 0
        self.orders_created = 0
        self.orders_filled = 0
        self.orders_cancelled = 0
        self.orders_rejected = 0
        self.cash_shortage_count = 0
        self.inventory_days: list[int] = []
        self._reserved_by_code: dict[str, float] = {}
        self._core_positions: dict[str, int] = {}

    # ── helpers ──

    def _fx_rate(self, date: str) -> float:
        rate = self._fx_index.get(date)
        if rate is not None and rate > 0:
            return rate
        # fall back to the latest rate <= date
        best: float | None = None
        for d, r in self._fx_index.items():
            if d <= date and (best is None or r > 0):
                best = r
        return best if best and best > 0 else 150.0

    def _bars(self, code: str) -> list[Bar]:
        return [bar_from_dict(row) for row in self.data.get(code, [])]

    def _close_on(self, code: str, bars: list[Bar], index: int) -> float:
        return bars[index].close

    def _approve_buy(
        self,
        desired: DesiredOrder,
        state: CashPosition,
        prices: dict[str, float],
        regime: Regime,
        day: str,
        bars_by_code: dict[str, list[Bar]],
    ) -> ApprovedOrder | None:
        grid = self.grid
        # Regime gating (regime from the previous bar already applied).
        if grid.regime_filter_enabled and regime == Regime.RISK_OFF:
            self.orders_rejected += 1
            return None
        if grid.regime_filter_enabled and regime == Regime.TREND_UP:
            self.orders_rejected += 1
            return None

        notional = desired.limit_price * desired.quantity
        # Cash check must include cash already reserved by resting BUY orders.
        already_reserved = self._reserved_by_code.get(desired.code, 0.0)
        if notional > state.cash_usd - already_reserved + 1e-9:
            self.cash_shortage_count += 1
            self.orders_rejected += 1
            return None

        # Per-symbol allocation limit.
        if grid.risk.max_symbol_allocation_pct > 0:
            equity = state.total_equity_usd(prices)
            symbol_value = (
                state.positions.get(desired.code, 0)
                * prices.get(desired.code, desired.limit_price)
                + notional
            )
            limit_value = equity * grid.risk.max_symbol_allocation_pct / 100.0
            if equity > 0 and symbol_value > limit_value:
                self.orders_rejected += 1
                return None

        # Total deployed cap (cash reserve floor).
        if grid.risk.max_total_deployed_pct > 0:
            equity = state.total_equity_usd(prices)
            deployed_limit = equity * grid.risk.max_total_deployed_pct / 100.0
            projected_deployed = (equity - state.available_cash_usd()) + notional
            if equity > 0 and projected_deployed > deployed_limit:
                self.orders_rejected += 1
                return None

        # Grid width must beat round-trip cost.
        min_width_bps = min_profitable_spacing_bps(grid.costs)
        spacing_bps = (self.instances[desired.code].spacing_pct or 0) * 100
        if spacing_bps > 0 and spacing_bps < min_width_bps:
            self.orders_rejected += 1
            self.skipped.append(
                f"{day}: spacing {spacing_bps:.1f}bps < cost {min_width_bps:.1f}bps"
            )
            return None

        key = f"grid:{desired.code}:{desired.side}:{desired.grid_level_index}:{self._generation(desired)}"
        return ApprovedOrder(
            code=desired.code,
            side="BUY",
            quantity=desired.quantity,
            limit_price=desired.limit_price,
            client_order_key=key,
            grid_instance_id=desired.grid_instance_id,
            grid_level_index=desired.grid_level_index,
            reason=desired.reason,
        )

    def _approve_sell(
        self, desired: DesiredOrder, state: CashPosition
    ) -> ApprovedOrder | None:
        held = state.positions.get(desired.code, 0)
        if held < desired.quantity:
            self.orders_rejected += 1
            return None
        # Grid SELL may not consume the core position.
        core_qty = self._core_positions.get(desired.code, 0)
        sellable = held - core_qty
        if sellable < desired.quantity:
            self.orders_rejected += 1
            return None
        key = f"grid:{desired.code}:{desired.side}:{desired.grid_level_index}:{self._generation(desired)}"
        return ApprovedOrder(
            code=desired.code,
            side="SELL",
            quantity=desired.quantity,
            limit_price=desired.limit_price,
            client_order_key=key,
            grid_instance_id=desired.grid_instance_id,
            grid_level_index=desired.grid_level_index,
            reason=desired.reason,
        )

    def _generation(self, desired: DesiredOrder) -> int:
        instance = self.instances.get(desired.code)
        if instance is None:
            return 0
        return instance.version

    # ── core loop ──

    def run(
        self,
        start_date: str,
        end_date: str,
        seed: int = 0,
    ) -> BacktestResult:
        grid = self.grid
        run_id = hashlib.sha256(
            f"{grid.strategy_name}|{start_date}|{end_date}|{seed}|{sorted(grid.symbols)}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]

        # Build bar series per symbol and the union calendar.
        bars_by_code: dict[str, list[Bar]] = {}
        for code in grid.symbols:
            code_bars = self._bars(code)
            if not code_bars:
                self.skipped.append(f"{code}: no data")
                continue
            bars_by_code[code] = code_bars

        if not bars_by_code:
            raise ValueError("no symbol data available for backtest")

        # Calendar = sorted union of dates across symbols within [start, end].
        all_dates: set[str] = set()
        for code, bars in bars_by_code.items():
            for bar in bars:
                if start_date <= bar.date <= end_date:
                    all_dates.add(bar.date)
        calendar = sorted(all_dates)
        if not calendar:
            raise ValueError(f"no bars in range {start_date}..{end_date}")

        # Precompute per-symbol date -> bar index and regime series.
        date_index: dict[str, dict[str, int]] = {}
        regime_series: dict[str, RegimeSeries] = {}
        for code, bars in bars_by_code.items():
            date_index[code] = {bar.date: i for i, bar in enumerate(bars)}
            closes = [b.close for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            regime_series[code] = classify_regime(closes, highs, lows, grid)

        capital_jpy = grid.capital_jpy
        initial_fx = self._fx_rate(calendar[0])
        initial_cash_usd = capital_jpy / initial_fx

        state = CashPosition(
            cash_usd=initial_cash_usd,
            usd_jpy=initial_fx,
            initial_cash_jpy=capital_jpy,
        )

        # Core + grid: buy core at the first close of each symbol.
        if grid.core_allocation_pct > 0:
            self._seed_core(
                state, bars_by_code, date_index, calendar[0], initial_cash_usd
            )

        # Initialize grid instances at the first available close.
        for code, bars in bars_by_code.items():
            first_index = date_index[code].get(calendar[0])
            if first_index is None:
                # find first bar at/after calendar start
                first_index = next(
                    (i for i, b in enumerate(bars) if b.date >= calendar[0]),
                    None,
                )
            if first_index is None:
                continue
            center = bars[first_index].close
            spacing = compute_spacing_pct(
                grid,
                [b.high for b in bars[: first_index + 1]],
                [b.low for b in bars[: first_index + 1]],
                [b.close for b in bars[: first_index + 1]],
                center,
            )
            instance = build_grid_levels(
                grid,
                code,
                center,
                spacing,
                core_quantity=self._core_qty(code, state),
                grid_quantity=grid.quantity_per_level,
            )
            self.instance_counter += 1
            instance.id = self.instance_counter
            self.instances[code] = instance

        equity_curve: list[EquityPoint] = []
        peak_equity_usd = initial_cash_usd
        daily_returns_usd: list[float] = []
        prev_equity_usd = initial_cash_usd

        for day in calendar:
            day_prices: dict[str, float] = {}
            for code, bars in bars_by_code.items():
                idx = date_index[code].get(day)
                if idx is not None:
                    day_prices[code] = bars[idx].close

            # ── Phase 1: resting order fills on this bar ──
            self._process_fills(
                day, day_prices, bars_by_code, date_index, state, calendar
            )

            # ── Phase 2: corporate actions on this date ──
            self._apply_corporate_actions(day, state)

            # ── Phase 3: regime (computed on the previous bar) ──
            regime_today: dict[str, Regime] = {}
            for code, series in regime_series.items():
                idx = date_index[code].get(day)
                if idx is None:
                    regime_today[code] = Regime.UNKNOWN
                    continue
                prev_idx = idx - 1 if idx > 0 else None
                if prev_idx is None:
                    regime_today[code] = Regime.UNKNOWN
                else:
                    regime_today[code] = regime_for_bar(series, prev_idx)

            # ── Phase 4: desired orders → approve → record ──
            self._generate_orders(
                day,
                day_prices,
                state,
                regime_today,
                bars_by_code,
                date_index,
            )

            # ── Phase 5: equity mark and curve ──
            equity_usd = state.total_equity_usd(day_prices)
            equity_jpy = equity_usd * self._fx_rate(day)
            peak_equity_usd = max(peak_equity_usd, equity_usd)
            dd = (
                (peak_equity_usd - equity_usd) / peak_equity_usd * 100
                if peak_equity_usd > 0
                else 0.0
            )
            if prev_equity_usd > 0:
                daily_returns_usd.append(
                    (equity_usd - prev_equity_usd) / prev_equity_usd
                )
            prev_equity_usd = equity_usd

            equity_curve.append(
                EquityPoint(
                    date=day,
                    cash_usd=state.cash_usd,
                    position_value_usd=state.market_value_usd(day_prices),
                    total_equity_usd=equity_usd,
                    total_equity_jpy=equity_jpy,
                    fx_rate=self._fx_rate(day),
                    drawdown_pct=dd,
                    regime=",".join(
                        sorted({str(r.value) for r in regime_today.values()})
                    ),
                    open_orders=sum(1 for o in self.orders if o.active),
                    filled_levels=self._filled_level_count(),
                )
            )

        # ── final metrics ──
        # Mark positions at the last bar of the backtest window, never at a
        # future bar outside the window (no look-ahead).
        last_calendar_day = calendar[-1]
        final_prices: dict[str, float] = {}
        for code, bars in bars_by_code.items():
            idx = date_index[code].get(last_calendar_day)
            if idx is not None:
                final_prices[code] = bars[idx].close
            else:
                # fall back to the latest close at or before the last day
                for bar in reversed(bars):
                    if bar.date <= last_calendar_day:
                        final_prices[code] = bar.close
                        break
        final_equity_usd = state.total_equity_usd(final_prices)
        final_equity_jpy = final_equity_usd * self._fx_rate(last_calendar_day)

        total_return_usd = (
            (final_equity_usd - initial_cash_usd) / initial_cash_usd * 100
            if initial_cash_usd > 0
            else 0.0
        )
        total_return_jpy = jpy_return(grid, final_equity_jpy)

        n_days = max(len(calendar), 1)
        years = n_days / 252.0
        cagr = (
            ((final_equity_usd / initial_cash_usd) ** (1 / years) - 1) * 100
            if years > 0 and initial_cash_usd > 0
            else 0.0
        )

        metrics = _compute_metrics(
            equity_curve,
            daily_returns_usd,
            self.trades,
        )

        return BacktestResult(
            run_id=run_id,
            strategy_name=grid.strategy_name,
            symbols=sorted(bars_by_code.keys()),
            start_date=start_date,
            end_date=end_date,
            capital_jpy=capital_jpy,
            final_equity_usd=final_equity_usd,
            final_equity_jpy=final_equity_jpy,
            total_return_pct_usd=total_return_usd,
            total_return_pct_jpy=total_return_jpy,
            cagr_pct=cagr,
            max_drawdown_pct=metrics["max_drawdown_pct"],
            sharpe=metrics["sharpe"],
            sortino=metrics["sortino"],
            calmar=metrics["calmar"],
            trade_count=len(self.trades),
            round_trip_count=int(metrics["round_trip_count"]),
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            avg_gross_cycle_usd=metrics["avg_gross_cycle_usd"],
            avg_net_cycle_usd=metrics["avg_net_cycle_usd"],
            fee_total_usd=state.fee_total_usd,
            fee_drag_pct=metrics["fee_drag_pct"],
            dividend_income_usd=state.dividend_income_usd,
            equity_curve=equity_curve,
            trades=self.trades,
            orders_created=self.orders_created,
            orders_filled=self.orders_filled,
            orders_cancelled=self.orders_cancelled,
            orders_rejected=self.orders_rejected,
            cash_shortage_count=self.cash_shortage_count,
            inventory_days=self.inventory_days,
            warnings=self.warnings,
            skipped=self.skipped,
        )

    # ── phases ──

    def _process_fills(
        self,
        day: str,
        prices: dict[str, float],
        bars_by_code: dict[str, list[Bar]],
        date_index: dict[str, dict[str, int]],
        state: CashPosition,
        calendar: list[str],
    ) -> None:
        """Process resting orders against today's bar (conservative model)."""
        for order in self.orders:
            if not order.active:
                continue
            bars = bars_by_code.get(order.code)
            if not bars:
                continue
            idx = date_index[order.code].get(day)
            if idx is None:
                continue
            bar = bars[idx]

            if order.side == "BUY":
                decision = resting_buy_fill(order.limit_price, order.quantity, bar)
                if decision.filled:
                    # check cash at fill time (reservation was already
                    # deducted from available cash at approval time)
                    notional = decision.price * order.quantity
                    from .costs import commission_usd

                    fee = commission_usd(self.grid.costs, notional, order.quantity)
                    if notional + fee > state.cash_usd + 1e-9:
                        self.cash_shortage_count += 1
                        self.orders_cancelled += 1
                        order.active = False
                        self._release_reservation(order)
                        continue
                    self._release_reservation(order)
                    self._execute_fill(order, decision, state)
            else:
                decision = resting_sell_fill(order.limit_price, order.quantity, bar)
                if decision.filled:
                    self._execute_fill(order, decision, state)

    def _release_reservation(self, order: _OrderState) -> None:
        """Free the cash reserved for a resting BUY order."""
        if order.side != "BUY":
            return
        current = self._reserved_by_code.get(order.code, 0.0)
        reserved = order.limit_price * order.quantity
        self._reserved_by_code[order.code] = max(0.0, current - reserved)
        if self._reserved_by_code[order.code] <= 1e-9:
            self._reserved_by_code.pop(order.code, None)

    def _execute_fill(
        self,
        order: _OrderState,
        decision,
        state: CashPosition,
    ) -> None:
        from .costs import commission_usd, sell_regulatory_fee_usd
        from .fills import apply_cost_adjustment

        grid = self.grid
        # Apply spread + slippage to the limit-touch price: BUY pays up,
        # SELL receives less.
        price = apply_cost_adjustment(decision, order.side, grid)
        code = order.code
        qty = order.quantity
        notional = price * qty
        if order.side == "BUY":
            fee = commission_usd(grid.costs, notional, qty)
            state.buy(
                grid,
                code,
                qty,
                price,
                self._fx_rate(decision.fill_date),
                decision.fill_date,
            )
            self.trades.append(
                TradeRecord(
                    date=decision.fill_date,
                    code=code,
                    side="BUY",
                    quantity=qty,
                    price_usd=price,
                    fee_usd=fee,
                    reason="grid_buy",
                )
            )
            # arm the paired SELL one spacing above the fill price
            instance = self.instances.get(code)
            if instance is not None:
                arm_paired_sell(instance, grid, order.level_index, price)
            order.active = False
            order.filled_quantity = qty
            order.fill_price = price
            order.fill_date = decision.fill_date
            self.orders_filled += 1
        else:
            fee = commission_usd(grid.costs, notional, qty)
            reg = sell_regulatory_fee_usd(grid.costs, notional)
            state.sell(
                grid,
                code,
                qty,
                price,
                self._fx_rate(decision.fill_date),
                decision.fill_date,
            )
            self.trades.append(
                TradeRecord(
                    date=decision.fill_date,
                    code=code,
                    side="SELL",
                    quantity=qty,
                    price_usd=price,
                    fee_usd=fee + reg,
                    reason="grid_sell",
                )
            )
            # re-arm the paired BUY level
            instance = self.instances.get(code)
            if instance is not None:
                sell_level = next(
                    (
                        lv
                        for lv in instance.levels
                        if lv.side == "SELL" and lv.level_index == order.level_index
                    ),
                    None,
                )
                paired = (
                    sell_level.paired_level_index if sell_level else order.level_index
                )
                for level in instance.levels:
                    if level.side == "BUY" and level.level_index == paired:
                        level.status = GridLevelStatus.ACTIVE
                        level.last_order_id = None
                        break
                if sell_level is not None:
                    sell_level.status = GridLevelStatus.INACTIVE
                    sell_level.last_order_id = None
            order.active = False
            order.filled_quantity = qty
            order.fill_price = price
            order.fill_date = decision.fill_date
            self.orders_filled += 1

    def _apply_corporate_actions(self, day: str, state: CashPosition) -> None:
        """Apply splits and dividends from the data source.

        Data is split-adjusted by the fetcher (yfinance auto_adjust=True), so
        splits do not change the price series; we still adjust position
        quantities so SELL limits remain consistent. Dividends are credited
        as cash when the data source provides per-share amounts.
        """
        # NOTE: split/dividend metadata is passed via self._corporate_actions
        # (set externally before run()). Without metadata, position quantities
        # stay consistent with the adjusted series and no double counting
        # occurs.
        actions = getattr(self, "_corporate_actions", {})
        for code, action_list in actions.items():
            for action in action_list:
                if str(action.get("date")) != day:
                    continue
                kind = action.get("kind")
                if kind == "split":
                    state.apply_split(code, float(action["ratio"]))
                elif kind == "dividend":
                    qty = state.positions.get(code, 0)
                    state.apply_dividend(code, qty, float(action["per_share"]))

    def _generate_orders(
        self,
        day: str,
        prices: dict[str, float],
        state: CashPosition,
        regime_today: dict[str, Regime],
        bars_by_code: dict[str, list[Bar]],
        date_index: dict[str, dict[str, int]],
    ) -> None:
        grid = self.grid
        for code, instance in self.instances.items():
            regime = regime_today.get(code, Regime.UNKNOWN)

            # Adaptive spacing refresh (ATR mode).
            bars = bars_by_code.get(code)
            if bars:
                idx = date_index[code].get(day)
                if idx is not None:
                    refresh_spacing(
                        instance,
                        grid,
                        [b.high for b in bars[: idx + 1]],
                        [b.low for b in bars[: idx + 1]],
                        [b.close for b in bars[: idx + 1]],
                    )

            buy_allowed = True
            sell_allowed = True
            if grid.regime_filter_enabled:
                if regime == Regime.RISK_OFF:
                    buy_allowed = False
                elif regime == Regime.TREND_UP:
                    buy_allowed = False

            # SELL levels: arm only if inventory exists; never naked, never
            # consuming the core position.
            held = state.positions.get(code, 0)
            core_qty = self._core_positions.get(code, 0)
            sellable = held - core_qty
            for level in instance.levels:
                if level.side != "SELL" or level.status != GridLevelStatus.ACTIVE:
                    continue
                if level.last_order_id is not None:
                    continue
                if sellable < level.quantity:
                    # Not enough inventory: cancel the armed SELL.
                    level.status = GridLevelStatus.INACTIVE
                    continue
                if not sell_allowed:
                    continue
                desired = DesiredOrder(
                    code=code,
                    side="SELL",
                    quantity=level.quantity,
                    limit_price=level.target_price,
                    grid_instance_id=instance.id,
                    grid_level_index=level.level_index,
                    reason="grid_sell",
                )
                approved = self._approve_sell(desired, state)
                if approved is not None:
                    self._place_order(approved, day)

            # BUY levels.
            for level in instance.levels:
                if level.side != "BUY" or level.status != GridLevelStatus.ACTIVE:
                    continue
                if level.last_order_id is not None:
                    continue
                if not buy_allowed:
                    continue
                # Inventory level cap (no infinite averaging down).
                inventory_levels = state.positions.get(code, 0) // max(
                    grid.quantity_per_level, 1
                )
                if inventory_levels >= grid.risk.max_inventory_levels_per_symbol:
                    self.orders_rejected += 1
                    continue
                desired = DesiredOrder(
                    code=code,
                    side="BUY",
                    quantity=level.quantity,
                    limit_price=level.target_price,
                    grid_instance_id=instance.id,
                    grid_level_index=level.level_index,
                    reason="grid_buy",
                )
                approved = self._approve_buy(
                    desired,
                    state,
                    prices,
                    regime,
                    day,
                    bars_by_code,
                )
                if approved is not None:
                    self._place_order(approved, day)

            # Recentering check (buy levels may be below price after a rally).
            current = prices.get(code)
            if current is None:
                continue
            inventory_levels = state.positions.get(code, 0) // max(
                grid.quantity_per_level, 1
            )
            recenter_count = 0
            if should_recenter(
                instance,
                grid,
                current,
                None,
                instance.last_recenter_at,
                day,
                recenter_count,
                inventory_levels,
            ):
                # Determine ATR for distance check.
                from .indicators import atr as _atr

                closes = [b.close for b in bars] if bars else []
                highs = [b.high for b in bars] if bars else []
                lows = [b.low for b in bars] if bars else []
                atr_values = _atr(highs, lows, closes, grid.atr_period)
                last_atr = next(
                    (v for v in reversed(atr_values) if v is not None), None
                )
                if should_recenter(
                    instance,
                    grid,
                    current,
                    last_atr,
                    instance.last_recenter_at,
                    day,
                    recenter_count,
                    inventory_levels,
                ):
                    # Only recenter if there are no unfilled BUY orders below.
                    if not any(
                        o.active and o.side == "BUY" and o.code == code
                        for o in self.orders
                    ):
                        recenter_instance(instance, grid, current, day)
                        instance.version += 1
                        recenter_count += 1
                        self.orders_cancelled += 0

    def _place_order(self, approved: ApprovedOrder, day: str) -> None:
        order = _OrderState(
            code=approved.code,
            side=approved.side,
            quantity=approved.quantity,
            limit_price=approved.limit_price,
            instance_id=approved.grid_instance_id or 0,
            level_index=approved.grid_level_index or 0,
            client_key=approved.client_order_key,
        )
        self.orders.append(order)
        self.orders_created += 1
        # Reserve cash for resting BUY orders so multiple same-day approvals
        # cannot over-reserve.
        if approved.side == "BUY":
            self._reserved_by_code[approved.code] = (
                self._reserved_by_code.get(approved.code, 0.0)
                + approved.limit_price * approved.quantity
            )
        # Link the level to the order id.
        instance = self.instances.get(approved.code)
        if instance is not None:
            for level in instance.levels:
                if level.level_index == approved.grid_level_index:
                    level.last_order_id = len(self.orders)
                    level.last_order_key = approved.client_order_key
                    break

    def _seed_core(
        self,
        state: CashPosition,
        bars_by_code: dict[str, list[Bar]],
        date_index: dict[str, dict[str, int]],
        start_day: str,
        initial_cash_usd: float,
    ) -> None:
        grid = self.grid
        core_budget = initial_cash_usd * grid.core_allocation_pct / 100.0
        if core_budget <= 0:
            return
        per_symbol = core_budget / max(len(self.grid.symbols), 1)
        for code, bars in bars_by_code.items():
            idx = date_index[code].get(start_day)
            if idx is None:
                continue
            price = bars[idx].close
            if price <= 0:
                continue
            qty = int(per_symbol / price)
            if qty <= 0:
                continue
            from .costs import commission_usd

            fee = commission_usd(grid.costs, price * qty, qty)
            if price * qty + fee > state.available_cash_usd() + 1e-9:
                continue
            state.buy(grid, code, qty, price, self._fx_rate(start_day), start_day)
            self._core_positions[code] = self._core_positions.get(code, 0) + qty
            self.trades.append(
                TradeRecord(
                    date=start_day,
                    code=code,
                    side="BUY",
                    quantity=qty,
                    price_usd=price,
                    fee_usd=fee,
                    reason="core",
                )
            )

    def _core_qty(self, code: str, state: CashPosition) -> int:
        return self._core_positions.get(code, 0)

    def _filled_level_count(self) -> int:
        return sum(
            1
            for instance in self.instances.values()
            for level in instance.levels
            if level.status == GridLevelStatus.FILLED
        )


def _compute_metrics(
    equity_curve: list[EquityPoint],
    daily_returns: list[float],
    trades: list[TradeRecord],
) -> dict[str, float]:
    max_dd = max((p.drawdown_pct for p in equity_curve), default=0.0)

    n = len(daily_returns)
    mean = sum(daily_returns) / n if n else 0.0
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1) if n > 1 else 0.0
    std = variance**0.5
    sharpe = mean / std * (252**0.5) if std > 0 else 0.0

    downside = [r for r in daily_returns if r < 0]
    dvar = sum(r * r for r in downside) / (n - 1) if n > 1 else 0.0
    dstd = dvar**0.5
    sortino = mean / dstd * (252**0.5) if dstd > 0 else 0.0

    # Round trips: a BUY followed by a SELL of the same quantity.
    buys: list[tuple[str, float, int]] = []
    realized: list[float] = []
    gross_cycles: list[float] = []
    net_cycles: list[float] = []
    for t in trades:
        if t.side == "BUY":
            buys.append((t.code, t.price_usd, t.quantity))
        else:
            for i, (code, price, qty) in enumerate(buys):
                if code == t.code and qty == t.quantity:
                    gross = (t.price_usd - price) * qty
                    net = gross - t.fee_usd  # approximate (buy fee not tracked here)
                    realized.append(gross)
                    gross_cycles.append(gross)
                    net_cycles.append(net)
                    buys.pop(i)
                    break

    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    win_rate = len(wins) / len(realized) * 100 if realized else 0.0
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = (
        total_win / total_loss
        if total_loss > 0
        else (float("inf") if total_win > 0 else 0.0)
    )

    total_fee = sum(t.fee_usd for t in trades)
    final_equity = equity_curve[-1].total_equity_usd if equity_curve else 0.0
    fee_drag = total_fee / final_equity * 100 if final_equity else 0.0

    return {
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": (mean * 252 / max_dd * 100 if max_dd > 0 else 0.0),
        "round_trip_count": len(realized),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_gross_cycle_usd": sum(gross_cycles) / len(gross_cycles)
        if gross_cycles
        else 0.0,
        "avg_net_cycle_usd": sum(net_cycles) / len(net_cycles) if net_cycles else 0.0,
        "fee_drag_pct": fee_drag,
    }
