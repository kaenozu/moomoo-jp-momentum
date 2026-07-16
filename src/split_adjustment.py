"""
株式分割・受益権分割の価格調整モジュール。

rawのdaily_barsは変更せず、読み込み時に分割前のOHLCへ調整係数を適用する。
これにより、分割日をまたぐリターン・移動平均・バックテスト価格を連続系列として扱える。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


CORPORATE_ACTION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    action_type TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    ratio_before REAL NOT NULL,
    ratio_after REAL NOT NULL,
    adjustment_factor REAL NOT NULL,
    source_name TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(code, action_type, effective_date)
);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    observed_value REAL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(code, date, flag_type)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_code_date
    ON corporate_actions(code, effective_date);
CREATE INDEX IF NOT EXISTS idx_data_quality_flags_code_date
    ON data_quality_flags(code, date);
"""


CONFIRMED_CORPORATE_ACTIONS = (
    (
        "JP.1306",
        "split",
        "2026-04-01",
        1.0,
        10.0,
        0.1,
        "user-confirmed",
        "confirmed",
        "受益権1口を10口へ分割",
    ),
    (
        "JP.2559",
        "split",
        "2026-06-09",
        1.0,
        10.0,
        0.1,
        "user-confirmed",
        "confirmed",
        "受益権1口を10口へ分割",
    ),
)

PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class SplitAction:
    """確定済みの分割情報。"""

    code: str
    effective_date: str
    adjustment_factor: float


def initialize_corporate_action_schema(db_path: str | Path) -> None:
    """テーブルを作成し、確認済み分割情報を冪等に登録する。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(CORPORATE_ACTION_SCHEMA_SQL)
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
            """,
            CONFIRMED_CORPORATE_ACTIONS,
        )


class SplitAdjustmentService:
    """DB上の確定済み分割情報を使って価格系列を調整する。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        initialize_corporate_action_schema(self.db_path)
        self._cache: dict[str, tuple[SplitAction, ...]] = {}

    def refresh(self) -> None:
        """corporate_actions更新後にキャッシュを破棄する。"""
        self._cache.clear()

    def get_splits(self, code: str) -> tuple[SplitAction, ...]:
        """銘柄の確定済み分割を日付昇順で返す。"""
        if code not in self._cache:
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT code, effective_date, adjustment_factor
                    FROM corporate_actions
                    WHERE code = ?
                      AND action_type = 'split'
                      AND status = 'confirmed'
                    ORDER BY effective_date
                    """,
                    (code,),
                ).fetchall()
            self._cache[code] = tuple(
                SplitAction(
                    code=str(row[0]),
                    effective_date=str(row[1])[:10],
                    adjustment_factor=float(row[2]),
                )
                for row in rows
                if row[2] is not None and float(row[2]) > 0
            )
        return self._cache[code]

    def adjust_price(
        self,
        code: str,
        date: str,
        price: float | int | None,
    ) -> float | None:
        """単一価格を分割後口数ベースへ調整する。"""
        if price is None:
            return None

        adjusted = float(price)
        normalized_date = str(date)[:10]
        for action in self.get_splits(code):
            if normalized_date < action.effective_date:
                adjusted *= action.adjustment_factor
        return adjusted

    def apply_to_dataframe(
        self,
        df: pd.DataFrame,
        code: str,
        *,
        date_column: str | None = None,
        price_columns: Iterable[str] = PRICE_COLUMNS,
    ) -> pd.DataFrame:
        """DataFrameの分割前OHLCを調整したコピーを返す。"""
        if df.empty:
            return df.copy()

        actions = self.get_splits(code)
        if not actions:
            return df.copy()

        resolved_date_column = date_column
        if resolved_date_column is None:
            if "date" in df.columns:
                resolved_date_column = "date"
            elif "time_key" in df.columns:
                resolved_date_column = "time_key"
            else:
                raise ValueError("分割調整にはdateまたはtime_key列が必要です")

        adjusted = df.copy()
        normalized_dates = adjusted[resolved_date_column].astype(str).str.slice(0, 10)

        for action in actions:
            mask = normalized_dates < action.effective_date
            if not mask.any():
                continue
            for column in price_columns:
                if column not in adjusted.columns:
                    continue
                numeric = pd.to_numeric(adjusted.loc[mask, column], errors="coerce")
                adjusted.loc[mask, column] = numeric * action.adjustment_factor

        return adjusted
