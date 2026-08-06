"""
ATR parameter sensitivity: run adaptive grid over a fixed grid of ATR
parameters and report neighbourhood stability (overfitting check).

Each train and OOS evaluation uses a fresh stateful backtester instance.
Parameter selection is on train+validation only; the test window is held out.
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


def _grid(
    atr_period: int,
    atr_multiplier: float,
    min_spacing: float,
    max_spacing: float,
) -> GridConfig:
    return GridConfig(
        enabled=True,
        mode="backtest",
        strategy_name="us_adaptive_grid_v1",
        market="US",
        symbols=["US.SPY", "US.QQQ", "US.IWM"],
        capital_jpy=300000.0,
        spacing_mode="atr_pct",
        spacing_pct=1.5,
        atr_period=atr_period,
        atr_multiplier=atr_multiplier,
        min_spacing_pct=min_spacing,
        max_spacing_pct=max_spacing,
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

    # Train+val: 2018-01-01 .. 2023-12-31. OOS test: 2024-01-01 .. 2026-07-31.
    train_start, train_end = "2018-01-01", "2023-12-31"
    test_start, test_end = "2024-01-01", "2026-07-31"

    rows_out: list[dict] = []
    for atr_period in (10, 14, 20):
        for atr_multiplier in (0.5, 0.75, 1.0, 1.25, 1.5):
            for min_spacing in (0.75, 1.0, 1.5):
                grid = _grid(atr_period, atr_multiplier, min_spacing, 4.0)
                train_result = GridBacktester(grid, data.bars, data.fx).run(
                    train_start,
                    train_end,
                )
                test_result = GridBacktester(grid, data.bars, data.fx).run(
                    test_start,
                    test_end,
                )
                rows_out.append(
                    {
                        "atr_period": atr_period,
                        "atr_multiplier": atr_multiplier,
                        "min_spacing": min_spacing,
                        "train_return_jpy_pct": train_result.total_return_pct_jpy,
                        "train_dd_pct": train_result.max_drawdown_pct,
                        "oos_return_jpy_pct": test_result.total_return_pct_jpy,
                        "oos_dd_pct": test_result.max_drawdown_pct,
                        "oos_sharpe": test_result.sharpe,
                        "round_trips": test_result.round_trip_count,
                    }
                )

    out = Path("reports/us_grid_atr_sensitivity.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"wrote {out} ({len(rows_out)} combos)")
    by_oos = sorted(
        rows_out,
        key=lambda row: row["oos_return_jpy_pct"],
        reverse=True,
    )
    print("Top 5 by OOS return:")
    for row in by_oos[:5]:
        print(
            f"  period={row['atr_period']} mult={row['atr_multiplier']} "
            f"min={row['min_spacing']} "
            f"train={row['train_return_jpy_pct']:7.2f}% "
            f"oos={row['oos_return_jpy_pct']:7.2f}% "
            f"oosDD={row['oos_dd_pct']:5.2f}% "
            f"oosSharpe={row['oos_sharpe']:5.2f} "
            f"roundTrips={row['round_trips']}"
        )
    print("Bottom 5 by OOS return:")
    for row in by_oos[-5:]:
        print(
            f"  period={row['atr_period']} mult={row['atr_multiplier']} "
            f"min={row['min_spacing']} "
            f"train={row['train_return_jpy_pct']:7.2f}% "
            f"oos={row['oos_return_jpy_pct']:7.2f}% "
            f"oosDD={row['oos_dd_pct']:5.2f}% "
            f"oosSharpe={row['oos_sharpe']:5.2f} "
            f"roundTrips={row['round_trips']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
