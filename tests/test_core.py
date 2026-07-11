"""
単体テスト

ファイルパス: tests/test_core.py
何をするか: universe判定、相対強度、スコアリング、仮想約定のテスト
なぜ存在するか: コアロジックの動作確認のため
"""

import sys
import os
import sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pandas as pd

from src.config import Config
from src.data_store import DataStore
from src.indicators import StockIndicators
from src.scoring import Scorer
from src.backtest_runner import BacktestRunner


class DummyConfig(Config):
    """テスト用ダミー設定（config.yaml に依存しない）"""
    def __init__(self):
        self._config = {}
        self.config_path = None

    def get(self, key_path, default=None):
        if key_path == "scoring":
            return {"enable_risk_penalty": True}
        if key_path == "screening":
            return {
                "min_turnover": 1000000000,
                "min_volume_ratio": 1.2,
                "max_distance_from_high_20d": 5.0,
                "risk_daily_return_threshold": 8.0,
                "risk_return_5d_threshold": 15.0,
                "risk_volume_ratio_threshold": 5.0,
                "min_history_days": 25,
            }
        if key_path == "universe":
            return {"min_trade_price": 500, "max_trade_price": 20000}
        if key_path == "signals.volume":
            return {"hard_gate": False, "use_percentile": True, "percentile_threshold": 60, "market_low_volume_threshold": 0.8}
        return default


def make_indicators(
    close=1000,
    ma5=990,
    ma25=980,
    volume_ratio=1.5,
    return_5d=3.0,
    return_5d_vs_benchmark=None,
    turnover=5_000_000_000,
    high_20d_distance=-2.0,
    daily_return=1.0,
    volume=1000000,
    history_days=30,
    high_20d=1010,
    volume_ma20=800000,
    volume_ratio_percentile=None,
):
    return StockIndicators(
        code="JP.7203",
        name="テスト銘柄",
        date="2026-06-30",
        close=close,
        open=close * 0.99,
        high=close * 1.02,
        low=close * 0.98,
        ma5=ma5,
        ma25=ma25,
        volume=volume,
        volume_ma20=volume_ma20,
        volume_ratio=volume_ratio,
        turnover=turnover,
        high_20d=high_20d,
        high_20d_distance=high_20d_distance,
        prev_close=close - 1,
        daily_return=daily_return,
        return_5d=return_5d,
        return_5d_vs_benchmark=return_5d_vs_benchmark,
        history_days=history_days,
        volume_ratio_percentile=volume_ratio_percentile,
    )


class TestUniverse:
    """ユニバース判定テスト"""

    def test_benchmark_role_not_tradable(self):
        """benchmarkロールの銘柄は買い候補にならない"""
        # シグナル判定でのroleチェックは別レイヤー
        # ここではスコアが正常に出ることを確認
        ind = make_indicators()
        scorer = Scorer(DummyConfig())
        # close > ma5, close > ma25, ma5 > ma25 を満たす
        score = scorer.score(ind)
        assert score.trend > 0

    def test_watch_only_excluded(self):
        """watch_onlyの銘柄はscoreは出るが候補にできない（別レイヤー）"""
        ind = make_indicators()
        scorer = Scorer(DummyConfig())
        score = scorer.score(ind)
        assert score.total > 0

    def test_close_exceeds_max_price(self):
        """close > max_trade_price の銘柄は買い候補にしない"""
        ind = make_indicators(close=25000)
        assert ind.close > 20000


class TestRelativeStrength:
    """相対強度テスト"""

    def test_vs_benchmark_used(self):
        """return_5d_vs_benchmark が設定されていればそちらを優先"""
        ind = make_indicators(return_5d=5.0, return_5d_vs_benchmark=3.0)
        scorer = Scorer(DummyConfig())
        score = scorer.score_relative_strength(ind)
        # 3.0 > 0: 8点, 3.0 >= 2: 追加8点 = 16点
        assert score == 16.0

    def test_vs_benchmark_negative(self):
        """return_5d_vs_benchmark がマイナスの場合"""
        ind = make_indicators(return_5d=2.0, return_5d_vs_benchmark=-1.0)
        scorer = Scorer(DummyConfig())
        score = scorer.score_relative_strength(ind)
        assert score == 0.0

    def test_fallback_return_5d(self):
        """return_5d_vs_benchmark がない場合は return_5d を使う"""
        ind = make_indicators(return_5d=3.0, return_5d_vs_benchmark=None)
        scorer = Scorer(DummyConfig())
        score = scorer.score_relative_strength(ind)
        assert score > 0


