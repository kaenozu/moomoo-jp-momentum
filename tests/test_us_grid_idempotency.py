"""Unit tests for idempotency / order state machine contracts."""

from __future__ import annotations

from src.us_grid.model import (
    GridFillRecord,
    GridOrderRecord,
    OrderStatus,
    deterministic_client_order_key,
)


def test_deterministic_key_is_stable() -> None:
    key1 = deterministic_client_order_key(
        "us_fixed_grid_v1", 3, 2, 1, "BUY"
    )
    key2 = deterministic_client_order_key(
        "us_fixed_grid_v1", 3, 2, 1, "BUY"
    )
    assert key1 == key2


def test_deterministic_key_differs_by_side() -> None:
    buy = deterministic_client_order_key("s", 3, 2, 1, "BUY")
    sell = deterministic_client_order_key("s", 3, 2, 1, "SELL")
    assert buy != sell


def test_deterministic_key_differs_by_generation() -> None:
    gen1 = deterministic_client_order_key("s", 3, 2, 1, "BUY")
    gen2 = deterministic_client_order_key("s", 3, 2, 2, "BUY")
    assert gen1 != gen2


def test_order_status_state_machine() -> None:
    """Terminal and intermediate states are distinct and stable."""
    statuses = {s.value for s in OrderStatus}
    assert "PLANNED" in statuses
    assert "SUBMITTING" in statuses
    assert "OPEN" in statuses
    assert "PARTIALLY_FILLED" in statuses
    assert "FILLED" in statuses
    assert "CANCELLED" in statuses
    assert "REJECTED" in statuses
    assert "UNKNOWN" in statuses


def test_order_record_defaults() -> None:
    record = GridOrderRecord(strategy_name="s", code="US.SPY", side="BUY")
    assert record.status == OrderStatus.PLANNED
    assert record.filled_quantity == 0
    assert record.version == 1


def test_fill_record_links_order() -> None:
    fill = GridFillRecord(
        order_id=7,
        strategy_name="s",
        code="US.SPY",
        side="BUY",
        quantity=1,
        price=100.0,
        filled_at="2026-01-05",
    )
    assert fill.order_id == 7
    assert fill.quantity == 1
