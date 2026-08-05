"""
Cost sensitivity: current / 1.5x / 2x / stress for the fixed grid.

Shows how much of the gross profit is consumed by fees and slippage.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.us_grid.backtest import GridBacktester
from src.us_grid.config import CostModel, GridConfig
from src.us_grid.data import load_or_fetch


def _grid(cost: CostModel) -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name="us_fixed_grid_cost",
        market="US",
        symbols=["US.SPY", "US.QQQ", "US.IWM"],
        capital_jpy=300000.0,
        spacing_mode="fixed_pct",
        spacing_pct=1.5,
        buy_levels=3,
        sell_levels=3,
        quantity_per_level=1,
        costs=cost,
        data_dir="data/us_grid",
    )


COSTS = {
    "cost_zero": CostModel(
        commission_rate=0.0,
        minimum_commission_usd=0.0,
        maximum_commission_usd=0.0,
        spread_bps=0,
        slippage_bps=0,
        sell_regulatory_fee_enabled=False,
    ),
    "cost_current": CostModel(
        commission_rate=0.00132,
        minimum_commission_usd=0.01,
        maximum_commission_usd=22.0,
        spread_bps=5,
        slippage_bps=5,
        sell_regulatory_fee_enabled=True,
    ),
    "cost_1_5x": CostModel(
        commission_rate=0.00132 * 1.5,
        minimum_commission_usd=0.015,
        maximum_commission_usd=33.0,
        spread_bps=7.5,
        slippage_bps=7.5,
        sell_regulatory_fee_enabled=True,
    ),
    "cost_2x": CostModel(
        commission_rate=0.00132 * 2,
        minimum_commission_usd=0.02,
        maximum_commission_usd=44.0,
        spread_bps=10,
        slippage_bps=10,
        sell_regulatory_fee_enabled=True,
    ),
    "cost_stress": CostModel(
        commission_rate=0.00132 * 3,
        minimum_commission_usd=0.03,
        maximum_commission_usd=66.0,
        spread_bps=25,
        slippage_bps=25,
        sell_regulatory_fee_enabled=True,
    ),
}


def main() -> int:
    data = load_or_fetch(
        ["US.SPY", "US.QQQ", "US.IWM"],
        "2018-01-01",
        "2026-07-31",
        "data/us_grid",
        fetch=False,
    )

    rows_out: list[dict] = []
    for name, cost in COSTS.items():
        grid = _grid(cost)
        bt = GridBacktester(grid, data.bars, data.fx)
        result = bt.run("2018-01-01", "2026-07-31")
        rows_out.append(
            {
                "cost_scenario": name,
                "total_return_jpy_pct": result.total_return_pct_jpy,
                "max_drawdown_pct": result.max_drawdown_pct,
                "sharpe": result.sharpe,
                "round_trips": result.round_trip_count,
                "fees_usd": result.fee_total_usd,
                "fee_drag_pct": result.fee_drag_pct,
            }
        )

    out = Path("reports/us_grid_cost_sensitivity.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"wrote {out}")
    for r in rows_out:
        print(
            f"{r['cost_scenario']:<16} ret={r['total_return_jpy_pct']:7.2f}% "
            f"dd={r['max_drawdown_pct']:5.2f}% sharpe={r['sharpe']:5.2f} "
            f"rt={r['round_trips']:4d} fee=${r['fees_usd']:6.2f} drag={r['fee_drag_pct']:5.2f}%"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
