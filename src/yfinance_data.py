"""yfinance日足の取得・正規化・安全なDB保存。

価格は ``auto_adjust=True`` で取得し、株式分割による価格系列の断絶を
yfinance側で調整する。分割イベント自体は corporate_actions に記録し、
既存のmoomoo行は上書きしない。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DetectedSplit:
    """yfinanceから検出した株式分割。"""

    effective_date: str
    ratio: float


@dataclass(frozen=True)
class YFinanceFetchResult:
    """正規化済み日足と検出済み分割。"""

    bars: pd.DataFrame
    splits: tuple[DetectedSplit, ...]


@dataclass(frozen=True)
class UpsertStats:
    """daily_bars保存結果。"""

    inserted: int
    updated: int
    preserved: int

    @property
    def written(self) -> int:
        return self.inserted + self.updated


def to_yfinance_ticker(code: str) -> str:
    """``JP.7203`` を ``7203.T`` へ変換する。"""
    if not code.startswith("JP."):
        raise ValueError(f"日本株コードではありません: {code}")
    local_code = code.removeprefix("JP.")
    if not local_code.isdigit():
        raise ValueError(f"日本株コード形式が不正です: {code}")
    return f"{local_code}.T"


def _exclusive_end(end_date: str) -> str:
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return (end + timedelta(days=1)).strftime("%Y-%m-%d")


def _normalize_history(data: pd.DataFrame) -> YFinanceFetchResult:
    if data.empty:
        return YFinanceFetchResult(pd.DataFrame(), ())

    normalized = data.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Stock Splits": "stock_splits",
        }
    ).copy()
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(normalized.columns))
    if missing:
        raise ValueError(f"yfinance応答に必要列がありません: {missing}")

    normalized = normalized.reset_index()
    date_column = "Date" if "Date" in normalized.columns else normalized.columns[0]
    normalized["time_key"] = pd.to_datetime(normalized[date_column]).dt.strftime(
        "%Y-%m-%d"
    )
    normalized = (
        normalized.sort_values("time_key")
        .drop_duplicates(subset=["time_key"], keep="last")
        .reset_index(drop=True)
    )

    accepted_rows: list[dict[str, Any]] = []
    splits: list[DetectedSplit] = []
    for _, row in normalized.iterrows():
        try:
            prices = tuple(
                float(row[column]) for column in ("open", "high", "low", "close")
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if any(not pd.notna(price) or price <= 0 for price in prices):
            continue

        try:
            volume = int(row["volume"]) if pd.notna(row["volume"]) else 0
        except (TypeError, ValueError, OverflowError):
            volume = 0
        split_ratio = row.get("stock_splits", 0)
        try:
            normalized_split_ratio = float(split_ratio)
        except (TypeError, ValueError, OverflowError):
            normalized_split_ratio = 0.0
        if pd.notna(normalized_split_ratio) and normalized_split_ratio > 0:
            splits.append(
                DetectedSplit(
                    effective_date=str(row["time_key"]),
                    ratio=normalized_split_ratio,
                )
            )

        accepted_rows.append(
            {
                "time_key": str(row["time_key"]),
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": max(volume, 0),
                "turnover": max(volume, 0) * prices[3],
                "source": "yfinance",
                "turnover_source": "estimated",
            }
        )

    return YFinanceFetchResult(pd.DataFrame(accepted_rows), tuple(splits))


def fetch_adjusted_history(
    code: str,
    start_date: str,
    end_date: str,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> YFinanceFetchResult:
    """yfinanceから調整済み日足と分割イベントを取得する。

    ``end_date`` は利用者視点では包含。yfinanceの排他的endへ変換する。
    ``auto_adjust=True`` によりOHLCを分割・配当調整済みで取得する。
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError(f"取得期間が不正です: start={start_date}, end={end_date}")

    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker

    ticker_name = to_yfinance_ticker(code)
    ticker = ticker_factory(ticker_name)
    data = ticker.history(
        start=start_date,
        end=_exclusive_end(end_date),
        interval="1d",
        auto_adjust=True,
        actions=True,
    )
    if not isinstance(data, pd.DataFrame):
        raise TypeError(f"yfinance応答がDataFrameではありません: {code}")
    return _normalize_history(data)


