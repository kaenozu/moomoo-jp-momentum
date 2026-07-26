#!/usr/bin/env python3
"""永続化済みバックテスト2戦略の重複分析CLI。"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monthly_attribution import BacktestRun, resolve_backtest_run  # noqa: E402
from src.strategy_overlap import (  # noqa: E402
    calculate_strategy_overlap,
    load_equity_points,
    load_fill_events,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="2つのバックテスト戦略の売買・保有・収益系列の重複を分析します"
    )
    parser.add_argument("--db", default="data/moomoo.db")
    parser.add_argument("--run-a-id", type=int)
    parser.add_argument("--run-b-id", type=int)
    parser.add_argument("--strategy-a", default="momentum")
    parser.add_argument("--strategy-b", default="quality_low_risk")
    parser.add_argument("--output-dir", default="reports/strategy-overlap")
    return parser


def _resolve_pair(args: argparse.Namespace) -> tuple[BacktestRun, BacktestRun]:
    if args.run_a_id is not None or args.run_b_id is not None:
        if args.run_a_id is None or args.run_b_id is None:
            raise ValueError("--run-a-id と --run-b-id を両方指定してください")
        pair = (
            resolve_backtest_run(args.db, run_id=args.run_a_id),
            resolve_backtest_run(args.db, run_id=args.run_b_id),
        )
    else:
        pair = (
            resolve_backtest_run(args.db, strategy_name=args.strategy_a),
            resolve_backtest_run(args.db, strategy_name=args.strategy_b),
        )
    if pair[0].run_id == pair[1].run_id:
        raise ValueError("同一run同士は比較できません")
    return pair


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _format(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def main() -> int:
    args = _build_parser().parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DBが見つかりません: {db_path}")

    try:
        run_a, run_b = _resolve_pair(args)
        result = calculate_strategy_overlap(
            load_equity_points(db_path, run_a.run_id),
            load_equity_points(db_path, run_b.run_id),
            load_fill_events(db_path, run_a.run_id),
            load_fill_events(db_path, run_b.run_id),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    summary = result.summary.to_dict()
    summary.update(
        {
            "run_a_id": run_a.run_id,
            "strategy_a": run_a.strategy_name,
            "run_b_id": run_b.run_id,
            "strategy_b": run_b.strategy_name,
        }
    )
    output_dir = Path(args.output_dir)
    stem = f"run_{run_a.run_id}_vs_{run_b.run_id}"
    _write_csv(output_dir / f"summary_{stem}.csv", [summary])
    _write_csv(output_dir / f"daily_overlap_{stem}.csv", result.daily_rows)
    _write_csv(output_dir / f"symbol_overlap_{stem}.csv", result.symbol_rows)
    _write_csv(output_dir / f"entry_overlap_{stem}.csv", result.entry_rows)

    print(
        f"A=run:{run_a.run_id}/{run_a.strategy_name} "
        f"B=run:{run_b.run_id}/{run_b.strategy_name}"
    )
    print(
        f"period={result.summary.overlap_start_date}.."
        f"{result.summary.overlap_end_date} "
        f"days={result.summary.aligned_return_days}"
    )
    print(
        "daily_return_correlation="
        f"{_format(result.summary.daily_return_correlation)} "
        "exact_entry_jaccard_pct="
        f"{_format(result.summary.exact_entry_jaccard_pct)} "
        "avg_holdings_jaccard_pct="
        f"{_format(result.summary.avg_holdings_jaccard_pct)} "
        "avg_overlap_coefficient_pct="
        f"{_format(result.summary.avg_holdings_overlap_coefficient_pct)}"
    )
    print(
        "return_pct: "
        f"A={result.summary.strategy_a_return_pct:.2f} "
        f"B={result.summary.strategy_b_return_pct:.2f} "
        f"50/50={result.summary.combined_50_50_return_pct:.2f}"
    )
    print(
        "max_drawdown_pct: "
        f"A={result.summary.strategy_a_max_drawdown_pct:.2f} "
        f"B={result.summary.strategy_b_max_drawdown_pct:.2f} "
        f"50/50={result.summary.combined_50_50_max_drawdown_pct:.2f}"
    )
    print(f"output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