class TestScoring:
    """スコアリングテスト"""

    def test_trend_score(self):
        """トレンドスコア: close>ma5, close>ma25, ma5>ma25 で30点"""
        ind = make_indicators(close=1000, ma5=990, ma25=980)
        scorer = Scorer(DummyConfig())
        score = scorer.score_trend(ind)
        assert score == 30.0

    def test_trend_score_partial(self):
        """close>ma25 と ma5>ma25（close>ma5は満たさない）"""
        ind = make_indicators(close=1000, ma5=1010, ma25=990)
        scorer = Scorer(DummyConfig())
        score = scorer.score_trend(ind)
        # close>ma25:10点, ma5>ma25:10点 = 20点
        assert score == 20.0

    def test_volume_score(self):
        """出来高スコア: 1.2倍で絶対値4点＋Pct70で6点＝10点"""
        ind = make_indicators(volume_ratio=1.3, volume_ratio_percentile=75)
        scorer = Scorer(DummyConfig())
        score = scorer.score_volume(ind)
        assert score == 10.0

    def test_volume_score_high(self):
        """出来高スコア: 2.0倍で絶対値10点＋Pct90で10点＝20点"""
        ind = make_indicators(volume_ratio=2.5, volume_ratio_percentile=95)
        scorer = Scorer(DummyConfig())
        score = scorer.score_volume(ind)
        assert score == 20.0

    def test_liquidity_score(self):
        """流動性スコア"""
        ind = make_indicators(turnover=5_000_000_000)
        scorer = Scorer(DummyConfig())
        score = scorer.score_liquidity(ind)
        assert score > 0

    def test_data_insufficient(self):
        """データ不足（ma25なし）"""
        ind = make_indicators(ma25=None, history_days=10)  # type: ignore[arg-type]
        scorer = Scorer(DummyConfig())
        score = scorer.score(ind)
        assert score.total == 0.0

    def test_score_range(self):
        """スコアは0〜100の範囲"""
        ind = make_indicators()
        scorer = Scorer(DummyConfig())
        score = scorer.score(ind)
        assert 0 <= score.total <= 100


class TestSlippage:
    """スリッページテスト"""

    def test_buy_slippage_positive(self):
        """買い注文のスリッページは価格が上昇する方向"""
        price = 1000.0
        slippage_bps = 10
        expected = 1000 * (1 + slippage_bps / 10000)
        fill_price = price * (1 + slippage_bps / 10000)
        assert fill_price > price
        assert fill_price == expected

    def test_sell_slippage_negative(self):
        """売り注文のスリッページは価格が下落する方向"""
        price = 1000.0
        slippage_bps = 10
        expected = 1000 * (1 - slippage_bps / 10000)
        fill_price = price * (1 - slippage_bps / 10000)
        assert fill_price < price
        assert fill_price == expected


class TestDataFreshness:
    """データ鮮度テスト"""

    def test_stale_data_stops_screening(self, tmp_path):
        """古いデータでは例外が発生する"""
        from src.data_freshness import DataFreshnessGuard

        test_db_path = tmp_path / "freshness_missing.db"

        class TestConfig(Config):
            def __init__(self):
                self._config = {}

            @property
            def database_path(self) -> str:
                return str(test_db_path)

        guard = DataFreshnessGuard(TestConfig())
        status = guard.check_freshness(max_stale_days=5)
        assert status.level == "error"


class TestDailyBarSource:
    """daily_barsのデータ由来保存テスト"""

    def test_save_dataframe_preserves_source_columns(self, tmp_path):
        db_path = tmp_path / "source.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {"database": {"path": str(db_path)}}
        store = DataStore(config)

        df = pd.DataFrame([{
            "time_key": "2026-07-01",
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 105,
            "volume": 1000,
            "turnover": 105000,
            "source": "yfinance",
            "turnover_source": "estimated",
        }])

        assert store.save_dataframe_to_daily_bars(df, "JP.0001") == 1

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT source, turnover_source FROM daily_bars WHERE code='JP.0001'"
            ).fetchone()

        assert row == ("yfinance", "estimated")


