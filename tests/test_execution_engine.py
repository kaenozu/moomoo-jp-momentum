from __future__ import annotations

import pytest

from src.execution_engine import ExecutionEngine, PositionState


def test_buy_and_sell_transitions_share_one_accounting_model() -> None:
    engine = ExecutionEngine(commission=10, max_total_positions=3)
    bought = engine.apply_fill(1000, PositionState(), "BUY", 100, 5)
    assert bought.cash == 490
    assert bought.cash_delta == -510
    assert bought.gross == 500
    assert bought.commission == 10
    assert bought.position.quantity == 5
    assert bought.position.avg_cost == 102

    sold = engine.apply_fill(bought.cash, bought.position, "SELL", 120, 2)
    assert sold.cash == 720
    assert sold.cash_delta == 230
    assert sold.gross == 240
    assert sold.commission == 10
    assert sold.position.quantity == 3
    assert sold.position.avg_cost == 102
    assert sold.realized_pl_delta == 26
    assert sold.position.realized_pl == 26


def test_full_round_trip_reconciles_cash_realized_pl_and_total_fees() -> None:
    initial_cash = 1000.0
    quantity = 5
    buy_price = 100.0
    sell_price = 120.0
    engine = ExecutionEngine(commission=10)

    bought = engine.apply_fill(
        initial_cash,
        PositionState(),
        "BUY",
        buy_price,
        quantity,
    )
    sold = engine.apply_fill(
        bought.cash,
        bought.position,
        "SELL",
        sell_price,
        quantity,
    )

    total_fees = bought.commission + sold.commission
    gross_profit = (sell_price - buy_price) * quantity
    cash_profit = sold.cash - initial_cash

    assert total_fees == 20
    assert cash_profit == gross_profit - total_fees == 80
    assert sold.position.quantity == 0
    assert sold.realized_pl_delta == cash_profit
    assert sold.position.realized_pl == cash_profit


def test_weighted_average_cost_is_deterministic() -> None:
    engine = ExecutionEngine()
    first = engine.apply_fill(1000, PositionState(), "BUY", 100, 2)
    second = engine.apply_fill(first.cash, first.position, "BUY", 130, 2)
    assert second.position.quantity == 4
    assert second.position.avg_cost == 115


def test_reservations_and_slots_include_pending_buys() -> None:
    engine = ExecutionEngine(commission=5, max_total_positions=4)
    assert engine.reservation_total([(100, 2), (50, 1)]) == 260
    assert engine.available_cash(1000, 260) == 740
    assert engine.available_slots(2, 1) == 1


def test_fill_rejects_negative_cash_and_oversell() -> None:
    engine = ExecutionEngine(commission=1)
    with pytest.raises(ValueError, match="insufficient cash"):
        engine.apply_fill(100, PositionState(), "BUY", 100, 1)
    with pytest.raises(ValueError, match="insufficient position"):
        engine.apply_fill(
            100,
            PositionState(quantity=1, avg_cost=100),
            "SELL",
            110,
            2,
        )
