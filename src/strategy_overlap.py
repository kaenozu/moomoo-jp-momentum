"""バックテスト戦略間の重複度分析。

2つのrunを、実約定、復元した日末保有、同日entry、日次equity returnで比較する。
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd


_FILL_COLUMNS = {"code", "side", "quantity", "filled_at"}
_EQUITY_COLUMNS = {"date", "total_equity"}
_ENTRY_COLUMNS = pd.Index(
    ["code", "entry_date", "entered_a", "entered_b", "exact_overlap"]
)
_SYMBOL_COLUMNS = pd.Index(
    [
        "code",
        "traded_a",
        "traded_b",
        "common_symbol",
        "buy_count_a",
        "buy_count_b",
        "first_entry_a",
        "first_entry_b",
    ]
)


@dataclass(frozen=True)
class StrategyOverlapResult:
    """戦略重複度のサマリーと詳細テーブル。"""

    summary: pd.DataFrame
    daily: pd.DataFrame
    symbols: pd.DataFrame
    entries: pd.DataFrame


def _prepare_fills(fills: pd.DataFrame, label: str) -> pd.DataFrame:
    missing = sorted(_FILL_COLUMNS.difference(fills.columns))
    if missing:
        raise ValueError(f"{label} fillsに必要列がありません: {missing}")

    frame = fills.copy()
    if frame.empty:
        frame["filled_at"] = pd.to_datetime(frame["filled_at"])
        frame["side"] = frame["side"].astype(str)
        frame["quantity"] = pd.to_numeric(frame["quantity"])
        return frame

    frame["filled_at"] = pd.to_datetime(
        frame["filled_at"], errors="raise"
    ).dt.normalize()
    frame["code"] = frame["code"].astype(str)
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="raise")

    invalid_sides = sorted(
        set(frame.loc[~frame["side"].isin(["BUY", "SELL"]), "side"])
    )
    if invalid_sides:
        raise ValueError(f"{label} fillsに未対応sideがあります: {invalid_sides}")
    if bool((frame["quantity"] <= 0).to_numpy().any()):
        raise ValueError(f"{label} fillsのquantityは正数である必要があります")
    return frame.sort_values(["filled_at", "code", "side"]).reset_index(drop=True)


def _prepare_equity(equity: pd.DataFrame, label: str) -> pd.DataFrame:
    missing = sorted(_EQUITY_COLUMNS.difference(equity.columns))
    if missing:
        raise ValueError(f"{label} equityに必要列がありません: {missing}")
    if equity.empty:
        raise ValueError(f"{label} equityが空です")

    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    if bool(frame["date"].duplicated().to_numpy().any()):
        duplicate = cast(
            pd.Timestamp,
            frame.loc[frame["date"].duplicated(), "date"].iloc[0],
        )
        raise ValueError(f"{label} equityの日付が重複しています: {duplicate.date()}")
    frame["total_equity"] = pd.to_numeric(
        frame["total_equity"], errors="raise"
    )
    if bool(frame["total_equity"].isna().to_numpy().any()):
        raise ValueError(f"{label} equityに欠損があります")
    if bool((frame["total_equity"] <= 0).to_numpy().any()):
        raise ValueError(f"{label} total_equityは正数である必要があります")
    return frame.sort_values("date").reset_index(drop=True)


def _holding_matrix(
    fills: pd.DataFrame,
    dates: pd.DatetimeIndex,
    label: str,
) -> pd.DataFrame:
    codes = sorted(set(cast(pd.Series, fills["code"])))
    if not codes:
        return pd.DataFrame(index=dates)

    signed = fills.copy()
    signed["delta"] = signed["quantity"].where(
        signed["side"] == "BUY",
        -signed["quantity"],
    )
    daily_delta = (
        signed.groupby(["filled_at", "code"], sort=True)["delta"]
        .sum()
        .unstack(fill_value=0.0)
    )
    full_dates = daily_delta.index.union(dates).sort_values()
    holdings = daily_delta.reindex(full_dates, fill_value=0.0).cumsum()
    if bool((holdings < -1e-9).to_numpy().any()):
        raise ValueError(f"{label} fillsから負の保有数量が発生しました")
    holdings = (
        holdings.clip(lower=0.0)
        .reindex(dates, method="ffill")
        .fillna(0.0)
    )
    return holdings.reindex(columns=pd.Index(codes), fill_value=0.0)


def _event_keys(entries: pd.DataFrame) -> set[tuple[str, str]]:
    rows = entries.loc[:, ["code", "filled_at"]].itertuples(
        index=False,
        name=None,
    )
    return {
        (str(code), pd.Timestamp(filled_at).strftime("%Y-%m-%d"))
        for code, filled_at in rows
    }


def _safe_percent(numerator: int, denominator: int) -> float:
    return numerator / denominator * 100.0 if denominator else float("nan")


def _safe_correlation(left: pd.Series, right: pd.Series) -> float:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < 2:
        return float("nan")
    left_values = cast(pd.Series, paired.iloc[:, 0])
    right_values = cast(pd.Series, paired.iloc[:, 1])
    if math.isclose(float(left_values.std()), 0.0, abs_tol=1e-15):
        return float("nan")
    if math.isclose(float(right_values.std()), 0.0, abs_tol=1e-15):
        return float("nan")
    return float(left_values.corr(right_values))


def calculate_strategy_overlap(
    fills_a: pd.DataFrame,
    fills_b: pd.DataFrame,
    equity_a: pd.DataFrame,
    equity_b: pd.DataFrame,
) -> StrategyOverlapResult:
    """2つのバックテストrun間の重複度を計算する。"""
    prepared_fills_a = _prepare_fills(fills_a, "A")
    prepared_fills_b = _prepare_fills(fills_b, "B")
    prepared_equity_a = _prepare_equity(equity_a, "A")
    prepared_equity_b = _prepare_equity(equity_b, "B")

    common_dates = pd.DatetimeIndex(
        sorted(
            set(cast(pd.Series, prepared_equity_a["date"]))
            & set(cast(pd.Series, prepared_equity_b["date"]))
        )
    )
    if common_dates.empty:
        raise ValueError("2つのrunに共通するequity日付がありません")

    holdings_a = _holding_matrix(prepared_fills_a, common_dates, "A")
    holdings_b = _holding_matrix(prepared_fills_b, common_dates, "B")

    daily_rows: list[dict[str, Any]] = []
    for date in common_dates:
        held_a = set(holdings_a.columns[holdings_a.loc[date] > 0])
        held_b = set(holdings_b.columns[holdings_b.loc[date] > 0])
        intersection = held_a & held_b
        union = held_a | held_b
        both_active = bool(held_a and held_b)
        daily_rows.append(
            {
                "date": date,
                "positions_a": len(held_a),
                "positions_b": len(held_b),
                "common_positions": len(intersection),
                "union_positions": len(union),
                "holdings_jaccard_pct": _safe_percent(
                    len(intersection), len(union)
                ),
                "overlap_coefficient_pct": (
                    _safe_percent(
                        len(intersection),
                        min(len(held_a), len(held_b)),
                    )
                    if both_active
                    else float("nan")
                ),
                "codes_a": ",".join(sorted(held_a)),
                "codes_b": ",".join(sorted(held_b)),
                "common_codes": ",".join(sorted(intersection)),
            }
        )
    daily = pd.DataFrame(daily_rows)

    entries_a = prepared_fills_a.loc[prepared_fills_a["side"] == "BUY"].copy()
    entries_b = prepared_fills_b.loc[prepared_fills_b["side"] == "BUY"].copy()
    entry_keys_a = _event_keys(entries_a)
    entry_keys_b = _event_keys(entries_b)
    all_entry_keys = sorted(entry_keys_a | entry_keys_b)
    entry_rows = [
        {
            "code": code,
            "entry_date": date,
            "entered_a": (code, date) in entry_keys_a,
            "entered_b": (code, date) in entry_keys_b,
            "exact_overlap": (code, date) in entry_keys_a & entry_keys_b,
        }
        for code, date in all_entry_keys
    ]
    entries = pd.DataFrame.from_records(entry_rows, columns=_ENTRY_COLUMNS)

    codes_a = set(cast(pd.Series, entries_a["code"]))
    codes_b = set(cast(pd.Series, entries_b["code"]))
    all_codes = sorted(codes_a | codes_b)
    symbol_rows: list[dict[str, Any]] = []
    for code in all_codes:
        code_entries_a = entries_a.loc[entries_a["code"] == code, "filled_at"]
        code_entries_b = entries_b.loc[entries_b["code"] == code, "filled_at"]
        symbol_rows.append(
            {
                "code": code,
                "traded_a": code in codes_a,
                "traded_b": code in codes_b,
                "common_symbol": code in codes_a & codes_b,
                "buy_count_a": int(len(code_entries_a)),
                "buy_count_b": int(len(code_entries_b)),
                "first_entry_a": (
                    cast(pd.Timestamp, code_entries_a.min()).strftime("%Y-%m-%d")
                    if not code_entries_a.empty
                    else None
                ),
                "first_entry_b": (
                    cast(pd.Timestamp, code_entries_b.min()).strftime("%Y-%m-%d")
                    if not code_entries_b.empty
                    else None
                ),
            }
        )
    symbols = pd.DataFrame.from_records(symbol_rows, columns=_SYMBOL_COLUMNS)

    indexed_a = prepared_equity_a.set_index("date")["total_equity"].pct_change()
    indexed_b = prepared_equity_b.set_index("date")["total_equity"].pct_change()
    return_correlation = _safe_correlation(
        cast(pd.Series, indexed_a.reindex(common_dates)),
        cast(pd.Series, indexed_b.reindex(common_dates)),
    )

    active_jaccard = cast(
        pd.Series,
        daily.loc[daily["union_positions"] > 0, "holdings_jaccard_pct"],
    )
    both_active_overlap = cast(
        pd.Series,
        daily.loc[
            (daily["positions_a"] > 0) & (daily["positions_b"] > 0),
            "overlap_coefficient_pct",
        ],
    )
    common_symbols = codes_a & codes_b
    union_symbols = codes_a | codes_b
    common_entries = entry_keys_a & entry_keys_b
    union_entries = entry_keys_a | entry_keys_b
    common_start = pd.Timestamp(common_dates[0])
    common_end = pd.Timestamp(common_dates[-1])

    summary = pd.DataFrame(
        [
            {
                "common_start": common_start.strftime("%Y-%m-%d"),
                "common_end": common_end.strftime("%Y-%m-%d"),
                "common_days": len(common_dates),
                "active_comparison_days": int(
                    (daily["union_positions"] > 0).sum()
                ),
                "avg_daily_holdings_jaccard_pct": (
                    float(active_jaccard.mean())
                    if not active_jaccard.empty
                    else float("nan")
                ),
                "median_daily_holdings_jaccard_pct": (
                    float(active_jaccard.median())
                    if not active_jaccard.empty
                    else float("nan")
                ),
                "avg_overlap_coefficient_pct": (
                    float(both_active_overlap.mean())
                    if not both_active_overlap.empty
                    else float("nan")
                ),
                "max_common_positions": int(daily["common_positions"].max()),
                "traded_symbols_a": len(codes_a),
                "traded_symbols_b": len(codes_b),
                "common_traded_symbols": len(common_symbols),
                "traded_symbol_jaccard_pct": _safe_percent(
                    len(common_symbols), len(union_symbols)
                ),
                "entry_events_a": len(entry_keys_a),
                "entry_events_b": len(entry_keys_b),
                "exact_common_entry_events": len(common_entries),
                "exact_entry_jaccard_pct": _safe_percent(
                    len(common_entries), len(union_entries)
                ),
                "daily_return_correlation": return_correlation,
            }
        ]
    )

    return StrategyOverlapResult(
        summary=summary,
        daily=daily,
        symbols=symbols,
        entries=entries,
    )


def load_backtest_fills(db_path: str | Path, run_id: int) -> pd.DataFrame:
    """1つのbacktest runの約定を読み込む。"""
    query = """
        SELECT code, side, quantity, filled_at, price, fill_mode
        FROM backtest_fills
        WHERE run_id = ?
        ORDER BY filled_at, id
    """
    with sqlite3.connect(str(db_path)) as conn:
        return pd.read_sql_query(query, conn, params=[run_id])


def load_backtest_equity(db_path: str | Path, run_id: int) -> pd.DataFrame:
    """1つのbacktest runのequity観測を読み込む。"""
    query = """
        SELECT date, total_equity
        FROM backtest_equity_curve
        WHERE run_id = ?
        ORDER BY date
    """
    with sqlite3.connect(str(db_path)) as conn:
        frame = pd.read_sql_query(query, conn, params=[run_id])
    if frame.empty:
        raise ValueError(f"equity curveが見つかりません: run_id={run_id}")
    return frame
