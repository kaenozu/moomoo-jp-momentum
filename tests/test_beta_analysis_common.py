from __future__ import annotations

import math

import pandas as pd

from scripts.beta_analysis_common import (
    cap_sector_weights,
    monthly_metrics,
    target_beta_weights,
)


def test_cap_sector_weights_leaves_excess_as_cash() -> None:
    positions = [
        {"code": "A", "sector": "Tech", "market_value": 60.0},
        {"code": "B", "sector": "Tech", "market_value": 20.0},
        {"code": "C", "sector": "Food", "market_value": 20.0},
    ]

    weights, cash_weight = cap_sector_weights(
        positions,
        max_sector_weight=0.50,
    )

    assert math.isclose(weights["A"] + weights["B"], 0.50)
    assert math.isclose(weights["C"], 0.20)
    assert math.isclose(cash_weight, 0.30)
    assert math.isclose(sum(weights.values()) + cash_weight, 1.0)


def test_target_beta_weights_hits_attainable_target_without_shorting() -> None:
    positions = [
        {"code": "A", "market_value": 60.0, "beta": 1.40},
        {"code": "B", "market_value": 20.0, "beta": 0.80},
        {"code": "C", "market_value": 20.0, "beta": 0.60},
    ]

    weights, achieved_beta, reached = target_beta_weights(
        positions,
        target_beta=1.0,
    )

    assert reached is True
    assert math.isclose(sum(weights.values()), 1.0)
    assert all(weight >= 0 for weight in weights.values())
    assert math.isclose(achieved_beta, 1.0)


def test_target_beta_weights_clamps_unattainable_target() -> None:
    positions = [
        {"code": "A", "market_value": 50.0, "beta": 0.80},
        {"code": "B", "market_value": 50.0, "beta": 0.60},
    ]

    weights, achieved_beta, reached = target_beta_weights(
        positions,
        target_beta=1.0,
    )

    assert reached is False
    assert weights == {"A": 1.0, "B": 0.0}
    assert math.isclose(achieved_beta, 0.80)


def test_monthly_metrics_compounds_from_initial_equity() -> None:
    equity = pd.Series(
        [100.0, 101.0, 103.02, 104.0502],
        index=["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"],
    )
    benchmark = pd.Series(
        [100.0, 100.5, 101.0, 101.5],
        index=equity.index,
    )

    result = monthly_metrics(
        equity_series=equity,
        benchmark_series=benchmark,
        initial_equity=100.0,
        scenario="test",
        implementation="unit",
    )

    assert list(result["month"]) == ["2026-01", "2026-02", "2026-03"]
    assert math.isclose(result.iloc[0]["monthly_return_pct"], 0.0)
    assert math.isclose(result.iloc[1]["monthly_return_pct"], 3.02)
    assert math.isclose(result.iloc[2]["monthly_return_pct"], 1.0)
