from pathlib import Path


def replace_exact(path: str, old: str, new: str, expected: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {actual}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")


path = Path("src/virtual_trade.py")
source = path.read_text(encoding="utf-8")

old_snapshot = '''    def _snapshot_positions_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        rows = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ? AND quantity > 0
            ORDER BY code
            """,
            (strategy_name,),
        ).fetchall()
        return {str(row["code"]): self._row_to_position(row) for row in rows}
'''
new_snapshot = '''    def _snapshot_position_cache_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        rows = conn.execute(
            """
            SELECT * FROM virtual_positions
            WHERE strategy_name = ?
            ORDER BY code
            """,
            (strategy_name,),
        ).fetchall()
        return {str(row["code"]): self._row_to_position(row) for row in rows}

    def _snapshot_positions_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> dict[str, VirtualPosition]:
        return {
            code: position
            for code, position in self._snapshot_position_cache_with_conn(
                conn,
                strategy_name,
            ).items()
            if position.quantity > 0
        }

    def _position_cache_matches_replay(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        replayed: dict[str, VirtualPosition],
    ) -> bool:
        cached = self._snapshot_position_cache_with_conn(conn, strategy_name)
        if set(cached) != set(replayed):
            return False
        for code, cached_position in cached.items():
            replayed_position = replayed[code]
            if cached_position.quantity != replayed_position.quantity:
                return False
            if abs(cached_position.avg_cost - replayed_position.avg_cost) > 1e-6:
                return False
            if abs(cached_position.realized_pl - replayed_position.realized_pl) > 1e-6:
                return False
        return True
'''
if source.count(old_snapshot) != 1:
    raise RuntimeError("snapshot helper block mismatch")
source = source.replace(old_snapshot, new_snapshot)

source = source.replace(
    '''        as_of_date: str | None = None,
    ) -> tuple[dict[str, VirtualPosition], bool]:''',
    '''        as_of_date: str | None = None,
        exclude_order_id: int | None = None,
    ) -> tuple[dict[str, VirtualPosition], bool]:''',
    1,
)

old_asof_query = '''                SELECT id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND COALESCE(substr(filled_at, 1, 10), '') <= ?
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name, as_of_date),
'''
new_asof_query = '''                SELECT id, order_id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND COALESCE(substr(filled_at, 1, 10), '') <= ?
                  AND (? IS NULL OR order_id <> ?)
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (
                    strategy_name,
                    as_of_date,
                    exclude_order_id,
                    exclude_order_id,
                ),
'''
if source.count(old_asof_query) != 1:
    raise RuntimeError("as-of replay query mismatch")
source = source.replace(old_asof_query, new_asof_query)

old_all_query = '''                SELECT id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name,),
'''
new_all_query = '''                SELECT id, order_id, code, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND (? IS NULL OR order_id <> ?)
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name, exclude_order_id, exclude_order_id),
'''
if source.count(old_all_query) != 1:
    raise RuntimeError("full replay query mismatch")
source = source.replace(old_all_query, new_all_query)

old_reference = '''        if reference_date and self._has_fill_history_with_conn(conn, strategy_name):
            replayed, complete = self._replay_positions_with_conn(
                conn,
                strategy_name,
                reference_date,
            )
            if complete:
                return {
                    code: position
                    for code, position in replayed.items()
                    if position.quantity > 0
                }
            logger.warning(
                "仮想ポジション履歴をfillsだけで再構築できないため"
                "現在スナップショットへフォールバックします: strategy=%s, date=%s",
                strategy_name,
                reference_date,
            )
        return self._snapshot_positions_with_conn(conn, strategy_name)
'''
new_reference = '''        if reference_date and self._has_fill_history_with_conn(conn, strategy_name):
            current_replayed, current_complete = self._replay_positions_with_conn(
                conn,
                strategy_name,
            )
            cache_complete = (
                current_complete
                and self._position_cache_matches_replay(
                    conn,
                    strategy_name,
                    current_replayed,
                )
            )
            if cache_complete:
                replayed, complete = self._replay_positions_with_conn(
                    conn,
                    strategy_name,
                    reference_date,
                )
                if complete:
                    return {
                        code: position
                        for code, position in replayed.items()
                        if position.quantity > 0
                    }
            logger.warning(
                "仮想ポジション履歴と現在キャッシュの整合性を確認できないため"
                "現在スナップショットへフォールバックします: strategy=%s, date=%s",
                strategy_name,
                reference_date,
            )
        return self._snapshot_positions_with_conn(conn, strategy_name)
'''
if source.count(old_reference) != 1:
    raise RuntimeError("reference-position block mismatch")
source = source.replace(old_reference, new_reference)

old_rebuild = '''    def _rebuild_position_cache_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
    ) -> bool:
        """Rebuild the current-position cache when fill history is self-contained."""
        if not self._has_fill_history_with_conn(conn, strategy_name):
            return False
        replayed, complete = self._replay_positions_with_conn(conn, strategy_name)
        if not complete:
            return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
'''
new_rebuild = '''    def _rebuild_position_cache_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        exclude_order_id: int | None = None,
    ) -> bool:
        """Rebuild the cache only when existing state is fully fill-derived."""
        if not self._has_fill_history_with_conn(conn, strategy_name):
            return False
        previous_replayed, previous_complete = self._replay_positions_with_conn(
            conn,
            strategy_name,
            exclude_order_id=exclude_order_id,
        )
        if not previous_complete or not self._position_cache_matches_replay(
            conn,
            strategy_name,
            previous_replayed,
        ):
            return False

        replayed, complete = self._replay_positions_with_conn(conn, strategy_name)
        if not complete:
            return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
'''
if source.count(old_rebuild) != 1:
    raise RuntimeError("cache rebuild block mismatch")
source = source.replace(old_rebuild, new_rebuild)

old_call = '''        if self._rebuild_position_cache_from_fills(conn, order.strategy_name):'''
new_call = '''        if self._rebuild_position_cache_from_fills(
            conn,
            order.strategy_name,
            exclude_order_id=order.id,
        ):'''
if source.count(old_call) != 1:
    raise RuntimeError("cache rebuild call mismatch")
source = source.replace(old_call, new_call)

compile(source, "src/virtual_trade.py", "exec")
path.write_text(source, encoding="utf-8")


test_path = Path("tests/test_virtual_trade_position_history.py")
tests = test_path.read_text(encoding="utf-8")

old_helper_sig = '''def _set_snapshot(
    db_path: Path,
    *,
    code: str,
    quantity: int,
    avg_cost: float,
) -> None:'''
new_helper_sig = '''def _set_snapshot(
    db_path: Path,
    *,
    code: str,
    quantity: int,
    avg_cost: float,
    realized_pl: float = 0.0,
) -> None:'''
if tests.count(old_helper_sig) != 1:
    raise RuntimeError("snapshot test helper signature mismatch")
tests = tests.replace(old_helper_sig, new_helper_sig)

tests = tests.replace(
    '''             market_value, unrealized_pl, realized_pl, updated_at)
            VALUES ('default', ?, ?, ?, ?, ?, 0, 0, 'snapshot')
            ON CONFLICT(strategy_name, code) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                market_price = excluded.market_price,
                market_value = excluded.market_value
            """,
            (code, quantity, avg_cost, avg_cost, quantity * avg_cost),
''',
    '''             market_value, unrealized_pl, realized_pl, updated_at)
            VALUES ('default', ?, ?, ?, ?, ?, 0, ?, 'snapshot')
            ON CONFLICT(strategy_name, code) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                market_price = excluded.market_price,
                market_value = excluded.market_value,
                realized_pl = excluded.realized_pl
            """,
            (
                code,
                quantity,
                avg_cost,
                avg_cost,
                quantity * avg_cost,
                realized_pl,
            ),
''',
    1,
)

old_future_sell_snapshot = '''    _set_snapshot(db_path, code="JP.0001", quantity=0, avg_cost=100)'''
new_future_sell_snapshot = '''    _set_snapshot(
        db_path,
        code="JP.0001",
        quantity=0,
        avg_cost=100,
        realized_pl=200,
    )'''
if tests.count(old_future_sell_snapshot) != 1:
    raise RuntimeError("future SELL snapshot marker mismatch")
tests = tests.replace(old_future_sell_snapshot, new_future_sell_snapshot)

old_rebuild_snapshot = '''    _set_snapshot(db_path, code="JP.0001", quantity=0, avg_cost=200)

    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rebuilt = manager._rebuild_position_cache_from_fills(conn, "default")
'''
new_rebuild_snapshot = '''    _set_snapshot(
        db_path,
        code="JP.0001",
        quantity=0,
        avg_cost=200,
        realized_pl=500,
    )

    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rebuilt = manager._rebuild_position_cache_from_fills(
            conn,
            "default",
            exclude_order_id=1,
        )
'''
if tests.count(old_rebuild_snapshot) != 1:
    raise RuntimeError("out-of-order rebuild test marker mismatch")
tests = tests.replace(old_rebuild_snapshot, new_rebuild_snapshot)

marker = "def test_mixed_snapshot_only_position_blocks_destructive_cache_rebuild"
if marker in tests:
    raise RuntimeError("mixed-cache regression test already exists")
tests += '''\n\ndef test_mixed_snapshot_only_position_blocks_destructive_cache_rebuild(\n    tmp_path: Path,\n) -> None:\n    manager, db_path = _make_manager(tmp_path)\n    _set_snapshot(db_path, code="JP.0002", quantity=2, avg_cost=100)\n    _insert_fill(\n        db_path,\n        order_id=1,\n        code="JP.0001",\n        side="BUY",\n        quantity=1,\n        price=200,\n        filled_at="2026-01-10 10:00:00",\n    )\n\n    with manager._get_connection() as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        rebuilt = manager._rebuild_position_cache_from_fills(\n            conn,\n            "default",\n            exclude_order_id=1,\n        )\n\n    assert not rebuilt\n    current_positions = manager.get_positions("default")\n    assert [(position.code, position.quantity) for position in current_positions] == [\n        ("JP.0002", 2)\n    ]\n\n    historical_positions = manager.get_positions(\n        "default",\n        as_of_date="2026-01-05",\n    )\n    assert [(position.code, position.quantity) for position in historical_positions] == [\n        ("JP.0002", 2)\n    ]\n'''

compile(tests, "tests/test_virtual_trade_position_history.py", "exec")
test_path.write_text(tests, encoding="utf-8")
