"""
Core data model for the US grid strategy.

State machine notes:

- A ``GridLevel`` represents one resting order (either a BUY below center or a
  SELL above the fill price of a paired BUY). A level is identified by its
  ``level_index`` and side within a ``GridInstance``.
- ``OrderStatus`` follows: PLANNED -> SUBMITTING -> OPEN -> PARTIALLY_FILLED
  -> FILLED, with CANCEL_PENDING -> CANCELLED and REJECTED as terminal states.
  UNKNOWN is used when a broker callback cannot be mapped.
- Idempotency: each order carries a ``client_order_key`` that is persisted
  before submission. Retrying the same logical order reuses the key so a
  duplicate cannot be created.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


def _new_client_order_key() -> str:
    return uuid.uuid4().hex


def deterministic_client_order_key(
    strategy_name: str,
    grid_instance_id: int | None,
    grid_level_index: int | None,
    generation: int,
    side: str,
) -> str:
    """Deterministic idempotency key for a grid level order.

    Re-running the same logical order (same instance/level/generation/side)
    always produces the same key, which the broker adapter uses to detect and
    skip duplicates.
    """
    raw = f"{strategy_name}|{grid_instance_id}|{grid_level_index}|{generation}|{side}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OrderStatus(str, Enum):
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class GridLevelStatus(str, Enum):
    ACTIVE = "ACTIVE"  # resting order (open or planned)
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    INACTIVE = "INACTIVE"  # waiting to be re-armed after a paired fill


class Regime(str, Enum):
    ACTIVE_RANGE = "ACTIVE_RANGE"
    TREND_UP = "TREND_UP"
    RISK_OFF = "RISK_OFF"
    UNKNOWN = "UNKNOWN"


@dataclass
class GridLevel:
    level_index: int
    side: str  # BUY / SELL
    target_price: float
    quantity: int
    status: GridLevelStatus = GridLevelStatus.ACTIVE
    paired_level_index: Optional[int] = None
    last_order_id: Optional[int] = None
    last_order_key: Optional[str] = None
    activated_at: Optional[str] = None
    filled_at: Optional[str] = None
    fill_price: Optional[float] = None


@dataclass
class GridInstance:
    id: Optional[int] = None
    strategy_name: str = "us_adaptive_grid_v1"
    code: str = ""
    mode: str = "backtest"
    status: str = "RUNNING"  # RUNNING / PAUSED / CLOSED
    center_price: Optional[float] = None
    spacing_pct: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    core_quantity: int = 0
    grid_quantity: int = 0
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    closed_at: Optional[str] = None
    version: int = 1

    # runtime state (not necessarily persisted)
    levels: list[GridLevel] = field(default_factory=list)
    last_recenter_at: Optional[str] = None


@dataclass
class DesiredOrder:
    """A strategy-level desired order before risk approval."""

    code: str
    side: str  # BUY / SELL
    quantity: int
    limit_price: float
    grid_instance_id: Optional[int] = None
    grid_level_index: Optional[int] = None
    reason: str = ""


@dataclass
class ApprovedOrder:
    """An order approved by the risk engine, ready for execution."""

    code: str
    side: str
    quantity: int
    limit_price: float
    client_order_key: str = field(default_factory=_new_client_order_key)
    grid_instance_id: Optional[int] = None
    grid_level_index: Optional[int] = None
    reason: str = ""


@dataclass
class GridOrderRecord:
    """A persisted order record (mirrors the grid_orders SQLite table)."""

    id: Optional[int] = None
    strategy_name: str = ""
    code: str = ""
    side: str = ""
    quantity: int = 0
    filled_quantity: int = 0
    limit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PLANNED
    client_order_key: str = ""
    grid_instance_id: Optional[int] = None
    grid_level_index: Optional[int] = None
    submitted_at: Optional[str] = None
    filled_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    broker_order_id: Optional[str] = None
    broker_status: Optional[str] = None
    last_error: Optional[str] = None
    version: int = 1


@dataclass
class GridFillRecord:
    """A fill (or partial fill) record."""

    id: Optional[int] = None
    order_id: int = 0
    strategy_name: str = ""
    code: str = ""
    side: str = ""
    quantity: int = 0
    price: float = 0.0
    filled_at: str = ""
    fill_mode: str = ""  # limit_touch / next_bar_open / partial
