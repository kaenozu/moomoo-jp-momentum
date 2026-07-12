"""Daily-cycle market closure and operational notification regressions."""

from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import run_daily_cycle
from src.market_calendar import JST, UnsupportedCalendarYear


def test_jpx_holiday_skips_before_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class _UnexpectedConnection:
        def __init__(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("休場日はOpenDへ接続してはいけません")

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", _UnexpectedConnection)
    results = run_daily_cycle.run_cycle(
        "2026-07-20",
        config_path="tests/fixtures/config.test.yaml",
    )

    assert results["calendar_checked"] is True
    assert results["is_trading_day"] is False
    assert results["cycle_skipped"] is True
    assert results["skip_reason"] == "jpx_market_closed"
    assert results["connection_attempted"] is False
    assert results["database_write_attempted"] is False


def test_weekend_dry_run_is_a_clean_skip() -> None:
    results = run_daily_cycle.run_cycle(
        "2026-07-12",
        dry_run=True,
        config_path="tests/fixtures/config.test.yaml",
    )
    assert results["cycle_skipped"] is True
    assert results["is_trading_day"] is False


def test_trading_day_dry_run_reports_calendar_state() -> None:
    results = run_daily_cycle.run_cycle(
        "2026-07-13",
        dry_run=True,
        config_path="tests/fixtures/config.test.yaml",
    )
    assert results["calendar_checked"] is True
    assert results["is_trading_day"] is True
    assert results["cycle_skipped"] is False
    assert results["skip_reason"] == ""


def test_unsupported_calendar_year_is_not_silently_skipped() -> None:
    with pytest.raises(UnsupportedCalendarYear):
        run_daily_cycle.run_cycle(
            "2028-01-04",
            dry_run=True,
            config_path="tests/fixtures/config.test.yaml",
        )


def test_opend_failure_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Connection:
        def __init__(self, _config: Any):
            pass

        def connect(self) -> SimpleNamespace:
            return SimpleNamespace(connected=False, message="offline", quote_context=None)

    monkeypatch.setattr(run_daily_cycle, "OpenDConnection", _Connection)
    with pytest.raises(run_daily_cycle.DailyCycleStoppedError) as caught:
        run_daily_cycle.run_cycle(
            "2026-07-13",
            config_path="tests/fixtures/config.test.yaml",
        )
    assert caught.value.event_type == "opend_connection_failure"


def test_main_notifies_classified_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fail_cycle(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise run_daily_cycle.DailyCycleStoppedError(
            "integrity failed",
            event_type="integrity_failure",
        )

    def notify(
        _config_path: str,
        event_type: str,
        target_date: str,
        message: str,
        _context: dict[str, object] | None = None,
    ) -> bool:
        calls.append((event_type, target_date, message))
        return True

    monkeypatch.setattr(run_daily_cycle, "run_cycle", fail_cycle)
    monkeypatch.setattr(run_daily_cycle, "_notify_operational_failure", notify)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_daily_cycle.py", "--date", "2026-07-13", "--config", "x.yaml"],
    )

    assert run_daily_cycle.main() == 1
    assert calls == [("integrity_failure", "2026-07-13", "integrity failed")]


def test_default_target_date_uses_jst(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == JST
            return cls(2026, 7, 13, 0, 5, tzinfo=JST)

    monkeypatch.setattr(run_daily_cycle, "datetime", _FixedDateTime)
    assert run_daily_cycle._default_target_date() == "2026-07-13"
