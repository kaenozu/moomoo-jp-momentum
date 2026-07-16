from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from src.backtest_runner import BacktestRunner
from src.config import Config
from src.data_store import DataStore


def make_config(tmp_path: Path) -> Config:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(tmp_path / "test.db")},
                "watchlist": {"symbols_file": str(tmp_path / "symbols.json")},
                "backtest": {"market": "JP", "max_positions": 5},
                "universe": {"min_trade_price": 1, "max_trade_price": 100000},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (tmp_path / "symbols.json").write_text("[]", encoding="utf-8")
    return Config(str(config_path))


def seed_data(store: DataStore) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO symbols(code,name,market,type,role,tradable,enabled) "
            "VALUES('JP.1111','A','JP','stock','trade_candidate',1,1)"
        )
        conn.execute(
            "INSERT INTO daily_bars(code,date,open,high,low,close,volume,turnover) "
            "VALUES('JP.1111','2026-07-01',100,110,90,105,1000,105000)"
        )


def test_create_run_persists_reproducibility_metadata(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = DataStore(config)
    seed_data(store)
    runner = BacktestRunner(config)

    first_id = runner.create_run("momentum", "2026-07-01", "2026-07-01")
    second_id = runner.create_run("momentum", "2026-07-01", "2026-07-01")

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        first = conn.execute(
            "SELECT * FROM backtest_runs WHERE id=?", (first_id,)
        ).fetchone()
        second = conn.execute(
            "SELECT * FROM backtest_runs WHERE id=?", (second_id,)
        ).fetchone()
        assert first is not None and second is not None
        for key in ("config_hash", "universe_hash", "data_snapshot_hash"):
            assert len(first[key]) == 64
            assert first[key] == second[key]
        assert first["data_max_date"] == "2026-07-01"
        assert first["market"] == "JP"
        assert first["engine_version"] == "2.0.0"
        assert first["adjustment_policy"] == "qfq_no_additional_adjustment"

        conn.execute(
            "UPDATE daily_bars SET close=106 "
            "WHERE code='JP.1111' AND date='2026-07-01'"
        )

    third_id = runner.create_run("momentum", "2026-07-01", "2026-07-01")
    with sqlite3.connect(store.db_path) as conn:
        first_hash = conn.execute(
            "SELECT data_snapshot_hash FROM backtest_runs WHERE id=?", (first_id,)
        ).fetchone()[0]
        third_hash = conn.execute(
            "SELECT data_snapshot_hash FROM backtest_runs WHERE id=?", (third_id,)
        ).fetchone()[0]
    assert first_hash != third_hash


def test_existing_database_receives_metadata_columns(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = DataStore(config)
    with sqlite3.connect(store.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}
    assert {
        "market",
        "git_commit",
        "config_hash",
        "universe_hash",
        "data_snapshot_hash",
        "data_max_date",
        "engine_version",
        "adjustment_policy",
    }.issubset(columns)
