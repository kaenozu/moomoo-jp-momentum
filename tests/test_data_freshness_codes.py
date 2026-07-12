"""Regression coverage for per-symbol daily-bar freshness checks."""

import sqlite3
from pathlib import Path

import pytest

from run_daily_cycle import _assert_cycle_data_freshness
from src.config import Config
from src.data_freshness import DataFreshnessGuard
from src.data_store import DataStore


def _config(tmp_path: Path) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "freshness.db")}
    DataStore(config)
    return config


def _insert_bar(config: Config, code: str, date: str) -> None:
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            """
            INSERT INTO daily_bars
            (code, date, open, high, low, close, volume, turnover)
            VALUES (?, ?, 100, 101, 99, 100, 1000, 100000)
            """,
            (code, date),
        )


def test_global_max_cannot_hide_stale_required_symbol(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.FRESH", "2026-07-10")
    _insert_bar(config, "JP.STALE", "2026-05-01")
    guard = DataFreshnessGuard(config)

    global_status = guard.check_freshness(reference_date="2026-07-10")
    statuses = guard.check_required_codes_freshness(
        ["JP.FRESH", "JP.STALE"],
        reference_date="2026-07-10",
    )

    assert global_status.level == "ok"
    assert statuses["JP.FRESH"].level == "ok"
    assert statuses["JP.STALE"].level == "error"
    with pytest.raises(SystemError, match="JP.STALE"):
        guard.assert_required_codes_fresh_or_stop(
            ["JP.FRESH", "JP.STALE"],
            reference_date="2026-07-10",
        )


def test_empty_database_never_passes_required_symbol_check(tmp_path: Path) -> None:
    config = _config(tmp_path)
    guard = DataFreshnessGuard(config)

    status = guard.check_required_codes_freshness(
        ["JP.1306"],
        reference_date="2026-07-10",
    )["JP.1306"]

    assert status.level == "error"
    assert status.latest_date is None
    assert status.days_stale == 9999
    with pytest.raises(SystemError, match="JP.1306"):
        guard.assert_required_codes_fresh_or_stop(
            ["JP.1306"],
            reference_date="2026-07-10",
        )


def test_future_bar_is_not_used_for_historical_reference_date(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-07-11")
    guard = DataFreshnessGuard(config)

    status = guard.check_freshness(
        code="JP.1306",
        reference_date="2026-07-10",
    )

    assert status.level == "error"
    assert status.latest_date is None
    assert "基準日 2026-07-10 以前" in status.message


def test_warning_range_is_reported_but_does_not_stop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-07-01")
    guard = DataFreshnessGuard(config)

    statuses = guard.assert_required_codes_fresh_or_stop(
        ["JP.1306"],
        reference_date="2026-07-10",
        max_stale_days=5,
    )

    assert statuses["JP.1306"].level == "warning"
    assert statuses["JP.1306"].days_stale == 7


def test_empty_required_code_list_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    guard = DataFreshnessGuard(config)

    with pytest.raises(SystemError, match="対象銘柄が0件"):
        guard.assert_required_codes_fresh_or_stop(
            [],
            reference_date="2026-07-10",
        )


def test_daily_cycle_helper_checks_every_enabled_code(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-07-10")

    with pytest.raises(SystemError, match="JP.7203"):
        _assert_cycle_data_freshness(
            config,
            ["JP.1306", "JP.7203"],
            "2026-07-10",
        )


def test_holiday_gap_has_zero_missing_trading_days(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-05-01")
    guard = DataFreshnessGuard(config)

    status = guard.check_freshness(
        code="JP.1306",
        reference_date="2026-05-06",
        max_stale_days=0,
    )

    assert status.level == "ok"
    assert status.days_stale == 0
    assert "期待取引日2026-05-01" in status.message


def test_missing_day_count_starts_after_golden_week(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _insert_bar(config, "JP.1306", "2026-05-01")
    guard = DataFreshnessGuard(config)

    status = guard.check_freshness(
        code="JP.1306",
        reference_date="2026-05-07",
        max_stale_days=0,
    )

    assert status.level == "warning"
    assert status.days_stale == 1
