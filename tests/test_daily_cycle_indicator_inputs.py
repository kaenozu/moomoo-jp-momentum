"""Regression coverage for canonical DB-backed daily-cycle indicator inputs."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from run_daily_cycle import DEFAULT_HISTORY_LIMIT, _load_indicator_inputs
from src.config import Config
from src.data_store import DataStore
from src.indicators import calculate_indicators_batch
from src.models import DailyBar


def _store(tmp_path: Path) -> DataStore:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "cycle.db")}
    return DataStore(config)


def _insert_symbol(store: DataStore, code: str) -> None:
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            INSERT INTO symbols
            (code, name, market, type, role, tradable, enabled)
            VALUES (?, ?, 'JP', 'stock', 'trade_candidate', 1, 1)
            """,
            (code, code),
        )


def _insert_bars(
    store: DataStore,
    code: str,
    dates: list[str],
    base_close: float,
) -> None:
    _insert_symbol(store, code)
    for index, date in enumerate(dates):
        close = base_close + index
        store.save_daily_bar(
            DailyBar(
                code=code,
                date=date,
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=1000 + index,
                turnover=close * (1000 + index),
            )
        )


def test_loads_every_required_symbol_from_database(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dates = [f"2026-07-{day:02d}" for day in range(1, 11)]
    _insert_bars(store, "JP.1306", dates, 100.0)
    _insert_bars(store, "JP.7203", dates, 200.0)

    inputs = _load_indicator_inputs(
        store,
        ["JP.1306", "JP.7203"],
        target_date="2026-07-10",
    )
    indicators = calculate_indicators_batch(
        inputs,
        {"JP.1306": "TOPIX", "JP.7203": "Toyota"},
    )

    assert set(inputs) == {"JP.1306", "JP.7203"}
    assert {indicator.code for indicator in indicators} == {
        "JP.1306",
        "JP.7203",
    }


def test_loads_all_symbols_with_one_sqlite_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    dates = [f"2026-07-{day:02d}" for day in range(1, 6)]
    _insert_bars(store, "JP.1306", dates, 100.0)
    _insert_bars(store, "JP.7203", dates, 200.0)
    original_get_connection = store._get_connection
    connection_calls = 0

    def counted_get_connection() -> sqlite3.Connection:
        nonlocal connection_calls
        connection_calls += 1
        return original_get_connection()

    monkeypatch.setattr(store, "_get_connection", counted_get_connection)

    inputs = _load_indicator_inputs(
        store,
        ["JP.1306", "JP.7203"],
        target_date="2026-07-05",
    )

    assert set(inputs) == {"JP.1306", "JP.7203"}
    assert connection_calls == 1


def test_excludes_rows_after_target_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_bars(
        store,
        "JP.1306",
        ["2026-07-09", "2026-07-10", "2026-07-11"],
        100.0,
    )

    inputs = _load_indicator_inputs(
        store,
        ["JP.1306"],
        target_date="2026-07-10",
    )

    assert list(inputs["JP.1306"]["date"]) == [
        "2026-07-10",
        "2026-07-09",
    ]


def test_respects_history_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dates = [f"2026-06-{day:02d}" for day in range(1, 21)]
    _insert_bars(store, "JP.1306", dates, 100.0)

    inputs = _load_indicator_inputs(
        store,
        ["JP.1306"],
        target_date="2026-06-20",
        history_limit=5,
    )

    assert len(inputs["JP.1306"]) == 5
    assert inputs["JP.1306"]["date"].iloc[0] == "2026-06-20"


def test_missing_required_symbol_stops_before_indicator_calculation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _insert_bars(store, "JP.1306", ["2026-07-10"], 100.0)

    with pytest.raises(SystemError, match="JP.7203"):
        _load_indicator_inputs(
            store,
            ["JP.1306", "JP.7203"],
            target_date="2026-07-10",
        )


def test_non_positive_history_limit_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="history_limit"):
        _load_indicator_inputs(
            store,
            ["JP.1306"],
            target_date="2026-07-10",
            history_limit=0,
        )


def test_normalizes_codes_and_preserves_first_seen_order() -> None:
    store = MagicMock(spec=DataStore)
    first_frame = pd.DataFrame({"date": ["2026-07-10"]})
    second_frame = pd.DataFrame({"date": ["2026-07-10"]})
    store.get_daily_bars_for_codes.return_value = {
        "JP.1306": second_frame,
        "JP.7203": first_frame,
    }

    inputs = _load_indicator_inputs(
        store,
        [" JP.7203 ", "JP.1306", "JP.7203", "   "],
        target_date="2026-07-10",
    )

    store.get_daily_bars_for_codes.assert_called_once_with(
        ["JP.7203", "JP.1306"],
        end_date="2026-07-10",
        limit_per_code=DEFAULT_HISTORY_LIMIT,
    )
    assert list(inputs) == ["JP.7203", "JP.1306"]
    assert inputs["JP.7203"] is first_frame
    assert inputs["JP.1306"] is second_frame


def test_missing_symbol_error_lists_every_code() -> None:
    store = MagicMock(spec=DataStore)
    store.get_daily_bars_for_codes.return_value = {}
    codes = [f"JP.{index:04d}" for index in range(25)]

    with pytest.raises(SystemError) as error:
        _load_indicator_inputs(
            store,
            codes,
            target_date="2026-07-10",
        )

    message = str(error.value)
    assert all(code in message for code in codes)
