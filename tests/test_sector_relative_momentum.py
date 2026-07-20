from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.indicators import StockIndicators, add_cross_sectional_stats
from src.strategies.sector_relative_momentum import SectorRelativeMomentumStrategy


def _create_database(path: Path, sectors: dict[str, str]) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE symbols (
                code TEXT PRIMARY KEY,
                sector TEXT,
                enabled INTEGER,
                role TEXT,
                tradable INTEGER
            );
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT
            );
            CREATE TABLE backtest_positions (
                run_id INTEGER,
                strategy_name TEXT,
                code TEXT,
                quantity INTEGER
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO symbols (code, sector, enabled, role, tradable)
            VALUES (?, ?, 1, 'trade_candidate', 1)
            """,
            sectors.items(),
        )


class _TestConfig:
    def __init__(self, values: dict) -> None:
        self._config = values

    def get(self, key_path: str, default=None):
        value = self._config
        for key in key_path.split("."):
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    @property
    def database_path(self) -> str:
        return str(self.get("database.path"))


def _config(path: Path, **overrides) -> _TestConfig:
    strategy = {
        "enabled": False,
        "raw_weight": 0.5,
        "relative_weight": 0.5,
        "max_sector_active_weight": 10.0,
        "top_n_per_sector": 2,
        "min_sector_size": 5,
    }
    strategy.update(overrides)
    return _TestConfig(
        {
            "database": {"path": str(path)},
            "backtest": {"max_positions": 10},
            "strategies": {"sector_relative_momentum": strategy},
            "screening": {
                "min_turnover": 1_000_000_000,
                "min_volume_ratio": 1.2,
                "max_distance_from_high_20d": 5.0,
                "min_history_days": 25,
            },
            "signals": {"volume": {"use_percentile": True, "percentile_threshold": 60}},
        }
    )


def _indicator(code: str, return_20d: float) -> StockIndicators:
    return StockIndicators(
        code=code,
        name=code,
        date="2026-06-30",
        close=110.0,
        open=109.0,
        high=111.0,
        low=108.0,
        ma5=105.0,
        ma25=100.0,
        volume=2_000_000,
        volume_ratio=2.0,
        turnover=2_000_000_000.0,
        high_20d=111.0,
        high_20d_distance=-0.9,
        return_5d=2.0,
        return_20d=return_20d,
        history_days=60,
        volume_ratio_percentile=80.0,
    )


def test_selects_top_n_within_each_sector(tmp_path: Path) -> None:
    sectors = {
        **{f"T{i}": "Tech" for i in range(5)},
        **{f"F{i}": "Finance" for i in range(5)},
    }
    db_path = tmp_path / "test.db"
    _create_database(db_path, sectors)
    strategy = SectorRelativeMomentumStrategy(_config(db_path))
    indicators = [
        *[_indicator(f"T{i}", value) for i, value in enumerate([10, 8, 6, 4, 2])],
        *[_indicator(f"F{i}", value) for i, value in enumerate([5, 4, 3, 2, 1])],
    ]

    add_cross_sectional_stats(indicators)

    selected = {
        indicator.code
        for indicator in indicators
        if strategy.evaluate(indicator).signal_type == "BUY_CANDIDATE"
    }
    assert selected == {"T0", "T1", "F0", "F1"}


def test_skips_sector_smaller_than_minimum(tmp_path: Path) -> None:
    sectors = {
        **{f"L{i}": "Large" for i in range(5)},
        **{f"S{i}": "Small" for i in range(4)},
    }
    db_path = tmp_path / "test.db"
    _create_database(db_path, sectors)
    strategy = SectorRelativeMomentumStrategy(_config(db_path))
    indicators = [_indicator(code, 10 - index) for index, code in enumerate(sectors)]

    strategy.prepare_cross_section(indicators)

    selected = {
        indicator.code
        for indicator in indicators
        if strategy.evaluate(indicator).signal_type == "BUY_CANDIDATE"
    }
    assert selected
    assert all(code.startswith("L") for code in selected)


def test_sector_limit_counts_existing_positions(tmp_path: Path) -> None:
    sectors = {
        **{f"A{i}": "A" for i in range(5)},
        **{f"B{i}": "B" for i in range(15)},
    }
    db_path = tmp_path / "test.db"
    _create_database(db_path, sectors)
    with sqlite3.connect(db_path) as connection:
        run_id = connection.execute(
            "INSERT INTO backtest_runs (strategy_name) VALUES (?)",
            ("sector_relative_momentum",),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO backtest_positions (run_id, strategy_name, code, quantity)
            VALUES (?, 'sector_relative_momentum', 'A0', 1)
            """,
            (run_id,),
        )

    config = _config(db_path, max_sector_active_weight=1.0, top_n_per_sector=5)
    config._config["backtest"]["max_positions"] = 4
    strategy = SectorRelativeMomentumStrategy(config)
    indicators = [
        *[_indicator(f"A{i}", 20 - i) for i in range(1, 5)],
        *[_indicator(f"B{i}", 10 - i / 10) for i in range(15)],
    ]

    strategy.prepare_cross_section(indicators)

    selected = {
        indicator.code
        for indicator in indicators
        if strategy.evaluate(indicator).signal_type == "BUY_CANDIDATE"
    }
    assert len(selected) == 3
    assert all(code.startswith("B") for code in selected)


def test_zero_variance_uses_deterministic_code_tiebreak(tmp_path: Path) -> None:
    sectors = {f"A{i}": "A" for i in range(5)}
    db_path = tmp_path / "test.db"
    _create_database(db_path, sectors)
    strategy = SectorRelativeMomentumStrategy(_config(db_path))
    indicators = [_indicator(code, 5.0) for code in reversed(list(sectors))]

    strategy.prepare_cross_section(indicators)

    selected = {
        indicator.code
        for indicator in indicators
        if strategy.evaluate(indicator).signal_type == "BUY_CANDIDATE"
    }
    assert selected == {"A0", "A1"}


def test_rejects_zero_total_weight(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    _create_database(db_path, {f"A{i}": "A" for i in range(5)})

    with pytest.raises(ValueError, match="合計"):
        SectorRelativeMomentumStrategy(
            _config(db_path, raw_weight=0.0, relative_weight=0.0)
        )
