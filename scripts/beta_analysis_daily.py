"""Daily holdings and beta decomposition helpers."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict, deque
from typing import Any

import pandas as pd

try:
    from .beta_analysis_data import (
        DEFAULT_BENCHMARK_CODE,
        DEFAULT_BETA_LOOKBACK,
        MIN_BETA_OBSERVATIONS,
        AnalysisError,
        _fifo_sell,
        _STOP_REASONS,
        build_closed_trades,
        BetaEstimator,
        Lot,
        RunInfo,
        last_price,
        normalize_date,
        load_close_frame,
        load_equity_curve,
        load_fills,
        load_sector_map,
        require_tables,
        trading_days,
    )
except ImportError:  # Direct execution from scripts/.
    from beta_analysis_data import (
        DEFAULT_BENCHMARK_CODE,
        DEFAULT_BETA_LOOKBACK,
        MIN_BETA_OBSERVATIONS,
        AnalysisError,
        _fifo_sell,
        _STOP_REASONS,
        build_closed_trades,
        BetaEstimator,
        Lot,
        RunInfo,
        last_price,
        normalize_date,
        load_close_frame,
        load_equity_curve,
        load_fills,
        load_sector_map,
        require_tables,
        trading_days,
    )


def build_daily_beta_decomposition(
    conn: sqlite3.Connection,
    *,
    run_info: RunInfo,
    start_date: str,
    end_date: str,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    beta_lookback_days: int = DEFAULT_BETA_LOOKBACK,
    min_beta_observations: int = MIN_BETA_OBSERVATIONS,
) -> pd.DataFrame:
    require_tables(
        conn,
        (
            "symbols",
            "daily_bars",
            "backtest_runs",
            "backtest_orders",
            "backtest_fills",
            "backtest_equity_curve",
        ),
    )
    all_days = trading_days(
        conn,
        run_info.start_date,
        run_info.end_date,
        benchmark_code=benchmark_code,
    )
    requested_days = [day for day in all_days if start_date <= day <= end_date]
    if not requested_days:
        raise AnalysisError(f"No trading days found in {start_date}..{end_date}")

    fills = load_fills(conn, run_info.run_id)
    equity = load_equity_curve(conn, run_info.run_id).set_index("date")
    sector_map = load_sector_map(conn)
    codes = set(fills["code"].astype(str)) if not fills.empty else set()
    codes.add(benchmark_code)
    close_frame = load_close_frame(conn, codes=codes, end_date=end_date)
    beta_estimator = BetaEstimator(
        close_frame,
        benchmark_code=benchmark_code,
        lookback_days=beta_lookback_days,
        min_observations=min_beta_observations,
    )

    fills_by_day: dict[str, list[Any]] = defaultdict(list)
    for row in fills.itertuples(index=False):
        fills_by_day[normalize_date(row.filled_date)].append(row)

    lots_by_code: dict[str, deque[Lot]] = defaultdict(deque)
    previous_equity = run_info.initial_cash
    previous_strategy_equity: float | None = None
    previous_benchmark_close: float | None = None
    all_day_index = {day: index for index, day in enumerate(all_days)}
    rows: list[dict[str, Any]] = []

    for day in all_days:
        day_fills = fills_by_day.get(day, [])
        for fill in day_fills:
            code = str(fill.code)
            side = str(fill.side).upper()
            if side == "BUY":
                lots_by_code[code].append(
                    Lot(day, int(fill.quantity), float(fill.price))
                )
            elif side == "SELL":
                _fifo_sell(lots_by_code[code], int(fill.quantity))

        equity_row = equity.loc[day] if day in equity.index else None
        total_equity = (
            float(equity_row["total_equity"])
            if equity_row is not None and pd.notna(equity_row["total_equity"])
            else previous_equity
        )
        cash = (
            float(equity_row["cash"])
            if equity_row is not None and pd.notna(equity_row["cash"])
            else math.nan
        )
        position_value = (
            float(equity_row["position_value"])
            if equity_row is not None and pd.notna(equity_row["position_value"])
            else math.nan
        )

        positions: list[dict[str, Any]] = []
        beta_numerator = 0.0
        beta_weight_value = 0.0
        market_value_total = 0.0
        for code in sorted(lots_by_code):
            lots = lots_by_code[code]
            quantity = sum(lot.quantity for lot in lots)
            if quantity <= 0:
                continue
            price = last_price(close_frame, code, day)
            if price is None:
                continue
            market_value = price * quantity
            beta = beta_estimator.beta(code, day)
            first_entry_date = lots[0].entry_date
            entry_idx = all_day_index.get(first_entry_date)
            current_idx = all_day_index.get(day)
            holding_days = (
                current_idx - entry_idx + 1
                if entry_idx is not None and current_idx is not None
                else max(
                    1, (pd.Timestamp(day) - pd.Timestamp(first_entry_date)).days + 1
                )
            )
            market_value_total += market_value
            if beta is not None:
                beta_numerator += market_value * beta
                beta_weight_value += market_value
            positions.append(
                {
                    "code": code,
                    "sector": sector_map.get(code, "Unknown"),
                    "quantity": quantity,
                    "close": round(price, 6),
                    "market_value": round(market_value, 2),
                    "beta": round(beta, 6) if beta is not None else None,
                    "holding_days": holding_days,
                }
            )

        for position in positions:
            position["weight_pct"] = (
                round(position["market_value"] / market_value_total * 100.0, 4)
                if market_value_total > 0
                else 0.0
            )

        holdings_implied_beta = (
            beta_numerator / beta_weight_value if beta_weight_value > 0 else math.nan
        )
        beta_coverage_pct = (
            beta_weight_value / market_value_total * 100.0
            if market_value_total > 0
            else math.nan
        )

        benchmark_close = last_price(close_frame, benchmark_code, day)
        strategy_return = (
            total_equity / previous_strategy_equity - 1.0
            if previous_strategy_equity and previous_strategy_equity > 0
            else math.nan
        )
        benchmark_return = (
            benchmark_close / previous_benchmark_close - 1.0
            if benchmark_close is not None
            and previous_benchmark_close is not None
            and previous_benchmark_close > 0
            else math.nan
        )
        realized_beta_daily = (
            strategy_return / benchmark_return
            if pd.notna(strategy_return)
            and pd.notna(benchmark_return)
            and abs(float(benchmark_return)) > 1e-8
            else math.nan
        )

        new_entries: list[dict[str, Any]] = []
        exits: list[dict[str, Any]] = []
        gross_notional = 0.0
        for fill in day_fills:
            fill_beta = beta_estimator.beta(str(fill.code), day)
            item = {
                "code": str(fill.code),
                "quantity": int(fill.quantity),
                "price": round(float(fill.price), 6),
                "beta": round(fill_beta, 6) if fill_beta is not None else None,
            }
            gross_notional += abs(float(fill.price) * int(fill.quantity))
            if str(fill.side).upper() == "BUY":
                new_entries.append(item)
            elif str(fill.side).upper() == "SELL":
                item["exit_reason"] = str(fill.exit_reason or "unknown")
                exits.append(item)

        churn_today = (
            0.5 * gross_notional / previous_equity * 100.0
            if previous_equity > 0
            else math.nan
        )

        if day in requested_days:
            holding_days_map = {
                str(position["code"]): int(position["holding_days"])
                for position in positions
            }
            rows.append(
                {
                    "date": day,
                    "run_id": run_info.run_id,
                    "strategy": run_info.strategy_name,
                    "holdings_implied_beta": holdings_implied_beta,
                    "realized_beta_daily": realized_beta_daily,
                    "strategy_return_daily": strategy_return,
                    "benchmark_return_daily": benchmark_return,
                    "current_positions": json.dumps(
                        positions, ensure_ascii=False, separators=(",", ":")
                    ),
                    "current_position_count": len(positions),
                    "new_entries_today": json.dumps(
                        new_entries, ensure_ascii=False, separators=(",", ":")
                    ),
                    "new_entry_count": len(new_entries),
                    "exits_today": json.dumps(
                        exits, ensure_ascii=False, separators=(",", ":")
                    ),
                    "exit_count": len(exits),
                    "holding_days": json.dumps(
                        holding_days_map, ensure_ascii=False, separators=(",", ":")
                    ),
                    "churn_today": churn_today,
                    "beta_coverage_pct": beta_coverage_pct,
                    "cash": cash,
                    "position_value": position_value,
                    "total_equity": total_equity,
                }
            )

        previous_equity = total_equity
        previous_strategy_equity = total_equity
        previous_benchmark_close = benchmark_close

    frame = pd.DataFrame(rows)
    numeric_columns = [
        "holdings_implied_beta",
        "realized_beta_daily",
        "strategy_return_daily",
        "benchmark_return_daily",
        "churn_today",
        "beta_coverage_pct",
        "cash",
        "position_value",
        "total_equity",
    ]
    for column in numeric_columns:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def closed_trades_frame(
    conn: sqlite3.Connection,
    *,
    run_info: RunInfo,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
) -> pd.DataFrame:
    fills = load_fills(conn, run_info.run_id)
    sectors = load_sector_map(conn)
    days = trading_days(
        conn,
        run_info.start_date,
        run_info.end_date,
        benchmark_code=benchmark_code,
    )
    trades = build_closed_trades(fills, sector_map=sectors, all_trading_days=days)
    return pd.DataFrame(
        [
            {
                "code": trade.code,
                "entry_date": trade.entry_date,
                "exit_date": trade.exit_date,
                "quantity": trade.quantity,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "exit_reason": trade.exit_reason,
                "holding_days": trade.holding_days,
                "sector": trade.sector,
                "return_pct": trade.return_pct,
                "is_stop_loss": trade.exit_reason in _STOP_REASONS,
            }
            for trade in trades
        ]
    )