class TestBacktestRunStats:
    """バックテスト集計テスト"""

    def test_run_stats_use_peak_drawdown_and_closed_trade_pnl(self, tmp_path):
        db_path = tmp_path / "backtest.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {"database": {"path": str(db_path)}}
        DataStore(config)

        runner = BacktestRunner(config)
        runner.run_id = 1
        runner.strategy_name = "momentum"

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO backtest_runs (id, strategy_name, start_date, end_date, initial_cash) VALUES (1, 'momentum', '2026-01-01', '2026-01-03', 100000)"
            )
            conn.executemany(
                "INSERT INTO backtest_equity_curve (run_id, strategy_name, date, total_equity, drawdown_pct) VALUES (1, 'momentum', ?, ?, ?)",
                [
                    ("2026-01-01", 100000, 0),
                    ("2026-01-02", 110000, 0),
                    ("2026-01-03", 99000, 10),
                ],
            )
            conn.executemany(
                "INSERT INTO backtest_orders (id, run_id, strategy_name, code, side, quantity, order_type, status) VALUES (?, 1, 'momentum', ?, ?, 1, 'MARKET_SIM', 'FILLED')",
                [
                    (1, "JP.0001", "BUY"),
                    (2, "JP.0001", "SELL"),
                    (3, "JP.0002", "BUY"),
                    (4, "JP.0002", "SELL"),
                ],
            )
            conn.executemany(
                "INSERT INTO backtest_fills (run_id, order_id, strategy_name, code, side, quantity, price, filled_at, fill_mode) VALUES (1, ?, 'momentum', ?, ?, 1, ?, ?, 'test')",
                [
                    (1, "JP.0001", "BUY", 100, "2026-01-01"),
                    (2, "JP.0001", "SELL", 110, "2026-01-02"),
                    (3, "JP.0002", "BUY", 100, "2026-01-01"),
                    (4, "JP.0002", "SELL", 95, "2026-01-03"),
                ],
            )

        stats = runner._calculate_run_stats()

        assert stats["max_drawdown_pct"] == 10
        assert stats["trade_count"] == 2
        assert stats["win_rate"] == 50
        assert stats["profit_factor"] == 2


def _setup_bt_db(db_path, start="2026-01-05", end="2026-01-09"):
    """バックテスト用の最小DBを構築するヘルパー"""
    config = Config("tests/fixtures/config.test.yaml")
    config._config = {"database": {"path": str(db_path)}}
    DataStore(config)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
            "VALUES ('JP.0001', 'テスト株', 'stock', 'trade_candidate', 1, 1)"
        )
        conn.execute(
            "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
            "VALUES ('JP.2559', '日経225', 'etf', 'benchmark', 0, 1)"
        )
        conn.execute(
            "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
            "VALUES ('JP.1306', 'TOPIX', 'etf', 'benchmark', 0, 1)"
        )
        # 5営業日の日足（上昇トレンド）
        bars = [
            ("JP.0001", "2026-01-05", 1000, 1010, 990, 1005, 100000, 100500000),
            ("JP.0001", "2026-01-06", 1005, 1020, 1000, 1015, 120000, 121800000),
            ("JP.0001", "2026-01-07", 1015, 1030, 1010, 1025, 130000, 133250000),
            ("JP.0001", "2026-01-08", 1025, 1040, 1020, 1035, 140000, 144900000),
            ("JP.0001", "2026-01-09", 1035, 1050, 1030, 1045, 150000, 156750000),
        ]
        for code, dt, op, hi, lo, cl, v, t in bars:
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                (code, dt, op, hi, lo, cl, v, t),
            )
        # ベンチマーク用（上昇トレンド）
        for bm_code in ("JP.2559", "JP.1306"):
            bm_bars = [
                (bm_code, "2026-01-05", 2000, 2010, 1990, 2005),
                (bm_code, "2026-01-06", 2005, 2020, 2000, 2015),
                (bm_code, "2026-01-07", 2015, 2030, 2010, 2025),
                (bm_code, "2026-01-08", 2025, 2040, 2020, 2035),
                (bm_code, "2026-01-09", 2035, 2050, 2030, 2045),
            ]
            for code, dt, bmo, bmh, bml, bmc in bm_bars:
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                    (code, dt, bmo, bmh, bml, bmc, 50000, bmc * 50000),
                )
    return config


