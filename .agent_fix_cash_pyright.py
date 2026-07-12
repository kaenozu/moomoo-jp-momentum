from pathlib import Path

path = Path("tests/test_virtual_trade_cash_history.py")
text = path.read_text(encoding="utf-8")
old = '''    def counted_replay(*args: object, **kwargs: object) -> tuple[float, bool]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
'''
new = '''    def counted_replay(
        conn: sqlite3.Connection,
        strategy_name: str,
        as_of_date: str | None = None,
        exclude_order_id: int | None = None,
    ) -> tuple[float, bool]:
        nonlocal calls
        calls += 1
        return original(
            conn,
            strategy_name,
            as_of_date,
            exclude_order_id,
        )
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one wrapper match, found {text.count(old)}")
path.write_text(text.replace(old, new), encoding="utf-8")
Path(".github/workflows/tests.yml").write_text(
    Path(".agent_original_tests.yml").read_text(encoding="utf-8"),
    encoding="utf-8",
)
Path(".agent_fix_cash_pyright.py").unlink()
Path(".agent_original_tests.yml").unlink()
