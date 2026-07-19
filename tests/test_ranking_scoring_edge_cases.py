import math
from pathlib import Path

import yaml

from src.config import Config
from src.indicators import StockIndicators
from src.ranking import score_desc_code_asc_key
from src.scoring import Scorer
from src.strategies import StrategyResult


def _config(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(tmp_path / "test.db")},
                "signals": {"quality_filter": None},
                "scoring": {
                    "enable_risk_penalty": True,
                    "weights": {"risk_warning": -30},
                },
            }
        ),
        encoding="utf-8",
    )
    return Config(str(path))


def _indicators() -> StockIndicators:
    return StockIndicators(
        code="JP.7203",
        name="Toyota",
        date="2026-07-01",
        close=100.0,
        open=99.0,
        high=101.0,
        low=98.0,
        ma5=95.0,
        ma25=90.0,
        volume=1000,
        volume_ratio=1.5,
        turnover=3_000_000_000,
        high_20d_distance=-1.0,
        return_5d=3.0,
        history_days=30,
    )


def test_quality_filter_null_falls_back_to_empty_mapping(tmp_path: Path) -> None:
    assert _config(tmp_path).quality_filter_config == {}


def test_nan_score_has_a_deterministic_zero_score_key() -> None:
    assert score_desc_code_asc_key(math.nan, "JP.7203") == (0.0, "JP.7203")


def test_signal_warning_applies_configured_penalty(tmp_path: Path) -> None:
    scorer = Scorer(_config(tmp_path))
    indicators = _indicators()
    clean = StrategyResult(
        code=indicators.code,
        name=indicators.name,
        date=indicators.date,
        strategy_name="momentum",
        signal_type="BUY_CANDIDATE",
    )
    warned = StrategyResult(
        code=indicators.code,
        name=indicators.name,
        date=indicators.date,
        strategy_name="momentum",
        signal_type="BUY_CANDIDATE",
        risk_warnings=["出来高急増"],
    )

    clean_score = scorer.score(indicators, clean)
    warned_score = scorer.score(indicators, warned)

    assert clean_score.risk_penalty == 0.0
    assert warned_score.risk_penalty == -30.0
    assert warned_score.total == max(0.0, clean_score.total - 30.0)
