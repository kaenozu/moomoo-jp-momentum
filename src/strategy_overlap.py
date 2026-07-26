"""バックテスト戦略間の重複・分散効果を分析する。"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EquityPoint:
    date: str
    total_equity: float


@dataclass(frozen=True)
class FillEvent:
    date: str
    code: str
    side: str
    quantity: int
    signal_date: str


@dataclass(frozen=True)
class StrategyOverlapSummary:
    overlap_start_date: str
    overlap_end_date: str
    aligned_return_days: int
    strategy_a_return_pct: float
    strategy_b_return_pct: float
    combined_50_50_return_pct: float
    strategy_a_max_drawdown_pct: float
    strategy_b_max_drawdown_pct: float
    combined_50_50_max_drawdown_pct: float
    daily_return_correlation: float | None
    same_direction_days_pct: float
    negative_day_jaccard_pct: float | None
    avg_holdings_jaccard_pct: float | None
    exact_entry_jaccard_pct: float | None
    code_month_entry_jaccard_pct: float | None
    symbol_jaccard_pct: float | None
    strategy_a_buy_entries: int
    strategy_b_buy_entries: int
    exact_entry_overlap_count: int
    symbol_overlap_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyOverlapResult:
    summary: StrategyOverlapSummary
    daily_rows: list[dict[str, Any]]
    entry_rows: list[dict[str, Any]]


def _equity_values(points: list[EquityPoint]) -> dict[str, float]:
    values: dict[str, float] = {}
    for point in points:
        if point.date in values:
            raise ValueError(f"duplicate equity date: {point.date}")
        if point.total_equity <= 0:
            raise ValueError(f"total_equity must be positive: {point.date}")
        values[point.date] = point.total_equity
    return values


def _aligned_daily_returns(
    points_a: list[EquityPoint],
    points_b: list[EquityPoint],
) -> tuple[str, list[str], list[float], list[float]]:
    values_a = _equity_values(points_a)
    values_b = _equity_values(points_b)
    common_points = sorted(set(values_a) & set(values_b))
    if len(common_points) < 2:
        raise ValueError("overlapping return dates were not found")

    dates: list[str] = []
    returns_a: list[float] = []
    returns_b: list[float] = []
    for previous_date, date in zip(common_points, common_points[1:], strict=False):
        dates.append(date)
        returns_a.append(values_a[date] / values_a[previous_date] - 1.0)
        returns_b.append(values_b[date] / values_b[previous_date] - 1.0)
    return common_points[0], dates, returns_a, returns_b


def _compound(returns: list[float]) -> float:
    value = 1.0
    for daily_return in returns:
        value *= 1.0 + daily_return
    return value - 1.0


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for daily_return in returns:
        equity *= 1.0 + daily_return
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _correlation(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) != len(values_b):
        raise ValueError("return series length mismatch")
    if len(values_a) < 2:
        return None
    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    numerator = sum(a * b for a, b in zip(centered_a, centered_b, strict=True))
    denominator = math.sqrt(
        sum(value * value for value in centered_a)
        * sum(value * value for value in centered_b)
    )
    if math.isclose(denominator, 0.0, abs_tol=1e-15):
        return None
    return numerator / denominator


def _jaccard(set_a: set[Any], set_b: set[Any]) -> float | None:
    union = set_a | set_b
    if not union:
        return None
    return len(set_a & set_b) / len(union)


def _apply_events(
    positions: dict[str, int],
    events: list[FillEvent],
    index: int,
    date: str,
) -> int:
    while index < len(events) and events[index].date <= date:
        event = events[index]
        current = positions.get(event.code, 0)
        if event.side == "BUY":
            positions[event.code] = current + event.quantity
        elif event.side == "SELL":
            remaining = current - event.quantity
            if remaining > 0:
                positions[event.code] = remaining
            else:
                positions.pop(event.code, None)
        index += 1
    return index


def calculate_strategy_overlap(
    equity_a: list[EquityPoint],
    equity_b: list[EquityPoint],
    fills_a: list[FillEvent],
    fills_b: list[FillEvent],
) -> StrategyOverlapResult:
    """2戦略の売買・保有・日次リターン重複を分析する。"""

    overlap_start_date, common_dates, daily_a, daily_b = _aligned_daily_returns(
        equity_a,
        equity_b,
    )
    overlap_end_date = common_dates[-1]

    ordered_fills_a = sorted(fills_a, key=lambda event: (event.date, event.code))
    ordered_fills_b = sorted(fills_b, key=lambda event: (event.date, event.code))
    positions_a: dict[str, int] = {}
    positions_b: dict[str, int] = {}
    index_a = 0
    index_b = 0
    daily_rows: list[dict[str, Any]] = []
    holdings_jaccards: list[float] = []

    combined: list[float] = []
    for date, return_a, return_b in zip(
        common_dates,
        daily_a,
        daily_b,
        strict=True,
    ):
        index_a = _apply_events(positions_a, ordered_fills_a, index_a, date)
        index_b = _apply_events(positions_b, ordered_fills_b, index_b, date)
        held_a = {code for code, quantity in positions_a.items() if quantity > 0}
        held_b = {code for code, quantity in positions_b.items() if quantity > 0}
        holdings_jaccard = _jaccard(held_a, held_b)
        if holdings_jaccard is not None:
            holdings_jaccards.append(holdings_jaccard)

        combined_return = (return_a + return_b) / 2.0
        combined.append(combined_return)
        daily_rows.append(
            {
                "date": date,
                "strategy_a_return_pct": return_a * 100.0,
                "strategy_b_return_pct": return_b * 100.0,
                "combined_50_50_return_pct": combined_return * 100.0,
                "same_direction": (return_a >= 0) == (return_b >= 0),
                "strategy_a_holdings": len(held_a),
                "strategy_b_holdings": len(held_b),
                "holdings_overlap_count": len(held_a & held_b),
                "holdings_jaccard_pct": (
                    holdings_jaccard * 100.0
                    if holdings_jaccard is not None
                    else None
                ),
            }
        )

    buy_a = [
        event
        for event in ordered_fills_a
        if event.side == "BUY"
        and overlap_start_date <= event.date <= overlap_end_date
    ]
    buy_b = [
        event
        for event in ordered_fills_b
        if event.side == "BUY"
        and overlap_start_date <= event.date <= overlap_end_date
    ]
    exact_a = {(event.code, event.signal_date) for event in buy_a}
    exact_b = {(event.code, event.signal_date) for event in buy_b}
    code_month_a = {(event.code, event.signal_date[:7]) for event in buy_a}
    code_month_b = {(event.code, event.signal_date[:7]) for event in buy_b}
    symbols_a = {event.code for event in buy_a}
    symbols_b = {event.code for event in buy_b}

    entry_rows = [
        {
            "code": code,
            "signal_date": signal_date,
            "in_strategy_a": (code, signal_date) in exact_a,
            "in_strategy_b": (code, signal_date) in exact_b,
        }
        for code, signal_date in sorted(exact_a | exact_b)
    ]

    negative_a = {
        date for date, value in zip(common_dates, daily_a, strict=True) if value < 0
    }
    negative_b = {
        date for date, value in zip(common_dates, daily_b, strict=True) if value < 0
    }
    same_direction_count = sum(
        1 for a, b in zip(daily_a, daily_b, strict=True) if (a >= 0) == (b >= 0)
    )

    negative_jaccard = _jaccard(negative_a, negative_b)
    exact_entry_jaccard = _jaccard(exact_a, exact_b)
    code_month_entry_jaccard = _jaccard(code_month_a, code_month_b)
    symbol_jaccard = _jaccard(symbols_a, symbols_b)

    summary = StrategyOverlapSummary(
        overlap_start_date=overlap_start_date,
        overlap_end_date=overlap_end_date,
        aligned_return_days=len(common_dates),
        strategy_a_return_pct=_compound(daily_a) * 100.0,
        strategy_b_return_pct=_compound(daily_b) * 100.0,
        combined_50_50_return_pct=_compound(combined) * 100.0,
        strategy_a_max_drawdown_pct=_max_drawdown(daily_a) * 100.0,
        strategy_b_max_drawdown_pct=_max_drawdown(daily_b) * 100.0,
        combined_50_50_max_drawdown_pct=_max_drawdown(combined) * 100.0,
        daily_return_correlation=_correlation(daily_a, daily_b),
        same_direction_days_pct=same_direction_count / len(common_dates) * 100.0,
        negative_day_jaccard_pct=(
            negative_jaccard * 100.0 if negative_jaccard is not None else None
        ),
        avg_holdings_jaccard_pct=(
            sum(holdings_jaccards) / len(holdings_jaccards) * 100.0
            if holdings_jaccards
            else None
        ),
        exact_entry_jaccard_pct=(
            exact_entry_jaccard * 100.0
            if exact_entry_jaccard is not None
            else None
        ),
        code_month_entry_jaccard_pct=(
            code_month_entry_jaccard * 100.0
            if code_month_entry_jaccard is not None
            else None
        ),
        symbol_jaccard_pct=(
            symbol_jaccard * 100.0 if symbol_jaccard is not None else None
        ),
        strategy_a_buy_entries=len(buy_a),
        strategy_b_buy_entries=len(buy_b),
        exact_entry_overlap_count=len(exact_a & exact_b),
        symbol_overlap_count=len(symbols_a & symbols_b),
    )
    return StrategyOverlapResult(
        summary=summary,
        daily_rows=daily_rows,
        entry_rows=entry_rows,
    )


def load_equity_points(db_path: str | Path, run_id: int) -> list[EquityPoint]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT date, total_equity
            FROM backtest_equity_curve
            WHERE run_id = ?
            ORDER BY date
            """,
            (run_id,),
        ).fetchall()
    if len(rows) < 2:
        raise ValueError(f"equity curve is insufficient: run_id={run_id}")
    return [EquityPoint(date=str(row[0]), total_equity=float(row[1])) for row in rows]


def load_fill_events(db_path: str | Path, run_id: int) -> list[FillEvent]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT
                substr(f.filled_at, 1, 10) AS fill_date,
                f.code,
                f.side,
                f.quantity,
                o.signal_date
            FROM backtest_fills AS f
            JOIN backtest_orders AS o ON o.id = f.order_id
            WHERE f.run_id = ?
            ORDER BY f.filled_at, f.id
            """,
            (run_id,),
        ).fetchall()
    return [
        FillEvent(
            date=str(row[0]),
            code=str(row[1]),
            side=str(row[2]).upper(),
            quantity=int(row[3]),
            signal_date=str(row[4] or row[0]),
        )
        for row in rows
    ]
