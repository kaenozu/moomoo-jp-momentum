"""
仮想トレードレポートテスト

ファイルパス: tests/test_virtual_report.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import sqlite3
from src.models import CREATE_TABLES_SQL


@pytest.fixture
def seed_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_TABLES_SQL)
    conn.executemany(
        "INSERT INTO symbols (code, name, role, tradable, type) VALUES (?, ?, ?, ?, ?)",
        [("JP.7203", "T1", "trade_candidate", 1, "stock"),
         ("JP.2559", "BM1", "benchmark", 0, "etf"),
         ("JP.1306", "BM2", "benchmark", 0, "etf")]
    )
    for d in ["2026-07-01", "2026-07-05"]:
        conn.execute("INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                     ("JP.7203", d, 1000, 1100, 900, 1000, 100000, 100000000))
    conn.execute("INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, daily_return) VALUES (?,?,?,?,?,?)",
                 ("default", "2026-06-30", 100000, 0, 100000, 0.0))
    conn.execute("""INSERT INTO virtual_orders (id, strategy_name, code, side, quantity, order_type, status, exit_reason, submitted_at, filled_at, fill_price)
                    VALUES (1, 'default', 'JP.7203', 'BUY', 1, 'MARKET_SIM', 'FILLED', NULL, '2026-07-01', '2026-07-01', 1000)""")
    conn.execute("""INSERT INTO virtual_fills (id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode)
                    VALUES (1, 1, 'default', 'JP.7203', 'BUY', 1, 1000.0, '2026-07-01', 'test')""")
    conn.execute("""INSERT INTO virtual_positions (strategy_name, code, quantity, avg_cost, realized_pl)
                    VALUES ('default', 'JP.7203', 1, 1000, 0)""")
    conn.execute("""INSERT INTO virtual_orders (id, strategy_name, code, side, quantity, order_type, status, exit_reason, submitted_at, filled_at, fill_price)
                    VALUES (2, 'default', 'JP.7203', 'SELL', 1, 'MARKET_SIM', 'FILLED', 'stop_loss', '2026-07-05', '2026-07-05', 950)""")
    conn.execute("""INSERT INTO virtual_fills (id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode)
                    VALUES (2, 2, 'default', 'JP.7203', 'SELL', 1, 950.0, '2026-07-05', 'test')""")
    conn.execute("""UPDATE virtual_positions SET quantity=0, realized_pl=-50 WHERE strategy_name='default' AND code='JP.7203'""")
    conn.execute("""INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, daily_return)
                    VALUES ('default', '2026-07-05', 99950, 0, 99950, -0.05)""")
    conn.execute("INSERT INTO benchmark_prices (benchmark_code, date, close, daily_return) VALUES ('JP.2559', '2026-06-30', 1000, 0)")
    conn.execute("INSERT INTO benchmark_prices (benchmark_code, date, close, daily_return) VALUES ('JP.2559', '2026-07-05', 1010, 1.0)")
    conn.execute("INSERT INTO benchmark_prices (benchmark_code, date, close, daily_return) VALUES ('JP.1306', '2026-06-30', 100, 0)")
    conn.execute("INSERT INTO benchmark_prices (benchmark_code, date, close, daily_return) VALUES ('JP.1306', '2026-07-05', 102, 2.0)")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def cfg(seed_db):
    class TestConfig:
        def get(self, key, default=None):
            if key == "database":
                return {"path": str(seed_db)}
            if key == "virtual_trade":
                return {"enabled": True, "initial_cash": 100000, "max_position_amount": 50000,
                        "max_total_positions": 5, "max_position_per_symbol": 1,
                        "market_fill_mode": "next_day_open", "commission": 0, "slippage_bps": 10}
            if key == "universe":
                return {"min_trade_price": 500, "max_trade_price": 50000}
            return default
        @property
        def database_path(self):
            return str(seed_db)
    return TestConfig()


class TestVirtualReport:
    def test_closed_trade_created(self, cfg):
        from src.virtual_report import VirtualReportGenerator
        gen = VirtualReportGenerator(cfg)
        closed = gen.get_closed_trades("default")
        assert len(closed) == 1
        assert closed[0].code == "JP.7203"
        assert closed[0].exit_reason == "stop_loss"
        assert closed[0].realized_pl == -50.0
        assert closed[0].return_pct == -5.0

    def test_report_metrics(self, cfg):
        from src.virtual_report import VirtualReportGenerator
        gen = VirtualReportGenerator(cfg)
        report = gen.generate("default")
        assert report.closed_trade_count == 1
        assert report.win_count == 0
        assert report.loss_count == 1
        assert report.win_rate == 0.0
        assert report.realized_pl == -50.0

    def test_benchmark_comparison(self, cfg):
        from src.virtual_report import VirtualReportGenerator
        gen = VirtualReportGenerator(cfg)
        report = gen.generate("default")
        assert report.benchmark_2559_return is not None
        assert abs(report.benchmark_2559_return - 1.0) < 0.01
        assert report.benchmark_1306_return is not None
        assert abs(report.benchmark_1306_return - 2.0) < 0.01

    def test_exit_reason_stats(self, cfg):
        from src.virtual_report import VirtualReportGenerator
        gen = VirtualReportGenerator(cfg)
        report = gen.generate("default")
        assert report.exit_reason_stats is not None
        assert len(report.exit_reason_stats) == 1
        assert report.exit_reason_stats[0].exit_reason == "stop_loss"
        assert report.exit_reason_stats[0].realized_pl == -50.0

    def test_max_drawdown(self, cfg):
        from src.virtual_report import VirtualReportGenerator
        gen = VirtualReportGenerator(cfg)
        report = gen.generate("default")
        assert report.max_drawdown_pct is not None
        assert report.max_drawdown_pct > 0
