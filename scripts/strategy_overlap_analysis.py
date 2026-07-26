#!/usr/bin/env python3
"""2つのバックテストrunの戦略重複度を比較するCLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monthly_attribution import BacktestRun, resolve_backtest_run  # noqa: E402
from src.strategy_overlap import (  # noqa: E402
    calculate_strategy_overlap,
    load_backtest_equity,
    load_backtest_fills,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "2つのbacktest runを約定銘柄、entry、日末保有、equity returnで比較します"
        )
    )
    parser.add_argument(
        "--db",
        default="data/moomoo.db",
        help="SQLite DB path (default: data/moomoo.db)",
    )
    parser.add_argument("--run-a", type=int, help="比較Aのbacktest_runs.id")
    parser.add_argument(
        "--strategy-a",
        default="momentum",
        help="--run-a未指定時に使用する最新strategy (default: momentum)",
    )
    parser.add_argument("--run-b", type=int, help="比較Bのbacktest_runs.id")
    parser.add_argument(
        "--strategy-b",
        default="quality_low_risk",
        help=(
            "--run-b未指定時に使用する最新strategy "
            "(default: quality_low_risk)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="CSV output directory (default: reports)",
    )
    return parser


def _resolve(
    db_path: Path,
    run_id: int | None,
    strategy_name: str,
) -> BacktestRun:
    if run_id is not None:
        return resolve_backtest_run(db_path, run_id=run_id)
    return resolve_backtest_run(db_path, strategy_name=strategy_name)


def main() -> int:
    args = _build_parser().parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DBが見つかりません: {db_path}")

    run_a = _resolve(db_path, args.run_a, args.strategy_a)
    run_b = _resolve(db_path, args.run_b, args.strategy_b)
    if run_a.run_id == run_b.run_id:
        raise SystemExit("同一run同士は比較できません")

    result = calculate_strategy_overlap(
        load_backtest_fills(db_path, run_a.run_id),
        load_backtest_fills(db_path, run_b.run_id),
        load_backtest_equity(db_path, run_a.run_id),
        load_backtest_equity(db_path, run_b.run_id),
    )

    summary = result.summary.copy()
    summary.insert(0, "run_a", run_a.run_id)
    summary.insert(1, "strategy_a", run_a.strategy_name)
    summary.insert(2, "run_b", run_b.run_id)
    summary.insert(3, "strategy_b", run_b.strategy_name)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"run_{run_a.run_id}_vs_{run_b.run_id}"
    paths = {
        "summary": output_dir / f"strategy_overlap_{stem}_summary.csv",
        "daily": output_dir / f"strategy_overlap_{stem}_daily.csv",
        "symbols": output_dir / f"strategy_overlap_{stem}_symbols.csv",
        "entries": output_dir / f"strategy_overlap_{stem}_entries.csv",
    }
    summary.to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    result.daily.to_csv(paths["daily"], index=False, encoding="utf-8-sig")
    result.symbols.to_csv(paths["symbols"], index=False, encoding="utf-8-sig")
    result.entries.to_csv(paths["entries"], index=False, encoding="utf-8-sig")

    print(
        f"A: run={run_a.run_id} strategy={run_a.strategy_name} "
        f"period={run_a.start_date}..{run_a.end_date}"
    )
    print(
        f"B: run={run_b.run_id} strategy={run_b.strategy_name} "
        f"period={run_b.start_date}..{run_b.end_date}"
    )
    print(summary.transpose().to_string(header=False))
    for label, path in paths.items():
        print(f"{label}_csv={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
