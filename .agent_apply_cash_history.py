from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


virtual_trade = Path("src/virtual_trade.py")
old_cash = '''    def _get_cash_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> float:
        if as_of_date:
            row = conn.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name, as_of_date),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT cash FROM virtual_equity_curve
                WHERE strategy_name = ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name,),
            ).fetchone()
        if row and row["cash"] is not None:
            return float(row["cash"])
        return self.initial_cash
'''
new_cash = '''    def _snapshot_cash_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> tuple[float, str | None]:
        if as_of_date:
            row = conn.execute(
                """
                SELECT date, cash FROM virtual_equity_curve
                WHERE strategy_name = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name, as_of_date),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT date, cash FROM virtual_equity_curve
                WHERE strategy_name = ?
                ORDER BY date DESC LIMIT 1
                """,
                (strategy_name,),
            ).fetchone()
        if row and row["cash"] is not None:
            return float(row["cash"]), str(row["date"])
        return self.initial_cash, None

    def _replay_cash_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
        exclude_order_id: int | None = None,
    ) -> tuple[float, bool]:
        if as_of_date:
            rows = conn.execute(
                """
                SELECT order_id, side, quantity, price, filled_at
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
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT order_id, side, quantity, price, filled_at
                FROM virtual_fills
                WHERE strategy_name = ?
                  AND (? IS NULL OR order_id <> ?)
                ORDER BY COALESCE(filled_at, ''), id
                """,
                (strategy_name, exclude_order_id, exclude_order_id),
            ).fetchall()

        cash = self.initial_cash
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

    def _cash_history_matches_replay(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        replayed_cash: float,
        exclude_order_id: int | None = None,
    ) -> bool:
        latest_fill = conn.execute(
            """
            SELECT MAX(substr(filled_at, 1, 10)) AS latest_date
            FROM virtual_fills
            WHERE strategy_name = ?
              AND (? IS NULL OR order_id <> ?)
            """,
            (strategy_name, exclude_order_id, exclude_order_id),
        ).fetchone()
        latest_fill_date = (
            str(latest_fill["latest_date"])
            if latest_fill and latest_fill["latest_date"]
            else None
        )
        snapshot_cash, snapshot_date = self._snapshot_cash_with_conn(
            conn,
            strategy_name,
        )
        if snapshot_date is None or latest_fill_date is None:
            return True
        if snapshot_date < latest_fill_date:
            return True
        return abs(snapshot_cash - replayed_cash) <= 0.01

    def _get_cash_with_conn(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
    ) -> float:
        if self._has_fill_history_with_conn(conn, strategy_name):
            current_cash, current_complete = self._replay_cash_with_conn(
                conn,
                strategy_name,
            )
            if current_complete and self._cash_history_matches_replay(
                conn,
                strategy_name,
                current_cash,
            ):
                replayed_cash, replay_complete = self._replay_cash_with_conn(
                    conn,
                    strategy_name,
                    as_of_date,
                )
                if replay_complete:
                    return replayed_cash
            logger.warning(
                "仮想cash履歴とequityスナップショットの整合性を確認できないため"
                "保存済みcashへフォールバックします: strategy=%s, date=%s",
                strategy_name,
                as_of_date,
            )
        snapshot_cash, _ = self._snapshot_cash_with_conn(
            conn,
            strategy_name,
            as_of_date,
        )
        return snapshot_cash

    def _recalculate_equity_returns_from_date(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        start_date: str,
    ) -> None:
        rows = conn.execute(
            """
            SELECT date, total_equity, benchmark_return
            FROM virtual_equity_curve
            WHERE strategy_name = ?
            ORDER BY date
            """,
            (strategy_name,),
        ).fetchall()
        previous_equity: float | None = None
        updates: list[tuple[float, float | None, str, str]] = []
        for row in rows:
            total_equity = float(row["total_equity"] or 0.0)
            if str(row["date"]) >= start_date:
                daily_return = (
                    (total_equity - previous_equity) / previous_equity * 100
                    if previous_equity
                    else 0.0
                )
                benchmark_return = (
                    float(row["benchmark_return"])
                    if row["benchmark_return"] is not None
                    else None
                )
                excess_return = (
                    daily_return - benchmark_return
                    if benchmark_return is not None
                    else None
                )
                updates.append(
                    (daily_return, excess_return, strategy_name, str(row["date"]))
                )
            previous_equity = total_equity
        if updates:
            conn.executemany(
                """
                UPDATE virtual_equity_curve
                SET daily_return = ?, excess_return = ?
                WHERE strategy_name = ? AND date = ?
                """,
                updates,
            )

    def _rebuild_equity_curve_from_fills(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        start_date: str,
        exclude_order_id: int | None = None,
    ) -> bool:
        previous_cash, previous_complete = self._replay_cash_with_conn(
            conn,
            strategy_name,
            exclude_order_id=exclude_order_id,
        )
        if not previous_complete or not self._cash_history_matches_replay(
            conn,
            strategy_name,
            previous_cash,
            exclude_order_id,
        ):
            return False

        rows = conn.execute(
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

        for target_date, cash in rebuilt:
            self._set_cash(conn, strategy_name, target_date, cash)
        self._recalculate_equity_returns_from_date(
            conn,
            strategy_name,
            start_date,
        )
        return True
'''
replace_once(virtual_trade, old_cash, new_cash)

