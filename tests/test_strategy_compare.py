"""
戦略比較テスト

ファイルパス: tests/test_strategy_compare.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from src.strategies import StrategyRegistry
from src.strategies.momentum import MomentumStrategy
from src.strategies.quality_low_risk import QualityLowRiskStrategy
from src.strategies.etf_rotation import ETFRotationStrategy


class TestStrategyNames:
    def test_strategies_registered(self):
        """3戦略が登録されている"""
        names = StrategyRegistry.list_names()
        assert "momentum" in names
        assert "quality_low_risk" in names
        assert "etf_rotation" in names

    def test_momentum_returns_strategy_name(self, tmp_path):
        from src.config import Config
        import yaml
        cfg_path = tmp_path / "test_config.yaml"
        with open(cfg_path, "w") as f:
            yaml.dump({
                "opend": {"host": "127.0.0.1", "port": 11111, "timeout": 10},
                "database": {"path": str(tmp_path / "test.db")},
                "screening": {},
                "universe": {"min_trade_price": 500, "max_trade_price": 50000},
            }, f)
        config = Config(str(cfg_path))
        strategy = MomentumStrategy(config)
        assert strategy.strategy_name == "momentum"

    def test_quality_low_risk_name(self, tmp_path):
        from src.config import Config
        import yaml
        cfg_path = tmp_path / "cfg.yaml"
        with open(cfg_path, "w") as f:
            yaml.dump({
                "opend": {"host": "127.0.0.1", "port": 11111, "timeout": 10},
                "database": {"path": str(tmp_path / "t.db")},
                "screening": {},
                "universe": {"min_trade_price": 500, "max_trade_price": 50000},
            }, f)
        config = Config(str(cfg_path))
        strategy = QualityLowRiskStrategy(config)
        assert strategy.strategy_name == "quality_low_risk"

    def test_etf_rotation_name(self, tmp_path):
        from src.config import Config
        import yaml
        cfg_path = tmp_path / "cfg.yaml"
        with open(cfg_path, "w") as f:
            yaml.dump({
                "opend": {"host": "127.0.0.1", "port": 11111, "timeout": 10},
                "database": {"path": str(tmp_path / "t.db")},
                "screening": {},
                "universe": {"min_trade_price": 500, "max_trade_price": 50000},
            }, f)
        config = Config(str(cfg_path))
        strategy = ETFRotationStrategy(config)
        assert strategy.strategy_name == "etf_rotation"

    def test_etf_rotation_only_etf(self):
        """etf_rotationはETFのみ対象"""
        class Cfg:
            def get(self, k, d=None):
                u = {"min_trade_price": 500, "max_trade_price": 50000}
                s = {"min_history_days": 25}
                return {"universe": u}.get(k, s) if k in ("universe", "screening") else d
            @property
            def database_path(self): return ":memory:"
            @property
            def opend_host(self): return "127.0.0.1"
            @property
            def opend_port(self): return 11111

        strategy = ETFRotationStrategy(Cfg())
        assert strategy._is_etf("JP.2559") is True
        assert strategy._is_etf("JP.7203") is False


class TestSignalStorage:
    def test_signal_has_strategy_name(self, tmp_path):
        """signalsにstrategy_nameが保存される"""
        import yaml
        cfg_path = tmp_path / "cfg.yaml"
        db_path = tmp_path / "test.db"
        with open(cfg_path, "w") as f:
            yaml.dump({
                "opend": {"host": "127.0.0.1", "port": 11111, "timeout": 10},
                "database": {"path": str(db_path)},
                "screening": {},
                "universe": {"min_trade_price": 500, "max_trade_price": 50000},
            }, f)

        from src.config import Config
        from src.data_store import DataStore
        from src.screener import Screener

        config = Config(str(cfg_path))
        ds = DataStore(config)
        ds.load_symbols_from_json("data/symbols.json")

        # 日足データを1件追加
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO daily_bars (code, date, close, volume, turnover) VALUES ('JP.7203', '2026-07-01', 1000, 1000, 100000000)")
        conn.execute("""
            INSERT INTO indicators (code, date, close, volume, turnover, ma5, ma25, daily_return, return_5d, history_days, volume_ratio, high_20d, distance_from_high_20d, volume_ma20)
            VALUES ('JP.7203', '2026-07-01', 1000, 1000, 100000000, 990, 980, 1.0, 3.0, 30, 1.5, 1010, -1.0, 800)
        """)
        conn.commit()
        conn.close()

        screener = Screener(config)
        candidates = screener.screen_candidates(date="2026-07-01")
        screener.save_signals_to_db(candidates)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT strategy_name FROM signals LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "momentum"
        conn.close()
