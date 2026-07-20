"""
sector-relative momentum比較バックテストCLI。

実行例:
    python scripts/compare_sector_momentum.py \
        --from 2022-01-01 \
        --to 2026-06-30 \
        --strategies momentum,sector_relative_momentum \
        --csv

業種配分・業種内選択は、JP.2559の構成銘柄ウェイトがDBにないため、
enabledかつtradableなtrade_candidateの等ウェイト業種ベンチマークを
JP.2559の日次リターンへアンカーして推定する。
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import active_return_attribution as attribution
from src.backtest_runner import BacktestRunner
from src.config import load_config
from src.strategies import StrategyRegistry

DEFAULT_BENCHMARK = "JP.2559"
EPSILON = 1e-12


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def _parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"日付はYYYY-MM-DD形式で指定してください: {value}"
        ) from exc


def _parse_strategies(value: str) -> list[str]:
    strategies = []
    for item in value.split(","):
        name = item.strip()
        if name and name not in strategies:
            strategies.append(name)
    if len(strategies) < 2:
        raise argparse.ArgumentTypeError("--strategiesには2戦略以上を指定してください")
    return strategies


def _resolve_config_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if not candidate.exists() and not Path(value).is_absolute():
        repository_candidate = (REPO_ROOT / value).resolve()
        if repository_candidate.exists():
            candidate = repository_candidate
    if not candidate.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {candidate}")
    return candidate


def _run_backtests(
    config_path: Path,
    strategies: Sequence[str],
    from_date: str,
    to_date: str,
) -> dict[str, int]:
    run_ids: dict[str, int] = {}
    with _working_directory(config_path.parent):
        config = load_config(str(config_path))
        registered = set(StrategyRegistry.list_names())
        unknown = [strategy for strategy in strategies if strategy not in registered]
        if unknown:
            raise ValueError(
                "未登録の戦略があります: "
                + ", ".join(unknown)
                + f"（登録済み: {', '.join(sorted(registered))}）"
            )

        for strategy in strategies:
            print(f"\n[INFO] backtest開始: {strategy} {from_date}〜{to_date}")
            runner = BacktestRunner(config)
            run_ids[strategy] = int(runner.run(strategy, from_date, to_date))
            print(f"[OK] backtest完了: {strategy} run_id={run_ids[strategy]}")
    return run_ids


def _calculate_sharpe(daily_returns: pd.Series) -> float:
    returns = pd.to_numeric(daily_returns, errors="coerce").dropna()
    if len(returns) < 2:
        return math.nan
    standard_deviation = float(returns.std(ddof=1))
    if not math.isfinite(standard_deviation) or standard_deviation <= EPSILON:
        return math.nan
    return float(returns.mean() / standard_deviation * math.sqrt(252.0))


def _calculate_turnover(
    connection: sqlite3.Connection,
    run_id: int,
    average_equity: float,
    trading_days: int,
) -> tuple[float, float]:
    row = connection.execute(
        """
        SELECT
            SUM(CASE WHEN side = 'BUY' THEN ABS(price * quantity) ELSE 0 END),
            SUM(CASE WHEN side = 'SELL' THEN ABS(price * quantity) ELSE 0 END)
        FROM backtest_fills
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    buy_notional = float(row[0] or 0.0)
    sell_notional = float(row[1] or 0.0)
    if average_equity <= EPSILON or trading_days <= 0:
        return math.nan, math.nan

    period_turnover = 0.5 * (buy_notional + sell_notional) / average_equity
    annualized_turnover = period_turnover * 252.0 / trading_days
    return period_turnover * 100.0, annualized_turnover * 100.0


