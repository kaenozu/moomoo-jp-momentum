"""JPX calendar protection at the daily_update process boundary."""

import sys
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import daily_update
from src.config import Config
from src.data_store import DataStore
from src.models import DailyBar
from src.market_calendar import JST


CONFIG_PATH = Path(__file__).parent / "fixtures" / "config.test.yaml"


def _store(tmp_path: Path) -> DataStore:
    config = Config(str(CONFIG_PATH))
    config._config["database"] = {"path": str(tmp_path / "calendar.db")}
    store = DataStore(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "INSERT INTO symbols (code, name) VALUES (?, ?)",
            ("JP.0001", "Test symbol"),
        )
    return store


def _save_bar(store: DataStore, bar_date: str) -> None:
    store.save_daily_bar(
        DailyBar(
            code="JP.0001",
            date=bar_date,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
            turnover=100000,
        )
    )


def test_fetch_skip_uses_jpx_closure_and_resumes_next_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _save_bar(store, "2026-08-10")

    assert daily_update.should_skip_fetch(
        store,
        "JP.0001",
        "2026-08-11",
        reference_datetime=datetime(2026, 8, 11, 18, 0, tzinfo=JST),
    ) is True
    assert daily_update.should_skip_fetch(
        store,
        "JP.0001",
        "2026-08-12",
        reference_datetime=datetime(2026, 8, 12, 18, 0, tzinfo=JST),
    ) is False


def test_daily_update_closed_day_returns_before_data_store_or_opend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return cls(2026, 8, 11, 18, 0, tzinfo=tz)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("closed-day daily_update must not initialize services")

    monkeypatch.setattr(daily_update, "datetime", FrozenDateTime)
    monkeypatch.setattr(daily_update, "DataStore", forbidden)
    monkeypatch.setattr(daily_update, "OpenDConnection", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        ["daily_update.py", "--config", str(CONFIG_PATH)],
    )

    assert daily_update.main() == 0
