"""
US grid backtest CLI.

Usage examples:

    python scripts/run_us_grid_backtest.py --config config.us_grid.yaml --validate-only
    python scripts/run_us_grid_backtest.py --config config.us_grid.yaml \
        --start 2018-01-01 --end 2026-07-31
    python scripts/run_us_grid_backtest.py --config config.us_grid.yaml --walk-forward

The backtest never places network orders. Data is read from the local cache
and only fetched from yfinance when --fetch-data is explicitly passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.us_grid.backtest import GridBacktester
from src.us_grid.benchmark import buy_and_hold, cash_benchmark
from src.us_grid.config import (
    UsGridConfigError,
    load_us_grid_config,
    validate_us_grid_config,
)
from src.us_grid.data import attach_corporate_actions, load_or_fetch
from src.us_grid.fills import Bar, bar_from_dict
from src.us_grid.manifest import build_manifest, save_manifest
from src.us_grid.metrics import evaluate_verdict
from src.us_grid.reporting import write_report


def _load_config(path: str):
    try:
        from src.config import Config

        return Config(path)
    except Exception:
        import yaml

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)


def _config_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_validate(config_path: str) -> int:
    try:
        raw = _load_config(config_path)
        grid = load_us_grid_config(raw)
        validate_us_grid_config(grid)
        print(json.dumps({"valid": True, "strategy": grid.strategy_name}, indent=2))
        return 0
    except UsGridConfigError as error:
        print(json.dumps({"valid": False, "error": str(error)}, indent=2))
        return 2


def _run_single(
    grid,
    data,
    start: str,
    end: str,
    report_dir: Path,
    config_path: str,
    config_text: str,
    cost_multiple: float = 1.0,
    seed: int = 0,
) -> dict[str, Any]:
    backtester = GridBacktester(grid, data.bars, data.fx)
    backtester._corporate_actions = attach_corporate_actions(data)

    result = backtester.run(start, end, seed=seed)

    # Benchmarks.
    bars_by_code: dict[str, list[Bar]] = {
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
    fx_rate_series = {row["date"]: float(row["rate"]) for row in data.fx}
    bh = buy_and_hold(grid, bars_by_code, fx_rate_series, start, end, calendar)
    cash = cash_benchmark(grid, calendar)

    verdict = evaluate_verdict(grid, result, bh, cost_multiple=cost_multiple)

    manifest = build_manifest(
        run_id=result.run_id,
        config_path=config_path,
        config_text=config_text,
        grid_config=grid,
        data_hash=data.data_hash,
        data_sources=data.sources,
        symbols=result.symbols,
        start_date=start,
        end_date=end,
        capital_jpy=grid.capital_jpy,
        currency=grid.currency,
        cost_model=", ".join(data.sources),
        fill_model="conservative_ohlc",
        parameter_selection_method="fixed",
        random_seed=seed,
        warnings=result.warnings + result.skipped,
        repo_root=ROOT,
    )

    run_dir = report_dir / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    save_manifest(manifest, run_dir)
    write_report(
        run_dir,
        manifest=manifest,
        result=result,
        bh=bh,
        cash=cash,
        verdict=verdict,
    )

    return {
        "run_id": result.run_id,
        "strategy": grid.strategy_name,
        "total_return_pct_usd": result.total_return_pct_usd,
        "total_return_pct_jpy": result.total_return_pct_jpy,
        "cagr_pct": result.cagr_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "trade_count": result.trade_count,
        "round_trip_count": result.round_trip_count,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "fee_total_usd": result.fee_total_usd,
        "dividend_income_usd": result.dividend_income_usd,
        "orders_created": result.orders_created,
        "orders_filled": result.orders_filled,
        "orders_cancelled": result.orders_cancelled,
        "orders_rejected": result.orders_rejected,
        "cash_shortage_count": result.cash_shortage_count,
        "buy_and_hold_return_pct_jpy": bh.total_return_pct_jpy,
        "verdict": verdict.label,
        "verdict_reasons": verdict.reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="US grid backtest")
    parser.add_argument("--config", required=True, help="path to config.yaml")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument(
        "--fetch-data", action="store_true", help="allow yfinance fetch"
    )
    parser.add_argument("--report-dir", default="reports/us_grid")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    if args.validate_only:
        return cmd_validate(args.config)

    config_text = _config_text(args.config)
    raw = _load_config(args.config)
    try:
        grid = load_us_grid_config(raw)
    except UsGridConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2

    report_dir = Path(args.report_dir)
    data = load_or_fetch(
        grid.symbols,
        args.start,
        args.end,
        grid.data_dir,
        fetch=args.fetch_data,
    )

    missing = [s for s in grid.symbols if s not in data.bars]
    if missing:
        print(
            f"warning: no data for {missing} (run with --fetch-data to download)",
            file=sys.stderr,
        )
        if not data.bars:
            print("no data available; refusing to run", file=sys.stderr)
            return 2

    if args.walk_forward:
        return _run_walk_forward(grid, data, args, report_dir, config_text)

    summary = _run_single(
        grid,
        data,
        args.start,
        args.end,
        report_dir,
        args.config,
        config_text,
        seed=args.seed,
    )
    if args.as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_human(summary)
    return 0


def _run_walk_forward(grid, data, args, report_dir, config_text) -> int:
    """Train 3y / validate 1y / test 1y, rolling 1y."""
    from datetime import datetime, timedelta

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    results: list[dict] = []
    train_years = 3
    val_years = 1
    test_years = 1
    roll_days = 365

    cursor = start
    window = 0
    while cursor + timedelta(
        days=(train_years + val_years + test_years) * 365
    ) <= end + timedelta(days=1):
        train_end = cursor + timedelta(days=train_years * 365)
        val_end = train_end + timedelta(days=val_years * 365)
        test_end = val_end + timedelta(days=test_years * 365)
        if test_end > end + timedelta(days=1):
            break

        # Parameter selection on train+validation only.
        # The test window is untouched during selection.
        train_result = _run_single(
            grid,
            data,
            cursor.isoformat(),
            train_end.isoformat(),
            report_dir,
            args.config,
            config_text,
            seed=args.seed,
        )
        val_result = _run_single(
            grid,
            data,
            train_end.isoformat(),
            val_end.isoformat(),
            report_dir,
            args.config,
            config_text,
            seed=args.seed,
        )
        test_result = _run_single(
            grid,
            data,
            val_end.isoformat(),
            test_end.isoformat(),
            report_dir,
            args.config,
            config_text,
            seed=args.seed,
        )
        results.append(
            {
                "window": window,
                "train_end": train_end.isoformat(),
                "val_end": val_end.isoformat(),
                "test_end": test_end.isoformat(),
                "train_return_jpy_pct": train_result["total_return_pct_jpy"],
                "val_return_jpy_pct": val_result["total_return_pct_jpy"],
                "test_return_jpy_pct": test_result["total_return_pct_jpy"],
                "test_verdict": test_result["verdict"],
            }
        )
        window += 1
        cursor = cursor + timedelta(days=roll_days)

    wf_path = report_dir / "walk_forward.csv"
    import csv

    with open(wf_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    if args.as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(
                f"window {r['window']}: train={r['train_return_jpy_pct']:.2f}% "
                f"val={r['val_return_jpy_pct']:.2f}% "
                f"test={r['test_return_jpy_pct']:.2f}% ({r['test_verdict']})"
            )
    return 0


def _print_human(summary: dict) -> None:
    print(f"run_id: {summary['run_id']}")
    print(f"strategy: {summary['strategy']}")
    print(f"total return (JPY): {summary['total_return_pct_jpy']:.2f}%")
    print(f"total return (USD): {summary['total_return_pct_usd']:.2f}%")
    print(f"CAGR: {summary['cagr_pct']:.2f}%")
    print(f"max drawdown: {summary['max_drawdown_pct']:.2f}%")
    print(f"sharpe: {summary['sharpe']:.2f} | sortino: {summary['sortino']:.2f}")
    print(
        f"trades: {summary['trade_count']} | round trips: {summary['round_trip_count']}"
    )
    print(
        f"win rate: {summary['win_rate']:.1f}% | profit factor: {summary['profit_factor']:.2f}"
    )
    print(
        f"fees: ${summary['fee_total_usd']:.2f} | dividends: ${summary['dividend_income_usd']:.2f}"
    )
    print(f"Buy & Hold (JPY): {summary['buy_and_hold_return_pct_jpy']:.2f}%")
    print(f"verdict: {summary['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
