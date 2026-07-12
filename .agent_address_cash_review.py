from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


virtual_trade = Path("src/virtual_trade.py")
old_loop = '''        cash = self.initial_cash
        complete = True
        for row in rows:
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
            if not row["filled_at"] or quantity <= 0 or price < 0:
                complete = False
                continue
            gross = price * quantity
            if side == "BUY":
                cash -= gross + self.commission
            elif side == "SELL":
                cash += gross - self.commission
            else:
                complete = False
        return cash, complete
'''
new_loop = '''        cash = self.initial_cash
        complete = True
        for row in rows:
            delta, valid = self._cash_delta_from_fill_row(row)
            if not valid:
                complete = False
                continue
            cash += delta
        return cash, complete

    def _cash_delta_from_fill_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[float, bool]:
        if (
            row["side"] is None
            or row["quantity"] is None
            or row["price"] is None
            or not row["filled_at"]
        ):
            return 0.0, False
        try:
            side = str(row["side"])
            quantity = int(row["quantity"])
            price = float(row["price"])
        except (TypeError, ValueError):
            return 0.0, False
        if quantity <= 0 or price < 0:
            return 0.0, False
        gross = price * quantity
        if side == "BUY":
            return -(gross + self.commission), True
        if side == "SELL":
            return gross - self.commission, True
        return 0.0, False
'''
replace_once(virtual_trade, old_loop, new_loop)

old_rebuild = '''        rows = conn.execute(
            """
            SELECT date FROM virtual_equity_curve
            WHERE strategy_name = ? AND date >= ?
            ORDER BY date
            """,
            (strategy_name, start_date),
        ).fetchall()
        dates = sorted({start_date, *(str(row["date"]) for row in rows)})
        rebuilt: list[tuple[str, float]] = []
        for target_date in dates:
            cash, complete = self._replay_cash_with_conn(
                conn,
                strategy_name,
                target_date,
            )
            if not complete:
                return False
            rebuilt.append((target_date, cash))

        now = datetime.now().isoformat()
'''
new_rebuild = '''        rows = conn.execute(
            """
            SELECT date FROM virtual_equity_curve
            WHERE strategy_name = ? AND date >= ?
            ORDER BY date
            """,
            (strategy_name, start_date),
        ).fetchall()
        dates = sorted({start_date, *(str(row["date"]) for row in rows)})
        fills = conn.execute(
            """
            SELECT side, quantity, price, filled_at
            FROM virtual_fills
            WHERE strategy_name = ?
            ORDER BY COALESCE(filled_at, ''), id
            """,
            (strategy_name,),
        ).fetchall()

        rebuilt: list[tuple[str, float]] = []
        cash = self.initial_cash
        fill_index = 0
        for target_date in dates:
            while fill_index < len(fills):
                fill_row = fills[fill_index]
                filled_at = fill_row["filled_at"]
                if not filled_at:
                    return False
                fill_date = str(filled_at)[:10]
                if fill_date > target_date:
                    break
                delta, valid = self._cash_delta_from_fill_row(fill_row)
                if not valid:
                    return False
                cash += delta
                fill_index += 1
            rebuilt.append((target_date, cash))

        now = datetime.now().isoformat()
'''
replace_once(virtual_trade, old_rebuild, new_rebuild)

tests = Path("tests/test_virtual_trade_cash_history.py")
append = '''

def test_invalid_fill_values_fall_back_without_raising(tmp_path: Path) -> None:
    manager, db_path = _make_manager(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_fills
            (order_id, strategy_name, code, side, quantity, price,
             filled_at, fill_mode, created_at)
            VALUES (1, 'default', 'JP.0001', 'BUY', 'bad', 'bad',
                    '2026-01-05 10:00:00', 'test', 'corrupt')
            """
        )

    assert manager.get_cash("default", "2026-01-05") == pytest.approx(100000)


def test_equity_rebuild_replays_cash_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, db_path = _make_manager(tmp_path)
    _insert_fill(
        db_path,
        order_id=2,
        side="BUY",
        quantity=1,
        price=200,
        filled_at="2026-01-10 10:00:00",
    )
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO virtual_equity_curve
            (strategy_name, date, cash, position_value, total_equity, created_at)
            VALUES ('default', ?, 99800, 0, 99800, 'before-backfill')
            """,
            [("2026-01-10",), ("2026-01-11",), ("2026-01-12",)],
        )
    _insert_fill(
        db_path,
        order_id=1,
        side="BUY",
        quantity=1,
        price=100,
        filled_at="2026-01-05 10:00:00",
    )

    original = manager._replay_cash_with_conn
    calls = 0

    def counted_replay(*args: object, **kwargs: object) -> tuple[float, bool]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_replay_cash_with_conn", counted_replay)
    with manager._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rebuilt = manager._rebuild_equity_curve_from_fills(
            conn,
            "default",
            "2026-01-05",
            exclude_order_id=1,
        )

    assert rebuilt
    assert calls == 1
'''
text = tests.read_text(encoding="utf-8")
if "def test_invalid_fill_values_fall_back_without_raising" in text:
    raise RuntimeError("review regression tests already exist")
tests.write_text(text + append, encoding="utf-8")

Path(".github/workflows/tests.yml").write_text(
    Path(".agent_original_tests.yml").read_text(encoding="utf-8"),
    encoding="utf-8",
)
Path(".agent_address_cash_review.py").unlink()
Path(".agent_original_tests.yml").unlink()
