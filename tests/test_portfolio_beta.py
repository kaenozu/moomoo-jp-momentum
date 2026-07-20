import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.portfolio_beta import BETA_BENCHMARK_CODE, HoldingsBetaFloor


class DummyConfig:
    def __init__(self, db_path: Path, floor: float = 0.5, lookback: int = 60):
        self.database_path = str(db_path)
        self.values = {
            "risk_controls.min_portfolio_beta": floor,
            "risk_controls.min_portfolio_beta_holdings_lookback": lookback,
        }

    def get(self, key_path, default=None):
        return self.values.get(key_path, default)


def _prices_from_returns(returns: list[float], start: float = 100.0) -> list[float]:
    prices = [start]
    for ret in returns:
        prices.append(prices[-1] * (1.0 + ret))
    return prices


def _create_db(tmp_path: Path, series: dict[str, list[float]]) -> Path:
    db_path = tmp_path / "beta.db"
    start = date(2026, 1, 1)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_bars (code TEXT, date TEXT, close REAL)")
        for code, prices in series.items():
            for index, close in enumerate(prices):
                conn.execute(
                    "INSERT INTO daily_bars(code, date, close) VALUES (?, ?, ?)",
                    (code, (start + timedelta(days=index)).isoformat(), close),
                )
    return db_path


def _benchmark_returns() -> list[float]:
    pattern = [0.010, -0.004, 0.006, -0.008, 0.003]
    return (pattern * 12)[:60]


def test_rolling_beta_and_floor_ratio(tmp_path):
    benchmark_returns = _benchmark_returns()
    asset_returns = [0.4 * value for value in benchmark_returns]
    db_path = _create_db(
        tmp_path,
        {
            BETA_BENCHMARK_CODE: _prices_from_returns(benchmark_returns),
            "JP.1111": _prices_from_returns(asset_returns),
        },
    )
    controller = HoldingsBetaFloor(DummyConfig(db_path))

    snapshot = controller.evaluate({"JP.1111": 100_000.0}, "2026-03-02")

    assert snapshot.holdings_implied_beta == pytest.approx(0.4)
    assert snapshot.target_investment_ratio == pytest.approx(0.8)
    assert snapshot.covered_weight == pytest.approx(1.0)


def test_market_value_weighted_holdings_beta(tmp_path):
    benchmark_returns = _benchmark_returns()
    db_path = _create_db(
        tmp_path,
        {
            BETA_BENCHMARK_CODE: _prices_from_returns(benchmark_returns),
            "JP.1111": _prices_from_returns([0.2 * value for value in benchmark_returns]),
            "JP.2222": _prices_from_returns([0.8 * value for value in benchmark_returns]),
        },
    )
    controller = HoldingsBetaFloor(DummyConfig(db_path))

    snapshot = controller.evaluate(
        {"JP.1111": 25_000.0, "JP.2222": 75_000.0},
        "2026-03-02",
    )

    assert snapshot.holdings_implied_beta == pytest.approx(0.65)
    assert snapshot.target_investment_ratio == pytest.approx(1.0)


def test_missing_history_fails_open(tmp_path):
    benchmark_returns = _benchmark_returns()
    db_path = _create_db(
        tmp_path,
        {
            BETA_BENCHMARK_CODE: _prices_from_returns(benchmark_returns),
            "JP.1111": _prices_from_returns([0.3 * value for value in benchmark_returns]),
            "JP.2222": _prices_from_returns([0.3 * value for value in benchmark_returns[:10]]),
        },
    )
    controller = HoldingsBetaFloor(DummyConfig(db_path))

    snapshot = controller.evaluate(
        {"JP.1111": 50_000.0, "JP.2222": 50_000.0},
        "2026-03-02",
    )

    assert snapshot.holdings_implied_beta is None
    assert snapshot.target_investment_ratio == pytest.approx(1.0)
    assert snapshot.covered_weight == pytest.approx(0.5)
    assert snapshot.missing_codes == ("JP.2222",)


def test_disabled_floor_keeps_full_investment(tmp_path):
    benchmark_returns = _benchmark_returns()
    db_path = _create_db(
        tmp_path,
        {
            BETA_BENCHMARK_CODE: _prices_from_returns(benchmark_returns),
            "JP.1111": _prices_from_returns([0.2 * value for value in benchmark_returns]),
        },
    )
    controller = HoldingsBetaFloor(
        DummyConfig(db_path),
        enabled_override=False,
    )

    snapshot = controller.evaluate({"JP.1111": 100_000.0}, "2026-03-02")

    assert snapshot.holdings_implied_beta == pytest.approx(0.2)
    assert snapshot.target_investment_ratio == pytest.approx(1.0)
