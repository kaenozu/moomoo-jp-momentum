"""日次エクスポージャー台帳を作成するCLI。

実行例:
    python scripts/daily_exposure_ledger.py \
        --from 2026-01-01 --to 2026-06-30 \
        --strategy momentum --csv

割合・日次効果は百分率。sector_herfindahlは投資部分を1へ正規化したHHI。
holdings_implied_betaは60営業日betaを総資産ウェイトで加重し、現金betaを0とする。
turnover_pctは当日BUY+SELL約定金額 / 前日総資産のgross turnover。
signal_countはbacktest_ordersのBUY注文をsignal_date単位で数えたユニーク銘柄数。
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import active_return_attribution as attribution  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
EPSILON = attribution.EPSILON


class LedgerError(attribution.AttributionError):
    """日次台帳固有の入力・DB不整合。"""


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"日付はYYYY-MM-DD形式で指定してください: {value}"
        ) from exc


def _validate_schema(conn: sqlite3.Connection) -> None:
    attribution._validate_schema(conn)
    required = {
        "backtest_orders": {"id", "run_id", "code", "side", "signal_date"},
        "backtest_fills": {
            "run_id",
            "code",
            "side",
            "quantity",
            "price",
            "filled_at",
        },
    }
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing_tables = sorted(set(required) - tables)
    if missing_tables:
        raise LedgerError(f"必要なテーブルがありません: {', '.join(missing_tables)}")
    missing = [
        f"{table}.{column}"
        for table, columns in required.items()
        for column in sorted(columns - attribution._table_columns(conn, table))
    ]
    if missing:
        raise LedgerError(f"必要なカラムがありません: {', '.join(missing)}")


def _load_fills(conn: sqlite3.Connection, run_id: int, to_date: str) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT id, code, side, quantity, price, filled_at
        FROM backtest_fills
        WHERE run_id = ? AND substr(filled_at, 1, 10) <= ?
        ORDER BY substr(filled_at, 1, 10), id
        """,
        conn,
        params=[run_id, to_date],
    )
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "code",
                "side",
                "quantity",
                "price",
                "filled_at",
                "date",
                "notional",
            ]
        )
    frame["date"] = pd.to_datetime(
        frame["filled_at"].astype(str).str[:10], errors="raise"
    )
    frame["code"] = frame["code"].astype(str)
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="raise").astype(int)
    frame["price"] = pd.to_numeric(frame["price"], errors="raise")
    invalid = sorted(set(frame["side"]) - {"BUY", "SELL"})
    if invalid:
        raise LedgerError(f"未対応のfill sideがあります: {', '.join(invalid)}")
    if (frame["quantity"] <= 0).any() or (frame["price"] <= 0).any():
        raise LedgerError("backtest_fillsのquantityとpriceは正数である必要があります")
    frame["notional"] = frame["quantity"] * frame["price"]
    return frame


def _load_signal_counts(
    conn: sqlite3.Connection,
    run_id: int,
    to_date: str,
) -> pd.Series:
    frame = pd.read_sql_query(
        """
        SELECT code, signal_date
        FROM backtest_orders
        WHERE run_id = ? AND UPPER(side) = 'BUY'
          AND signal_date IS NOT NULL
          AND substr(signal_date, 1, 10) <= ?
        ORDER BY substr(signal_date, 1, 10), id
        """,
        conn,
        params=[run_id, to_date],
    )
    if frame.empty:
        return pd.Series(dtype="int64", name="signal_count")
    frame["date"] = pd.to_datetime(
        frame["signal_date"].astype(str).str[:10], errors="raise"
    )
    return frame.groupby("date")["code"].nunique().astype(int)


