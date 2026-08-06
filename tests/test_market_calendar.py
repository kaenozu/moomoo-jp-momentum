"""JPX market calendar and closed-day contract tests."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.market_calendar import (
    CalendarConfigurationError,
    UnsupportedCalendarYear,
    get_jpx_calendar,
    is_trading_day,
    previous_trading_day,
)


def test_regular_weekday_is_a_trading_day() -> None:
    assert is_trading_day("2026-07-21") is True


def test_weekend_jpx_holiday_and_year_end_are_closed() -> None:
    assert is_trading_day("2026-07-18") is False
    assert is_trading_day("2026-07-20") is False
    assert is_trading_day("2026-12-31") is False
    assert is_trading_day("2027-01-01") is False


def test_previous_trading_day_crosses_new_year_closure() -> None:
    assert previous_trading_day("2025-01-01") == date(2024, 12, 30)


def test_aware_datetime_is_normalized_to_jst_at_utc_boundary() -> None:
    just_before_jst_midnight = datetime(
        2026, 7, 20, 14, 59, tzinfo=timezone.utc
    )
    just_after_jst_midnight = datetime(
        2026, 7, 20, 15, 0, tzinfo=timezone.utc
    )

    assert is_trading_day(just_before_jst_midnight) is False
    assert is_trading_day(just_after_jst_midnight) is True


def test_naive_datetime_is_interpreted_as_jst() -> None:
    assert is_trading_day(datetime(2026, 7, 20, 23, 59)) is False
    assert is_trading_day(datetime(2026, 7, 21, 0, 1)) is True


def test_unsupported_year_is_a_failure() -> None:
    with pytest.raises(UnsupportedCalendarYear, match="2028"):
        is_trading_day("2028-01-04")


def test_malformed_calendar_is_a_configuration_failure(tmp_path: Path) -> None:
    calendar_path = tmp_path / "jpx_closed_dates.yaml"
    calendar_path.write_text(
        "supported_years:\n  - 2026\nclosed_dates: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(CalendarConfigurationError):
        get_jpx_calendar(calendar_path)
