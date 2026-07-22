"""Shared helpers for beta, turnover, holdings, and counterfactual analysis.

This module is intentionally read-only for normal analysis.  Counterfactual
callers should pass a temporary SQLite copy when they need BacktestRunner to
write new run rows.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

DEFAULT_BENCHMARK_CODE = "JP.2559"
DEFAULT_BETA_LOOKBACK = 60
MIN_BETA_OBSERVATIONS = 20
_STOP_REASONS = frozenset({"stop_loss", "trailing_stop"})


class AnalysisError(RuntimeError):
    """Raised when required backtest or market data is missing."""


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    strategy_name: str
    start_date: str
    end_date: str
    initial_cash: float


@dataclass
class Lot:
    entry_date: str
    quantity: int
    price: float


@dataclass(frozen=True)
class ClosedTrade:
    code: str
    entry_date: str
    exit_date: str
    quantity: int
    entry_price: float
    exit_price: float
    exit_reason: str
    holding_days: int
    sector: str

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return math.nan
        return (self.exit_price / self.entry_price - 1.0) * 100.0


def normalize_date(value: Any) -> str:
    """Return a SQLite-compatible YYYY-MM-DD date string."""
    text = str(value)
    return text[:10]


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def require_tables(conn: sqlite3.Connection, table_names: Sequence[str]) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    existing = {str(row[0]) for row in rows}
    missing = [name for name in table_names if name not in existing]
    if missing:
        raise AnalysisError(f"Required SQLite tables are missing: {', '.join(missing)}")


def get_run_info(conn: sqlite3.Connection, run_id: int) -> RunInfo:
    row = conn.execute(
        """
        SELECT id, strategy_name, start_date, end_date, initial_cash
        FROM backtest_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise AnalysisError(f"Backtest run not found: run_id={run_id}")
    return RunInfo(
        run_id=int(row["id"]),
        strategy_name=str(row["strategy_name"]),
        start_date=normalize_date(row["start_date"]),
        end_date=normalize_date(row["end_date"]),
        initial_cash=float(row["initial_cash"]),
    )


def select_run(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    start_date: str,
    end_date: str,
    run_id: int | None = None,
) -> RunInfo:
    """Select a run that covers the requested window.

    Explicit run IDs are validated rather than silently accepting a different
    strategy or a partial date range.
    """
    if run_id is not None:
        info = get_run_info(conn, run_id)
        if info.strategy_name != strategy_name:
            raise AnalysisError(
                f"run_id={run_id} strategy is {info.strategy_name!r}, "
                f"not {strategy_name!r}"
            )
        if info.start_date > start_date or info.end_date < end_date:
            raise AnalysisError(
                f"run_id={run_id} covers {info.start_date}..{info.end_date}, "
                f"not requested {start_date}..{end_date}"
            )
        return info

    row = conn.execute(
        """
        SELECT id, strategy_name, start_date, end_date, initial_cash
        FROM backtest_runs
        WHERE strategy_name = ?
          AND start_date <= ?
          AND end_date >= ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (strategy_name, start_date, end_date),
    ).fetchone()
    if row is None:
        raise AnalysisError(
            f"No {strategy_name!r} backtest run covers {start_date}..{end_date}. "
            "Specify --run-id or run historical_backtest.py first."
        )
    return RunInfo(
        run_id=int(row["id"]),
        strategy_name=str(row["strategy_name"]),
        start_date=normalize_date(row["start_date"]),
        end_date=normalize_date(row["end_date"]),
        initial_cash=float(row["initial_cash"]),
    )


def trading_days(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    *,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT date
        FROM daily_bars
        WHERE code = ? AND date >= ? AND date <= ? AND close IS NOT NULL
        ORDER BY date
        """,
        (benchmark_code, start_date, end_date),
    ).fetchall()
    days = [normalize_date(row[0]) for row in rows]
    if days:
        return days

    rows = conn.execute(
        """
        SELECT DISTINCT date
        FROM daily_bars
        WHERE date >= ? AND date <= ?
        ORDER BY date
        """,
        (start_date, end_date),
    ).fetchall()
    return [normalize_date(row[0]) for row in rows]


def load_sector_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT code, sector FROM symbols").fetchall()
    return {str(row[0]): (str(row[1]).strip() if row[1] else "Unknown") for row in rows}


def load_fills(conn: sqlite3.Connection, run_id: int) -> pd.DataFrame:
    order_columns = table_columns(conn, "backtest_orders")
    exit_expr = "o.exit_reason" if "exit_reason" in order_columns else "NULL"
    frame = pd.read_sql_query(
        f"""
        SELECT
            f.id,
            f.order_id,
            f.code,
            UPPER(f.side) AS side,
            f.quantity,
            f.price,
            substr(f.filled_at, 1, 10) AS filled_date,
            COALESCE({exit_expr}, '') AS exit_reason
        FROM backtest_fills AS f
        LEFT JOIN backtest_orders AS o ON o.id = f.order_id
        WHERE f.run_id = ?
        ORDER BY substr(f.filled_at, 1, 10), f.id
        """,
        conn,
        params=(run_id,),
    )
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "order_id",
                "code",
                "side",
                "quantity",
                "price",
                "filled_date",
                "exit_reason",
            ]
        )
    frame["filled_date"] = frame["filled_date"].astype(str).str[:10]
    frame["quantity"] = frame["quantity"].astype(int)
    frame["price"] = frame["price"].astype(float)
    return frame


