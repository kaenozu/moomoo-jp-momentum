"""Closed-day no-op tests for the normal daily-cycle entrypoint."""

from pathlib import Path

import pytest

import run_daily_cycle


CONFIG_PATH = Path(__file__).parent / "fixtures" / "config.test.yaml"


def test_closed_day_returns_auditable_noop_before_external_or_db_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("closed-day cycle must not initialize this service")

    for service_name in (
        "OpenDConnection",
        "DataStore",
        "QuoteService",
        "Screener",
        "VirtualTradeManager",
        "AlertManager",
    ):
        monkeypatch.setattr(run_daily_cycle, service_name, forbidden)

    result = run_daily_cycle.run_cycle(
        "2026-08-11",
        config_path=str(CONFIG_PATH),
        provider="auto",
    )

    assert result["calendar_checked"] is True
    assert result["is_trading_day"] is False
    assert result["cycle_skipped"] is True
    assert result["skip_reason"] == "jpx_market_closed"
    assert result["connection_attempted"] is False
    assert result["database_write_attempted"] is False
    assert result["quote_fetch_attempted"] is False
    assert result["signal_generation_attempted"] is False
    assert result["virtual_orders"] == 0
    assert result["fills"] == 0
    assert result["exits"] == 0
    assert result["alerts"] == 0


def test_trading_day_dry_run_records_calendar_checked() -> None:
    result = run_daily_cycle.run_cycle(
        "2026-08-10",
        dry_run=True,
        config_path=str(CONFIG_PATH),
    )

    assert result["calendar_checked"] is True
    assert result["is_trading_day"] is True
    assert result["cycle_skipped"] is False
    assert result["skip_reason"] == ""
    assert result["connection_attempted"] is False
    assert result["database_write_attempted"] is False
