"""
Report writer for US grid backtest runs.

Writes machine-readable CSVs/JSON plus a human summary and verdict into the
run report directory.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .backtest import BacktestResult
from .benchmark import BenchmarkResult
from .metrics import Verdict


def write_report(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    result: BacktestResult,
    bh: BenchmarkResult,
    cash: BenchmarkResult,
    verdict: Verdict,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)

    # summary.md
    _write_summary_md(run_dir, manifest, result, bh, cash, verdict)

    # metrics.json
    (run_dir / "metrics.json").write_text(
        json.dumps(
            _metrics_dict(result, bh, cash, verdict), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )

    # equity_curve.csv
    _write_csv(
        run_dir / "equity_curve.csv",
        [
            "date",
            "cash_usd",
            "position_value_usd",
            "total_equity_usd",
            "total_equity_jpy",
            "fx_rate",
            "drawdown_pct",
            "regime",
            "open_orders",
        ],
        [
            [
                p.date,
                f"{p.cash_usd:.2f}",
                f"{p.position_value_usd:.2f}",
                f"{p.total_equity_usd:.2f}",
                f"{p.total_equity_jpy:.2f}",
                f"{p.fx_rate:.4f}",
                f"{p.drawdown_pct:.4f}",
                p.regime,
                p.open_orders,
            ]
            for p in result.equity_curve
        ],
    )

    # trades.csv
    _write_csv(
        run_dir / "trades.csv",
        ["date", "code", "side", "quantity", "price_usd", "fee_usd", "reason"],
        [
            [
                t.date,
                t.code,
                t.side,
                t.quantity,
                f"{t.price_usd:.4f}",
                f"{t.fee_usd:.4f}",
                t.reason,
            ]
            for t in result.trades
        ],
    )

    # benchmark_curve.csv
    _write_csv(
        run_dir / "benchmark_curve.csv",
        ["date", "bh_total_equity_jpy", "bh_drawdown_pct", "cash_total_equity_jpy"],
        [
            [
                p["date"],
                f"{p['total_equity_jpy']:.2f}",
                f"{p['drawdown_pct']:.4f}",
                f"{cash.equity_curve[0]['total_equity_jpy']:.2f}"
                if cash.equity_curve
                else "0.0",
            ]
            for p in bh.equity_curve
        ],
    )

    # verdict.json
    (run_dir / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": verdict.label,
                "reasons": verdict.reasons,
                "evidence": verdict.evidence,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # skipped / warnings
    (run_dir / "warnings.txt").write_text(
        "\n".join(result.warnings + result.skipped),
        encoding="utf-8",
    )


def _metrics_dict(
    result: BacktestResult,
    bh: BenchmarkResult,
    cash: BenchmarkResult,
    verdict: Verdict,
) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "strategy_name": result.strategy_name,
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
        "avg_gross_cycle_usd": result.avg_gross_cycle_usd,
        "avg_net_cycle_usd": result.avg_net_cycle_usd,
        "fee_total_usd": result.fee_total_usd,
        "fee_drag_pct": result.fee_drag_pct,
        "dividend_income_usd": result.dividend_income_usd,
        "orders_created": result.orders_created,
        "orders_filled": result.orders_filled,
        "orders_cancelled": result.orders_cancelled,
        "orders_rejected": result.orders_rejected,
        "cash_shortage_count": result.cash_shortage_count,
        "buy_and_hold_return_pct_jpy": bh.total_return_pct_jpy,
        "buy_and_hold_cagr_pct": bh.cagr_pct,
        "buy_and_hold_max_drawdown_pct": bh.max_drawdown_pct,
        "cash_return_pct_jpy": cash.total_return_pct_jpy,
        "verdict": verdict.label,
        "verdict_reasons": verdict.reasons,
    }


def _write_summary_md(
    run_dir: Path,
    manifest: dict[str, Any],
    result: BacktestResult,
    bh: BenchmarkResult,
    cash: BenchmarkResult,
    verdict: Verdict,
) -> None:
    lines = [
        f"# US Grid Backtest {result.run_id}",
        "",
        f"- strategy: {result.strategy_name}",
        f"- period: {result.start_date} .. {result.end_date}",
        f"- symbols: {', '.join(result.symbols)}",
        f"- capital: {result.capital_jpy:,.0f} JPY",
        f"- git sha: {manifest.get('git_sha', 'unknown')}",
        f"- data hash: {manifest.get('data_sha256', 'unknown')}",
        "",
        "## Results",
        "",
        "| metric | value |",
        "|---|---|",
        f"| total return (JPY) | {result.total_return_pct_jpy:.2f}% |",
        f"| total return (USD) | {result.total_return_pct_usd:.2f}% |",
        f"| CAGR | {result.cagr_pct:.2f}% |",
        f"| max drawdown | {result.max_drawdown_pct:.2f}% |",
        f"| sharpe | {result.sharpe:.2f} |",
        f"| sortino | {result.sortino:.2f} |",
        f"| calmar | {result.calmar:.2f} |",
        f"| trades | {result.trade_count} |",
        f"| round trips | {result.round_trip_count} |",
        f"| win rate | {result.win_rate:.1f}% |",
        f"| profit factor | {result.profit_factor:.2f} |",
        f"| fees (USD) | {result.fee_total_usd:.2f} |",
        f"| fee drag | {result.fee_drag_pct:.2f}% |",
        f"| dividends (USD) | {result.dividend_income_usd:.2f} |",
        "",
        "## Benchmarks",
        "",
        "| benchmark | total return (JPY) | CAGR | max drawdown |",
        "|---|---|---|---|",
        f"| Buy & Hold | {bh.total_return_pct_jpy:.2f}% | {bh.cagr_pct:.2f}% | {bh.max_drawdown_pct:.2f}% |",
        f"| cash | {cash.total_return_pct_jpy:.2f}% | 0.00% | 0.00% |",
        "",
        "## Verdict",
        "",
        f"**{verdict.label}**",
        "",
        *[f"- {reason}" for reason in verdict.reasons],
        "",
        "## Warnings / skipped",
        "",
        *(f"- {w}" for w in result.warnings + result.skipped),
        "",
    ]
    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
