"""Export daily holdings-implied and realized beta diagnostics for a backtest.

Examples:
    python scripts/daily_beta_decomposition.py \
        --from 2026-04-01 --to 2026-04-30 --strategy momentum --csv
    python scripts/daily_beta_decomposition.py \
        --run-id 156 --from 2026-04-01 --to 2026-04-30 --strategy momentum --csv
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from scripts.beta_analysis_common import (  # noqa: E402
    DEFAULT_BENCHMARK_CODE,
    AnalysisError,
    build_daily_beta_decomposition,
    connect_readonly,
    safe_slug,
    select_run,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="日次の保有β・実現β・エントリー/イグジット・入替率を出力します。"
    )
    parser.add_argument(
        "--from", dest="start_date", required=True, help="開始日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--to", dest="end_date", required=True, help="終了日 YYYY-MM-DD"
    )
    parser.add_argument("--strategy", default="momentum", help="戦略名")
    parser.add_argument(
        "--run-id", type=int, help="対象backtest run ID。省略時は期間を覆う最新run"
    )
    parser.add_argument("--config", default="config.yaml", help="設定ファイル")
    parser.add_argument(
        "--benchmark-code",
        default=DEFAULT_BENCHMARK_CODE,
        help="β計算・realized beta分母のベンチマーク",
    )
    parser.add_argument(
        "--beta-lookback", type=int, default=60, help="銘柄β推定の営業日数"
    )
    parser.add_argument(
        "--min-beta-observations",
        type=int,
        default=20,
        help="β推定に必要な最小リターン観測数",
    )
    parser.add_argument("--csv", action="store_true", help="reports配下へCSVを保存")
    parser.add_argument("--output", help="CSV出力パス。--csvを暗黙に有効化")
    parser.add_argument(
        "--limit", type=int, default=0, help="コンソール表示行数。0は全件"
    )
    return parser


def _display(frame: pd.DataFrame, limit: int) -> None:
    columns = [
        "date",
        "holdings_implied_beta",
        "realized_beta_daily",
        "current_position_count",
        "new_entry_count",
        "exit_count",
        "churn_today",
        "beta_coverage_pct",
    ]
    display = frame[columns].copy()
    for column in ("holdings_implied_beta", "realized_beta_daily"):
        display[column] = display[column].map(
            lambda value: (
                f"{value:.3f}" if pd.notna(value) and math.isfinite(value) else "-"
            )
        )
    for column in ("churn_today", "beta_coverage_pct"):
        display[column] = display[column].map(
            lambda value: (
                f"{value:.1f}%" if pd.notna(value) and math.isfinite(value) else "-"
            )
        )
    if limit > 0:
        display = display.head(limit)
    print(display.to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.start_date > args.end_date:
        raise SystemExit("--from must be on or before --to")
    if args.beta_lookback < 2:
        raise SystemExit("--beta-lookback must be >= 2")
    if args.min_beta_observations < 2:
        raise SystemExit("--min-beta-observations must be >= 2")

    config = load_config(args.config)
    try:
        with connect_readonly(config.database_path) as conn:
            run_info = select_run(
                conn,
                strategy_name=args.strategy,
                start_date=args.start_date,
                end_date=args.end_date,
                run_id=args.run_id,
            )
            frame = build_daily_beta_decomposition(
                conn,
                run_info=run_info,
                start_date=args.start_date,
                end_date=args.end_date,
                benchmark_code=args.benchmark_code,
                beta_lookback_days=args.beta_lookback,
                min_beta_observations=args.min_beta_observations,
            )
    except (AnalysisError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(
        f"run_id={run_info.run_id} strategy={run_info.strategy_name} "
        f"period={args.start_date}..{args.end_date} benchmark={args.benchmark_code}"
    )
    _display(frame, args.limit)

    if args.csv or args.output:
        output = (
            Path(args.output)
            if args.output
            else Path("reports")
            / (
                "daily_beta_decomposition_"
                f"{safe_slug(args.strategy)}_{args.start_date}_{args.end_date}_run{run_info.run_id}.csv"
            )
        )
        write_csv(frame, output)
        print(f"[OK] CSV: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
