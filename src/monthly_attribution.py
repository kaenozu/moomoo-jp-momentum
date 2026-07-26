"""バックテスト月別超過リターンの寄与度分析。

比較ベンチマークに対する超過リターンを次の2要因へ分解する。

- cash drag: 前日キャッシュ比率により取り逃したベンチマークリターン
- residual effect: 銘柄選択、売買タイミング、執行コスト、およびcash残高へ
  組み込まれたidle-cash overlayを含む残差

日次寄与はCarinoの対数リンク法で月次の算術超過リターンへ厳密に再調整する。
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd


_REQUIRED_COLUMNS = {"date", "cash", "total_equity", "benchmark_value"}
_BENCHMARK_COLUMNS = {
    "1306": "benchmark_1306_value",
    "JP.1306": "benchmark_1306_value",
    "2559": "benchmark_2559_value",
    "JP.2559": "benchmark_2559_value",
}


@dataclass(frozen=True)
class BacktestRun:
    """永続化済みバックテストのメタデータ。"""

    run_id: int
    strategy_name: str
    start_date: str
    end_date: str
    initial_cash: float


@dataclass(frozen=True)
class AttributionResult:
    """日次・月次の寄与度テーブル。"""

    daily: pd.DataFrame
    monthly: pd.DataFrame


def _carino_coefficient(strategy_return: float, benchmark_return: float) -> float:
    """1リンク区間のCarino係数を返す。"""
    if strategy_return <= -1.0 or benchmark_return <= -1.0:
        raise ValueError("リターンは-100%より大きい必要があります")
    difference = strategy_return - benchmark_return
    if math.isclose(difference, 0.0, abs_tol=1e-14):
        return 1.0 / (1.0 + strategy_return)
    return (
        math.log1p(strategy_return) - math.log1p(benchmark_return)
    ) / difference


def _compound(returns: pd.Series) -> float:
    """小数表記の日次リターンを複利集計する。"""
    result = 1.0
    for value in returns:
        result *= 1.0 + float(value)
    return result - 1.0


def _prepare_equity_curve(equity_curve: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(_REQUIRED_COLUMNS.difference(equity_curve.columns))
    if missing:
        raise ValueError(f"必要列がありません: {missing}")
    if equity_curve.empty:
        raise ValueError("equity curveが空です")

    frame = equity_curve.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].duplicated().any():
        duplicates = frame.loc[frame["date"].duplicated(), "date"].dt.strftime(
            "%Y-%m-%d"
        )
        raise ValueError(f"dateが重複しています: {duplicates.iloc[0]}")

    frame = frame.sort_values("date").reset_index(drop=True)
    numeric_columns = ["cash", "total_equity", "benchmark_value"]
    if "drawdown_pct" in frame.columns:
        numeric_columns.append("drawdown_pct")
    else:
        frame["drawdown_pct"] = float("nan")

    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")

    required_values = frame[["cash", "total_equity", "benchmark_value"]]
    if bool(required_values.isna().to_numpy().any()):
        raise ValueError("cash, total_equity, benchmark_valueに欠損があります")
    if (frame["total_equity"] <= 0).any():
        raise ValueError("total_equityは正の値である必要があります")
    if (frame["benchmark_value"] <= 0).any():
        raise ValueError("benchmark_valueは正の値である必要があります")

    cash_weight = frame["cash"] / frame["total_equity"]
    tolerance = 1e-9
    if (cash_weight < -tolerance).any() or (cash_weight > 1.0 + tolerance).any():
        raise ValueError("cashは0以上かつtotal_equity以下である必要があります")
    frame["cash_weight"] = cash_weight.clip(lower=0.0, upper=1.0)
    return frame


def calculate_monthly_attribution(
    equity_curve: pd.DataFrame,
) -> AttributionResult:
    """月別の超過リターン寄与を計算する。

    日次リターンは終了観測日の月へ帰属させる。入力の先頭行は比較元が
    存在しないため、リターン0のベースラインとして扱う。
    """
    frame = _prepare_equity_curve(equity_curve)

    frame["strategy_return"] = frame["total_equity"].pct_change().fillna(0.0)
    frame["benchmark_return"] = frame["benchmark_value"].pct_change().fillna(0.0)
    frame["lagged_cash_weight"] = (
        frame["cash_weight"].shift(1).fillna(frame["cash_weight"])
    )
    frame["invested_weight"] = 1.0 - frame["lagged_cash_weight"]
    frame["active_return"] = (
        frame["strategy_return"] - frame["benchmark_return"]
    )
    frame["cash_drag"] = (
        -frame["lagged_cash_weight"] * frame["benchmark_return"]
    )
    frame["residual_effect"] = (
        frame["strategy_return"]
        - frame["invested_weight"] * frame["benchmark_return"]
    )

    reconciliation = (
        frame["active_return"]
        - frame["cash_drag"]
        - frame["residual_effect"]
    ).abs()
    if float(reconciliation.max()) > 1e-12:
        raise RuntimeError("日次寄与の再調整に失敗しました")

    frame["carino_k"] = [
        _carino_coefficient(float(strategy), float(benchmark))
        for strategy, benchmark in zip(
            frame["strategy_return"],
            frame["benchmark_return"],
            strict=True,
        )
    ]
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    frame["is_baseline"] = False
    frame.loc[0, "is_baseline"] = True

    monthly_rows: list[dict[str, float | int | str]] = []
    for month, group in frame.groupby("month", sort=True):
        strategy_returns = cast(pd.Series, group["strategy_return"])
        benchmark_returns = cast(pd.Series, group["benchmark_return"])
        strategy_return = _compound(strategy_returns)
        benchmark_return = _compound(benchmark_returns)
        active_return = strategy_return - benchmark_return
        period_k = _carino_coefficient(strategy_return, benchmark_return)

        cash_drag = float(
            (group["cash_drag"] * group["carino_k"] / period_k).sum()
        )
        residual_effect = float(
            (group["residual_effect"] * group["carino_k"] / period_k).sum()
        )
        reconciliation_error = active_return - cash_drag - residual_effect

        drawdown = cast(pd.Series, group["drawdown_pct"]).dropna()
        max_drawdown = (
            float(drawdown.max()) if not drawdown.empty else float("nan")
        )
        monthly_rows.append(
            {
                "month": str(month),
                "strategy_return_pct": strategy_return * 100.0,
                "benchmark_return_pct": benchmark_return * 100.0,
                "active_return_pct": active_return * 100.0,
                "cash_drag_pct": cash_drag * 100.0,
                "residual_effect_pct": residual_effect * 100.0,
                "avg_cash_weight_pct": float(
                    group["lagged_cash_weight"].mean() * 100.0
                ),
                "avg_invested_weight_pct": float(
                    group["invested_weight"].mean() * 100.0
                ),
                "max_drawdown_pct": max_drawdown,
                "trading_days": int(len(group)),
                "reconciliation_error_bps": reconciliation_error * 10000.0,
            }
        )

    daily_columns = [
        "date",
        "month",
        "is_baseline",
        "cash",
        "total_equity",
        "benchmark_value",
        "lagged_cash_weight",
        "invested_weight",
        "strategy_return",
        "benchmark_return",
        "active_return",
        "cash_drag",
        "residual_effect",
        "drawdown_pct",
    ]
    return AttributionResult(
        daily=frame.loc[:, daily_columns].copy(),
        monthly=pd.DataFrame(monthly_rows),
    )


def normalize_benchmark_code(benchmark_code: str) -> str:
    """対応ベンチマークを数値ローカルコードへ正規化する。"""
    normalized = benchmark_code.strip().upper()
    if normalized not in _BENCHMARK_COLUMNS:
        supported = ", ".join(sorted(_BENCHMARK_COLUMNS))
        raise ValueError(
            f"未対応benchmarkです: {benchmark_code} (supported: {supported})"
        )
    return normalized.removeprefix("JP.")


def resolve_backtest_run(
    db_path: str | Path,
    *,
    run_id: int | None = None,
    strategy_name: str | None = None,
) -> BacktestRun:
    """指定run、または指定strategyの最新runを解決する。"""
    if (run_id is None) == (strategy_name is None):
        raise ValueError("run_idまたはstrategy_nameのどちらか一方を指定してください")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if run_id is not None:
            row = conn.execute(
                """
                SELECT id, strategy_name, start_date, end_date, initial_cash
                FROM backtest_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, strategy_name, start_date, end_date, initial_cash
                FROM backtest_runs
                WHERE strategy_name = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (strategy_name,),
            ).fetchone()

    if row is None:
        target = (
            f"run_id={run_id}"
            if run_id is not None
            else f"strategy_name={strategy_name}"
        )
        raise ValueError(f"backtest runが見つかりません: {target}")

    return BacktestRun(
        run_id=int(row["id"]),
        strategy_name=str(row["strategy_name"]),
        start_date=str(row["start_date"]),
        end_date=str(row["end_date"]),
        initial_cash=float(row["initial_cash"]),
    )


def load_backtest_equity_curve(
    db_path: str | Path,
    run_id: int,
    benchmark_code: str = "1306",
) -> pd.DataFrame:
    """SQLiteから寄与度分析用equity curveを読み込む。"""
    local_code = normalize_benchmark_code(benchmark_code)
    benchmark_column = _BENCHMARK_COLUMNS[local_code]

    query = f"""
        SELECT
            date,
            cash,
            total_equity,
            {benchmark_column} AS benchmark_value,
            drawdown_pct
        FROM backtest_equity_curve
        WHERE run_id = ?
        ORDER BY date
    """
    with sqlite3.connect(str(db_path)) as conn:
        frame = pd.read_sql_query(query, conn, params=[run_id])

    if frame.empty:
        raise ValueError(f"equity curveが見つかりません: run_id={run_id}")
    return frame
