import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.portfolio_beta_risk import PortfolioBetaRiskManager


class DummyConfig:
    def __init__(self, db_path: Path):
        self.database_path = str(db_path)
        self.values = {
            "risk_controls.min_portfolio_beta": 0.5,
            "risk_controls.min_portfolio_beta_holdings_lookback": 60,
        }

    def get(self, key_path, default=None):
        return self.values.get(key_path, default)


@dataclass
class Position:
    code: str
    quantity: int


@dataclass
class Order:
    id: int | None
    code: str
    side: str
    quantity: int


class FakeManager:
    def __init__(self, positions, cash=0.0, pending=None):
        self.positions = positions
        self.cash = cash
        self.pending = list(pending or [])
        self.cancelled = []
        self.placed = []

    def get_positions(self, strategy_name):
        return self.positions

    def get_cash(self, strategy_name, target_date=None):
        return self.cash

    def get_pending_orders(self, strategy_name):
        return list(self.pending)

    def cancel_order(self, order_id):
        for order in self.pending:
            if order.id == order_id:
                self.pending.remove(order)
                self.cancelled.append(order_id)
                return True
        return False

    def place_order(self, **kwargs):
        order = Order(
            id=100 + len(self.placed),
            code=kwargs["code"],
            side=kwargs["side"],
            quantity=kwargs["quantity"],
        )
        self.placed.append((order, kwargs))
        self.pending.append(order)
        return order


def _prices(returns, start=100.0):
    values = [start]
    for value in returns:
        values.append(values[-1] * (1.0 + value))
    return values


def _create_db(tmp_path, asset_beta=0.4, asset_days=60):
    db_path = tmp_path / "risk.db"
    pattern = [0.010, -0.004, 0.006, -0.008, 0.003]
    benchmark_returns = (pattern * 12)[:60]
    asset_returns = [asset_beta * value for value in benchmark_returns[:asset_days]]
    start = date(2026, 1, 1)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_bars (code TEXT, date TEXT, close REAL)")
        for code, prices in {
            "JP.2559": _prices(benchmark_returns),
            "JP.1111": _prices(asset_returns),
        }.items():
            for index, close in enumerate(prices):
                conn.execute(
                    "INSERT INTO daily_bars(code, date, close) VALUES (?, ?, ?)",
                    (code, (start + timedelta(days=index)).isoformat(), close),
                )
    return db_path


def test_low_beta_cancels_buys_and_schedules_trim(tmp_path):
    db_path = _create_db(tmp_path, asset_beta=0.4)
    manager = FakeManager(
        [Position("JP.1111", 10)],
        cash=0.0,
        pending=[Order(1, "JP.2222", "BUY", 1)],
    )

    decision = PortfolioBetaRiskManager(DummyConfig(db_path)).apply(
        manager,
        "default",
        "2026-03-02",
    )

    assert decision.snapshot.holdings_implied_beta == pytest.approx(0.4)
    assert decision.snapshot.target_investment_ratio == pytest.approx(0.8)
    assert decision.cancelled_buy_orders == 1
    assert decision.trim_orders == 1
    assert manager.placed[0][1]["side"] == "SELL"
    assert manager.placed[0][1]["exit_reason"] == "beta_floor"
    assert decision.max_new_investment == 0.0


def test_missing_beta_history_fails_open_without_orders(tmp_path):
    db_path = _create_db(tmp_path, asset_beta=0.4, asset_days=10)
    manager = FakeManager([Position("JP.1111", 10)], cash=5_000.0)

    decision = PortfolioBetaRiskManager(DummyConfig(db_path)).apply(
        manager,
        "default",
        "2026-03-02",
    )

    assert decision.snapshot.holdings_implied_beta is None
    assert decision.snapshot.target_investment_ratio == 1.0
    assert decision.trim_orders == 0
    assert decision.max_new_investment == pytest.approx(5_000.0)