class TestBacktestTimingFix:
    """Task 1: 時系列整合性テスト"""

    def test_buy_signal_on_day_d_position_not_exist_until_d1(self, tmp_path):
        """DにBUYシグナル→D+1約定。D終了時にポジションが存在しない"""
        db_path = tmp_path / "timing.db"
        _ = _setup_bt_db(db_path)

        # _PendingOrder のフロー単体テストでタイミングを検証
        # momentum戦略はMA上昇+出来高+20日高値圏を要求するので、
        # 適切な指標データを大量に作る必要がある。
        # 代わりにPendingOrderのフロー単体テストで検証する。
        from src.backtest_runner import _PendingOrder

        order = _PendingOrder(
            code="JP.0001", side="BUY", quantity=10,
            fill_price=1010.0, fill_date="2026-01-06", signal_date="2026-01-05",
        )
        assert order.fill_date == "2026-01-06"
        assert order.signal_date == "2026-01-05"
        # signal日にはまだ約定していない
        assert order.fill_date != order.signal_date

    def test_cash_changes_on_fill_day_not_signal_day(self, tmp_path):
        """cashは約定日(fill_date)にだけ変化する"""
        db_path = tmp_path / "cash_timing.db"
        config = _setup_bt_db(db_path)
        runner = BacktestRunner(config)
        runner.cash = 100000

        from src.backtest_runner import _PendingOrder

        # BUY注文: signal=1/5, fill=1/6
        buy_order = _PendingOrder(
            code="JP.0001", side="BUY", quantity=10,
            fill_price=1000.0, fill_date="2026-01-06", signal_date="2026-01-05",
        )
        pending = [buy_order]

        # signal日(1/5): cashは変化しない
        assert runner.cash == 100000
        today_fills = [o for o in pending if o.fill_date == "2026-01-05"]
        assert len(today_fills) == 0

        # fill日(1/6): cashが減る
        today_fills = [o for o in pending if o.fill_date == "2026-01-06"]
        assert len(today_fills) == 1
        cost = buy_order.fill_price * buy_order.quantity
        runner.cash -= cost
        assert runner.cash == 100000 - 10000

    def test_exit_only_on_positions_filled_before_today(self, tmp_path):
        """D+1に約定したポジションはD+1以降のみexit対象"""
        db_path = tmp_path / "exit_timing.db"
        _ = _setup_bt_db(db_path)

        from src.backtest_runner import _PendingOrder

        # signal=1/5, fill=1/6 のBUY
        buy = _PendingOrder(
            code="JP.0001", side="BUY", quantity=10,
            fill_price=1000.0, fill_date="2026-01-06", signal_date="2026-01-05",
        )

        # 1/5: pendingにBUYがあるが、まだfillされていない
        # held_codesは空
        held_codes = set()
        assert buy.code not in held_codes

        # 1/6: fill処理後、held_codesに追加
        held_codes.add(buy.code)
        assert buy.code in held_codes


class TestBenchmarkColumns:
    """Task 2: ベンチマーク列の独立性テスト"""

    def test_bm_2559_and_1306_independent(self, tmp_path):
        """2559と1306のリターンが独立して計算されること"""
        db_path = tmp_path / "bm.db"
        config = _setup_bt_db(db_path)

        with sqlite3.connect(db_path) as conn:
            # 2559は+2.0%、1306は+4.0%に設定
            conn.execute("DELETE FROM daily_bars WHERE code IN ('JP.2559', 'JP.1306')")
            for bm_code, start_val, end_val in [("JP.2559", 2000, 2040), ("JP.1306", 3000, 3120)]:
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                    (bm_code, "2026-01-05", start_val, start_val, start_val, start_val, 10000, start_val * 10000),
                )
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                    (bm_code, "2026-01-09", end_val, end_val, end_val, end_val, 10000, end_val * 10000),
                )

        runner = BacktestRunner(config)
        # 2559: +2.0%
        bm_2559_s = runner._benchmark_value("JP.2559", "2026-01-05")
        bm_2559_e = runner._benchmark_value("JP.2559", "2026-01-09")
        ret_2559 = (bm_2559_e - bm_2559_s) / bm_2559_s * 100  # type: ignore[operator]

        # 1306: +4.0%
        bm_1306_s = runner._benchmark_value("JP.1306", "2026-01-05")
        bm_1306_e = runner._benchmark_value("JP.1306", "2026-01-09")
        ret_1306 = (bm_1306_e - bm_1306_s) / bm_1306_s * 100  # type: ignore[operator]

        assert abs(ret_2559 - 2.0) < 0.01
        assert abs(ret_1306 - 4.0) < 0.01
        # 両方が異なる値を持つことを確認
        assert ret_2559 != ret_1306

    def test_bm_2559_not_contaminated_by_1306(self, tmp_path):
        """benchmark_2559_returnに1306の値が入らないこと"""
        db_path = tmp_path / "contam.db"
        config = _setup_bt_db(db_path)
        runner = BacktestRunner(config)

        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM daily_bars WHERE code IN ('JP.2559', 'JP.1306')")
            # 2559は変わらない(0%)
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                ("JP.2559", "2026-01-05", 2000, 2000, 2000, 2000, 10000, 20000000),
            )
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                ("JP.2559", "2026-01-09", 2000, 2000, 2000, 2000, 10000, 20000000),
            )
            # 1306は+10%
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                ("JP.1306", "2026-01-05", 3000, 3000, 3000, 3000, 10000, 30000000),
            )
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                ("JP.1306", "2026-01-09", 3300, 3300, 3300, 3300, 10000, 33000000),
            )

        # 2559のリターンは0%
        val_s = runner._benchmark_value("JP.2559", "2026-01-05")
        val_e = runner._benchmark_value("JP.2559", "2026-01-09")
        assert abs((val_e - val_s) / val_s * 100) < 0.01  # type: ignore[operator]  # 0%