def _sector_return_contributions(
    daily: pd.DataFrame,
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    symbols: pd.DataFrame,
    price_matrix: pd.DataFrame,
    initial_cash: float,
    strategy: str,
) -> pd.DataFrame:
    """前日保有ウェイト×当日銘柄リターンを月次リンクして業種寄与を出す。"""
    if daily.empty:
        return pd.DataFrame(
            columns=["strategy", "month", "sector", "return_contribution"]
        )

    snapshots = attribution._position_snapshots(equity.index, fills)
    returns = price_matrix.pct_change(fill_method=None)
    symbol_index = symbols.set_index("code")
    sector_by_code = symbol_index["sector"].astype(str).to_dict()

    contribution_rows: list[dict] = []
    full_dates = list(equity.index)
    date_positions = {day: index for index, day in enumerate(full_dates)}
    for day in daily.index:
        index = date_positions.get(day)
        if index is None or index == 0:
            continue
        previous_day = full_dates[index - 1]
        previous_equity = float(equity.at[previous_day, "total_equity"])
        if previous_equity <= EPSILON:
            previous_equity = initial_cash
        holdings = snapshots.get(previous_day, {})
        if not holdings or previous_day not in price_matrix.index or day not in returns.index:
            continue

        previous_prices = price_matrix.loc[previous_day]
        day_returns = returns.loc[day]
        sector_contributions: dict[str, float] = {}
        for code, quantity in holdings.items():
            previous_price = previous_prices.get(code)
            stock_return = day_returns.get(code)
            if (
                quantity <= 0
                or pd.isna(previous_price)
                or pd.isna(stock_return)
                or float(previous_price) <= 0
            ):
                continue
            sector = sector_by_code.get(code, attribution.UNKNOWN_SECTOR)
            weight = float(previous_price) * int(quantity) / previous_equity
            sector_contributions[sector] = (
                sector_contributions.get(sector, 0.0)
                + weight * float(stock_return)
            )

        for sector, contribution in sector_contributions.items():
            contribution_rows.append(
                {"date": day, "sector": sector, "contribution": contribution}
            )

    if not contribution_rows:
        return pd.DataFrame(
            columns=["strategy", "month", "sector", "return_contribution"]
        )

    contributions = pd.DataFrame(contribution_rows)
    pivot = contributions.pivot_table(
        index="date", columns="sector", values="contribution", aggfunc="sum", fill_value=0.0
    ).reindex(daily.index, fill_value=0.0)

    output_rows: list[dict] = []
    for period, group in daily.groupby(daily.index.to_period("M"), sort=True):
        group_returns = pd.to_numeric(
            group["strategy_daily_return"], errors="coerce"
        ).fillna(0.0)
        growth_after = (
            (1.0 + group_returns.iloc[::-1])
            .cumprod()
            .shift(1, fill_value=1.0)
            .iloc[::-1]
        )
        linked = pivot.reindex(group.index, fill_value=0.0).mul(growth_after, axis=0)
        totals = linked.sum(axis=0) * 100.0
        for sector, contribution in totals.items():
            if abs(float(contribution)) <= EPSILON:
                continue
            output_rows.append(
                {
                    "strategy": strategy,
                    "month": str(period),
                    "sector": str(sector),
                    "return_contribution": float(contribution),
                }
            )
    return pd.DataFrame(output_rows)


