"""
Benchmark models: Buy & Hold, cash, and broad-market comparisons.

The Buy & Hold benchmark buys whole shares of each symbol at the first close
with the same capital, same FX, same fees, and same corporate actions as the
grid strategy, so the comparison is fair.
"""

from __future__ import annotations

from dataclasses import dataclass

from .accounting import CashPosition
from .config import GridConfig
from .costs import commission_usd
from .fills import Bar


@dataclass
class BenchmarkResult:
    name: str
    total_return_pct_usd: float
    total_return_pct_jpy: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    equity_curve: list[dict]


def buy_and_hold(
    grid: GridConfig,
    bars_by_code: dict[str, list[Bar]],
    fx_rate_series: dict[str, float],
    start_date: str,
    end_date: str,
    calendar: list[str],
) -> BenchmarkResult:
    """Whole-share Buy & Hold with the same fee/FX/corporate-action policy."""
    capital_jpy = grid.capital_jpy
    initial_fx = fx_rate_series.get(calendar[0], 150.0)
    cash_usd = capital_jpy / initial_fx

    state = CashPosition(
        cash_usd=cash_usd,
        usd_jpy=initial_fx,
        initial_cash_jpy=capital_jpy,
    )

    # Buy at the first available close.
    for code, bars in bars_by_code.items():
        if not bars:
            continue
        first = next((b for b in bars if b.date >= start_date), None)
        if first is None:
            continue
        price = first.close
        if price <= 0:
            continue
        budget = cash_usd / max(len(bars_by_code), 1)
        qty = int(budget / price)
        while qty > 0:
            fee = commission_usd(grid.costs, price * qty, qty)
            if price * qty + fee <= state.cash_usd + 1e-9:
                break
            qty -= 1
        if qty <= 0:
            continue
        state.buy(grid, code, qty, price, initial_fx, first.date)

    # Walk the calendar.
    curve: list[dict] = []
    peak = state.total_equity_usd({c: b[0].close for c, b in bars_by_code.items() if b})
    daily: list[float] = []
    prev = peak
    for day in calendar:
        prices = {
            code: _close_on_or_before(bars, day)
            for code, bars in bars_by_code.items()
            if bars
        }
        equity = state.total_equity_usd(prices)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if prev > 0:
            daily.append((equity - prev) / prev)
        prev = equity
        curve.append(
            {
                "date": day,
                "total_equity_usd": equity,
                "total_equity_jpy": equity * fx_rate_series.get(day, initial_fx),
                "drawdown_pct": dd,
            }
        )

    final_prices = {
        code: _close_on_or_before(bars, calendar[-1])
        for code, bars in bars_by_code.items()
        if bars
    }
    final_equity = state.total_equity_usd(final_prices)
    total_return_usd = (
        (final_equity - cash_usd) / cash_usd * 100 if cash_usd > 0 else 0.0
    )
    total_return_jpy = (
        (final_equity * fx_rate_series.get(calendar[-1], initial_fx) - capital_jpy)
        / capital_jpy
        * 100
        if capital_jpy > 0
        else 0.0
    )
    years = max(len(calendar), 1) / 252.0
    cagr = (
        ((final_equity / cash_usd) ** (1 / years) - 1) * 100
        if years > 0 and cash_usd > 0
        else 0.0
    )

    mean = sum(daily) / len(daily) if daily else 0.0
    var = (
        sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)
        if len(daily) > 1
        else 0.0
    )
    std = var**0.5
    sharpe = mean / std * (252**0.5) if std > 0 else 0.0
    downside = [r for r in daily if r < 0]
    dvar = sum(r * r for r in downside) / (len(daily) - 1) if len(daily) > 1 else 0.0
    dstd = dvar**0.5
    sortino = mean / dstd * (252**0.5) if dstd > 0 else 0.0

    max_dd = max((p["drawdown_pct"] for p in curve), default=0.0)

    return BenchmarkResult(
        name="buy_and_hold",
        total_return_pct_usd=total_return_usd,
        total_return_pct_jpy=total_return_jpy,
        cagr_pct=cagr,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        sortino=sortino,
        equity_curve=curve,
    )


def _close_on_or_before(bars: list[Bar], day: str) -> float:
    best = None
    for b in bars:
        if b.date <= day:
            best = b.close
        else:
            break
    return best if best is not None else (bars[0].close if bars else 0.0)


def cash_benchmark(grid: GridConfig, calendar: list[str]) -> BenchmarkResult:
    """Cash (JPY) benchmark: flat, zero return."""
    curve = [
        {
            "date": day,
            "total_equity_usd": grid.capital_jpy / 150.0,
            "total_equity_jpy": grid.capital_jpy,
            "drawdown_pct": 0.0,
        }
        for day in calendar
    ]
    return BenchmarkResult(
        name="cash",
        total_return_pct_usd=0.0,
        total_return_pct_jpy=0.0,
        cagr_pct=0.0,
        max_drawdown_pct=0.0,
        sharpe=0.0,
        sortino=0.0,
        equity_curve=curve,
    )
