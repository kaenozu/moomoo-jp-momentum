"""Monthly metrics and counterfactual portfolio overlay helpers."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import pandas as pd

try:
    from .beta_analysis_data import (
        DEFAULT_BENCHMARK_CODE,
        AnalysisError,
        RunInfo,
        load_close_frame,
        load_fills,
    )
    from .beta_analysis_daily import build_daily_beta_decomposition
except ImportError:  # Direct execution from scripts/.
    from beta_analysis_data import (
        DEFAULT_BENCHMARK_CODE,
        AnalysisError,
        RunInfo,
        load_close_frame,
        load_fills,
    )
    from beta_analysis_daily import build_daily_beta_decomposition


def sector_return_contribution(
    conn: sqlite3.Connection,
    *,
    run_info: RunInfo,
    start_date: str,
    end_date: str,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
) -> pd.DataFrame:
    """Approximate daily sector contribution using prior-close holdings weights."""
    decomposition = build_daily_beta_decomposition(
        conn,
        run_info=run_info,
        start_date=run_info.start_date,
        end_date=end_date,
        benchmark_code=benchmark_code,
    )
    fills = load_fills(conn, run_info.run_id)
    codes = set(fills["code"].astype(str)) if not fills.empty else set()
    close_frame = load_close_frame(conn, codes=codes, end_date=end_date)
    daily_returns = close_frame.pct_change(fill_method=None).replace(
        [math.inf, -math.inf], math.nan
    )

    contributions: dict[str, float] = defaultdict(float)
    previous_positions: list[dict[str, Any]] = []
    previous_total_equity = run_info.initial_cash
    for row in decomposition.itertuples(index=False):
        day = str(row.date)
        current_positions = parse_positions_json(str(row.current_positions))
        if (
            start_date <= day <= end_date
            and previous_total_equity > 0
            and day in daily_returns.index
        ):
            for position in previous_positions:
                code = str(position["code"])
                sector = str(position.get("sector") or "Unknown")
                market_value = float(position.get("market_value") or 0.0)
                code_return = (
                    daily_returns.at[day, code]
                    if code in daily_returns.columns
                    else math.nan
                )
                if pd.isna(code_return):
                    continue
                contributions[sector] += (
                    market_value / previous_total_equity * float(code_return) * 100.0
                )
        previous_positions = current_positions
        if pd.notna(row.total_equity) and float(row.total_equity) > 0:
            previous_total_equity = float(row.total_equity)

    return pd.DataFrame(
        [
            {"sector": sector, "contribution_pct": value}
            for sector, value in sorted(
                contributions.items(), key=lambda item: abs(item[1]), reverse=True
            )
        ]
    )


def monthly_metrics(
    *,
    equity_series: pd.Series,
    benchmark_series: pd.Series,
    initial_equity: float,
    scenario: str,
    implementation: str,
) -> pd.DataFrame:
    equity = equity_series.dropna().astype(float).sort_index()
    benchmark = benchmark_series.dropna().astype(float).sort_index()
    if equity.empty:
        raise AnalysisError(f"No equity observations for scenario {scenario}")

    frame = pd.concat(
        [equity.rename("equity"), benchmark.rename("benchmark")], axis=1
    ).dropna(subset=["equity"])
    frame.index = pd.to_datetime(frame.index)
    frame["strategy_return_daily"] = frame["equity"].pct_change(fill_method=None)
    frame["benchmark_return_daily"] = frame["benchmark"].pct_change(fill_method=None)

    result: list[dict[str, Any]] = []
    previous_equity = float(initial_equity)
    previous_benchmark: float | None = None
    for period, group in frame.groupby(frame.index.to_period("M")):
        last_equity = float(group["equity"].dropna().iloc[-1])
        strategy_return = last_equity / previous_equity - 1.0

        benchmark_values = group["benchmark"].dropna()
        benchmark_return = math.nan
        if not benchmark_values.empty:
            first_reference = (
                previous_benchmark
                if previous_benchmark is not None
                else float(benchmark_values.iloc[0])
            )
            last_benchmark = float(benchmark_values.iloc[-1])
            if first_reference > 0:
                benchmark_return = last_benchmark / first_reference - 1.0
            previous_benchmark = last_benchmark

        paired = group[["strategy_return_daily", "benchmark_return_daily"]].dropna()
        beta = math.nan
        if len(paired) >= 2:
            variance = float(paired["benchmark_return_daily"].var())
            if math.isfinite(variance) and variance > 1e-15:
                beta = float(
                    paired["strategy_return_daily"].cov(
                        paired["benchmark_return_daily"]
                    )
                    / variance
                )
        result.append(
            {
                "scenario": scenario,
                "implementation": implementation,
                "month": str(period),
                "monthly_return_pct": strategy_return * 100.0,
                "benchmark_return_pct": benchmark_return * 100.0
                if pd.notna(benchmark_return)
                else math.nan,
                "active_return_pct": (strategy_return - benchmark_return) * 100.0
                if pd.notna(benchmark_return)
                else math.nan,
                "realized_beta": beta,
                "month_end_equity": last_equity,
                "observation_count": int(len(group)),
            }
        )
        previous_equity = last_equity
    return pd.DataFrame(result)


def cap_sector_weights(
    positions: Sequence[Mapping[str, Any]],
    *,
    max_sector_weight: float,
) -> tuple[dict[str, float], float]:
    """Cap normalized holdings weights by sector; excess remains cash."""
    max_weight = float(max_sector_weight)
    if max_weight > 1.0:
        max_weight /= 100.0
    if not 0 < max_weight <= 1:
        raise ValueError("max_sector_weight must be in (0, 1] or (0, 100]")

    values = {
        str(position["code"]): max(0.0, float(position.get("market_value") or 0.0))
        for position in positions
    }
    total = sum(values.values())
    if total <= 0:
        return {}, 1.0
    base_weights = {code: value / total for code, value in values.items()}
    sector_by_code = {
        str(position["code"]): str(position.get("sector") or "Unknown")
        for position in positions
    }
    sector_totals: dict[str, float] = defaultdict(float)
    for code, weight in base_weights.items():
        sector_totals[sector_by_code[code]] += weight

    result: dict[str, float] = {}
    for code, weight in base_weights.items():
        sector_total = sector_totals[sector_by_code[code]]
        scale = min(1.0, max_weight / sector_total) if sector_total > 0 else 0.0
        result[code] = weight * scale
    cash_weight = max(0.0, 1.0 - sum(result.values()))
    return result, cash_weight


def target_beta_weights(
    positions: Sequence[Mapping[str, Any]],
    *,
    target_beta: float,
) -> tuple[dict[str, float], float, bool]:
    """Tilt normalized holdings weights to the requested beta without shorting.

    The method minimally mixes the baseline portfolio with the single highest-
    or lowest-beta holding.  It reaches the target exactly when the target lies
    within the available holdings' beta range; otherwise it returns the nearest
    attainable long-only portfolio.
    """
    usable = [
        position
        for position in positions
        if position.get("beta") is not None
        and math.isfinite(float(position["beta"]))
        and float(position.get("market_value") or 0.0) > 0
    ]
    total = sum(float(position["market_value"]) for position in usable)
    if total <= 0:
        return {}, math.nan, False
    base = {
        str(position["code"]): float(position["market_value"]) / total
        for position in usable
    }
    betas = {str(position["code"]): float(position["beta"]) for position in usable}
    current_beta = sum(base[code] * betas[code] for code in base)
    target = float(target_beta)
    if math.isclose(current_beta, target, rel_tol=1e-9, abs_tol=1e-9):
        return base, current_beta, True

    if target > current_beta:
        anchor = max(betas, key=betas.get)
    else:
        anchor = min(betas, key=betas.get)
    anchor_beta = betas[anchor]
    denominator = anchor_beta - current_beta
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        return base, current_beta, False

    alpha = (target - current_beta) / denominator
    reached = 0.0 <= alpha <= 1.0
    alpha = min(1.0, max(0.0, alpha))
    weights = {code: (1.0 - alpha) * weight for code, weight in base.items()}
    weights[anchor] = weights.get(anchor, 0.0) + alpha
    achieved = sum(weights[code] * betas[code] for code in weights)
    return weights, achieved, reached


def parse_positions_json(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def overlay_equity_curve(
    *,
    decomposition: pd.DataFrame,
    close_frame: pd.DataFrame,
    initial_equity: float,
    mode: str,
    max_sector_weight: float = 0.25,
    target_beta: float = 1.0,
    idle_benchmark_code: str | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Build a daily-close counterfactual from BacktestRunner holdings.

    Rebalancing occurs at each close and applies to the next close-to-close
    return.  This preserves the runner's selected holdings and trade timing but
    changes only portfolio weights.
    """
    if decomposition.empty:
        raise AnalysisError("Cannot build overlay from an empty decomposition")
    returns = close_frame.pct_change(fill_method=None).replace(
        [math.inf, -math.inf], math.nan
    )
    equity = float(initial_equity)
    equity_points: dict[str, float] = {}
    diagnostics: list[dict[str, Any]] = []
    previous_weights: dict[str, float] = {}
    previous_cash_weight = 1.0
    previous_achieved_beta = math.nan
    previous_reached = False

    for row in decomposition.sort_values("date").itertuples(index=False):
        day = str(row.date)
        if day in returns.index:
            daily_return = 0.0
            for code, weight in previous_weights.items():
                value = returns.at[day, code] if code in returns.columns else math.nan
                if pd.notna(value):
                    daily_return += weight * float(value)
            if (
                idle_benchmark_code
                and previous_cash_weight > 0
                and idle_benchmark_code in returns.columns
            ):
                idle_return = returns.at[day, idle_benchmark_code]
                if pd.notna(idle_return):
                    daily_return += previous_cash_weight * float(idle_return)
            equity *= 1.0 + daily_return
        equity_points[day] = equity

        positions = parse_positions_json(str(row.current_positions))
        invested_ratio = (
            float(row.position_value) / float(row.total_equity)
            if pd.notna(row.position_value)
            and pd.notna(row.total_equity)
            and float(row.total_equity) > 0
            else 0.0
        )
        invested_ratio = min(1.0, max(0.0, invested_ratio))
        if mode == "sector_cap":
            normalized, overlay_cash = cap_sector_weights(
                positions, max_sector_weight=max_sector_weight
            )
            previous_weights = {
                code: weight * invested_ratio for code, weight in normalized.items()
            }
            previous_cash_weight = 1.0 - sum(previous_weights.values())
            previous_achieved_beta = math.nan
            previous_reached = True
        elif mode == "beta_target":
            normalized, achieved, reached = target_beta_weights(
                positions, target_beta=target_beta
            )
            previous_weights = {
                code: weight * invested_ratio for code, weight in normalized.items()
            }
            previous_cash_weight = 1.0 - sum(previous_weights.values())
            previous_achieved_beta = achieved
            previous_reached = reached
        else:
            raise ValueError(f"Unknown overlay mode: {mode}")

        diagnostics.append(
            {
                "date": day,
                "mode": mode,
                "invested_ratio": invested_ratio,
                "cash_weight": previous_cash_weight,
                "achieved_holdings_beta": previous_achieved_beta,
                "target_reached": previous_reached,
                "position_count": len(previous_weights),
            }
        )

    return pd.Series(equity_points, name="total_equity", dtype=float), pd.DataFrame(
        diagnostics
    )


def benchmark_close_series(
    conn: sqlite3.Connection,
    *,
    benchmark_code: str,
    start_date: str,
    end_date: str,
) -> pd.Series:
    frame = pd.read_sql_query(
        """
        SELECT date, close
        FROM daily_bars
        WHERE code = ? AND date >= ? AND date <= ? AND close IS NOT NULL
        ORDER BY date
        """,
        conn,
        params=(benchmark_code, start_date, end_date),
    )
    if frame.empty:
        raise AnalysisError(
            f"No benchmark prices for {benchmark_code} in {start_date}..{end_date}"
        )
    frame["date"] = frame["date"].astype(str).str[:10]
    return frame.set_index("date")["close"].astype(float)


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, encoding="utf-8-sig")
    return output


def month_bounds(month: str) -> tuple[str, str]:
    period = pd.Period(month, freq="M")
    return period.start_time.strftime("%Y-%m-%d"), period.end_time.strftime("%Y-%m-%d")


def safe_slug(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )


def iter_months(start_date: str, end_date: str) -> Iterator[str]:
    for period in pd.period_range(start=start_date, end=end_date, freq="M"):
        yield str(period)