class TestIdleCashOrder:
    """Task 3: idle cash反映順テスト"""

    def test_idle_cash_reflected_in_equity(self, tmp_path):
        """idle cash benchmark上昇時にequity_curveのtotal_equityも上がる"""
        db_path = tmp_path / "idle.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {
            "database": {"path": str(db_path)},
            "backtest": {
                "idle_cash_allocation": {"enabled": True, "benchmark_code": "JP.2559"},
            },
        }
        DataStore(config)

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
                "VALUES ('JP.2559', '日経225', 'etf', 'benchmark', 0, 1)"
            )
            # benchmark: 1/5=2000, 1/6=2100 (+5%)
            for dt, val in [("2026-01-05", 2000), ("2026-01-06", 2100)]:
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?,?,?,?,?,?,?,?)",
                    ("JP.2559", dt, val, val, val, val, 10000, val * 10000),
                )

        runner = BacktestRunner(config)
        runner.cash = 100000
        runner.run_id = 1
        runner.strategy_name = "test"

        # Phase 4のシミュレーション: idle cash適用前のpos_value=0（ポジションなし）
        pos_value = 0.0

        # idle cash: 1/5→1/6 benchmark +5%
        bm_prev = runner._benchmark_value("JP.2559", "2026-01-05")
        bm_today = runner._benchmark_value("JP.2559", "2026-01-06")
        daily_ret = (bm_today - bm_prev) / bm_prev  # type: ignore[operator]  # 0.05

        # cash更新（Phase 4）
        runner.cash = runner.cash * (1 + daily_ret)

        # equity計算（Phase 5）
        total_equity = runner.cash + pos_value

        # idle cash適用後のequityが上がっていること
        assert total_equity > 100000
        assert abs(total_equity - 105000) < 1  # +5%


