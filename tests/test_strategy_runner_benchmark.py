"""Regression coverage for StrategyRunner benchmark handling."""

import sqlite3
from pathlib import Path
from typing import Optional

import pytest

from src.config import Config
from src.data_store import DataStore
from src.indicators import StockIndicators
from src.strategies import BaseStrategy, StrategyRegistry, StrategyResult
from src.strategy_runner import StrategyRunner


def _config(tmp_path: Path) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "strategy.db")}
    signals = config._config.setdefault("signals", {})
    relative_strength = signals.setdefault("relative_strength", {})
    relative_strength["benchmark_code"] = "JP.1306"
    DataStore(config)
    return config


def _indicator(code: str, return_5d: float) -> StockIndicators:
    return StockIndicators(
        code=code,
        name=code,
        date="2026-01-08",
        close=1000.0,
        open=995.0,
        high=1010.0,
        low=990.0,
        ma5=990.0,
        ma25=980.0,
        volume=100_000,
        volume_ma20=80_000.0,
        volume_ratio=1.25,
        turnover=200_000_000.0,
        high_20d=1010.0,
        high_20d_distance=-1.0,
        daily_return=1.0,
        return_5d=return_5d,
        history_days=30,
    )


class _CaptureStrategy(BaseStrategy):
    seen_benchmarks: list[dict[str, Optional[float]]] = []

    def __init__(self, config: Config):
        super().__init__(config)
        self.strategy_name = "capture_test"

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        self.seen_benchmarks.append(dict(benchmark_returns or {}))
        return StrategyResult(
            code=indicators.code,
            name=indicators.name,
            date=indicators.date,
            strategy_name=self.strategy_name,
            signal_type="WATCH",
            score=1.0,
            reason="captured",
            price_at_signal=indicators.close,
        )


def test_benchmark_returns_use_configured_code_and_target_date(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with sqlite3.connect(config.database_path) as connection:
        connection.executemany(
            "INSERT INTO daily_bars (code, date, close) VALUES (?, ?, ?)",
            [
                ("JP.1306", "2026-01-01", 100.0),
                ("JP.1306", "2026-01-02", 102.0),
                ("JP.1306", "2026-01-05", 104.0),
                ("JP.1306", "2026-01-06", 106.0),
                ("JP.1306", "2026-01-07", 108.0),
                ("JP.1306", "2026-01-08", 110.0),
                ("JP.1306", "2026-01-09", 1000.0),
                ("JP.2559", "2026-01-01", 100.0),
                ("JP.2559", "2026-01-02", 120.0),
                ("JP.2559", "2026-01-05", 140.0),
                ("JP.2559", "2026-01-06", 160.0),
                ("JP.2559", "2026-01-07", 180.0),
                ("JP.2559", "2026-01-08", 200.0),
            ],
        )

    returns = StrategyRunner(config)._benchmark_returns("2026-01-08")

    assert returns["return_5d"] == pytest.approx(10.0)
    assert set(returns) == {"return_5d", "return_20d", "return_60d"}


def test_run_all_does_not_infer_benchmark_from_first_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    runner = StrategyRunner(config)
    expected: dict[str, Optional[float]] = {
        "return_5d": 2.5,
        "return_20d": 4.0,
        "return_60d": 8.0,
    }
    requested_dates: list[str] = []

    def fake_benchmark_returns(
        target_date: str,
    ) -> dict[str, Optional[float]]:
        requested_dates.append(target_date)
        return expected

    monkeypatch.setattr(runner, "_benchmark_returns", fake_benchmark_returns)
    monkeypatch.setitem(
        StrategyRegistry._strategies,
        "capture_test",
        _CaptureStrategy,
    )
    _CaptureStrategy.seen_benchmarks.clear()

    saved = runner.run_all(
        [
            _indicator("JP.0001", return_5d=99.0),
            _indicator("JP.0002", return_5d=-99.0),
        ],
        "2026-01-08",
        ["capture_test"],
    )

    assert requested_dates == ["2026-01-08"]
    assert saved == {"capture_test": 2}
    assert _CaptureStrategy.seen_benchmarks == [expected, expected]


def test_empty_run_removes_stale_signals_for_target_date(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = StrategyRunner(config)

    with sqlite3.connect(config.database_path) as connection:
        connection.executemany(
            """
            INSERT INTO signals
            (code, date, signal_type, strategy_name)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("JP.0001", "2026-01-08", "WATCH", "momentum"),
                ("JP.0002", "2026-01-07", "WATCH", "momentum"),
            ],
        )

    assert runner._save_signals([], "momentum", "2026-01-08") == 0

    with sqlite3.connect(config.database_path) as connection:
        dates = connection.execute(
            "SELECT date FROM signals WHERE strategy_name = ? ORDER BY date",
            ("momentum",),
        ).fetchall()

    assert dates == [("2026-01-07",)]


def test_benchmark_returns_require_full_period_history(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with sqlite3.connect(config.database_path) as connection:
        connection.executemany(
            "INSERT INTO daily_bars (code, date, close) VALUES (?, ?, ?)",
            [
                ("JP.1306", "2026-01-01", 100.0),
                ("JP.1306", "2026-01-02", 102.0),
                ("JP.1306", "2026-01-05", 104.0),
                ("JP.1306", "2026-01-06", 106.0),
                ("JP.1306", "2026-01-07", 108.0),
                ("JP.1306", "2026-01-08", 110.0),
            ],
        )

    returns = StrategyRunner(config)._benchmark_returns("2026-01-08")

    assert returns["return_5d"] == pytest.approx(10.0)
    assert returns["return_20d"] is None
    assert returns["return_60d"] is None


def test_momentum_uses_20d_and_60d_benchmark_returns(tmp_path: Path) -> None:
    from src.strategies.momentum import MomentumStrategy

    config = _config(tmp_path)
    indicators = _indicator("JP.0001", return_5d=8.0)
    indicators.return_20d = 15.0
    indicators.return_60d = 30.0

    result = MomentumStrategy(config).evaluate(
        indicators,
        {
            "return_5d": 2.0,
            "return_20d": 5.0,
            "return_60d": 12.0,
        },
    )

    assert result.return_5d_vs_benchmark == pytest.approx(6.0)
    assert result.return_20d_vs_benchmark == pytest.approx(10.0)
    assert result.return_60d_vs_benchmark == pytest.approx(18.0)


def test_runner_uses_periods_from_current_relative_strength_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config._config["signals"]["relative_strength"]["periods"] = [5, 10]
    runner = StrategyRunner(config)
    calls: list[tuple[str, str, int]] = []

    def fake_return(code: str, target_date: str, period: int) -> float:
        calls.append((code, target_date, period))
        return float(period)

    monkeypatch.setattr(
        runner.relative_strength,
        "calc_benchmark_return",
        fake_return,
    )

    returns = runner._benchmark_returns("2026-01-08")

    assert returns == {"return_5d": 5.0, "return_10d": 10.0}
    assert calls == [
        ("JP.1306", "2026-01-08", 5),
        ("JP.1306", "2026-01-08", 10),
    ]
