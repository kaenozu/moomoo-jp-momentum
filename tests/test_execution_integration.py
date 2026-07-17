from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from src.config import Config
from src.data_store import DataStore
from src.virtual_trade import VirtualTradeManager


def make_config(tmp_path: Path) -> Config:
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text("[]", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(tmp_path / "test.db")},
                "watchlist": {"symbols_file": str(symbols_path)},
                "virtual_trade": {
                    "enabled": True,
                    "initial_cash": 1000,
                    "max_position_amount": 1000,
                    "max_total_positions": 5,
                    "max_position_per_symbol": 1,
                    "market_fill_mode": "next_day_open",
                    "slippage_bps": 0,
                    "commission": 10,
                },
                "universe": {
                    "min_trade_price": 1,
                    "max_trade_price": 100000,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return Config(str(config_path))


def seed_round_trip(store: DataStore) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO symbols(code,name,market,type,role,tradable,enabled) "
            "VALUES('JP.1111','A','JP','stock','trade_candidate',1,1)"
        )
        conn.executemany(
            "INSERT INTO daily_bars(code,date,open,high,low,close,volume,turnover) "
            "VALUES('JP.1111',?,?,?,?,?,?,?)",
            [
                ("2026-07-01", 100, 100, 100, 100, 1000, 100000),
                ("2026-07-02", 110, 110, 110, 110, 1000, 110000),
                ("2026-07-03", 120, 120, 120, 120, 1000, 120000),
            ],
        )


def test_virtual_round_trip_uses_shared_execution_accounting(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = DataStore(config)
    seed_round_trip(store)
    manager = VirtualTradeManager(config)

    buy = manager.place_order(
        "default",
        "JP.1111",
        "BUY",
        5,
        submitted_at="2026-07-01",
    )
    assert buy is not None
    buy_fills = manager.process_fills("default", "2026-07-02")
    assert len(buy_fills) == 1
    assert buy_fills[0].price == 110
    positions = manager.get_positions("default")
    assert [
        (position.code, position.quantity, position.avg_cost)
        for position in positions
    ] == [("JP.1111", 5, 112)]
    assert manager.get_cash("default", "2026-07-02") == 440

    sell = manager.place_order(
        "default",
        "JP.1111",
        "SELL",
        5,
        submitted_at="2026-07-02",
    )
    assert sell is not None
    sell_fills = manager.process_fills("default", "2026-07-03")
    assert len(sell_fills) == 1
    assert sell_fills[0].price == 120
    assert manager.get_positions("default") == []
    assert manager.get_cash("default", "2026-07-03") == 1030

    with sqlite3.connect(store.db_path) as conn:
        closed = conn.execute(
            """
            SELECT quantity, avg_cost, realized_pl
            FROM virtual_positions
            WHERE strategy_name='default' AND code='JP.1111'
            """
        ).fetchone()
    assert closed == (0, 112.0, 30.0)