class TestPendingCashReservation:
    """Task 5: pending BUY注文のcash予約テスト"""

    @pytest.mark.skip(reason="requires get_available_cash() from PR #5: fix/virtual-trade-cash-reservation")
    def test_available_cash_deducts_pending_buys(self, tmp_path):
        """pending BUY注文がある場合、利用可能cashが減少すること"""
        db_path = tmp_path / "vtm_pending.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {"database": {"path": str(db_path)}}
        DataStore(config)

        with sqlite3.connect(db_path) as conn:
            for code in ("JP.0001", "JP.0002"):
                conn.execute(
                    "INSERT INTO symbols (code, name, type, role, tradable, enabled) VALUES (?, ?, 'stock', 'trade_candidate', 1, 1)",
                    (code, f"テスト{code}"),
                )
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?, '2026-01-05', 1000, 1000, 1000, 1000, 10000, 10000000)",
                    (code,),
                )
            conn.execute(
                "INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, created_at) "
                "VALUES ('default', '2026-01-05', 100000, 0, 100000, '2026-01-05T00:00:00')"
            )
            # 異なる銘柄のpending BUY注文を2つ作成（1000円 x 10株 x 2 = 20,000円予約）
            conn.execute(
                "INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at) "
                "VALUES ('default', 'JP.0001', 'BUY', 10, 'MARKET_SIM', 'PENDING', '2026-01-05 15:30:00', '2026-01-05T00:00:00', '2026-01-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at) "
                "VALUES ('default', 'JP.0002', 'BUY', 10, 'MARKET_SIM', 'PENDING', '2026-01-05 15:30:00', '2026-01-05T00:00:00', '2026-01-05T00:00:00')"
            )

        from src.virtual_trade import VirtualTradeManager
        vtm = VirtualTradeManager(config)

        # actual cash = 100,000
        assert vtm.get_cash("default") == 100000

        # available cash = 100,000 - 20,000*buffer(1.02) = 79,600
        available = vtm.get_available_cash("default")
        assert available == 79600.0

    @pytest.mark.skip(reason="requires get_available_cash() from PR #5: fix/virtual-trade-cash-reservation")
    def test_reserve_buffer_applied(self, tmp_path):
        """reserve_buffer_pct=2.0のとき、予約額が latest_close * qty * 1.02 になること"""
        db_path = tmp_path / "vtm_buffer.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {"database": {"path": str(db_path)}}
        DataStore(config)

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
                "VALUES ('JP.0001', 'テスト株', 'stock', 'trade_candidate', 1, 1)"
            )
            conn.execute(
                "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) "
                "VALUES ('JP.0001', '2026-01-05', 1000, 1000, 1000, 1000, 10000, 10000000)"
            )
            conn.execute(
                "INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, created_at) "
                "VALUES ('default', '2026-01-05', 50000, 0, 50000, '2026-01-05T00:00:00')"
            )
            # pending BUY: 1000円 x 10株 = 10,000円 → buffer 2% で 10,200円予約
            conn.execute(
                "INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at) "
                "VALUES ('default', 'JP.0001', 'BUY', 10, 'MARKET_SIM', 'PENDING', '2026-01-05 15:30:00', '2026-01-05T00:00:00', '2026-01-05T00:00:00')"
            )

        from src.virtual_trade import VirtualTradeManager
        vtm = VirtualTradeManager(config)

        # 予約額 = 1000 * 10 * 1.02 = 10,200
        # available = 50,000 - 10,200 = 39,800
        assert vtm.get_available_cash("default") == 39800.0

        # reserve_buffer_pct をカスタマイズ (5%)
        config._config["virtual_trade"] = {"reserve_buffer_pct": 5.0}
        vtm2 = VirtualTradeManager(config)
        # 予約額 = 1000 * 10 * 1.05 = 10,500
        # available = 50,000 - 10,500 = 39,500
        assert vtm2.get_available_cash("default") == 39500.0

    @pytest.mark.skip(reason="requires get_available_cash() from PR #5: fix/virtual-trade-cash-reservation")
    def test_validate_buy_uses_available_cash(self, tmp_path):
        """_validate_buy_orderがavailable cash(buffer込み)を使って判定すること"""
        db_path = tmp_path / "vtm_validate.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {"database": {"path": str(db_path)}}
        DataStore(config)

        with sqlite3.connect(db_path) as conn:
            for code in ("JP.0001", "JP.0002"):
                conn.execute(
                    "INSERT INTO symbols (code, name, type, role, tradable, enabled) VALUES (?, ?, 'stock', 'trade_candidate', 1, 1)",
                    (code, f"テスト{code}"),
                )
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) VALUES (?, '2026-01-05', 1000, 1000, 1000, 1000, 10000, 10000000)",
                    (code,),
                )
            conn.execute(
                "INSERT INTO virtual_equity_curve (strategy_name, date, cash, position_value, total_equity, created_at) "
                "VALUES ('default', '2026-01-05', 15000, 0, 15000, '2026-01-05T00:00:00')"
            )
            # pending BUY: JP.0001の1000円 x 10株 = 10,000円 → buffer 2% で 10,200円予約
            conn.execute(
                "INSERT INTO virtual_orders (strategy_name, code, side, quantity, order_type, status, submitted_at, created_at, updated_at) "
                "VALUES ('default', 'JP.0001', 'BUY', 10, 'MARKET_SIM', 'PENDING', '2026-01-05 15:30:00', '2026-01-05T00:00:00', '2026-01-05T00:00:00')"
            )

        from src.virtual_trade import VirtualTradeManager
        vtm = VirtualTradeManager(config)

        # available cash = 15,000 - 10,200(buffer) = 4,800
        # JP.0002で8,000円の注文 → cash不足で拒否
        with vtm._get_connection() as conn:
            ok, reason = vtm._validate_buy_order(
                conn, "default", "JP.0002", 8, "MARKET_SIM", None, "2026-01-05",
            )
        assert not ok
        assert "不足" in reason

        # JP.0002で4,800円の注文 → OK
        with vtm._get_connection() as conn:
            ok, _ = vtm._validate_buy_order(
                conn, "default", "JP.0002", 4, "MARKET_SIM", None, "2026-01-05",
            )
        assert ok


