"""Regression coverage for per-fill commission and read-only integrity checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.data_store import DataStore
from src.migrations import migrate_virtual_fills_commission
from src.models import DailyBar
from src.virtual_trade import VirtualTradeManager
from src.virtual_trade_integrity import VirtualTradeIntegrityChecker, main


def _config(tmp_path: Path, commission: float = 55.0) -> Config:
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(tmp_path / "virtual.db")}
    config._config["virtual_trade"] = {
        **config._config["virtual_trade"],
        "commission": commission,
        "initial_cash": 100000,
        "market_fill_mode": "next_day_open",
        "slippage_bps": 0,
    }
    DataStore(config)
    return config


def _seed_symbol_and_bars(config: Config) -> None:
    store = DataStore(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO symbols
            (code, name, market, type, role, tradable, enabled)
            VALUES ('JP.0001', 'Test', 'JP', 'stock', 'trade_candidate', 1, 1)
            """
        )
    for bar_date, price in (
        ("2026-07-13", 1000.0),
        ("2026-07-14", 1010.0),
        ("2026-07-15", 1020.0),
    ):
        store.save_daily_bar(
            DailyBar(
                code="JP.0001",
                date=bar_date,
                open=price,
                high=price + 10,
                low=price - 10,
                close=price,
                volume=1000,
                turnover=price * 1000,
            )
        )


def _fill_buy(config: Config) -> tuple[VirtualTradeManager, float]:
    _seed_symbol_and_bars(config)
    manager = VirtualTradeManager(config)
    order = manager.place_order(
        "momentum",
        "JP.0001",
        "BUY",
        10,
        submitted_at="2026-07-13",
    )
    assert order is not None
    fills = manager.process_fills("momentum", "2026-07-14")
    assert len(fills) == 1
    return manager, fills[0].price