def _sector_columns(sectors: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for sector in sorted(set(sectors)):
        token = re.sub(r"\s+", "_", sector.strip())
        token = re.sub(r"[^0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]", "_", token)
        token = re.sub(r"_+", "_", token).strip("_") or "unknown"
        base = f"sector_weight_{token}"
        column = base
        suffix = 2
        while column in used:
            column = f"{base}_{suffix}"
            suffix += 1
        result[sector] = column
        used.add(column)
    return result


def _beta(
    stock_returns: pd.Series,
    benchmark_returns: pd.Series,
    day: pd.Timestamp,
    window: int,
) -> float:
    min_periods = max(20, min(window, window // 2))
    aligned = pd.concat(
        [stock_returns.loc[:day], benchmark_returns.loc[:day]], axis=1
    ).apply(pd.to_numeric, errors="coerce").dropna().tail(window)
    if len(aligned) < min_periods:
        return math.nan
    variance = float(aligned.iloc[:, 1].var())
    if not math.isfinite(variance) or abs(variance) <= EPSILON:
        return math.nan
    return float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1])) / variance


def _implied_beta(
    day: pd.Timestamp,
    weights: Mapping[str, float],
    returns: pd.DataFrame,
    benchmark: str,
    window: int,
) -> float:
    if not weights:
        return 0.0
    benchmark_returns = returns[benchmark]
    values: list[tuple[float, float]] = []
    for code, weight in weights.items():
        if code not in returns.columns or weight <= EPSILON:
            continue
        stock_beta = _beta(returns[code], benchmark_returns, day, window)
        if math.isfinite(stock_beta):
            values.append((float(weight), stock_beta))
    covered = sum(weight for weight, _ in values)
    invested = sum(max(0.0, float(weight)) for weight in weights.values())
    if covered <= EPSILON:
        return math.nan
    holdings_beta = sum(weight * beta for weight, beta in values) / covered
    return holdings_beta * invested


def _trade_metrics(
    fills: pd.DataFrame,
    signals: pd.Series,
    equity: pd.DataFrame,
    run: attribution.RunInfo,
) -> pd.DataFrame:
    dates = equity.index
    previous_equity = equity["total_equity"].shift(1)
    previous_equity.iloc[0] = run.initial_cash
    result = pd.DataFrame(index=dates)
    result["signal_count"] = signals.reindex(dates, fill_value=0).astype(int)
    for side, column in (("BUY", "new_entries"), ("SELL", "exits")):
        counts = fills.loc[fills["side"] == side].groupby("date")["code"].nunique()
        result[column] = counts.reindex(dates, fill_value=0).astype(int)
    gross = fills.groupby("date")["notional"].sum().reindex(dates, fill_value=0.0)
    result["turnover_pct"] = (
        gross / previous_equity.where(previous_equity > EPSILON)
    ).fillna(0.0) * 100.0
    return result