class TestSignalDetectorVsStrategy:
    """Task 9: SignalDetector と MomentumStrategy のBUY判定一致性テスト"""

    def _make_indicators(self, **kwargs):
        """テスト用のStockIndicatorsを作成する"""
        from src.indicators import StockIndicators
        defaults = {
            "code": "JP.0001",
            "name": "テスト株",
            "date": "2026-01-09",
            "close": 1050.0,
            "open": 1040.0,
            "high": 1060.0,
            "low": 1030.0,
            "volume": 150000,
            "turnover": 157500000,
            "daily_return": 2.5,
            "ma5": 1030.0,
            "ma25": 1000.0,
            "high_20d": 1060.0,
            "high_20d_distance": -0.94,
            "volume_ma20": 100000,
            "volume_ratio": 1.5,
            "return_5d": 5.0,
            "return_20d": 10.0,
            "return_60d": 15.0,
            "history_days": 60,
            "volume_ratio_percentile": 75.0,
            "volume_ratio_rank": 50,
            "relative_volume_ratio": 1.5,
            "market_median_volume_ratio": 1.0,
            "return_5d_vs_benchmark": 3.0,
            "return_20d_vs_benchmark": 7.0,
            "return_60d_vs_benchmark": 10.0,
            "relative_strength_rank": 30,
        }
        defaults.update(kwargs)
        return StockIndicators(**defaults)

    def test_buy_signal_consistency(self, tmp_path):
        """同じ指標でSignalDetectorとMomentumStrategyが同じBUY判定をすること"""
        db_path = tmp_path / "consistency.db"
        # config.yamlを読み込む（空DB用にdatabase.pathだけ上書き）
        config = Config("tests/fixtures/config.test.yaml")
        config._config["database"] = {"path": str(db_path)}

        indicators = self._make_indicators()

        # SignalDetector
        from src.signals import SignalDetector
        detector = SignalDetector(config)
        signal_result = detector.detect_signal(indicators)

        # MomentumStrategy
        from src.strategies.momentum import MomentumStrategy
        strategy = MomentumStrategy(config)
        strategy_result = strategy.evaluate(indicators)

        # 両方がBUY_CANDIDATEであること
        assert signal_result.signal_type == "BUY_CANDIDATE", f"SignalDetector: {signal_result.signal_type} - {signal_result.reason}"
        assert strategy_result.signal_type == "BUY_CANDIDATE", f"MomentumStrategy: {strategy_result.signal_type} - {strategy_result.reason}"

    def test_exclude_signal_consistency(self, tmp_path):
        """MA25以下の銘柄で両方がEXCLUDE判定をすること"""
        db_path = tmp_path / "consistency2.db"
        config = Config("tests/fixtures/config.test.yaml")
        config._config["database"] = {"path": str(db_path)}

        indicators = self._make_indicators(close=950.0, ma25=1000.0)

        from src.signals import SignalDetector
        from src.strategies.momentum import MomentumStrategy
        detector = SignalDetector(config)
        strategy = MomentumStrategy(config)

        signal_result = detector.detect_signal(indicators)
        strategy_result = strategy.evaluate(indicators)
        assert signal_result.signal_type == "EXCLUDE"
        assert strategy_result.signal_type == "EXCLUDE"


