"""Re-run the documented P2 periods with corrected benchmark roles.

The command works on the configured SQLite database. It first initializes the
corporate-action schema and seeds configured actions, then executes all three
strategies for the four documented P2 windows and writes a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from src.backtest_runner import BacktestRunner
from src.benchmarking import (
    ensure_benchmark_schema,
    load_run_benchmark_results,
    scan_data_quality_flags,
    seed_configured_actions,
)
from src.config import load_config


PERIODS = {
    "A": ("2026-05-21", "2026-06-30"),
    "B": ("2026-01-01", "2026-03-31"),
    "C": ("2026-04-01", "2026-06-30"),
    "D": ("2026-01-01", "2026-06-30"),
}
STRATEGIES = ("momentum", "quality_low_risk", "etf_rotation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    config = load_config(args.config)

    with sqlite3.connect(config.database_path) as connection:
        ensure_benchmark_schema(connection)
        seeded = seed_configured_actions(connection, config)
        flagged = scan_data_quality_flags(connection, "JP.2559")

    results: list[dict] = []
    for label, (start, end) in PERIODS.items():
        for strategy in STRATEGIES:
            runner = BacktestRunner(config)
            run_id = runner.run(strategy, start, end)
            with sqlite3.connect(config.database_path) as connection:
                connection.row_factory = sqlite3.Row
                run = connection.execute(
                    "SELECT strategy_name, total_return_pct, max_drawdown_pct, "
                    "trade_count FROM backtest_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                benchmarks = load_run_benchmark_results(connection, run_id)
            results.append(
                {
                    "period": label,
                    "start_date": start,
                    "end_date": end,
                    "run_id": run_id,
                    "strategy": strategy,
                    "total_return_pct": run["total_return_pct"],
                    "max_drawdown_pct": run["max_drawdown_pct"],
                    "trade_count": run["trade_count"],
                    "benchmarks": benchmarks,
                }
            )

    output = (
        Path(args.output)
        if args.output
        else Path("reports") / f"p2_benchmark_rerun_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "corporate_actions_seeded": seeded,
                "new_data_quality_flags": flagged,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
