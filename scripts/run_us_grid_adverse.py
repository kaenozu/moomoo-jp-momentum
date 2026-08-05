"""
Adverse period analysis: 2018 Q4, 2020 crash, 2022 bear, and a bull period.

Runs the fixed grid on each adverse window and compares against Buy & Hold.
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

WINDOWS = [
    ("2018_q4", "2018-09-28", "2018-12-31"),
    ("2020_crash", "2020-02-01", "2020-04-30"),
    ("2022_bear", "2022-01-03", "2022-12-30"),
    ("2020_21_bull", "2020-05-01", "2021-12-31"),
    ("2023_26_bull", "2023-01-03", "2026-07-31"),
]


def _grid() -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name="us_fixed_grid_v1",
        market="US",
        symbols=["US.SPY", "US.QQQ", "US.IWM"],
        capital_jpy=300000.0,
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
    data = load_or_fetch(
        ["US.SPY", "US.QQQ", "US.IWM"],
        "2018-01-01",
        "2026-07-31",
        "data/us_grid",
        fetch=False,
    )
    bars_by_code = {
        code: [bar_from_dict(row) for row in rows] for code, rows in data.bars.items()
    }
    fx_rates = {row["date"]: float(row["rate"]) for row in data.fx}

    rows_out: list[dict] = []
    for name, start, end in WINDOWS:
        grid = _grid()
        bt = GridBacktester(grid, data.bars, data.fx)
        result = bt.run(start, end)
        calendar = sorted(
            {
                b.date
                for bars in bars_by_code.values()
                for b in bars
                if start <= b.date <= end
            }
        )
        bh = buy_and_hold(grid, bars_by_code, fx_rates, start, end, calendar)
        verdict = evaluate_verdict(grid, result, bh)
        rows_out.append(
            {
                "window": name,
                "start": start,
                "end": end,
                "grid_return_jpy_pct": result.total_return_pct_jpy,
                "bh_return_jpy_pct": bh.total_return_pct_jpy,
                "grid_dd_pct": result.max_drawdown_pct,
                "bh_dd_pct": bh.max_drawdown_pct,
                "sharpe": result.sharpe,
                "round_trips": result.round_trip_count,
                "verdict": verdict.label,
            }
        )

    out = Path("reports/us_grid_adverse.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"wrote {out}")
    for r in rows_out:
        print(
            f"{r['window']:<12} grid={r['grid_return_jpy_pct']:8.2f}% "
            f"bh={r['bh_return_jpy_pct']:8.2f}% "
            f"gridDD={r['grid_dd_pct']:5.2f}% bhDD={r['bh_dd_pct']:5.2f}% "
            f"sharpe={r['sharpe']:5.2f} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
