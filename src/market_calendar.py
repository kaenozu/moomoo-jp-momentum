"""Deterministic JPX cash-market trading-day helpers.

The calendar is loaded from ``config/jpx_closed_dates.yaml`` so historical
runs do not depend on a network request. Unsupported years are rejected
explicitly; callers must update the checked-in calendar before processing a
new year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

JST = ZoneInfo("Asia/Tokyo")
MARKET_CLOSE_TIME = time(15, 30)
DEFAULT_CALENDAR_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "jpx_closed_dates.yaml"
)

DateLike = date | datetime | str


class CalendarConfigurationError(RuntimeError):
    """Raised when the checked-in JPX calendar is missing or malformed."""


class UnsupportedCalendarYear(ValueError):
    """Raised when a date falls outside the checked-in calendar range."""


def _coerce_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return _normalize_datetime(value).date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as error:
        raise ValueError(f"日付形式が不正です: {value!r}") from error


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=JST)
    return value.astimezone(JST)


@dataclass(frozen=True)
class JPXTradingCalendar:
    """Checked-in JPX cash-market closure calendar."""

    closed_dates_by_year: dict[int, frozenset[date]]
    source_path: Path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CALENDAR_PATH) -> "JPXTradingCalendar":
        source_path = Path(path)
        if not source_path.exists():
            raise CalendarConfigurationError(
                f"JPX営業日カレンダーが見つかりません: {source_path}"
            )

        try:
            raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise CalendarConfigurationError(
                f"JPX営業日カレンダーを読み込めません: {source_path}: {error}"
            ) from error

        if not isinstance(raw, dict):
            raise CalendarConfigurationError(
                "JPX営業日カレンダーのトップレベルはmappingである必要があります"
            )

        supported_raw = raw.get("supported_years")
        closures_raw = raw.get("closed_dates")
        if not isinstance(supported_raw, list) or not supported_raw:
            raise CalendarConfigurationError("supported_yearsが空または不正です")
        if not isinstance(closures_raw, dict):
            raise CalendarConfigurationError("closed_datesが不正です")

        try:
            supported_years = {int(year) for year in supported_raw}
        except (TypeError, ValueError) as error:
            raise CalendarConfigurationError(
                "supported_yearsには西暦の整数を指定してください"
            ) from error

        closed_dates_by_year: dict[int, frozenset[date]] = {}
        for year in sorted(supported_years):
            values = closures_raw.get(str(year), closures_raw.get(year))
            if not isinstance(values, list):
                raise CalendarConfigurationError(
                    f"closed_dates.{year}が存在しないかlistではありません"
                )

            parsed: set[date] = set()
            for value in values:
                if not isinstance(value, str):
                    raise CalendarConfigurationError(
                        f"closed_dates.{year}の日付は文字列で指定してください: {value!r}"
                    )
                try:
                    closed_date = datetime.strptime(value, "%Y-%m-%d").date()
                except ValueError as error:
                    raise CalendarConfigurationError(
                        f"closed_dates.{year}の日付形式が不正です: {value}"
                    ) from error
                if closed_date.year != year:
                    raise CalendarConfigurationError(
                        f"closed_dates.{year}に別年の日付があります: {value}"
                    )
                parsed.add(closed_date)
            closed_dates_by_year[year] = frozenset(parsed)

        extra_years: set[int] = set()
        for key in closures_raw:
            try:
                extra_years.add(int(key))
            except (TypeError, ValueError) as error:
                raise CalendarConfigurationError(
                    f"closed_datesの年キーが不正です: {key!r}"
                ) from error
        if extra_years != supported_years:
            raise CalendarConfigurationError(
                "supported_yearsとclosed_datesの年が一致しません: "
                f"supported={sorted(supported_years)}, closures={sorted(extra_years)}"
            )

        return cls(closed_dates_by_year, source_path)

    @property
    def supported_years(self) -> tuple[int, ...]:
        return tuple(sorted(self.closed_dates_by_year))

    def _require_year(self, year: int) -> None:
        if year not in self.closed_dates_by_year:
            supported = self.supported_years
            raise UnsupportedCalendarYear(
                "JPX営業日カレンダーの対応年外です: "
                f"year={year}, supported={supported[0]}-{supported[-1]}"
            )

    def is_trading_day(self, value: DateLike) -> bool:
        target = _coerce_date(value)
        self._require_year(target.year)
        return (
            target.weekday() < 5
            and target not in self.closed_dates_by_year[target.year]
        )

    def previous_trading_day(self, value: DateLike) -> date:
        candidate = _coerce_date(value) - timedelta(days=1)
        while True:
            self._require_year(candidate.year)
            if self.is_trading_day(candidate):
                return candidate
            candidate -= timedelta(days=1)

    def expected_trading_day_for_date(self, value: DateLike) -> date:
        target = _coerce_date(value)
        if self.is_trading_day(target):
            return target
        return self.previous_trading_day(target)

    def latest_expected_trading_day(
        self,
        reference_datetime: datetime,
        market_close_time: time = MARKET_CLOSE_TIME,
    ) -> date:
        reference = _normalize_datetime(reference_datetime)
        target = reference.date()
        if not self.is_trading_day(target):
            return self.previous_trading_day(target)
        if reference.time().replace(tzinfo=None) >= market_close_time:
            return target
        return self.previous_trading_day(target)

    def count_missing_trading_days(
        self,
        latest_date: DateLike,
        reference_date: DateLike,
    ) -> int:
        latest = _coerce_date(latest_date)
        if not self.is_trading_day(latest):
            raise ValueError(
                "最新データ日がJPX取引日ではありません: "
                f"{latest.isoformat()}"
            )
        if isinstance(reference_date, datetime):
            expected = self.latest_expected_trading_day(reference_date)
        else:
            expected = self.expected_trading_day_for_date(reference_date)

        if latest > expected:
            raise ValueError(
                "最新データ日が期待取引日より未来です: "
                f"latest={latest.isoformat()}, expected={expected.isoformat()}"
            )

        missing = 0
        candidate = latest + timedelta(days=1)
        while candidate <= expected:
            if self.is_trading_day(candidate):
                missing += 1
            candidate += timedelta(days=1)
        return missing


@lru_cache(maxsize=4)
def _load_calendar(path: str) -> JPXTradingCalendar:
    return JPXTradingCalendar.load(path)


def get_jpx_calendar(path: str | Path | None = None) -> JPXTradingCalendar:
    calendar_path = Path(path) if path is not None else DEFAULT_CALENDAR_PATH
    return _load_calendar(str(calendar_path.resolve()))


def is_trading_day(value: DateLike) -> bool:
    return get_jpx_calendar().is_trading_day(value)


def previous_trading_day(value: DateLike) -> date:
    return get_jpx_calendar().previous_trading_day(value)


def latest_expected_trading_day(reference_datetime: datetime) -> date:
    return get_jpx_calendar().latest_expected_trading_day(reference_datetime)


def expected_trading_day_for_date(value: DateLike) -> date:
    return get_jpx_calendar().expected_trading_day_for_date(value)


def count_missing_trading_days(
    latest_date: DateLike,
    reference_date: DateLike,
) -> int:
    return get_jpx_calendar().count_missing_trading_days(
        latest_date,
        reference_date,
    )
