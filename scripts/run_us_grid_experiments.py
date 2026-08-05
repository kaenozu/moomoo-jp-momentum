"""
Experiment driver for the US grid research.

Runs the configured strategy variants over the cached data and writes a
comparison CSV. This script is deliberately run-only (no fetching); data must
already be cached by run_us_grid_backtest.py --fetch-data.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.us_grid.backtest import GridBacktester
from src.us_grid.benchmark import buy_and_hold, cash_benchmark
from src.us_grid.config import CostModel, GridConfig
from src.us_grid.data import attach_corporate_actions, load_or_fetch
from src.us_grid.fills import Bar, bar_from_dict
from src.us_grid.metrics import evaluate_verdict
from src.us_grid.reporting import write_report
from src.us_grid.manifest import build_manifest, save_manifest


def base_config() -> GridConfig:
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
            commission_mode="percentage",
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
            maximum_commission_usd=22.0,
            spread_bps=5,
            slippage_bps=5,
            sell_regulatory_fee_enabled=True,
        ),
        data_dir="data/us_grid",
    )


def run_variant(grid: GridConfig, data, start: str, end: str) -> dict:
    backtester = GridBacktester(grid, data.bars, data.fx)
    backtester._corporate_actions = attach_corporate_actions(data)
    result = backtester.run(start, end)

    bars_by_code = {
        code: [bar_from_dict(row) for row in rows] for code, rows in data.bars.items()
    }
    calendar = sorted(
        {
            bar.date
            for bars in bars_by_code.values()
            for bar in bars
            if start <= bar.date <= end
        }
    )
    fx_rates = {row["date"]: float(row["rate"]) for row in data.fx}
    bh = buy_and_hold(grid, bars_by_code, fx_rates, start, end, calendar)
    cash = cash_benchmark(grid, calendar)
    verdict = evaluate_verdict(grid, result, bh)

    return {
        "strategy": grid.strategy_name,
        "spacing_mode": grid.spacing_mode,
        "regime": grid.regime_filter_enabled,
        "core_pct": grid.core_allocation_pct,
        "capital_jpy": grid.capital_jpy,
        "symbols": "|".join(result.symbols),
        "start": start,
        "end": end,
        "total_return_jpy_pct": result.total_return_pct_jpy,
        "total_return_usd_pct": result.total_return_pct_usd,
        "cagr_pct": result.cagr_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "trades": result.trade_count,
        "round_trips": result.round_trip_count,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "fees_usd": result.fee_total_usd,
        "dividends_usd": result.dividend_income_usd,
        "bh_return_jpy_pct": bh.total_return_pct_jpy,
        "bh_cagr_pct": bh.cagr_pct,
        "bh_max_drawdown_pct": bh.max_drawdown_pct,
        "verdict": verdict.label,
        "verdict_reasons": "; ".join(verdict.reasons),
    }


def main() -> int:
    start = "2018-01-01"
    end = "2026-07-31"

    data = load_or_fetch(
        ["US.SPY", "US.QQQ", "US.IWM", "US.DIA", "US.TLT"],
        start,
        end,
        "data/us_grid",
        fetch=False,
    )

    variants = []
    base = base_config()

    # Experiment 1: fixed grid baseline (SPY/QQQ/IWM)
    variants.append(run_variant(base, data, start, end))

    # Experiment 4: core + grid (20% / 30% / 40%)
    for core in (20, 30, 40):
        g = base_config()
        g.core_allocation_pct = core
        g.strategy_name = f"us_core{core}_grid_v1"
        variants.append(run_variant(g, data, start, end))

    # Experiment 2: adaptive grid (ATR spacing)
    g = base_config()
    g.spacing_mode = "atr_pct"
    g.atr_period = 14
    g.atr_multiplier = 1.0
    g.min_spacing_pct = 0.75
    g.max_spacing_pct = 4.0
    g.strategy_name = "us_adaptive_grid_v1"
    variants.append(run_variant(g, data, start, end))

    # Experiment 3: regime-gated adaptive grid
    g = base_config()
    g.spacing_mode = "atr_pct"
    g.regime_filter_enabled = True
    g.strategy_name = "us_regime_adaptive_grid_v1"
    variants.append(run_variant(g, data, start, end))

    # Experiment 5: cost stress (2x)
    g = base_config()
    g.costs = CostModel(
        commission_rate=0.00132 * 2,
        minimum_commission_usd=0.02,
        maximum_commission_usd=44.0,
        spread_bps=10,
        slippage_bps=10,
        sell_regulatory_fee_enabled=True,
    )
    g.strategy_name = "us_fixed_grid_cost2x_v1"
    variants.append(run_variant(g, data, start, end))

    out_path = Path("reports/us_grid_comparison.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(variants[0].keys()))
        writer.writeheader()
        writer.writerows(variants)

    print(f"wrote {out_path} ({len(variants)} variants)")
    for v in variants:
        print(
            f"{v['strategy']:<28} ret={v['total_return_jpy_pct']:7.2f}% "
            f"bh={v['bh_return_jpy_pct']:7.2f}% dd={v['max_drawdown_pct']:5.2f}% "
            f"sharpe={v['sharpe']:5.2f} {v['verdict']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
