from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from src.v2_validation import (
    REQUIRED_BACKTEST_RUN_COLUMNS,
    compare_backtest_runs,
    file_sha256,
    online_backup,
    validate_database_migration,
    write_json_report,
    write_markdown_report,
)


def write_config(tmp_path: Path, database: Path) -> Path:
    symbols = tmp_path / "symbols.json"
    symbols.write_text("[]", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "database": {"path": str(database)},
                "watchlist": {"symbols_file": str(symbols)},
                "backtest": {"market": "JP", "max_positions": 5},
                "universe": {
                    "min_trade_price": 1,
                    "max_trade_price": 100000,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config


def create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_name TEXT,
                strategy_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                initial_cash REAL NOT NULL DEFAULT 100000,
                final_equity REAL,
                created_at TEXT NOT NULL DEFAULT '2026-07-01 00:00:00'
            );
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                score REAL,
                reason TEXT,
                risk_warnings TEXT,
                price_at_signal REAL,
                created_at TEXT NOT NULL DEFAULT '2026-07-01 00:00:00',
                UNIQUE(code, date)
            );
            INSERT INTO backtest_runs (
                run_name, strategy_name, start_date, end_date,
                initial_cash, final_equity
            ) VALUES (
                'legacy', 'momentum', '2026-06-01', '2026-06-30',
                100000, 103000
            );
            INSERT INTO signals (
                code, date, signal_type, score, reason, price_at_signal
            ) VALUES (
                'JP.1111', '2026-06-01', 'BUY', 80,
                'legacy signal', 100
            );
            """
        )


def test_copy_migration_is_non_destructive_preserving_and_idempotent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    create_legacy_database(source)
    config = write_config(tmp_path, source)
    source_hash = file_sha256(source)

    report = validate_database_migration(
        source_database=source,
        config_path=config,
        output_directory=tmp_path / "validation",
    )

    assert report.status == "PASS", report.errors
    assert report.source_unchanged is True
    assert report.source_sha256_before == source_hash
    assert report.source_sha256_after == source_hash
    assert report.integrity_check == "ok"
    assert report.foreign_key_violations == ()
    assert report.required_columns_present is True
    assert report.idempotent is True
    assert "backtest_runs" in report.preserved_tables
    assert "signals" in report.preserved_tables
    assert report.changed_tables == ()

    with sqlite3.connect(report.migrated_copy) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")
        }
        assert REQUIRED_BACKTEST_RUN_COLUMNS.issubset(columns)
        signal = conn.execute(
            "SELECT code, date, strategy_name, score FROM signals"
        ).fetchone()
        assert signal == ("JP.1111", "2026-06-01", "momentum", 80.0)
        run = conn.execute(
            "SELECT run_name, final_equity, engine_version, adjustment_policy "
            "FROM backtest_runs"
        ).fetchone()
        assert run == (
            "legacy",
            103000.0,
            "legacy",
            "qfq_no_additional_adjustment",
        )


def create_backtest_result_database(path: Path, fill_price: float = 110.0) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY,
                strategy_name TEXT NOT NULL
            );
            INSERT INTO backtest_runs(id, strategy_name) VALUES (1, 'momentum');
            CREATE TABLE backtest_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                signal_date TEXT,
                exit_reason TEXT
            );
            CREATE TABLE backtest_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                filled_at TEXT NOT NULL,
                fill_mode TEXT
            );
            CREATE TABLE backtest_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_cost REAL,
                market_price REAL,
                market_value REAL,
                unrealized_pl REAL,
                realized_pl REAL
            );
            CREATE TABLE backtest_equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                cash REAL,
                position_value REAL,
                total_equity REAL,
                drawdown_pct REAL
            );
            INSERT INTO backtest_orders (
                run_id, code, side, quantity, order_type, status, signal_date
            ) VALUES (
                1, 'JP.1111', 'BUY', 1,
                'MARKET_SIM', 'FILLED', '2026-07-01'
            );
            INSERT INTO backtest_positions (
                run_id, code, quantity, avg_cost, market_price,
                market_value, unrealized_pl, realized_pl
            ) VALUES (1, 'JP.1111', 1, 110, 110, 110, 0, 0);
            INSERT INTO backtest_equity_curve (
                run_id, date, cash, position_value, total_equity, drawdown_pct
            ) VALUES (1, '2026-07-02', 890, 110, 1000, 0);
            """
        )
        conn.execute(
            """
            INSERT INTO backtest_fills (
                run_id, code, side, quantity, price, filled_at, fill_mode
            ) VALUES (
                1, 'JP.1111', 'BUY', 1, ?,
                '2026-07-02', 'next_day_open'
            )
            """,
            (fill_price,),
        )


def test_backtest_comparison_classifies_tolerance_and_expected_differences(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-result.db"
    candidate = tmp_path / "candidate-result.db"
    create_backtest_result_database(legacy, fill_price=110.0)
    create_backtest_result_database(candidate, fill_price=110.0000001)

    within_tolerance = compare_backtest_runs(
        legacy,
        1,
        candidate,
        1,
        tolerance=1e-6,
    )
    assert within_tolerance.status == "PASS"
    assert within_tolerance.differences == ()

    with sqlite3.connect(candidate) as conn:
        conn.execute("UPDATE backtest_fills SET price=111 WHERE run_id=1")

    unexpected = compare_backtest_runs(legacy, 1, candidate, 1)
    assert unexpected.status == "DIFF_UNEXPECTED"
    assert len(unexpected.differences) == 1
    assert unexpected.differences[0].field == "price"
    assert unexpected.differences[0].expected is False

    expected = compare_backtest_runs(
        legacy,
        1,
        candidate,
        1,
        expected_difference_fields={"fills.price"},
    )
    assert expected.status == "DIFF_EXPECTED"
    assert expected.passed is True
    assert expected.differences[0].expected is True


def test_validation_reports_are_machine_and_human_readable(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.db"
    candidate = tmp_path / "candidate.db"
    create_backtest_result_database(legacy)
    create_backtest_result_database(candidate)
    report = compare_backtest_runs(legacy, 1, candidate, 1)

    json_path = write_json_report(report, tmp_path / "report.json")
    markdown_path = write_markdown_report(report, tmp_path / "report.md")

    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "PASS"
    assert "# V2 Backtest Comparison" in markdown_path.read_text(encoding="utf-8")


def test_backtest_comparison_rejects_unknown_run_id(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy-missing-run.db"
    candidate = tmp_path / "candidate-missing-run.db"
    create_backtest_result_database(legacy)
    create_backtest_result_database(candidate)

    with pytest.raises(ValueError, match="backtest run not found"):
        compare_backtest_runs(legacy, 999, candidate, 1)


def test_online_backup_rejects_source_as_destination(tmp_path: Path) -> None:
    database = tmp_path / "same.db"
    create_backtest_result_database(database)
    before = file_sha256(database)

    with pytest.raises(ValueError, match="must be different"):
        online_backup(database, database)

    assert file_sha256(database) == before
