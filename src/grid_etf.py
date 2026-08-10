"""独立した日本ETF向けロング専用グリッド戦略。

このモジュールは既存のmomentum/SQLite仮想口座とは状態を共有しない。
日足OHLCでは、当日約定したBUYから当日SELLへ回すことを禁止し、
新しい反対注文は翌バーから有効にする。
"""

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class GridOrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class GridBar:
    date: str
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(isfinite(value) and value > 0 for value in values):
            raise ValueError("OHLCは正の有限値でなければなりません")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLCのhigh/lowが不正です")


@dataclass(frozen=True)
class GridConfig:
    strategy_name: str = "grid_etf_v1"
    initial_cash: float = 100_000.0
    atr_period: int = 14
    atr_multiplier: float = 0.75
    levels: int = 4
    level_capital: float = 10_000.0
    max_capital_pct: float = 60.0
    max_drawdown_pct: float = 10.0
    lot_size: int = 1

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.level_capital <= 0:
            raise ValueError("initial_cashとlevel_capitalは正数でなければなりません")
        if self.atr_period < 1 or self.levels < 1 or self.lot_size < 1:
            raise ValueError("atr_period/levels/lot_sizeは1以上でなければなりません")
        if self.atr_multiplier <= 0 or not 0 < self.max_capital_pct <= 100:
            raise ValueError("ATR倍率と最大資金拘束率が不正です")
        if self.max_drawdown_pct <= 0:
            raise ValueError("最大ドローダウン停止率は正数でなければなりません")


@dataclass
class GridOrder:
    side: GridOrderSide
    price: float
    quantity: int
    level: int
    created_index: int


@dataclass(frozen=True)
class GridFill:
    date: str
    side: GridOrderSide
    price: float
    quantity: int
    level: int


@dataclass(frozen=True)
class GridBarResult:
    date: str
    orders: list[GridOrder]
    fills: list[GridFill]
    equity: float
    reserved_cash: float
    stopped: bool
    reason: str


@dataclass(frozen=True)
class GridBacktestResult:
    strategy_name: str
    initial_cash: float
    final_equity: float
    max_drawdown_pct: float
    fills: list[GridFill]
    equity_curve: list[tuple[str, float]]
    stopped: bool


