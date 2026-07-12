"""Temporary branch-local patch applicator; removed by its workflow."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def update_models() -> None:
    path = Path("src/models.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_orders_pending ON virtual_orders(strategy_name, code, side) WHERE status = 'PENDING';",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_orders_pending ON virtual_orders(strategy_name, code, side, substr(submitted_at, 1, 10)) WHERE status = 'PENDING';",
        "models pending index",
    )
    path.write_text(text, encoding="utf-8")


def update_migrations() -> None:
    path = Path("src/migrations.py")
    text = path.read_text(encoding="utf-8")
    addition = '''


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
    if "substr(submitted_at, 1, 10)" in index_sql:
        return

    conn.execute("DROP INDEX IF EXISTS idx_virtual_orders_pending")
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_virtual_orders_pending
        ON virtual_orders(
            strategy_name,
            code,
            side,
            substr(submitted_at, 1, 10)
        )
        WHERE status = 'PENDING'
        """
    )
    logger.info("migration: scoped pending virtual-order uniqueness by date")
'''
    if "def migrate_virtual_orders_pending_index" in text:
        raise RuntimeError("pending-index migration already exists")
    path.write_text(text.rstrip() + addition, encoding="utf-8")


def update_virtual_trade() -> None:
    path = Path("src/virtual_trade.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .migrations import migrate_virtual_orders_reserved_amount",
        '''from .migrations import (
    migrate_virtual_orders_pending_index,
    migrate_virtual_orders_reserved_amount,
)''',
        "virtual_trade migration import",
    )
    text = replace_once(
        text,
        '''        with self._get_connection() as conn:
            migrate_virtual_orders_reserved_amount(conn)
''',
        '''        with self._get_connection() as conn:
            migrate_virtual_orders_reserved_amount(conn)
            migrate_virtual_orders_pending_index(conn)
''',
        "virtual_trade migration call",
    )
    path.write_text(text, encoding="utf-8")


def update_tests() -> None:
    path = Path("tests/test_virtual_trade_order_dates.py")
    text = path.read_text(encoding="utf-8")
    addition = '''


def test_manager_migrates_legacy_pending_index(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_pending_index.db"
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(db_path)}
    DataStore(config)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX idx_virtual_orders_pending")
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_virtual_orders_pending
            ON virtual_orders(strategy_name, code, side)
            WHERE status = 'PENDING'
            """
        )

    VirtualTradeManager(config)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_virtual_orders_pending'"
        ).fetchone()

    assert row is not None
    assert "substr(submitted_at, 1, 10)" in str(row[0])
'''
    if "test_manager_migrates_legacy_pending_index" in text:
        raise RuntimeError("migration regression test already exists")
    path.write_text(text.rstrip() + addition + "\n", encoding="utf-8")


def main() -> None:
    update_models()
    update_migrations()
    update_virtual_trade()
    update_tests()


if __name__ == "__main__":
    main()
