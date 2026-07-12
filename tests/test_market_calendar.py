"""Regression coverage for deterministic JPX trading-day behavior."""

from datetime import date, datetime
from pathlib import Path

import pytest

from daily_update import should_skip_fetch
from src.config import Config
from src.data_store import DataStore
from src.market_calendar import (
    JST,
    UnsupportedCalendarYear,
    count_missing_trading_days,
    get_jpx_calendar,
    is_trading_day,
    latest_expected_trading_day,
    previous_trading_day,
)
from src.models import DailyBar


def _store(tmp_path: Path) -> DataStore:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "calendar.db")}
    return DataStore(config)


def _save_bar(store: DataStore, code: str, bar_date: str) -> None:
    store.save_daily_bar(
        DailyBar(
            code=code,
            date=bar_date,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
            turnover=100000,
        )
    )


def test_regular_weekday_is_a_trading_day() -> None:
    assert is_trading_day("2026-07-21") is True


def test_weekend_and_published_holidays_are_closed() -> None:
    assert is_trading_day("2026-07-18") is False
    assert is_trading_day("2026-07-20") is False
    assert is_trading_day("2026-09-22") is False
    assert is_trading_day("2026-12-31") is False


def test_previous_trading_day_crosses_golden_week() -> None:
    assert previous_trading_day("2026-05-07") == date(2026, 5, 1)


def test_previous_trading_day_crosses_repository_start_year() -> None:
    assert previous_trading_day("2025-01-01") == date(2024, 12, 30)


def test_latest_expected_day_changes_at_market_close() -> None:
    before_close = datetime(2026, 7, 13, 10, 0, tzinfo=JST)
    after_close = datetime(2026, 7, 13, 15, 30, tzinfo=JST)

    assert latest_expected_trading_day(before_close) == date(2026, 7, 10)
    assert latest_expected_trading_day(after_close) == date(2026, 7, 13)


def test_latest_expected_day_on_holiday_is_previous_session() -> None:
    holiday = datetime(2026, 7, 20, 18, 0, tzinfo=JST)

    assert latest_expected_trading_day(holiday) == date(2026, 7, 17)


def test_missing_days_count_uses_sessions_not_calendar_days() -> None:
    assert count_missing_trading_days("2026-07-17", "2026-07-20") == 0
    assert count_missing_trading_days("2026-07-17", "2026-07-21") == 1
    assert count_missing_trading_days("2026-07-16", "2026-07-21") == 2


def test_unsupported_year_is_rejected_explicitly() -> None:
    calendar = get_jpx_calendar()

    with pytest.raises(UnsupportedCalendarYear, match="2028"):
        calendar.is_trading_day("2028-01-04")


def test_fetch_is_skipped_on_holiday_when_previous_session_exists(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _save_bar(store, "JP.0001", "2026-07-17")

    assert should_skip_fetch(
        store,
        "JP.0001",
        "2026-07-20",
        reference_datetime=datetime(2026, 7, 20, 18, 0, tzinfo=JST),
    ) is True


def test_fetch_waits_until_close_on_a_trading_day(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _save_bar(store, "JP.0001", "2026-07-17")

    assert should_skip_fetch(
        store,
        "JP.0001",
        "2026-07-21",
        reference_datetime=datetime(2026, 7, 21, 10, 0, tzinfo=JST),
    ) is True
    assert should_skip_fetch(
        store,
        "JP.0001",
        "2026-07-21",
        reference_datetime=datetime(2026, 7, 21, 16, 0, tzinfo=JST),
    ) is False