class GridEtfV1:
    """1銘柄・ロングのみのATR適応型グリッド。"""

    def __init__(self, config: GridConfig = GridConfig()):
        self.config = config
        self.cash = config.initial_cash
        self.pending_orders: list[GridOrder] = []
        self.positions: dict[int, tuple[int, float]] = {}
        self._bars: list[GridBar] = []
        self._base_price: float | None = None
        self._spacing: float | None = None
        self._peak_equity = config.initial_cash
        self._max_drawdown = 0.0
        self._stopped = False
        self._all_fills: list[GridFill] = []
        self._equity_curve: list[tuple[str, float]] = []

    @property
    def reserved_cash(self) -> float:
        return sum(order.price * order.quantity for order in self.pending_orders if order.side is GridOrderSide.BUY)

    def _atr(self) -> float | None:
        if len(self._bars) < self.config.atr_period + 1:
            return None
        recent = self._bars[-self.config.atr_period :]
        previous = self._bars[-self.config.atr_period - 1 : -1]
        true_ranges = [
            max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
            for bar, prev in zip(recent, previous)
        ]
        return sum(true_ranges) / len(true_ranges)

    def _equity(self, close: float) -> float:
        return self.cash + sum(quantity * close for quantity, _ in self.positions.values())

    def _new_buy_orders(self, index: int) -> list[GridOrder]:
        if self._base_price is None or self._spacing is None:
            return []
        maximum = self.config.initial_cash * self.config.max_capital_pct / 100.0
        committed = self.config.initial_cash - self.cash + self.reserved_cash
        available = max(0.0, maximum - committed)
        orders: list[GridOrder] = []
        for level in range(1, self.config.levels + 1):
            price = self._base_price - self._spacing * level
            quantity = int(min(self.config.level_capital, available) // price)
            quantity = quantity - quantity % self.config.lot_size
            if quantity < self.config.lot_size or any(order.level == level for order in self.pending_orders):
                continue
            order = GridOrder(GridOrderSide.BUY, price, quantity, level, index)
            orders.append(order)
            available -= price * quantity
        return orders

    def _fill_order(self, order: GridOrder, bar: GridBar, index: int) -> GridFill | None:
        if order.created_index >= index:
            return None
        touched = bar.low <= order.price if order.side is GridOrderSide.BUY else bar.high >= order.price
        if not touched:
            return None
        if order.side is GridOrderSide.BUY:
            cost = order.price * order.quantity
            if cost > self.cash:
                return None
            self.cash -= cost
            self.positions[order.level] = (order.quantity, order.price)
            sell = GridOrder(GridOrderSide.SELL, order.price + (self._spacing or 0.0), order.quantity, order.level, index)
            self.pending_orders.append(sell)
        else:
            position = self.positions.pop(order.level, None)
            if position is None:
                return None
            self.cash += order.price * order.quantity
            # Restore the same lower grid level, but only from the next bar.
            # This keeps the cycle independent from the momentum portfolio.
            if self._spacing is not None:
                replacement = GridOrder(
                    GridOrderSide.BUY,
                    order.price - self._spacing,
                    order.quantity,
                    order.level,
                    index,
                )
                maximum = self.config.initial_cash * self.config.max_capital_pct / 100.0
                committed = self.config.initial_cash - self.cash + self.reserved_cash
                if committed + replacement.price * replacement.quantity <= maximum:
                    self.pending_orders.append(replacement)
        return GridFill(bar.date, order.side, order.price, order.quantity, order.level)

    def on_bar(self, bar: GridBar) -> GridBarResult:
        index = len(self._bars)
        self._bars.append(bar)
        if self._stopped:
            return GridBarResult(bar.date, [], [], self._equity(bar.close), 0.0, True, "drawdown_stop")

        fills: list[GridFill] = []
        # Process only orders that existed before this bar. Orders paired from a fill
        # receive created_index=index and cannot fill in the same OHLC bar.
        for order in list(self.pending_orders):
            fill = self._fill_order(order, bar, index)
            if fill is not None:
                fills.append(fill)
                self.pending_orders.remove(order)

        if self._spacing is None:
            atr = self._atr()
            if atr is None:
                equity = self._equity(bar.close)
                self._equity_curve.append((bar.date, equity))
                return GridBarResult(bar.date, [], fills, equity, self.reserved_cash, False, "insufficient_atr_history")
            self._base_price = bar.close
            self._spacing = atr * self.config.atr_multiplier
            self.pending_orders.extend(self._new_buy_orders(index))

        equity = self._equity(bar.close)
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = (self._peak_equity - equity) / self._peak_equity * 100.0
        self._max_drawdown = max(self._max_drawdown, drawdown)
        if drawdown >= self.config.max_drawdown_pct:
            self._stopped = True
            self.pending_orders.clear()
        self._all_fills.extend(fills)
        self._equity_curve.append((bar.date, equity))
        reason = "drawdown_stop" if self._stopped else "active"
        return GridBarResult(bar.date, list(self.pending_orders), fills, equity, self.reserved_cash, self._stopped, reason)

    def backtest(self, bars: list[GridBar]) -> GridBacktestResult:
        for bar in bars:
            self.on_bar(bar)
        final_equity = self._equity_curve[-1][1] if self._equity_curve else self.cash
        return GridBacktestResult(
            strategy_name=self.config.strategy_name,
            initial_cash=self.config.initial_cash,
            final_equity=final_equity,
            max_drawdown_pct=self._max_drawdown,
            fills=list(self._all_fills),
            equity_curve=list(self._equity_curve),
            stopped=self._stopped,
        )
