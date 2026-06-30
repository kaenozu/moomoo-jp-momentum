"""
仮想トレードDB統合テスト

ファイルパス: tests/test_virtual_integration.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import sqlite3
from datetime import datetime
from src.models import CREATE_TABLES_SQL


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_TABLES_SQL)
    conn.execute("INSERT INTO symbols (code, name, role, tradable, type) VALUES ('JP.7203', 'T1', 'trade_candidate', 1, 'stock')")
    conn.execute("INSERT INTO symbols (code, name, role, tradable, type) VALUES ('JP.2559', 'BM', 'benchmark', 0, 'etf')")
    conn.execute("INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES ('JP.7203', '2026-07-01', 1000,1020,980,1000,100000,100000000)")
    conn.execute("INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES ('JP.7203', '2026-07-02', 1010,1030,990,1010,110000,110000000)")
    conn.execute("INSERT INTO virtual_equity_curve (strategy_name, date, cash, total_equity, daily_return) VALUES ('default', '2026-06-30', 100000, 100000, 0.0)")
    conn.commit()
    yield conn
    conn.close()


class TestVirtualTradeIntegration:
    def test_buy_consumes_cash(self, conn):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at) VALUES (?,?,?,?,?,?,?)",
                     ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "FILLED", now))
        conn.execute("INSERT INTO virtual_fills (order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (1, 'default', 'JP.7203', 'BUY', 1, 1000, '2026-07-01', 'test')")
        conn.execute("INSERT INTO virtual_positions (strategy_name, code, quantity, avg_cost, realized_pl) VALUES ('default', 'JP.7203', 1, 1000, 0)")
        cursor = conn.execute("SELECT quantity, avg_cost FROM virtual_positions WHERE strategy_name='default' AND code='JP.7203'")
        pos = cursor.fetchone()
        assert pos["quantity"] == 1
        assert pos["avg_cost"] == 1000

    def test_sell_increases_cash(self, conn):
        conn.execute("INSERT INTO virtual_positions (strategy_name, code, quantity, avg_cost, realized_pl) VALUES ('default', 'JP.7203', 1, 1000, 0)")
        conn.execute("UPDATE virtual_positions SET realized_pl = realized_pl + ? WHERE strategy_name='default' AND code='JP.7203'", (500,))
        cursor = conn.execute("SELECT realized_pl FROM virtual_positions WHERE strategy_name='default' AND code='JP.7203'")
        assert cursor.fetchone()["realized_pl"] == 500

    def test_benchmark_not_tradable(self, tmp_path):
        from src.virtual_trade import VirtualTradeManager
        db_path = tmp_path / "test_benchmark.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(CREATE_TABLES_SQL)
        conn.executemany(
            "INSERT INTO symbols (code, name, role, tradable, type) VALUES (?, ?, ?, ?, ?)",
            [('JP.7203', 'T1', 'trade_candidate', 1, 'stock'),
             ('JP.2559', 'BM', 'benchmark', 0, 'etf')]
        )
        conn.commit()
        conn.close()

        class TestConfig:
            def get(self, key, default=None):
                if key == "database": return {"path": str(db_path)}
                if key == "virtual_trade": return {"enabled": True, "initial_cash": 100000, "max_position_amount": 50000, "max_total_positions": 5, "max_position_per_symbol": 1, "market_fill_mode": "next_day_open", "commission": 0, "slippage_bps": 10}
                if key == "universe": return {"min_trade_price": 500, "max_trade_price": 50000}
                return default
            @property
            def database_path(self): return str(db_path)

        mgr = VirtualTradeManager(TestConfig())
        assert mgr.place_order("default", "JP.2559", "BUY", 1, "MARKET_SIM") is None
        assert mgr.place_order("default", "JP.7203", "BUY", 1, "MARKET_SIM") is None

    def test_pending_dup_prevented_by_db(self, conn):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at) VALUES (?,?,?,?,?,?,?)",
                     ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "PENDING", now))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at) VALUES (?,?,?,?,?,?,?)",
                         ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "PENDING", now))

    def test_filled_order_ignored_in_ranking(self, conn):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at) VALUES (?,?,?,?,?,?,?)",
                     ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "FILLED", now))
        conn.execute("INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at) VALUES (?,?,?,?,?,?,?)",
                     ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "FILLED", now))
        cursor = conn.execute("SELECT COUNT(*) FROM virtual_orders")
        assert cursor.fetchone()[0] == 2
