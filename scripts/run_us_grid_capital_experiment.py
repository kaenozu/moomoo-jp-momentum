"""
Capital scenario experiments: 100k / 300k / 500k / 1M JPY.

Shows how whole-share constraints interact with grid levels per symbol.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.us_grid.backtest import GridBacktester
from src.us_grid.benchmark import buy_and_hold
from src.us_grid.config import CostModel, GridConfig
from src.us_grid.data import load_or_fetch
from src.us_grid.fills import Bar, bar_from_dict
from src.us_grid.metrics import evaluate_verdict


def _grid(capital: float, symbols: list[str]) -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name="us_fixed_grid_v1",
        market="US",
        symbols=symbols,
        capital_jpy=capital,
        spacing_mode="fixed_pct",
        spacing_pct=1.5,
        buy_levels=3,
        sell_levels=3,
        quantity_per_level=1,
        costs=CostModel(
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
            maximum_commission_usd=22.0,
            spread_bps=5,
            slippage_bps=5,
            sell_regulatory_fee_enabled=True,
        ),
        data_dir="data/us_grid",
    )


def main() -> int:
    start = "2018-01-01"
    end = "2026-07-31"
    data = load_or_fetch(
        ["US.SPY", "US.QQQ", "US.IWM"],
        start,
        end,
        "data/us_grid",
        fetch=False,
    )
    bars_by_code = {
        code: [bar_from_dict(row) for row in rows] for code, rows in data.bars.items()
    }
    calendar = sorted(
        {
            b.date
            for bars in bars_by_code.values()
            for b in bars
            if start <= b.date <= end
        }
    )
    fx_rates = {row["date"]: float(row["rate"]) for row in data.fx}

    rows_out: list[dict] = []
    for capital in (100000, 300000, 500000, 1000000):
        for symbols in (
            ["US.SPY"],
            ["US.SPY", "US.QQQ", "US.IWM"],
        ):
            grid = _grid(capital, symbols)
            bt = GridBacktester(grid, data.bars, data.fx)
            result = bt.run(start, end)
            bh = buy_and_hold(grid, bars_by_code, fx_rates, start, end, calendar)
            verdict = evaluate_verdict(grid, result, bh)
            rows_out.append(
                {
                    "capital_jpy": capital,
                    "symbols": "|".join(symbols),
                    "total_return_jpy_pct": result.total_return_pct_jpy,
                    "cagr_pct": result.cagr_pct,
                    "max_drawdown_pct": result.max_drawdown_pct,
                    "sharpe": result.sharpe,
                    "round_trips": result.round_trip_count,
                    "bh_return_jpy_pct": bh.total_return_pct_jpy,
                    "verdict": verdict.label,
                }
            )

    out = Path("reports/us_grid_capital.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"wrote {out}")
    for r in rows_out:
        print(
            f"capital={r['capital_jpy']:>9,} symbols={r['symbols']:<25} "
            f"ret={r['total_return_jpy_pct']:7.2f}% bh={r['bh_return_jpy_pct']:7.2f}% "
            f"dd={r['max_drawdown_pct']:5.2f}% {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
