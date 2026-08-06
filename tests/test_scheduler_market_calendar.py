"""Scheduler wiring tests for JPX closed-day no-op behavior."""

import logging

import pytest

import scheduler
from src.market_calendar import UnsupportedCalendarYear


def test_daily_update_job_logs_closed_day_and_does_not_start_child_process(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(scheduler, "_current_jst_date", lambda: "2026-08-11")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("closed-day scheduler job must not start a child")

    monkeypatch.setattr(scheduler, "_run_script", forbidden)

    with caplog.at_level(logging.INFO):
        result = scheduler.job_daily_update()

    assert result is not None
    assert result["calendar_checked"] is True
    assert result["is_trading_day"] is False
    assert result["cycle_skipped"] is True
    assert result["skip_reason"] == "jpx_market_closed"
    assert "calendar_checked = true" in caplog.text
    assert "is_trading_day = false" in caplog.text
    assert "cycle_skipped = true" in caplog.text
    assert "skip_reason = jpx_market_closed" in caplog.text


def test_daily_update_job_runs_on_the_next_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "_current_jst_date", lambda: "2026-08-12")
    calls: list[tuple[list[str], int, str]] = []
    monkeypatch.setattr(
        scheduler,
        "_run_script",
        lambda args, timeout, name: calls.append((args, timeout, name)),
    )

    result = scheduler.job_daily_update()

    assert result is None
    assert calls == [(["daily_update.py", "--force"], 600, "日次更新")]


def test_scheduler_does_not_convert_calendar_errors_to_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "_current_jst_date", lambda: "2028-01-04")

    with pytest.raises(UnsupportedCalendarYear):
        scheduler.job_daily_update()
