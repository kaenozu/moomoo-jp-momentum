from pathlib import Path
import sqlite3

import yaml

from validated_backtest import (
    _evidence_status,
    build_capital_plan,
    create_isolated_workspace,
)
from src.config import load_config


def _write_config(path: Path, db_path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(db_path)},
                "virtual_trade": {
                    "initial_cash": 150000,
                    "max_position_amount": 20000,
                    "max_total_positions": 5,
                },
                "backtest": {"max_positions": 5},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_capital_plan_preserves_cash_above_position_limits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "source.sqlite3"
    db_path.touch()
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, db_path)

    plan = build_capital_plan(load_config(str(config_path)))

    assert plan.account_initial_cash == 150000
    assert plan.active_cash == 100000
    assert plan.cash_reserve == 50000
    assert plan.max_positions == 5
    assert plan.max_position_amount == 20000


def test_workspace_uses_backup_copy_without_mutating_source(
    tmp_path: Path,
) -> None:
    source_db = tmp_path / "source.sqlite3"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('original')")
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, source_db)

    workspace_config, provenance = create_isolated_workspace(
        str(config_path),
        tmp_path / "workspace",
    )
    with sqlite3.connect(workspace_config.database_path) as connection:
        connection.execute("UPDATE sample SET value = 'workspace'")
    with sqlite3.connect(source_db) as connection:
        source_value = connection.execute(
            "SELECT value FROM sample"
        ).fetchone()[0]

    assert source_value == "original"
    assert provenance["source_quick_check"] == "ok"
    assert provenance["workspace_quick_check"] == "ok"


def test_evidence_requires_enough_days_and_closed_trades() -> None:
    status = _evidence_status(
        trading_days=119,
        trade_count=100,
        total_return_pct=10,
        excess_vs_1306=5,
        profit_factor=2,
        max_drawdown_pct=5,
        min_trading_days=120,
        min_closed_trades=30,
    )
    assert status == "INSUFFICIENT_EVIDENCE"


def test_negative_or_underperforming_result_is_not_promising() -> None:
    status = _evidence_status(
        trading_days=250,
        trade_count=50,
        total_return_pct=8,
        excess_vs_1306=-1,
        profit_factor=2,
        max_drawdown_pct=5,
        min_trading_days=120,
        min_closed_trades=30,
    )
    assert status == "NO_EDGE_OBSERVED"
