from __future__ import annotations

import math
from collections.abc import Sequence


METRIC_NAMES = (
    "cagr",
    "excess_cagr",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "turnover",
    "exposure",
)


def calculate_metrics(
    equity: Sequence[float],
    *,
    benchmark_equity: Sequence[float] | None = None,
    turnover: float = 0.0,
    exposure: float = 0.0,
    periods_per_year: int = 252,
) -> dict[str, float]:
    if len(equity) < 2 or any(value <= 0 for value in equity):
        raise ValueError("equity requires at least two positive observations")
    returns = [current / previous - 1.0 for previous, current in zip(equity, equity[1:])]
    years = len(returns) / periods_per_year
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years else 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    downside = [min(value, 0.0) ** 2 for value in returns]
    sharpe = mean / math.sqrt(variance) * math.sqrt(periods_per_year) if variance else 0.0
    downside_dev = math.sqrt(sum(downside) / len(downside))
    sortino = mean / downside_dev * math.sqrt(periods_per_year) if downside_dev else 0.0
    peak = equity[0]
    drawdowns: list[float] = []
    for value in equity:
        peak = max(peak, value)
        drawdowns.append((peak - value) / peak)
    max_drawdown = max(drawdowns)
    calmar = cagr / max_drawdown if max_drawdown else 0.0
    excess_cagr = 0.0
    if benchmark_equity is not None:
        if len(benchmark_equity) != len(equity) or any(value <= 0 for value in benchmark_equity):
            raise ValueError("benchmark equity must align with positive strategy equity")
        benchmark_cagr = (benchmark_equity[-1] / benchmark_equity[0]) ** (1 / years) - 1 if years else 0.0
        excess_cagr = cagr - benchmark_cagr
    return {
        "cagr": cagr,
        "excess_cagr": excess_cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "turnover": turnover,
        "exposure": exposure,
    }