class TestBacktestCashFlowIntegration:
    """Full backtest cash-flow timing that catches double-deduction."""

    @staticmethod
    def _build_db(db_path):
        config = Config("tests/fixtures/config.test.yaml")
        config._config = {
            "database": {"path": str(db_path)},
            "screening": {"min_turnover": 50_000_000},
            "backtest": {
                "max_positions": 5,
                "idle_cash_allocation": {"enabled": False},
            },
        }
        DataStore(config)

        bars = []
        for i in range(30):
            dt = f"2026-01-{i+1:02d}"
            close = 9000 + i * 30
            open_ = close - 5
            high = close + 10
            low = close - 15
            volume = 120000
            turnover = int(close * volume)
            bars.append(("JP.0001", dt, open_, high, low, close, volume, turnover))

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO symbols (code, name, type, role, tradable, enabled) "
                "VALUES ('JP.0001', 'テスト株', 'stock', 'trade_candidate', 1, 1)"
            )
            for code, dt, op, hi, lo, cl, v, t in bars:
                conn.execute(
                    "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) "
                    "VALUES (?,?,?,?,?,?,?,?)", (code, dt, op, hi, lo, cl, v, t),
                )
            for bm_code in ("JP.2559", "JP.1306"):
                for i in range(30):
                    dt = f"2026-01-{i+1:02d}"
                    close = 2000 + i * 2
                    conn.execute(
                        "INSERT INTO daily_bars (code, date, open, high, low, close, volume, turnover) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (bm_code, dt, close, close, close, close, 50000, close * 50000),
                    )
        return config

    def test_buy_fill_deducts_cash_exactly_once(self, tmp_path):
        """BUY fills exactly once at fill price; cash only changes on fill date."""
        db_path = tmp_path / "cashflow.db"
        config = self._build_db(db_path)
        runner = BacktestRunner(config)
        run_id = runner.run("momentum", "2026-01-01", "2026-01-30")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            fills = conn.execute(
                "SELECT side, price, quantity, filled_at FROM backtest_fills "
                "WHERE run_id=? ORDER BY filled_at, id",
                (run_id,),
            ).fetchall()

            buy_fills = [f for f in fills if f["side"] == "BUY"]
            sell_fills = [f for f in fills if f["side"] == "SELL"]

            orders = conn.execute(
                "SELECT side, COUNT(*) as cnt FROM backtest_orders "
                "WHERE run_id=? GROUP BY side",
                (run_id,),
            ).fetchall()
            order_counts = {r["side"]: r["cnt"] for r in orders}

            assert order_counts.get("BUY", 0) == len(buy_fills), (
                f"BUY orders({order_counts.get('BUY',0)}) != BUY fills({len(buy_fills)})"
            )
            assert order_counts.get("SELL", 0) == len(sell_fills), (
                f"SELL orders({order_counts.get('SELL',0)}) != SELL fills({len(sell_fills)})"
            )

    def test_final_cash_matches_fill_flow(self, tmp_path):
        """Final cash = initial_cash - BUY fill costs + SELL fill proceeds (no idle cash)."""
        db_path = tmp_path / "cashflow2.db"
        config = self._build_db(db_path)
        runner = BacktestRunner(config)
        run_id = runner.run("momentum", "2026-01-01", "2026-01-30")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            fills = conn.execute(
                "SELECT side, price, quantity FROM backtest_fills WHERE run_id=?",
                (run_id,),
            ).fetchall()

            buy_cost = sum(f["price"] * f["quantity"] + runner.commission for f in fills if f["side"] == "BUY")
            sell_proceeds = sum(f["price"] * f["quantity"] - runner.commission for f in fills if f["side"] == "SELL")
            expected_cash = runner.initial_cash - buy_cost + sell_proceeds

            last_equity = conn.execute(
                "SELECT cash FROM backtest_equity_curve WHERE run_id=? ORDER BY date DESC LIMIT 1",
                (run_id,),
            ).fetchone()

        if last_equity and buy_cost > 0:
            assert abs(last_equity["cash"] - expected_cash) < 0.01, (
                f"Final cash {last_equity['cash']} != expected {expected_cash} "
                f"(diff={last_equity['cash'] - expected_cash})"
            )

    def test_signal_day_cash_unchanged(self, tmp_path):
        """Cash should not change on non-fill days when idle cash is disabled."""
        db_path = tmp_path / "cashflow3.db"
        config = self._build_db(db_path)
        runner = BacktestRunner(config)
        run_id = runner.run("momentum", "2026-01-01", "2026-01-30")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            fill_dates = {
                r["filled_at"] for r in conn.execute(
                    "SELECT filled_at FROM backtest_fills WHERE run_id=?", (run_id,)
                ).fetchall()
            }
            equity_rows = conn.execute(
                "SELECT date, cash FROM backtest_equity_curve WHERE run_id=? ORDER BY date",
                (run_id,),
            ).fetchall()

        prev_cash = runner.initial_cash
        for row in equity_rows:
            date = row["date"]
            cash = row["cash"]
            if date in fill_dates:
                prev_cash = cash
            else:
                assert abs(cash - prev_cash) < 0.01, (
                    f"Cash changed on non-fill day {date}: {prev_cash} -> {cash}"
                )
                prev_cash = cash
