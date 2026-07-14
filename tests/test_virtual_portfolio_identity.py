from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from src.models import CREATE_TABLES_SQL
from src.trading_identity import signal_strategy_name, virtual_portfolio_name
from src.virtual_trade_integrity import VirtualTradeIntegrityChecker


class StubConfig:
    def __init__(self, database_path: Path, values: dict[str, Any] | None = None):
        self.database_path = str(database_path)
        self.values = values or {}

    def get(self, key_path: str, default: Any = None) -> Any:
        return self.values.get(key_path, default)


def create_database(path: Path, *, portfolio: str | None) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(CREATE_TABLES_SQL)
        if portfolio is None:
            return
        connection.execute(
            "INSERT INTO virtual_orders "
            "(id, strategy_name, code, side, quantity, order_type, status, submitted_at) "
            "VALUES (1, ?, 'JP.7203', 'BUY', 1, 'MARKET_SIM', 'PENDING', '2026-07-01')",
            (portfolio,),
        )
        connection.commit()


def test_identity_defaults_and_overrides(tmp_path: Path) -> None:
    config = StubConfig(tmp_path / "unused.db")
    assert signal_strategy_name(config) == "momentum"
    assert virtual_portfolio_name(config) == "default"

    configured = StubConfig(
        tmp_path / "unused.db",
        {
            "signals.strategy_name": "quality-low-risk",
            "virtual_trade.portfolio_name": "paper-jp",
        },
    )
    assert signal_strategy_name(configured) == "quality-low-risk"
    assert virtual_portfolio_name(configured) == "paper-jp"


def test_wrong_empty_portfolio_fails_when_another_has_history(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.db"
    create_database(database, portfolio="default")
    checker = VirtualTradeIntegrityChecker(
        cast(Any, StubConfig(database, {"virtual_trade": {"initial_cash": 150000}}))
    )

    report = checker.run("momentum", require_history=True)

    assert report.exit_code == 2
    assert any(
        finding.code == "portfolio.empty_selection" for finding in report.errors
    )
    assert report.checked["portfolio_rows"] == 0


def test_no_history_is_distinct_and_strict_mode_fails(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    create_database(database, portfolio=None)
    checker = VirtualTradeIntegrityChecker(
        cast(Any, StubConfig(database, {"virtual_trade": {"initial_cash": 150000}}))
    )

    normal = checker.run("default")
    strict = checker.run("default", require_history=True)

    assert normal.exit_code == 1
    assert any(
        finding.code == "portfolio.no_virtual_trade_history"
        for finding in normal.warnings
    )
    assert strict.exit_code == 2
    assert any(
        finding.code == "portfolio.no_virtual_trade_history"
        for finding in strict.errors
    )