def _build_ledger(
    daily: pd.DataFrame,
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    signals: pd.Series,
    symbols: pd.DataFrame,
    prices: pd.DataFrame,
    run: attribution.RunInfo,
    benchmark: str,
    holding_beta_window: int,
) -> pd.DataFrame:
    snapshots = attribution._position_snapshots(equity.index, fills)
    prices = prices.reindex(prices.index.union(equity.index)).sort_index().ffill()
    returns = prices.pct_change(fill_method=None)
    sector_by_code = symbols.set_index("code")["sector"].astype(str).to_dict()
    sector_map = _sector_columns(
        [
            sector_by_code.get(code, attribution.UNKNOWN_SECTOR)
            for holdings in snapshots.values()
            for code in holdings
        ]
    )
    trades = _trade_metrics(fills, signals, equity, run)
    rows: list[dict[str, float | int | str]] = []

    for day in daily.index:
        total = float(equity.at[day, "total_equity"])
        cash = float(equity.at[day, "cash"])
        position_value = float(equity.at[day, "position_value"])
        if total <= EPSILON or position_value < -EPSILON:
            raise LedgerError(f"不正なequityです: {day.date()}")

        holdings = {code: qty for code, qty in snapshots.get(day, {}).items() if qty > 0}
        raw_values: dict[str, float] = {}
        missing: list[str] = []
        for code, quantity in holdings.items():
            if code not in prices.columns or day not in prices.index:
                missing.append(code)
                continue
            price = prices.at[day, code]
            if pd.isna(price) or float(price) <= 0:
                missing.append(code)
            else:
                raw_values[code] = int(quantity) * float(price)
        if missing:
            raise LedgerError(
                f"保有銘柄の終値がありません: {day.date()} {', '.join(sorted(missing))}"
            )
        raw_total = sum(raw_values.values())
        if position_value > EPSILON and raw_total <= EPSILON:
            raise LedgerError(f"保有評価額を再構築できません: {day.date()}")
        scale = position_value / raw_total if raw_total > EPSILON else 0.0
        weights = {
            code: value * scale / total for code, value in raw_values.items()
        }
        invested_weight = position_value / total
        sector_weights: dict[str, float] = defaultdict(float)
        for code, weight in weights.items():
            sector_weights[sector_by_code.get(code, attribution.UNKNOWN_SECTOR)] += weight
        hhi = (
            sum((weight / invested_weight) ** 2 for weight in sector_weights.values())
            if invested_weight > EPSILON
            else 0.0
        )

        row: dict[str, float | int | str] = {
            "date": day.strftime("%Y-%m-%d"),
            "cash_pct": cash / total * 100.0,
            "invested_pct": invested_weight * 100.0,
            "position_count": len(holdings),
            "top5_weight": sum(sorted(weights.values(), reverse=True)[:5]) * 100.0,
        }
        row.update(
            {
                column: sector_weights.get(sector, 0.0) * 100.0
                for sector, column in sector_map.items()
            }
        )
        row.update(
            {
                "sector_herfindahl": hhi,
                "holdings_implied_beta": _implied_beta(
                    day, weights, returns, benchmark, holding_beta_window
                ),
                "realized_beta": float(daily.at[day, "portfolio_beta"]),
                "signal_count": int(trades.at[day, "signal_count"]),
                "new_entries": int(trades.at[day, "new_entries"]),
                "exits": int(trades.at[day, "exits"]),
                "turnover_pct": float(trades.at[day, "turnover_pct"]),
                "sector_allocation": float(daily.at[day, "sector_allocation_daily"])
                * 100.0,
                "within_sector": float(daily.at[day, "within_sector_daily"]) * 100.0,
                "cash_drag": float(daily.at[day, "cash_drag_daily"]) * 100.0,
            }
        )
        rows.append(row)

    prefix = ["date", "cash_pct", "invested_pct", "position_count", "top5_weight"]
    suffix = [
        "sector_herfindahl",
        "holdings_implied_beta",
        "realized_beta",
        "signal_count",
        "new_entries",
        "exits",
        "turnover_pct",
        "sector_allocation",
        "within_sector",
        "cash_drag",
    ]
    frame = pd.DataFrame(rows)
    return frame[prefix + list(sector_map.values()) + suffix]


