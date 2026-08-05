"""
Core+grid hybrid: high core allocations (60-90%) with a small grid overlay.

Tests whether a B&H-like return with reduced drawdown is achievable, which
would be the only defensible reason to run a grid at all.
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


def _grid(core_pct: float) -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name=f"us_core{core_pct}_grid_v1",
        market="US",
        symbols=["US.SPY", "US.QQQ", "US.IWM"],
        capital_jpy=300000.0,
        spacing_mode="fixed_pct",
        spacing_pct=1.5,
        buy_levels=3,
        sell_levels=3,
        quantity_per_level=1,
        core_allocation_pct=core_pct,
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
    calendar = sorted(
        {
            b.date
            for bars in bars_by_code.values()
            for b in bars
            if "2018-01-01" <= b.date <= "2026-07-31"
        }
    )
    fx_rates = {row["date"]: float(row["rate"]) for row in data.fx}

    rows_out: list[dict] = []
    for core in (0, 20, 30, 40, 50, 60, 70, 80, 90):
        grid = _grid(core)
        bt = GridBacktester(grid, data.bars, data.fx)
        result = bt.run("2018-01-01", "2026-07-31")
        bh = buy_and_hold(
            grid, bars_by_code, fx_rates, "2018-01-01", "2026-07-31", calendar
        )
        verdict = evaluate_verdict(grid, result, bh)
        rows_out.append(
            {
                "core_pct": core,
                "total_return_jpy_pct": result.total_return_pct_jpy,
                "max_drawdown_pct": result.max_drawdown_pct,
                "sharpe": result.sharpe,
                "bh_return_jpy_pct": bh.total_return_pct_jpy,
                "verdict": verdict.label,
            }
        )

    out = Path("reports/us_grid_core_hybrid.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"wrote {out}")
    for r in rows_out:
        print(
            f"core={r['core_pct']:>3}%  ret={r['total_return_jpy_pct']:7.2f}% "
            f"bh={r['bh_return_jpy_pct']:7.2f}% dd={r['max_drawdown_pct']:5.2f}% "
            f"sharpe={r['sharpe']:5.2f} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
