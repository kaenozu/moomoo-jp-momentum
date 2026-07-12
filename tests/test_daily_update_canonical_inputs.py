"""Regression coverage for DB-backed standalone daily-update inputs."""

from pathlib import Path
from typing import cast

import pandas as pd

from daily_update import fetch_and_save_daily_klines
from src.config import Config
from src.data_store import DataStore
from src.models import DailyBar
from src.quote_service import QuoteService


class _FakeQuoteService:
    def __init__(self, responses: dict[str, pd.DataFrame]):
        self.responses = responses

    def batch_fetch_daily_klines(
        self,
        codes: list[str],
        mode: str,
        num: int,
        start: str | None,
        batch_size: int,
        retry_count: int,
    ) -> dict[str, pd.DataFrame]:
        del mode, num, start, batch_size, retry_count
        return {
            code: self.responses[code]
            for code in codes
            if code in self.responses
        }


def _store(tmp_path: Path) -> DataStore:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "daily_update.db")}
    return DataStore(config)


def _save_bar(store: DataStore, code: str, date: str, close: float) -> None:
    store.save_daily_bar(
        DailyBar(
            code=code,
            date=date,
            open=close - 1,
            high=close + 1,
            low=close - 2,
            close=close,
            volume=1000,
            turnover=close * 1000,
        )
    )


def test_reloads_all_codes_from_database_after_partial_api_success(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _save_bar(store, "JP.0001", "2026-07-09", 100.0)
    _save_bar(store, "JP.0002", "2026-07-09", 200.0)
    api_row = pd.DataFrame(
        [
            {
                "time_key": "2026-07-10",
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "volume": 1100,
                "turnover": 112200.0,
            }
        ]
    )
    quote_service = cast(
        QuoteService,
        _FakeQuoteService({"JP.0001": api_row}),
    )

    inputs = fetch_and_save_daily_klines(
        quote_service,
        store,
        ["JP.0001", "JP.0002"],
        num_days=120,
        force=True,
        mode="history",
        batch_size=2,
    )

    assert list(inputs) == ["JP.0001", "JP.0002"]
    assert list(inputs["JP.0001"]["date"]) == ["2026-07-10", "2026-07-09"]
    assert list(inputs["JP.0002"]["date"]) == ["2026-07-09"]


def test_stops_when_required_code_cannot_be_reloaded_from_database(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _save_bar(store, "JP.0001", "2026-07-09", 100.0)
    quote_service = cast(QuoteService, _FakeQuoteService({}))

    inputs = fetch_and_save_daily_klines(
        quote_service,
        store,
        ["JP.0001", "JP.0002"],
        num_days=120,
        force=True,
        mode="history",
        batch_size=2,
    )

    assert inputs == {}
