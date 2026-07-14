"""CLI for safe rolling-origin walk-forward validation."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backtest_evaluation import build_walk_forward_folds
from src.walk_forward_validation import (
    run_walk_forward,
    trading_days,
    validate_grid,
)
from validated_backtest import build_capital_plan, create_isolated_workspace


def _parse_float_list(value: str) -> list[float]:
    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not values:
        raise ValueError("parameter list must not be empty")
    return list(dict.fromkeys(values))


def _parse_int_list(value: str) -> list[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values:
        raise ValueError("parameter list must not be empty")
    return list(dict.fromkeys(values))


def _write_json(path: Path, payload: dict[str, Any], force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(
        "w" if force else "x",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="安全なローリング・ウォークフォワード検証"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--workspace")
    parser.add_argument("--output-json")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--initial-cash", type=float)
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--step-days", type=int, default=60)
    parser.add_argument("--score-thresholds", default="60,65,70,75,80")
    parser.add_argument("--max-positions", default="3,5,8")
    parser.add_argument(
        "--cash-reserve-ratios",
        default="0,0.2,0.3333333333",
    )
    parser.add_argument("--stop-loss-pcts", default="3,5,7,10")
    parser.add_argument("--slippage-bps", type=float, default=10)
    parser.add_argument("--stress-slippage-bps", type=float, default=20)
    parser.add_argument("--commission", type=float, default=0)
    parser.add_argument(
        "--minimum-train-closed-trades",
        type=int,
        default=10,
    )
    parser.add_argument("--minimum-folds", type=int, default=2)
    parser.add_argument(
        "--minimum-total-closed-trades",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--minimum-positive-fold-ratio",
        type=float,
        default=2.0 / 3.0,
    )
    parser.add_argument("--minimum-profit-factor", type=float, default=1.2)
    parser.add_argument("--maximum-drawdown-pct", type=float, default=20)
    parser.add_argument(
        "--maximum-top-5-profit-share-pct",
        type=float,
        default=50,
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = Path(
        args.workspace or f".runtime/walk-forward/{stamp}"
    ).resolve()
    try:
        thresholds = _parse_float_list(args.score_thresholds)
        positions = _parse_int_list(args.max_positions)
        reserves = _parse_float_list(args.cash_reserve_ratios)
        stop_losses = _parse_float_list(args.stop_loss_pcts)
        validate_grid(thresholds, positions, reserves, stop_losses)
        if args.from_date and args.to_date and args.from_date > args.to_date:
            raise ValueError("--from must be on or before --to")

        config, provenance = create_isolated_workspace(
            args.config,
            workspace,
        )
        account_cash = (
            float(args.initial_cash)
            if args.initial_cash is not None
            else build_capital_plan(config).account_initial_cash
        )
        days = trading_days(
            config.database_path,
            args.from_date,
            args.to_date,
        )
        folds = build_walk_forward_folds(
            days,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
        )
        if not folds:
            raise ValueError("not enough complete days for one fold")
        report = run_walk_forward(
            config,
            workspace=workspace,
            provenance=provenance,
            base_strategy=args.strategy,
            account_cash=account_cash,
            folds=folds,
            grids={
                "score_threshold": thresholds,
                "max_positions": positions,
                "cash_reserve_ratio": reserves,
                "stop_loss_pct": stop_losses,
            },
            slippage_bps=args.slippage_bps,
            stress_slippage_bps=args.stress_slippage_bps,
            commission=args.commission,
            minimum_train_closed_trades=(
                args.minimum_train_closed_trades
            ),
            minimum_folds=args.minimum_folds,
            minimum_total_closed_trades=(
                args.minimum_total_closed_trades
            ),
            minimum_positive_fold_ratio=(
                args.minimum_positive_fold_ratio
            ),
            minimum_profit_factor=args.minimum_profit_factor,
            maximum_drawdown_pct=args.maximum_drawdown_pct,
            maximum_top_5_profit_share_pct=(
                args.maximum_top_5_profit_share_pct
            ),
        )
        output = (
            Path(args.output_json).resolve()
            if args.output_json
            else workspace / "walk_forward_report.json"
        )
        _write_json(output, report, args.force)
        aggregate = report["aggregate"]
        print(f"status: {report['status']}")
        print(f"folds: {aggregate['fold_count']}")
        print(
            "median_cash_matched_excess_vs_JP.1306_pct: "
            f"{aggregate['median_cash_matched_excess_vs_JP.1306_pct']}"
        )
        print(
            "positive_excess_fold_ratio: "
            f"{aggregate['positive_excess_fold_ratio']:.3f}"
        )
        print(
            "stress_positive_excess_fold_ratio: "
            f"{aggregate['stress_positive_excess_fold_ratio']:.3f}"
        )
        print(f"report: {output}")
        return 0
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
    ) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