def load_equity_curve(conn: sqlite3.Connection, run_id: int) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """
        SELECT date, cash, position_value, total_equity
        FROM backtest_equity_curve
        WHERE run_id = ?
        ORDER BY date
        """,
        conn,
        params=(run_id,),
    )
    if frame.empty:
        raise AnalysisError(f"No equity curve found for run_id={run_id}")
    frame["date"] = frame["date"].astype(str).str[:10]
    for column in ("cash", "position_value", "total_equity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_close_frame(
    conn: sqlite3.Connection,
    *,
    codes: Iterable[str],
    end_date: str,
) -> pd.DataFrame:
    code_list = sorted({str(code) for code in codes if code})
    if not code_list:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in code_list)
    query = f"""
        SELECT code, date, close
        FROM daily_bars
        WHERE code IN ({placeholders})
          AND date <= ?
          AND close IS NOT NULL
        ORDER BY date, code
    """
    frame = pd.read_sql_query(query, conn, params=(*code_list, end_date))
    if frame.empty:
        return pd.DataFrame()
    frame["date"] = frame["date"].astype(str).str[:10]
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.pivot_table(
        index="date", columns="code", values="close", aggfunc="last"
    ).sort_index()


def last_price(close_frame: pd.DataFrame, code: str, day: str) -> float | None:
    if code not in close_frame.columns:
        return None
    series = close_frame.loc[:day, code].dropna()
    if series.empty:
        return None
    value = float(series.iloc[-1])
    return value if math.isfinite(value) and value > 0 else None


class BetaEstimator:
    def __init__(
        self,
        close_frame: pd.DataFrame,
        *,
        benchmark_code: str = DEFAULT_BENCHMARK_CODE,
        lookback_days: int = DEFAULT_BETA_LOOKBACK,
        min_observations: int = MIN_BETA_OBSERVATIONS,
    ) -> None:
        self.close_frame = close_frame
        self.benchmark_code = benchmark_code
        self.lookback_days = max(2, int(lookback_days))
        self.min_observations = max(2, int(min_observations))
        self._cache: dict[tuple[str, str], float | None] = {}

    def beta(self, code: str, day: str) -> float | None:
        key = (code, day)
        if key in self._cache:
            return self._cache[key]
        if code == self.benchmark_code:
            self._cache[key] = 1.0
            return 1.0
        if (
            code not in self.close_frame.columns
            or self.benchmark_code not in self.close_frame.columns
        ):
            self._cache[key] = None
            return None

        pair = self.close_frame.loc[:day, [code, self.benchmark_code]].dropna()
        if len(pair) < self.min_observations + 1:
            self._cache[key] = None
            return None
        returns = (
            pair.pct_change(fill_method=None)
            .replace([math.inf, -math.inf], math.nan)
            .dropna()
        )
        returns = returns.tail(self.lookback_days)
        if len(returns) < self.min_observations:
            self._cache[key] = None
            return None
        benchmark_variance = float(returns[self.benchmark_code].var())
        if not math.isfinite(benchmark_variance) or benchmark_variance <= 1e-15:
            self._cache[key] = None
            return None
        covariance = float(returns[code].cov(returns[self.benchmark_code]))
        beta = covariance / benchmark_variance
        self._cache[key] = beta if math.isfinite(beta) else None
        return self._cache[key]


def _fifo_sell(lots: deque[Lot], quantity: int) -> list[tuple[Lot, int]]:
    remaining = int(quantity)
    matched: list[tuple[Lot, int]] = []
    while remaining > 0 and lots:
        lot = lots[0]
        take = min(remaining, lot.quantity)
        matched.append((Lot(lot.entry_date, take, lot.price), take))
        lot.quantity -= take
        remaining -= take
        if lot.quantity == 0:
            lots.popleft()
    return matched


def build_closed_trades(
    fills: pd.DataFrame,
    *,
    sector_map: Mapping[str, str],
    all_trading_days: Sequence[str],
) -> list[ClosedTrade]:
    day_index = {day: index for index, day in enumerate(all_trading_days)}
    lots_by_code: dict[str, deque[Lot]] = defaultdict(deque)
    trades: list[ClosedTrade] = []
    for row in fills.itertuples(index=False):
        code = str(row.code)
        side = str(row.side).upper()
        quantity = int(row.quantity)
        date = normalize_date(row.filled_date)
        price = float(row.price)
        if side == "BUY":
            lots_by_code[code].append(Lot(date, quantity, price))
            continue
        if side != "SELL":
            continue
        for lot, matched_qty in _fifo_sell(lots_by_code[code], quantity):
            start_idx = day_index.get(lot.entry_date)
            end_idx = day_index.get(date)
            holding_days = (
                end_idx - start_idx + 1
                if start_idx is not None and end_idx is not None
                else max(
                    1, (pd.Timestamp(date) - pd.Timestamp(lot.entry_date)).days + 1
                )
            )
            trades.append(
                ClosedTrade(
                    code=code,
                    entry_date=lot.entry_date,
                    exit_date=date,
                    quantity=matched_qty,
                    entry_price=lot.price,
                    exit_price=price,
                    exit_reason=str(row.exit_reason or "unknown"),
                    holding_days=holding_days,
                    sector=sector_map.get(code, "Unknown"),
                )
            )
    return trades