def record_splits(
    db_path: str | Path,
    code: str,
    splits: tuple[DetectedSplit, ...],
) -> int:
    """検出した分割を corporate_actions へ冪等登録する。"""
    if not splits:
        return 0

    params = []
    for split in splits:
        ratio = float(split.ratio)
        if ratio <= 0:
            continue
        params.append(
            (
                code,
                "split",
                split.effective_date,
                1.0,
                ratio,
                1.0 / ratio,
                "yfinance",
                "confirmed",
                "yfinance auto_adjust=Trueで価格調整済み。追加調整は不要。",
            )
        )

    if not params:
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO corporate_actions (
                code, action_type, effective_date, ratio_before, ratio_after,
                adjustment_factor, source_name, status, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, action_type, effective_date) DO UPDATE SET
                ratio_before = excluded.ratio_before,
                ratio_after = excluded.ratio_after,
                adjustment_factor = excluded.adjustment_factor,
                source_name = excluded.source_name,
                status = excluded.status,
                notes = excluded.notes
            WHERE corporate_actions.source_name = 'yfinance'
            """,
            params,
        )
    return len(params)


def upsert_yfinance_bars(
    db_path: str | Path,
    code: str,
    bars: pd.DataFrame,
) -> UpsertStats:
    """yfinance日足を保存する。moomoo行は維持し、yfinance行のみ更新する。"""
    if bars.empty:
        return UpsertStats(0, 0, 0)

    normalized_rows: list[tuple[Any, ...]] = []
    for _, row in bars.iterrows():
        normalized_rows.append(
            (
                code,
                str(row["time_key"])[:10],
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                int(row["volume"]),
                float(row["turnover"]),
                "yfinance",
                "estimated",
            )
        )

    with sqlite3.connect(str(db_path)) as conn:
        existing_rows = conn.execute(
            "SELECT date, source FROM daily_bars WHERE code = ?",
            (code,),
        ).fetchall()
        existing = {str(row[0]): str(row[1] or "").lower() for row in existing_rows}

        inserts = [row for row in normalized_rows if row[1] not in existing]
        updates = [
            (
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[0],
                row[1],
            )
            for row in normalized_rows
            if existing.get(str(row[1])) == "yfinance"
        ]
        preserved = sum(
            1
            for row in normalized_rows
            if row[1] in existing and existing[str(row[1])] != "yfinance"
        )

        if inserts:
            conn.executemany(
                """
                INSERT INTO daily_bars (
                    code, date, open, high, low, close, volume, turnover,
                    source, turnover_source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                inserts,
            )
        if updates:
            conn.executemany(
                """
                UPDATE daily_bars
                SET open = ?, high = ?, low = ?, close = ?, volume = ?,
                    turnover = ?, source = ?, turnover_source = ?
                WHERE code = ? AND date = ?
                """,
                updates,
            )

    return UpsertStats(len(inserts), len(updates), preserved)


def latest_bar_date(
    db_path: str | Path,
    code: str,
    *,
    end_date: str | None = None,
) -> str | None:
    """対象銘柄のDB最新日を返す。"""
    query = "SELECT MAX(date) FROM daily_bars WHERE code = ?"
    params: list[str] = [code]
    if end_date is not None:
        query += " AND date <= ?"
        params.append(end_date)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(query, params).fetchone()
    return str(row[0]) if row and row[0] else None


def incremental_start_date(
    db_path: str | Path,
    code: str,
    target_date: str,
    *,
    fallback_days: int = 120,
) -> str:
    """差分取得開始日。既存最新日を再取得し、yfinance行の修正を許容する。"""
    latest = latest_bar_date(db_path, code, end_date=target_date)
    if latest:
        return latest
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    return (target - timedelta(days=fallback_days)).strftime("%Y-%m-%d")


def is_stale_unavailable(
    bars: pd.DataFrame,
    *,
    end_date: str,
    grace_days: int = 180,
) -> bool:
    """取得結果が長期間更新されていない銘柄を上場廃止候補として分類する。"""
    if bars.empty:
        return True
    latest = datetime.strptime(str(bars["time_key"].max())[:10], "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return (end - latest).days > grace_days
