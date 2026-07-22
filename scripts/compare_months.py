"""Compare two months of beta, turnover, holding periods, sectors, and stops.

Example:
    python scripts/compare_months.py --strategy momentum --run-id 156
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from scripts.beta_analysis_common import (  # noqa: E402
    DEFAULT_BENCHMARK_CODE,
    AnalysisError,
    build_daily_beta_decomposition,
    closed_trades_frame,
    connect_readonly,
    month_bounds,
    safe_slug,
    sector_return_contribution,
    select_run,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="2か月の日次β、turnover、保有日数、業種寄与、stop lossを比較します。"
    )
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--run-id", type=int, help="比較対象backtest run ID")
    parser.add_argument(
        "--months",
        nargs=2,
        default=("2026-02", "2026-04"),
        metavar=("MONTH_A", "MONTH_B"),
        help="比較月 YYYY-MM YYYY-MM",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--benchmark-code", default=DEFAULT_BENCHMARK_CODE)
    parser.add_argument("--beta-lookback", type=int, default=60)
    parser.add_argument("--output-dir", default="reports")
    return parser


def _save_beta_plot(frames: dict[str, pd.DataFrame], path: Path) -> None:
    fig, axes = plt.subplots(
        len(frames), 1, figsize=(12, 4 * len(frames)), sharex=False
    )
    if len(frames) == 1:
        axes = [axes]
    for axis, (month, frame) in zip(axes, frames.items()):
        plot_frame = frame.copy()
        plot_frame["date"] = pd.to_datetime(plot_frame["date"])
        axis.plot(
            plot_frame["date"],
            plot_frame["holdings_implied_beta"],
            marker="o",
            label="holdings implied beta",
        )
        realized = plot_frame["realized_beta_daily"].clip(lower=-5, upper=5)
        axis.scatter(
            plot_frame["date"],
            realized,
            s=22,
            alpha=0.75,
            label="realized beta daily (display clipped to ±5)",
        )
        axis.axhline(1.0, linewidth=1, linestyle="--")
        axis.set_title(month)
        axis.set_ylabel("beta")
        axis.grid(True, alpha=0.25)
        axis.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_turnover_plot(frames: dict[str, pd.DataFrame], path: Path) -> None:
    fig, axis = plt.subplots(figsize=(12, 5))
    for month, frame in frames.items():
        x = pd.to_datetime(frame["date"]).dt.day
        axis.plot(x, frame["churn_today"], marker="o", label=month)
    axis.set_xlabel("day of month")
    axis.set_ylabel("daily churn / turnover (%)")
    axis.set_title("Daily turnover comparison")
    axis.grid(True, alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_holding_plot(trades_by_month: dict[str, pd.DataFrame], path: Path) -> None:
    fig, axis = plt.subplots(figsize=(10, 5))
    has_data = False
    for month, trades in trades_by_month.items():
        values = (
            trades["holding_days"].dropna()
            if not trades.empty
            else pd.Series(dtype=float)
        )
        if values.empty:
            continue
        has_data = True
        bins = range(1, int(values.max()) + 3)
        axis.hist(values, bins=bins, alpha=0.45, label=f"{month} (n={len(values)})")
    axis.set_xlabel("holding days (trading sessions)")
    axis.set_ylabel("closed trades")
    axis.set_title("Holding-period distribution")
    axis.grid(True, alpha=0.2)
    if has_data:
        axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_sector_plot(contributions: dict[str, pd.DataFrame], path: Path) -> None:
    merged: pd.DataFrame | None = None
    for month, frame in contributions.items():
        renamed = frame.rename(columns={"contribution_pct": month})
        merged = (
            renamed
            if merged is None
            else merged.merge(renamed, on="sector", how="outer")
        )
    if merged is None:
        merged = pd.DataFrame(columns=["sector", *contributions])
    merged = merged.fillna(0.0)
    value_columns = list(contributions)
    if not merged.empty:
        merged["abs_total"] = merged[value_columns].abs().sum(axis=1)
        merged = (
            merged.sort_values("abs_total", ascending=False)
            .head(15)
            .drop(columns="abs_total")
        )
        merged = merged.set_index("sector")
    fig, axis = plt.subplots(figsize=(12, 6))
    if not merged.empty:
        merged.plot(kind="bar", ax=axis)
    axis.axhline(0.0, linewidth=1)
    axis.set_ylabel("approx. return contribution (%)")
    axis.set_title("Sector return contribution")
    axis.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _save_stop_plot(trades_by_month: dict[str, pd.DataFrame], path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for month, trades in trades_by_month.items():
        stops = trades[trades["is_stop_loss"]].copy() if not trades.empty else trades
        if stops.empty:
            continue
        stop_dates = pd.to_datetime(stops["exit_date"])
        daily_counts = stop_dates.dt.day.value_counts().sort_index()
        axes[0].plot(daily_counts.index, daily_counts.values, marker="o", label=month)
        axes[1].hist(
            stops["holding_days"].dropna(),
            bins=range(1, int(stops["holding_days"].max()) + 3),
            alpha=0.45,
            label=f"{month} (n={len(stops)})",
        )
    axes[0].set_title("Stop-loss exits by day")
    axes[0].set_xlabel("day of month")
    axes[0].set_ylabel("stop exits")
    axes[1].set_title("Holding days until stop")
    axes[1].set_xlabel("holding days")
    axes[1].set_ylabel("stop exits")
    for axis in axes:
        axis.grid(True, alpha=0.2)
        handles, _ = axis.get_legend_handles_labels()
        if handles:
            axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _summary_rows(
    frames: dict[str, pd.DataFrame], trades_by_month: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []
    for month, frame in frames.items():
        trades = trades_by_month[month]
        stops = trades[trades["is_stop_loss"]] if not trades.empty else trades
        implied = frame["holdings_implied_beta"].dropna()
        realized = (
            frame["realized_beta_daily"]
            .replace([math.inf, -math.inf], math.nan)
            .dropna()
        )
        rows.append(
            {
                "month": month,
                "trading_days": len(frame),
                "avg_holdings_implied_beta": implied.mean()
                if not implied.empty
                else math.nan,
                "median_realized_beta_daily": realized.median()
                if not realized.empty
                else math.nan,
                "sum_churn_pct": frame["churn_today"].sum(min_count=1),
                "new_entries": int(frame["new_entry_count"].sum()),
                "exits": int(frame["exit_count"].sum()),
                "closed_trades": len(trades),
                "avg_holding_days": trades["holding_days"].mean()
                if not trades.empty
                else math.nan,
                "stop_loss_count": len(stops),
                "stop_loss_frequency_pct": len(stops) / len(trades) * 100.0
                if len(trades)
                else math.nan,
                "avg_days_to_stop": stops["holding_days"].mean()
                if not stops.empty
                else math.nan,
            }
        )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    month_ranges = {month: month_bounds(month) for month in args.months}
    overall_start = min(bounds[0] for bounds in month_ranges.values())
    overall_end = max(bounds[1] for bounds in month_ranges.values())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    try:
        with connect_readonly(config.database_path) as conn:
            run_info = select_run(
                conn,
                strategy_name=args.strategy,
                start_date=overall_start,
                end_date=overall_end,
                run_id=args.run_id,
            )
            frames = {
                month: build_daily_beta_decomposition(
                    conn,
                    run_info=run_info,
                    start_date=bounds[0],
                    end_date=bounds[1],
                    benchmark_code=args.benchmark_code,
                    beta_lookback_days=args.beta_lookback,
                )
                for month, bounds in month_ranges.items()
            }
            all_trades = closed_trades_frame(
                conn,
                run_info=run_info,
                benchmark_code=args.benchmark_code,
            )
            trades_by_month = {
                month: all_trades[
                    all_trades["exit_date"].astype(str).str.startswith(month)
                ].copy()
                if not all_trades.empty
                else all_trades.copy()
                for month in args.months
            }
            contributions = {
                month: sector_return_contribution(
                    conn,
                    run_info=run_info,
                    start_date=bounds[0],
                    end_date=bounds[1],
                    benchmark_code=args.benchmark_code,
                )
                for month, bounds in month_ranges.items()
            }
    except (AnalysisError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    stem = (
        f"compare_months_{safe_slug(args.strategy)}_"
        f"{safe_slug(args.months[0])}_vs_{safe_slug(args.months[1])}_run{run_info.run_id}"
    )
    paths = {
        "beta": output_dir / f"{stem}_beta.png",
        "turnover": output_dir / f"{stem}_turnover.png",
        "holding": output_dir / f"{stem}_holding_days.png",
        "sector": output_dir / f"{stem}_sector_contribution.png",
        "stops": output_dir / f"{stem}_stop_loss.png",
        "summary": output_dir / f"{stem}_summary.csv",
        "daily": output_dir / f"{stem}_daily.csv",
        "trades": output_dir / f"{stem}_trades.csv",
        "sectors": output_dir / f"{stem}_sectors.csv",
    }

    _save_beta_plot(frames, paths["beta"])
    _save_turnover_plot(frames, paths["turnover"])
    _save_holding_plot(trades_by_month, paths["holding"])
    _save_sector_plot(contributions, paths["sector"])
    _save_stop_plot(trades_by_month, paths["stops"])

    summary = _summary_rows(frames, trades_by_month)
    write_csv(summary, paths["summary"])
    write_csv(
        pd.concat(
            [frame.assign(month=month) for month, frame in frames.items()],
            ignore_index=True,
        ),
        paths["daily"],
    )
    write_csv(
        pd.concat(
            [trades.assign(month=month) for month, trades in trades_by_month.items()],
            ignore_index=True,
        ),
        paths["trades"],
    )
    write_csv(
        pd.concat(
            [frame.assign(month=month) for month, frame in contributions.items()],
            ignore_index=True,
        ),
        paths["sectors"],
    )

    print(f"run_id={run_info.run_id} strategy={run_info.strategy_name}")
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    for label, path in paths.items():
        print(f"[OK] {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
