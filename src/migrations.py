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


def migrate_virtual_orders_pending_index(conn: sqlite3.Connection) -> None:
    """Allow one pending order per strategy, symbol, side, and submission date.

    Earlier schemas allowed only one pending order for a symbol and side across
    the entire database. That made historical replay fail when a future-dated
    pending order already existed, even though validation correctly ignored it.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'virtual_orders'"
    ).fetchone()
    if table_exists is None:
        return

    index_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_virtual_orders_pending'"
    ).fetchone()
    index_sql = str(index_row[0] or "") if index_row else ""
    normalized_index_sql = index_sql.lower()
    if "coalesce(substr(submitted_at, 1, 10), '')" in normalized_index_sql:
        return

    conn.execute("DROP INDEX IF EXISTS idx_virtual_orders_pending")
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_virtual_orders_pending
        ON virtual_orders(
            strategy_name,
            code,
            side,
            COALESCE(substr(submitted_at, 1, 10), '')
        )
        WHERE status = 'PENDING'
        """
    )
    logger.info("migration: scoped pending virtual-order uniqueness by date")