old_apply = '''    def _apply_cash_delta(self, conn: sqlite3.Connection, strategy_name: str, target_date: str, delta: float) -> None:
        current_cash = self._get_cash_with_conn(conn, strategy_name, target_date)
        self._set_cash(conn, strategy_name, target_date, current_cash + delta)
'''
new_apply = '''    def _apply_cash_delta(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        target_date: str,
        delta: float,
    ) -> None:
        current_cash, _ = self._snapshot_cash_with_conn(
            conn,
            strategy_name,
            target_date,
        )
        self._set_cash(conn, strategy_name, target_date, current_cash + delta)
'''
replace_once(virtual_trade, old_apply, new_apply)

old_update = '''        gross = fill.price * fill.quantity
        if (
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
            delta = (
                -(gross + self.commission)
                if order.side == "BUY"
                else gross - self.commission
            )
            self._apply_cash_delta(
                conn,
                order.strategy_name,
                fill.filled_at,
                delta,
            )
            return
'''
new_update = '''        gross = fill.price * fill.quantity
        requires_rebuild = self._fill_requires_cache_rebuild(
            conn,
            order.strategy_name,
            fill,
        )
        if requires_rebuild:
            positions_rebuilt = self._rebuild_position_cache_from_fills(
                conn,
                order.strategy_name,
                exclude_order_id=order.id,
            )
            if positions_rebuilt and self._rebuild_equity_curve_from_fills(
                conn,
                order.strategy_name,
                fill.filled_at[:10],
                exclude_order_id=order.id,
            ):
                return
            if positions_rebuilt:
                logger.warning(
                    "過去日fillのcash履歴を安全に再構築できないため"
                    "対象日の増分更新へフォールバックします: strategy=%s, date=%s",
                    order.strategy_name,
                    fill.filled_at,
                )
                delta = (
                    -(gross + self.commission)
                    if order.side == "BUY"
                    else gross - self.commission
                )
                self._apply_cash_delta(
                    conn,
                    order.strategy_name,
                    fill.filled_at,
                    delta,
                )
                return
'''
replace_once(virtual_trade, old_update, new_update)

pyright = Path("pyrightconfig.json")
text = pyright.read_text(encoding="utf-8")
required = '    "tests/test_virtual_trade_cash_history.py",\n'
if text.count(required) != 1:
    raise RuntimeError("cash-history test is missing or duplicated in pyrightconfig")

Path(".github/workflows/tests.yml").write_text(
    Path(".agent_original_tests.yml").read_text(encoding="utf-8"),
    encoding="utf-8",
)
Path(".agent_apply_cash_history.py").unlink()
Path(".agent_original_tests.yml").unlink()
