"""
Holdings Beta Floor 有効/無効の比較バックテスト。

使用例:
    python compare_beta_floor.py --from 2026-01-01 --to 2026-06-30 --strategy momentum
    python compare_beta_floor.py --from 2026-01-01 --to 2026-06-30 --strategy all --csv
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.backtest_runner import BacktestRunner
from src.config import Config, load_config
from src.strategies import StrategyRegistry


def _load_run(config: Config, run_id: int) -> dict[str, Any]:
    with sqlite3.connect(str(config.database_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"backtest_runs が見つかりません: run_id={run_id}")
    return dict(row)


def _result_row(
    strategy_name: str,
    mode: str,
    run: dict[str, Any],
    *,
    trigger_days: int = 0,
    missing_beta_days: int = 0,
    min_holdings_beta: float | None = None,
) -> dict[str, Any]:
    return {
        "strategy_name": strategy_name,
        "beta_floor": mode,
        "run_id": run["id"],
        "start_date": run["start_date"],
        "end_date": run["end_date"],
        "final_equity": run["final_equity"],
        "total_return_pct": run["total_return_pct"],
        "max_drawdown_pct": run["max_drawdown_pct"],
        "trade_count": run["trade_count"],
        "benchmark_2559_return": run["benchmark_2559_return"],
        "excess_vs_2559": run["excess_vs_2559"],
        "benchmark_1306_return": run["benchmark_1306_return"],
        "excess_vs_1306": run["excess_vs_1306"],
        "beta_floor_trigger_days": trigger_days,
        "missing_beta_days": missing_beta_days,
        "min_holdings_implied_beta": min_holdings_beta,
    }


def _print_comparison(strategy_name: str, off: dict[str, Any], on: dict[str, Any]) -> None:
    return_diff = float(on["total_return_pct"]) - float(off["total_return_pct"])
    drawdown_diff = float(on["max_drawdown_pct"]) - float(off["max_drawdown_pct"])

    print("\n" + "=" * 72)
    print(f"Holdings Beta Floor 比較: {strategy_name}")
    print("=" * 72)
    print(
        f"  OFF: return={off['total_return_pct']:.2f}%  "
        f"MDD={off['max_drawdown_pct']:.2f}%  trades={off['trade_count']}"
    )
    print(
        f"   ON: return={on['total_return_pct']:.2f}%  "
        f"MDD={on['max_drawdown_pct']:.2f}%  trades={on['trade_count']}  "
        f"trigger_days={on['beta_floor_trigger_days']}"
    )
    print(f"  差分: return={return_diff:+.2f}pt  MDD={drawdown_diff:+.2f}pt")
    if on["missing_beta_days"]:
        print(
            "  注意: β履歴不足でフェイルオープンした日数="
            f"{on['missing_beta_days']}"
        )


def run_comparison(
    config: Config,
    strategy_name: str,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_runner = BacktestRunner(config, beta_floor_enabled=False)
    baseline_run_id = baseline_runner.run(strategy_name, start_date, end_date)
    baseline = _result_row(
        strategy_name,
        "off",
        _load_run(config, baseline_run_id),
    )

    beta_runner = BacktestRunner(config, beta_floor_enabled=True)
    beta_run_id = beta_runner.run(strategy_name, start_date, end_date)
    enabled = _result_row(
        strategy_name,
        "on",
        _load_run(config, beta_run_id),
        trigger_days=beta_runner.beta_floor_trigger_days,
        missing_beta_days=beta_runner.beta_floor_missing_days,
        min_holdings_beta=beta_runner.beta_floor_min_beta,
    )
    return baseline, enabled


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Holdings Beta Floor 有効/無効の比較バックテスト"
    )
    parser.add_argument("--from", dest="from_date", default="2026-01-01")
    parser.add_argument("--to", dest="to_date", default="2026-06-30")
    parser.add_argument("--strategy", default="momentum", help="戦略名またはall")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--csv", action="store_true")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 1

    floor = float(config.get("risk_controls.min_portfolio_beta", 0.0) or 0.0)
    lookback = int(
        config.get("risk_controls.min_portfolio_beta_holdings_lookback", 60)
    )
    if floor <= 0.0:
        print("[ERROR] risk_controls.min_portfolio_beta は0より大きい値が必要です")
        return 1

    print(
        f"設定: min_portfolio_beta={floor:.2f}, lookback={lookback}, "
        f"benchmark=JP.2559"
    )

    strategies = (
        StrategyRegistry.list_names()
        if args.strategy == "all"
        else [args.strategy]
    )
    rows: list[dict[str, Any]] = []

    for strategy_name in strategies:
        off, on = run_comparison(
            config,
            strategy_name,
            args.from_date,
            args.to_date,
        )
        rows.extend([off, on])
        _print_comparison(strategy_name, off, on)

    if args.csv:
        output_dir = Path("reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / (
            "backtest_beta_floor_comparison_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        pd.DataFrame(rows).to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"\n[OK] {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
