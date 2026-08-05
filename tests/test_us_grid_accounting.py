"""Unit tests for cash/position accounting invariants."""

from __future__ import annotations

import pytest

from src.us_grid.accounting import CashPosition
from src.us_grid.config import CostModel, GridConfig


def _grid() -> GridConfig:
    return GridConfig(
        costs=CostModel(
            commission_mode="percentage",
            commission_rate=0.0,
            minimum_commission_usd=0.0,
        ),
    )


def test_buy_debits_cash() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 2, 100.0, 150.0, "2026-01-05")
    assert state.cash_usd == pytest.approx(800.0)
    assert state.positions["US.SPY"] == 2
    assert state.avg_cost_usd["US.SPY"] == pytest.approx(100.0)


def test_buy_exceeding_cash_raises() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=50.0, usd_jpy=150.0, initial_cash_jpy=7500.0)
    with pytest.raises(ValueError):
        state.buy(grid, "US.SPY", 2, 100.0, 150.0, "2026-01-05")


def test_sell_credits_cash_and_realized() -> None:
    grid = GridConfig(
        costs=CostModel(
            commission_mode="percentage",
            commission_rate=0.0,
            minimum_commission_usd=0.0,
            sell_regulatory_fee_enabled=False,
        )
    )
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 1, 100.0, 150.0, "2026-01-05")
    state.sell(grid, "US.SPY", 1, 110.0, 150.0, "2026-01-10")
    assert state.positions.get("US.SPY", 0) == 0
    assert state.cash_usd == pytest.approx(1000.0 - 100.0 + 110.0)
    assert state.realized_pl_usd["US.SPY"] == pytest.approx(10.0)


def test_sell_exceeding_holdings_raises() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 1, 100.0, 150.0, "2026-01-05")
    with pytest.raises(ValueError):
        state.sell(grid, "US.SPY", 2, 110.0, 150.0, "2026-01-10")


def test_no_naked_sell() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    with pytest.raises(ValueError):
        state.sell(grid, "US.QQQ", 1, 200.0, 150.0, "2026-01-05")


def test_equity_identity() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 1, 100.0, 150.0, "2026-01-05")
    prices = {"US.SPY": 105.0}
    equity = state.total_equity_usd(prices)
    assert equity == pytest.approx(state.cash_usd + state.market_value_usd(prices))


def test_dividend_credit() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 10, 100.0, 150.0, "2026-01-05")
    state.apply_dividend("US.SPY", 10, 0.5)
    assert state.cash_usd == pytest.approx(1000.0 - 1000.0 + 5.0)
    assert state.dividend_income_usd == pytest.approx(5.0)


def test_split_adjusts_quantity() -> None:
    grid = _grid()
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 1, 400.0, 150.0, "2026-01-05")
    state.apply_split("US.SPY", 10.0)
    assert state.positions["US.SPY"] == 10
    assert state.avg_cost_usd["US.SPY"] == pytest.approx(40.0)


def test_commission_debit() -> None:
    grid = GridConfig(
        costs=CostModel(
            commission_mode="percentage",
            commission_rate=0.00132,
            minimum_commission_usd=0.01,
        )
    )
    state = CashPosition(cash_usd=1000.0, usd_jpy=150.0, initial_cash_jpy=150000.0)
    state.buy(grid, "US.SPY", 1, 100.0, 150.0, "2026-01-05")
    fee = 100.0 * 0.00132
    assert state.cash_usd == pytest.approx(1000.0 - 100.0 - fee)
    assert state.fee_total_usd == pytest.approx(fee)
