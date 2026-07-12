"""Regression coverage for authoritative watchlist-to-database synchronization."""

import json
import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore
from src.models import DailyBar


def _config(tmp_path: Path) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "symbols.db")}
    return config


def _write_watchlist(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _enabled_codes(store: DataStore) -> list[str]:
    return [
        symbol.code
        for symbol in store.get_enabled_symbols(include_benchmarks=True)
    ]


def _symbol_enabled(store: DataStore, code: str) -> int:
    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT enabled FROM symbols WHERE code = ?",
            (code,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_sync_disables_removed_symbol_without_deleting_history(tmp_path: Path) -> None:
    store = DataStore(_config(tmp_path))
    watchlist = tmp_path / "symbols.json"
    _write_watchlist(
        watchlist,
        [
            {"code": "JP.1306", "name": "TOPIX", "role": "benchmark"},
            {"code": "JP.7203", "name": "Toyota"},
        ],
    )
    store.sync_symbols_from_json(str(watchlist))
    store.save_daily_bar(
        DailyBar(
            code="JP.7203",
            date="2026-07-10",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            turnover=100500.0,
        )
    )

    _write_watchlist(
        watchlist,
        [{"code": "JP.1306", "name": "TOPIX", "role": "benchmark"}],
    )
    store.sync_symbols_from_json(str(watchlist))

    assert _enabled_codes(store) == ["JP.1306"]
    assert _symbol_enabled(store, "JP.7203") == 0
    with sqlite3.connect(store.db_path) as connection:
        bar_count = connection.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE code = 'JP.7203'"
        ).fetchone()[0]
    assert bar_count == 1


def test_sync_reenables_symbol_when_readded(tmp_path: Path) -> None:
    store = DataStore(_config(tmp_path))
    watchlist = tmp_path / "symbols.json"
    _write_watchlist(
        watchlist,
        [
            {"code": "JP.1306", "name": "TOPIX", "role": "benchmark"},
            {"code": "JP.7203", "name": "Toyota"},
        ],
    )
    store.sync_symbols_from_json(str(watchlist))

    _write_watchlist(
        watchlist,
        [{"code": "JP.1306", "name": "TOPIX", "role": "benchmark"}],
    )
    store.sync_symbols_from_json(str(watchlist))
    assert _symbol_enabled(store, "JP.7203") == 0

    _write_watchlist(
        watchlist,
        [
            {"code": "JP.1306", "name": "TOPIX", "role": "benchmark"},
            {"code": "JP.7203", "name": "Toyota Updated"},
        ],
    )
    store.sync_symbols_from_json(str(watchlist))

    assert _symbol_enabled(store, "JP.7203") == 1
    assert _enabled_codes(store) == ["JP.1306", "JP.7203"]


def test_explicitly_disabled_symbol_stays_disabled(tmp_path: Path) -> None:
    store = DataStore(_config(tmp_path))
    watchlist = tmp_path / "symbols.json"
    _write_watchlist(
        watchlist,
        [
            {"code": "JP.1306", "name": "TOPIX", "role": "benchmark"},
            {"code": "JP.7203", "name": "Toyota", "enabled": False},
        ],
    )

    store.sync_symbols_from_json(str(watchlist))

    assert _enabled_codes(store) == ["JP.1306"]
    assert _symbol_enabled(store, "JP.7203") == 0


@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "watchlist JSONが空"),
        (
            [
                {"code": "JP.1306", "name": "TOPIX"},
                {"code": "JP.1306", "name": "Duplicate"},
            ],
            "重複したcode",
        ),
        ({"code": "JP.1306", "name": "TOPIX"}, "トップレベルはlist"),
    ],
)
def test_invalid_watchlist_does_not_mutate_existing_symbols(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    store = DataStore(_config(tmp_path))
    watchlist = tmp_path / "symbols.json"
    _write_watchlist(
        watchlist,
        [
            {"code": "JP.1306", "name": "TOPIX"},
            {"code": "JP.7203", "name": "Toyota"},
        ],
    )
    store.sync_symbols_from_json(str(watchlist))

    _write_watchlist(watchlist, payload)
    with pytest.raises(ValueError, match=message):
        store.sync_symbols_from_json(str(watchlist))

    assert _enabled_codes(store) == ["JP.1306", "JP.7203"]


def test_load_is_additive_and_does_not_disable_unlisted_symbols(
    tmp_path: Path,
) -> None:
    store = DataStore(_config(tmp_path))
    full = tmp_path / "full.json"
    partial = tmp_path / "partial.json"
    _write_watchlist(
        full,
        [
            {"code": "JP.1306", "name": "TOPIX"},
            {"code": "JP.7203", "name": "Toyota"},
        ],
    )
    _write_watchlist(
        partial,
        [{"code": "JP.1306", "name": "TOPIX Updated"}],
    )
    store.sync_symbols_from_json(str(full))

    store.load_symbols_from_json(str(partial))

    assert _enabled_codes(store) == ["JP.1306", "JP.7203"]


def test_sync_handles_more_than_sqlite_parameter_limit(tmp_path: Path) -> None:
    store = DataStore(_config(tmp_path))
    watchlist = tmp_path / "symbols.json"
    payload = [
        {"code": f"JP.{index:04d}", "name": f"Symbol {index}"}
        for index in range(1005)
    ]
    _write_watchlist(watchlist, payload)

    count = store.sync_symbols_from_json(str(watchlist))

    assert count == 1005
    assert len(_enabled_codes(store)) == 1005
