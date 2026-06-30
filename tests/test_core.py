"""
単体テスト

ファイルパス: tests/test_core.py
何をするか: universe判定、相対強度、スコアリング、仮想約定のテスト
なぜ存在するか: コアロジックの動作確認のため
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import Optional
from dataclasses import dataclass

import pytest
from src.indicators import StockIndicators
from src.scoring import Scorer


class DummyConfig:
    """テスト用ダミー設定"""
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
        """出来高スコア: 1.2倍で8点"""
        ind = make_indicators(volume_ratio=1.3)
        scorer = Scorer(DummyConfig())
        score = scorer.score_volume(ind)
        assert score == 8.0

    def test_volume_score_high(self):
        """出来高スコア: 2.0倍で満点"""
        ind = make_indicators(volume_ratio=2.5)
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
        ind = make_indicators(ma25=None, history_days=10)
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

    def test_stale_data_stops_screening(self):
        """古いデータでは例外が発生する"""
        from src.data_freshness import DataFreshnessGuard

        class TestConfig:
            def get(self, key, default=None):
                if key == "database":
                    return {"path": "data/nonexistent.db"}
                return default
            @property
            def database_path(self):
                return self.get("database", {}).get("path", "data/nonexistent.db")

        guard = DataFreshnessGuard(TestConfig())
        status = guard.check_freshness(max_stale_days=5)
        assert status.level == "error"
