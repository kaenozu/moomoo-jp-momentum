import pytest

from src.backtest_runner import BacktestRunner, _PendingOrder


def test_backtest_schedules_integer_beta_floor_reductions():
    runner = BacktestRunner.__new__(BacktestRunner)
    runner.slippage_bps = 10
    runner.commission = 0
    runner._get_all_positions = lambda: [
        {"code": f"JP.{index:04d}", "quantity": 1, "avg_cost": 900.0}
        for index in range(20)
    ]
    runner._next_open_bar = lambda code, day: ("2026-03-03", 1_000.0)
    position_values = {f"JP.{index:04d}": 1_000.0 for index in range(20)}
    pending_orders = []

    created, scheduled_value = runner._schedule_beta_floor_reductions(
        "2026-03-02",
        position_values,
        16_000.0,
        pending_orders,
    )

    assert created == 4
    assert scheduled_value == pytest.approx(4_000.0)
    assert len(pending_orders) == 4
    assert all(order.side == "SELL" for order in pending_orders)
    assert all(order.exit_reason == "beta_floor" for order in pending_orders)


def test_backtest_cancels_pending_buys_and_releases_cash():
    runner = BacktestRunner.__new__(BacktestRunner)
    runner.commission = 0
    runner.reserved_cash = 3_000.0
    pending_orders = [
        _PendingOrder("JP.1111", "BUY", 2, 1_000.0, "2026-03-03", "2026-03-02"),
        _PendingOrder("JP.2222", "SELL", 1, 1_000.0, "2026-03-03", "2026-03-02"),
    ]

    cancelled = runner._cancel_pending_buys(pending_orders)

    assert cancelled == 1
    assert runner.reserved_cash == pytest.approx(1_000.0)
    assert len(pending_orders) == 1
    assert pending_orders[0].side == "SELL"