def _last_finite(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else math.nan


def _monthly_summary(ledger: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    source = ledger.copy()
    source["month"] = pd.to_datetime(source["date"]).dt.to_period("M").astype(str)
    rows = []
    for month, group in source.groupby("month", sort=True):
        rows.append(
            {
                "month": month,
                "avg_cash_pct": group["cash_pct"].mean(),
                "avg_invested_pct": group["invested_pct"].mean(),
                "avg_positions": group["position_count"].mean(),
                "avg_top5_weight": group["top5_weight"].mean(),
                "avg_sector_hhi": group["sector_herfindahl"].mean(),
                "avg_holdings_beta": pd.to_numeric(
                    group["holdings_implied_beta"], errors="coerce"
                ).mean(),
                "month_end_realized_beta": _last_finite(group["realized_beta"]),
                "signals": int(group["signal_count"].sum()),
                "entries": int(group["new_entries"].sum()),
                "exits": int(group["exits"].sum()),
                "turnover_pct": group["turnover_pct"].sum(),
            }
        )
    linked = attribution._aggregate_monthly(daily)[
        [
            "month",
            "strategy_sector_allocation",
            "strategy_within_sector",
            "strategy_cash_drag",
        ]
    ].rename(
        columns={
            "strategy_sector_allocation": "sector_allocation",
            "strategy_within_sector": "within_sector",
            "strategy_cash_drag": "cash_drag",
        }
    )
    return pd.DataFrame(rows).merge(linked, on="month", validate="one_to_one")


def _display(summary: pd.DataFrame, run: attribution.RunInfo, benchmark: str) -> None:
    print("\n" + "=" * 168)
    print(
        f"日次エクスポージャー台帳 月次サマリー | run_id={run.run_id} | "
        f"strategy={run.strategy_name} | benchmark={benchmark}"
    )
    print(
        "単位: exposure/turnover/effect=%、beta=無次元、sector HHI=0〜1。"
        "effectは既存attributionと同じCarino月次link。"
    )
    print("=" * 168)
    table = summary.copy()
    numeric = [column for column in table.columns if column != "month"]
    table[numeric] = table[numeric].round(4)
    print(table.to_string(index=False, na_rep="N/A"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="全取引日の日次エクスポージャー台帳を作成する"
    )
    parser.add_argument("--from", dest="from_date", required=True, type=_parse_date)
    parser.add_argument("--to", dest="to_date", required=True, type=_parse_date)
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--benchmark", default=attribution.DEFAULT_BENCHMARK)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db")
    parser.add_argument("--run-id", type=int)
    parser.add_argument(
        "--auto-run",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--holding-beta-window", type=int, default=60)
    parser.add_argument("--realized-beta-window", type=int, default=20)
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--output-dir", default="reports")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.from_date > args.to_date:
        parser.error("--fromは--to以前の日付にしてください")
    if args.holding_beta_window < 20:
        parser.error("--holding-beta-windowは20以上にしてください")
    if args.realized_beta_window < 5:
        parser.error("--realized-beta-windowは5以上にしてください")

    from_date, to_date = args.from_date.isoformat(), args.to_date.isoformat()
    config_path = attribution._resolve_path(args.config, base=Path.cwd())
    if not config_path.exists() and not Path(args.config).is_absolute():
        candidate = attribution._resolve_path(args.config, base=REPO_ROOT)
        if candidate.exists():
            config_path = candidate
    config = attribution._load_yaml_config(config_path)
    db_path = attribution._database_path(config, config_path, args.db)
    idle_policy = attribution._idle_cash_policy(config)

    try:
        run = attribution._ensure_run(
            db_path,
            config_path,
            args.strategy,
            from_date,
            to_date,
            args.run_id,
            bool(args.auto_run),
            args.db is not None,
        )
        with attribution._read_only_connection(db_path) as conn:
            _validate_schema(conn)
            equity = attribution._load_equity_curve(conn, run, to_date)
            fills = _load_fills(conn, run.run_id, to_date)
            signals = _load_signal_counts(conn, run.run_id, to_date)
            symbols = attribution._load_symbols(conn)
            if pd.Timestamp(from_date) < equity.index.min():
                raise LedgerError(
                    f"equity curve開始日より前は分析できません: {equity.index.min().date()}"
                )
            universe = symbols.loc[
                (symbols["role"] == "trade_candidate")
                & (pd.to_numeric(symbols["tradable"], errors="coerce").fillna(0) == 1),
                "code",
            ].astype(str)
            codes = set(universe) | set(fills["code"].astype(str)) | {args.benchmark}
            if idle_policy.enabled and idle_policy.benchmark_code:
                codes.add(idle_policy.benchmark_code)
            lookback = max(120, args.holding_beta_window * 3)
            price_start = (
                min(equity.index.min().date(), args.from_date) - timedelta(days=lookback)
            ).isoformat()
            prices = attribution._load_close_prices(
                conn, sorted(codes), price_start, to_date
            )

        daily = attribution._build_daily_attribution(
            equity,
            fills,
            symbols,
            prices,
            run,
            from_date,
            to_date,
            args.benchmark,
            idle_policy,
            args.realized_beta_window,
        )
        ledger = _build_ledger(
            daily,
            equity,
            fills,
            signals,
            symbols,
            prices,
            run,
            args.benchmark,
            args.holding_beta_window,
        )
        _display(_monthly_summary(ledger, daily), run, args.benchmark)
        if args.csv:
            output_dir = attribution._resolve_path(args.output_dir, base=REPO_ROOT)
            output = (
                attribution._resolve_path(args.output, base=Path.cwd())
                if args.output
                else output_dir
                / f"daily_exposure_ledger_{args.strategy}_{from_date}_{to_date}.csv"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            ledger.to_csv(
                output,
                index=False,
                encoding="utf-8-sig",
                float_format="%.10f",
            )
            print(f"[OK] CSV: {output}")
        return 0
    except (LedgerError, attribution.AttributionError, sqlite3.Error, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
