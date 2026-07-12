from pathlib import Path


def replace_exact(path: str, old: str, new: str, expected: int = 1) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {actual}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")


replace_exact(
    "src/virtual_trade.py",
    "substr(submitted_at, 1, 10) <= ?",
    "COALESCE(substr(submitted_at, 1, 10), '') <= ?",
    expected=4,
)

replace_exact(
    "src/models.py",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_orders_pending ON virtual_orders(strategy_name, code, side, substr(submitted_at, 1, 10)) WHERE status = 'PENDING';",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_orders_pending ON virtual_orders(strategy_name, code, side, COALESCE(substr(submitted_at, 1, 10), '')) WHERE status = 'PENDING';",
)

replace_exact(
    "src/migrations.py",
    "    if \"substr(submitted_at, 1, 10)\" in index_sql:\n        return",
    "    normalized_index_sql = index_sql.lower()\n    if \"coalesce(substr(submitted_at, 1, 10), '')\" in normalized_index_sql:\n        return",
)
replace_exact(
    "src/migrations.py",
    "            substr(submitted_at, 1, 10)",
    "            COALESCE(substr(submitted_at, 1, 10), '')",
)

replace_exact(
    "tests/test_virtual_trade_order_dates.py",
    "import sqlite3\nfrom pathlib import Path\n\nfrom src.config import Config",
    "import sqlite3\nfrom pathlib import Path\n\nimport pytest\n\nfrom src.config import Config",
)

test_path = Path("tests/test_virtual_trade_order_dates.py")
test_text = test_path.read_text(encoding="utf-8")
marker = "def test_null_submitted_pending_is_conservative_and_unique"
if marker in test_text:
    raise RuntimeError("NULL submitted_at regression test already exists")

test_text += '''\n\ndef test_null_submitted_pending_is_conservative_and_unique(tmp_path: Path) -> None:\n    db_path = tmp_path / "nullable_submitted_at.db"\n    config = Config("tests/fixtures/config.test.yaml")\n    config._config["database"] = {"path": str(db_path)}\n\n    with sqlite3.connect(db_path) as conn:\n        conn.executescript(\n            """\n            CREATE TABLE virtual_orders (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                strategy_name TEXT NOT NULL,\n                code TEXT NOT NULL,\n                side TEXT NOT NULL,\n                quantity INTEGER NOT NULL,\n                order_type TEXT NOT NULL,\n                limit_price REAL,\n                status TEXT NOT NULL,\n                signal_id INTEGER,\n                exit_reason TEXT,\n                order_reason TEXT,\n                submitted_at TEXT,\n                filled_at TEXT,\n                cancelled_at TEXT,\n                fill_price REAL,\n                fill_reason TEXT,\n                reserved_amount REAL,\n                created_at TEXT,\n                updated_at TEXT\n            );\n            CREATE UNIQUE INDEX idx_virtual_orders_pending\n            ON virtual_orders(\n                strategy_name, code, side, substr(submitted_at, 1, 10)\n            )\n            WHERE status = 'PENDING';\n            """\n        )\n\n    DataStore(config)\n    with sqlite3.connect(db_path) as conn:\n        conn.execute(\n            """\n            INSERT INTO symbols\n            (code, name, type, role, tradable, enabled)\n            VALUES ('JP.0001', 'JP.0001', 'stock', 'trade_candidate', 1, 1)\n            """\n        )\n        conn.execute(\n            """\n            INSERT INTO daily_bars\n            (code, date, open, high, low, close, volume, turnover)\n            VALUES ('JP.0001', '2026-01-05', 1000, 1010, 990, 1000,\n                    10000, 10000000)\n            """\n        )\n        conn.execute(\n            """\n            INSERT INTO virtual_equity_curve\n            (strategy_name, date, cash, position_value, total_equity, created_at)\n            VALUES ('default', '2026-01-04', 100000, 0, 100000,\n                    '2026-01-04T00:00:00')\n            """\n        )\n        conn.execute(\n            """\n            INSERT INTO virtual_orders\n            (strategy_name, code, side, quantity, order_type, status,\n             submitted_at, reserved_amount, created_at, updated_at)\n            VALUES ('default', 'JP.0001', 'BUY', 1, 'MARKET_SIM',\n                    'PENDING', NULL, NULL, 'legacy', 'legacy')\n            """\n        )\n\n    manager = VirtualTradeManager(config)\n    with manager._get_connection() as conn:\n        ok, reason = manager._validate_buy_order(\n            conn,\n            "default",\n            "JP.0001",\n            1,\n            "MARKET_SIM",\n            None,\n            "2026-01-05",\n        )\n\n    assert not ok\n    assert "未約定BUY注文" in reason\n\n    with sqlite3.connect(db_path) as conn:\n        row = conn.execute(\n            "SELECT sql FROM sqlite_master WHERE type = 'index' "\n            "AND name = 'idx_virtual_orders_pending'"\n        ).fetchone()\n        assert row is not None\n        assert "COALESCE(substr(submitted_at, 1, 10), '')" in str(row[0])\n\n        with pytest.raises(sqlite3.IntegrityError):\n            conn.execute(\n                """\n                INSERT INTO virtual_orders\n                (strategy_name, code, side, quantity, order_type, status,\n                 submitted_at, reserved_amount, created_at, updated_at)\n                VALUES ('default', 'JP.0001', 'BUY', 1, 'MARKET_SIM',\n                        'PENDING', NULL, NULL, 'legacy-2', 'legacy-2')\n                """\n            )\n'''
test_path.write_text(test_text, encoding="utf-8")
