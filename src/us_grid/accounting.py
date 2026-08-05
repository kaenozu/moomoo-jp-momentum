"""
Cash / position / equity accounting for the US grid backtest.

Invariants enforced by this module:

- cash never goes negative
- a SELL never exceeds the held quantity
- a BUY never exceeds available cash (including pending reservations)
- equity = cash + market value of holdings (marked at the last close)
- dividends and FX gains are accounted separately from trading P/L
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import GridConfig
from .costs import commission_usd, sell_regulatory_fee_usd


@dataclass
class CashPosition:
    """Cash (USD) plus per-symbol holdings for the strategy."""

    cash_usd: float
    usd_jpy: float  # conversion rate used to report JPY
    initial_cash_jpy: float

    positions: dict[str, int] = field(default_factory=dict)  # code -> qty
    avg_cost_usd: dict[str, float] = field(default_factory=dict)
    realized_pl_usd: dict[str, float] = field(default_factory=dict)
    reserved_cash_usd: float = 0.0  # resting BUY reservations

    # attribution
    dividend_income_usd: float = 0.0
    fx_income_jpy: float = 0.0
    fee_total_usd: float = 0.0

    def _qty(self, code: str) -> int:
        return self.positions.get(code, 0)

    def available_cash_usd(self) -> float:
        return self.cash_usd - self.reserved_cash_usd

    def market_value_usd(self, prices: dict[str, float]) -> float:
        return sum(
            qty * prices[code] for code, qty in self.positions.items() if code in prices
        )

    def total_equity_usd(self, prices: dict[str, float]) -> float:
        return self.cash_usd + self.market_value_usd(prices)

    def total_equity_jpy(self, prices: dict[str, float]) -> float:
        return self.total_equity_usd(prices) * self.usd_jpy

    def buy(
        self,
        grid: GridConfig,
        code: str,
        quantity: int,
        price_usd: float,
        fx_rate: float,
        date: str,
    ) -> None:
        """Execute a BUY fill. Raises ValueError on cash violation."""
        notional = price_usd * quantity
        fee = commission_usd(grid.costs, notional, quantity)
        total = notional + fee
        if total > self.available_cash_usd() + 1e-9:
            raise ValueError(
                f"BUY would exceed available cash: {code} total={total:.2f} "
                f"available={self.available_cash_usd():.2f}"
            )
        self.cash_usd -= total
        self.fee_total_usd += fee
        held = self._qty(code)
        new_qty = held + quantity
        new_avg = (
            (self.avg_cost_usd.get(code, 0.0) * held + notional) / new_qty
            if new_qty > 0
            else 0.0
        )
        self.positions[code] = new_qty
        self.avg_cost_usd[code] = new_avg

    def sell(
        self,
        grid: GridConfig,
        code: str,
        quantity: int,
        price_usd: float,
        fx_rate: float,
        date: str,
    ) -> None:
        """Execute a SELL fill. Raises ValueError if it would exceed holdings."""
        held = self._qty(code)
        if quantity > held:
            raise ValueError(
                f"SELL exceeds holdings: {code} sell={quantity} held={held}"
            )
        notional = price_usd * quantity
        fee = commission_usd(grid.costs, notional, quantity)
        reg_fee = sell_regulatory_fee_usd(grid.costs, notional)
        proceeds = notional - fee - reg_fee
        self.cash_usd += proceeds
        self.fee_total_usd += fee + reg_fee
        avg = self.avg_cost_usd.get(code, 0.0)
        realized = (price_usd - avg) * quantity - fee - reg_fee
        self.realized_pl_usd[code] = self.realized_pl_usd.get(code, 0.0) + realized
        self.positions[code] = held - quantity
        if self.positions[code] <= 0:
            self.positions.pop(code, None)
            self.avg_cost_usd.pop(code, None)

    def apply_dividend(
        self, code: str, quantity: int, dividend_per_share_usd: float
    ) -> None:
        """Credit a dividend on a position (ex-date)."""
        amount = quantity * dividend_per_share_usd
        self.cash_usd += amount
        self.dividend_income_usd += amount

    def apply_split(self, code: str, ratio: float) -> None:
        """Adjust position quantity and average cost for a split."""
        held = self._qty(code)
        if held <= 0:
            return
        self.positions[code] = int(held * ratio)
        self.avg_cost_usd[code] = self.avg_cost_usd.get(code, 0.0) / ratio

    def fx_convert_income(self, fx_rate: float) -> None:
        """Attribution helper: not used by the core engine (see backtest)."""
        return None


def jpy_return(
    grid: GridConfig,
    equity_jpy: float,
) -> float:
    """Total return in JPY percent."""
    if grid.capital_jpy <= 0:
        return 0.0
    return (equity_jpy - grid.capital_jpy) / grid.capital_jpy * 100.0
