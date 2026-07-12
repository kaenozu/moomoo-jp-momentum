"""Regression coverage for configurable scoring component weights."""

import pytest

from src.config import Config
from src.indicators import StockIndicators
from src.scoring import Scorer


def _config(weights: dict[str, float] | None = None) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    scoring: dict[str, object] = {"enable_risk_penalty": True}
    if weights is not None:
        scoring["weights"] = weights
    config._config["scoring"] = scoring
    config._config["screening"] = {
        "min_turnover": 1_000_000_000,
        "risk_daily_return_threshold": 8.0,
        "risk_return_5d_threshold": 15.0,
        "risk_volume_ratio_threshold": 5.0,
    }
    return config


def _indicators(
    *,
    close: float = 1000.0,
    ma5: float = 990.0,
    ma25: float = 980.0,
    volume_ratio: float = 2.5,
    volume_ratio_percentile: float = 90.0,
    return_5d: float = 6.0,
    return_5d_vs_benchmark: float = 6.0,
    turnover: float = 10_000_000_000.0,
    high_20d_distance: float = -1.0,
    daily_return: float = 1.0,
) -> StockIndicators:
    return StockIndicators(
        code="JP.7203",
        name="テスト銘柄",
        date="2026-07-10",
        close=close,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        ma5=ma5,
        ma25=ma25,
        volume=1_000_000,
        volume_ma20=500_000,
        volume_ratio=volume_ratio,
        turnover=turnover,
        high_20d=close * 1.01,
        high_20d_distance=high_20d_distance,
        prev_close=close - 1,
        daily_return=daily_return,
        return_5d=return_5d,
        return_5d_vs_benchmark=return_5d_vs_benchmark,
        history_days=60,
        volume_ratio_percentile=volume_ratio_percentile,
    )


def test_default_weights_preserve_existing_maximum_scores() -> None:
    score = Scorer(_config()).score(_indicators())

    assert score.trend == 30.0
    assert score.volume == 20.0
    assert score.relative_strength == 25.0
    assert score.liquidity == 15.0
    assert score.high_20d == 10.0
    assert score.risk_penalty == 0.0
    assert score.total == 100.0


def test_custom_weights_scale_each_component_maximum() -> None:
    score = Scorer(
        _config(
            {
                "trend": 15,
                "volume": 10,
                "relative_strength": 50,
                "liquidity": 0,
                "high_20d": 20,
                "risk_warning": -12,
            }
        )
    ).score(_indicators())

    assert score.trend == 15.0
    assert score.volume == 10.0
    assert score.relative_strength == 50.0
    assert score.liquidity == 0.0
    assert score.high_20d == 20.0
    assert score.risk_penalty == 0.0
    assert score.total == 95.0


def test_partial_raw_score_is_scaled_proportionally() -> None:
    scorer = Scorer(
        _config(
            {
                "trend": 60,
                "volume": 0,
                "relative_strength": 0,
                "liquidity": 0,
                "high_20d": 0,
                "risk_warning": 0,
            }
        )
    )
    indicators = _indicators(ma5=1010.0, ma25=990.0)

    score = scorer.score(indicators)

    assert scorer.score_trend(indicators) == 20.0
    assert score.trend == 40.0
    assert score.total == 40.0


def test_risk_warning_weight_scales_negative_penalty() -> None:
    score = Scorer(
        _config(
            {
                "trend": 30,
                "volume": 20,
                "relative_strength": 25,
                "liquidity": 15,
                "high_20d": 10,
                "risk_warning": -15,
            }
        )
    ).score(
        _indicators(
            daily_return=8.0,
            return_5d=15.0,
            volume_ratio=5.0,
        )
    )

    assert score.risk_penalty == -15.0
    assert score.total == 85.0


def test_zero_weights_disable_all_components() -> None:
    score = Scorer(
        _config(
            {
                "trend": 0,
                "volume": 0,
                "relative_strength": 0,
                "liquidity": 0,
                "high_20d": 0,
                "risk_warning": 0,
            }
        )
    ).score(_indicators())

    assert score.total == 0.0


@pytest.mark.parametrize(
    "weights, expected_message",
    [
        ({"trend": -1}, "scoring.weights.trend"),
        ({"risk_warning": 1}, "scoring.weights.risk_warning"),
        ({"volume": float("inf")}, "scoring.weights.volume"),
    ],
)
def test_invalid_weights_fail_fast(
    weights: dict[str, float],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        Scorer(_config(weights))