def _analyze_run(
    connection: sqlite3.Connection,
    strategy: str,
    run_id: int,
    from_date: str,
    to_date: str,
    benchmark: str,
    idle_policy: attribution.IdleCashPolicy,
    beta_window: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    run = attribution._load_run_by_id(connection, run_id)
    equity = attribution._load_equity_curve(connection, run, to_date)
    fills = attribution._load_fills(connection, run_id, to_date)
    symbols = attribution._load_symbols(connection)

    held_codes = sorted(set(fills["code"].astype(str))) if not fills.empty else []
    universe_codes = symbols.loc[
        (symbols["role"] == "trade_candidate")
        & (pd.to_numeric(symbols["tradable"], errors="coerce").fillna(0).astype(int) == 1),
        "code",
    ].astype(str).tolist()
    price_codes = set(universe_codes) | set(held_codes) | {benchmark}
    if idle_policy.enabled and idle_policy.benchmark_code:
        price_codes.add(idle_policy.benchmark_code)

    curve_start = equity.index.min().date()
    lookback_start = (min(curve_start, date.fromisoformat(from_date)) - timedelta(days=60)).isoformat()
    price_matrix = attribution._load_close_prices(
        connection, sorted(price_codes), lookback_start, to_date
    )
    daily = attribution._build_daily_attribution(
        equity=equity,
        fills=fills,
        symbols=symbols,
        price_matrix=price_matrix,
        run=run,
        from_date=from_date,
        to_date=to_date,
        benchmark_code=benchmark,
        idle_policy=idle_policy,
        beta_window=beta_window,
    )
    monthly = attribution._aggregate_monthly(daily)
    monthly.insert(0, "strategy", strategy)

    contributions = _sector_return_contributions(
        daily=daily,
        equity=equity,
        fills=fills,
        symbols=symbols,
        price_matrix=price_matrix,
        initial_cash=run.initial_cash,
        strategy=strategy,
    )

    run_row = connection.execute(
        """
        SELECT final_equity, total_return_pct, max_drawdown_pct, trade_count
        FROM backtest_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    average_equity = float(equity["total_equity"].mean())
    period_turnover, annualized_turnover = _calculate_turnover(
        connection, run_id, average_equity, len(daily)
    )
    summary = {
        "strategy": strategy,
        "run_id": run_id,
        "from_date": from_date,
        "to_date": to_date,
        "final_equity": float(run_row[0]) if run_row[0] is not None else math.nan,
        "total_return_pct": float(run_row[1]) if run_row[1] is not None else math.nan,
        "max_drawdown_pct": float(run_row[2]) if run_row[2] is not None else math.nan,
        "sharpe": _calculate_sharpe(daily["strategy_daily_return"]),
        "period_turnover_pct": period_turnover,
        "annualized_turnover_pct": annualized_turnover,
        "trade_count": int(run_row[3] or 0),
    }
    return monthly, contributions, summary


def _display_table(title: str, frame: pd.DataFrame) -> None:
    print("\n" + "=" * 120)
    print(title)
    print("=" * 120)
    if frame.empty:
        print("データなし")
        return
    display = frame.copy()
    numeric_columns = display.select_dtypes(include="number").columns
    display[numeric_columns] = display[numeric_columns].round(4)
    print(display.to_string(index=False, na_rep="N/A"))


def _export_csvs(
    output_dir: Path,
    from_date: str,
    to_date: str,
    monthly_returns: pd.DataFrame,
    monthly_allocation: pd.DataFrame,
    sector_contributions: pd.DataFrame,
    attribution_frame: pd.DataFrame,
    summary: pd.DataFrame,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{from_date}_{to_date}"
    outputs = {
        output_dir / f"sector_momentum_monthly_returns_{suffix}.csv": monthly_returns,
        output_dir / f"sector_momentum_monthly_sector_allocation_{suffix}.csv": monthly_allocation,
        output_dir / f"sector_momentum_sector_contributions_{suffix}.csv": sector_contributions,
        output_dir / f"sector_momentum_active_attribution_{suffix}.csv": attribution_frame,
        output_dir / f"sector_momentum_summary_{suffix}.csv": summary,
    }
    for path, frame in outputs.items():
        frame.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.10f")
    return list(outputs)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="momentumとsector-relative momentumを比較する"
    )
    parser.add_argument("--from", dest="from_date", required=True, type=_parse_iso_date)
    parser.add_argument("--to", dest="to_date", required=True, type=_parse_iso_date)
    parser.add_argument(
        "--strategies",
        type=_parse_strategies,
        default=_parse_strategies("momentum,sector_relative_momentum"),
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK)
    parser.add_argument("--beta-window", type=int, default=20)
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--output-dir", default="reports")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.from_date > args.to_date:
        parser.error("--fromは--to以前の日付にしてください")
    if args.beta_window < 5:
        parser.error("--beta-windowは5以上にしてください")

    from_date = args.from_date.isoformat()
    to_date = args.to_date.isoformat()
    try:
        config_path = _resolve_config_path(args.config)
        raw_config = attribution._load_yaml_config(config_path)
        db_path = attribution._database_path(raw_config, config_path, None)
        idle_policy = attribution._idle_cash_policy(raw_config)

        run_ids = _run_backtests(
            config_path=config_path,
            strategies=args.strategies,
            from_date=from_date,
            to_date=to_date,
        )

        monthly_frames: list[pd.DataFrame] = []
        contribution_frames: list[pd.DataFrame] = []
        summaries: list[dict] = []
        with attribution._read_only_connection(db_path) as connection:
            attribution._validate_schema(connection)
            for strategy, run_id in run_ids.items():
                monthly, contributions, summary = _analyze_run(
                    connection=connection,
                    strategy=strategy,
                    run_id=run_id,
                    from_date=from_date,
                    to_date=to_date,
                    benchmark=args.benchmark,
                    idle_policy=idle_policy,
                    beta_window=args.beta_window,
                )
                monthly_frames.append(monthly)
                contribution_frames.append(contributions)
                summaries.append(summary)

        attribution_frame = pd.concat(monthly_frames, ignore_index=True)
        sector_contributions = (
            pd.concat(contribution_frames, ignore_index=True)
            if contribution_frames
            else pd.DataFrame()
        )
        summary_frame = pd.DataFrame(summaries)
        monthly_returns = attribution_frame.pivot(
            index="month", columns="strategy", values="strategy_return"
        ).reset_index()
        monthly_allocation = attribution_frame.pivot(
            index="month", columns="strategy", values="strategy_sector_allocation"
        ).reset_index()
        period_sector_contributions = (
            sector_contributions.groupby(["strategy", "sector"], as_index=False)[
                "return_contribution"
            ]
            .sum()
            .sort_values(["strategy", "return_contribution"], ascending=[True, False])
            if not sector_contributions.empty
            else sector_contributions
        )

        _display_table("月次リターン比較（%）", monthly_returns)
        _display_table("月次業種配分効果比較（percentage points）", monthly_allocation)
        _display_table("業種別リターン寄与・期間合計（percentage points）", period_sector_contributions)
        _display_table("アクティブリターン要因分解", attribution_frame)
        _display_table("最大DD・Sharpe・turnover", summary_frame)
        print(
            "\n[NOTE] 業種配分/業種内選択はtrade_candidate等ウェイト業種proxyを"
            "JP.2559へアンカーした推定値です。"
        )

        if args.csv:
            output_dir = Path(args.output_dir).expanduser()
            if not output_dir.is_absolute():
                output_dir = (REPO_ROOT / output_dir).resolve()
            paths = _export_csvs(
                output_dir=output_dir,
                from_date=from_date,
                to_date=to_date,
                monthly_returns=monthly_returns,
                monthly_allocation=monthly_allocation,
                sector_contributions=sector_contributions,
                attribution_frame=attribution_frame,
                summary=summary_frame,
            )
            for path in paths:
                print(f"[OK] CSV: {path}")
        return 0
    except (
        attribution.AttributionError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
