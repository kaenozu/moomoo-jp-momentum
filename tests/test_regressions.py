"""CIで検出した回帰の再発防止テスト。"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
import yaml

import run_daily_cycle as daily_cycle_module
import src.connection as connection_module
from src.benchmark import BenchmarkManager
from src.config import Config
from src.data_store import DataStore
from src.screener import Screener
from src.strategy_runner import StrategyRunner


class FakeQuoteContext:
    """OpenD通信を行わないテスト用quote context。"""

    def __init__(self, ret: int, data: object) -> None:
        self.ret = ret
        self.data = data
        self.closed = False

    def get_market_snapshot(self, _codes: list[str]) -> tuple[int, object]:
        return self.ret, self.data

    def close(self) -> None:
        self.closed = True


def make_config(tmp_path: Path) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "test.db")}
    return config


def write_dry_run_config(
    tmp_path: Path,
    watchlist: object,
) -> Path:
    watchlist_path = tmp_path / "symbols.json"
    with watchlist_path.open("w", encoding="utf-8") as file:
        import json

        json.dump(watchlist, file)

    config_path = tmp_path / "config.yaml"
    config_data = {
        "opend": {"host": "127.0.0.1", "port": 11111, "timeout": 1},
        "watchlist": {"symbols_file": str(watchlist_path)},
        "database": {"path": str(tmp_path / "dry-run.db")},
        "virtual_trade": {"enabled": True},
    }
    with config_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(config_data, file, allow_unicode=True)
    return config_path


def test_connection_success_returns_connected(monkeypatch, tmp_path):
    context = FakeQuoteContext(connection_module.RET_OK, {"ok": True})
    monkeypatch.setattr(connection_module, "_check_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        connection_module,
        "OpenQuoteContext",
        lambda **_kwargs: context,
    )

    connection = connection_module.OpenDConnection(make_config(tmp_path))
    status = connection.connect()

    assert status.connected is True
    assert status.quote_context is context
    assert context.closed is False


def test_connection_failure_closes_context(monkeypatch, tmp_path):
    context = FakeQuoteContext(-1, "permission denied")
    monkeypatch.setattr(connection_module, "_check_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        connection_module,
        "OpenQuoteContext",
        lambda **_kwargs: context,
    )

    connection = connection_module.OpenDConnection(make_config(tmp_path))
    status = connection.connect()

    assert status.connected is False
    assert context.closed is True
    assert connection.get_quote_context() is None


def test_strategy_runner_run_one_returns_saved_count(monkeypatch, tmp_path):
    runner = StrategyRunner(make_config(tmp_path))
    monkeypatch.setattr(
        runner,
        "run_all",
        lambda *_args, **_kwargs: {"momentum": 3},
    )

    assert runner.run_one([], "2026-07-01", "momentum") == 3


def test_screener_rejects_missing_close(tmp_path):
    screener = Screener(make_config(tmp_path))
    row = pd.Series(
        {
            "code": "JP.7203",
            "date": "2026-07-01",
            "close": None,
        }
    )

    with pytest.raises(
        ValueError,
        match=r"code=JP\.7203, date=2026-07-01",
    ):
        screener._row_to_indicators(row)


def test_benchmark_return_lifecycle(tmp_path):
    config = make_config(tmp_path)
    DataStore(config)
    manager = BenchmarkManager(config)
    dataframe = pd.DataFrame(
        [
            {"time_key": "2026-07-01", "close": 100.0},
            {"time_key": "2026-07-02", "close": 110.0},
        ]
    )

    assert manager.save_benchmark_prices("JP.1306", dataframe) == 2
    with sqlite3.connect(config.database_path) as connection:
        before = connection.execute(
            "SELECT date, daily_return FROM benchmark_prices "
            "WHERE benchmark_code = ? ORDER BY date",
            ("JP.1306",),
        ).fetchall()
    assert before == [("2026-07-01", None), ("2026-07-02", None)]

    assert manager.update_daily_returns("JP.1306") == 1
    with sqlite3.connect(config.database_path) as connection:
        after = connection.execute(
            "SELECT date, daily_return FROM benchmark_prices "
            "WHERE benchmark_code = ? ORDER BY date",
            ("JP.1306",),
        ).fetchall()
    assert after[0] == ("2026-07-01", None)
    assert after[1][0] == "2026-07-02"
    assert after[1][1] == pytest.approx(10.0)


def test_dry_run_is_read_only(monkeypatch, tmp_path):
    config_path = write_dry_run_config(
        tmp_path,
        [
            {
                "code": "JP.7203",
                "name": "Toyota",
                "role": "trade_candidate",
            },
            {
                "code": "JP.1306",
                "name": "TOPIX",
                "role": "benchmark",
            },
        ],
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("dry-run must not initialize external or DB services")

    monkeypatch.setattr(daily_cycle_module, "OpenDConnection", fail_if_called)
    monkeypatch.setattr(daily_cycle_module, "DataStore", fail_if_called)

    results = daily_cycle_module.run_cycle(
        "2026-07-01",
        dry_run=True,
        config_path=str(config_path),
    )

    assert results == {
        "connection_attempted": False,
        "database_write_attempted": False,
        "virtual_trade_enabled": True,
        "integrity_check_enabled": True,
        "integrity_fail_on_warning": False,
        "integrity_errors": 0,
        "integrity_warnings": 0,
        "integrity_exit_code": 0,
        "calendar_checked": True,
        "is_trading_day": True,
        "cycle_skipped": False,
        "skip_reason": "",
        "virtual_portfolio": "default",
        "symbols": 2,
        "benchmarks": 1,
    }
    assert not (tmp_path / "dry-run.db").exists()


def test_dry_run_rejects_watchlist_without_benchmark(tmp_path):
    config_path = write_dry_run_config(
        tmp_path,
        [
            {
                "code": "JP.7203",
                "name": "Toyota",
                "role": "trade_candidate",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="benchmark が0件です"):
        daily_cycle_module.run_cycle(
            "2026-07-01",
            dry_run=True,
            config_path=str(config_path),
        )


def test_dry_run_rejects_non_list_watchlist(tmp_path):
    config_path = write_dry_run_config(
        tmp_path,
        {"code": "JP.1306", "role": "benchmark"},
    )

    with pytest.raises(RuntimeError, match="トップレベルがlistではありません"):
        daily_cycle_module.run_cycle(
            "2026-07-01",
            dry_run=True,
            config_path=str(config_path),
        )
