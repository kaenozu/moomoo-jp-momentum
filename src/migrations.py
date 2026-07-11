"""SQLite schema migrations used by runtime components."""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def migrate_virtual_orders_reserved_amount(conn: sqlite3.Connection) -> None:
    """Add and normalize ``virtual_orders.reserved_amount`` idempotently.

    The column is intentionally nullable. Legacy pending BUY orders therefore
    fall back to their historical reference price instead of being treated as
    having a zero reservation.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'virtual_orders'"
    ).fetchone()
    if table_exists is None:
        return

    columns = conn.execute("PRAGMA table_info(virtual_orders)").fetchall()
    if not any(column[1] == "reserved_amount" for column in columns):
        conn.execute("ALTER TABLE virtual_orders ADD COLUMN reserved_amount REAL")
        logger.info("migration: added virtual_orders.reserved_amount")

    # Earlier development builds used DEFAULT 0. Zero cannot be a valid BUY
    # reservation, so normalize it to NULL to restore the legacy fallback.
    conn.execute(
        """
        UPDATE virtual_orders
        SET reserved_amount = NULL
        WHERE side = 'BUY'
          AND status = 'PENDING'
          AND reserved_amount = 0
        """
    )
