"""
品質・過熱フィルターの単体テスト。

ファイルパス: tests/test_quality_risk_filter.py
何をするか: QualityRiskFilterの有効・無効と各閾値境界を検証する
なぜ存在するか: momentumへ追加した過熱除外条件の回帰を防ぐため
関連ファイル: src/filters/quality_risk_filter.py, src/strategies/momentum.py
"""

import pytest

from src.filters import QualityRiskFilter
from src.indicators import StockIndicators


def _indicators(
    *,
    daily_return: float | None = 1.0,
    return_5d: float | None = 3.0,
    volume_ratio: float | None = 1.5,
) -> StockIndicators:
    """テストに必要な最小限の指標データを作る。"""
    return StockIndicators(
        code="JP.0001",
        name="テスト銘柄",
        date="2026-07-18",
        close=1000.0,
        open=995.0,
        high=1010.0,
        low=990.0,
        daily_return=daily_return,
        return_5d=return_5d,
        volume_ratio=volume_ratio,
    )


def test_disabled_filter_accepts_overheated_candidate() -> None:
    """フィルター無効時は過熱値でも候補を除外しない。"""
    quality_filter = QualityRiskFilter(enabled=False)

    assert quality_filter.accept(
        _indicators(daily_return=20.0, return_5d=30.0, volume_ratio=8.0)
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("daily_return", 5.0, "daily_return"),
        ("return_5d", 10.0, "return_5d"),
        ("volume_ratio", 3.0, "volume_ratio"),
    ],
)
def test_threshold_is_rejected(
    field: str,
    value: float,
    expected_fragment: str,
) -> None:
    """各指標が上限以上になった境界値を除外する。"""
    values = {
        "daily_return": 1.0,
        "return_5d": 3.0,
        "volume_ratio": 1.5,
    }
    values[field] = value
    quality_filter = QualityRiskFilter(enabled=True)
    indicators = _indicators(**values)

    assert not quality_filter.accept(indicators)
    assert expected_fragment in (quality_filter.rejection_reason(indicators) or "")


def test_values_below_all_thresholds_are_accepted() -> None:
    """すべての指標が上限未満なら候補を受理する。"""
    quality_filter = QualityRiskFilter(enabled=True)

    assert quality_filter.accept(
        _indicators(daily_return=4.99, return_5d=9.99, volume_ratio=2.99)
    )


def test_missing_optional_values_are_not_treated_as_overheat() -> None:
    """欠損値は過熱と断定せず、既存のデータ品質判定へ委ねる。"""
    quality_filter = QualityRiskFilter(enabled=True)

    assert quality_filter.accept(
        _indicators(daily_return=None, return_5d=None, volume_ratio=None)
    )


def test_non_positive_threshold_is_rejected() -> None:
    """設定ミスによる0以下の閾値を初期化時に拒否する。"""
    with pytest.raises(ValueError, match="max_daily_return"):
        QualityRiskFilter(enabled=True, max_daily_return=0.0)
