"""
日次運用テスト

ファイルパス: tests/test_daily_cycle.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime
from src.data_freshness import DataFreshnessGuard


class TestIdempotency:
    """冪等性テスト"""

    def test_duplicate_signals_dedup(self):
        """同一日・同一銘柄のsignalは重複しない"""
        from src.models import CREATE_TABLES_SQL
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.executescript(CREATE_TABLES_SQL)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO signals (code, date, signal_type, score, reason, price_at_signal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("JP.7203", "2026-07-01", "BUY_CANDIDATE", 80.0, "test", 1000.0, now))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO signals (code, date, signal_type, score, reason, price_at_signal, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("JP.7203", "2026-07-01", "BUY_CANDIDATE", 85.0, "dup", 1000.0, now))
        conn.close()

    def test_dup_order_blocked_by_db(self):
        """同一戦術・同一コード・同一sideのPENDING注文はDB制約で重複不可"""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        from src.models import CREATE_TABLES_SQL
        conn.executescript(CREATE_TABLES_SQL)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO virtual_orders
            (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "PENDING", now, now, now))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO virtual_orders
                (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("default", "JP.7203", "BUY", 1, "MARKET_SIM", "PENDING", now, now, now))
        cursor = conn.execute("SELECT COUNT(*) FROM virtual_orders")
        assert cursor.fetchone()[0] == 1  # 1件のみで重複なし
        conn.close()

    def test_upsert_equity_curve(self):
        """equity_curveは同一日付でUPSERTされる"""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        from src.models import CREATE_TABLES_SQL
        conn.executescript(CREATE_TABLES_SQL)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT OR REPLACE INTO virtual_equity_curve
            (strategy_name, date, cash, total_equity, daily_return, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", "2026-07-01", 90000, 90000, 0.0, now))
        conn.execute("""
            INSERT OR REPLACE INTO virtual_equity_curve
            (strategy_name, date, cash, total_equity, daily_return, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", "2026-07-01", 95000, 95000, 5.0, now))
        cursor = conn.execute("SELECT total_equity FROM virtual_equity_curve WHERE strategy_name='default' AND date='2026-07-01'")
        assert cursor.fetchone()[0] == 95000
        conn.close()


class TestDailyCycle:
    """日次サイクルテスト"""

    def test_dry_run_returns(self):
        """dry-runがエラーなく終了する"""
        from run_daily_cycle import run_cycle
        # --dry-run相当を直接実行（テスト用設定ファイルを使用）
        results = run_cycle("2026-07-01", dry_run=True, config_path="tests/fixtures/config.test.yaml")
        assert results is not None
        assert results.get("connection") is True
        assert results.get("symbols", 0) > 0

    def test_freshness_guard_stale(self):
        """DBがない場合、鮮度チェックがエラーになる"""
        class TestConfig:
            def get(self, key, default=None):
                if key == "database":
                    return {"path": "data/nonexistent_xyz.db"}
                return default
            @property
            def database_path(self):
                return "data/nonexistent_xyz.db"

        guard = DataFreshnessGuard(TestConfig())  # type: ignore[arg-type]  # test stub, not full Config
        status = guard.check_freshness()
        assert status.level == "error"
