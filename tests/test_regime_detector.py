"""
相場レジーム検出器の回帰テスト。

ファイルパス: tests/test_regime_detector.py
何をするか: bull/bear/range判定、1日遅延、設定による有効化を検証する
なぜ存在するか: 先読みや後方互換性の回帰を防ぐため
関連ファイル: src/regime_detector.py, src/backtest_runner.py
"""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.config import Config
from src.regime_detector import RegimeDetector


def _config(tmp_path: Path, regime: dict | None = None) -> Config:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backtest": {
                    "idle_cash_allocation": {
                        "regime": regime or {},
                    }
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return Config(str(config_path))


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [10.0, 11.0, 12.0, 8.0, 10.0, 13.0],
        }
    )


def test_detects_unknown_bull_bear_and_range(tmp_path: Path) -> None:
    detector = RegimeDetector(
        _config(
            tmp_path,
            {
                "enabled": True,
                "short_window": 2,
                "long_window": 3,
            },
        )
    )

    regimes = detector.detect(_prices())

    assert [item["regime"] for item in regimes] == [
        "unknown",
        "unknown",
        "bull",
        "bear",
        "range",
        "bull",
    ]
    assert regimes[2]["sma50"] == 11.5
    assert regimes[2]["sma200"] == 11.0


def test_regime_on_uses_previous_day_to_prevent_lookahead(
    tmp_path: Path,
) -> None:
    detector = RegimeDetector(
        _config(
            tmp_path,
            {
                "enabled": True,
                "short_window": 2,
                "long_window": 3,
            },
        )
    )
    regimes = detector.detect(_prices())

    assert detector.regime_on("2026-01-01", regimes) is None
    assert detector.regime_on("2026-01-04", regimes) == "bull"
    assert detector.regime_on("2026-01-05", regimes) == "bear"
    assert detector.regime_on("2026-01-07", regimes) is None


def test_overlay_activation_respects_configuration(
    tmp_path: Path,
) -> None:
    disabled = RegimeDetector(_config(tmp_path))
    assert disabled.is_overlay_active("bull") is False

    enabled = RegimeDetector(
        _config(
            tmp_path,
            {
                "enabled": True,
                "active_regimes": ["bull", "range"],
            },
        )
    )
    assert enabled.is_overlay_active("bull") is True
    assert enabled.is_overlay_active("range") is True
    assert enabled.is_overlay_active("bear") is False
    assert enabled.is_overlay_active(None) is False


def test_detect_rejects_missing_required_columns(tmp_path: Path) -> None:
    detector = RegimeDetector(_config(tmp_path))

    with pytest.raises(ValueError, match="close"):
        detector.detect(pd.DataFrame({"date": ["2026-01-01"]}))


def test_rejects_non_positive_windows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="正の整数"):
        RegimeDetector(
            _config(
                tmp_path,
                {
                    "short_window": 0,
                    "long_window": 200,
                },
            )
        )
