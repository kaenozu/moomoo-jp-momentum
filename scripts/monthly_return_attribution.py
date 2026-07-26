#!/usr/bin/env python3
"""永続化済みバックテストの月別超過リターン寄与度分析CLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monthly_attribution import (  # noqa: E402
    calculate_monthly_attribution,
    load_backtest_equity_curve,
    normalize_benchmark_code,
    resolve_backtest_run,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "バックテストの月別超過リターンをcash dragと残差へ分解します"
        )
    )
    parser.add_argument(
        "--db",
        default="data/moomoo.db",
        help="SQLite DB path (default: data/moomoo.db)",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", type=int, help="backtest_runs.id")
    target.add_argument(
        "--strategy",
        help="指定strategyの最新backtest runを使用",
    )
    parser.add_argument(
        "--benchmark",
        default="1306",
        choices=["1306", "2559"],
        help="比較benchmark (default: 1306)",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="CSV output directory (default: reports)",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DBが見つかりません: {db_path}")

    run = resolve_backtest_run(
        db_path,
        run_id=args.run_id,
        strategy_name=args.strategy,
    )
    benchmark = normalize_benchmark_code(args.benchmark)
    equity_curve = load_backtest_equity_curve(
        db_path,
        run.run_id,
        benchmark,
    )
    result = calculate_monthly_attribution(equity_curve)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"run_{run.run_id}_{benchmark}"
    daily_path = output_dir / f"daily_attribution_{stem}.csv"
    monthly_path = output_dir / f"monthly_attribution_{stem}.csv"
    result.daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    result.monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")

    print(
        f"run={run.run_id} strategy={run.strategy_name} "
        f"period={run.start_date}..{run.end_date} benchmark=JP.{benchmark}"
    )
    print(
        result.monthly.to_string(
            index=False,
            float_format=lambda value: f"{value:.4f}",
        )
    )
    print(f"daily_csv={daily_path}")
    print(f"monthly_csv={monthly_path}")
    print(
        "residual_effectには銘柄選択、タイミング、執行コスト、"
        "cashに組み込まれたidle-cash overlayが含まれます。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
