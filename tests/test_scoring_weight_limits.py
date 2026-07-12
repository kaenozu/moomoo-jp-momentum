"""Regression coverage for scoring-weight range validation."""

import pytest

from src.config import Config
from src.scoring import Scorer


def _config(weights: dict[str, float]) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["scoring"] = {
        "enable_risk_penalty": True,
        "weights": weights,
    }
    return config


def test_positive_weight_sum_over_100_is_rejected() -> None:
    with pytest.raises(ValueError, match="加点合計は100以下"):
        Scorer(
            _config(
                {
                    "trend": 50,
                    "volume": 50,
                    "relative_strength": 1,
                    "liquidity": 0,
                    "high_20d": 0,
                    "risk_warning": 0,
                }
            )
        )


def test_positive_weight_sum_of_100_is_accepted() -> None:
    scorer = Scorer(
        _config(
            {
                "trend": 40,
                "volume": 20,
                "relative_strength": 20,
                "liquidity": 10,
                "high_20d": 10,
                "risk_warning": -30,
            }
        )
    )

    assert scorer.max_possible_score == 100.0