def test_migration_adds_nullable_commission_idempotently(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE virtual_fills (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL UNIQUE,
                strategy_name TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                filled_at TEXT NOT NULL,
                fill_mode TEXT,
                created_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO virtual_fills
            (id, order_id, strategy_name, code, side, quantity, price, filled_at)
            VALUES (1, 1, 'momentum', 'JP.0001', 'BUY', 1, 1000, '2026-07-14')
            """
        )
        migrate_virtual_fills_commission(connection)
        migrate_virtual_fills_commission(connection)
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(virtual_fills)")
        }
        value = connection.execute(
            "SELECT commission FROM virtual_fills WHERE id = 1"
        ).fetchone()[0]

    assert "commission" in columns
    assert value is None


def test_new_buy_fill_persists_commission(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    _, _ = _fill_buy(config)

    with sqlite3.connect(config.database_path) as connection:
        stored = connection.execute(
            "SELECT commission FROM virtual_fills"
        ).fetchone()[0]

    assert stored == pytest.approx(55.0)


def test_cash_replay_uses_stored_commission_after_config_change(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, commission=55.0)
    _, fill_price = _fill_buy(config)
    expected = 100000 - fill_price * 10 - 55.0

    changed = Config("tests/fixtures/config.test.yaml")
    changed._config["database"] = {"path": config.database_path}
    changed._config["virtual_trade"] = {
        **changed._config["virtual_trade"],
        "commission": 999.0,
        "initial_cash": 100000,
        "market_fill_mode": "next_day_open",
        "slippage_bps": 0,
    }
    manager = VirtualTradeManager(changed)

    assert manager.get_cash("momentum", "2026-07-14") == pytest.approx(expected)


def test_buy_and_sell_can_store_different_commissions(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    manager, _ = _fill_buy(config)

    manager.commission = 77.0
    order = manager.place_order(
        "momentum",
        "JP.0001",
        "SELL",
        10,
        submitted_at="2026-07-14",
    )
    assert order is not None
    fills = manager.process_fills("momentum", "2026-07-15")
    assert len(fills) == 1

    with sqlite3.connect(config.database_path) as connection:
        values = [
            row[0]
            for row in connection.execute(
                "SELECT commission FROM virtual_fills ORDER BY id"
            )
        ]

    assert values == [pytest.approx(55.0), pytest.approx(77.0)]


def test_zero_commission_is_a_valid_persisted_value(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=0.0)
    manager, fill_price = _fill_buy(config)

    fills = manager.get_fills("momentum")
    assert fills[0].commission == pytest.approx(0.0)
    assert manager.get_cash("momentum", "2026-07-14") == pytest.approx(
        100000 - fill_price * 10
    )


def test_legacy_null_commission_uses_config_and_is_reported(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, commission=55.0)
    _, fill_price = _fill_buy(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE virtual_fills SET commission = NULL")
        connection.execute(
            "UPDATE virtual_equity_curve SET cash = ?",
            (100000 - fill_price * 10 - 55.0,),
        )

    report = VirtualTradeIntegrityChecker(config).run("momentum")

    assert report.exit_code == 1
    assert sum(
        item.code == "fill.legacy_commission" for item in report.warnings
    ) == 1


def test_invalid_negative_commission_is_rejected_by_replay(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    manager, _ = _fill_buy(config)
    with sqlite3.connect(config.database_path) as connection:
        snapshot = connection.execute(
            "SELECT cash FROM virtual_equity_curve WHERE strategy_name = 'momentum'"
        ).fetchone()[0]
        connection.execute("UPDATE virtual_fills SET commission = -1")

    assert manager.get_cash("momentum", "2026-07-14") == pytest.approx(snapshot)
    report = VirtualTradeIntegrityChecker(config).run("momentum")
    assert report.exit_code == 2
    assert any(item.code == "fill.invalid_commission" for item in report.errors)


def test_integrity_report_is_clean_for_consistent_history(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    _fill_buy(config)

    report = VirtualTradeIntegrityChecker(config).run("momentum")

    assert report.exit_code == 0
    assert report.findings == []
    assert report.checked["fill_rows"] == 1


def test_integrity_detects_equity_cash_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    _fill_buy(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE virtual_equity_curve SET cash = cash + 1 "
            "WHERE strategy_name = 'momentum'"
        )

    report = VirtualTradeIntegrityChecker(config).run("momentum")

    assert report.exit_code == 2
    assert any(item.code == "equity.cash_mismatch" for item in report.errors)


def test_integrity_detects_non_trading_fill_date(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    _fill_buy(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE virtual_fills SET filled_at = '2026-07-20'"
        )
        connection.execute(
            "UPDATE virtual_orders SET filled_at = '2026-07-20'"
        )

    report = VirtualTradeIntegrityChecker(config).run("momentum")

    assert report.exit_code == 2
    assert any(item.code == "fill.non_trading_day" for item in report.errors)


def test_integrity_checker_does_not_migrate_read_only_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-read-only.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE virtual_orders (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT,
                status TEXT,
                submitted_at TEXT,
                filled_at TEXT
            );
            CREATE TABLE virtual_fills (
                id INTEGER PRIMARY KEY,
                order_id INTEGER,
                strategy_name TEXT,
                code TEXT,
                side TEXT,
                quantity INTEGER,
                price REAL,
                filled_at TEXT,
                fill_mode TEXT
            );
            CREATE TABLE virtual_positions (
                code TEXT,
                strategy_name TEXT,
                quantity INTEGER,
                avg_cost REAL,
                realized_pl REAL
            );
            CREATE TABLE virtual_equity_curve (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT,
                date TEXT,
                cash REAL,
                position_value REAL,
                total_equity REAL
            );
            CREATE TABLE daily_bars (
                code TEXT,
                date TEXT,
                close REAL
            );
            """
        )
    config = Config("tests/fixtures/config.test.yaml")
    config._config["database"] = {"path": str(database)}

    report = VirtualTradeIntegrityChecker(config).run("momentum")

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(virtual_fills)")
        }
    assert report.exit_code == 2
    assert any(
        item.code == "schema.missing_fill_commission" for item in report.errors
    )
    assert "commission" not in columns


def test_cli_returns_report_exit_code(tmp_path: Path) -> None:
    config = _config(tmp_path, commission=55.0)
    _fill_buy(config)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database:\n"
        f"  path: {config.database_path}\n"
        "virtual_trade:\n"
        "  initial_cash: 100000\n"
        "  commission: 55\n",
        encoding="utf-8",
    )

    assert main([
        "--config",
        str(config_path),
        "--strategy",
        "momentum",
        "--json",
    ]) == 0

def test_as_of_latest_fill_still_checks_current_position_cache(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, commission=55.0)
    _fill_buy(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "UPDATE virtual_positions SET quantity = quantity + 1 "
            "WHERE strategy_name = 'momentum'"
        )

    report = VirtualTradeIntegrityChecker(config).run(
        "momentum",
        as_of_date="2026-07-14",
    )

    assert any(
        item.code == "position.quantity_mismatch" for item in report.errors
    )
    assert report.checked["position_comparison_skipped_future_fills"] == 0

