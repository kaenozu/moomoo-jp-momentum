"""Temporary patch for SELL self-validation and adjustment-policy naming."""

from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{path}: boundaries not found")
    path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


virtual_path = ROOT / "src/virtual_trade.py"
sell_validator = '''    def _validate_sell_order(
        self,
        conn: sqlite3.Connection,
        strategy_name: str,
        code: str,
        quantity: int,
        exclude_order_id: int | None = None,
    ) -> tuple[bool, str]:
        pos = conn.execute(
            """
            SELECT quantity FROM virtual_positions
            WHERE strategy_name = ? AND code = ?
            """,
            (strategy_name, code),
        ).fetchone()
        if not pos or pos["quantity"] < quantity:
            return False, "売却可能な仮想ポジションが不足しています"

        sql = """
            SELECT 1 FROM virtual_orders
            WHERE strategy_name = ? AND code = ?
              AND side = 'SELL' AND status = 'PENDING'
        """
        params: list[object] = [strategy_name, code]
        if exclude_order_id is not None:
            sql += " AND id != ?"
            params.append(exclude_order_id)
        sql += " LIMIT 1"
        pending = conn.execute(sql, params).fetchone()
        if pending:
            return False, "同一銘柄の未約定SELL注文が既に存在します"
        return True, ""

'''
replace_between(
    virtual_path,
    "    def _validate_sell_order(",
    "    def get_cash(",
    sell_validator,
)
replace_once(
    virtual_path,
    "                ok, reason = self._validate_sell_order(conn, order.strategy_name, order.code, order.quantity)\n",
    "                ok, reason = self._validate_sell_order(\n"
    "                    conn,\n"
    "                    order.strategy_name,\n"
    "                    order.code,\n"
    "                    order.quantity,\n"
    "                    exclude_order_id=order.id,\n"
    "                )\n",
)

expected_policy_occurrences = {
    "src/models.py": 1,
    "src/data_store.py": 1,
    "src/run_fingerprint.py": 2,
}
for relative_path, expected_count in expected_policy_occurrences.items():
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    count = text.count("split_adjustment_service")
    if count != expected_count:
        raise RuntimeError(
            f"{relative_path}: expected {expected_count} adjustment policy defaults, found {count}"
        )
    path.write_text(
        text.replace("split_adjustment_service", "qfq_no_additional_adjustment"),
        encoding="utf-8",
    )

metadata_test = ROOT / "tests/test_backtest_run_metadata.py"
replace_once(
    metadata_test,
    '        assert first["engine_version"] == "2.0.0"\n',
    '        assert first["engine_version"] == "2.0.0"\n'
    '        assert first["adjustment_policy"] == "qfq_no_additional_adjustment"\n',
)

print("Applied V2 round-trip correction.")
