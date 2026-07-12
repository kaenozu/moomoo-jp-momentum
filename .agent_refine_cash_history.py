from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


virtual_trade = Path("src/virtual_trade.py")
old_consistency = '''        if snapshot_date is None or latest_fill_date is None:
            return True
        if snapshot_date < latest_fill_date:
            return True
        return abs(snapshot_cash - replayed_cash) <= 0.01
'''
new_consistency = '''        if snapshot_date is None or latest_fill_date is None:
            return True
        expected_cash = replayed_cash
        if snapshot_date < latest_fill_date:
            expected_cash, snapshot_complete = self._replay_cash_with_conn(
                conn,
                strategy_name,
                snapshot_date,
                exclude_order_id,
            )
            if not snapshot_complete:
                return False
        return abs(snapshot_cash - expected_cash) <= 0.01
'''
replace_once(virtual_trade, old_consistency, new_consistency)

old_rebuild_write = '''        for target_date, cash in rebuilt:
            self._set_cash(conn, strategy_name, target_date, cash)
        self._recalculate_equity_returns_from_date(
'''
new_rebuild_write = '''        now = datetime.now().isoformat()
        for target_date, cash in rebuilt:
            position_value = self._position_value_with_conn(
                conn,
                strategy_name,
                target_date,
            )
            total_equity = cash + position_value
            conn.execute(
                """
                INSERT INTO virtual_equity_curve
                (strategy_name, date, cash, position_value, total_equity,
                 benchmark_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_name, date) DO UPDATE SET
                    cash = excluded.cash,
                    position_value = excluded.position_value,
                    total_equity = excluded.total_equity,
                    created_at = excluded.created_at
                """,
                (
                    strategy_name,
                    target_date,
                    cash,
                    position_value,
                    total_equity,
                    self.default_benchmark,
                    now,
                ),
            )
        self._recalculate_equity_returns_from_date(
'''
replace_once(virtual_trade, old_rebuild_write, new_rebuild_write)

tests = Path("tests/test_virtual_trade_cash_history.py")
old_legacy_test = '''def test_inconsistent_current_snapshot_falls_back_conservatively(
    tmp_path: Path,
) -> None:
'''
new_legacy_test = '''def test_older_manual_cash_snapshot_is_not_treated_as_stale(
    tmp_path: Path,
) -> None:
    manager, db_path = _make_manager(tmp_path, seed_equity=False)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', '2026-01-04', 90000, 0, 90000, 'manual')
            """
        )
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )

    assert manager.get_cash("default", "2026-01-05") == pytest.approx(90000)


def test_inconsistent_current_snapshot_falls_back_conservatively(
    tmp_path: Path,
) -> None:
'''
replace_once(tests, old_legacy_test, new_legacy_test)

old_equity_insert = '''        conn.executemany(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', ?, ?, 0, ?, 'before-backfill')
            """,
            [
                ("2026-01-10", 99800, 99800),
                ("2026-01-11", 100050, 100050),
            ],
        )
'''
new_equity_insert = '''        conn.executemany(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', ?, ?, 0, ?, 'before-backfill')
            """,
            [
                ("2026-01-10", 99800, 99800),
                ("2026-01-11", 100050, 100050),
            ],
        )
        conn.execute(
            """
            UPDATE virtual_equity_curve
            SET benchmark_code = 'JP.CUSTOM', benchmark_return = 0.2
            WHERE strategy_name = 'default' AND date = '2026-01-10'
            """
        )
'''
replace_once(tests, old_equity_insert, new_equity_insert)

old_select = '''            SELECT date, cash, position_value, total_equity, daily_return
            FROM virtual_equity_curve
'''
new_select = '''            SELECT date, cash, position_value, total_equity, daily_return,
                   benchmark_code, benchmark_return
            FROM virtual_equity_curve
'''
replace_once(tests, old_select, new_select)

old_assert_tail = '''    assert rows[1]["daily_return"] == pytest.approx(0.1)
    assert rows[2]["daily_return"] == pytest.approx(100 / 100100 * 100)
'''
new_assert_tail = '''    assert rows[1]["daily_return"] == pytest.approx(0.1)
    assert rows[2]["daily_return"] == pytest.approx(100 / 100100 * 100)
    assert rows[1]["benchmark_code"] == "JP.CUSTOM"
    assert rows[1]["benchmark_return"] == pytest.approx(0.2)
'''
replace_once(tests, old_assert_tail, new_assert_tail)

Path(".github/workflows/tests.yml").write_text(
    Path(".agent_original_tests.yml").read_text(encoding="utf-8"),
    encoding="utf-8",
)
Path(".agent_refine_cash_history.py").unlink()
Path(".agent_original_tests.yml").unlink()
