from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.momentum_v2.adapters import SQLiteReadOnlyBarSource  # noqa: E402
from src.momentum_v2.tournament import StrategyTournament  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the V2 tournament from a read-only SQLite daily_bars table."
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Path to an existing moomoo.db; it is opened read-only.",
    )
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--benchmark", default="JP.1306")
    parser.add_argument(
        "--code",
        dest="codes",
        action="append",
        help="Restrict the universe; repeat this option.",
    )
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--max-positions", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    codes = set(args.codes) if args.codes else None
    snapshots = SQLiteReadOnlyBarSource(args.db).load_snapshots(
        args.start,
        args.end,
        codes=codes,
        benchmark_code=args.benchmark,
    )
    if not snapshots:
        raise SystemExit(
            "No complete daily_bars snapshots found for the requested range."
        )
    rows = StrategyTournament(
        initial_cash=args.initial_cash,
        max_positions=args.max_positions,
        benchmark_code=args.benchmark,
    ).run(snapshots)
    print(f"source_db={args.db.resolve()} snapshots={len(snapshots)}")
    print(
        "strategy,cagr,excess_cagr,sharpe,sortino,max_drawdown,calmar,turnover,exposure"
    )
    for row in rows:
        metrics = row.metrics
        print(
            f"{row.strategy},"
            f"{metrics['cagr']:.6f},{metrics['excess_cagr']:.6f},"
            f"{metrics['sharpe']:.6f},{metrics['sortino']:.6f},"
            f"{metrics['max_drawdown']:.6f},{metrics['calmar']:.6f},"
            f"{metrics['turnover']:.6f},{metrics['exposure']:.6f}"
        )


if __name__ == "__main__":
    main()
