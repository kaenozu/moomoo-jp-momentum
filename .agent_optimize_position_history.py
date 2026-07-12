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

latest_close_marker = '        return float(row["close"]) if row and row["close"] is not None else None\n'
if source.count(latest_close_marker) != 1:
    raise RuntimeError("latest close marker mismatch")
batch_helper = r'''

    def _latest_closes_with_conn(
        self,
        conn: sqlite3.Connection,
        target_date: str | None = None,
    ) -> dict[str, float]:
        if target_date:
            rows = conn.execute(
                """
                SELECT code, close
                FROM (
                    SELECT code, close,
                           ROW_NUMBER() OVER (
                               PARTITION BY code ORDER BY date DESC
                           ) AS row_number
                    FROM daily_bars
                    WHERE date <= ?
                )
                WHERE row_number = 1
                  AND close IS NOT NULL
                """,
                (target_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT code, close
                FROM (
                    SELECT code, close,
                           ROW_NUMBER() OVER (
                               PARTITION BY code ORDER BY date DESC
                           ) AS row_number
                    FROM daily_bars
                )
                WHERE row_number = 1
                  AND close IS NOT NULL
                """
            ).fetchall()
        return {str(row["code"]): float(row["close"]) for row in rows}
'''
source = source.replace(latest_close_marker, latest_close_marker + batch_helper)

old_cache_compare = '''        cached = self._snapshot_position_cache_with_conn(conn, strategy_name)
        if set(cached) != set(replayed):
            return False
'''
new_cache_compare = '''        cached = self._snapshot_position_cache_with_conn(conn, strategy_name)
        if not cached:
            return True
        if set(cached) != set(replayed):
            return False
'''
if source.count(old_cache_compare) != 1:
    raise RuntimeError("cache compare block mismatch")
source = source.replace(old_cache_compare, new_cache_compare)

old_positions_loop = '''        positions: dict[str, VirtualPosition] = {}
        for code, state in states.items():
            market_price = (
                self._latest_close(conn, code, as_of_date)
                or state.last_price
                or state.avg_cost
            )
'''
new_positions_loop = '''        latest_closes = self._latest_closes_with_conn(conn, as_of_date)
        positions: dict[str, VirtualPosition] = {}
        for code, state in states.items():
            market_price = (
                latest_closes.get(code)
                or state.last_price
                or state.avg_cost
            )
'''
if source.count(old_positions_loop) != 1:
    raise RuntimeError("replay market price loop mismatch")
source = source.replace(old_positions_loop, new_positions_loop)

rebuild_marker = '''    def _rebuild_position_cache_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        exclude_order_id: int | None = None,
    ) -> bool:
'''
if source.count(rebuild_marker) != 1:
    raise RuntimeError("rebuild method marker mismatch")
order_check = r'''    def _fill_requires_cache_rebuild(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        fill: VirtualFill,
    ) -> bool:
        row = conn.execute(
            """
            SELECT MAX(COALESCE(filled_at, ''))
            FROM virtual_fills
            WHERE strategy_name = ?
              AND (? IS NULL OR order_id <> ?)
            """,
            (strategy_name, fill.order_id, fill.order_id),
        ).fetchone()
        previous_latest = str(row[0]) if row and row[0] else None
        return previous_latest is not None and fill.filled_at < previous_latest

'''
source = source.replace(rebuild_marker, order_check + rebuild_marker)

old_insert_loop = '''        for position in replayed.values():
            conn.execute(
                """
                INSERT INTO virtual_positions
                (strategy_name, code, quantity, avg_cost, market_price,
                 market_value, unrealized_pl, realized_pl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_name,
                    position.code,
                    position.quantity,
                    position.avg_cost,
                    position.market_price,
                    position.market_value,
                    position.unrealized_pl,
                    position.realized_pl,
                    now,
                ),
            )
'''
new_insert_loop = '''        if replayed:
            conn.executemany(
                """
                INSERT INTO virtual_positions
                (strategy_name, code, quantity, avg_cost, market_price,
                 market_value, unrealized_pl, realized_pl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        strategy_name,
                        position.code,
                        position.quantity,
                        position.avg_cost,
                        position.market_price,
                        position.market_value,
                        position.unrealized_pl,
                        position.realized_pl,
                        now,
                    )
                    for position in replayed.values()
                ],
            )
'''
if source.count(old_insert_loop) != 1:
    raise RuntimeError("cache insertion loop mismatch")
source = source.replace(old_insert_loop, new_insert_loop)

old_rebuild_call = '''        if self._rebuild_position_cache_from_fills(
            conn,
            order.strategy_name,
            exclude_order_id=order.id,
        ):
'''
new_rebuild_call = '''        if (
            self._fill_requires_cache_rebuild(
                conn,
                order.strategy_name,
                fill,
            )
            and self._rebuild_position_cache_from_fills(
                conn,
                order.strategy_name,
                exclude_order_id=order.id,
            )
        ):
'''
if source.count(old_rebuild_call) != 1:
    raise RuntimeError("position update rebuild call mismatch")
source = source.replace(old_rebuild_call, new_rebuild_call)

compile(source, "src/virtual_trade.py", "exec")
path.write_text(source, encoding="utf-8")


test_path = Path("tests/test_virtual_trade_position_history.py")
tests = test_path.read_text(encoding="utf-8")
marker = "def test_chronological_fill_uses_incremental_cache_update"
if marker in tests:
    raise RuntimeError("chronological cache regression already exists")
tests += '''\n\ndef test_chronological_fill_uses_incremental_cache_update(\n    tmp_path: Path,\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    manager, _ = _make_manager(tmp_path)\n    order = manager.place_order(\n        "default",\n        "JP.0001",\n        "BUY",\n        1,\n        submitted_at="2026-01-05",\n    )\n    assert order is not None\n\n    def fail_rebuild(*_args: object, **_kwargs: object) -> bool:\n        pytest.fail("chronological fills must not rebuild the full cache")\n\n    monkeypatch.setattr(\n        manager,\n        "_rebuild_position_cache_from_fills",\n        fail_rebuild,\n    )\n\n    fills = manager.process_fills("default", "2026-01-06")\n\n    assert len(fills) == 1\n    positions = manager.get_positions("default")\n    assert len(positions) == 1\n    assert positions[0].code == "JP.0001"\n    assert positions[0].quantity == 1\n'''
compile(tests, "tests/test_virtual_trade_position_history.py", "exec")
test_path.write_text(tests, encoding="utf-8")
